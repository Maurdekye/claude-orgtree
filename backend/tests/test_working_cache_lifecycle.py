"""Reported-working prompt-cache lifecycle — hermetic regression suite.

Run: python backend/tests/test_working_cache_lifecycle.py

The pre-fix supervisor had no periodic cache request and both automatic
compaction doors ignored durable `last_status`. These checks pin the whole
contract: provider/billing cadence, busy/queue exclusion, both compaction
gates, fork isolation, timestamp/cost accounting, and fork cleanup.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
import tempfile

ROOT = tempfile.mkdtemp(prefix="orgtree-working-cache-")
os.environ["ORGTREE_DATA"] = ROOT
os.environ["HOME"] = os.path.join(ROOT, "home")
os.environ["USERPROFILE"] = os.path.join(ROOT, "home")
os.environ["ORGTREE_PORT"] = "7419"
os.makedirs(os.environ["HOME"], exist_ok=True)
with open(os.path.join(ROOT, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from orgtree import store, supervisor as S  # noqa: E402
from orgtree.ledger import USER              # noqa: E402

PASS = FAIL = 0


def check(label, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")
    except Exception as e:  # noqa: BLE001
        FAIL += 1
        import traceback
        print(f"  FAIL   {label}: {e}")
        traceback.print_exc(limit=5)


def stamp(seconds_ago: float) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds_ago)
            ).isoformat().replace("+00:00", "Z")


def fixture(name: str = "zz-working-cache"):
    org = store.create_org(name)
    org.hire(USER, None, "haiku", 0, "agent")
    n = org.node("agent")
    n["last_status"] = {"status": "working", "summary": "continuing",
                        "at": stamp(4000)}
    n["turns"] = [{"at": stamp(4000), "cost": 0.0, "ms": 1,
                   "denials": 0}]
    store.save_org(org)
    return org


def cadence_and_provider_gate():
    org = fixture("zz-working-cadence")
    got = S._working_cache_interval(org, "agent")
    assert got == (S.WORKING_CACHE_SUBSCRIPTION_S, False), got
    org.d["api_key"] = "sk-ant-test"
    got = S._working_cache_interval(org, "agent")
    assert got == (S.WORKING_CACHE_API_KEY_S, True), got
    org.node("agent")["model"] = "luna"
    assert S._working_cache_interval(org, "agent") is None
    org.node("agent")["model"] = "flash"
    assert S._working_cache_interval(org, "agent") is None
    org.node("agent")["model"] = "haiku"
    org.node("agent")["last_status"] = {"status": "idle"}
    assert S._working_cache_interval(org, "agent") is None
    store.save_org(org)


check("Claude cadence splits by billing lane; Codex/Gemini/idle are skipped",
      cadence_and_provider_gate)


def busy_and_queue_gate():
    org = fixture("zz-working-busy")
    real_tp = S.transcript_path
    try:
        S.transcript_path = lambda sid, root=None: "existing.jsonl"
        calls = []
        st = S.state(org.d["slug"], "agent")
        st["busy"] = True
        S._working_cache_keeper_pass(
            launch=lambda slug, nid: calls.append((slug, nid)),
            now=dt.datetime.now(dt.timezone.utc).timestamp())
        assert calls == [], calls
        st["busy"] = False
        st["queue"] = ["real turn waiting"]
        S._working_cache_keeper_pass(
            launch=lambda slug, nid: calls.append((slug, nid)),
            now=dt.datetime.now(dt.timezone.utc).timestamp())
        assert calls == [], calls
        st["queue"] = []
        S._working_cache_keeper_pass(
            launch=lambda slug, nid: calls.append((slug, nid)),
            now=dt.datetime.now(dt.timezone.utc).timestamp())
        assert calls == [(org.d["slug"], "agent")], calls
    finally:
        S.transcript_path = real_tp


check("keeper never launches over a busy or queued real turn", busy_and_queue_gate)


def wake_compaction_gate_and_freshness():
    base = {"model": "unknown", "occupancy": 80, "context_window": 100,
            "turns": [{"at": stamp(7200)}],
            "last_status": {"status": "working"}}
    cfg = {"occ": 0.5, "idle_s": 3600.0}
    assert not S._auto_cheap_ready(base, cfg)
    idle = {**base, "last_status": {"status": "idle"},
            "cache_keepalive_at": stamp(10)}
    assert not S._auto_cheap_ready(idle, cfg), (
        "a fresh keepalive was ignored by the coldness heuristic")
    idle["cache_keepalive_at"] = stamp(7200)
    assert S._auto_cheap_ready(idle, cfg)


check("wake-time auto cheap compact skips working and honors keepalive freshness",
      wake_compaction_gate_and_freshness)


def after_turn_gate_reloads_durable_status():
    org = fixture("zz-working-threshold")
    org.node("agent")["cli_compactions"] = 0
    org.node("agent")["occupancy"] = 190_000
    store.save_org(org)
    stale = store.load_org(org.d["slug"])
    # Make the passed object stale on purpose: status arrived through MCP
    # after the turn loaded it. The destructive boundary must re-read disk.
    stale.node("agent").pop("last_status", None)
    calls = []
    real_count, real_split = S._count_cli_compactions, S._compact_split
    try:
        S._count_cli_compactions = lambda o, n: (0, None, [])
        S._compact_split = lambda slug, nid: calls.append((slug, nid))
        S._after_turn(org.d["slug"], "agent", stale, {"usage": {}}, {},
                      occ=190_000)
        assert calls == [], calls
        current = store.load_org(org.d["slug"])
        current.node("agent")["last_status"] = {"status": "idle"}
        store.save_org(current)
        S._after_turn(org.d["slug"], "agent", stale, {"usage": {}}, {},
                      occ=190_000)
        assert calls == [(org.d["slug"], "agent")], calls
    finally:
        S._count_cli_compactions, S._compact_split = real_count, real_split


check("post-turn threshold compaction re-reads and obeys durable working status",
      after_turn_gate_reloads_durable_status)


def disposable_fork_is_accounted_and_reaped():
    org = fixture("zz-working-fork")
    org.d["api_key"] = "sk-ant-test"
    store.save_org(org)
    old_sid = org.node("agent")["session_id"]
    fork_file = os.path.join(ROOT, "fork-session.jsonl")
    with open(fork_file, "w", encoding="utf-8") as f:
        f.write("fork evidence")
    seen = {}

    class Proc:
        returncode = 0

        def __init__(self, cmd, **kw):
            seen["cmd"], seen["env"], seen["cwd"] = cmd, kw["env"], kw["cwd"]

        def communicate(self, input=None, timeout=None):
            seen["input"], seen["timeout"] = input, timeout
            return (json.dumps({"type": "system", "subtype": "init",
                                "session_id": "fork-session"}) + "\n"
                    + json.dumps({"type": "result", "subtype": "success",
                                  "session_id": "fork-session",
                                  "total_cost_usd": 0.125}) + "\n", "")

    saved = (S.transcript_path, S._build_cmd, S.spawn_env,
             S.subprocess.Popen, S._leash, S.scratch_dir)
    try:
        S.transcript_path = lambda sid, root=None: (
            fork_file if sid == "fork-session" else "existing.jsonl")
        S._build_cmd = lambda o, n: ["claude", "-p", "--resume", old_sid,
                                            "--strict-mcp-config"]
        S.spawn_env = lambda o, tier=None, nid=None: {"LANE": "key"}
        S.subprocess.Popen = Proc
        S._leash = lambda proc: None
        S.scratch_dir = lambda slug, nid: ROOT
        S._working_cache_read(org.d["slug"], "agent")
    finally:
        (S.transcript_path, S._build_cmd, S.spawn_env,
         S.subprocess.Popen, S._leash, S.scratch_dir) = saved

    assert seen["cmd"][-1] == "--fork-session", seen["cmd"]
    assert "--strict-mcp-config" in seen["cmd"], seen["cmd"]
    event = json.loads(seen["input"])
    assert S.WORKING_CACHE_PROMPT in event["message"]["content"][0]["text"]
    assert not os.path.exists(fork_file), "disposable fork transcript survived"
    after = store.load_org(org.d["slug"])
    n = after.node("agent")
    assert n["session_id"] == old_sid, "keepalive replaced the live session"
    assert n.get("cache_keepalive_at"), n
    assert abs(float(n.get("cost_usd") or 0) - 0.125) < 1e-9, n
    assert abs(float(after.d.get("api_cost_usd") or 0) - 0.125) < 1e-9
    assert len(n.get("turns") or []) == 1, "keepalive was recorded as agent work"


check("keepalive mirrors real argv, forks, accounts cost, stamps freshness, and reaps",
      disposable_fork_is_accounted_and_reaped)


shutil.rmtree(ROOT, ignore_errors=True)
print(f"\nALL {PASS} CHECKS PASS" if not FAIL else f"\n{FAIL} FAILED, {PASS} PASSED")
raise SystemExit(1 if FAIL else 0)
