"""D-206 — fleet cache-break diagnoser env + per-node env overrides.

Two spawn-path properties land together and each check names the mutant that
must make it fail (org discipline: before believing a passing check, name the
input that would make it fail):

    §1  CLAUDE_CODE_IS_COWORK=1 reaches every unsandboxed claude spawn — and
        never a sandboxed one, and never by inheriting the backend's own env
    §2  env-overrides.json: explicit entries only, credential names refused,
        applied only when the caller names a node
    §3  an override change MOVES warmpool.ident_hash — without that, editing
        the file would silently not reach a parked process (spawn env is
        otherwise not part of the identity hash; docs/cache-hazards.md)

Hermetic: throwaway data root + HOME, no listener, no Docker, no CLI.

    python backend/tests/test_d206_env.py [-v]
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback
import types

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-d206env-")
_HOME = os.path.join(_TMP, "home")
os.makedirs(_HOME, exist_ok=True)
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
# hub guard — same rationale as test_headless: a bare data root must not let
# any accidentally-started daemon register fixture orgs on the real hub
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
os.environ["USERPROFILE"] = _HOME
os.environ["HOME"] = _HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"
os.environ["ORGTREE_PORT"] = "7412"          # never bound
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

from orgtree import sandbox as sbx, store, supervisor, warmpool    # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
VERBOSE = "-v" in sys.argv


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


class _StubOrg:
    """The minimum spawn_env touches on an org: `.d` (slug, no api_key)."""
    def __init__(self, slug="t", **d):
        self.d = {"slug": slug, **d}


_OV_PATH = os.path.join(store.DATA_ROOT, "env-overrides.json")


def _fresh_cache() -> None:
    supervisor._ENV_OVERRIDES_CACHE.update(at=0.0, mtime=None, val={})


def _write_overrides(doc) -> None:
    with open(_OV_PATH, "w", encoding="utf-8") as f:
        f.write(doc if isinstance(doc, str) else json.dumps(doc))
    _fresh_cache()


def _clear_overrides() -> None:
    try:
        os.remove(_OV_PATH)
    except OSError:
        pass
    _fresh_cache()


def _unsandboxed(fn):
    """Run fn with sandbox detection forced OFF, restored afterwards."""
    real = sbx.is_sandboxed
    sbx.is_sandboxed = lambda _o: False
    try:
        return fn()
    finally:
        sbx.is_sandboxed = real


# ── §1 the fleet diagnoser flag ────────────────────────────────────────────

def s1_cowork_set_for_unsandboxed():
    # Mutant that must fail here: delete the env["CLAUDE_CODE_IS_COWORK"]
    # line in spawn_env.
    _clear_overrides()
    env = _unsandboxed(lambda: supervisor.spawn_env(_StubOrg()))
    assert env.get("CLAUDE_CODE_IS_COWORK") == "1", (
        "the fleet diagnoser flag is not reaching claude spawns")


def s1_cowork_is_injected_not_inherited():
    # clean_env strips every CLAUDE_CODE_* var; the value must come from the
    # injection after the strip, not survive from the backend's own env.
    # Mutant that must fail here: move the injection ABOVE clean_env().
    os.environ["CLAUDE_CODE_IS_COWORK"] = "inherited"
    try:
        env = _unsandboxed(lambda: supervisor.spawn_env(_StubOrg()))
        assert env.get("CLAUDE_CODE_IS_COWORK") == "1", (
            "the flag rode the ambient environment instead of the injection")
    finally:
        os.environ.pop("CLAUDE_CODE_IS_COWORK", None)


def s1_cowork_absent_for_sandboxed():
    # Mutant that must fail here: move the injection ABOVE the sandbox
    # early-return.
    real = sbx.is_sandboxed
    sbx.is_sandboxed = lambda _o: True
    try:
        env = supervisor.spawn_env(_StubOrg())
    finally:
        sbx.is_sandboxed = real
    assert "CLAUDE_CODE_IS_COWORK" not in env, (
        "sandbox spawns must keep the host-side env untouched")


# ── §2 env-overrides.json ──────────────────────────────────────────────────

def s2_absent_file_means_no_overrides():
    _clear_overrides()
    assert supervisor.env_overrides("t", "a") == {}


def s2_explicit_entry_reaches_its_node_only():
    _write_overrides({"t/a": {"MY_TRIAL_FLAG": "on"}})
    try:
        assert supervisor.env_overrides("t", "a") == {"MY_TRIAL_FLAG": "on"}
        assert supervisor.env_overrides("t", "b") == {}, (
            "an override leaked onto a node the file never named")
    finally:
        _clear_overrides()


def s2_malformed_file_means_no_overrides():
    _write_overrides("{not json")
    try:
        assert supervisor.env_overrides("t", "a") == {}, (
            "a malformed overrides file must read as empty, never raise")
    finally:
        _clear_overrides()


def s2_credential_names_are_refused():
    # Mutant that must fail here: drop the deny check in env_overrides.
    _write_overrides({"t/a": {
        "ANTHROPIC_API_KEY": "sk-x",
        "ANTHROPIC_AUTH_TOKEN": "sk-y",
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-z",
        "SAFE_VAR": "ok",
    }})
    try:
        assert supervisor.env_overrides("t", "a") == {"SAFE_VAR": "ok"}, (
            "an override smuggled a credential name past the deny list")
    finally:
        _clear_overrides()


def s2_spawn_env_applies_overrides_only_with_nid():
    _write_overrides({"t/a": {"MY_TRIAL_FLAG": "on"}})
    try:
        with_nid = _unsandboxed(
            lambda: supervisor.spawn_env(_StubOrg(), nid="a"))
        without = _unsandboxed(lambda: supervisor.spawn_env(_StubOrg()))
        assert with_nid.get("MY_TRIAL_FLAG") == "on", (
            "the turn spawn does not receive the node's override")
        assert "MY_TRIAL_FLAG" not in without, (
            "an override leaked into a spawn that passed no nid")
    finally:
        _clear_overrides()


# ── §3 overrides move the warm-pool identity hash ──────────────────────────

def s3_ident_hash_moves_with_overrides():
    """Mutant that must fail here: remove the env_overrides input from
    warmpool.ident_hash. Control: identical inputs → identical hash, else
    the hash is noise and every boundary would respawn."""
    saved = {n: getattr(supervisor, n) for n in
             ("identity_prompt", "_build_cmd", "identity_in_env",
              "spawn_env", "env_overrides")}
    ov = {"v": {}}
    try:
        supervisor.identity_prompt = lambda _o, _n, **_k: "P"
        supervisor._build_cmd = lambda _o, _n, **_k: ["c"]
        supervisor.identity_in_env = lambda _e: "primary"
        supervisor.spawn_env = lambda _o, tier=None, nid=None: {}
        supervisor.env_overrides = lambda _s, _n: ov["v"]
        org = types.SimpleNamespace(
            d={"slug": "t"}, node=lambda _n: {"model": "m"})
        base = warmpool.ident_hash(org, "a")
        same = warmpool.ident_hash(org, "a")
        assert base == same, "hash is unstable with unchanged inputs"
        ov["v"] = {"MY_TRIAL_FLAG": "on"}
        moved = warmpool.ident_hash(org, "a")
        assert moved != base, (
            "an env-override change does not move ident_hash — a parked "
            "process would keep serving without the new env")
    finally:
        for n, f in saved.items():
            setattr(supervisor, n, f)


def main() -> int:
    print("§1 the fleet diagnoser flag")
    check("CLAUDE_CODE_IS_COWORK=1 reaches an unsandboxed spawn",
          s1_cowork_set_for_unsandboxed)
    check("the flag is injected after clean_env, not inherited",
          s1_cowork_is_injected_not_inherited)
    check("a sandboxed spawn's env stays untouched",
          s1_cowork_absent_for_sandboxed)
    print("§2 env-overrides.json")
    check("absent file → no overrides", s2_absent_file_means_no_overrides)
    check("an explicit entry reaches its node and no other",
          s2_explicit_entry_reaches_its_node_only)
    check("malformed file → no overrides, no raise",
          s2_malformed_file_means_no_overrides)
    check("credential names are refused",
          s2_credential_names_are_refused)
    check("spawn_env applies overrides only when a nid is named",
          s2_spawn_env_applies_overrides_only_with_nid)
    print("§3 overrides are identity")
    check("an override change moves warmpool.ident_hash (and only a change)",
          s3_ident_hash_moves_with_overrides)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n✗ {label}\n{tb}")
        print(f"d206-env: {PASS} passed · {len(FAIL)} FAILED")
        return 1
    print(f"d206-env: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)
    sys.exit(rc)
