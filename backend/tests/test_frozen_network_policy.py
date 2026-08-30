"""Frozen host-listener and Anthropic proxy policy checks.

Run:
    python backend/tests/test_frozen_network_policy.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uvicorn

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi import HTTPException  # noqa: E402
from starlette.requests import Request  # noqa: E402

from orgtree import api, deployment, sandbox, store, subproxy  # noqa: E402
from orgtree.ledger import LedgerError  # noqa: E402


PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def profile(name):
    if name is None:
        os.environ.pop(deployment.PROFILE_ENV, None)
    else:
        os.environ[deployment.PROFILE_ENV] = name


try:
    def standard_proxy_is_unchanged():
        profile(None)
        for method in ("GET", "POST", "HEAD", "PUT", "DELETE"):
            for path in ("v1/messages", "v1/models", "anything/else"):
                assert api._anthropic_operation_allowed(method, path), \
                    (method, path)
        assert sandbox.bridge_bind_host() == "0.0.0.0"

    check("standard keeps the broad proxy and 0.0.0.0 bridge behavior",
          standard_proxy_is_unchanged)

    def frozen_proxy_is_one_exact_operation():
        profile("frozen")
        assert api._anthropic_operation_allowed("POST", "v1/messages")
        for method, path in (
                ("GET", "v1/messages"), ("HEAD", "v1/messages"),
                ("PUT", "v1/messages"), ("DELETE", "v1/messages"),
                ("POST", "/v1/messages"), ("POST", "v1/messages/"),
                ("POST", "v1/models"), ("POST", "api/oauth/usage")):
            assert not api._anthropic_operation_allowed(method, path), \
                (method, path)

    check("frozen admits only POST v1/messages",
          frozen_proxy_is_one_exact_operation)

    def frozen_bridge_bind_is_not_lan_wide():
        profile("frozen")
        real = sandbox._docker
        if sys.platform not in ("win32", "darwin"):
            sandbox._docker = lambda *a, **kw: subprocess.CompletedProcess(
                a, 0, "172.17.0.1\n", "")
        try:
            host = sandbox.bridge_bind_host()
        finally:
            sandbox._docker = real
        assert host in ("127.0.0.1", "172.17.0.1"), host
        assert host != "0.0.0.0", host

    check("frozen bridge binds loopback/Docker-host only, never every interface",
          frozen_bridge_bind_is_not_lan_wide)

    def supported_main_uses_the_host_only_bridge_bind():
        profile("frozen")
        configs = []

        class Config:
            def __init__(self, app, *, host, port):
                self.app, self.host, self.port = app, host, port
                configs.append(self)

        class Server:
            def __init__(self, config):
                self.config = config

            async def serve(self):
                return None

        saved = {
            "claim": store.claim_data_root,
            "preflight": api._deployment_preflight,
            "ws": api._ws_impl,
            "diag": api.supervisor.cli_diagnosis,
            "bridge": api.BridgeGateway,
            "bind": sandbox.bridge_bind_host,
            "config": uvicorn.Config,
            "server": uvicorn.Server,
            "public": api.PUBLIC_PORT,
            "bridge_port": sandbox.BRIDGE_PORT,
        }
        try:
            store.claim_data_root = lambda: None
            api._deployment_preflight = lambda: deployment.FROZEN
            api._ws_impl = lambda: "websockets"
            api.supervisor.cli_diagnosis = lambda: "fixture skips CLI probe"
            api.BridgeGateway = lambda app: ("bridge", app)
            sandbox.bridge_bind_host = lambda: "127.0.0.1"
            uvicorn.Config, uvicorn.Server = Config, Server
            api.PUBLIC_PORT = 0
            sandbox.BRIDGE_PORT = 7362
            api.main()
        finally:
            store.claim_data_root = saved["claim"]
            api._deployment_preflight = saved["preflight"]
            api._ws_impl = saved["ws"]
            api.supervisor.cli_diagnosis = saved["diag"]
            api.BridgeGateway = saved["bridge"]
            sandbox.bridge_bind_host = saved["bind"]
            uvicorn.Config, uvicorn.Server = saved["config"], saved["server"]
            api.PUBLIC_PORT = saved["public"]
            sandbox.BRIDGE_PORT = saved["bridge_port"]
        assert [(c.host, c.port) for c in configs] == [
            ("127.0.0.1", api.PORT), ("127.0.0.1", 7362)], configs

    check("supported `python -m orgtree.api` wires the host-only bridge bind",
          supported_main_uses_the_host_only_bridge_bind)

    def denied_proxy_never_reads_credentials_or_opens_upstream():
        profile("frozen")
        calls = []
        real_token, real_upstream = subproxy.get_access_token, api._upstream
        subproxy.get_access_token = lambda: calls.append("token") or "secret"
        api._upstream = lambda: calls.append("upstream") or None

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        try:
            for method, path in (("GET", "v1/messages"),
                                 ("POST", "v1/models"),
                                 ("DELETE", "anything")):
                req = Request({"type": "http", "method": method,
                               "path": "/anthropic/" + path,
                               "query_string": b"", "headers": [],
                               "state": {"bridge_slug": "example"}}, receive)
                try:
                    asyncio.run(api.anthropic_proxy(path, req))
                    raise AssertionError((method, path, "was relayed"))
                except HTTPException as e:
                    assert e.status_code == 403, e
            assert calls == [], calls
        finally:
            subproxy.get_access_token = real_token
            api._upstream = real_upstream

    check("a denied operation is rejected before host credentials/upstream",
          denied_proxy_never_reads_credentials_or_opens_upstream)

    def exact_operation_reaches_credential_step():
        profile("frozen")
        calls = []
        real_token, real_load = subproxy.get_access_token, store.load_org
        store.load_org = lambda slug: (_ for _ in ()).throw(
            LedgerError("fixture has no org"))
        subproxy.get_access_token = lambda: calls.append("token") or (
            (_ for _ in ()).throw(RuntimeError("fixture-token-stop")))

        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        req = Request({"type": "http", "method": "POST",
                       "path": "/anthropic/v1/messages",
                       "query_string": b"", "headers": [],
                       "state": {"bridge_slug": "example"}}, receive)
        try:
            try:
                asyncio.run(api.anthropic_proxy("v1/messages", req))
                raise AssertionError("exact operation did not reach token step")
            except HTTPException as e:
                assert e.status_code == 502 and "fixture-token-stop" in str(e.detail)
            assert calls == ["token"], calls
        finally:
            subproxy.get_access_token = real_token
            store.load_org = real_load

    check("the exact messages operation still reaches proxied authentication",
          exact_operation_reaches_credential_step)
finally:
    profile(None)

print(f"ALL {PASS} CHECKS PASS")
