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


def t_the_failure_summary_is_not_matched_by_an_ok_line_alone() -> None:
    """Anti-vacuity for the check above: the recogniser must key on the
    SUMMARY, not on the presence of the word FAILED anywhere. A suite that
    printed a FAIL line and then died is still an abort."""
    out = "  ok   1  a thing\n  FAIL     another thing\nTraceback…\n"
    assert rt.stopped_early(out), (
        "a suite that printed a FAIL line and then died was treated as "
        "finished - the recogniser is matching the word, not the summary")


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
    assert "r.truncated = stopped_early(r.out)" in body, (
        "run_one no longer routes truncation through stopped_early - the "
        "tested predicate and the shipped behaviour have separated")
    # ⚠ AND NOT ONE CHARACTER MORE. The first draft also banned
    # "rc == 0" anywhere before that line, which is wrong: run_one
    # legitimately reads the exit code two lines up to set PASS/FAIL. A
    # check that forbids a correct line is a check someone deletes. The
    # gate cannot return through the predicate anyway - stopped_early takes
    # only its output text, pinned by t_the_rc_gate_is_gone.
    assert body.count("r.truncated") == 1, (
        "truncation is decided in more than one place in run_one - one of "
        "them will drift")


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
    print("group finished: a suite that reached its own end is left alone")
    check("a green suite is not flagged, in all three conventions",
          t_a_green_suite_is_not_flagged)
    check("a suite with no ok lines at all is not flagged",
          t_a_suite_with_no_ok_lines_is_not_flagged)
    print("group finished-with-failures: the one a careless fix gets wrong")
    check("a catching suite that failed is not branded truncated",
          t_a_catching_suite_that_failed_is_not_branded_truncated)
    check("...and the recogniser keys on the summary, not the word FAILED",
          t_the_failure_summary_is_not_matched_by_an_ok_line_alone)
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
