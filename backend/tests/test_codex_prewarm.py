"""Full Codex app-server prewarming: initialize + MCP readiness before warm.

Hermetic (fakecodex impostor, no provider calls). What this suite pins, per
the user-authorized 2026-09-01 contract:

  · prewarm completes `initialize`/`initialized` and PLANTS the runtime MCP
    inventory before any prompt exists, and sends NO thread/start, NO
    thread/resume, NO developer instructions and NO turn/start;
  · the seat is not CALLED warm until then — lifecycle runs
    initializing → ready (or explicitly degraded), visibly on the WS stream;
  · the first prompt still claims the exact prewarmed PID and initialize
    stays once-per-process;
  · a mute or dying handshake is reaped (children killed, classified exit
    row) and the seat falls back to cold turns and later re-warms.

    python backend/tests/test_codex_prewarm.py
"""

import json
import os
import sys
import tempfile
import threading
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-codexprewarm-")
HOME = tempfile.mkdtemp(prefix="codexprewarm-home-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_WARM"] = "1"
os.environ["ORGTREE_WARM_POLL"] = "3600"   # keeper passes are manual here
os.environ["ORGTREE_PORT"] = "9"           # see test_codex_dispatch's note
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME           # hermetic ~/.claude.json etc.
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
with open(os.path.join(HOME, ".claude.json"), "w", encoding="utf-8") as _f:
    _f.write("{}")

FAKECODEX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fakecodex.py")
CODEX_HOME = tempfile.mkdtemp(prefix="codexprewarm-codexhome-")
os.environ["ORGTREE_CODEX"] = FAKECODEX
os.environ["CODEX_HOME"] = CODEX_HOME
with open(os.path.join(CODEX_HOME, "auth.json"), "w", encoding="utf-8") as _f:
    _f.write('{"tokens": {}}')

import _no_deploy                                                # noqa: E402
from orgtree import appsettings, store, supervisor, warmpool        # noqa: E402
from orgtree.ledger import USER                                     # noqa: E402

_no_deploy.assert_isolated_data_root()
assert store.DATA_ROOT == DATA, (
    f"store.DATA_ROOT ({store.DATA_ROOT}) does not match test DATA ({DATA}). "
    f"store was imported before ORGTREE_DATA was set!"
)

PASS = 0
FAIL: list[tuple[str, str]] = []
STREAMED: list[dict] = []
supervisor.stream = lambda slug, nid, payload: STREAMED.append(dict(payload))


def check(label, fn):
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


def wait_for(cond, timeout=10.0, why=""):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {why or 'condition'}")


def mkorg(label: str, probe: str | None) -> tuple[str, str]:
    if probe is not None:
        os.environ["FAKECODEX_WIREPROBE"] = probe
    org = store.create_org(f"zz codexprewarm {label}")
    r = org.hire(USER, None, "sol", 2, "cx", add_dirs=[],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": False, "mcp": []},
                 org_visibility="team", charter="a codex prewarm test agent")
    nid = r["node"]
    store.save_org(org)
    return org.d["slug"], nid


def probe_rows(probe: str) -> list[dict]:
    if not os.path.exists(probe):
        return []
    out = []
    for ln in open(probe, encoding="utf-8"):
        try:
            out.append(json.loads(ln))
        except ValueError:
            pass
    return out


def probe_methods(probe: str, pid: int | None = None) -> list[str]:
    rows = probe_rows(probe)
    if pid is not None:
        rows = [r for r in rows if r.get("pid") == pid]
    return [str(r.get("method")) for r in rows if "method" in r]


def journal_rows(kind: str, slug: str, nid: str,
                 event: str | None = None) -> list[dict]:
    p = os.path.join(DATA, "journals", "warm.jsonl")
    rows = []
    if os.path.exists(p):
        for ln in open(p, encoding="utf-8"):
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if r.get("kind") == kind and r.get("slug") == slug \
                    and r.get("nid") == nid and (
                    event is None or r.get("event") == event):
                rows.append(r)
    return rows


def bench_done(slug: str, nid: str) -> None:
    """Retire a finished bench org from the shared keeper's world: kill its
    pool entry AND durably exclude the seat, so later manual passes (some
    running under planted fault modes) cannot respawn it and cross-pollute."""
    warmpool.kill_org(slug, "suite-teardown")
    warmpool.set_node_excluded(slug, nid, True)


def readiness_states(nid_events: list[dict]) -> list[str]:
    return [str(p.get("state")) for p in nid_events
            if p.get("kind") == "mcp_readiness"]


def st_of(slug, nid):
    return supervisor.state(slug, nid)


def run_turn(slug, nid, text):
    st = st_of(slug, nid)
    with supervisor._state_lock:
        st["busy"] = True
    return supervisor._run_one_turn(slug, nid, {"text": text, "view": text})


def pooled(slug, nid):
    with warmpool._pool_lock:
        return warmpool._pool.get((slug, nid))


PROBE1 = os.path.join(DATA, "wire1.jsonl")
SLUG = NID = ""


def prewarm_full_readiness_before_any_prompt():
    global SLUG, NID
    os.environ["FAKECODEX_SCENARIO"] = "tool"
    os.environ["FAKECODEX_INIT_MODE"] = "answer"
    SLUG, NID = mkorg("base", PROBE1)
    STREAMED.clear()
    warmpool.keeper_pass_now()
    wp = pooled(SLUG, NID)
    assert isinstance(wp, warmpool.CodexWarmProc), "no prewarmed app-server"
    wait_for(lambda: bool(st_of(SLUG, NID).get("proc_warm")),
             why="prewarm finisher to mark warm")
    eq(wp.warm_state, "ready", "pool-side warm state")
    methods = probe_methods(PROBE1, wp.proc.pid)
    assert "initialize" in methods and "initialized" in methods, methods
    assert "mcpServerStatus/list" in methods, methods
    for banned in ("thread/start", "thread/resume", "turn/start",
                   "turn/steer", "thread/fork"):
        assert banned not in methods, f"{banned} sent during prewarm"
    eq(methods.count("initialize"), 1, "initialize once during prewarm")
    st = st_of(SLUG, NID)
    eq(st.get("mcp_tool_count"), 2, "planted inventory count")
    eq(st.get("mcp_tool_names"),
       {"mcp__fakesrv__toolA", "mcp__fakesrv__toolB"},
       "planted inventory names")
    assert "initializing" in readiness_states(STREAMED), \
        [s for s in readiness_states(STREAMED)]
    ready_rows = journal_rows("proc", SLUG, NID, "prewarm-ready")
    assert ready_rows and ready_rows[-1].get("elapsed_ms") >= 0, ready_rows
    eq(st.get("turns_run", 0), 0, "prewarm starts no turn")


check("prewarm initializes, plants MCP inventory, sends no thread/turn traffic",
      prewarm_full_readiness_before_any_prompt)


def first_prompt_retains_pid_single_initialize():
    wp = pooled(SLUG, NID)
    pid0 = wp.proc.pid
    run_turn(SLUG, NID, "hello prewarmed codex")
    st = st_of(SLUG, NID)
    eq(st["last_error"], None, "turn error")
    eq(st["turns_run"], 1, "turn counted")
    wait_for(lambda: warmpool.is_warm(SLUG, NID), why="park back")
    after = pooled(SLUG, NID)
    eq(after.proc.pid, pid0, "PID across the first prompt")
    assert after is wp, "the very same process object parks back"
    eq(after.warm_state, "ready", "warm state survives the turn")
    methods = probe_methods(PROBE1, pid0)
    eq(methods.count("initialize"), 1, "initialize stays once per process")
    first_list = methods.index("mcpServerStatus/list")
    assert methods.index("thread/start") > first_list, \
        "inventory must be planted before the first prompt"
    assert methods.index("turn/start") > methods.index("initialize")
    adm = journal_rows("admit", SLUG, NID)
    assert adm and adm[-1]["served"] == "warm" \
        and adm[-1]["reason"] == "warm-hit", adm[-1:]


check("first prompt claims the exact prewarmed PID; initialize never repeats",
      first_prompt_retains_pid_single_initialize)


def model_inventory_handshake_isolated_from_lane_process():
    # Pin the regression discovered on 2026-09-04:
    # Available tools assembly queries Codex model inventory (providers.codex_model_inventory).
    # On a cold cache, this spawns an ephemeral app-server helper that writes its own
    # handshake (initialize + initialized + model/list) to the wire probe.
    # That helper handshake must be attributed to a distinct PID and must NOT count
    # against the lane process's single-initialize invariant.
    rows = probe_rows(PROBE1)
    pids = {r.get("pid") for r in rows if r.get("pid") is not None}
    assert len(pids) >= 2, f"expected at least 2 distinct PIDs in probe, got {pids}"
    wp = pooled(SLUG, NID)
    pid0 = wp.proc.pid
    assert pid0 in pids, f"lane PID {pid0} not found in probe PIDs {pids}"

    helper_pids = pids - {pid0}
    assert helper_pids, "expected distinct helper PID for model inventory query"
    helper_pid = next(iter(helper_pids))
    helper_methods = [str(r.get("method")) for r in rows if r.get("pid") == helper_pid]
    lane_methods = [str(r.get("method")) for r in rows if r.get("pid") == pid0]

    assert "initialize" in helper_methods, helper_methods
    assert "model/list" in helper_methods, helper_methods
    for banned in ("thread/start", "thread/resume", "turn/start"):
        assert banned not in helper_methods, f"helper process sent {banned}"

    eq(lane_methods.count("initialize"), 1, "lane process initialize count")
    eq(helper_methods.count("initialize"), 1, "helper process initialize count")


check("helper inventory process handshake is isolated and does not count against lane process",
      model_inventory_handshake_isolated_from_lane_process)


def ws_lifecycle_ready_transition():
    appsettings.set_wait_for_mcp_tools_enabled(True)
    try:
        probe = os.path.join(DATA, "wire2.jsonl")
        slug, nid = mkorg("ready", probe)
        org = store.load_org(slug)
        fp = supervisor._mcp_infrastructure_fingerprint(org, nid)
        assert fp, "fingerprint must be observable in this rig"
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.node(nid)["last_turn_mcp_fingerprint"] = fp
            o.node(nid)["last_turn_mcp_tools"] = [
                "mcp__fakesrv__toolA", "mcp__fakesrv__toolB"]
            store.save_org(o)
        STREAMED.clear()
        warmpool.keeper_pass_now()
        wait_for(lambda: bool(st_of(slug, nid).get("proc_warm")),
                 why="ready prewarm")
        states = readiness_states([p for p in STREAMED])
        assert "initializing" in states and "ready" in states, states
        assert states.index("initializing") < states.index("ready"), states
        eq(pooled(slug, nid).warm_state, "ready", "warm state")
        assert journal_rows("proc", slug, nid, "prewarm-ready"), "ready row missing"
        bench_done(slug, nid)
    finally:
        appsettings.set_wait_for_mcp_tools_enabled(False)


check("WS lifecycle transitions initializing → ready under the readiness gate",
      ws_lifecycle_ready_transition)


def ws_lifecycle_degraded_transition():
    appsettings.set_wait_for_mcp_tools_enabled(True)
    old_timeout = supervisor.MCP_READINESS_TIMEOUT_S
    supervisor.MCP_READINESS_TIMEOUT_S = 0.3
    try:
        probe = os.path.join(DATA, "wire3.jsonl")
        slug, nid = mkorg("degraded", probe)
        org = store.load_org(slug)
        fp = supervisor._mcp_infrastructure_fingerprint(org, nid)
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.node(nid)["last_turn_mcp_fingerprint"] = fp
            o.node(nid)["last_turn_mcp_tools"] = ["mcp__nope__missing"]
            store.save_org(o)
        STREAMED.clear()
        warmpool.keeper_pass_now()
        wait_for(lambda: bool(st_of(slug, nid).get("proc_warm")),
                 why="degraded prewarm")
        states = readiness_states([p for p in STREAMED])
        assert "initializing" in states and "degraded" in states, states
        assert states.index("initializing") < states.index("degraded"), states
        eq(pooled(slug, nid).warm_state, "degraded", "warm state")
        rows = journal_rows("proc", slug, nid, "prewarm-degraded")
        assert rows and "timed out" in str(rows[-1].get("reason")), rows
        # degraded is still claimable and truthful, not a lie about readiness
        assert warmpool.is_warm(slug, nid)
        bench_done(slug, nid)
    finally:
        supervisor.MCP_READINESS_TIMEOUT_S = old_timeout
        appsettings.set_wait_for_mcp_tools_enabled(False)


check("WS lifecycle transitions initializing → degraded when readiness times out",
      ws_lifecycle_degraded_transition)


def mute_handshake_reaped_and_cold_fallback():
    os.environ["FAKECODEX_INIT_MODE"] = "mute"
    old_bound = warmpool.CODEX_PREWARM_INIT_TIMEOUT_S
    warmpool.CODEX_PREWARM_INIT_TIMEOUT_S = 0.5
    try:
        probe = os.path.join(DATA, "wire4.jsonl")
        slug, nid = mkorg("mute", probe)
        warmpool.keeper_pass_now()
        wp = pooled(slug, nid)
        assert isinstance(wp, warmpool.CodexWarmProc), "spawned"
        assert not st_of(slug, nid).get("proc_warm"), \
            "a mute handshake must never be called warm"
        wait_for(lambda: pooled(slug, nid) is None, why="mute reap")
        wait_for(lambda: wp.proc.poll() is not None, why="child killed")
        rows = journal_rows("proc", slug, nid, "exit")
        assert rows and rows[-1]["reason"] == "prewarm-failed" \
            and rows[-1]["reason_class"] == "prewarm-abort", rows[-1:]
        assert journal_rows("proc", slug, nid, "prewarm-failed"), "failure row"
        eq(bool(st_of(slug, nid).get("proc_warm")), False, "proc_warm")

        # the seat falls back to a COLD turn that still completes…
        os.environ["FAKECODEX_INIT_MODE"] = "answer"
        run_turn(slug, nid, "cold fallback prompt")
        st = st_of(slug, nid)
        eq(st["last_error"], None, "cold fallback turn error")
        adm = journal_rows("admit", slug, nid)
        assert adm and adm[-1]["served"] == "cold", adm[-1:]
        # …and the keeper retries the prewarm afterward
        warmpool.keeper_pass_now()
        wait_for(lambda: bool(st_of(slug, nid).get("proc_warm")),
                 why="re-warm after failure")
        eq(pooled(slug, nid).warm_state, "ready", "recovered warm state")
        bench_done(slug, nid)
    finally:
        warmpool.CODEX_PREWARM_INIT_TIMEOUT_S = old_bound
        os.environ["FAKECODEX_INIT_MODE"] = "answer"


check("a mute handshake is reaped with a classified exit; cold turns and re-warm survive",
      mute_handshake_reaped_and_cold_fallback)


def dying_handshake_reaped():
    os.environ["FAKECODEX_INIT_MODE"] = "die"
    try:
        probe = os.path.join(DATA, "wire5.jsonl")
        slug, nid = mkorg("die", probe)
        warmpool.keeper_pass_now()
        wait_for(lambda: pooled(slug, nid) is None, why="die reap")
        rows = journal_rows("proc", slug, nid, "exit")
        assert rows and rows[-1]["reason"] in ("prewarm-failed", "crash"),             rows[-1:]
        assert rows[-1]["reason_class"] in ("prewarm-abort",
                                            "observed-death"), rows[-1:]
        wait_for(lambda: bool(journal_rows("proc", slug, nid, "prewarm-failed")),
                 why="the finisher's own prewarm-failed attribution row")
        eq(bool(st_of(slug, nid).get("proc_warm")), False, "proc_warm")
        bench_done(slug, nid)
    finally:
        os.environ["FAKECODEX_INIT_MODE"] = "answer"


check("a handshake that kills the server is reaped the same classified way",
      dying_handshake_reaped)


def claim_during_initialize_keeps_pid():
    # claim the process while its finisher is still mid-handshake: the turn
    # must reuse the exact PID and the finisher must stand down silently
    os.environ["FAKECODEX_INIT_MODE"] = "answer"
    probe = os.path.join(DATA, "wire6.jsonl")
    slug, nid = mkorg("race", probe)
    real_finish = warmpool._codex_prewarm_finish
    hold = threading.Event()

    def slow_finish(org, fnid, wp):
        hold.wait(5)                     # park first, initialize later
        real_finish(org, fnid, wp)

    warmpool._codex_prewarm_finish = slow_finish
    try:
        warmpool.keeper_pass_now()
        wp = pooled(slug, nid)
        assert isinstance(wp, warmpool.CodexWarmProc)
        pid0 = wp.proc.pid
        assert not st_of(slug, nid).get("proc_warm"), "not yet warm"
        done: list = []
        th = threading.Thread(
            target=lambda: done.append(run_turn(slug, nid, "race prompt")),
            daemon=True)
        th.start()
        time.sleep(0.3)                  # let the claim land mid-"handshake"
        hold.set()
        th.join(15)
        assert not th.is_alive(), "racing turn hung"
        st = st_of(slug, nid)
        eq(st["last_error"], None, "racing turn error")
        wait_for(lambda: warmpool.is_warm(slug, nid), why="race park back")
        eq(pooled(slug, nid).proc.pid, pid0, "PID across the racing claim")
        adm = journal_rows("admit", slug, nid)
        assert adm and adm[-1]["served"] == "warm", adm[-1:]
        bench_done(slug, nid)
    finally:
        warmpool._codex_prewarm_finish = real_finish
        hold.set()


check("a claim racing the prewarm handshake still reuses the exact PID safely",
      claim_during_initialize_keeps_pid)


print()
if FAIL:
    for label, tb in FAIL:
        print(f"FAILED: {label}\n{tb}")
    print(f"{len(FAIL)} FAILED, {PASS} PASSED")
    raise SystemExit(1)
print(f"ALL {PASS} CHECKS PASS")
raise SystemExit(0)
