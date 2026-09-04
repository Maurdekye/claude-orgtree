"""org.md (the workspace CLAUDE.md) participates in the warm identity.

The org-charter editor writes `<workspace>/CLAUDE.md` and the API docstring
promises it is "injected into every node that holds the workspace". It was
not reaching the identity hash at all:

* `native_startup_context_digest` walked the cwd PARENT CHAIN, home and
  managed policy. The workspace is a SIBLING of scratch, never an ancestor,
  so the walk could not reach it.
* The CLI nevertheless loads it - instruction files come from every
  `--add-dir` working directory, not just the cwd chain. Measured on the live
  fleet: an agent with no CLAUDE.md anywhere in its cwd chain and none at
  ~/.claude/CLAUDE.md still had the granted workspace file's text in context.

Net effect before the fix: editing org.md moved no hash, killed no process,
and silently did not apply to any parked agent until an unrelated respawn -
the same defect D-206 closed for the scratch CLAUDE.md, one grant surface
over, and indistinguishable from "the setting does nothing".

Falsifiers supplied here against the real implementation:

F1 drop granted dirs from the digest (pre-fix behaviour) -> group 2 FAILS
F2 hash the whole granted directory, not its instruction
   files                                                 -> group 4 FAILS
F3 hash granted dirs for every node regardless of grant  -> group 3 FAILS

Hermetic: throwaway data/home/workspace, no CLI, listener, network or
production journal.

    python backend/tests/test_orgmd_identity.py
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

RIG = tempfile.mkdtemp(prefix="orgtree-orgmd-identity-")
TEST_HOME = os.path.join(RIG, "home")
DATA = os.path.join(RIG, "data")
WS = os.path.join(RIG, "workspace")          # deliberately NOT under scratch
for _d in (TEST_HOME, DATA, WS):
    os.makedirs(_d, exist_ok=True)
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


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


org = store.create_org("orgmd identity rig")
SLUG = org.d["slug"]
org.d["workspace"] = WS
# HOLDER holds the workspace grant; OUTSIDER deliberately does not.
org.hire(USER, None, "haiku", 5, "holder",
         add_dirs=[{"path": WS, "mode": "rw"}],
         tools={"mcp": []}, org_visibility="full", charter="c")
org.hire(USER, None, "haiku", 5, "outsider", add_dirs=[],
         tools={"mcp": []}, org_visibility="full", charter="c")
store.save_org(org)

ORGMD = os.path.join(WS, "CLAUDE.md")


def digest(nid: str) -> str:
    return W.native_startup_context_digest(store.load_org(SLUG), nid)


def snapshot(nid: str):
    return W.identity_snapshot(store.load_org(SLUG), nid)


def t_scratch_claudemd_moves_the_digest() -> None:
    """The CONTRAST half, and a real user's real memory.

    An agent's own scratch CLAUDE.md is IN the cwd parent chain, so it has
    always been fingerprinted (D-206). Editing it flips the cache flag, which
    is exactly what an operator remembers seeing. Pinned here because the
    org.md bug is only intelligible next to it: same filename, same loader,
    different directory, opposite behaviour before the fix."""
    cwd = S.scratch_dir(SLUG, "holder")
    path = os.path.join(cwd, "CLAUDE.md")
    remove(path)
    base = digest("holder")
    write(path, "my own working notes\n")
    try:
        assert digest("holder") != base, (
            "a scratch CLAUDE.md edit did not move the digest - the D-206 "
            "guarantee has regressed")
    finally:
        remove(path)
    assert digest("holder") == base, "removal did not restore the digest"


def t_workspace_is_not_an_ancestor_of_scratch() -> None:
    """The premise the whole defect rests on. If someone later moves scratch
    under the workspace, the cwd walk would cover org.md by accident and the
    rest of this file would be testing nothing - so pin the geometry."""
    cwd = os.path.abspath(S.scratch_dir(SLUG, "holder"))
    chain = []
    at = cwd
    while True:
        chain.append(os.path.normcase(at))
        parent = os.path.dirname(at)
        if parent == at:
            break
        at = parent
    assert os.path.normcase(os.path.abspath(WS)) not in chain, (
        "workspace IS an ancestor of scratch - the cwd walk would already "
        "cover org.md and this suite no longer proves anything")


def t_orgmd_edit_moves_and_restores_identity() -> None:
    """The headline: an org.md edit must reach the holder's identity."""
    remove(ORGMD)
    empty = snapshot("holder")
    write(ORGMD, "org charter alpha\n")
    alpha = snapshot("holder")
    assert empty[0] != alpha[0], (
        "creating org.md did not move the holder's identity hash - a parked "
        "agent would keep serving without the org charter")
    fields = W.identity_change_fields(empty[0], empty[1], alpha[0], alpha[1])
    assert fields["changed_inputs"] == ["prompt"], fields

    write(ORGMD, "org charter beta\n")
    beta = snapshot("holder")
    assert alpha[0] != beta[0], "editing org.md did not move the hash"

    write(ORGMD, "org charter alpha\n")
    assert snapshot("holder") == alpha, (
        "restoring org.md content did not restore identity - the digest is "
        "reading something other than the bytes")
    remove(ORGMD)
    assert snapshot("holder") == empty, (
        "deleting org.md did not restore the absent-file identity")


def t_orgmd_reaches_only_nodes_that_hold_the_workspace() -> None:
    """F3: the grant is what carries it. A node without the workspace must
    not be dirtied by an org.md edit, or one org-charter edit would respawn
    agents that never see the file."""
    remove(ORGMD)
    before = digest("outsider")
    holder_before = digest("holder")
    write(ORGMD, "org charter for holders only\n")
    try:
        assert digest("outsider") == before, (
            "org.md moved the identity of a node that does NOT hold the "
            "workspace - the digest is hashing beyond the grant")
        assert digest("holder") != holder_before, (
            "control failed: org.md did not move the HOLDER either, so the "
            "outsider result above proves nothing")
    finally:
        remove(ORGMD)


def t_only_instruction_files_in_a_granted_dir_count() -> None:
    """F2: we hash a granted dir's INSTRUCTION FILES, not its contents. A
    workspace is a working directory full of churning source; hashing it
    wholesale would respawn the fleet on every unrelated file write."""
    remove(ORGMD)
    base = digest("holder")
    noise = os.path.join(WS, "README.md")
    src = os.path.join(WS, "sub", "module.py")
    for path in (noise, src):
        write(path, "content that is not a startup instruction\n")
        assert digest("holder") == base, (
            f"{os.path.basename(path)} in a granted dir moved the digest - "
            f"the whole directory is being hashed")
        remove(path)

    local = os.path.join(WS, "CLAUDE.local.md")
    write(local, "local override\n")
    assert digest("holder") != base, (
        "CLAUDE.local.md in a granted dir did not move the digest")
    remove(local)
    assert digest("holder") == base, "removal did not restore the digest"


def t_falsifier_pre_fix_behaviour_reproduces_the_fault() -> None:
    """F1 - the instrument must prove it can see the fault it exists for.

    Recompute with the holder's grants stripped, which is what the pre-fix
    code effectively saw: grants contributed nothing. An org.md edit must
    then be INVISIBLE. If this fails, the groups above are passing for some
    reason other than the fix and cannot be trusted."""
    remove(ORGMD)
    blind = store.load_org(SLUG)
    blind.nodes["holder"]["scope"]["add_dirs"] = []
    blind_before = W.native_startup_context_digest(blind, "holder")
    write(ORGMD, "an edit the pre-fix digest cannot see\n")
    try:
        blind2 = store.load_org(SLUG)
        blind2.nodes["holder"]["scope"]["add_dirs"] = []
        assert W.native_startup_context_digest(
            blind2, "holder") == blind_before, (
            "the planted fault did not reproduce: stripping the grant still "
            "showed the org.md edit, so this suite is not measuring the fix")
        assert digest("holder") != blind_before, (
            "with the grant present the same edit was still invisible")
    finally:
        remove(ORGMD)


def main() -> int:
    print("contrast: which CLAUDE.md was already covered")
    check("a scratch CLAUDE.md edit moves the digest (D-206, unchanged)",
          t_scratch_claudemd_moves_the_digest)
    check("workspace is not an ancestor of scratch (the defect's premise)",
          t_workspace_is_not_an_ancestor_of_scratch)
    print("org.md participates in warm identity")
    check("an org.md edit moves and restores the holder's identity",
          t_orgmd_edit_moves_and_restores_identity)
    check("org.md reaches only nodes that hold the workspace",
          t_orgmd_reaches_only_nodes_that_hold_the_workspace)
    check("a granted dir contributes instruction files, not its contents",
          t_only_instruction_files_in_a_granted_dir_count)
    print("falsifier")
    check("pre-fix behaviour reproduces the fault this suite exists to catch",
          t_falsifier_pre_fix_behaviour_reproduces_the_fault)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n[X] {label}\n{tb}")
        print(f"orgmd-identity: {PASS} passed - {len(FAIL)} FAILED")
        return 1
    print(f"orgmd-identity: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(RIG, ignore_errors=True)
    sys.exit(rc)
