"""The rot alarm itself: does it still fire, and has it stopped crying wolf?

`run_tests.py` classifies each suite's OUTPUT as a fired guard, a held guard,
or silence, by matching phrases line by line. That makes the alarm an
instrument that reads output AS DATA — the same class of thing it exists to
police — and on 2026-08-26 it was measured doing exactly what it was built to
prevent. `test_compaction.py` carries a check whose LABEL describes a shape
having moved, and that label matched one of the fired-guard phrases, so every
clean fast tier printed the full alarm banner ("until then every check
downstream of it is a fiction") over a suite that had just passed. The alarm
that cried wolf on every run was, by the runner's own docstring, an alarm
nobody reads.

The fix filters lines that a check reported itself PASSING on. The risk in
that fix is the reason this suite exists: **silencing the alarm and repairing
it look identical from the outside.** A pattern that matches nothing passes
every run beautifully. So the load-bearing check here is not that the false
positive is gone — it is that a GENUINE breakage still trips it.

    §1  a real contract, really broken, really raises — and the break is
        proved to have landed before anything is read
    §2  the runner still classifies that real breakage as FIRED
    §3  a passing check's line is not a failure — and the phrase in it still
        matches the raw pattern, so this was a repair and not a gag
    §4  the over-reach test: `not ok` is not `ok`

§1 borrows the tree's REAL guard (`msgvis.assert_client_model_matches_source`)
against a COPY of the repo, so the message §2 is classified on is the message
the guard actually emits, not one written from memory here. Nothing in the
real tree is modified.

    python backend/tests/test_drift_alarm.py [-v]
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))

import msgvis                                                    # noqa: E402
import run_tests as rt                                           # noqa: E402

VERBOSE = "-v" in sys.argv
PASS = 0
FAIL: list[tuple[str, str]] = []

#: the real label out of `test_compaction.py` that set the alarm off on every
#: run. Held as a fixture AND re-checked against the live source in §3, so
#: this suite cannot quietly start testing a string nobody prints any more.
COMPACTION_OK_LINE = (
    "  ok 326  drift · (the shape) the phantom no longer matches the live "
    "node's session — it matches the BEARER that inherited it")


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def _true(cond, msg) -> None:
    if not cond:
        raise AssertionError(msg)


def classify(text):
    """Run `text` through the runner's REAL classification path.

    Not a call to the regex: `run_one` is where the decision is actually made,
    and a check that exercised the pattern alone would keep passing if the
    filter around it were removed. The child simply prints the fixture.
    """
    d = tempfile.mkdtemp(prefix="orgtree-driftalarm-")
    src = os.path.join(d, "say.txt")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(text)
    suite = rt.Suite("alarm-probe", None, None, REPO, guard=True)
    cmd = [sys.executable, "-c",
           "import sys,io; sys.stdout.write(open(sys.argv[1], encoding='utf-8')"
           ".read())", src]
    return rt.run_one(suite, cmd, 120, d)


# ════════════════════════════════════ §1  a real break, proved to have landed

_REAL_BREAKAGE: list[str] = []


def sec_real_break() -> None:
    print("\n§1  a real contract, really broken")

    # ⚠ the wording carries weight: `run_tests.py` detects that this file
    # names a drift guard, then hunts the output for a verdict. "drift guard"
    # in a passing label is what `_GUARD_HELD` reads, and without it this
    # suite reports "⚐ guard silent" on every run — a second nuisance flag on
    # the very suite that exists to remove the first one. It is also the
    # honest label: this check runs the tree's real guard against the real
    # repo, and it held.
    check("the drift guard holds against the real repo",
          lambda: _true(msgvis.assert_client_model_matches_source(REPO),
                        "the guard returned nothing — it verified no contracts"))

    rel, pat, _why = msgvis._SOURCE_CONTRACTS[0]
    fake = tempfile.mkdtemp(prefix="orgtree-driftalarm-repo-")
    for r, _p, _w in msgvis._SOURCE_CONTRACTS:
        dst = os.path.join(fake, r.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst):
            shutil.copy2(os.path.join(REPO, r.replace("/", os.sep)), dst)

    check("the copy is faithful — the guard passes against it untouched",
          lambda: _true(msgvis.assert_client_model_matches_source(fake),
                        "the copied tree does not satisfy the contracts, so a "
                        "later failure would prove nothing about the break"))

    target = os.path.join(fake, rel.replace("/", os.sep))
    body = open(target, encoding="utf-8").read()
    broken = body.replace("COPIES_WINDOW = 200", "COPIES_WINDOW = 999", 1)

    def _landed():
        # ⚠ PROVE THE BREAK LANDED. A `replace` that matched nothing returns
        # the string unchanged and every assertion after it would pass for the
        # wrong reason — which is the exact failure this tree keeps producing.
        _true(broken != body,
              f"the edit changed nothing in {rel} — no break was injected")
        with open(target, "w", encoding="utf-8", newline="") as fh:
            fh.write(broken)
        import re as _re
        fresh = open(target, encoding="utf-8").read().replace("\r\n", "\n")
        _true(not _re.search(pat, fresh),
              f"/{pat}/ still matches {rel} after the edit — the contract is "
              f"not actually broken")
    check("the injected break is on disk and the contract no longer holds",
          _landed)

    def _raises():
        try:
            msgvis.assert_client_model_matches_source(fake)
        except AssertionError as e:
            _REAL_BREAKAGE.append(str(e))
            return
        raise AssertionError("the guard PASSED against a broken tree — it is "
                             "not checking what it claims to check")
    check("the real guard raises on it", _raises)

    check("…and its message names the file and the pattern",
          lambda: _true(rel in _REAL_BREAKAGE[0] and "msgvis" in _REAL_BREAKAGE[0],
                        f"unhelpful message: {_REAL_BREAKAGE[0][:200]!r}"))


# ════════════════════════════════════ §2  the runner still calls it a failure

def sec_still_fires() -> None:
    print("\n§2  the runner still raises the alarm on it")
    _true(_REAL_BREAKAGE, "§1 produced no message to classify")
    # the shape a suite really emits: the guard raises inside `check`, so the
    # message reaches stdout on a FAIL line and in the traceback under it
    out = ("  ok   1  something unrelated\n"
           "  FAIL     drift guard · client model matches source\n"
           "Traceback (most recent call last):\n"
           f"AssertionError: {_REAL_BREAKAGE[0]}\n")
    r = classify(out)
    if VERBOSE:
        print(f"    guard_state={r.guard_state!r} lines={r.guard_lines}")

    check("a genuine break is classified FIRED — the alarm was repaired, "
          "not gagged",
          lambda: _true(r.guard_state == "FIRED",
                        f"guard_state was {r.guard_state!r}; the alarm no "
                        f"longer fires on a real drift"))
    check("…and the offending line is captured for the report",
          lambda: _true(any("msgvis" in ln for ln in r.guard_lines),
                        f"guard_lines={r.guard_lines}"))


# ════════════════════════════════════ §3  a passing check is not a failure

def sec_passing_line() -> None:
    print("\n§3  a check that reported itself passing is not evidence")

    def _still_in_tree():
        src = open(os.path.join(_HERE, "test_compaction.py"),
                   encoding="utf-8", errors="replace").read()
        _true("the phantom no longer matches the live node" in src,
              "test_compaction.py no longer carries the label this suite "
              "pins — re-derive the fixture from the live source or drop it")
    check("the label this regression came from is still in the tree",
          _still_in_tree)

    def _still_matches_raw():
        # ⚠ THE DIFFERENCE BETWEEN A REPAIR AND A GAG. If the phrase stopped
        # matching the pattern at all, §2 would be passing for a reason that
        # has nothing to do with the filter, and a future edit that widened
        # the pattern back would silently restore the false positive.
        _true(rt._GUARD_FIRED.search(COMPACTION_OK_LINE),
              "the raw pattern no longer matches the label — this fix was a "
              "pattern change, not a line filter, and §2 no longer proves it")
    check("the phrase in it still matches the raw pattern", _still_matches_raw)

    r = classify(COMPACTION_OK_LINE + "\n")
    check("but the runner does NOT call it a fired guard",
          lambda: _true(r.guard_state != "FIRED",
                        f"guard_state={r.guard_state!r}; the alarm is crying "
                        f"wolf on a passing check again"))


# ════════════════════════════════════ §4  the over-reach test

def sec_not_ok() -> None:
    print("\n§4  `not ok` is not `ok` — the filter must not over-reach")
    r = classify("  not ok 7  drift guard · contracts intact ✗\n")
    check("a `not ok` line still fires the alarm",
          lambda: _true(r.guard_state == "FIRED",
                        f"guard_state={r.guard_state!r} — the filter swallowed "
                        f"a FAILING check because its line contains 'ok'"))

    r2 = classify("  ok 12  drift guard · contracts intact\n")
    check("a passing guard line still reads as held, not fired",
          lambda: _true(r2.guard_state == "held",
                        f"guard_state={r2.guard_state!r}"))


# ═════════════════════════════════════════════════════════════════════════ main

def main() -> None:
    print("═══ the rot alarm: still fires, no longer cries wolf ═══")
    sec_real_break()
    sec_still_fires()
    sec_passing_line()
    sec_not_ok()

    print(f"\n{'═' * 70}\n{PASS} checks passed, {len(FAIL)} failed")
    for label, tb in FAIL:
        print(f"\nFAIL  {label}\n{tb}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
