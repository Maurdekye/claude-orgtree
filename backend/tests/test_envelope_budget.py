"""D-223: bounded-staleness suppression of the per-turn dynamic envelope.

The property under test is NOT "fewer bytes". It is "fewer bytes and the agent
still knows everything it could have acted on". Every check here is written
against that second half — what an agent would be missing if the rule fired
wrongly — because the failure mode is silent by construction: a suppressed
block leaves no trace of what it did not say.

Plain deterministic checks; no provider/network calls. Run with:
    python backend/tests/test_envelope_budget.py
"""

from __future__ import annotations

import atexit
import copy
import datetime as dt
import inspect
import os
import shutil
import sys
import tempfile
from typing import Any, Callable

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA = tempfile.mkdtemp(prefix="orgtree-envelope-")
os.environ["ORGTREE_DATA"] = DATA
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from orgtree import (accounts, codex_limits, envelope, limits,  # noqa: E402
                     store, supervisor as S, turnusage)
from orgtree.ledger import USER                                 # noqa: E402

assert DATA != os.path.expanduser("~/orgtree")
S.chatq_register_org = lambda slug: None
S.chatq_deregister_org = lambda slug: None
atexit.register(lambda: shutil.rmtree(DATA, ignore_errors=True))

NOW = 1788253200.0
PASS = FAIL = 0


def iso(epoch: float) -> str:
    return (dt.datetime.fromtimestamp(epoch, dt.timezone.utc)
            .isoformat().replace("+00:00", "Z"))


def check(label: str, fn: Callable[[], None]) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")
    except Exception as e:
        FAIL += 1
        print(f"  FAIL    {label}: {e}")
        import traceback
        traceback.print_exc()


def snap(**kw: Any) -> envelope.Snapshot:
    base: dict[str, Any] = {"seq": 1, "dig": "D", "sid": "S", "at": NOW,
                            "occ": 100_000, "turns": 0, "why": "first"}
    base.update(kw)
    return base                                  # type: ignore[return-value]


def fixture(name: str, tier: str = "haiku", vis: str = "full",
            kids: int = 12):
    """A boss with enough reports that the chart span is worth suppressing.

    Deliberately past `_CHART_SUPPRESS_MIN`: a two-person org's chart is below
    the floor and is never suppressed at all, which is correct behaviour but
    tests nothing about suppression.
    """
    org = store.create_org(name)
    org.hire(USER, None, tier, 60, "boss")
    org.node("boss")["scope"]["org_visibility"] = vis
    for kid in [f"kid-{i:02d}" for i in range(kids)]:
        org.hire("boss", "boss", tier, 0, kid, add_dirs=[],
                 tools={"bash": False, "web": False, "edit": False,
                        "subagents": False, "mcp": []},
                 org_visibility="self", charter="a chart row, nothing more")
    org.node("boss")["session_id"] = "sess-A"
    org.node("boss")["occupancy"] = 100_000
    store.save_org(org)
    return org, "boss"


# ── §1 the decision rule ────────────────────────────────────────────────────

def unchanged_state_is_suppressed_and_everything_else_is_not() -> None:
    """The one case that saves anything, and the six that must not."""
    full, why = envelope.decide(snap(), sid="S", dig="D",
                                now=NOW + 60, occ=100_000)
    assert not full and why == "unchanged", why

    # …and every trigger, each of which is an agent that would otherwise be
    # reading a pointer at something it cannot see or should not trust.
    for label, kw, expect in (
        ("no prior at all", {"prior": None}, "first"),
        ("a different session", {"sid": "sess-B"}, "new-session"),
        ("the org moved", {"dig": "DIFFERENT"}, "changed"),
        ("the context shrank", {"occ": 40_000}, "context-shrank"),
        ("60k tokens progressed", {"occ": 160_000}, "token-threshold"),
        ("11 suppressed turns", {"prior": snap(turns=10)}, "turn-threshold"),
        ("15 minutes elapsed", {"now": NOW + 900}, "age-threshold"),
        ("the clock went backwards", {"now": NOW - 1}, "clock-moved"),
    ):
        args: dict[str, Any] = {"prior": snap(), "sid": "S", "dig": "D",
                                "now": NOW + 60, "occ": 100_000}
        args.update(kw)
        prior = args.pop("prior")
        full, why = envelope.decide(prior, **args)
        assert full, f"{label} did not force a full block"
        assert why == expect, f"{label}: reported {why!r}, wanted {expect!r}"


def a_snapshot_that_cannot_be_trusted_is_not_repaired() -> None:
    """Malformed persisted state must fail to parse, not be made plausible.

    A half-written record that got "fixed up" into something parseable is the
    worst outcome available here: it suppresses against a snapshot nobody can
    describe. Every one of these must come back None, which `decide` reads as
    "first" and sends the whole block.
    """
    for bad in (None, "", [], {}, {"seq": 1},
                {"seq": 1, "dig": "", "sid": "S", "at": NOW},
                {"seq": 1, "dig": "D", "sid": "", "at": NOW},
                {"seq": -1, "dig": "D", "sid": "S", "at": NOW},
                {"seq": 1, "dig": "D", "sid": "S", "at": 0},
                {"seq": 1, "dig": "D", "sid": "S", "at": float("nan")},
                {"seq": 1, "dig": "D", "sid": "S", "at": float("inf")},
                {"seq": "x", "dig": "D", "sid": "S", "at": NOW},
                {"seq": 1, "dig": "D", "sid": "S", "at": NOW, "turns": -3}):
        node: dict[str, Any] = {"envelope": {envelope.ORG_STATE: bad}}
        assert envelope.read(node, envelope.ORG_STATE) is None, bad
        full, why = envelope.decide(
            envelope.read(node, envelope.ORG_STATE),
            sid="S", dig="D", now=NOW, occ=1)
        assert full and why == "first", (bad, why)
    # A record from before `why` existed still parses — the field is
    # observability, not a correctness input.
    old = {"seq": 2, "dig": "D", "sid": "S", "at": NOW, "occ": 5, "turns": 1}
    assert envelope.read({"envelope": {envelope.USAGE: old}},
                         envelope.USAGE) is not None


def unmeasured_occupancy_disables_only_the_occupancy_rules() -> None:
    """A node that has never completed a turn reports occupancy None.

    Reading that as "the context shrank to zero" would make every such turn a
    full send forever; reading it as "no growth" would disable the token bound
    silently. It disables both occupancy rules and nothing else — the turn and
    age bounds still cap staleness.
    """
    full, why = envelope.decide(snap(occ=0), sid="S", dig="D",
                                now=NOW + 60, occ=0)
    assert not full, why
    full, _ = envelope.decide(snap(occ=0), sid="S", dig="D",
                              now=NOW + 901, occ=0)
    assert full, "age bound stopped applying when occupancy was unknown"
    full, _ = envelope.decide(snap(occ=0, turns=10), sid="S", dig="D",
                              now=NOW + 60, occ=0)
    assert full, "turn bound stopped applying when occupancy was unknown"


def a_suppressed_turn_does_not_move_the_anchor() -> None:
    """Suppression counts a turn against the OLD snapshot; it does not renew
    it. Otherwise `turns`/`at`/`occ` would reset on every suppressed turn and
    the bounds would never fire at all — an unbounded pointer."""
    s = snap(seq=4, turns=2)
    nxt = envelope.advance(s, sid="S", dig="D", now=NOW + 500, occ=150_000,
                           full=False)
    assert nxt["seq"] == 4 and nxt["at"] == NOW and nxt["occ"] == 100_000
    assert nxt["turns"] == 3
    fresh = envelope.advance(s, sid="S", dig="E", now=NOW + 500, occ=150_000,
                             full=True, why="changed")
    assert fresh["seq"] == 5 and fresh["turns"] == 0
    assert fresh["at"] == NOW + 500 and fresh["occ"] == 150_000
    assert fresh["why"] == "changed"


# ── §2 what the rendered blocks promise ─────────────────────────────────────

def the_facts_an_agent_acts_on_are_never_behind_a_pointer() -> None:
    """THE CENTRAL SAFETY CHECK.

    Only the chart may be replaced by a reference. Reports, peers, the credit
    balance, the stale-copy warning and the open-ask reminder are what an agent
    reads without scrolling, and they must survive suppression verbatim.
    """
    org, nid = fixture("zz-env-facts")
    org.ask_user(nid, "which branch?")
    store.save_org(org)
    full = S.org_state_block(org, nid, seq=3)
    supp = S.org_state_block(org, nid, seq=3, chart_ref=3)
    for must in ("Your reports: kid-00,",
                 "Credits: seat", "free ",
                 "EARLIER COPIES", "which branch?",
                 "orgtree_withdraw_ask",
                 S.ORG_STATE_OPEN, S.ORG_STATE_CLOSE):
        assert must in supp, f"suppression dropped {must!r}"
        assert must in full, f"the full block lost {must!r}"
    assert "kid-00 [" in full, "the full block stopped rendering a chart"
    assert "Chart unchanged since #3" in supp
    assert "orgtree_chart" in supp, (
        "a suppressed chart must name the route to a fresh one — an agent "
        "whose context lost snapshot #3 has no other way back")
    assert len(supp) < len(full), (len(supp), len(full))


def a_node_with_no_chart_has_nothing_to_suppress() -> None:
    """`self`/`team` visibility renders no chart span at all. Such a node must
    keep getting exactly the block it got before, and must never emit a
    dangling reference to a chart it was never shown."""
    for vis in ("self", "team"):
        org, nid = fixture(f"zz-env-nochart-{vis}", vis=vis)
        assert S.org_state_chart(org, nid) == ""
        text = S._envelope_state_block(org, nid, NOW, {})
        assert "Chart unchanged" not in text, vis
        assert "Your reports:" in text, vis


def the_default_rendering_is_byte_identical_to_the_old_one() -> None:
    """Every non-turn caller — orgtree_chart above all — asks for the whole
    block. Those callers pass no seq and no chart_ref and must be unaffected:
    the chart tool returning a chart-shaped pointer would be a bad joke."""
    org, nid = fixture("zz-env-default")
    plain = S.org_state_block(org, nid)
    assert "#" not in plain.split("\n", 1)[0], plain.split("\n", 1)[0]
    assert "Chart unchanged" not in plain
    assert "kid-00 [" in plain and "kid-11 [" in plain


# ── §3 the provider board ───────────────────────────────────────────────────

def registry() -> dict[str, Any]:
    return {"version": 2, "keys": [], "usage_refreshes": {},
            "key_liveness": {}}


def board(percent: float = 25.5, *, stale: bool = False,
          extra: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "available": True,
        "observed_at": iso(NOW - (1200 if stale else 90)),
        "age": 1200.0 if stale else 90.0, "stale": stale,
        "limits": [{"kind": "session", "percent": percent,
                    "resets_at": iso(NOW + 1800), "is_active": False},
                   *(extra or [])],
    }


def plant(claude: dict[str, Any]) -> None:
    limits.snapshot = lambda now=None: copy.deepcopy(claude)     # type: ignore[assignment]
    codex_limits.snapshot = lambda now=None: {"available": False}  # type: ignore[assignment]
    accounts.load = lambda strict=False: copy.deepcopy(registry())  # type: ignore[assignment]


REAL = (limits.snapshot, codex_limits.snapshot, accounts.load)


def restore() -> None:
    limits.snapshot, codex_limits.snapshot, accounts.load = REAL  # type: ignore[assignment]


def identical_limit_rows_are_one_row() -> None:
    """Two limits with the same window, percentage, reset and active flag are
    one fact reported twice. Live-caught on this org's own codex board, where
    `#N` disambiguation rendered them as two byte-different rows.

    ⚠ AND GENUINELY DIFFERENT ROWS MUST BOTH SURVIVE — the codex lane really
    does carry distinct buckets under one kind, and folding those would hide a
    real wall (caution from codex-stream-order, 2026-09-01).
    """
    org, nid = fixture("zz-env-dupe")
    same = {"kind": "session", "percent": 25.5,
            "resets_at": iso(NOW + 1800), "is_active": False}
    plant(board(extra=[copy.deepcopy(same)]))
    try:
        text, _key = turnusage.board(org, nid, selected_provider="claude",
                                     selected_lane="primary", now=NOW)
        assert "session#2" not in text, text
        assert text.count("| session |") == 1, text

        plant(board(extra=[{"kind": "session", "percent": 91.0,
                            "resets_at": iso(NOW + 1800),
                            "is_active": False}]))
        text, _key = turnusage.board(org, nid, selected_provider="claude",
                                     selected_lane="primary", now=NOW)
        assert "session#2" in text, ("a genuinely different bucket under the "
                                     f"same kind was folded away:\n{text}")
        assert "91%" in text and "25.5%" in text, text
    finally:
        restore()


def a_ticking_countdown_is_not_a_change() -> None:
    """The board's bytes churn every turn because countdowns and observation
    ages move. The material key must ignore exactly that, or nothing is ever
    suppressed — measured 76.1% byte-churn against 34.0% semantic churn."""
    org, nid = fixture("zz-env-tick")
    plant(board())
    try:
        _t1, k1 = turnusage.board(org, nid, selected_provider="claude",
                                  selected_lane="primary", now=NOW)
        _t2, k2 = turnusage.board(org, nid, selected_provider="claude",
                                  selected_lane="primary", now=NOW + 300)
        assert k1 == k2, "a ticking countdown read as a material change"
        # …but a real move does not hide behind that tolerance.
        plant(board(percent=91.0))
        _t3, k3 = turnusage.board(org, nid, selected_provider="claude",
                                  selected_lane="primary", now=NOW)
        assert k3 != k1, "crossing a usage band did not register"
        plant(board(stale=True))
        _t4, k4 = turnusage.board(org, nid, selected_provider="claude",
                                  selected_lane="primary", now=NOW)
        assert k4 != k1, "telemetry going stale did not register"
    finally:
        restore()


def a_one_second_wobble_across_a_minute_does_not_resend_the_board() -> None:
    """MEASURED, not hypothetical: this org's boards reported the same reset
    window as 23:00:00Z and then 22:59:59Z on consecutive turns. Truncating to
    the minute puts those in different buckets and re-sends the whole board
    because a clock wobbled backwards by one second."""
    org, nid = fixture("zz-env-wobble")
    base = NOW - (NOW % 3600) + 3600            # a clean hour boundary
    plant({"available": True, "observed_at": iso(NOW - 90), "age": 90.0,
           "stale": False,
           "limits": [{"kind": "session", "percent": 25.5,
                       "resets_at": iso(base), "is_active": False}]})
    try:
        _t, k1 = turnusage.board(org, nid, selected_provider="claude",
                                 selected_lane="primary", now=NOW)
        plant({"available": True, "observed_at": iso(NOW - 90), "age": 90.0,
               "stale": False,
               "limits": [{"kind": "session", "percent": 25.5,
                           "resets_at": iso(base - 1), "is_active": False}]})
        _t, k2 = turnusage.board(org, nid, selected_provider="claude",
                                 selected_lane="primary", now=NOW)
        assert k1 == k2, "a one-second reset wobble re-sent the whole board"
    finally:
        restore()


def the_lane_in_use_is_never_folded_or_summarised_away() -> None:
    """The compact line stands in for the board, so the lane this turn actually
    runs on keeps its exact numbers there. An agent throttling itself against
    its own usage must never be reading a band.

    ⚠ The FULL board is left exactly as `turnusage` renders it — every lane an
    explicit row, including the constant `unavailable(unsupported)` ones. An
    earlier draft folded those (261 of the board's 986 characters, measured)
    and it broke four deliberate "every state is explicit" pins in
    test_turn_usage_envelope. Suppression already removes those rows on the
    turns where they cost anything, so the fold bought little and spent
    someone else's invariant to get it."""
    org, nid = fixture("zz-env-compact")
    plant(board(percent=73.0))
    try:
        text, _k = turnusage.board(org, nid, selected_provider="claude",
                                   selected_lane="primary", now=NOW)
        rows = [ln for ln in text.splitlines() if ln.count("|") >= 6]
        line = turnusage.compact(rows, 4)
        assert "73%" in line, line
        assert turnusage.OPEN in line and line.endswith("]"), line
        assert "#4" in line, line
        assert len(line) < len(text) / 2, (len(line), len(text))
    finally:
        restore()


def a_telemetry_failure_is_never_read_as_unchanged() -> None:
    """Two failures in a row are not evidence that the board did not move —
    the board is unknown. The failure key must differ from any real key so the
    next successful render re-sends in full."""
    org, nid = fixture("zz-env-fail")

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("no telemetry")
    plant(board())
    # ⚠ Break something OUTSIDE the per-source try blocks. Patching a single
    # snapshot source only proves the fail-open row already pinned elsewhere;
    # the question here is what the KEY says when assembly itself dies.
    real_rows = turnusage._fallback_rows                        # type: ignore[attr-defined]
    turnusage._fallback_rows = boom                             # type: ignore[assignment]
    try:
        text, key = turnusage.board(org, nid, selected_provider="claude",
                                    selected_lane="primary", now=NOW)
        assert key == "telemetry-failure", key
        assert "telemetry-error" in text
    finally:
        turnusage._fallback_rows = real_rows                    # type: ignore[assignment]
        restore()
    good_text, good_key = turnusage.board(org, nid,
                                          selected_provider="claude",
                                          selected_lane="primary", now=NOW)
    assert good_key != "telemetry-failure"
    assert turnusage.number(good_text, 9).startswith(
        f"{turnusage.OPEN} #9"), turnusage.number(good_text, 9)[:60]


# ── §4 delivery, recovery and the seam ──────────────────────────────────────

def nothing_is_recorded_until_the_text_was_read() -> None:
    """The staging/commit split, which is the whole reason a dropped turn
    cannot cost an agent its roster."""
    org, nid = fixture("zz-env-commit")
    pending: dict[str, Any] = {}
    S._envelope_state_block(org, nid, NOW, pending)
    assert pending, "nothing was staged"
    fresh = store.load_org("zz-env-commit")
    assert envelope.read(fresh.node(nid), envelope.ORG_STATE) is None, (
        "the snapshot reached the org doc before the CLI had read anything — "
        "a turn that dies before launch would now suppress on replay")
    S._commit_envelope("zz-env-commit", nid, pending)
    fresh = store.load_org("zz-env-commit")
    assert envelope.read(fresh.node(nid), envelope.ORG_STATE) is not None
    assert not pending, "commit left the staging dict populated"


def a_session_replaced_mid_turn_does_not_inherit_the_snapshot() -> None:
    """Between render and confirm a node can be re-seeded, forked or
    cheap-compacted onto a new session. That block went to the OLD
    conversation; recording it against the new one would point a successor at
    something that was never in its context."""
    org, nid = fixture("zz-env-reseed")
    pending: dict[str, Any] = {}
    S._envelope_state_block(org, nid, NOW, pending)
    moved = store.load_org("zz-env-reseed")
    moved.node(nid)["session_id"] = "sess-ZZZ"
    store.save_org(moved)
    S._commit_envelope("zz-env-reseed", nid, pending)
    after = store.load_org("zz-env-reseed")
    assert envelope.read(after.node(nid), envelope.ORG_STATE) is None, (
        "a snapshot delivered to the previous session was recorded against "
        "the replacement")


def a_new_session_re_sends_everything_from_scratch() -> None:
    """Compaction, restart and cheap-compact all land here: whatever the old
    conversation was shown is not in this one."""
    org, nid = fixture("zz-env-newsess")
    p1: dict[str, Any] = {}
    first = S._envelope_state_block(org, nid, NOW, p1)
    S._commit_envelope("zz-env-newsess", nid, p1)
    org = store.load_org("zz-env-newsess")
    p2: dict[str, Any] = {}
    second = S._envelope_state_block(org, nid, NOW + 30, p2)
    assert "Chart unchanged" in second, "an unchanged org still re-sent"
    org.node(nid)["session_id"] = "sess-B"
    store.save_org(org)
    org = store.load_org("zz-env-newsess")
    p3: dict[str, Any] = {}
    third = S._envelope_state_block(org, nid, NOW + 60, p3)
    assert "Chart unchanged" not in third, (
        "a fresh session was pointed at a snapshot from the old one")
    assert "kid-00 [" in third and "kid-00 [" in first


def a_missing_snapshot_is_recoverable_without_the_backend_knowing() -> None:
    """THE LOST-DELTA CASE. If the pointed-at block is gone from the agent's
    context — a compaction the occupancy signal missed, a truncated resume —
    the agent must be able to recover by itself. The suppressed line therefore
    names both the snapshot and the tool that rebuilds it, and that tool
    returns the WHOLE block including the chart."""
    org, nid = fixture("zz-env-lost")
    supp = S.org_state_block(org, nid, seq=8, chart_ref=8)
    assert "#8" in supp and "orgtree_chart" in supp
    src = inspect.getsource(S)
    assert "org_state_block(" in src, (
        "the recovery route the suppressed line promises must exist")
    # …and the tool really does concatenate both halves (api.py's chart tool).
    api = open(os.path.join(BACKEND, "orgtree", "api.py"),
               encoding="utf-8").read()
    assert "supervisor.org_state_block(" in api, (
        "orgtree_chart stopped returning the org-state half — the suppressed "
        "line now promises a recovery that does not happen")


def suppression_never_touches_mail_notices_or_authority() -> None:
    """Mail and notices are drained-once and delivered whatever else happens.
    D-223 must be nowhere near them, and the source is the cheapest place to
    keep that true."""
    env_src = inspect.getsource(envelope)
    for forbidden in ("mail", "notice", "USER", "authority"):
        assert forbidden not in env_src.replace(
            "# ", "").split('"""')[-1], forbidden
    decide_src = inspect.getsource(envelope.decide)
    assert "mail" not in decide_src and "notice" not in decide_src
    run = inspect.getsource(S._run_one_turn)
    i_mail = run.index("mtext, turn_images = _mail_block")
    i_state = run.index("state_block = _envelope_state_block")
    assert i_mail < i_state, (
        "the mail block must be assembled before, and independently of, any "
        "envelope suppression decision")
    # The staging dict is DECLARED before the drain (it is a local, and the
    # drain block is where the turn's locals live). What must not happen there
    # is a DECISION: no suppression logic may run before mail and notices are
    # assembled, or a drain could come to depend on it.
    for call in ("_envelope_state_block(", "_envelope_decide(",
                 "turn_usage_block("):
        assert call not in run[:i_mail], (
            f"{call} runs before the mail/notice drain")


def the_turn_path_still_builds_both_blocks_and_commits_them() -> None:
    """Placement pins, in the spirit of the ones D-181 and the usage envelope
    already carry: a saving that quietly stopped delivering would look exactly
    like a saving that worked."""
    run = inspect.getsource(S._run_one_turn)
    assert run.count("turn_usage_block(") == 2, (
        "ordinary and boundary-fed turns each need their own board")
    assert "_envelope_state_block(" in run
    assert run.index("state_block + ") < run.index("_codex_leg("), (
        "the provider seam is reached before the state block is attached")
    assert "_commit_envelope(slug, nid, env_pending)" in run
    i_confirm = run.index("_confirm_delivered(slug, nid, pend_toks)")
    i_commit = run.index("_commit_envelope(slug, nid, env_pending)")
    assert i_confirm < i_commit, (
        "the envelope snapshot must be committed on the same proof of "
        "consumption as the mail journal, never earlier")
    state_src = inspect.getsource(S._envelope_state_block)
    assert "org_state_block(" in state_src, (
        "the turn path no longer renders the org state block at all")


def every_provider_takes_the_same_door() -> None:
    """Codex and Gemini turns rejoin through the same prologue, so suppression
    must be provider-neutral: it is decided before the seam and reads nothing
    provider-specific."""
    run = inspect.getsource(S._run_one_turn)
    i_state = run.index("state_block = _envelope_state_block")
    for seam in ("_codex_leg(", "_gemini_leg("):
        assert i_state < run.index(seam), seam
    dec = inspect.getsource(S._envelope_decide)
    for leaked in ("claude", "codex", "openai", "gemini", "google"):
        assert leaked not in dec.lower(), leaked
    for tier, name in (("haiku", "zz-env-prov-claude"),
                       ("sol", "zz-env-prov-codex"),
                       ("flash", "zz-env-prov-gemini")):
        org, nid = fixture(name, tier=tier)
        pending: dict[str, Any] = {}
        text = S._envelope_state_block(org, nid, NOW, pending)
        assert "Your reports:" in text and pending, tier


def the_measured_saving_is_real() -> None:
    """A regression fence around the number this work exists to produce. If a
    later change makes the suppressed block nearly as long as the full one,
    the machinery is still running and buying nothing."""
    org, nid = fixture("zz-env-saving")
    full = S.org_state_block(org, nid, seq=2)
    supp = S.org_state_block(org, nid, seq=2, chart_ref=2)
    assert "kid-00 [" not in supp, "a suppressed chart still listed nodes"
    assert len(supp) < len(full), (len(supp), len(full))
    # …and on a realistically sized org the block as a whole gets materially
    # shorter. Measured on this machine's live org: 1427 -> 901 chars (36.9%).
    big = store.create_org("zz-env-saving-big")
    big.hire(USER, None, "haiku", 60, "boss")
    big.node("boss")["scope"]["org_visibility"] = "full"
    for i in range(12):
        big.hire("boss", "boss", "haiku", 0, f"kid-{i:02d}", add_dirs=[],
                 tools={"bash": False, "web": False, "edit": False,
                        "subagents": False, "mcp": []},
                 org_visibility="self", charter="a chart row")
    store.save_org(big)
    ratio = 1.0 - (len(S.org_state_block(big, "boss", seq=2, chart_ref=2))
                   / len(S.org_state_block(big, "boss", seq=2)))
    assert ratio > 0.30, f"a 13-node chart only saved {ratio:.1%}"


for label, fn in (
    ("unchanged state suppresses; every other case sends in full",
     unchanged_state_is_suppressed_and_everything_else_is_not),
    ("a snapshot that cannot be trusted is not repaired",
     a_snapshot_that_cannot_be_trusted_is_not_repaired),
    ("unmeasured occupancy disables only the occupancy rules",
     unmeasured_occupancy_disables_only_the_occupancy_rules),
    ("a suppressed turn counts against the anchor, never renews it",
     a_suppressed_turn_does_not_move_the_anchor),
    ("roster, credits, staleness and the open ask survive suppression",
     the_facts_an_agent_acts_on_are_never_behind_a_pointer),
    ("a node with no chart emits no dangling chart reference",
     a_node_with_no_chart_has_nothing_to_suppress),
    ("the default rendering is unchanged for orgtree_chart and tests",
     the_default_rendering_is_byte_identical_to_the_old_one),
    ("identical limit rows collapse; distinct ones both survive",
     identical_limit_rows_are_one_row),
    ("a ticking countdown is not a change, but a real move is",
     a_ticking_countdown_is_not_a_change),
    ("a one-second reset wobble does not re-send the board",
     a_one_second_wobble_across_a_minute_does_not_resend_the_board),
    ("the lane in use keeps exact numbers in the compact line",
     the_lane_in_use_is_never_folded_or_summarised_away),
    ("a telemetry failure is never read as unchanged",
     a_telemetry_failure_is_never_read_as_unchanged),
    ("nothing is recorded until the CLI has read the text",
     nothing_is_recorded_until_the_text_was_read),
    ("a session replaced mid-turn does not inherit the snapshot",
     a_session_replaced_mid_turn_does_not_inherit_the_snapshot),
    ("a new session re-sends everything from scratch",
     a_new_session_re_sends_everything_from_scratch),
    ("a lost snapshot is recoverable by the agent alone",
     a_missing_snapshot_is_recoverable_without_the_backend_knowing),
    ("suppression never touches mail, notices or authority",
     suppression_never_touches_mail_notices_or_authority),
    ("the turn path still builds both blocks and commits them",
     the_turn_path_still_builds_both_blocks_and_commits_them),
    ("every provider takes the same door",
     every_provider_takes_the_same_door),
    ("the measured saving is real", the_measured_saving_is_real),
):
    check(label, fn)

restore()
if FAIL:
    print(f"\n{FAIL} FAILED, {PASS} PASSED")
    raise SystemExit(1)
print(f"\nALL {PASS} CHECKS PASS")
