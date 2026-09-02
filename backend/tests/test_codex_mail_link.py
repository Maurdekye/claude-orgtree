"""Regression test for the missing "view mail directly" link on Codex-tier
agents (user report, 2026-08-30: "orgtree messages in codex agents dont
contain the link to view the mail directly like they do in claude agents").

EMPIRICAL CONFIRMATION (not just code-reading -- pulled straight from a real,
live Codex-tier agent's own transcript on this machine): the delivered mail
TEXT is byte-identical across every tier (`_mail_block` in supervisor.py is
the one shared formatter for both the turn-start envelope and mid-task
steering, used regardless of provider) -- there is no link in that text for
ANY tier, so the missing link was never a text-content difference. The real
difference is in a piece of UI METADATA `read_chat` attaches to a SENT mail's
tool-call chip (the "open in mailbox" link, ledger.py's `t.mail` /
desk.tsx's `onMailLink`): a real Codex agent's own transcript
(journals/projects/<org>/<sid>.jsonl) records its `orgtree_message` /
`orgtree_status` tool calls under the BARE tool name Codex's dynamicTools
registers them under (`orgtree_message`, confirmed straight from a live
transcript) -- never the `mcp__orgtree__orgtree_message` form Claude Code's
own MCP client prefixes automatically. The link-detection check in
`read_chat` (supervisor.py, in the tool_result branch) only ever matched the
prefixed form, so it silently never fired for Codex (or Antigravity, which shares
the same bare-name registration) tool calls, even though the underlying mail
send succeeded and carried everything (`id`, `delivered`) the link needs.

This test builds a MINIMAL, real transcript file shaped exactly like the live
one inspected above (an assistant tool_use record naming the bare tool,
followed by a user tool_result record whose body is the tool's actual JSON
reply) and asserts `read_chat` attaches the same `mail` link metadata to it
that a Claude-shaped (`mcp__orgtree__...`-prefixed) transcript already gets.

Run: python tests/test_codex_mail_link.py
"""
import io
import json
import os
import sys
import tempfile
import time

if not getattr(sys, "_utf8_wrapped", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    sys._utf8_wrapped = True

RIG = tempfile.mkdtemp(prefix="codexmaillink-")
HOME = os.path.join(RIG, "home")
os.makedirs(HOME, exist_ok=True)
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

os.environ["ORGTREE_DATA"] = RIG
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME
sys.path.insert(0, BACKEND)

from orgtree import store, supervisor as S                         # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

PASS = FAIL = 0


def check(label, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:3d}  {label}")
    except Exception as e:                                        # noqa: BLE001
        FAIL += 1
        import traceback
        print(f"  FAIL     {label}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=6)


def _write_transcript(sid, tool_name, result_body):
    """One assistant tool_use record + one user tool_result record, exactly
    the two-record shape both the real Claude CLI and orgtree's own
    `_codex_journal` write (supervisor.py ~line 4512-4515, 4581-4586)."""
    d = os.path.join(RIG, "journals", "projects", "sendmail-rig")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, sid + ".jsonl")
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "assistant", "timestamp": ts,
            "message": {"id": "row-1", "role": "assistant", "model": "x",
                        "content": [{"type": "tool_use", "id": "tu-1",
                                     "name": tool_name, "input": {}}]}}) + "\n")
        f.write(json.dumps({
            "type": "user", "timestamp": ts,
            "message": {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "tu-1",
                "content": result_body, "is_error": False}]}}) + "\n")
    return path


def _find_tool_entry(out, tool_id="tu-1"):
    for m in out["messages"]:
        for t in m.get("tools") or []:
            if t and t.get("id") == tool_id:
                return t
    return None


def bare_codex_tool_name_gets_the_mail_link():
    """The bug: a Codex-tier agent's own bare-named `orgtree_message` tool
    call must carry the same `mail` link metadata a Claude-tier agent's
    `mcp__orgtree__orgtree_message` call does."""
    org = store.create_org("sendmail rig")
    slug = org.d["slug"]
    org.hire(USER, None, "haiku", 5, "sender", add_dirs=[],
             tools={"bash": True, "web": False, "edit": False,
                    "subagents": False, "mcp": []},
             org_visibility="team", charter="rig agent")
    store.save_org(org)
    sid = "codex-thread-0000-0000-0000-000000000001"
    org.node("sender")["session_id"] = sid
    store.save_org(org)

    result = json.dumps({"delivered": "coordinator", "id": "abc123",
                         "deferred": False, "warnings": []})
    _write_transcript(sid, "orgtree_message", result)

    out = S.read_chat(store.load_org(slug), "sender")
    entry = _find_tool_entry(out)
    assert entry is not None, f"tool_use row never made it into read_chat: {out}"
    assert entry.get("mail") == {"id": "abc123", "to": "coordinator"}, (
        f"a Codex-shaped (bare-named) orgtree_message call must carry the "
        f"'open in mailbox' link metadata just like Claude's does: {entry}")


def prefixed_claude_tool_name_still_gets_the_mail_link():
    """Companion check: the fix must not regress the existing Claude path —
    the `mcp__orgtree__`-prefixed name it always used must still match."""
    org = store.create_org("sendmail rig claude")
    slug = org.d["slug"]
    org.hire(USER, None, "haiku", 5, "sender", add_dirs=[],
             tools={"bash": True, "web": False, "edit": False,
                    "subagents": False, "mcp": []},
             org_visibility="team", charter="rig agent")
    store.save_org(org)
    sid = "claude-thread-0000-0000-0000-000000000002"
    org.node("sender")["session_id"] = sid
    store.save_org(org)

    result = json.dumps({"delivered": "coordinator", "id": "def456",
                         "deferred": False, "warnings": []})
    _write_transcript(sid, "mcp__orgtree__orgtree_message", result)

    out = S.read_chat(store.load_org(slug), "sender")
    entry = _find_tool_entry(out)
    assert entry is not None, f"tool_use row never made it into read_chat: {out}"
    assert entry.get("mail") == {"id": "def456", "to": "coordinator"}, (
        f"the Claude (mcp__orgtree__-prefixed) path must keep working: {entry}")


check("codexlink1 · a bare-named Codex tool_use gets the mail link",
      bare_codex_tool_name_gets_the_mail_link)
check("codexlink2 · a prefixed Claude tool_use still gets the mail link "
      "(no regression)", prefixed_claude_tool_name_still_gets_the_mail_link)

print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
