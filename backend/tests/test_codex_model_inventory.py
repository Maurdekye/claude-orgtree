"""Conditional Codex tiers: provider-confirmed offers and a fresh hire gate.

    python backend/tests/test_codex_model_inventory.py

No real account is touched here. The app-server client is replaced at its
module seam, while the separate live negative probe is run explicitly during
release verification on a signed-in machine.
"""

import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-astra-")
os.environ["ORGTREE_ANTIGRAVITY"] = os.path.join(
    os.environ["ORGTREE_DATA"], "missing-agy")

from orgtree import api, codexrun, mcptool, openrouter, providers  # noqa: E402
from orgtree.ledger import LedgerError, MODELS, Org, TIERS  # noqa: E402

PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def refusal(fn, needle):
    try:
        fn()
    except LedgerError as e:
        assert needle.lower() in str(e).lower(), (needle, str(e))
        return str(e)
    raise AssertionError("expected LedgerError")


class FakeClient:
    pages = []
    calls = []
    made = 0

    def __init__(self, *args, **kwargs):
        type(self).made += 1

    def initialize(self, timeout=0):
        return {}

    def request(self, method, params, timeout=0):
        assert method == "model/list", method
        assert params["includeHidden"] is True, params
        type(self).calls.append(dict(params))
        page = type(self).pages.pop(0)
        if isinstance(page, Exception):
            raise page
        return page

    def close(self):
        pass


class InventoryRig:
    def __init__(self, pages):
        self.pages = list(pages)

    def __enter__(self):
        self.saved = (
            codexrun.AppServerClient, providers.codex_status,
            providers.codex_path, providers._codex_model_inventory_cache)
        FakeClient.pages = list(self.pages)
        FakeClient.calls = []
        FakeClient.made = 0
        codexrun.AppServerClient = FakeClient
        providers.codex_status = lambda force=False: {
            "installed": True, "connected": True, "kind": "chatgpt",
            "codex_home": "X:/fake-home"}
        providers.codex_path = lambda: ("codex.exe", "env")
        providers._codex_model_inventory_cache = None
        return self

    def __exit__(self, *args):
        (codexrun.AppServerClient, providers.codex_status,
         providers.codex_path,
         providers._codex_model_inventory_cache) = self.saved


def metadata_is_installed_but_dark_without_evidence():
    assert (TIERS["astra"], MODELS["astra"]) == (10, "gpt-6-astra")
    assert "astra" in providers.CODEX_TIERS
    assert "astra" in providers.CONDITIONAL_CODEX_TIERS
    assert "astra" not in [row["tier"] for row in providers.codex_tiers()]
    assert "astra" in [row["tier"] for row in providers.codex_tiers(
        {"gpt-6-astra"})]
    org = Org.create("astra-ledger")
    org.hire("@user", None, "fable", 30, "root")
    org.hire("@user", "root", "astra", 0, "astra-node")
    assert org.seat_cost("astra-node") == 10


def full_hidden_inventory_is_paginated_and_cached():
    with InventoryRig([
        {"data": [{"id": "gpt-reserve", "hidden": True}],
         "nextCursor": "two"},
        {"data": [{"id": "gpt-6-astra", "hidden": True}],
         "nextCursor": None},
    ]):
        got = providers.codex_model_inventory(force=True)
        assert got["available"] is True, got
        assert got["models"] == ["gpt-6-astra", "gpt-reserve"], got
        assert FakeClient.calls == [
            {"limit": 100, "includeHidden": True},
            {"limit": 100, "includeHidden": True, "cursor": "two"},
        ], FakeClient.calls
        again = providers.codex_model_inventory()
        assert again["models"] == got["models"]
        assert FakeClient.made == 1, "fresh UI cache should avoid a second spawn"


def stale_success_never_authorizes_after_refresh_failure():
    with InventoryRig([RuntimeError("provider unavailable")]):
        providers._codex_model_inventory_cache = (
            time.time() - providers.CODEX_MODEL_INVENTORY_TTL - 1,
            {"available": True, "models": ["gpt-6-astra"], "error": None,
             "fetched_at": time.time() - 999})
        got = providers.codex_model_inventory()
        assert got["available"] is False, got
        assert got["models"] == [], got
        assert "provider unavailable" in got["error"], got


def malformed_rows_fail_closed():
    for data in (None, [None], [{"model": "gpt-6-astra"}]):
        page = {} if data is None else {"data": data, "nextCursor": None}
        with InventoryRig([page]):
            got = providers.codex_model_inventory(force=True)
            assert got["available"] is False, (data, got)
            assert got["models"] == [], (data, got)


def exact_id_membership_controls_the_tier():
    saved = providers.codex_model_inventory
    try:
        providers.codex_model_inventory = lambda force=False, status=None: {
            "available": True, "models": ["gpt-6-astra-preview"],
            "error": None}
        assert providers.conditional_codex_availability(
            "astra", force=True)["enabled"] is False
        providers.codex_model_inventory = lambda force=False, status=None: {
            "available": True, "models": ["gpt-6-astra"], "error": None}
        assert providers.conditional_codex_availability(
            "astra", force=True)["enabled"] is True
    finally:
        providers.codex_model_inventory = saved


def provider_payload_only_offers_confirmed_astra():
    saved = (providers.codex_status, providers.codex_model_inventory,
             providers.reserve_availability, providers.antigravity_status)
    providers.codex_status = lambda force=False: {
        "installed": True, "connected": True, "kind": "chatgpt"}
    providers.reserve_availability = lambda status=None: {
        "enabled": True, "reason": None, "evidence": "test"}
    providers.antigravity_status = lambda force=False: {
        "installed": False, "connected": False}
    try:
        for ids, expected in (([], False), (["gpt-6-astra"], True)):
            providers.codex_model_inventory = lambda force=False, status=None, ids=ids: {
                "available": True, "models": ids, "error": None}
            payload = providers.providers_payload({"installed": True,
                                                   "connected": True})
            openai = next(p for p in payload["providers"] if p["id"] == "openai")
            assert ("astra" in [t["tier"] for t in openai["tiers"]]) is expected
    finally:
        (providers.codex_status, providers.codex_model_inventory,
         providers.reserve_availability, providers.antigravity_status) = saved


def hire_gate_force_refreshes_and_refuses_the_negative_case():
    saved = (providers.codex_status, providers.conditional_codex_availability)
    seen = []
    providers.codex_status = lambda force=False: {
        "installed": True, "connected": True, "kind": "api-key"}
    providers.conditional_codex_availability = lambda tier, force=False, status=None: (
        seen.append((tier, force, status)) or {
            "enabled": False, "reason": "model missing", "evidence": "test"})
    try:
        refusal(lambda: api.provider_hire_gate(Org.create("astra-gate"), "astra"),
                "model missing")
        assert seen and seen[0][0:2] == ("astra", True), seen
    finally:
        providers.codex_status, providers.conditional_codex_availability = saved


def hire_gate_passes_only_the_positive_case():
    saved = (providers.codex_status, providers.conditional_codex_availability)
    providers.codex_status = lambda force=False: {
        "installed": True, "connected": True, "kind": "api-key"}
    providers.conditional_codex_availability = lambda tier, force=False, status=None: {
        "enabled": True, "reason": None, "evidence": "model-present"}
    try:
        api.provider_hire_gate(Org.create("astra-gate-pass"), "astra")
    finally:
        providers.codex_status, providers.conditional_codex_availability = saved


def mcp_cards_are_stable_across_rollout_and_connection_changes():
    saved = (providers.codex_status, providers.codex_model_inventory,
             openrouter.tiers, mcptool.deployment.current_policy)
    mcptool.deployment.current_policy = lambda: type(
        "Policy", (), {"allow_agent_restart": True})()
    try:
        providers.codex_status = lambda force=False: {
            "installed": True, "connected": True, "kind": "chatgpt"}
        providers.codex_model_inventory = lambda force=False, status=None: {
            "available": True, "models": ["gpt-6-astra"], "error": None}
        openrouter.tiers = lambda: {"or-synthetic": 1}
        lit = json.dumps(mcptool.available_tools(), sort_keys=True,
                         separators=(",", ":")).encode()
        providers.codex_status = lambda force=False: {
            "installed": False, "connected": False}
        providers.codex_model_inventory = lambda force=False, status=None: {
            "available": False, "models": [], "error": "fixture"}
        openrouter.tiers = lambda: {}
        dark = json.dumps(mcptool.available_tools(), sort_keys=True,
                          separators=(",", ":")).encode()
        assert lit == dark
        served = {row["name"]: row for row in mcptool.available_tools()}
        for name in ("orgtree_hire", "orgtree_switch_model"):
            card = served[name]
            tier = card["inputSchema"]["properties"]["tier"]
            assert tier["type"] == "string" and "enum" not in tier, name
    finally:
        (providers.codex_status, providers.codex_model_inventory,
         openrouter.tiers, mcptool.deployment.current_policy) = saved


check("Astra metadata exists at seat 10 but is dark without evidence",
      metadata_is_installed_but_dark_without_evidence)
check("model/list includes hidden rows, follows pagination, and caches briefly",
      full_hidden_inventory_is_paginated_and_cached)
check("a stale success plus refresh error fails closed",
      stale_success_never_authorizes_after_refresh_failure)
check("missing data, malformed rows, and missing ids fail closed",
      malformed_rows_fail_closed)
check("only exact gpt-6-astra id membership lights the tier",
      exact_id_membership_controls_the_tier)
check("the provider payload omits Astra until the inventory confirms it",
      provider_payload_only_offers_confirmed_astra)
check("the hire gate force-refreshes and refuses a missing Astra",
      hire_gate_force_refreshes_and_refuses_the_negative_case)
check("the hire gate passes confirmed Astra (anti-vacuity)",
      hire_gate_passes_only_the_positive_case)
check("agent tool schemas stay byte-identical across provider state",
      mcp_cards_are_stable_across_rollout_and_connection_changes)

print(f"\nPASS — conditional Codex inventory, {PASS} checks")
