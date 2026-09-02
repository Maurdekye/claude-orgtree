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
        self.assertEqual(node["mcp_tool_count_reason"],
                         "no live provider process")
        self.assertEqual(node["mcp_readiness_state"], "process-ended")

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
