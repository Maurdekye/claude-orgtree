"""Set-like grants have canonical warm identity independent of input order.

    python backend/tests/test_identity_set_order.py

Hermetic real-ledger controls: reverse identical directory grants and external
response handles, then change one directory mode. The first two must preserve
prompt/argv/hash exactly; the permission change must move prompt+argv and
restore exactly. Duplicate entries also retain the ledger's first-wins rule.
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
RIG = tempfile.mkdtemp(prefix="orgtree-identity-order-")
HOME = os.path.join(RIG, "home")
DATA = os.path.join(RIG, "data")
DIR_A = os.path.join(RIG, "grant-a")
DIR_B = os.path.join(RIG, "grant-b")
for path in (HOME, DATA, DIR_A, DIR_B):
    os.makedirs(path, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ.update({
    "ORGTREE_DATA": DATA,
    "HOME": HOME,
    "USERPROFILE": HOME,
    "ORGTREE_WARM": "0",
    "ORGTREE_STEER_HOOK": "0",
    "ORGTREE_PORT": "7417",                         # never bound
})

from orgtree import store, supervisor as S, warmpool as W  # noqa: E402
from orgtree.ledger import USER                              # noqa: E402

DIRS = [{"path": DIR_A, "mode": "rw"},
        {"path": DIR_B, "mode": "ro"}]
HANDLES = ["@mcp:alpha", "@mcp:beta"]
PASS = 0
FAIL: list[tuple[str, str]] = []


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                      # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


org = store.create_org("identity set order")
org.hire(USER, None, "haiku", 5, "boss", add_dirs=DIRS,
         tools={"mcp": []}, org_visibility="full", charter="boss",
         external_handles=HANDLES)
store.save_org(org)
S.scratch_dir(org.d["slug"], "boss")


def capture() -> dict:
    cmd = S._build_cmd(org, "boss", write_ident=False)
    return {
        "prompt": S.identity_prompt(org, "boss"),
        "argv": W._argv_normalized(cmd),
        "snapshot": W.identity_snapshot(org, "boss", cmd=cmd),
    }


def same(before: dict, after: dict, label: str) -> None:
    assert before == after, f"{label} changed identity: {before} != {after}"


def t_directory_order_is_inert() -> None:
    base = capture()
    org.set_scope(USER, "boss", add_dirs=list(reversed(DIRS)))
    same(base, capture(), "reversed directory grants")
    assert org.node("boss")["scope"]["add_dirs"] == DIRS, (
        "stored grants are not canonical")
    # Exact duplicates retain first-wins dedupe and do not perturb the set.
    org.set_scope(USER, "boss", add_dirs=[DIRS[1], DIRS[0], DIRS[1]])
    same(base, capture(), "duplicate directory grant")


def t_handle_order_is_inert() -> None:
    base = capture()
    org.set_scope(USER, "boss", external_handles=list(reversed(HANDLES)))
    same(base, capture(), "reversed external handles")
    assert org.node("boss")["external_handles"] == HANDLES, (
        "stored handles are not canonical")
    org.set_scope(USER, "boss", external_handles=[HANDLES[1], HANDLES[0],
                                                   HANDLES[1]])
    same(base, capture(), "duplicate external handle")


def t_real_mode_change_moves_and_restores() -> None:
    base = capture()
    changed = [{"path": DIR_A, "mode": "rw"},
               {"path": DIR_B, "mode": "rw"}]
    org.set_scope(USER, "boss", add_dirs=changed)
    moved = capture()
    fields = W.identity_change_fields(
        base["snapshot"][0], base["snapshot"][1],
        moved["snapshot"][0], moved["snapshot"][1])
    assert fields["changed_inputs"] == ["prompt", "argv"], fields
    org.set_scope(USER, "boss", add_dirs=DIRS)
    same(base, capture(), "restored directory mode")


def main() -> int:
    print("set-like identity order")
    check("reversing/duplicating directory grants is byte-identical",
          t_directory_order_is_inert)
    check("reversing/duplicating external handles is byte-identical",
          t_handle_order_is_inert)
    check("ro->rw moves prompt+argv and restoration is exact",
          t_real_mode_change_moves_and_restores)
    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\nFAIL: {label}\n{tb}")
        print(f"identity-set-order: {PASS} passed - {len(FAIL)} FAILED")
        return 1
    print(f"identity-set-order: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(RIG, ignore_errors=True)
    sys.exit(rc)
