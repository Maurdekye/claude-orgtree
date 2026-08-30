"""Fail-closed API/startup controls for the frozen deployment profile.

Run directly:
    python backend/tests/test_frozen_policy_enforcement.py
"""

from __future__ import annotations

from contextlib import contextmanager
import os
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
from orgtree import api, deployment, store  # noqa: E402


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
                                slug, "migrate")
    finally:
        store.delete_org(slug)


def test_sandboxed_inventory_passes() -> None:
    org = store.create_org("Boxed Org")
    slug = org.d["slug"]
    try:
        org.d["sandbox"] = {"enabled": True, "secret": "a" * 32}
        store.save_org(org)
        with env(ORGTREE_DEPLOYMENT_PROFILE="frozen"):
            assert api._deployment_preflight() is deployment.FROZEN
    finally:
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


test_standard_preserves_exposure_controls()
test_frozen_admin_is_loopback_only()
test_frozen_rejects_public_listener()
test_frozen_rejects_legacy_credential_copy()
test_frozen_inventories_unsandboxed_orgs()
test_sandboxed_inventory_passes()
test_creation_refuses_before_writing()
print("ALL 7 CHECKS PASS")
