"""Mutation harness for the docket's STATUS CLOCK (`status_at`).

    python backend/tests/mutate_status_clock.py

Every failure this clock can have is a quiet one. Break any of it and the
docket still lists, still sorts, still shows a sensible-looking timestamp —
the ordering is simply wrong in a way nobody can see without knowing what the
right answer was. A green suite proves nothing on its own, because the state
BEFORE this feature is green too. Each mutant restores one silence and
requires the named check to go red.

⚠ RUN INSIDE A WORKTREE ONLY. It rewrites `ledger.py` in place and restores
the exact BYTES in a finally block. The file is CRLF and the patterns here are
written LF, so both sides are converted before the search — a byte-for-byte
search against LF patterns finds NOTHING and every mutant reports as skipped,
which looks like a clean run if you are not reading closely.

⚠ TWO MUTANTS IN THIS FILE WERE MALFORMED WHEN FIRST WRITTEN — they patched a
COMMENT next to the call instead of the call, left the code working, and duly
reported SURVIVED. That is the failure mode to watch for here: a mutant that
does not actually break anything is indistinguishable, in the output, from a
check that does not work. If one survives, read the diff it applied before
believing the check is at fault.
"""
from __future__ import annotations

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
LEDGER = os.path.join(ROOT, "backend", "orgtree", "ledger.py")
SUITE = os.path.join(ROOT, "backend", "tests", "test_work_items.py")

MUTANTS: list[tuple[str, str, str, str]] = [
    (
        # THE PLAIN SILENCE: a real transition stops being recorded, so
        # "most recently changed state" quietly becomes creation order.
        "an update stops stamping — a transition looks like a note",
        '            self._work_stamp_status(it)\n        if it.get("status") == "blocked":',
        '        if it.get("status") == "blocked":',
        "moves on a transition",
    ),
    (
        # THE OPPOSITE ERROR, and the subtler one: every update that MENTIONS
        # a status stamps, so a progress note that restates `in_progress`
        # reads as a state change.
        "a RESTATED status stamps too — any mention counts as a change",
        '        if status is not None and status != it.get("status"):',
        '        if status is not None:',
        "moves on a transition",
    ),
    (
        # the transitions nobody types a status to reach
        "accept stops stamping",
        '        it["status"] = "done"\n        self._work_stamp_status(it)\n',
        '        it["status"] = "done"\n',
        "accept, reopen and a user dismissal",
    ),
    (
        "a user's dismissal stops stamping",
        '        self._work_stamp_status(it)\n        it["blocked_reason"] = f"attention flag dismissed',
        '        it["blocked_reason"] = f"attention flag dismissed',
        "accept, reopen and a user dismissal",
    ),
    (
        # ⚠ THE LIE THE WHOLE DERIVATION EXISTS TO AVOID. `updated_at` moves
        # for edits, so an item nobody has transitioned in weeks sorts as
        # "just changed" because someone fixed its title.
        "legacy derivation falls back to the edit clock",
        '        return str(it.get("at") or "")\n',
        '        return str(it.get("updated_at") or it.get("at") or "")\n',
        "legacy item derives",
    ),
    (
        # the folded summary row carries no `op`; reading it as a transition
        # dates a state change to whenever the fold happened to run
        "the folded summary row is read as a status change",
        '            if row.get("kind") == "folded":\n                continue\n',
        '            if row.get("kind") == "folded":\n                return str(row.get("at") or "")\n',
        "folded history row",
    ),
]


def crlf(s: str) -> bytes:
    return s.replace("\n", "\r\n").encode("utf-8")


def run_suite() -> tuple[bool, str]:
    r = subprocess.run([sys.executable, SUITE], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode != 0, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    original = open(LEDGER, "rb").read()

    print("baseline — the work-items suite must be GREEN before anything is mutated")
    red, out = run_suite()
    if red:
        print("  BASELINE RED — fix that first, not this file")
        print("\n".join(out.splitlines()[-30:]))
        return 2
    print("  ok")

    survived = 0
    try:
        for name, frm, to, kills in MUTANTS:
            f, t = crlf(frm), crlf(to)
            n = original.count(f)
            if n != 1:
                print(f"SKIPPED (target found {n}x, expected 1) — {name}")
                print("  the harness is stale, NOT a pass")
                survived += 1
                continue
            open(LEDGER, "wb").write(original.replace(f, t))
            red, out = run_suite()
            named = kills in out and "FAIL" in out
            if red and named:
                print(f"killed   — {name}")
            elif red:
                print(f"WRONG CHECK — {name}")
                print(f"  the suite went red but {kills!r} is not among the failures")
                survived += 1
            else:
                print(f"SURVIVED — {name}")
                survived += 1
    finally:
        open(LEDGER, "wb").write(original)
        same = open(LEDGER, "rb").read() == original
        print(f"restored exact bytes: {same}")
        if not same:                       # never seen; loud if it ever is
            print("⚠ THE SOURCE WAS NOT RESTORED — fix that before anything else")

    print(f"\n{len(MUTANTS) - survived}/{len(MUTANTS)} killed")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
