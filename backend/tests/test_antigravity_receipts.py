"""Antigravity token receipts: what orgtree can actually add up.

    python backend/tests/test_antigravity_receipts.py   (no pytest; asserts)

THE PROBLEM THIS ADDRESSES. `antigravity_limits.estimate` takes its token
numbers from an injected callable so its arithmetic can be tested against
known values. That leaves the real question open: where do the numbers come
from, and are they addable? They come from the synthetic usage record every
Antigravity turn journals - and the ones written before 2026-09-04 hold
SESSION-CUMULATIVE usage, so adding them up bills the same tokens again and
again. Two real specimens 40 seconds apart in the operator journals read
487,941 then 511,084 input tokens; a naive sum reports a million.

WHAT THIS SUITE PINS, and why each one can fail:

 · the collector FINDS receipts and adds them correctly (a collector that
   always answered zero would satisfy every "does not overcount" check ever
   written, so the positive control comes first);
 · a legacy cumulative row is NOT summed, and is NOT silently dropped either -
   it is reported as `unsummable_receipts`, and a window holding one is
   reported as measured IN PART;
 · being measured in part CAPS the confidence, so the shortfall reaches the
   surface instead of sitting in a field nobody reads;
 · the fast byte gate is necessary but NOT sufficient - a line that merely
   MENTIONS a receipt id is not a receipt (this is real: the gate admits 66
   lines of the operator journals, of which 65 are receipts);
 · the interval is half-open, and the mtime prefilter never drops a row;
 · the boot marker records nothing on a machine with no Antigravity CLI, and
   DOES record on one that has it.

Hermetic: throwaway ORGTREE_DATA set BEFORE any orgtree import, no CLI, no
network. The journal rows are written by this file, in the shape the
supervisor writes them.
"""

import datetime
import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-agyrec-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_PORT"] = "9"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import antigravity_limits as agy                    # noqa: E402
from orgtree import antigravity_receipts as rec                  # noqa: E402
from orgtree import store, supervisor                            # noqa: E402

assert os.path.realpath(store.DATA_ROOT) == os.path.realpath(DATA), \
    f"DATA_ROOT bound to {store.DATA_ROOT}, not the throwaway {DATA}"
# the collector reads whatever `journal_store()` points at; if that resolved
# anywhere but the throwaway root this suite would be reading the operator's
# real journals and calling it a fixture
assert os.path.realpath(supervisor.journal_store()).startswith(
    os.path.realpath(DATA)), \
    f"journal store escaped the throwaway root: {supervisor.journal_store()}"

FAILED: list[str] = []
PROJ = os.path.join(supervisor.journal_store(), "projects", "testorg")

SHORT_WALL = ("Individual quota reached. Please upgrade your subscription to "
              "increase your limits. Resets in 3h20m48s.")


def check(ok: object, label: str) -> None:
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        FAILED.append(label)


def iso(at: float) -> str:
    return datetime.datetime.utcfromtimestamp(at).isoformat() + "Z"


def receipt(at: float, tin: int, cached: int, out: int,
            *, per_turn: bool = True, turn: str = "t") -> dict[str, object]:
    """One turn-end usage record, in the shape `_codex_leg` writes for the
    Antigravity lane. `per_turn=False` is a PRE-2026-09-04 row: no
    `last_prompt_tokens`, and its usage is the session running total."""
    message: dict[str, object] = {
        "id": f"agy-{turn}-usage", "role": "assistant",
        "model": "gemini-3.8-flash", "content": [],
        "usage": {"input_tokens": tin, "cache_read_input_tokens": cached,
                  "output_tokens": out}}
    if per_turn:
        message["last_prompt_tokens"] = 200_000
    return {"type": "assistant", "timestamp": iso(at), "message": message}


def write_journal(name: str, rows: list[dict[str, object]],
                  mtime: float | None = None) -> str:
    os.makedirs(PROJ, exist_ok=True)
    path = os.path.join(PROJ, name)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def clear_journals() -> None:
    if not os.path.isdir(PROJ):
        return
    for name in os.listdir(PROJ):
        os.remove(os.path.join(PROJ, name))


# the two REAL specimens: consecutive rows 40 seconds apart in the operator
# journals, both pre-marker, both session-cumulative
LEGACY_A = (487_941, 7_951_112, 38_717)
LEGACY_B = (511_084, 8_658_756, 40_525)
# and two real post-marker rows from the same journals, which are per-turn
MODERN_A = (942_729, 16_344_845, 50_528)
MODERN_B = (15_953, 618_099, 3_098)

T0 = 1_788_000_000.0


# ------------------------------------------------------------- the positive
def test_the_collector_finds_receipts_and_adds_them() -> None:
    print("collection")
    clear_journals()
    write_journal("s1.jsonl", [receipt(T0 + 10, *MODERN_A, turn="a"),
                              receipt(T0 + 20, *MODERN_B, turn="b")])
    got = rec.receipts_between(T0, T0 + 3600)
    want = sum(MODERN_A) + sum(MODERN_B)
    check(got["receipts"] == 2,
          f"CONTROL: the collector FINDS the receipts: {got['receipts']}")
    check(got["tokens"] == want,
          f"and sums them: {got['tokens']} vs {want}")
    check(got["input"] == MODERN_A[0] + MODERN_B[0]
          and got["cached"] == MODERN_A[1] + MODERN_B[1]
          and got["output"] == MODERN_A[2] + MODERN_B[2],
          "with the components kept apart, since which of them the provider "
          f"charges is not published: {got['input']}/{got['cached']}/"
          f"{got['output']}")
    check(got["sessions"] == 1, f"and the session counted once: {got['sessions']}")


def test_a_cumulative_row_is_refused_not_added_and_not_hidden() -> None:
    print("the pre-2026-09-04 rows")
    clear_journals()
    write_journal("s2.jsonl", [
        receipt(T0 + 10, *LEGACY_A, per_turn=False, turn="a"),
        receipt(T0 + 50, *LEGACY_B, per_turn=False, turn="b")])
    got = rec.receipts_between(T0, T0 + 3600)
    naive = sum(LEGACY_A) + sum(LEGACY_B)
    check(got["tokens"] == 0,
          f"a session-cumulative row is NOT added (a naive sum would report "
          f"{naive:,}): {got['tokens']}")
    check(got["receipts"] == 0, "and is not counted as a measured receipt")
    check(got["unsummable_receipts"] == 2,
          f"but it IS reported, so the interval reads as partly measured "
          f"rather than empty: {got['unsummable_receipts']}")
    # the control: the SAME numbers with the marker are summed
    clear_journals()
    write_journal("s3.jsonl", [
        receipt(T0 + 10, *LEGACY_A, turn="a"),
        receipt(T0 + 50, *LEGACY_B, turn="b")])
    marked = rec.receipts_between(T0, T0 + 3600)
    check(marked["tokens"] == naive and marked["unsummable_receipts"] == 0,
          f"CONTROL: identical numbers WITH the per-turn marker are summed - "
          f"the marker decides, not the values: {marked['tokens']}")


def test_the_byte_gate_is_not_the_check() -> None:
    print("gate vs check")
    clear_journals()
    # a record that NAMES a receipt id where JSON puts quotes either side of
    # it - which is all the byte gate looks for
    decoy = {"type": "assistant", "timestamp": iso(T0 + 30),
             "message": {"id": "msg_01claude", "role": "assistant",
                         "model": "claude-opus-5",
                         "content": [{"type": "text", "text": "agy-t-usage"}],
                         "last_prompt_tokens": 5,
                         "usage": {"input_tokens": 999_999,
                                   "cache_read_input_tokens": 999_999,
                                   "output_tokens": 999_999}}}
    line = json.dumps(decoy).encode("utf-8")
    check(rec._GATE_A in line and rec._GATE_B in line,
          "CONTROL: the decoy really does pass the fast byte gate")
    write_journal("s4.jsonl", [decoy, receipt(T0 + 40, *MODERN_B, turn="b")])
    got = rec.receipts_between(T0, T0 + 3600)
    check(got["tokens"] == sum(MODERN_B) and got["receipts"] == 1,
          f"a line that merely MENTIONS a receipt id is not a receipt: "
          f"{got['tokens']} vs {sum(MODERN_B)}")


def test_other_providers_receipts_are_not_ours() -> None:
    print("lane isolation")
    clear_journals()
    codex_row = {"type": "assistant", "timestamp": iso(T0 + 30),
                 "message": {"id": "cdx-1", "role": "assistant",
                             "model": "gpt-5.6",
                             "last_prompt_tokens": 1,
                             "usage": {"input_tokens": 777_777,
                                       "cache_read_input_tokens": 0,
                                       "output_tokens": 1}}}
    write_journal("s5.jsonl", [codex_row, receipt(T0 + 40, *MODERN_B)])
    got = rec.receipts_between(T0, T0 + 3600)
    check(got["tokens"] == sum(MODERN_B),
          f"another lane's usage record is not an Antigravity receipt: "
          f"{got['tokens']}")


def test_the_interval_is_half_open() -> None:
    print("boundaries")
    clear_journals()
    write_journal("s6.jsonl", [receipt(T0, *MODERN_B, turn="start"),
                               receipt(T0 + 3600, *MODERN_A, turn="end")])
    got = rec.receipts_between(T0, T0 + 3600)
    check(got["receipts"] == 1 and got["tokens"] == sum(MODERN_B),
          f"the row AT the start counts and the row AT the end does not "
          f"(the next window owns it): {got['receipts']} receipt(s)")
    later = rec.receipts_between(T0 + 3600, T0 + 7200)
    check(later["receipts"] == 1 and later["tokens"] == sum(MODERN_A),
          "CONTROL: and the next interval does pick it up")
    check(rec.receipts_between(T0 + 100, T0 + 100)["receipts"] == 0,
          "an empty interval measures nothing")


def test_the_mtime_prefilter_never_drops_a_row() -> None:
    print("prefilter")
    clear_journals()
    # a file last written LONG before the interval cannot hold a row inside
    # it: skipping it is the whole point of the prefilter
    write_journal("old.jsonl", [receipt(T0 - 90_000, *MODERN_A, turn="old")],
                  mtime=T0 - 80_000)
    # a session that stopped part-way through the window: its last write is
    # INSIDE the interval, so a prefilter keyed on the interval END instead of
    # its start would drop a row orgtree really did record
    write_journal("mid.jsonl", [receipt(T0 + 10, *MODERN_B, turn="mid")],
                  mtime=T0 + 1800)
    # and one still being appended to now
    write_journal("live.jsonl", [receipt(T0 + 20, *MODERN_A, turn="live")],
                  mtime=time.time())
    got = rec.receipts_between(T0, T0 + 3600)
    check(got["tokens"] == sum(MODERN_B) + sum(MODERN_A),
          f"every in-window row survives the prefilter, whether its file was "
          f"last written inside the window or after it: {got['tokens']}")
    check(got["scanned_files"] == 2,
          f"and the file that cannot hold one is skipped, not read: "
          f"{got['scanned_files']} file(s) scanned")


# ------------------------------------------------- the estimate, end to end
def test_the_estimate_reads_real_receipts() -> None:
    print("estimate through the real collector")
    clear_journals()
    first = agy._window_event("wall", T0, tier="flash", message=SHORT_WALL,
                              resets_at=agy.reset_at(SHORT_WALL, T0),
                              wall_id="w1")
    opened = float(first["resets_at"])
    second = agy._window_event("wall", opened + 3600, tier="flash",
                               message=SHORT_WALL,
                               resets_at=agy.reset_at(SHORT_WALL,
                                                      opened + 3600),
                               wall_id="w2")
    write_journal("w.jsonl", [receipt(opened + 60, *MODERN_A, turn="a"),
                              receipt(opened + 120, *MODERN_B, turn="b")])
    out = agy.estimate([first, second], rec.tokens_between)
    want = sum(MODERN_A) + sum(MODERN_B)
    check(out["available"] is True and out["samples"] == 1,
          f"one complete window, measured from the journals: {out}")
    check(out["estimate"] == {"tokens": want},
          f"the number is the real receipts, not an injected one: "
          f"{out['estimate']} vs {want}")
    check(out["comparability"] == "unknown",
          "and it still says nothing proves this window comparable to another")
    check(out["confidence"] == "experimental",
          f"CONTROL: a fully measured single window is 'experimental': "
          f"{out['confidence']}")
    check(out["coverage"]["unsummable_receipts"] == 0
          and out["coverage"]["windows_partly_measured"] == 0,
          "and nothing is reported as unmeasured")
    check("lower bound" in out["warning"].lower(),
          "the lower-bound warning still rides on every estimate")


def test_a_partly_measured_window_cannot_read_as_a_good_sample() -> None:
    print("partial windows")
    clear_journals()
    first = agy._window_event("wall", T0, tier="flash", message=SHORT_WALL,
                              resets_at=agy.reset_at(SHORT_WALL, T0),
                              wall_id="w1")
    opened = float(first["resets_at"])
    second = agy._window_event("wall", opened + 3600, tier="flash",
                               message=SHORT_WALL,
                               resets_at=agy.reset_at(SHORT_WALL,
                                                      opened + 3600),
                               wall_id="w2")
    write_journal("p.jsonl", [
        receipt(opened + 60, *MODERN_A, turn="a"),
        receipt(opened + 90, *LEGACY_A, per_turn=False, turn="old")])
    out = agy.estimate([first, second], rec.tokens_between)
    check(out["estimate"] == {"tokens": sum(MODERN_A)},
          f"the number counts only what could be counted: {out['estimate']}")
    check(out["coverage"]["unsummable_receipts"] == 1
          and out["coverage"]["windows_partly_measured"] == 1,
          f"the shortfall is reported, not swallowed: {out['coverage']}")
    check(out["confidence"] == "low",
          f"and a window measured in part cannot read as a good sample - "
          f"the same window WITHOUT the legacy row reads 'experimental': "
          f"{out['confidence']}")


def test_an_injected_number_still_works() -> None:
    print("back-compat")
    first = agy._window_event("wall", T0, tier="flash", message=SHORT_WALL,
                              resets_at=agy.reset_at(SHORT_WALL, T0),
                              wall_id="w1")
    opened = float(first["resets_at"])
    second = agy._window_event("wall", opened + 3600, tier="flash",
                               message=SHORT_WALL,
                               resets_at=agy.reset_at(SHORT_WALL,
                                                      opened + 3600),
                               wall_id="w2")
    out = agy.estimate([first, second], lambda s, e: 4242)
    check(out["estimate"] == {"tokens": 4242},
          f"a bare number is still a whole answer: {out['estimate']}")
    check(out["coverage"]["windows_partly_measured"] == 0,
          "and claims full coverage, which is what a bare number means")
    check(agy._receipt("nonsense") is None and agy._receipt({"x": 1}) is None,
          "CONTROL: something that is not an answer is not read as zero")


# ------------------------------------------------------------ the boot mark
def test_the_boot_mark_needs_a_cli_to_mark() -> None:
    print("boot marker")
    for suffix in ("", ".1"):
        try:
            os.remove(agy._events_path() + suffix)
        except OSError:
            pass
    original = agy.providers.antigravity_status
    try:
        agy.providers.antigravity_status = lambda: {   # type: ignore[assignment]
            "installed": False, "email": None}
        agy.note_boot(now=T0)
        check(agy.read_events() == [],
              "a machine with no Antigravity CLI records no boot markers - "
              "nothing there can ever produce an observation")
        agy.providers.antigravity_status = lambda: {   # type: ignore[assignment]
            "installed": True, "email": "someone@example.com"}
        agy.note_boot(now=T0 + 1)
        kinds = [e.get("kind") for e in agy.read_events()]
        check(kinds == ["boot"],
              f"CONTROL: a machine that HAS it does record one: {kinds}")
    finally:
        agy.providers.antigravity_status = original   # type: ignore[assignment]


for fn in (test_the_collector_finds_receipts_and_adds_them,
           test_a_cumulative_row_is_refused_not_added_and_not_hidden,
           test_the_byte_gate_is_not_the_check,
           test_other_providers_receipts_are_not_ours,
           test_the_interval_is_half_open,
           test_the_mtime_prefilter_never_drops_a_row,
           test_the_estimate_reads_real_receipts,
           test_a_partly_measured_window_cannot_read_as_a_good_sample,
           test_an_injected_number_still_works,
           test_the_boot_mark_needs_a_cli_to_mark):
    fn()

print()
if FAILED:
    print(f"FAIL - {len(FAILED)} check(s):")
    for f in FAILED:
        print("  -", f)
    raise SystemExit(1)
print("PASS - receipts are collected, cumulative rows are refused rather "
      "than summed, and what could not be counted is reported")
