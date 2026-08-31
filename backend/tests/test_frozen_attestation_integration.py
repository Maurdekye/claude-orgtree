"""Frozen attestation against what actually landed on main.

This suite covers the three things the approved-install checkpoint could not
know about, because they were written after it:

* the **rotatable per-org bridge credential** must be attested, and attested
  HONESTLY.  The approved claim is per-ORG rotation.  Sandboxed nodes inside
  one org share a root-capable container and can read each other's bearer out
  of ``/proc``; they are mutually trusted at this boundary.  Several checks
  below exist specifically to fail an attestation that quietly upgraded that
  claim to per-node isolation.
* the **per-org network** work gave the backend a second listener whose
  approved bind address is not loopback on Linux, so "the admin port is
  loopback" stopped being a complete listener check.
* the **pins themselves** must match the files in this repository.  The
  checkpoint's manifest did not: six of its seven digests were stale against
  the files committed beside it, and every check still reported success
  because they were only ever exercised against synthetic fixtures.

⚠ THE HONESTY GATE.  A verifier that cannot fail is worth nothing.  Every
planted case below is a deliberately non-conforming install, and each one is
REQUIRED to be rejected with a named check code.  ``planted()`` refuses to
let a negative test pass by accident: it asserts the exact failing codes, so
a check that silently stopped running turns the test red instead of green.

Run directly:
    python backend/tests/test_frozen_attestation_integration.py
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Callable


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# A private data root: nothing here may read, mint into, or mutate the
# operator's real ~/orgtree. Set before importing anything that captures it.
DATA = tempfile.mkdtemp(prefix="orgtree-attest-integration-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_PORT"] = "7417"
os.environ["ORGTREE_BRIDGE_PORT"] = "7417"
os.environ["ORGTREE_DEPLOYMENT_PROFILE"] = "standard"
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    json.dump({"net_hub_address": "http://127.0.0.1:9"}, f)

from orgtree import (bridgeauth, deployment,  # noqa: E402
                     frozen_install, store)
from orgtree.ledger import USER  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
PASS = 0


def check(label: str, fn: Callable[[], None]) -> None:
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def codes(report: frozen_install.AttestationReport) -> set[str]:
    return {c.code for c in report.failures}


# --------------------------------------------------------------------------
# a conforming synthetic install, then one defect at a time
# --------------------------------------------------------------------------

def good_launch() -> dict[str, Any]:
    return {
        "mode": "live", "supported": True,
        "command": "python -m orgtree.api",
        "deployment_profile": "frozen",
        "admin_hosts": ["127.0.0.1"], "public_port": 0, "expose_admin": None,
        "admin_port": 7360, "bridge_port": 7362,
        "listeners": [{"ip": "127.0.0.1", "port": 7360},
                      {"ip": "127.0.0.1", "port": 7362}],
        "expected_bridge_hosts": ["127.0.0.1"],
    }


def good_bridge() -> dict[str, Any]:
    return {
        "key_path": os.path.join(DATA, ".bridge-credentials.key"),
        "key_present": True,
        "sandboxed_orgs": ["acme"],
        "orgs": {"acme": {
            "scheme": "hmac-sha256-org-v1", "scope": "org", "org": "acme",
            "generation": 3, "fingerprint": "sha256:" + "a" * 64,
            "rotated_at": "2026-08-31T00:00:00Z",
            "legacy_credentials_accepted": False,
            "same_org_nodes_mutually_trusted": True,
            "previous_generation_rejected": True}},
        "error": "",
    }


def build_fixture(root: Path) -> str:
    """A minimal but fully conforming approved installation on disk."""
    (root / "frozen").mkdir(parents=True)
    (root / "frontend" / "node_modules").mkdir(parents=True)
    (root / "frozen" / "requirements.txt").write_text(
        "alpha==1.0.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    (root / "frozen" / "sandbox.Dockerfile").write_text(
        "FROM example.invalid/base@sha256:" + "b" * 64 + "\n",
        encoding="utf-8")
    lock = {"lockfileVersion": 3,
            "packages": {"": {}, "node_modules/ui": {
                "version": "3.0.0", "integrity": "sha512-ui"}}}
    for rel in ("frontend/package-lock.json",
                "frontend/node_modules/.package-lock.json"):
        (root / rel).write_text(json.dumps(lock, indent=2) + "\n",
                                encoding="utf-8")
    manifest: dict[str, Any] = {
        "schema": 1, "profile": "frozen",
        "files": {"frozen/requirements.txt": "",
                  "frontend/package-lock.json": "",
                  "frozen/sandbox.Dockerfile": ""},
        "python": {"requirements": "frozen/requirements.txt",
                   "implementation": __import__("platform").python_implementation(),
                   "versions": [f"{sys.version_info.major}."
                                f"{sys.version_info.minor}"],
                   "platform_system": __import__("platform").system(),
                   "platform_machine": __import__("platform").machine(),
                   "allowed_bootstrap_packages": ["pip"]},
        "frontend": {"lockfile": "frontend/package-lock.json",
                     "installed_lockfile":
                         "frontend/node_modules/.package-lock.json"},
        "providers": [{"id": "claude", "package": "@vendor/cli",
                       "prefix": "cli", "version": "4.0.0",
                       "integrity": "sha512-cli", "required": True}],
        "containers": [{"id": "sandbox", "repository": "orgtree-sandbox",
                        "dockerfile": "frozen/sandbox.Dockerfile",
                        "platform": "linux/amd64", "required": True,
                        "labels": {"io.orgtree.frozen.component": "sandbox"}}],
        "bridge": {"scheme": "hmac-sha256-org-v1", "scope": "org",
                   "same_org_nodes_mutually_trusted": True},
    }
    path = root / "frozen" / "approved-install.json"
    for rel in manifest["files"]:
        manifest["files"][rel] = sha(root / rel)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return sha(path)


def run(root: Path, digest: str, **changes: Any) \
        -> frozen_install.AttestationReport:
    values: dict[str, Any] = {
        "python_inventory": {"alpha": "1.0.0", "pip": "99.0"},
        "frontend_inventory": {"node_modules/ui": {
            "version": "3.0.0", "integrity": "sha512-ui"}},
        "provider_inventory": {"claude": {
            "installed": True, "private_present": True, "source": "pin",
            "version": "4.0.0", "integrity": "sha512-cli"}},
        "container_inventory": {"sandbox": {
            "exists": True, "image_id": "sha256:img",
            "labels": {"io.orgtree.frozen.component": "sandbox",
                       "io.orgtree.frozen.config": digest}}},
        "launch_inventory": good_launch(),
        "bridge_inventory": good_bridge(),
    }
    values.update(changes)
    return frozen_install.verify_approved_install(
        root=root, policy=deployment.FROZEN,
        expected_manifest_sha256=digest, **values)


def with_fixture(fn: Callable[[Path, str], None]) -> None:
    with tempfile.TemporaryDirectory(prefix="orgtree-attest-fx-") as td:
        root = Path(td)
        fn(root, build_fixture(root))


def planted(expected_codes: set[str], **changes: Any) -> Callable[[], None]:
    """Assert a deliberately non-conforming install is rejected, precisely.

    Equality — not ``in`` — is the point. A check that stopped running would
    shrink this set, and a check that started firing spuriously would grow it.
    Both are failures of the instrument, and both turn this test red.
    """
    def fn() -> None:
        def body(root: Path, digest: str) -> None:
            report = run(root, digest, **changes)
            assert not report.ok, "planted defect was ACCEPTED: " + \
                frozen_install.format_report(report, verbose=True)
            assert codes(report) == expected_codes, \
                f"expected {sorted(expected_codes)}, got {sorted(codes(report))}"
        with_fixture(body)
    return fn


def mutate_bridge(**fields: Any) -> dict[str, Any]:
    inv = good_bridge()
    inv["orgs"]["acme"].update(fields)
    return inv


def mutate_launch(**fields: Any) -> dict[str, Any]:
    inv = good_launch()
    inv.update(fields)
    return inv


# --------------------------------------------------------------------------
# 1. the instrument can pass, and the planted-fault gate can fail it
# --------------------------------------------------------------------------

def conforming_install_attests() -> None:
    def body(root: Path, digest: str) -> None:
        report = run(root, digest)
        assert report.ok, frozen_install.format_report(report, verbose=True)
        # A pass must actually have performed the new work, not skipped it.
        seen = {c.code for c in report.checks}
        for required in ("BRIDGE_SCOPE", "BRIDGE_PREVIOUS_GENERATION_REJECTED",
                         "BRIDGE_LEGACY_CREDENTIALS_REFUSED",
                         "NO_WILDCARD_LISTENER", "LISTENER_PORT_SET",
                         "BRIDGE_LISTENER_HOST_ONLY"):
            assert required in seen, f"{required} never ran"
    with_fixture(body)


def planted_source_pin_is_caught() -> None:
    def body(root: Path, digest: str) -> None:
        # One byte of an approved lock file, changed after approval.
        p = root / "frozen" / "requirements.txt"
        p.write_text(p.read_text(encoding="utf-8").replace("1.0.0", "1.0.1"),
                     encoding="utf-8")
        report = run(root, digest)
        assert "SOURCE_FILE_DIGEST" in codes(report), codes(report)
        assert frozen_install.format_report(report).startswith(
            "FROZEN INSTALLATION REFUSED")
        assert "Nothing was approved." in frozen_install.format_report(report)
    with_fixture(body)


def planted_manifest_edit_stops_all_trust() -> None:
    def body(root: Path, digest: str) -> None:
        path = root / "frozen" / "approved-install.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["providers"][0]["version"] = "9.9.9"     # self-approve a new pin
        path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        report = run(root, digest)
        # The edited document must not be used to decide what is approved:
        # verification stops at the digest and no later check runs at all.
        assert codes(report) == {"MANIFEST_DIGEST"}, codes(report)
        assert {c.code for c in report.checks} == {
            "DEPLOYMENT_PROFILE", "MANIFEST_DIGEST"}
    with_fixture(body)


# --------------------------------------------------------------------------
# 2. bridge attestation — including the per-node overclaim guards
# --------------------------------------------------------------------------

def bridge_scope_must_stay_per_org() -> None:
    # ⚠ ADJUDICATED: the frozen bridge is a rotatable PER-ORG credential.
    # An attestation record claiming node scope is describing isolation this
    # deployment does not provide, and must be refused.
    planted({"BRIDGE_SCOPE"}, bridge_inventory=mutate_bridge(scope="node"))()


def bridge_must_not_deny_same_org_trust() -> None:
    # The mirror image of the above, and the more dangerous direction: a
    # record that says same-org nodes are NOT mutually trusted is an
    # overclaim, because one sibling can read another's bearer from /proc.
    planted({"BRIDGE_TRUST_BOUNDARY_DECLARED"},
            bridge_inventory=mutate_bridge(
                same_org_nodes_mutually_trusted=False))()


def rotation_that_does_not_rotate_is_caught() -> None:
    planted({"BRIDGE_PREVIOUS_GENERATION_REJECTED"},
            bridge_inventory=mutate_bridge(
                previous_generation_rejected=False))()


def never_rotated_org_is_allowed() -> None:
    def body(root: Path, digest: str) -> None:
        # generation 0 has no previous credential to reject; ``None`` is the
        # honest answer and must not be treated as a failure.
        report = run(root, digest, bridge_inventory=mutate_bridge(
            generation=0, previous_generation_rejected=None))
        assert report.ok, frozen_install.format_report(report, verbose=True)
    with_fixture(body)


def legacy_shared_root_is_caught() -> None:
    planted({"BRIDGE_LEGACY_CREDENTIALS_REFUSED"},
            bridge_inventory=mutate_bridge(
                legacy_credentials_accepted=True))()


def bridge_scheme_drift_is_caught() -> None:
    planted({"BRIDGE_SCHEME"},
            bridge_inventory=mutate_bridge(scheme="hmac-sha256-node-v9"))()


def recoverable_bearer_is_caught() -> None:
    planted({"BRIDGE_FINGERPRINT"},
            bridge_inventory=mutate_bridge(fingerprint="otb1.YWNtZQ.deadbeef"))()


def missing_install_key_is_caught() -> None:
    def body(root: Path, digest: str) -> None:
        inv = good_bridge()
        inv["key_present"] = False
        inv["error"] = "no host-only bridge credential key"
        report = run(root, digest, bridge_inventory=inv)
        # Without a key there is nothing to attest: the per-org checks must
        # be SKIPPED rather than silently passing on stale records.
        assert codes(report) == {"BRIDGE_KEY_PRESENT"}, codes(report)
        assert "BRIDGE_SCOPE" not in {c.code for c in report.checks}
    with_fixture(body)


def uncovered_sandboxed_org_is_caught() -> None:
    inv = good_bridge()
    inv["sandboxed_orgs"] = ["acme", "forgotten"]
    planted({"BRIDGE_ORGS_COVERED"}, bridge_inventory=inv)()


def key_inside_a_sandbox_bind_is_caught() -> None:
    from orgtree import sandbox as sandbox_mod
    inv = good_bridge()
    inv["key_path"] = os.path.join(
        sandbox_mod.sandbox_root("acme"), ".bridge-credentials.key")
    planted({"BRIDGE_KEY_HOST_ONLY"}, bridge_inventory=inv)()


def manifest_without_a_bridge_section_is_caught() -> None:
    def body(root: Path, digest: str) -> None:
        # An approved manifest that simply omits the boundary cannot be used
        # to attest it. (Digest recomputed: this is an *approved* document
        # that is nonetheless incomplete, not a tampered one.)
        path = root / "frozen" / "approved-install.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc.pop("bridge")
        path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        report = run(root, sha(path))
        assert codes(report) == {"BRIDGE_SPEC_INVALID"}, codes(report)
    with_fixture(body)


def manifest_claiming_node_isolation_is_caught() -> None:
    def body(root: Path, digest: str) -> None:
        path = root / "frozen" / "approved-install.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["bridge"]["scope"] = "node"
        doc["bridge"]["same_org_nodes_mutually_trusted"] = False
        path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
        report = run(root, sha(path))
        # Even a correctly-digested manifest cannot approve the wrong claim.
        assert codes(report) == {"BRIDGE_SPEC_INVALID"}, codes(report)
    with_fixture(body)


# --------------------------------------------------------------------------
# 3. the listener table, as the per-org network work left it
# --------------------------------------------------------------------------

def open_public_kiosk_listener_is_caught() -> None:
    # ORGTREE_PUBLIC_PORT=0 in the environment, but a kiosk listener is
    # actually bound. Only the kernel table can see this.
    inv = good_launch()
    inv["listeners"] = inv["listeners"] + [{"ip": "127.0.0.1", "port": 7361}]
    planted({"LISTENER_PORT_SET"}, launch_inventory=inv)()


def public_bridge_bind_is_caught() -> None:
    inv = good_launch()
    inv["listeners"] = [{"ip": "127.0.0.1", "port": 7360},
                        {"ip": "0.0.0.0", "port": 7362}]
    planted({"NO_WILDCARD_LISTENER", "BRIDGE_LISTENER_HOST_ONLY"},
            launch_inventory=inv)()


def live_evidence_without_a_listener_table_is_caught() -> None:
    inv = good_launch()
    inv["listeners"] = None
    planted({"LISTENER_TABLE_OBSERVED"}, launch_inventory=inv)()


def prebind_startup_evidence_is_accepted() -> None:
    def body(root: Path, digest: str) -> None:
        # Startup runs before uvicorn binds. That is a real limit, recorded
        # honestly, not a check quietly reported as passing.
        report = run(root, digest, launch_inventory=mutate_launch(
            mode="planned", listeners=None))
        assert report.ok, frozen_install.format_report(report, verbose=True)
    with_fixture(body)


def linux_docker_gateway_bridge_bind_is_accepted() -> None:
    def body(root: Path, digest: str) -> None:
        # On native Linux the relay reaches the backend through Docker's
        # host-side bridge gateway, which is host-only but NOT loopback.
        # Demanding loopback everywhere would be a wrong assertion.
        report = run(root, digest, launch_inventory=mutate_launch(
            listeners=[{"ip": "127.0.0.1", "port": 7360},
                       {"ip": "172.17.0.1", "port": 7362}],
            expected_bridge_hosts=["172.17.0.1"]))
        assert report.ok, frozen_install.format_report(report, verbose=True)
    with_fixture(body)


def unresolvable_bridge_address_is_not_a_pass() -> None:
    planted({"BRIDGE_LISTENER_HOST_ONLY"},
            launch_inventory=mutate_launch(expected_bridge_hosts=[]))()


def disabled_bridge_port_is_caught() -> None:
    planted({"BRIDGE_LISTENER_HOST_ONLY", "LISTENER_PORT_SET"},
            launch_inventory=mutate_launch(
                bridge_port=0,
                listeners=[{"ip": "127.0.0.1", "port": 7360},
                           {"ip": "127.0.0.1", "port": 7362}]))()


def direct_uvicorn_is_still_refused() -> None:
    # Restated here because it is a contract boundary, not an implementation
    # detail: a direct uvicorn command can select a public bind that ASGI
    # preflight inside the app can never observe.
    planted({"LAUNCH_PATH_SUPPORTED"},
            launch_inventory=mutate_launch(
                supported=False,
                command="uvicorn orgtree.api:app --host 0.0.0.0"))()


def public_port_must_be_a_literal_zero() -> None:
    # The other contract boundary, restated for the same reason: the update
    # scripts supply their standard-mode default of 7361 unless the variable
    # is present with value "0", so UNSET is not the frozen contract and
    # blank is not either. Both must be refused, not normalised to zero.
    for absent in (None, "", "7361"):
        planted({"PUBLIC_LISTENER_DISABLED"},
                launch_inventory=mutate_launch(public_port=absent))()


def admin_exposure_blank_is_not_unset() -> None:
    planted({"ADMIN_EXPOSURE_UNSET"},
            launch_inventory=mutate_launch(expose_admin=""))()


def a_standard_profile_backend_is_refused() -> None:
    planted({"LAUNCH_PROFILE_ACTIVE"},
            launch_inventory=mutate_launch(deployment_profile="standard"))()


# --------------------------------------------------------------------------
# 4. the REAL repository's pins, not a fixture
# --------------------------------------------------------------------------

def committed_manifest_matches_its_pinned_digest() -> None:
    path = REPO_ROOT / "frozen" / "approved-install.json"
    assert path.is_file(), path
    assert sha(path) == frozen_install.APPROVED_MANIFEST_SHA256, (
        "frozen/approved-install.json does not match "
        "APPROVED_MANIFEST_SHA256 in frozen_install.py")


def committed_pins_match_the_files_beside_them() -> None:
    # The exact defect the landed checkpoint carried: a manifest whose file
    # digests described an earlier state of the tree.
    doc = json.loads((REPO_ROOT / "frozen" / "approved-install.json")
                     .read_text(encoding="utf-8"))
    stale = []
    for rel, expected in doc["files"].items():
        p = REPO_ROOT / rel
        assert p.is_file(), f"pinned file missing from the repo: {rel}"
        if sha(p) != expected:
            stale.append(rel)
    assert not stale, f"stale pins in approved-install.json: {stale}"


def frozen_boundary_source_is_pinned() -> None:
    doc = json.loads((REPO_ROOT / "frozen" / "approved-install.json")
                     .read_text(encoding="utf-8"))
    # The relay IS the operation allowlist and is bind-mounted into the only
    # dual-homed container; bridgeauth defines the credential scheme. Both
    # are part of the frozen boundary, so both are approved content.
    for rel in ("backend/orgtree/frozen_gateway.py",
                "backend/orgtree/bridgeauth.py"):
        assert rel in doc["files"], f"{rel} is not pinned by the manifest"


def committed_manifest_declares_the_adjudicated_boundary() -> None:
    doc = json.loads((REPO_ROOT / "frozen" / "approved-install.json")
                     .read_text(encoding="utf-8"))
    assert doc["bridge"]["scope"] == "org"
    assert doc["bridge"]["same_org_nodes_mutually_trusted"] is True
    assert doc["bridge"]["scheme"] == "hmac-sha256-org-v1"


def image_tags_follow_the_rebuilt_digest() -> None:
    digest = frozen_install.APPROVED_MANIFEST_SHA256
    commands = frozen_install.build_commands(REPO_ROOT)
    assert len(commands) == 2, commands
    for cmd in commands:
        assert f"frozen-{digest[:16]}" in cmd, cmd
        assert f"--build-arg ORGTREE_FROZEN_CONFIG={digest}" in cmd, cmd


# --------------------------------------------------------------------------
# 5. the real bridgeauth round trip (not a hand-written fixture)
# --------------------------------------------------------------------------

def real_rotation_attestation_satisfies_the_checks() -> None:
    """Drive the actual bridgeauth code and attest its real output.

    Everything above uses injected inventories. This proves the shape the
    verifier expects is the shape ``credential_attestation`` really returns —
    otherwise the whole suite could agree perfectly with itself and disagree
    with the program.
    """
    org = store.create_org("attest integration rig")
    slug = org.d["slug"]
    with store.DOC_LOCK:
        org = store.load_org(slug)
        org.d["sandbox"] = {"enabled": True, "secret": "5c" * 16}
        org.hire(USER, None, "haiku", 0, "alpha")
        store.save_org(org)

    old = os.environ.get(deployment.PROFILE_ENV)
    os.environ[deployment.PROFILE_ENV] = "frozen"
    try:
        before = bridgeauth.credential_attestation(store.load_org(slug))
        assert before["scope"] == "org"
        assert before["generation"] == 0
        assert before["previous_generation_rejected"] is None
        assert before["same_org_nodes_mutually_trusted"] is True
        assert before["legacy_credentials_accepted"] is False

        live_before = bridgeauth.org_credential(store.load_org(slug))
        receipt = bridgeauth.rotate_org_credential(slug)
        assert receipt["previous_generation_rejected"] is True
        assert receipt["old_credential_rejected"] is True
        # The credential that worked a moment ago must not work now.
        assert bridgeauth.resolve_org_credential(live_before) is None
        assert bridgeauth.resolve_org_credential(
            bridgeauth.org_credential(store.load_org(slug))) == slug
        # No bearer may appear anywhere in the attestation record.
        assert live_before not in json.dumps(receipt)

        after = bridgeauth.credential_attestation(store.load_org(slug))
        inv = {"key_path": bridgeauth.credential_key_path(),
               "key_present": True, "sandboxed_orgs": [slug],
               "orgs": {slug: after}, "error": ""}

        def body(root: Path, digest: str) -> None:
            report = run(root, digest, bridge_inventory=inv)
            assert report.ok, frozen_install.format_report(report,
                                                           verbose=True)
        with_fixture(body)
    finally:
        if old is None:
            os.environ.pop(deployment.PROFILE_ENV, None)
        else:
            os.environ[deployment.PROFILE_ENV] = old


def the_verifier_never_mints_a_key() -> None:
    """The standalone verifier is read-only; only startup may mint."""
    key = Path(bridgeauth.credential_key_path())
    saved = key.read_bytes() if key.is_file() else None
    if key.is_file():
        key.unlink()
    old_ready = bridgeauth._KEY_READY
    old = os.environ.get(deployment.PROFILE_ENV)
    os.environ[deployment.PROFILE_ENV] = "frozen"
    bridgeauth._KEY_READY = False
    try:
        inv = frozen_install._bridge_inventory()
        assert inv["key_present"] is False, inv
        assert not key.exists(), "the read-only verifier MINTED a bridge key"
    finally:
        bridgeauth._KEY_READY = old_ready
        if saved is not None:
            key.write_bytes(saved)
        if old is None:
            os.environ.pop(deployment.PROFILE_ENV, None)
        else:
            os.environ[deployment.PROFILE_ENV] = old


# --------------------------------------------------------------------------
# 6. standard mode is untouched — deploying main must not freeze this install
# --------------------------------------------------------------------------

def standard_mode_is_a_complete_no_op() -> None:
    saved = os.environ.pop(deployment.PROFILE_ENV, None)
    try:
        assert deployment.current_policy().name == "standard", (
            "an unset profile must remain the existing standard deployment")
        # No manifest read, no key mint, no docker call, no refusal.
        frozen_install.require_approved_install(
            policy=deployment.current_policy())
        report = frozen_install.verify_approved_install(
            root=Path("definitely-does-not-exist"), policy=deployment.STANDARD)
        assert codes(report) == {"DEPLOYMENT_PROFILE"}, codes(report)
    finally:
        if saved is not None:
            os.environ[deployment.PROFILE_ENV] = saved


def blank_profile_is_standard() -> None:
    saved = os.environ.get(deployment.PROFILE_ENV)
    os.environ[deployment.PROFILE_ENV] = "  "
    try:
        assert deployment.current_policy().name == "standard"
    finally:
        if saved is None:
            os.environ.pop(deployment.PROFILE_ENV, None)
        else:
            os.environ[deployment.PROFILE_ENV] = saved


def main() -> None:
    print("frozen attestation integration")
    check("a conforming install attests, and ran the new checks",
          conforming_install_attests)
    check("PLANTED: an edited approved lock file is caught",
          planted_source_pin_is_caught)
    check("PLANTED: a self-approving manifest edit stops all trust",
          planted_manifest_edit_stops_all_trust)

    print("bridge (rotatable per-org, NOT per-node)")
    check("PLANTED: node-scoped attestation is refused",
          bridge_scope_must_stay_per_org)
    check("PLANTED: denying same-org mutual trust is refused",
          bridge_must_not_deny_same_org_trust)
    check("PLANTED: a rotation that does not rotate is caught",
          rotation_that_does_not_rotate_is_caught)
    check("a never-rotated org is allowed", never_rotated_org_is_allowed)
    check("PLANTED: accepting the legacy shared root is caught",
          legacy_shared_root_is_caught)
    check("PLANTED: credential scheme drift is caught",
          bridge_scheme_drift_is_caught)
    check("PLANTED: a recoverable bearer in the record is caught",
          recoverable_bearer_is_caught)
    check("PLANTED: a missing install key skips, not passes, the org checks",
          missing_install_key_is_caught)
    check("PLANTED: an unattested sandboxed org is caught",
          uncovered_sandboxed_org_is_caught)
    check("PLANTED: a key inside a sandbox bind is caught",
          key_inside_a_sandbox_bind_is_caught)
    check("PLANTED: a manifest with no bridge section cannot attest one",
          manifest_without_a_bridge_section_is_caught)
    check("PLANTED: a manifest claiming node isolation is refused",
          manifest_claiming_node_isolation_is_caught)

    print("listener table (per-org network)")
    check("PLANTED: an open public kiosk listener is caught",
          open_public_kiosk_listener_is_caught)
    check("PLANTED: a public bridge bind is caught",
          public_bridge_bind_is_caught)
    check("PLANTED: live evidence with no listener table is caught",
          live_evidence_without_a_listener_table_is_caught)
    check("pre-bind startup evidence is accepted and labelled",
          prebind_startup_evidence_is_accepted)
    check("a Linux docker-gateway bridge bind is accepted",
          linux_docker_gateway_bridge_bind_is_accepted)
    check("PLANTED: an unresolvable bridge address is not a pass",
          unresolvable_bridge_address_is_not_a_pass)
    check("PLANTED: a disabled bridge port is caught",
          disabled_bridge_port_is_caught)
    check("PLANTED: direct uvicorn launch is still refused",
          direct_uvicorn_is_still_refused)
    check("PLANTED: ORGTREE_PUBLIC_PORT must be a literal 0",
          public_port_must_be_a_literal_zero)
    check("PLANTED: a blank ORGTREE_EXPOSE_ADMIN is not unset",
          admin_exposure_blank_is_not_unset)
    check("PLANTED: a standard-profile backend process is refused",
          a_standard_profile_backend_is_refused)

    print("the real repository's pins")
    check("the committed manifest matches its pinned digest",
          committed_manifest_matches_its_pinned_digest)
    check("every committed pin matches the file beside it",
          committed_pins_match_the_files_beside_them)
    check("the frozen boundary's own source is pinned",
          frozen_boundary_source_is_pinned)
    check("the committed manifest declares the adjudicated boundary",
          committed_manifest_declares_the_adjudicated_boundary)
    check("image tags follow the rebuilt digest",
          image_tags_follow_the_rebuilt_digest)

    print("the real bridgeauth round trip")
    check("real rotation output satisfies the attestation checks",
          real_rotation_attestation_satisfies_the_checks)
    check("the standalone verifier never mints a key",
          the_verifier_never_mints_a_key)

    print("standard mode")
    check("standard mode is a complete no-op", standard_mode_is_a_complete_no_op)
    check("a blank profile stays standard", blank_profile_is_standard)

    print(f"ALL {PASS} CHECKS PASS")


if __name__ == "__main__":
    try:
        main()
    finally:
        shutil.rmtree(DATA, ignore_errors=True)
