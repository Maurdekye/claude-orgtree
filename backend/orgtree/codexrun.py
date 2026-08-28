# pyright: strict
"""The codex turn runner: one `codex app-server` JSON-RPC session per turn.

FR-15 Phase 1 (design-multi-provider.md §3.2 + Appendix B/C, all measured on
codex-cli 0.150.1 against a live account). The shape deliberately mirrors the
Claude lane: ONE PROCESS PER TURN, resumed by a durable session id — here the
app-server `threadId`, harvested from `thread/start` and passed back through
`thread/resume` on the next turn. Steering and interrupting act on the LIVE
client object the supervisor holds while the turn runs:

    turn/steer      — append input to the in-flight turn (expectedTurnId
                      guard; measured landing inside the same turn, C.2)
    turn/interrupt  — graceful stop; the turn completes with
                      status "interrupted" (C.3)

Org powers attach as **dynamicTools** (measured round-trip, M6 probe): the
thread is started with the orgtree tool cards as client-defined function
tools; the model's calls arrive as `item/tool/call` server-requests and the
supervisor answers them in-process — no bridge process, no writes to the
user's codex config, and per-agent identity is simply which dispatcher
answers. Approval callbacks (`item/commandExecution/requestApproval`,
`item/fileChange/requestApproval`, …) arrive the same way and are decided by
the caller-supplied policy hook — that is the ⚙-rights seam.

⚠ CREDENTIALS: this module never reads, copies or moves auth material. The
CLI process inherits CODEX_HOME (default: the machine's own ~/.codex, the
signed-in primary) and maintains its own auth.json in place. Copying that
file anywhere would split-brain the refresh cycle (design §3.4); nothing
here, and nothing built on top of this, may do it.

Hermetic by construction: everything is parameterized (exe, home, cwd,
hooks), so tests drive it against an impostor script instead of the real
CLI — see backend/tests/test_codexrun.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any, Final

#: how long request() waits before declaring the server unresponsive. Turns
#: themselves are unbounded (the caller owns the turn timeout); this bounds
#: single request/response exchanges like initialize or thread/start.
REQUEST_TIMEOUT: Final = 120.0

#: normalized turn statuses (M2 vocabulary): the supervisor's policy layer
#: consumes these, never codex's raw strings. "interrupted" is a completed
#: turn (C.3) — codex reports it inside turn/completed, not as a failure.
STATUS_COMPLETED: Final = "completed"
STATUS_INTERRUPTED: Final = "interrupted"
STATUS_FAILED: Final = "failed"


class CodexServerError(RuntimeError):
    """The app-server refused or never answered a protocol request."""


def _dyn_tool(name: str, description: str,
              input_schema: dict[str, Any]) -> dict[str, Any]:
    """One orgtree tool card as a DynamicToolSpec function entry."""
    return {"type": "function", "name": name, "description": description,
            "inputSchema": input_schema}


class AppServerClient:
    """One `codex app-server` child process spoken to over stdio NDJSON.

    Threading model: a reader thread pumps stdout; server->client REQUESTS
    (tool calls, approvals) are answered synchronously on that thread via the
    caller's hooks, so a slow tool handler backpressures the model exactly
    like a slow MCP server would. Notifications append to an internal list
    AND stream to `on_event` as they arrive.
    """

    def __init__(self, argv_head: list[str], *, codex_home: str | None = None,
                 cwd: str | None = None,
                 on_event: Callable[[dict[str, Any]], None] | None = None,
                 tool_dispatch: Callable[[str, dict[str, Any]], str] | None = None,
                 approval_decide: Callable[[str, dict[str, Any]], str] | None = None,
                 env_extra: dict[str, str] | None = None) -> None:
        # an ARGV HEAD, not a bare exe — the same shape as supervisor's
        # _claude_argv(): production passes [codex.exe], tests pass
        # [python, fakecodex.py], and nobody ever routes through a .CMD shim
        # (the argv-truncation hazard the claude resolver documents).
        env = dict(os.environ)
        # the claude lane's hygiene, mirrored: a codex child must never see
        # Anthropic credentials (one-credential-per-spawn, supervisor
        # spawn_env) — and equally never a stray OPENAI_API_KEY that would
        # silently flip the billing lane away from the subscription login.
        for k in list(env):
            if k.startswith(("ANTHROPIC_", "CLAUDE_CODE_")) or k in (
                    "CLAUDECODE", "OPENAI_API_KEY"):
                env.pop(k, None)
        if codex_home:
            env["CODEX_HOME"] = codex_home
        if env_extra:
            env.update(env_extra)
        # cwd is the agent's own scratch, same as the claude lane's Popen —
        # the process-level cwd, not just thread/start's `cwd` param, because
        # AGENTS.md discovery and any relative path the model touches resolve
        # against the PROCESS
        self.proc = subprocess.Popen(
            argv_head + ["app-server"], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd)
        self.on_event = on_event
        self.tool_dispatch = tool_dispatch
        self.approval_decide = approval_decide
        self._lock = threading.Lock()
        self._next_id = 1
        self._responses: dict[int, dict[str, Any]] = {}
        self.notifications: list[dict[str, Any]] = []
        self.stderr_tail: list[str] = []
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()
        threading.Thread(target=self._pump_err, daemon=True).start()

    # ── wire plumbing ────────────────────────────────────────────────────

    def _pump_err(self) -> None:
        err = self.proc.stderr
        assert err is not None
        for raw in err:
            line = raw.decode(errors="replace").rstrip()
            self.stderr_tail.append(line)
            del self.stderr_tail[:-50]

    def _pump(self) -> None:
        out = self.proc.stdout
        assert out is not None
        for raw in out:
            try:
                msg: dict[str, Any] = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                with self._lock:
                    self._responses[int(msg["id"])] = msg
            elif "id" in msg and "method" in msg:
                self._answer_server_request(msg)
            else:
                self.notifications.append(msg)
                if self.on_event:
                    try:
                        self.on_event(msg)
                    except Exception:
                        pass   # an observer must never kill the wire reader

    def _answer_server_request(self, msg: dict[str, Any]) -> None:
        method = str(msg.get("method", ""))
        params: dict[str, Any] = msg.get("params") or {}
        rid = msg["id"]
        if method == "item/tool/call" and self.tool_dispatch is not None:
            tool = str(params.get("tool", ""))
            args = params.get("arguments")
            try:
                text = self.tool_dispatch(
                    tool, args if isinstance(args, dict) else {})
                ok = True
            except Exception as e:   # a tool error is an ANSWER, not a hang
                text, ok = f"tool {tool} failed: {e}", False
            self._send({"jsonrpc": "2.0", "id": rid, "result": {
                "success": ok,
                "contentItems": [{"type": "inputText", "text": text}]}})
            return
        if "requestApproval" in method:
            decision = "decline"
            if self.approval_decide is not None:
                try:
                    decision = self.approval_decide(method, params)
                except Exception:
                    decision = "decline"   # fail CLOSED, loudly in the turn
            self._send({"jsonrpc": "2.0", "id": rid,
                        "result": {"decision": decision}})
            return
        # anything unexpected: refuse loudly rather than hang the server.
        self._send({"jsonrpc": "2.0", "id": rid, "error": {
            "code": -32601, "message": f"orgtree declines: {method}"}})

    def _send(self, obj: dict[str, Any]) -> None:
        stdin = self.proc.stdin
        assert stdin is not None
        with self._lock:
            stdin.write((json.dumps(obj) + "\n").encode())
            stdin.flush()

    # ── protocol surface ─────────────────────────────────────────────────

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def request(self, method: str, params: dict[str, Any],
                timeout: float = REQUEST_TIMEOUT) -> dict[str, Any]:
        with self._lock:
            rid = self._next_id
            self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": rid,
                    "method": method, "params": params})
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if rid in self._responses:
                    resp = self._responses.pop(rid)
                    if "error" in resp and resp["error"]:
                        raise CodexServerError(
                            f"{method}: {json.dumps(resp['error'])[:400]}")
                    result: dict[str, Any] = resp.get("result") or {}
                    return result
            if self.proc.poll() is not None:
                raise CodexServerError(
                    f"{method}: app-server exited rc={self.proc.returncode}; "
                    f"stderr tail: {' | '.join(self.stderr_tail[-3:])[:400]}")
            time.sleep(0.02)
        raise CodexServerError(f"{method}: no answer in {timeout:.0f}s")

    def initialize(self) -> dict[str, Any]:
        r = self.request("initialize", {
            "clientInfo": {"name": "orgtree", "title": "orgtree supervisor",
                           "version": "1"},
            "capabilities": {"experimentalApi": True}}, 60)
        self.notify("initialized", {})
        return r

    def close(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass


def _thread_id_of(result: dict[str, Any]) -> str | None:
    thread = result.get("thread")
    if isinstance(thread, dict) and thread.get("id"):
        return str(thread["id"])
    for k in ("threadId", "id"):
        if result.get(k):
            return str(result[k])
    return None


class CodexTurn:
    """One turn's lifecycle, from spawn to normalized result.

    The supervisor holds this object while the turn runs — steer() and
    interrupt() act on the live session, which is exactly the shape the
    Claude lane's mid-turn machinery (steer hook / control_request) expects
    to find behind the provider seam.
    """

    def __init__(self, argv_head: list[str], *, cwd: str, model: str | None,
                 effort: str | None, thread_id: str | None,
                 codex_home: str | None = None,
                 sandbox: str = "workspace-write",
                 approval_policy: str = "on-request",
                 dynamic_tools: list[dict[str, Any]] | None = None,
                 developer_instructions: str | None = None,
                 on_event: Callable[[dict[str, Any]], None] | None = None,
                 tool_dispatch: Callable[[str, dict[str, Any]], str] | None = None,
                 approval_decide: Callable[[str, dict[str, Any]], str] | None = None,
                 env_extra: dict[str, str] | None = None) -> None:
        self.client = AppServerClient(
            argv_head, codex_home=codex_home, cwd=cwd, on_event=self._observe,
            tool_dispatch=tool_dispatch, approval_decide=approval_decide,
            env_extra=env_extra)
        self._caller_on_event = on_event
        self.cwd = cwd
        self.model = model
        self.effort = effort
        self.sandbox = sandbox
        self.approval_policy = approval_policy
        self.dynamic_tools = dynamic_tools or []
        self.developer_instructions = developer_instructions
        self.thread_id = thread_id
        self.turn_id: str | None = None
        self.agent_text: list[str] = []
        self.token_usage: dict[str, Any] | None = None
        self.rate_limits: dict[str, Any] | None = None
        self.status: str | None = None
        self._done = threading.Event()

    # ── event fold (M2: raw notifications → normalized fields) ───────────

    def _observe(self, msg: dict[str, Any]) -> None:
        method = str(msg.get("method", ""))
        params: dict[str, Any] = msg.get("params") or {}
        if method == "item/agentMessage/delta":
            d = params.get("delta")
            if isinstance(d, str):
                self.agent_text.append(d)
        elif method == "thread/tokenUsage/updated":
            tu = params.get("tokenUsage")
            if isinstance(tu, dict):
                self.token_usage = tu
        elif method == "account/rateLimits/updated":
            rl = params.get("rateLimits")
            if isinstance(rl, dict):
                self.rate_limits = rl
        elif method == "turn/completed":
            turn = params.get("turn")
            if isinstance(turn, dict):
                raw = str(turn.get("status") or "")
                self.status = (STATUS_INTERRUPTED if raw == "interrupted"
                               else STATUS_COMPLETED)
            else:
                self.status = STATUS_COMPLETED
            self._done.set()
        elif method == "turn/failed":
            self.status = STATUS_FAILED
            self._done.set()
        if self._caller_on_event:
            self._caller_on_event(msg)

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self, input_text: str) -> str:
        """Initialize, open/resume the thread, start the turn. Returns the
        durable thread id (the provider session id the node records)."""
        self.client.initialize()
        if self.thread_id:
            # dynamicTools + developerInstructions ride the RESUME too —
            # measured (probe_resume_dyntools.py, 2026-08-29): the server
            # accepts both and calls the tool in the resumed turn. Without
            # them every turn after an agent's first had no org powers and a
            # stale identity, which fakecodex (mirroring the wire) caught.
            res = self.client.request("thread/resume", {
                "threadId": self.thread_id,
                "developerInstructions": self.developer_instructions,
                "dynamicTools": self.dynamic_tools or None})
            resumed = _thread_id_of(res)
            if resumed:
                self.thread_id = resumed
        else:
            res = self.client.request("thread/start", {
                "model": self.model, "cwd": self.cwd,
                "sandbox": self.sandbox,
                "approvalPolicy": self.approval_policy,
                "developerInstructions": self.developer_instructions,
                "dynamicTools": self.dynamic_tools or None})
            tid = _thread_id_of(res)
            if not tid:
                raise CodexServerError(
                    f"thread/start returned no thread id: "
                    f"{json.dumps(res)[:300]}")
            self.thread_id = tid
        turn = self.client.request("turn/start", {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": input_text}],
            "model": self.model, "effort": self.effort,
            "cwd": self.cwd, "summary": "none"})
        t = turn.get("turn")
        self.turn_id = (str(t["id"]) if isinstance(t, dict) and t.get("id")
                        else str(turn.get("turnId") or "") or None)
        assert self.thread_id is not None
        return self.thread_id

    def steer(self, text: str) -> bool:
        """Mid-turn input (C.2). False = the guard refused (turn already
        over) — the caller falls back to queueing for the next turn."""
        if not (self.thread_id and self.turn_id):
            return False
        try:
            self.client.request("turn/steer", {
                "threadId": self.thread_id,
                "expectedTurnId": self.turn_id,
                "input": [{"type": "text", "text": text}]}, 30)
            return True
        except CodexServerError:
            return False

    def interrupt(self) -> bool:
        if not (self.thread_id and self.turn_id):
            return False
        try:
            self.client.request("turn/interrupt", {
                "threadId": self.thread_id, "turnId": self.turn_id}, 30)
            return True
        except CodexServerError:
            return False

    def wait(self, timeout: float | None = None) -> dict[str, Any]:
        """Block until the turn ends; kill the process; return the
        normalized result the policy layer consumes."""
        finished = self._done.wait(timeout) if timeout else self._done.wait()
        if not finished and self.status is None:
            self.status = STATUS_FAILED
        self.client.close()
        return {
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "status": self.status or STATUS_FAILED,
            "agent_text": "".join(self.agent_text),
            "token_usage": self.token_usage,
            "rate_limits": self.rate_limits,
        }
