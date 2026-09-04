"""gpt-reserve availability: detected from what MOVES, not from the login.

    python backend/tests/test_gpt_reserve_detection.py      (no pytest)

THE REPORT, IN TWO HALVES (user, 2026-09-02/03):

  "i had access to gpt-reserve in my codex limit earlier today and could use
   it via the codex cli. however i no longer have access. yet the reserve
   token still appears."
  "i suddenly have access to gpt reserve again, but the reserve token is not
   showing anymore."

Both halves are the same bug — a signal that does not track the grant — and a
fix for one that does not answer the other is not a fix.

d7b98c7 gated the tier on the Codex login KIND (a ChatGPT subscription, not an
API key).  Necessary; nowhere near sufficient, because the login did not change
across the outage.  Measured on the reporting machine from the CLI's own
session rollouts:

  16:06-16:38Z  model=gpt-reserve, a weekly window of its OWN, 2% -> 8%,
                resetting Sep 9 — while the account's plan window sat spent at
                100% resetting Sep 7.
  19:15Z        model=gpt-reserve, limit_id "premium", no windows at all,
                credits balance "0" -> the turn failed `usage_limit_exceeded`.

⚠ AND THE MODEL REGISTRY IS NOT THE SIGNAL EITHER.  The next pass read
`visibility` out of the CLI's `models_cache.json` and refused on `"hide"`.
That shipped, the grant came back, and the token stayed hidden — the second
half of the report.  Measured 2026-09-03T00:03:43Z from a registry file
written seconds earlier, while the account held a live 8%-used reserve window
and the user could use the model from the CLI:

    offered: gpt-5.6-sol, gpt-5.6-terra, gpt-5.6-luna, gpt-5.5, gpt-5.4,
             gpt-5.4-mini, gpt-5.3-codex-spark
    hidden : gpt-reserve, codex-auto-review

`hide` means "not offered in the model PICKER".  `codex-auto-review` carries it
permanently, and gpt-reserve is a routed pool that is never picked — so it is
ALWAYS hidden and that check could only ever hide the tier forever.  It had
been inferred from one observation of the withdrawn state and never compared
against a granted one.  §4 keeps it from coming back.

WHAT ACTUALLY MOVES is the account's rate-limit board: a granted pool gets a
bucket of its OWN there, named after the model, and a withdrawn one has none.

    §1  the grant signal — presence of a window, in both directions
    §2  the usage board is NOT a hire gate (ruling)
    §3  the rule both doors ask
    §4  controls: what would make the above vacuous
"""

import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_ROOT = tempfile.mkdtemp(prefix="orgtree-reserve-detect-")
_HOME = os.path.join(_ROOT, "codex-home")
os.makedirs(_HOME)
# the dead-hub invariant every private data root in this directory carries:
# importing the net daemon must never register against the operator's roster
with open(os.path.join(_ROOT, "defaults.json"), "w", encoding="utf-8") as _f:
    json.dump({"net_hub_address": "http://127.0.0.1:9"}, _f)
with open(os.path.join(_HOME, "auth.json"), "w", encoding="utf-8") as _f:
    json.dump({"tokens": {"id_token": "eyJh.e30.sig"}}, _f)
os.environ["ORGTREE_DATA"] = _ROOT
os.environ["CODEX_HOME"] = _HOME
os.environ["ORGTREE_CODEX"] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fakecodex.py")

from orgtree import codex_limits, codexrun, providers    # noqa: E402

PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


# ------------------------------------------------------------- the fixture
#: the reserve bucket's own reset, 2026-09-09T13:26:53Z — the SAME window the
#: 16:06Z rollout billed to, three hours before the grant lapsed
RESERVE_RESET = 1788960413


def raw_board(*, reserve, percent=100, reached=True, reserve_percent=8):
    """The shape `account/rateLimits/read` returns, verbatim as measured on
    the reporting machine 2026-09-03T00:03Z.

    `reserve` is the whole question this file is about: a granted pool has a
    bucket here and a withdrawn one has none.  Note the bucket's id is
    `base_model_inference` while its `limitName` is the model — hard-coding
    the id would be reading OpenAI's internals; the NAME is the contract.
    """
    codex = {
        "limitId": "codex", "limitName": None,
        "primary": {"usedPercent": percent, "windowDurationMins": 10080,
                    "resetsAt": 1788764643},
        "secondary": None,
        "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
        "individualLimit": None, "spendControlReached": False,
        "planType": "prolite",
        "rateLimitReachedType": "rate_limit_reached" if reached else None,
    }
    by_id = {"codex": codex}
    if reserve:
        by_id["base_model_inference"] = {
            "limitId": "base_model_inference", "limitName": "gpt-reserve",
            "primary": {"usedPercent": reserve_percent,
                        "windowDurationMins": 10080,
                        "resetsAt": RESERVE_RESET},
            "secondary": None, "credits": None, "individualLimit": None,
            "spendControlReached": None, "planType": "prolite",
            "rateLimitReachedType":
                "rate_limit_reached" if reserve_percent >= 100 else None,
        }
    return {"rateLimits": codex, "rateLimitsByLimitId": by_id}


def set_board(*, age=0.0, **kw):
    """Publish a board into the usage cache, `age` seconds old."""
    codex_limits._cache.update(at=time.time() - age,
                               data=codex_limits._normalize(raw_board(**kw)))


class NoAppServer:
    """Nothing below may be answered by SPAWNING a codex.

    Every check here is about a board that is already in hand; if `grants`
    reached for a process the assertions would still pass on a developer
    machine with a real CLI and mean nothing on CI.
    """

    def __enter__(self):
        self._c = codexrun.AppServerClient

        def refuse(*a, **k):
            raise AssertionError("the reserve check spawned an app-server")

        codexrun.AppServerClient = refuse
        return self

    def __exit__(self, *a):
        codexrun.AppServerClient = self._c


#: The Codex tiers whose availability does not depend on live evidence.
#:
#: ⚠ THIS SPLIT IS LOAD-BEARING AND IT IS NEW. `c5049fa` (2026-09-04, "gate
#: rollout models on live Codex inventory") made rollout tiers CONDITIONAL:
#: `provider_hire_gate` re-queries account-scoped inventory for them with
#: `force=True`, which reaches for an app-server. The spent-window checks
#: below used to loop `providers.CODEX_TIERS` wholesale, so the day `astra`
#: became conditional they started failing on a gate that has nothing to do
#: with a usage window — and because `check()` in this file does not catch,
#: that abort took the other 11 checks with it.
#:
#: The guard was not wrong and neither was the gate: `NoAppServer` caught a
#: real change. The narrowing keeps the spent-window property honest, and the
#: conditional gate gets its OWN check below rather than being routed around.
_ALWAYS_CODEX_TIERS = sorted(
    set(providers.CODEX_TIERS) - set(providers.CONDITIONAL_CODEX_TIERS))


def _always_and_conditional_are_both_non_empty():
    """ANTI-VACUITY for the two checks below. If every Codex tier became
    conditional the spent-window loop would iterate nothing and pass while
    proving nothing; if none were, the conditional check would have no
    subject. Assert the split has members on both sides before leaning on
    either."""
    assert _ALWAYS_CODEX_TIERS, (
        "every Codex tier is now CONDITIONAL — the spent-window check below "
        "would loop over nothing and pass vacuously")
    assert providers.CONDITIONAL_CODEX_TIERS, (
        "no Codex tier is conditional any more — the companion check below "
        "has no subject, so delete it rather than let it pass empty")


# ------------------------------------------------------- §1 the grant signal
print("\n§1  the grant signal — a window of its own, in BOTH directions")


def the_fixture_really_carries_the_two_states():
    """ANTI-VACUITY FIRST. If both fixtures normalized the same way, every
    direction test below would pass for the wrong reason."""
    with_it = codex_limits._normalize(raw_board(reserve=True))
    without = codex_limits._normalize(raw_board(reserve=False))
    eq([w["model"] for w in with_it["limits"] if w["model"]], ["gpt-reserve"],
       "the granted board names the model")
    eq([w["model"] for w in without["limits"] if w["model"]], [],
       "the withdrawn board names nothing")


def the_model_name_is_the_contract_not_the_limit_id():
    """`limitName` is what `_normalize` carries through as `model`; the id it
    arrives under is OpenAI's internal `base_model_inference`. A fix that
    matched on the id would break the day they rename it — silently, and in
    the hiding direction."""
    import inspect
    data = codex_limits._normalize(raw_board(reserve=True))
    win = next(w for w in data["limits"] if w["model"] == "gpt-reserve")
    eq(win["group"], "base_model_inference", "…which is NOT what we match on")
    src = inspect.getsource(codex_limits.grants)
    assert "base_model_inference" not in src, \
        "grants() is matching OpenAI's internal limit id instead of the name"


def a_granted_pool_is_seen():
    set_board(reserve=True)
    with NoAppServer():
        eq(codex_limits.grants("gpt-reserve"), True, "granted")


def a_withdrawn_pool_is_seen():
    set_board(reserve=False)
    with NoAppServer():
        eq(codex_limits.grants("gpt-reserve"), False, "withdrawn")


def a_model_with_no_window_of_its_own_is_not_granted():
    """The leg that keeps this from being vacuous the other way: sol bills to
    the account's own bucket and has no named window, so a `grants` that
    answered True for everything would be caught here. Asking it about sol is
    not how that tier is gated — §4 pins that the reserve signal stays put."""
    set_board(reserve=True)
    with NoAppServer():
        eq(codex_limits.grants("gpt-5.6-sol"), False, "no window of its own")


def presence_not_fullness():
    """THE RULING, INSIDE THE SIGNAL. A granted-but-spent reserve window still
    answers True: a spent window prepares an agent rather than refusing one."""
    set_board(reserve=True, reserve_percent=100)
    with NoAppServer():
        eq(codex_limits.grants("gpt-reserve"), True, "spent but granted")


def an_unreadable_board_is_unknown_not_no():
    """The rule that keeps a detection bug from becoming an outage: a machine
    that could not be asked knows NOTHING, and `None` is not `False`."""
    codex_limits.invalidate()
    saved = codex_limits.fetch
    codex_limits.fetch = lambda force=False: {"available": False}
    try:
        eq(codex_limits.grants("gpt-reserve"), None, "no board")
    finally:
        codex_limits.fetch = saved


def a_stale_board_is_unknown_too():
    """Evidence older than `MAX_EVIDENCE_AGE` is not evidence. Hiding a tier
    on a board from an hour ago is exactly the bug this file exists for."""
    set_board(reserve=False, age=codex_limits.MAX_EVIDENCE_AGE + 60)
    saved = codex_limits.fetch
    codex_limits.fetch = lambda force=False: {"available": True}
    try:
        with NoAppServer():
            eq(codex_limits.grants("gpt-reserve"), None, "stale board")
    finally:
        codex_limits.fetch = saved
        codex_limits.invalidate()


check("the granted / withdrawn fixtures really differ (anti-vacuity)",
      the_fixture_really_carries_the_two_states)
check("the MODEL NAME is matched, never OpenAI's internal limit id",
      the_model_name_is_the_contract_not_the_limit_id)
check("a granted reserve pool has a window of its own, and is seen",
      a_granted_pool_is_seen)
check("a withdrawn one has none, and that is seen too",
      a_withdrawn_pool_is_seen)
check("a model with no window of its own is not 'granted' (the other leg)",
      a_model_with_no_window_of_its_own_is_not_granted)
check("PRESENCE, not fullness — a spent reserve window is still granted",
      presence_not_fullness)
check("a board that could not be read is UNKNOWN, not 'no'",
      an_unreadable_board_is_unknown_not_no)
check("…and so is a stale one", a_stale_board_is_unknown_too)


# ------------------------------- §2 the usage board is NOT a hire gate
print("\n§2  the usage board — deliberately not a hire gate")


def the_board_really_does_say_spent():
    """ANTI-VACUITY. Everything below asserts that a spent account changes
    nothing; if the fixture were not actually spent it would all pass for the
    wrong reason."""
    data = codex_limits._normalize(raw_board(reserve=True, percent=100,
                                             reached=True))
    account = [x for x in data["limits"] if x["group"] == "codex"]
    eq([x["percent"] for x in account], [100.0], "the window is full")
    eq([x["is_active"] for x in account], [True], "and flagged rate-limited")


def a_spent_window_withholds_no_codex_tier():
    """USER RULING 2026-09-02: "i should still be able to hire agents if my
    usage window is up; i would like the ability to prepare an agent with a
    charter, even if i cant run it actively."

    65273fa refused every Codex hire on exactly this board. Hiring names an
    agent, writes its charter and fixes its scope — none of that spends a
    token, and a window that resets on a schedule is a reason to PREPARE work,
    not to be locked out of preparing it. Capacity is the TURN's question, and
    the Codex CLI already answers it loudly there."""
    set_board(reserve=True, percent=100, reached=True)
    try:
        with NoAppServer():
            pay = providers.providers_payload(
                {"installed": True, "connected": True})
    finally:
        codex_limits.invalidate()
    cx = next(x for x in pay["providers"] if x["id"] == "openai")
    eq(cx["hire_enabled"], True, "the family stays hireable while spent")
    eq(cx["reason"], None, "and carries no apology for it")
    eq(cx["reserve_hire_enabled"], True,
       "reserve is prepared like any other tier — its own gate is the GRANT")


def a_spent_window_refuses_no_hire_at_the_door():
    """The other door. The chip not stopping a click is not the same as the
    server accepting one, and 65273fa gated both.

    ⚠ ALWAYS-AVAILABLE TIERS ONLY — see `_ALWAYS_CODEX_TIERS`. The property
    here is about the SPENT USAGE WINDOW. A conditional tier is refused by a
    different gate for a different reason, and that reason is pinned by
    `a_conditional_tier_is_refused_for_inventory_not_the_window` rather than
    hidden by widening this loop back out.
    """
    from orgtree import api
    from orgtree.ledger import Org
    set_board(reserve=True, percent=100, reached=True)
    org = Org.create("ruling")
    org.d["max_top_grant"] = 200
    try:
        with NoAppServer():
            for tier in _ALWAYS_CODEX_TIERS:
                api.provider_hire_gate(org, tier)
    finally:
        codex_limits.invalidate()


def a_conditional_tier_is_refused_for_inventory_not_the_window():
    """The companion, and what makes the narrowing above honest rather than
    convenient.

    A conditional tier IS refused with no app-server available — its gate
    re-queries live inventory with `force=True` and missing evidence refuses.
    What this pins is the REASON: it must name the conditional-availability
    gate and must NOT cite the usage window. If it ever starts citing the
    window, the two gates have been conflated and the user ruling this whole
    section exists for — hiring PREPARES an agent, a spent window does not
    refuse one — has been quietly undone for rollout tiers.
    """
    from orgtree import api
    from orgtree.ledger import LedgerError, Org
    set_board(reserve=True, percent=100, reached=True)
    org = Org.create("conditional")
    org.d["max_top_grant"] = 200
    try:
        with NoAppServer():
            for tier in sorted(providers.CONDITIONAL_CODEX_TIERS):
                try:
                    api.provider_hire_gate(org, tier)
                except LedgerError as e:
                    msg = str(e)
                    assert "conditional Codex tier" in msg, (
                        f"{tier} was refused, but not by the conditional "
                        f"gate: {msg}")
                    for window_word in ("usage window", "spent", "limit "
                                        "reached"):
                        assert window_word not in msg.lower(), (
                            f"{tier}'s refusal cites the usage window "
                            f"({window_word!r}) — the availability gate and "
                            f"the spent-window ruling have been conflated: "
                            f"{msg}")
                else:
                    raise AssertionError(
                        f"{tier} was ADMITTED with no app-server available. "
                        f"Its gate re-queries inventory with force=True, so "
                        f"either evidence is being taken from a cache it "
                        f"should not trust, or this check no longer "
                        f"reproduces the condition it was written for.")
    finally:
        codex_limits.invalidate()


check("the spent-account fixture is really spent (anti-vacuity)",
      the_board_really_does_say_spent)
check("a spent usage window withholds no Codex tier — user ruling: hiring "
      "PREPARES an agent", a_spent_window_withholds_no_codex_tier)
check("the always/conditional tier split has members both sides "
      "(anti-vacuity)", _always_and_conditional_are_both_non_empty)
check("…and refuses no ALWAYS-available hire at the door either",
      a_spent_window_refuses_no_hire_at_the_door)
check("a CONDITIONAL tier is refused for inventory, never for the "
      "spent window",
      a_conditional_tier_is_refused_for_inventory_not_the_window)


# ------------------------------------------------------------- §3 the rule
print("\n§3  the rule both the chip and the hire gate ask")


def chatgpt(kind="chatgpt"):
    return {"installed": True, "connected": True, "kind": kind}


def api_key_never_holds_reserve():
    set_board(reserve=True)
    with NoAppServer():
        got = providers.reserve_availability(chatgpt("api-key"))
    eq(got["enabled"], False, "api-key")
    eq(got["evidence"], "login-kind", "which signal refused")


def the_grant_going_away_is_seen():
    """THE FIRST HALF OF THE REPORT. Login unchanged; the tier goes dark."""
    set_board(reserve=False)
    with NoAppServer():
        got = providers.reserve_availability(chatgpt())
    eq(got["enabled"], False, "withdrawn grant")
    eq(got["evidence"], "no-reserve-window", "which signal refused")
    assert "comes back on its own" in (got["reason"] or ""), got


def the_grant_coming_back_is_seen():
    """THE SECOND HALF OF THE REPORT, and the reason this file was reopened:
    "i suddenly have access to gpt reserve again, but the reserve token is not
    showing anymore."

    The registry check could go dark but never come back. This runs BOTH
    transitions in one process, with no reimport and no restart, because "it
    works after a redeploy" is not a fix for a signal that has to breathe."""
    set_board(reserve=False)
    with NoAppServer():
        gone = providers.reserve_availability(chatgpt())
        # …the grant returns; the next board carries the window again
        set_board(reserve=True)
        back = providers.reserve_availability(chatgpt())
    eq(gone["enabled"], False, "dark while withdrawn")
    eq((back["enabled"], back["reason"]), (True, None),
       "and LIT again on the next board, same process")


def the_payload_un_hides_too():
    """The rule breathing is not the same as the document breathing — the
    payload is what the UI reads, and it must cache nothing of its own."""
    try:
        with NoAppServer():
            set_board(reserve=False)
            dark = providers.providers_payload(
                {"installed": True, "connected": True})
            set_board(reserve=True)
            lit = providers.providers_payload(
                {"installed": True, "connected": True})
    finally:
        codex_limits.invalidate()
    dark_cx = next(x for x in dark["providers"] if x["id"] == "openai")
    lit_cx = next(x for x in lit["providers"] if x["id"] == "openai")
    eq(dark_cx["reserve_hire_enabled"], False, "hidden while withdrawn")
    eq(lit_cx["reserve_hire_enabled"], True, "back on the very next payload")
    eq(lit_cx["reserve_reason"], None, "with the tooltip cleared")


def an_unknown_board_offers_rather_than_hides():
    """The direction this whole file gets wrong when it gets it wrong."""
    codex_limits.invalidate()
    saved = codex_limits.fetch
    codex_limits.fetch = lambda force=False: {"available": False}
    try:
        got = providers.reserve_availability(chatgpt())
    finally:
        codex_limits.fetch = saved
    eq((got["enabled"], got["reason"]), (True, None),
       "unknown offers — the CLI refuses the turn loudly on its own")


def a_live_grant_passes():
    """THE LEG THAT MUST HOLD."""
    set_board(reserve=True)
    with NoAppServer():
        got = providers.reserve_availability(chatgpt())
    eq((got["enabled"], got["reason"]), (True, None), "live grant")


check("an api-key login can never hold reserve capacity",
      api_key_never_holds_reserve)
check("a WITHDRAWN grant is seen, on an unchanged ChatGPT login",
      the_grant_going_away_is_seen)
check("…AND A RETURNING ONE IS SEEN TOO, in the same process (the reopen)",
      the_grant_coming_back_is_seen)
check("the /api/providers document un-hides on the very next poll",
      the_payload_un_hides_too)
check("an unknown board OFFERS rather than hides",
      an_unknown_board_offers_rather_than_hides)
check("…and a live grant passes (the leg that must hold)", a_live_grant_passes)


# ---------------------------------------------------------- §4 the controls
print("\n§4  controls — what would make the above vacuous")


def the_gate_and_the_chip_are_one_function():
    """d7b98c7 wrote the reserve rule twice — once in `provider_hire_gate`,
    once inline in `providers_payload` — so the chip and the refusal could
    drift. Both now read `reserve_availability`, and this is the check that
    notices if a third copy appears."""
    import inspect
    from orgtree import api
    gate = inspect.getsource(api.provider_hire_gate)
    assert "reserve_availability" in gate, gate[-900:]
    assert 'kind") != "chatgpt"' not in gate.split("headless")[-1], (
        "the reserve branch is asking the login directly again")
    payload = inspect.getsource(providers.providers_payload)
    assert "reserve_availability" in payload, payload


def the_reserve_tier_name_lives_once():
    eq(providers.RESERVE_TIER, "gpt-reserve", "the tier this is all about")
    assert providers.RESERVE_TIER in providers.CODEX_TIERS, providers.CODEX_TIERS


def the_ruling_is_written_where_it_would_be_undone():
    """The 2026-09-02 reversal is a JUDGEMENT, not a bug fix, so the next
    reader has to find it where they would be tempted to re-add the gate —
    beside `RESERVE_TIER`, which both doors already reference. A test alone
    would only tell them they broke something, not why it was chosen."""
    import inspect
    src = inspect.getsource(providers)
    assert "do not re-add `codex_capacity`" in src, \
        "the ruling's note has gone missing from providers.py"
    assert not hasattr(providers, "codex_capacity"), \
        "the capacity gate is back — see the note above RESERVE_TIER"
    assert not hasattr(codex_limits, "exhausted"), \
        "codex_limits.exhausted is back and nothing should be asking it"


def the_model_registry_signal_stays_dead():
    """THE SECOND BUG, PINNED. `models_cache.json` visibility looked like the
    grant signal and is not one: gpt-reserve is `visibility: "hide"` even
    while granted, because `hide` means "not in the model picker" — so a gate
    built on it can go dark and never come back. Deleted, and it does not get
    to return as "one more cheap check first"."""
    import importlib
    import inspect
    try:
        importlib.import_module("orgtree.codex_models")
    except ImportError:
        pass
    else:
        raise AssertionError("orgtree.codex_models is back — read this file's "
                             "header before wiring it to the reserve gate")
    src = inspect.getsource(providers.reserve_availability)
    assert "codex_models" not in src, "the registry check is back"
    body = src.split('"""')[-1]
    assert "visibility" not in body, \
        "the registry check is back under another name"
    # …and the WHY survives where the next reader will actually look
    assert "THE MODEL REGISTRY IS NOT THAT SIGNAL" in \
        (providers.reserve_availability.__doc__ or ""), \
        "the note explaining why has gone missing"


def the_reserve_signal_never_touches_the_other_three():
    """Anti-vacuity for the whole file: the RESERVE-specific signal may not
    take luna/terra/sol away — the user's report was explicitly that those
    three kept working while gpt-reserve did not."""
    set_board(reserve=False)
    try:
        with NoAppServer():
            pay = providers.providers_payload(
                {"installed": True, "connected": True})
    finally:
        codex_limits.invalidate()
    cx = next(x for x in pay["providers"] if x["id"] == "openai")
    eq(cx["hire_enabled"], True, "the family stays hireable")
    eq(cx["reason"], None, "…with nothing to apologise for")
    eq(cx["reserve_hire_enabled"], False, "only reserve goes dark")


check("one implementation, asked by both doors",
      the_gate_and_the_chip_are_one_function)
check("the tier name is a constant, not a literal in two files",
      the_reserve_tier_name_lives_once)
check("the reserve-specific signal never leaks onto luna/terra/sol",
      the_reserve_signal_never_touches_the_other_three)
check("a spent window is not a hire gate, and the code says so out loud",
      the_ruling_is_written_where_it_would_be_undone)
check("the disproven model-registry signal stays dead, with its reason",
      the_model_registry_signal_stays_dead)


print(f"\nPASS — gpt-reserve detection, {PASS} checks")
