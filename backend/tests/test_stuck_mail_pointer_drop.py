"""Regression test for the "held and never delivered" mail bug — SECOND
instance, user-reported 2026-08-30 18:47Z: "stuff keeps getting held and never
delivered", exemplified by "the scope request approval decision for your rw to
your own scratch folder", then at 18:51Z "this scope request approval is
*still* waiting to be delivered mid-task, i approved this like 5 or 6 messages
back", then 18:51:16Z "there it just arrived for some reason".

FORENSICS (org doc, live):
  18:45:55.485Z  [SCOPE REQUEST decided] posted to the coordinator, then driven
                 with send_message(..., mail_ping=True)  (api.py batch_resolve).
  18:46:05.049Z  steered_log: "1 mid-turn message(s) missed the steer window
                 (result boundary: no further tool call)".
  18:51:16Z      the user sees it arrive — 5m21s later, flushed by unrelated
                 traffic. The 17:49Z incident has the identical signature.

THE DEFECT. A "mail pointer" is a nudge whose whole content is "there is mail
in your box"; it is droppable when the box is empty, which is what stops
phantom wakes. But a steer carrier that was folded into the queue ALREADY
HOLDS its drained batch — _envelope() took the mail out of doc["mail"] and
wrote it to doc["delivering"], so the box is empty BY CONSTRUCTION and the
carrier itself is the only thing still pointing at the message.

There are two drop sites. The result-boundary one gets this right:

    elif nping and not ntoks:            # supervisor.py, boundary feed
        _phantom_log(...); continue

`not ntoks` means "this carrier owes no journal token", i.e. it is a bare
pointer and not one holding mail. test_turn_lifecycle.py's
`_a_carrier_already_holding_mail_is_never_dropped` states in prose that "Both
drop sites now also require that the carrier owes no journal token" — but it
only asserts that _mark_ping PRESERVES the tokens. It never exercises either
drop site, so it cannot see that the SECOND site was never given the clause:

    if _carrier_is_ping(nxt) and not _has_deliverable(slug, nid):   # _run_turn
        _phantom_log(slug, nid, "turn start")
        nxt = _drop_ping(slug, nid)

_has_deliverable() reads only doc["mail"] and doc["notices"]. The mail is in
neither. So a carrier holding a real message is judged a phantom and thrown
away, no turn runs, and the message survives only as an unconfirmed batch in
doc["delivering"] until some LATER, unrelated turn happens to call
_fold_back_undelivered and put it back. That is the "it just arrived for some
reason" the user reported, twice.

WHY THE EXISTING REPRO MISSED IT. test_stuck_mail_boundary_race.py builds the
whole steer-fold + identity-change sequence but sends with the DEFAULT
mail_ping=False, so its carrier is never marked as a pointer and the gate
above never arms. It self-heals in ~66ms and reports the path healthy. The one
missing input is mail_ping=True — which is what every real caller uses
(api.py has nine of them, including the scope-decision one).

D1 is the repro and MUST FAIL before the fix. D2 and D3 are the controls that
stop the fix from overshooting: the genuine phantom drop must survive.

Run: python tests/test_stuck_mail_pointer_drop.py
"""
import glob
import json
import os
import sys
import tempfile
import time

if not getattr(sys, "_utf8_wrapped", False):
    sys.stdout = __import__("io").TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys._utf8_wrapped = True

RIG = tempfile.mkdtemp(prefix="pingdrop-")
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
os.environ["ORGTREE_TURN_IDLE"] = "60"
os.environ["ORGTREE_WARM"] = "1"
os.environ["ORGTREE_WARM_POLL"] = "3600"
os.environ.pop("ORGTREE_STEER_HOOK", None)
sys.path.insert(0, BACKEND)

with open(os.path.join(RIG, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address": "http://127.0.0.1:9"}')
with open(CFG, "w", encoding="utf-8") as f:
    json.dump({
        "default": {"echoMs": 30, "firstEventMs": 50, "resultMs": 30},
        "pointerboy": {"echoMs": 30, "firstEventMs": 60, "resultMs": 900,
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
        print("  ok %3d  %s" % (PASS, label))
    except Exception as e:                                        # noqa: BLE001
        FAIL += 1
        import traceback
        print("  FAIL     %s: %s: %s" % (label, type(e).__name__, e))
        traceback.print_exc(limit=6)


def token():
    return "PD" + os.urandom(5).hex()


def wait_for(pred, secs=5.0, why="condition"):
    t0 = time.time()
    while time.time() - t0 < secs:
        if pred():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for " + why)


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
    """Every place a posted mail can legitimately be waiting. "nowhere the
    agent will ever look" is the bug."""
    o = reload_org()
    st = S.state(SLUG, nid)
    return {
        "mailbox": any(tok in (m.get("body") or "")
                       for m in (o.d.get("mail") or {}).get(nid, [])),
        "journal": any(tok in (m.get("body") or "")
                       for b in (o.d.get("delivering") or {}).get(nid, [])
                       for m in (b.get("mail") or [])),
        "in_memory_queue": any(tok in json.dumps(x) for x in S.state(SLUG, nid).get("queue") or []),
        "in_memory_steer": any(tok in json.dumps(x) for x in st.get("steer") or []),
        "transcript": tok in transcript_text(),
    }


# rig org
org = store.create_org("pointer-drop rig")
SLUG = org.d["slug"]
org.hire(USER, None, "haiku", 5, "pointerboy", add_dirs=[],
         tools={"bash": False, "web": False, "edit": False,
                "subagents": False, "mcp": []},
         org_visibility="team", charter="pointer-drop rig agent")
store.save_org(org)
NID = "pointerboy"


def _journal_a_drained_batch(tok):
    """Recreate EXACTLY the state _envelope() leaves behind after a steer:
    the mail is out of doc["mail"] and sitting in doc["delivering"] under a
    journal token. This is the trap — the mailbox is empty and the carrier is
    the only live pointer at the message."""
    jt = "jt" + tok
    body = "[MAIL - 1 message(s)]\nFROM @user - decision\n[SCOPE REQUEST decided]\n" + tok
    with store.DOC_LOCK:
        o = reload_org()
        o.d.setdefault("delivering", {}).setdefault(NID, []).append(
            {"tok": jt, "at": "2026-08-30T18:45:55.485Z", "via": "steer",
             "mail": [{"id": tok[:8], "from": "@user", "kind": "decision",
                       "body": body, "at": "2026-08-30T18:45:55.485Z"}],
             "notices": []})
        store.save_org(o)
    return jt, body


def d1_folded_steer_pointer_holding_mail_must_still_be_delivered():
    """THE REPRO. A folded steer carrier — marked as a pointer, holding a real
    drained message and its journal token — is handed to _run_turn exactly as
    _run_one_turn's finally hands it over (queue pop -> `follow` -> the loop).
    The mailbox is empty because this carrier is what emptied it.

    On the unfixed engine the turn-start gate calls it a phantom and drops it:
    no turn runs and the message is not delivered."""
    tok = token()
    jt, body = _journal_a_drained_batch(tok)
    assert not S._has_deliverable(SLUG, NID), (
        "the fixture is wrong: the mailbox must be EMPTY for this to be the "
        "trap the live bug walks into")
    carrier = S._mark_ping({"toks": [jt], "text": body})
    assert S._carrier_is_ping(carrier) and carrier.get("toks"), \
        "the fixture is not a pointer-holding-mail, so it proves nothing"

    st = S.state(SLUG, NID)
    with S._state_lock:
        st["busy"] = True
    try:
        S._run_turn(SLUG, NID, carrier)          # blocking
    finally:
        with S._state_lock:
            st["busy"] = False

    where = carriers(NID, tok)
    assert where["transcript"], (
        "HELD, NOT DELIVERED: the scope-decision message %s was carried by a "
        "folded steer pointer, the turn-start gate judged it a phantom "
        "because doc['mail'] was empty, and no turn ever ran. It is still "
        "sitting in %s. This is the user's 18:45:55 -> 18:51:16 hold, "
        "reproduced." % (tok, {k: v for k, v in where.items() if v} or "NOWHERE"))


check("D1 - REPRO: a folded steer pointer holding drained mail is delivered, "
      "not dropped as a phantom", d1_folded_steer_pointer_holding_mail_must_still_be_delivered)


def d2_a_bare_pointer_is_still_dropped_at_the_TURN_START_gate():
    """CONTROL - the fix must not overshoot.

    \u26a0 THIS CHECK IS DELIBERATELY NARROWER THAN "no phantom wake reached the
    agent", and the first version of it was WORTHLESS for exactly that reason.
    orgtree has FOUR phantom drop sites, and the one immediately downstream of
    this one (_run_one_turn's post-slot drop, "the box emptied while this turn
    waited for a slot") independently catches a bare pointer. So a mutant with
    the turn-start gate deleted OUTRIGHT still produced no phantom wake, and a
    transcript-only assertion passed against it - a control that cannot fail
    when the thing it guards is removed.

    Measured, not argued: planting that mutant left the transcript assertion
    green and only the LOG changed. So this pins the log marker, which is the
    one observable that distinguishes site 1 from site 2. Site 1 prints
    "dropped at turn start -"; site 2 prints "dropped at turn start (the box
    emptied while this turn waited for a slot)"."""
    tok = token()
    assert not S._has_deliverable(SLUG, NID), "the box should be empty here"
    carrier = S._mark_ping("(orgtree) You have new mail above " + tok)
    assert S._carrier_is_ping(carrier) and not carrier.get("toks"), \
        "the control fixture must be a BARE pointer (no journal token)"
    st = S.state(SLUG, NID)
    buf = __import__("io").StringIO()
    real_stdout = sys.stdout
    with S._state_lock:
        st["busy"] = True
    sys.stdout = buf
    try:
        S._run_turn(SLUG, NID, carrier)
    finally:
        sys.stdout = real_stdout
        with S._state_lock:
            st["busy"] = False
    printed = buf.getvalue()

    assert tok not in transcript_text(), (
        "PHANTOM WAKE REGRESSION: a bare pointer against an empty mailbox "
        "spent a whole turn saying nothing (%s reached the transcript)" % tok)
    assert "turn start" in printed, (
        "no drop was logged at all - the bare pointer went somewhere "
        "unaccounted for: %r" % printed)
    assert "waited for a slot" not in printed, (
        "OVERSHOOT: the TURN-START gate no longer drops a bare pointer - it "
        "fell through to the post-slot backstop instead, which means the "
        "cheap early gate is gone and every phantom now pays a full turn-slot "
        "wait before being discarded: %r" % printed)


check("D2 - CONTROL: a bare pointer is still dropped AT THE TURN-START "
      "gate (no phantom wake, no overshoot)",
      d2_a_bare_pointer_is_still_dropped_at_the_TURN_START_gate)


def d3_end_to_end_scope_decision_survives_a_mid_turn_identity_change():
    """The live sequence end to end, with the one ingredient
    test_stuck_mail_boundary_race.py is missing: mail_ping=True, which is what
    every real caller uses (api.py's batch_resolve, which delivers the scope
    decision, is one of nine).

    post_mail then send_message(mail_ping=True) while the recipient is
    responding -> steer -> the identity is dirtied mid-turn (a routine
    hire/retire) -> the boundary feed declines -> the carrier is folded to the
    queue and handed to _run_turn. It must not evaporate there."""
    W.keeper_pass_now()
    wait_for(lambda: W.is_warm(SLUG, NID), why="pre-warm pointerboy")

    tok1, tok2 = token(), token()
    st = S.state(SLUG, NID)
    assert S.send_message(SLUG, NID, "slow one " + tok1)["accepted"]
    wait_for(lambda: st["busy"], why="turn one starts")
    wait_for(lambda: st.get("responding"), why="turn one is responding")
    time.sleep(0.25)

    # EXACTLY what api.py:batch_resolve does for a scope decision
    with store.DOC_LOCK:
        o = reload_org()
        o.post_mail(USER, NID,
                    "[SCOPE REQUEST decided]\n- folder ... (rw) -> GRANTED " + tok2)
        store.save_org(o)
    S.send_message(SLUG, NID,
                   "(orgtree) The mail above resolves your request batch - act "
                   "on it now.", mail_ping=True)

    # dirty the identity mid-turn, like any hire/retire elsewhere in the org
    with store.DOC_LOCK:
        o = reload_org()
        o.node(NID)["charter"] = "dirtied mid-turn at %f" % time.time()
        store.save_org(o)

    wait_for(lambda: not st["busy"] and not st.get("queue"), secs=25,
             why="the dirtied turn and its lingering background subagent to end")

    where = carriers(NID, tok2)
    assert where["transcript"] or where["mailbox"], (
        "HELD: the scope decision %s never reached the agent and is not even "
        "back in the durable mailbox for the next turn to find - it is in %s. "
        "That is the user's report verbatim: approved, then nothing, until "
        "unrelated traffic flushed it minutes later."
        % (tok2, {k: v for k, v in where.items() if v} or "NOWHERE"))


check("D3 - END TO END: a scope decision driven with mail_ping=True survives "
      "a mid-turn identity change", d3_end_to_end_scope_decision_survives_a_mid_turn_identity_change)


print("\n%d passed, %d failed" % (PASS, FAIL))
if FAIL:
    sys.exit(1)
