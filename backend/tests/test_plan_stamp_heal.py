"""D-219: 'plan' birth-stamps are healed once, not floored forever.

An old 'plan' org default left 'plan' stamped in node scopes (78 of 106
archived nodes, live-measured 2026-09-01); a headless plan-mode agent is
mute, so every bare rehire stalled. The heal is one-shot and marker-keyed: a
'plan' set deliberately AFTER it is preserved. Run with:
    python backend/tests/test_plan_stamp_heal.py
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from typing import Any, Callable

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DATA = tempfile.mkdtemp(prefix="orgtree-plan-heal-")
os.environ["ORGTREE_DATA"] = DATA
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from orgtree import store                                        # noqa: E402
from orgtree.ledger import USER                                  # noqa: E402

atexit.register(lambda: shutil.rmtree(DATA, ignore_errors=True))

PASS = FAIL = 0


def check(label: str, fn: Callable[[], None]) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")
    except Exception as exc:
        FAIL += 1
        print(f"  FAIL    {label}: {exc}")
        import traceback
        traceback.print_exc()


def eq(got: Any, want: Any) -> None:
    assert got == want, f"got {got!r}; want {want!r}"


ORG = store.create_org("zz-plan-heal")
ORG.hire(USER, None, "haiku", 0, "stamped")
ORG.hire(USER, None, "haiku", 0, "clean")
ORG.hire(USER, None, "haiku", 0, "retiree")
ORG.retire(USER, "retiree")

# the damage: an old 'plan' default, stamped into live AND archived scopes
ORG.d["permission_mode"] = "plan"
ORG.node("stamped")["scope"]["permission_mode"] = "plan"
ORG.node("retiree")["scope"]["permission_mode"] = "plan"
ORG.node("clean")["scope"]["permission_mode"] = "acceptEdits"
store.save_org(ORG)


def heal_rewrites_all_stamps_once() -> None:
    org = store.load_org("zz-plan-heal")
    healed = org.heal_plan_stamps()
    assert healed is not None, "first run must actually run"
    eq(sorted(healed), sorted(["<org default>", "stamped", "retiree"]))
    eq(org.d["permission_mode"], "acceptEdits")
    eq(org.node("stamped")["scope"]["permission_mode"], "acceptEdits")
    eq(org.node("retiree")["scope"]["permission_mode"], "acceptEdits")
    eq(org.node("clean")["scope"]["permission_mode"], "acceptEdits")
    marker = org.d["_migrations"]["pm_plan_stamp_heal"]
    eq(sorted(marker["healed"]), sorted(healed))
    assert marker["at"]
    ops = [e.get("op") for e in org.d.get("events") or []]
    assert "heal" in ops, ops
    store.save_org(org)


check("the heal rewrites every stamp — live, archived, org default",
      heal_rewrites_all_stamps_once)


def deliberate_plan_survives_the_marker() -> None:
    org = store.load_org("zz-plan-heal")
    eq(org.heal_plan_stamps(), None)          # marker: already ran
    # a plan set DELIBERATELY after the heal is a choice, not damage
    org.node("stamped")["scope"]["permission_mode"] = "plan"
    store.save_org(org)
    org = store.load_org("zz-plan-heal")
    eq(org.heal_plan_stamps(), None)
    eq(org.node("stamped")["scope"]["permission_mode"], "plan")


check("a post-heal deliberate 'plan' survives every later startup",
      deliberate_plan_survives_the_marker)


def empty_run_still_marks() -> None:
    org2 = store.create_org("zz-plan-clean")
    org2.hire(USER, None, "haiku", 0, "aya")
    healed = org2.heal_plan_stamps()
    eq(healed, [])
    assert "pm_plan_stamp_heal" in org2.d["_migrations"]
    eq(org2.heal_plan_stamps(), None)


check("an org with nothing to heal is marked and never rescanned",
      empty_run_still_marks)

print(f"\nALL {PASS} CHECKS PASS" if not FAIL else
      f"\n{FAIL} FAILED, {PASS} PASSED")
raise SystemExit(1 if FAIL else 0)
