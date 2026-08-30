"""Focused checks for the install-wide frozen deployment selector.

Run directly:
    python backend/tests/test_deployment_policy.py
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import os
import sys
from typing import Iterator

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree import deployment  # noqa: E402


@contextmanager
def selected(value: str | None) -> Iterator[None]:
    old = os.environ.get(deployment.PROFILE_ENV)
    try:
        if value is None:
            os.environ.pop(deployment.PROFILE_ENV, None)
        else:
            os.environ[deployment.PROFILE_ENV] = value
        yield
    finally:
        if old is None:
            os.environ.pop(deployment.PROFILE_ENV, None)
        else:
            os.environ[deployment.PROFILE_ENV] = old


def test_standard_default() -> None:
    for value in (None, "", "  ", "standard", " STANDARD "):
        with selected(value):
            policy = deployment.current_policy()
            assert policy is deployment.STANDARD
            assert policy.name == "standard"
            assert not policy.require_sandboxed_orgs
            assert policy.allow_agent_restart
            assert policy.allow_public_listener
            assert policy.allow_admin_exposure
            assert policy.allow_legacy_sandbox_credentials
            assert policy.allow_sandbox_internet
            assert policy.allow_broad_anthropic_proxy


def test_frozen_policy() -> None:
    for value in ("frozen", " FROZEN "):
        with selected(value):
            policy = deployment.current_policy()
            assert policy is deployment.FROZEN
            assert policy.name == "frozen"
            assert policy.require_sandboxed_orgs
            assert not policy.allow_agent_restart
            assert not policy.allow_public_listener
            assert not policy.allow_admin_exposure
            assert not policy.allow_legacy_sandbox_credentials
            assert not policy.allow_sandbox_internet
            assert not policy.allow_broad_anthropic_proxy


def test_unknown_fails_closed() -> None:
    for value in ("hardened", "frozn", "0"):
        with selected(value):
            try:
                deployment.current_policy()
            except deployment.DeploymentConfigError as e:
                text = str(e)
                assert deployment.PROFILE_ENV in text
                assert "standard" in text and "frozen" in text
                assert value in text
                assert "less restrictive fallback" in text
            else:
                raise AssertionError(f"unknown selector {value!r} was accepted")


def test_policy_is_immutable() -> None:
    try:
        deployment.FROZEN.allow_agent_restart = True  # type: ignore[misc]
    except FrozenInstanceError:
        pass
    else:
        raise AssertionError("deployment policy was mutable")


test_standard_default()
test_frozen_policy()
test_unknown_fails_closed()
test_policy_is_immutable()
print("ALL 4 CHECKS PASS")
