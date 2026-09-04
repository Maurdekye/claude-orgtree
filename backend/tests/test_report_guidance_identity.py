"""Report-management guidance is stable across the empty-team boundary.

The D-181 prompt split moved live roster state out of ``identity_prompt``, but
five child-count branches survived: a manager's own team charter; inspect,
retire, and compact guidance; and archived-agent guidance.  A first hire thus
killed the parent's warm process even after D-201 made its argv stable.

This test uses real ledger hire/retire operations and the real warm identity:

* 0 -> 1 -> 2 -> 1 -> 0 live reports keeps prompt, normalized argv, component
  digests, and combined identity byte-identical;
* all five formerly conditional facts are present before the first hire;
* actual role-charter and team-charter content changes still move only the
  prompt component and restore exactly when their values are restored;
* planted child-count/value-replacement mutants are detected, so the stable
  cycle cannot pass because the compared input happened to be inert.

Hermetic: throwaway data/home, no CLI, listener, network, or production journal.

    python backend/tests/test_report_guidance_identity.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

RIG = tempfile.mkdtemp(prefix="orgtree-report-identity-")
TEST_HOME = os.path.join(RIG, "home")
DATA = os.path.join(RIG, "data")
os.makedirs(TEST_HOME, exist_ok=True)
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["ORGTREE_DATA"] = DATA
os.environ["USERPROFILE"] = TEST_HOME
os.environ["HOME"] = TEST_HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"
os.environ["ORGTREE_PORT"] = "7416"             # never bound
os.environ["ORGTREE_WARM"] = "1"

from orgtree import store, supervisor as S, warmpool as W  # noqa: E402
from orgtree.ledger import USER                              # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
ROLE_A = "ROLE-CONTENT-A"
ROLE_B = "ROLE-CONTENT-B"
TEAM_A = "TEAM-CONTENT-A"
TEAM_B = "TEAM-CONTENT-B"


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


def fixture(label: str):
    org = store.create_org(label)
    boss = org.hire(USER, None, "haiku", 8, "boss",
                    charter=ROLE_A)["node"]
    org.set_scope(USER, boss, team_charter=TEAM_A)
    store.save_org(org)
    # Establish the fixed scratch root before the baseline. This makes the
    # check about ledger mutations, not first-use directory creation.
    S.scratch_dir(org.d["slug"], boss)
    return org, boss


def capture(org, nid: str) -> dict:
    cmd = S._build_cmd(org, nid, write_ident=False)
    snap = W.identity_snapshot(org, nid, cmd=cmd)
    return {
        "prompt": S.identity_prompt(org, nid),
        "argv": W._argv_normalized(cmd),
        "snapshot": snap,
    }


def assert_same(label: str, baseline: dict, current: dict) -> None:
    assert current["prompt"] == baseline["prompt"], (
        f"{label}: system prompt moved")
    assert current["argv"] == baseline["argv"], (
        f"{label}: normalized spawn argv moved")
    assert current["snapshot"] == baseline["snapshot"], (
        f"{label}: warm identity/components moved: "
        f"{W.identity_change_fields(baseline['snapshot'][0], baseline['snapshot'][1], current['snapshot'][0], current['snapshot'][1])}")


def assert_prompt_change(before: dict, after: dict) -> None:
    fields = W.identity_change_fields(
        before["snapshot"][0], before["snapshot"][1],
        after["snapshot"][0], after["snapshot"][1])
    assert fields["changed_inputs"] == ["prompt"], fields
    assert before["argv"] == after["argv"], "content change moved argv"


def t_real_ledger_cycle_is_stable() -> None:
    org, boss = fixture("report identity cycle")
    baseline = capture(org, boss)
    # ⚠ WHAT THESE PINS DO AND DO NOT COVER. They assert that each imperative
    # is PRESENT in an agent's identity prompt before it has hired anyone —
    # that is all. Nothing here checks a single word of the reasoning around
    # them, and the rest of this suite compares the prompt TO ITSELF across a
    # hire/retire cycle. You can rewrite every justification in that block and
    # this suite stays green. Do not read a passing run as "the guidance text
    # is tested"; it is not, and saying so has been the standing caveat on
    # every change to it.
    # Two markers changed 2026-09-04 with the guidance rewrite:
    #   "WHEN A REPORT'S ANSWER DOES NOT ADD UP" -> "LOOK AT YOUR REPORTS, …"
    #   "WHEN A REPORT IS FINISHED, RETIRE IT"   -> "RETIRE A FINISHED REPORT …"
    # and two are new (the rehire trade-off, and background work at turn end).
    for marker in (
        TEAM_A,
        "LOOK AT YOUR REPORTS, DO NOT INTERROGATE THEM",
        "RETIRE A FINISHED REPORT TO FREE IT",
        "WHEN A LONG-CONTEXT REPORT HAS SAT IDLE FOR HOURS",
        "RETIRED AGENTS ARE NOT GONE",
        "A rehire RESUMES A FULL TRANSCRIPT",
        "NEVER END A TURN WITH BACKGROUND WORK STILL RUNNING",
    ):
        assert marker in baseline["prompt"], (
            f"guidance {marker!r} is absent before the first hire")

    one = org.hire(USER, boss, "haiku", 0, "one")["node"]
    store.save_org(org)
    assert_same("0->1", baseline, capture(org, boss))

    two = org.hire(USER, boss, "haiku", 0, "two")["node"]
    store.save_org(org)
    assert_same("1->2", baseline, capture(org, boss))

    org.retire(USER, two)
    store.save_org(org)
    assert_same("2->1", baseline, capture(org, boss))

    org.retire(USER, one)
    store.save_org(org)
    assert_same("1->0", baseline, capture(org, boss))


def t_real_content_changes_move_and_restore() -> None:
    org, boss = fixture("report identity controls")
    baseline = capture(org, boss)

    org.set_scope(USER, boss, charter=ROLE_B)
    role_b = capture(org, boss)
    assert ROLE_B in role_b["prompt"]
    assert_prompt_change(baseline, role_b)
    org.set_scope(USER, boss, charter=ROLE_A)
    assert_same("role charter restore", baseline, capture(org, boss))

    org.set_scope(USER, boss, team_charter=TEAM_B)
    team_b = capture(org, boss)
    assert TEAM_B in team_b["prompt"]
    assert_prompt_change(baseline, team_b)
    org.set_scope(USER, boss, team_charter=TEAM_A)
    assert_same("team charter restore", baseline, capture(org, boss))


def t_value_replacement_mutants_are_detected() -> None:
    org, boss = fixture("report identity mutants")
    real = S.identity_prompt
    base_real = real(org, boss)
    one = org.hire(USER, boss, "haiku", 0, "one")["node"]
    one_real = real(org, boss)
    assert base_real == one_real, "real prompt is not stable before mutation"

    detections: list[tuple[str, bool]] = []

    # M1: restore the old live-child gate for report-management guidance.
    report_text = "LOOK AT YOUR REPORTS, DO NOT INTERROGATE THEM"
    base_m1 = base_real.replace(report_text, "")
    one_m1 = one_real
    detections.append(("live-report conditional", base_m1 != one_m1))

    # M2: restore the old any-child gate for archived-agent guidance.
    archived_text = "RETIRED AGENTS ARE NOT GONE"
    base_m2 = base_real.replace(archived_text, "")
    one_m2 = one_real
    detections.append(("first-ever-child conditional", base_m2 != one_m2))

    # M3: restore the old team-charter/live-child gate.
    base_m3 = base_real.replace(TEAM_A, "")
    one_m3 = one_real
    detections.append(("team-charter conditional", base_m3 != one_m3))

    # M4: suppress a real content change. The positive control must notice.
    org.set_scope(USER, boss, charter=ROLE_B)
    changed = real(org, boss)
    detections.append(("constant role charter", changed != one_real))
    org.set_scope(USER, boss, charter=ROLE_A)
    org.retire(USER, one)

    blind = [name for name, detected in detections if not detected]
    assert not blind, f"mutants escaped detection: {blind}"


def main() -> int:
    print("report-guidance warm identity")
    check("real ledger 0->1->2->1->0 keeps prompt, argv and hash stable",
          t_real_ledger_cycle_is_stable)
    check("real role/team-charter changes move prompt only and restore",
          t_real_content_changes_move_and_restore)
    check("all four planted child/content mutants are detected",
          t_value_replacement_mutants_are_detected)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\nFAIL: {label}\n{tb}")
        print(f"report-guidance-identity: {PASS} passed - {len(FAIL)} FAILED")
        return 1
    print(f"report-guidance-identity: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(RIG, ignore_errors=True)
    sys.exit(rc)
