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

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

PORT = os.environ.get("ORGTREE_PORT", "7360")
BASE = os.environ.get("ORGTREE_BASE") or f"http://127.0.0.1:{PORT}"


def peer_id() -> str:
    pid = os.environ.get("ORGTREE_EXTERN_ID", "").strip()
    if pid:
        return pid
    path = os.path.join(os.path.expanduser("~"), ".orgtree", "extern-id")
    try:
        pid = open(path, encoding="utf-8").read().strip()
        if pid:
            return pid
    except OSError:
        pass
    pid = uuid.uuid4().hex[:12]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(pid)
    except OSError:
        pass
    return pid


PEER = peer_id()

TOOLS = [
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
            },
            "required": ["org", "body"],
        },
    },
    {
        "name": "orgtree_read",
        "description": ("Read messages organizations have sent TO this session "
                        "(replies to your orgtree_send mail). Optionally filter "
                        "by org, and/or pass `after` (an ISO timestamp from a "
                        "previous message's `at`) to read only newer mail."),
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
                        "of a Q&A loop: orgtree_send a question, then "
                        "orgtree_wait for the answer. Returns the new messages, "
                        "or an empty list on timeout. Optionally filter by org; "
                        "`after` as in orgtree_read; timeout_s up to 300 "
                        "(default 120)."),
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


def http(method: str, path: str, body: dict | None = None, timeout: int = 60):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def run_tool(name: str, args: dict) -> tuple[str, bool]:
    try:
        if name == "orgtree_list_orgs":
            orgs = http("GET", "/api/orgs")
            rows = [{"slug": o["slug"], "name": o.get("name", o["slug"])}
                    for o in orgs if not o.get("kiosk_cfg")]
            return json.dumps({"orgs": rows, "your_peer_id": f"@mcp:{PEER}"}), False
        if name == "orgtree_send":
            out = http("POST", f"/api/extern/{PEER}/send",
                       {"org": args.get("org", ""), "body": args.get("body", "")})
            return json.dumps(out), False
        if name == "orgtree_read":
            q = {k: v for k, v in (("org", args.get("org")),
                                   ("after", args.get("after"))) if v}
            out = http("GET", f"/api/extern/{PEER}/messages?"
                       + urllib.parse.urlencode(q))
            return json.dumps(out), False
        if name == "orgtree_wait":
            q = {k: v for k, v in (("org", args.get("org")),
                                   ("after", args.get("after"))) if v}
            deadline = time.monotonic() + min(max(int(args.get("timeout_s") or 120), 5), 300)
            while True:
                slice_s = max(5, min(25, int(deadline - time.monotonic())))
                out = http("GET", f"/api/extern/{PEER}/wait?"
                           + urllib.parse.urlencode({**q, "timeout": slice_s}),
                           timeout=slice_s + 15)
                if out.get("messages") or time.monotonic() >= deadline:
                    return json.dumps(out), False
        return json.dumps({"error": f"unknown tool {name!r}"}), True
    except urllib.error.HTTPError as e:
        return e.read().decode("utf-8", "replace")[:500], True
    except Exception as e:                                   # noqa: BLE001
        return f"orgtree unreachable at {BASE}: {e}", True


def reply(id_, result=None):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": id_, "result": result}) + "\n")
    sys.stdout.flush()


def main():
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
