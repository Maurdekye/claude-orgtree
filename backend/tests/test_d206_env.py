"""D-206: fleet cache-break diagnoser env + per-node env overrides.

New file per the additive-edits rule. Each check names the input that would
make it fail (org discipline): the mutants are stated next to the asserts.
"""
from __future__ import annotations

import json
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, ".."))

from orgtree import supervisor, warmpool  # noqa: E402


class _StubOrg:
    """The minimum spawn_env touches on an unsandboxed org: `.d`."""
    def __init__(self, slug="t", **d):
        self.d = {"slug": slug, **d}


def _fresh_cache():
    supervisor._ENV_OVERRIDES_CACHE.update(at=0.0, mtime=None, val={})


def _no_sandbox(monkeypatch):
    monkeypatch.setattr(supervisor.sbx, "is_sandboxed", lambda _o: False)


def test_env_overrides_reads_only_explicit_entries(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor.store, "DATA_ROOT", str(tmp_path))
    _fresh_cache()
    # absent file → {}
    assert supervisor.env_overrides("t", "a") == {}
    # present entry → returned; other node → {}
    p = tmp_path / "env-overrides.json"
    p.write_text(json.dumps({"t/a": {"MY_TRIAL_FLAG": "on"}}),
                 encoding="utf-8")
    _fresh_cache()
    assert supervisor.env_overrides("t", "a") == {"MY_TRIAL_FLAG": "on"}
    assert supervisor.env_overrides("t", "b") == {}
    # malformed file → {} (never raises)
    p.write_text("{not json", encoding="utf-8")
    _fresh_cache()
    assert supervisor.env_overrides("t", "a") == {}


def test_env_overrides_refuse_credential_names(monkeypatch, tmp_path):
    # Mutant that must fail here: dropping the deny check in env_overrides.
    monkeypatch.setattr(supervisor.store, "DATA_ROOT", str(tmp_path))
    (tmp_path / "env-overrides.json").write_text(json.dumps({"t/a": {
        "ANTHROPIC_API_KEY": "sk-x",
        "ANTHROPIC_AUTH_TOKEN": "sk-y",
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-z",
        "SAFE_VAR": "ok",
    }}), encoding="utf-8")
    _fresh_cache()
    assert supervisor.env_overrides("t", "a") == {"SAFE_VAR": "ok"}, (
        "an override smuggled a credential name past the deny list")


def test_spawn_env_applies_overrides_only_with_nid(monkeypatch, tmp_path):
    _no_sandbox(monkeypatch)
    monkeypatch.setattr(supervisor.store, "DATA_ROOT", str(tmp_path))
    (tmp_path / "env-overrides.json").write_text(
        json.dumps({"t/a": {"MY_TRIAL_FLAG": "on"}}), encoding="utf-8")
    _fresh_cache()
    with_nid = supervisor.spawn_env(_StubOrg(), nid="a")
    without = supervisor.spawn_env(_StubOrg())
    assert with_nid.get("MY_TRIAL_FLAG") == "on", (
        "the turn spawn does not receive the node's override")
    assert "MY_TRIAL_FLAG" not in without, (
        "an override leaked into a spawn that passed no nid")


def test_ident_hash_moves_with_env_overrides(monkeypatch):
    """The trap this closes: env is not otherwise hashed, so an override
    change would silently not reach a parked process. Mutant that must fail
    here: removing the env_overrides input from warmpool.ident_hash.
    Control: identical overrides → identical hash (else the hash is noise
    and every boundary would respawn)."""
    sup = supervisor
    monkeypatch.setattr(sup, "identity_prompt", lambda _o, _n: "P")
    monkeypatch.setattr(sup, "_build_cmd",
                        lambda _o, _n, write_ident=False: ["c"])
    monkeypatch.setattr(sup, "identity_in_env", lambda _e: "primary")
    monkeypatch.setattr(sup, "spawn_env",
                        lambda _o, tier=None, nid=None: {})
    org = types.SimpleNamespace(
        d={"slug": "t"}, node=lambda _n: {"model": "m"})
    ov = {"v": {}}
    monkeypatch.setattr(sup, "env_overrides", lambda _s, _n: ov["v"])
    base = warmpool.ident_hash(org, "a")
    same = warmpool.ident_hash(org, "a")
    assert base == same, "hash is unstable with unchanged inputs"
    ov["v"] = {"MY_TRIAL_FLAG": "on"}
    moved = warmpool.ident_hash(org, "a")
    assert moved != base, (
        "an env-override change does not move ident_hash — a parked "
        "process would keep serving without the new env")
