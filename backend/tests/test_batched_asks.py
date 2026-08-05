"""FR-04 · batched asks — THE CONTRACT, WRITTEN BEFORE THE BUILD.

The user asked for several questions in one card, in the shape Claude Code's
own batch AskUserQuestion uses: a tab strip, one submit bar, one cancel. That
is a UI description, and the interesting half is underneath it — a batch is a
single ask that happens to have several questions, and almost every way of
getting it wrong comes from treating it as several asks that happen to arrive
together.

The three that would hurt, and that this file exists to prevent:
  * N ENTRIES INSTEAD OF ONE. The ledger's rule is one open ask per node;
    N entries would break amend, void-on-wake and the desk's card in one go.
  * A PARTIAL VOID. Mail arriving mid-batch must void the WHOLE card — a
    half-answered batch that survives is a card whose remaining tabs answer a
    question the agent has already moved past.
  * A UI-ONLY SUBMIT GATE. "Submit is disabled until every tab is answered"
    is a rendering detail; if the server does not enforce it, the agent
    receives a batch answer with holes in it and no way to tell.

⚠ THE FEATURE IS NOT BUILT YET. This suite detects that and reports its checks
as PENDING rather than failing — the fast tier stays green, and the day the
build lands every pending check runs for real. Nothing here is a gap(): a gap
records something wrong with shipped code, and there is no shipped code to be
wrong yet. What this is, is the acceptance gate, agreed in advance.

    python backend/tests/test_batched_asks.py [-v]
"""

from __future__ import annotations

import inspect
import os
import shutil
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-batchask-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.environ["USERPROFILE"] = os.environ["HOME"] = os.path.join(_TMP, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)
os.environ["ORGTREE_STEER_HOOK"] = "0"

from orgtree import mcptool                                      # noqa: E402
from orgtree.ledger import LedgerError, Org, USER                # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
PENDING: list[str] = []
VERBOSE = "-v" in sys.argv
ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True, "mcp": []}


# ─────────────────────────────────────────────────── is the feature there yet?
def _batch_supported() -> bool:
    """Two independent signals, because either alone can be half-true during a
    build: the ledger verb takes a batch, and the agent-facing tool advertises
    one."""
    try:
        sig = inspect.signature(Org.ask_user)
        ledger_ok = "questions" in sig.parameters
    except (TypeError, ValueError):
        ledger_ok = False
    tool = next((t for t in mcptool.TOOLS
                 if t.get("name") == "orgtree_ask"), None)
    props = ((tool or {}).get("inputSchema") or {}).get("properties") or {}
    return bool(ledger_ok and "questions" in props)


BUILT = _batch_supported()


def check(label, fn) -> None:
    """A check that runs today."""
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def contract(label, fn) -> None:
    """A check of the UNBUILT contract: runs for real once the feature exists,
    reported as pending until then. It never fails the suite while BUILT is
    false — but it is written to pass only against a correct implementation,
    so it is a gate rather than a wish."""
    if not BUILT:
        PENDING.append(label)
        if VERBOSE:
            print(f"  … pending  {label}")
        return
    check(label, fn)


def spec(**over):
    s = dict(add_dirs=[], tools=dict(ALL_TOOLS), org_visibility="team",
             charter="batched-ask test hire")
    s.update(over)
    return s


_n = [0]


def org2():
    _n[0] += 1
    org = Org.create(f"zz batch {_n[0]}")
    org.hire(USER, None, "opus", 20, "boss")
    org.hire("boss", "boss", "haiku", 5, "kid", **spec())
    return org


def open_asks(org):
    return [a for a in org.d.get("asks", []) if a["status"] == "open"]


QS = [
    {"question": "which storage backend?", "header": "Storage",
     "options": [{"label": "sqlite"}, {"label": "postgres"}]},
    {"question": "ship behind a flag?", "header": "Rollout",
     "options": [{"label": "flag"}, {"label": "straight to main"}]},
    {"question": "who reviews it?", "header": "Review"},
]


def main() -> int:
    print("orgtree · FR-04 batched asks — the contract "
          + ("(feature DETECTED: running for real)" if BUILT
             else "(NOT BUILT: checks are pending)"))

    print("\n§1  what must keep working — the migration guarantee")

    def _single_form_unchanged():
        org = org2()
        r = org.ask_user("boss", "ship now or wait?",
                         options=["ship", "wait"], header="Ship gate")
        assert r.get("asked"), r
        a = open_asks(org)[0]
        assert a["node"] == "boss" and a["header"] == "Ship gate"
        assert a["options"] == [{"label": "ship"}, {"label": "wait"}]
    check("the single-question form still works exactly as before",
          _single_form_unchanged)

    def _one_open_ask_per_node():
        org = org2()
        org.ask_user("boss", "first?")
        org.ask_user("boss", "second?")
        assert len(open_asks(org)) == 1, open_asks(org)
        assert open_asks(org)[0]["question"] == "second?", \
            "re-asking must AMEND the open card, not stack a second one"
    check("one open ask per node — re-asking amends", _one_open_ask_per_node)

    print("\n§2  the batch's shape")

    def _single_normalizes_to_a_batch():
        org = org2()
        org.ask_user("boss", "just the one?", header="Solo")
        a = open_asks(org)[0]
        assert a.get("questions"), "a single ask must expose a 1-entry batch"
        assert len(a["questions"]) == 1
        assert a["questions"][0]["question"] == "just the one?"
        assert a["questions"][0]["header"] == "Solo"
    contract("a single-question ask normalizes to a one-question batch",
             _single_normalizes_to_a_batch)

    def _batch_is_one_entry():
        org = org2()
        r = org.ask_user("boss", questions=QS)
        assert r.get("asked"), r
        assert len(open_asks(org)) == 1, (
            "a batch must be ONE ask entry — N entries break the one-open-ask "
            "rule, amend, void-on-wake and the desk card at once")
        a = open_asks(org)[0]
        assert len(a["questions"]) == 3
        assert [q["header"] for q in a["questions"]] \
            == ["Storage", "Rollout", "Review"]
    contract("a batch of three is a single ask entry", _batch_is_one_entry)

    def _bounds():
        org = org2()
        expect = lambda fn, needle: None                        # noqa: E731
        try:
            org.ask_user("boss", questions=[])
            raise AssertionError("an empty batch was accepted")
        except LedgerError:
            pass
        try:
            org.ask_user("boss", questions=[dict(q) for q in (QS * 2)][:5])
            raise AssertionError("a 5-question batch was accepted")
        except LedgerError:
            pass
        _ = expect
    contract("1–4 questions; empty and over-four are refused", _bounds)

    def _each_entry_needs_a_question():
        org = org2()
        try:
            org.ask_user("boss", questions=[{"header": "No text"}])
            raise AssertionError("a question with no text was accepted")
        except LedgerError:
            pass
    contract("every entry in the batch needs question text",
             _each_entry_needs_a_question)

    def _duplicate_headers_survive():
        org = org2()
        org.ask_user("boss", questions=[
            {"question": "first area?", "header": "Area"},
            {"question": "second area?", "header": "Area"}])
        a = open_asks(org)[0]
        assert len(a["questions"]) == 2, (
            "two questions with the SAME header collapsed — tabs must be "
            "identified by position, not by their label")
    contract("two tabs may share a header", _duplicate_headers_survive)

    def _options_are_per_question():
        org = org2()
        org.ask_user("boss", questions=[
            {"question": "pick one", "options": ["a", "b"], "multi": False},
            {"question": "pick many", "options": ["x", "y"], "multi": True}])
        qs = open_asks(org)[0]["questions"]
        assert qs[0]["options"] == [{"label": "a"}, {"label": "b"}]
        assert qs[1].get("multi") is True and qs[0].get("multi") is not True, qs
    contract("options and multi are per question, not per card",
             _options_are_per_question)

    print("\n§3  the lifecycle — amend, void, answer")

    def _amend_replaces_the_whole_batch():
        org = org2()
        org.ask_user("boss", questions=QS)
        org.ask_user("boss", questions=[{"question": "changed my mind",
                                         "header": "New"}])
        assert len(open_asks(org)) == 1
        a = open_asks(org)[0]
        assert len(a["questions"]) == 1 and a["questions"][0]["header"] == "New", (
            "amending a batch must REPLACE it — a merge would leave tabs from "
            "a question the agent has already moved past")
    contract("re-asking replaces the entire batch", _amend_replaces_the_whole_batch)

    def _void_is_all_or_nothing():
        # NB (build 2026-08-05): the void fires at WAKE time — the turn-start
        # `void_open_asks` call (supervisor.py:1461) — not at post time. That
        # is deliberate: mail queued behind a busy node leaves the card
        # answerable until the turn actually starts, and an answer landing in
        # that window still validly reaches the same turn. So this check
        # exercises the void hook itself, which is what every wake path runs.
        org = org2()
        org.ask_user("boss", questions=QS)
        aid = open_asks(org)[0]["id"]
        org.post_mail(USER, "boss", "something else entirely")
        org.void_open_asks("boss")                    # = the wake's turn start
        left = [a for a in org.d["asks"] if a["id"] == aid][0]
        assert left["status"] != "open", "the batch survived a wake"
        assert not any(q.get("answer") for q in left.get("questions", [])), (
            "a voided batch kept per-tab answers — the void is the whole card")
    contract("any wake voids the WHOLE batch, not the unanswered tabs",
             _void_is_all_or_nothing)

    def _answer_requires_every_tab():
        # the UI disables submit until every tab is answered; the SERVER must
        # enforce it too, or a batch answer arrives with holes and the agent
        # cannot tell which tab was skipped
        org = org2()
        org.ask_user("boss", questions=QS)
        aid = open_asks(org)[0]["id"]
        try:
            # one selection for a three-tab card
            org.ask_answer(aid, selected=["sqlite"])
            raise AssertionError("a partial batch answer was accepted")
        except LedgerError as e:
            assert "answer" in str(e).lower(), e
    contract("a partial batch answer is refused server-side",
             _answer_requires_every_tab)

    def _one_body_labelled_by_header():
        # ask_answer composes the body and hands it back; the CALLER delivers
        # it as ordinary user mail (that is what drives the turn). So the
        # contract is about the composed body: one of them, carrying every
        # tab labelled by its header.
        org = org2()
        org.ask_user("boss", questions=QS)
        aid = open_asks(org)[0]["id"]
        r = org.ask_answer(aid, selected=["sqlite", "flag", "the redteam"])
        assert r["node"] == "boss", r
        body = r["body"]
        for header, answer in (("Storage", "sqlite"), ("Rollout", "flag"),
                               ("Review", "the redteam")):
            assert header in body and answer in body, (header, body)
        assert body.count("[ANSWER") == 1, (
            "the batch composed more than one answer body — all tabs travel "
            "in ONE mail, or the agent runs a turn per tab")
    contract("one composed answer body carries every tab, labelled by header",
             _one_body_labelled_by_header)

    print("\n§4  the rules a batch must not escape")

    def _headless_denies_the_batch():
        org = org2()
        org.d["headless"] = True
        try:
            org.ask_user("boss", questions=QS)
            raise AssertionError("a headless org parked a batch")
        except LedgerError as e:
            assert "headless" in str(e).lower(), e
        assert not open_asks(org)
    contract("a headless org denies a batch exactly as it denies one question",
             _headless_denies_the_batch)

    def _routing_carries_every_question():
        # a deep agent with no user audience has its ask ROUTED to its superior
        # as mail (the auto-bridge). That mail must carry the whole batch.
        org = org2()
        r = org.ask_user("kid", questions=QS)
        assert r.get("routed") == "boss", r
        body = org.d["mail"]["boss"][-1]["body"]
        for q in QS:
            assert q["question"] in body, (
                f"the routed mail dropped {q['header']!r} — a batch routed to "
                f"a superior must arrive whole, or the superior answers a "
                f"question they cannot see")
    contract("a routed batch reaches the superior with every question intact",
             _routing_carries_every_question)

    def _tree_payload_counts_the_card_once():
        org = org2()
        org.ask_user("boss", questions=QS)
        t = org.tree()
        assert t["asks_open"] == 1, (
            f"asks_open counted {t['asks_open']} for one batch — the badge "
            f"counts CARDS, not questions")
        node = next(n for n in t["roots"] if n["id"] == "boss")
        assert node["ask"] is not None
        assert len(node["ask"].get("questions") or []) == 3
    contract("the tree payload counts one card and carries its questions",
             _tree_payload_counts_the_card_once)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n✗ {label}\n{tb}")
        print(f"batched-asks: {PASS} passed · {len(FAIL)} FAILED"
              + (f" · {len(PENDING)} pending" if PENDING else ""))
        return 1
    if PENDING:
        print(f"⧗ {len(PENDING)} checks PENDING — FR-04 is not built yet. They "
              f"run automatically once orgtree_ask takes `questions` and "
              f"Org.ask_user accepts a batch:")
        for label in PENDING:
            print(f"    ⧗ {label}")
        print()
    print(f"batched-asks: all {PASS} checks passed"
          + (f" · {len(PENDING)} pending the build" if PENDING else ""))
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
