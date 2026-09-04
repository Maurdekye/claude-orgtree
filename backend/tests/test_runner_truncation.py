"""The runner must say when a suite STOPPED EARLY, not just how far it got.

THE DEFECT, and it is the reason four dark tails were invisible for up to 25
days. `tools/run_tests.py` derives a failing suite's check count from the last
`ok N` line — so **the number it prints is the ABORT POINT**, in the same
column and the same words it uses for a completed total. `extern-handle-attach`
printed "2 checks" for a 19-check suite. Nothing anywhere said the other 17
had not run.

And the flag that existed for exactly this, `r.truncated`, was gated on
`rc == 0` — so it could never fire on a FAILING suite, which is the only
population where it matters.

Most suites in this tree use a deliberate fail-fast `check()`: it raises
rather than recording, so a red suite dies at its first failure. THAT
CONVENTION IS NOT THE DEFECT — it is what makes a green run a clean binary
signal, and `extern-handle-attach` documents it in its own closing comment.
Reporting an abort as if it were a total is the defect.

Measured 2026-09-04, from source and git history:

    crash-reports          1 of  8 checks ran, since 2026-08-30
    extern-handle-attach   2 of 19 checks ran, since 2026-08-31
    external-mail          8 of 241 checks ran, for 25 days

with kiosk sealing, the extern HTTP surface and an authorization section
inside the dark part.

WHAT THIS PINS. `stopped_early()` is the predicate, lifted out of `run_one` so
it can be exercised without a subprocess — a predicate this load-bearing that
could only be reached by running a real suite is one nobody checks. The
fixtures are REAL output shapes taken from suites in this tree, not invented
ones, because the whole failure was a regex that did not match reality.

⚠ THE SUBTLE HALF: a catch-and-continue suite that RAN EVERYTHING and reported
failures prints "12 passed - 1 FAILED", which contains neither "checks passed"
nor "ALL N CHECKS PASS". Once truncation stopped being gated on rc == 0, that
shape had to be recognised or every honest suite would be branded cut-short.
Group `finished-with-failures` is that check, and it is the one a careless
version of this fix would get wrong.

Hermetic: pure predicate, no subprocess, no suite executed.

    python backend/tests/test_runner_truncation.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "..", "..", "tools", "run_tests.py")

_spec = importlib.util.spec_from_file_location("orgtree_run_tests", RUNNER)
assert _spec and _spec.loader, f"cannot load the runner at {RUNNER}"
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)

PASS = 0
FAIL: list[tuple[str, str]] = []


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                       # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


# ---- real output shapes from suites in this tree ------------------------
ABORTED = """
§1 fixtures + the shape of the funnel
  ok   1  a fresh org has NO extern recipients
  ok   2  first contact bootstraps the LEFTMOST live top-level
Traceback (most recent call last):
  File "tests/test_external_mail.py", line 371, in _
    assert not missing, (
AssertionError: these rigs mint their own ORGTREE_DATA
"""

GREEN_ALL_CAPS = """
  ok   1  a thing
  ok   2  another thing

ALL 2 CHECKS PASS
"""

GREEN_LOWER = """
  ok   1  a thing
orgmd-identity: all 6 checks passed
"""

GREEN_NODE = """
✔ a thing (0.6ms)
ℹ tests 9
ℹ pass 9
ℹ fail 0
"""

FINISHED_WITH_FAILURES_DASH = """
  ok   1  a thing
  FAIL     another thing

org-charter-prompt: 12 passed - 1 FAILED
"""

FINISHED_WITH_FAILURES_DOT = """
  ok  35  a thing
headless: 35 passed · 1 FAILED · 0 findings
"""

FINISHED_WITH_FAILURES_COMMA = """
  ok  38  a thing
38 checks passed, 1 FAILED
"""

NO_OK_LINES_AT_ALL = """
Traceback (most recent call last):
ImportError: no module named orgtree
"""


FINISHED_COUNT_OF_TOTAL = """
ok  38  4.5 building the standing table makes no network call
ok  39  4.6 …and the tripwire can fire
   · 1.7a the live primary 429 wording enters the limit path
Traceback (most recent call last):
  File "x.py", line 1, in _
AssertionError: 429 rate_limit_error

1 of 39 checks FAILED
"""

FINISHED_FAILED_FIRST = """
  ok   6  a thing
Traceback (most recent call last):
AssertionError: initialize stays once per process

1 FAILED, 6 PASSED
"""

GREEN_OWN_FORMAT = """
  ok  24  a thing

PASS — gpt-reserve detection, 24 checks
"""


#: THE SHAPE THAT DEFEATED THE SECOND VERSION OF THIS PREDICATE, taken
#: verbatim from a real sweep log. Every fail-fast suite in this tree routes
#: through a function named `check`, so its traceback frames read
#: `File "...", line 142, in check` — digits and a keyword on one line. A
#: summary recogniser that allowed the bare word "check" matched THAT, decided
#: a summary followed every death, and reported that nothing had aborted at
#: all. The sweep came back clean and the instrument was simply blind.
REAL_ABORT_THROUGH_CHECK = """
  ok  78  a kiosk is sealed from the outside
  ok  79  …and from the inside
Traceback (most recent call last):
  File "C:\wt\backend\tests\test_external_mail.py", line 2610, in <module>
    s4_kiosk()
  File "C:\wt\backend\tests\test_external_mail.py", line 142, in check
    fn()
  File "C:\wt\backend\tests\test_external_mail.py", line 983, in _
    assert "kiosk_cfg" not in row and row["kiosk"] is True, row
AssertionError: {'slug': 'sealed-renamed', 'nodes': 1, 'live': 1}
"""

# ------------------------------------------------------- group aborted
def t_a_fail_fast_abort_is_reported_as_stopped_early() -> None:
    """THE HEADLINE. A suite that died at its first failure printed `ok`
    lines and never reached a total; that is exactly what must be visible."""
    assert rt.stopped_early(ABORTED), (
        "a suite that aborted mid-run was not flagged as stopped early - "
        "its abort point would be printed as if it were a total")


def t_the_rc_gate_is_gone() -> None:
    """The specific regression this file exists for. `stopped_early` takes
    ONLY the output: there is no exit code in its signature to gate on, so
    the old `rc == 0 and ...` shape cannot come back by accident."""
    import inspect
    params = list(inspect.signature(rt.stopped_early).parameters)
    assert params == ["out"], (
        f"stopped_early takes {params} - if an exit code is back in this "
        f"predicate, a failing suite can be excluded from truncation again, "
        f"which is the defect that hid four suites")



def t_a_suite_that_finished_with_failures_is_not_branded() -> None:
    """THE CHECKS A FULL SWEEP EARNED, and the reason this predicate was
    rebuilt. The first version asked "is a recognised total missing" — an
    open-ended absence — and a real sweep immediately branded two suites
    that had run every check: `account-pool-state` ends "1 of 39 checks
    FAILED" and `codex-prewarm` ends "1 FAILED, 6 PASSED". Neither
    matched the phrase list, and there is no closed set of summary
    formats to chase. There IS a closed set of ways a run dies, so the
    predicate asks that instead.

    A flag that cries wolf is worthless within a day; a false positive
    here would be worse than the failure it reports."""
    for name, out in (("count-of-total", FINISHED_COUNT_OF_TOTAL),
                      ("failed-first", FINISHED_FAILED_FIRST)):
        assert not rt.stopped_early(out), (
            f"a suite that ran every check and summarised its failures "
            f"({name}) was branded as having stopped early")


def t_a_green_suite_in_its_own_format_is_not_branded() -> None:
    """A suite whose final line the runner does not recognise did not stop
    early — it just spells its total its own way. That is a different and
    much older complaint, and it is not this flag's business."""
    assert not rt.stopped_early(GREEN_OWN_FORMAT)


def t_a_caught_failure_echoing_a_traceback_is_not_a_death() -> None:
    """Catch-and-continue suites PRINT tracebacks for the failures they
    recorded, then carry on. The discriminator is whether anything reports
    counts AFTER the last one."""
    assert not rt.stopped_early(FINISHED_WITH_FAILURES_DASH
                                + "\nTraceback (most recent call last):"
                                + "\nAssertionError: x"
                                + "\n12 passed - 1 FAILED\n")


def t_a_real_abort_through_a_check_frame_is_still_caught() -> None:
    """THE FALSIFIER THIS FILE MOST NEEDED, and it was added after a full
    sweep came back with ZERO aborts — including the one suite that
    verifiably dies. Every fail-fast suite here routes through a function
    named `check`, so every traceback frame carries `in check`; a summary
    recogniser that accepted the bare word matched inside the traceback
    and concluded a summary followed every death.

    An instrument that reports nothing found must prove it can find
    something. This is that proof, on real bytes from a real log."""
    assert rt.stopped_early(REAL_ABORT_THROUGH_CHECK), (
        "a genuine abort was missed because its traceback frames pass "
        "through a function named check - the recogniser is matching "
        "inside the traceback it is supposed to be looking past")

# -------------------------------------------------------- group finished
def t_a_green_suite_is_not_flagged() -> None:
    for name, out in (("ALL N CHECKS PASS", GREEN_ALL_CAPS),
                      ("N checks passed", GREEN_LOWER),
                      ("node's pass line", GREEN_NODE)):
        assert not rt.stopped_early(out), (
            f"a finished green suite reporting {name} was flagged as "
            f"stopped early")


def t_a_suite_with_no_ok_lines_is_not_flagged() -> None:
    """An import error is a failure, not a truncation. Flagging it would put
    "the tail is unmeasured" on a suite that has no measured head either -
    noise on top of a real error, which is how a flag stops being read."""
    assert not rt.stopped_early(NO_OK_LINES_AT_ALL)


# ------------------------------------------ group finished-with-failures
def t_a_catching_suite_that_failed_is_not_branded_truncated() -> None:
    """THE ONE A CARELESS FIX GETS WRONG. Catch-and-continue suites run every
    check and then report failures - "12 passed - 1 FAILED" contains neither
    "checks passed" nor "ALL N CHECKS PASS". Once truncation stopped being
    gated on rc == 0, that shape had to be recognised or every honest suite
    in the tree would be branded cut-short, and the flag would be worth
    nothing within a day."""
    for name, out in (("dash", FINISHED_WITH_FAILURES_DASH),
                      ("middot", FINISHED_WITH_FAILURES_DOT),
                      ("comma", FINISHED_WITH_FAILURES_COMMA)):
        assert not rt.stopped_early(out), (
            f"a suite that RAN EVERY CHECK and reported failures ({name} "
            f"separator) was flagged as stopped early")


def t_a_fail_line_then_death_is_still_a_death() -> None:
    """ANTI-VACUITY for the group above. A suite may print a FAIL line
    and THEN die - a catching wrapper that records one failure and is
    killed by the next. Nothing reports counts after the traceback, so
    this is an abort and must still be flagged. Without this the group
    above could be satisfied by a predicate that simply never fires.

    (The first version of this check fed a fixture ending in a literal
    "Traceback…" with an ellipsis rather than the real first line, so it
    passed against the phrase-based predicate for the wrong reason and
    failed the moment the predicate started looking for the real thing.
    A fixture that does not resemble the input is not a fixture.)"""
    out = (" ok   1  a thing\n"
           "  FAIL     another thing\n"
           "Traceback (most recent call last):\n"
           "AssertionError: the second one killed it\n")
    assert rt.stopped_early(out), (
        "a suite that printed a FAIL line and then DIED was treated as "
        "finished - nothing reported counts after the traceback")


# --------------------------------------------------------- group wiring
def t_run_one_uses_the_predicate() -> None:
    """The predicate is only worth anything if the runner asks it. In the
    spirit of `group: callers`: a floor nothing stands on is not a floor.

    ⚠ READ THE AST, NOT THE TEXT — and this check taught itself that lesson
    on its first run. The first version grepped the file for
    `"rc == 0 and oks"` and failed, because `stopped_early`'s own comment
    QUOTES the old gate while explaining why it is gone. A source grep cannot
    tell code from prose about code, which is the same fragility that makes
    `harvest` and `turn-lifecycle` drift detectors rather than tests.
    `ast.unparse` discards comments entirely, so what is asserted here is what
    the interpreter will actually execute.
    """
    import ast
    mod = ast.parse(open(RUNNER, encoding="utf-8").read())
    fns = {n.name: n for n in ast.walk(mod)
           if isinstance(n, ast.FunctionDef)}
    assert "run_one" in fns, "run_one is gone from the runner"
    body = ast.unparse(fns["run_one"])
    assert "r.aborted = stopped_early(r.out)" in body, (
        "run_one no longer routes truncation through stopped_early - the "
        "tested predicate and the shipped behaviour have separated")
    # ⚠ AND NOT ONE CHARACTER MORE. The first draft also banned
    # "rc == 0" anywhere before that line, which is wrong: run_one
    # legitimately reads the exit code two lines up to set PASS/FAIL. A
    # check that forbids a correct line is a check someone deletes. The
    # gate cannot return through the predicate anyway - stopped_early takes
    # only its output text, pinned by t_the_rc_gate_is_gone.
    assert body.count("r.aborted") == 1, (
        "the abort verdict is reached in more than one place in run_one - "
        "one of them will drift")


def t_a_timeout_writes_itself_into_the_log() -> None:
    """From a saved logdir a HANG was indistinguishable from a suite that
    aborted having printed nothing - both are a file that stops. The ⏱ mark
    lived only in the runner's console, so anyone reading logs afterwards
    (a flip diff, a bisect, an agent who was not there) needed the runner
    transcript handed to them separately."""
    src = open(RUNNER, encoding="utf-8").read()
    i = src.index("except subprocess.TimeoutExpired:")
    j = src.index("r.secs = time.time() - t0", i)
    assert "TIMEOUT after" in src[i:j], (
        "the timeout path writes no marker into the captured output - a "
        "killed suite is indistinguishable from a silent abort in the log")


def main() -> int:
    print("group aborted: a fail-fast abort must be visible")
    check("a fail-fast abort is reported as stopped early",
          t_a_fail_fast_abort_is_reported_as_stopped_early)
    check("the rc == 0 gate cannot come back by accident",
          t_the_rc_gate_is_gone)
    check("a real abort through a `check` frame is still caught",
          t_a_real_abort_through_a_check_frame_is_still_caught)
    print("group false-positives: what a full sweep caught")
    check("a suite that finished WITH failures is not branded",
          t_a_suite_that_finished_with_failures_is_not_branded)
    check("a green suite in its own total format is not branded",
          t_a_green_suite_in_its_own_format_is_not_branded)
    check("a caught failure echoing a traceback is not a death",
          t_a_caught_failure_echoing_a_traceback_is_not_a_death)
    print("group finished: a suite that reached its own end is left alone")
    check("a green suite is not flagged, in all three conventions",
          t_a_green_suite_is_not_flagged)
    check("a suite with no ok lines at all is not flagged",
          t_a_suite_with_no_ok_lines_is_not_flagged)
    print("group finished-with-failures: the one a careless fix gets wrong")
    check("a catching suite that failed is not branded truncated",
          t_a_catching_suite_that_failed_is_not_branded_truncated)
    check("ANTI-VACUITY: a FAIL line then death is still a death",
          t_a_fail_line_then_death_is_still_a_death)
    print("group wiring: the runner actually asks")
    check("run_one routes truncation through the predicate",
          t_run_one_uses_the_predicate)
    check("a timeout writes itself into the log",
          t_a_timeout_writes_itself_into_the_log)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n[X] {label}\n{tb}")
        print(f"runner-truncation: {PASS} passed - {len(FAIL)} FAILED")
        return 1
    print(f"runner-truncation: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
