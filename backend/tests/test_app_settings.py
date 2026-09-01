"""D-203 — machine-wide provider choices are durable admission policy.

This file tests the new fact separately from provider detection. A provider
may be installed and connected while the user has deliberately turned it off;
the payload must publish both truths and every admission door consumes the
durable choice through ``provider_hire_gate``.

Run directly:
    python backend/tests/test_app_settings.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="orgtree-appsettings-")
os.environ["ORGTREE_DATA"] = _TMP
os.environ.pop("ORGTREE_WARM", None)
os.makedirs(_TMP, exist_ok=True)
with open(os.path.join(_TMP, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address":"http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient                    # noqa: E402
from orgtree import api, appsettings, providers, store, warmpool  # noqa: E402
from orgtree.ledger import LedgerError, Org                  # noqa: E402

FAILED: list[str] = []
PASSED = 0


def check(label, fn) -> None:
    global PASSED
    try:
        fn()
    except Exception as e:                                   # noqa: BLE001
        FAILED.append(f"{label}\n      {type(e).__name__}: {e}")
        print(f"  x {label}")
    else:
        PASSED += 1
        print(f"  ok {label}")


def expect_gate_error(tier: str, *needles: str) -> None:
    try:
        api.provider_hire_gate(Org.create("gate"), tier)
    except LedgerError as e:
        msg = str(e).lower()
        for needle in needles:
            assert needle.lower() in msg, (needle, msg)
    else:
        raise AssertionError("expected provider gate refusal")


print("\n§1  persistence and defaults")


def missing_means_on() -> None:
    assert not os.path.exists(appsettings.path())
    assert appsettings.provider_choices() == {
        "claude": True, "openai": True, "google": True}
    assert appsettings.working_checkups_enabled() is True


def explicit_values_round_trip() -> None:
    appsettings.set_provider_enabled("openai", False)
    appsettings.set_provider_enabled("google", True)
    with open(appsettings.path(), encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["providers"]["openai"] is False, raw
    assert raw["providers"]["google"] is True, raw
    # A fresh read, not a module-local cache, proves the value is durable.
    assert appsettings.load()["providers"]["openai"] is False


def bad_file_is_not_silently_destroyed() -> None:
    with open(appsettings.path(), "w", encoding="utf-8") as f:
        f.write('{"version":999,"providers":{"claude":false}}')
    before = open(appsettings.path(), encoding="utf-8").read()
    try:
        appsettings.set_provider_enabled("claude", True)
    except appsettings.AppSettingsUnreadable:
        pass
    else:
        raise AssertionError("writer replaced an unreadable settings record")
    assert open(appsettings.path(), encoding="utf-8").read() == before
    os.remove(appsettings.path())


check("a missing record leaves every provider enabled", missing_means_on)
check("explicit off and on values survive a fresh file read",
      explicit_values_round_trip)
check("an unknown-version record is preserved, not overwritten",
      bad_file_is_not_silently_destroyed)


print("\n§2  provider payload keeps choice and detection separate")


def payload_keeps_real_install_state() -> None:
    old_codex, old_gemini = providers.codex_status, providers.gemini_status
    providers.codex_status = lambda force=False: {            # type: ignore[assignment]
        "installed": True, "connected": True, "source": "path"}
    providers.gemini_status = lambda force=False: {           # type: ignore[assignment]
        "installed": False, "connected": False, "source": "none"}
    try:
        appsettings.set_provider_enabled("openai", False)
        doc = providers.providers_payload({
            "installed": True, "connected": True, "source": "path"})
        codex = next(p for p in doc["providers"] if p["id"] == "openai")
        assert codex["user_enabled"] is False, codex
        assert codex["hire_enabled"] is False, codex
        assert codex["status"]["installed"] is True, codex
        assert codex["status"]["connected"] is True, codex
        assert "App settings" in codex["reason"], codex
    finally:
        providers.codex_status = old_codex                   # type: ignore[assignment]
        providers.gemini_status = old_gemini                 # type: ignore[assignment]


check("configured + user-disabled stays truthfully installed and connected",
      payload_keeps_real_install_state)


print("\n§3  the admission predicate")


def explicit_off_beats_detection() -> None:
    appsettings.set_provider_enabled("claude", False)
    expect_gate_error("sonnet", "claude", "turned off", "app settings")


def plain_rehire_skips_only_transient_detection() -> None:
    appsettings.set_provider_enabled("claude", True)
    # This mode is used only by the two plain-rehire doors. It must pass even
    # on the isolated test HOME, where Claude is not signed in (D-197).
    api.provider_hire_gate(
        Org.create("plain"), "sonnet", user_choice_only=True)
    appsettings.set_provider_enabled("claude", False)
    try:
        api.provider_hire_gate(
            Org.create("plain-off"), "sonnet", user_choice_only=True)
    except LedgerError as e:
        assert "turned off" in str(e).lower(), e
    else:
        raise AssertionError("plain rehire bypassed the user's off choice")


check("an explicit off choice refuses an otherwise valid provider",
      explicit_off_beats_detection)
check("plain rehire skips sign-in state but never skips explicit off",
      plain_rehire_skips_only_transient_detection)


print("\n§4  the machine-wide API seam")


def put_round_trip_and_unknown_refusal() -> None:
    real_payload = api._providers_payload
    api._providers_payload = lambda: {                        # type: ignore[assignment]
        "providers": [{"id": p, "user_enabled":
                       appsettings.provider_enabled(p)}
                      for p in ("claude", "openai", "google")]}
    try:
        client = TestClient(api.app)
        r = client.put("/api/providers/google/enabled", json={"enabled": False})
        assert r.status_code == 200, r.text
        assert appsettings.provider_enabled("google") is False
        got = next(p for p in r.json()["providers"] if p["id"] == "google")
        assert got["user_enabled"] is False, got
        bad = client.put("/api/providers/nope/enabled", json={"enabled": False})
        assert bad.status_code == 404, bad.text
        denied = api._public_denied(
            "PUT", "/api/providers/claude/enabled", "public-org")
        assert denied == (
            403, "kiosk: configuration is managed from the admin side"), denied
    finally:
        api._providers_payload = real_payload                 # type: ignore[assignment]


check("PUT saves, reads back, and rejects an unknown provider",
      put_round_trip_and_unknown_refusal)


print("\n§5  machine-wide runtime settings keep their established stores")


def runtime_round_trip_uses_warm_flag() -> None:
    flag = os.path.join(_TMP, "warm.flag")
    if os.path.exists(flag):
        os.remove(flag)
    warmpool._FLAG_CACHE["at"] = 0.0
    client = TestClient(api.app)

    initial = client.get("/api/app-settings/runtime")
    assert initial.status_code == 200, initial.text
    assert initial.json() == {
        "warming_enabled": True,
        "working_checkups_enabled": True}, initial.json()

    off = client.put(
        "/api/app-settings/runtime", json={"enabled": False})
    assert off.status_code == 200, off.text
    assert off.json() == {
        "warming_enabled": False,
        "working_checkups_enabled": True}, off.json()
    assert open(flag, encoding="utf-8").read().strip() == "0"
    warmpool._FLAG_CACHE["at"] = 0.0
    assert warmpool.warm_enabled() is False

    checkups_off = client.put(
        "/api/app-settings/runtime",
        json={"working_checkups_enabled": False})
    assert checkups_off.status_code == 200, checkups_off.text
    assert checkups_off.json() == {
        "warming_enabled": False,
        "working_checkups_enabled": False}, checkups_off.json()
    assert appsettings.working_checkups_enabled() is False

    # Process warming has no preference mirror: warm.flag remains both its
    # runtime lever and visible setting. The additive checkup choice belongs
    # in app-settings.json.
    settings_doc = appsettings.load(strict=True)
    assert settings_doc["runtime"]["working_checkups"] is False, settings_doc
    assert "warming_enabled" not in settings_doc, settings_doc

    on = client.put("/api/app-settings/runtime", json={"enabled": True})
    assert on.status_code == 200, on.text
    assert on.json() == {
        "warming_enabled": True,
        "working_checkups_enabled": False}, on.json()
    checkups_on = client.put(
        "/api/app-settings/runtime",
        json={"working_checkups_enabled": True})
    assert checkups_on.status_code == 200, checkups_on.text
    assert checkups_on.json()["working_checkups_enabled"] is True
    assert appsettings.working_checkups_enabled() is True
    denied = api._public_denied(
        "PUT", "/api/app-settings/runtime", "public-org")
    assert denied == (
        403, "kiosk: configuration is managed from the admin side"), denied


check("GET/PUT round-trip both runtime choices in their durable stores",
      runtime_round_trip_uses_warm_flag)

print(f"\n{PASSED}/{PASSED + len(FAILED)} checks passed")
if FAILED:
    print("\n".join(FAILED))
    raise SystemExit(1)
