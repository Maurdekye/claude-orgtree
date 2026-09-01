"""№30 names TREES: holding a tree is holding every subtree of it.

The dir-grant clamp used an exact-key lookup, so a parent holding C:\\ could
not grant a folder UNDER it (live-hit 2026-09-01) — narrowing was impossible
and every refusal pushed toward over-granting. The watchdog/send-file gates
shared a second bug in the same family: `root + os.sep` doubles the
separator when the root is a drive root, refusing everything under it.
Run with:
    python backend/tests/test_dir_grant_containment.py
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from typing import Any, Callable

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DATA = tempfile.mkdtemp(prefix="orgtree-dir-contain-")
os.environ["ORGTREE_DATA"] = DATA
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from orgtree import store, supervisor as S                       # noqa: E402
from orgtree.ledger import LedgerError, Org, USER                # noqa: E402

S.chatq_register_org = lambda slug: None
S.chatq_deregister_org = lambda slug: None
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


ROOT = os.path.normpath(DATA)
SUB = os.path.join(ROOT, "deep", "subtree")
DRIVE = "C:\\" if os.name == "nt" else "/"
UNDER_DRIVE = (os.path.join(DRIVE, "Users", "someone", "project")
               if os.name == "nt" else "/srv/someone/project")


def clamp(requested: list[dict[str, str]], held: dict[str, str],
          strict: bool = True) -> tuple[list[dict[str, str]], list[str]]:
    kept, lost = Org._clamp_dirs(
        [{"path": p["path"], "mode": p["mode"]} for p in requested],  # type: ignore[misc]
        held, strict)
    return [dict(d) for d in kept], lost


def subtree_of_a_held_tree_is_grantable() -> None:
    kept, lost = clamp([{"path": SUB, "mode": "rw"}], {ROOT: "rw"})
    eq(kept, [{"path": SUB, "mode": "rw"}])
    eq(lost, [])


check("a subtree of a held rw tree is grantable rw",
      subtree_of_a_held_tree_is_grantable)


def drive_root_holding_covers_everything_under_it() -> None:
    kept, lost = clamp([{"path": UNDER_DRIVE, "mode": "rw"}], {DRIVE: "rw"})
    eq(kept, [{"path": UNDER_DRIVE, "mode": "rw"}])
    eq(lost, [])


check("a drive-root holding covers every folder under it",
      drive_root_holding_covers_everything_under_it)


def ro_holding_clamps_the_subtree() -> None:
    try:
        clamp([{"path": SUB, "mode": "rw"}], {ROOT: "ro"})
    except LedgerError as e:
        assert "read-only" in str(e), e
    else:
        raise AssertionError("rw under an ro holding must refuse at hire")
    kept, lost = clamp([{"path": SUB, "mode": "rw"}], {ROOT: "ro"},
                       strict=False)
    eq(kept, [{"path": SUB, "mode": "ro"}])
    assert lost and "downgraded" in lost[0], lost


check("rw under a read-only holding refuses strictly, downgrades on sweep",
      ro_holding_clamps_the_subtree)


def rw_wins_over_a_covering_ro() -> None:
    kept, _ = clamp([{"path": SUB, "mode": "rw"}],
                    {ROOT: "ro", os.path.join(ROOT, "deep"): "rw"})
    eq(kept, [{"path": SUB, "mode": "rw"}])


check("the most permissive covering tree decides the mode",
      rw_wins_over_a_covering_ro)


def uncovered_paths_still_refuse() -> None:
    outside = (os.path.join(tempfile.gettempdir(), "zz-elsewhere-xyz")
               if os.name == "nt" else "/zz-elsewhere-xyz")
    try:
        clamp([{"path": outside, "mode": "ro"}], {ROOT: "rw"})
    except LedgerError as e:
        assert "№30" in str(e), e
    else:
        raise AssertionError("an uncovered path must still refuse")
    # …and a SIBLING whose name merely extends the held prefix is uncovered
    try:
        clamp([{"path": ROOT + "-evil", "mode": "ro"}], {ROOT: "rw"})
    except LedgerError:
        pass
    else:
        raise AssertionError("prefix-sibling escape: <root>-evil was granted")


check("paths outside every held tree still refuse, including <root>-evil",
      uncovered_paths_still_refuse)


ORG = store.create_org("zz-dir-contain")
ORG.hire(USER, None, "haiku", 0, "aya")
SCRATCH = os.path.normpath(S.scratch_dir("zz-dir-contain", "aya"))
os.makedirs(SCRATCH, exist_ok=True)
TARGET = os.path.join(ROOT, "logs", "some.log")
os.makedirs(os.path.dirname(TARGET), exist_ok=True)
with open(TARGET, "w", encoding="utf-8") as f:
    f.write("x")


def watchdog_containment_honors_held_trees() -> None:
    ORG.node("aya")["scope"]["add_dirs"] = [{"path": ROOT, "mode": "ro"}]
    assert S.wd_file_contained(ORG, "aya", TARGET)
    assert S.wd_file_contained(ORG, "aya",
                               os.path.join(SCRATCH, "notes.md"))
    outside = os.path.join(tempfile.gettempdir(), "zz-out.log")
    assert not S.wd_file_contained(ORG, "aya", outside), \
        "a file outside every held tree must not be watchable"


check("a file dog may watch under a held tree and its own scratch",
      watchdog_containment_honors_held_trees)


def watchdog_containment_survives_a_drive_root() -> None:
    ORG.node("aya")["scope"]["add_dirs"] = [{"path": DRIVE, "mode": "ro"}]
    assert S.wd_file_contained(ORG, "aya", TARGET), \
        "a drive-root grant must cover files under it"


check("a drive-root grant makes files under it watchable",
      watchdog_containment_survives_a_drive_root)

print(f"\nALL {PASS} CHECKS PASS" if not FAIL else
      f"\n{FAIL} FAILED, {PASS} PASSED")
raise SystemExit(1 if FAIL else 0)
