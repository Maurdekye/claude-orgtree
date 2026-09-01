"""D-215: idle admin desks may stop and restart one parked CLI process.

Run directly::

    python backend/tests/test_process_control.py

The process is a small fake rather than a real CLI child. The checks exercise
the durable flag writer, keeper exclusion, start eligibility, the state/pool
reservation, queued-mail handoff surface, generation-safe kill, audit rows,
and the admin/public API boundary without spending a provider turn.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import types

from fastapi import HTTPException
from starlette.requests import Request

RIG = tempfile.mkdtemp(prefix="d215-process-control-")
os.environ["ORGTREE_DATA"] = RIG
os.environ["HOME"] = os.path.join(RIG, "home")
os.environ["USERPROFILE"] = os.path.join(RIG, "home")
os.environ["ORGTREE_WARM"] = "1"
os.makedirs(os.environ["HOME"], exist_ok=True)
with open(os.path.join(RIG, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address": "http://127.0.0.1:9"}')

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from orgtree import api, store, supervisor as S, warmpool as W  # noqa: E402
from orgtree.ledger import USER                               # noqa: E402


class FakeProc:
    def __init__(self, pid: int):
        self.pid = pid
        self.dead = False

    def poll(self) -> int | None:
        return 0 if self.dead else None


def fake_process(slug: str, nid: str, pid: int):
    proc = FakeProc(pid)
    # WarmProcess and CodexWarmProc share the fields used by the control and
    # teardown paths; this object intentionally avoids starting pump threads.
    return types.SimpleNamespace(
        slug=slug, nid=nid, proc=proc, sid=f"sid-{pid}", hash=f"hash-{pid}",
        ident_components={}, claimed=False, exit_journaled=True,
        exit_reason=None, identity_change=None, _lk=threading.Lock(),
        alive=lambda: proc.poll() is None,
    )


def request(scope_state: dict[str, object] | None = None) -> Request:
    return Request({
        "type": "http", "method": "POST", "path": "/api/test",
        "raw_path": b"/api/test", "query_string": b"", "headers": [],
        "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 7360),
        "scheme": "http", "state": scope_state or {},
    })


SLUG = ""
NID = "agent"
FLAG = ""

try:
    W.set_flag("1")
    W._FLAG_CACHE["at"] = 0.0
    org = store.create_org("D215 control rig")
    SLUG = org.d["slug"]
    FLAG = os.path.join(RIG, "warm.flag")
    org.hire(USER, None, "haiku", 0, NID, add_dirs=[],
             tools={"bash": False, "web": False, "edit": False,
                    "subagents": False, "mcp": []},
             org_visibility="team", charter="D-215 control test")
    store.save_org(org)

    saved_kill = W._kill_proc
    saved_exit = W._journal_exit_once
    saved_spawn = W._spawn_for
    saved_poke = W.poke
    with W._pool_lock:
        saved_pool = dict(W._pool)
        saved_serving = dict(W._serving)
        W._pool.clear()
        W._serving.clear()
    with S._state_lock:
        saved_state = dict(S._state)
        S._state.clear()

    kills: list[int] = []

    def fake_kill(wp):
        kills.append(int(wp.proc.pid))
        wp.proc.dead = True

    W._kill_proc = fake_kill
    # The process-control journal is the subject of this test; suppress the
    # normal process-exit backstop because the fake has no stdout pump.
    W._journal_exit_once = lambda _wp, _reason=None: None

    def put_parked(pid: int):
        wp = fake_process(SLUG, NID, pid)
        with W._pool_lock:
            W._pool[(SLUG, NID)] = wp
        st = S.state(SLUG, NID)
        with S._state_lock:
            st.update(proc_live=True, proc_warm=True,
                      proc_relaunch=False, proc_relaunch_reason=None)
        return wp

    def clear_runtime():
        with W._pool_lock:
            W._pool.clear()
            W._serving.clear()
        with S._state_lock:
            S._state.clear()

    def assert_refused(fn, text: str):
        try:
            fn()
        except W.ProcessControlRefused as e:
            assert text in str(e), (text, e)
            return
        raise AssertionError("process control unexpectedly succeeded")

    # A parked, fully idle process exposes STOP and the accepted operation
    # persists the exclusion before killing the exact generation.
    put_parked(101)
    status = W.process_control_status(store.load_org(SLUG), NID)
    assert status["action"] == "stop" and status["enabled"] is True, status
    result = W.process_control(SLUG, NID, "stop")
    assert result["ok"] and result["paused"] and result["killed"]
    assert kills == [101], kills
    W._FLAG_CACHE["at"] = 0.0
    assert W.node_excluded(SLUG, NID), "manual stop was not durable"
    with open(FLAG, encoding="utf-8") as f:
        durable = json.load(f)
    assert durable["enabled"] is True
    assert durable["exclude"] == [f"{SLUG}/{NID}"], durable

    # A keeper pass after a simulated backend restart still excludes the seat.
    spawned: list[tuple[str, str]] = []
    W._spawn_for = lambda _org, nid, _why: spawned.append((SLUG, nid))
    W.keeper_pass_now()
    assert spawned == [], spawned
    W._spawn_for = saved_spawn

    # START cannot override the machine-wide off arm and leaves the manual
    # exclusion in place, even though the node is otherwise idle.
    W.set_enabled(False)
    status = W.process_control_status(store.load_org(SLUG), NID)
    assert status["action"] == "start" and not status["enabled"]
    assert "disabled globally" in (status["reason"] or ""), status
    assert_refused(lambda: W.process_control(SLUG, NID, "start"),
                   "disabled globally")
    assert W.node_excluded(SLUG, NID)

    # Lifecycle eligibility is surfaced and enforced before START clears the
    # exclusion. A frozen seat is not made warm by a manual click.
    W.set_enabled(True)
    with store.DOC_LOCK:
        frozen = store.load_org(SLUG)
        frozen.node(NID)["frozen"] = {"kind": "connection"}
        store.save_org(frozen)
    status = W.process_control_status(store.load_org(SLUG), NID)
    assert not status["enabled"] and "frozen" in (status["reason"] or ""), status
    assert_refused(lambda: W.process_control(SLUG, NID, "start"), "frozen")
    assert W.node_excluded(SLUG, NID)
    with store.DOC_LOCK:
        unfrozen = store.load_org(SLUG)
        unfrozen.node(NID).pop("frozen", None)
        store.save_org(unfrozen)

    # START clears the durable exclusion and pokes the keeper for immediate
    # prewarm. The next keeper pass, rather than this request thread, owns the
    # provider process spawn.
    start_pokes: list[bool] = []
    W.poke = lambda: start_pokes.append(True)
    try:
        result = W.process_control(SLUG, NID, "start")
    finally:
        W.poke = saved_poke
    assert result["ok"] and not result["paused"]
    assert not W.node_excluded(SLUG, NID)
    assert start_pokes, "manual start did not request an immediate keeper pass"

    # A busy turn makes the stale UI request fail safely and does not mutate
    # the flag or kill the parked generation.
    put_parked(102)
    st = S.state(SLUG, NID)
    with S._state_lock:
        st["busy"] = True
    status = W.process_control_status(store.load_org(SLUG), NID)
    assert not status["enabled"] and "active turn" in (status["reason"] or ""), status
    before_kills = list(kills)
    assert_refused(lambda: W.process_control(SLUG, NID, "stop"), "active turn")
    assert kills == before_kills and not W.node_excluded(SLUG, NID)
    with S._state_lock:
        st["busy"] = False

    # Two concurrent STOP clicks are serialized: one accepted kill and one
    # idempotent already-stopped result, never two kills.
    clear_runtime()
    put_parked(103)
    outcomes: list[dict[str, object]] = []
    gate = threading.Barrier(2)

    def stop_racer():
        gate.wait()
        try:
            outcomes.append(W.process_control(SLUG, NID, "stop"))
        except Exception as e:                            # noqa: BLE001
            outcomes.append({"error": str(e)})

    threads = [threading.Thread(target=stop_racer) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(outcomes) == 2 and sum(not bool(x.get("already"))
                                      for x in outcomes) == 1, outcomes
    assert kills.count(103) == 1, kills

    # Mail arriving during the reservation is queued, never started against a
    # process being stopped/started; release code later hands it off.
    clear_runtime()
    st = S.state(SLUG, NID)
    marker = object()
    with S._state_lock:
        st["proc_control"] = marker
    queued = S.send_message(SLUG, NID, "queued while controlling", wake=True)
    assert queued.get("process_control") and queued.get("queued") == 1, queued
    with S._state_lock:
        assert st["queue"] and st["queue"][0] == "queued while controlling"
        st["queue"].clear()
        st.pop("proc_control", None)

    # The expected-generation guard refuses to kill a replacement that won the
    # pool race. The current process remains available for its next turn.
    old = fake_process(SLUG, NID, 201)
    current = fake_process(SLUG, NID, 202)
    with W._pool_lock:
        W._pool[(SLUG, NID)] = current
    assert W.kill_node(SLUG, NID, "excluded-by-flag", expected=old) is False
    with W._pool_lock:
        assert W._pool[(SLUG, NID)] is current
    assert kills.count(202) == 0

    # The route rejects public callers even when called directly, and tree
    # projection exposes backend-owned control fields without enabling a kiosk.
    public_req = request({"public_slug": SLUG})
    try:
        api.node_process(SLUG, NID, api.ProcessControl(action="start"), public_req)
    except HTTPException as e:
        assert e.status_code == 403
    else:
        raise AssertionError("public process control unexpectedly succeeded")
    denied = api._public_denied(
        "POST", f"/api/orgs/{SLUG}/nodes/{NID}/process", SLUG)
    assert denied and denied[0] == 403, denied
    tree = api.org_tree(SLUG, request())
    projected = tree["roots"][0]
    for field in ("proc_paused", "proc_control_enabled",
                  "proc_control_action", "proc_control_reason"):
        assert field in projected, (field, projected)
    assert projected["proc_control_enabled"] is True
    public_tree = api.org_tree(SLUG, public_req)
    public_projected = public_tree["roots"][0]
    assert public_projected["proc_control_enabled"] is False
    assert "admin" in (public_projected["proc_control_reason"] or "")

    # Audit rows include accepted and refused/idempotent operations while
    # leaving process identity details in the private warm journal only.
    journal = os.path.join(RIG, "journals", "warm.jsonl")
    with open(journal, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    controls = [row for row in rows if row.get("kind") == "control"]
    assert any(row.get("result") == "accepted" for row in controls), controls
    assert any(row.get("result") == "refused" for row in controls), controls
    assert all(row.get("slug") == SLUG and row.get("nid") == NID
               for row in controls)
    print(f"process control OK ({len(controls)} audit rows)")
finally:
    # This is a standalone hermetic script, but restoring module globals keeps
    # it safe to import from a larger test runner too.
    try:
        W._kill_proc = saved_kill
        W._journal_exit_once = saved_exit
        W._spawn_for = saved_spawn
        W.poke = saved_poke
        with W._pool_lock:
            W._pool.clear()
            W._pool.update(saved_pool)
            W._serving.clear()
            W._serving.update(saved_serving)
        with S._state_lock:
            S._state.clear()
            S._state.update(saved_state)
    except (NameError, AttributeError):
        pass
    shutil.rmtree(RIG, ignore_errors=True)
