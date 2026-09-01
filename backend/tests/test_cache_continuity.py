"""Cache-continuity doctrine, predictor, receipt and policy regression.

Plain deterministic checks; no provider/network calls. Run with:
    python backend/tests/test_cache_continuity.py
"""

from __future__ import annotations

import atexit
import copy
import inspect
import json
import os
import shutil
import sys
import tempfile
from typing import Any, Callable

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
DATA = tempfile.mkdtemp(prefix="orgtree-cache-continuity-")
os.environ["ORGTREE_DATA"] = DATA
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from orgtree import cachecontinuity as C, store, supervisor as S  # noqa: E402
from orgtree.ledger import Org, USER                            # noqa: E402

assert DATA != os.path.expanduser("~/orgtree")
S.chatq_register_org = lambda slug: None
S.chatq_deregister_org = lambda slug: None
atexit.register(lambda: shutil.rmtree(DATA, ignore_errors=True))

NOW = 1788253200.0
PASS = FAIL = 0


def check(label: str, fn: Callable[[], None]) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")
    except Exception as exc:
        FAIL += 1
        print(f"  FAIL    {label}: {exc}")
        import traceback
        traceback.print_exc()


def eq(got: Any, want: Any) -> None:
    assert got == want, f"got {got!r}; want {want!r}"


def snapshot(**changes: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "provider": "claude", "account": "primary", "lane": "subscription",
        "model": "claude-sonnet-5", "session": "session-a",
        "node_generation": 3, "captured_at": C.iso(NOW),
        "components": {k: f"{k}-same" for k in
                       ("system", "tools", "argv", "env", "startup", "lineage")},
        "history": {"bytes": 4, "sha256": "abcd"},
        "fingerprint": "launch-fingerprint", "expected_input_tokens": 120000,
        "last_turn_history_relation": "same_or_appended",
        "receipt_history_relation": "same_or_appended",
    }
    row.update(changes)
    return row


def book(*, receipt_at: float | None = None,
         receipt_changes: dict[str, Any] | None = None) -> dict[str, Any]:
    last = snapshot()
    last.pop("last_turn_history_relation", None)
    last.pop("receipt_history_relation", None)
    out: dict[str, Any] = {"last_turn": last}
    if receipt_at is not None:
        receipt = copy.deepcopy(last)
        receipt["observed_at"] = C.iso(receipt_at)
        receipt["cache_read_tokens"] = 50000
        receipt.update(receipt_changes or {})
        out["receipt"] = receipt
    return out


check("doctrine is explicit, stable, provider-aware and not telemetry", lambda: (
    eq(C.CACHE_CONTINUITY_BLOCK.count("[CACHE CONTINUITY]"), 1),
    eq(C.CACHE_CONTINUITY_BLOCK.count("[END CACHE CONTINUITY]"), 1),
    None if "local process restart" in C.CACHE_CONTINUITY_BLOCK else
    (_ for _ in ()).throw(AssertionError("missing restart distinction")),
    None if all(value in C.CACHE_CONTINUITY_BLOCK for value in
                ("60 minutes", "5 minutes", "30-minute estimate")) else
    (_ for _ in ()).throw(AssertionError("missing derived TTLs")),
    None if "provider-specific session/context continuity" in
    C.CACHE_CONTINUITY_BLOCK else
    (_ for _ in ()).throw(AssertionError("missing provider-switch warning")),
    None if "{org" not in C.CACHE_CONTINUITY_BLOCK and "observed_at" not in
    C.CACHE_CONTINUITY_BLOCK else
    (_ for _ in ()).throw(AssertionError("live interpolation leaked")),
))


def identity_stability() -> None:
    org = store.create_org("zz-cache-doctrine")
    org.hire(USER, None, "haiku", 4, "agent")
    before = S.identity_prompt(org, "agent")
    org.d["notices"] = {"agent": [{"at": C.iso(NOW), "text": "dynamic"}]}
    org.node("agent")["cost_usd"] = 99.0
    after = S.identity_prompt(org, "agent")
    eq(before, after)
    eq(before.count(C.CACHE_CONTINUITY_BLOCK), 1)


check("every managed identity gets one doctrine block; live state cannot move it",
      identity_stability)

check("no completed fingerprint is uncertain",
      lambda: eq(C.classify(snapshot(), {}, NOW)["state"], "uncertain"))
check("matching fingerprint without a positive receipt is uncertain",
      lambda: eq(C.classify(snapshot(), book(), NOW)["state"], "uncertain"))
check("matching positive subscription receipt is compatible-observed, not guaranteed",
      lambda: eq(C.classify(snapshot(), book(receipt_at=NOW - 10), NOW)["state"],
                 "compatible_observed"))
check("subscription receipt expires exactly at 60 minutes",
      lambda: eq(C.classify(snapshot(), book(receipt_at=NOW - 3600), NOW)["state"],
                 "expired_known_entry"))


def api_ttl_boundary() -> None:
    cur = snapshot(lane="api_key", account="api-key")
    prior = book(receipt_at=NOW - 299)
    prior["last_turn"].update({"lane": "api_key", "account": "api-key"})
    prior["receipt"].update({"lane": "api_key", "account": "api-key"})
    eq(C.classify(cur, prior, NOW)["state"], "compatible_observed")
    prior["receipt"]["observed_at"] = C.iso(NOW - 300)
    eq(C.classify(cur, prior, NOW)["state"], "expired_known_entry")


check("API-key receipt expires exactly at five minutes", api_ttl_boundary)
check("future receipt is clock-skew uncertainty",
      lambda: eq(C.classify(snapshot(), book(receipt_at=NOW + 1), NOW)["source"],
                 "clock_skew"))


def codex_subscription_ttl() -> None:
    cur = snapshot(provider="openai", account="codex-account",
                   lane="subscription", model="gpt-5.6-sol")
    prior = book(receipt_at=NOW - 1799)
    for key in ("last_turn", "receipt"):
        prior[key].update({"provider": "openai", "account": "codex-account",
                           "lane": "subscription", "model": "gpt-5.6-sol"})
    row = C.classify(cur, prior, NOW)
    eq(row["state"], "compatible_observed")
    eq(row["source"], "codex_subscription_fixed_estimate")
    assert "not guaranteed" in row["reason"]
    prior["receipt"]["observed_at"] = C.iso(NOW - 1800)
    row = C.classify(cur, prior, NOW)
    eq(row["state"], "expired_known_entry")
    eq(row["source"], "codex_subscription_fixed_estimate")
    eq(row["confidence"], "estimated")
    assert "expected, not guaranteed" in row["reason"]


def unsupported_ttl() -> None:
    for provider, lane in (("openai", "api_key"),
                           ("google", "provider_unsupported")):
        cur = snapshot(provider=provider, account="account", lane=lane,
                       model="provider-model")
        prior = book(receipt_at=NOW - 99999)
        for key in ("last_turn", "receipt"):
            prior[key].update({"provider": provider, "account": "account",
                               "lane": lane, "model": "provider-model"})
        row = C.classify(cur, prior, NOW)
        eq(row["state"], "uncertain")
        eq(row["source"], "ttl_unobserved")


check("Codex subscription uses fixed 30-minute documented estimate",
      codex_subscription_ttl)
check("Codex API-key and Gemini TTLs remain unknown", unsupported_ttl)


def warning_boundaries() -> None:
    org = store.create_org("zz-cache-warning-boundaries")
    org.hire(USER, None, "haiku", 4, "agent")
    n = org.node("agent")
    n.update({"state": "live", "occupancy": 50_000,
              "context_window": 200_000, "occupancy_est": False,
              "session_unrun": False, "compacted_unrun": False,
              "cheap_compacted": False})
    incompatible = {"state": "known_incompatible"}
    expired = {"state": "expired_known_entry"}

    # Disabled: the user chose strictly ABOVE 25%, so equality is quiet.
    org.d["auto_cheap_compact"] = {"enabled": False, "occ": 0.6}
    eq(S._cache_precompact_decision(org, "agent", incompatible)[0],
       "not_applicable")
    n["occupancy"] = 50_001
    eq(S._cache_precompact_decision(org, "agent", incompatible)[0],
       "miss_expected")
    eq(S._cache_precompact_decision(org, "agent", expired)[0],
       "miss_expected")

    # Enabled: its configured threshold is inclusive, and never produces red.
    org.d["auto_cheap_compact"] = {"enabled": True, "occ": 0.6}
    n["occupancy"] = 119_999
    eq(S._cache_precompact_decision(org, "agent", incompatible)[0],
       "not_applicable")
    n["occupancy"] = 120_000
    eq(S._cache_precompact_decision(org, "agent", incompatible)[0],
       "will_compact")
    eq(S._cache_precompact_decision(org, "agent", expired)[0],
       "will_compact")
    n["occupancy"] = 199_000
    assert all(S._cache_precompact_decision(org, "agent", state)[0]
               != "miss_expected" for state in (incompatible, expired))

    for state in ({"state": "uncertain"},
                  {"state": "compatible_observed"}):
        eq(S._cache_precompact_decision(org, "agent", state)[0],
           "not_applicable")


check("banner boundaries: off >25% red; on >=threshold yellow; never enabled-red",
      warning_boundaries)


def every_changed_input() -> None:
    cur = snapshot(provider="openai", account="other", lane="api_key",
                   model="other-model", session="session-b")
    cur["components"] = {k: f"{k}-changed" for k in
                         ("system", "tools", "argv", "env", "startup", "lineage")}
    cur["last_turn_history_relation"] = "changed"
    row = C.classify(cur, book(receipt_at=NOW - 10), NOW)
    eq(row["state"], "known_incompatible")
    safe = C.public(row, generation="g", precompact_action="miss_expected",
                    precompact_reason="off")
    eq(safe["changed_inputs"], list(C.component_names()))
    serialized = repr(safe)
    assert "other-model" not in serialized and "session-b" not in serialized


check("known-incompatible reports every changed safe component in stable order",
      every_changed_input)


def receipt_component_mismatch() -> None:
    prior = book(receipt_at=NOW - 10)
    prior["receipt"]["components"] = {
        **prior["receipt"]["components"], "tools": "old", "startup": "old"}
    cur = snapshot(receipt_history_relation="changed")
    row = C.classify(cur, prior, NOW)
    safe = C.public(row, generation="g", precompact_action="miss_expected",
                    precompact_reason="cold")
    eq(safe["changed_inputs"], ["tools", "startup", "history"])


check("receipt comparison also enumerates all changed prefix components",
      receipt_component_mismatch)


def combined_baseline_mismatch() -> None:
    prior = book(receipt_at=NOW - 10)
    # The last completed request already moved `system` away from the positive
    # receipt. The pending request then moves `tools` away from that last turn.
    # A first-proof-wins implementation reported only tools and violated the
    # complete tooltip contract.
    prior["last_turn"]["components"]["system"] = "system-current"
    cur = snapshot(last_turn_history_relation="unobserved")
    cur["components"] = {
        **cur["components"], "system": "system-current",
        "tools": "tools-pending",
    }
    row = C.classify(cur, prior, NOW)
    eq(row["state"], "known_incompatible")
    eq(row["source"], "fingerprint_and_receipt_mismatch")
    safe = C.public(row, generation="g", precompact_action="miss_expected",
                    precompact_reason="cold")
    eq(safe["changed_inputs"], ["system", "tools"])


check("tooltip unions every changed last-turn and receipt component in stable order",
      combined_baseline_mismatch)
check("non-incompatible forecast always carries changed_inputs=[]",
      lambda: eq(C.public(C.classify(snapshot(), book(), NOW), generation="g",
                          precompact_action="not_applicable",
                          precompact_reason="none")["changed_inputs"], []))


def policy_guards() -> None:
    node: dict[str, Any] = {"model": "sonnet", "occupancy": 600000,
                            "context_window": 1000000}
    cold = {"state": "known_incompatible"}
    expired = {"state": "expired_known_entry"}
    uncertain = {"state": "uncertain"}
    assert S._auto_cheap_ready(node, {"occ": .5}, cold)
    assert S._auto_cheap_ready(node, {"occ": .5}, expired)
    assert not S._auto_cheap_ready(node, {"occ": .5}, uncertain)
    assert not S._auto_cheap_ready({**node, "occupancy": 499999}, {"occ": .5}, cold)
    for flag in ("session_unrun", "compacted_unrun", "cheap_compacted",
                 "occupancy_est", "bearer_state", "frozen", "limit_locked",
                 "remote_controlled"):
        assert not S._auto_cheap_ready({**node, flag: True}, {"occ": .5}, cold)
    assert not S._auto_cheap_ready(
        {**node, "last_status": {"status": "blocked"}}, {"occ": .5}, cold)


check("precompact policy requires proven cold + measured threshold + old context",
      policy_guards)


def effort_and_process_controls_excluded() -> None:
    projected = S._cache_cmd_projection([
        "claude", "--model", "m", "--effort", "max", "--debug-to-stderr",
        "--resume", "sid", "--permission-mode", "acceptEdits",
        "--add-dir", "B", "--add-dir", "A"])
    eq(projected, {"--model": "m", "--permission-mode": "acceptEdits",
                   "--add-dir": ["B", "A"]})
    assert "effort" not in repr(projected) and "resume" not in repr(projected)


check("effort/debug/local-resume process controls do not enter provider fingerprint",
      effort_and_process_controls_excluded)


def claude_namespace_evidence() -> None:
    org = store.create_org("zz-cache-namespace")
    org.hire(USER, None, "haiku", 0, "agent")
    first = S._cache_claude_namespace(
        org, "haiku", {"ANTHROPIC_API_KEY": "secret-a"}, NOW)
    second = S._cache_claude_namespace(
        org, "haiku", {"ANTHROPIC_API_KEY": "secret-b"}, NOW)
    eq(first[1], "api_key")
    eq(second[1], "api_key")
    assert first[0] != second[0]
    assert "secret-a" not in repr(first) and "secret-b" not in repr(second)
    eq(S._cache_claude_namespace(org, "haiku", {}, NOW),
       (S.accounts.PRIMARY, "subscription"))
    resolved = S.clean_env()
    resolved.update({
        "ANTHROPIC_API_KEY": "secret-a",
        "CLAUDE_CODE_DEBUG_LOG_LEVEL": "warn",
        "CLAUDE_CODE_IS_COWORK": "1",
        "ORGTREE_CACHE_TEST_SEMANTIC": "changed",
    })
    eq(S._cache_claude_env_projection(resolved), {
        "CLAUDE_CODE_IS_COWORK": "1",
        "ORGTREE_CACHE_TEST_SEMANTIC": "changed",
    })


check("Claude account evidence detects key rotation without persisting secrets",
      claude_namespace_evidence)


def other_provider_account_evidence() -> None:
    old_codex = os.environ.get("CODEX_HOME")
    old_gemini = os.environ.get("ORGTREE_GEMINI_HOME")
    codex_home = os.path.join(DATA, "codex-home")
    gemini_home = os.path.join(DATA, "gemini-home")
    os.makedirs(codex_home, exist_ok=True)
    os.makedirs(gemini_home, exist_ok=True)
    os.environ["CODEX_HOME"] = codex_home
    os.environ["ORGTREE_GEMINI_HOME"] = gemini_home
    try:
        auth_path = os.path.join(codex_home, "auth.json")
        with open(auth_path, "w", encoding="utf-8") as f:
            json.dump({"tokens": {"account_id": "account-a"}}, f)
        codex_a, lane_a = S._cache_codex_account_namespace()
        with open(auth_path, "w", encoding="utf-8") as f:
            json.dump({"tokens": {"account_id": "account-b"}}, f)
        codex_b, lane_b = S._cache_codex_account_namespace()
        assert codex_a != codex_b
        assert "account-a" not in codex_a and "account-b" not in codex_b
        eq((lane_a, lane_b), ("subscription", "subscription"))
        with open(auth_path, "w", encoding="utf-8") as f:
            json.dump({"OPENAI_API_KEY": "secret-key"}, f)
        codex_key, key_lane = S._cache_codex_account_namespace()
        eq(key_lane, "api_key")
        assert "secret-key" not in codex_key

        with open(os.path.join(gemini_home, "settings.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"security": {"auth": {
                "selectedType": "oauth-personal"}}}, f)
        with open(os.path.join(gemini_home, "oauth_creds.json"), "w",
                  encoding="utf-8") as f:
            f.write("{}")
        accounts_path = os.path.join(gemini_home, "google_accounts.json")
        with open(accounts_path, "w", encoding="utf-8") as f:
            json.dump({"active": "first@example.invalid"}, f)
        gemini_a = S._cache_gemini_account_namespace()
        with open(accounts_path, "w", encoding="utf-8") as f:
            json.dump({"active": "second@example.invalid"}, f)
        gemini_b = S._cache_gemini_account_namespace()
        assert gemini_a != gemini_b
        assert "example.invalid" not in gemini_a + gemini_b
    finally:
        if old_codex is None:
            os.environ.pop("CODEX_HOME", None)
        else:
            os.environ["CODEX_HOME"] = old_codex
        if old_gemini is None:
            os.environ.pop("ORGTREE_GEMINI_HOME", None)
        else:
            os.environ["ORGTREE_GEMINI_HOME"] = old_gemini


check("Codex/Gemini observable account changes move private namespaces safely",
      other_provider_account_evidence)


def legacy_migration() -> None:
    org = store.create_org("zz-cache-migration")
    org.hire(USER, None, "haiku", 0, "agent")
    org.d["auto_cheap_compact"] = {
        "enabled": True, "occ": .6, "idle_s": 17}
    org.node("agent")["scope"]["auto_cheap_compact"] = {"idle_s": 99}
    loaded = Org(copy.deepcopy(org.d))
    eq(loaded.d["auto_cheap_compact"], {"enabled": True, "occ": .6})
    assert "auto_cheap_compact" not in loaded.node("agent")["scope"]
    eq(S._auto_cheap_cfg(loaded, "agent"), {"occ": .6})
    loaded.set_scope(USER, "agent", auto_cheap_compact={"idle_s": 1})
    assert "auto_cheap_compact" not in loaded.node("agent")["scope"]
    loaded.set_scope(USER, "agent", auto_cheap_compact={"occ": .7})
    loaded.set_scope(USER, "agent", auto_cheap_compact={"idle_s": 2})
    eq(loaded.node("agent")["scope"]["auto_cheap_compact"], {"occ": .7})


check("legacy idle timeout is ignored/removed while enabled and threshold survive",
      legacy_migration)


def receipt_reconciliation() -> None:
    org = store.create_org("zz-cache-receipt")
    org.hire(USER, None, "haiku", 0, "agent")
    n = org.node("agent")
    attempt = snapshot(node_generation=int(n.get("generation") or 0),
                       session=n["session_id"], session_was_unrun=True,
                       history={"bytes": 0, "sha256": "empty"})
    current = copy.deepcopy(attempt)
    current.update({"history": {"bytes": 12, "sha256": "after"},
                    "session_was_unrun": False, "_history_path": None})
    old_snapshot = S._cache_snapshot
    S._cache_snapshot = lambda *args, **kwargs: copy.deepcopy(current)
    try:
        safe = S._cache_finish_turn(
            org, "agent", attempt,
            {"cache_read_input_tokens": 4000}, now=NOW)
        assert safe is not None
        cc = n["cache_continuity"]
        eq(cc["receipt"]["history"], attempt["history"])
        eq(cc["receipt"]["ttl_seconds"], 3600)
        eq(cc["last_turn"]["history"], current["history"])
        persisted = Org(copy.deepcopy(org.d)).node("agent")["cache_continuity"]
        eq(persisted["receipt"]["observed_at"], C.iso(NOW))
        before = copy.deepcopy(cc)
        n["generation"] = int(n.get("generation") or 0) + 1
        eq(S._cache_finish_turn(org, "agent", attempt,
                                {"cache_read_input_tokens": 1}, now=NOW + 1), None)
        eq(n["cache_continuity"], before)
        n["generation"] = int(attempt["node_generation"])
        n["session_id"] = "replacement-session"
        eq(S._cache_finish_turn(org, "agent", attempt,
                                {"cache_read_input_tokens": 1}, now=NOW + 2), None)
        eq(n["cache_continuity"], before)
        eq(S._cache_boundary_attempt(org, "agent"), None)
    finally:
        S._cache_snapshot = old_snapshot


check("positive receipt persists exact launch prefix; stale generation/session is ignored",
      receipt_reconciliation)
check("zero cache usage cannot refresh expiry",
      lambda: eq(S._cache_refresh_receipt(None, "none", None, {}), None))


def public_semantic_preview() -> None:
    org = store.create_org("zz-cache-public-preview")
    org.hire(USER, None, "haiku", 0, "agent")
    n = org.node("agent")
    prior = book(receipt_at=NOW - 10)
    compatible = C.classify(snapshot(), prior, NOW)
    prior.update({
        "forecast": compatible,
        "public": C.public(
            compatible, generation="durable-generation",
            precompact_action="not_applicable", precompact_reason=
            "No proven cold entry requires compaction."),
        "seq": 1,
    })
    n["cache_continuity"] = copy.deepcopy(prior)
    persisted = copy.deepcopy(n["cache_continuity"])
    rendered = snapshot()
    rendered["components"] = {
        **rendered["components"], "system": "pending-system"}
    old_snapshot = S._cache_snapshot
    calls: list[bool] = []

    def preview(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append(bool(kwargs.get("include_history")))
        return copy.deepcopy(rendered)

    S._cache_snapshot = preview
    try:
        one = S.cache_forecast_public(org, "agent", now=NOW)
        assert one is not None
        eq(one["state"], "known_incompatible")
        eq(one["changed_inputs"], ["system"])
        assert one["generation"] != "durable-generation"
        assert calls == [False], "tree preview must not hash the transcript"
        eq(n["cache_continuity"], persisted)

        # The summary reason remains the same (`system` is first), but adding a
        # second changed component changes the atomic object and must therefore
        # mint a different opaque generation.
        rendered["components"]["tools"] = "pending-tools"
        two = S.cache_forecast_public(org, "agent", now=NOW)
        assert two is not None
        eq(two["changed_inputs"], ["system", "tools"])
        assert two["generation"] != one["generation"]
        eq(n["cache_continuity"], persisted)
    finally:
        S._cache_snapshot = old_snapshot


check("tree preview catches pending semantic changes without transcript I/O or mutation",
      public_semantic_preview)


def source_contract() -> None:
    run = inspect.getsource(S._run_one_turn)
    assert run.index("_cache_forecast_now") < run.index("org.take_mail")
    assert 'if not is_cmd:' in run
    assert '"kind": "cache_forecast"' in run and '"forecast"' in run
    ready = inspect.getsource(S._auto_cheap_ready)
    assert "turns" not in ready and "idle_s" not in ready
    assert "expired_known_entry" in ready and "known_incompatible" in ready


check("one pre-drain ordinary-turn gate owns policy and WebSocket contract",
      source_contract)

print(f"\nALL {PASS} CHECKS PASS" if not FAIL else
      f"\n{FAIL} FAILED, {PASS} PASSED")
raise SystemExit(1 if FAIL else 0)
