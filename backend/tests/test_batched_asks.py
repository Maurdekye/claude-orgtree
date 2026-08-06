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


GAPS: list[tuple[str, str, str]] = []


def fixture(ok, msg) -> None:
    """A PRECONDITION inside a gap body — raised as a RuntimeError so `gap`
    below re-reports it as a broken check instead of swallowing it as the
    finding.

    ⚠ Learned twice in one day (2026-08-06). A gap body's whole contract is
    "this assert fails", so an assert that sets the fixture up is
    indistinguishable from the assert that measures the defect: a finding can
    be reported for a reason that has nothing to do with it. It happened here
    — a credit request for 8 against a grant of 20 takes the ≤-current no-op
    branch (motto A3), so no request existed, and the gap fired on its own
    scaffolding while the defect it named was real but unexercised. Use
    `fixture(...)` for every setup precondition in a gap body; keep bare
    `assert` for the property under test."""
    if not ok:
        raise RuntimeError(f"fixture: {msg}")


def gap(label, why, fn) -> None:
    """SHOULD hold, currently does not — inverted so the suite stays green and
    turns RED the day it is fixed. (The gate above predates the build; these
    are findings against the shipped code.)

    ⚠ Set preconditions with `fixture(...)`, never a bare assert — see there."""
    global PASS
    try:
        fn()
    except AssertionError as e:
        GAPS.append((label, why, str(e).split("\n")[0][:300]))
        print(f"  ⚑ GAP    {label}")
        return
    except Exception:                                            # noqa: BLE001
        FAIL.append((label + " (gap check errored)", traceback.format_exc()))
        print(f"  FAIL     {label} — the gap check itself broke")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}  ← FIXED: promote this out of gap()")


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



# ═══════════════════════════════════════════════════ the built feature, attacked
def sec_attack() -> None:
    """The gate above asked whether the batch behaves like ONE ask. This asks
    what the shipped implementation does with input it did not expect — the
    positional answer array is the whole coupling between a card the user sees
    and questions the server holds, and nothing ties the two together."""
    print("\n§attack  the shipped batch — positional answers and amends")

    def _amend_then_stale_answer_is_refused():
        # was a gap: an amend kept the id and replaced `questions` in place
        # while the answer stayed positional, so a same-length amend between
        # render and submit silently attributed every answer to a question
        # the user never saw. Fixed 2026-08-05 with the prescribed CAS: the
        # entry carries `rev` (bumped on amend), the card echoes it, and
        # ask_answer refuses a mismatched — or, on an amended card, a
        # missing — stamp so the UI re-renders instead of lying.
        org = org2()
        org.ask_user("boss", questions=QS)                 # 3 tabs, rev 1
        a0 = open_asks(org)[0]
        aid, rev0 = a0["id"], a0.get("rev")
        assert rev0 == 1, a0
        org.ask_user("boss", questions=[
            {"question": "deploy tonight?", "header": "Deploy"},
            {"question": "notify the on-call?", "header": "Oncall"},
            {"question": "roll back at what error rate?", "header": "Budget"}])
        assert open_asks(org)[0]["id"] == aid, "fixture: the amend re-used the id"
        assert open_asks(org)[0]["rev"] == 2, "the amend must bump rev"
        for stale in ({"rev": rev0}, {}):     # echoed-stale AND unstamped
            try:
                org.ask_answer(aid, selected=["sqlite", "flag", "alice"],
                               **stale)
                raise AssertionError(
                    f"a stale submission ({stale or 'no stamp'}) was "
                    f"accepted against the amended card")
            except LedgerError as e:
                assert "re-read" in str(e), e
        # the honest path still works: answer against the CURRENT rev
        r = org.ask_answer(aid, selected=["yes", "yes", "5%"], rev=2)
        assert "deploy tonight?" in r["body"], r["body"]
    check("amend · a stale answer is refused (rev CAS); the current card "
          "still answers", _amend_then_stale_answer_is_refused)

    def _withdraw_nulls_the_whole_batch():
        # REWRITTEN 2026-08-06 (user ruling): the wake-void is RETIRED — a
        # batch now dies only manually. The whole-card concern the old void
        # check pinned still applies to the withdraw path: one card, one
        # fate, never a tab at a time.
        org = org2()
        org.ask_user("boss", questions=QS)
        assert not hasattr(org, "void_open_asks"), \
            "the wake-void is retired — nothing voids a card on turn start"
        r = org.withdraw_ask("boss")
        assert r.get("withdrawn"), r
        a = org.d["asks"][0]
        assert a["status"] == "withdrawn", a["status"]
        assert len(a["questions"]) == 3, \
            "the withdrawn card must still carry every tab for the record"
    check("withdraw · the agent's withdraw nulls the WHOLE batch, one fate "
          "for all tabs", _withdraw_nulls_the_whole_batch)

    def _retiring_the_asker_takes_its_card_with_it():
        """The ruling of 2026-08-06 names every way a request may die: the
        user answers, dismisses or denies it; the agent poses a NEWER one; the
        agent withdraws it. Retirement is not on that list, and it is the one
        event that removes the party who could withdraw.

        ⚠ NOT a regression from that ruling — measured against the old
        wake-void too, this was already true: the void fired at TURN START,
        and an archived node never takes another turn. The ruling makes it
        legible rather than causing it, by turning 'nothing sweeps a request'
        from an accident into a stated design property. Retirement is where
        that property has no hand left to act."""
        org = org2()
        org.ask_user("boss", questions=QS)                 # a 3-tab card
        aid = open_asks(org)[0]["id"]
        org.retire(USER, "boss")
        assert org.node("boss")["state"] == "archived", "fixture: not archived"
        t = org.tree()
        assert t["asks_open"] == 0, (
            f"the header ask-bell still glows for {t['asks_open']} request(s) "
            f"belonging to an agent that no longer exists; the card sits in "
            f"the user's inbox addressed to an archived node. It is CLEARABLE "
            f"— ask_dismiss still works — so this is a wart, not a trap: the "
            f"user must dismiss a question on behalf of an agent that cannot "
            f"be asking anything any more")
    # ← FIXED (promoted out of gap(), 2026-08-06, same day): retire and each
    # node of a dissolved subtree now run `_moot_asks` — a distinct `moot`
    # status with reason "the asking agent was retired/dissolved …" rather
    # than the suggested withdraw_ask, because "withdrawn by the asking
    # agent" would be a false record for an agent that never acted; `moot`
    # is the status credit-approval already uses for a no-longer-live node.
    # `delete` additionally sweeps d["asks"] like it swept credit_requests
    # (the re-minted-namesake hazard, the sweep's fifth site). ask_answer
    # deliberately still accepts late answers, per this finding's own note.
    check("retire · archiving an agent takes its open request with it",
          _retiring_the_asker_takes_its_card_with_it)

    def _the_moot_credit_request_keeps_its_history_too():
        """The other half of the same event. Retiring an agent moots BOTH its
        active kinds (abd9cf3), but the two leave different traces: a mooted
        question stays in the payload as a nulled card explaining itself,
        while a mooted credit request is filtered out of `asks` (the
        `status not in ("withdrawn", "moot")` clause) and out of
        `credit_requests` (pending only), so it vanishes with no record.

        The filter predates the fix — `moot` was already the credit-approve
        fallback's status — but retirement is a far more common act than that
        fallback, so the same line now hides a case a user performs by hand
        and may well want explained. This suite's own neighbouring check pins
        the opposite rule for questions ("the superseded question stays in
        the nulled history")."""
        org = org2()
        # ⚠ fixture repair (implementer, on promotion): the original asked
        # for 8 against a grant of 20 — the ≤-current no-op branch, so no
        # request ever existed and the gap fired on the fixture, not the
        # filter. A request must EXCEED the current grant to be pending.
        org.request_credits("boss", 30, "need more room")
        org.retire(USER, "boss")
        doc = [r["status"] for r in org.d.get("credit_requests", [])]
        fixture(doc == ["moot"], f"expected one mooted request, got {doc}")
        t = org.tree()
        shown = [a for a in t["asks"] if a.get("kind") == "credit"]
        assert shown, (
            "the mooted credit request is in the doc but reaches no payload: "
            f"asks={[(a.get('kind'), a['status']) for a in t['asks']]}, "
            f"credit_requests={[r['status'] for r in t['credit_requests']]}. "
            "Retire an agent holding a QUESTION and the user gets a nulled "
            "card saying the asker was retired; retire one holding a CREDIT "
            "REQUEST and the card just disappears — same event, same ruling, "
            "two different histories")
    # ← FIXED (promoted out of gap(), 2026-08-06, same day): both tree()'s
    # `asks` and node_ask's pool now hide ONLY `withdrawn` — a mooted credit
    # request renders as a nulled card like its question twin, exactly the
    # smallest-fix shape the finding prescribed. (Fixture repaired on
    # promotion: the original 8-against-20 request was the no-op branch and
    # never created the row the check meant to trace.)
    check("retire · a mooted CREDIT request keeps its history like a mooted "
          "question does",
        _the_moot_credit_request_keeps_its_history_too)

    def _extra_answers_are_not_silently_dropped():
        org = org2()
        org.ask_user("boss", questions=QS[:2])
        aid = open_asks(org)[0]["id"]
        try:
            org.ask_answer(aid, selected=["a", "b", "c", "d"])
        except LedgerError:
            return                                   # refused — the safe shape
        raise AssertionError(
            "four answers were accepted for a two-tab card; the extras were "
            "truncated by selected[:len(qs)] with no complaint")
    # was a gap: `per_tab[:len(qs)]` dropped the tail silently while the
    # other direction errored. Fixed 2026-08-05: over-length refuses too.
    check("answer · more answers than tabs is refused rather than truncated",
          _extra_answers_are_not_silently_dropped)

    def _wire_type_still_refuses_a_bare_string():
        """CLEAN, and pinned so it stays that way. list(selected) over a bare
        string would split it into CHARACTERS — 'yes' answering a 3-tab card
        as y/e/s, accepted and completely wrong. The only thing standing
        between the ledger and that is the endpoint's pydantic type."""
        from orgtree import api
        ann = api.AskAnswer.model_fields["selected"].annotation
        assert "list" in str(ann), ann
        assert "str]" in str(ann).replace(" ", ""), ann
    check("wire · DRIFT GUARD: /asks/{id}/answer types `selected` as a LIST "
          "(a bare string would be split into per-tab characters by "
          "list(selected) and accepted)", _wire_type_still_refuses_a_bare_string)

    def _multi_tab_keeps_its_shape():
        org = org2()
        org.ask_user("boss", questions=[
            {"question": "which tiers?", "multi": True,
             "options": ["haiku", "opus"]},
            {"question": "when?"}])
        aid = open_asks(org)[0]["id"]
        r = org.ask_answer(aid, selected=[["haiku", "opus"], "tonight"])
        qs = next(a for a in org.d["asks"] if a["id"] == aid)["questions"]
        assert qs[0]["answer"] == ["haiku", "opus"], qs[0]
        assert qs[1]["answer"] == "tonight", qs[1]
        assert "haiku · opus" in r["body"], r["body"]
    check("answer · a multi tab keeps its list and a single tab its string, "
          "per question, in the durable record and the mail", _multi_tab_keeps_its_shape)

    def _flattened_selection_is_characterised():
        org = org2()
        org.ask_user("boss", questions=[{"question": "a?"}, {"question": "b?"}])
        aid = open_asks(org)[0]["id"]
        org.ask_answer(aid, selected=["same", "same"])
        a = next(x for x in org.d["asks"] if x["id"] == aid)
        assert a["answer"]["selected"] == ["same", "same"], a["answer"]
    check("answer · characterised: the entry's top-level `selected` is a FLAT "
          "list across tabs (per-tab attribution lives on questions[i].answer "
          "— any future reader of the flat list must know that)",
          _flattened_selection_is_characterised)


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

    def _wake_leaves_the_batch_standing():
        # REWRITTEN 2026-08-06 (user ruling — INVERTS the old contract): the
        # wake-void is retired, so the exact sequence that used to kill the
        # card (other mail arriving, a turn starting) now leaves it OPEN and
        # answerable. The card dies only by the user's hand or the agent's.
        org = org2()
        org.ask_user("boss", questions=QS)
        aid = open_asks(org)[0]["id"]
        org.post_mail(USER, "boss", "something else entirely")
        left = [a for a in org.d["asks"] if a["id"] == aid][0]
        assert left["status"] == "open", (
            "other mail must NOT touch an open card (ruling 2026-08-06)")
        r = org.ask_answer(aid, selected=["sqlite", "flag", "alice"])
        assert "sqlite" in r["body"], "the surviving card still answers"
    contract("other mail leaves the WHOLE batch standing and answerable",
             _wake_leaves_the_batch_standing)

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


    sec_attack()

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n✗ {label}\n{tb}")
        print(f"batched-asks: {PASS} passed · {len(FAIL)} FAILED"
              + (f" · {len(PENDING)} pending" if PENDING else ""))
        return 1
    if GAPS:
        print("\n⚑ GAPS — measured against the SHIPPED batch:")
        for label, why, detail in GAPS:
            print(f"\n  ⚑ {label}\n    measured: {detail}\n    {why}")
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
