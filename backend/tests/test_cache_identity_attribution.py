"""D-206 follow-up: cache-break visibility + identity-change attribution.

Every check has a named falsifier, and the mutant run in the landing notes
supplies them against the real source:

M1  force changed_inputs=[] -> the four single-input and two-input cases fail
M2  ignore the supplied spawn env -> exact-spawn snapshot check fails
M3  drop the identity payload from exit/dirty rows -> row contract fails
M4  remove either stderr owner -> warm/cold sentinel owner check fails
M5  journal general stderr -> the zero-row control fails
M6  remove the 4096 cap -> the long-line control fails

Hermetic: throwaway data root/home, no listener, no CLI, no network.

    python backend/tests/test_cache_identity_attribution.py [-v]
"""
from __future__ import annotations

import collections
import inspect
import io
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

TMP = tempfile.mkdtemp(prefix="orgtree-cache-attribution-")
HOME = os.path.join(TMP, "home")
DATA = os.path.join(TMP, "data")
os.makedirs(HOME, exist_ok=True)
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["ORGTREE_DATA"] = DATA
os.environ["USERPROFILE"] = HOME
os.environ["HOME"] = HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"
os.environ["ORGTREE_PORT"] = "7414"       # never bound

from orgtree import store, supervisor as S, warmpool as W  # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
JOURNAL = os.path.join(DATA, "journals", "warm.jsonl")


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                           # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def clear_journal() -> None:
    try:
        os.remove(JOURNAL)
    except OSError:
        pass


def rows() -> list[dict]:
    if not os.path.exists(JOURNAL):
        return []
    out = []
    with open(JOURNAL, encoding="utf-8") as f:
        for line in f:
            out.append(json.loads(line))
    return out


ORG = types.SimpleNamespace(
    d={"slug": "t"},
    nodes={"a": {"state": "live", "model": "opus"}},
    node=lambda _nid: {"state": "live", "model": "opus"},
)


def snapshot(values):
    return W.identity_snapshot(
        ORG, "a", cmd=list(values["argv"]),
        env={"TEST_CREDENTIAL": values["cred"]},
        overrides=dict(values["envov"]))


def with_identity_stubs(fn):
    saved_prompt = S.identity_prompt
    saved_identity = S.identity_in_env
    values = {
        "prompt": "prompt-A",
        "argv": ["claude", "--session-id", "sid", "--model", "m"],
        "cred": "primary",
        "envov": {"TRIAL": "off"},
    }
    S.identity_prompt = lambda _org, _nid, **_kw: values["prompt"]
    S.identity_in_env = lambda env: env["TEST_CREDENTIAL"]
    try:
        return fn(values)
    finally:
        S.identity_prompt = saved_prompt
        S.identity_in_env = saved_identity


def t_components_name_exact_changes():
    """M1: hard-code/omit any changed-input name -> exact cases fail."""
    def run(values):
        base_hash, base = snapshot(values)
        assert tuple(base) == W.IDENTITY_COMPONENTS
        mutations = {
            "prompt": lambda: values.__setitem__("prompt", "prompt-B"),
            "argv": lambda: values["argv"].append("--verbose"),
            "cred": lambda: values.__setitem__("cred", "key-row-2"),
            "envov": lambda: values.__setitem__("envov", {"TRIAL": "on"}),
        }
        original = {"prompt": values["prompt"], "argv": list(values["argv"]),
                    "cred": values["cred"], "envov": dict(values["envov"])}
        for name, mutate in mutations.items():
            values.update(prompt=original["prompt"], argv=list(original["argv"]),
                          cred=original["cred"], envov=dict(original["envov"]))
            mutate()
            new_hash, new = snapshot(values)
            fields = W.identity_change_fields(base_hash, base, new_hash, new)
            assert new_hash != base_hash, f"{name} did not move combined hash"
            assert fields["changed_inputs"] == [name], (name, fields)
            assert fields["attribution_complete"] is True
            assert fields["changed_inputs"] == [
                k for k in W.IDENTITY_COMPONENTS if base[k] != new[k]]

        values.update(prompt="prompt-B", argv=list(original["argv"]),
                      cred="key-row-2", envov=dict(original["envov"]))
        two_hash, two = snapshot(values)
        fields = W.identity_change_fields(base_hash, base, two_hash, two)
        assert fields["changed_inputs"] == ["prompt", "cred"], fields

        values.update(prompt=original["prompt"], argv=list(original["argv"]),
                      cred=original["cred"], envov=dict(original["envov"]))
        same_hash, same = snapshot(values)
        fields = W.identity_change_fields(base_hash, base, same_hash, same)
        assert same_hash == base_hash
        assert fields["changed_inputs"] == [] and fields["attribution_complete"]

        fields = W.identity_change_fields(base_hash, None, two_hash, two)
        assert fields["attribution_complete"] is False
        assert fields["previous_components"] is None
        assert fields["next_components"] == two
        assert fields["changed_inputs"] is None
    with_identity_stubs(run)


def t_snapshot_uses_actual_spawn_values():
    """M2: re-resolve cmd/env/override despite supplied values -> raises."""
    def run(values):
        saved = (S._build_cmd, S.spawn_env, S.env_overrides)
        S._build_cmd = lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("re-built argv instead of using spawn argv"))
        S.spawn_env = lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("re-resolved credential instead of using spawn env"))
        S.env_overrides = lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("re-read overrides instead of using spawn override"))
        try:
            h1, p1 = snapshot(values)
            h2, p2 = snapshot(values)
            assert h1 == h2 and p1 == p2
        finally:
            S._build_cmd, S.spawn_env, S.env_overrides = saved
    with_identity_stubs(run)


def fake_wp(base_hash, components, pid=4242):
    return types.SimpleNamespace(
        slug="t", nid="a", sid="session-join-key", hash=base_hash,
        ident_components=components, identity_change=None,
        _lk=threading.Lock(), exit_journaled=False, exit_reason=None,
        proc=types.SimpleNamespace(pid=pid), claimed=False,
        alive=lambda: True,
    )


def assert_attribution_row(row, previous, nxt, changed):
    assert row["at"].endswith("Z")
    assert row["session_id"] == "session-join-key"
    assert row["pid"] == 4242
    assert row["previous_components"] == previous
    assert row["next_components"] == nxt
    assert row["changed_inputs"] == changed
    assert row["changed_inputs"] == [
        k for k in W.IDENTITY_COMPONENTS if previous[k] != nxt[k]]
    assert row["attribution_complete"] is True


def t_keeper_dirty_and_exit_rows_are_verifiable():
    """M3: drop payload/session/pid from dirty or any exit -> contract fails."""
    def run(values):
        base_hash, base = snapshot(values)
        values["argv"].append("--changed")
        next_hash, nxt = snapshot(values)
        wp = fake_wp(base_hash, base)
        saved = {
            "list_orgs": store.list_orgs, "load_org": store.load_org,
            "warm_enabled": W.warm_enabled, "eligible": W.eligible,
            "busy": W._busy, "snapshot": W.identity_snapshot,
            "spawn": W._spawn_for, "kill": W._kill_proc,
            "warm": W._set_proc_warm,
        }
        clear_journal()
        with W._pool_lock:
            old_pool = dict(W._pool)
            W._pool.clear()
            W._pool[("t", "a")] = wp
        store.list_orgs = lambda: [{"slug": "t"}]
        store.load_org = lambda _slug: ORG
        W.warm_enabled = lambda: True
        W.eligible = lambda _org, _nid: (True, "")
        W._busy = lambda _slug, _nid: False
        W.identity_snapshot = lambda _org, _nid, **_kw: (next_hash, nxt)
        W._spawn_for = lambda _org, _nid, _why: None
        W._kill_proc = lambda _wp: None
        W._set_proc_warm = lambda _slug, _nid, _v: None
        try:
            W._keeper_pass()
            got = [r for r in rows() if r.get("reason") == "identity-changed"]
            assert [r["event"] for r in got] == ["dirty", "exit"], got
            for row in got:
                assert_attribution_row(row, base, nxt, ["argv"])

            # The other keeper exit owner is a process already found dead.
            # It has no identity delta, but it must retain the same ordering
            # keys; calling _journal_proc directly used to omit both.
            clear_journal()
            dead = fake_wp(base_hash, base, pid=4243)
            dead.alive = lambda: False
            with W._pool_lock:
                W._pool[("t", "a")] = dead
            W._keeper_pass()
            crash = [r for r in rows()
                     if r.get("event") == "exit"
                     and r.get("reason") == "crash"]
            assert len(crash) == 1, crash
            assert crash[0]["session_id"] == "session-join-key"
            assert crash[0]["pid"] == 4243
            assert crash[0]["at"].endswith("Z")
        finally:
            store.list_orgs = saved["list_orgs"]
            store.load_org = saved["load_org"]
            W.warm_enabled = saved["warm_enabled"]
            W.eligible = saved["eligible"]
            W._busy = saved["busy"]
            W.identity_snapshot = saved["snapshot"]
            W._spawn_for = saved["spawn"]
            W._kill_proc = saved["kill"]
            W._set_proc_warm = saved["warm"]
            with W._pool_lock:
                W._pool.clear()
                W._pool.update(old_pool)
    with_identity_stubs(run)


def t_claim_and_boundary_paths_record_components():
    """Keeper is not the only detector: claim-time and mid-turn must match."""
    def run(values):
        base_hash, base = snapshot(values)
        values["cred"] = "key-row-3"
        next_hash, nxt = snapshot(values)
        saved_kill, saved_warm = W._kill_proc, W._set_proc_warm
        try:
            clear_journal()
            wp = fake_wp(base_hash, base)
            with W._pool_lock:
                W._pool[("t", "a")] = wp
            W._kill_proc = lambda _wp: None
            W._set_proc_warm = lambda _slug, _nid, _v: None
            got_wp, reason = W.claim_snapshot("t", "a", next_hash, nxt)
            assert got_wp is None and reason == "identity-changed"
            claim_row = rows()[-1]
            assert claim_row["event"] == "exit"
            assert_attribution_row(claim_row, base, nxt, ["cred"])

            clear_journal()
            wp = fake_wp(base_hash, base)
            saved_load, saved_elig, saved_snap = (
                store.load_org, W.eligible, W.identity_snapshot)
            store.load_org = lambda _slug: ORG
            W.eligible = lambda _org, _nid: (True, "")
            W.identity_snapshot = lambda _org, _nid, **_kw: (next_hash, nxt)
            try:
                ok, _label, why = W.boundary_check("t", "a", base_hash, wp)
                assert not ok and why == "identity-changed"
                W._journal_exit_once(wp, why)
                boundary_row = rows()[-1]
                assert_attribution_row(boundary_row, base, nxt, ["cred"])
            finally:
                store.load_org, W.eligible, W.identity_snapshot = (
                    saved_load, saved_elig, saved_snap)
        finally:
            W._kill_proc, W._set_proc_warm = saved_kill, saved_warm
            with W._pool_lock:
                W._pool.pop(("t", "a"), None)
    with_identity_stubs(run)


def t_stderr_owners_and_safety_controls():
    """M4/M5/M6: both owners, non-marker zero, deterministic long cap."""
    clear_journal()
    W.journal_cache_break_lines(
        "t", "a", "s", 1, "control", "ordinary stderr: token=do-not-log")
    assert rows() == [], "general stderr escaped the private tail"

    warm_line = f"warn: {W.CACHE_BREAK_MARKER} warm cause"
    wp = object.__new__(W.WarmProc)
    wp.slug, wp.nid, wp.sid = "t", "warm", "warm-session"
    wp.proc = types.SimpleNamespace(
        pid=5001, stderr=iter(["ordinary warm stderr\n", warm_line + "\n"]))
    wp.err_tail = collections.deque(maxlen=200)
    W.WarmProc._pump_err(wp)
    warm_rows = rows()
    assert len(warm_rows) == 1 and warm_rows[0]["source"] == "warm-stderr"
    assert warm_rows[0]["line"] == warm_line
    assert warm_rows[0]["session_id"] == "warm-session"
    assert warm_rows[0]["pid"] == 5001 and warm_rows[0]["at"].endswith("Z")

    clear_journal()
    cold_line = f"warn: {W.CACHE_BREAK_MARKER} cold cause"
    proc = types.SimpleNamespace(
        pid=5002, stderr=io.StringIO("ordinary cold stderr\n" + cold_line + "\n"))
    err = W.read_cold_stderr(proc, "t", "cold", "cold-session")
    assert cold_line in err
    cold_rows = rows()
    assert len(cold_rows) == 1 and cold_rows[0]["source"] == "cold-stderr"
    assert cold_rows[0]["line"] == cold_line
    assert cold_rows[0]["session_id"] == "cold-session"
    assert cold_rows[0]["pid"] == 5002 and cold_rows[0]["at"].endswith("Z")

    clear_journal()
    long_line = W.CACHE_BREAK_MARKER + " " + ("x" * 5000)
    W.journal_cache_break_lines("t", "long", "long-session", 5003,
                                "control", long_line)
    row = rows()[0]
    assert row["line"] == long_line[:W.CACHE_BREAK_LINE_MAX]
    assert len(row["line"]) == 4096
    assert row["raw_length"] == len(long_line)
    assert row["truncated"] is True


def t_supervisor_uses_the_instrumented_seams():
    """Source wiring guard; behavioral checks above prove each seam itself."""
    src = inspect.getsource(S._run_one_turn)
    assert "warmpool.identity_snapshot(" in src and "env=env" in src
    assert "turn_hash, turn_components" in src
    assert "warmpool.claim_snapshot(" in src
    assert "slug, nid, turn_hash, turn_components" in src
    assert "slug, nid, turn_hash, wp_turn" in src
    assert "warmpool.read_cold_stderr(proc, slug, nid, sid)" in src


def main() -> int:
    print("identity components")
    check("each of four inputs, two inputs, no-op, missing baseline",
          t_components_name_exact_changes)
    check("snapshot uses actual spawn cmd/env/overrides without re-resolving",
          t_snapshot_uses_actual_spawn_values)
    print("identity-change paths")
    check("keeper dirty + every exit owner carries join keys and digests",
          t_keeper_dirty_and_exit_rows_are_verifiable)
    check("claim-time + boundary-time changes carry the same attribution",
          t_claim_and_boundary_paths_record_components)
    print("cache-break stderr")
    check("warm+cold owners, ordinary zero, long line capped",
          t_stderr_owners_and_safety_controls)
    check("supervisor is wired through the tested spawn/cold-stderr seams",
          t_supervisor_uses_the_instrumented_seams)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"\n✗ {label}\n{tb}")
        print(f"cache-attribution: {PASS} passed · {len(FAIL)} FAILED")
        return 1
    print(f"cache-attribution: all {PASS} checks passed")
    return 0


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(rc)
