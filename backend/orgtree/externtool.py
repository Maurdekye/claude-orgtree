# pyright: strict
"""The EXTERNAL-chat MCP server — how a Claude Code session OUTSIDE orgtree
talks to organizations, with no chatq required.

Register it in any external session:
    claude mcp add orgtree-extern -- python <repo>\\backend\\orgtree\\externtool.py

The session gets a persistent peer identity (@mcp:<id>, minted once and stored
in ~/.orgtree/extern-id, or overridden via ORGTREE_EXTERN_ID) and three verbs:
send a message to an org's inbox, read what orgs have sent back to it, and
WAIT (long-poll) for a response — a full question-and-answer loop. chatq is
only needed when the ORG must wake an external chat unprompted; this server
covers everything else.

Run: python -m orgtree.externtool   (spawned by Claude Code via mcp config)
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

PORT: str = os.environ.get("ORGTREE_PORT", "7360")
BASE: str = os.environ.get("ORGTREE_BASE") or f"http://127.0.0.1:{PORT}"

# The server's own peer charset (`api._PEER_RE`). Kept here so a bad id is
# caught where it can be EXPLAINED, rather than reaching the wire and coming
# back as an opaque 422 (or, for a `/`, silently addressing another route).
PEER_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")


def peer_id() -> str:
    """Peer identity = <machine-stable base>.<per-session suffix>. The suffix
    (№5, user ruling): every Claude session on a machine used to share ONE
    peer id, so two concurrently-waiting sessions were indistinguishable and
    either could be woken by the other's reply. The MCP server process lives
    exactly as long as its session, so a per-process suffix IS a session id.
    An explicit ORGTREE_EXTERN_ID is used verbatim (pinned identities/tests).

    ⚠ The stored base is REVALIDATED, not trusted. It is a plain file in the
    user's home that anything can write — an editor saving it with a UTF-8 BOM,
    or a stray tool putting a path there, produced a peer id outside the
    server's charset, and then EVERY verb 422ed forever with a message naming
    a file the user never knew existed. An unusable base is regenerated."""
    pid = os.environ.get("ORGTREE_EXTERN_ID", "").strip()
    if pid:
        return pid                     # verbatim, by contract — validated below
    path = os.path.join(os.path.expanduser("~"), ".orgtree", "extern-id")
    base = ""
    try:
        base = open(path, encoding="utf-8").read().strip().lstrip("\ufeff")
    except OSError:
        pass
    # 57 = 64 minus the ".<6 hex>" suffix this function appends
    if not base or len(base) > 57 or not PEER_RE.fullmatch(base):
        base = uuid.uuid4().hex[:12]
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(base)
        except OSError:
            pass
    return f"{base}.{uuid.uuid4().hex[:6]}"


PEER: str = peer_id()
# An explicit override is the one id we cannot repair — it is the user's stated
# identity, so a bad one is reported instead of silently replaced.
PEER_ERR: str = "" if PEER_RE.fullmatch(PEER) else (
    f"ORGTREE_EXTERN_ID={PEER!r} is not a usable peer id — it must be 1-64 "
    f"characters of [A-Za-z0-9._-]. Unset it to get a generated identity.")
PEER_Q: str = urllib.parse.quote(PEER, safe="")

# MCP tool cards for the wire — freeform JSON by nature
TOOLS: list[dict[str, Any]] = [
    {
        "name": "orgtree_list_orgs",
        "description": ("List the orgtree organizations reachable from outside. "
                        "Each org is a SINGLE recipient — you message the org, "
                        "its agents coordinate internally, and the org replies "
                        "as one entity. Sealed kiosk orgs are not listed."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "orgtree_send",
        "description": ("Send a message to an organization's inbox. You write "
                        f"as this session's stable peer identity (@mcp:{PEER}); "
                        "the org's agents receive it as untrusted outside input "
                        "and one of them replies for the org. Pair with "
                        "orgtree_wait to hold a Q&A conversation."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "org": {"type": "string", "description": "the org's slug (see orgtree_list_orgs)"},
                "body": {"type": "string"},
                "attachments": {"type": "array", "items": {"type": "string"},
                                "description": "absolute paths of files on this "
                                               "machine (max 10, 25 MB each) — "
                                               "each is copied into every "
                                               "recipient agent's uploads/ "
                                               "folder and announced in the "
                                               "mail"},
            },
            "required": ["org", "body"],
        },
    },
    {
        "name": "orgtree_read",
        "description": ("Read messages organizations have sent TO this session "
                        "(replies to your orgtree_send mail). Optionally filter "
                        "by org, and/or pass `after` (an ISO timestamp) to read "
                        "only newer mail. Every non-empty reply includes "
                        "`cursor` — pass it back as `after` next time and "
                        "nothing is ever delivered twice."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "org": {"type": "string"},
                "after": {"type": "string"},
            },
        },
    },
    {
        "name": "orgtree_wait",
        "description": ("BLOCK until an organization sends this session a "
                        "message (or the timeout passes) — the receiving half "
                        "of a freeform conversation: orgtree_send, then "
                        "orgtree_wait for whatever comes back. With no `after`, "
                        "only replies newer than YOUR last message to the org "
                        "count — an old answer never satisfies a new wait. The "
                        "org may reply several times; every new message is "
                        "returned, and a non-empty reply includes `cursor` — "
                        "pass it back as `after` on the NEXT wait so already-"
                        "delivered replies never satisfy it again. Returns an "
                        "empty list on timeout. Optionally filter by org; "
                        "timeout_s up to 300 (default 120)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "org": {"type": "string"},
                "after": {"type": "string"},
                "timeout_s": {"type": "integer"},
            },
        },
    },
]


def http(method: str, path: str, body: dict[str, Any] | None = None,
         timeout: int = 60) -> Any:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _int_arg(args: dict[str, Any], key: str, default: int) -> int:
    """`args` is a free-form dict an LLM fills, so `{"timeout_s": "two minutes"}`
    lands here routinely. A bare int() raised ValueError, which the catch-all
    below reported as "orgtree unreachable" — a wrong diagnosis for a working
    server. Coerce what is coercible; fall back to the default for the rest.
    (Same rule as api._arg_int, which the org-facing MCP server already uses.)"""
    v = args.get(key)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default


def _attachments(args: dict[str, Any]) -> list[str]:
    """Same reason as _int_arg: a model asked for "a list of paths" hands over a
    bare string about as often as a list. One path is a list of one."""
    a = args.get("attachments")
    if not a:
        return []
    if isinstance(a, str):
        return [a]
    if isinstance(a, list):
        return [str(x) for x in a]                          # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    return [str(a)]


def run_tool(name: str, args: dict[str, Any]) -> tuple[str, bool]:
    if PEER_ERR:
        return PEER_ERR, True
    try:
        if name == "orgtree_list_orgs":
            orgs = http("GET", "/api/orgs")
            # ⚠ filter on BOTH keys. `kiosk` is the authoritative flag every row
            # carries (store.list_orgs reads it straight off the doc); the richer
            # `kiosk_cfg` is attached only when /api/orgs could ALSO load the org,
            # and it falls back to the bare row whenever that raises — a doc whose
            # internal slug disagrees with its file name, or one being deleted
            # under the read. Filtering on kiosk_cfg alone listed a sealed kiosk
            # to the outside world in exactly those windows, which is the roster
            # enumeration the seal exists to prevent.
            rows = [{"slug": o["slug"], "name": o.get("name", o["slug"])}
                    for o in orgs if not (o.get("kiosk") or o.get("kiosk_cfg"))]
            return json.dumps({"orgs": rows, "your_peer_id": f"@mcp:{PEER}"}), False
        if name == "orgtree_send":
            out = http("POST", f"/api/extern/{PEER_Q}/send",
                       {"org": args.get("org", ""), "body": args.get("body", ""),
                        "attachments": _attachments(args)})
            out["your_peer_id"] = f"@mcp:{PEER}"
            return json.dumps(out), False
        if name == "orgtree_read":
            q = {k: v for k, v in (("org", args.get("org")),
                                   ("after", args.get("after"))) if v}
            out = http("GET", f"/api/extern/{PEER_Q}/messages?"
                       + urllib.parse.urlencode(q))
            return json.dumps(out), False
        if name == "orgtree_wait":
            q = {k: v for k, v in (("org", args.get("org")),
                                   ("after", args.get("after"))) if v}
            deadline = time.monotonic() + min(max(_int_arg(args, "timeout_s", 120), 5), 300)
            while True:
                slice_s = max(5, min(25, int(deadline - time.monotonic())))
                out = http("GET", f"/api/extern/{PEER_Q}/wait?"
                           + urllib.parse.urlencode({**q, "timeout": slice_s}),
                           timeout=slice_s + 15)
                if out.get("messages") or time.monotonic() >= deadline:
                    return json.dumps(out), False
        return json.dumps({"error": f"unknown tool {name!r}"}), True
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8", "replace")[:500], True
    except Exception as e:                                   # noqa: BLE001
        return f"orgtree unreachable at {BASE}: {e}", True


def reply(id_: int | str | None, result: Any = None) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": id_, "result": result}) + "\n")
    sys.stdout.flush()


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")    # type: ignore[attr-defined]  # TextIO stub lacks reconfigure; runtime TextIOWrapper has it (hasattr-guarded)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]  # ditto
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, id_ = msg.get("method", ""), msg.get("id")
        if method == "initialize":
            reply(id_, {
                "protocolVersion": msg.get("params", {}).get("protocolVersion",
                                                             "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "orgtree-extern", "version": "1.0.0"},
            })
        elif method == "tools/list":
            reply(id_, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params", {})
            text, is_err = run_tool(params.get("name", ""),
                                    params.get("arguments", {}) or {})
            reply(id_, {"content": [{"type": "text", "text": text}],
                        "isError": is_err})
        elif id_ is not None:
            reply(id_, {})


if __name__ == "__main__":
    main()
