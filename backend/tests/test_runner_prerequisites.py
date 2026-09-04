"""A missing mandatory suite makes the aggregate runner fail loudly.

    python backend/tests/test_runner_prerequisites.py

The canary replaces the prerequisite policy with its old value (no blocked
skips) and requires the same synthetic tier to exit 0. The fixed arm then
requires exit 1. That value replacement proves the check distinguishes the
old and new behaviours rather than merely observing a runner error.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "tools"))

import run_tests as rt  # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []


def check(label, fn):
    global PASS
    try:
        fn()
    except Exception:  # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, want {want!r}")


def run(fake_policy=None, no_frontend=False):
    """Run one real passing child beside one dependency-blocked frontend."""
    root = tempfile.mkdtemp(prefix="orgtree-runner-prereq-")
    backend = rt.Suite(
        "control", [sys.executable, "-c", "print('1 checks passed')"],
        [sys.executable, "-c", "print('1 checks passed')"], root)
    frontend = rt.Suite(
        "frontend", ["node", "tests/run.mjs"],
        ["node", "tests/run.mjs"], root,
        skip="frontend/node_modules is missing — run `npm ci` in frontend/")

    real_discover = rt.discover
    real_policy = rt.required_skip_failures
    old_argv = sys.argv
    rt.discover = lambda _py: [backend, frontend]
    if fake_policy is not None:
        rt.required_skip_failures = fake_policy
    sys.argv = ["run_tests.py", "--serial", "--logdir", root]
    if no_frontend:
        sys.argv.append("--no-frontend")
    out = io.StringIO()
    # ⚠ MINT A SCRATCH ORGTREE_DATA BEFORE DRIVING THE RUNNER. `rt.main()`
    # runs IN THIS PROCESS, and `run_tests.py` strips every ORGTREE_* from the
    # suites it launches — so without this the runner sees no data root and
    # REFUSES, exit 2, the moment `store.py` defaults to sqlite. That refusal
    # is correct and this suite was the thing at fault: it drove the runner
    # while relying on the default root being harmless, which under JSON it
    # was and under SQLite it is not (an unrooted sqlite claim migrates
    # ~/orgtree). The guard did not break this suite; it revealed the
    # assumption. `root` is already this case's own temp directory.
    # (sqlite-review, 2026-09-04, found by the full-suite flip diff.)
    had = os.environ.get("ORGTREE_DATA")
    os.environ["ORGTREE_DATA"] = root
    try:
        with contextlib.redirect_stdout(out):
            rc = rt.main()
    finally:
        if had is None:
            os.environ.pop("ORGTREE_DATA", None)
        else:
            os.environ["ORGTREE_DATA"] = had
        rt.discover = real_discover
        rt.required_skip_failures = real_policy
        sys.argv = old_argv
    return rc, out.getvalue()


def main():
    print("§1 canary — the old value really produces a false-green tier")
    old_rc, old_out = run(fake_policy=lambda _skipped: [])
    check("the planted old policy exits 0", lambda: eq(old_rc, 0, old_out))
    check("the control suite really ran and passed",
          lambda: eq("control                   PASS" in old_out, True, old_out))
    check("the frontend was genuinely skipped for missing dependencies",
          lambda: eq("node_modules is missing" in old_out, True, old_out))

    print("\n§2 fixed — the same tier is red and says exactly why")
    rc, out = run()
    check("a mandatory dependency skip exits 1", lambda: eq(rc, 1, out))
    check("the summary calls the frontend BLOCKED, not passed",
          lambda: eq("frontend                  BLOCKED" in out, True, out))
    check("the loud reason is adjacent to the red result",
          lambda: eq("REQUIRED SUITE BLOCKED" in out
                     and "run `npm ci` in frontend/" in out, True, out))
    check("the completion contract carries rc=1",
          lambda: eq("RUN COMPLETE" in out and "rc=1" in out, True, out))

    print("\n§3 explicit opt-out remains intentional and green")
    opt_rc, opt_out = run(no_frontend=True)
    check("--no-frontend exits 0", lambda: eq(opt_rc, 0, opt_out))
    check("the reason is the explicit flag, not missing dependencies",
          lambda: eq("--no-frontend" in opt_out
                     and "REQUIRED SUITE BLOCKED" not in opt_out, True, opt_out))

    if FAIL:
        print(f"\n{len(FAIL)} FAILED")
        for label, tb in FAIL:
            print(f"\n--- {label} ---\n{tb}")
        raise SystemExit(1)
    print(f"\nALL {PASS} CHECKS PASS")


if __name__ == "__main__":
    main()
