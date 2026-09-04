"""Codex startup instruction files participate in the Codex warm identity.

The Claude lane hashes its session-start instruction files; the Codex lane
hashed NONE. A Codex agent could therefore serve from a parked app-server
whose instruction files had changed underneath it - the same defect D-206
closed for Claude, one provider over.

The file set is NOT Claude's, and copying that list across would have been
worse than the gap: hashing files codex ignores restarts agents for edits
that change nothing. So the set below was MEASURED against the pinned codex
0.150.1 using `codex debug prompt-input` (renders the model-visible prompt
with no API call), planting distinctive markers and reading back which ones
actually reached the prompt:

    <cwd>/AGENTS.md           LOADED
    <cwd>/AGENTS.override.md  LOADED, and SUPPRESSES AGENTS.md in that dir
    <cwd>/CLAUDE.md           NOT loaded (fallback filenames default to [])
    ancestors' AGENTS.md      only up to a `.git` root; with no marker the
                              walk collapses to cwd, which is every orgtree
                              seat today
    $CODEX_HOME/AGENTS.md     LOADED

Falsifiers supplied here against the real implementation:

F1 omit codex_startup_context_digest from the prompt component -> group 4
F2 hash <cwd>/CLAUDE.md too, as the Claude lane does           -> group 2
F3 walk ancestors even with no `.git` root                     -> group 3
F4 let AGENTS.md win over AGENTS.override.md in one directory   -> group 1

Hermetic: throwaway data/home/CODEX_HOME, a synthetic provider spec so no
codex binary, login, network or app-server is required.

    python backend/tests/test_codex_startup_identity.py
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

RIG = tempfile.mkdtemp(prefix="orgtree-codex-startup-")
TEST_HOME = os.path.join(RIG, "home")
DATA = os.path.join(RIG, "data")
CODEX_HOME = os.path.join(RIG, "codexhome")
for _d in (TEST_HOME, DATA, CODEX_HOME):
    os.makedirs(_d, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["ORGTREE_DATA"] = DATA
os.environ["USERPROFILE"] = TEST_HOME
os.environ["HOME"] = TEST_HOME
os.environ["CODEX_HOME"] = CODEX_HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"
os.environ["ORGTREE_PORT"] = "7418"             # never bound
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


org = store.create_org("codex startup rig")
SLUG = org.d["slug"]
org.hire(USER, None, "sol", 5, "cx", add_dirs=[],
         tools={"mcp": []}, org_visibility="full", charter="c")
store.save_org(org)
CWD = S.scratch_dir(SLUG, "cx")
os.makedirs(CWD, exist_ok=True)

AGENTS = os.path.join(CWD, "AGENTS.md")
OVERRIDE = os.path.join(CWD, "AGENTS.override.md")
CLAUDEMD = os.path.join(CWD, "CLAUDE.md")
GLOBAL_AGENTS = os.path.join(CODEX_HOME, "AGENTS.md")

# A synthetic spec keeps this hermetic: the real one demands an installed,
# signed-in codex. Identity must not depend on that to be testable.
SPEC = {"argv_head": ["codex"], "config_overrides": [], "exe": "codex.exe",
        "env_extra": {"ORGTREE_ORG": SLUG, "ORGTREE_NODE": "cx"}}


def digest() -> str:
    return W.codex_startup_context_digest(store.load_org(SLUG), "cx")


def snapshot():
    return W.identity_snapshot(store.load_org(SLUG), "cx", provider_spec=SPEC)


def clean() -> None:
    for f in (AGENTS, OVERRIDE, CLAUDEMD, GLOBAL_AGENTS):
        remove(f)


def t_cwd_agents_and_override_precedence() -> None:
    """F4: the measured precedence. An override REPLACES AGENTS.md in its own
    directory - so while one exists, edits to AGENTS.md must be invisible,
    because codex is not reading that file."""
    clean()
    base = digest()
    write(AGENTS, "managed identity alpha\n")
    alpha = digest()
    assert alpha != base, "creating <cwd>/AGENTS.md did not move the digest"
    write(AGENTS, "managed identity beta\n")
    assert digest() != alpha, "editing <cwd>/AGENTS.md did not move the digest"
    write(AGENTS, "managed identity alpha\n")
    assert digest() == alpha, "restoring AGENTS.md did not restore the digest"

    write(OVERRIDE, "an override that suppresses the managed identity\n")
    withov = digest()
    assert withov != alpha, "AGENTS.override.md did not move the digest"
    # ...and now AGENTS.md is NOT read, so touching it must change nothing.
    write(AGENTS, "managed identity gamma - codex cannot see this\n")
    assert digest() == withov, (
        "an AGENTS.md edit moved the digest while an AGENTS.override.md "
        "exists - precedence is wrong, we are hashing a file codex ignores")
    remove(OVERRIDE)
    write(AGENTS, "managed identity alpha\n")
    assert digest() == alpha, "removing the override did not restore identity"
    clean()


def t_claude_md_is_not_a_codex_startup_input() -> None:
    """F2: the anti-false-positive guarantee, and the whole reason this is a
    separate function from the Claude lane's. Codex's
    `project_doc_fallback_filenames` defaults to `[]`; measured, CLAUDE.md
    never reaches its prompt. Hashing it would restart Codex agents for edits
    that change nothing they see - worse than the gap this closes."""
    # ALONE FIRST. With an AGENTS.md present the per-directory loop stops
    # there and never consults a later name, so testing only that case passes
    # even when CLAUDE.md IS wrongly in the list - a blind spot a mutant run
    # caught in the first draft of this check. The claim is that CLAUDE.md is
    # NEVER a codex input, so it must hold with nothing else masking it.
    clean()
    bare = digest()
    write(CLAUDEMD, "standing notes codex will never read\n")
    assert digest() == bare, (
        "<cwd>/CLAUDE.md ALONE moved the Codex digest - it is in the doc "
        "name list, and Codex agents will respawn for a file they never read")
    clean()

    # ...and again with the managed AGENTS.md present, the ordinary state.
    write(AGENTS, "managed identity\n")
    base = digest()
    write(CLAUDEMD, "standing notes codex will never read\n")
    assert digest() == base, (
        "<cwd>/CLAUDE.md moved the Codex digest - the Claude file list has "
        "been copied across and Codex agents will respawn for nothing")
    write(CLAUDEMD, "edited standing notes\n")
    assert digest() == base, "editing CLAUDE.md moved the Codex digest"
    clean()


def t_ancestor_docs_only_under_a_git_root() -> None:
    """F3: with no `.git` marker anywhere the walk collapses to cwd. Every
    orgtree seat is in that state today (nothing above the scratch root is a
    repo), so a digest that walked ancestors unconditionally would hash files
    codex never opens."""
    clean()
    write(AGENTS, "managed identity\n")
    parent = os.path.dirname(CWD)
    parent_doc = os.path.join(parent, "AGENTS.md")
    remove(parent_doc)
    base = digest()
    write(parent_doc, "an ancestor doc, no git root present\n")
    try:
        assert digest() == base, (
            "an ancestor AGENTS.md moved the digest with NO .git root - the "
            "walk is unbounded and hashes files codex does not read")
        # Now make the parent a project root: the SAME file becomes real.
        gitdir = os.path.join(parent, ".git")
        os.makedirs(gitdir, exist_ok=True)
        try:
            assert digest() != base, (
                "with a .git root at the parent, its AGENTS.md was still not "
                "hashed - the walk never reaches the project root")
        finally:
            shutil.rmtree(gitdir, ignore_errors=True)
        assert digest() == base, "removing the .git root did not restore"
    finally:
        remove(parent_doc)
    clean()


def t_global_codex_home_agents_is_hashed() -> None:
    """Measured with an isolated CODEX_HOME: $CODEX_HOME/AGENTS.md reaches the
    prompt. It does not exist on this machine today, which is exactly why it
    needs a test rather than an assumption."""
    clean()
    write(AGENTS, "managed identity\n")
    base = digest()
    write(GLOBAL_AGENTS, "machine-wide codex instructions\n")
    assert digest() != base, "$CODEX_HOME/AGENTS.md did not move the digest"
    remove(GLOBAL_AGENTS)
    assert digest() == base, "removing the global doc did not restore"
    clean()


def t_identity_moves_end_to_end_and_falsifier() -> None:
    """F1: the wiring, plus proof the suite can see its own fault.

    An AGENTS.override.md is the sharpest case - it SUPPRESSES the managed
    identity orgtree wrote, so a parked process serving under one is running
    instructions orgtree did not author. The identity hash must move. Then,
    with the digest stubbed to a constant (pre-fix behaviour), the same edit
    must become invisible - otherwise this group proves nothing."""
    clean()
    write(AGENTS, "managed identity\n")
    before = snapshot()
    write(OVERRIDE, "instructions orgtree never wrote\n")
    after = snapshot()
    assert before[0] != after[0], (
        "planting AGENTS.override.md did not move the Codex identity hash")
    fields = W.identity_change_fields(before[0], before[1],
                                      after[0], after[1])
    assert fields["changed_inputs"] == ["prompt"], fields

    saved = W.codex_startup_context_digest
    try:
        W.codex_startup_context_digest = lambda *_a, **_k: "constant"
        blind_before = snapshot()
        write(OVERRIDE, "a different override the pre-fix code cannot see\n")
        assert snapshot()[0] == blind_before[0], (
            "the planted fault did not reproduce: with the digest stubbed "
            "out the edit was STILL visible, so this suite is not measuring "
            "the fix")
    finally:
        W.codex_startup_context_digest = saved
    assert snapshot()[0] != after[0], "restoring the real digest saw nothing"
    clean()


def main() -> int:
    print("codex startup instruction files")
    check("cwd AGENTS.md moves; AGENTS.override.md wins in its directory",
          t_cwd_agents_and_override_precedence)
    check("CLAUDE.md is NOT a codex startup input and must not be hashed",
          t_claude_md_is_not_a_codex_startup_input)
    check("ancestor docs count only under a .git project root",
          t_ancestor_docs_only_under_a_git_root)
    check("$CODEX_HOME/AGENTS.md is hashed",
          t_global_codex_home_agents_is_hashed)
    print("wiring + falsifier")
    check("identity moves end-to-end, and the stubbed digest reproduces the "
          "pre-fix fault", t_identity_moves_end_to_end_and_falsifier)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n[X] {label}\n{tb}")
        print(f"codex-startup-identity: {PASS} passed - {len(FAIL)} FAILED")
        return 1
    print(f"codex-startup-identity: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(RIG, ignore_errors=True)
    sys.exit(rc)
