"""FR-27 · THE PRIMED RESTART — `orgtree_prime_restart`.

WHAT IS BEING BUILT AND WHY IT IS SHAPED THIS WAY
-------------------------------------------------
User design, 2026-08-27: "a restart will automatically occur the moment all
agent turns have stopped and no pending turn-starting mail is in flight."

The bug behind it is NOT that `orgtree_self_restart` misbehaves — its mid-turn
refusal is the precondition doing its job. The bug is that the agent holding
the intent kept deferring the call to "next wake" and was cheap-compacted
before making it, so a merged fix sat undeployed for a full day. ⇒ THE
PROPERTY THAT MATTERS MOST IS THAT ARMING OUTLIVES THE ARMING AGENT — its
compaction, its retirement, and a backend bounce.

That is why the persistence checks (§1) are not bookkeeping. A prime that
lives in process memory passes every behavioural check in this file and still
rebuilds the original bug.

WHY THE CHECKS COME IN PAIRS
----------------------------
An all-green harness is the symptom, not the proof (team charter §3). This
subtree's standing failure is an ABSTENTION READING AS A PASS: a guard that
never fires, a claim that never happens, a hold nobody took. So every
assertion that something is refused/held/disarmed has a twin that must come
out the other way — otherwise "the machine is never claimable" and "the claim
works" are the same green.

THE TWO CHECKS THAT ARE THE POINT OF THE FILE
---------------------------------------------
  §3 `_claim_quiet_machine` — the race close. It is not enough that the fire
     path reads the machine idle; a turn accepted a millisecond later would be
     cut. §3d drives a REAL `_hold_for_deploy` and proves it parks.
  §4 the arm→idle→fire path, end to end, through `_prime_tick` — including
     that the prime is DISARMED BEFORE THE SPAWN (proved from inside the spawn
     seam, not after the fact), because the other order is a restart LOOP.

Run:  python tests/test_prime_restart.py
"""

import json
import os
import sys
import tempfile
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ⚠ BEFORE the first orgtree import — `store` resolves ORGTREE_DATA at import
# time, so a root set afterwards leaves an env var that says "isolated" and a
# module pointed at production.
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-prime-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

import _no_deploy                                                # noqa: E402
from orgtree import mcptool, store, supervisor                   # noqa: E402
from orgtree.ledger import USER, LedgerError                     # noqa: E402

# ☠ This suite calls `launch_self_restart` FOR REAL, on purpose — a fire path
# that stops short of the launch proves nothing. The interlock is what stands
# between that and a genuine `update.ps1` against the production port.
_no_deploy.install()
_no_deploy.assert_isolated_data_root()

# ⚠ …and confirm WHICH orgtree we imported. A suite run from a worktree while
# PYTHONPATH points at main reports confident numbers about the wrong code.
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


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
def reset_machine():
    """Between checks: no prime on disk, no busy nodes, no deploy window.

    ⚠ `_deploy_done` is restored to SET. A check that leaves it clear makes
    every later check's `_claim_quiet_machine` return None ("a deploy is
    already in flight"), and they would all pass their negative half for
    entirely the wrong reason."""
    with supervisor._prime_lock:
        try:
            os.remove(supervisor._prime_path())
        except FileNotFoundError:
            pass
    with supervisor._state_lock:
        supervisor._state.clear()
    supervisor._deploy_done.set()
    supervisor._prime_idle_since[0] = 0.0
    supervisor._self_restart_at[0] = 0.0


def busy_node(slug="zz-busy", nid="n1", *, queue=False):
    """Put one node into `_state` looking mid-turn (or holding queued mail —
    `others_working` counts both, and so must everything built on it)."""
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["busy"] = not queue
        st["queue"] = ["a queued message"] if queue else []
    return st


SCOPE = {"bash": False, "web": False, "edit": False,
         "subagents": False, "mcp": []}


def make_org(name, *, sub=False):
    """An org with a real TOP-LEVEL node "boss" and optionally a subordinate
    "worker" under it.

    ⚠ `store.create_org` returns an org with NO nodes at all, and hiring with
    parent=None makes a TOP-LEVEL agent. Getting that wrong makes every
    authority check in §2 pass vacuously — the "subordinate" would be
    top-level, so of course the gate admits it."""
    o = store.create_org(name)
    o.hire(USER, None, "haiku", 5, "boss", add_dirs=[], tools=dict(SCOPE),
           org_visibility="team", charter="prime fixture root")
    if sub:
        o.hire(USER, "boss", "haiku", 2, "worker", add_dirs=[],
               tools=dict(SCOPE), org_visibility="team",
               charter="prime fixture subordinate")
        assert o.node("worker")["parent"] == "boss", \
            "the fixture's subordinate is not actually subordinate"
    store.save_org(o)
    return o, o.d["slug"]


def raises(fn, frag=None):
    """Run `fn`; return the LedgerError message. Fails if it does not raise —
    which is the whole point in a refusal check."""
    try:
        fn()
    except LedgerError as e:
        msg = str(e)
        if frag and frag not in msg:
            raise AssertionError(
                f"refused, but not for the expected reason: {msg!r} "
                f"does not contain {frag!r}")
        return msg
    raise AssertionError("NOT REFUSED — the call went through")


# ---------------------------------------------------------------------------
print("\n§0 · the interlocks themselves")
# This suite reaches the one spawn that restarts the machine. Charter §3
# applies to the guard before it applies to anything the guard protects.


def _interlocks_are_armed():
    assert _no_deploy.installed(), \
        "the deploy interlock is NOT installed — this suite would run a real " \
        "update.ps1 against the production port"
    assert _no_deploy.data_root_isolated(), \
        "the interlock says this suite's own temp root is production"
    real = store.DATA_ROOT
    try:
        store.DATA_ROOT = os.path.expanduser("~/orgtree")
        assert not _no_deploy.data_root_isolated(), (
            "THE ROOT INTERLOCK IS A FICTION: pointed straight at ~/orgtree "
            "it still reported the root isolated")
    finally:
        store.DATA_ROOT = real


check("interlock: deploy seam armed, and the root check REFUSES ~/orgtree "
      "(control pair)", _interlocks_are_armed)


def _prime_file_lives_in_the_isolated_root():
    # ⚠ If `_prime_path` read the ENV VAR instead of the resolved
    # store.DATA_ROOT, a suite that set ORGTREE_DATA late would arm primes in
    # ~/orgtree — and `assert_isolated_data_root` (which reads the resolved
    # value) would not cover this file at all.
    p = os.path.realpath(supervisor._prime_path())
    root = os.path.realpath(store.DATA_ROOT)
    assert p.startswith(root + os.sep), \
        f"the prime record lives at {p!r}, OUTSIDE the isolated root {root!r}"
    real = store.DATA_ROOT
    try:
        store.DATA_ROOT = os.path.join(root, "elsewhere")
        assert os.path.realpath(supervisor._prime_path()).startswith(
            os.path.realpath(store.DATA_ROOT) + os.sep), \
            "_prime_path ignores store.DATA_ROOT — it is reading something " \
            "else, so the data-root interlock does not cover the prime record"
    finally:
        store.DATA_ROOT = real


check("interlock: the prime record follows store.DATA_ROOT (control pair)",
      _prime_file_lives_in_the_isolated_root)


# ---------------------------------------------------------------------------
print("\n§1 · persistence and idempotency — the property that matters most")


def _arming_writes_a_durable_record():
    reset_machine()
    assert supervisor.primed_restart() is None, "started out already primed"
    r = supervisor.arm_prime_restart("orgA", "boss", "org", "deploy d777e76")
    assert r["armed"] is True and r["already_armed"] is False, r
    # THE DURABILITY CLAIM, checked against the FILE and not the return value:
    # a record that exists only in the answer is exactly the bug this feature
    # exists to fix.
    with open(supervisor._prime_path(), encoding="utf-8") as f:
        on_disk = json.load(f)["armed"]
    assert on_disk["by_node"] == "boss" and on_disk["target"] == "org", on_disk
    assert on_disk["reason"] == "deploy d777e76", on_disk
    assert supervisor.primed_restart()["by_org"] == "orgA"


check("arm: the record is on DISK, not just in the answer",
      _arming_writes_a_durable_record)


def _arming_survives_a_backend_bounce():
    """The whole feature in one check. Everything in process memory is thrown
    away — exactly what a restart does — and the prime must still be there."""
    reset_machine()
    supervisor.arm_prime_restart("orgA", "boss", "both", "why")
    # ☠ simulate the bounce: drop every scrap of runtime state the module
    # holds. If ANY of the answer came from memory this goes to None.
    with supervisor._state_lock:
        supervisor._state.clear()
    supervisor._prime_idle_since[0] = 0.0
    supervisor._self_restart_at[0] = 0.0
    supervisor._prime_started = False
    after = supervisor.primed_restart()
    assert after is not None, \
        "THE PRIME DIED WITH THE PROCESS — this is the original bug with " \
        "extra steps: an intent that does not outlive its holder"
    assert after["target"] == "both" and after["by_node"] == "boss", after
    # …and the control: a prime that was CANCELLED must not come back from
    # the same reload, or "survives a bounce" would just mean "never clears".
    supervisor.cancel_prime_restart("orgA", "boss")
    supervisor._prime_started = False
    assert supervisor.primed_restart() is None, \
        "a cancelled prime reappeared after the reload"


check("arm: survives a backend bounce; a CANCELLED one does not come back "
      "(control pair)", _arming_survives_a_backend_bounce)


def _arming_is_idempotent_and_says_so():
    reset_machine()
    first = supervisor.arm_prime_restart("orgA", "boss", "org", "first")
    assert first["armed"] is True
    # a second arm — DIFFERENT target and reason, so a silent overwrite is
    # visible rather than indistinguishable from a no-op
    second = supervisor.arm_prime_restart("orgB", "other", "mailhub", "second")
    assert second["armed"] is False, \
        "the second arm reported itself as having armed something"
    assert second["already_armed"] is True, second
    cur = supervisor.primed_restart()
    assert cur["target"] == "org" and cur["by_node"] == "boss", (
        f"THE SECOND ARM OVERWROTE THE FIRST: {cur} — priming is supposed to "
        f"be idempotent, and silently re-targeting a machine-wide restart is "
        f"the worst way to break that")
    assert cur["reason"] == "first", cur
    # "did mine take effect?" must be answerable FROM THE ANSWER: it names
    # who holds the live prime and what target is actually going to run.
    assert "boss" in second["status"] and "'org'" in second["status"], \
        f"the already-armed answer does not name the live prime: " \
        f"{second['status']!r}"
    # …and the control: once cancelled, arming DOES take effect. Without this
    # every assertion above is also satisfied by an arm that never works.
    supervisor.cancel_prime_restart("orgA", "boss")
    third = supervisor.arm_prime_restart("orgB", "other", "mailhub", "second")
    assert third["armed"] is True, third
    assert supervisor.primed_restart()["target"] == "mailhub"


check("arm: idempotent — a second arm changes NOTHING and names the holder; "
      "after a cancel it works again (control pair)",
      _arming_is_idempotent_and_says_so)


def _idempotency_holds_across_a_bounce():
    """The coordinator's exact worry: "a bounce turns one prime into two"."""
    reset_machine()
    supervisor.arm_prime_restart("orgA", "boss", "org", "first")
    supervisor._prime_started = False           # ← the bounce
    again = supervisor.arm_prime_restart("orgA", "boss", "org", "first")
    assert again["already_armed"] is True, \
        "after a bounce the machine forgot it was primed and armed a SECOND " \
        "restart — idempotency that only holds in one process is not " \
        "idempotency, because the bounce is the case it has to cover"
    d = json.load(open(supervisor._prime_path(), encoding="utf-8"))
    assert isinstance(d["armed"], dict), \
        "the record is not a single armed prime any more"


check("arm: idempotency survives a bounce — no second prime",
      _idempotency_holds_across_a_bounce)


def _cancel_is_honest_both_ways():
    reset_machine()
    none = supervisor.cancel_prime_restart("orgA", "boss")
    assert none["cancelled"] is False and "nothing to cancel" in none["status"]
    supervisor.arm_prime_restart("orgA", "boss", "org", "r")
    got = supervisor.cancel_prime_restart("orgA", "boss")
    assert got["cancelled"] is True, got
    assert supervisor.primed_restart() is None
    assert got["was"]["by_node"] == "boss", got


check("cancel: no-op says so; a real cancel disarms and reports what it "
      "cancelled (control pair)", _cancel_is_honest_both_ways)


def _a_corrupt_record_reads_as_unprimed_not_as_a_crash():
    reset_machine()
    for junk in ("{not json", "[]", ""):
        with open(supervisor._prime_path(), "w", encoding="utf-8") as f:
            f.write(junk)
        assert supervisor.primed_restart() is None, \
            f"a corrupt record {junk!r} was read as an armed prime"
        # …and it must stay ARMABLE. A torn write that permanently bricks the
        # tool is strictly worse than one that loses a prime.
        r = supervisor.arm_prime_restart("orgA", "boss", "org", "after junk")
        assert r["armed"] is True, \
            f"the tool could not re-arm after a corrupt record {junk!r}"
        os.remove(supervisor._prime_path())


check("a corrupt record reads as UNPRIMED and stays armable",
      _a_corrupt_record_reads_as_unprimed_not_as_a_crash)


def _an_unknown_target_is_refused_at_the_arm():
    reset_machine()
    for bad in ("orgs", "", "ORG", "everything"):
        try:
            supervisor.arm_prime_restart("orgA", "boss", bad)
        except ValueError:
            continue
        raise AssertionError(
            f"target {bad!r} was ACCEPTED and armed — an unknown target is "
            f"only discovered at fire time, hours later, with nobody watching")
    assert supervisor.primed_restart() is None
    # control: the three real ones are accepted
    for good in ("org", "mailhub", "both"):
        reset_machine()
        assert supervisor.arm_prime_restart("orgA", "boss", good)["armed"]


check("arm: an unknown target is refused NOW, not at fire time (control pair)",
      _an_unknown_target_is_refused_at_the_arm)


# ---------------------------------------------------------------------------
print("\n§2 · authority — priming is the same decision as restarting")


def _the_prime_gate_matches_the_self_restart_gate():
    o, slug = make_org("zz prime gate", sub=True)
    try:
        root = "boss"
        # ① a TOP-LEVEL node passes both
        o.self_restart_gate(root)
        o.prime_restart_gate(root, "arm")
        o.prime_restart_gate(root, "cancel")
        # ② a SUBORDINATE with no user audience is refused by both — and the
        #    equivalence is the point: an agent refused the immediate tool
        #    must not be able to reach the same machine-wide restart through
        #    the patient one.
        raises(lambda: o.self_restart_gate("worker"), "EVERY org")
        raises(lambda: o.prime_restart_gate("worker", "arm"), "EVERY org")
        raises(lambda: o.prime_restart_gate("worker", "cancel"), "EVERY org")
        # ③ …and the control that ② is not just "everything is refused":
        #    grant the audience and the SAME node passes.
        o.audience_grant(root, "worker", USER)
        o.prime_restart_gate("worker", "arm")
        o.self_restart_gate("worker")
        # ④ arming and cancelling are LOGGED as their own facts — "armed a
        #    restart" and "restarted the machine" are different things to
        #    have done, and the audit must be able to tell them apart.
        ops = [e["op"] for e in o.d["events"]]
        assert "prime_restart_arm" in ops and "prime_restart_cancel" in ops, \
            f"the prime is not in the org log: {sorted(set(ops))}"
        assert "self_restart" in ops, \
            "the shared authority body swallowed self_restart's own log entry"
    finally:
        store.delete_org(slug)


check("gate: prime and self_restart refuse and admit exactly the same nodes; "
      "each logs its own event (control pair)",
      _the_prime_gate_matches_the_self_restart_gate)


def _kiosks_are_sealed_against_priming():
    o, slug = make_org("zz prime kiosk")
    try:
        root = "boss"
        o.prime_restart_gate(root, "arm")          # control: works normally
        o.d["kiosk"] = {"enabled": True}
        assert o.is_kiosk, "the fixture did not actually become a kiosk"
        raises(lambda: o.prime_restart_gate(root, "arm"), "sealed")
        raises(lambda: o.prime_restart_gate(root, "cancel"), "sealed")
    finally:
        store.delete_org(slug)


check("gate: kiosks are sealed against priming too (control pair)",
      _kiosks_are_sealed_against_priming)


# ---------------------------------------------------------------------------
print("\n§3 · the idle predicate and THE RACE CLOSE")


def _one_predicate_two_doors():
    """`_working_locked` is `others_working`'s body. If they can disagree,
    a primed restart can cut a turn the manual tool would have refused."""
    reset_machine()
    assert supervisor.others_working() == []
    with supervisor._state_lock:
        assert supervisor._working_locked() == []
    for kw in ({}, {"queue": True}):
        reset_machine()
        busy_node("zz-p", "n1", **kw)
        outer = supervisor.others_working()
        with supervisor._state_lock:
            inner = supervisor._working_locked()
        assert outer == inner == ["zz-p/n1"], (
            f"the two doors disagree: others_working={outer} "
            f"_working_locked={inner} (kw={kw})")
        # exclusion has to behave the same through both, or the launch's
        # "everyone except me" and the engine's "everyone" diverge
        assert supervisor.others_working(("zz-p", "n1")) == []
        with supervisor._state_lock:
            assert supervisor._working_locked(("zz-p", "n1")) == []
    reset_machine()


check("predicate: one body, two doors — busy AND queued agree through both "
      "(control pair)", _one_predicate_two_doors)


def _a_busy_machine_is_not_claimed_and_is_not_held():
    reset_machine()
    busy_node("zz-p", "n1")
    got = supervisor._claim_quiet_machine(True)
    assert got == ["zz-p/n1"], got
    # ☠ THE HALF THAT WEDGES THE MACHINE. A refused claim that clears
    # `_deploy_done` on its way out holds every turn on the box for
    # DEPLOY_HOLD_MAX seconds for a deploy that was never launched — and it
    # would do it on a 5-second poll, forever, while an agent is working.
    assert supervisor._deploy_done.is_set(), \
        "a REFUSED claim left the machine held — every org here would stop " \
        "running turns for a restart that is not coming"
    reset_machine()


check("claim: a busy machine is refused AND left unheld",
      _a_busy_machine_is_not_claimed_and_is_not_held)


def _an_idle_machine_is_claimed_and_held():
    reset_machine()
    got = supervisor._claim_quiet_machine(True)
    assert got == [], got
    assert not supervisor._deploy_done.is_set(), \
        "the claim succeeded but took NO hold — the window between reading " \
        "idle and spawning is wide open, which is the whole race"
    supervisor._deploy_done.set()
    # …and the mailhub control: that leg rebuilds a container and never
    # touches this backend, so holding every org's turns for it would stop
    # the machine for a restart that was never coming (D-142/a).
    got = supervisor._claim_quiet_machine(False)
    assert got == [], got
    assert supervisor._deploy_done.is_set(), \
        "a mailhub-only prime held every turn on the machine"
    reset_machine()


check("claim: an idle machine is claimed AND held; a mailhub prime is "
      "claimed and NOT held (control pair)", _an_idle_machine_is_claimed_and_held)


def _the_claim_actually_parks_a_turn():
    """THE RACE, END TO END. `_hold_for_deploy` is the first thing
    `_run_turn` does — "the single choke point: all three thread starts target
    this function". So: claim the machine, then drive the real hold and prove
    it parks. Anything less is a claim about a lock, not about turns."""
    reset_machine()
    # ① control FIRST, so a hold that never blocks cannot masquerade as a
    #    pass: on an unclaimed machine the threshold is a straight-through.
    t0 = time.monotonic()
    supervisor._hold_for_deploy("zz-p", "n1")
    assert time.monotonic() - t0 < 0.5, \
        "the threshold blocked with NO deploy window open"
    # ② now claim, and watch a turn park at the threshold
    assert supervisor._claim_quiet_machine(True) == []
    started, released = threading.Event(), threading.Event()

    def _turn():
        started.set()
        supervisor._hold_for_deploy("zz-p", "n1")
        released.set()

    th = threading.Thread(target=_turn, daemon=True)
    th.start()
    assert started.wait(5), "the fixture thread never ran"
    assert not released.wait(1.5), (
        "A TURN STARTED INSIDE THE CLAIMED WINDOW. The prime engine would "
        "spawn the deploy on top of it and cut it mid-flight — the exact "
        "harm the mid-turn refusal exists to prevent, arriving through the "
        "automated door")
    # ③ and it must not park FOREVER: releasing the window readmits it
    supervisor._deploy_done.set()
    assert released.wait(5), \
        "the held turn never resumed after the window closed — a turn that " \
        "parks and never wakes is worse than one that is cut"
    th.join(timeout=5)
    reset_machine()


check("claim: a turn accepted after the claim PARKS at the threshold, and "
      "resumes when the window closes (control pair)",
      _the_claim_actually_parks_a_turn)


def _a_deploy_already_in_flight_is_not_adopted():
    reset_machine()
    supervisor._deploy_done.clear()          # somebody else is deploying
    try:
        got = supervisor._claim_quiet_machine(True)
        assert got is None, (
            f"claimed a machine that already has a deploy window open "
            f"({got!r}) — the prime would later RELEASE somebody else's "
            f"hold and readmit turns into a live deploy")
    finally:
        supervisor._deploy_done.set()
    assert supervisor._claim_quiet_machine(True) == [], \
        "…and the control failed: with the window closed it should claim"
    reset_machine()


check("claim: refuses when a deploy window is already open (control pair)",
      _a_deploy_already_in_flight_is_not_adopted)


# ---------------------------------------------------------------------------
print("\n§4 · arm → idle → FIRE, end to end")


class _SpawnSpy:
    """Wraps the deploy interlock and records the world AS SEEN FROM INSIDE
    the spawn — which is the only place the disarm-before-spawn ordering can
    actually be observed. Checking after the fact cannot tell "disarmed
    first" from "disarmed second"."""

    def __init__(self):
        self.argv: list[list[str]] = []
        self.primed_at_spawn: list = []
        self._real = supervisor._detached_spawn

    def __enter__(self):
        def spy(args, cwd, logpath, env=None):
            self.argv.append(list(args))
            self.primed_at_spawn.append(supervisor.primed_restart())
            return self._real(args, cwd, logpath, env)
        supervisor._detached_spawn = spy
        return self

    def __exit__(self, *exc):
        supervisor._detached_spawn = self._real
        # ⚠ restored EXACTLY, or `_no_deploy.installed()` goes false for every
        # later suite and the gun is re-armed.
        assert _no_deploy.installed(), \
            "the deploy interlock was not restored after the spy"
        return False


def _deploys(argv):
    return [a for a in argv
            if any(x in " ".join(a).lower()
                   for x in ("update.ps1", "update.sh"))]


def _the_whole_path_arm_then_idle_then_fire():
    reset_machine()
    with _SpawnSpy() as spy:
        supervisor.arm_prime_restart("orgA", "boss", "org", "ship bb5236e")

        # ① BUSY: nothing fires, and the prime is STILL ARMED. A prime spent
        #    on a busy machine is the feature failing at its one job.
        busy_node("zz-p", "n1")
        supervisor._prime_tick()
        supervisor._prime_tick()
        assert not _deploys(spy.argv), \
            f"FIRED WHILE AN AGENT WAS MID-TURN: {spy.argv}"
        assert supervisor.primed_restart() is not None, \
            "the prime was consumed without deploying anything"
        assert supervisor._deploy_done.is_set(), \
            "ticking against a busy machine left it held"

        # ② IDLE, but not for long enough: the settling period must bite, or
        #    "no pending turn-starting mail in flight" is only ever true by
        #    luck at the instant we happen to look.
        with supervisor._state_lock:
            supervisor._state.clear()
        supervisor._prime_tick()
        supervisor._prime_tick()
        assert not _deploys(spy.argv), \
            f"fired on the first idle tick — the settling period does " \
            f"nothing: {spy.argv}"
        assert supervisor._prime_idle_since[0] != 0.0, \
            "the engine never started counting the quiet period"

        # ③ the quiet period has now been served (backdating the stamp is
        #    what the clock would have done; ② already proved it is real)
        supervisor._prime_idle_since[0] -= supervisor.PRIME_QUIET_S + 1
        supervisor._prime_tick()

        fired = _deploys(spy.argv)
        assert fired, (
            "THE PRIME NEVER FIRED on a quiet machine — every negative check "
            "above is satisfied by a feature that simply does nothing")
        assert len(fired) == 1, f"fired more than once: {fired}"

        # ④ ☠ DISARMED BEFORE THE SPAWN, observed FROM INSIDE the spawn. The
        #    other order is a restart LOOP: update.ps1 Stop-Processes this
        #    backend, the disarm write never lands, and the next boot finds
        #    the prime still armed.
        assert (spy.primed_at_spawn[-1] is not None
                and spy.primed_at_spawn[-1]["state"] == "executing"), (
            f"the prime was STILL ARMED at the moment the deploy was "
            f"spawned ({spy.primed_at_spawn[-1]}) — if the restart lands "
            f"before the disarm write, this machine restarts forever")
        raw = json.load(open(supervisor._prime_path(), encoding="utf-8"))
        assert raw["armed"] is None, raw
        # ⑤ and it is not merely gone: what happened is on the record
        d = json.load(open(supervisor._prime_path(), encoding="utf-8"))
        assert d["last_fired"]["was"]["by_node"] == "boss", d
        assert d["last_fired"]["launched"], d

        # ⑥ a second tick does not fire again
        n = len(_deploys(spy.argv))
        supervisor._prime_idle_since[0] = 0.0
        supervisor._prime_tick()
        supervisor._prime_tick()
        assert len(_deploys(spy.argv)) == n, \
            "a spent prime fired a second time"
    reset_machine()


check("E2E: armed → refuses while busy → refuses before the quiet period → "
      "FIRES → disarmed BEFORE the spawn → does not fire twice",
      _the_whole_path_arm_then_idle_then_fire)


def _the_fire_never_leaves_the_machine_held():
    """The orphaned hold. `_fire_prime` clears `_deploy_done` itself to close
    the race, so every path where the launch does NOT adopt that hold has to
    hand it back — or the machine runs no turns at all for DEPLOY_HOLD_MAX."""
    reset_machine()
    with _SpawnSpy() as spy:
        # ① the launch REFUSES (its own one-at-a-time window is hot). Nothing
        #    is spawned, so nothing will ever release the hold we took.
        supervisor.arm_prime_restart("orgA", "boss", "org", "r")
        supervisor._self_restart_at[0] = time.time()
        r = supervisor._fire_prime(supervisor.primed_restart())
        assert not _deploys(spy.argv), \
            "the rate limit did not actually refuse — this check proves " \
            "nothing about the orphaned hold"
        assert supervisor._deploy_done.is_set(), (
            "A REFUSED LAUNCH LEFT THE MACHINE HELD. Every org on this box "
            "would stop running turns for a deploy that never started")
        assert r["fired"] is True and not r["result"].get("deploy_window")

        # ② the control: a launch that DOES spawn hands the hold to the
        #    child's watcher instead. (The interlock's refused child has
        #    already exited, so the watcher releases it promptly — what
        #    matters is that `deploy_window` came back true, i.e. the release
        #    has an owner and `_fire_prime` did NOT do it itself.)
        reset_machine()
        supervisor.arm_prime_restart("orgA", "boss", "org", "r")
        r2 = supervisor._fire_prime(supervisor.primed_restart())
        assert _deploys(spy.argv), "the control never spawned anything"
        assert r2["result"]["deploy_window"] is True, (
            "the launch spawned a deploy child but reported no window — "
            "`_fire_prime` cannot tell an adopted hold from an orphaned one, "
            "so it must either wedge the machine or readmit turns into a "
            "live deploy")

        # ③ mailhub: never held in the first place, so nothing to hand back
        reset_machine()
        supervisor.arm_prime_restart("orgA", "boss", "mailhub", "r")
        supervisor._fire_prime(supervisor.primed_restart())
        assert supervisor._deploy_done.is_set(), \
            "a mailhub prime held every turn on the machine"
    reset_machine()


check("fire: a refused launch hands the hold back; a real one hands it to "
      "the child's watcher (control pair)", _the_fire_never_leaves_the_machine_held)


def _the_rate_limit_is_waited_out_not_spent_on():
    """`_self_restart_at` is process memory and a restart zeroes it — so the
    durable half of the gap lives in `last_fired`. Without it, a prime armed
    during a deploy can fire straight into the deploy that just finished."""
    reset_machine()
    with _SpawnSpy() as spy:
        supervisor.arm_prime_restart("orgA", "boss", "org", "r")
        # a fire that happened 10s ago, ON DISK, with process memory clean —
        # which is precisely the state a backend has just after a restart
        with supervisor._prime_lock:
            d = supervisor._prime_read()
            d["last_fired"] = {"at": supervisor.now_iso(),
                               "at_ts": time.time() - 10}
            supervisor._prime_write(d)
        supervisor._self_restart_at[0] = 0.0
        supervisor._prime_idle_since[0] = time.monotonic() - 999
        supervisor._prime_tick()
        assert not _deploys(spy.argv), \
            "fired 10s after the last deploy — the machine-wide one-at-a-" \
            "time gap does not survive the restart it is supposed to govern"
        assert supervisor.primed_restart() is not None, (
            "worse: the prime was SPENT on a refusal. Waiting costs nothing; "
            "losing the prime means nobody deploys and nobody is told")
        # control: age the stamp past the gap and the same tick fires
        with supervisor._prime_lock:
            d = supervisor._prime_read()
            d["last_fired"]["at_ts"] = (time.time()
                                        - supervisor.SELF_RESTART_MIN_GAP - 5)
            supervisor._prime_write(d)
        supervisor._prime_idle_since[0] = time.monotonic() - 999
        supervisor._prime_tick()
        assert _deploys(spy.argv), \
            "…and the control failed: past the gap it still did not fire"
    reset_machine()


check("fire: the one-at-a-time gap is WAITED OUT and survives a bounce; the "
      "prime is not spent on a refusal (control pair)",
      _the_rate_limit_is_waited_out_not_spent_on)


def _a_prime_outlives_the_agent_that_armed_it():
    """Arm from a real node, then RETIRE it. The prime must still fire —
    surviving its author is the entire feature, and re-checking authority at
    fire time would silently kill exactly this case."""
    reset_machine()
    o, slug = make_org("zz prime outlives")
    with _SpawnSpy() as spy:
        try:
            root = "boss"
            o.prime_restart_gate(root, "arm")
            supervisor.arm_prime_restart(slug, root, "org", "merged fix")
            store.save_org(o)
            # ☠ the arming agent is gone — the exact situation the feature
            # exists for (its predecessor was compacted mid-intent)
            o.retire(USER, root)
            store.save_org(o)
            assert o.node(root)["state"] != "live", \
                "the fixture did not actually retire the arming agent"
            supervisor._prime_idle_since[0] = time.monotonic() - 999
            supervisor._prime_tick()
            assert _deploys(spy.argv), (
                "THE PRIME DIED WITH ITS AUTHOR. An agent that arms a "
                "restart and is then retired or compacted is the entire "
                "motivating case; if that prime does not fire, the feature "
                "fixes nothing")
        finally:
            store.delete_org(slug)
    reset_machine()


check("E2E: a prime armed by an agent that has since been RETIRED still fires",
      _a_prime_outlives_the_agent_that_armed_it)


# ---------------------------------------------------------------------------
print("\n§5 · the surfaces agents and the user actually see")


def _the_tool_card_is_in_the_catalogue():
    card = next((t for t in mcptool.TOOLS
                 if t["name"] == "orgtree_prime_restart"), None)
    assert card is not None, \
        "orgtree_prime_restart is not in mcptool.TOOLS — no session will " \
        "ever be taught it exists"
    props = card["inputSchema"]["properties"]
    assert set(props["action"]["enum"]) == {"arm", "cancel", "status"}, props
    assert set(props["target"]["enum"]) == {"org", "mailhub", "both"}, props
    assert not card["inputSchema"]["required"], \
        "arming should need no arguments — the default is the common case"
    # the card has to carry the two facts an agent cannot infer
    d = card["description"].lower()
    assert "idempotent" in d, "the card does not say arming is idempotent"
    for word in ("compaction", "retirement"):
        assert word in d, \
            f"the card does not say the prime survives {word} — that is the " \
            f"only reason to use it instead of planning to call again"


check("surface: the tool card exists, with all three actions and the "
      "survival claim", _the_tool_card_is_in_the_catalogue)


def _the_recital_points_a_refused_restart_at_the_prime():
    """The identity prompt used to end "wait and call again" — which is the
    plan that failed. It must now name the tool that replaces it."""
    o, slug = make_org("zz prime recital", sub=True)
    try:
        top = supervisor.identity_prompt(o, "boss")
        assert "orgtree_prime_restart" in top, \
            "a top-level agent is never told the prime tool exists"
        assert "call again" not in top.lower().split(
            "orgtree_prime_restart")[0][-400:], \
            "the recital still recommends the plan that failed"
        # control: the recital's restart block is authority-gated, so a
        # subordinate must not be told either — otherwise this assertion
        # would pass on a string that is simply always present
        sub = supervisor.identity_prompt(o, "worker")
        assert "orgtree_prime_restart" not in sub, \
            "a subordinate with no user audience is taught a tool it cannot " \
            "use — the recital's restart block is supposed to be gated"
    finally:
        store.delete_org(slug)


check("surface: the recital sends a refused restart to the prime, and only "
      "for agents allowed to use it (control pair)",
      _the_recital_points_a_refused_restart_at_the_prime)


def _the_indicator_reaches_every_org():
    """The chip is fed by `tree()["primed_restart"]`, injected in api.py. A
    prime armed from org A must show in org B, because the restart cuts B."""
    reset_machine()
    a, slug_a = make_org("zz prime vis a")
    b, slug_b = make_org("zz prime vis b")
    try:
        from fastapi.testclient import TestClient
        from orgtree import api
        c = TestClient(api.app)
        for s in (slug_a, slug_b):
            assert c.get(f"/api/orgs/{s}").json()["primed_restart"] is None, \
                "an unprimed machine already reports a primed restart"
        supervisor.arm_prime_restart(slug_a, "boss", "org", "ship it")
        for s in (slug_a, slug_b):
            got = c.get(f"/api/orgs/{s}").json()["primed_restart"]
            assert got is not None, (
                f"org {s} does not see the primed restart. It is about to be "
                f"restarted by it — the org that did NOT arm it is exactly "
                f"the one that needs the warning")
            assert got["by_org"] == slug_a and got["reason"] == "ship it", got
        supervisor.cancel_prime_restart(slug_a, "boss")
        assert c.get(f"/api/orgs/{slug_b}").json()["primed_restart"] is None, \
            "the chip outlived the cancel"
    finally:
        for s in (slug_a, slug_b):
            try:
                store.delete_org(s)
            except Exception:                                # noqa: BLE001
                pass
    reset_machine()


check("surface: the indicator reaches EVERY org's tree, and clears on cancel "
      "(control pair)", _the_indicator_reaches_every_org)


def _the_tool_round_trips_through_the_real_handler():
    """arm → status → idempotent re-arm → cancel, through /api/agent — the
    path an actual agent's tool call takes, gate and all."""
    reset_machine()
    o, slug = make_org("zz prime handler", sub=True)
    try:
        from fastapi.testclient import TestClient
        from orgtree import api
        c = TestClient(api.app)
        root = "boss"

        def call(node, args):
            return c.post("/api/agent", json={
                "org": slug, "node": node,
                "tool": "orgtree_prime_restart", "args": args})

        r = call(root, {"target": "org", "reason": "handler"})
        assert r.status_code == 200, (r.status_code, r.text)
        assert r.json()["armed"] is True, r.json()

        st = call(root, {"action": "status"}).json()
        assert st["primed"]["reason"] == "handler", st

        again = call(root, {"target": "both"}).json()
        assert again["already_armed"] is True and again["armed"] is False
        assert supervisor.primed_restart()["target"] == "org", \
            "the handler let a second call re-target the restart"

        # the gate really is in this path — a subordinate is refused, and the
        # prime it tried to cancel is still armed afterwards
        bad = call("worker", {"action": "cancel"})
        assert bad.status_code >= 400, (bad.status_code, bad.text)
        assert supervisor.primed_restart() is not None, \
            "a node the gate refused still managed to disarm the machine"

        gone = call(root, {"action": "cancel"}).json()
        assert gone["cancelled"] is True, gone
        assert supervisor.primed_restart() is None

        assert call(root, {"action": "wat"}).status_code == 422
    finally:
        try:
            store.delete_org(slug)
        except Exception:                                    # noqa: BLE001
            pass
    reset_machine()


check("surface: arm/status/re-arm/cancel round-trip through /api/agent, gate "
      "included (control pair)", _the_tool_round_trips_through_the_real_handler)


# ---------------------------------------------------------------------------
print("\n§5 · FR-32 · THE DEADLINE — 'deploy when quiet, or force at N min'")
# Coordinator decision 2026-09-04. A prime with no deadline waits forever, and
# on this machine one waited over two hours while ten commits stacked behind
# it. The deadline bounds that wait with an ESCALATION: the same quiesce an
# agent's `force=true` runs, fired unattended.
#
# ⚠ THE CHECKS THAT ARE THE POINT OF THIS SECTION, in the order they matter:
#   §5c a DEADLINE-LESS prime behaves exactly as before — the default is not
#       merely correct, it is UNCHANGED (constraint 5).
#   §5d a deadline that has expired on a QUIET machine takes the ordinary
#       path and never escalates (constraint 3). Its twin, §5e, proves the
#       escalation exists at all — without it §5d passes for a feature that
#       simply does nothing.
#   §5f the escalation calls the SAME quiesce (constraint 1) and leaves the
#       record saying it escalated (constraint 2).
#   §5g the cut agents are woken on the new build (constraint 4) — with the
#       control that a wake is NOT armed for anyone who was not cut.


def _a_deadline_needs_a_reason_and_sane_bounds():
    """The gate. A deadline is a SCHEDULED force, so it takes force's brake:
    it cannot be armed without saying why. Without this, `prime_restart` with
    a five-minute deadline would be a route to a forced deploy that never
    records a justification — in the one path where nobody is present to be
    asked afterwards."""
    o, slug = make_org("zz dl gate")
    raises(lambda: o.prime_restart_gate("boss", "arm", deadline_minutes=15),
           "requires a `reason`")
    raises(lambda: o.prime_restart_gate("boss", "arm", reason="  ",
                                        deadline_minutes=15),
           "requires a `reason`")
    lo = supervisor.PRIME_DEADLINE_MIN_MINUTES
    hi = supervisor.PRIME_DEADLINE_MAX_MINUTES
    for bad in (0, 1, lo - 1, hi + 1, -5):
        raises(lambda b=bad: o.prime_restart_gate(
            "boss", "arm", reason="r", deadline_minutes=b),
            "deadline_minutes must be")
    # ☠ THE CONTROL PAIR. Without these the gate could refuse EVERYTHING and
    # every assertion above would still pass.
    o.prime_restart_gate("boss", "arm", reason="r", deadline_minutes=lo)
    o.prime_restart_gate("boss", "arm", reason="r", deadline_minutes=hi)
    # …and an ordinary reasonless prime is STILL fine — the brake is on the
    # deadline, not on priming
    o.prime_restart_gate("boss", "arm")
    o.prime_restart_gate("boss", "cancel")
    store.delete_org(slug)


check("deadline: needs a reason and sane bounds; a plain prime is untouched "
      "(control pair)", _a_deadline_needs_a_reason_and_sane_bounds)


def _the_deadline_is_stored_absolute_and_survives_a_bounce():
    """Stored as an epoch, not a duration. A backend that bounces every few
    minutes must not restart the clock — that would leave a deadline that can
    never expire on exactly the box that needs one most."""
    reset_machine()
    supervisor.arm_prime_restart("orgA", "boss", "org", "ship it",
                                 deadline_minutes=30)
    raw = json.load(open(supervisor._prime_path(), encoding="utf-8"))
    rec = raw["armed"]
    assert rec["deadline_minutes"] == 30, rec
    assert rec["deadline_ts"] > time.time() + 29 * 60, rec
    assert rec["deadline"].startswith("20"), rec
    ts = rec["deadline_ts"]
    # the bounce: process memory gone, the file is all that is left
    supervisor._prime_idle_since[0] = 0.0
    again = supervisor.primed_restart()
    assert again["deadline_ts"] == ts, (again, ts)
    assert supervisor._deadline_expired(again) is False, again
    # …and it does expire once the clock passes it (the control — otherwise
    # "never expired" and "the predicate is broken" are the same green)
    again["deadline_ts"] = time.time() - 1
    assert supervisor._deadline_expired(again) is True, again
    # a corrupt deadline degrades to "no deadline", never to "escalate now"
    assert supervisor._deadline_expired({"deadline_ts": "soon"}) is False
    assert supervisor._deadline_expired({}) is False
    reset_machine()


check("deadline: stored ABSOLUTE, survives a bounce, and a corrupt one reads "
      "as no-deadline (control pair)",
      _the_deadline_is_stored_absolute_and_survives_a_bounce)


def _a_prime_with_no_deadline_is_unchanged():
    """☠ CONSTRAINT 5. The default is not merely 'still works' — it must be
    the SAME behaviour: a busy machine ticks forever and nothing escalates,
    however long it has been armed."""
    reset_machine()
    with _SpawnSpy() as spy:
        supervisor.arm_prime_restart("orgA", "boss", "org", "no deadline")
        rec = supervisor.primed_restart()
        assert "deadline_ts" not in rec, rec
        busy_node("zz-nodl", "n1")
        # ticks well past any deadline anyone might have set
        for _ in range(6):
            supervisor._prime_tick()
        assert not _deploys(spy.argv), \
            f"a deadline-LESS prime escalated on a busy machine: {spy.argv}"
        assert supervisor.primed_restart() is not None, \
            "the prime was consumed without deploying"
        assert supervisor._deploy_done.is_set(), \
            "ticking a deadline-less prime against a busy machine left the " \
            "machine held"
        assert supervisor.state("zz-nodl", "n1")["busy"], \
            "a deadline-less prime interrupted a working agent"
    reset_machine()


check("☠ deadline: a prime WITHOUT one is exactly as before — a busy machine "
      "is never cut, however long it ticks", _a_prime_with_no_deadline_is_unchanged)


def _an_expired_deadline_on_a_QUIET_machine_never_escalates():
    """☠ CONSTRAINT 3, and the reason it is structural rather than a
    comparison: the escalation lives inside `_prime_tick`'s `if busy:` branch,
    so an idle machine cannot reach it — it has already taken the ordinary
    quiet path. A deadline that forced when it did not need to would be a
    permanent small tax on every prime carrying one.

    The deadline here is ALREADY IN THE PAST and the machine is idle."""
    reset_machine()
    with _SpawnSpy() as spy:
        supervisor.arm_prime_restart("orgA", "boss", "org", "r",
                                     deadline_minutes=10)
        with supervisor._prime_lock:
            d = supervisor._prime_read()
            d["armed"]["deadline_ts"] = time.time() - 3600
            supervisor._prime_write(d)
        # idle machine, quiet period served
        supervisor._prime_tick()
        supervisor._prime_idle_since[0] -= supervisor.PRIME_QUIET_S + 1
        supervisor._prime_tick()
        assert _deploys(spy.argv), "it did not fire at all"
        d = json.load(open(supervisor._prime_path(), encoding="utf-8"))
        lf = d["last_fired"]
        assert not lf.get("escalated"), (
            "an EXPIRED deadline escalated on an IDLE machine — the ordinary "
            "quiet path was right there and the escalation is now a tax on "
            f"every prime that carries a deadline: {lf}")
        assert not lf.get("cut"), lf
    reset_machine()


check("☠ deadline: expired but the machine is QUIET → the ordinary path "
      "fires and NOTHING escalates",
      _an_expired_deadline_on_a_QUIET_machine_never_escalates)


def _an_expired_deadline_on_a_BUSY_machine_escalates_through_the_same_quiesce():
    """☠ THE POSITIVE CONTROL for the check above, and constraints 1+2.

    Without this, "never escalates on a quiet machine" is satisfied by an
    escalation that never happens at all. And it pins the two properties the
    coordinator asked for: the SAME `force_quiesce_for_restart` (not a second
    idea of what is safe to cut), and a record that says it escalated without
    anyone having to infer it from a non-empty cut list."""
    reset_machine()
    calls: list[dict] = []
    real_q = supervisor.force_quiesce_for_restart

    def spy_q(exclude=None, timeout=supervisor.FORCE_QUIESCE_TIMEOUT_S,
              why=""):
        calls.append({"exclude": exclude, "why": why})
        return real_q(exclude=exclude, timeout=0.4, why=why)
    supervisor.force_quiesce_for_restart = spy_q
    try:
        with _SpawnSpy() as spy:
            supervisor.arm_prime_restart("orgA", "boss", "org",
                                         "ten commits are stuck",
                                         deadline_minutes=10)
            busy_node("zz-esc", "n1")
            # not yet expired: it must NOT escalate merely because it is busy
            supervisor._prime_tick()
            assert not calls, "it escalated before the deadline expired"
            assert not _deploys(spy.argv), spy.argv
            with supervisor._prime_lock:
                d = supervisor._prime_read()
                d["armed"]["deadline_ts"] = time.time() - 1
                supervisor._prime_write(d)
            supervisor._prime_tick()
            # ① it went through THE SAME quiesce, and excluded nobody — an
            #    unattended escalation has no caller to exempt
            assert len(calls) == 1, calls
            assert calls[0]["exclude"] is None, calls
            assert "deadline expired" in calls[0]["why"], calls
            # ② it actually deployed
            assert _deploys(spy.argv), \
                f"the deadline expired on a busy machine and nothing "\
                f"deployed: {spy.argv}"
            # ③ the agent was stopped, not deployed on top of
            assert not supervisor.state("zz-esc", "n1")["queue"], \
                "the quiesce did not clear the cut agent's queue"
            # ④ ☠ THE RECORD SAYS IT ESCALATED — constraint 2
            d = json.load(open(supervisor._prime_path(), encoding="utf-8"))
            lf = d["last_fired"]
            assert lf.get("escalated") is True, lf
            assert "zz-esc/n1" in lf.get("cut", []), lf
            assert "deadline expired" in lf.get("why_forced", ""), lf
            assert "ten commits are stuck" in lf.get("why_forced", ""), lf
            # ⑤ and so does the machine-wide deploy log
            log = open(lf["log"], encoding="utf-8", errors="replace").read()
            assert "FORCED" in log and "deadline expired" in log, log
            assert "zz-esc/n1" in log, log
    finally:
        supervisor.force_quiesce_for_restart = real_q
    reset_machine()


check("☠ deadline: expired on a BUSY machine → escalates through the SAME "
      "quiesce, and the record says so",
      _an_expired_deadline_on_a_BUSY_machine_escalates_through_the_same_quiesce)


def _the_escalation_wakes_the_agents_it_cut():
    """☠ CONSTRAINT 4. An escalated deploy has no caller to be told "go nudge
    them", so it leaves the machine able to pick itself back up: a one-shot
    restart wake on every agent it stopped, armed BEFORE the spawn (the
    deploy Stop-Processes this backend, so a wake armed after it may never be
    written at all).

    The control is the agent that was NOT cut: if a wake were armed for
    everyone, "the cut agents are woken" would be true for a feature that
    simply woke the world."""
    from orgtree import restart_wake                            # noqa: PLC0415
    reset_machine()
    o, slug = make_org("zz wake", sub=True)
    real_q = supervisor.force_quiesce_for_restart

    def fast_q(exclude=None, timeout=supervisor.FORCE_QUIESCE_TIMEOUT_S,
               why=""):
        return real_q(exclude=exclude, timeout=0.4, why=why)
    supervisor.force_quiesce_for_restart = fast_q
    try:
        with _SpawnSpy() as spy:
            supervisor.arm_prime_restart(slug, "boss", "org", "urgent",
                                         deadline_minutes=10)
            busy_node(slug, "worker")           # cut
            supervisor.state(slug, "boss")      # live, idle — NOT cut
            with supervisor._prime_lock:
                d = supervisor._prime_read()
                d["armed"]["deadline_ts"] = time.time() - 1
                supervisor._prime_write(d)
            supervisor._prime_tick()
            assert _deploys(spy.argv), spy.argv
            wakes = restart_wake._wakes_read().get("wakes") or {}
            assert f"{slug}:worker" in wakes, (
                "the escalation cut this agent and left it idle on the new "
                f"build with nobody to nudge it: {sorted(wakes)}")
            assert "deadline expired" in (
                wakes[f"{slug}:worker"].get("reason") or ""), wakes
            # ☠ the control: an agent that was NOT working is not woken
            assert f"{slug}:boss" not in wakes, (
                "a wake was armed for an agent the escalation never cut — "
                f"this check would pass for a feature that wakes everyone: "
                f"{sorted(wakes)}")
            # and the record names who was woken
            d = json.load(open(supervisor._prime_path(), encoding="utf-8"))
            assert d["last_fired"]["woken"] == [f"{slug}/worker"], \
                d["last_fired"]
            # …as does the arming org's own event log (the third place)
            ev = store.load_org(slug).d["events"][-1]
            assert ev["op"] == "self_restart_forced", ev
            assert ev["detail"]["escalated"] is True, ev
            assert ev["detail"]["woken"] == [f"{slug}/worker"], ev
            assert "deadline expired" in ev["detail"]["why"], ev
    finally:
        supervisor.force_quiesce_for_restart = real_q
        with restart_wake._wakes_lock:
            w = restart_wake._wakes_read()
            w["wakes"] = {}
            restart_wake._wakes_write(w)
        try:
            store.delete_org(slug)
        except Exception:                                    # noqa: BLE001
            pass
    reset_machine()


check("☠ deadline: the escalation WAKES every agent it cut, and only those "
      "(control pair)", _the_escalation_wakes_the_agents_it_cut)


def _a_manual_force_does_not_wake_anyone():
    """The other half of constraint 4, and a deliberate asymmetry rather than
    an oversight: a manual `force=true` has a CALLER. It reads the result,
    which tells it in as many words that the agents stall and it must go
    nudge them. Arming wakes there would spend a turn per cut agent on top of
    a human decision that was already made with the cost in view."""
    src = open(supervisor.__file__, encoding="utf-8").read()
    body = src[src.index("def _fire_prime("):]
    body = body[:body.index("\ndef _log_escalation_to_org(")]
    assert "_wake_the_cut(" in body, \
        "the escalation no longer wakes the agents it cut — an unattended " \
        "deploy would leave every one of them silently idle"
    from orgtree import api as _apimod                          # noqa: PLC0415
    api = open(_apimod.__file__, encoding="utf-8").read()
    fb = api[api.index("def _forced_self_restart("):]
    fb = fb[:fb.index("\n@app.post")]
    assert "_wake_the_cut" not in fb, \
        "the MANUAL force path now arms wakes too — that spends a turn per " \
        "cut agent on a decision whose caller was already told to nudge them"


check("deadline: the wake-up is the ESCALATION's job, not the manual force's "
      "(the asymmetry is deliberate)", _a_manual_force_does_not_wake_anyone)


def _a_mailhub_deadline_escalates_without_cutting_anyone():
    """☠ THE INERT SWITCH. `deadline_minutes` on a target='mailhub' prime
    must not be a control that reads fine, arms fine and silently does
    nothing — the exact shape this tree keeps getting caught by.

    The hub leg rebuilds a container and no agent turn runs through it, so
    there is nothing to quiesce and quiescing would be pure damage. But the
    ordinary path still routes a mailhub prime through
    `_claim_quiet_machine`, which refuses a BUSY machine whatever the
    target — so without the escalation a mailhub prime on a permanently busy
    box waits exactly as forever as an org one. Both halves are asserted:
    it FIRES, and it cuts NOBODY."""
    reset_machine()
    with _SpawnSpy() as spy:
        supervisor.arm_prime_restart("orgA", "boss", "mailhub", "hub fix",
                                     deadline_minutes=10)
        st = busy_node("zz-hub", "n1")
        supervisor._prime_tick()
        assert not spy.argv, "it fired before the deadline expired"
        with supervisor._prime_lock:
            d = supervisor._prime_read()
            d["armed"]["deadline_ts"] = time.time() - 1
            supervisor._prime_write(d)
        supervisor._prime_tick()
        # ① it fired despite the busy machine
        assert spy.argv, (
            "a mailhub prime's deadline expired on a busy machine and "
            "NOTHING happened — the option is inert on this target")
        assert any("docker" in " ".join(a).lower() for a in spy.argv), spy.argv
        # ② …and cut nobody doing it
        assert st["busy"], \
            "a mailhub escalation interrupted an agent for a container rebuild"
        assert supervisor._deploy_done.is_set(), \
            "a mailhub escalation held every org's turns for a deploy that " \
            "never touches them"
        # ③ the record still says it escalated — this is the deploy that most
        #    needs explaining, and it quiesced nobody to infer it from
        d = json.load(open(supervisor._prime_path(), encoding="utf-8"))
        lf = d["last_fired"]
        assert lf.get("escalated") is True, lf
        assert lf.get("cut") == [] and lf.get("woken") == [], lf
    reset_machine()


check("☠ deadline: a MAILHUB deadline escalates without cutting anyone — the "
      "option is not inert on that target",
      _a_mailhub_deadline_escalates_without_cutting_anyone)


def _the_escalation_never_leaves_the_machine_held():
    """The orphaned hold again, on the new path. The escalation's hold is
    taken by `force_quiesce_for_restart` and settled by `launch_self_restart`
    — two different owners from the ordinary path's, and a double release
    would readmit turns INTO a live deploy while a missing one silences every
    org for DEPLOY_HOLD_MAX."""
    reset_machine()
    real_q = supervisor.force_quiesce_for_restart

    def fast_q(exclude=None, timeout=supervisor.FORCE_QUIESCE_TIMEOUT_S,
               why=""):
        return real_q(exclude=exclude, timeout=0.3, why=why)
    supervisor.force_quiesce_for_restart = fast_q
    try:
        # ① the launch REFUSES — its one-at-a-time window is hot, so nothing
        #    is spawned and nothing will ever release the hold
        supervisor.arm_prime_restart("orgA", "boss", "org", "r",
                                     deadline_minutes=10)
        busy_node("zz-hold", "n1")
        with supervisor._prime_lock:
            d = supervisor._prime_read()
            d["armed"]["deadline_ts"] = time.time() - 1
            supervisor._prime_write(d)
        supervisor._self_restart_at[0] = time.time()   # rate limit hot
        supervisor._prime_tick()
        assert supervisor._deploy_done.is_set(), \
            "a rate-limited escalation walked away holding the machine"
        # …and it cut nobody on the way to that refusal
        assert supervisor.state("zz-hold", "n1")["busy"], \
            "a rate-limited escalation still interrupted an agent"
        assert supervisor.primed_restart() is not None, \
            "the prime was spent on a refusal"

        # ② the real thing: the launch adopts the hold, and the (already
        #    exited) interlock child releases it
        supervisor._self_restart_at[0] = 0.0
        supervisor._prime_tick()
        for _ in range(40):
            if supervisor._deploy_done.is_set():
                break
            time.sleep(0.05)
        assert supervisor._deploy_done.is_set(), \
            "the escalated deploy's window never released the hold"
    finally:
        supervisor.force_quiesce_for_restart = real_q
    reset_machine()


check("☠ deadline: a refused escalation cuts nobody and hands the hold back; "
      "a real one hands it to the deploy window",
      _the_escalation_never_leaves_the_machine_held)


def _the_deadline_reaches_the_card_the_gate_and_the_status():
    """The surface. A deadline you cannot see is a forced deploy nobody knows
    is scheduled."""
    card = next(t for t in mcptool.TOOLS
                if t["name"] == "orgtree_prime_restart")
    props = card["inputSchema"]["properties"]
    assert props.get("deadline_minutes", {}).get("type") == "integer", props
    assert "default" not in props["deadline_minutes"], \
        "the card gives deadline_minutes a default — every prime ever armed " \
        "would then carry a scheduled forced deploy"
    assert "deadline_minutes" not in card["inputSchema"].get("required", [])
    d = card["description"]
    for needle in ("NOBODY WILL BE PRESENT", "WOKEN AGAIN", "NEVER escalates"):
        assert needle in d, f"the card does not say {needle!r}"
    # the ARM event records the deadline, or a reader a week later cannot
    # tell why the machine was cut
    o, slug = make_org("zz dl surface")
    o.prime_restart_gate("boss", "arm", target="org", reason="ship it",
                         deadline_minutes=20)
    ev = o.d["events"][-1]
    assert ev["op"] == "prime_restart_arm", ev
    assert ev["detail"]["deadline_minutes"] == 20, ev
    assert ev["detail"]["reason"] == "ship it", ev
    # …and a deadline-less arm records no deadline field at all
    o.prime_restart_gate("boss", "arm", target="org")
    assert "deadline_minutes" not in o.d["events"][-1]["detail"], \
        o.d["events"][-1]
    store.delete_org(slug)
    # and the status answer says it out loud, both ways
    reset_machine()
    supervisor.arm_prime_restart("orgA", "boss", "org", "r",
                                 deadline_minutes=45)
    pr = supervisor.primed_restart()
    assert pr["deadline_minutes"] == 45 and pr["deadline_ts"], pr
    reset_machine()
    supervisor.arm_prime_restart("orgA", "boss", "org", "r")
    assert "deadline_ts" not in supervisor.primed_restart()
    reset_machine()


check("deadline: reaches the card, the arm event and the status projection "
      "(control pair)", _the_deadline_reaches_the_card_the_gate_and_the_status)


# ---------------------------------------------------------------------------
reset_machine()
assert _no_deploy.installed(), \
    "the deploy interlock was left uninstalled for whatever runs next"
print(f"\n{PASS} passed, {len(FAIL)} failed")
if FAIL:
    for f in FAIL:
        print("  ✗ " + f)
    sys.exit(1)
# ⚠ THE TOTAL LINE, in the exact words `tools/run_tests.py` parses
# (`ALL <n> CHECKS PASS`). Without it the runner prints this suite with a
# BLANK check count — which is what a suite cut off halfway also looks like,
# so a truncated run would read as a pass in the tier summary. Two suites in
# this tree already have that defect and it is not worth adding a third.
print(f"\nALL {PASS} CHECKS PASS")
