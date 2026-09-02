"""Regression test for the missing download card on Codex/Antigravity-tier
agents' `orgtree_send_file` calls -- the sibling of the mail-link bug fixed
in test_codex_mail_link.py, same shape, found by sweeping for the pattern
after that fix (coordinator's request, 2026-08-30).

WHY THIS MATTERS (user ruling): a sent file must arrive as a real download
card, never as a pasted path -- a path is not a delivery. `read_chat`
(supervisor.py) turns a completed `orgtree_send_file` tool call into that
card by reading the result JSON's `sent` object. The check that recognizes
the call was, until this fix, `entry.get("name") ==
"mcp__orgtree__orgtree_send_file"` -- the name Claude Code's own MCP client
prefixes automatically. Codex and Antigravity register the SAME tool under its
bare name (`orgtree_send_file`, from `mcptool.TOOLS`, confirmed live for the
sibling mail-link bug straight from a real Codex agent's transcript) and
journal their tool_use blocks with that bare name -- so the check never
matched, the card never appeared, and an agent that called the tool
correctly and got a real success reply back still delivered nothing a user
could see.

Run: python tests/test_codex_sendfile_link.py
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

RIG = tempfile.mkdtemp(prefix="codexsendfile-")
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
    """One assistant tool_use record + one user tool_result record -- exactly
    the two-record shape both the real Claude CLI and orgtree's own
    `_codex_journal` write (supervisor.py ~line 4512-4515, 4581-4586)."""
    d = os.path.join(RIG, "journals", "projects", "sendfile-rig")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, sid + ".jsonl")
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "assistant", "timestamp": ts,
            "message": {"id": "row-1", "role": "assistant", "model": "x",
                        "content": [{"type": "tool_use", "id": "tu-1",
                                     "name": tool_name,
                                     "input": {"path": "report.txt"}}]}}) + "\n")
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


_SENT = {"name": "report.txt", "path": "outbox/report.txt", "bytes": 1234}
_RESULT = json.dumps({
    "sent": _SENT,
    "hint": "delivered — the user sees a download card in your chat; "
            "announce the file in your reply or report"})


def bare_codex_tool_name_gets_the_download_card():
    """The bug: a Codex-tier agent's own bare-named `orgtree_send_file` call
    must carry the same `file` download-card metadata a Claude-tier agent's
    `mcp__orgtree__orgtree_send_file` call does."""
    org = store.create_org("sendfile rig")
    slug = org.d["slug"]
    org.hire(USER, None, "haiku", 5, "sender", add_dirs=[],
             tools={"bash": True, "web": False, "edit": False,
                    "subagents": False, "mcp": []},
             org_visibility="team", charter="rig agent")
    store.save_org(org)
    sid = "codex-thread-0000-0000-0000-000000000011"
    org.node("sender")["session_id"] = sid
    store.save_org(org)

    _write_transcript(sid, "orgtree_send_file", _RESULT)

    out = S.read_chat(store.load_org(slug), "sender")
    entry = _find_tool_entry(out)
    assert entry is not None, f"tool_use row never made it into read_chat: {out}"
    assert entry.get("file") == _SENT, (
        f"a Codex-shaped (bare-named) orgtree_send_file call must carry the "
        f"download-card metadata just like Claude's does: {entry}")


def prefixed_claude_tool_name_still_gets_the_download_card():
    """Companion check: the fix must not regress the existing Claude path —
    the `mcp__orgtree__`-prefixed name it always used must still match."""
    org = store.create_org("sendfile rig claude")
    slug = org.d["slug"]
    org.hire(USER, None, "haiku", 5, "sender", add_dirs=[],
             tools={"bash": True, "web": False, "edit": False,
                    "subagents": False, "mcp": []},
             org_visibility="team", charter="rig agent")
    store.save_org(org)
    sid = "claude-thread-0000-0000-0000-000000000012"
    org.node("sender")["session_id"] = sid
    store.save_org(org)

    _write_transcript(sid, "mcp__orgtree__orgtree_send_file", _RESULT)

    out = S.read_chat(store.load_org(slug), "sender")
    entry = _find_tool_entry(out)
    assert entry is not None, f"tool_use row never made it into read_chat: {out}"
    assert entry.get("file") == _SENT, (
        f"the Claude (mcp__orgtree__-prefixed) path must keep working: {entry}")


check("sendfilelink1 · a bare-named Codex orgtree_send_file gets the "
      "download card", bare_codex_tool_name_gets_the_download_card)
check("sendfilelink2 · a prefixed Claude orgtree_send_file still gets the "
      "download card (no regression)",
      prefixed_claude_tool_name_still_gets_the_download_card)

print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
