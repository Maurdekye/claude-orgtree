# pyright: strict
"""hubtool — an independent Claude Code chat as a first-class MAIL HUB client
(FR-06, user-ruled 2026-08-05: the strictly-org-to-org scope is reversed).

Two halves, one file, stdlib only (the externtool.py pattern):

MCP server (what a session adds):
    claude mcp add mailhub -- python <repo>\\hub\\hubtool.py
  Tools: hub_register (first run chooses the NAME — persisted; later calls
  are idempotent), hub_list (roster with kinds + presence), hub_send,
  hub_read, hub_wait (bounded long-poll).

Listener (the chatq-shape delivery half — arm it once with the Monitor tool):
    python hub/hubtool.py listen <name>       (or MAILHUB_NAME=<name>)
  Emits one line per inbound mail (long-polling the hub, acking after print),
  which is exactly how chatq delivers today — making the hub a candidate
  end-to-end chatq replacement. The name selects WHICH session identity
  listens; use the name this session registered with.

Identity (user-ruled 2026-08-05, superseding the per-profile single
identity): EACH SESSION mints its own unique identity, keyed by a NAME the
session chooses itself — semantically appropriate to its own context /
directive / purpose (e.g. "orgtree-redteam", "nebula-builder"). Identities
live one-per-file at ~/.orgtree/hub-clients/<name>.json: the 256-bit uid in
the file is the secret (the hub stores sha256(uid)), and the address is
    <name>.<username>.<sha256(uid)[:6]>        (kind: chat)
The session REMEMBERS its chosen name and registers with it again to resume
the same address — the name is the key, so a different name is a different
identity (which is exactly what makes N concurrent sessions safe: each has
its own address and its own deliver-once mailbox, no racing). Orgs address a
chat as @net:<slug>; the dial-out direction is preserved (the chat polls the
hub; nothing ever reaches in).

Env: MAILHUB_URL (default http://127.0.0.1:7370) · MAILHUB_NAME (pre-seeds /
selects the name; the listener requires one).
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, cast

HUB = os.environ.get("MAILHUB_URL", "http://127.0.0.1:7370").rstrip("/")
_ID_DIR = os.path.expanduser("~/.orgtree/hub-clients")
_LEGACY_ID = os.path.expanduser("~/.orgtree/hub-client.json")
_NAME_RE = re.compile(r"[^a-z0-9-]+")
_CUR: list[str] = []            # this process's active identity NAME


def _user() -> str:
    return _NAME_RE.sub("-", getpass.getuser().lower()).strip("-") or "user"


def _norm_name(raw: str) -> str:
    """Sanitize + length-budget a chosen name (the hub's slug cap is 128;
    username + fingerprint are fixed width, so the budget is exact —
    redteam ②: an over-long IMMUTABLE name bricked the client)."""
    pick = _NAME_RE.sub("-", str(raw or "").strip().lower()).strip("-")
    budget = 128 - len(_user()) - 6 - 2
    return pick[:max(1, budget)].strip("-")


def _id_path(name: str) -> str:
    return os.path.join(_ID_DIR, f"{name}.json")


def _active_name(explicit: str | None = None) -> str:
    if explicit:
        return _norm_name(explicit)
    if _CUR:
        return _CUR[0]
    return _norm_name(os.environ.get("MAILHUB_NAME") or "")


def _load_ident(name: str) -> dict[str, Any]:
    try:
        d = json.load(open(_id_path(name), encoding="utf-8"))
        if isinstance(d, dict) and cast("dict[str, Any]", d).get("uid"):
            return cast("dict[str, Any]", d)
    except (OSError, ValueError):
        pass
    # one-time adoption of the pre-ruling single-profile identity: if the
    # legacy file carries THIS name, its uid moves here so the already-
    # registered address keeps working (the hub is first-write-wins)
    try:
        legacy = json.load(open(_LEGACY_ID, encoding="utf-8"))
        if isinstance(legacy, dict) \
                and cast("dict[str, Any]", legacy).get("uid") \
                and _norm_name(str(cast("dict[str, Any]",
                                        legacy).get("name") or "")) == name:
            d2 = cast("dict[str, Any]", legacy)
            _save_ident(name, d2)
            return d2
    except (OSError, ValueError):
        pass
    return {}


def _save_ident(name: str, d: dict[str, Any]) -> None:
    os.makedirs(_ID_DIR, exist_ok=True)
    with open(_id_path(name), "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    try:
        # the uid IS the credential (redteam ④): owner-only on POSIX
        os.chmod(_id_path(name), 0o600)
    except OSError:
        pass


def _mint_uid(name: str) -> dict[str, Any]:
    """Mint this identity's uid ATOMICALLY (redteam ①): two processes
    choosing the SAME name concurrently must end up with ONE uid — the
    losing writer of an unlocked read-modify-write registered an address
    whose secret died at its restart, stranding the slug on the
    first-write-wins hub forever. O_EXCL means exactly one minter wins;
    everyone else adopts the file."""
    os.makedirs(_ID_DIR, exist_ok=True)
    fresh = {"uid": uuid.uuid4().hex + uuid.uuid4().hex}     # 256-bit uid
    try:
        fd = os.open(_id_path(name), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(fresh, f, indent=1)
        try:
            os.chmod(_id_path(name), 0o600)
        except OSError:
            pass
        return fresh
    except FileExistsError:
        return _load_ident(name)      # the other starter won — adopt theirs


def _ident(name: str | None = None) -> dict[str, Any]:
    """The persistent PER-SESSION identity (user ruling 2026-08-05): keyed
    by the session's self-chosen name — ~/.orgtree/hub-clients/<name>.json.
    Registering with a remembered name resumes the same uid and therefore
    the same address; the name is immutable per identity (the fingerprint
    suffix rides the address, so a rename would change it — same rule as
    orgs). No name resolved → empty dict; hub_register must supply one."""
    nm = _active_name(name)
    if not nm:
        return {}
    d = _load_ident(nm)
    if not d.get("uid"):
        d = _mint_uid(nm)
        if not d.get("uid"):          # corrupt file lost the race — rare
            d = {"uid": uuid.uuid4().hex + uuid.uuid4().hex}
            _save_ident(nm, d)
    fp = hashlib.sha256(str(d["uid"]).encode()).hexdigest()
    d["name"] = nm
    d["slug"] = f"{nm}.{_user()}.{fp[:6]}"
    _save_ident(nm, d)
    _CUR[:] = [nm]
    return d


def _call(path: str, payload: dict[str, Any] | None = None,
          method: str = "POST", timeout: float = 30.0) -> dict[str, Any]:
    d = _ident()
    req = urllib.request.Request(
        HUB + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Content-Type": "application/json",
                 # the uid IS the secret — headers only, never a URL
                 **({"X-Org-Auth": f"{d['slug']}:{d['uid']}"}
                    if d.get("slug") else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return cast("dict[str, Any]",
                    json.loads(r.read().decode() or "{}"))


def register(name: str | None = None) -> dict[str, Any]:
    d = _ident(name)
    if not d.get("slug"):
        return {"error": "no name chosen yet — call hub_register with a "
                         "name (it becomes part of your permanent address)"}
    out = _call("/api/register", {
        "slug": d["slug"], "org_name": d["name"],
        "username": getpass.getuser(), "kind": "chat",
        "blurb": "independent Claude Code chat"})
    return {"slug": d["slug"], "hub": out.get("name"),
            "roster": out.get("roster")}


def poll(wait: float) -> dict[str, Any]:
    return _call(f"/api/poll?wait={wait}", {}, timeout=wait + 10.0)


def ack(ids: list[str]) -> None:
    if ids:
        _call("/api/ack", {"ids": ids})


def take_fresh(out: dict[str, Any]) -> list[dict[str, Any]]:
    """Surface each hub message ONCE (redteam ③): the hub is at-least-once,
    so during an ACK OUTAGE the same message redelivers on every poll — not
    a crash-window duplicate but a repeat per pass. A small persisted ring
    in the identity file (the org client's pattern) collapses repeats; the
    ack is still attempted for EVERY delivered id, seen or not."""
    msgs = cast("list[dict[str, Any]]", out.get("messages") or [])
    if not msgs:
        return []
    nm = _active_name()
    d = _load_ident(nm)
    ring = [str(x) for x in cast("list[Any]", d.get("seen_ids") or [])]
    fresh = [m for m in msgs if str(m.get("id")) not in ring]
    if fresh:
        ring.extend(str(m.get("id")) for m in fresh)
        d["seen_ids"] = ring[-200:]
        _save_ident(nm, d)
    try:
        ack([str(m["id"]) for m in msgs])
    except Exception:                                            # noqa: BLE001
        pass          # unacked → redelivered → the ring collapses the repeat
    return fresh


def fmt(m: dict[str, Any]) -> str:
    return (f"[hub mail from {m.get('from')} at {m.get('received_at')}] "
            + str(m.get("body") or "").replace("\n", "\n  "))


# ─────────────────────────────────────────────── the Monitor-armable listener

def listen(name: str | None = None) -> None:
    """python hub/hubtool.py listen <name>   (or MAILHUB_NAME) — the name
    selects WHICH session identity listens; per-session identities are the
    whole point (user ruling 2026-08-05), so an unnamed listener would be
    ambiguous and is refused."""
    d = _ident(name)
    if not d.get("slug"):
        print("no identity name — pass one (python hubtool.py listen "
              "<name>) or set MAILHUB_NAME; use the name this session "
              "registered with", flush=True)
        return
    print(f"listening as {d['slug']} …", flush=True)
    try:
        register()
    except Exception:                                            # noqa: BLE001
        pass                       # hub down: the loop below keeps retrying
    while True:
        try:
            for m in take_fresh(poll(25.0)):
                print(fmt(m), flush=True)
        except Exception:                                        # noqa: BLE001
            time.sleep(5.0)


# ──────────────────────────────────────────────────────────── the MCP server

TOOLS: list[dict[str, Any]] = [
    {"name": "hub_register",
     "description": (
         "Join the mail hub as THIS SESSION. Choose a UNIQUE, semantically "
         "appropriate `name` that reflects this session's own context / "
         "directive / purpose (e.g. 'orgtree-redteam', 'terrain-pipeline') "
         "— every session has its own identity, and the name is the key. "
         "REMEMBER the name you chose: registering with it again later "
         "resumes the SAME address (<name>.<user>.<fingerprint>); a "
         "different name is a different identity. Immutable once minted. "
         "Returns your address and the roster."),
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string",
                  "description": "this session's self-chosen identity name "
                                 "— unique, purpose-describing, reused on "
                                 "every later register"},
     }}},
    {"name": "hub_list",
     "description": "Everyone on the hub — orgs and chats — with kind, "
                    "presence and last_seen.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "hub_send",
     "description": "Send mail to any hub client (org or chat) by its slug "
                    "from hub_list.",
     "inputSchema": {"type": "object", "properties": {
         "to": {"type": "string"}, "body": {"type": "string"}},
         "required": ["to", "body"]}},
    {"name": "hub_read",
     "description": "Fetch (and consume) any mail waiting for you right now.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "hub_wait",
     "description": "Wait up to `timeout` seconds (max 55) for new mail — "
                    "long-polls the hub; empty result on timeout.",
     "inputSchema": {"type": "object", "properties": {
         "timeout": {"type": "number"}}}},
]


def dispatch(tool: str, args: dict[str, Any]) -> str:
    if tool == "hub_register":
        return json.dumps(register(str(args.get("name") or "") or None))
    d = _ident()
    if not d.get("slug"):
        return json.dumps({"error": "not registered — call hub_register "
                                    "with this session's self-chosen name "
                                    "first (pick one from your purpose; "
                                    "reuse it every session)"})
    if tool == "hub_list":
        out = _call("/api/roster", None, method="GET")
        return json.dumps(out.get("roster") or [])
    if tool == "hub_send":
        out = _call("/api/send", {"id": uuid.uuid4().hex,
                                  "to": str(args.get("to") or ""),
                                  "body": str(args.get("body") or ""),
                                  "sent_at": time.strftime(
                                      "%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return json.dumps(out)
    if tool in ("hub_read", "hub_wait"):
        wait = min(max(float(args.get("timeout") or 0), 0.0), 55.0) \
            if tool == "hub_wait" else 0.0
        msgs = take_fresh(poll(wait))
        return json.dumps({"messages": [
            {"from": m.get("from"), "body": m.get("body"),
             "received_at": m.get("received_at")} for m in msgs]})
    return json.dumps({"error": f"unknown tool {tool!r}"})


def serve() -> None:
    """Minimal JSON-RPC MCP over stdio (the externtool.py shape)."""
    def reply(id_: Any, result: dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(
            {"jsonrpc": "2.0", "id": id_, "result": result}) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = cast("dict[str, Any]", json.loads(line))
        except ValueError:
            continue
        method, id_ = msg.get("method"), msg.get("id")
        if method == "initialize":
            reply(id_, {"protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "mailhub", "version": "1.0"}})
        elif method == "tools/list":
            reply(id_, {"tools": TOOLS})
        elif method == "tools/call":
            p = cast("dict[str, Any]", msg.get("params") or {})
            try:
                text = dispatch(str(p.get("name")),
                                cast("dict[str, Any]",
                                     dict(p.get("arguments") or {})))
            except urllib.error.URLError as e:
                text = json.dumps({"error": f"hub unreachable: {e.reason}"})
            except Exception as e:                               # noqa: BLE001
                text = json.dumps({"error": str(e)})
            reply(id_, {"content": [{"type": "text", "text": text}]})
        elif id_ is not None:
            reply(id_, {})


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "listen":
        listen(sys.argv[2] if len(sys.argv) > 2 else None)
    else:
        serve()
