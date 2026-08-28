"""A watchdog whose SUBJECT died — D-176. MEASURE THE SUBJECT, NOT THE WATCHER.

THE DEFECT THIS COVERS
----------------------
`inline-images@0` armed a file dog on a tier log, waiting for `RUN COMPLETE`.
The tier was killed about a minute after its owner's turn ended. The log's
last write was 21:20; the string it waited for was never going to be appended.
**The agent sat idle for ninety minutes believing it was waiting on a slow
run.** The dog was armed, healthy, and watching a corpse.

WHY THE OBVIOUS FIX IS A TRAP, MEASURED BEFORE ANY CODE WAS WRITTEN
-------------------------------------------------------------------
The diagnosis already existed. Reconstructing that dog's real numbers (535
checks, 4.5 h, never matched) and calling the SHIPPED `wd_health` on it
returns a warning — it always did, in the org doc, the whole time. Nobody was
ever told, because `wd_health` is PULL-ONLY: it answers a question you have to
already suspect the answer to, and an agent that believes it is waiting on a
slow job does not call `list`.

And the second half is why routing that same note to agents would have been
worse than nothing: **feed it a file that is demonstrably GROWING, with the
same age and the same check count, and it returns the identical sentence.**
An instrument that cannot discriminate becomes harmful the moment you act on
it — a false "your producer died" after every quiet hour trains everyone here
to ignore the one alert that matters. §1 pins both halves of that measurement,
because it is the reason this suite exists rather than a one-line change.

THE RULE, AND WHY THE COUNTER SHAPE IS LOAD-BEARING
---------------------------------------------------
An orgtree restart kills every watcher process on this machine, and surviving
restarts is a watchdog's advertised virtue. So "my watcher died" is evidence
about nothing. Every input here is a fact about the WATCHED THING, recorded by
a check that actually ran — and the counter is in **observations, not wall
time**, so downtime cannot accrue staleness: while orgtree is down, no checks
run and the counter simply stops. §2 pins that as a property rather than a
comment, and §4 is the restart-shaped canary the user's constraint demands.

WHAT EACH KIND CAN HONESTLY KNOW  (§3 file · §5 command · §6 process · §7 stream)
--------------------------------------------------------------------------------
  file     NOTHING about death. A path does not know what writes it, so a dead
           producer and a quiet one are the same observation. Reported as
           STALENESS, in those words, and the dog is left ARMED. §3.
  command  Not "the command failed" — a `findstr` waiting for a string exits 1
           on every check and that is HEALTHY. Only "the check could not be
           performed at all". Paused, not removed. §5.
  process  Already correct: a dead subject IS the event. §6 proves it, and
           proves a spent `pid:` dog stops pretending to guard anything.
  stream   Already correct: the engine owns the child and has its exit code.
           §7 proves it, and proves a restart re-spawns instead of reporting a
           false exit.

§8 is the other half of the task: watchdog children are spawned by the BACKEND,
not by the turn that armed them — and the create-time smoke run was leaking a
grandchild on every create, measured on the live box.

Run:  python tests/test_watchdog_death.py
"""

import os
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ⚠ BEFORE the first orgtree import — `store` resolves ORGTREE_DATA at import
# time, so a root set afterwards leaves an env var that says "isolated" and a
# module pointed at production.
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-wddeath-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    # the discard port: nothing here may reach the operator's real mail hub
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

import _no_deploy                                                # noqa: E402
from orgtree import store, supervisor                            # noqa: E402
from orgtree.ledger import USER                                  # noqa: E402

# ☠ this suite RUNS the real engine, which spawns real processes and can WAKE
# real agents. Both interlocks, armed before any check.
_no_deploy.install()
_no_deploy.assert_isolated_data_root()

_HERE = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
_GOT = os.path.realpath(os.path.dirname(os.path.dirname(supervisor.__file__)))
if _GOT != _HERE:
    raise SystemExit(
        f"☠ REFUSING TO RUN: this suite lives under {_HERE!r} but imported "
        f"orgtree from {_GOT!r}. Every number it printed would be about a "
        f"different checkout. Clear PYTHONPATH and run it again.")
print(f"testing orgtree at: {_GOT}")

PASS = 0
FAIL: list[str] = []
WIN = os.name == "nt"


def check(label, fn):
    global PASS
    try:
        fn()
    except Exception as e:                                       # noqa: BLE001
        FAIL.append(f"{label}: {e}")
        print(f"  FAIL  {label}\n        {e}")
        return
    PASS += 1
    print(f"  ok    {label}")


def aged(seconds):
    import datetime as dtm
    t = dtm.datetime.now(dtm.timezone.utc) - dtm.timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


#: ⚠ a NEUTRAL target string, not `__file__`. The first version used this
#: file's own path and §1 failed inside the worktree `wt-deaddog`, because a
#: check looking for the word "dead" in a message found it in the DIRECTORY
#: NAME. A check that reads text as data has to be given text it controls —
#: which is D-158's subject, caught by D-158's own suite style.
FAKE_LOG = "/var/log/tier.log"


def dog(**over):
    d = {"id": "w1", "owner": "k", "name": "d", "kind": "file",
         "target": FAKE_LOG, "state": "armed", "at": aged(4.5 * 3600),
         "fired": 0, "checks_run": 535, "events": []}
    d.update(over)
    return d


def stale_hw(quiet=99, since=4000):
    """A high-water mark that has ALREADY crossed both thresholds, so a check
    can reach the decision site without sleeping an hour. The thresholds
    themselves are exercised as thresholds in §2."""
    return {"quiet": quiet, "alive_at": aged(since)}


# ---------------------------------------------------------------------------
print("\n§1 · THE MEASUREMENT THAT SHAPED THE FIX")
# The whole design rests on one claim: the signal that already existed cannot
# tell a dead producer from a live one, so it could not simply be mailed out.
# If that claim is false the fix is over-engineering, so it is asserted here
# first, as a control pair, against the REAL shipped function.


def _the_old_signal_cannot_discriminate_and_the_new_one_can():
    seen_dead = "(tier.log is 918273 bytes; +0 new byte(s) this check, 0 matched)"
    seen_live = "(tier.log is 918273 bytes; +4096 new byte(s) this check, 0 matched)"
    # ① THE DOGS EXACTLY AS THEY WERE BEFORE THIS CHANGE — no `quiet`, no
    #    `alive_at`, because those fields did not exist. That is the honest
    #    reconstruction: a doc written yesterday has neither.
    was_dead = dog(last_output=seen_dead)
    # SAME age, SAME check count. The ONLY difference is that this file is
    # demonstrably still being written to.
    was_live = dog(last_output=seen_live)
    h_dead, h_live = supervisor.wd_health(was_dead), supervisor.wd_health(was_live)
    assert h_dead and "NEVER matched" in h_dead, \
        f"the pre-existing abstention note no longer fires at all: {h_dead!r}"
    # ⚠ compare the DIAGNOSIS, not the raw evidence the note echoes after it.
    # The two dogs' `last_output` strings differ by construction — that is the
    # data the old note was already carrying and nobody read. The claim under
    # test is that the SENTENCE, the part that says what is wrong, is word for
    # word the same for a corpse and for a healthy producer.
    def verdict(h):
        return (h or "").split("`last_output`")[0].strip()
    assert verdict(h_dead) == verdict(h_live), (
        "THE PREMISE OF THIS WHOLE SUITE IS THAT THE OLD SIGNAL CANNOT "
        "DISCRIMINATE. It now answers differently for a dead producer and a "
        f"growing one, so the design should be revisited.\n dead={h_dead!r}\n "
        f"live={h_live!r}")
    assert "NEVER matched" in verdict(h_live), verdict(h_live)

    # ② the new predicate separates the same two. Both directions, or it
    #    proves nothing: an always-None detector passes a one-sided test.
    dead = dog(last_output=seen_dead, high_water=stale_hw())
    live = dog(last_output=seen_live,
               high_water={"quiet": 0, "alive_at": aged(2)})
    lost_dead = supervisor.wd_subject_lost(dead)
    lost_live = supervisor.wd_subject_lost(live)
    assert lost_dead, f"the dead producer was not detected at all: {dead!r}"
    assert lost_dead["why"] == "stale", lost_dead
    assert lost_live is None, (
        "A FILE THAT IS ACTIVELY GROWING WAS REPORTED AS A DEAD PRODUCER. "
        "This is the false alarm the design exists to avoid — shipping it "
        f"would train every agent here to ignore the signal. {lost_live!r}")

    # ③ …and `list` now shows the discriminating sentence, not the vague
    #    one — one predicate behind two surfaces.
    assert "not grown" in (supervisor.wd_health(dead) or ""), (
        "`list` still shows the undiscriminating sentence for a dog whose "
        f"subject is gone: {supervisor.wd_health(dead)!r}")
    assert supervisor.wd_health(live) != supervisor.wd_health(dead), \
        "the two are still indistinguishable in `list`"


check("§1 the OLD note says the same thing about a dead producer and a "
      "growing one; the NEW predicate tells them apart (control pair)",
      _the_old_signal_cannot_discriminate_and_the_new_one_can)


def _staleness_never_claims_death():
    lost = supervisor.wd_subject_lost(dog(high_water=stale_hw()))
    assert lost and not lost["pause"], \
        f"a file dog was paused on a suspicion: {lost!r}"
    low = (lost["headline"] + " " + lost["advice"]).lower()
    assert "staleness" in low, (
        "the file-dog message must say STALENESS in its own words — the "
        f"honest limit is the deliverable here: {lost!r}")
    for word in ("died", "dead", "is gone"):
        assert word not in lost["headline"].lower(), (
            f"the headline claims death it cannot observe ({word!r}): "
            f"{lost['headline']!r}")


check("§1 a file dog's report is STALENESS in words, never a death claim, "
      "and never pauses the dog",
      _staleness_never_claims_death)


# ---------------------------------------------------------------------------
print("\n§2 · THE COUNTER IS IN OBSERVATIONS, NOT WALL TIME")
# This is the property that makes an orgtree restart harmless, and it is the
# one a future "simplification" would delete. A restart produces exactly the
# state below: a long wall-clock gap with NO checks in it. If wall time alone
# could trip the detector, every deploy would report every dog as dead.


def _wall_time_alone_never_trips_it():
    # the restart's own signature: hours since the last sign of life, but the
    # engine was DOWN for them, so barely any checks happened
    restart_gap = dog(high_water={"quiet": 3, "alive_at": aged(9 * 3600)})
    assert supervisor.wd_subject_lost(restart_gap) is None, (
        "AN ORGTREE OUTAGE WOULD BE REPORTED AS A DEAD PRODUCER. This is the "
        "user's constraint, and the failure it names: a nine-hour gap with "
        "only three checks in it is a downed watcher, not a dead subject. "
        f"{restart_gap!r}")
    # …and the mirror: many checks, but the subject was alive moments ago
    busy = dog(high_water={"quiet": 500, "alive_at": aged(4)})
    assert supervisor.wd_subject_lost(busy) is None, \
        f"a subject seen alive 4s ago was reported lost: {busy!r}"
    # only BOTH together
    both = dog(high_water={"quiet": supervisor._WD_STALE_CHECKS,
                           "alive_at": aged(supervisor._WD_STALE_AGE_S + 60)})
    assert supervisor.wd_subject_lost(both), \
        f"the detector cannot fire even at its own thresholds: {both!r}"


check("§2 hours of silence with no checks in them is NOT staleness (the "
      "restart's signature); both counters must cross (control pair)",
      _wall_time_alone_never_trips_it)


def _life_resets_the_counter_and_re_arms_the_alert():
    hw = {"quiet": 99, "alive_at": aged(9999)}
    supervisor._wd_note_life(hw, False)
    assert hw["quiet"] == 100, hw
    supervisor._wd_note_life(hw, True)
    assert hw["quiet"] == 0, f"a sign of life did not reset the counter: {hw}"
    assert supervisor._wd_age_s(hw["alive_at"]) < 5, hw


check("§2 a sign of life zeroes the silence counter and restamps it",
      _life_resets_the_counter_and_re_arms_the_alert)


# ---------------------------------------------------------------------------
print("\n§3 · FILE DOGS, THROUGH THE REAL ENGINE — the death-shaped canary")
# The canary the coordinator required: kill a producer for real, run the REAL
# `_wd_tick`, and require the alert to ARRIVE and to carry context. Its
# control is the same dog over a file that is still being written.


def _tick_pair(seed_hw, writer_appends):
    """One real tick over a file dog whose high-water is pre-aged. Returns
    (dog after, wakes, mailbox). `writer_appends` decides whether the
    'producer' is alive during the tick — the only difference between the
    canary and its control."""
    _no_deploy.install_no_turn_spawn()
    _no_deploy.WAKES.clear()
    o = store.create_org("zz wd death file")
    d = tempfile.mkdtemp(prefix="wddeath-")
    log = os.path.join(d, "tier.log")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write("suite 1 ok\n")
    try:
        slug = o.d["slug"]
        o.hire(USER, None, "haiku", 5, "k", add_dirs=[{"path": d}],
               tools={"bash": True, "web": False, "edit": False,
                      "subagents": False, "mcp": []},
               org_visibility="team", charter="file dog fixture")
        w = o.watchdog_create("k", "tier", "file", log, "RUN COMPLETE", 15)
        wid = w["id"]
        # pre-age it past both thresholds; `_last_check_ts` cleared so the
        # tick treats it as due
        wd = o._watchdog(wid)
        # ⚠ `off` must be the file's CURRENT size or the first check reads the
        # whole existing file, counts that as growth, and the fixture silently
        # measures a healthy dog. That is how this canary first passed for the
        # wrong reason.
        wd["high_water"] = dict(seed_hw, off=os.path.getsize(log))
        wd["_last_check_ts"] = 0
        wd["checks_run"] = 535
        wd["at"] = aged(4.5 * 3600)
        store.save_org(o)
        if writer_appends:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write("suite 2 ok\n")
        supervisor._wd_tick()
        o2 = store.load_org(slug)
        box = [m for m in (o2.d.get("mail", {}).get("k") or [])]
        return dict(o2._watchdog(wid)), list(_no_deploy.WAKES), box, log
    finally:
        supervisor.send_message = _no_deploy._REAL_SEND_MESSAGE
        try:
            store.delete_org(o.d["slug"])
        except Exception:                                    # noqa: BLE001
            pass


def _a_dead_producer_wakes_its_owner_with_the_facts():
    after, wakes, box, log = _tick_pair(stale_hw(), writer_appends=False)

    # ① the alert happened at all
    assert after.get("alerted_why") == "stale", (
        "THE DEAD PRODUCER WAS NOT REPORTED. This is the ninety minutes of "
        f"idle waiting, unfixed: {after!r}")
    # ② it WOKE the owner — a notice would land in a mailbox that an idle
    #    agent never opens, which is the whole defect
    assert any(nid == "k" and woke for _s, nid, _t, woke in wakes), (
        f"the owner was not woken; a notice cannot reach an idle agent. "
        f"WAKES={wakes!r}")
    # ③ THE CONTEXT IS THE DELIVERABLE — "your watchdog stopped" teaches an
    #    agent nothing it can act on
    assert box, "nothing was put in the owner's mailbox"
    body = box[-1]["body"]
    for must in (log, "checks run", "silent for", "last written",
                 "STALENESS", "consecutive checks"):
        assert must in body, (
            f"the alert does not carry {must!r} — the context IS the fix, "
            f"not the notification. body={body!r}")
    assert "535" in body or "536" in body, \
        f"the alert does not say how many checks it ran: {body!r}"
    # ④ …and it says, in the mail itself, that this is not about a restart
    assert "not about orgtree" in body.lower() or "restarts and deploys" \
        in body.lower(), f"the alert invites the restart misreading: {body!r}"
    # ⑤ the dog is LEFT ARMED — a suspicion may not disarm an instrument
    assert after["state"] == "armed", (
        "a file dog was disarmed on staleness. A false positive here "
        f"destroys a working instrument: {after!r}")


def _a_live_producer_is_left_alone():
    after, wakes, box, _log = _tick_pair(stale_hw(), writer_appends=True)
    assert "alerted_why" not in after, (
        "A FILE THAT GREW DURING THE CHECK WAS REPORTED AS A DEAD PRODUCER — "
        f"the false alarm that would make the signal worthless: {after!r}")
    assert int((after.get("high_water") or {}).get("quiet") or 0) == 0, \
        f"growth did not reset the silence counter: {after!r}"
    assert not [w for w in wakes if w[1] == "k"], \
        f"a healthy dog woke its owner: {wakes!r}"
    assert not box, f"a healthy dog mailed its owner: {box!r}"


check("§3 CANARY (death-shaped): a killed producer wakes its owner with the "
      "file's size, mtime, check count and silence — and leaves it ARMED",
      _a_dead_producer_wakes_its_owner_with_the_facts)
check("§3 CONTROL: the same aged dog over a file that GREW during the tick "
      "says nothing at all",
      _a_live_producer_is_left_alone)


def _the_alert_does_not_repeat_every_interval():
    """Once per episode. An alert that arrives every 15s is an alert that gets
    filtered, and then the next real one is invisible."""
    _no_deploy.install_no_turn_spawn()
    _no_deploy.WAKES.clear()
    o = store.create_org("zz wd death once")
    d = tempfile.mkdtemp(prefix="wddeath-once-")
    log = os.path.join(d, "q.log")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write("x\n")
    try:
        slug = o.d["slug"]
        o.hire(USER, None, "haiku", 5, "k", add_dirs=[{"path": d}],
               tools={"bash": True, "web": False, "edit": False,
                      "subagents": False, "mcp": []},
               org_visibility="team", charter="once fixture")
        wid = o.watchdog_create("k", "q", "file", log, "NEVER", 15)["id"]
        wd = o._watchdog(wid)
        wd["high_water"] = dict(stale_hw(), off=os.path.getsize(log))
        wd["_last_check_ts"] = 0
        store.save_org(o)
        for _ in range(3):
            o3 = store.load_org(slug)
            o3._watchdog(wid)["_last_check_ts"] = 0
            store.save_org(o3)
            supervisor._wd_tick()
        o2 = store.load_org(slug)
        box = o2.d.get("mail", {}).get("k") or []
        assert len(box) == 1, (
            f"the alert repeated on every tick ({len(box)} copies) — it will "
            f"be filtered, and then the next real one is invisible")
        # …and it comes back if the subject revives and dies again
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("alive again\n")
        o3 = store.load_org(slug)
        o3._watchdog(wid)["_last_check_ts"] = 0
        store.save_org(o3)
        supervisor._wd_tick()
        o4 = store.load_org(slug)
        assert "alerted_why" not in o4._watchdog(wid), (
            "the subject came back to life and the alert did not re-arm — a "
            "log that goes quiet twice must be reported twice")
    finally:
        supervisor.send_message = _no_deploy._REAL_SEND_MESSAGE
        try:
            store.delete_org(o.d["slug"])
        except Exception:                                    # noqa: BLE001
            pass


check("§3 the alert is sent ONCE per episode and re-arms when the subject "
      "revives (control pair)",
      _the_alert_does_not_repeat_every_interval)


# ---------------------------------------------------------------------------
print("\n§4 · THE RESTART-SHAPED CANARY — the user's constraint")
# "make sure this doesnt conflict with orgtree shutdowns and restarts, which
# would also kill watchdog watcher processes, but shouldnt remove the dogs".
# So this reproduces a restart as faithfully as a test can: the engine's
# in-memory tables are dropped, the watcher children are killed, wall-clock
# time passes — and then the engine comes back. NOTHING may be removed,
# paused, or reported.


def _a_restart_removes_nothing_and_reports_nothing():
    _no_deploy.install_no_turn_spawn()
    _no_deploy.WAKES.clear()
    o = store.create_org("zz wd death restart")
    d = tempfile.mkdtemp(prefix="wddeath-restart-")
    log = os.path.join(d, "healthy.log")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write("line 1\n")
    real_streams = dict(supervisor._wd_streams)
    try:
        slug = o.d["slug"]
        o.hire(USER, None, "haiku", 5, "k", add_dirs=[{"path": d}],
               tools={"bash": True, "web": False, "edit": False,
                      "subagents": False, "mcp": []},
               org_visibility="team", charter="restart fixture")
        f_id = o.watchdog_create("k", "f", "file", log, "BOOM", 15)["id"]
        p_id = o.watchdog_create("k", "p", "process",
                                 f"pid:{os.getpid()}", None, 15)["id"]
        store.save_org(o)
        supervisor._wd_tick()          # establish high-water on both

        # ---- THE RESTART. Every watcher process on the machine dies and the
        # engine's memory goes with it; the doc is all that survives. This is
        # exactly the state a deploy leaves behind.
        for key in list(supervisor._wd_streams):
            supervisor._wd_reap_stream(key)
        supervisor._wd_streams.clear()
        # …and hours pass with NO checks in them, which is what an outage is
        o2 = store.load_org(slug)
        for wid in (f_id, p_id):
            w = o2._watchdog(wid)
            w["_last_check_ts"] = 0
            hw = dict(w.get("high_water") or {})
            hw["alive_at"] = aged(9 * 3600)      # the gap the outage created
            w["high_water"] = hw
        store.save_org(o2)
        _no_deploy.WAKES.clear()

        supervisor._wd_tick()          # the engine comes back up

        o3 = store.load_org(slug)
        for wid, what in ((f_id, "file"), (p_id, "process")):
            w = o3._watchdog(wid)
            assert w is not None, f"the {what} dog was REMOVED by a restart"
            assert w["state"] == "armed", (
                f"THE {what.upper()} DOG WAS {str(w['state']).upper()} BY A "
                f"RESTART. A dog whose whole point is outliving a restart "
                f"cannot disarm itself on one: {w!r}")
            assert "alerted_why" not in w, (
                f"the {what} dog reported a failure caused by orgtree's own "
                f"downtime — this is the false alarm the user ruled out: "
                f"{w!r}")
        assert not [w for w in _no_deploy.WAKES if w[1] == "k"], (
            f"a restart woke an agent to tell it about a restart: "
            f"{_no_deploy.WAKES!r}")
        assert not (o3.d.get("mail", {}).get("k") or []), \
            "a restart put mail in an owner's box"
    finally:
        supervisor._wd_streams.clear()
        supervisor._wd_streams.update(real_streams)
        supervisor.send_message = _no_deploy._REAL_SEND_MESSAGE
        try:
            store.delete_org(o.d["slug"])
        except Exception:                                    # noqa: BLE001
            pass


check("§4 CANARY (restart-shaped): watchers killed, engine memory dropped, "
      "a nine-hour gap — nothing removed, nothing paused, nobody woken",
      _a_restart_removes_nothing_and_reports_nothing)


def _the_restart_canary_can_actually_fail():
    """☠ THE CANARY'S OWN CANARY. §4 asserts a list of things that did NOT
    happen, and an all-absences check passes just as well when the detector is
    disconnected entirely. So: take the same dog, give it the state a real
    dead subject leaves — SILENT CHECKS, not merely elapsed time — and require
    the detector to speak. If this fails, §4's silence proved nothing."""
    only_a_gap = dog(high_water={"quiet": 3, "alive_at": aged(9 * 3600)})
    real_death = dog(high_water={"quiet": 200, "alive_at": aged(9 * 3600)})
    assert supervisor.wd_subject_lost(only_a_gap) is None
    assert supervisor.wd_subject_lost(real_death), (
        "the detector is silent even for a subject that went silent across "
        "two hundred REAL checks — §4's clean sheet is a broken instrument, "
        "not a passing test")


check("§4 …and the same fixture DOES report a subject that went silent "
      "across real checks — so §4's silence is a result, not a dead check",
      _the_restart_canary_can_actually_fail)


# ---------------------------------------------------------------------------
print("\n§5 · COMMAND DOGS — 'cannot run at all', not 'returned non-zero'")


def _a_waiting_findstr_is_healthy_not_broken():
    """The control that decides whether this is safe to ship. `findstr` that
    matches nothing exits 1 EVERY time — that is a working dog waiting, and
    treating a non-zero exit as failure would pause every healthy command dog
    on the machine."""
    waiting = dog(kind="command", target="findstr /C:\"X\" y.log",
                  pattern="X", last_exit=1,
                  last_output="", high_water={"quiet": 0,
                                              "alive_at": aged(5)})
    assert supervisor.wd_subject_lost(waiting) is None, (
        "A HEALTHY COMMAND DOG WAS DECLARED BROKEN because its target exits "
        f"non-zero while it waits. This would pause working dogs: {waiting!r}")
    broken = dog(kind="command", target="grep X y.log", pattern="X",
                 high_water={"broken": supervisor._WD_BROKEN_STREAK,
                             "quiet": 9, "alive_at": aged(600)})
    lost = supervisor.wd_subject_lost(broken)
    assert lost and lost["why"] == "broken", \
        f"a target the shell cannot run at all was not detected: {broken!r}"
    assert lost["pause"], "a dog that can never fire was left armed"
    assert "removed" in lost["advice"], \
        f"the advice does not explain why it is paused not removed: {lost!r}"


def _one_bad_check_is_not_a_streak():
    for n in range(supervisor._WD_BROKEN_STREAK):
        d = dog(kind="command", target="grep X y", pattern="X",
                high_water={"broken": n, "quiet": n, "alive_at": aged(600)})
        assert supervisor.wd_subject_lost(d) is None, (
            f"a command dog was paused after only {n} bad check(s) — a "
            f"transient must not disarm an instrument: {d!r}")


check("§5 a `findstr` exiting 1 while it waits is HEALTHY; a target the shell "
      "cannot run at all is BROKEN and paused (control pair)",
      _a_waiting_findstr_is_healthy_not_broken)
check("§5 …and it takes a STREAK, so one transient failure disarms nothing",
      _one_bad_check_is_not_a_streak)


def _the_broken_streak_is_really_counted_by_the_engine():
    """Through the real command path, not the predicate alone: the streak has
    to be written by `_wd_cmd_submit`'s done-callback or the detector above is
    reading a field nothing ever sets."""
    from concurrent.futures import ThreadPoolExecutor          # noqa: PLC0415
    _no_deploy.install_no_turn_spawn()
    _no_deploy.WAKES.clear()
    o = store.create_org("zz wd death cmd")
    real_pool = supervisor._wd_cmd_pool
    real_path = os.environ.get("PATH", "")
    try:
        slug = o.d["slug"]
        o.hire(USER, None, "haiku", 5, "k", add_dirs=[],
               tools={"bash": True, "web": False, "edit": False,
                      "subagents": False, "mcp": []},
               org_visibility="team", charter="cmd fixture")
        # the idiom that cannot run under the service PATH on Windows
        wid = o.watchdog_create("k", "bad", "command",
                                "cat nothing | grep X", "X", 15)["id"]
        store.save_org(o)
        supervisor._wd_cmd_pool = ThreadPoolExecutor(max_workers=2)
        if WIN:
            os.environ["PATH"] = os.path.join(
                os.environ.get("SystemRoot", r"C:\Windows"), "system32")
        for _ in range(supervisor._WD_BROKEN_STREAK + 1):
            o3 = store.load_org(slug)
            o3._watchdog(wid)["_last_check_ts"] = 0
            store.save_org(o3)
            supervisor._wd_tick()
            deadline = time.time() + 60
            while time.time() < deadline:
                if not supervisor._wd_cmd_inflight:
                    break
                time.sleep(0.2)
            time.sleep(0.3)
        w = store.load_org(slug)._watchdog(wid)
        if not WIN:
            return                       # `cat|grep` runs fine on POSIX
        assert int((w.get("high_water") or {}).get("broken") or 0) >= \
            supervisor._WD_BROKEN_STREAK, (
            "the engine never counted the broken checks, so the detector "
            f"reads a field nothing writes: {w!r}")
        assert w["state"] == "paused", \
            f"a dog that can never fire was left armed by the engine: {w!r}"
        assert "could not be run" in str(w.get("paused_why") or ""), \
            f"the pause does not say why: {w.get('paused_why')!r}"
        box = store.load_org(slug).d.get("mail", {}).get("k") or []
        assert box and "could not be run" in box[-1]["body"].lower(), (
            f"the dog was paused and NOBODY WAS TOLD — that turns a wait "
            f"into a permanent AND invisible one: {box!r}")
        assert "not recognized" in box[-1]["body"].lower(), (
            "the alert does not carry what the shell actually said, which is "
            f"the only thing that tells the owner how to fix it: {box[-1]!r}")
    finally:
        os.environ["PATH"] = real_path
        try:
            supervisor._wd_cmd_pool.shutdown(wait=True)
        except Exception:                                    # noqa: BLE001
            pass
        supervisor._wd_cmd_pool = real_pool
        supervisor.send_message = _no_deploy._REAL_SEND_MESSAGE
        try:
            store.delete_org(o.d["slug"])
        except Exception:                                    # noqa: BLE001
            pass


check("§5 the engine itself counts the streak, pauses the dog and MAILS the "
      "reason (a silent pause is worse than the bug)",
      _the_broken_streak_is_really_counted_by_the_engine)


# ---------------------------------------------------------------------------
print("\n§6 · PROCESS DOGS — already correct, and now proved")
# The honest deliverable for this kind was a canary, not a rewrite. Two things
# to prove: the DOWN edge still survives a restart (it lives in the doc), and
# a `pid:` dog that has already fired stops pretending to guard anything.


def _the_down_edge_survives_a_restart():
    o = store.create_org("zz wd death proc")
    try:
        slug = o.d["slug"]
        o.hire(USER, None, "haiku", 5, "k", add_dirs=[],
               tools={"bash": True, "web": False, "edit": False,
                      "subagents": False, "mcp": []},
               org_visibility="team", charter="proc fixture")
        # a real child we can kill: alive now, dead in a moment
        p = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
        wid = o.watchdog_create("k", "p", "process", f"pid:{p.pid}",
                                None, 15)["id"]
        store.save_org(o)
        supervisor._wd_tick()                       # sees it UP
        up = (store.load_org(slug)._watchdog(wid).get("high_water") or {})
        assert up.get("up") is True, f"the dog never saw its subject UP: {up}"
        # the subject dies WHILE ORGTREE IS DOWN — the ambiguous middle
        p.kill()
        p.wait(timeout=10)
        supervisor._wd_streams.clear()              # the restart
        o2 = store.load_org(slug)
        o2._watchdog(wid)["_last_check_ts"] = 0
        store.save_org(o2)
        _no_deploy.install_no_turn_spawn()
        _no_deploy.WAKES.clear()
        supervisor._wd_tick()                       # the engine returns
        w = store.load_org(slug)._watchdog(wid)
        assert int(w.get("fired") or 0) >= 1, (
            "a subject that died during the restart window was never "
            f"reported — the DOWN edge did not survive the restart: {w!r}")
    finally:
        supervisor.send_message = _no_deploy._REAL_SEND_MESSAGE
        try:
            store.delete_org(o.d["slug"])
        except Exception:                                    # noqa: BLE001
            pass


def _a_spent_pid_dog_stops_pretending_to_guard():
    """Found live: my own `pid:57332` dog had fired its DOWN edge on 2026-08-27
    and then run 2,412 further checks against a pid that is permanently gone,
    reporting `health: ok`. A pid does not come back, so that dog could never
    fire again — and if the OS recycled the number it would fire about a
    stranger."""
    spent = dog(kind="process", target="pid:57332", fired=1,
                high_water={"up": False, "quiet": 2412,
                            "alive_at": aged(20 * 3600)})
    lost = supervisor.wd_subject_lost(spent)
    assert lost and lost["why"] == "spent", \
        f"a spent pid dog still reports itself healthy: {spent!r}"
    assert lost["pause"], "a spent dog was left armed"
    # ⚠ a PORT is not a pid: a service restarting brings its port back, which
    # is most of why port dogs exist. This is the control that keeps the rule
    # from over-reaching.
    port = dog(kind="process", target="port:7401", fired=1,
               high_water={"up": False, "quiet": 2412,
                           "alive_at": aged(20 * 3600)})
    assert supervisor.wd_subject_lost(port) is None, (
        "a PORT dog was declared spent — a port comes back when its service "
        f"restarts, and that is what the dog is for: {port!r}")
    # and one that has NOT yet fired is still waiting for its edge
    fresh = dog(kind="process", target="pid:1", fired=0,
                high_water={"up": False, "quiet": 2412,
                            "alive_at": aged(20 * 3600)})
    assert supervisor.wd_subject_lost(fresh) is None, fresh


check("§6 a subject that dies during the restart window is still caught on "
      "the first tick back (the DOWN edge lives in the doc)",
      _the_down_edge_survives_a_restart)
check("§6 a spent `pid:` dog is paused; a `port:` dog with identical numbers "
      "is NOT, because a port comes back (control pair)",
      _a_spent_pid_dog_stops_pretending_to_guard)


# ---------------------------------------------------------------------------
print("\n§7 · STREAM DOGS — already correct, and now proved")


def _a_stream_that_exits_is_reported_and_a_restart_respawns():
    _no_deploy.install_no_turn_spawn()
    _no_deploy.WAKES.clear()
    o = store.create_org("zz wd death stream")
    try:
        slug = o.d["slug"]
        o.hire(USER, None, "haiku", 5, "k", add_dirs=[],
               tools={"bash": True, "web": False, "edit": False,
                      "subagents": False, "mcp": []},
               org_visibility="team", charter="stream fixture")
        # a "listener" that exits almost at once — the producer dying
        wid = o.watchdog_create("k", "s", "stream", "echo one", "one",
                                5)["id"]
        store.save_org(o)
        deadline = time.time() + 60
        while time.time() < deadline:
            supervisor._wd_tick()
            if store.load_org(slug)._watchdog(wid).get("state") == "exited":
                break
            time.sleep(0.5)
        w = store.load_org(slug)._watchdog(wid)
        assert w["state"] == "exited", (
            "a stream dog whose command exited is still reported as armed — "
            f"the claim that this kind is already correct is false: {w!r}")
        assert w.get("exit", {}).get("code") is not None, \
            f"the exit was recorded without its code: {w!r}"
        box = store.load_org(slug).d.get("mail", {}).get("k") or []
        assert any("STREAM EXITED" in m["body"] for m in box), (
            f"the owner was never told its listener died: {box!r}")

        # ---- and the restart case: the engine's memory is empty after a
        # restart, which must mean RE-SPAWN, never "it exited".
        o2 = store.load_org(slug)
        w2 = o2._watchdog(wid)
        w2["state"] = "armed"
        w2.pop("exit", None)
        store.save_org(o2)
        supervisor._wd_streams.clear()               # the restart
        supervisor._wd_tick()
        w3 = store.load_org(slug)._watchdog(wid)
        assert w3["state"] == "armed", (
            "an empty in-memory stream table was read as 'the child exited' "
            f"— every restart would report every stream dog dead: {w3!r}")
    finally:
        for key in list(supervisor._wd_streams):
            supervisor._wd_reap_stream(key)
        supervisor.send_message = _no_deploy._REAL_SEND_MESSAGE
        try:
            store.delete_org(o.d["slug"])
        except Exception:                                    # noqa: BLE001
            pass


check("§7 a stream whose command exits is reported with its code; a restart "
      "re-spawns instead of reporting a false exit (control pair)",
      _a_stream_that_exits_is_reported_and_a_restart_respawns)


# ---------------------------------------------------------------------------
print("\n§8 · WHOSE PROCESS TREE A DOG'S CHILD IS IN")
# The second half of the task: "make sure watchdog processes are actually
# detached and running in an execution context divorced from the turn that
# spawned them". The answer is that they already are — the engine is a daemon
# thread in the BACKEND, so a dog's child is the backend's child and not the
# arming turn's. What was NOT true is that killing one killed what it started.


def _children_belong_to_the_spawning_backend_not_to_a_cli():
    class _O:
        d = {"slug": "zz-wdtree", "key": None}
    # ⚠ a target that STAYS ALIVE. The first version used `echo hi`, which had
    # already exited before the query ran, so the lookup returned "" and the
    # check reported the wrong tree rather than no tree. An instrument must be
    # able to see its subject before its answer means anything.
    proc = supervisor._wd_popen(_O(), "k", "ping -n 100000 127.0.0.33")  # type: ignore[arg-type]
    try:
        # the only structural fact a test can assert here, and it is the one
        # that matters: the child is OURS. In production this code runs on the
        # backend's watchdog thread, so "ours" is the backend — not the CLI of
        # whichever turn armed the dog, which is a different process that the
        # harness kills at every turn boundary.
        if WIN:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-CimInstance Win32_Process -Filter "
                 f"'ProcessId={proc.pid}').ParentProcessId"],
                capture_output=True, text=True, timeout=60).stdout.strip()
            assert out, (
                f"the watchdog child {proc.pid} was already gone when the "
                f"tree was queried — this check measured nothing")
            assert out == str(os.getpid()), (
                f"a watchdog child's parent is {out!r}, not the process that "
                f"spawned it ({os.getpid()}) — it is in somebody else's tree")
    finally:
        supervisor._wd_kill_tree(proc)


def _killing_the_shell_is_not_enough_but_kill_tree_is():
    """☠ THE MEASURED BUG. `_wd_popen` runs the target through `cmd.exe /c`,
    so `proc.kill()` kills the SHELL and leaves the target running. Found on
    the live box: a create-time smoke run of `ping -n 100000` was killed after
    its 8s timeout and the PING was still going afterwards, orphaned, good for
    another 27 hours — one leaked per create.

    The negative control comes FIRST and must show the leak, or the positive
    half proves nothing about `_wd_kill_tree`."""
    if not WIN:
        return                      # the grandchild shape is the cmd.exe one

    class _O:
        d = {"slug": "zz-wdtree", "key": None}

    def grandchildren(marker):
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "@(Get-CimInstance Win32_Process -Filter \"Name='PING.EXE'\" | "
             f"Where-Object {{ $_.CommandLine -like '*{marker}*' }}).Count"],
            capture_output=True, text=True, timeout=60).stdout.strip()
        return int(out or 0)

    # ① NEGATIVE CONTROL — the old kill, and the leak it leaves
    leak_ip, tree_ip = "127.0.0.31", "127.0.0.32"
    a = supervisor._wd_popen(_O(), "k", f"ping -n 100000 {leak_ip}")  # type: ignore[arg-type]
    b = supervisor._wd_popen(_O(), "k", f"ping -n 100000 {tree_ip}")  # type: ignore[arg-type]
    try:
        deadline = time.time() + 30
        while time.time() < deadline and not (grandchildren(leak_ip)
                                              and grandchildren(tree_ip)):
            time.sleep(0.5)
        assert grandchildren(leak_ip) and grandchildren(tree_ip), \
            "neither target ever started — this check measured nothing"
        a.kill()                       # the old behaviour, exactly
        a.wait(timeout=10)
        time.sleep(2)
        assert grandchildren(leak_ip) >= 1, (
            "killing the shell already killed the target, so the bug this "
            "fixes does not exist on this machine and the check below is "
            "vacuous")
        # ② and the fix
        supervisor._wd_kill_tree(b)
        deadline = time.time() + 20
        while time.time() < deadline and grandchildren(tree_ip):
            time.sleep(0.5)
        assert grandchildren(tree_ip) == 0, (
            "_wd_kill_tree left the target running — every smoke run of a "
            "long-lived target still leaks a process")
    finally:
        for ip, p in ((leak_ip, a), (tree_ip, b)):
            supervisor._wd_kill_tree(p)
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process -Filter \"Name='PING.EXE'\" | "
                 f"Where-Object {{ $_.CommandLine -like '*{ip}*' }} | "
                 "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
                 "-ErrorAction SilentlyContinue }"],
                capture_output=True, text=True, timeout=60)


check("§8 a watchdog child is a child of the process that spawned it — the "
      "backend, never the arming turn's CLI",
      _children_belong_to_the_spawning_backend_not_to_a_cli)
check("§8 killing the shell LEAVES the target running (the measured leak); "
      "_wd_kill_tree does not (control pair)",
      _killing_the_shell_is_not_enough_but_kill_tree_is)


# ---------------------------------------------------------------------------
print(f"\n{PASS} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print(f"  · {f}")
    sys.exit(1)
print(f"\nALL {PASS} CHECKS PASS")
