"""Regression test for the "stopped-task never wakes its agent" incident,
2026-09-01 (user report via coordinator; incident evidence from
fable-cli-migration, reproduced twice on the real CLI: task `bjl7hffri` and
`b2v748xj4`, both a backgrounded `Bash(run_in_background:true)` test run
that died mid-flight while the owning turn had already ended and parked).

FORENSICS (org doc + `journals/warm.jsonl`, both still on disk):
  23:02:40Z  fable-cli-migration backgrounds a long test run.
  23:06:16Z  its OWN turn ends normally (`_bg_count()==0` by then — no
             "background-children" exit was ever journaled) and the process
             parks, clean and untouched, for the next 30 minutes (confirmed
             via `warm.jsonl`: no kill/respawn between park and reuse).
  23:36:35Z  an UNRELATED 30-minute working-status heartbeat is the first
             thing to start a new turn — and only THEN does the CLI's own
             passive session-resume reconciliation ("No completion record
             was found for this background shell command from the previous
             session...") get a turn to ride in on. 30 minutes of silence
             for a task that had already ended.

THE DEFECT. `_bg_orphaned` (supervisor.py) already handles "the CLI PROCESS
died while a background task was still tracked live" — durable mail, then a
drive, kind="message" (not "notice", which is the no-wake marker). But it can
only fire from bg_live, and a task that ends WITHOUT the process dying is
invisible to it: the CLI reports the outcome via `task_notification`
(`status`, `summary` — proven by `tests/fakecli.js`'s own pre-existing
simulation of the success case, and by the real installed CLI binary's
strings), but the handler for that event (~line 8890) read only
`description`/`output_file`. The one field that mattered — `status` — was
read by nobody. A "stopped"/"failed" status was silently absorbed into
bookkeeping exactly like a "completed" one, and the agent was left waiting on
a notification that had already arrived and been discarded.

THE FIX adds `_bg_task_stopped`, sibling to `_bg_orphaned`, fired the moment
`task_notification` reports a `status` other than "completed" for a task
still in `bg_live`. Same durable-mail-then-drive shape, reusing the same
proven `send_message` drive path.

D1 is the repro (must fail before the fix). D2 is the control: a normal
"completed" background task must NOT raise a false alarm (the exact thing
test_turn_lifecycle.py's own orphan-detection controls already guard for the
*process-death* path — this is its sibling for the *live-process* path).
D3 checks the report is durable-mail-first (kind != "notice", the no-wake
marker) and actually DRIVES the idle node, not just deposits mail nobody
reads.

Run: python tests/test_bg_task_stopped_notification.py
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

RIG = tempfile.mkdtemp(prefix="bgstop-")
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
    return "BS" + os.urandom(5).hex()


def set_cfg(node: str, cfg: dict) -> None:
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump({"default": {}, node: dict(cfg)}, f)


def wait_for(pred, secs=20.0, why="condition"):
    t0 = time.time()
    while time.time() - t0 < secs:
        if pred():
            return
        time.sleep(0.05)
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


def mail_bodies(nid):
    """mail_log (durable, survives delivery) + the still-pending mailbox —
    both are places a real reader would look, matching test_turn_lifecycle's
    own orphan-detection idiom."""
    o = reload_org()
    d = o.d
    out = [(m.get("kind"), str(m.get("body") or ""))
           for m in (d.get("mail_log") or {}).get(nid, [])]
    out += [(m.get("kind"), str(m.get("body") or ""))
            for m in (d.get("mail") or {}).get(nid, [])]
    return out


# rig org
org = store.create_org("bgstop rig")
SLUG = org.d["slug"]
org.hire(USER, None, "haiku", 5, "bgstopboy", add_dirs=[],
         tools={"bash": False, "web": False, "edit": False,
                "subagents": False, "mcp": []},
         org_visibility="team", charter="bg-stop rig agent")
store.save_org(org)
NID = "bgstopboy"


def d1_a_task_that_stops_without_the_process_dying_wakes_its_agent():
    """THE REPRO. Background one task, let the turn's own reply land at its
    boundary (so `bg_live` genuinely believed it was still outstanding), then
    the fake CLI reports it ending with status "stopped" instead of
    "completed" — the shape the real CLI used for fable-cli-migration's
    two dead test runs. On the unfixed engine this is read for
    `description`/`output_file` only and thrown away: no mail, no drive."""
    tok = token()
    set_cfg(NID, {"echoMs": 120, "firstEventMs": 220, "resultMs": 20,
                  "bgTasks": 1, "bgMs": 800, "bgOnce": True,
                  "bgStatus": "stopped", "bgSummary": "STOPPED-" + tok})
    st = S.state(SLUG, NID)
    assert S.send_message(SLUG, NID, "launch a background task " + tok)["accepted"]
    wait_for(lambda: (st.get("bg_tasks") or 0) >= 1,
             why="the background task to actually be launched")
    # Neuter further launches NOW that this one is confirmed under way — the
    # fix's own drive (see D3) starts a fresh turn, and fakecli launches a
    # background task on every served message unconditionally, so without
    # this the drive would relaunch another one forever. `bgOnce` guards a
    # SINGLE reused process; this guards a freshly cold-spawned one, which is
    # what actually happens here (measured).
    set_cfg(NID, {"echoMs": 120, "firstEventMs": 220, "resultMs": 20,
                  "bgTasks": 0})

    def _reported() -> None:
        bodies = mail_bodies(NID)
        hit = [b for k, b in bodies if "BACKGROUND TASK STOPPED" in b]
        assert hit, (
            "STILL SILENT: the background task reported status='stopped' "
            "with summary STOPPED-%s and nothing durable was ever written "
            "for %s — this is fable-cli-migration's 30-minute hang, "
            "reproduced. All mail: %r" % (tok, NID, bodies))
        assert any(("STOPPED-" + tok) in b for b in hit), (
            "a stop was reported but the CLI's own summary text was not "
            "carried into the mail — the agent cannot tell WHAT stopped")

    wait_for(lambda: any("BACKGROUND TASK STOPPED" in b
                         for _, b in mail_bodies(NID)),
             secs=15, why="the stopped-task notice to be written")
    _reported()
    wait_for(lambda: not st["busy"], secs=15,
             why="the launching-then-driven turn(s) to settle")


check("D1 - REPRO: a background task that stops without the process dying "
      "produces durable, actionable mail", d1_a_task_that_stops_without_the_process_dying_wakes_its_agent)


def d2_a_task_that_completes_normally_raises_no_false_alarm():
    """CONTROL. Same shape, status="completed" (the default/existing
    behaviour) — must NOT produce a "BACKGROUND TASK STOPPED" mail. Mirrors
    test_turn_lifecycle's own positive control for the process-death path
    (`_survives`, asserting "no orphan notice" for a child that finished)."""
    tok = token()
    # D1 already left a "BACKGROUND TASK STOPPED" mail on this SAME node's
    # durable mail_log (by design — mail_log never clears) — this control
    # shares the rig deliberately (no reason a healthy background task should
    # need a fresh node), so the assertion below must count NEW hits only.
    before = sum(1 for _, b in mail_bodies(NID) if "BACKGROUND TASK STOPPED" in b)
    set_cfg(NID, {"echoMs": 120, "firstEventMs": 220, "resultMs": 20,
                  "bgTasks": 1, "bgMs": 500, "bgStatus": "completed"})
    st = S.state(SLUG, NID)
    assert S.send_message(SLUG, NID, "launch a normal background task " + tok)["accepted"]
    wait_for(lambda: st["busy"], why="the turn to start")
    wait_for(lambda: not st["busy"], secs=15, why="the turn to park")
    wait_for(lambda: "BG-LANDED-0" in transcript_text(), secs=10,
             why="the background child to land normally")

    bodies = mail_bodies(NID)
    hit = [b for k, b in bodies if "BACKGROUND TASK STOPPED" in b]
    assert len(hit) == before, (
        "OVERSHOOT: a background task that completed normally ('status': "
        "'completed') was reported as stopped anyway: %r" % hit[before:])


check("D2 - CONTROL: a normally-completed background task raises no false "
      "alarm", d2_a_task_that_completes_normally_raises_no_false_alarm)


def d3_the_report_is_a_driving_message_not_a_passive_notice():
    """The mail must be kind="message" (the wake-capable kind), never
    "notice" — `Org.waking_mail`'s own no-wake marker — and it must actually
    DRIVE the idle node, not just sit in the box for a turn that may never
    come (which is the entire bug being fixed). `send_message`'s drive nudge
    lands as its own assistant reply (fakecli's default `replyText`), so a
    fresh reply appearing after the node had gone idle is the positive
    evidence a real turn ran — not merely that mail was deposited."""
    tok = token()
    set_cfg(NID, {"echoMs": 120, "firstEventMs": 220, "resultMs": 20,
                  "bgTasks": 1, "bgMs": 800, "bgOnce": True,
                  "bgStatus": "failed", "bgSummary": "FAILED-" + tok})
    st = S.state(SLUG, NID)
    # baseline BEFORE this test's own launch turn runs at all, so "a fresh
    # reply landed" can be measured by COUNT — "ack." merely being present
    # is meaningless (the launch turn's own ordinary reply already is "ack.")
    before = transcript_text().count("ack.")
    assert S.send_message(SLUG, NID, "launch a background task " + tok)["accepted"]
    wait_for(lambda: (st.get("bg_tasks") or 0) >= 1,
             why="the background task to actually be launched")
    set_cfg(NID, {"echoMs": 120, "firstEventMs": 220, "resultMs": 20,
                  "bgTasks": 0})

    wait_for(lambda: any("BACKGROUND TASK STOPPED" in b
                         for _, b in mail_bodies(NID)),
             secs=15, why="the stopped-task notice to be written")
    kinds = {k for k, b in mail_bodies(NID) if "BACKGROUND TASK STOPPED" in b}
    assert kinds == {"message"}, (
        "the stopped-task report used kind=%r — it must be \"message\", not "
        "\"notice\" (Org.waking_mail's own no-wake marker), or it will sit "
        "unread exactly like the incident this fixes" % kinds)

    # the drive: TWO fresh replies must land — the launch turn's own ("ack."
    # #1) AND a SECOND one from the turn the stopped-task mail drives ("ack."
    # #2). Mail sitting in the box unread would stop at #1 forever, which is
    # exactly fable-cli-migration's 30-minute hang.
    wait_for(lambda: transcript_text().count("ack.") >= before + 2, secs=15,
             why="a second assistant reply proving a fresh turn actually ran "
                 "(not just that mail sat in the box)")


check("D3 - the stopped-task report is a driving message (kind=\"message\") "
      "and actually wakes the idle node", d3_the_report_is_a_driving_message_not_a_passive_notice)


if FAIL:
    print("\n%d passed, %d FAILED" % (PASS, FAIL))
    sys.exit(1)
print("\nALL %d CHECKS PASS" % PASS)
