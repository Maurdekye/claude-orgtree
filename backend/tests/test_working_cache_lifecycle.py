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
import subprocess
import sys
import tempfile
import threading

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

from orgtree import appsettings, cachedeny, store, supervisor as S  # noqa: E402
from orgtree.ledger import USER              # noqa: E402

# This suite pins the fallback mode. The new default-on real checkup mode is
# covered separately and suppresses these disposable reads by design.
appsettings.set_working_checkups_enabled(False)

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


def settings_of(cmd):
    """The spawned CLI's settings through either door of the real contract
    (`--settings <file-or-json>`): inline JSON, or the D-218 scratch file the
    keepalive parks it in."""
    val = cmd[cmd.index("--settings") + 1]
    if val.lstrip().startswith("{"):
        return json.loads(val)
    with open(val, encoding="utf-8") as f:
        return json.load(f)


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


check("Claude cadence splits by billing lane; Codex/Antigravity/idle are skipped",
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
    cfg = {"occ": 0.5}
    assert not S._auto_cheap_ready(base, cfg, {"state": "uncertain"})
    idle = {**base, "last_status": {"status": "idle"},
            "cache_keepalive_at": stamp(10)}
    assert not S._auto_cheap_ready(idle, cfg, {"state": "uncertain"}), (
        "a timestamp without a positive same-lane receipt invented coldness")
    idle["cache_keepalive_at"] = stamp(7200)
    assert not S._auto_cheap_ready(idle, cfg, {"state": "uncertain"})
    assert S._auto_cheap_ready(
        idle, cfg, {"state": "expired_known_entry"})


check("cache-protective compact follows receipt forecast, never raw keepalive age",
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
        S._build_cmd = lambda o, n: [
            "claude", "-p", "--resume", old_sid, "--settings",
            json.dumps({"hooks": {"PostToolUse": [{"hooks": [{
                "type": "command", "command": "steer"}]}]}}),
            "--strict-mcp-config"]
        S.spawn_env = lambda o, tier=None, nid=None: {"LANE": "key"}
        S.subprocess.Popen = Proc
        S._leash = lambda proc: None
        S.scratch_dir = lambda slug, nid: ROOT
        S._working_cache_read(org.d["slug"], "agent")
    finally:
        (S.transcript_path, S._build_cmd, S.spawn_env,
         S.subprocess.Popen, S._leash, S.scratch_dir) = saved

    assert seen["cmd"][-3:] == ["--fork-session", "--max-turns", "1"], \
        seen["cmd"]
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


def tool_use_is_locally_denied_without_hiding_tools():
    org = fixture("zz-working-deny")
    old_sid = org.node("agent")["session_id"]
    fork_file = os.path.join(ROOT, "fork-deny.jsonl")
    with open(fork_file, "w", encoding="utf-8") as f:
        f.write("fork evidence")
    seen = {"executed": False}
    original_settings = {"hooks": {"PostToolUse": [{"hooks": [{
        "type": "command", "command": "existing-steer"}]}]},
        "permissions": {"deny": ["Edit(/read-only/**)"]}}

    class Proc:
        returncode = 0

        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd

        def communicate(self, input=None, timeout=None):
            settings = settings_of(seen["cmd"])
            hook = settings.get("hooks", {}).get("PreToolUse", [])
            decision = cachedeny.decision() if hook else {}
            denied = (decision.get("hookSpecificOutput", {})
                      .get("permissionDecision") == "deny")
            # A fake model response attempts the most privileged org MCP
            # action. The fake CLI executes it only if the local barrier is
            # missing, exactly the regression this test is meant to expose.
            attempted = ["Bash", "Edit", "mcp__orgtree__orgtree_message"]
            tool = {"type": "assistant", "session_id": "fork-deny",
                    "message": {"content": [{"type": "tool_use",
                    "name": name, "input": {}} for name in attempted]}}
            if not denied:
                seen["executed"] = attempted
            result = {"type": "result", "session_id": "fork-deny",
                      "is_error": True, "total_cost_usd": 0.031}
            return json.dumps(tool) + "\n" + json.dumps(result) + "\n", ""

    saved = (S.transcript_path, S._build_cmd, S.spawn_env,
             S.subprocess.Popen, S._leash, S.scratch_dir)
    try:
        S.transcript_path = lambda sid, root=None: (
            fork_file if sid == "fork-deny" else "existing.jsonl")
        S._build_cmd = lambda o, n: [
            "claude", "-p", "--resume", old_sid,
            "--settings", json.dumps(original_settings),
            "--strict-mcp-config", "--mcp-config", '{"mcpServers":{"orgtree":{}}}']
        S.spawn_env = lambda o, tier=None, nid=None: {}
        S.subprocess.Popen = Proc
        S._leash = lambda proc: None
        S.scratch_dir = lambda slug, nid: ROOT
        S._working_cache_read(org.d["slug"], "agent")
    finally:
        (S.transcript_path, S._build_cmd, S.spawn_env,
         S.subprocess.Popen, S._leash, S.scratch_dir) = saved

    cmd = seen["cmd"]
    settings = settings_of(cmd)
    assert not seen["executed"], "fake Bash/edit/MCP tools escaped local deny"
    assert settings["hooks"]["PostToolUse"] == \
        original_settings["hooks"]["PostToolUse"]
    assert settings["permissions"] == original_settings["permissions"]
    assert "--mcp-config" in cmd and "--strict-mcp-config" in cmd
    assert cmd[-3:] == ["--fork-session", "--max-turns", "1"], cmd
    assert not os.path.exists(fork_file)
    after = store.load_org(org.d["slug"])
    assert abs(float(after.node("agent").get("cost_usd") or 0) - 0.031) < 1e-9
    assert not after.node("agent").get("cache_keepalive_at")


check("fake Bash/edit/MCP response is denied while real tools/MCP settings remain",
      tool_use_is_locally_denied_without_hiding_tools)


def real_turn_cancels_child_before_resuming_session():
    org = fixture("zz-working-cancel")
    slug = org.d["slug"]
    old_sid = org.node("agent")["session_id"]
    started = threading.Event()
    killed = threading.Event()
    real_ran = []

    class Proc:
        returncode = None

        def __init__(self, cmd, **kw):
            self.cmd = cmd
            started.set()

        def communicate(self, input=None, timeout=None):
            assert killed.wait(3), "maintenance child was not cancelled"
            self.returncode = -9
            return "", ""

        def kill(self):
            killed.set()
            self.returncode = -9

        def wait(self, timeout=None):
            assert killed.wait(timeout or 3)
            return self.returncode

        def poll(self):
            return self.returncode

    saved = (S.transcript_path, S._build_cmd, S.spawn_env,
             S.subprocess.Popen, S._leash, S.scratch_dir, S._wd_kill_tree,
             S._hold_for_deploy, S._run_one_turn)
    try:
        S.transcript_path = lambda sid, root=None: "existing.jsonl"
        S._build_cmd = lambda o, n: [
            "claude", "--resume", old_sid, "--settings", "{}"]
        S.spawn_env = lambda o, tier=None, nid=None: {}
        S.subprocess.Popen = Proc
        S._leash = lambda proc: None
        S.scratch_dir = lambda s, n: ROOT
        S._wd_kill_tree = lambda proc: (proc.kill(), proc.wait(timeout=1))
        S._hold_for_deploy = lambda s, n: None

        def real_turn(s, n, text):
            assert killed.is_set(), "real resume overlapped cache child"
            real_ran.append(text)
            return None

        S._run_one_turn = real_turn
        S._launch_working_cache_read(slug, "agent")
        assert started.wait(3), "maintenance child never started"
        # send_message owns this bit before its worker reaches `_run_turn`.
        st = S.state(slug, "agent")
        with S._state_lock:
            st["busy"] = True
        S._run_turn(slug, "agent", "real work")
    finally:
        (S.transcript_path, S._build_cmd, S.spawn_env,
         S.subprocess.Popen, S._leash, S.scratch_dir, S._wd_kill_tree,
         S._hold_for_deploy, S._run_one_turn) = saved
        st = S.state(slug, "agent")
        with S._state_lock:
            st["busy"] = False
    assert real_ran == ["real work"]


check("real turn kills and waits for maintenance child before session resume",
      real_turn_cancels_child_before_resuming_session)


def cancellation_closes_the_check_to_popen_window():
    org = fixture("zz-working-prepopen")
    slug = org.d["slug"]
    building = threading.Event()
    release_build = threading.Event()
    popens = []
    real_ran = []

    def slow_cmd(o, n):
        building.set()
        assert release_build.wait(3)
        return ["claude", "--settings", "{}", "--fork-session",
                "--max-turns", "1"]

    class ForbiddenProc:
        def __init__(self, cmd, **kw):
            popens.append(cmd)

    saved = (S.transcript_path, S._working_cache_cmd, S.spawn_env,
             S.subprocess.Popen, S.scratch_dir, S._hold_for_deploy,
             S._run_one_turn)
    worker = None
    try:
        S.transcript_path = lambda sid, root=None: "existing.jsonl"
        S._working_cache_cmd = slow_cmd
        S.spawn_env = lambda o, tier=None, nid=None: {}
        S.subprocess.Popen = ForbiddenProc
        S.scratch_dir = lambda s, n: ROOT
        S._hold_for_deploy = lambda s, n: None
        S._run_one_turn = lambda s, n, text: (real_ran.append(text), None)[1]

        S._launch_working_cache_read(slug, "agent")
        assert building.wait(3), "maintenance did not enter pre-spawn setup"
        st = S.state(slug, "agent")
        lease = st["cache_keepalive"]
        with S._state_lock:
            st["busy"] = True
        worker = threading.Thread(
            target=S._run_turn, args=(slug, "agent", "real work"))
        worker.start()
        assert lease["cancel"].wait(3), "real turn did not cancel reservation"
        release_build.set()
        worker.join(3)
        assert not worker.is_alive(), "real turn stayed behind maintenance"
    finally:
        release_build.set()
        if worker is not None:
            worker.join(3)
        (S.transcript_path, S._working_cache_cmd, S.spawn_env,
         S.subprocess.Popen, S.scratch_dir, S._hold_for_deploy,
         S._run_one_turn) = saved
        st = S.state(slug, "agent")
        with S._state_lock:
            st["busy"] = False

    assert popens == [], "cancelled reservation spawned after the real turn"
    assert real_ran == ["real work"]


check("real turn cancellation closes the maintenance check-to-Popen race",
      cancellation_closes_the_check_to_popen_window)


def timeout_reaps_partial_fork_banks_cost_and_backs_off():
    org = fixture("zz-working-timeout")
    org.d["api_key"] = "sk-ant-test"
    store.save_org(org)
    slug = org.d["slug"]
    old_sid = org.node("agent")["session_id"]
    fork_file = os.path.join(ROOT, "fork-timeout.jsonl")
    with open(fork_file, "w", encoding="utf-8") as f:
        f.write("partial fork")
    partial = (json.dumps({"type": "system", "session_id": "fork-timeout"})
               + "\n" + json.dumps({"type": "result",
                   "session_id": "fork-timeout", "is_error": True,
                   "total_cost_usd": 0.25}) + "\n")

    class Proc:
        returncode = None

        def __init__(self, cmd, **kw):
            self.calls = 0

        def communicate(self, input=None, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("claude", timeout,
                                                output=partial)
            return "", ""

        def kill(self):
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

        def poll(self):
            return self.returncode

    saved = (S.transcript_path, S._build_cmd, S.spawn_env,
             S.subprocess.Popen, S._leash, S.scratch_dir, S._wd_kill_tree)
    try:
        S.transcript_path = lambda sid, root=None: (
            fork_file if sid == "fork-timeout" else "existing.jsonl")
        S._build_cmd = lambda o, n: [
            "claude", "--resume", old_sid, "--settings", "{}"]
        S.spawn_env = lambda o, tier=None, nid=None: {}
        S.subprocess.Popen = Proc
        S._leash = lambda proc: None
        S.scratch_dir = lambda s, n: ROOT
        S._wd_kill_tree = lambda proc: (proc.kill(), proc.wait(timeout=1))
        S._working_cache_read(slug, "agent")
        failures, retry_at = S._working_cache_retry[(slug, "agent")]
        assert failures == 1
        calls = []
        S._working_cache_keeper_pass(
            launch=lambda s, n: calls.append((s, n)), now=retry_at - 0.01)
        assert (slug, "agent") not in calls, calls
        calls.clear()
        S._working_cache_keeper_pass(
            launch=lambda s, n: calls.append((s, n)), now=retry_at + 0.01)
        assert (slug, "agent") in calls, calls
    finally:
        (S.transcript_path, S._build_cmd, S.spawn_env,
         S.subprocess.Popen, S._leash, S.scratch_dir, S._wd_kill_tree) = saved
        S._working_cache_clear_failure(slug, "agent")

    assert not os.path.exists(fork_file), "partial timeout fork survived"
    after = store.load_org(slug)
    assert abs(float(after.node("agent").get("cost_usd") or 0) - 0.25) < 1e-9
    assert abs(float(after.d.get("api_cost_usd") or 0) - 0.25) < 1e-9
    assert not after.node("agent").get("cache_keepalive_at")


check("timeout reaps partial fork, banks reported cost, and delays retry",
      timeout_reaps_partial_fork_banks_cost_and_backs_off)


def retry_backoff_is_bounded():
    slug, nid, now = "zz-backoff", "agent", 1000.0
    S._working_cache_clear_failure(slug, nid)
    delays = []
    try:
        for _ in range(12):
            delays.append(S._working_cache_note_failure(slug, nid, now) - now)
        assert delays[0] == S.WORKING_CACHE_RETRY_BASE_S, delays
        assert delays[1] == min(S.WORKING_CACHE_RETRY_MAX_S,
                                S.WORKING_CACHE_RETRY_BASE_S * 2), delays
        assert delays[-1] == S.WORKING_CACHE_RETRY_MAX_S, delays
        assert all(a <= b for a, b in zip(delays, delays[1:])), delays
    finally:
        S._working_cache_clear_failure(slug, nid)


check("failed-account retry backoff grows exponentially and stays bounded",
      retry_backoff_is_bounded)


shutil.rmtree(ROOT, ignore_errors=True)
print(f"\nALL {PASS} CHECKS PASS" if not FAIL else f"\n{FAIL} FAILED, {PASS} PASSED")
raise SystemExit(1 if FAIL else 0)
