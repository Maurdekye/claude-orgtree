"""Optional last-turn MCP tool-surface admission gate.

Hermetic: no provider CLI, network, listener, or operator data.

    python backend/tests/test_mcp_readiness.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

TMP = tempfile.mkdtemp(prefix="orgtree-mcp-ready-")
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

from orgtree import appsettings, store, supervisor as S  # noqa: E402
from orgtree.ledger import USER  # noqa: E402


class McpReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        org = store.create_org("zz mcp readiness")
        org.hire(USER, None, "haiku", 5, "agent")
        store.save_org(org)
        cls.slug = org.d["slug"]
        cls.nid = "agent"

    def setUp(self) -> None:
        appsettings.set_wait_for_mcp_tools_enabled(False)
        st = S.state(self.slug, self.nid)
        with S._state_lock:
            for key in list(st):
                if key.startswith("mcp_") or key == "interrupted":
                    st.pop(key, None)
        with store.DOC_LOCK:
            org = store.load_org(self.slug)
            n = org.node(self.nid)
            n.pop("last_turn_mcp_tools", None)
            n.pop("last_turn_mcp_fingerprint", None)
            store.save_org(org)

    def _baseline(self, tools: list[str], fingerprint: str = "fp") -> None:
        with store.DOC_LOCK:
            org = store.load_org(self.slug)
            n = org.node(self.nid)
            n["last_turn_mcp_tools"] = list(tools)
            n["last_turn_mcp_fingerprint"] = fingerprint
            store.save_org(org)

    def _begin(self, owner: object, names: list[str] | None = None) -> None:
        S._mcp_tool_count_begin(
            self.slug, self.nid, owner, "claude", "system/init.tools",
            "initializing")
        if names is not None:
            S._mcp_tool_count_names(
                self.slug, self.nid, owner, names, "claude",
                "system/init.tools")

    def _wait_until_gated(self, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if S.state(self.slug, self.nid).get("mcp_readiness_waiting"):
                return
            time.sleep(0.005)
        self.fail("readiness gate never entered its waiting state")

    def test_default_off_preserves_no_wait_behavior(self) -> None:
        self._baseline(["mcp__old__tool"])
        owner = object()
        self._begin(owner, [])
        outcome = S._mcp_wait_for_surface(
            store.load_org(self.slug), self.nid, owner, "claude", "fp",
            timeout_s=1)
        self.assertEqual(outcome, "off")
        self.assertFalse(S.state(self.slug, self.nid)
                         ["mcp_readiness_waiting"])

    def test_equal_and_superset_release_immediately(self) -> None:
        appsettings.set_wait_for_mcp_tools_enabled(True)
        expected = ["mcp__a__one", "mcp__b__two"]
        self._baseline(expected)
        for current in (expected, [*expected, "mcp__extra__three"]):
            owner = object()
            self._begin(owner, current)
            outcome = S._mcp_wait_for_surface(
                store.load_org(self.slug), self.nid, owner, "claude", "fp",
                timeout_s=1)
            self.assertEqual(outcome, "ready")

    def test_equal_count_different_names_waits_then_realtime_release(self) -> None:
        appsettings.set_wait_for_mcp_tools_enabled(True)
        expected = ["mcp__a__one", "mcp__b__two"]
        self._baseline(expected)
        owner = object()
        # Same count, but b/two is missing. Count equality must not admit.
        self._begin(owner, ["mcp__a__one", "mcp__c__other"])
        result: list[str] = []
        thread = threading.Thread(target=lambda: result.append(
            S._mcp_wait_for_surface(
                store.load_org(self.slug), self.nid, owner, "claude", "fp",
                timeout_s=2)))
        thread.start()
        self._wait_until_gated()
        self.assertIn("mcp__b__two", S.state(self.slug, self.nid)
                      ["mcp_readiness_reason"])
        S._mcp_tool_count_names(
            self.slug, self.nid, owner,
            ["mcp__a__one", "mcp__b__two", "mcp__c__other"],
            "claude", "system/init.tools")
        thread.join(0.5)
        self.assertFalse(thread.is_alive(), "delta did not release gate")
        self.assertEqual(result, ["ready"])

    def test_infrastructure_change_rebases_without_waiting(self) -> None:
        appsettings.set_wait_for_mcp_tools_enabled(True)
        self._baseline(["mcp__obsolete__tool"], "old-fingerprint")
        owner = object()
        self._begin(owner, None)
        outcome = S._mcp_wait_for_surface(
            store.load_org(self.slug), self.nid, owner, "claude",
            "new-fingerprint", timeout_s=1)
        self.assertEqual(outcome, "infrastructure-changed")
        self.assertFalse(S.state(self.slug, self.nid)
                         ["mcp_readiness_waiting"])

    def test_fingerprint_tracks_config_not_transient_inventory(self) -> None:
        saved_registry = S._mcp_registry_observed
        old_mcp: list[str] = []
        registry = {"alpha": {
            "command": "server-one", "args": ["--serve"],
            "env": {"SECRET_TOKEN": "must-not-escape"}}}
        S._mcp_registry_observed = lambda: registry  # type: ignore[assignment]
        try:
            with store.DOC_LOCK:
                org = store.load_org(self.slug)
                old_mcp = list(org.node(self.nid)["scope"]["tools"]
                               .get("mcp") or [])
                org.node(self.nid)["scope"]["tools"]["mcp"] = ["alpha"]
                store.save_org(org)
            org = store.load_org(self.slug)
            first = S._mcp_infrastructure_fingerprint(org, self.nid)
            owner = object()
            self._begin(owner, ["mcp__alpha__one"])
            S._mcp_tool_count_names(
                self.slug, self.nid, owner,
                ["mcp__alpha__one", "mcp__alpha__two"], "claude",
                "system/init.tools")
            self.assertEqual(
                first, S._mcp_infrastructure_fingerprint(org, self.nid),
                "transient readiness changed the infrastructure generation")
            registry["alpha"] = {
                "command": "server-two", "args": ["--serve"],
                "env": {"SECRET_TOKEN": "must-not-escape"}}
            changed = S._mcp_infrastructure_fingerprint(org, self.nid)
            self.assertNotEqual(first, changed)
            self.assertNotIn("must-not-escape", str(first))
            self.assertNotIn("must-not-escape", str(changed))
        finally:
            S._mcp_registry_observed = saved_registry  # type: ignore[assignment]
            with store.DOC_LOCK:
                org = store.load_org(self.slug)
                org.node(self.nid)["scope"]["tools"]["mcp"] = old_mcp
                store.save_org(org)

    def test_timeout_is_bounded_and_fails_open(self) -> None:
        appsettings.set_wait_for_mcp_tools_enabled(True)
        self._baseline(["mcp__missing__tool"])
        owner = object()
        self._begin(owner, [])
        started = time.monotonic()
        outcome = S._mcp_wait_for_surface(
            store.load_org(self.slug), self.nid, owner, "claude", "fp",
            timeout_s=0.02)
        self.assertEqual(outcome, "timed-out")
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertIn("failed open", S.state(self.slug, self.nid)
                      ["mcp_readiness_reason"])

    def test_cancellation_and_generation_replacement_are_terminal(self) -> None:
        appsettings.set_wait_for_mcp_tools_enabled(True)
        self._baseline(["mcp__missing__tool"])
        old = object()
        self._begin(old, [])
        result: list[str] = []
        thread = threading.Thread(target=lambda: result.append(
            S._mcp_wait_for_surface(
                store.load_org(self.slug), self.nid, old, "claude", "fp",
                timeout_s=2)))
        thread.start()
        self._wait_until_gated()
        st = S.state(self.slug, self.nid)
        with S._state_lock:
            st["interrupted"] = True
            event = st["mcp_tool_event"]
        event.set()
        thread.join(0.5)
        self.assertEqual(result, ["cancelled"])

        st = S.state(self.slug, self.nid)
        with S._state_lock:
            st.pop("interrupted", None)
        old = object()
        new = object()
        self._begin(old, [])
        result.clear()
        thread = threading.Thread(target=lambda: result.append(
            S._mcp_wait_for_surface(
                store.load_org(self.slug), self.nid, old, "claude", "fp",
                timeout_s=2)))
        thread.start()
        self._wait_until_gated()
        self._begin(new, ["mcp__missing__tool"])
        thread.join(0.5)
        self.assertEqual(result, ["generation-changed"])
        self.assertFalse(S._mcp_tool_count_names(
            self.slug, self.nid, old, ["mcp__ghost__tool"], "claude",
            "stale"))
        self.assertEqual(S._mcp_tool_surface_for_owner(
            self.slug, self.nid, new)[1], ["mcp__missing__tool"])

    def test_durable_baseline_survives_state_reconciliation(self) -> None:
        appsettings.set_wait_for_mcp_tools_enabled(True)
        expected = ["mcp__a__one"]
        self._baseline(expected)
        # Simulate a backend restart: the process state is gone; node evidence
        # remains in the durable org document.
        with S._state_lock:
            S._state.pop((self.slug, self.nid), None)
        owner = object()
        self._begin(owner, [*expected, "mcp__b__two"])
        self.assertEqual(S._mcp_wait_for_surface(
            store.load_org(self.slug), self.nid, owner, "claude", "fp",
            timeout_s=1), "ready")

    def test_gemini_gap_is_explicit_and_fail_open(self) -> None:
        appsettings.set_wait_for_mcp_tools_enabled(True)
        self._baseline(["mcp__a__one"])
        owner = object()
        self._begin(owner, None)
        outcome = S._mcp_wait_for_surface(
            store.load_org(self.slug), self.nid, owner, "google", "fp",
            timeout_s=1)
        self.assertEqual(outcome, "unsupported")
        self.assertIn("Gemini ACP", S.state(self.slug, self.nid)
                      ["mcp_readiness_reason"])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
