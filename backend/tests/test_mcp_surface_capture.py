"""Durable last-turn MCP surface must survive the generation's own teardown.

Hermetic: no CLI, no fake CLI, no network, no listener, no operator data — these
drive the state functions directly, so they hold regardless of provider.

    python backend/tests/test_mcp_surface_capture.py

STATUS ON `7305e9a`: `test_eof_before_capture_still_yields_the_final_surface`
FAILS. That is the point — it is the open second defect, handed over as an
executable statement of the contract rather than a prose description. The
sibling ordering test passes today and must keep passing after any fix.

THE ORDERING UNDER TEST (supervisor.py:9408-9435, and the Codex twin at
:7374-7402). A turn whose process DIES at the boundary rather than parking:

    warmpool.discard(...)         # kills; does NOT clear the MCP surface
    proc.wait()                   # 9408  main thread blocks until dead
      ↕ pump thread sees stdout EOF -> warmpool._on_proc_exit
        -> _mcp_tool_count_end(slug, nid, wp.proc)   # warmpool.py:1655
                                  #       pops owner AND names
    _mcp_tool_surface_for_owner(slug, nid, proc)     # 9435  main thread

`proc.wait()` returns only once the process is dead, so by the time the main
thread reaches the capture the pump has almost certainly already seen EOF:
teardown does not merely race the capture, it reliably WINS it.

WHY IT MATTERS MORE THAN A MISSED WRITE. At supervisor.py:11162-11168 a `None`
names-snapshot does not skip the durable write — it POPS `last_turn_mcp_tools`
and `last_turn_mcp_fingerprint`. So an exiting turn ERASES the baseline an
earlier parking turn recorded. A node whose turns end by exit is therefore not
merely never-baselined but repeatedly stripped, leaving the readiness gate
permanently at `no-baseline` (fail-open) on exactly the nodes most likely to
have been replaced.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

TMP = tempfile.mkdtemp(prefix="orgtree-mcp-capture-")
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

TOOLS = ["mcp__orgtree__message", "mcp__orgtree__status"]


class _Owner:
    """A stand-in provider process that can DIE.

    `poll()` is the only thing the state machine reads to decide whether a
    generation has ended, so an owner without one is UNOBSERVABLE — and since
    unit 2 an unobservable owner keeps its seat, its inventory and its
    ownership. These tests used bare `object()` owners, so after unit 2 landed
    `_mcp_tool_count_end` stopped reaping them: every assertion below was
    silently satisfied by the LIVE entry and none of them reached the stash
    they exist to pin. Measured: mutant R4 (drop `final["names"] = None` from
    `_mcp_tool_count_server`) survived all six suites.

    Every test here is about what survives a generation's DEATH, so its owner
    has to be able to die. `die()` is called at the point the real process
    would have exited — before the EOF/teardown call, never after.
    """

    def __init__(self, alive: bool = True) -> None:
        self._alive = alive

    def poll(self):                                     # noqa: ANN201
        return None if self._alive else 0

    def die(self) -> None:
        self._alive = False


class McpSurfaceCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        org = store.create_org("zz mcp capture")
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

    def _run_a_turn(self, owner: object) -> None:
        """Adopt a generation and let it publish its own real surface."""
        S._mcp_tool_count_begin(
            self.slug, self.nid, owner, "claude", "system/init.tools",
            "starting")
        S._mcp_tool_count_names(
            self.slug, self.nid, owner, TOOLS, "claude", "system/init.tools")
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, owner),
            (2, TOOLS), "precondition: the live generation owns its surface")

    def test_capture_before_eof_yields_the_final_surface(self) -> None:
        """The benign ordering — passes today, must keep passing."""
        proc = _Owner()
        self._run_a_turn(proc)

        captured = S._mcp_tool_surface_for_owner(self.slug, self.nid, proc)
        proc.die()      # the process really exits first
        S._mcp_tool_count_end(self.slug, self.nid, proc)

        self.assertEqual(captured, (2, TOOLS))

    def test_eof_before_capture_still_yields_the_final_surface(self) -> None:
        """The live ordering — FAILS on 7305e9a. This is the open defect.

        The generation published a perfectly good surface and the turn
        completed on it. Only the order of two threads decides whether that
        surface is remembered, and the order that actually happens is this one.
        """
        proc = _Owner()
        self._run_a_turn(proc)

        # pump thread: stdout EOF -> _on_proc_exit -> _mcp_tool_count_end
        proc.die()      # the process really exits first
        S._mcp_tool_count_end(self.slug, self.nid, proc)
        # main thread: the turn's `finally`, after proc.wait()
        captured = S._mcp_tool_surface_for_owner(self.slug, self.nid, proc)

        self.assertEqual(
            captured, (2, TOOLS),
            "teardown destroyed the surface this turn was about to record; "
            "the boundary then POPS last_turn_mcp_tools (supervisor:11167), "
            "erasing any baseline an earlier parking turn had written")

    def test_a_later_generation_never_inherits_the_recovered_surface(
            self) -> None:
        """Anti-regression for whatever fix lands: no stash may leak forward.

        Any recovery path must stay generation-scoped, or it reintroduces
        exactly the staleness 7305e9a removed — in a rarer, harder form.
        Note this is precisely what keying a stash on ``id(owner)`` rather
        than a retained reference would break, since CPython reuses ids.
        """
        old = _Owner()
        self._run_a_turn(old)
        old.die()      # the process really exits first
        S._mcp_tool_count_end(self.slug, self.nid, old)

        new = _Owner()
        S._mcp_tool_count_begin(
            self.slug, self.nid, new, "claude", "system/init.tools",
            "replacement starting")

        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, new),
            (None, None),
            "the replacement inherited a surface it never registered")

    def test_a_foreign_owner_recovers_nothing(self) -> None:
        """Neither the live read nor any recovery path answers a stranger."""
        proc = _Owner()
        self._run_a_turn(proc)
        proc.die()      # the process really exits first
        S._mcp_tool_count_end(self.slug, self.nid, proc)

        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, _Owner()),
            (None, None))

    def test_a_live_surface_never_names_tools_it_cannot_count(self) -> None:
        """Coherence, stated without presuming which repair is right.

        `_mcp_tool_count_unknown` ("publish a truthful unknown without
        replacing the process owner") zeroes the count but does NOT pop
        `mcp_tool_names`, and it is reachable AFTER a successful enumeration:
        warmpool.py:1798-1812 calls it when a LATER `mcp_tool_names()` refresh
        raises. The generation is alive, its owner is still current, and its
        surface reads `(None, [<two names>])` — two tools named, no count.

        The boundary then writes `last_turn_mcp_tools` while popping
        `last_turn_mcp_tool_count`, so the node carries a full tool list and no
        count. Note this producer is NOT the one `ae101e6` introduced; it is on
        the Codex path and predates it. Fixing only the unobserved-turn path
        leaves this one standing.

        Two coherent repairs, both of which satisfy this test, and the choice
        belongs to whoever owns the landing:
          - pop the names too (if you cannot enumerate, you cannot vouch for
            the previous enumeration either — the `_mcp_tool_count_server`
            precedent), or
          - keep the names and let the count follow from them, since a
            generation-correct name set already implies its own total.
        """
        proc = _Owner()
        self._run_a_turn(proc)

        S._mcp_tool_count_unknown(
            self.slug, self.nid, proc, "codex", "mcpServerStatus/list",
            "Codex runtime inventory unavailable: TimeoutError")

        st = S.state(self.slug, self.nid)
        with S._state_lock:
            self.assertIs(st.get("mcp_tool_owner"), proc,
                          "precondition: the process is still live and owned")

        count, names = S._mcp_tool_surface_for_owner(self.slug, self.nid, proc)
        if names is not None:
            self.assertIsNotNone(
                count,
                "a live generation named its tools while reporting no count; "
                "the boundary then records the list and pops the count")

    def test_a_count_only_refresh_invalidates_the_recovered_names(
            self) -> None:
        """A recovered surface may not outlive the identities it names.

        `_mcp_tool_count_server` is the Claude `RefreshMcpTools` path — the MCP
        RELOAD path — and it pops `mcp_tool_names` deliberately, for the reason
        stated at its own call site: the count-only shape "proves the total but
        not canonical identities. Never keep an earlier exact set under a
        changed runtime."

        A durable copy written at publish time must honour that invalidation.
        If it keeps the pre-refresh names while tracking the post-refresh
        count, the boundary recovers a surface the runtime explicitly refused
        to vouch for — and one whose count and name list contradict each other.
        """
        proc = _Owner()
        self._run_a_turn(proc)

        # the agent reloads MCP: the total is proven, the identities are not
        S._mcp_tool_count_server(
            self.slug, self.nid, proc, "extra", 5, "claude",
            "RefreshMcpTools")
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, proc),
            (7, None),
            "the LIVE read is honest: the exact names were invalidated")

        # the process then dies at the boundary and the capture falls back
        proc.die()      # the process really exits first
        S._mcp_tool_count_end(self.slug, self.nid, proc)
        count, names = S._mcp_tool_surface_for_owner(
            self.slug, self.nid, proc)

        self.assertIsNone(
            names,
            "the recovered surface resurrected names RefreshMcpTools had "
            "just invalidated; these become the gate's durable baseline")
        if names is not None:                       # pragma: no cover
            self.assertEqual(
                count, len(names),
                "a recovered surface whose count contradicts its own names")


if __name__ == "__main__":
    unittest.main(verbosity=2)
