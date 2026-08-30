"""Frozen rotatable per-org bridge credentials and secret-free attestation.

Run directly:
    python backend/tests/test_bridge_credentials.py
"""

from __future__ import annotations

from contextlib import contextmanager
import asyncio
import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile
import uuid

from fastapi import HTTPException, Request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-bridgeauth-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_PORT"] = "7416"
os.environ["ORGTREE_BRIDGE_PORT"] = "7416"
os.environ["ORGTREE_DEPLOYMENT_PROFILE"] = "standard"
os.makedirs(DATA, exist_ok=True)
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as f:
    json.dump({"net_hub_address": "http://127.0.0.1:9"}, f)

from orgtree import api, bridgeauth, deployment, sandbox, steer, store  # noqa: E402
from orgtree import disk as dsk  # noqa: E402
from orgtree.ledger import USER  # noqa: E402


PASS = 0
STALE_TOKEN = ""


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


@contextmanager
def profile(name: str):
    old = os.environ.get(deployment.PROFILE_ENV)
    os.environ[deployment.PROFILE_ENV] = name
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(deployment.PROFILE_ENV, None)
        else:
            os.environ[deployment.PROFILE_ENV] = old


def secret_free(value, *secrets_to_hide: str):
    blob = json.dumps(value, sort_keys=True)
    for secret in secrets_to_hide:
        assert not secret or secret not in blob
    return blob


org = store.create_org("bridge credential rig")
slug = org.d["slug"]
root = "8a" * 16
with store.DOC_LOCK:
    org = store.load_org(slug)
    org.d["sandbox"] = {"enabled": True, "secret": root}
    alpha = org.hire(USER, None, "haiku", 0, "alpha")["node"]
    beta = org.hire(USER, None, "haiku", 0, "beta")["node"]
    store.save_org(org)

other = store.create_org("bridge credential other")
other_slug = other.d["slug"]
other_root = "9b" * 16
with store.DOC_LOCK:
    other = store.load_org(other_slug)
    other.d["sandbox"] = {"enabled": True, "secret": other_root}
    gamma = other.hire(USER, None, "haiku", 0, "gamma")["node"]
    store.save_org(other)


class Res:
    def __init__(self, status, body):
        self.status = status
        self.body = body
        try:
            self.json = json.loads(body)
        except Exception:  # noqa: BLE001
            self.json = None

    @property
    def text(self):
        return self.body.decode("utf-8", "replace")


def call(app, method, path, body=None, secret=None):
    payload = b"" if body is None else json.dumps(body).encode()
    headers = [(b"host", b"127.0.0.1:7416")]
    if payload:
        headers += [(b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode())]
    if secret is not None:
        headers.append((b"x-orgtree-bridge", secret.encode("latin1")))
    status, chunks = [0], []

    async def receive():
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(msg):
        if msg["type"] == "http.response.start":
            status[0] = msg["status"]
        elif msg["type"] == "http.response.body":
            chunks.append(msg.get("body", b""))

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "headers": headers, "client": ("127.0.0.1", 5555),
        "server": ("127.0.0.1", 7416),
    }
    asyncio.run(app(scope, receive, send))
    return Res(status[0], b"".join(chunks))


BRIDGE = api.BridgeGateway(api.app)


def chart(org_slug, node, secret):
    return call(BRIDGE, "POST", "/api/agent",
                {"org": org_slug, "node": node, "tool": "orgtree_chart",
                 "args": {}}, secret)


def test_standard_migration():
    with profile("standard"):
        current = store.load_org(slug)
        assert bridgeauth.legacy_credentials_allowed()
        assert sandbox.sandbox_secret(current) == root
        assert sandbox.bridge_credential(current) == root
        assert sandbox.bridge_file_config(current) == {
            "url": sandbox.bridge_url(), "secret": root}
        assert sandbox.shared_container_auth_env(current) == {
            "ANTHROPIC_BASE_URL": f"{sandbox.bridge_url()}/anthropic/{root}",
            "ANTHROPIC_API_KEY": "orgtree-proxied",
        }
        assert sandbox.bridge_exec_env(current) == {}
        assert not os.path.exists(bridgeauth.credential_key_path())
        api._bridge_cache["at"] = 0.0
        # Existing behavior: the org root can name either mutually-trusted
        # node in its own sandbox, but never another org.
        assert chart(slug, alpha, root).status == 200
        assert chart(slug, beta, root).status == 200
        assert chart(other_slug, gamma, root).status == 403


check("standard mode preserves the existing root credential migration path",
      test_standard_migration)


def test_org_tokens_are_distinct_canonical_and_not_root_forgeable():
    with profile("frozen"):
        current = store.load_org(slug)
        one = bridgeauth.org_credential(current)
        two = bridgeauth.org_credential(store.load_org(other_slug))
        assert one.startswith("otb1.") and one != two and root not in one
        assert bridgeauth.org_credential(current) == one
        assert bridgeauth.parse_org_credential(one) == slug
        assert bridgeauth.resolve_org_credential(one) == slug
        assert bridgeauth.resolve_org_credential(two) == other_slug

        key_path = bridgeauth.credential_key_path()
        key_text = open(key_path, encoding="ascii").read().strip()
        assert len(key_text) == 64 and root not in key_text

        # A historically shared root must not forge the new org credential.
        payload = one.split(".")[1]
        material = (bridgeauth._DOMAIN + payload.encode("ascii") + b"\0"
                    + b"0")
        forged = f"otb1.{payload}." + hmac.new(
            root.encode(), material, hashlib.sha256).hexdigest()[:32]
        assert bridgeauth.resolve_org_credential(forged) is None
        for bad in ("", root, "otb1", "otb1.." + "0" * 32,
                    one.upper(), one + "x", one[:-1], "☃",
                    "otb1." + "a" * 5000):
            assert bridgeauth.parse_org_credential(bad) is None, bad[:80]

        att = bridgeauth.credential_attestation(current)
        assert att["scheme"] == "hmac-sha256-org-v1"
        assert att["scope"] == "org" and att["org"] == slug
        assert att["generation"] == 0
        assert att["same_org_nodes_mutually_trusted"] is True
        assert att["legacy_credentials_accepted"] is False
        assert att["previous_generation_rejected"] is None
        assert len(att["fingerprint"]) == len("sha256:") + 64
        secret_free(att, one, root, key_text)


check("frozen credentials are distinct per org and cannot be root-forged",
      test_org_tokens_are_distinct_canonical_and_not_root_forgeable)


def test_malformed_install_key_fails_closed():
    path = bridgeauth.credential_key_path()
    original = open(path, "rb").read()
    current_token = bridgeauth.org_credential(store.load_org(slug))
    try:
        with open(path, "wb") as f:
            f.write(b"truncated")
        try:
            bridgeauth.install_key()
        except bridgeauth.BridgeCredentialError as e:
            assert "malformed" in str(e) and "legacy" in str(e)
        else:
            raise AssertionError("malformed install key was silently replaced")
    finally:
        with open(path, "wb") as f:
            f.write(original)
    assert bridgeauth.org_credential(store.load_org(slug)) == current_token

    os.remove(path)
    try:
        bridgeauth.install_key()
    except bridgeauth.BridgeCredentialError as e:
        assert "disappeared" in str(e) and "silently rotate" in str(e)
    else:
        raise AssertionError("a live key disappearance silently rotated tokens")
    finally:
        with open(path, "wb") as f:
            f.write(original)
    assert bridgeauth.org_credential(store.load_org(slug)) == current_token


check("malformed or disappeared install keys fail closed and restore cleanly",
      test_malformed_install_key_fails_closed)


def test_gateway_enforces_org_scope_and_declares_mutual_trust():
    seen = {}

    async def inner(scope, receive, send):
        seen.update(path=scope["path"], state=dict(scope.get("state") or {}))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    gateway = api.BridgeGateway(inner)
    with profile("frozen"):
        key = bridgeauth.org_credential(store.load_org(slug))
        other_key = bridgeauth.org_credential(store.load_org(other_slug))
        # Same-org cross-node use is supported and named: the shared container
        # makes these nodes mutually trusted at this bearer boundary.
        assert chart(slug, alpha, key).status == 200
        assert chart(slug, beta, key).status == 200
        cross = chart(other_slug, gamma, key)
        assert cross.status == 403 and "own org" in cross.text
        assert chart(slug, alpha, other_key).status == 403
        assert chart(slug, alpha, root).status == 403
        assert call(BRIDGE, "POST",
                    f"/api/orgs/{slug}/nodes/{alpha}/steer", {}, key).status == 200
        assert call(BRIDGE, "POST",
                    f"/api/orgs/{slug}/nodes/{beta}/steer", {}, key).status == 200
        assert call(BRIDGE, "POST",
                    f"/api/orgs/{other_slug}/nodes/{gamma}/steer", {}, key).status == 403

        proxied = call(gateway, "POST", f"/anthropic/{key}/v1/messages", {})
        assert proxied.status == 204
        assert seen["path"] == "/anthropic/v1/messages"
        assert seen["state"]["bridge_slug"] == slug
        assert seen["state"]["bridge_scope"] == "org"
        assert "bridge_node" not in seen["state"]


check("gateway scopes orgs while treating same-org sandbox nodes as trusted",
      test_gateway_enforces_org_scope_and_declares_mutual_trust)


def test_rotation_receipt_and_old_token_control():
    global STALE_TOKEN
    with profile("frozen"):
        before_org = store.load_org(slug)
        old = bridgeauth.org_credential(before_org)
        before = bridgeauth.credential_attestation(before_org)
        receipt = bridgeauth.rotate_org_credential(slug)
        current = store.load_org(slug)
        new = bridgeauth.org_credential(current)
        STALE_TOKEN = old

        assert new != old
        assert receipt["generation"] == before["generation"] + 1
        assert receipt["previous_generation"] == before["generation"]
        assert receipt["previous_fingerprint"] == before["fingerprint"]
        assert receipt["fingerprint"] != before["fingerprint"]
        assert receipt["old_credential_rejected"] is True
        assert receipt["previous_generation_rejected"] is True
        assert receipt["rotated_at"]
        assert receipt["same_org_nodes_mutually_trusted"] is True
        secret_free(receipt, old, new, root,
                    open(bridgeauth.credential_key_path(), encoding="ascii").read())

        assert bridgeauth.resolve_org_credential(old) is None
        assert bridgeauth.resolve_org_credential(new) == slug
        assert chart(slug, alpha, old).status == 403
        assert chart(slug, alpha, new).status == 200
        status = bridgeauth.credential_attestation(current)
        assert status["previous_generation_rejected"] is True
        assert status["fingerprint"] == receipt["fingerprint"]


check("rotation returns only attestation and immediately rejects the old bearer",
      test_rotation_receipt_and_old_token_control)


def test_node_lifecycle_does_not_claim_isolation_or_rotate_org():
    with profile("frozen"):
        current = store.load_org(slug)
        before = bridgeauth.org_credential(current)
        delta = current.hire(USER, None, "haiku", 0, "delta")["node"]
        current.node(delta)["session_id"] = str(uuid.uuid4())
        renamed = current.rename(USER, delta, "delta-renamed")["node"]
        current.retire(USER, renamed)
        current.rehire(USER, renamed)
        store.save_org(current)
        after = bridgeauth.org_credential(store.load_org(slug))
        assert after == before
        assert chart(slug, renamed, after).status == 200


check("node session, rename, retire, and rehire leave the org bearer unchanged",
      test_node_lifecycle_does_not_claim_isolation_or_rotate_org)


def test_operator_rotation_api_is_secret_free_and_admin_only():
    admin = Request({"type": "http", "method": "GET", "path": "/",
                     "headers": [], "query_string": b"", "state": {}})
    public = Request({"type": "http", "method": "GET", "path": "/",
                      "headers": [], "query_string": b"",
                      "state": {"public_slug": slug}})
    with profile("frozen"):
        old = bridgeauth.org_credential(store.load_org(slug))
        status = api.bridge_credential_status(slug, admin)
        assert status["scope"] == "org"
        receipt = asyncio.run(api.bridge_credential_rotate(slug, admin))
        new = bridgeauth.org_credential(store.load_org(slug))
        assert receipt["old_credential_rejected"] is True
        assert receipt["existing_processes_must_refresh"] is True
        assert bridgeauth.resolve_org_credential(old) is None
        secret_free(status, old, root)
        secret_free(receipt, old, new, root)
        try:
            api.bridge_credential_status(slug, public)
        except HTTPException as e:
            assert e.status_code == 403
        else:
            raise AssertionError("public kiosk reached bridge attestation")
        deny = api._public_denied(
            "POST", f"/api/orgs/{slug}/bridge-credential/rotate", slug)
        assert deny and deny[0] == 403

    with profile("standard"):
        try:
            api.bridge_credential_status(slug, admin)
        except HTTPException as e:
            assert e.status_code == 409 and "frozen" in str(e.detail)
        else:
            raise AssertionError("standard mode claimed frozen attestation")


check("operator rotation API is frozen-only, admin-only, and secret-free",
      test_operator_rotation_api_is_secret_free_and_admin_only)


def test_frozen_process_and_provider_key_exposure():
    with profile("frozen"):
        current = store.load_org(slug)
        key = bridgeauth.org_credential(current)
        env = sandbox.bridge_exec_env(current)
        assert env == {
            "ANTHROPIC_BASE_URL": f"{sandbox.bridge_url()}/anthropic/{key}",
            "ANTHROPIC_API_KEY": "orgtree-proxied",
        }
        assert root not in json.dumps(env)
        assert sandbox.bridge_file_config(current) == {"url": sandbox.bridge_url()}
        assert sandbox.shared_container_auth_env(current) == {}
        assert sandbox.auth_label(current) == "org-v1"

        explicit = "sk-ant-frozen-host-only"
        current.d["api_key"] = explicit
        store.save_org(current)
        assert sandbox.shared_container_auth_env(current) == {}
        assert sandbox.bridge_exec_env(current) == env
        assert sandbox.anthropic_proxy_api_key(current) == explicit
        assert sandbox.auth_label(current) == "org-v1"
        assert explicit not in json.dumps(env)

    with profile("standard"):
        current = store.load_org(slug)
        explicit = str(current.d["api_key"])
        assert sandbox.shared_container_auth_env(current) == {
            "ANTHROPIC_API_KEY": explicit}
        assert sandbox.bridge_exec_env(current) == {}
        assert sandbox.anthropic_proxy_api_key(current) == ""
        assert sandbox.anthropic_proxy_api_key(
            current, fallback_active=True) == explicit

    current = store.load_org(slug)
    current.d.pop("api_key", None)
    store.save_org(current)


check("frozen bridge and provider keys stay out of shared persistent state",
      test_frozen_process_and_provider_key_exposure)


def test_host_proxy_attaches_live_explicit_key():
    seen = []

    class FakeResponse:
        status_code = 200
        headers = {}

        async def aiter_raw(self):
            if False:
                yield b""

        async def aclose(self):
            return None

    class FakeUpstream:
        def build_request(self, method, url, *, headers, content):
            seen.append((method, url, dict(headers), content))
            return object()

        async def send(self, _request, *, stream):
            assert stream is True
            return FakeResponse()

    async def invoke():
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": b"{}",
                    "more_body": False}

        request = Request({
            "type": "http", "method": "POST", "scheme": "http",
            "path": "/anthropic/v1/messages", "raw_path": b"/anthropic/v1/messages",
            "query_string": b"", "root_path": "",
            "headers": [(b"x-api-key", b"sandbox-supplied-must-be-stripped"),
                        (b"content-type", b"application/json")],
            "client": ("127.0.0.1", 5555),
            "server": ("127.0.0.1", 7416),
            "state": {"bridge_slug": slug, "bridge_scope": "org"},
        }, receive)
        return await api.anthropic_proxy("v1/messages", request)

    old_upstream = api._upstream
    old_access_token = api.subproxy.get_access_token
    fake = FakeUpstream()
    try:
        api._upstream = lambda: fake
        api.subproxy.get_access_token = lambda: (_ for _ in ()).throw(
            AssertionError("explicit-key relay fell through to host OAuth"))
        with profile("frozen"):
            current = store.load_org(slug)
            current.d["api_key"] = "sk-ant-live-one"
            store.save_org(current)
            asyncio.run(invoke())
            assert seen[-1][2]["x-api-key"] == "sk-ant-live-one"
            assert "Authorization" not in seen[-1][2]
            current = store.load_org(slug)
            current.d["api_key"] = "sk-ant-live-two"
            store.save_org(current)
            asyncio.run(invoke())
            assert seen[-1][2]["x-api-key"] == "sk-ant-live-two"
            assert all("sandbox-supplied" not in str(item) for item in seen)
    finally:
        api._upstream = old_upstream
        api.subproxy.get_access_token = old_access_token
        current = store.load_org(slug)
        current.d.pop("api_key", None)
        store.save_org(current)


check("host proxy strips sandbox auth and attaches the live explicit key",
      test_host_proxy_attaches_live_explicit_key)


def test_bridge_helpers_cannot_bypass_legacy_selector_rejection():
    def rejected(fn):
        try:
            fn()
        except deployment.DeploymentConfigError as e:
            assert "subscription" in str(e).lower() or "copied" in str(e).lower()
        else:
            raise AssertionError("frozen bridge helper accepted legacy credentials")

    current = store.load_org(slug)
    original_kiosk = current.d.get("kiosk")
    original_key = current.d.get("api_key")
    original_env = os.environ.get("ORGTREE_SANDBOX_API_KEY")
    copied = os.path.join(sandbox.sandbox_home(slug), ".claude",
                          ".credentials.json")
    try:
        with profile("frozen"):
            helpers = (
                lambda: sandbox.shared_container_auth_env(current),
                lambda: sandbox.bridge_exec_env(current),
                lambda: sandbox.anthropic_proxy_api_key(current),
            )
            current.d["api_key"] = "subscription"
            for helper in helpers:
                rejected(helper)
            current.d.pop("api_key", None)
            current.d["kiosk"] = {"api_key": " subscription "}
            for helper in helpers:
                rejected(helper)
            current.d.pop("kiosk", None)
            os.environ["ORGTREE_SANDBOX_API_KEY"] = "subscription"
            for helper in helpers:
                rejected(helper)
            os.environ.pop("ORGTREE_SANDBOX_API_KEY", None)
            os.makedirs(os.path.dirname(copied), exist_ok=True)
            with open(copied, "w", encoding="utf-8") as f:
                f.write('{"copied": true}')
            for helper in helpers:
                rejected(helper)
    finally:
        if original_kiosk is None:
            current.d.pop("kiosk", None)
        else:
            current.d["kiosk"] = original_kiosk
        if original_key is None:
            current.d.pop("api_key", None)
        else:
            current.d["api_key"] = original_key
        if original_env is None:
            os.environ.pop("ORGTREE_SANDBOX_API_KEY", None)
        else:
            os.environ["ORGTREE_SANDBOX_API_KEY"] = original_env
        try:
            os.remove(copied)
        except FileNotFoundError:
            pass
        store.save_org(current)


check("bridge helpers reject org, kiosk, default, and copied legacy auth",
      test_bridge_helpers_cannot_bypass_legacy_selector_rejection)


def test_public_disk_cannot_rename_out_org_token():
    fake_disk = tempfile.mkdtemp(prefix="orgtree-bridge-copy-")
    os.makedirs(os.path.join(fake_disk, "workspace"), exist_ok=True)
    old_windows_path = dsk.windows_path
    try:
        with profile("frozen"):
            current = store.load_org(slug)
            current.d["disk"] = {"size_mb": 4096}
            store.save_org(current)
            key = bridgeauth.org_credential(current)
            leak = os.path.join(fake_disk, "workspace", "ordinary-notes.txt")
            with open(leak, "w", encoding="utf-8") as f:
                f.write("copied from process env: " + key)
            dsk.windows_path = lambda _slug: fake_disk
            public = Request({
                "type": "http", "method": "GET", "path": "/disk/file",
                "headers": [], "query_string": b"", "state": {"public_slug": slug},
            })
            try:
                api.disk_file(slug, public, "workspace/ordinary-notes.txt")
            except HTTPException as e:
                assert e.status_code == 403 and "credential" in str(e.detail)
            else:
                raise AssertionError("a renamed org token was served publicly")
    finally:
        dsk.windows_path = old_windows_path
        current = store.load_org(slug)
        current.d.pop("disk", None)
        store.save_org(current)
        shutil.rmtree(fake_disk, ignore_errors=True)


check("renaming a frozen org token cannot bypass the public disk guard",
      test_public_disk_cannot_rename_out_org_token)


def test_hook_and_downgrade_compatibility():
    bridge_file = os.path.join(DATA, ".bridge")
    with profile("frozen"):
        key = bridgeauth.org_credential(store.load_org(slug))
        with open(bridge_file, "w", encoding="utf-8") as f:
            json.dump({"url": "http://host.docker.internal:7416"}, f)
        old_argv = sys.argv
        try:
            sys.argv = [steer.__file__ or "steer.py", slug, alpha, key]
            got = steer.identity()
        finally:
            sys.argv = old_argv
        assert got == (slug, alpha, "http://host.docker.internal:7416", key)
        assert root not in open(bridge_file, encoding="utf-8").read()

    with profile("standard"):
        current = store.load_org(slug)
        current_token = bridgeauth.org_credential(current)
        assert sandbox.bridge_credential(current) == root
        # Downgrade migration accepts both the old standard root and the
        # currently minted frozen token, but never a rotated-out generation.
        assert chart(slug, alpha, root).status == 200
        assert chart(slug, beta, current_token).status == 200
        assert STALE_TOKEN and chart(slug, alpha, STALE_TOKEN).status == 403
        try:
            bridgeauth.credential_attestation(current)
        except deployment.DeploymentConfigError:
            pass
        else:
            raise AssertionError("standard mode emitted frozen attestation")


check("hook uses the org bearer and downgrade accepts only its live generation",
      test_hook_and_downgrade_compatibility)


try:
    store.delete_org(slug)
    store.delete_org(other_slug)
finally:
    shutil.rmtree(DATA, ignore_errors=True)

print(f"ALL {PASS} CHECKS PASS")
