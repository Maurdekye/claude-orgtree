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
    python hub/hubtool.py listen
  Emits one line per inbound mail (long-polling the hub, acking after print),
  which is exactly how chatq delivers today — making the hub a candidate
  end-to-end chatq replacement.

Identity (user-ruled): the client's UID — minted once per user profile at
~/.orgtree/hub-client.json and reused forever — is the secret; the hub stores
sha256(uid). On FIRST registration the user's session must choose a display
NAME, which is persisted beside the uid and becomes part of the address:
    <name>.<username>.<sha256(uid)[:6]>        (kind: chat)
Orgs address a chat as @net:<that slug>, exactly like a remote org; the chat
addresses anyone on the roster by their slug. The dial-out direction of the
connection is preserved: the chat polls the hub; nothing ever reaches in.

Env: MAILHUB_URL (default http://127.0.0.1:7370) · MAILHUB_NAME (pre-seeds
the name choice; useful for scripts).
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
_ID_PATH = os.path.expanduser("~/.orgtree/hub-client.json")
_NAME_RE = re.compile(r"[^a-z0-9-]+")


def _load_ident() -> dict[str, Any]:
    try:
        d = json.load(open(_ID_PATH, encoding="utf-8"))
        if isinstance(d, dict) and cast("dict[str, Any]", d).get("uid"):
            return cast("dict[str, Any]", d)
    except (OSError, ValueError):
        pass
    return {}


def _save_ident(d: dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(_ID_PATH), exist_ok=True)
    with open(_ID_PATH, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1)
    try:
        # the uid IS the credential (redteam ④): owner-only on POSIX
        os.chmod(_ID_PATH, 0o600)
    except OSError:
        pass


def _mint_uid() -> dict[str, Any]:
    """Mint the shared per-profile uid ATOMICALLY (redteam ①): two chats
    starting together must end up with ONE uid — the losing writer of an
    unlocked read-modify-write registered an address whose secret died at
    its restart, stranding the slug on the first-write-wins hub forever.
    O_EXCL means exactly one minter wins; everyone else adopts the file."""
    os.makedirs(os.path.dirname(_ID_PATH), exist_ok=True)
    fresh = {"uid": uuid.uuid4().hex + uuid.uuid4().hex}     # 256-bit uid
    try:
        fd = os.open(_ID_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(fresh, f, indent=1)
        try:
            os.chmod(_ID_PATH, 0o600)
        except OSError:
            pass
        return fresh
    except FileExistsError:
        return _load_ident()          # the other starter won — adopt theirs


def _ident(name: str | None = None) -> dict[str, Any]:
    """The persistent client identity. The uid is minted ONCE per user
    profile; the NAME must be chosen on first registration (user ruling) and
    is immutable thereafter (the fingerprint suffix rides the address, so a
    rename would change the address — same immutability rule as orgs)."""
    d = _load_ident()
    if not d.get("uid"):
        d = _mint_uid()
        if not d.get("uid"):          # corrupt file lost the race — rare
            d = {"uid": uuid.uuid4().hex + uuid.uuid4().hex}
            _save_ident(d)
    fp = hashlib.sha256(str(d["uid"]).encode()).hexdigest()
    user = _NAME_RE.sub("-", getpass.getuser().lower()).strip("-") or "user"
    if not d.get("name"):
        pick = (name or os.environ.get("MAILHUB_NAME") or "").strip().lower()
        pick = _NAME_RE.sub("-", pick).strip("-")
        # the hub's slug cap is 128 (redteam ②): an over-long IMMUTABLE name
        # bricked the client — every register 422'd and no rename exists.
        # username + fingerprint are fixed width, so the budget is exact.
        budget = 128 - len(user) - 6 - 2
        pick = pick[:max(1, budget)].strip("-")
        if not pick:
            return d          # unnamed — hub_register must supply a name
        d["name"] = pick
        _save_ident(d)
        # read BACK (redteam ①): a concurrent chooser may have won the name
        # write — adopt whatever the file holds so both agree with the disk
        d = _load_ident()
    if d.get("name"):
        d["slug"] = f"{d['name']}.{user}.{fp[:6]}"
        _save_ident(d)
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
    d = _load_ident()
    ring = [str(x) for x in cast("list[Any]", d.get("seen_ids") or [])]
    fresh = [m for m in msgs if str(m.get("id")) not in ring]
    if fresh:
        ring.extend(str(m.get("id")) for m in fresh)
        d["seen_ids"] = ring[-200:]
        _save_ident(d)
    try:
        ack([str(m["id"]) for m in msgs])
    except Exception:                                            # noqa: BLE001
        pass          # unacked → redelivered → the ring collapses the repeat
    return fresh


def fmt(m: dict[str, Any]) -> str:
    return (f"[hub mail from {m.get('from')} at {m.get('received_at')}] "
            + str(m.get("body") or "").replace("\n", "\n  "))


# ─────────────────────────────────────────────── the Monitor-armable listener

def listen() -> None:
    d = _ident()
    if not d.get("slug"):
        print("no identity yet — run hub_register with a name first",
              flush=True)
        return
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
         "Join the mail hub as this chat. FIRST call must include `name` — "
         "it becomes part of your permanent address "
         "(<name>.<user>.<fingerprint>) and cannot change later. Idempotent "
         "afterwards; returns your address and the roster."),
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string",
                  "description": "your chosen display name (first call only)"},
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
                                    "with a name first"})
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
        listen()
    else:
        serve()
