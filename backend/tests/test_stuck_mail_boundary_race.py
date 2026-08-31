"""Regression test for the "stuck in mid-delivery" mail bug (user report,
2026-08-30/31): a message posted while its recipient is mid-turn can be folded
from the steer path into the in-memory queue at a result boundary. D-201 used
an identity-hash mismatch (a routine event — any hire/retire/charter edit on
a live org member dirties it) as BOTH a cache-reuse gate and a delivery gate.
It therefore refused to feed the waiting message to the live process and did
not reach the iterative queue handoff until that process actually exited. A
still-running background subagent could hold that exit indefinitely, leaving
the node busy and its messages accumulating for the whole turn.

Forensics on the live incident (cross-referencing the org doc's mail_log,
steered_log, and the D-201 warm.jsonl telemetry, all timestamped to the
millisecond) found:
  1. message A posted while the recipient is mid-turn -> steer path.
  2. steered_log: "missed the steer window" -> folded steer -> queue at a
     result boundary.
  3. in the SAME window the recipient's identity hash changed (a report was
     retired, which rewrites the org-state block embedded in every prompt) ->
     warmpool.boundary_check declines ("identity-changed") -> the process is
     torn down instead of parked.
  4. a LATER, unrelated message runs a completely clean turn on a freshly
     re-warmed process ~55s later -- but its own mailbox read does not see
     message A. It surfaces again, batched with a THIRD message, ~73s after
     it was first sent.

The fix separates those state transitions: identity dirtiness forbids parking
and classifies the eventual relaunch, but the already-open process drains its
queue first. Kill-switch and eligibility changes still close delivery. This
file adds the ingredient the old D7 did not have: a background subagent
(fakecli's `bgTasks`) that keeps the OS process alive after stdin closes. It is
the deterministic witness that the message no longer waits for teardown.

Run: python tests/test_stuck_mail_boundary_race.py
"""
import glob
import json
import os
import sys
import tempfile
import time

if not getattr(sys, "_utf8_wrapped", False):
    sys.stdout = io = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys._utf8_wrapped = True

RIG = tempfile.mkdtemp(prefix="stuckmail-")
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
os.environ["ORGTREE_TURN_IDLE"] = "60"     # the background hold must not trip this
os.environ["ORGTREE_WARM"] = "1"
os.environ["ORGTREE_WARM_POLL"] = "3600"   # keeper passes are manual here
os.environ.pop("ORGTREE_STEER_HOOK", None)
sys.path.insert(0, BACKEND)

with open(os.path.join(RIG, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address": "http://127.0.0.1:9"}')
with open(CFG, "w", encoding="utf-8") as f:
    json.dump({
        "default": {"echoMs": 30, "firstEventMs": 50, "resultMs": 30},
        # a real multi-second response (mirrors the ~2.7-minute live turn,
        # scaled down) with ONE background subagent that outlives the CLI's
        # own stdin EOF by BG_HOLD_MS -- the ingredient D7 does not have.
        "vanishboy": {"echoMs": 30, "firstEventMs": 60, "resultMs": 900,
                      "bgTasks": 1, "bgMs": 1200},
    }, f)

from orgtree import store, supervisor as S, warmpool as W          # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

W._FLAG_TTL = 0.5

PASS = FAIL = 0


def check(label, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:3d}  {label}")
    except Exception as e:                                        # noqa: BLE001
        FAIL += 1
        import traceback
        print(f"  FAIL     {label}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=6)


def token():
    return "SM" + os.urandom(5).hex()


def wait_for(pred, secs=5.0, why="condition"):
    t0 = time.time()
    while time.time() - t0 < secs:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {why}")


def reload_org():
    return store.load_org(SLUG)


def transcript_text():
    out = []
    for p in glob.glob(os.path.join(HOME, ".claude", "projects", "*", "*.jsonl")):
        try:
            out.append(open(p, encoding="utf-8", errors="replace").read())
        except OSError:
            pass
    return "\n".join(out)


def carriers(nid, tok):
    """Every place a posted mail can legitimately be waiting -- the doc's
    durable structures, the in-memory turn state, and the transcript. The
    invariant (test_turn_lifecycle.py's, restated here): at least one of
    these must be true, always, once the recipient has gone idle again."""
    o = reload_org()
    st = S.state(SLUG, nid)
    return {
        "mailbox": any(tok in (m.get("body") or "")
                       for m in (o.d.get("mail") or {}).get(nid, [])),
        "journal": any(tok in (m.get("body") or "")
                       for b in (o.d.get("delivering") or {}).get(nid, [])
                       for m in (b.get("mail") or [])),
        "in_memory_queue": any(tok in json.dumps(x) for x in st.get("queue") or []),
        "in_memory_steer": any(tok in json.dumps(x) for x in st.get("steer") or []),
        "transcript": tok in transcript_text(),
    }


def admit_lines(nid):
    p = os.path.join(RIG, "journals", "warm.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for ln in open(p, encoding="utf-8"):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("kind") == "admit" and r.get("nid") == nid:
            out.append(r)
    return out


def exit_lines(nid):
    p = os.path.join(RIG, "journals", "warm.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for ln in open(p, encoding="utf-8"):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if r.get("kind") == "proc" and r.get("event") == "exit" \
                and r.get("nid") == nid:
            out.append(r)
    return out


# ── rig org ──────────────────────────────────────────────────────────────
org = store.create_org("stuck-mail rig")
SLUG = org.d["slug"]
org.hire(USER, None, "haiku", 5, "vanishboy", add_dirs=[],
         tools={"bash": True, "web": False, "edit": False,
                "subagents": False, "mcp": []},
         org_visibility="team", charter="stuck-mail rig agent")
store.save_org(org)
NID = "vanishboy"


def identity_dirty_does_not_block_claude_hook_fetch():
    """The Claude lane's live PostToolUse hook consumes ``pop_steer``. An
    identity refresh may classify the process for later relaunch, but it must
    not make that already-running response stop accepting hook context."""
    tok = token()
    st = S.state(SLUG, NID)
    with S._state_lock:
        st["busy"] = True
        st["responding"] = True
    try:
        r = S.send_message(SLUG, NID, "live hook mail " + tok)
        assert r.get("steering"), r
        with store.DOC_LOCK:
            o = reload_org()
            o.node(NID)["charter"] = "dirty before the next hook fetch"
            store.save_org(o)
        got = S.pop_steer(SLUG, NID)
        assert len(got) == 1 and tok in got[0], \
            f"identity dirtiness blocked the live hook fetch: {got}"
    finally:
        with S._state_lock:
            st["busy"] = False
            st["responding"] = False
            st["steer"] = []


check("R0 · identity dirtiness does not stop Claude's live hook fetch",
      identity_dirty_does_not_block_claude_hook_fetch)


def steer_then_dirty_drains_before_bg_delayed_relaunch():
    """The exact live sequence: msg2 arrives mid-turn (steer path), the
    recipient's identity is dirtied in the same window (an ordinary
    hire/retire/charter edit on a live org member), and the turn's own process
    has a background subagent that would delay teardown. msg2 must ride the
    live result boundary instead of waiting for that teardown."""
    W.keeper_pass_now()
    wait_for(lambda: W.is_warm(SLUG, NID), why="pre-warm vanishboy")
    with W._pool_lock:
        stale_pid = W._pool[(SLUG, NID)].proc.pid

    tok1 = token()
    queued = [token(), token(), token()]
    after_relaunch = token()
    n_before = len(admit_lines(NID))
    exits_before = len(exit_lines(NID))
    r = S.send_message(SLUG, NID, f"slow one {tok1}")
    assert r["accepted"]
    st = S.state(SLUG, NID)
    wait_for(lambda: st["busy"], why="turn one starts")
    wait_for(lambda: st.get("responding"), why="turn one is responding")
    time.sleep(0.25)                       # inside msg1's 900ms result window

    for i, tok in enumerate(queued, 1):
        r = S.send_message(SLUG, NID, f"queued steer {i} {tok}")
        assert r.get("steering"), f"message {i} missed the steer lane: {r}"
    # dirty the identity RIGHT NOW -- exactly like a hire/retire elsewhere in
    # the org would, per the coordinator's own diagnosis of the live incident
    with store.DOC_LOCK:
        o = reload_org()
        o.node(NID)["charter"] = f"dirtied mid-turn at {time.time()}"
        store.save_org(o)

    # The live process must drain msg2 before its identity-dirty relaunch.
    # Before the fix, the false boundary decision closed stdin here and the
    # background subagent held the entire delivery pump past this deadline.
    wait_for(lambda: not st["busy"] and not st.get("queue"), secs=15,
             why="the dirtied turn (plus its lingering background subagent) "
                 "to fully end")

    after_dirty = admit_lines(NID)[n_before:]
    assert len(after_dirty) == 1 + len(queued), after_dirty
    assert after_dirty[0].get("reason") == "warm-hit", after_dirty
    assert all(a.get("reason") == "boundary-feed"
               for a in after_dirty[1:]), (
        "identity dirtiness stopped the live delivery pump; queued mail "
        "waited for process teardown instead of draining through consecutive "
        f"result boundaries: {after_dirty}")

    # msg2 must be somewhere inspectable -- the mailbox, the delivery
    # journal, still riding an in-memory carrier, or already in the
    # transcript. "nowhere" is the bug.
    transcript = transcript_text()
    missing = [tok for tok in queued if tok not in transcript]
    assert not missing, f"queued messages never reached the live process: {missing}"

    # Dirtiness still means RELAUNCH. The stale process must not park after
    # draining the continuously-busy chain, and the next independently-started
    # turn must run only after a fresh-identity process is ready.
    W.keeper_pass_now()
    wait_for(lambda: W.is_warm(SLUG, NID), why="fresh process after drain")
    with W._pool_lock:
        fresh_pid = W._pool[(SLUG, NID)].proc.pid
    assert fresh_pid != stale_pid, (
        f"the identity-dirty process parked after draining: pid={stale_pid}")
    exits = exit_lines(NID)[exits_before:]
    assert len(exits) == 1 and exits[0].get("reason") == "identity-changed", (
        f"relaunch was not classified exactly once: {exits}")

    S.send_message(SLUG, NID, f"independent turn {after_relaunch}")
    wait_for(lambda: not st["busy"], secs=15, why="post-relaunch turn")
    assert after_relaunch in transcript_text(), \
        "the independent turn did not run after relaunch"


check("R1 · identity-dirty relaunch does not stop the live delivery pump",
      steer_then_dirty_drains_before_bg_delayed_relaunch)


def slot_wait_is_measured_and_warned_on():
    """D-201's admit journal could not previously say whether an admission
    ever waited for the machine-wide `_turn_slots` seat -- the one carrier
    a stuck-mail incident (user report 2026-08-30) could not be ruled in or
    out against, because a slot wait leaves NO other trace. This proves the
    new `slot_wait_s` field can report a REAL nonzero number (not a field
    that is structurally always zero -- the vacuous-check shape) and that
    the loud warning actually fires above threshold, by manufacturing a
    genuinely saturated cap: hold every seat, send a message, and watch it
    wait."""
    import io as _io
    import threading as _threading

    time.sleep(0.1)   # let R1's own slot release (its `with` __exit__) settle
    held = [S._turn_slots.acquire() for _ in range(S.MAX_CONCURRENT)]
    orig_warn = S.SLOT_WAIT_WARN_S
    S.SLOT_WAIT_WARN_S = 0.2     # small, so the test does not need real seconds
    HOLD_S = 0.6                 # > SLOT_WAIT_WARN_S, so the print must fire
    try:
        def _release_one_after_a_while():
            time.sleep(HOLD_S)
            S._turn_slots.release()
        _threading.Thread(target=_release_one_after_a_while, daemon=True).start()

        n_before = len(admit_lines(NID))
        buf = _io.StringIO()
        real_stdout = sys.stdout
        sys.stdout = buf
        try:
            tok = token()
            r = S.send_message(SLUG, NID, f"waits for a seat {tok}")
            assert r["accepted"]
            wait_for(lambda: not S.state(SLUG, NID)["busy"], secs=15,
                     why="the slot-starved turn to finally run")
        finally:
            sys.stdout = real_stdout
        printed = buf.getvalue()

        adm = admit_lines(NID)[n_before:]
        assert adm, f"the slot-starved message never got an admission at all: {adm}"
        waits = [a.get("slot_wait_s") for a in adm]
        assert any((w or 0) >= HOLD_S * 0.5 for w in waits), (
            f"slot_wait_s must report something close to the real "
            f"~{HOLD_S}s wait, not a structurally-zero field: {waits}")
        assert "waited" in printed and "turn slot" in printed, (
            f"the loud above-threshold warning did not fire even though the "
            f"wait ({waits}) was well past SLOT_WAIT_WARN_S={S.SLOT_WAIT_WARN_S}: "
            f"captured stdout was {printed!r}")
    finally:
        S.SLOT_WAIT_WARN_S = orig_warn
        for _ in held[1:]:            # the delayed thread already freed one
            try:
                S._turn_slots.release()
            except ValueError:
                pass


check("R2 · slot-acquisition wait is measured on the admit row and warned "
      "on loudly above threshold", slot_wait_is_measured_and_warned_on)

print(f"\n{PASS} passed, {FAIL} failed")
if FAIL:
    sys.exit(1)
