"""Can this repo tell a KILLED test run from a passing one? — the acceptance suite.

Diagnosis 2026-08-26 (`task-timeouts`), measured rather than argued: work an
agent starts as a harness *background task* is a child of that turn's CLI, and
the supervisor closes that CLI's stdin at every turn boundary — so the job dies
when the turn ends, and so does any waiter started the same way. Two tier runs
died that way at 49 s and 36 s, having completed 19 and 25 suites with **zero
failures between them**. Their stdout was a column of `✓` and nothing else, and
a deploy was gated on one of them.

Nothing timed out. The point of this suite is not the killing — it is that the
killing was INVISIBLE. `tools/run_tests.py` now ends a run with two artefacts
written together (`emit_completion`): a final `RUN COMPLETE …` stdout line and
a `COMPLETE` file in the log directory. Their absence is the signal.

    §1  a run that finishes says so — both artefacts, and they agree
    §2  a run that is KILLED mid-flight produces NEITHER
    §3  the retroactive discriminator — log count vs the plan header — which
        is the only one that works on the ~800 runs already on disk
    §4  a run that FAILED still finished: rc travels in the line, not in the
        marker's presence

⚠ §2 IS THE CHECK THAT CAN FAIL, and it is worthless unless the kill actually
landed on a run that was actually running. So it proves both before it reads
the result: the child is confirmed gone, and its captured stdout is confirmed
to show a run in progress. A kill that missed, or a run that never started,
would BOTH leave the marker absent and pass a naive version of this check for
entirely the wrong reason.

This suite shells out to the real runner — no mocks. It is deliberately cheap:
the completing run is one fast suite, and the killed run is stopped the moment
it proves it started.

    python backend/tests/test_run_completion.py [-v]
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
RUNNER = os.path.join(REPO, "tools", "run_tests.py")
MARKER = "COMPLETE"
VERBOSE = "-v" in sys.argv

PASS = 0
FAIL: list[tuple[str, str]] = []


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


def _env():
    """The runner's own rule, applied to the runner: no child of this suite may
    inherit a pointer at the operator's live data directory."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("ORGTREE_")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _kill_tree(proc) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       capture_output=True)
    else:
        import signal
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:                            # pragma: no cover
        proc.kill()


# ═══════════════════════════════════════════ §1  a finished run says so

COMPLETE_RE = re.compile(
    r"^RUN COMPLETE\s+suites=(\d+)/(\d+)\s+passed=(\d+)\s+failed=(\d+)\s+"
    r"skipped=(\d+)\s+rc=(\d+)\s+wall=", re.M)

_done: dict[str, object] = {}


def sec_completes() -> None:
    print("\n§1  a run that finishes says so")
    logdir = tempfile.mkdtemp(prefix="orgtree-runcomplete-ok-")
    p = subprocess.run(
        [sys.executable, RUNNER, "--only", "resolver", "--logdir", logdir],
        cwd=REPO, env=_env(), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600)
    out = p.stdout or ""
    _done["out"], _done["logdir"], _done["rc"] = out, logdir, p.returncode
    if VERBOSE:
        print(out)

    check("the run exited 0",
          lambda: _eq(p.returncode, 0, f"rc={p.returncode}\n{out[-800:]}"))
    check("stdout carries a RUN COMPLETE line",
          lambda: _true(COMPLETE_RE.search(out), f"no RUN COMPLETE in:\n{out}"))
    check(f"the {MARKER} marker is in the log directory",
          lambda: _true(os.path.exists(os.path.join(logdir, MARKER)),
                        f"{MARKER} missing from {logdir}: "
                        f"{sorted(os.listdir(logdir))}"))

    def _last_line():
        live = [ln for ln in out.splitlines() if ln.strip()]
        _true(live[-1].startswith("RUN COMPLETE"),
              f"last non-blank stdout line was {live[-1]!r}")
    check("RUN COMPLETE is the LAST line — nothing prints after it", _last_line)

    def _agrees():
        m = COMPLETE_RE.search(out)
        ran, planned, passed, failed, _skipped, rc = (int(g) for g in m.groups())
        _eq(ran, planned, f"suites={ran}/{planned} — a finished run ran its plan")
        _eq(ran, 1, "--only resolver plans exactly one suite")
        _eq(passed, 1, "resolver passed")
        _eq(failed, 0, "no failures")
        _eq(rc, p.returncode, "rc in the line matches the exit status")
    check("the line agrees with the plan and with the exit status", _agrees)

    def _marker_agrees():
        with open(os.path.join(logdir, MARKER), encoding="utf-8") as fh:
            body = fh.read()
        _true(body.startswith("RUN COMPLETE"),
              f"{MARKER} does not lead with the summary line: {body[:120]!r}")
        _true(COMPLETE_RE.search(out).group(0) in body,
              "the marker file and stdout disagree about the same run")
    check("the marker file repeats the same line, so either alone suffices",
          _marker_agrees)


# ═══════════════════════════════════════════ §2  a killed run produces NEITHER

def sec_killed() -> None:
    print("\n§2  a run KILLED mid-flight produces neither artefact")
    logdir = tempfile.mkdtemp(prefix="orgtree-runcomplete-kill-")
    outfile = os.path.join(logdir, "_stdout.txt")

    # `limit-freeze` is a parallel-lane suite (no listener, no container) that
    # takes ~30 s — long enough to be caught in the act. The runner's own
    # classification is what makes a second concurrent copy safe.
    with open(outfile, "w", encoding="utf-8") as fh:
        kw = {} if os.name == "nt" else {"start_new_session": True}
        proc = subprocess.Popen(
            [sys.executable, RUNNER, "--only", "limit-freeze",
             "--logdir", logdir],
            cwd=REPO, env=_env(), stdout=fh, stderr=subprocess.STDOUT, **kw)

        # ⚠ PROOF THE RUN WAS RUNNING. Wait for the plan header rather than
        # sleeping a fixed amount: a fixed sleep on a loaded machine kills a
        # process that never got started, and "marker absent" would then pass
        # for the wrong reason entirely.
        started, deadline = "", time.time() + 120
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            with open(outfile, encoding="utf-8", errors="replace") as rf:
                started = rf.read()
            if "plan · 1 to run" in started:
                break
            time.sleep(0.2)

    check("the run had started — its plan header reached stdout",
          lambda: _true("plan · 1 to run" in started,
                        f"never saw the plan header; stdout was:\n{started!r}"))
    check("…and it was still alive at the moment of the kill",
          lambda: _true(proc.poll() is None,
                        f"the run had already exited (rc={proc.poll()}) — "
                        f"nothing was killed, so this section proves nothing"))

    _kill_tree(proc)

    check("the kill landed — the process is gone",
          lambda: _true(proc.poll() is not None,
                        "the runner survived taskkill /T /F"))

    with open(outfile, encoding="utf-8", errors="replace") as rf:
        out = rf.read()
    if VERBOSE:
        print(out)

    check("the killed run printed NO RUN COMPLETE line",
          lambda: _true(not COMPLETE_RE.search(out),
                        f"a killed run claimed completion:\n{out[-600:]}"))
    check(f"the killed run wrote NO {MARKER} marker",
          lambda: _true(not os.path.exists(os.path.join(logdir, MARKER)),
                        f"a killed run left a {MARKER} marker — the marker lies"))

    def _looks_like_a_pass():
        """The hazard itself, pinned. If this ever stops being true the
        mechanism above is no longer needed — but until then, a killed run
        really is a column of ticks with nothing to say it was cut."""
        _true(not re.search(r"[✗✖]|FAIL", out),
              "this killed run happens to show a failure, so it is not the "
              "silent shape this suite is about — pick a different suite")
    check("…and is otherwise indistinguishable: no ✗, no error, nothing",
          _looks_like_a_pass)


# ═══════════════════════════════════════════ §3  the retroactive discriminator

PLAN_RE = re.compile(r"^plan · (\d+) to run", re.M)


def sec_retroactive() -> None:
    print("\n§3  log count vs the plan header — works on runs already on disk")
    out, logdir = str(_done["out"]), str(_done["logdir"])

    check("the plan header states how many suites were planned",
          lambda: _true(PLAN_RE.search(out), "no `plan · N to run` header"))

    def _counts_match():
        planned = int(PLAN_RE.search(out).group(1))
        logs = [f for f in os.listdir(logdir) if f.endswith(".log")]
        _eq(len(logs), planned,
            f"{len(logs)} suite logs for a plan of {planned}")
    check("a finished run leaves one suite log per planned suite", _counts_match)

    def _written_on_completion():
        """`run_one` writes a suite's log only after that suite has finished,
        which is what makes the count a measure of PROGRESS rather than of
        intent. Pinned here because a refactor that opened the log up front —
        to stream into it, say — would silently destroy the only instrument
        that works on a past run."""
        src = open(RUNNER, encoding="utf-8", errors="replace").read()
        body = src[src.index("def run_one("):src.index("# ----", src.index(
            "def run_one("))]
        write_at = body.index("suite.id + \".log\"")
        _true(body.index("proc.communicate") < write_at,
              "run_one now opens the suite log before the suite has run — the "
              "log count no longer measures how far a run got")
    check("suite logs are written on completion, not on start",
          _written_on_completion)


# ═══════════════════════════════════════════ §4  failed ≠ unfinished

def sec_failed_still_finished() -> None:
    print("\n§4  a run that FAILED still finished")

    def _not_gated_on_bad():
        src = open(RUNNER, encoding="utf-8", errors="replace").read()
        # ⚠ the CALL, not the `def` — `emit_completion(logdir` alone matches
        # the definition first, which sits at indent 0 and would make this
        # check pass no matter where the call actually moved to
        i = src.index("emit_completion(logdir, len(run)")
        # the call must sit at function level in main(), not inside `if bad:`
        line = src[src.rindex("\n", 0, i) + 1:i]
        _eq(len(line) - len(line.lstrip()), 4,
            f"emit_completion is indented {len(line) - len(line.lstrip())} "
            f"spaces — it looks nested inside a branch, so some runs would "
            f"finish without saying so")
    check("emit_completion is unconditional, not inside the failure branch",
          _not_gated_on_bad)

    def _rc_carries_it():
        m = COMPLETE_RE.search(str(_done["out"]))
        _true("failed=" in m.group(0) and "rc=" in m.group(0),
              "the completion line must carry the verdict, since its mere "
              "presence no longer does")
    check("the verdict travels in the line (failed=, rc=), not in its presence",
          _rc_carries_it)


# ═══════════════════════════════════════════════════════════════════ helpers

def _true(cond, msg) -> None:
    if not cond:
        raise AssertionError(msg)


def _eq(got, want, msg) -> None:
    if got != want:
        raise AssertionError(f"{msg}   (got {got!r}, want {want!r})")


# ═════════════════════════════════════════════════════════════════════════ main

def main() -> None:
    print("═══ killed run vs passing run — the acceptance suite ═══")
    sec_completes()
    sec_killed()
    sec_retroactive()
    sec_failed_still_finished()

    print(f"\n{'═' * 70}\n{PASS} checks passed, {len(FAIL)} failed")
    for label, tb in FAIL:
        print(f"\nFAIL  {label}\n{tb}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
