"""UNIT 2 — a channel closing is not a process ending.

    python backend/tests/test_mcp_unit2_contract.py

Hermetic: no CLI, no fake CLI, no network, no listener. These drive the state
functions directly, so they hold on every provider.

THE DEFECT. `_mcp_tool_count_end` is the only producer of the terminal MCP
readiness state, and it published `process-ended` unconditionally. On the
Claude lane its trigger is a stdout EOF raised from the pump thread's `finally`
(`warmpool.py:391`) — and a channel closing is not a process exiting. A CLI
that closes stdout while its background children are still running is alive;
`proc exit reason="background-children"` is a measured case on this machine.
So a live agent was published as ENDED, on the strength of an event that proves
only that we stopped being able to hear it.

THE SHAPE OF THE FIX, and why it is this shape:

  · The claim is narrowed to what was observed. `poll()` returning an exit code
    proves the process is gone and still publishes `process-ended`. `poll()`
    returning None proves it is alive. Anything else proves neither, and the
    unobservable case is NOT folded into "dead" — asserting an exit nothing saw
    is the same error one layer along.

  · The weaker state is `withdrawn`, and it is true in every case, which is why
    it needs no timer, no probe and no kill to justify. Before this function is
    reached, `_on_proc_exit` has already removed the generation from `_pool`
    and `_serving`: it can never be claimed for another turn, and nothing will
    publish into its inventory again. Withdrawn from service, inventory final —
    a statement about OUR bookkeeping, which we can actually make.

  · It could not have been `loaded`. The last-known surface really is accurate
    at the instant of the EOF (MCP tools do not unregister because a pipe
    closed), but it decays and nothing can tell us it has: the channel that
    would report a RefreshMcpTools or a crashed MCP server is the one that just
    closed. `loaded` is present tense. Asserting it would be a cached answer
    standing in for an abstention — the defect class this whole chain removes.

  · No re-observation probe exists on the Claude lane and none is pretended.
    Every caller of `_mcp_tool_count_names` reads the stdout that just closed
    (`supervisor.py:8425`, `:8791`, `warmpool.py:375`), so a "degraded pending
    a probe" state would be permanently pending there. The recovery that does
    exist (`b236c72`) is event-driven: if the live process speaks again, it is
    accepted again.

  · Recycling stays out. Live background children are a concrete unsafe state
    and at least one node here carries a standing instruction not to be killed
    without user authority. An accounting function must not be able to end
    processes; the lifecycle owner has the attempt counters and the
    background-child awareness that a safe recycle needs.

WHAT IS DELIBERATELY NOT BUILT, so nobody reads a gap as an oversight. The
design (`unit2-design.md` §4b) also specifies a bounded re-observation probe on
the CODEX lane, where the JSON-RPC transport survives the trigger and a probe
genuinely can be scheduled. It is not implemented here. `b236c72` already
recovers that lane the moment any ordinary request enumerates, which on a
serving node is the next turn; a dedicated probe would add retry, backoff and
storm-guard machinery to accelerate a recovery that already arrives. §§6-7
below therefore pin the ABSENCE — that nothing schedules, and that no state
requiring a probe was introduced — so that if the probe is ever added these
tests fail loudly rather than passing vacuously.

    §1  a spurious EOF never reports the surface as ready/loaded
    §2  a spurious EOF never claims the process ended
    §3  the durable last-known surface survives it
    §4  nothing in this layer is keyed on elapsed time
    §5  the readiness layer touches nothing but `poll()` — it cannot kill
    §6  no state was introduced that requires a probe to resolve
    §7  the transport is never probed; the bound is structural, not a counter
    §8  an OBSERVED death still reaches `process-ended` immediately

The recovery path itself (`b236c72`, and the pre-lock window its liveness probe
must not be gated on) is covered in `test_mcp_tool_count.py`, next to the other
generation-ownership tests, rather than duplicated here.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

TMP = tempfile.mkdtemp(prefix="orgtree-mcp-unit2-")
HOME = os.path.join(TMP, "home")
DATA = os.path.join(TMP, "data")
os.makedirs(HOME, exist_ok=True)
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["ORGTREE_DATA"] = DATA
os.environ["USERPROFILE"] = HOME
os.environ["HOME"] = HOME
os.environ["ORGTREE_STEER_HOOK"] = "0"

from orgtree import store, supervisor as S  # noqa: E402
from orgtree.ledger import USER  # noqa: E402

TOOLS = ["mcp__orgtree__orgtree_message", "mcp__orgtree__orgtree_status"]

# Every readiness state that would tell a reader the surface is usable NOW.
# `_mcp_tool_count_end` may publish none of them.
LIVE_STATES = {"ready", "recovered", "loaded", "waiting", "initializing"}


class RecordingProc:
    """A process that records every attribute the readiness layer touches.

    Anything beyond `poll` raises `AttributeError` and is logged, so §5 states
    the strong form — the layer cannot kill, terminate or signal, because it
    never reaches for the means to.
    """

    def __init__(self, alive: bool = True) -> None:
        self.touched: list[str] = []
        self.polls = 0
        self._alive = alive

    def poll(self):                                          # noqa: ANN201
        self.polls += 1
        return None if self._alive else 0

    def die(self) -> None:
        self._alive = False

    def __getattr__(self, name: str):                        # noqa: ANN204
        self.touched.append(name)
        raise AttributeError(name)


class NoSchedulingThreading:
    """`supervisor.threading` stand-in that refuses to schedule anything.

    A timer or a background thread started from this layer would mean some
    transition is keyed on elapsed time. The repo already ruled against that
    for provider processes — `supervisor.py` ~8503 picks `idle_cap = BG_IDLE if
    nbg else TURN_IDLE`, commented "live background work ⇒ silence is expected,
    not a wedge" — and a second, less aware timeout here would contradict it.
    """

    def __init__(self, real) -> None:                        # noqa: ANN001
        self._real = real

    def __getattr__(self, name: str):                        # noqa: ANN204
        if name in ("Timer", "Thread"):
            raise AssertionError(
                "the MCP readiness layer scheduled a " + name + ": unit 2 is "
                "event-driven and no transition may be keyed on elapsed time")
        return getattr(self._real, name)


class McpUnit2ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        org = store.create_org("zz mcp unit2")
        org.hire(USER, None, "haiku", 5, "agent")
        store.save_org(org)
        cls.slug = org.d["slug"]
        cls.nid = "agent"

    def setUp(self) -> None:
        self.streamed: list[dict] = []
        self.saved_stream = S.stream
        S.stream = lambda slug, nid, payload: self.streamed.append(payload)
        st = S.state(self.slug, self.nid)
        with S._state_lock:
            for key in list(st):
                if key.startswith("mcp_"):
                    st.pop(key, None)

    def tearDown(self) -> None:
        S.stream = self.saved_stream

    def _publish(self, owner: object, provider: str = "claude") -> None:
        """Adopt a generation and let it publish a real surface."""
        S._mcp_tool_count_begin(
            self.slug, self.nid, owner, provider, "system/init.tools", "start")
        S._mcp_tool_count_names(
            self.slug, self.nid, owner, TOOLS, provider, "system/init.tools")
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, owner),
            (2, sorted(TOOLS)), "precondition: the generation owns a surface")

    # §1 ---------------------------------------------------------------------
    def test_a_spurious_eof_never_reports_the_surface_as_live(self) -> None:
        """The state that decays and cannot be re-checked is not asserted."""
        proc = RecordingProc()
        self._publish(proc)
        S._mcp_tool_count_end(self.slug, self.nid, proc, "pump saw EOF")

        st = S.state(self.slug, self.nid)
        self.assertNotIn(
            st.get("mcp_readiness_state"), LIVE_STATES,
            "a surface nothing can re-observe was reported as current")
        self.assertFalse(
            st.get("mcp_readiness_waiting"),
            "a terminal state must not leave a reader waiting")

    # §2 ---------------------------------------------------------------------
    def test_a_spurious_eof_never_claims_the_process_ended(self) -> None:
        """The whole unit, in one assertion — and its reason must say why."""
        proc = RecordingProc()
        self._publish(proc)
        S._mcp_tool_count_end(self.slug, self.nid, proc, "pump saw EOF")

        st = S.state(self.slug, self.nid)
        self.assertEqual(
            st.get("mcp_readiness_state"), "withdrawn",
            "a live process was published as ENDED on the strength of a "
            "closed channel")
        self.assertIn(
            "still running", str(st.get("mcp_readiness_reason")),
            "terminal must not mean silent: the reason has to state what was "
            "actually observed, or an operator sees a dead agent")
        published = [p for p in self.streamed
                     if p.get("kind") == "mcp_readiness"]
        self.assertTrue(
            published and published[-1].get("state") == "withdrawn",
            "the UI was not told; a stored state nobody streams leaves a live "
            "agent showing whatever it showed before")

    def test_an_unobservable_owner_is_not_evidence_of_an_exit(self) -> None:
        """The third value. Not observed is not the same as observed dead."""
        opaque = object()                       # no `poll` to ask
        self._publish(opaque)
        S._mcp_tool_count_end(self.slug, self.nid, opaque, "teardown")

        st = S.state(self.slug, self.nid)
        self.assertEqual(
            st.get("mcp_readiness_state"), "withdrawn",
            "an exit was asserted about a process nothing could observe")
        self.assertIn("could not be observed",
                      str(st.get("mcp_readiness_reason")))

    # §3 ---------------------------------------------------------------------
    def test_the_durable_surface_survives_the_spurious_eof(self) -> None:
        """Regression guard for `ae101e6` + `51757c6`. Already true; stays so.

        The audit that shaped this unit turned on the live/durable split: the
        record is explicitly PAST tense, so it is legitimate exactly where the
        live `loaded` claim is not.
        """
        proc = RecordingProc()
        self._publish(proc)
        S._mcp_tool_count_end(self.slug, self.nid, proc, "pump saw EOF")

        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, proc),
            (2, sorted(TOOLS)),
            "the turn boundary lost the surface this generation published; it "
            "then POPS last_turn_mcp_tools and strips the gate's baseline")

    # §4 ---------------------------------------------------------------------
    def test_nothing_in_this_layer_is_keyed_on_elapsed_time(self) -> None:
        """No timer, no thread — for a live process or a dead one."""
        saved = S.threading
        S.threading = NoSchedulingThreading(threading)
        try:
            for alive in (True, False):
                proc = RecordingProc(alive=True)
                self._publish(proc)
                if not alive:
                    proc.die()
                S._mcp_tool_count_end(self.slug, self.nid, proc, "EOF")
                S._mcp_tool_count_names(
                    self.slug, self.nid, proc, TOOLS, "claude",
                    "system/init.tools")
        finally:
            S.threading = saved

    # §5 ---------------------------------------------------------------------
    def test_the_readiness_layer_cannot_kill_anything(self) -> None:
        """Recycle is not merely refused here — it is unreachable.

        The node this was measured on is under a standing instruction not to be
        killed without user authority, and its background children's results
        can never return once its process is destroyed (`supervisor.py` ~9432
        is FAIL-LOUD about the CLI queueing its own "killed" notice into the
        process being torn down). Policy that important does not belong in an
        accounting function, so the function is not given the means.
        """
        proc = RecordingProc()
        self._publish(proc)
        S._mcp_tool_count_end(self.slug, self.nid, proc, "background-children")
        S._mcp_tool_count_names(
            self.slug, self.nid, proc, TOOLS, "claude", "system/init.tools")

        self.assertEqual(
            proc.touched, [],
            "the readiness layer reached for " + repr(proc.touched) + " on a "
            "live process; it may ask whether a process is running, and "
            "nothing else")

    # §6 ---------------------------------------------------------------------
    def test_no_state_was_introduced_that_needs_a_probe_to_resolve(
            self) -> None:
        """Every published state resolves without re-observation.

        A "degraded, pending a probe" state is only honest on a lane where a
        probe can actually be scheduled, and on Claude none can: stdout EOF IS
        the trigger and every publisher reads stdout. A state whose exit
        condition is unreachable is `loading` that never finishes — the same
        invariant break, quieter. So the terminal states here are reachable in
        one call, with no second observation pending.
        """
        for alive in (True, False):
            proc = RecordingProc()
            self._publish(proc)
            if not alive:
                proc.die()
            S._mcp_tool_count_end(self.slug, self.nid, proc, "EOF")
            st = S.state(self.slug, self.nid)
            self.assertIn(
                st.get("mcp_readiness_state"), ("withdrawn", "process-ended"),
                "an unresolved intermediate state was published by a layer "
                "that has no way to resolve it")

    # §7 ---------------------------------------------------------------------
    def test_the_transport_is_never_probed_and_the_poll_is_bounded(
            self) -> None:
        """Storm-proof structurally: the only probe is a local syscall.

        `poll()` reads the OS process table; it sends nothing to the CLI and
        cannot hang on a wedged transport. Counting it pins that the bound is
        structural rather than a retry counter someone has to maintain — the
        design's §7 ("probe attempts are bounded and cannot storm") holds
        because there are no transport probes to bound.
        """
        proc = RecordingProc()
        self._publish(proc)
        before = proc.polls
        S._mcp_tool_count_end(self.slug, self.nid, proc, "pump saw EOF")

        self.assertLessEqual(
            proc.polls - before, 1,
            "one teardown made more than one liveness observation")
        self.assertEqual(proc.touched, [],
                         "the transport was reached for; a probe on a wedged "
                         "channel is exactly what must not exist here")

    # §8 ---------------------------------------------------------------------
    def test_an_observed_death_still_reaches_process_ended(self) -> None:
        """Negative control. Narrowing the claim must not weaken a real exit.

        Without this the unit would be satisfiable by never saying
        `process-ended` at all, which trades a false positive for a state that
        can never be reached.
        """
        proc = RecordingProc()
        self._publish(proc)
        proc.die()
        S._mcp_tool_count_end(self.slug, self.nid, proc, "process exited")

        st = S.state(self.slug, self.nid)
        self.assertEqual(
            st.get("mcp_readiness_state"), "process-ended",
            "an observed exit no longer reaches the strong terminal state")
        self.assertEqual(st.get("mcp_readiness_reason"), "process exited",
                         "a real exit lost the caller's stated cause")
        self.assertIsNone(st.get("mcp_tool_owner"))

if __name__ == "__main__":
    unittest.main(verbosity=2)
