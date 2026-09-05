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
 · two different limits are NOT averaged together - the 165h and 3h20m windows
   measured on one real account are the fixture;
 · with no complete window there is NO NUMBER, only a reason;
 · one complete window DOES produce a number, labelled `experimental` with
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
    check(ws[0]["complete"] is False,
          "the FIRST window has no defensible start and is not complete")
    check(ws[1]["complete"] is True and ws[1]["started_at"] == reset1,
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
    check(ws[1]["complete"] is True,
          "the window is still usable - a restart does not by itself mean "
          "receipts are missing, it means a wall could have passed unseen")


def test_two_different_limits_are_not_averaged() -> None:
    print("distinct limits")
    t0 = 1_788_000_000.0
    a1 = wall_event(t0, SHORT_WALL, wall_id="s1")
    a2 = wall_event(float(a1["resets_at"]) + 60, SHORT_WALL, wall_id="s2")
    b1 = wall_event(float(a2["resets_at"]) + 60, LONG_WALL, wall_id="l1")
    ws = agy.windows([a1, a2, b1])
    short, long_ = ws[1], ws[2]
    check(not agy._comparable(short, long_),
          f"a 3h20m window and a 165h window are NOT the same limit "
          f"({short['reset_seconds']}s vs {long_['reset_seconds']}s)")
    check(agy._comparable(ws[1], ws[1]), "CONTROL: a window matches itself")
    out = agy.estimate([a1, a2, b1], tokens_between=lambda s, e: 1000)
    check(out["samples"] == 1,
          f"the estimate uses only the comparable group, not all three "
          f"closed windows: samples={out['samples']}")


# ----------------------------------------------------------------- estimate
def test_no_number_without_a_complete_window() -> None:
    print("refusal")
    out = agy.estimate([], tokens_between=lambda s, e: 999999)
    check(out["available"] is False and out["estimate"] is None,
          f"nothing observed: no number at all, {out.get('reason')!r}")
    check(out["samples"] == 0, "and it says so as a sample count")
    only = wall_event(1_788_000_000.0, SHORT_WALL, wall_id="w1")
    out = agy.estimate([only], tokens_between=lambda s, e: 999999)
    check(out["available"] is False and out["estimate"] is None,
          "one wall with no defensible start: still no number "
          f"({out.get('reason')!r})")


def test_one_complete_window_is_reported_as_one_sample() -> None:
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
          f"one complete window yields a number, as ONE sample: {out['samples']}")
    check(out["confidence"] == "experimental",
          f"labelled experimental, not presented as a limit: {out['confidence']}")
    check(out["estimate"]["tokens_latest"] == 41_000,
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
    check(out2["estimate"]["tokens_latest"] == 82_000,
          f"CONTROL: different receipts, different estimate: {out2['estimate']}")


def test_more_samples_raise_confidence_and_spread_lowers_it() -> None:
    print("confidence")
    t = 1_788_000_000.0
    evs = []
    for i in range(4):
        e = wall_event(t, SHORT_WALL, wall_id=f"w{i}")
        evs.append(e)
        t = float(e["resets_at"]) + 3600
    steady = agy.estimate(evs, tokens_between=lambda s, e: 40_000)
    check(steady["samples"] == 3,
          f"three complete windows out of four walls: {steady['samples']}")
    check(steady["confidence"] == "indicative",
          f"consistent samples read as indicative: {steady['confidence']}")
    vals = iter([10_000, 90_000, 45_000])
    noisy = agy.estimate(evs, tokens_between=lambda s, e: next(vals))
    check(noisy["confidence"] == "low",
          f"CONTROL: widely spread samples are NOT indicative: "
          f"{noisy['confidence']}")
    check(noisy["estimate"]["tokens_lowest"] == 10_000
          and noisy["estimate"]["tokens_highest"] == 90_000,
          f"and the spread itself is reported: {noisy['estimate']}")


for fn in (test_the_journal_keeps_what_the_standing_throws_away,
           test_a_clear_with_nothing_standing_is_not_an_observation,
           test_the_record_is_bounded,
           test_no_secrets_but_still_identifying,
           test_windows_close_on_walls_and_open_on_resets,
           test_a_boot_gap_is_recorded_not_hidden,
           test_two_different_limits_are_not_averaged,
           test_no_number_without_a_complete_window,
           test_one_complete_window_is_reported_as_one_sample,
           test_more_samples_raise_confidence_and_spread_lowers_it):
    fn()

print()
if FAILED:
    print(f"FAIL - {len(FAILED)} check(s):")
    for f in FAILED:
        print("  -", f)
    raise SystemExit(1)
print(f"PASS - observed windows are recorded, bounded, and claim only what "
      f"they measured")
