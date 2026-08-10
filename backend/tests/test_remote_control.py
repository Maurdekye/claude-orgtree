"""FR-01 remote control (b71a16d) — the park, and whether it actually parks.

`remote-control` hands the user's phone the agent's REAL session id. The
feature's entire safety argument is one sentence from its own header comment:

    "Two writers on one session id is the hazard, so while the server runs the
     node is PARKED."

So the suite asks one question in several ways — CAN A SECOND WRITER STILL GET
ON THE SESSION? Everything else (the refusals, the flag, reconcile) is checked
because it is the machinery that answer depends on.

    §1  the gates — who may start it, and what it refuses
    §2  the park — mail, commands, and the other spawn paths
    §3  the race — start is not atomic, and a turn can win the window
    §4  the exit — release, reconcile, and what happens to a leashed server
        whose node stops existing

The CLI stand-in logs every invocation with its session id and its mode, so
"two writers on one session" is a MEASUREMENT (two live rows, same sid), not an
inference from the code.

Hermetic: throwaway ORGTREE_DATA + HOME, no port, no Docker, no real CLI, no
network. Spawns `node` — skipped with a note if absent.

    python backend/tests/test_remote_control.py [-v]
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-remotectl-")
_HOME = os.path.join(_TMP, "home")
_CLI = os.path.join(_TMP, "rccli.js")
_LOG = os.path.join(_TMP, "invocations.log")
os.makedirs(_HOME, exist_ok=True)
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")

# ⚠ a throwaway ORGTREE_DATA does NOT isolate the MAIL HUB: net._default_address
# falls back to net.DEFAULT_HUB_ADDRESS — the operator's real hub — when this
# root has no defaults.json, and any rig that starts the net daemon then
# registers its fixture orgs there permanently. Measured twice (user report
# 2026-08-06; ~45 fixture orgs again on 2026-08-10). The discard port refuses
# instantly, so registration fails harmlessly into the backoff.
# Guarded over this whole directory by test_external_mail §1.
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

os.environ["USERPROFILE"] = _HOME
os.environ["HOME"] = _HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"
os.environ["ORGTREE_CLAUDE_CLI"] = _CLI      # read at import time
os.environ["RCCLI_LOG"] = _LOG

from orgtree import store, supervisor                            # noqa: E402
from orgtree.ledger import USER                                  # noqa: E402

_REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", ".."))

PASS = 0
FAIL: list[tuple[str, str]] = []
GAPS: list[tuple[str, str, str]] = []
NOTES: list[str] = []
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


def gap(label, why, fn) -> None:
    """SHOULD hold, currently does not — inverted so the suite stays green and
    turns RED the day it is fixed."""
    global PASS
    try:
        fn()
    except AssertionError as e:
        GAPS.append((label, why, str(e).split("\n")[0][:300]))
        print(f"  ⚑ GAP    {label}")
        return
    except Exception:                                            # noqa: BLE001
        FAIL.append((label + " (gap check errored)", traceback.format_exc()))
        print(f"  FAIL     {label} — the gap check itself broke")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}  ← FIXED: promote this out of gap()")


def note(msg: str) -> None:
    NOTES.append(msg)
    print(f"       · {msg}")


# ══════════════════════════════════════════════════════════════ the CLI stand-in
#
# One script, two personalities, decided by argv exactly as the real binary
# decides: `remote-control` is a long-lived server that never exits; anything
# else is an ordinary streaming turn. Both log their invocation with the
# session id they were handed, which is what makes "two writers, one session"
# measurable rather than arguable.

RC_JS = r"""
'use strict'
const fs = require('fs'), os = require('os'), path = require('path')
const argv = process.argv.slice(2)
if (argv.includes('--version')) { console.log('9.9.9 (rccli)'); process.exit(0) }
function arg(n) { const i = argv.indexOf(n); return i >= 0 && i + 1 < argv.length ? argv[i + 1] : null }
const remote = argv.includes('remote-control')
const sid = arg('--session-id') || arg('--resume') || 'no-session'
function log(ev) {
  try {
    fs.appendFileSync(process.env.RCCLI_LOG, JSON.stringify(
      Object.assign({ at: Date.now(), pid: process.pid, sid: sid,
                      mode: remote ? 'remote' : 'turn' }, ev)) + '\n')
  } catch (e) {}
}
log({ ev: 'launch' })
if (remote) {
  // the remote-control server: stays up until it is killed, exactly like the
  // real one (the start path probes `poll() is None` after 2.5 s)
  process.on('exit', () => log({ ev: 'exit' }))
  setInterval(() => {}, 1 << 30)
  return
}

const home = process.env.USERPROFILE || process.env.HOME || os.homedir()
const projDir = path.join(home, '.claude', 'projects',
  process.cwd().replace(/[\\/:]+/g, '-').replace(/^-+/, ''))
fs.mkdirSync(projDir, { recursive: true })
const tpath = path.join(projDir, sid + '.jsonl')
function record(rec) {
  if (!rec.timestamp) rec.timestamp = new Date().toISOString()
  const fd = fs.openSync(tpath, 'a')
  fs.writeSync(fd, JSON.stringify(rec) + '\n'); fs.fsyncSync(fd); fs.closeSync(fd)
}
function say(o) { process.stdout.write(JSON.stringify(o) + '\n') }
say({ type: 'system', subtype: 'init', model: 'fake', permissionMode: 'acceptEdits',
      cwd: process.cwd(), tools: [], mcp_servers: [] })
let buf = ''
process.stdin.setEncoding('utf8')
process.stdin.on('data', (d) => {
  buf += d
  let i
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1)
    if (!line) continue
    let ev; try { ev = JSON.parse(line) } catch (e) { continue }
    if (ev.type !== 'user') continue
    const c = ev.message && ev.message.content
    const text = typeof c === 'string' ? c : (c || []).map((b) => b.text || '').join('')
    log({ ev: 'served', text: text.slice(-120) })
    record({ type: 'user', message: { role: 'user', content: text } })
    const msg = { role: 'assistant', model: 'fake',
                  content: [{ type: 'text', text: 'ack.' }],
                  usage: { input_tokens: 1000 } }
    say({ type: 'assistant', message: msg })
    record({ type: 'assistant', message: msg })
    say({ type: 'result', subtype: 'success', is_error: false, result: 'ok',
          usage: { input_tokens: 1000 }, total_cost_usd: 0.0001 })
  }
})
process.stdin.on('end', () => process.exit(0))
"""

with open(_CLI, "w", encoding="utf-8") as _f:
    _f.write(RC_JS)


def invocations() -> list[dict]:
    try:
        return [json.loads(x) for x in
                open(_LOG, encoding="utf-8").read().splitlines() if x.strip()]
    except OSError:
        return []


def clear_log() -> None:
    open(_LOG, "w", encoding="utf-8").close()


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        try:
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return str(pid) in (r.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def live_pids() -> set[int]:
    """Stand-in processes that logged a launch and are STILL RUNNING.

    Was launch-minus-exit from the invocation log — but the reap kills with
    TerminateProcess on Windows, which runs NO exit handler, so a correctly
    killed server never logged its exit and the fix was invisible to the
    instrument (implementer measurement note, 2026-08-05). Real pid liveness
    is the honest measure; exited turn personalities are equally excluded."""
    started = {r["pid"] for r in invocations() if r.get("ev") == "launch"}
    return {pid for pid in started if _pid_alive(pid)}


_n = [0]


def probe_org(sandboxed: bool = False) -> tuple[str, str]:
    _n[0] += 1
    org = store.create_org(f"zz remotectl {_n[0]}")
    r = org.hire(USER, None, "haiku", 20, "probe",
                 add_dirs=[], tools={"bash": False, "web": False, "edit": False,
                                     "subagents": False, "mcp": []},
                 org_visibility="team", charter="remote-control probe")
    if sandboxed:
        org.d["sandbox"] = {"enabled": True, "secret": "s3cret"}
    store.save_org(org)
    return org.d["slug"], r["node"]


def node(slug: str, nid: str) -> dict:
    return store.load_org(slug).nodes[nid]


def stop_all(slug: str, nid: str) -> None:
    try:
        supervisor.remote_control_stop(slug, nid)
    except Exception:                                            # noqa: BLE001
        pass


def wait_idle(slug: str, nid: str, secs: float = 20) -> None:
    end = time.time() + secs
    while time.time() < end and supervisor.state(slug, nid)["busy"]:
        time.sleep(0.1)


# ══════════════════════════════════════════════════════════════════════════ §1

def sec_gates() -> None:
    print("\n§1  the gates — who may start it")

    def _sandboxed_refused():
        slug, nid = probe_org(sandboxed=True)
        r = supervisor.remote_control_start(slug, nid)
        assert r.get("error") and "sandbox" in r["error"], r
        assert not node(slug, nid).get("remote_controlled"), "flagged anyway"
    check("gate · a sandboxed org is refused (its session files never hold the "
          "subscription token) and nothing is flagged", _sandboxed_refused)

    def _archived_refused():
        slug, nid = probe_org()
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.nodes[nid]["state"] = "archived"
            store.save_org(o)
        r = supervisor.remote_control_start(slug, nid)
        assert r.get("error") and "archived" in r["error"], r
    check("gate · an archived agent is refused", _archived_refused)

    def _missing_refused():
        slug, _ = probe_org()
        r = supervisor.remote_control_start(slug, "nope")
        assert r.get("error"), r
    check("gate · an unknown node is refused, not crashed", _missing_refused)

    def _no_process_on_refusal():
        assert not any(r.get("mode") == "remote" for r in invocations()), (
            f"a refused start still spawned a server: {invocations()}")
    check("gate · none of the refusals spawned a process", _no_process_on_refusal)


# ══════════════════════════════════════════════════════════════════════════ §2

def sec_park() -> None:
    print("\n§2  the park — mail, commands, and the other spawn paths")

    slug, nid = probe_org()
    clear_log()
    r = supervisor.remote_control_start(slug, nid)
    assert r.get("ok"), f"the rig needs a running server: {r}"

    def _flagged():
        rc = node(slug, nid).get("remote_controlled")
        assert rc and rc.get("pid"), rc
        assert any(x.get("mode") == "remote" for x in invocations()), invocations()
    check("park · the flag records the server, and the server is running",
          _flagged)

    def _mail_parks():
        before = len([x for x in invocations() if x.get("ev") == "served"])
        res = supervisor.send_message(slug, nid, "(orgtree) You have new mail above.")
        assert res.get("remote") and res.get("queued") == 0, res
        time.sleep(1.0)
        after = len([x for x in invocations() if x.get("ev") == "served"])
        assert after == before, "a turn ran while the session was remote-controlled"
    check("park · send_message refuses to drive — no second CLI touches the "
          "session", _mail_parks)

    def _command_is_not_swallowed():
        # a slash command is NOT mail: nothing else carries it, so a park that
        # merely returns 'accepted' loses it outright. The frozen path knows
        # this — api.py raises 409 "a session command would be dropped, not
        # queued" — and the remote path has no such guard.
        res = supervisor.send_message(slug, nid, "/context", command=True)
        assert not (res.get("accepted") and not res.get("queued")), (
            f"the command was accepted and dropped: {res}")
    # was a gap: a command was "accepted" and dropped (no mailbox behind
    # it). Fixed 2026-08-05: send_message refuses commands on a remote-
    # controlled node (accepted:False + error), and the /command endpoint
    # 409s beside its frozen clause.
    check("park · a session command is refused rather than silently "
          "swallowed", _command_is_not_swallowed)

    def _compact_is_gated():
        # manual_compact forks the session (--resume <sid> --fork-session) and
        # then REPLACES the node's session_id. Do that while a phone is
        # driving <sid> and the user is left holding an id the org no longer
        # uses.
        src = open(os.path.join(_REPO, "backend", "orgtree", "api.py"),
                   encoding="utf-8").read()
        i = src.find("a knowledge bearer never re-compacts")
        seg = src[i:i + 900]
        assert "remote_controlled" in seg, (
            "the compaction endpoint gates on frozen/busy/bearer but not on "
            "remote control")
    # was a gap: the fork rebinds the session id out from under the phone.
    # Fixed 2026-08-05 at THREE layers: the /command endpoint's remote 409
    # (also covers the /context one-shot), the /compact branch's own gate,
    # and supervisor.manual_compact itself.
    check("park · compaction is refused while the session is "
          "remote-controlled", _compact_is_gated)

    stop_all(slug, nid)


# ══════════════════════════════════════════════════════════════════════════ §3

def sec_race() -> None:
    print("\n§3  the race — start is not atomic")

    slug, nid = probe_org()
    clear_log()
    sid = node(slug, nid)["session_id"]

    started: list[dict] = []

    def _start():
        started.append(supervisor.remote_control_start(slug, nid))

    def _turn_cannot_win_the_start_window():
        t = threading.Thread(target=_start, daemon=True)
        t.start()
        # the start path spawns the server, then sleeps 2.5 s probing it, and
        # only writes `remote_controlled` afterwards. Mail arriving inside that
        # window sees an unflagged, unbusy node.
        time.sleep(1.0)
        supervisor.send_message(slug, nid, "mail during the start window")
        t.join(20)
        wait_idle(slug, nid)
        turns = [x for x in invocations() if x.get("ev") == "served"]
        assert not turns, (
            "a turn ran INSIDE the start window and was handed the same "
            f"session id the remote-control server is driving ({sid}) — two "
            "writers, which is the exact hazard the park exists to prevent")
    # was a gap: the flag was written AFTER the spawn + 2.5 s probe, so the
    # node looked idle and unflagged for the whole window (measured with a
    # 1 s delay). Fixed 2026-08-05: park first, prove second — the flag goes
    # into the doc before anything spawns, busy is re-checked after (roll
    # back + refuse on a race), and every failure path unparks.
    check("race · a turn cannot start inside the remote-control start "
          "window", _turn_cannot_win_the_start_window)

    def _no_two_writers_on_one_sid():
        live = [x for x in invocations() if x.get("ev") == "launch"
                and x.get("sid") == sid]
        modes = {x["mode"] for x in live}
        assert modes != {"remote", "turn"}, (
            f"both a remote-control server and a turn were launched on {sid}: "
            f"{live}")
    # was a gap — the consequence of the start window above, now the
    # standing invariant the feature's header comment promises
    check("race · one session id is never handed to two processes at once",
          _no_two_writers_on_one_sid)

    stop_all(slug, nid)


# ══════════════════════════════════════════════════════════════════════════ §4

def sec_exit() -> None:
    print("\n§4  the exit — release, reconcile, and the orphan")

    def _release_clears_and_nudges():
        slug, nid = probe_org()
        clear_log()
        assert supervisor.remote_control_start(slug, nid).get("ok")
        with store.DOC_LOCK:                       # mail waiting at release
            o = store.load_org(slug)
            o.post_mail(USER, nid, "while you were away")
            store.save_org(o)
        supervisor.remote_control_stop(slug, nid)
        assert not node(slug, nid).get("remote_controlled"), "flag survived"
        wait_idle(slug, nid, 25)
        served = [x for x in invocations() if x.get("ev") == "served"]
        assert served, "release did not drive the parked mail"
    check("exit · release clears the flag and drives the mail that waited",
          _release_clears_and_nudges)

    def _release_is_idempotent():
        slug, nid = probe_org()
        supervisor.remote_control_stop(slug, nid)      # never started
        r = supervisor.remote_control_stop(slug, nid)
        assert r.get("ok"), r
    check("exit · releasing a node that was never controlled is a no-op, not "
          "an error", _release_is_idempotent)

    def _reconcile_clears_stale():
        slug, nid = probe_org()
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.nodes[nid]["remote_controlled"] = {"at": "2026-01-01", "pid": 999999}
            store.save_org(o)
        supervisor.reconcile(slug)
        assert not node(slug, nid).get("remote_controlled"), (
            "a flag left by a dead backend parks the node forever")
    check("exit · reconcile clears a flag left behind by a dead backend",
          _reconcile_clears_stale)

    def _deleted_node_takes_its_server_with_it():
        slug, nid = probe_org()
        clear_log()
        assert supervisor.remote_control_start(slug, nid).get("ok")
        before = live_pids()
        assert before, "precondition: a server is running"
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.delete(USER, nid)
            store.save_org(o)
        time.sleep(0.5)
        assert not live_pids(), (
            "the node is gone and its remote-control server is still running "
            f"(pids {live_pids()}) — nothing will ever stop it: _remote_procs "
            "is keyed by (slug, nid) and only remote_control_stop pops it, "
            "reconcile only clears FLAGS on nodes that still exist, and the "
            "leash releases it only when the whole backend exits")
    # was a gap: delete removed the seat, `_remote_procs` kept the handle,
    # the server ran forever. Fixed 2026-08-05 via store.save_hooks — EVERY
    # doc save re-checks that running servers still have a live flagged seat
    # (so ledger-level delete/retire/rename with a plain save reap too, not
    # just the API paths, which also reap explicitly). NB the measurement
    # here needed pid liveness, not the exit log: the reap kills with
    # TerminateProcess, which runs no exit handler.
    check("exit · deleting a remote-controlled node stops its server",
          _deleted_node_takes_its_server_with_it)

    def _leash_is_real():
        src = open(os.path.join(_REPO, "backend", "orgtree", "supervisor.py"),
                   encoding="utf-8").read()
        i = src.find("def _leash(")
        seg = src[i:i + 700]
        assert "AssignProcessToJobObject" in seg and "_ORPHANS.add" in seg, seg
        assert "except Exception" in seg, seg
    check("exit · characterised: the leash is a Windows job object with a "
          "POSIX atexit fallback, and it swallows its own failures — so "
          "'leashed' is best-effort, which is exactly why reconcile clears "
          "stale flags on startup", _leash_is_real)
    note("if the leash silently fails (the except swallows it) a surviving "
         "server outlives the backend, and reconcile then clears the flag on "
         "startup — leaving a phone attached to a session orgtree believes is "
         "free. Cheap hardening: record the pid (already stored) and check it "
         "is gone before clearing the flag, or kill it by pid.")


# ═════════════════════════════════════════════════════════════════════════ main

def main() -> None:
    print("═══ FR-01 remote control — does the park actually park? ═══")
    if not shutil.which("node"):
        note("node is not on PATH — the whole suite needs the CLI stand-in")
    else:
        try:
            sec_gates()
            sec_park()
            sec_race()
            sec_exit()
        finally:
            supervisor._reap_orphans()      # nothing outlives the suite

    print(f"\n{'═' * 70}\n{PASS} checks passed, {len(FAIL)} failed, "
          f"{len(GAPS)} gaps")
    for label, tb in FAIL:
        print(f"\nFAIL  {label}\n{tb}")
    if GAPS:
        print("\n⚑ GAPS — measured, currently true, reported to the implementer:")
        for label, why, detail in GAPS:
            print(f"\n  ⚑ {label}\n    measured: {detail}\n    {why}")
    if NOTES:
        print("\nnotes:")
        for m in NOTES:
            print(f"  · {m}")
    try:
        shutil.rmtree(_TMP, ignore_errors=True)
    except OSError:
        pass
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
