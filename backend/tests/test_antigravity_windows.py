"""Antigravity observed windows, and what an estimate from them may claim.

    python backend/tests/test_antigravity_windows.py   (no pytest; asserts)

THE PROBLEM THIS ADDRESSES. `standing.json` holds the CURRENT wall and nothing
else: observe_wall REPLACES it, observe_clear sets it to None. Correct for a
standing, useless for measurement - the moment a turn succeeds, the window
that just ended is gone. So walls and clears are ALSO appended to a bounded
journal, and the estimate is computed from that.

WHAT THE SUITE PINS, and why each one can fail:

 · the journal survives what the standing does not (a clear erases the
   standing; the record still has both events);
 · it is BOUNDED - rotation is driven and the file count is asserted, so
   "append-only" cannot quietly mean "grows forever";
 · no secrets - the signed-in address must NOT appear, and the namespace hash
   must still change when the account changes (a constant would "not leak" too);
 · intervals are NEVER averaged together, and the answer says outright that
   comparability is UNKNOWN: nothing recorded here can prove two walls came
   from one ceiling, so repetition is not corroboration;
 · a BOOT is not an interval start - it marks when orgtree began watching,
   which can fall anywhere inside a window already running;
 · an interval does not survive an ACCOUNT change;
 · ONE LIMIT'S RESET DOES NOT OPEN ANOTHER LIMIT'S WINDOW - an interval opened
   by a flash reset and closed by a pro wall (or by a different named metric)
   measures neither, and is refused rather than reported with a caveat;
 · an identity MISSING at one end is recorded as unknown and the interval is
   kept - not knowing is not the same as knowing they differ - but the answer
   must say `limit_continuity: unknown` rather than certify continuity;
 · the countdown decides nothing in either direction: the same duration on a
   different tier is still a different thing, and two different durations are
   not proof of two limits;
 · with no interval from an observed reset to a later wall there is NO NUMBER,
   only a reason;
 · one such interval DOES produce a number, labelled `experimental` with
   samples=1 - a first sample is the only number anyone has, and suppressing
   it entirely would be as dishonest as dressing it up as a ceiling;
 · every estimate carries the lower-bound warning, because IDE usage is
   unobservable and a remaining-budget reading from this is optimistic.

Hermetic: throwaway ORGTREE_DATA set BEFORE any orgtree import, no CLI, no
network; token receipts are injected, so the arithmetic is checked against
numbers the test chose.
"""

import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-agywin-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_PORT"] = "9"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import antigravity_limits as agy, store   # noqa: E402

assert os.path.realpath(store.DATA_ROOT) == os.path.realpath(DATA), \
    f"DATA_ROOT bound to {store.DATA_ROOT}, not the throwaway {DATA}"

FAILED: list[str] = []
# the two REAL specimens measured on the operator's account
LONG_WALL = ("Individual quota reached. Please upgrade your subscription to "
             "increase your limits. Resets in 165h21m54s.")
SHORT_WALL = ("Individual quota reached. Please upgrade your subscription to "
              "increase your limits. Resets in 3h20m48s.")
# a DIFFERENT named metric, same countdown - the CLI names the metric when it
# has one, and `_label` reads it out of the quoted clause
MODEL_WALL = ("Quota reached for limit 'model quota'. Please upgrade your "
              "subscription to increase your limits. Resets in 3h20m48s.")


def check(ok: object, label: str) -> None:
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        FAILED.append(label)


def reset_journal() -> None:
    agy.forget_memory()
    for suffix in ("", ".1"):
        try:
            os.remove(agy._events_path() + suffix)
        except OSError:
            pass


def wall_event(at: float, message: str, tier: str = "flash",
               wall_id: str = "w") -> dict[str, object]:
    resets = agy.reset_at(message, at)
    row = agy._window_event("wall", at, tier=tier, message=message,
                            resets_at=resets, wall_id=wall_id)
    return row


# ------------------------------------------------------------------ journal
def test_the_journal_keeps_what_the_standing_throws_away() -> None:
    print("journal")
    reset_journal()
    t0 = 1_788_000_000.0
    agy.observe_wall(SHORT_WALL, tier="flash", now=t0)
    check(agy._wall is not None, "CONTROL: the standing holds the wall")
    agy.observe_clear(now=t0 + 60)
    check(agy._wall is None, "CONTROL: a clear erases the standing")
    events = agy.read_events()
    kinds = [e.get("kind") for e in events]
    check(kinds == ["wall", "clear"],
          f"the journal still has BOTH observations: {kinds}")
    wall = events[0]
    check(wall.get("wall_id"), "the wall carries an id")
    check(events[1].get("after_wall") == wall.get("wall_id"),
          "and the clear names the wall it ended, closing the window")
    check(abs(float(wall["reset_seconds"]) - (3 * 3600 + 20 * 60 + 48)) < 1,
          f"the observed reset DURATION is recorded: {wall.get('reset_seconds')}")
    check(wall.get("label") == "individual quota",
          f"and the metric it named: {wall.get('label')}")


def test_a_clear_with_nothing_standing_is_not_an_observation() -> None:
    print("spurious clears")
    reset_journal()
    agy.observe_clear(now=1_788_000_000.0)
    agy.observe_clear(now=1_788_000_100.0)
    check(agy.read_events() == [],
          "successful turns with no wall standing record nothing "
          "(otherwise the journal fills with non-events)")


def test_the_record_is_bounded() -> None:
    print("bounded retention")
    reset_journal()
    base = agy._events_path()
    for i in range(agy.MAX_EVENTS + 25):
        agy._append_event({"v": 1, "kind": "boot", "at": 1_788_000_000.0 + i})
    present = [p for p in (base, base + ".1") if os.path.exists(p)]
    check(len(present) == 2, f"rotation happened: {[os.path.basename(p) for p in present]}")
    check(not os.path.exists(base + ".2"),
          "and only ONE older generation is kept - the record cannot grow "
          "without limit")
    with open(base, encoding="utf-8") as f:
        live = sum(1 for _ in f)
    check(live <= agy.MAX_EVENTS,
          f"the live generation stays under the cap: {live}")


def test_no_secrets_but_still_identifying() -> None:
    print("account namespace")
    real = "someone@example.com"
    original = agy.providers.antigravity_status
    try:
        agy.providers.antigravity_status = lambda: {"email": real}   # type: ignore[assignment]
        a = agy._account_ns()
        agy.providers.antigravity_status = lambda: {"email": "other@example.com"}   # type: ignore[assignment]
        b = agy._account_ns()
    finally:
        agy.providers.antigravity_status = original     # type: ignore[assignment]
    check(real not in a and "@" not in a,
          f"the signed-in address does not appear in the record: {a!r}")
    check(a and a != b,
          "CONTROL: a DIFFERENT account yields a different namespace - a "
          "constant would also 'leak nothing' and identify nothing")


# ------------------------------------------------------------------ windows
def test_windows_close_on_walls_and_open_on_resets() -> None:
    print("window reconstruction")
    t0 = 1_788_000_000.0
    first = wall_event(t0, SHORT_WALL, wall_id="w1")
    reset1 = float(first["resets_at"])
    second = wall_event(reset1 + 3600, SHORT_WALL, wall_id="w2")
    ws = agy.windows([first, second])
    check(len(ws) == 2, f"two walls, two windows: {len(ws)}")
    check(ws[0]["reset_to_wall"] is False,
          "the FIRST interval has no observed reset at its near end")
    check(ws[1]["reset_to_wall"] is True and ws[1]["started_at"] == reset1,
          f"the second opens at the first one's reset: {ws[1]['started_at']} "
          f"vs {reset1}")
    check(ws[1]["walled_at"] == reset1 + 3600, "and closes at its own wall")


def test_a_boot_gap_is_recorded_not_hidden() -> None:
    print("coverage")
    t0 = 1_788_000_000.0
    first = wall_event(t0, SHORT_WALL, wall_id="w1")
    reset1 = float(first["resets_at"])
    boot = {"v": 1, "kind": "boot", "at": reset1 + 1800}
    second = wall_event(reset1 + 3600, SHORT_WALL, wall_id="w2")
    ws = agy.windows([first, boot, second])
    check(ws[1]["gap_before_s"] > 0,
          f"the period orgtree was not observing is attached to the window: "
          f"{ws[1]['gap_before_s']}s")
    check(ws[1]["reset_to_wall"] is True,
          "the interval is still usable - a restart does not by itself mean "
          "receipts are missing, it means a wall could have passed unseen")


def own(event: dict[str, object], account: str = "acct") -> dict[str, object]:
    """Stamp an event with an account handle the test controls."""
    event["account_ns"] = account
    return event


def test_windows_are_never_averaged_together() -> None:
    print("no averaging")
    t0 = 1_788_000_000.0
    a1 = own(wall_event(t0, SHORT_WALL, wall_id="s1"))
    a2 = own(wall_event(float(a1["resets_at"]) + 60, SHORT_WALL, wall_id="s2"))
    b1 = own(wall_event(float(a2["resets_at"]) + 60, LONG_WALL, wall_id="l1"))
    seen: list[tuple[float, float]] = []

    def receipts(start: float, end: float) -> int:
        seen.append((start, end))
        return 45_000

    out = agy.estimate([a1, a2, b1], tokens_between=receipts)
    check(len(seen) == 1,
          f"three walls, two measurable intervals, and exactly ONE of them is "
          f"measured: {len(seen)} call(s)")
    check(seen == [(float(a2["resets_at"]), float(b1["at"]))],
          f"and it is the LATEST one, over its own interval: {seen}")
    check(out["samples"] == 1 and out["estimate"] == {"tokens": 45_000},
          f"one observation, its own number, no mean of anything: {out['estimate']}")
    check(out["comparability"] == "unknown",
          f"and it says outright that nothing proves these the same limit: "
          f"{out.get('comparability')}")
    check(out["other_intervals"]["reset_to_wall"] == 1,
          f"the other measurable interval is COUNTED, not folded in: "
          f"{out['other_intervals']}")


def test_the_countdown_decides_nothing_in_either_direction() -> None:
    print("countdown is not identity")
    t0 = 1_788_000_000.0
    # the SAME countdown on two tiers: identical durations, different things
    same_a = agy.windows([own(wall_event(t0, SHORT_WALL, tier="flash",
                                         wall_id="a"))])[0]
    same_b = agy.windows([own(wall_event(t0, SHORT_WALL, tier="pro",
                                         wall_id="b"))])[0]
    check(same_a["reset_seconds"] == same_b["reset_seconds"],
          "CONTROL: the two really do carry the same countdown")
    check(agy._differs(same_a, same_b) == "tier",
          f"an identical countdown on a different tier is still a "
          f"demonstrably different thing: {agy._differs(same_a, same_b)!r}")
    # and two DIFFERENT countdowns are not proof of two limits
    diff_a = agy.windows([own(wall_event(t0, SHORT_WALL, wall_id="c"))])[0]
    diff_b = agy.windows([own(wall_event(t0, LONG_WALL, wall_id="d"))])[0]
    check(agy._differs(diff_a, diff_b) is None,
          f"3h20m against 165h is not PROOF of two limits either - the CLI "
          f"prints time REMAINING, and None here means UNKNOWN, never 'the "
          f"same' ({diff_a['reset_seconds']}s vs {diff_b['reset_seconds']}s)")
    check(not hasattr(agy, "_comparable"),
          "and the predicate that called countdown similarity 'the same "
          "limit' is GONE, not merely left unused")


def test_a_boot_is_not_a_window_start() -> None:
    print("boot mid-window")
    t0 = 1_788_000_000.0
    # the account was refilled at t0; orgtree only began watching at t0+3000,
    # five sixths of the way into a window that was already running
    boot = own({"v": 1, "kind": "boot", "at": t0 + 3000})
    wall = own(wall_event(t0 + 3600, SHORT_WALL, wall_id="w1"))
    ws = agy.windows([boot, wall])
    check(ws[0]["start_kind"] == "boot" and ws[0]["started_at"] == t0 + 3000,
          f"the boot start is still RECORDED: {ws[0]['start_kind']}")
    check(ws[0]["reset_to_wall"] is False,
          "but it is not a defensible near end - no account is refilled "
          "because "
          "a process started, and timing from it would measure a fraction of "
          "a window and report it as the whole")
    out = agy.estimate([boot, wall], tokens_between=lambda s, e: 40_000)
    check(out["available"] is False and out["estimate"] is None,
          f"so there is NO number: {out.get('reason')!r}")
    check("boot" in str(out.get("reason") or ""),
          "and the refusal names the boot rather than reporting nothing seen")
    # CONTROL: the same wall, opened by a reset the PROVIDER named, does count
    prior = own(wall_event(t0 - 20_000, SHORT_WALL, wall_id="w0"))
    wall2 = own(wall_event(float(prior["resets_at"]) + 600, SHORT_WALL,
                           wall_id="w2"))
    ok = agy.estimate([prior, wall2], tokens_between=lambda s, e: 40_000)
    check(ok["available"] is True and ok["estimate"] == {"tokens": 40_000},
          f"CONTROL: a reset-started window DOES yield a number: {ok.get('estimate')}")


def test_a_window_does_not_survive_an_account_change() -> None:
    print("account switch")
    t0 = 1_788_000_000.0
    first = own(wall_event(t0, SHORT_WALL, wall_id="w1"), "aaaaaaaaaaaa")
    second = own(wall_event(float(first["resets_at"]) + 3600, SHORT_WALL,
                            wall_id="w2"), "bbbbbbbbbbbb")
    ws = agy.windows([first, second])
    check(ws[1]["reset_to_wall"] is False,
          "one account's wall is not timed from ANOTHER account's refill - "
          "the receipts in between belong to neither interval")
    out = agy.estimate([first, second], tokens_between=lambda s, e: 40_000)
    check(out["available"] is False,
          f"so there is no number to report: {out.get('reason')!r}")
    # CONTROL: the very same events on ONE account do close a window
    same = own(dict(second), "aaaaaaaaaaaa")
    ws2 = agy.windows([first, same])
    check(ws2[1]["reset_to_wall"] is True and ws2[1]["boundary"] == "consistent",
          "CONTROL: identical events, one account - the interval closes and "
          "its two ends agree")
    # and an UNKNOWN handle is not evidence of a different account
    unknown = own(dict(second), "")
    ws3 = agy.windows([first, unknown])
    check(ws3[1]["reset_to_wall"] is True,
          "an empty handle means 'could not tell', not 'someone else'")
    check(ws3[1]["boundary"] == "unknown",
          f"but it is NOT read as continuity either: {ws3[1]['boundary']}")


# ----------------------------------------------------------------- estimate
def test_no_number_without_a_reset_started_interval() -> None:
    print("refusal")
    out = agy.estimate([], tokens_between=lambda s, e: 999999)
    check(out["available"] is False and out["estimate"] is None,
          f"nothing observed: no number at all, {out.get('reason')!r}")
    check(out["samples"] == 0, "and it says so as a sample count")
    only = wall_event(1_788_000_000.0, SHORT_WALL, wall_id="w1")
    out = agy.estimate([only], tokens_between=lambda s, e: 999999)
    check(out["available"] is False and out["estimate"] is None,
          "one wall with no observed reset before it: still no number "
          f"({out.get('reason')!r})")


def test_one_measurable_interval_is_reported_as_one_sample() -> None:
    print("first sample")
    t0 = 1_788_000_000.0
    first = wall_event(t0, SHORT_WALL, wall_id="w1")
    reset1 = float(first["resets_at"])
    second = wall_event(reset1 + 7200, SHORT_WALL, wall_id="w2")
    seen: list[tuple[float, float]] = []

    def receipts(start: float, end: float) -> int:
        seen.append((start, end))
        return 41_000

    out = agy.estimate([first, second], tokens_between=receipts)
    check(out["available"] is True and out["samples"] == 1,
          f"one measurable interval yields a number, as ONE sample: "
          f"{out['samples']}")
    check(out["confidence"] == "experimental",
          f"labelled experimental, not presented as a limit: {out['confidence']}")
    check(out["estimate"] == {"tokens": 41_000},
          f"the number is the measured spend: {out['estimate']}")
    check(seen == [(reset1, reset1 + 7200)],
          f"measured over exactly the window, not some other interval: {seen}")
    check("lower bound" in out["warning"].lower()
          and "optimistic" in out["warning"].lower(),
          "and carries the lower-bound / optimistic warning")
    check("not a reported limit" in out["basis"],
          "and says plainly that the provider reported nothing")
    # CONTROL: the number must follow the receipts, not be decoration
    out2 = agy.estimate([first, second], tokens_between=lambda s, e: 82_000)
    check(out2["estimate"] == {"tokens": 82_000},
          f"CONTROL: different receipts, different estimate: {out2['estimate']}")
    check(out["comparability"] == "unknown",
          "and every answer states that comparability is unknown")


def test_more_windows_never_raise_the_confidence() -> None:
    print("confidence does not accrue")
    t = 1_788_000_000.0
    evs: list[dict[str, object]] = []
    for i in range(4):
        e = own(wall_event(t, SHORT_WALL, wall_id=f"w{i}"))
        evs.append(e)
        t = float(e["resets_at"]) + 3600
    out = agy.estimate(evs, tokens_between=lambda s, e: 40_000)
    check(out["samples"] == 1,
          f"four walls, three measurable intervals, ONE observation: "
          f"{out['samples']}")
    check(out["other_intervals"]["reset_to_wall"] == 2,
          f"the rest are counted, never combined: {out['other_intervals']}")
    check(out["confidence"] == "experimental",
          f"seeing the same thing repeatedly is not corroboration while "
          f"comparability is unknown: {out['confidence']}")
    check(out["comparability"] == "unknown",
          "and the answer says so rather than leaving it to a sample count")
    # CONTROL: the label is not a constant - it drops for the one reason it may
    partial = agy.estimate(evs, tokens_between=lambda s, e: {
        "tokens": 40_000, "unsummable_receipts": 3})
    check(partial["confidence"] == "low",
          f"CONTROL: a window measured only IN PART still drops to low: "
          f"{partial['confidence']}")


def test_one_limits_reset_does_not_open_another_limits_window() -> None:
    print("boundary crossing")
    t0 = 1_788_000_000.0
    # a flash "individual quota" wall names a reset; the NEXT wall is a
    # different tier AND a different metric. The interval between them was
    # opened by one limit and closed by another, so it measures neither.
    a = own(wall_event(t0, SHORT_WALL, tier="flash", wall_id="a1"))
    b = own(wall_event(float(a["resets_at"]) + 3600, MODEL_WALL, tier="pro",
                       wall_id="b1"))
    ws = agy.windows([a, b])
    check(ws[1]["reset_to_wall"] is True,
          "CONTROL: the interval really does run from an observed reset to a "
          "later wall - the only thing wrong with it is WHOSE reset it was")
    check(ws[1]["opened_by"].get("tier") == "flash",
          f"the identity that named the opening reset is kept, not discarded: "
          f"{ws[1]['opened_by']}")
    check(ws[1]["boundary"] == "mismatch"
          and ws[1]["boundary_differs"] == "tier",
          f"and the two ends are DEMONSTRABLY different: "
          f"{ws[1]['boundary']}/{ws[1]['boundary_differs']}")
    out = agy.estimate([a, b], tokens_between=lambda s, e: 40_000)
    check(out["available"] is False and out["estimate"] is None,
          f"so there is NO number: {out.get('reason')!r}")
    check("neither" in str(out.get("reason") or ""),
          "and the refusal says the interval measures neither limit")
    # CONTROL: the same two instants, one tier and one metric, DO measure
    same = own(wall_event(float(a["resets_at"]) + 3600, SHORT_WALL,
                          tier="flash", wall_id="b2"))
    ok = agy.estimate([a, same], tokens_between=lambda s, e: 40_000)
    check(ok["available"] is True and ok["estimate"] == {"tokens": 40_000},
          f"CONTROL: identical interval, one limit at both ends, and it is "
          f"measured: {ok.get('estimate')}")
    check(ok["limit_continuity"] == "consistent",
          f"reported as consistent rather than assumed: "
          f"{ok.get('limit_continuity')}")
    # a different NAMED METRIC is enough on its own, with the tier held equal
    c = own(wall_event(float(a["resets_at"]) + 3600, MODEL_WALL, tier="flash",
                       wall_id="c1"))
    check(agy.windows([a, c])[1]["boundary_differs"] == "metric",
          f"the same tier is not a limit identity: "
          f"{agy.windows([a, c])[1]['boundary_differs']!r}")


def test_an_unrecorded_identity_is_unknown_not_certified() -> None:
    print("unknown identity at a boundary")
    t0 = 1_788_000_000.0
    known = own(wall_event(t0, SHORT_WALL, wall_id="k1"))
    blank = own(dict(wall_event(float(known["resets_at"]) + 3600, SHORT_WALL,
                                wall_id="k2")), "")
    ws = agy.windows([known, blank])
    check(ws[1]["reset_to_wall"] is True,
          "an unreadable account handle does not throw the interval away - a "
          "transient probe failure is not evidence of anything")
    check(ws[1]["boundary"] == "unknown"
          and ws[1]["boundary_differs"] is None,
          f"but it is RECORDED as unknown, not read as continuity: "
          f"{ws[1]['boundary']}")
    out = agy.estimate([known, blank], tokens_between=lambda s, e: 40_000)
    check(out["available"] is True,
          f"the measurement is still reported - the evidence is kept: "
          f"{out.get('reason')!r}")
    check(out["limit_continuity"] == "unknown",
          f"and the answer states that the two ends are not known to be one "
          f"limit: {out.get('limit_continuity')}")
    check("neither establishes" in out["limit_continuity_note"],
          "in words on the answer, not only as a token")
    # CONTROL: the same interval with the handle recorded at BOTH ends
    both = own(dict(blank), "acct")
    ok = agy.estimate([known, both], tokens_between=lambda s, e: 40_000)
    check(ok["limit_continuity"] == "consistent",
          f"CONTROL: recorded at both ends and agreeing: "
          f"{ok.get('limit_continuity')}")



for fn in (test_the_journal_keeps_what_the_standing_throws_away,
           test_a_clear_with_nothing_standing_is_not_an_observation,
           test_the_record_is_bounded,
           test_no_secrets_but_still_identifying,
           test_windows_close_on_walls_and_open_on_resets,
           test_a_boot_gap_is_recorded_not_hidden,
           test_windows_are_never_averaged_together,
           test_the_countdown_decides_nothing_in_either_direction,
           test_a_boot_is_not_a_window_start,
           test_a_window_does_not_survive_an_account_change,
           test_one_limits_reset_does_not_open_another_limits_window,
           test_an_unrecorded_identity_is_unknown_not_certified,
           test_no_number_without_a_reset_started_interval,
           test_one_measurable_interval_is_reported_as_one_sample,
           test_more_windows_never_raise_the_confidence):
    fn()

print()
if FAILED:
    print(f"FAIL - {len(FAILED)} check(s):")
    for f in FAILED:
        print("  -", f)
    raise SystemExit(1)
print(f"PASS - observed windows are recorded, bounded, and claim only what "
      f"they measured")
