"""Regression test for the "stuck in mid-delivery" mail bug (user report,
2026-08-30): a message posted while its recipient is mid-turn can be folded
from the steer path into the in-memory queue at a result boundary, decline a
D-201 warm-pool boundary-feed because the recipient's identity hash changed
mid-turn (a routine event — any hire/retire/charter edit on a live org member
dirties it), and then simply VANISH if that dirtied process's actual OS exit
is delayed (e.g. by a still-running background subagent) rather than being
retried immediately or folded back to the durable mailbox.

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

test_warmpool.py's D7 (`d_boundary_feed_declines_dirtied_process`) already
covers "identity dirtied mid-turn while a second message is queued" and
passes -- but only for a short, single-boundary turn with no background
work, where the dirtied process's actual OS exit is immediate. This file adds
the one ingredient D7 does not have: a background subagent (fakecli's
`bgTasks`) that keeps the OS process alive for a while AFTER orgtree closes
its stdin, so the teardown -- and everything that is supposed to run in its
wake, the queue-pop-and-retry or the mailbox fold-back -- is delayed. That is
the untested combination the live incident actually hit.

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


# ── rig org ──────────────────────────────────────────────────────────────
org = store.create_org("stuck-mail rig")
SLUG = org.d["slug"]
org.hire(USER, None, "haiku", 5, "vanishboy", add_dirs=[],
         tools={"bash": True, "web": False, "edit": False,
                "subagents": False, "mcp": []},
         org_visibility="team", charter="stuck-mail rig agent")
store.save_org(org)
NID = "vanishboy"


def steer_then_dirty_then_bg_delay():
    """The exact live sequence: msg2 arrives mid-turn (steer path), the
    recipient's identity is dirtied in the same window (an ordinary
    hire/retire/charter edit on a live org member), the turn's own process
    keeps running a while longer (a background subagent), and only THEN does
    the turn actually end. msg2 must not be lost in that stretch."""
    W.keeper_pass_now()
    wait_for(lambda: W.is_warm(SLUG, NID), why="pre-warm vanishboy")

    tok1, tok2, tok3 = token(), token(), token()
    r = S.send_message(SLUG, NID, f"slow one {tok1}")
    assert r["accepted"]
    st = S.state(SLUG, NID)
    wait_for(lambda: st["busy"], why="turn one starts")
    wait_for(lambda: st.get("responding"), why="turn one is responding")
    time.sleep(0.25)                       # inside msg1's 900ms result window

    S.send_message(SLUG, NID, f"steer me {tok2}")
    # dirty the identity RIGHT NOW -- exactly like a hire/retire elsewhere in
    # the org would, per the coordinator's own diagnosis of the live incident
    with store.DOC_LOCK:
        o = reload_org()
        o.node(NID)["charter"] = f"dirtied mid-turn at {time.time()}"
        store.save_org(o)

    # the OLD turn must fully end -- result boundary, declined boundary-feed,
    # stdin closed, AND the background subagent's extra bgMs on top of that
    n_before = len(admit_lines(NID))
    wait_for(lambda: not st["busy"] and not st.get("queue"), secs=15,
             why="the dirtied turn (plus its lingering background subagent) "
                 "to fully end")

    # msg2 must be somewhere inspectable -- the mailbox, the delivery
    # journal, still riding an in-memory carrier, or already in the
    # transcript. "nowhere" is the bug.
    c2 = carriers(NID, tok2)
    if not any(c2.values()):
        raise AssertionError(
            f"MAIL LOST: {tok2} (msg2, folded from steer during a mid-turn "
            f"identity change whose process teardown was delayed by a "
            f"background subagent) is in NO carrier: {c2}")

    # mirror the live incident's THIRD message: sent after the dirtied turn
    # has fully settled, it must run its own clean turn -- and by then msg2
    # should already have resurfaced (mailbox or transcript), not merely be
    # "still queued/steered", which is what "test"'s own clean run in
    # production proved was NOT the case for message A.
    S.send_message(SLUG, NID, f"third one {tok3}")
    wait_for(lambda: not st["busy"], secs=15, why="the third message's turn")
    assert tok3 in transcript_text(), "the third message was never delivered"

    c2_final = carriers(NID, tok2)
    assert c2_final["mailbox"] or c2_final["transcript"], (
        f"msg2 ({tok2}) never reached a DURABLE, inspectable home (mailbox "
        f"or transcript) even after a third, unrelated message ran its own "
        f"clean turn -- it is stuck exactly the way the user's message was: "
        f"{c2_final}")

    adm_after = admit_lines(NID)
    assert len(adm_after) > n_before + 1, (
        "msg2 never got its own admission (warm or cold) at all -- it did "
        f"not even get the delayed retry the finally-block is supposed to "
        f"give it: {adm_after[n_before:]}")


check("R1 · a mid-turn identity change + a lingering background subagent "
      "must not lose the folded steer message", steer_then_dirty_then_bg_delay)


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
