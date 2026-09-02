"""Generation-safe, realtime runtime MCP inventory contract.

Hermetic: no CLI, network, listener, or operator data.

    python backend/tests/test_mcp_tool_count.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

TMP = tempfile.mkdtemp(prefix="orgtree-mcp-count-")
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
os.environ["ORGTREE_PORT"] = "7491"

from fastapi.testclient import TestClient  # noqa: E402
from orgtree import api, appsettings, codexrun, store, supervisor as S, warmpool as W  # noqa: E402
from orgtree.ledger import USER  # noqa: E402


class McpToolCountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        org = store.create_org("zz mcp count")
        org.hire(USER, None, "haiku", 5, "agent")
        store.save_org(org)
        cls.slug = org.d["slug"]
        cls.nid = "agent"

    def setUp(self) -> None:
        self.events: list[dict] = []
        self.saved_stream = S.stream
        S.stream = lambda slug, nid, payload: self.events.append({
            "slug": slug, "nid": nid, **payload})
        st = S.state(self.slug, self.nid)
        with S._state_lock:
            for key in list(st):
                if (key.startswith("mcp_tool_")
                        or key.startswith("mcp_readiness_")
                        or key == "last_turn_mcp_tool_count"):
                    st.pop(key, None)
        with store.DOC_LOCK:
            org = store.load_org(self.slug)
            node = org.node(self.nid)
            node.pop("last_turn_mcp_tool_count", None)
            node.pop("last_turn_mcp_tools", None)
            node.pop("last_turn_mcp_fingerprint", None)
            store.save_org(org)

    def tearDown(self) -> None:
        S.stream = self.saved_stream

    def test_stepwise_websocket_deltas_and_stale_generation(self) -> None:
        old, new = object(), object()
        S._mcp_tool_count_begin(
            self.slug, self.nid, old, "claude", "system/init.tools",
            "initializing", 4)
        # A real empty runtime list is known zero, not unknown.
        S._mcp_tool_count_names(
            self.slug, self.nid, old, ["Bash", "Read"], "claude",
            "system/init.tools")
        # Separate server refresh rows must repaint before any turn boundary.
        S._mcp_tool_count_server(
            self.slug, self.nid, old, "alpha", 2, "claude",
            "RefreshMcpTools")
        S._mcp_tool_count_server(
            self.slug, self.nid, old, "beta", 1, "claude",
            "RefreshMcpTools")
        S._mcp_tool_count_server(
            self.slug, self.nid, old, "alpha", 1, "claude",
            "RefreshMcpTools")
        S._mcp_tool_count_server(
            self.slug, self.nid, old, "beta", 0, "claude",
            "RefreshMcpTools")

        S._mcp_tool_count_begin(
            self.slug, self.nid, new, "claude", "system/init.tools",
            "relaunching", 4)
        before_stale = len(self.events)
        self.assertFalse(S._mcp_tool_count_server(
            self.slug, self.nid, old, "ghost", 99, "claude",
            "RefreshMcpTools"))
        self.assertFalse(S._mcp_tool_count_end(
            self.slug, self.nid, old, "stale process exited"))
        self.assertEqual(len(self.events), before_stale)
        S._mcp_tool_count_names(
            self.slug, self.nid, new,
            ["Bash", "mcp__alpha__one", "mcp__alpha__two"],
            "claude", "system/init.tools")
        S._mcp_tool_count_end(self.slug, self.nid, new)

        count_events = [e for e in self.events
                        if e["kind"] == "mcp_tool_count"]
        self.assertEqual(
            [e["count"] for e in count_events],
            [None, 0, 2, 3, 2, 1, None, 2, None])
        self.assertTrue(all(isinstance(e.get("emitted_at_ms"), int)
                            for e in count_events))
        self.assertEqual(count_events[1]["last_turn_count"], 4)

    def test_replacement_does_not_inherit_the_dead_generation_names(
            self) -> None:
        """A replaced process starts with NO surface, not its predecessor's.

        The regression (measured 2026-09-02, live CLI replacement): `_begin`
        reset the count and the per-server breakdown but left `mcp_tool_names`
        standing, so a brand-new process that had published nothing reported
        `(None, <predecessor's tools>)` — an unknown count beside a dead
        process's full tool list.
        """
        old, new = object(), object()
        S._mcp_tool_count_begin(
            self.slug, self.nid, old, "claude", "system/init.tools", "start")
        S._mcp_tool_count_names(
            self.slug, self.nid, old,
            ["mcp__orgtree__one", "mcp__alpha__two", "Bash"],
            "claude", "system/init.tools")
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, old),
            (2, ["mcp__alpha__two", "mcp__orgtree__one"]))

        # the CLI process is killed and replaced
        S._mcp_tool_count_begin(
            self.slug, self.nid, new, "claude", "system/init.tools",
            "restarting")
        count, names = S._mcp_tool_surface_for_owner(self.slug, self.nid, new)
        self.assertIsNone(count)
        self.assertIsNone(
            names, "the new generation inherited the dead one's tool names")
        st = S.state(self.slug, self.nid)
        with S._state_lock:
            self.assertIsNone(st.get("mcp_tool_names"))
            self.assertEqual(st.get("mcp_tool_server_counts"), {})

        # …and once the replacement publishes, the surface is ITS own
        S._mcp_tool_count_names(
            self.slug, self.nid, new, ["mcp__orgtree__one"], "claude",
            "system/init.tools")
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, new),
            (1, ["mcp__orgtree__one"]))

    def test_surface_survives_a_process_that_dies_at_the_turn_boundary(
            self) -> None:
        """The turn boundary runs after the process is gone; it must still see it.

        `_mcp_tool_count_end` fires on the pump thread at stdout EOF, while the
        turn captures its surface in a `finally` after `proc.wait()`. On a turn
        whose process DIES rather than parks, EOF wins and the capture used to
        read `(None, None)` — so the turn recorded no durable
        `last_turn_mcp_tools`. Measured live: nodes that park carry a baseline,
        the one node that drains to exit every turn carried none.
        """
        gen = object()
        S._mcp_tool_count_begin(
            self.slug, self.nid, gen, "claude", "system/init.tools", "start")
        S._mcp_tool_count_names(
            self.slug, self.nid, gen,
            ["mcp__orgtree__one", "mcp__alpha__two"], "claude",
            "system/init.tools")
        # stdout EOF: the pump reaps the generation…
        S._mcp_tool_count_end(self.slug, self.nid, gen, "process exited")
        # …and only now does the turn boundary get to capture it
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, gen),
            (2, ["mcp__alpha__two", "mcp__orgtree__one"]))

    def test_final_surface_is_readable_only_by_its_own_generation(self) -> None:
        """The dying generation's handoff must not become a back door.

        A successor must still earn its own surface (that is the whole point of
        clearing names at `_begin`), and a foreign process must read nothing.
        """
        old, new, ghost = object(), object(), object()
        S._mcp_tool_count_begin(
            self.slug, self.nid, old, "claude", "system/init.tools", "start")
        S._mcp_tool_count_names(
            self.slug, self.nid, old, ["mcp__orgtree__one"], "claude",
            "system/init.tools")
        S._mcp_tool_count_end(self.slug, self.nid, old, "process exited")

        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, ghost),
            (None, None))
        S._mcp_tool_count_begin(
            self.slug, self.nid, new, "claude", "system/init.tools", "restart")
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, new),
            (None, None),
            "the successor was answered with its predecessor's final surface")
        # the dead generation can still be closed out correctly
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, old),
            (1, ["mcp__orgtree__one"]))

    def test_reclaiming_the_same_process_keeps_its_surface(self) -> None:
        """The clear above must fire on a NEW generation, never a re-adopted one.

        A warm process is re-adopted by `_begin` on every turn it serves. If
        that cleared the surface, this fix would trade a stale-tools bug for a
        blind-warm-turn bug — the warm path publishes its names once, at its
        own init, and would never publish them again.
        """
        warm = object()
        S._mcp_tool_count_begin(
            self.slug, self.nid, warm, "claude", "system/init.tools", "start")
        S._mcp_tool_count_names(
            self.slug, self.nid, warm, ["mcp__orgtree__one"], "claude",
            "system/init.tools")
        S._mcp_tool_count_begin(
            self.slug, self.nid, warm, "claude", "system/init.tools",
            "claimed again")
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, warm),
            (1, ["mcp__orgtree__one"]))

    def test_foreign_owner_can_neither_publish_nor_read(self) -> None:
        """Negative control: only the current generation may write or be read."""
        live, ghost = object(), object()
        S._mcp_tool_count_begin(
            self.slug, self.nid, live, "claude", "system/init.tools", "start")
        S._mcp_tool_count_names(
            self.slug, self.nid, live, ["mcp__orgtree__one"], "claude",
            "system/init.tools")
        self.assertFalse(S._mcp_tool_count_names(
            self.slug, self.nid, ghost, ["mcp__evil__x"], "claude",
            "system/init.tools"))
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, ghost),
            (None, None))
        # the live generation is untouched by the foreign write
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, live),
            (1, ["mcp__orgtree__one"]))

    def test_claude_refresh_parser_handles_zero_removal(self) -> None:
        content = [{"type": "text", "text": (
            '{"servers":[{"server":"one","toolCount":3},'
            '{"name":"gone","tool_count":0}]}'
        )}]
        self.assertEqual(S._claude_mcp_refresh_counts(content),
                         [("one", 3), ("gone", 0)])
        self.assertEqual(S._claude_mcp_refresh_counts("not json"), [])

    def test_codex_runtime_list_is_exact_paginated_mcp_only(self) -> None:
        client = object.__new__(codexrun.AppServerClient)
        calls: list[tuple[str, dict]] = []
        pages = iter([
            {"data": [
                {"name": "server-b", "tools": {"two": {}, "one": {}}},
                {"name": "configured-only", "tools": None},
            ], "nextCursor": "next"},
            {"data": [{"name": "server-a", "tools": {"tool": {}}}],
             "nextCursor": None},
        ])

        def request(method: str, params: dict) -> dict:
            calls.append((method, params))
            return next(pages)

        client.request = request  # type: ignore[method-assign]
        self.assertEqual(client.mcp_tool_names(), [
            "mcp__server-a__tool", "mcp__server-b__one",
            "mcp__server-b__two"])
        self.assertEqual(calls, [
            ("mcpServerStatus/list", {}),
            ("mcpServerStatus/list", {"cursor": "next"}),
        ])

    def test_success_snapshots_but_failure_does_not(self) -> None:
        owner = object()
        org = store.load_org(self.slug)
        S._mcp_tool_count_begin(
            self.slug, self.nid, owner, "claude", "system/init.tools",
            "initializing")
        S._mcp_tool_count_names(
            self.slug, self.nid, owner,
            ["mcp__orgtree__one", "mcp__orgtree__two"],
            "claude", "system/init.tools")
        st = S.state(self.slug, self.nid)
        st["interrupted"] = False
        S._after_turn(self.slug, self.nid, org, {
            "_mcp_tool_count": 2, "status": "completed",
            "_mcp_tool_names": [
                "mcp__orgtree__one", "mcp__orgtree__two"],
            "_mcp_tool_fingerprint": "fingerprint-one",
            "total_cost_usd": 0, "usage": {}, "duration_ms": 1,
        }, st, 10, on_key=False)
        saved = store.load_org(self.slug).node(self.nid)
        self.assertEqual(saved["last_turn_mcp_tool_count"], 2)
        self.assertEqual(saved["last_turn_mcp_tools"], [
            "mcp__orgtree__one", "mcp__orgtree__two"])
        self.assertEqual(saved["last_turn_mcp_fingerprint"],
                         "fingerprint-one")

        org = store.load_org(self.slug)
        S._after_turn(self.slug, self.nid, org, {
            "_mcp_tool_count": 9, "status": "failed", "is_error": True,
            "_mcp_tool_names": ["mcp__ghost__tool"],
            "_mcp_tool_fingerprint": "fingerprint-two",
            "total_cost_usd": 0, "usage": {}, "duration_ms": 1,
        }, st, 10, on_key=False)
        saved = store.load_org(self.slug).node(self.nid)
        self.assertEqual(saved["last_turn_mcp_tool_count"], 2)
        self.assertEqual(saved["last_turn_mcp_tools"], [
            "mcp__orgtree__one", "mcp__orgtree__two"])
        self.assertEqual(saved["last_turn_mcp_fingerprint"],
                         "fingerprint-one")

    def test_unobserved_surface_does_not_erase_a_known_good_baseline(
            self) -> None:
        """`None` means "not observed this turn", not "this node has no tools".

        The boundary used to pop `last_turn_mcp_tools` whenever the snapshot
        was `None`, so a turn that failed to observe its own surface DESTROYED
        the baseline an earlier turn had recorded. Observation fails on exactly
        the turns whose process dies at the boundary, so a node that never
        parks was repeatedly stripped and its gate sat permanently at
        `no-baseline` — fail-open on the nodes most likely to be replaced.

        A node that genuinely has no MCP tools reports an observed EMPTY list,
        which is recorded as the empty surface it is; that case is unaffected.
        """
        owner = object()
        org = store.load_org(self.slug)
        S._mcp_tool_count_begin(
            self.slug, self.nid, owner, "claude", "system/init.tools", "init")
        st = S.state(self.slug, self.nid)
        st["interrupted"] = False
        S._after_turn(self.slug, self.nid, org, {
            "_mcp_tool_count": 2, "status": "completed",
            "_mcp_tool_names": ["mcp__orgtree__one", "mcp__orgtree__two"],
            "_mcp_tool_fingerprint": "fp-1",
            "total_cost_usd": 0, "usage": {}, "duration_ms": 1,
        }, st, 10, on_key=False)

        # a LATER SUCCESSFUL turn whose surface could not be observed
        org = store.load_org(self.slug)
        S._after_turn(self.slug, self.nid, org, {
            "_mcp_tool_count": None, "status": "completed",
            "_mcp_tool_names": None, "_mcp_tool_fingerprint": "fp-1",
            "total_cost_usd": 0, "usage": {}, "duration_ms": 1,
        }, st, 10, on_key=False)
        saved = store.load_org(self.slug).node(self.nid)
        self.assertEqual(
            saved.get("last_turn_mcp_tools"),
            ["mcp__orgtree__one", "mcp__orgtree__two"],
            "an unobserved turn erased the baseline a good turn recorded")
        self.assertEqual(saved.get("last_turn_mcp_fingerprint"), "fp-1")
        # the COUNT is kept for the same reason and in the same breath: a node
        # carrying a full tool list and no count is a divergence, and this is
        # the value behind the "had N last turn, now loading" chip on exactly
        # the replacement turns where it is most wanted
        self.assertEqual(saved.get("last_turn_mcp_tool_count"), 2)

        # …while an OBSERVED EMPTY surface is still recorded as empty
        org = store.load_org(self.slug)
        S._after_turn(self.slug, self.nid, org, {
            "_mcp_tool_count": 0, "status": "completed",
            "_mcp_tool_names": [], "_mcp_tool_fingerprint": "fp-1",
            "total_cost_usd": 0, "usage": {}, "duration_ms": 1,
        }, st, 10, on_key=False)
        saved = store.load_org(self.slug).node(self.nid)
        self.assertEqual(saved.get("last_turn_mcp_tools"), [])

    def test_durable_surface_does_not_depend_on_teardown_running(self) -> None:
        """The boundary's copy is written at PUBLISH time, not at reap time.

        Teardown is not a reliable hook: `warmpool.discard()` never calls
        `_mcp_tool_count_end`, `kill_node` early-returns on a claimed process
        before reaching it, and a pump thread dying without a clean EOF never
        fires it. So the surface must already be durable before any of that —
        here the generation is replaced with NO `_end` call at all.
        """
        old, new = object(), object()
        S._mcp_tool_count_begin(
            self.slug, self.nid, old, "claude", "system/init.tools", "start")
        S._mcp_tool_count_names(
            self.slug, self.nid, old, ["mcp__orgtree__one"], "claude",
            "system/init.tools")
        # no _mcp_tool_count_end: the discard/kill paths simply never call it
        S._mcp_tool_count_begin(
            self.slug, self.nid, new, "claude", "system/init.tools", "restart")
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, old),
            (1, ["mcp__orgtree__one"]),
            "the dead generation's turn boundary lost its surface")
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, new),
            (None, None))

    class _Proc:
        """A stand-in CLI process: poll() is None while running."""

        def __init__(self, pid: int, alive: bool = True) -> None:
            self.pid, self._alive = pid, alive

        def poll(self):                                  # noqa: ANN201
            return None if self._alive else 0

        def die(self) -> None:
            self._alive = False

    def test_a_failed_enumeration_leaves_an_unknown_count_and_kept_names(
            self) -> None:
        """Unit 3, and specifically WHERE it lives. Two fields, three states.

        `_mcp_tool_count_unknown` fires when an enumeration FAILED — a later
        `mcp_tool_names()` refresh raising on the Codex path (warmpool.py:1806,
        :1892). Two things must both hold afterwards and they pull in opposite
        directions, which is why the repair is split across two functions:

        · the LIVE field must go back to unknown. `mcp_tool_count` is the
          measured-NOW carrier, and `test_status_zero_vs_unknown` §2 pins it:
          a value we measured earlier has its own carrier in
          `last_turn_mcp_tool_count`, so putting a stale total in the live
          field collapses three states into two — the exact defect that suite
          was written for after the 2026-09-01 user report.

        · the SURFACE must stay coherent. The names are NOT popped, because a
          failed observation is not evidence the surface changed, so the reader
          would otherwise hand the turn boundary a name list with no count
          beside it — and that record becomes the readiness gate's baseline.

        Both, therefore: unknown on the live field, derived on the reader.
        Asserted together, because either one alone is satisfiable by a wrong
        implementation.
        """
        proc = self._Proc(31337)
        S._mcp_tool_count_begin(
            self.slug, self.nid, proc, "codex", "mcpServerStatus/list", "s")
        S._mcp_tool_count_names(
            self.slug, self.nid, proc,
            ["mcp__orgtree__one", "mcp__orgtree__two"], "codex",
            "mcpServerStatus/list")

        S._mcp_tool_count_unknown(
            self.slug, self.nid, proc, "codex", "mcpServerStatus/list",
            "Codex runtime inventory unavailable: TimeoutError")

        st = S.state(self.slug, self.nid)
        self.assertIs(st.get("mcp_tool_owner"), proc,
                      "precondition: the generation is still live and owned")
        self.assertIsNone(
            st.get("mcp_tool_count"),
            "the live count reported a total nothing measured this turn")
        self.assertEqual(
            st.get("mcp_tool_names"),
            {"mcp__orgtree__one", "mcp__orgtree__two"},
            "a failed enumeration destroyed a generation-correct name set")
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, proc),
            (2, ["mcp__orgtree__one", "mcp__orgtree__two"]),
            "the surface named two tools while reporting no total; the turn "
            "boundary records that list as the gate's durable baseline")

    def test_a_never_enumerated_surface_stays_honestly_unknown(self) -> None:
        """Negative control for the derivation. It may not invent a count.

        With no names held there is nothing to derive from, and the reader must
        say so — otherwise "count follows names" quietly becomes "count is
        zero", which is the measured-zero state and a different claim entirely.
        """
        proc = self._Proc(31338)
        S._mcp_tool_count_begin(
            self.slug, self.nid, proc, "codex", "mcpServerStatus/list", "s")
        S._mcp_tool_count_unknown(
            self.slug, self.nid, proc, "codex", "mcpServerStatus/list",
            "Codex runtime inventory unavailable: TimeoutError")

        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, proc),
            (None, None),
            "an unmeasured surface was given a fabricated total")

    def test_a_live_process_recovers_from_a_spurious_eof(self) -> None:
        """An ACTIVE process must never be pinned in a terminal state.

        A spurious stdout EOF reaps the generation while its process is still
        running: `_end` pops the owner and publishes a terminal readiness
        state. The live process's own inventory was then refused forever by the
        owner guard — reporting neither LOADING nor LOADED, the one
        combination the lifecycle invariant forbids.

        Since unit 2 the terminal state it publishes here is `withdrawn`, not
        `process-ended`: `poll()` says the process is alive, so the strong
        claim is unavailable and the honest one — removed from service,
        inventory final — is published instead.
        """
        live = self._Proc(4242)
        S._mcp_tool_count_begin(
            self.slug, self.nid, live, "claude", "system/init.tools", "start")
        S._mcp_tool_count_names(
            self.slug, self.nid, live, ["mcp__orgtree__one"], "claude",
            "system/init.tools")
        S._mcp_tool_count_end(self.slug, self.nid, live, "pump saw EOF")
        st = S.state(self.slug, self.nid)
        self.assertEqual(
            st.get("mcp_readiness_state"), "withdrawn",
            "a live process was reported ENDED on the strength of a closed "
            "channel")

        # the same, still-running process reports its inventory again
        self.assertTrue(S._mcp_tool_count_names(
            self.slug, self.nid, live,
            ["mcp__orgtree__one", "mcp__orgtree__two"], "claude",
            "system/init.tools"))
        self.assertIs(st.get("mcp_tool_owner"), live)
        self.assertEqual(st.get("mcp_readiness_state"), "recovered")
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, live),
            (2, ["mcp__orgtree__one", "mcp__orgtree__two"]))

    class _ReapingLock:
        """`_state_lock` stand-in that fires a spurious EOF as it is taken.

        Makes the pre-lock window deterministic: the reap has to land AFTER
        `_mcp_tool_count_names` has read the owner and probed liveness, and
        BEFORE it takes the lock it acts under.

        Keyed on the Nth acquisition, not the first, and the number matters.
        `_mcp_tool_count_names` opens with `st = state(slug, nid)`, which takes
        this same lock — firing there would reap the generation before the
        pre-lock read, so the gated implementation would see "already reaped",
        probe, and recover. The test would pass against the bug it exists to
        catch. Acquisition 2 is the `with _state_lock:` inside the function,
        under both the gated and the ungated shape.

        One shot, and it fires BEFORE acquiring the real lock, so the nested
        acquisitions inside `_mcp_tool_count_end` cannot deadlock the plain
        `threading.Lock` it stands in for.
        """

        def __init__(self, real, fire, on=2):                # noqa: ANN001
            self._real, self._fire, self._on = real, fire, on
            self._n = 0

        def __enter__(self):                                 # noqa: ANN204
            self._n += 1
            if self._n == self._on:
                self._fire()
            return self._real.__enter__()

        def __exit__(self, *exc):                            # noqa: ANN002
            return self._real.__exit__(*exc)

    def test_an_eof_inside_the_pre_lock_window_still_recovers(self) -> None:
        """The liveness probe may not be gated on a dirty pre-lock read.

        `_mcp_tool_count_names` observes liveness outside `_state_lock` so that
        a subprocess syscall never sets the lock's hold time. Gating that probe
        on an unlocked "does this generation already look reaped?" read put the
        defect back in a smaller window, which is the whole finding: the read
        sees the owner still current, so no probe runs and `owner_running`
        stays False; a spurious EOF then reaps the generation before the lock
        is taken, the recovery branch IS entered, and it refuses a live process
        on the strength of a liveness answer that was never asked for.

        Self-healing on the next publish — and on the Claude lane there may not
        be one, because stdout EOF is the trigger and every publisher reads
        stdout. Probing unconditionally removes the window instead of narrowing
        it, at the cost of one non-blocking `poll()` on an init-or-refresh
        event.
        """
        live = self._Proc(2718)
        S._mcp_tool_count_begin(
            self.slug, self.nid, live, "claude", "system/init.tools", "start")
        S._mcp_tool_count_names(
            self.slug, self.nid, live, ["mcp__orgtree__one"], "claude",
            "system/init.tools")

        saved = S._state_lock
        S._state_lock = self._ReapingLock(
            saved,
            lambda: S._mcp_tool_count_end(
                self.slug, self.nid, live, "pump saw EOF"))
        try:
            accepted = S._mcp_tool_count_names(
                self.slug, self.nid, live,
                ["mcp__orgtree__one", "mcp__orgtree__two"], "claude",
                "system/init.tools")
        finally:
            S._state_lock = saved

        st = S.state(self.slug, self.nid)
        self.assertTrue(
            accepted,
            "a live process that spoke was refused because the reap landed in "
            "the window the liveness gate could not see")
        self.assertIs(st.get("mcp_tool_owner"), live)
        self.assertEqual(st.get("mcp_readiness_state"), "recovered")
        self.assertEqual(
            S._mcp_tool_surface_for_owner(self.slug, self.nid, live),
            (2, ["mcp__orgtree__one", "mcp__orgtree__two"]))

    def test_a_truly_dead_owner_is_never_revived(self) -> None:
        """Recovery proves liveness or refuses. Negative control."""
        dead = self._Proc(5150)
        S._mcp_tool_count_begin(
            self.slug, self.nid, dead, "claude", "system/init.tools", "start")
        S._mcp_tool_count_names(
            self.slug, self.nid, dead, ["mcp__orgtree__one"], "claude",
            "system/init.tools")
        # dead BEFORE teardown — the ordering a genuine exit produces, and the
        # one that lets `_end` observe the death rather than assume it
        dead.die()
        S._mcp_tool_count_end(self.slug, self.nid, dead, "process exited")
        self.assertFalse(S._mcp_tool_count_names(
            self.slug, self.nid, dead, ["mcp__ghost__tool"], "claude",
            "system/init.tools"))
        st = S.state(self.slug, self.nid)
        self.assertIsNone(st.get("mcp_tool_owner"))
        self.assertEqual(
            st.get("mcp_readiness_state"), "process-ended",
            "an OBSERVED death must still reach the strong terminal state; "
            "unit 2 narrows the claim, it must not weaken a real exit")

        # an owner that cannot even be asked is likewise never revived
        opaque = object()
        S._mcp_tool_count_begin(
            self.slug, self.nid, opaque, "claude", "system/init.tools", "s")
        S._mcp_tool_count_names(
            self.slug, self.nid, opaque, ["mcp__orgtree__one"], "claude",
            "system/init.tools")
        S._mcp_tool_count_end(self.slug, self.nid, opaque, "exited")
        self.assertFalse(S._mcp_tool_count_names(
            self.slug, self.nid, opaque, ["mcp__orgtree__one"], "claude",
            "system/init.tools"))

    def test_recovery_never_displaces_an_adopted_successor(self) -> None:
        """A live predecessor must not steal the seat from its replacement."""
        old, new = self._Proc(1), self._Proc(2)
        S._mcp_tool_count_begin(
            self.slug, self.nid, old, "claude", "system/init.tools", "start")
        S._mcp_tool_count_names(
            self.slug, self.nid, old, ["mcp__orgtree__one"], "claude",
            "system/init.tools")
        S._mcp_tool_count_end(self.slug, self.nid, old, "pump saw EOF")
        S._mcp_tool_count_begin(
            self.slug, self.nid, new, "claude", "system/init.tools", "restart")
        # `old` is still running, but the seat is taken
        self.assertFalse(S._mcp_tool_count_names(
            self.slug, self.nid, old, ["mcp__stale__tool"], "claude",
            "system/init.tools"))
        st = S.state(self.slug, self.nid)
        self.assertIs(st.get("mcp_tool_owner"), new)
        self.assertIsNone(st.get("mcp_tool_names"))

    def test_unknown_provider_and_tree_contract(self) -> None:
        owner = object()
        with store.DOC_LOCK:
            org = store.load_org(self.slug)
            org.node(self.nid)["last_turn_mcp_tool_count"] = 2
            store.save_org(org)
        S._mcp_tool_count_begin(
            self.slug, self.nid, owner, "gemini", "ACP",
            "Gemini ACP does not expose runtime-loaded MCP inventory", 2)
        payload = TestClient(api.app).get(f"/api/orgs/{self.slug}")
        self.assertEqual(payload.status_code, 200, payload.text[:300])
        node = payload.json()["roots"][0]
        self.assertIsNone(node["mcp_tool_count"])
        self.assertEqual(node["last_turn_mcp_tool_count"], 2)
        self.assertEqual(node["mcp_tool_count_provider"], "gemini")
        self.assertEqual(node["mcp_tool_count_source"], "ACP")
        self.assertIn("does not expose", node["mcp_tool_count_reason"])
        self.assertFalse(node["mcp_readiness_waiting"])
        self.assertEqual(node["mcp_readiness_state"], "initializing")

        S._mcp_tool_count_end(self.slug, self.nid, owner)
        payload = TestClient(api.app).get(f"/api/orgs/{self.slug}").json()
        node = payload["roots"][0]
        self.assertIsNone(node["mcp_tool_count"])
        # The INVENTORY reason is unchanged by unit 2 and stays pinned here:
        # in this field the phrase means "nothing is publishing this node's
        # inventory", which is true once the owner is popped.
        self.assertEqual(node["mcp_tool_count_reason"],
                         "no live provider process")
        # The READINESS state is the one that names the process, and `owner` is
        # a bare object with no `poll` — nothing observed an exit, so the API
        # reports the withdrawal rather than a death it cannot see.
        self.assertEqual(node["mcp_readiness_state"], "withdrawn")

    def test_runtime_counts_do_not_change_warm_identity(self) -> None:
        org = store.load_org(self.slug)
        before = W.identity_snapshot(
            org, self.nid, cmd=["claude", "--model", "test"],
            env={"ORGTREE_SAFE_TEST": "same"}, overrides={})
        owner = object()
        S._mcp_tool_count_begin(
            self.slug, self.nid, owner, "claude", "system/init.tools", "x")
        S._mcp_tool_count_names(
            self.slug, self.nid, owner, ["mcp__a__x", "mcp__b__y"],
            "claude", "system/init.tools")
        appsettings.set_wait_for_mcp_tools_enabled(True)
        after = W.identity_snapshot(
            org, self.nid, cmd=["claude", "--model", "test"],
            env={"ORGTREE_SAFE_TEST": "same"}, overrides={})
        self.assertEqual(before, after)
        appsettings.set_wait_for_mcp_tools_enabled(False)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
