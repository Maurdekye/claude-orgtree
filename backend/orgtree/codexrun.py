# pyright: strict
"""The Codex turn runner over a reusable `codex app-server` JSON-RPC client.

FR-15 Phase 1 (design-multi-provider.md §3.2 + Appendix B/C, all measured on
codex-cli 0.150.1 against a live account). The shape deliberately mirrors the
Claude lane: one turn object at a time, resumed by a durable session id — here
the app-server `threadId`, harvested from `thread/start` and passed back
through `thread/resume` on the next turn. The process itself may come from the
warm pool and survive that boundary. Steering and interrupting act on the
LIVE client object the supervisor holds while the turn runs:

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
import re
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any, Final, cast

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

#: codex's own `TurnStatus` → ours (D-209). ⚠ THERE IS NO `turn/failed`
#: NOTIFICATION IN codex-cli 0.150.1 — the literal string does not occur
#: anywhere in the binary, and the notification set interned there is
#: turn/started, turn/completed, turn/diff/updated, turn/plan/updated. A FAILED
#: turn arrives as `turn/completed` carrying `turn.status = "failed"` and a
#: `turn.error`, and codex's TurnStatus enum is exactly
#: completed | interrupted | failed | inProgress.
#:
#: ⚠ THIS TABLE IS THE DEFECT'S WHOLE STORY. What stood here was
#: `INTERRUPTED if raw == "interrupted" else COMPLETED`, so "failed" WAS
#: "completed": a Codex agent that hit its usage limit had the failure booked
#: as a successful turn — normal tokens, normal cost, no error row, no freeze
#: — and simply went quiet. Measured on cache-structural, 2026-08-30T22:41:41Z,
#: silent for 9h47m until a person noticed.
_TURN_STATUS: Final = {
    "completed": STATUS_COMPLETED,
    "interrupted": STATUS_INTERRUPTED,
    "failed": STATUS_FAILED,
}


def _status_of(raw: str) -> str:
    """Normalize one of codex's turn statuses.

    ⚠ AN UNKNOWN STATUS IS A FAILURE, NOT A SUCCESS, and the asymmetry is
    deliberate: `compact_fork` in this same module already refuses anything
    outside (completed, interrupted), and the cost of the two mistakes is not
    symmetric. Calling a healthy new status a failure costs one visible error
    row the operator can read and complain about; calling a failure a success
    is what made an agent disappear for ten hours with nobody able to tell it
    from an idle one."""
    return _TURN_STATUS.get(raw.strip(), STATUS_FAILED) if raw else (
        STATUS_COMPLETED)


def error_text(error: Any) -> str:
    """A `TurnError` flattened into the one string the classifiers read.

    The wire shape is `{message, codexErrorInfo, additionalDetails}` (measured:
    `struct TurnError with 3 elements` in the 0.150.1 binary), and
    `codexErrorInfo` is a short machine tag — `"usage_limit_exceeded"` in the
    specimen. Both halves are kept: the MESSAGE is what
    `supervisor._looks_like_usage_limit` matches on and what a person reads,
    the TAG is what survives a wording change upstream.

    Never raises and never returns None — a caller building a failure blob has
    nothing else to fall back on but the stderr tail, which for a limit is
    empty (the app-server says all of this on the wire, not on stderr)."""
    if not isinstance(error, dict):
        return ""
    err: dict[str, Any] = error
    msg = str(err.get("message") or "").strip()
    extra = str(err.get("additionalDetails") or "").strip()
    info: Any = err.get("codexErrorInfo")
    if isinstance(info, dict):
        # the tagged-object form, should the protocol ever send one
        code = str(info.get("type") or info.get("kind") or "").strip()
    else:
        code = str(info or "").strip()
    out = " — ".join(p for p in (msg, extra) if p)
    if code and code.lower() not in out.lower():
        out = f"{out} [{code}]" if out else code
    return out[:600]


def _window_reset(window: Any) -> float | None:
    """`resetsAt` of a window that is actually EXHAUSTED, or None.

    Only a window at 100% describes the wall the turn just hit. A window with
    room left has a reset time too, and taking it would park the agent on a
    deadline belonging to a limit it never reached."""
    if not isinstance(window, dict):
        return None
    win: dict[str, Any] = window
    try:
        if float(win.get("usedPercent") or 0) < 100.0:
            return None
        return float(win.get("resetsAt") or 0) or None
    except (TypeError, ValueError):
        return None


def limit_reset_epoch(snapshots: Any) -> float | None:
    """The soonest reset among the EXHAUSTED windows of every rate-limit
    snapshot the turn saw → epoch seconds, or None (D-209).

    This is the number the prose usually cannot give us. The specimen's message
    said "try again at Sep 6th, 2026 10:33 AM", which no reset parser in this
    codebase can read; the notification 298 ms earlier carried
    `resets_at: 1788680032`, which is that instant exactly.

    ⚠ TAKES THE WHOLE BOARD, keyed by limitId — see `CodexTurn.rate_limit_
    snapshots`. The notifications are sparse and arrive per bucket: in the
    specimen the exhausted `codex` bucket came first and a `premium` bucket
    with `primary: null` came 286 ms later, so a single last-wins field held
    the useless one at exactly the moment the useful one was needed.

    Unbanded on purpose — the caller bands it against the same horizon it bands
    a prose timestamp with, so one rule governs both."""
    if not isinstance(snapshots, dict):
        return None
    best: float | None = None
    for snap in list(cast("dict[str, Any]", snapshots).values()):
        if not isinstance(snap, dict):
            continue
        for slot in ("primary", "secondary"):
            ts = _window_reset(cast("dict[str, Any]", snap).get(slot))
            if ts is not None and (best is None or ts < best):
                best = ts
    return best


class CodexServerError(RuntimeError):
    """The app-server refused or never answered a protocol request."""


def _dyn_tool(name: str, description: str,
              input_schema: dict[str, Any]) -> dict[str, Any]:
    """One orgtree tool card as a DynamicToolSpec function entry."""
    return {"type": "function", "name": name, "description": description,
            "inputSchema": input_schema}


#: the registry fields we translate into codex `[mcp_servers.*]`. Claude's
#: `type` discriminator is deliberately NOT among them: codex infers transport
#: from command-vs-url, and `--strict-config` rejects keys it does not know.
_MCP_FIELDS: Final = ("command", "args", "env", "url")

#: a server name must be a TOML BARE KEY. `-c` splits its dotted path BEFORE
#: honouring quotes, so `mcp_servers."dot.name".command=…` does not merely fail
#: to attach — it aborts the whole app-server with "failed to load bootstrap
#: configuration / invalid transport" (measured, codex 0.150.1). A name we
#: cannot express is therefore undeliverable, and `deliverable_mcp` reports it
#: so the identity prompt can stop promising it.
_BARE_KEY: Final = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")


def _toml(value: Any) -> str:
    """One TOML scalar for `-c`. JSON string escaping is a subset of TOML's
    basic-string escaping, so json.dumps is a correct encoder here — and it is
    the one that survives the BACKSLASHES in a registered server's Windows
    command path, which naive quoting corrupts silently."""
    return json.dumps(str(value))


def deliverable_mcp(servers: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Split a registry subset into (what this lane can attach, what it cannot).

    The codex lane cannot carry every shape the claude lane can, and BOTH the
    spawn and the identity prompt are built from this one split — promising a
    capability the config drops is the bug class this function exists to close.
    """
    ok: dict[str, Any] = {}
    dropped: list[str] = []
    for name in sorted(servers):
        srv = servers[name]
        if (isinstance(srv, dict) and _BARE_KEY.match(name)
                and (srv.get("command") or srv.get("url"))):
            ok[name] = srv
        else:
            dropped.append(name)
    return ok, dropped


def mcp_config_overrides(servers: dict[str, Any]) -> list[str]:
    """`-c key=value` argv attaching `servers` as codex `[mcp_servers.*]`.

    LAUNCH-scoped by design. Measured 2026-08-29 (probe_resume.py): codex
    starts configured MCP servers when the APP-SERVER starts, not when a thread
    is created — the marker appeared in a run where no thread existed at all.
    So the set is process-scoped and no thread operation (start, resume, fork)
    can drop it, which is the failure `thread/resume` already caused once for
    dynamicTools. It also writes NOTHING to the user's config.toml and never
    repoints CODEX_HOME (that would split-brain the auth refresh cycle, §3.4).
    """
    out: list[str] = []
    for name, srv in sorted(servers.items()):
        for field in _MCP_FIELDS:
            if field not in srv:
                continue
            val = srv[field]
            if field == "args":
                if not isinstance(val, list):
                    continue
                rendered = "[" + ", ".join(_toml(v) for v in val) + "]"
            elif field == "env":
                if not isinstance(val, dict):
                    continue
                rendered = "{" + ", ".join(
                    f"{k} = {_toml(v)}" for k, v in sorted(val.items())
                    if _BARE_KEY.match(str(k))) + "}"
            else:
                rendered = _toml(val)
            out += ["-c", f"mcp_servers.{name}.{field}={rendered}"]
        # `servers` is already the node's granted + deliverable subset (the
        # caller gets it from codex_mcp_grant).  Codex otherwise defaults MCP
        # tools to prompting, but Orgtree runs headless and answers an MCP
        # prompt as a rejection.  Approve only this attached server's tools;
        # never change the thread's broader approval or sandbox policy.
        out += ["-c", (f"mcp_servers.{name}."
                       'default_tools_approval_mode="approve"')]
    return out


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
                 env_extra: dict[str, str] | None = None,
                 config_overrides: list[str] | None = None) -> None:
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
        # `-c` overrides are GLOBAL options and must precede the subcommand —
        # codex parses them off the top-level command line, not off app-server.
        self.proc = subprocess.Popen(
            argv_head + list(config_overrides or []) + ["app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd,
            creationflags=(subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
                           if os.name == "nt" else 0))
        self.on_event = on_event
        self.on_exit: Callable[[], None] | None = None
        self.tool_dispatch = tool_dispatch
        self.approval_decide = approval_decide
        # A pre-warmed app-server is initialized by its first claimant and
        # then reused.  JSON-RPC initialize is process-scoped, not turn-
        # scoped, so issuing it again on every claim is both unnecessary and
        # rejected by stricter server versions.
        self._initialized = False
        self._initialize_result: dict[str, Any] = {}
        self._initialize_lock = threading.Lock()
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
        try:
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
                            pass   # observer must never kill the wire reader
        finally:
            if self.on_exit:
                try:
                    self.on_exit()
                except Exception:
                    pass

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

    def initialize(self, timeout: float = 60.0) -> dict[str, Any]:
        with self._initialize_lock:
            if self._initialized:
                return dict(self._initialize_result)
            r = self.request("initialize", {
                "clientInfo": {"name": "orgtree",
                               "title": "orgtree supervisor",
                               "version": "1"},
                "capabilities": {"experimentalApi": True}}, timeout)
            self.notify("initialized", {})
            self._initialize_result = dict(r)
            self._initialized = True
            return dict(r)

    def mcp_tool_names(self) -> list[str]:
        """Exact MCP function names currently callable in this app-server.

        ``mcpServerStatus/list`` is runtime evidence: unlike launch config it
        reports each connected server's resolved ``tools`` map.  Pagination is
        followed deterministically and built-in/dynamic tools never enter the
        result.
        """
        names: set[str] = set()
        cursor: str | None = None
        seen: set[str] = set()
        while True:
            params = {"cursor": cursor} if cursor else {}
            result = self.request("mcpServerStatus/list", params)
            rows = result.get("data") or []
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    server = str(row.get("name") or "")
                    tools = row.get("tools")
                    if not server or not isinstance(tools, dict):
                        continue
                    for tool in tools:
                        if isinstance(tool, str) and tool:
                            names.add(f"mcp__{server}__{tool}")
            nxt = result.get("nextCursor")
            cursor = str(nxt) if nxt else None
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
        return sorted(names)

    def bind(self, *,
             on_event: Callable[[dict[str, Any]], None] | None = None,
             tool_dispatch: Callable[[str, dict[str, Any]], str] | None = None,
             approval_decide: Callable[[str, dict[str, Any]], str] | None = None,
             ) -> None:
        """Attach one turn's callbacks to a parked app-server client."""
        self.on_event = on_event
        self.tool_dispatch = tool_dispatch
        self.approval_decide = approval_decide

    def unbind(self) -> None:
        """Drop references to the completed turn while the process parks."""
        self.on_event = None
        self.tool_dispatch = None
        self.approval_decide = None

    def close(self) -> None:
        """Tear the app-server down — the WHOLE process tree, and wait for it.

        ⚠ `codex app-server` (the `node …/codex.js` entry orgtree spawns) forks
        a native `codex-*-win32-x64` engine child and a `codex-code-mode-host`
        child. A bare `self.proc.kill()` kills only the node parent; on Windows
        the children are orphaned and KEEP THE THREAD'S `~/.codex` write lock,
        so the NEXT turn's `thread/resume` fails with "thread … already has an
        active writer" (measured 2026-08-30: a run of orphaned pairs from four
        failed turns). Kill by pid through the OS so the tree goes, then
        `wait()` so the next turn does not spawn into a lock the dying tree
        still holds (the same rapid-kill→spawn contention the module docstring
        warns about)."""
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                    check=False, capture_output=True, timeout=10,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            self.proc.kill()          # POSIX, and a belt over taskkill
        except OSError:
            pass
        try:
            self.proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError, ValueError):
            pass


def _thread_id_of(result: dict[str, Any]) -> str | None:
    thread = result.get("thread")
    if isinstance(thread, dict) and thread.get("id"):
        return str(thread["id"])
    for k in ("threadId", "id"):
        if result.get(k):
            return str(result[k])
    return None


def compact_fork(argv_head: list[str], *, cwd: str, model: str | None,
                 thread_id: str, timeout: float,
                 codex_home: str | None = None,
                 sandbox: str = "workspace-write",
                 approval_policy: str = "on-request",
                 developer_instructions: str | None = None,
                 env_extra: dict[str, str] | None = None) -> dict[str, Any]:
    """Fork ``thread_id`` and compact the fork with the app-server's native
    lifecycle.  The source thread is never modified, so it remains a usable
    knowledge bearer while the returned thread becomes the live successor.

    ``thread/compact/start`` only acknowledges that compaction started.  The
    durable success proof is the compact turn's ``turn/completed`` event; an
    empty request response must never be mistaken for a completed compact.
    """
    client = AppServerClient(
        argv_head, codex_home=codex_home, cwd=cwd, env_extra=env_extra)
    try:
        client.initialize()
        forked = client.request("thread/fork", {
            "threadId": thread_id,
            "model": model,
            "cwd": cwd,
            "sandbox": sandbox,
            "approvalPolicy": approval_policy,
            "developerInstructions": developer_instructions,
            # A compaction split needs the new handle, not a second copy of a
            # potentially enormous turn list in the JSON-RPC response.
            "excludeTurns": True,
            # Preserve an active goal but let the next explicit Orgtree turn
            # own its continuation; compaction itself is not agent work.
            "deferGoalContinuation": True,
        })
        new_thread_id = _thread_id_of(forked)
        if not new_thread_id or new_thread_id == thread_id:
            raise CodexServerError(
                "thread/fork returned no distinct successor thread id")

        first_event = len(client.notifications)
        client.request("thread/compact/start", {"threadId": new_thread_id})
        deadline = time.time() + timeout
        seen = first_event
        token_usage: dict[str, Any] | None = None
        while time.time() < deadline:
            while seen < len(client.notifications):
                msg = client.notifications[seen]
                seen += 1
                method = str(msg.get("method") or "")
                params = (msg.get("params")
                          if isinstance(msg.get("params"), dict) else {})
                event_thread = str(params.get("threadId") or "")
                if event_thread and event_thread != new_thread_id:
                    continue
                if method == "thread/tokenUsage/updated":
                    value = params.get("tokenUsage")
                    if isinstance(value, dict):
                        token_usage = value
                    continue
                if method == "turn/failed":
                    raise CodexServerError(
                        "thread/compact/start: compact turn failed")
                if method != "turn/completed":
                    continue
                turn = params.get("turn")
                status = (str(turn.get("status") or "completed")
                          if isinstance(turn, dict) else "completed")
                if status not in (STATUS_COMPLETED, STATUS_INTERRUPTED):
                    detail = (turn.get("error")
                              if isinstance(turn, dict) else None)
                    raise CodexServerError(
                        f"thread/compact/start: compact turn {status}: "
                        f"{str(detail)[:300]}")
                return {"thread_id": new_thread_id,
                        "token_usage": token_usage}
            if client.proc.poll() is not None:
                raise CodexServerError(
                    "thread/compact/start: app-server exited "
                    f"rc={client.proc.returncode}; stderr tail: "
                    f"{' | '.join(client.stderr_tail[-3:])[:400]}")
            time.sleep(0.02)
        raise CodexServerError(
            f"thread/compact/start: no completion in {timeout:.0f}s")
    finally:
        client.close()


_USAGE_COUNTER_FIELDS = (
    "totalTokens", "inputTokens", "cachedInputTokens",
    "outputTokens", "reasoningOutputTokens", "cacheWriteInputTokens")


def _usage_before_last(token_usage: dict[str, Any]) -> dict[str, Any] | None:
    """Infer the thread counter immediately before this turn's first request."""
    total = token_usage.get("total")
    last = token_usage.get("last")
    if not isinstance(total, dict) or not isinstance(last, dict):
        return None
    base: dict[str, Any] = {}
    for key in _USAGE_COUNTER_FIELDS:
        if key in total or key in last:
            value = int(total.get(key) or 0) - int(last.get(key) or 0)
            if value < 0:
                return None
            base[key] = value
    return base or None


def _turn_usage(token_usage: dict[str, Any] | None,
                baseline: dict[str, Any] | None
                ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Convert Codex's thread-cumulative counter into this turn's delta.

    A missing baseline retains the old full-snapshot fallback.  If a known
    counter moves backwards (thread reset, app-server replacement, or provider
    discontinuity), the current snapshot becomes the new baseline and is
    booked whole: never a negative cost and never a silent zero.  The second
    return value is durable audit evidence for the supervisor to record.
    """
    if not isinstance(token_usage, dict):
        return token_usage, None
    total = token_usage.get("total")
    if not isinstance(total, dict) or not isinstance(baseline, dict):
        return token_usage, None
    backwards = [key for key in _USAGE_COUNTER_FIELDS
                 if key in baseline and key in total
                 and int(total.get(key) or 0) < int(baseline.get(key) or 0)]
    normalized = dict(token_usage)
    normalized["sessionTotal"] = dict(total)
    if backwards:
        reset = {"fields": backwards, "baseline": dict(baseline),
                 "current": dict(total), "policy": "book_current_snapshot"}
        return normalized, reset
    delta = dict(total)
    for key in _USAGE_COUNTER_FIELDS:
        if key in total or key in baseline:
            delta[key] = max(0, int(total.get(key) or 0)
                             - int(baseline.get(key) or 0))
    normalized["total"] = delta
    return normalized, None


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
                 env_extra: dict[str, str] | None = None,
                 config_overrides: list[str] | None = None,
                 usage_baseline: dict[str, Any] | None = None,
                 client: AppServerClient | None = None) -> None:
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
        # `tokenUsage.total` is THREAD-cumulative, despite its turn-local
        # notification name.  A pre-turn notification gives the baseline
        # directly; otherwise the first post-start snapshot minus its `last`
        # request reconstructs it.  Keeping this inside the adapter prevents
        # every consumer (billing, compaction, future telemetry) from having
        # to rediscover the provider's counter semantics.
        self._token_usage_base: dict[str, Any] | None = (
            dict(usage_baseline) if isinstance(usage_baseline, dict) else None)
        self._turn_started = False
        self.rate_limits: dict[str, Any] | None = None
        #: EVERY rate-limit snapshot this turn saw, keyed by limitId — the
        #: board, not the last card off it. `rate_limits` above stays last-wins
        #: for its existing readers; this is what `limit_reset_epoch` prices a
        #: freeze from, and it exists because in the measured specimen the last
        #: card was the empty one (D-209).
        self.rate_limit_snapshots: dict[str, dict[str, Any]] = {}
        #: the `turn.error` of a failed turn — the CLI's own words for why.
        #: Discarded entirely until D-209, which is how a usage limit reached
        #: no detector: it is not on stderr, it is here.
        self.error: dict[str, Any] | None = None
        self.status: str | None = None
        self._done = threading.Event()
        self.client = client or AppServerClient(
            argv_head, codex_home=codex_home, cwd=cwd,
            env_extra=env_extra, config_overrides=config_overrides)
        self.client.bind(on_event=self._observe,
                         tool_dispatch=tool_dispatch,
                         approval_decide=approval_decide)

    # ── event fold (M2: raw notifications → normalized fields) ───────────

    def _note_error(self, err: Any) -> None:
        """Keep a `turn.error` if there is one. A turn that completes cleanly
        sends `"error": null`, so the guard is what stops a clean completion
        from erasing an error an earlier event already recorded."""
        if isinstance(err, dict) and error_text(err):
            self.error = dict(cast("dict[str, Any]", err))

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
                if not self._turn_started and self._token_usage_base is None:
                    total = tu.get("total")
                    if isinstance(total, dict):
                        self._token_usage_base = dict(total)
                else:
                    # Infer only from the FIRST in-turn snapshot. If an older
                    # server omits `last` there, retain the safe full-total
                    # fallback; inferring from a later request would discard
                    # earlier work in this same turn.
                    if self._token_usage_base is None \
                            and self.token_usage is None:
                        self._token_usage_base = _usage_before_last(tu)
                    self.token_usage = tu
        elif method == "account/rateLimits/updated":
            rl = params.get("rateLimits")
            if isinstance(rl, dict):
                self.rate_limits = rl
                self.rate_limit_snapshots[
                    str(rl.get("limitId") or "codex")] = rl
        elif method == "turn/completed":
            turn = params.get("turn")
            if isinstance(turn, dict):
                # ⚠ THE STATUS IS READ, NOT ASSUMED (D-209). See `_status_of`.
                self.status = _status_of(str(turn.get("status") or ""))
                self._note_error(turn.get("error"))
            else:
                self.status = STATUS_COMPLETED
            self._done.set()
        elif method == "turn/failed":
            # Kept although codex-cli 0.150.1 never sends it (see _TURN_STATUS)
            # — a future server that does must not land back in silence. The
            # error is read from either shape it could plausibly wear.
            self.status = STATUS_FAILED
            _t = params.get("turn")
            self._note_error(_t.get("error") if isinstance(_t, dict)
                             else params.get("error"))
            self._done.set()
        if self._caller_on_event:
            self._caller_on_event(msg)

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self, input_text: str,
              image_inputs: list[dict[str, Any]] | None = None,
              on_thread: Callable[[str], None] | None = None) -> str:
        """Initialize, open/resume the thread, start the turn. Returns the
        durable thread id (the provider session id the node records).

        `on_thread(thread_id)` fires the instant the thread id is FINAL and
        before `turn/start` goes on the wire — the last moment at which no
        notification for this turn can exist yet, because the turn does not.

        That hook is not a convenience; it is where the caller's ordering
        invariant is enforceable (supervisor `_codex_leg`). Returning the
        thread id from here is too late: `_pump` dispatches notifications on
        the READER thread while this one is still inside `request()`'s poll
        loop, so `item/started`, `item/agentMessage/delta` and
        `item/completed` are all observed BEFORE this method returns — on
        every run of a ten-run measurement, fresh threads and resumed ones
        alike. A caller that opens its journal on the return value therefore
        streams assistant output against a transcript that does not yet carry
        the user's message. test_codex_stream_order.py holds that failure
        down: remove this hook and it reports it.

        The hook is fail-open: journaling must never be the reason a turn
        does not run (same contract as `_codex_journal`), so an exception in
        it is swallowed and the turn proceeds."""
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
        user_input: list[dict[str, Any]] = [
            {"type": "text", "text": input_text}]
        user_input.extend(image_inputs or [])
        if on_thread is not None and self.thread_id:
            try:
                on_thread(self.thread_id)
            except Exception:                              # noqa: BLE001
                pass      # journaling never blocks a turn — see the docstring
        self._turn_started = True
        turn = self.client.request("turn/start", {
            "threadId": self.thread_id,
            "input": user_input,
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

    def wait(self, timeout: float | None = None, *,
             close_client: bool = True) -> dict[str, Any]:
        """Block until the turn ends and return the normalized result.

        Cold callers retain the historical default and close the app-server.
        A warm-pool claimant passes ``close_client=False`` and parks the same
        process after this boundary.
        """
        finished = self._done.wait(timeout) if timeout else self._done.wait()
        if not finished and self.status is None:
            self.status = STATUS_FAILED
        if close_client:
            self.client.close()
        usage, usage_reset = _turn_usage(
            self.token_usage, self._token_usage_base)
        return {
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "status": self.status or STATUS_FAILED,
            "agent_text": "".join(self.agent_text),
            "token_usage": usage,
            # A counter moving backwards is not silently clamped: the current
            # snapshot is booked as a new baseline and the supervisor records
            # this evidence on the node for audit/reconciliation.
            "usage_reset": usage_reset,
            "rate_limits": self.rate_limits,
            # D-209: the CLI's own reason, and the whole rate-limit board that
            # dates it. Both are None/{} on every healthy turn.
            "error": self.error,
            "rate_limit_snapshots": dict(self.rate_limit_snapshots),
        }
