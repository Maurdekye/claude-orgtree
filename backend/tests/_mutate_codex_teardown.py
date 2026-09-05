"""Mutation harness for §8 of test_codex_dispatch.py — the codex leg's
process-lifecycle record at turn teardown.

Team charter §2/§3: a check that reports "nothing wrong" must prove it can
report something, and the worst defect here is a guard that reads correctly,
runs, and means nothing. Two of §8's checks (the park control and the
"process outlived its teardown" check) are green on the UNFIXED code as well,
which is exactly why they need mutants of their own: without them they would
be decoration.

One behaviour is rewritten at a time in a COPY of the tree; nothing here
touches the working tree, so the copy's line endings are irrelevant.

Two controls make the rest mean anything:
  NOOP    one comment word changed        must SURVIVE (else the suite is
                                          environment-sensitive and every kill
                                          below is noise)
  SANITY  the record is never raised      must DIE (else the suite is not
                                          running the code under test)

    python backend/tests/_mutate_codex_teardown.py
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

# ⚠ BOTH ANCHORS BELOW CARRY A CODEX-ONLY LINE ON PURPOSE. The antigravity leg
# holds the byte-identical `_set_proc_lifecycle(…, live=False, owner=turn)`
# pair at the same indentation, and both legs raise the record with a
# byte-identical `live=True … adopt=True` call — so the short forms match
# twice and this harness refuses them rather than mutating the wrong leg.
_PUBLISH = ("                warmpool.publish_observed_exit(\n"
            "                    slug, nid, turn,\n"
            "                    exited=turn.client.proc.poll() is not None)")
# the two legs' whole `with _state_lock:` block is identical from
# `st["responding"] = True` down, so the anchor has to start one line higher,
# at the leg-named turn handle.
_RAISE_KEEP = ('            st["codex_turn"] = turn   # the ⏸ escape hatch '
               "(interrupt_turn)\n"
               '            st["responding"] = True   # mail now steers '
               "instead of queueing\n"
               '            # D-236: the turn is the baseline for "how long '
               "has this\n"
               "            # agent gone without an injection point\". Seeded "
               "here so a\n"
               "            # first tool call that never returns is measurable "
               "too.\n"
               '            st["boundary_at"] = time.time()\n'
               '            st["boundary_polls"] = 0')
_RAISE = (_RAISE_KEEP + "\n"
          "        warmpool._set_proc_lifecycle(slug, nid, live=True, "
          "owner=turn,\n"
          "                                     adopt=True)")

# (name, relative file, original snippet, replacement, check label that must
#  fail — "" means the mutant must SURVIVE)
MUTANTS: list[tuple[str, str, str, str, str]] = [
    (
        "NOOP-CONTROL-comment-only",
        SUP,
        "                # PARKED IS UNTOUCHED: the pool owns that record. "
        "Otherwise",
        "                # PARKED IS UNTOUCHED (noop control): the pool owns "
        "that record. Otherwise",
        "",
    ),
    (
        "SANITY-CONTROL-the-record-is-never-raised",
        SUP,
        _RAISE,
        _RAISE_KEEP,
        "the record is raised for the owning turn",
    ),

    # ---- the defect this work fixes, restored exactly ----
    (
        "clear-skipped-when-the-teardown-raised",     # the original defect
        SUP,
        "            if not parked:\n"
        "                # PARKED IS UNTOUCHED",
        "            if not parked and sys.exc_info()[0] is None:\n"
        "                # PARKED IS UNTOUCHED",
        "the record clears when the cold client's close() raises",
    ),
    (
        "clear-dropped-entirely",
        SUP,
        _PUBLISH,
        "                pass",
        "the record clears when the cold client's close() raises",
    ),
    (
        "clear-runs-with-a-token-that-can-never-match",   # present and inert
        SUP,
        _PUBLISH,
        _PUBLISH.replace("slug, nid, turn,", "slug, nid, object(),"),
        "the record clears when the cold client's close() raises",
    ),

    # ---- the two halves of "truthful", each with its own check ----
    (
        "an-unobserved-exit-is-published-anyway",
        SUP,
        "                    exited=turn.client.proc.poll() is not None)",
        "                    exited=True)",
        "a process still running after the teardown keeps the record",
    ),
    (
        # the shared publisher itself: one mutant, and every lane's own
        # surviving-process check should feel it
        "the-shared-publisher-stops-checking-the-exit",
        "orgtree/warmpool.py",
        "    if not exited:\n        return",
        "    if False:\n        return",
        "a process still running after the teardown keeps the record",
    ),
    (
        "the-surviving-app-server-is-never-closed",
        SUP,
        "                if turn.client.proc.poll() is None:",
        "                if False:",
        "does not leak the app-server",
    ),
]


def run_suite(root: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["ORGTREE_DATA"] = os.path.join(root, "throwaway-data")
    os.makedirs(env["ORGTREE_DATA"], exist_ok=True)
    r = subprocess.run(
        [sys.executable, os.path.join(root, "tests", "test_codex_dispatch.py")],
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
    base = tempfile.mkdtemp(prefix="mut-cxtd-base-")
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
        tmp = tempfile.mkdtemp(prefix="mut-cxtd-")
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
                print(f"  killed   {name}  ->  FAIL {hit[0][:66]}")
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
