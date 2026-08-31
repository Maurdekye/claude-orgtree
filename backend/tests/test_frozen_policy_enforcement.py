"""Fail-closed API/startup controls for the frozen deployment profile.

Run directly:
    python backend/tests/test_frozen_policy_enforcement.py
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import os
from pathlib import Path
import sys
import tempfile
from typing import Iterator

_TMP = tempfile.mkdtemp(prefix="orgtree-frozen-policy-")
os.environ["ORGTREE_DATA"] = _TMP
os.environ["ORGTREE_WARM"] = "0"
os.environ.pop("ORGTREE_DEPLOYMENT_PROFILE", None)
os.environ.pop("ORGTREE_EXPOSE_ADMIN", None)
os.environ.pop("ORGTREE_PUBLIC_PORT", None)
os.environ.pop("ORGTREE_SANDBOX_API_KEY", None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import HTTPException  # noqa: E402
import httpx  # noqa: E402
from starlette.requests import Request  # noqa: E402
from orgtree import (api, deployment, frozen_install, mcptool, sandbox, store,
                     supervisor)  # noqa: E402
from orgtree.ledger import LedgerError, Org, USER  # noqa: E402


@contextmanager
def env(**values: str | None) -> Iterator[None]:
    old = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def expect_config_error(fn, *needles: str) -> None:
    try:
        fn()
    except deployment.DeploymentConfigError as e:
        text = str(e).lower()
        for needle in needles:
            assert needle.lower() in text, (needle, text)
    else:
        raise AssertionError("expected deployment configuration refusal")


def expect_error(error_type, fn, *needles: str) -> None:
    try:
        fn()
    except error_type as e:
        text = str(e).lower()
        for needle in needles:
            assert needle.lower() in text, (needle, text)
    else:
        raise AssertionError(f"expected {error_type.__name__} refusal")


def boxed_org(name: str) -> Org:
    org = store.create_org(name)
    org.d["sandbox"] = {"enabled": True, "secret": "a" * 32}
    store.save_org(org)
    return org


def test_standard_preserves_exposure_controls() -> None:
    with env(ORGTREE_DEPLOYMENT_PROFILE="standard",
             ORGTREE_EXPOSE_ADMIN="yes"):
        assert api._admin_host() == "0.0.0.0"
    with env(ORGTREE_DEPLOYMENT_PROFILE="standard",
             ORGTREE_EXPOSE_ADMIN=None):
        assert api._admin_host() == "127.0.0.1"


def test_frozen_admin_is_loopback_only() -> None:
    with env(ORGTREE_DEPLOYMENT_PROFILE="frozen",
             ORGTREE_EXPOSE_ADMIN=None):
        assert api._admin_host() == "127.0.0.1"
    with env(ORGTREE_DEPLOYMENT_PROFILE="frozen",
             ORGTREE_EXPOSE_ADMIN="1"):
        expect_config_error(api._admin_host, "frozen", "expose_admin",
                            "loopback")


def test_frozen_rejects_public_listener() -> None:
    old = api.PUBLIC_PORT
    try:
        api.PUBLIC_PORT = 7444
        with env(ORGTREE_DEPLOYMENT_PROFILE="frozen"):
            expect_config_error(api._deployment_preflight, "frozen",
                                "public", "ORGTREE_PUBLIC_PORT")
            assert api._share_url("a" * 32) is None
    finally:
        api.PUBLIC_PORT = old


def test_frozen_rejects_legacy_credential_copy() -> None:
    with env(ORGTREE_DEPLOYMENT_PROFILE="frozen",
             ORGTREE_SANDBOX_API_KEY="subscription"):
        expect_config_error(api._deployment_preflight, "frozen",
                            "credential", "subscription", "proxied")


def test_frozen_inventories_unsandboxed_orgs() -> None:
    org = store.create_org("Old Host Org")
    slug = org.d["slug"]
    try:
        with env(ORGTREE_DEPLOYMENT_PROFILE="frozen"):
            expect_config_error(api._deployment_preflight, "every org",
                                slug, "recreate")
    finally:
        store.delete_org(slug)


def test_sandboxed_inventory_passes() -> None:
    org = store.create_org("Boxed Org")
    slug = org.d["slug"]
    original = frozen_install.require_approved_install
    checked: list[deployment.DeploymentPolicy] = []
    try:
        org.d["sandbox"] = {"enabled": True, "secret": "a" * 32}
        store.save_org(org)
        frozen_install.require_approved_install = \
            lambda *, policy: (_ for _ in ()).throw(
                AssertionError(f"standard called attestation: {policy.name}"))
        with env(ORGTREE_DEPLOYMENT_PROFILE="standard"):
            assert api._deployment_preflight() is deployment.STANDARD
        frozen_install.require_approved_install = \
            lambda *, policy: checked.append(policy)
        with env(ORGTREE_DEPLOYMENT_PROFILE="frozen"):
            assert api._deployment_preflight() is deployment.FROZEN
        assert checked == [deployment.FROZEN]
    finally:
        frozen_install.require_approved_install = original
        store.delete_org(slug)


def test_creation_refuses_before_writing() -> None:
    before = {row["slug"] for row in store.list_orgs()}
    with env(ORGTREE_DEPLOYMENT_PROFILE="frozen"):
        for body in (
                api.OrgCreate(name="Unsafe Normal"),
                api.OrgCreate(name="Unsafe Kiosk",
                              kiosk=api.KioskSpec(sandbox=False))):
            try:
                api.orgs_create(body)
            except HTTPException as e:
                assert e.status_code == 422
                assert "frozen" in str(e.detail).lower()
                assert "sandbox" in str(e.detail).lower()
            else:
                raise AssertionError("unsandboxed org creation was accepted")
    assert {row["slug"] for row in store.list_orgs()} == before


def test_persisted_legacy_selectors_are_inventoried() -> None:
    org = boxed_org("Persisted Legacy Auth")
    slug = org.d["slug"]
    try:
        org.d["api_key"] = "subscription"
        store.save_org(org)
        with env(ORGTREE_DEPLOYMENT_PROFILE="frozen"):
            expect_config_error(api._deployment_preflight, slug,
                                "subscription")
        org.d.pop("api_key")
        org.d["kiosk"] = {"enabled": False, "sandbox": True,
                          "api_key": "subscription"}
        store.save_org(org)
        with env(ORGTREE_DEPLOYMENT_PROFILE="frozen"):
            expect_config_error(api._deployment_preflight, slug,
                                "subscription")
    finally:
        store.delete_org(slug)


def test_settings_refuses_legacy_auth_before_persisting() -> None:
    org = boxed_org("Settings Legacy Auth")
    slug = org.d["slug"]
    try:
        with env(ORGTREE_DEPLOYMENT_PROFILE="frozen"):
            try:
                api._org_settings_locked(
                    slug, api.Settings(api_key=" subscription "))
            except HTTPException as e:
                assert e.status_code == 422
                assert "frozen" in str(e.detail).lower()
            else:
                raise AssertionError("settings accepted subscription auth")
        assert str(store.load_org(slug).d.get("api_key") or "") == ""
    finally:
        store.delete_org(slug)


def test_runtime_rejects_mutated_and_copied_credentials() -> None:
    org = boxed_org("Runtime Legacy Auth")
    slug = org.d["slug"]
    try:
        org.d["api_key"] = "subscription"
        with env(ORGTREE_DEPLOYMENT_PROFILE="standard"):
            assert sandbox.container_auth(org) == "subscription"
        with env(ORGTREE_DEPLOYMENT_PROFILE="frozen"):
            expect_config_error(lambda: sandbox.container_auth(org), "frozen",
                                "subscription")
        org.d["api_fallback"] = True
        with env(ORGTREE_DEPLOYMENT_PROFILE="standard"):
            assert sandbox.container_auth(org) == "proxied"
        with env(ORGTREE_DEPLOYMENT_PROFILE="frozen"):
            expect_config_error(lambda: sandbox.container_auth(org), "frozen",
                                "subscription")

        org.d.pop("api_key")
        org.d.pop("api_fallback")
        copied = Path(sandbox.sandbox_home(slug)) / ".claude" \
            / ".credentials.json"
        copied.parent.mkdir(parents=True, exist_ok=True)
        copied.write_text('{"sentinel": true}', encoding="utf-8")
        with env(ORGTREE_DEPLOYMENT_PROFILE="frozen"):
            expect_config_error(api._deployment_preflight, slug, "copied")
            expect_config_error(lambda: sandbox.container_auth(org), "frozen",
                                "already exist")
    finally:
        store.delete_org(slug)


def test_frozen_rejects_legacy_auth_in_global_defaults() -> None:
    path = Path(store.DATA_ROOT) / "defaults.json"
    old = path.read_bytes() if path.exists() else None
    try:
        path.write_text('{"api_key": "subscription"}', encoding="utf-8")
        with env(ORGTREE_DEPLOYMENT_PROFILE="frozen"):
            expect_config_error(api._deployment_preflight, "defaults.json",
                                "subscription")
    finally:
        if old is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(old)


def test_restart_tools_are_hidden_and_refused_at_each_layer() -> None:
    with env(ORGTREE_DEPLOYMENT_PROFILE="standard"):
        standard = {tool["name"] for tool in mcptool.available_tools()}
        assert "orgtree_self_restart" in standard
        assert "orgtree_prime_restart" in standard
    with env(ORGTREE_DEPLOYMENT_PROFILE="frozen"):
        frozen = {tool["name"] for tool in mcptool.available_tools()}
        assert "orgtree_self_restart" not in frozen
        assert "orgtree_prime_restart" not in frozen

        scope = {"type": "http", "method": "POST", "path": "/api/agent",
                 "headers": [], "state": {}}
        request = Request(scope)
        for tool in ("orgtree_self_restart", "orgtree_self_update",
                     "orgtree_prime_restart"):
            try:
                api.agent_call(api.AgentCall(org="missing", node="missing",
                                             tool=tool), request)
            except HTTPException as e:
                assert e.status_code == 403
                assert "operator-controlled" in str(e.detail).lower()
            else:
                raise AssertionError(f"API accepted {tool}")

        org = Org.create("Frozen Restart Ledger")
        org.hire(USER, None, "haiku", 0, "boss")
        assert "KEEPING THIS MACHINE UP TO DATE" not in \
            supervisor.identity_prompt(org, "boss")
        with env(ORGTREE_DEPLOYMENT_PROFILE="standard"):
            assert "KEEPING THIS MACHINE UP TO DATE" in \
                supervisor.identity_prompt(org, "boss")
        expect_error(LedgerError, lambda: org.self_restart_gate("boss"),
                     "frozen", "operator-controlled")
        expect_error(LedgerError,
                     lambda: org.prime_restart_gate("boss", "arm"),
                     "frozen", "operator-controlled")
        expect_error(RuntimeError,
                     lambda: supervisor.launch_self_restart(
                         "org", "boss", "mailhub"), "frozen")
        expect_error(RuntimeError,
                     lambda: supervisor.arm_prime_restart(
                         "org", "boss", "mailhub"), "frozen")
        expect_error(RuntimeError,
                     lambda: supervisor.cancel_prime_restart("org", "boss"),
                     "frozen")

        old_started = supervisor._prime_started
        try:
            supervisor._prime_started = False
            supervisor.start_prime_restart_engine()
            assert not supervisor._prime_started
        finally:
            supervisor._prime_started = old_started


def test_turn_gate_requires_a_sandbox() -> None:
    org = Org.create("Frozen Turn Gate")
    with env(ORGTREE_DEPLOYMENT_PROFILE="standard"):
        supervisor._deployment_org_gate(org)
    with env(ORGTREE_DEPLOYMENT_PROFILE="frozen"):
        expect_error(RuntimeError, lambda: supervisor._deployment_org_gate(org),
                     "frozen", "unsandboxed")
        org.d["sandbox"] = {"enabled": True, "secret": "a" * 32}
        supervisor._deployment_org_gate(org)


def test_bare_asgi_admin_rejects_non_loopback_clients() -> None:
    async def request_status() -> int:
        transport = httpx.ASGITransport(
            app=api.app, client=("198.51.100.23", 41234))
        async with httpx.AsyncClient(
                transport=transport, base_url="http://frozen-admin") as client:
            return (await client.get("/api/host")).status_code

    with env(ORGTREE_DEPLOYMENT_PROFILE="frozen"):
        assert asyncio.run(request_status()) == 403


test_standard_preserves_exposure_controls()
test_frozen_admin_is_loopback_only()
test_frozen_rejects_public_listener()
test_frozen_rejects_legacy_credential_copy()
test_frozen_inventories_unsandboxed_orgs()
test_sandboxed_inventory_passes()
test_creation_refuses_before_writing()
test_persisted_legacy_selectors_are_inventoried()
test_settings_refuses_legacy_auth_before_persisting()
test_runtime_rejects_mutated_and_copied_credentials()
test_frozen_rejects_legacy_auth_in_global_defaults()
test_restart_tools_are_hidden_and_refused_at_each_layer()
test_turn_gate_requires_a_sandbox()
test_bare_asgi_admin_rejects_non_loopback_clients()
print("ALL 14 CHECKS PASS")
