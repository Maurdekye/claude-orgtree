"""Mutation harness for test_harvest.py.

Not a test — a check on the checks. test_harvest.py went green the first time
it ran, and green proves nothing until each guard has been watched FAILING for
its own named reason. (It also shipped one check that compared a value to
ITSELF and could never fail; that is what this harness exists to catch.)

The CONTROL PAIR is the point:
  * NO-OP  — a real edit to the same lines that changes no behaviour. It must
             SURVIVE. If it "dies", the suite is keying on text, not conduct.
  * SANITY — an obviously broken mutant that must DIE. If it survives, the
             harness is not running the code it thinks it is.

⚠ Several mutants below are DELIBERATE VIOLATIONS OF THE SEPARATION — they
wire the widened record into `err_blob`, into `_turn_abandoned`'s mail, or
into a predicate directly. Each must be killed by the §2 rule that names that
exact hazard. A separation whose violation kills nothing is a comment, not a
property.

    python backend/tests/_mutate_harvest.py

Restores supervisor.py from git after each run (the worktree must be clean).
"""

import os as _os
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
SUP = ROOT / "backend" / "orgtree" / "supervisor.py"
SUITE = ROOT / "backend" / "tests" / "test_harvest.py"

_RAISE = ('                raise RuntimeError(\n'
          '                    f"turn failed: "\n'
          '                    f"{_for_the_record(err_blob, res)[:400] '
          'or \'no output\'}")')

# (name, file, find, replace, must-kill-this-check-or-None-for-survive)
MUTANTS = [
    ("NO-OP CONTROL: reword a comment beside the harvest helper",
     SUP,
     "    txt = str(res.get(\"result\") or \"\").strip()",
     "    txt = str(res.get(\"result\") or \"\").strip()  # noqa",
     None),

    ("SANITY CONTROL: _for_the_record returns the blob untouched",
     SUP,
     "    if not err_blob:\n        return err_blob\n    detail = _result_detail(res)",
     "    if not err_blob:\n        return err_blob\n    return err_blob\n    detail = _result_detail(res)",
     "the recorded error carries the reason, NOT just the placeholder"),

    ("the harvest stops reading the CLI's result text",
     SUP,
     "    txt = str(res.get(\"result\") or \"\").strip()",
     "    txt = \"\"",
     "the measured 401 payload yields its real reason"),

    # ⚠ anchored with the following line: `status = res.get(...)` also appears
    # in `_looks_like_auth_failure`, which reads the same field for a
    # different purpose. The bare line became ambiguous the moment step 2
    # landed.
    ("the harvest stops reading api_error_status",
     SUP,
     "    status = res.get(\"api_error_status\")\n    bits = []",
     "    status = None\n    bits = []",
     "the measured 401 payload yields its real reason"),

    ("the record REPLACES the original blob instead of appending",
     SUP,
     "    return f\"{err_blob}  ⟵ the CLI's own reason: {detail}\"",
     "    return f\"the CLI's own reason: {detail}\"",
     "the ORIGINAL blob survives verbatim (append, never replace)"),

    ("an empty blob (a manual ⏸ pause) is given a record anyway",
     SUP,
     "    if not err_blob:\n        return err_blob\n    detail = _result_detail(res)",
     "    detail = _result_detail(res)",
     "an EMPTY blob is never given a record (a ⏸ pause is not a failure)"),

    ("the duplicate-reason guard is dropped",
     SUP,
     "    if detail and detail in err_blob:\n        return err_blob",
     "    if False:\n        return err_blob",
     "a reason already present is not duplicated"),

    # ── separation violations ──────────────────────────────────────────────
    ("LEAK: the widened record is assigned back onto err_blob",
     SUP,
     _RAISE,
     '                err_blob = _for_the_record(err_blob, res)\n'
     '                raise RuntimeError(\n'
     '                    f"turn failed: {err_blob[:400] or \'no output\'}")',
     "RULE 1 · the widened text is never assigned onto err_blob"),

    ("LEAK: the widened record is handed to _turn_abandoned (it MAILS it)",
     SUP,
     "                    _turn_abandoned(slug, nid, _door, err_blob)",
     "                    _turn_abandoned(slug, nid, _door,\n"
     "                                    _for_the_record(err_blob, res))",
     "RULE 2 · the widened text is never passed to _turn_abandoned"),

    ("LEAK: a predicate is called directly on the widened record",
     SUP,
     _RAISE,
     '                if _looks_like_usage_limit(_for_the_record(err_blob,\n'
     '                                                          res)):\n'
     '                    pass\n' + _RAISE,
     "RULE 3 · no _looks_like_* predicate is called on the widened text"),

    ("LEAK: the widened record goes to _retry_exhausted (it MAILS AND WAKES)",
     SUP,
     "                            _retry_exhausted(slug, nid, run, err_blob, kind_txt)",
     "                            _retry_exhausted(slug, nid, run,\n"
     "                                             _for_the_record(err_blob, res),\n"
     "                                             kind_txt)",
     "RULE 2b · the widened text is never passed to _retry_exhausted"),

    ("the RETRY door quietly reverts to the placeholder (the other door)",
     SUP,
     "                            f\"{_for_the_record(err_blob, res)[:300]}\")",
     "                            f\"{err_blob[:300]}\")",
     "POSITIVE CONTROL: both operator-facing doors are wired"),

    ("LEAK: the record is assembled EARLY, before the classifiers run",
     SUP,
     "            err_blob = _name_the_cause(err_blob)",
     "            err_blob = _name_the_cause(err_blob)\n"
     "            _for_the_record(err_blob, res)",
     "ORDER · the widened text is assembled after the last predicate"),

    ("the TERMINAL door reverts to the placeholder",
     SUP,
     _RAISE,
     '                raise RuntimeError(\n'
     '                    f"turn failed: {err_blob[:400] or \'no output\'}")',
     "POSITIVE CONTROL: both operator-facing doors are wired"),

    # ── step 2: the narrow positive auth predicate ─────────────────────────
    # ⚠ BOTH auth mutants below must anchor on the COMMENT line as well as the
    # assignment. `status = res.get("api_error_status")` appears in
    # `_result_detail` TOO, and it appears there FIRST — so a bare anchor plus
    # `replace(..., 1)` mutated the wrong function entirely and killed a
    # scatter of harvest checks. The harness reporting "killed the WRONG
    # check" is the only reason that was caught rather than believed.
    ("WIDENING: the auth predicate starts substring-searching TEXT",
     SUP,
     "    status = res.get(\"api_error_status\")\n"
     "    # ⚠ no `isinstance(status, bool)` guard here, deliberately.",
     "    if \"invalid api key\" in str(res.get(\"result\") or \"\").lower():\n"
     "        return True\n"
     "    status = res.get(\"api_error_status\")\n"
     "    # ⚠ no `isinstance(status, bool)` guard here, deliberately.",
     "ANTI-WIDENING · auth-sounding TEXT alone never classifies"),

    ("the auth predicate widens to 403 (an org policy answer)",
     SUP,
     "        return status == 401",
     "        return status in (401, 403)",
     "403 is deliberately NOT an auth failure"),

    ("the auth predicate stops firing at all",
     SUP,
     "    status = res.get(\"api_error_status\")\n"
     "    # ⚠ no `isinstance(status, bool)` guard here, deliberately.",
     "    return False\n"
     "    status = res.get(\"api_error_status\")\n"
     "    # ⚠ no `isinstance(status, bool)` guard here, deliberately.",
     "the measured 401 shape classifies as an auth failure"),

    # ⚠ retargeted. This used to mutate an `isinstance(status, bool)` guard —
    # but that guard could never change an answer (`True == 401` is already
    # False), so the mutant was a no-op and its check was UNKILLABLE. The
    # guard has been deleted from the code; truthiness is the mutation that
    # actually makes a bool classify.
    ("the status is read as truthiness rather than compared to 401",
     SUP,
     "    if isinstance(status, int):\n        return status == 401",
     "    if isinstance(status, int):\n        return bool(status)",
     "True is not a status (bools are ints in Python)"),

    ("the operator's record stops NAMING the auth failure",
     SUP,
     "    # acting on it is step 3.\n    if _looks_like_auth_failure(res):",
     "    if False and _looks_like_auth_failure(res):",
     "an auth failure is NAMED on the operator's record"),

    ("every failure is mislabelled as an auth failure",
     SUP,
     "    # acting on it is step 3.\n    if _looks_like_auth_failure(res):",
     "    if True or _looks_like_auth_failure(res):",
     "a NON-auth failure is not mislabelled"),

    ("the duplicate guard moves BACK below the label (double-records)",
     SUP,
     "    if detail and detail in err_blob:\n        return err_blob\n"
     "    # step 2, CLASSIFICATION ONLY",
     "    # step 2, CLASSIFICATION ONLY",
     "a reason already present is not duplicated"),

    ("WIDENING: the auth predicate is called on the harvested TEXT",
     SUP,
     "    # acting on it is step 3.\n    if _looks_like_auth_failure(res):",
     "    if _looks_like_auth_failure(res) or _looks_like_auth_failure(\n"
     "            {\"api_error_status\": detail}):",
     "the auth predicate is never called on harvested text"),

    ("BEHAVIOUR: the auth predicate is wired to drive _turn_abandoned",
     SUP,
     "                    _turn_abandoned(slug, nid, _door, err_blob)",
     "                    _turn_abandoned(slug, nid, _door, err_blob) \\\n"
     "                        if not _looks_like_auth_failure(res) else None",
     "no freeze/retry/mail path is wired to the auth predicate yet"),

    # ⚠ this mutant SURVIVED the first round, and both halves were wrong: the
    # mutant used a bare attribute REFERENCE (`store.save_org`), which is not
    # a side effect at all, and the check it targeted could only see bare-name
    # calls, so it would have missed the real `store.save_org(...)` shape too.
    # Now a genuine call, against a check that sees attribute calls.
    ("_for_the_record grows a side effect (writes the org doc)",
     SUP,
     # ⚠ anchored on the ASSIGNMENT alone, not on the line that follows it.
     # This mutant went VACUOUS ("pattern not found") the moment step 2
     # restructured the guard beneath it — the exact failure the accounts
     # handover warned about, where eight mutants silently stopped applying
     # after the code under test was rewritten.
     "    detail = _result_detail(res)\n",
     "    detail = _result_detail(res)\n    store.save_org(res)\n",
     "_for_the_record is pure — no doc writes, no mail, no notify"),
]


def restore():
    subprocess.run(["git", "-C", str(ROOT), "checkout", "--",
                    "backend/orgtree", "backend/tests"], check=False)


def run_suite():
    env = {**_os.environ, "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run([sys.executable, str(SUITE)], env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=str(ROOT / "backend"))
    passed = set(re.findall(r"ok\s+\d+\s+(.*)", p.stdout))
    return p.returncode == 0, {s.strip() for s in passed}


def main():
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain",
                            "backend/orgtree", "backend/tests"],
                           capture_output=True, text=True).stdout
    if dirty.strip():
        sys.exit("refusing to run: backend/orgtree or backend/tests is dirty "
                 "— commit first (a mutation round would revert it)\n" + dirty)

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
        # ⚠ THIS FILE IS WHERE THE PROBLEM WAS FIRST SEEN: two auth mutants
        # anchored on a line that also appears in `_result_detail`, and
        # appears there FIRST, so they mutated the harvest helper and reported
        # kills of unrelated checks the whole time. An anchor matching more
        # than once is a coin flip, not a mutant.
        if src.count(find) > 1:
            misses.append(f"{name}: AMBIGUOUS PATTERN — matches "
                          f"{src.count(find)} places; would mutate the FIRST")
            print(f"  ?? {name}\n     ambiguous anchor ({src.count(find)} "
                  f"matches) — refusing to guess")
            continue
        path.write_text(src.replace(find, repl, 1), encoding="utf-8")
        try:
            # ⚠ A MUTANT THAT NEVER RAN IS NOT A KILL (ps-guards audit
            # 2026-08-27; the identical hole was measured in
            # _mutate_handles.py and the full account is in that file).
            # Short form: `killed = baseline - passed`, and a suite that never
            # STARTED prints no `ok N` lines, so `passed` is empty, `killed` is
            # the whole baseline, and `must_kill in killed` is trivially true —
            # "✓ KILLED", exit 0, nothing executed. A mutant is SUPPOSED to
            # turn the suite red, so redness carries no information here;
            # positive evidence that the suite RAN does.
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except SyntaxError as e:
                misses.append(f"{name}: MUTANT DOES NOT PARSE ({e}) — a file "
                              f"that cannot be imported is not a killed check")
                print(f"  ?? {name}\n     mutant does not parse — no verdict "
                      f"is available: {e}")
                continue
            ok, passed = run_suite()
            if must_kill is not None and not passed:
                misses.append(f"{name}: SUITE PRODUCED NO CHECK OUTPUT — it "
                              f"did not run, so nothing can be attributed to "
                              f"“{must_kill}”")
                print(f"  ?? {name}\n     suite emitted no `ok` lines at all "
                      f"— it did not run; NOT counted as a kill")
                continue
            if must_kill is None and len(passed) != len(baseline):
                misses.append(f"{name}: no-op control changed the pass COUNT "
                              f"({len(baseline)} -> {len(passed)})")
            killed = baseline - passed
            if must_kill is None:
                if ok:
                    print(f"  ✓ SURVIVED  {name}")
                else:
                    misses.append(f"{name}: no-op control DIED "
                                  f"(killed {sorted(killed)})")
                    print(f"  ✗ {name}\n     no-op control died — suite keys "
                          f"on text, not conduct: {sorted(killed)}")
            else:
                if must_kill in killed:
                    extra = killed - {must_kill}
                    note = f" (+{len(extra)} more)" if extra else ""
                    print(f"  ✓ KILLED by “{must_kill}”{note}\n     {name}")
                elif not killed:
                    misses.append(f"{name}: SURVIVED — nothing guards it")
                    print(f"  ✗ {name}\n     SURVIVED — no check covers this")
                else:
                    misses.append(f"{name}: killed the WRONG check "
                                  f"{sorted(killed)}")
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
