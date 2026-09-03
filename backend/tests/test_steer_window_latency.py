"""MID-TURN MAIL INSIDE A LONG TOOL CALL: THE WAIT IS REAL AND THE SENDER IS TOLD.

    python backend/tests/test_steer_window_latency.py
      --keep     leave the rig (and its backend log) on disk

THE INCIDENT (live org `orgtree`, 2026-09-03, user-observed):

    12:56:54.420Z  coordinator sends an urgent PAUSE to `pan-hunt`
    12:57:04.823Z  coordinator sends the same PAUSE to `doc-gallery`
                   — both mid-turn, both opus, both its direct reports.
                   `orgtree_message` answered the same way for both.
    12:57:29.249Z  pan-hunt's steered_log row      (+35 s   — it acted at once)
    13:01:52.368Z  doc-gallery's steered_log row   (+4m 47s — the user saw the
                   mail sitting unread while the agent kept working, and
                   reported the message as NEVER DELIVERED)

Nothing was lost, and nothing was broken. doc-gallery's transcript
(d341050f-…jsonl) says exactly what happened, to the millisecond:

    12:57:04.361Z  tool_result — the last PostToolUse boundary before the send
    12:57:04.823Z  the PAUSE is posted (462 ms after that boundary closed)
    12:57:10.664Z  TOOL_USE Bash `python tools/run_tests.py …` (the full suite)
    13:01:53.265Z  its tool_result — and the hook fires, on the very next
                   boundary, delivering the mail

Mid-turn delivery rides `steer.py`, a PostToolUse hook: an agent INSIDE a
single tool call has no injection point until that call returns. That is the
ratified design (D-045/D-046) and it is not a bug. THE BUG IS THE SILENCE.
`orgtree_message` hands the carrier to the steer store and answers with
`{"delivered": …, "deferred": false}` — the same answer it gives for a message
that lands in 200 ms — so a coordinator who sends a stop order is told nothing
that would let it know the order will not be read for five minutes.

WHAT THIS FILE PINS

  §1  THE WAIT IS REAL, and it is the tool call's length: an agent inside a
      TOOL_MS tool call, messaged just after a boundary, receives the mail only
      when that call returns. Measured end to end against a real backend, the
      real steer hook, and a real HTTP door.
  §2  THE SENDER IS TOLD. `orgtree_message`'s own result names the carrier and
      the wait — and, when the recipient has been inside one tool call long
      enough that the next boundary is not in sight, names ⏸ orgtree_interrupt
      as the thing that DOES land now. Anti-vacuity: the same assertion is run
      against a recipient with a SHORT tool call, where the warning must be
      absent — so §2 cannot pass by always shouting.
  §3  THE LATE STEER ALARMS ITS SENDER. A carrier that outstays
      `STEER_LATE_AFTER` in the store gets one passive notice back to whoever
      sent it, naming how long it has waited. This is the half that would have
      reached the coordinator at 12:58Z instead of never.
  §4  BOUNDARY BOOKKEEPING: every steer poll is a heartbeat, so
      `steer_wait()` measures the CURRENT tool call and not the turn.
  §5  EXACTLY-ONCE ACROSS A RESTART. The backend is actually bounced with
      alarms already sent, and the restarted sweeper must not report the same
      carriers again — a false "may not have landed" is worse than silence,
      because it teaches senders to distrust a channel that mostly works.

The rig is `test_message_visibility_live.py`'s — a real uvicorn backend, its
own ORGTREE_DATA/HOME/port, and `fakecli.js`, whose `toolMs` makes the length
of a tool call a NUMBER instead of whatever the day happens to produce.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))

KEEP = "--keep" in sys.argv

#: how long the fake agent's tool calls take. Long enough that a multi-second
#: silent wait cannot be mistaken for scheduling noise, short enough to run.
TOOL_MS = 6000
#: the anti-vacuity twin: a recipient whose boundaries come round constantly
FAST_MS = 120

PASS = 0
FAIL: list[tuple[str, str]] = []


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


# ------------------------------------------------------------------ the rig

TMP = tempfile.mkdtemp(prefix="orgtree-steerlat-")
HOME = os.path.join(TMP, "home")
DATA = os.path.join(TMP, "data")
CFG = os.path.join(TMP, "fakecli.json")
LOG = os.path.join(TMP, "backend.log")
os.makedirs(HOME, exist_ok=True)
os.makedirs(DATA, exist_ok=True)

# the mail hub is NOT isolated by ORGTREE_DATA — point this rig's default hub
# at the discard port so no throwaway org ever registers on the user's real
# roster (the note in test_message_visibility_live.py, same reason)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    json.dump({"net_hub_address": "http://127.0.0.1:9"}, _f)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
PROC: subprocess.Popen | None = None


def set_cfg(**per_node) -> None:
    cfg = {"default": per_node.pop("default", {})}
    cfg.update(per_node)
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f)


def start_backend() -> None:
    global PROC
    env = dict(os.environ)
    env.update({
        "ORGTREE_DATA": DATA, "USERPROFILE": HOME, "HOME": HOME,
        "ORGTREE_PORT": str(PORT),
        "ORGTREE_CLAUDE_CLI": os.path.join(_HERE, "fakecli.js"),
        "FAKECLI_CONFIG": CFG,
        # the whole point: force the PostToolUse steer hook on, whatever CLI
        # version happens to be pinned on the machine running this
        "ORGTREE_STEER_HOOK": "1",
        "ORGTREE_MAX_TURNS": "24",
        "ORGTREE_TURN_TIMEOUT": "120",
        "ORGTREE_TURN_IDLE": "120",
        "ORGTREE_WARM": "0",
        "PYTHONPATH": os.path.join(_REPO, "backend"),
        "PYTHONIOENCODING": "utf-8",
        # never claim the real install's sandbox bridge listener
        "ORGTREE_BRIDGE_PORT": "0",
    })
    env.pop("ORGTREE_PUBLIC_PORT", None)
    env.pop("ORGTREE_EXPOSE_ADMIN", None)
    log = open(LOG, "a", encoding="utf-8")
    PROC = subprocess.Popen(
        [sys.executable, "-m", "orgtree.api"],
        cwd=os.path.join(_REPO, "backend"), env=env,
        stdout=log, stderr=log, text=True)
    for _ in range(300):
        if PROC.poll() is not None:
            raise RuntimeError(f"backend exited {PROC.returncode}\n{log_tail()}")
        try:
            urllib.request.urlopen(BASE + "/api/orgs", timeout=1).read()
            return
        except Exception:                                        # noqa: BLE001
            time.sleep(0.1)
    raise RuntimeError(f"backend never came up on {PORT}\n{log_tail()}")


def log_tail(n: int = 3000) -> str:
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()[-n:]
    except OSError:
        return "(no log)"


def stop_backend() -> None:
    global PROC
    if PROC is None:
        return
    try:
        PROC.terminate()
        PROC.wait(timeout=15)
    except Exception:                                            # noqa: BLE001
        PROC.kill()
    PROC = None


def api(method: str, path: str, body=None, timeout: float = 30):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw else {}


def hire(slug: str, name: str, parent) -> str:
    r = api("POST", f"/api/orgs/{slug}/ops",
            {"op": "hire", "actor": "@user", "parent": parent,
             "tier": "haiku", "grant": 2, "name": name,
             "charter": "a test agent",
             "tools": {"bash": True, "web": False, "edit": False,
                       "subagents": False, "mcp": []},
             "org_visibility": "team", "add_dirs": []})
    return r.get("node") or name


def doc(slug: str) -> dict:
    """The org document, read off disk in whichever format this rig's backend
    wrote it.

    SQLite EQUIVALENT (SQLITE-SPEC 10.2). This suite is deliberately BLACK
    BOX — it never imports orgtree, it only drives a backend subprocess — so
    the sqlite branch reads the database the same way the json branch reads
    the file: directly, assembling the sections the checks below look at
    (`notices` and `mail` are small `doc` blobs, `steered_log` is rows in
    `log_d`). Schema: SQLITE-SPEC 3.1. Opened `mode=ro` so this reader can
    never create or alter the backend's database."""
    jp = os.path.join(DATA, "orgs", f"{slug}.json")
    db = os.path.join(DATA, "orgs", f"{slug}.db")
    for _ in range(20):
        try:
            if os.path.exists(jp):
                with open(jp, encoding="utf-8") as f:
                    return json.load(f)
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
            try:
                out = {k: json.loads(v) for k, v in
                       c.execute("SELECT key, val FROM doc")}
                out["nodes"] = {i: json.loads(v) for i, v in
                                c.execute("SELECT id, val FROM nodes ORDER BY ord")}
                for sect, owner, val in c.execute(
                        "SELECT sect, owner, val FROM log_d ORDER BY seq"):
                    out.setdefault(sect, {}).setdefault(owner, []).append(
                        json.loads(val))
                for sect, val in c.execute(
                        "SELECT sect, val FROM log_l ORDER BY seq"):
                    out.setdefault(sect, []).append(json.loads(val))
                return out
            finally:
                c.close()
        except (OSError, ValueError, sqlite3.Error):
            time.sleep(0.05)
    raise AssertionError("org doc unreadable")


def steer_rows(slug: str, nid: str) -> list[dict]:
    return [r for r in (doc(slug).get("steered_log") or {}).get(nid, [])
            if not r.get("fold")]


def wait_for(pred, secs: float, why: str):
    t0 = time.time()
    while time.time() - t0 < secs:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(f"timed out after {secs}s waiting for {why}")


def agent_send(slug: str, sender: str, to: str, body: str) -> dict:
    """`orgtree_message`, through the door a real agent's MCP server uses."""
    return api("POST", "/api/agent",
               {"org": slug, "node": sender, "tool": "orgtree_message",
                "args": {"to": to, "body": body, "kind": "decision"}})


# ------------------------------------------------------------------ fixture

_orgs: list[str] = []


def make_org(label: str) -> tuple[str, str, str]:
    """(slug, boss, worker) — worker reports to boss, so boss may message it."""
    r = api("POST", "/api/orgs", {"name": f"zz steerlat {len(_orgs)} {label}"[:60]})
    slug = r.get("slug") or (r.get("org") or {}).get("slug")
    _orgs.append(slug)
    boss = hire(slug, "boss", None)
    worker = hire(slug, label, boss)
    return slug, boss, worker


def drive(slug: str, nid: str, text: str) -> dict:
    return api("POST", f"/api/orgs/{slug}/nodes/{nid}/message", {"text": text})


def responding(slug: str, nid: str) -> bool:
    try:
        t = api("GET", f"/api/orgs/{slug}/tree", timeout=10)
    except Exception:                                            # noqa: BLE001
        return False
    for n in t.get("nodes", t if isinstance(t, list) else []):
        if isinstance(n, dict) and n.get("id") == nid:
            return bool(n.get("responding") or n.get("busy"))
    return False


def start_long_turn(slug: str, nid: str) -> None:
    """Drive `nid` and return once it is provably INSIDE a tool call.

    The first steer poll of the turn is the proof: `steer_wait` only starts
    counting once a boundary has been seen, so waiting for it to become a
    number means the agent has finished tool #1 and entered tool #2 — the
    same place doc-gallery was at 12:57:10Z."""
    drive(slug, nid, "start working")
    wait_for(lambda: steer_state(slug, nid).get("polls", 0) >= 1, 40,
             f"{nid}'s first tool boundary")


def steer_state(slug: str, nid: str) -> dict:
    try:
        return api("GET", f"/api/orgs/{slug}/nodes/{nid}/steer-state", timeout=10)
    except Exception:                                            # noqa: BLE001
        return {}


def plant_identity() -> None:
    """The hire gate asks whether Claude is signed in ON THIS MACHINE and
    reads ~/.claude.json — which resolves against the rig's redirected HOME,
    where nothing is signed in. Plant an identity so a `haiku` hire is
    admitted; the CLI it would spawn is fakecli.js either way."""
    with open(os.path.join(HOME, ".claude.json"), "w", encoding="utf-8") as f:
        json.dump({"oauthAccount": {
            "accountUuid": "00000000-0000-0000-0000-000000000000",
            "emailAddress": "rig@example.invalid"}}, f)


# --------------------------------------------------------------------- §1

def s1_the_wait_is_real() -> None:
    slug, boss, worker = make_org("slowtool")
    set_cfg(default={"echoMs": 30, "firstEventMs": 60, "resultMs": 40,
                     "tools": 6, "toolMs": TOOL_MS})
    start_long_turn(slug, worker)
    before = len(steer_rows(slug, worker))

    t0 = time.time()
    res = agent_send(slug, boss, worker,
                     "**PAUSE NOW** — end your turn as soon as you can.")
    assert res.get("delivered") == worker, res
    rows = wait_for(
        lambda: (steer_rows(slug, worker)[before:] or None),
        TOOL_MS / 1000.0 * 3 + 20, "the steered row")
    waited = time.time() - t0

    # NOTHING WAS LOST — the mail did arrive, exactly once, at a boundary
    assert len(rows) == 1, rows
    assert "PAUSE NOW" in rows[0].get("text", ""), rows[0]
    # …and the wait was the TOOL CALL, not scheduling noise. Half of TOOL_MS
    # is the floor: the send lands at a uniformly random point inside a
    # TOOL_MS call, so the expected wait is TOOL_MS/2 and a slack floor of
    # TOOL_MS/3 keeps this from flaking while still being a wait no
    # message-passing layer could produce on its own.
    assert waited > TOOL_MS / 3000.0, (
        f"expected a multi-second wait inside a {TOOL_MS}ms tool call, "
        f"got {waited:.2f}s — the fixture never reached the seam")
    print(f"        · steer waited {waited:.2f}s inside a "
          f"{TOOL_MS / 1000:.0f}s tool call")
    S1["slug"], S1["boss"], S1["worker"] = slug, boss, worker
    S1["result"], S1["waited"] = res, waited


S1: dict = {}


# --------------------------------------------------------------------- §2

def s2_the_sender_is_told() -> None:
    """The result of the very send §1 made has to describe what happened."""
    res = S1["result"]
    d = str(res.get("delivery") or "")
    assert d, ("orgtree_message answered with no `delivery` at all — this is "
               f"the defect: {res}")
    low = d.lower()
    assert "not" in low and ("read" in low or "seen" in low or "deliver" in low), d
    assert "tool call" in low, d
    print(f"        · delivery: {d}")


def s2_short_call_is_not_alarmed() -> None:
    """ANTI-VACUITY. The same send into an agent whose boundaries come round
    every FAST_MS must NOT carry the long-tool-call warning, or §2 is just a
    constant string."""
    slug, boss, worker = make_org("fasttool")
    set_cfg(default={"echoMs": 30, "firstEventMs": 60, "resultMs": 40,
                     "tools": 200, "toolMs": FAST_MS})
    start_long_turn(slug, worker)
    res = agent_send(slug, boss, worker, "a routine note, no rush")
    d = str(res.get("delivery") or "")
    assert d, res
    assert "orgtree_interrupt" not in d, (
        f"a {FAST_MS}ms tool call must not raise the stuck-agent warning: {d}")
    # and it still says what carrier it is on
    assert "tool call" in d.lower(), d
    print(f"        · delivery: {d}")


def s2_long_call_names_the_interrupt() -> None:
    """The recipient of §1 has been inside one tool call for most of TOOL_MS
    by the time a SECOND message is sent — long enough that the next boundary
    is not in sight, which is when the sender is pointed at ⏸."""
    slug, boss, worker = S1["slug"], S1["boss"], S1["worker"]
    # sit inside one tool call until the wait crosses the alarm line
    wait_for(lambda: steer_state(slug, worker).get("wait", 0) >= LATE, 40,
             "the recipient to be deep inside one tool call")
    res = agent_send(slug, boss, worker, "second PAUSE, same reason")
    d = str(res.get("delivery") or "")
    assert "orgtree_interrupt" in d, (
        f"a recipient {LATE}s into one tool call must be flagged: {d}")
    print(f"        · delivery: {d}")


# --------------------------------------------------------------------- §3

def s3_the_late_steer_alarms_its_sender() -> None:
    """The half that would have reached the coordinator at 12:58Z.

    The alarm rides the org NOTICE box — passive by construction: it steers
    into a sender that is mid-turn, and waits for an idle one rather than
    waking it to report a delay."""
    slug, boss, worker = S1["slug"], S1["boss"], S1["worker"]

    def told():
        n = (doc(slug).get("notices") or {}).get(boss) or []
        hit = [e for e in n if "has NOT been read" in str(e.get("text") or "")]
        return hit or None

    late = wait_for(told, LATE + TOOL_MS / 1000.0 * 2 + 30,
                    "the late-steer notice to the sender")
    text = str(late[0].get("text") or "")
    assert worker in text, text
    assert "orgtree_interrupt" in text, text
    print(f"        · sender was told: {text[:150]}")
    # ONE alarm per waiting carrier, however long the tool call runs. §1 and
    # §2's second send are the two carriers this org ever steered late.
    assert len(late) <= 2, f"one notice per waiting carrier, got {len(late)}"


# --------------------------------------------------------------------- §4

def s4_boundary_bookkeeping() -> None:
    """`steer_wait` measures the CURRENT tool call. After the turn ends it
    reports nothing at all — an idle agent is not 'stuck in a tool call'."""
    slug, _boss, worker = S1["slug"], S1["boss"], S1["worker"]
    st = steer_state(slug, worker)
    assert st.get("polls", 0) >= 2, st
    wait_for(lambda: not steer_state(slug, worker).get("responding"), 120,
             "the long turn to end")
    st = steer_state(slug, worker)
    assert st.get("wait") is None, (
        f"an idle agent has no tool call in flight: {st}")


# --------------------------------------------------------------------- §5

def s5_a_restart_cannot_re_alarm() -> None:
    """THE ALARM IS EXACTLY-ONCE ACROSS A BACKEND BOUNCE (coordinator ruling).

    A false "your message may not have landed" is worse than silence, because
    it teaches senders to distrust a channel that mostly works — so the one
    thing this must never do is re-fire for a carrier that was already
    reported, or one the recipient legitimately consumed.

    It cannot, and the reason is structural rather than careful: the steer
    store lives in `supervisor._state`, which is process memory and is never
    written to the org doc. The `late_told` stamp is a key on the carrier
    itself, so the alarm state and the thing it describes are the same object
    — they cannot desynchronise, and a bounce drops both together. There is
    no persisted ledger for a restarted sweeper to read, which is why it has
    nothing to re-fire from.

    Proved here the expensive way — actually bounce the backend and let the
    restarted sweeper run for several of its own poll intervals — because the
    structural argument is exactly the kind that stays true right up until
    someone adds persistence to the steer store."""
    slug, boss = S1["slug"], S1["boss"]

    def alarms() -> int:
        n = (doc(slug).get("notices") or {}).get(boss) or []
        return len([e for e in n if "has NOT been read" in str(e.get("text") or "")])

    before = alarms()
    assert before >= 1, "§3 should have left at least one alarm to re-fire"
    # nothing on disk should even describe a steered carrier
    blob = json.dumps(doc(slug))
    assert "late_told" not in blob, (
        "the alarm stamp reached the org doc — a restarted sweeper could read "
        "it, and then this is no longer exactly-once by construction")

    stop_backend()
    start_backend()
    # give the restarted sweeper several passes at whatever it thinks is
    # pending; a re-fire would show up as a second notice for the same carrier
    time.sleep(max(6.0, STEER_LATE_POLL_S * 5))
    after = alarms()
    assert after == before, (
        f"a backend restart re-alarmed: {before} notice(s) before the bounce, "
        f"{after} after — the same carriers were reported twice")
    print(f"        · {before} alarm(s) before the bounce, {after} after")


# -------------------------------------------------------------------- main

LATE = 3.0        # overridden below from the backend's own constant
STEER_LATE_POLL_S = 1.0


def main() -> int:
    global LATE
    print(f"\nsteer-window latency  (rig {TMP}, port {PORT})\n")
    # the alarm line, scaled to a rig whose tool calls are seconds rather than
    # minutes — the backend reads the same two env vars
    LATE = 3.0
    os.environ["ORGTREE_STEER_LATE"] = str(LATE)
    os.environ["ORGTREE_STEER_LATE_POLL"] = str(STEER_LATE_POLL_S)
    plant_identity()
    set_cfg(default={"echoMs": 30, "firstEventMs": 60, "resultMs": 40})
    start_backend()
    try:
        check("§1  a mid-turn message inside a long tool call waits for it",
              s1_the_wait_is_real)
        check("§2  and the sender's own result says so",
              s2_the_sender_is_told)
        check("§2  a short tool call is NOT flagged (anti-vacuity)",
              s2_short_call_is_not_alarmed)
        check("§2  a recipient deep in one call is pointed at ⏸",
              s2_long_call_names_the_interrupt)
        check("§3  a late steer alarms its sender, once",
              s3_the_late_steer_alarms_its_sender)
        check("§4  the boundary heartbeat measures the current tool call",
              s4_boundary_bookkeeping)
        check("§5  a backend restart cannot re-alarm an old carrier",
              s5_a_restart_cannot_re_alarm)
    finally:
        for slug in list(_orgs):
            try:
                api("DELETE", f"/api/orgs/{slug}", timeout=10)
            except Exception:                                    # noqa: BLE001
                pass
        stop_backend()
    print()
    for label, tb in FAIL:
        print(f"--- {label}\n{tb}")
    print(f"{PASS} passed, {len(FAIL)} failed")
    if FAIL:
        print(f"\nbackend log tail:\n{log_tail()}")
    if not KEEP and not FAIL:
        shutil.rmtree(TMP, ignore_errors=True)
    else:
        print(f"rig kept at {TMP}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
