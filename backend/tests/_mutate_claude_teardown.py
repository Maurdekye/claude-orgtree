"""Mutation harness for test_claude_teardown.py — the claude leg's turn
teardown.

Team charter §2/§3: an instrument reporting "nothing wrong" must prove it can
report something, and the worst defect is a guard that reads correctly, runs,
and means nothing. Three of that suite's eight checks are green on the UNFIXED
code as well (they are the controls), and one more — the publish gate — cannot
go red on the unfixed code either, because the fixture's CLI always dies. So
every claim that matters gets a mutant that restores the exact behaviour it
was written against; the gate's mutant SURVIVED a round before §4 existed,
which is why §4 exists.

One behaviour is rewritten at a time in a COPY of the tree; nothing here
touches the working tree, so the copy's line endings are irrelevant.

Two controls make the rest mean anything:
  NOOP    one comment word changed        must SURVIVE (else the suite is
                                          environment-sensitive and every kill
                                          below is noise)
  SANITY  the record is never raised      must DIE (else the suite is not
                                          running the code under test)

    python backend/tests/_mutate_claude_teardown.py
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
SUP = "orgtree/supervisor.py"

# ⚠ `if proc.poll() is not None:` appears TWICE in supervisor.py, so that
# anchor carries the line under it. The `live=True` raise is anchored on
# `owner=wp_turn or proc`, which is this leg's token and no other's.
_PUBLISH = ("                                exited=proc.poll() is not None)")
_RAISE = ("            warmpool._set_proc_lifecycle(slug, nid, live=True,\n"
          "                                         owner=wp_turn or proc, "
          "adopt=True)")
_ORPHANS = ('                        orphans = [(t, d, bg_out.get(t, "")) '
            "for t, d\n"
            "                                   in bg_live.items()]")

# (name, relative file, original snippet, replacement, check label that must
#  fail — "" means the mutant must SURVIVE)
MUTANTS: list[tuple[str, str, str, str, str]] = [
    (
        "NOOP-CONTROL-comment-only",
        SUP,
        "                    # and `st[\"proc\"]` is the handle ⏸ acts on.",
        "                    # and `st[\"proc\"]` is the handle ⏸ acts on "
        "(noop control).",
        "",
    ),
    (
        "SANITY-CONTROL-the-record-is-never-raised",
        SUP,
        _RAISE,
        "            pass",
        "an ordinary cold turn clears the record",
    ),

    # ---- what §2 measured: a death published for a process still running ----
    (
        "an-unobserved-exit-is-published-anyway",
        SUP,
        _PUBLISH,
        "                                exited=True)",
        "outlives the teardown's bounded kill",
    ),
    (
        # the shared publisher itself, mutated once: this lane's own
        # surviving-process check must feel it
        "the-shared-publisher-stops-checking-the-exit",
        "orgtree/warmpool.py",
        "    if not exited:\n        return",
        "    if False:\n        return",
        "outlives the teardown's bounded kill",
    ),
    (
        "the-surviving-process-is-never-ended",
        SUP,
        "                            if proc.poll() is None:\n"
        "                                _wd_kill_tree(proc)",
        "                            if False:\n"
        "                                _wd_kill_tree(proc)",
        "a stdout-loop exception ends the process",
    ),

    # ---- what §3 measured: FAIL LOUD skipped by a raise above it ----
    (
        "the-orphan-sweep-is-skipped-when-the-cleanup-raised",
        SUP,
        _ORPHANS,
        "                        orphans = [] if sys.exc_info()[0] "
        "is not None else [\n"
        '                            (t, d, bg_out.get(t, "")) for t, d\n'
        "                            in bg_live.items()]",
        "the orphan sweep still fires",
    ),
    (
        "the-lifecycle-block-is-skipped-when-the-capture-raised",
        SUP,
        "                    finally:\n"
        "                        if not parked:",
        "                    finally:\n"
        "                        if not parked and sys.exc_info()[0] is None:",
        "the lifecycle record is still cleared on that same exit",
    ),
    (
        "the-state-flags-run-after-the-capture-again",
        SUP,
        "                    with _state_lock:\n"
        '                        st["proc"] = None',
        "                    _mcp_tool_surface_for_owner(slug, nid, proc)\n"
        "                    with _state_lock:\n"
        '                        st["proc"] = None',
        "still clears the process handle",
    ),
]


def run_suite(root: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["ORGTREE_DATA"] = os.path.join(root, "throwaway-data")
    os.makedirs(env["ORGTREE_DATA"], exist_ok=True)
    r = subprocess.run(
        [sys.executable,
         os.path.join(root, "tests", "test_claude_teardown.py")],
        capture_output=True, text=True, env=env, cwd=root, timeout=900,
        encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def failed_labels(out: str) -> list[str]:
    """Every red check's label, from BOTH places the suite writes one.

    ⚠ The live `  FAIL     <label>` line is printed while the fixture's own
    background threads are still writing `[orgtree] …` lines to the same
    stream, and an interleaved write can corrupt it. That is not theoretical:
    one round here reported a mutant SURVIVED, and the same mutant applied by
    hand to the same tree killed its named check every time afterwards. The
    end-of-run `FAILED: <label>` block is written after every turn has ended,
    so it is the quieter of the two; read both and de-duplicate.
    """
    seen: list[str] = []
    for pat in (r"^  FAIL\s+(.+)$", r"^FAILED: (.+)$"):
        for x in re.findall(pat, out, re.M):
            if x.strip() not in seen:
                seen.append(x.strip())
    return seen


def main() -> int:
    print("baseline (unmutated copy) ...")
    base = tempfile.mkdtemp(prefix="mut-cltd-base-")
    try:
        root = os.path.join(base, "backend")
        shutil.copytree(BACKEND, root,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        code, out = run_suite(root)
        if code != 0 or "FAILED" in out:
            print(out[-4000:])
            print("BASELINE IS NOT GREEN — every mutant result below would be "
                  "meaningless. Stopping.")
            return 2
        print("  " + (re.findall(r"^\d+ checks passed$", out, re.M)
                      or ["green"])[0])
    finally:
        shutil.rmtree(base, ignore_errors=True)

    bad: list[str] = []
    for name, rel, old, new, must_fail in MUTANTS:
        tmp = tempfile.mkdtemp(prefix="mut-cltd-")
        try:
            root = os.path.join(tmp, "backend")
            shutil.copytree(BACKEND, root,
                            ignore=shutil.ignore_patterns("__pycache__",
                                                          "*.pyc"))
            path = os.path.join(root, rel.replace("/", os.sep))
            with open(path, encoding="utf-8") as f:
                src = f.read()
            if src.count(old) != 1:
                print(f"  ! {name}: the target snippet appears "
                      f"{src.count(old)} times — the harness is stale, not the "
                      f"code. FIX THIS HARNESS.")
                bad.append(name)
                continue
            with open(path, "w", encoding="utf-8") as f:
                f.write(src.replace(old, new))
            _code, out = run_suite(root)
            labels = failed_labels(out)
            if not must_fail:                       # the NOOP control
                if labels:
                    print(f"  ! {name}: a comment-only change went RED "
                          f"({labels}) — the suite is not deterministic and "
                          f"every kill below is noise.")
                    bad.append(name)
                else:
                    print(f"  survived {name}  (as required)")
                continue
            if _code != 0 and not labels:
                # rc says the suite failed and nothing parsed: the READING is
                # broken, not the mutant. Never let that read as "survived".
                print(f"  ! {name}: the suite exited {_code} but printed no "
                      f"parseable FAIL label. Re-run and read its output.")
                bad.append(name)
                continue
            hit = [x for x in labels if must_fail in x]
            if not labels:
                print(f"  SURVIVED {name}: the suite still passed. The check "
                      f"for {must_fail!r} does not test this.")
                bad.append(name)
            elif not hit:
                print(f"  MISDIRECTED {name}: red, but not on {must_fail!r} — "
                      f"got {labels}")
                bad.append(name)
            else:
                print(f"  killed   {name}  ->  FAIL {hit[0][:60]}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    if bad:
        print(f"\n{len(bad)} mutant(s) not properly handled: {bad}")
        return 1
    print(f"\nall {len(MUTANTS)} mutants behaved as required "
          f"(1 noop survived, {len(MUTANTS) - 1} killed by their named check)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
