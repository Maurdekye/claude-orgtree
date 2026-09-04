"""⏹ STOP ALL pauses every watchdog, so nothing wakes an agent back up.

WHY. STOP ALL used to call `interrupt_all` and nothing else. It interrupted
every agent and cleared every queue — and then the next watchdog to fire mailed
its owner with `wake=True` and the org was running again. An operator who hits
an emergency stop and watches an agent start talking two minutes later has not
been given a killswitch. (User request 2026-09-04: "it should also pause all
watchdogs immediately to prevent any agents from waking".)

USER RULING 2026-09-04, and it is the whole shape of this feature: "nothing
unpauses them automatically; its an emergency killswitch. it should be expected
that the effects could be a little destructive. the only thing that can unpause
the paused dogs is either manually visiting each one and resuming it, or telling
the agents … to unpause all their paused dogs."

⚠ SO THE PROPERTY UNDER TEST IS AN ABSENCE — "no agent woke" — AND AN ABSENCE
IS THIS REPO'S STANDING FAILURE SHAPE. A test that asserts no wake happened,
in a rig where no wake could ever have happened, passes for ever and guards
nothing. Every check here that asserts silence is therefore paired with a
POSITIVE CONTROL in the same function: the identical rig, minus the stop,
proving the wake it is looking for really does occur.

    §1  the pause — what STOP ALL now does to the dogs
    §2  the wake — the absence, each one with its own positive control
    §3  the in-flight race — a fire already committed when the stop lands
    §4  no automatic resume — restart, rehire, and the reason string
    §5  scope — the stop is the BUTTON's, not every caller's
    §6  the two exits the ruling names — manual, per-dog, and agent-usable

Hermetic: in-memory orgs plus one real subprocess for the restart check. No
port, no CLI, no network, no live provider.

    python backend/tests/test_killswitch_watchdogs.py [-v]
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-killswitch-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
# ⚠ same hub trap as test_present §1: a throwaway ORGTREE_DATA does NOT isolate
# the mail hub — it falls back to the operator's real one without this.
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
os.environ["USERPROFILE"] = os.environ["HOME"] = _TMP

from orgtree import store                                        # noqa: E402
from orgtree import supervisor as S                              # noqa: E402
from orgtree.ledger import Org, USER                             # noqa: E402

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


def _true(cond, msg) -> None:
    if not cond:
        raise AssertionError(msg)


_n = [0]


class Woke:
    """Records every wake `_wd_fire` asks for, by patching the real seam.

    ⚠ PATCHED AT `S.send_message`, which is what `_wd_fire` actually calls to
    start a turn. Asserting on anything further out (mail in the box, a
    notify) would not distinguish "the event was recorded" from "an agent was
    STARTED", and the whole point of the stop is that the first may happen and
    the second may not.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []
        self._orig = None

    def __enter__(self):
        self._orig = S.send_message

        def spy(slug, nid, text, wake=False, **kw):
            self.calls.append((nid, bool(wake)))
            return None
        S.send_message = spy                                     # type: ignore
        return self

    def __exit__(self, *a):
        S.send_message = self._orig                              # type: ignore

    @property
    def wakes(self) -> list[str]:
        return [nid for nid, wake in self.calls if wake]


def rig(dogs: int = 2, notice: bool = False):
    """An org with an owner and `dogs` armed file-watchdogs, saved to disk."""
    _n[0] += 1
    slug = f"ks{_n[0]}"
    o = Org.create(slug, dirs=[_TMP])
    o.hire(USER, None, "opus", 20, "boss")
    ids = []
    for i in range(dogs):
        tgt = os.path.join(_TMP, f"{slug}-{i}.log")
        with open(tgt, "w", encoding="utf-8") as f:
            f.write("start\n")
        r = o.watchdog_create("boss", f"dog{i}", "file", tgt,
                              pattern="BOOM", interval_s=15, notice=notice)
        ids.append(str(r["id"]))
    store.save_org(o)
    return slug, o, ids


def reload(slug: str) -> Org:
    return store.load_org(slug)


def dog(o: Org, wid: str) -> dict:
    return o._watchdog(wid)


# ───────────────────────────────────────────────── §1 the pause
def sec_pause() -> None:
    print("\n§1  the pause — what STOP ALL now does to the dogs")

    slug, o, ids = rig(dogs=3)
    check("before the stop, every dog is armed (the rig can be stopped)",
          lambda: _true(all(dog(reload(slug), w)["state"] == "armed"
                            for w in ids), "rig did not arm its dogs"))

    res = S.interrupt_all(slug, pause_watchdogs=True)

    check("STOP ALL pauses every armed watchdog",
          lambda: _true(all(dog(reload(slug), w)["state"] == "paused"
                            for w in ids),
                        [dog(reload(slug), w)["state"] for w in ids]))
    check("...and reports which ones, so the stop is accountable",
          lambda: _true(len(res.get("watchdogs_paused") or []) == 3
                        and {d["id"] for d in res["watchdogs_paused"]} == set(ids),
                        res.get("watchdogs_paused")))
    check("each paused dog carries the KILLSWITCH reason, in words",
          lambda: _true(all(dog(reload(slug), w)["paused_why"]
                            == Org.WATCHDOG_KILLSWITCH_PAUSE for w in ids),
                        [dog(reload(slug), w).get("paused_why") for w in ids]))
    check("the reason says out loud that nothing un-pauses it",
          lambda: _true("automatically" in Org.WATCHDOG_KILLSWITCH_PAUSE
                        and "resume" in Org.WATCHDOG_KILLSWITCH_PAUSE.lower(),
                        Org.WATCHDOG_KILLSWITCH_PAUSE))

    # A dog paused for ANOTHER reason must keep that reason. Overwriting an
    # archive-pause with the killswitch reason would quietly make it permanent
    # — §4 shows rehire only re-arms the archive reason — so the stop would
    # have broken an unrelated recovery path that nobody was looking at.
    slug2, o2, ids2 = rig(dogs=2)
    o2 = reload(slug2)
    w0 = dog(o2, ids2[0])
    w0["state"] = "paused"
    w0["paused_why"] = Org.WATCHDOG_ARCHIVE_PAUSE
    store.save_org(o2)
    S.interrupt_all(slug2, pause_watchdogs=True)

    check("a dog already paused keeps the reason it was paused FOR",
          lambda: _true(dog(reload(slug2), ids2[0])["paused_why"]
                        == Org.WATCHDOG_ARCHIVE_PAUSE,
                        dog(reload(slug2), ids2[0]).get("paused_why")))
    check("...while the still-armed one is taken by the stop",
          lambda: _true(dog(reload(slug2), ids2[1])["paused_why"]
                        == Org.WATCHDOG_KILLSWITCH_PAUSE,
                        dog(reload(slug2), ids2[1]).get("paused_why")))

    # No category is exempt (the coordinator's ruling: do not soften it).
    slug3, o3, ids3 = rig(dogs=1, notice=True)
    S.interrupt_all(slug3, pause_watchdogs=True)
    check("a NOTICE dog is paused too — no category is exempt",
          lambda: _true(dog(reload(slug3), ids3[0])["state"] == "paused",
                        dog(reload(slug3), ids3[0])["state"]))


# ───────────────────────────────────── §2 the wake, with positive controls
def sec_wake() -> None:
    print("\n§2  the wake — every absence paired with its positive control")

    # ⚠ THE CONTROL COMES FIRST, DELIBERATELY. If this half does not wake an
    # agent, the half below proves nothing at all, and it would still be green.
    slug, o, ids = rig(dogs=1)
    with Woke() as spy:
        S._wd_fire(slug, ids[0], "dog0", ["BOOM happened"])
    check("CONTROL · an armed dog's fire really does WAKE its owner",
          lambda: _true(spy.wakes == ["boss"],
                        f"the rig cannot wake anyone, so the check below "
                        f"would pass on an empty world: {spy.calls}"))

    slug2, o2, ids2 = rig(dogs=1)
    S.interrupt_all(slug2, pause_watchdogs=True)
    with Woke() as spy2:
        S._wd_fire(slug2, ids2[0], "dog0", ["BOOM happened"])
    check("after STOP ALL, the same fire wakes NOBODY",
          lambda: _true(spy2.wakes == [], spy2.calls))

    # The event must not be lost — only the wake. A stop that silently ate
    # events would be a different bug, and a worse one to diagnose.
    def _mail_kept():
        slug3, o3, ids3 = rig(dogs=1)
        before = len((reload(slug3).d.get("mail") or {}).get("boss", []))
        with Woke():
            S._wd_fire(slug3, ids3[0], "dog0", ["BOOM one"])
        mid = len((reload(slug3).d.get("mail") or {}).get("boss", []))
        _true(mid == before + 1, f"control: fire did not mail ({before}→{mid})")

    check("CONTROL · a fire puts exactly one mail in the owner's box",
          _mail_kept)

    # A paused dog does not fire at all — `watchdog_fire` refuses it under the
    # doc lock. Control: the same call on an ARMED dog does mail.
    def _paused_does_not_mail():
        slug4, o4, ids4 = rig(dogs=1)
        S.interrupt_all(slug4, pause_watchdogs=True)
        before = len((reload(slug4).d.get("mail") or {}).get("boss", []))
        with Woke():
            S._wd_fire(slug4, ids4[0], "dog0", ["BOOM"])
        after = len((reload(slug4).d.get("mail") or {}).get("boss", []))
        _true(after == before,
              f"a paused dog still mailed its owner ({before}→{after})")

    check("a paused dog's fire is refused outright — no mail, no wake",
          _paused_does_not_mail)

    # And the engine's own scheduler skips it, not just the fire path.
    def _tick_skips():
        slug5, o5, ids5 = rig(dogs=1)
        w = dog(reload(slug5), ids5[0])
        _true(w["state"] == "armed", "control: dog not armed")
        S.interrupt_all(slug5, pause_watchdogs=True)
        o6 = reload(slug5)
        w6 = dog(o6, ids5[0])
        # this is the gate _wd_tick uses for poll and command dogs
        _true(w6.get("state") != "armed",
              "the tick's own armed-gate would still run this dog")

    check("the engine's scheduler gate sees the dog as not-armed", _tick_skips)


# ───────────────────────────────────── §3 the in-flight race
def sec_race() -> None:
    print("\n§3  the in-flight race — a fire already committed when the stop lands")

    # ⚠ THE ONE WINDOW THE ORDERING CANNOT CLOSE. `_wd_fire` commits the mail
    # under DOC_LOCK, releases it, and only then calls send_message(wake=True).
    # A STOP ALL landing in that gap finds an armed dog (so the pause does not
    # help), interrupts every agent — and then the in-flight call wakes one.
    slug, o, ids = rig(dogs=1)

    orig = S.send_message
    fired: list[tuple[str, bool]] = []

    def spy_that_stops_mid_fire(slug_, nid, text, wake=False, **kw):
        fired.append((nid, bool(wake)))
        return None

    # Simulate the race exactly: bump the epoch AFTER the doc work has
    # committed but BEFORE the wake, by stopping the org from inside the
    # patched mail_spark, which _wd_fire calls between the two.
    orig_spark = S.mail_spark
    stopped: list[int] = []

    def spark_then_stop(slug_, frm, to):
        if not stopped:
            stopped.append(1)
            S.interrupt_all(slug_, pause_watchdogs=True)

    def _race():
        S.send_message = spy_that_stops_mid_fire                 # type: ignore
        S.mail_spark = spark_then_stop                           # type: ignore
        try:
            S._wd_fire(slug, ids[0], "dog0", ["BOOM"])
        finally:
            S.send_message = orig                                # type: ignore
            S.mail_spark = orig_spark                            # type: ignore
        _true(stopped, "the rig never actually ran the stop mid-fire")
        _true([n for n, w in fired if w] == [],
              f"a fire already in flight woke an agent AFTER STOP ALL: {fired}")

    check("a fire mid-flight when the stop lands does not wake its owner",
          _race)

    # CONTROL: the identical rig with no stop in the middle DOES wake.
    def _race_control():
        slug2, o2, ids2 = rig(dogs=1)
        with Woke() as spy:
            S._wd_fire(slug2, ids2[0], "dog0", ["BOOM"])
        _true(spy.wakes == ["boss"],
              f"control: the same path must wake when no stop lands: {spy.calls}")

    check("CONTROL · with no stop mid-fire, that same path wakes normally",
          _race_control)

    # The epoch is what carries it — prove it moves, and that it is per-org.
    def _epoch():
        a, _, _ = rig(dogs=1)
        b, _, _ = rig(dogs=1)
        e0a, e0b = S._wd_stop_epoch_of(a), S._wd_stop_epoch_of(b)
        S.interrupt_all(a, pause_watchdogs=True)
        _true(S._wd_stop_epoch_of(a) != e0a, "the stop epoch did not move")
        _true(S._wd_stop_epoch_of(b) == e0b,
              "stopping one org moved another org's epoch — a fire in an "
              "unrelated org would be silently swallowed")

    check("the stop epoch moves, and only for the org that was stopped",
          _epoch)


# ───────────────────────────────────── §4 no automatic resume
def sec_no_auto_resume() -> None:
    print("\n§4  no automatic resume — restart, rehire, and the reason")

    # ⚠ ACROSS A REAL PROCESS, not by re-reading a dict we just wrote. The
    # coordinator's instruction was explicit, and it is the right one: the
    # failure being guarded is "a bounce un-pauses them and the killswitch
    # silently expires", which an in-process assertion cannot see.
    slug, o, ids = rig(dogs=2)
    S.interrupt_all(slug, pause_watchdogs=True)

    def _survives_restart():
        code = (
            "import os,sys,json\n"
            f"sys.path.insert(0, {os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')!r})\n"
            "from orgtree import store\n"
            f"o = store.load_org({slug!r})\n"
            "print(json.dumps([[w['id'], w.get('state'), w.get('paused_why')]"
            "                  for w in (o.d.get('watchdogs') or [])]))\n")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=180)
        _true(p.returncode == 0, f"the fresh process failed: {p.stderr[-500:]}")
        rows = json.loads(p.stdout.strip().splitlines()[-1])
        _true(len(rows) == 2, rows)
        for wid, st, why in rows:
            _true(st == "paused",
                  f"a NEW PROCESS reading the same data root sees dog {wid} "
                  f"as {st!r} — a backend bounce un-pauses the killswitch and "
                  f"the org silently starts waking itself again")
            _true(why == Org.WATCHDOG_KILLSWITCH_PAUSE, (wid, why))

    check("the pause survives into a genuinely FRESH process (real restart)",
          _survives_restart)

    # CONTROL for the restart check: prove that subprocess would have SEEN an
    # armed dog. Otherwise "it read paused" could just mean it read nothing.
    def _restart_control():
        slug2, o2, ids2 = rig(dogs=1)          # armed, never stopped
        code = (
            "import os,sys,json\n"
            f"sys.path.insert(0, {os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')!r})\n"
            "from orgtree import store\n"
            f"o = store.load_org({slug2!r})\n"
            "print(json.dumps([w.get('state') for w in (o.d.get('watchdogs') or [])]))\n")
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=180)
        _true(p.returncode == 0, p.stderr[-500:])
        states = json.loads(p.stdout.strip().splitlines()[-1])
        _true(states == ["armed"],
              f"the restart probe cannot see an armed dog, so its 'paused' "
              f"reading proves nothing: {states}")

    check("CONTROL · that same probe reads 'armed' for a dog never stopped",
          _restart_control)

    # ⚠ REHIRE RE-ARMS PAUSED DOGS — but only ones paused BY THE ARCHIVE. The
    # killswitch having its own reason string is the entire thing standing
    # between an emergency stop and a rehire quietly undoing it.
    def _rehire_does_not_undo():
        slug3 = f"ks-rh{_n[0]}"
        _n[0] += 1
        o3 = Org.create(slug3, dirs=[_TMP])
        o3.hire(USER, None, "opus", 20, "boss")
        o3.hire(USER, "boss", "haiku", 1, "kid")
        tgt = os.path.join(_TMP, f"{slug3}.log")
        open(tgt, "w", encoding="utf-8").write("x\n")
        wid = str(o3.watchdog_create("kid", "kdog", "file", tgt,
                                     pattern="X", interval_s=15)["id"])
        store.save_org(o3)
        S.interrupt_all(slug3, pause_watchdogs=True)
        o3 = reload(slug3)
        _true(o3._watchdog(wid)["state"] == "paused", "setup: not paused")
        o3.retire(USER, "kid")
        o3.rehire(USER, "kid", grant=1)
        store.save_org(o3)
        st = reload(slug3)._watchdog(wid)
        _true(st["state"] == "paused",
              f"a rehire RE-ARMED a killswitch-paused dog: {st}")
        _true(st.get("paused_why") == Org.WATCHDOG_KILLSWITCH_PAUSE, st)

    check("a rehire does NOT re-arm a killswitch-paused dog", _rehire_does_not_undo)

    # CONTROL: the archive-pause path it must not disturb still works.
    def _rehire_control():
        slug4 = f"ks-rh{_n[0]}"
        _n[0] += 1
        o4 = Org.create(slug4, dirs=[_TMP])
        o4.hire(USER, None, "opus", 20, "boss")
        o4.hire(USER, "boss", "haiku", 1, "kid")
        tgt = os.path.join(_TMP, f"{slug4}.log")
        open(tgt, "w", encoding="utf-8").write("x\n")
        wid = str(o4.watchdog_create("kid", "kdog", "file", tgt,
                                     pattern="X", interval_s=15)["id"])
        o4.retire(USER, "kid")                 # archive-pauses the dog
        w = o4._watchdog(wid)
        w["state"] = "paused"
        w["paused_why"] = Org.WATCHDOG_ARCHIVE_PAUSE
        o4.rehire(USER, "kid", grant=1)
        _true(o4._watchdog(wid)["state"] == "armed",
              f"the archive-pause resume path is broken, so the check above "
              f"proves nothing about reasons: {o4._watchdog(wid)}")

    check("CONTROL · a rehire DOES still re-arm an archive-paused dog",
          _rehire_control)

    check("the two reason strings are distinct (the whole mechanism)",
          lambda: _true(Org.WATCHDOG_KILLSWITCH_PAUSE
                        != Org.WATCHDOG_ARCHIVE_PAUSE,
                        "the killswitch now auto-resumes on any rehire"))


# ───────────────────────────────────── §5 scope
def sec_scope() -> None:
    print("\n§5  scope — the stop belongs to the BUTTON, not every caller")

    # ⚠ `interrupt_all` HAS A SECOND CALLER: `hard_freeze`, the kiosk spend
    # limit. That one recovers by itself — the admin raises the limit and ▶
    # replays the interrupted turns. Pausing dogs there would convert a
    # self-recovering feature into one needing an operator to visit every dog,
    # for people who never asked for a killswitch.
    slug, o, ids = rig(dogs=2)
    S.interrupt_all(slug)                       # the default: no pause
    check("interrupt_all WITHOUT the flag leaves every dog armed",
          lambda: _true(all(dog(reload(slug), w)["state"] == "armed"
                            for w in ids),
                        [dog(reload(slug), w)["state"] for w in ids]))

    slug2, o2, ids2 = rig(dogs=2)
    S.hard_freeze(slug2, "spend", "test limit")
    check("a kiosk spend freeze does NOT pause watchdogs (no scope leak)",
          lambda: _true(all(dog(reload(slug2), w)["state"] == "armed"
                            for w in ids2),
                        [dog(reload(slug2), w)["state"] for w in ids2]))

    # CONTROL: that same rig IS pausable, so the check above is not passing
    # merely because nothing in it could ever pause.
    S.interrupt_all(slug2, pause_watchdogs=True)
    check("CONTROL · the same org's dogs DO pause when the button is used",
          lambda: _true(all(dog(reload(slug2), w)["state"] == "paused"
                            for w in ids2),
                        [dog(reload(slug2), w)["state"] for w in ids2]))


# ───────────────────────────────────── §6 the two exits
def sec_exits() -> None:
    print("\n§6  the two exits the ruling names — manual, per-dog, agent-usable")

    slug, o, ids = rig(dogs=2)
    S.interrupt_all(slug, pause_watchdogs=True)

    # Exit 1: the operator visits ONE dog and resumes it.
    def _user_resume_one():
        o2 = reload(slug)
        o2.watchdog_action(USER, ids[0], "resume")
        store.save_org(o2)
        o3 = reload(slug)
        _true(o3._watchdog(ids[0])["state"] == "armed", "the resume did nothing")
        _true("paused_why" not in o3._watchdog(ids[0]),
              "the reason outlived the pause it explains")
        _true(o3._watchdog(ids[1])["state"] == "paused",
              "resuming ONE dog resumed another — there is no bulk resume, "
              "and its absence is the safety property")

    check("the operator can resume ONE dog, and only that one",
          _user_resume_one)

    # Exit 2: the agent resumes its OWN dog — the second half of the ruling
    # ("telling the agents … to unpause all their paused dogs") only works if
    # an agent can actually do this.
    def _owner_resume_own():
        o2 = reload(slug)
        o2.watchdog_action("boss", ids[1], "resume")
        store.save_org(o2)
        _true(reload(slug)._watchdog(ids[1])["state"] == "armed",
              "an agent cannot resume its own dog, so one of the only two "
              "exits the user named does not exist")

    check("an AGENT can resume its own dog (the ruling's second exit)",
          _owner_resume_own)

    def _resumed_dog_fires_again():
        # the exit has to actually restore the behaviour, not just the word
        slug2, o2, ids2 = rig(dogs=1)
        S.interrupt_all(slug2, pause_watchdogs=True)
        o3 = reload(slug2)
        o3.watchdog_action(USER, ids2[0], "resume")
        store.save_org(o3)
        with Woke() as spy:
            S._wd_fire(slug2, ids2[0], "dog0", ["BOOM"])
        _true(spy.wakes == ["boss"],
              f"a resumed dog still cannot wake its owner: {spy.calls}")

    check("a resumed dog wakes its owner again — the exit really restores it",
          _resumed_dog_fires_again)

    # And there is deliberately NO bulk resume anywhere in the surface.
    def _no_bulk_resume():
        _true(not hasattr(Org, "watchdogs_resume_all"),
              "a resume-everything entry point exists — the user explicitly "
              "did not ask for one and its absence is the safety property")

    check("there is NO resume-everything counterpart, by ruling",
          _no_bulk_resume)


# ───────────────────────────────────── §7 the wiring
def sec_route() -> None:
    print("\n§7  the wiring — the BUTTON's own route, not just the mechanism")

    # ⚠ THIS SECTION EXISTS BECAUSE ITS ABSENCE WAS CAUGHT BY A MUTANT.
    # Every check above calls `interrupt_all(..., pause_watchdogs=True)`
    # directly. Deleting `pause_watchdogs=True` from the killswitch ROUTE —
    # i.e. making the actual ⏹ button stop pausing anything, the entire
    # feature — left all of them green. That is this repo's standing failure
    # shape exactly: a suite that proves a mechanism works while nothing
    # proves it is CONNECTED. So drive the real handler.
    import asyncio

    from orgtree import api                                     # noqa: PLC0415

    slug, o, ids = rig(dogs=2)

    def _route_pauses():
        _true(all(dog(reload(slug), w)["state"] == "armed" for w in ids),
              "setup: dogs not armed")
        r = asyncio.run(api.org_killswitch(slug))
        _true(all(dog(reload(slug), w)["state"] == "paused" for w in ids),
              f"the ⏹ route did not pause the dogs: "
              f"{[dog(reload(slug), w)['state'] for w in ids]}")
        _true(len(r.get("watchdogs_paused") or []) == 2, r)

    check("the ⏹ STOP ALL route itself pauses every watchdog", _route_pauses)

    def _route_reason():
        _true(all(dog(reload(slug), w).get("paused_why")
                  == Org.WATCHDOG_KILLSWITCH_PAUSE for w in ids),
              [dog(reload(slug), w).get("paused_why") for w in ids])

    check("...with the killswitch reason, through the real route", _route_reason)

    def _route_wake():
        slug2, o2, ids2 = rig(dogs=1)
        # control first: this rig can wake
        with Woke() as spy0:
            S._wd_fire(slug2, ids2[0], "dog0", ["BOOM"])
        _true(spy0.wakes == ["boss"], f"control: rig cannot wake: {spy0.calls}")
        slug3, o3, ids3 = rig(dogs=1)
        asyncio.run(api.org_killswitch(slug3))
        with Woke() as spy:
            S._wd_fire(slug3, ids3[0], "dog0", ["BOOM"])
        _true(spy.wakes == [],
              f"after the real ⏹ route, a dog still woke its owner: {spy.calls}")

    check("after the real route, a firing dog wakes nobody (with control)",
          _route_wake)


def main() -> None:
    print("═══ ⏹ STOP ALL pauses every watchdog ═══")
    sec_pause()
    sec_wake()
    sec_race()
    sec_no_auto_resume()
    sec_scope()
    sec_exits()
    sec_route()

    print(f"\n{'═' * 70}\n{PASS} checks passed, {len(FAIL)} failed")
    for label, tb in FAIL:
        print(f"\nFAIL  {label}\n{tb}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
