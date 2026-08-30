"""Warm identity covers native startup instructions and canonical MCP JSON.

Independent audit found two opposite defects on current main:

* editing an agent's own cwd CLAUDE.md did NOT move ident_hash, so a parked
  process could retain stale instructions forever;
* reordering object keys in an otherwise identical MCP spec DID move the
  hash, killing a valid process for a formatting-only edit.

Falsifiers supplied here against the real implementation:

M1 omit native_startup_context_digest from the prompt component -> groups 1-3
M2 hash global skills too                                      -> group 2
M3 hash lazy path-scoped rules                                 -> group 2
M4 ignore CLAUDE.md imports                                    -> group 2
M5 serialize MCP JSON without sort_keys                        -> group 4

Hermetic: throwaway data/home, fake process for the keeper decision, no CLI,
listener, network or production journal.

    python backend/tests/test_warm_native_identity.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import traceback
import types

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

RIG = tempfile.mkdtemp(prefix="orgtree-native-identity-")
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
os.environ["ORGTREE_PORT"] = "7415"             # never bound
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


org = store.create_org("native identity rig")
SLUG = org.d["slug"]
org.hire(USER, None, "haiku", 5, "boss", add_dirs=[],
         tools={"mcp": ["*"]}, org_visibility="full", charter="c")
store.save_org(org)
NID = "boss"
CWD = S.scratch_dir(SLUG, NID)


def snapshot() -> tuple[str, dict[str, str]]:
    return W.identity_snapshot(store.load_org(SLUG), NID)


def assert_prompt_only(before, after) -> None:
    assert before[0] != after[0], "combined hash did not move"
    fields = W.identity_change_fields(before[0], before[1],
                                      after[0], after[1])
    assert fields["changed_inputs"] == ["prompt"], fields


def t_native_cwd_file_moves_and_restores_hash() -> None:
    """M1: the independent audit's exact planted input."""
    path = os.path.join(CWD, "CLAUDE.md")
    remove(path)
    empty = snapshot()
    write(path, "alpha\n")
    alpha = snapshot()
    assert_prompt_only(empty, alpha)
    write(path, "beta\n")
    beta = snapshot()
    assert_prompt_only(alpha, beta)
    write(path, "alpha\n")
    assert snapshot() == alpha, "content restoration did not restore identity"
    remove(path)
    assert snapshot() == empty, "deletion did not restore absent-file identity"


def t_equivalent_startup_files_and_deliberate_exclusions() -> None:
    """M2-M4: real startup peers move; watched/lazy inputs do not."""
    # Production already has this standing directory. Creating the DIRECTORY
    # itself legitimately adds a stable --add-dir argv entry; establish that
    # condition before the baseline so this check isolates CONTENT edits.
    os.makedirs(os.path.join(TEST_HOME, ".claude", "skills"), exist_ok=True)
    base = snapshot()
    project_parent = os.path.dirname(CWD)
    memory = os.path.join(TEST_HOME, ".claude", "projects",
                          S._cli_project_dir(CWD), "memory", "MEMORY.md")
    startup_paths = [
        os.path.join(CWD, "CLAUDE.local.md"),
        os.path.join(CWD, ".claude", "CLAUDE.md"),
        os.path.join(project_parent, "CLAUDE.md"),
        os.path.join(TEST_HOME, ".claude", "CLAUDE.md"),
        os.path.join(CWD, ".claude", "rules", "always.md"),
        memory,
    ]
    for path in startup_paths:
        remove(path)
        write(path, f"startup input {os.path.basename(path)}\n")
        assert_prompt_only(base, snapshot())
        remove(path)
        assert snapshot() == base, f"removing {path} did not restore identity"

    # Imports are expanded at launch. Changing the target, without touching
    # CLAUDE.md itself, must still dirty the process and must restore exactly.
    native = os.path.join(CWD, "CLAUDE.md")
    imported = os.path.join(CWD, "imported.md")
    write(native, "Read @imported.md before acting.\n")
    write(imported, "one\n")
    one = snapshot()
    write(imported, "two\n")
    two = snapshot()
    assert_prompt_only(one, two)
    write(imported, "one\n")
    assert snapshot() == one
    remove(native)
    remove(imported)
    assert snapshot() == base

    # Global skills are watched live by the CLI; hashing them would be a
    # cache-only regression, not a stale-process fix.
    skill = os.path.join(TEST_HOME, ".claude", "skills", "demo", "SKILL.md")
    write(skill, "---\nname: demo\n---\nfirst\n")
    assert snapshot() == base, "a watched global skill dirtied the warm hash"
    write(skill, "---\nname: demo\n---\nsecond\n")
    assert snapshot() == base, "a watched global skill edit dirtied the hash"
    remove(skill)

    # A path-scoped rule is injected lazily only after a matching file read.
    scoped = os.path.join(CWD, ".claude", "rules", "lazy.md")
    write(scoped, "---\npaths:\n  - src/**\n---\nlazy\n")
    assert snapshot() == base, "a lazy path-scoped rule dirtied startup"
    remove(scoped)


def fake_wp(ihash: str, parts: dict[str, str], pid: int):
    return types.SimpleNamespace(
        slug=SLUG, nid=NID, sid="native-session", hash=ihash,
        ident_components=parts, identity_change=None,
        _lk=threading.Lock(), exit_journaled=False, exit_reason=None,
        proc=types.SimpleNamespace(pid=pid), claimed=False,
        alive=lambda: True,
    )


def t_keeper_rewarms_on_edit_and_revert() -> None:
    """M1 live path: a real file mutation drives keeper kill + rewarm twice."""
    path = os.path.join(CWD, "CLAUDE.md")
    write(path, "alpha\n")
    alpha = snapshot()
    write(path, "beta\n")
    beta = snapshot()
    write(path, "alpha\n")

    saved_spawn, saved_kill, saved_warm = (
        W._spawn_for, W._kill_proc, W._set_proc_warm)
    spawns: list[str] = []
    kills: list[int] = []
    W._spawn_for = lambda _org, _nid, why: spawns.append(why) or None
    W._kill_proc = lambda wp: kills.append(wp.proc.pid)
    W._set_proc_warm = lambda _slug, _nid, _value: None
    with W._pool_lock:
        prior = dict(W._pool)
        W._pool.clear()
    try:
        with W._pool_lock:
            W._pool[(SLUG, NID)] = fake_wp(alpha[0], alpha[1], 6101)
        write(path, "beta\n")
        W._keeper_pass()
        assert kills == [6101] and spawns == ["identity-changed"], (
            kills, spawns)
        assert snapshot() == beta

        kills.clear()
        spawns.clear()
        with W._pool_lock:
            W._pool[(SLUG, NID)] = fake_wp(beta[0], beta[1], 6102)
        write(path, "alpha\n")
        W._keeper_pass()
        assert kills == [6102] and spawns == ["identity-changed"], (
            kills, spawns)
        assert snapshot() == alpha, "revert did not restore original identity"
    finally:
        W._spawn_for, W._kill_proc, W._set_proc_warm = (
            saved_spawn, saved_kill, saved_warm)
        with W._pool_lock:
            W._pool.clear()
            W._pool.update(prior)
        remove(path)


def mcp_arg() -> str:
    cmd = S._build_cmd(store.load_org(SLUG), NID, write_ident=False)
    return cmd[cmd.index("--mcp-config") + 1]


def write_registry(doc: dict) -> None:
    write(os.path.join(TEST_HOME, ".claude.json"),
          json.dumps(doc, ensure_ascii=False))


def t_mcp_object_key_order_is_canonical() -> None:
    """M5: reorder objects only -> byte-identical argv/hash; value -> move."""
    first = {"mcpServers": {
        "zeta": {"command": "demo", "args": ["one", "two"],
                 "env": {"B": "2", "A": "1"}},
        "alpha": {"type": "http", "url": "http://127.0.0.1:9/mcp"},
    }}
    reordered = {"mcpServers": {
        "alpha": {"url": "http://127.0.0.1:9/mcp", "type": "http"},
        "zeta": {"env": {"A": "1", "B": "2"},
                 "args": ["one", "two"], "command": "demo"},
    }}
    changed = {"mcpServers": {
        **reordered["mcpServers"],
        "zeta": {**reordered["mcpServers"]["zeta"], "command": "demo-2"},
    }}

    write_registry(first)
    arg1, snap1 = mcp_arg(), snapshot()
    write_registry(reordered)
    arg2, snap2 = mcp_arg(), snapshot()
    assert arg1 == arg2, "object-key-only reorder changed actual spawn argv"
    assert snap1 == snap2, "object-key-only reorder changed warm identity"

    write_registry(changed)
    arg3, snap3 = mcp_arg(), snapshot()
    assert arg3 != arg2, "MCP value change did not move actual argv"
    fields = W.identity_change_fields(snap2[0], snap2[1],
                                      snap3[0], snap3[1])
    assert fields["changed_inputs"] == ["argv"], fields
    write_registry(first)
    assert mcp_arg() == arg1 and snapshot() == snap1, (
        "semantic restoration did not restore argv/hash")


def main() -> int:
    print("native session-start identity")
    check("cwd CLAUDE.md add/edit/revert/delete moves and restores prompt hash",
          t_native_cwd_file_moves_and_restores_hash)
    check("equivalent startup files move; watched/lazy inputs do not",
          t_equivalent_startup_files_and_deliberate_exclusions)
    check("keeper requests rewarm on a planted edit and its restoration",
          t_keeper_rewarms_on_edit_and_revert)
    print("MCP identity canonicalization")
    check("object-key reorder is stable; a value change moves and restores",
          t_mcp_object_key_order_is_canonical)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n✗ {label}\n{tb}")
        print(f"warm-native-identity: {PASS} passed · {len(FAIL)} FAILED")
        return 1
    print(f"warm-native-identity: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(RIG, ignore_errors=True)
    sys.exit(rc)
