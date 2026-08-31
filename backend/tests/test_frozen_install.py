"""Frozen approved-configuration attestation, including mutation negatives.

This suite is hermetic: it builds a tiny approved installation in a temporary
directory and injects runtime inventories. Docker, npm, live providers, and the
operator's real data directory are never touched.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
from typing import Any, Callable


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree import deployment, frozen_install, sandbox  # noqa: E402


CHECKS = 0


def check(name: str, fn: Callable[[], None]) -> None:
    global CHECKS
    try:
        fn()
    except Exception as e:
        print(f"FAIL {name}: {e}")
        raise
    CHECKS += 1
    print(f"ok {CHECKS} - {name}")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def approve(root: Path) -> str:
    path = root / "frozen" / "approved-install.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for rel in manifest["files"]:
        manifest["files"][rel] = sha(root / rel)
    write_json(path, manifest)
    return sha(path)


def fixture(root: Path) -> str:
    (root / "frozen").mkdir(parents=True)
    (root / "frontend" / "node_modules").mkdir(parents=True)
    (root / "frozen" / "requirements.txt").write_text(
        "alpha==1.0.0 --hash=sha256:" + "a" * 64
        + "\nbeta==2.0.0 --hash=sha256:" + "b" * 64 + "\n",
        encoding="utf-8")
    (root / "frozen" / "sandbox.Dockerfile").write_text(
        "FROM example.invalid/base@sha256:" + "a" * 64 + "\n",
        encoding="utf-8")
    write_json(root / "frontend" / "package-lock.json", {
        "lockfileVersion": 3,
        "packages": {
            "": {},
            "node_modules/ui": {
                "version": "3.0.0", "integrity": "sha512-ui"},
            "node_modules/os-extra": {
                "version": "1.0.0", "integrity": "sha512-os",
                "optional": True},
        },
    })
    write_json(root / "frontend" / "node_modules" / ".package-lock.json", {
        "lockfileVersion": 3,
        "packages": {
            "node_modules/ui": {
                "version": "3.0.0", "integrity": "sha512-ui"},
        },
    })
    manifest = {
        "schema": 1,
        "profile": "frozen",
        "files": {
            "frozen/requirements.txt": "pending",
            "frontend/package-lock.json": "pending",
            "frozen/sandbox.Dockerfile": "pending",
        },
        "python": {
            "requirements": "frozen/requirements.txt",
            "implementation": "CPython",
            "versions": [f"{sys.version_info.major}.{sys.version_info.minor}"],
            "platform_system": platform.system(),
            "platform_machine": platform.machine(),
            "allowed_bootstrap_packages": ["pip"],
        },
        "frontend": {
            "lockfile": "frontend/package-lock.json",
            "installed_lockfile": "frontend/node_modules/.package-lock.json",
        },
        "providers": [{
            "id": "claude", "package": "@vendor/cli", "prefix": "cli",
            "version": "4.0.0", "integrity": "sha512-cli",
            "required": True,
        }],
        "containers": [{
            "id": "sandbox", "repository": "orgtree-sandbox",
            "dockerfile": "frozen/sandbox.Dockerfile",
            "platform": "linux/amd64", "required": True,
            "labels": {"io.orgtree.frozen.component": "sandbox",
                       "io.orgtree.frozen.platform": "linux/amd64"},
        }],
        # The approved bridge boundary is a rotatable PER-ORG credential.
        # Same-org root-capable nodes are mutually trusted here; the manifest
        # states that literally so attestation cannot drift into claiming
        # per-node isolation. See test_frozen_attestation_integration.py.
        "bridge": {
            "scheme": "hmac-sha256-org-v1",
            "scope": "org",
            "same_org_nodes_mutually_trusted": True,
        },
    }
    write_json(root / "frozen" / "approved-install.json", manifest)
    return approve(root)


def inventories(digest: str) -> dict[str, Any]:
    return {
        "python_inventory": {"alpha": "1.0.0", "beta": "2.0.0",
                             "pip": "99.0"},
        "frontend_inventory": {
            "node_modules/ui": {
                "version": "3.0.0", "integrity": "sha512-ui"}},
        "provider_inventory": {
            "claude": {"installed": True, "private_present": True,
                       "source": "pin", "version": "4.0.0",
                       "integrity": "sha512-cli"}},
        "container_inventory": {
            "sandbox": {"exists": True, "image_id": "sha256:image",
                        "labels": {
                            "io.orgtree.frozen.component": "sandbox",
                            "io.orgtree.frozen.platform": "linux/amd64",
                            "io.orgtree.frozen.config": digest}}},
        "launch_inventory": {
            "mode": "live", "supported": True,
            "command": "python -m orgtree.api",
            "deployment_profile": "frozen",
            "admin_hosts": ["127.0.0.1"], "public_port": 0,
            "expose_admin": None,
            # Since the frozen network landed the backend holds two listeners:
            # the loopback admin app and the sandbox bridge the per-org relay
            # dials. The whole table is checked, not just the admin port.
            "admin_port": 7360, "bridge_port": 7362,
            "listeners": [{"ip": "127.0.0.1", "port": 7360},
                          {"ip": "127.0.0.1", "port": 7362}],
            "expected_bridge_hosts": ["127.0.0.1"]},
        "bridge_inventory": {
            "key_path": "/host-only/.bridge-credentials.key",
            "key_present": True,
            "sandboxed_orgs": ["acme"],
            "orgs": {"acme": {
                "scheme": "hmac-sha256-org-v1", "scope": "org", "org": "acme",
                "generation": 2, "fingerprint": "sha256:" + "c" * 64,
                "rotated_at": "2026-08-31T00:00:00Z",
                "legacy_credentials_accepted": False,
                "same_org_nodes_mutually_trusted": True,
                "previous_generation_rejected": True}},
            "error": ""},
    }


def verify(root: Path, digest: str, **changes: Any) \
        -> frozen_install.AttestationReport:
    values = inventories(digest)
    values.update(changes)
    return frozen_install.verify_approved_install(
        root=root, policy=deployment.FROZEN,
        expected_manifest_sha256=digest, **values)


def codes(report: frozen_install.AttestationReport) -> set[str]:
    return {v.code for v in report.failures}


def with_fixture(fn: Callable[[Path, str], None]) -> None:
    with tempfile.TemporaryDirectory(prefix="orgtree-frozen-attest-") as td:
        root = Path(td)
        fn(root, fixture(root))


def positive() -> None:
    def run(root: Path, digest: str) -> None:
        report = verify(root, digest)
        assert report.ok, frozen_install.format_report(report, verbose=True)
        assert report.configuration_sha256 == digest
        assert frozen_install.format_report(report).startswith(
            "FROZEN INSTALLATION VERIFIED")
    with_fixture(run)


def standard_is_untouched() -> None:
    # An explicit attestation refuses to mislabel standard as frozen, but the
    # startup enforcement entry point performs no reads or mutations at all.
    report = frozen_install.verify_approved_install(
        root=Path("definitely-does-not-exist"), policy=deployment.STANDARD)
    assert codes(report) == {"DEPLOYMENT_PROFILE"}
    frozen_install.require_approved_install(policy=deployment.STANDARD)


def manifest_mutation() -> None:
    def run(root: Path, digest: str) -> None:
        path = root / "frozen" / "approved-install.json"
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        report = verify(root, digest)
        assert codes(report) == {"MANIFEST_DIGEST"}, codes(report)
    with_fixture(run)


def source_mutation() -> None:
    def run(root: Path, digest: str) -> None:
        (root / "frozen" / "requirements.txt").write_text(
            "alpha==1.0.1\nbeta==2.0.0\n", encoding="utf-8")
        report = verify(root, digest)
        assert "SOURCE_FILE_DIGEST" in codes(report)
    with_fixture(run)


def non_exact_requirement() -> None:
    def run(root: Path, _digest: str) -> None:
        (root / "frozen" / "requirements.txt").write_text(
            "alpha>=1.0.0\nbeta==2.0.0\n", encoding="utf-8")
        digest = approve(root)
        report = verify(root, digest)
        assert "PYTHON_LOCK_INVALID" in codes(report)
    with_fixture(run)


def unhashed_requirement() -> None:
    def run(root: Path, _digest: str) -> None:
        (root / "frozen" / "requirements.txt").write_text(
            "alpha==1.0.0\nbeta==2.0.0 --hash=sha256:" + "b" * 64 + "\n",
            encoding="utf-8")
        digest = approve(root)
        report = verify(root, digest)
        assert "PYTHON_LOCK_INVALID" in codes(report)
        failure = next(v for v in report.failures
                       if v.code == "PYTHON_LOCK_INVALID")
        assert "authenticate" in failure.detail
    with_fixture(run)


def python_missing_and_extra() -> None:
    def run(root: Path, digest: str) -> None:
        report = verify(root, digest, python_inventory={
            "alpha": "1.0.0", "gamma": "9.0.0", "pip": "99.0"})
        assert "PYTHON_PACKAGE_VERSION" in codes(report)
        assert "PYTHON_PACKAGE_SET" in codes(report)
    with_fixture(run)


def unsupported_python_runtime() -> None:
    def run(root: Path, _digest: str) -> None:
        path = root / "frozen" / "approved-install.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["python"]["implementation"] = "NotPython"
        write_json(path, manifest)
        digest = approve(root)
        report = verify(root, digest)
        assert codes(report) == {"PYTHON_RUNTIME"}, codes(report)
    with_fixture(run)


def frontend_mutation() -> None:
    def run(root: Path, digest: str) -> None:
        report = verify(root, digest, frontend_inventory={
            "node_modules/ui": {
                "version": "3.0.0", "integrity": "sha512-tampered"},
            "node_modules/unapproved": {"version": "1.0.0"},
        })
        assert codes(report) == {"FRONTEND_PACKAGE_TREE"}, codes(report)
        failure = report.failures[0]
        assert "extra=" in failure.detail and "version/integrity=" in failure.detail
    with_fixture(run)


def provider_override_and_version() -> None:
    def run(root: Path, digest: str) -> None:
        report = verify(root, digest, provider_inventory={
            "claude": {"installed": True, "private_present": True,
                       "source": "env", "version": "4.0.1",
                       "integrity": "sha512-other"}})
        assert codes(report) == {
            "PROVIDER_SOURCE", "PROVIDER_VERSION", "PROVIDER_INTEGRITY"}
    with_fixture(run)


def optional_provider_absent() -> None:
    def run(root: Path, _digest: str) -> None:
        path = root / "frozen" / "approved-install.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["providers"].append({
            "id": "optional", "package": "@vendor/optional",
            "prefix": "optional", "version": "1.0.0",
            "integrity": "sha512-optional", "required": False})
        write_json(path, manifest)
        digest = approve(root)
        report = verify(root, digest)
        assert report.ok, frozen_install.format_report(report, verbose=True)
        assert any(c.code == "PROVIDER_OPTIONAL_ABSENT" for c in report.checks)
    with_fixture(run)


def required_container_missing() -> None:
    def run(root: Path, digest: str) -> None:
        report = verify(root, digest, container_inventory={
            "sandbox": {"exists": False, "error": "No such image"}})
        assert codes(report) == {"CONTAINER_IMAGE_PRESENT"}, codes(report)
    with_fixture(run)


def container_label_mutation() -> None:
    def run(root: Path, digest: str) -> None:
        report = verify(root, digest, container_inventory={
            "sandbox": {"exists": True, "image_id": "sha256:other",
                        "labels": {
                            "io.orgtree.frozen.component": "sandbox",
                            "io.orgtree.frozen.platform": "linux/amd64",
                            "io.orgtree.frozen.config": "0" * 64}}})
        assert codes(report) == {"CONTAINER_IMAGE_LABELS"}, codes(report)
    with_fixture(run)


def frozen_sandbox_never_builds_lazily() -> None:
    old_profile = os.environ.get(deployment.PROFILE_ENV)
    old_require = frozen_install.require_approved_sandbox_image
    old_docker = sandbox._docker
    docker_calls: list[tuple[str, ...]] = []
    try:
        os.environ[deployment.PROFILE_ENV] = "frozen"
        frozen_install.require_approved_sandbox_image = \
            lambda root=None: "orgtree-sandbox:frozen-approved"
        sandbox._docker = lambda *args, **_kwargs: docker_calls.append(args)
        assert sandbox.ensure_image() == "orgtree-sandbox:frozen-approved"
        assert not docker_calls, docker_calls
    finally:
        frozen_install.require_approved_sandbox_image = old_require
        sandbox._docker = old_docker
        if old_profile is None:
            os.environ.pop(deployment.PROFILE_ENV, None)
        else:
            os.environ[deployment.PROFILE_ENV] = old_profile


def direct_uvicorn_refused() -> None:
    def run(root: Path, digest: str) -> None:
        # Otherwise an entirely conforming live install: the ONLY defect is
        # the unsupported launch command.
        bad = inventories(digest)["launch_inventory"]
        bad["supported"] = False
        bad["command"] = "python -m uvicorn orgtree.api:app --host 127.0.0.1"
        report = verify(root, digest, launch_inventory=bad)
        assert codes(report) == {"LAUNCH_PATH_SUPPORTED"}, codes(report)
    with_fixture(run)


def public_admin_bind_refused() -> None:
    def run(root: Path, digest: str) -> None:
        bad = inventories(digest)["launch_inventory"]
        bad["admin_hosts"] = ["0.0.0.0"]
        bad["listeners"] = [{"ip": "0.0.0.0", "port": 7360},
                            {"ip": "127.0.0.1", "port": 7362}]
        report = verify(root, digest, launch_inventory=bad)
        # A public admin bind now trips two independent checks: the admin
        # listener is not loopback, and the process holds a wildcard bind.
        assert codes(report) == {"ADMIN_LISTENER_LOOPBACK",
                                 "NO_WILDCARD_LISTENER"}, codes(report)
    with_fixture(run)


def public_port_must_be_explicit_zero() -> None:
    def run(root: Path, digest: str) -> None:
        report = verify(root, digest, launch_inventory={
            "mode": "planned", "supported": True,
            "command": "python -m orgtree.api",
            "deployment_profile": "frozen",
            "admin_hosts": ["127.0.0.1"], "public_port": None,
            "expose_admin": None})
        assert codes(report) == {"PUBLIC_LISTENER_DISABLED"}, codes(report)
    with_fixture(run)


def admin_exposure_must_be_unset() -> None:
    def run(root: Path, digest: str) -> None:
        report = verify(root, digest, launch_inventory={
            "mode": "planned", "supported": True,
            "command": "python -m orgtree.api",
            "deployment_profile": "frozen",
            "admin_hosts": ["127.0.0.1"], "public_port": 0,
            "expose_admin": ""})
        assert codes(report) == {"ADMIN_EXPOSURE_UNSET"}, codes(report)
    with_fixture(run)


def running_backend_profile_is_observed() -> None:
    def run(root: Path, digest: str) -> None:
        bad = inventories(digest)["launch_inventory"]
        bad["deployment_profile"] = "standard"
        report = verify(root, digest, launch_inventory=bad)
        assert codes(report) == {"LAUNCH_PROFILE_ACTIVE"}, codes(report)
    with_fixture(run)


def build_commands_are_content_addressed() -> None:
    def run(root: Path, digest: str) -> None:
        old = frozen_install.APPROVED_MANIFEST_SHA256
        try:
            frozen_install.APPROVED_MANIFEST_SHA256 = digest
            commands = frozen_install.build_commands(root)
        finally:
            frozen_install.APPROVED_MANIFEST_SHA256 = old
        assert len(commands) == 1
        assert digest in commands[0]
        assert frozen_install.container_tag("orgtree-sandbox", digest) in commands[0]
        assert "--platform linux/amd64" in commands[0]
        assert "frozen/sandbox.Dockerfile" in commands[0]
    with_fixture(run)


def main() -> None:
    check("an exact approved installation attests", positive)
    check("standard startup remains untouched", standard_is_untouched)
    check("manifest mutation fails before trusting its contents", manifest_mutation)
    check("locked source mutation is named", source_mutation)
    check("a floating Python requirement is refused", non_exact_requirement)
    check("an exact but unhashed Python artifact is refused", unhashed_requirement)
    check("missing and extra Python packages fail", python_missing_and_extra)
    check("unsupported backend runtimes fail", unsupported_python_runtime)
    check("frontend version, integrity, and extras are compared", frontend_mutation)
    check("provider overrides and version drift fail", provider_override_and_version)
    check("an absent optional provider remains allowed", optional_provider_absent)
    check("the required sandbox image must exist", required_container_missing)
    check("container label mutation fails", container_label_mutation)
    check("frozen sandbox selection never builds lazily",
          frozen_sandbox_never_builds_lazily)
    check("direct Uvicorn launch is unsupported", direct_uvicorn_refused)
    check("a public admin bind is observed and refused", public_admin_bind_refused)
    check("public port must be explicitly zero", public_port_must_be_explicit_zero)
    check("admin exposure must be absent, not blank", admin_exposure_must_be_unset)
    check("the running backend profile is observed", running_backend_profile_is_observed)
    check("frozen image builds are content-addressed", build_commands_are_content_addressed)
    print(f"ALL {CHECKS} CHECKS PASS")


if __name__ == "__main__":
    main()
