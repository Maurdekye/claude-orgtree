"""The "open this item in the docket" link on an `orgtree_work` tool chip.

User request 2026-09-05: "when an agent updates a docket item, it should have
a button next to the item that opens the docket and selects the item, like how
mails have such a button". This is the metadata half — `read_chat` attaching
`work: {slug}` to the chip, exactly the way it attaches `mail: {id, to}` to a
send. The button itself is `desk.tsx`'s ToolChip.

WHAT THIS FILE IS REALLY GUARDING. Three ways the link could be built wrong,
each of which looks fine in a screenshot:

  * INFERRED FROM THE ARGUMENTS. The tool was called with a name; a chip that
    reads THAT offers to open the item the agent asked for rather than the one
    the ledger acted on. §3 makes them disagree on purpose.
  * OFFERED ON A FAILURE. A refused call has no item to open, so a button
    would lead nowhere while looking identical to one that works.
  * OFFERED ON A READ. `list`/`get` name no item at all.

And the fourth, which is not a design error but a lane error and has bitten
this exact code before (test_codex_mail_link.py): Claude Code prefixes MCP
tools with `mcp__orgtree__`, while the codex and antigravity lanes journal the
BARE name. Matching only one form means the link silently never appears for
half the org.

Run: python tests/test_docket_open_link.py
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

RIG = tempfile.mkdtemp(prefix="docketlink-")
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


_n = [0]


def rig(tool_name, result_body, is_error=False, inp=None):
    """An org with one agent whose transcript holds a single tool call, and
    the chip `read_chat` builds from it."""
    _n[0] += 1
    org = store.create_org(f"docket link rig {_n[0]}")
    slug = org.d["slug"]
    org.hire(USER, None, "haiku", 5, "worker", add_dirs=[],
             tools={"bash": True, "web": False, "edit": False,
                    "subagents": False, "mcp": []},
             org_visibility="team", charter="rig agent")
    store.save_org(org)
    sid = f"thread-0000-0000-0000-{_n[0]:012d}"
    org.node("worker")["session_id"] = sid
    store.save_org(org)

    d = os.path.join(RIG, "journals", "projects", f"docket-link-rig-{_n[0]}")
    os.makedirs(d, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    with open(os.path.join(d, sid + ".jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "assistant", "timestamp": ts,
            "message": {"id": "row-1", "role": "assistant", "model": "x",
                        "content": [{"type": "tool_use", "id": "tu-1",
                                     "name": tool_name,
                                     "input": inp or {}}]}}) + "\n")
        f.write(json.dumps({
            "type": "user", "timestamp": ts,
            "message": {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "tu-1",
                "content": result_body, "is_error": is_error}]}}) + "\n")

    out = S.read_chat(store.load_org(slug), "worker")
    for m in out["messages"]:
        for t in m.get("tools") or []:
            if t and t.get("id") == "tu-1":
                return t
    raise AssertionError(f"the tool row never reached read_chat: {out}")


UPDATED = json.dumps({"updated": "git-review-workspace", "rev": 4,
                      "status": "in_progress",
                      "item": "git-review-workspace"})


def a_successful_update_carries_the_item():
    t = rig("mcp__orgtree__orgtree_work", UPDATED)
    assert t.get("work") == {"slug": "git-review-workspace"}, t


def the_bare_name_carries_it_too():
    """⚠ THE LANE TRAP. Codex and antigravity journal the unprefixed name; a
    check written against the prefixed form only would leave every agent on
    those lanes without the button while the write itself succeeded. That is
    exactly how the mail link broke (user report 2026-08-30)."""
    t = rig("orgtree_work", UPDATED)
    assert t.get("work") == {"slug": "git-review-workspace"}, t


def the_item_comes_from_the_RESULT_not_the_arguments():
    """The link must point where the ledger ACTED, not where the caller
    aimed. Making the two disagree is the only way to tell which one a
    reader is using — with matching values, an argument-reading chip passes
    every other check in this file."""
    t = rig("orgtree_work",
            json.dumps({"created": "the-item-that-was-made",
                        "item": "the-item-that-was-made"}),
            inp={"action": "create", "slug": "a-name-the-caller-typed"})
    assert t.get("work") == {"slug": "the-item-that-was-made"}, (
        "the chip took its identity from the call's arguments, so it would "
        f"offer to open an item the write did not touch: {t}")


def a_failed_call_gets_no_button():
    t = rig("orgtree_work",
            json.dumps({"detail": "no work item 'nope' that you may read"}),
            is_error=True)
    assert "work" not in t, (
        f"a refused write offered a link to an item it did not write: {t}")


def a_read_action_gets_no_button():
    """`list` and `get` name no item — the result has no `item` field at all,
    which is what keeps this from needing a list of action names here."""
    t = rig("orgtree_work", json.dumps({"items": [], "counts": {}, "now": "x"}),
            inp={"action": "list"})
    assert "work" not in t, f"a read action was offered a docket link: {t}"


def a_failure_whose_body_LOOKS_like_a_success_still_gets_no_button():
    """⚠ THE `is_error` GUARD EARNS ITS PLACE HERE AND NOWHERE ELSE. On an
    ordinary refusal the body is `{"detail": ...}`, which has no `item`, so the
    inner guard alone already refuses the link — and a mutation that deletes
    the `is_error` check therefore SURVIVES every other test in this file.
    That is what a redundant-looking guard looks like from the inside, and the
    only way to find out whether it does anything is to give it the one case
    where it is the sole thing standing: a failed result whose content still
    parses as a success. Retry and replay harnesses produce exactly that."""
    t = rig("orgtree_work", UPDATED, is_error=True)
    assert "work" not in t, (
        "a FAILED write whose body still looks successful was offered a link "
        f"to an item it did not write: {t}")


def another_tool_never_gets_one():
    """POSITIVE CONTROL ON THE MATCH ITSELF: a different tool whose result
    happens to carry an `item` key must not pick up the link, or the check is
    matching the payload rather than the tool."""
    t = rig("mcp__orgtree__orgtree_message",
            json.dumps({"item": "not-a-docket-write", "delivered": "boss",
                        "id": "m1"}))
    assert "work" not in t, f"the docket link attached to a mail send: {t}"
    # ...and its own link is untouched by any of this
    assert t.get("mail") == {"id": "m1", "to": "boss"}, t


check("a successful docket write carries the item it acted on",
      a_successful_update_carries_the_item)
check("the bare (codex/antigravity) tool name carries it too",
      the_bare_name_carries_it_too)
check("the item comes from the RESULT, never from the call's arguments",
      the_item_comes_from_the_RESULT_not_the_arguments)
check("a refused write gets no link", a_failed_call_gets_no_button)
check("a failure whose body looks like a success still gets no link",
      a_failure_whose_body_LOOKS_like_a_success_still_gets_no_button)
check("a read action gets no link", a_read_action_gets_no_button)
check("another tool carrying an `item` key gets no docket link",
      another_tool_never_gets_one)

# the runner reads a final total to tell "finished" from "died quietly", and
# warns when a suite exits 0 without one
if FAIL:
    print(f"\n{FAIL} FAILED, {PASS} passed")
    sys.exit(1)
print(f"\nALL {PASS} CHECKS PASS")
