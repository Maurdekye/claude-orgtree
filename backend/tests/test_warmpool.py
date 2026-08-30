"""D-201 warm process pool — hermetic suite (new file per the landing rules).

Run: python tests/test_warmpool.py

Covers, in order:
  A. WarmProc delivery-gate probes (process-cache-2's reproducer, kept as the
     regression test): a line emitted after attach but BEFORE the turn's
     stdin write must never reach the turn; a line queued under a prior
     claim must never reach the next claim; the parked-period init IS
     replayed; parked-period noise is dropped and counted.
  B. Identity hash: charter edit moves it; the --session-id → --resume flip
     after the first transcript does NOT; a cheap-compact-shaped session swap
     DOES (the sid itself is hashed).
  C. Kill switch: warm.flag "0" reaps and forces cold service that still
     completes (the fallback IS today's behaviour); per-node exclude works;
     flag removal re-enables.
  D. End-to-end on the real `_run_turn` against fakecli.js:
     · boot pre-warm parks a process before any turn
     · a turn is SERVED warm (admit journal says so) and parks back
     · ⚠ THE PARKED PROCESS SURVIVES PAST TURN_IDLE (shrunk to 3 s here) and
       the next turn reuses the SAME PID — the watchdog is scoped to
       turns-in-flight, not to the process
     · a killed parked process degrades to a cold turn that completes
     · an idle identity change respawns the process immediately
     · a MID-TURN identity change stops the boundary feed: the queued second
       message runs as its own fresh turn instead of being fed to the stale
       process (control arm: without the change, it IS fed in-process)

MUTANT RUNS (value replacements, executed locally against this suite and
reverted — the checks below must be able to say BROKEN):
  M1 dog-outlives-turn: `dog_stop.set()` in _run_one_turn's inner finally
     replaced with `pass` — the still-armed watchdog reaps the parked
     process at TURN_IDLE and check "parked survives past TURN_IDLE" FAILS.
  M2 constant hash: `warmpool.ident_hash` forced to return "x" — the
     idle-respawn and mid-turn-dirty checks FAIL (stale process kept/fed).
Results are recorded in the D-201 breadcrumbs and the landing report.
"""
import io
import json
import os
import sys
import tempfile
import time

if not getattr(sys, "_utf8_wrapped", False):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    sys._utf8_wrapped = True

RIG = tempfile.mkdtemp(prefix="d201-warm-")
HOME = os.path.join(RIG, "home")
os.makedirs(HOME, exist_ok=True)
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKECLI = os.path.join(BACKEND, "tests", "fakecli.js")
CFG = os.path.join(RIG, "fakecli.json")

os.environ["ORGTREE_DATA"] = RIG
os.environ["HOME"] = HOME
os.environ["USERPROFILE"] = HOME
os.environ["ORGTREE_CLAUDE_CLI"] = FAKECLI
os.environ["FAKECLI_CONFIG"] = CFG
os.environ["ORGTREE_TURN_IDLE"] = "3"          # shrunk so the suite can wait it out
os.environ["ORGTREE_WARM"] = "1"
os.environ["ORGTREE_WARM_POLL"] = "3600"       # keeper passes are MANUAL here
os.environ.pop("ORGTREE_STEER_HOOK", None)
sys.path.insert(0, BACKEND)

with open(os.path.join(RIG, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address": "http://127.0.0.1:9"}')
with open(CFG, "w", encoding="utf-8") as f:
    # 'slowboy' gives the mid-turn tests a wide result window; everyone else
    # answers fast. Config is read at process LAUNCH, so the slow timing must
    # be in place before the warm spawn, not before the turn.
    json.dump({"default": {"echoMs": 40, "firstEventMs": 60, "resultMs": 30},
               "slowboy": {"echoMs": 40, "firstEventMs": 60,
                           "resultMs": 2500},
               "crashboy": {"echoMs": 40, "firstEventMs": 60,
                            "resultMs": 1200, "crashAtMs": 400}}, f)

from orgtree import store, supervisor as S, warmpool as W   # noqa: E402
from orgtree.ledger import USER                             # noqa: E402

W._FLAG_TTL = 0.5          # shrink the flag mtime-cache so mid-turn flips
FLAG = os.path.join(RIG, "warm.flag")   # land inside a 2.5 s result window

PASS = FAIL = 0


def check(label, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:3d}  {label}")
    except Exception as e:                                   # noqa: BLE001
        FAIL += 1
        import traceback
        print(f"  FAIL     {label}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=4)


# ── A. WarmProc delivery gate ──────────────────────────────────────────────
class FakeProc:
    """Just enough Popen face for WarmProc: pumpable stdout/stderr, poll()."""

    def __init__(self):
        r, w = os.pipe()
        self.stdout = os.fdopen(r, "r", encoding="utf-8")
        self._w = os.fdopen(w, "w", encoding="utf-8")
        r2, w2 = os.pipe()
        self.stderr = os.fdopen(r2, "r", encoding="utf-8")
        self._w2 = w2
        self.stdin = io.StringIO()
        self.pid = -1
        self._rc = None

    def emit(self, obj):
        self._w.write(json.dumps(obj) + "\n")
        self._w.flush()

    def die(self, rc=0):
        self._rc = rc
        self._w.close()
        os.close(self._w2)

    def poll(self):
        return self._rc


def wait_for(pred, secs=5.0, why="condition"):
    t0 = time.time()
    while time.time() - t0 < secs:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {why}")


def drain_available(wp, secs=0.6):
    """Lines the current claim can see within `secs` (non-blocking probe)."""
    got, t0 = [], time.time()
    while time.time() - t0 < secs:
        try:
            line = wp.lines.get(timeout=0.05)
        except Exception:                                    # noqa: BLE001
            continue
        if line is None:
            wp.lines.put(None)
            break
        got.append(json.loads(line))
    return got


def a_gate_blocks_pre_write_events():
    fp = FakeProc()
    wp = W.WarmProc("rig", "gate", fp, "sid0", "h0", "env0")
    # parked: an init arrives and is stored, an assistant line is dropped
    fp.emit({"type": "system", "subtype": "init", "tools": []})
    fp.emit({"type": "assistant", "message": {"content": []}, "mark": "parked"})
    wait_for(lambda: wp.dropped_inactive >= 1, why="parked drop")
    wp.attach()
    # claimed but stdin NOT yet written: a straggler arrives — must not be
    # delivered (this is process-cache-2's arm (a), previously BROKEN)
    fp.emit({"type": "result", "mark": "pre-write-straggler"})
    wait_for(lambda: wp.dropped_inactive >= 2, why="pre-write drop")
    wp.activate()                        # the turn wrote stdin — gate opens
    fp.emit({"type": "assistant", "mark": "genuine"})
    got = drain_available(wp)
    kinds = [(g.get("type"), g.get("mark")) for g in got]
    assert ("system", None) == (got[0].get("type"), got[0].get("mark")), \
        f"first delivered line must be the replayed init, got {kinds}"
    assert not any(g.get("mark") in ("parked", "pre-write-straggler")
                   for g in got), f"gated line delivered: {kinds}"
    assert any(g.get("mark") == "genuine" for g in got), \
        f"genuine post-write line missing: {kinds}"
    fp.die()


def a_prior_claim_leftovers_do_not_replay():
    fp = FakeProc()
    wp = W.WarmProc("rig", "gate2", fp, "sid0", "h0", "env0")
    wp.attach()
    wp.activate()
    fp.emit({"type": "result", "mark": "old-turn-straggler"})
    wait_for(lambda: not wp.lines.empty(), why="straggler queued")
    # turn 1 detaches WITHOUT consuming it (park closes the gate first)
    with wp._lk:
        wp.active = False
        wp.claimed = False
    wp.attach()                          # turn 2 (this is arm (b))
    wp.activate()
    fp.emit({"type": "assistant", "mark": "turn2"})
    got = drain_available(wp)
    assert not any(g.get("mark") == "old-turn-straggler" for g in got), \
        f"prior claim's queued event replayed into the next claim: {got}"
    assert any(g.get("mark") == "turn2" for g in got)
    fp.die()


def a_dead_before_claim_is_instant_eof():
    fp = FakeProc()
    wp = W.WarmProc("rig", "gate3", fp, "sid0", "h0", "env0")
    fp.die()
    wait_for(lambda: wp.dead.is_set(), why="pump EOF")
    wp.attach()
    it = wp.lines_iter()
    assert next(it, "EOF") == "EOF", "dead process must EOF the iterator"


check("A1 · pre-write straggler never delivered; init replayed; parked noise dropped",
      a_gate_blocks_pre_write_events)
check("A2 · prior claim's leftovers never replay into the next claim",
      a_prior_claim_leftovers_do_not_replay)
check("A3 · a process dead before the claim EOFs the iterator at once",
      a_dead_before_claim_is_instant_eof)


# ── rig org for B/C/D ──────────────────────────────────────────────────────
org = store.create_org("d201 warm rig")
SLUG = org.d["slug"]
for name in ("fastboy", "slowboy", "crashboy"):
    org.hire(USER, None, "haiku", 5, name, add_dirs=[],
             tools={"bash": True, "web": False, "edit": False,
                    "subagents": False, "mcp": []},
             org_visibility="team", charter="D-201 warm rig agent")
store.save_org(org)
NID = "fastboy"


def reload_org():
    return store.load_org(SLUG)


def admit_lines():
    p = os.path.join(RIG, "journals", "warm.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for ln in open(p, encoding="utf-8"):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("kind") == "admit":
            out.append(r)
    return out


def pooled(nid=NID):
    with W._pool_lock:
        return W._pool.get((SLUG, nid))


# ── B. identity hash ───────────────────────────────────────────────────────
def b_hash_moves_on_charter_and_not_on_session_flag():
    o = reload_org()
    h1 = W.ident_hash(o, NID)
    assert h1 == W.ident_hash(o, NID), "hash must be deterministic"
    # the --session-id → --resume flip: mint the transcript the predicate reads
    sid = o.node(NID)["session_id"]
    cwd = S.scratch_dir(SLUG, NID)
    proj = os.path.join(HOME, ".claude", "projects", S._cli_project_dir(cwd))
    os.makedirs(proj, exist_ok=True)
    tp = os.path.join(proj, sid + ".jsonl")
    with open(tp, "w", encoding="utf-8") as f:
        f.write("{}\n")
    argv = S._build_cmd(o, NID, write_ident=False)
    assert "--resume" in argv, "transcript mint should flip the session flag"
    h2 = W.ident_hash(o, NID)
    assert h2 == h1, "the session-flag flip must NOT dirty the hash"
    os.remove(tp)
    # a charter edit is an identity change and must dirty it
    with store.DOC_LOCK:
        o2 = reload_org()
        o2.node(NID)["charter"] = "changed charter — must dirty the hash"
        store.save_org(o2)
    h3 = W.ident_hash(reload_org(), NID)
    assert h3 != h1, "charter edit did not move the hash"
    # a session swap (cheap-compact shape) must dirty it too
    with store.DOC_LOCK:
        o3 = reload_org()
        o3.node(NID)["session_id"] = "00000000-feed-4bad-8000-000000000001"
        store.save_org(o3)
    h4 = W.ident_hash(reload_org(), NID)
    assert h4 != h3, "session swap did not move the hash"
    with store.DOC_LOCK:                 # restore
        o4 = reload_org()
        o4.node(NID)["session_id"] = sid
        store.save_org(o4)


check("B1 · hash: charter and session dirty it; the --resume flip does not",
      b_hash_moves_on_charter_and_not_on_session_flag)


# ── D. end-to-end (before C so the kill-switch test can reap something) ────
def d_boot_prewarm_parks_without_a_turn():
    W.keeper_pass_now()
    wait_for(lambda: pooled() is not None and pooled().alive(),
             why="pre-warm spawn")
    assert W.is_warm(SLUG, NID)
    time.sleep(0.8)                      # a parked process must not turn
    assert not admit_lines(), "pre-warm must not admit a turn"
    assert pooled().alive()


def d_turn_served_warm_and_parks_back():
    pid0 = pooled().proc.pid
    S._run_turn(SLUG, NID, "hello one")
    adm = admit_lines()
    assert adm and adm[-1]["nid"] == NID and adm[-1]["served"] == "warm" \
        and adm[-1]["reason"] == "warm-hit", f"admit says {adm[-1:]}"
    assert adm[-1]["session_id"], "session_id is cache-misses' join key"
    wait_for(lambda: W.is_warm(SLUG, NID), why="park back")
    assert pooled().proc.pid == pid0, "park must keep the SAME process"


def d_parked_survives_past_turn_idle_and_is_reused():
    pid0 = pooled().proc.pid
    time.sleep(S.TURN_IDLE + 2)          # the watchdog's whole window + slack
    assert pooled() is not None and pooled().alive(), \
        "PARKED PROCESS WAS REAPED — TURN_IDLE is scoped to the process, " \
        "not to turns-in-flight"
    n_before = len(admit_lines())
    S._run_turn(SLUG, NID, "hello two")
    adm = admit_lines()
    assert len(adm) == n_before + 1 and adm[-1]["served"] == "warm", \
        f"turn after the idle window was not served warm: {adm[-1:]}"
    wait_for(lambda: W.is_warm(SLUG, NID), why="re-park")
    assert pooled().proc.pid == pid0, "idle window must not change the process"


def d_killed_parked_process_degrades_to_cold():
    wp = pooled()
    wp.proc.kill()
    wait_for(lambda: wp.dead.is_set(), why="kill lands")
    n_before = len(admit_lines())
    S._run_turn(SLUG, NID, "hello three")     # must complete anyway
    adm = admit_lines()
    assert len(adm) == n_before + 1 and adm[-1]["served"] == "cold", \
        f"dead pool entry did not degrade to cold: {adm[-1:]}"
    assert adm[-1]["reason"] in ("no-process", "crashed"), adm[-1]
    st = S.state(SLUG, NID)
    assert st["last_error"] is None, f"fallback turn failed: {st['last_error']}"
    W.keeper_pass_now()                  # and the seat re-warms after
    wait_for(lambda: W.is_warm(SLUG, NID), why="re-warm after crash")


def d_idle_identity_change_respawns_immediately():
    pid0 = pooled().proc.pid
    with store.DOC_LOCK:
        o = reload_org()
        o.node(NID)["charter"] = "rescoped while idle — respawn me"
        store.save_org(o)
    W.keeper_pass_now()                  # the save-hook poke, made synchronous
    wait_for(lambda: W.is_warm(SLUG, NID) and pooled().proc.pid != pid0,
             why="idle respawn on identity change")
    n_before = len(admit_lines())
    S._run_turn(SLUG, NID, "hello four")
    adm = admit_lines()
    assert adm[-1]["served"] == "warm", "respawned process should serve warm"
    assert len(adm) == n_before + 1


def _two_messages(nid, dirty_mid_turn=False, flag_off_mid_turn=False):
    """Drive msg1 (slow result), queue msg2 while it runs, optionally dirty
    the prompt / flip the kill switch in the window. Returns this run's
    admit lines for `nid`."""
    n_before = len([a for a in admit_lines() if a["nid"] == nid])
    W.keeper_pass_now()
    wait_for(lambda: W.is_warm(SLUG, nid), why="pre-warm slow node")
    r = S.send_message(SLUG, nid, "slow message one")
    assert r["accepted"]
    st = S.state(SLUG, nid)
    wait_for(lambda: st["busy"], why="turn one starts")
    time.sleep(0.4)                      # inside msg1's 2.5 s result window
    S.send_message(SLUG, nid, "queued message two")
    if dirty_mid_turn:
        with store.DOC_LOCK:
            o = reload_org()
            o.node(nid)["charter"] = f"dirtied mid-turn at {time.time()}"
            store.save_org(o)
    if flag_off_mid_turn:
        with open(FLAG, "w", encoding="utf-8") as f:
            f.write("0")
    wait_for(lambda: not st["busy"] and not st["queue"], secs=25,
             why="both messages done")
    return [a for a in admit_lines() if a["nid"] == nid][n_before:]


def d_boundary_feed_control_arm():
    adm = _two_messages("slowboy")
    assert len(adm) == 2, (
        f"control arm: msg1 admission + msg2 boundary-feed row expected, "
        f"got {len(adm)}: {adm}")
    assert adm[0]["reason"] == "warm-hit"
    assert (adm[1]["served"], adm[1]["reason"]) == ("warm", "boundary-feed"), (
        f"in-process boundary feed must be journaled as such: {adm[1]}")


def d_boundary_feed_declines_dirtied_process():
    adm = _two_messages("slowboy", dirty_mid_turn=True)
    assert len(adm) == 2, f"expected two admissions, got {len(adm)}: {adm}"
    assert adm[1]["reason"] != "boundary-feed", (
        f"msg2 was fed to a process whose prompt had changed — it must run "
        f"as its own fresh turn: {adm}")
    assert adm[1]["served"] == "cold", (
        f"the fresh turn after a mid-turn dirty must not reuse the stale "
        f"process: {adm[1]}")


def d_flag_off_mid_turn_stops_the_boundary_feed():
    adm = _two_messages("slowboy", flag_off_mid_turn=True)
    os.remove(FLAG)
    time.sleep(W._FLAG_TTL + 0.3)
    assert len(adm) == 2, f"expected two admissions, got {len(adm)}: {adm}"
    assert adm[1]["reason"] != "boundary-feed" \
        and adm[1]["served"] == "cold" \
        and adm[1]["warm_enabled"] is False, (
        f"kill switch flipped OFF mid-turn must stop the very next queued "
        f"message from riding the warm process (clean A/B off-arm, working "
        f"back-out lever): {adm[1]}")


check("D1 · boot pre-warm parks a process, spends nothing",
      d_boot_prewarm_parks_without_a_turn)
check("D2 · a turn is served warm and parks the same process back",
      d_turn_served_warm_and_parks_back)
check("D3 · ⚠ parked process SURVIVES past TURN_IDLE and is reused",
      d_parked_survives_past_turn_idle_and_is_reused)
check("D4 · a dead pool entry degrades to a completing cold turn",
      d_killed_parked_process_degrades_to_cold)
check("D5 · an idle identity change respawns immediately",
      d_idle_identity_change_respawns_immediately)
check("D6 · control: clean boundary feeds in-process, journaled as boundary-feed",
      d_boundary_feed_control_arm)
check("D7 · a mid-turn identity change stops the boundary feed",
      d_boundary_feed_declines_dirtied_process)
check("D8 · kill switch OFF mid-turn stops the very next boundary feed",
      d_flag_off_mid_turn_stops_the_boundary_feed)


# ── E. startup ordering pin ────────────────────────────────────────────────
def e_warm_pool_starts_before_every_turn_driver():
    """Source-order pin (the observable form of the user's 'BEFORE a turn
    begins' ruling): in api._wire_notify, start_warm_pool() must precede
    every driver that can admit a turn at boot. Fails against the pre-fix
    ordering, where auto-resume started first."""
    src = open(os.path.join(BACKEND, "orgtree", "api.py"),
               encoding="utf-8").read()
    warm = src.index("warmpool.start_warm_pool()")
    for driver in ("supervisor.start_auto_resume_loop()",
                   "supervisor.start_usage_warm_loop()",
                   "supervisor.start_watchdog_engine()",
                   "supervisor.start_prime_restart_engine()",
                   "supervisor.reconcile("):
        assert warm < src.index(driver), \
            f"start_warm_pool() must run before {driver}"


check("E1 · start_warm_pool precedes every boot turn driver",
      e_warm_pool_starts_before_every_turn_driver)


def e_rename_noop_kills_nothing():
    """The death list is CLOSED: a refused or no-op rename changes neither
    prompt nor argv, so it must not kill a warm process (process-cache-2's
    rename probe). A real rename must — the parked cwd blocks the move —
    so the recorder CALLS THROUGH (a swallowed kill would wedge the move)."""
    killed = []
    real_kill = W.kill_node
    W.kill_node = lambda s, n, r: (killed.append((s, n, r)),
                                   real_kill(s, n, r))[1]
    try:
        W.keeper_pass_now()
        wait_for(lambda: W.is_warm(SLUG, "fastboy"), why="warm before rename")
        S.rename_node(SLUG, "fastboy", "fastboy", USER)     # no-op
        assert not killed, f"no-op rename killed a warm process: {killed}"
        S.rename_node(SLUG, "fastboy", "quickboy", USER)    # real rename
        assert killed and killed[0][2] == "renamed", \
            f"real rename must tear down the parked process: {killed}"
    finally:
        W.kill_node = real_kill
        S.rename_node(SLUG, "quickboy", "fastboy", USER)


check("E2 · rename: no-op kills nothing, a real rename tears down",
      e_rename_noop_kills_nothing)


def e_closed_death_list_table():
    """THE GLOBAL INVARIANT (coordinator ruling): across the operations an
    agent undergoes, its warm process died IF AND ONLY IF one of the
    legitimate causes held — retirement, a real prompt/identity change, or
    the kill switch (exercised in C/D8). Expectations are HARDCODED per row,
    not derived from the hash, so a hash that over-fires (killing on
    org-state noise) fails here just like a teardown that under-fires."""
    nid = NID

    def op_realloc():
        try:
            with store.DOC_LOCK:
                o = reload_org()
                o.reallocate(USER, nid, 1)
                store.save_org(o)
        except Exception:                                    # noqa: BLE001
            pass                     # a refused reallocation is a no-op too

    def op_visibility():
        with store.DOC_LOCK:
            o = reload_org()
            o.node(nid)["scope"]["org_visibility"] = "subtree"
            store.save_org(o)

    def op_charter():
        with store.DOC_LOCK:
            o = reload_org()
            o.node(nid)["charter"] = f"table charter {time.time()}"
            store.save_org(o)

    def op_same_model():
        with store.DOC_LOCK:
            o = reload_org()
            o.node(nid)["model"] = o.node(nid)["model"]
            store.save_org(o)

    def op_audience():
        with store.DOC_LOCK:
            o = reload_org()
            o.d["audiences"].append({"grantee": nid, "grantor": USER,
                                     "granted_at": time.time(),
                                     "reason": "table test"})
            store.save_org(o)

    rows = [
        ("no-op rename", lambda: S.rename_node(SLUG, nid, nid, USER), False),
        ("credit reallocation (org-state only)", op_realloc, False),
        ("org_visibility team→subtree (org-state only)", op_visibility, False),
        ("same-value model write (no-op)", op_same_model, False),
        ("charter edit (identity)", op_charter, True),
        # a USER-audience grant on a TOP-LEVEL agent is genuinely inert for
        # the prompt (_claudemd_caveat returns "" when parent is None) — the
        # first run of this table expected death here and the harness
        # correctly refused, which is the harness working. The
        # identity-moving audience case is the CHILD row below.
        ("USER audience grant on a top-level seat (inert)", op_audience,
         False),
    ]
    for label, op, expect_died in rows:
        W.keeper_pass_now()
        wait_for(lambda: W.is_warm(SLUG, nid), why=f"warm before {label}")
        wp0 = pooled(nid)
        pid0 = wp0.proc.pid
        op()
        W.keeper_pass_now()          # the save-hook poke, made synchronous
        cur = pooled(nid)
        died = (not wp0.alive()) or cur is None or cur.proc.pid != pid0
        assert died == expect_died, (
            f"{label}: process {'died' if died else 'survived'}, expected "
            f"{'death' if expect_died else 'survival'} — a warm process ends "
            f"ONLY on retirement, a prompt change, or shutdown")
    # a CHILD seat: the same audience grant IS an identity change one level
    # down (the CLAUDE.md-caveat paragraph flips to "you hold a USER
    # AUDIENCE"), so the process must die there
    with store.DOC_LOCK:
        o = reload_org()
        o.hire(nid, nid, "haiku", 1, "childboy", add_dirs=[],
               tools={"bash": True, "web": False, "edit": False,
                      "subagents": False, "mcp": []},
               org_visibility="team", charter="table child seat")
        store.save_org(o)
    W.keeper_pass_now()
    wait_for(lambda: W.is_warm(SLUG, "childboy"), why="warm the child seat")
    wp0 = pooled("childboy")
    with store.DOC_LOCK:
        o = reload_org()
        o.d["audiences"].append({"grantee": "childboy", "grantor": USER,
                                 "granted_at": time.time(),
                                 "reason": "table test"})
        store.save_org(o)
    W.keeper_pass_now()
    cur = pooled("childboy")
    assert (not wp0.alive()) or cur is None \
        or cur.proc.pid != wp0.proc.pid, (
        "USER audience grant on a CHILD seat is an identity change and must "
        "respawn its process")
    # retirement, on a disposable seat so the suite keeps its fixtures
    with store.DOC_LOCK:
        o = reload_org()
        o.hire(USER, None, "haiku", 1, "mortal", add_dirs=[],
               tools={"bash": True, "web": False, "edit": False,
                      "subagents": False, "mcp": []},
               org_visibility="team", charter="short-lived table seat")
        store.save_org(o)
    W.keeper_pass_now()
    wait_for(lambda: W.is_warm(SLUG, "mortal"), why="warm the mortal seat")
    wp0 = pooled("mortal")
    with store.DOC_LOCK:
        o = reload_org()
        o.retire(USER, "mortal")
        store.save_org(o)
    W.keeper_pass_now()
    wait_for(lambda: not wp0.alive(), why="retirement teardown")
    assert pooled("mortal") is None


def e_failed_spawn_leaves_no_child():
    """The other half of the invariant (coordinator: 'fails invisibly in
    both directions at once'): if spawn setup dies AFTER the child exists,
    the child is killed before the fallback — otherwise every keeper retry
    leaks a CLI+MCP tree while all correctness tests stay green."""
    kills = []

    class LeakProbeProc:
        pid = 999_999_999

        def __init__(self, *a, **k):
            self.stdin = self.stdout = self.stderr = io.StringIO()

        def kill(self):
            kills.append("kill")

        def poll(self):
            return None

    real_popen, real_leash = W._POPEN, S._leash
    W._POPEN = LeakProbeProc
    S._leash = lambda p: (_ for _ in ()).throw(RuntimeError("planted"))
    try:
        got = W._spawn_for(reload_org(), NID, "leak-probe")
        assert got is None, "planted setup failure must fall back to None"
        assert kills, (
            "spawn setup failed after the child existed and the child "
            "received no kill — this leaks a process tree per keeper retry")
    finally:
        W._POPEN, S._leash = real_popen, real_leash


check("E3 · closed-death-list table: died ⟺ retirement or identity change",
      e_closed_death_list_table)
check("E4 · a failed spawn setup kills the child it started",
      e_failed_spawn_leaves_no_child)


# ── F. cache-death sequence: the warm process dies at each stage ───────────
def transcript_hits(nid, marker):
    """How many USER records across ALL rig transcripts carry `marker` — the
    double-execution witness: a message may reach a process AT MOST once.
    Walks every project dir rather than computing the one path: fakecli's
    dir-name slugging differs from `_cli_project_dir` (single-dash vs
    double-dash escaping), and the marker is unique per test anyway."""
    del nid
    n = 0
    for dp, _dn, fns in os.walk(os.path.join(HOME, ".claude", "projects")):
        for fn in fns:
            if not fn.endswith(".jsonl"):
                continue
            for ln in open(os.path.join(dp, fn), encoding="utf-8"):
                if marker in ln and '"user"' in ln:
                    n += 1
    return n


def f_death_between_claim_and_write_falls_back_cold():
    """process-cache-2's claim-death reproducer (stage 1 — the one window
    D-201 itself created): the warm process dies after claim() said alive
    and before the first stdin write. Required: the turn completes EXACTLY
    ONCE on a fresh cold process, journaled so the A/B sees the miss.
    Pre-fix this was mail loss: OSError 22, last_error set, no answer."""
    W.keeper_pass_now()
    wait_for(lambda: W.is_warm(SLUG, NID), why="warm before claim-death")
    marker = f"F1-CLAIM-DEATH-{int(time.time() * 1000)}"
    real_claim = W.claim

    def killing_claim(s, n, h):
        wp, r = real_claim(s, n, h)
        if wp is not None:
            wp.proc.kill()
            wp.proc.wait()
        return wp, r

    W.claim = killing_claim
    n_before = len(admit_lines())
    try:
        S._run_turn(SLUG, NID, marker)
    finally:
        W.claim = real_claim
    st = S.state(SLUG, NID)
    assert st["last_error"] is None, (
        f"a dead warm process must be indistinguishable from never having "
        f"had one; turn failed with: {st['last_error']}")
    adm = admit_lines()[n_before:]
    assert [a["reason"] for a in adm] == ["warm-hit", "claim-died"], (
        f"expected the warm-hit then the journaled cold fallback: "
        f"{[(a['served'], a['reason']) for a in adm]}")
    assert adm[-1]["served"] == "cold"
    assert transcript_hits(NID, marker) == 1, (
        f"the retried message must reach a process EXACTLY once, got "
        f"{transcript_hits(NID, marker)}")


def f_death_after_consumption_is_never_retried_by_the_fallback():
    """The load-bearing condition of the stage-1 retry, witnessed from the
    other side (coordinator requirement): a message the process CONSUMED
    must NOT be re-sent by the fallback — a silently duplicated turn is
    worse than a lost one. crashboy's CLI dies mid-turn AFTER echoing the
    message (crashAtMs=400 > echoMs=40): whatever today's died-in-flight
    machinery does with that is its pre-existing business, but the
    claim-died fallback must not fire and the message must not reach a
    process a second time through it."""
    W.keeper_pass_now()
    wait_for(lambda: W.is_warm(SLUG, "crashboy"), why="warm crashboy")
    marker = f"F2-CONSUMED-{int(time.time() * 1000)}"
    n_before = len(admit_lines())
    S._run_turn(SLUG, "crashboy", marker)
    adm = [a for a in admit_lines()[n_before:] if a["nid"] == "crashboy"]
    assert not any(a["reason"] == "claim-died" for a in adm), (
        f"the initial-write fallback fired on a CONSUMED message — "
        f"double-execution hazard: {adm}")
    assert adm and adm[0]["reason"] == "warm-hit"
    assert transcript_hits("crashboy", marker) <= 1, (
        "a consumed message reached a process twice")


check("F1 · death between claim and write → exactly-once cold fallback",
      f_death_between_claim_and_write_falls_back_cold)
check("F2 · death after consumption never triggers the write fallback",
      f_death_after_consumption_is_never_retried_by_the_fallback)


# ── C. kill switch ─────────────────────────────────────────────────────────
def c_flag_off_reaps_and_serves_cold():
    W.keeper_pass_now()
    wait_for(lambda: W.is_warm(SLUG, NID), why="warm before the flag test")
    with open(FLAG, "w", encoding="utf-8") as f:
        f.write("0")
    time.sleep(W._FLAG_TTL + 0.3)        # let the mtime cache expire
    assert not W.warm_enabled()
    W.keeper_pass_now()
    assert not W.is_warm(SLUG, NID), "flag off must reap parked processes"
    n_before = len(admit_lines())
    S._run_turn(SLUG, NID, "cold arm turn")
    adm = admit_lines()
    assert len(adm) == n_before + 1 and adm[-1]["served"] == "cold"
    assert adm[-1]["warm_enabled"] is False, \
        "every admit line must carry the flag state (A/B analysability)"
    assert S.state(SLUG, NID)["last_error"] is None


def c_flag_exclude_is_per_node():
    with open(FLAG, "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "exclude": [f"{SLUG}/{NID}"]}, f)
    time.sleep(W._FLAG_TTL + 0.3)
    assert W.warm_enabled()
    assert W.node_excluded(SLUG, NID)
    assert not W.eligible(reload_org(), NID)[0]
    assert W.eligible(reload_org(), "slowboy")[0]
    os.remove(FLAG)
    time.sleep(W._FLAG_TTL + 0.3)
    W.keeper_pass_now()
    wait_for(lambda: W.is_warm(SLUG, NID), why="re-warm after flag removal")


check("C1 · warm.flag 0 reaps the pool; cold arm completes and is journaled",
      c_flag_off_reaps_and_serves_cold)
check("C2 · per-node exclude works and lifts cleanly",
      c_flag_exclude_is_per_node)


def c_malformed_flag_labels_unknown_never_guessed():
    """cache-misses' A/B contract: a malformed flag file (empty = torn
    write, or truncated JSON) may fall back to the env for BEHAVIOUR, but
    the admit LABEL must be null — an arm that gets guessed is silent
    misattribution in the measurement. Also pins the atomic writer seam."""
    for content in ("", '{"enab'):
        with open(FLAG, "w", encoding="utf-8") as f:
            f.write(content)
        time.sleep(W._FLAG_TTL + 0.3)
        on, label = W.warm_decision()
        assert on is True, "behaviour must fall to the env (ORGTREE_WARM=1)"
        assert label is None, (
            f"malformed flag {content!r} must label the arm UNKNOWN, "
            f"got {label!r}")
        n_before = len(admit_lines())
        S._run_turn(SLUG, NID, f"malformed flag arm {content!r}")
        adm = admit_lines()[n_before:]
        assert adm and all(a["warm_enabled"] is None for a in adm), (
            f"admit rows under a malformed flag must carry null "
            f"warm_enabled: {[(a['reason'], a['warm_enabled']) for a in adm]}")
    W.set_flag("1")                      # the atomic writer seam
    time.sleep(W._FLAG_TTL + 0.3)
    assert W.warm_decision() == (True, True)
    os.remove(FLAG)
    time.sleep(W._FLAG_TTL + 0.3)


def c_snapshot_counts_serving_processes():
    """The ceiling witness (process-cache-2's two-stub probe, coordinator-
    gated): a snapshot taken while a process is CLAIMED must count it —
    parked-only counting reported half the real memory, understating the
    one number the user asked for in the reassuring direction."""
    W.keeper_pass_now()
    wait_for(lambda: W.is_warm(SLUG, NID), why="warm before snapshot test")
    h = W.ident_hash(reload_org(), NID)
    wp, r = W.claim(SLUG, NID, h)
    assert wp is not None and r == "warm-hit"
    try:
        W._pool_snapshot()
        pools = [json.loads(ln) for ln in
                 open(os.path.join(RIG, "journals", "warm.jsonl"),
                      encoding="utf-8") if '"pool"' in ln]
        snap = pools[-1]
        assert snap["serving"] >= 1, f"serving process invisible: {snap}"
        assert snap["warm_count"] == snap["parked"] + snap["serving"], snap
    finally:
        W.park_back(wp, 0.0, 0)
    W._pool_snapshot()
    pools = [json.loads(ln) for ln in
             open(os.path.join(RIG, "journals", "warm.jsonl"),
                  encoding="utf-8") if '"pool"' in ln]
    assert pools[-1]["serving"] == 0, f"park did not release serving: {pools[-1]}"


check("C3 · malformed flag: behaviour falls to env, label is null (unknown arm)",
      c_malformed_flag_labels_unknown_never_guessed)
check("C4 · pool snapshot counts serving processes (the ceiling witness)",
      c_snapshot_counts_serving_processes)


# ── teardown ───────────────────────────────────────────────────────────────
with W._pool_lock:
    _left = list(W._pool.values())
for _wp in _left:
    W.discard(_wp, "suite-teardown")
try:
    store.delete_org(SLUG)
except Exception:                                            # noqa: BLE001
    pass

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
