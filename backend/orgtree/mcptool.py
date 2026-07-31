"""The orgtree MCP server every agent node loads — its hands on the org.

A minimal, dependency-free MCP stdio server (JSON-RPC 2.0). Identity comes from env
(ORGTREE_ORG / ORGTREE_NODE, set by the supervisor at spawn); every call forwards to
the orgtree API on localhost with that identity as the actor, so the LEDGER enforces
authority, budgets, capability subsets, addressing rules, and the no-defaults hire
rule — the schemas here mirror those rules so agents see them up front.

Run: python -m orgtree.mcptool   (spawned by Claude Code via --mcp-config)
"""

import json
import os
import sys
import urllib.error
import urllib.request

ORG = os.environ.get("ORGTREE_ORG", "")
NODE = os.environ.get("ORGTREE_NODE", "")
PORT = os.environ.get("ORGTREE_PORT", "7360")
# sandboxed kiosk orgs (containers) reach the backend through the bridge
# listener instead of loopback: an explicit base URL + the org's secret
BASE = os.environ.get("ORGTREE_BASE") or f"http://127.0.0.1:{PORT}"
BRIDGE_SECRET = os.environ.get("ORGTREE_BRIDGE_SECRET", "")

TOOLS_SCHEMA = {
    "type": "object",
    "properties": {
        "bash": {"type": "boolean", "description": "terminal access"},
        "web": {"type": "boolean", "description": "web search + fetch"},
        "edit": {"type": "boolean", "description": "file editing"},
        "subagents": {"type": "boolean", "description": "ephemeral subagent (Task) tool"},
        "mcp": {"type": "array", "items": {"type": "string"},
                "description": "MCP server names to grant (must be ones YOU hold)"},
    },
    "required": ["bash", "web", "edit", "subagents", "mcp"],
}

TOOLS = [
    {
        "name": "orgtree_message",
        "description": (
            "Send a message to another agent in your organization. Allowed: any "
            "descendant (any depth — messaging a non-child descendant grants it an "
            "audience to reply), your direct superior, your peers, any superior you "
            "hold an audience with, 'user' (top-level agents only), or an external "
            "Claude Code session as '@ext:<chat-id>' (top-level only — replies to "
            "mail that arrived from one). The recipient is driven on delivery; "
            "replies arrive in your own future turns. An ARCHIVED recipient still "
            "receives: the mail waits in its inbox and is acted on when rehired."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "recipient node id, or 'user'"},
                "body": {"type": "string"},
                "kind": {"type": "string", "enum": ["message", "question", "request",
                                                    "decision", "status"],
                         "description": "what kind of message this is"},
            },
            "required": ["to", "body"],
        },
    },
    {
        "name": "orgtree_request_credits",
        "description": (
            "TOP-LEVEL AGENTS ONLY: ask the user directly for a larger credit "
            "grant. Not mail — a structured request the user approves or denies "
            "with one click. State the requested NEW TOTAL grant (not the "
            "increase) and a concrete reason. One pending request at a time; "
            "the verdict arrives as a notice in a future turn."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "new_limit": {"type": "integer", "minimum": 1,
                              "description": "the requested new TOTAL grant"},
                "reason": {"type": "string",
                           "description": "why you need it — required"},
            },
            "required": ["new_limit", "reason"],
        },
    },
    {
        "name": "orgtree_hire",
        "description": (
            "Hire a subagent under you (or deeper in your subtree). There are NO "
            "defaults for you: state exactly what you are hiring for (purpose), and "
            "exactly which folders, tools and org visibility it needs — you cannot "
            "grant anything you do not hold yourself. Seat costs: haiku 1, sonnet 3, "
            "opus 5, fable 10; seat + grant must fit within YOUR free credits."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "1-2 words, the node id"},
                "tier": {"type": "string", "enum": ["haiku", "sonnet", "opus", "fable"]},
                "grant": {"type": "integer", "minimum": 0,
                          "description": "credits it may spend on ITS OWN hires"},
                "purpose": {"type": "string",
                            "description": "what this hire is for — required"},
                "add_dirs": {"type": "array",
                             "items": {"type": "object",
                                       "properties": {"path": {"type": "string"},
                                                      "mode": {"type": "string",
                                                               "enum": ["rw", "ro"]}},
                                       "required": ["path", "mode"]},
                             "description": "folder grants; [] means scratch-only"},
                "tools": TOOLS_SCHEMA,
                "org_visibility": {"type": "string",
                                   "enum": ["self", "team", "subtree", "full"]},
                "parent": {"type": "string",
                           "description": "omit to hire directly under yourself"},
            },
            "required": ["name", "tier", "grant", "purpose", "add_dirs", "tools",
                         "org_visibility"],
        },
    },
    {
        "name": "orgtree_retool",
        "description": (
            "Re-scope an existing agent in your subtree: change its folder grants, "
            "tool set, MCP servers, org visibility, charter or team charter. Only "
            "the fields you pass change. The capability rule still binds — you "
            "cannot grant anything you do not hold yourself, and shrinking a grant "
            "clamps everything beneath the target too."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "the agent to re-scope"},
                "add_dirs": {"type": "array",
                             "items": {"type": "object",
                                       "properties": {"path": {"type": "string"},
                                                      "mode": {"type": "string",
                                                               "enum": ["rw", "ro"]}},
                                       "required": ["path", "mode"]},
                             "description": "REPLACES its folder grants when passed"},
                "tools": TOOLS_SCHEMA,
                "org_visibility": {"type": "string",
                                   "enum": ["self", "team", "subtree", "full"]},
                "charter": {"type": "string",
                            "description": "its standing role card (every turn)"},
                "team_charter": {"type": "string",
                                 "description": "standing instructions binding its whole subtree"},
            },
            "required": ["node"],
        },
    },
    {
        "name": "orgtree_retire",
        "description": ("Retire a node in your subtree (frees its seat + grant "
                        "back to its parent), or yourself if you have no live reports. "
                        "Retiring a node that still has live reports dissolves its whole "
                        "subtree (you'll be told). "
                        "Its session is preserved and can be rehired with context intact. "
                        "Retirement is the MOST you can do — permanent deletion is the "
                        "user's alone; if you believe an agent should be deleted, retire "
                        "it and ask the user through your chain or inbox."),
        "inputSchema": {"type": "object",
                        "properties": {"node": {"type": "string"}},
                        "required": ["node"]},
    },
    {
        "name": "orgtree_rehire",
        "description": "Rehire an archived node in your subtree; it resumes with its full prior context.",
        "inputSchema": {"type": "object",
                        "properties": {"node": {"type": "string"},
                                       "grant": {"type": "integer", "minimum": 0}},
                        "required": ["node"]},
    },
    {
        "name": "orgtree_dissolve",
        "description": "Dissolve a node in your subtree AND everything beneath it (recursive retire, deepest first).",
        "inputSchema": {"type": "object",
                        "properties": {"node": {"type": "string"}},
                        "required": ["node"]},
    },
    {
        "name": "orgtree_reallocate",
        "description": "Move grant credits between one of your reports and its parent: positive delta grants more, negative claws back unused credits.",
        "inputSchema": {"type": "object",
                        "properties": {"node": {"type": "string"},
                                       "delta": {"type": "integer"}},
                        "required": ["node", "delta"]},
    },
    {
        "name": "orgtree_status",
        "description": ("Report your working status. REQUIRED when you finish or get "
                        "stuck: 'done' and 'blocked' notify your superior with your "
                        "summary; 'working' and 'idle' just record state."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["working", "done", "blocked", "idle"]},
                "summary": {"type": "string", "description": "one or two sentences"},
            },
            "required": ["status", "summary"],
        },
    },
    {
        "name": "orgtree_chart",
        "description": "Your current view of the organization (scoped to your org-visibility level), with your credits and scope.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "orgtree_read_transcript",
        "description": ("Read a report's conversation transcript (read access is "
                        "strictly DOWNWARD: yourself and your descendants only — you "
                        "can never read peers or superiors)."),
        "inputSchema": {"type": "object",
                        "properties": {"node": {"type": "string"},
                                       "last": {"type": "integer", "minimum": 1,
                                                "maximum": 80,
                                                "description": "how many recent messages"}},
                        "required": ["node"]},
    },
    {
        "name": "orgtree_read_scratch",
        "description": ("Browse or read a descendant's scratch space (downward-only). "
                        "Omit path to list the root; pass a file path to read it."),
        "inputSchema": {"type": "object",
                        "properties": {"node": {"type": "string"},
                                       "path": {"type": "string"}},
                        "required": ["node"]},
    },
    {
        "name": "orgtree_switch_model",
        "description": (
            "Switch the model of an agent in your SUBTREE on the fly (never your "
            "own — your superior can). Its session and context survive; the next "
            "turn runs the new model. Cheaper tier: the seat difference becomes "
            "the agent's own free allocation. Pricier: paid from its free first, "
            "any shortfall bubbles up the chain to YOU — refused only if the "
            "whole chain lacks it. Tiers: haiku 1 · sonnet 3 · opus 5 · fable 10."),
        "inputSchema": {"type": "object",
                        "properties": {"node": {"type": "string"},
                                       "tier": {"type": "string",
                                                "enum": ["haiku", "sonnet",
                                                         "opus", "fable"]}},
                        "required": ["node", "tier"]},
    },
    {
        "name": "orgtree_audience",
        "description": (
            "Audience machinery (§7.3). action=request: open a request to speak with a "
            "distant superior (or 'user') — it climbs your chain one refusable hop at a "
            "time, starting at your direct superior. action=forward/deny: act on a "
            "request currently awaiting YOU (from= the requester, target= who they "
            "seek). action=grant: grant a descendant (from=) an audience — with you "
            "by default, or DELEGATED to anyone in your own reach via target=: a "
            "live peer, or your direct superior ('user' if you are top-level, which "
            "hands the descendant a direct line to the user's inbox). "
            "action=revoke: rescind an audience you granted (grantee=)."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string",
                           "enum": ["request", "forward", "grant", "deny", "revoke"]},
                "target": {"type": "string"},
                "from": {"type": "string"},
                "grantee": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["action"],
        },
    },
]


def call_api(tool: str, args: dict) -> str:
    headers = {"Content-Type": "application/json"}
    if BRIDGE_SECRET:
        headers["X-Orgtree-Bridge"] = BRIDGE_SECRET
    req = urllib.request.Request(
        f"{BASE}/api/agent",
        data=json.dumps({"org": ORG, "node": NODE, "tool": tool, "args": args}).encode(),
        headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return json.dumps({"error": e.read().decode("utf-8", "replace")[:500]})
    except Exception as e:                                   # noqa: BLE001
        return json.dumps({"error": f"orgtree API unreachable: {e}"})


def reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = {"code": -32000, "message": str(error)}
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    # ⚠ Windows defaults stdio to cp1252 — the CLI speaks UTF-8 JSON-RPC, so
    # without this every non-ASCII char in mail bodies (em-dashes…) arrived
    # mojibake'd (observed live: "—" → "â€\"")
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
        method = msg.get("method", "")
        id_ = msg.get("id")
        if method == "initialize":
            reply(id_, {
                "protocolVersion": msg.get("params", {}).get("protocolVersion",
                                                             "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "orgtree", "version": "1.0.0"},
            })
        elif method == "tools/list":
            reply(id_, {"tools": TOOLS})
        elif method == "tools/call":
            params = msg.get("params", {})
            out = call_api(params.get("name", ""), params.get("arguments", {}) or {})
            try:
                parsed = json.loads(out)
                is_err = isinstance(parsed, dict) and ("error" in parsed or "detail" in parsed)
                text = parsed.get("error") or parsed.get("detail") or out \
                    if isinstance(parsed, dict) else out
            except json.JSONDecodeError:
                is_err, text = False, out
            reply(id_, {"content": [{"type": "text", "text": str(text)}],
                        "isError": bool(is_err)})
        elif id_ is not None:      # unknown request — answer, don't wedge the client
            reply(id_, {})
        # notifications (no id) are ignored


if __name__ == "__main__":
    main()
