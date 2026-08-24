"""Mutation harness for test_extern_handle_attach.py.

Not a test — a check on the checks. A suite that goes green proves nothing
until you have watched each guard FAIL for its own reason. Every mutant below
names the check it must kill; a mutant that kills nothing, or kills the wrong
check, is reported as a MISS.

The CONTROL PAIR is the point:
  * NO-OP  — a real edit to the same lines that changes no behaviour. It must
             SURVIVE. If it "dies", the suite is keying on text, not conduct.
  * SANITY — an obviously broken mutant that must DIE. If it survives, the
             harness is not running the code it thinks it is.

    python backend/tests/_mutate_handles.py

Restores every file from git after each run (the worktree must be clean).
"""

import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "backend" / "orgtree" / "ledger.py"
API = ROOT / "backend" / "orgtree" / "api.py"
SUITE = ROOT / "backend" / "tests" / "test_extern_handle_attach.py"

# (name, file, find, replace, must-kill-this-check-or-None-for-survive)
MUTANTS = [
    ("NO-OP CONTROL: rename a local in the write block",
     LEDGER,
     'if want_handles is not None:\n            # REPLACE',
     'if want_handles is not None:  # noqa\n            # REPLACE',
     None),

    ("SANITY CONTROL: attach becomes a no-op",
     LEDGER,
     'if want_handles:\n                n["external_handles"] = want_handles',
     'if False:\n                n["external_handles"] = want_handles',
     "a node hired without a handle can be given one"),

    ("drop external_handles from the self-retool fence",
     LEDGER,
     '("external_handles", external_handles)) if v is not None]',
     ')) if v is not None]',
     "a self-retool may NOT carry external_handles"),

    ("attach APPENDS instead of replacing",
     LEDGER,
     'n["external_handles"] = want_handles',
     'n["external_handles"] = (n.get("external_handles") or []) + want_handles',
     "set_scope REPLACES the handle set"),

    ("None is treated as a clear",
     LEDGER,
     'if want_handles is not None:',
     'if True:',
     "None leaves an existing grant untouched"),

    ("validation moves AFTER the writes (atomicity broken)",
     LEDGER,
     'want_handles: list[str] | None = None\n        if external_handles is not None:\n'
     '            want_handles = norm_extern_handles(external_handles, where="retool")',
     'want_handles: list[str] | None = None\n        if external_handles is not None:\n'
     '            want_handles = list(external_handles)',
     "a bad handle blocks the charter beside it"),

    ("the cap stops being enforced",
     LEDGER,
     'if len(handles) > MAX_EXTERN_HANDLES:',
     'if False:',
     "validator caps the count (and the boundary is legal)"),

    ("the @mcp: form stops being enforced",
     LEDGER,
     'if not (h.startswith("@mcp:")',
     'if not (h.startswith("@")',
     "validator rejects every non-@mcp: form"),

    ("handles vanish from the read surface",
     LEDGER,
     '"external_handles": n.get("external_handles") or [],',
     '"external_handles__x": n.get("external_handles") or [],',
     "tree() exposes the handle set"),

    ("the kiosk scrub stops dropping handles",
     API,
     'n.pop("external_handles", None)',
     'pass  # n.pop("external_handles", None)',
     "_scrub_public drops handles from kiosk trees"),
]


def restore():
    subprocess.run(["git", "-C", str(ROOT), "checkout", "--",
                    "backend/orgtree/ledger.py", "backend/orgtree/api.py"],
                   check=True, capture_output=True)


def run_suite():
    """Return (ok, set-of-check-labels-that-passed)."""
    p = subprocess.run([sys.executable, str(SUITE)],
                       capture_output=True, text=True, cwd=str(ROOT / "backend"))
    passed = set(re.findall(r"ok\s+\d+\s+(.*)", p.stdout))
    return p.returncode == 0, passed


def main():
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain",
                            "backend/orgtree"], capture_output=True, text=True).stdout
    if dirty.strip():
        sys.exit("refusing to run: backend/orgtree is dirty — commit first\n" + dirty)

    ok, baseline = run_suite()
    if not ok:
        sys.exit("baseline suite is RED — fix that before mutating")
    print(f"baseline: {len(baseline)} checks green\n")

    misses = []
    for name, path, find, repl, must_kill in MUTANTS:
        src = path.read_text(encoding="utf-8")
        if find not in src:
            misses.append(f"{name}: PATTERN NOT FOUND (mutant never applied)")
            print(f"  ?? {name}\n     pattern not found — mutant is vacuous")
            continue
        # ⚠ AN AMBIGUOUS ANCHOR IS A COIN FLIP, NOT A MUTANT. `replace(f, r, 1)`
        # takes the FIRST match, which may be in a different function entirely
        # — the mutant then either reports a kill of unrelated checks, or
        # SURVIVES while appearing to have been applied. Seen three times on
        # feat/multi-account-phase2; the first two were caught by luck and the
        # third by this check's absence being noticed. It is not overhead.
        if src.count(find) > 1:
            misses.append(f"{name}: AMBIGUOUS PATTERN — matches "
                          f"{src.count(find)} places; would mutate the FIRST")
            print(f"  ?? {name}\n     ambiguous anchor ({src.count(find)} "
                  f"matches) — refusing to guess")
            continue
        path.write_text(src.replace(find, repl, 1), encoding="utf-8")
        try:
            ok, passed = run_suite()
            killed = baseline - passed
            if must_kill is None:
                if ok:
                    print(f"  ✓ SURVIVED  {name}")
                else:
                    misses.append(f"{name}: no-op control DIED (killed {sorted(killed)})")
                    print(f"  ✗ {name}\n     no-op control died — suite keys on text, "
                          f"not conduct: {sorted(killed)}")
            else:
                if must_kill in killed:
                    extra = killed - {must_kill}
                    note = f" (+{len(extra)} more)" if extra else ""
                    print(f"  ✓ KILLED by “{must_kill}”{note}\n     {name}")
                elif not killed:
                    misses.append(f"{name}: SURVIVED — nothing guards it")
                    print(f"  ✗ {name}\n     SURVIVED — no check covers this")
                else:
                    misses.append(f"{name}: killed the WRONG check {sorted(killed)}")
                    print(f"  ✗ {name}\n     expected “{must_kill}”, "
                          f"actually killed {sorted(killed)}")
        finally:
            restore()

    print()
    if misses:
        print(f"{len(misses)} PROBLEM(S):")
        for m in misses:
            print("  - " + m)
        sys.exit(1)
    print(f"all {len(MUTANTS)} mutants behaved as specified "
          f"(1 no-op survived, {len(MUTANTS) - 1} died to their named checks)")


if __name__ == "__main__":
    main()
