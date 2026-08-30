"""A granted Codex MCP server may execute; an ungranted one may not.

    python backend/tests/test_codex_mcp_approval.py

The end-to-end leg runs the installed Codex app-server against the stdio MCP
server embedded in this file.  It starts a real Codex thread and sends a real
``mcpServer/tool/call`` request, which in turn must reach the fake server's
``tools/call`` handler and return the planted response value.

The negative control asks the same app-server for a server that was never put
in its launch config.  That request must fail and must not create a second
``tools/call`` entry in the fake server's log.  This pairs every absence claim
with a positive call in the same process, so a fake server that never started
cannot make the control pass vacuously.

When no real Codex executable is installed, the config-translation regression
still runs and the live protocol leg reports a skip.  Production machines and
developer machines with Orgtree's pinned Codex run both legs.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback


def _fake_mcp_server() -> None:
    log_path = os.environ["ORGTREE_FAKE_MCP_LOG"]
    token = os.environ["ORGTREE_FAKE_MCP_TOKEN"]

    def record(event: str) -> None:
        with open(log_path, "a", encoding="utf-8") as stream:
            stream.write(event + "\n")

    def send(message: dict) -> None:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    record(f"START {token}")
    for raw in sys.stdin:
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            record("INITIALIZE")
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": message.get("params", {}).get(
                    "protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "orgtree-approval-canary",
                               "version": "1"},
            }})
        elif method == "tools/list":
            record("TOOLS/LIST")
            send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [{
                "name": "approval_canary",
                "description": "return the planted approval canary",
                "inputSchema": {"type": "object", "properties": {
                    "value": {"type": "string"}}, "required": ["value"]},
            }]}})
        elif method == "tools/call":
            value = (message.get("params") or {}).get("arguments", {}).get(
                "value")
            record(f"TOOLS/CALL {value}")
            send({"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text",
                             "text": f"APPROVAL-CANARY:{token}:{value}"}],
                "isError": False,
            }})
        elif request_id is not None:
            send({"jsonrpc": "2.0", "id": request_id, "result": {}})


if "--fake-mcp-server" in sys.argv:
    _fake_mcp_server()
    raise SystemExit(0)


sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree import codexrun, providers  # noqa: E402


PASS = 0
SKIP = 0
FAIL: list[tuple[str, str]] = []


class SkipCheck(RuntimeError):
    pass


def check(label, fn) -> None:
    global PASS, SKIP
    try:
        fn()
    except SkipCheck as exc:
        SKIP += 1
        print(f"  SKIP     {label}: {exc}")
        return
    except Exception:  # noqa: BLE001 - this is a plain-assert suite
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def _server(log_path: str, token: str) -> dict:
    return {
        "type": "stdio",
        "command": sys.executable,
        "args": [os.path.abspath(__file__), "--fake-mcp-server"],
        "env": {
            "ORGTREE_FAKE_MCP_LOG": log_path,
            "ORGTREE_FAKE_MCP_TOKEN": token,
        },
    }


def _thread_id(result: dict) -> str:
    thread = result.get("thread") or {}
    value = thread.get("id") or result.get("threadId")
    if not value:
        raise AssertionError(f"thread/start returned no id: {result!r}")
    return str(value)


def _real_codex_call() -> None:
    exe, _source = providers.codex_path()
    if not exe or not os.path.exists(exe):
        raise SkipCheck(
            "real Codex is not installed; config assertion still ran")

    temp = tempfile.mkdtemp(prefix="orgtree-codex-mcp-approval-")
    log_path = os.path.join(temp, "fake-mcp.log")
    codex_home = os.path.join(temp, "codex-home")
    os.makedirs(codex_home)
    token = "GRANTED-5f3c"
    allowed = "granted-canary"
    ungranted = "ungranted-canary"
    client = codexrun.AppServerClient(
        providers.codex_argv(exe),
        codex_home=codex_home,
        cwd=temp,
        config_overrides=codexrun.mcp_config_overrides({
            allowed: _server(log_path, token),
        }))
    try:
        client.initialize()
        thread_id = _thread_id(client.request("thread/start", {
            "cwd": temp,
            "approvalPolicy": "never",
        }, 60))
        result = client.request("mcpServer/tool/call", {
            "server": allowed,
            "tool": "approval_canary",
            "arguments": {"value": "CALL-REACHED-8a2d"},
            "threadId": thread_id,
        }, 60)
        text = "\n".join(
            str(item.get("text", "")) for item in result.get("content", [])
            if isinstance(item, dict))
        expected = "APPROVAL-CANARY:GRANTED-5f3c:CALL-REACHED-8a2d"
        if expected not in text:
            raise AssertionError(
                f"granted tools/call did not return canary: {result!r}")

        try:
            denied = client.request("mcpServer/tool/call", {
                "server": ungranted,
                "tool": "approval_canary",
                "arguments": {"value": "MUST-NOT-ARRIVE"},
                "threadId": thread_id,
            }, 20)
        except codexrun.CodexServerError:
            denied = None
        if denied is not None and not denied.get("isError"):
            raise AssertionError(
                f"ungranted MCP server call unexpectedly succeeded: {denied!r}")

        with open(log_path, encoding="utf-8") as stream:
            events = stream.read()
        if events.count("TOOLS/CALL") != 1:
            raise AssertionError(f"wanted exactly one real tools/call: {events!r}")
        if "TOOLS/CALL CALL-REACHED-8a2d" not in events:
            raise AssertionError(f"positive call never reached MCP: {events!r}")
        if "MUST-NOT-ARRIVE" in events:
            raise AssertionError(f"ungranted call reached MCP: {events!r}")
    finally:
        client.close()


def main() -> int:
    print("A1 granted/deliverable config gets automatic MCP approval")

    def config_translation() -> None:
        server = _server("C:/tmp/unused.log", "unused")
        out = codexrun.mcp_config_overrides({
            "granted-canary": server,
            "second-granted": server,
        })
        approvals = {item for item in out
                     if "default_tools_approval_mode" in item}
        expected = {
            ('mcp_servers.granted-canary.'
             'default_tools_approval_mode="approve"'),
            ('mcp_servers.second-granted.'
             'default_tools_approval_mode="approve"'),
        }
        if approvals != expected:
            raise AssertionError(
                f"approval overrides: got {approvals!r}, wanted {expected!r}")
        if any("ungranted-canary" in item for item in out):
            raise AssertionError(f"ungranted server leaked into config: {out!r}")

    check("only the supplied server gets default approval=approve",
          config_translation)

    print("\nA2 real Codex app-server reaches tools/call, but not ungranted server")
    check("real tools/call returns the planted value and denial control holds",
          _real_codex_call)

    print(f"\n{PASS} checks passed, {SKIP} skipped, {len(FAIL)} failed")
    for label, tb in FAIL:
        print(f"\n--- {label} ---\n{tb}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
