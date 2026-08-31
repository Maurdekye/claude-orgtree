"""Mutation harness for test_provider_limit_freeze.py (D-209).

The suite is all-green, and on THIS defect an all-green suite is the symptom
rather than the proof: the bug being fixed was a failed turn reporting itself
as a successful one. A detector that cannot be shown to fail is exactly the
instrument that let a Codex agent vanish for ten hours while every counter
said it was fine.

Each mutation is a VALUE REPLACEMENT in the shipped code — never a deleted
call, never a raised exception, because a mutant that dies with a NameError
proves only that the line executes, not that anything CHECKS it. Two controls
make the rest mean anything:

  · a NOOP (one comment word) must SURVIVE. If it dies, the suite is
    environment-sensitive and every "killed" below is noise.
  · a SANITY mutant (a nonsense status) must DIE. If it survives, the suite is
    not running the code under test at all.

Run:  python tests/_mutate_provider_limit.py
"""
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
CODEXRUN = os.path.join(BACKEND, "orgtree", "codexrun.py")
SUP = os.path.join(BACKEND, "orgtree", "supervisor.py")
SUITE = os.path.join(HERE, "test_provider_limit_freeze.py")

# (label, file, find, replace, what it breaks)
MUTANTS = [
    ("NOOP CONTROL — one comment word changed",
     CODEXRUN,
     "#: codex's own `TurnStatus` → ours (D-209).",
     "#: codex's own `TurnStatus` -> ours (D-209).",
     "MUST SURVIVE — nothing about behaviour changed"),

    ("SANITY CONTROL — every turn status normalizes to nonsense",
     CODEXRUN,
     '    return _TURN_STATUS.get(raw.strip(), STATUS_FAILED) if raw else (\n'
     '        STATUS_COMPLETED)\n',
     '    return "banana"  # MUTANT\n',
     "MUST DIE — proves the suite runs the code under test"),

    ("M1 — THE ORIGINAL DEFECT: a failed turn is a completed turn",
     CODEXRUN,
     '    "failed": STATUS_FAILED,\n',
     '    "failed": STATUS_COMPLETED,  # MUTANT\n',
     "the wall is booked as a successful turn and the agent goes quiet"),

    ("M2 — an UNKNOWN status is treated as success again",
     CODEXRUN,
     "    return _TURN_STATUS.get(raw.strip(), STATUS_FAILED) if raw else (\n"
     "        STATUS_COMPLETED)\n",
     "    return _TURN_STATUS.get(raw.strip(), STATUS_COMPLETED) if raw "
     "else (  # MUTANT\n"
     "        STATUS_COMPLETED)\n",
     "a status the protocol grows later silently vanishes turns again"),

    ("M3 — the CLI's own reason is discarded (the D2 half)",
     CODEXRUN,
     "                self._note_error(turn.get(\"error\"))\n",
     "                self._note_error(None)  # MUTANT\n",
     "nothing carries the limit prose to a classifier — no freeze at all"),

    ("M4 — error_text keeps the message but drops the machine tag",
     CODEXRUN,
     '    if code and code.lower() not in out.lower():\n',
     '    if False and code and code.lower() not in out.lower():  # MUTANT\n',
     "`usage_limit_exceeded` never reaches the durable record"),

    ("M5 — LAST-WINS rate-limit retention (the D3 half)",
     CODEXRUN,
     "    for snap in list(cast(\"dict[str, Any]\", snapshots).values()):\n",
     "    for snap in list(cast(\"dict[str, Any]\", snapshots).values())"
     "[-1:]:  # MUTANT\n",
     "the empty `premium` bucket wins and the real resetsAt is lost"),

    ("M6 — a window with room left is read as the wall",
     CODEXRUN,
     '        if float(win.get("usedPercent") or 0) < 100.0:\n'
     "            return None\n",
     '        if False and float(win.get("usedPercent") or 0) < 100.0:'
     "  # MUTANT\n"
     "            return None\n",
     "the agent parks on a deadline belonging to a limit it never reached"),

    ("M7 — the provider's machine reset is thrown away",
     SUP,
     '    if reset_ts is not None and now < reset_ts <= now + '
     "limits.MAX_HORIZON:\n"
     '        return reset_ts, "provider"\n',
     "    if False:  # MUTANT\n"
     '        return reset_ts, "provider"\n',
     "an exact 6-day reset degrades to a blind 5-minute probe"),

    ("M8 — the machine reset is believed unbanded",
     SUP,
     '    if reset_ts is not None and now < reset_ts <= now + '
     "limits.MAX_HORIZON:\n",
     "    if reset_ts is not None:  # MUTANT\n",
     "a reshaped field parks an agent past the longest real lane, silently"),

    ("M9 — the freeze door at the shared seam is shut",
     SUP,
     "        if isinstance(e, _ProviderTurnFailed) \\\n"
     "                and _looks_like_usage_limit(e.blob):\n",
     "        if False and isinstance(e, _ProviderTurnFailed) \\\n"
     "                and _looks_like_usage_limit(e.blob):  # MUTANT\n",
     "back to a bare error row: no freeze, no reset, no auto-resume"),

    ("M10 — CONTROL HALF: every provider failure freezes",
     SUP,
     "        if isinstance(e, _ProviderTurnFailed) \\\n"
     "                and _looks_like_usage_limit(e.blob):\n",
     "        if isinstance(e, _ProviderTurnFailed) \\\n"
     "                and True:  # MUTANT\n",
     "a sandbox denial parks the agent as though it were out of capacity"),

    # ⚠ the trailing comment line is LOAD-BEARING, not decoration. Without it
    # the target is `            fz["limit"] = True\n` — twelve spaces — which
    # is a SUBSTRING of the claude lane's own twenty-eight-space line 3000
    # lines earlier, so `replace(…, 1)` mutated a code path this suite does not
    # exercise and the mutant "survived". It looked like a hole in the suite
    # and was a hole in the harness. See the uniqueness guard in main().
    ("M11 — the freeze stops marking itself a LIMIT",
     SUP,
     '            fz["limit"] = True\n'
     "            # the CLI reported this itself",
     '            fz["limit"] = False  # MUTANT\n'
     "            # the CLI reported this itself",
     "ledger re-tags the record as a kiosk SPEND freeze and ▶ skips it "
     "forever"),

    ("M12 — the interrupted prompt is not kept for replay",
     SUP,
     '                fz.setdefault("resume_texts", []).append(replay[-8000:])'
     "\n",
     '                fz.setdefault("resume_texts", [])  # MUTANT\n',
     "the agent wakes with no idea what it was doing"),

    ("M13 — the account standing is folded on the success path only",
     SUP,
     "    for _snap in (list(_snaps.values()) if isinstance(_snaps, dict)\n"
     "                  else [res_raw.get(\"rate_limits\")]):\n",
     "    for _snap in ([] if isinstance(_snaps, dict)  # MUTANT\n"
     "                  else [res_raw.get(\"rate_limits\")]):\n",
     "the one turn whose snapshot said 100% is the one that records nothing"),
]

#: mutants that must SURVIVE rather than die (the noop control)
MUST_SURVIVE = {"NOOP CONTROL — one comment word changed"}


def run_suite():
    p = subprocess.run([sys.executable, SUITE], cwd=BACKEND,
                       capture_output=True, text=True, timeout=1800)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    print("baseline (unmutated) …")
    rc, out = run_suite()
    if rc != 0:
        print(out[-4000:])
        print("BASELINE IS RED — fix the suite before mutating anything.")
        return 1
    m = re.search(r"(\d+) checks passed", out)
    print(f"  baseline: {m.group(0) if m else '?'}\n")

    bad = []
    for label, path, find, repl, why in MUTANTS:
        src = open(path, encoding="utf-8").read()
        hits = src.count(find)
        if hits == 0:
            print(f"  x {label}\n      MUTATION DID NOT APPLY — the code it "
                  f"targets has moved. This mutant tested NOTHING.")
            bad.append((label, "did not apply"))
            continue
        if hits > 1:
            # ⚠ A MUTANT THAT AIMS AT TWO PLACES AIMS AT NEITHER, and it fails
            # QUIETLY: `replace(…, 1)` takes whichever comes first in the file,
            # which may be code this suite never runs — so the mutant
            # "survives" and reads as an unchecked behaviour in the code under
            # test. That happened here for real (M11: a twelve-space target was
            # a substring of a twenty-eight-space line in the claude lane 3000
            # lines earlier). Refuse loudly instead; a mis-aimed mutant is a
            # broken instrument, not a finding.
            print(f"  x {label}\n      AMBIGUOUS TARGET — {hits} matches in "
                  f"{os.path.basename(path)}. Widen `find` until it is unique; "
                  f"this mutant tested nothing it claims to.")
            bad.append((label, f"ambiguous target ({hits} matches)"))
            continue
        open(path, "w", encoding="utf-8", newline="").write(
            src.replace(find, repl, 1))
        try:
            rc, out = run_suite()
        finally:
            open(path, "w", encoding="utf-8", newline="").write(src)
        killed = rc != 0
        want_survive = label in MUST_SURVIVE
        ok = (not killed) if want_survive else killed
        mm = re.search(r"(\d+) passed, (\d+) FAILED|(\d+) checks passed", out)
        verdict = ("survived" if not killed else "killed")
        print(f"  {'OK ' if ok else 'BAD'} {label}  [{verdict}]")
        print(f"      ({why}) -> {mm.group(0) if mm else 'no summary'}")
        if not ok:
            bad.append((label, why))

    print()
    if bad:
        print(f"{len(bad)} MUTANT(S) BEHAVED WRONGLY — that behaviour is NOT "
              f"actually checked:")
        for label, why in bad:
            print(f"   - {label}  ({why})")
        return 1
    print(f"all {len(MUTANTS)} mutants behaved as required")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
