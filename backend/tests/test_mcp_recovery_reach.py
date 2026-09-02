"""How far the spurious-EOF recovery in b236c72 actually reaches.

Hermetic: drives the state functions directly, no CLI or network.

    python backend/tests/test_mcp_recovery_reach.py

The recovery is correct where it fires. These tests record WHERE IT DOES NOT,
so the bounded-unobservable redesign is scoped against measurements rather than
against the commit message.

⚠ READ THIS FIRST — THE TRIGGER THIS SUITE WAS BUILT AROUND NO LONGER REAPS.
`active` was held to mean OS-LIVE, so a closed channel on a running process
now leaves the generation OWNED and merely `loading`; only a CONFIRMED exit
empties the seat. A spurious stdout EOF therefore cannot produce the
reaped-but-live generation these tests recover from, and the narrow reach
measured below is no longer the thing standing between a live agent and a
permanent terminal state — the fix for that is upstream, in not reaping it.

The measurements are kept, and the helper now MANUFACTURES the reaped-but-live
state (observed exit, then the handle reports alive again) rather than getting
it free from the EOF path. They still earn their place: the recovery branch is
defence-in-depth for anything that empties the seat in future, and its reach is
worth knowing before someone relies on it.

    republish via _names             accepted=True   readiness=recovered
    republish via _server (refresh)  accepted=False  readiness=process-ended
    republish via _unknown           accepted=False  readiness=process-ended

And the reach is narrower still once you ask which lane can reach `_names` at
all after the trigger fires:

  · CLAUDE — `_on_proc_exit` is called from the stdout pump's `finally` at
    warmpool.py:391, i.e. stdout EOF IS the trigger. Every Claude caller of
    `_mcp_tool_count_names` reads stdout (supervisor.py:8425 cold pump,
    :8791 main loop, warmpool.py:375 warm pump). So the event that falsely
    reaps the generation also destroys every channel that could recover it:
    recovery is structurally unreachable on this lane. The commit title stays
    true for Claude — a process reported dead while still running still cannot
    speak again, because its mouth is what closed.

  · CODEX — the trigger is `client.on_exit` (warmpool.py:1698), a callback
    independent of the JSON-RPC transport, and the recovery callers
    (warmpool.py:1810, :1886) are ordinary requests. Recovery genuinely works
    here — but only through `_names`, so a generation whose first post-reap
    event is a FAILED refresh stays terminal until a successful enumerate
    happens to arrive.

Neither is an error in the patch. Both bound how much of the invariant it can
carry, which is the case for finishing the job in the redesign.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

TMP = tempfile.mkdtemp(prefix="orgtree-mcp-reach-")
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

TOOLS = ["mcp__a__one", "mcp__b__two"]


class LiveProc:
    """Demonstrably still running: `poll()` returns None.

    `die`/`revive` exist only so the helper can MANUFACTURE a reaped-but-live
    generation, which the product no longer produces on its own — see header.
    """

    def __init__(self) -> None:
        self._alive = True

    def poll(self):                                          # noqa: ANN201
        return None if self._alive else 0

    def die(self) -> None:
        self._alive = False

    def revive(self) -> None:
        self._alive = True


class DeadProc:
    """Demonstrably gone: `poll()` returns an exit code."""

    def poll(self) -> int:
        return 0


class McpRecoveryReachTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        org = store.create_org("zz mcp reach")
        org.hire(USER, None, "haiku", 5, "agent")
        store.save_org(org)
        cls.slug = org.d["slug"]
        cls.nid = "agent"

    def setUp(self) -> None:
        self.saved_stream = S.stream
        S.stream = lambda slug, nid, payload: None
        st = S.state(self.slug, self.nid)
        with S._state_lock:
            for key in list(st):
                if key.startswith("mcp_"):
                    st.pop(key, None)

    def tearDown(self) -> None:
        S.stream = self.saved_stream

    def _spuriously_reap(self, owner: object) -> None:
        """Publish a real surface, then MANUFACTURE a reaped-but-live state.

        Constructed, not observed: the handle reports an exit at teardown —
        the only thing that empties a seat — and reports itself alive again
        afterwards. Nothing in the product produces this today; see the header.
        """
        S._mcp_tool_count_begin(
            self.slug, self.nid, owner, "codex", "mcpServerStatus/list",
            "starting")
        S._mcp_tool_count_names(
            self.slug, self.nid, owner, TOOLS, "codex",
            "mcpServerStatus/list")
        was_alive = getattr(owner, "poll", lambda: 0)() is None
        if was_alive:
            owner.die()
        S._mcp_tool_count_end(self.slug, self.nid, owner)
        if was_alive:
            owner.revive()
        self.assertEqual(
            S.state(self.slug, self.nid).get("mcp_readiness_state"),
            "process-ended",
            "precondition: the generation was reaped into a terminal state")
        self.assertIsNone(
            S.state(self.slug, self.nid).get("mcp_tool_owner"),
            "precondition: the seat is empty")

    def test_a_full_republish_recovers_the_live_generation(self) -> None:
        """The path b236c72 fixes. Positive control for the two below."""
        proc = LiveProc()
        self._spuriously_reap(proc)

        self.assertTrue(S._mcp_tool_count_names(
            self.slug, self.nid, proc, TOOLS, "codex",
            "mcpServerStatus/list"))
        st = S.state(self.slug, self.nid)
        self.assertIs(st.get("mcp_tool_owner"), proc)
        self.assertEqual(st.get("mcp_readiness_state"), "recovered")

    def test_a_dead_generation_is_never_resurrected(self) -> None:
        """Liveness must be proven. The reason recovery is safe at all."""
        proc = DeadProc()
        self._spuriously_reap(proc)

        self.assertFalse(S._mcp_tool_count_names(
            self.slug, self.nid, proc, TOOLS, "codex",
            "mcpServerStatus/list"))
        self.assertEqual(
            S.state(self.slug, self.nid).get("mcp_readiness_state"),
            "process-ended")

    def test_a_count_only_refresh_cannot_recover(self) -> None:
        """RefreshMcpTools leaves a live generation terminal. Documents reach.

        Not a regression — `b236c72` never claimed this path. It matters
        because on the Claude lane `_server` is the ONLY publisher an agent can
        reach mid-session, so a lane whose recovery lives solely in `_names`
        has no reachable recovery there at all.
        """
        proc = LiveProc()
        self._spuriously_reap(proc)

        accepted = S._mcp_tool_count_server(
            self.slug, self.nid, proc, "a", 5, "claude", "RefreshMcpTools")
        st = S.state(self.slug, self.nid)

        self.assertFalse(accepted)
        self.assertIsNone(st.get("mcp_tool_owner"))
        self.assertEqual(st.get("mcp_readiness_state"), "process-ended",
                         "a live process is still pinned in a terminal state")

    def test_a_failed_refresh_cannot_recover(self) -> None:
        """A generation whose first post-reap event fails stays terminal."""
        proc = LiveProc()
        self._spuriously_reap(proc)

        accepted = S._mcp_tool_count_unknown(
            self.slug, self.nid, proc, "codex", "mcpServerStatus/list",
            "Codex runtime inventory unavailable: TimeoutError")
        st = S.state(self.slug, self.nid)

        self.assertFalse(accepted)
        self.assertEqual(st.get("mcp_readiness_state"), "process-ended")

    def test_recovery_never_displaces_an_adopted_successor(self) -> None:
        """The clause that keeps recovery from resurrecting a predecessor."""
        old = LiveProc()
        self._spuriously_reap(old)
        new = LiveProc()
        S._mcp_tool_count_begin(
            self.slug, self.nid, new, "codex", "mcpServerStatus/list",
            "replacement starting")

        self.assertFalse(S._mcp_tool_count_names(
            self.slug, self.nid, old, TOOLS, "codex",
            "mcpServerStatus/list"))
        self.assertIs(S.state(self.slug, self.nid).get("mcp_tool_owner"), new)


if __name__ == "__main__":
    unittest.main(verbosity=2)
