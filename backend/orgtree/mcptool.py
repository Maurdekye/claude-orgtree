# pyright: strict
"""The orgtree MCP server every agent node loads — its hands on the org.

A minimal, dependency-free MCP stdio server (JSON-RPC 2.0). Identity comes from env
(ORGTREE_ORG / ORGTREE_NODE, set by the supervisor at spawn); every call forwards to
the orgtree API on localhost with that identity as the actor, so the LEDGER enforces
authority, budgets, capability subsets, addressing rules, and the no-defaults hire
rule — the schemas here mirror those rules so agents see them up front.

Run: python -m orgtree.mcptool   (spawned by Claude Code via --mcp-config)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, cast

ORG: str = os.environ.get("ORGTREE_ORG", "")
NODE: str = os.environ.get("ORGTREE_NODE", "")
PORT: str = os.environ.get("ORGTREE_PORT", "7360")
# sandboxed kiosk orgs (containers) reach the backend through the bridge
# listener instead of loopback: an explicit base URL + the org's secret
BASE: str = os.environ.get("ORGTREE_BASE") or f"http://127.0.0.1:{PORT}"
BRIDGE_SECRET: str = os.environ.get("ORGTREE_BRIDGE_SECRET", "")

# JSON-schema fragments/tool cards for the MCP wire — freeform JSON by nature
TOOLS_SCHEMA: dict[str, Any] = {
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

TOOLS: list[dict[str, Any]] = [
    {
        "name": "orgtree_message",
        "description": (
            "Send a message to another agent in your organization. Allowed: any "
            "descendant (any depth — messaging a non-child descendant grants it an "
            "audience to reply), your direct superior, your peers, any superior you "
            "hold an audience with, 'user' (top-level agents only), or an OUTSIDE "
            "party — '@ext:<chat-id>' (an external Claude Code session) or "
            "'@org:<slug>' (another organization's shared inbox). Outside mail is "
            "sent by top-level agents or org-inbox audience holders, goes out AS "
            "THE ORG (not under your name), and should be a single coordinated "
            "reply. The recipient is driven on delivery; replies arrive in your "
            "own future turns. An ARCHIVED recipient still receives: the mail "
            "waits in its inbox and is acted on when rehired."),
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
        "name": "orgtree_ask",
        "description": (
            "Ask the USER a structured question. It ALWAYS parks: an "
            "interactive card appears on your desk and in the user's inbox, "
            "and the answer arrives later as ordinary mail — so ask, then "
            "WRAP UP AND END YOUR TURN; never wait or poll. Optionally give "
            "2-4 options (the user can always answer free-text instead). "
            "⚠ If any OTHER mail wakes you before the user answers, the "
            "question is VOIDED everywhere and you must re-ask. Re-asking "
            "while one is open amends it. If you hold no user audience and "
            "are not top-level, the question is routed to your superior as "
            "mail instead."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string",
                             "description": "the complete question"},
                "options": {"type": "array", "items": {"type": "string"},
                            "maxItems": 4,
                            "description": "2-4 concise answer options (optional)"},
                "multi": {"type": "boolean",
                          "description": "several options may be selected"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "orgtree_request_credits",
        "description": (
            "TOP-LEVEL AGENTS ONLY: ask the user directly for a larger credit "
            "grant. Not mail — a structured request card on your desk and in "
            "the user's inbox. State the requested NEW TOTAL grant (not the "
            "increase) and a concrete reason. The user may grant the asked "
            "amount, MORE, LESS, or even reduce your grant — their decision "
            "arrives as mail, and you may take it as-is, re-ask, or route "
            "around it. If there are genuinely ZERO credits available to "
            "grant, the request is refused outright with no card. ⚠ Like a "
            "question, the pending request is VOIDED if other mail wakes you "
            "first — re-ask then. One pending request at a time; asking "
            "again amends it."),
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
            "defaults for you: write the hire's CHARTER in full (its role and "
            "standing instructions — injected into every one of its turns, "
            "editable later via orgtree_retool), and state exactly which "
            "folders, tools and org visibility it needs — you cannot grant "
            "anything you do not hold yourself. Seat costs: haiku 1, sonnet 3, "
            "opus 5, fable 10; seat + grant must fit within YOUR free credits. "
            "⚠ HIRING DOES NOT START ANYONE. A new hire sits IDLE until it "
            "receives its first message — the charter is who it is, not a task "
            "to begin. Follow every hire with an orgtree_message to it saying "
            "what to do now, or it will sit there doing nothing forever."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "1-2 words, the node id"},
                "tier": {"type": "string", "enum": ["haiku", "sonnet", "opus", "fable"]},
                "grant": {"type": "integer", "minimum": 0,
                          "description": "credits it may spend on ITS OWN hires"},
                "charter": {"type": "string",
                            "description": "the hire's role + standing "
                                           "instructions, written in full — "
                                           "required"},
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
            "required": ["name", "tier", "grant", "charter", "add_dirs", "tools",
                         "org_visibility"],
        },
    },
    {
        "name": "orgtree_retool",
        "description": (
            "Re-scope an existing agent in your subtree: change its folder grants, "
            "tool set, MCP servers, org visibility, charter, team charter, or its "
            "thinking effort (a cost/quality dial for your REPORTS — you never set "
            "your own). Only the fields you pass change. The capability rule still "
            "binds — you cannot grant anything you do not hold yourself, and "
            "shrinking a grant clamps everything beneath the target too."),
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
                "effort": {"type": "string",
                           "enum": ["low", "medium", "high", "xhigh", "max", ""],
                           "description": "thinking effort for this report "
                                          "('' clears to the CLI default)"},
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
        "description": (
            "Rehire an archived node in your subtree; it resumes with its full "
            "prior context. Rehiring under an archived superior rehires the "
            "whole chain first (costs bubble). You may also rehire YOUR OWN "
            "knowledge bearer (a past generation of yourself) — it then joins "
            "as your own subordinate. An unrecoverable node is re-seeded "
            "instead: fresh session, same role, credits and reports. The one "
            "refusal: a LOST generation (marked so in the chart) has no "
            "surviving transcript — there is no memory to wake, so it can "
            "never be rehired or consulted."),
        "inputSchema": {"type": "object",
                        "properties": {"node": {"type": "string"},
                                       "grant": {"type": "integer", "minimum": 0}},
                        "required": ["node"]},
    },
    {
        "name": "orgtree_list_orgs",
        "description": (
            "List the OTHER orgtree organizations on this backend — the "
            "reachable @org:<slug> peers for inter-org mail (top-level agents "
            "and org-inbox audience holders may write to them via "
            "orgtree_message). Sealed kiosk orgs are not listed."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "orgtree_move",
        "description": (
            "Reorganize: re-parent a node in your subtree under another node "
            "in your reach (promote toward you or demote under a descendant). "
            "Budget-neutral along the chain — a fully occupied tree can still "
            "reorganize (§4.5). The node's whole suborganization moves with "
            "it. Only the user can seat agents at top level."),
        "inputSchema": {"type": "object",
                        "properties": {"node": {"type": "string"},
                                       "new_parent": {"type": "string"}},
                        "required": ["node", "new_parent"]},
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
                        "summary; 'working' and 'idle' just record state. Reporting "
                        "'done' sends that report and then leaves you IDLE — "
                        "finished and idle are the same resting state, so there is "
                        "no need to follow a 'done' with an 'idle'."),
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
        "name": "orgtree_send_file",
        "description": (
            "Deliver a FILE to the user as a downloadable attachment: it is "
            "copied into your outbox/ (in your working folder) and appears in "
            "your chat as a download card. Sendable paths: files in your "
            "working folder (relative paths resolve there), the workspace, or "
            "any folder you hold. Announce the file in your reply or report — "
            "the card sits at the point in the chat where you sent it."),
        "inputSchema": {"type": "object",
                        "properties": {
                            "path": {"type": "string",
                                     "description": "the file to deliver"},
                            "note": {"type": "string",
                                     "description": "one-line caption shown "
                                                    "on the download card"}},
                        "required": ["path"]},
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
            "hands the descendant a direct line to the user's inbox), or "
            "target='extern' — audience with the ORG INBOX, so the descendant "
            "receives outside mail addressed to the org (@ext:/@org:) and may "
            "reply for it (the 'client contact' pattern; top-level grantors only). "
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


def call_api(tool: str, args: dict[str, Any]) -> str:
    headers: dict[str, str] = {"Content-Type": "application/json"}
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


def reply(id_: int | str | None, result: Any = None, error: Any = None) -> None:
    msg: dict[str, Any] = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = {"code": -32000, "message": str(error)}
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main() -> None:
    # ⚠ Windows defaults stdio to cp1252 — the CLI speaks UTF-8 JSON-RPC, so
    # without this every non-ASCII char in mail bodies (em-dashes…) arrived
    # mojibake'd (observed live: "—" → "â€\"")
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
        # ⚠ a line may parse as ANY json value — 5, "x", null, or a JSON-RPC 2.0
        # BATCH (a list), which is legal on the wire. `msg.get(...)` on those
        # raised AttributeError out of the loop and the process EXITED: an
        # agent whose MCP server dies mid-turn loses its only way to act, with
        # no error it can see. Same for a `params` that is not an object.
        if not isinstance(msg, dict):
            continue
        msg = cast("dict[str, Any]", msg)
        method = msg.get("method", "")
        id_ = msg.get("id")
        raw_params = msg.get("params")
        params: dict[str, Any] = (cast("dict[str, Any]", raw_params)
                                  if isinstance(raw_params, dict) else {})
        if id_ is None:
            # a notification draws NO response (JSON-RPC 2.0 / MCP): the
            # unsolicited `"id": null` this used to emit for an id-less
            # tools/call is a frame no client can match to a request
            continue
        if method == "initialize":
            reply(id_, {
                "protocolVersion": params.get("protocolVersion",
                                              "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "orgtree", "version": "1.0.0"},
            })
        elif method == "tools/list":
            reply(id_, {"tools": TOOLS})
        elif method == "tools/call":
            raw_args = params.get("arguments")
            out = call_api(str(params.get("name", "")),
                           cast("dict[str, Any]", raw_args)
                           if isinstance(raw_args, dict) else {})
            try:
                parsed = json.loads(out)
                is_err = isinstance(parsed, dict) and ("error" in parsed or "detail" in parsed)
                pd = cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else None
                text = pd.get("error") or pd.get("detail") or out \
                    if pd is not None else out
            except json.JSONDecodeError:
                is_err, text = False, out
            reply(id_, {"content": [{"type": "text", "text": str(text)}],
                        "isError": bool(is_err)})
        else:                      # unknown request — answer, don't wedge the client
            reply(id_, {})


if __name__ == "__main__":
    main()
