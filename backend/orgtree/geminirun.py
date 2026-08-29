# pyright: strict
"""The gemini turn runner: one `gemini --acp` JSON-RPC session per turn.

D-185. The shape deliberately mirrors codexrun.py: ONE PROCESS PER TURN,
resumed by a durable session id — here the ACP `sessionId`, harvested from
`session/new` and passed back through `session/load` on the next turn (both
measured cross-process, probe logs 2026-08-29). Interrupting acts on the live
client the supervisor holds while the turn runs:

    session/cancel   — graceful stop (a NOTIFICATION, not a request); the
                       in-flight session/prompt then resolves with
                       stopReason "cancelled" and NO usage metadata
                       (measured), which the caller books as an
                       interrupted-but-completed turn costing $0.

There is NO mid-turn steer verb on this wire — `steer()` always refuses, and
the supervisor's queue fallback (the same one the codex turn-over guard
falls to) delivers the mail at the next turn boundary instead.

Org powers attach as MCP SERVERS on `session/new`/`session/load` (measured
round-trip): the same `python -m orgtree.mcptool` stdio server the claude
lane spawns via --mcp-config rides the `mcpServers` param, so the ledger
enforces authority identically on every lane and nothing is written to the
user's gemini config. ⚠ env entries are an ARRAY of {name, value} pairs (ACP
EnvVariable), and a var NOT named in the spec is INHERITED from the CLI
process (measured — a stray ORGTREE_NODE leaked through), so the caller must
always name the full ORGTREE_* set explicitly.

⚠ THE MODEL PIN IS ASSERTED, NOT ASSUMED. The CLI silently substitutes its
default model for an id its registry does not know (measured: `-m
gemini-3.7-flash` served 3.5-flash with no warning). `-m` on the argv is
honored and REFLECTED in the session/new AND session/load results'
`models.currentModelId` (both measured), so the turn fails loudly on any
mismatch instead of running a whole conversation on the wrong model.

⚠ session/load REPLAYS the stored conversation as session/update
notifications BEFORE the live turn (measured: old user/agent/tool chunks
arrive during the load window). Events are folded to the caller only once
`session/prompt` is on the wire — without the gate, every resume would
re-stream and re-journal the node's whole history.

⚠ CREDENTIALS: this module never reads, copies or moves auth material. The
CLI self-authenticates from its own store (settings.json + the OS keychain
for an api-key login — measured; ~/.gemini/oauth_creds.json for OAuth); a
missing credential fails the turn with the CLI's own error. Env hygiene at
spawn strips the OTHER providers' material (ANTHROPIC_*/CLAUDE_CODE_*/
CLAUDECODE/OPENAI_API_KEY) — one credential per spawn, same as codexrun.

Hermetic by construction: everything is parameterized (argv head, home, cwd,
hooks), so tests drive it against backend/tests/fakegemini.py instead of the
real CLI — see backend/tests/test_geminirun.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any, Final

#: how long request() waits before declaring the agent unresponsive. Turns
#: themselves are unbounded (the caller owns the turn timeout); this bounds
#: single request/response exchanges like initialize or session/new.
REQUEST_TIMEOUT: Final = 120.0

#: normalized turn statuses — the same vocabulary codexrun exports, so the
#: supervisor's policy layer never learns a provider's raw strings.
STATUS_COMPLETED: Final = "completed"
STATUS_INTERRUPTED: Final = "interrupted"
STATUS_FAILED: Final = "failed"

#: ACP stopReason → normalized status. "cancelled" is a COMPLETED turn
#: (the ⏸ semantics); everything else that resolves is completed; a JSON-RPC
#: error response is the failure path.
_STOP_INTERRUPTED: Final = ("cancelled",)


class GeminiServerError(RuntimeError):
    """The ACP agent refused or never answered a protocol request."""


def deliverable_mcp(servers: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Split a registry subset into (what this lane can attach, what it
    cannot). The ACP `mcpServers` param expresses stdio servers (command +
    args + env) and — per the advertised mcpCapabilities — http/sse URL
    servers. Anything else is named so the identity prompt can say so out
    loud instead of promising it (the D-180 discipline)."""
    ok: dict[str, Any] = {}
    dropped: list[str] = []
    for name in sorted(servers):
        srv = servers[name]
        if isinstance(srv, dict) and (srv.get("command") or srv.get("url")):
            ok[name] = srv
        else:
            dropped.append(name)
    return ok, dropped


def acp_mcp_servers(servers: dict[str, Any]) -> list[dict[str, Any]]:
    """Registry entries → the ACP `mcpServers` list. Env is the measured
    ARRAY-of-{name,value} shape, sorted for a stable wire (and stable
    tests)."""
    out: list[dict[str, Any]] = []
    for name, srv in sorted(servers.items()):
        if not isinstance(srv, dict):
            continue
        if srv.get("command"):
            env_map = srv.get("env") if isinstance(srv.get("env"), dict) else {}
            out.append({
                "name": name,
                "command": str(srv["command"]),
                "args": [str(a) for a in (srv.get("args") or [])],
                "env": [{"name": str(k), "value": str(v)}
                        for k, v in sorted(env_map.items())],
            })
        elif srv.get("url"):
            entry: dict[str, Any] = {
                "type": str(srv.get("type") or "http"),
                "name": name, "url": str(srv["url"])}
            headers = srv.get("headers")
            if isinstance(headers, dict):
                entry["headers"] = [{"name": str(k), "value": str(v)}
                                    for k, v in sorted(headers.items())]
            out.append(entry)
    return out


class AcpClient:
    """One `gemini --acp` child process spoken to over stdio NDJSON.

    Threading model mirrors codexrun.AppServerClient: a reader thread pumps
    stdout; server->client REQUESTS (permission asks — we advertise no fs or
    terminal capability, so nothing else should arrive) are answered
    synchronously on that thread via the caller's hook, failing CLOSED.
    Notifications stream to `on_event` as they arrive.
    """

    def __init__(self, argv_head: list[str], *, cwd: str | None = None,
                 on_event: Callable[[dict[str, Any]], None] | None = None,
                 permission_decide: Callable[[dict[str, Any]],
                                             str | None] | None = None,
                 env_extra: dict[str, str] | None = None,
                 extra_args: list[str] | None = None) -> None:
        env = dict(os.environ)
        # one credential per spawn: a gemini child must never see Anthropic
        # or OpenAI material. GOOGLE_/GEMINI_ vars pass through untouched —
        # they are this provider's own, and the keychain lane needs nothing.
        for k in list(env):
            if k.startswith(("ANTHROPIC_", "CLAUDE_CODE_")) or k in (
                    "CLAUDECODE", "OPENAI_API_KEY"):
                env.pop(k, None)
        if env_extra:
            env.update(env_extra)
        # cwd is the agent's own scratch: GEMINI.md discovery (the identity
        # door — re-read on session/load too, measured) and every relative
        # path resolve against the PROCESS.
        self.proc = subprocess.Popen(
            argv_head + ["--acp"] + list(extra_args or []),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd)
        self.on_event = on_event
        self.permission_decide = permission_decide
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
        if method == "session/request_permission":
            # the ⚙-rights seam: the caller's policy picks an option id, or
            # None to refuse. Fail CLOSED on a missing/broken hook — a
            # cancelled outcome ends the tool call, never hangs it.
            option: str | None = None
            if self.permission_decide is not None:
                try:
                    option = self.permission_decide(params)
                except Exception:
                    option = None
            if option:
                res: dict[str, Any] = {"outcome": {
                    "outcome": "selected", "optionId": option}}
            else:
                res = {"outcome": {"outcome": "cancelled"}}
            self._send({"jsonrpc": "2.0", "id": rid, "result": res})
            return
        # fs/* and terminal/* cannot legitimately arrive — the initialize
        # advertised both capabilities false. Refuse loudly, never hang.
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

    def request_async(self, method: str, params: dict[str, Any]) -> int:
        """Send a request and return its id — the caller polls
        `take_response`. This is how the turn-long session/prompt rides."""
        with self._lock:
            rid = self._next_id
            self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": rid,
                    "method": method, "params": params})
        return rid

    def take_response(self, rid: int) -> dict[str, Any] | None:
        with self._lock:
            return self._responses.pop(rid, None)

    def request(self, method: str, params: dict[str, Any],
                timeout: float = REQUEST_TIMEOUT) -> dict[str, Any]:
        rid = self.request_async(method, params)
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = self.take_response(rid)
            if resp is not None:
                if "error" in resp and resp["error"]:
                    raise GeminiServerError(
                        f"{method}: {json.dumps(resp['error'])[:400]}")
                result: dict[str, Any] = resp.get("result") or {}
                return result
            if self.proc.poll() is not None:
                raise GeminiServerError(
                    f"{method}: gemini --acp exited rc={self.proc.returncode}; "
                    f"stderr tail: {' | '.join(self.stderr_tail[-3:])[:400]}")
            time.sleep(0.02)
        raise GeminiServerError(f"{method}: no answer in {timeout:.0f}s")

    def initialize(self) -> dict[str, Any]:
        # fs and terminal are advertised FALSE on purpose: the CLI's own
        # tools run in ITS process against the scratch cwd; orgtree is a
        # policy client here, not a filesystem.
        return self.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False}}, 60)

    def close(self) -> None:
        try:
            self.proc.kill()
        except OSError:
            pass


def _current_model(result: dict[str, Any]) -> str | None:
    models = result.get("models")
    if isinstance(models, dict) and models.get("currentModelId"):
        return str(models["currentModelId"])
    return None


def _normalize_usage(meta: dict[str, Any] | None,
                     main_model: str | None,
                     requests: int = 1) -> dict[str, Any] | None:
    """The ACP result's `_meta.quota` → the normalized usage document
    `providers.gemini_cost` consumes. Measured wire shape (probe logs):
    {"quota": {"token_count": {...}, "model_usage": [{"model": id,
    "token_count": {"input_tokens": n, "output_tokens": n}}]}} — per model,
    no cached/thoughts split (the cost fn documents that approximation).

    ⚠ `input_tokens` is the SUM over every API request the turn made, not
    the last request's prompt (measured twice: the M0 tool probe reported
    21,320 for a 2-request session whose context was ~10.6K, and the first
    real user-driven flash agent booked a 3.6M "occupancy" against a 1M
    window after a ~30-round tool loop). The sum is what Google BILLS — the
    cost fold wants exactly it — but context occupancy must not read it as
    a prompt size, so the document carries `requests` (the turn's observed
    request count) for `gemini_occupancy` to divide by."""
    if not isinstance(meta, dict):
        return None
    quota = meta.get("quota")
    if not isinstance(quota, dict):
        return None
    models: dict[str, Any] = {}
    usage_list = quota.get("model_usage")
    if isinstance(usage_list, list):
        for entry in usage_list:
            if not isinstance(entry, dict):
                continue
            mid = str(entry.get("model") or "")
            tc = entry.get("token_count")
            if not mid or not isinstance(tc, dict):
                continue
            inp = int(tc.get("input_tokens") or 0)
            out = int(tc.get("output_tokens") or 0)
            models[mid] = {"input": inp, "cached": 0,
                           "output": out, "prompt": inp}
    if not models:
        tc = quota.get("token_count")
        if isinstance(tc, dict) and main_model:
            inp = int(tc.get("input_tokens") or 0)
            models[main_model] = {"input": inp, "cached": 0,
                                  "output": int(tc.get("output_tokens") or 0),
                                  "prompt": inp}
    if not models:
        return None
    main = main_model if main_model in models else max(
        models, key=lambda m: int(models[m].get("prompt") or 0))
    return {"models": models, "main": main, "requests": max(1, int(requests))}


class GeminiTurn:
    """One turn's lifecycle, from spawn to normalized result — the same
    seam contract as codexrun.CodexTurn, so the supervisor's provider leg
    holds either object behind the same verbs."""

    def __init__(self, argv_head: list[str], *, cwd: str, model: str | None,
                 session_id: str | None = None,
                 approval_mode: str = "yolo",
                 mcp_servers: list[dict[str, Any]] | None = None,
                 on_event: Callable[[dict[str, Any]], None] | None = None,
                 permission_decide: Callable[[dict[str, Any]],
                                             str | None] | None = None,
                 env_extra: dict[str, str] | None = None) -> None:
        extra: list[str] = []
        if model:
            extra += ["-m", model]
        extra += ["--approval-mode", approval_mode]
        self.client = AcpClient(
            argv_head, cwd=cwd, on_event=self._observe,
            permission_decide=permission_decide,
            env_extra=env_extra, extra_args=extra)
        self._caller_on_event = on_event
        self.cwd = cwd
        self.model = model
        self.mcp_servers = mcp_servers or []
        self.session_id = session_id
        self.agent_text: list[str] = []
        self.token_usage: dict[str, Any] | None = None
        self.status: str | None = None
        self.stop_reason: str | None = None
        #: replay gate (measured): session/load re-emits the whole stored
        #: conversation as session/update notifications BEFORE the live turn.
        #: Nothing is folded to the caller until session/prompt is sent.
        self._live = False
        self._prompt_rid: int | None = None
        #: observed tool rounds — the request-count estimate the usage
        #: normalizer needs (every tool call adds an API request; the
        #: initial prompt is request one). Counting individual calls
        #: OVERCOUNTS a parallel batch, which only pushes the occupancy
        #: estimate LOWER — the safe direction; the unfixed reading was a
        #: 3.6× overestimate that spuriously pressured compaction.
        self._tool_calls: set[str] = set()

    # ── event fold ───────────────────────────────────────────────────────

    def _observe(self, msg: dict[str, Any]) -> None:
        if not self._live:
            return
        params: dict[str, Any] = msg.get("params") or {}
        update: dict[str, Any] = (params.get("update")
                                  if isinstance(params.get("update"), dict)
                                  else {})
        if str(msg.get("method", "")) == "session/update":
            kind = str(update.get("sessionUpdate") or "")
            if kind == "agent_message_chunk":
                content = update.get("content")
                if isinstance(content, dict) and isinstance(
                        content.get("text"), str):
                    self.agent_text.append(content["text"])
            elif kind == "tool_call":
                self._tool_calls.add(str(update.get("toolCallId") or ""))
        if self._caller_on_event:
            self._caller_on_event(msg)

    # ── lifecycle ────────────────────────────────────────────────────────

    def start(self, input_text: str,
              image_inputs: list[dict[str, Any]] | None = None) -> str:
        """Initialize, open/load the session, put the prompt on the wire.
        Returns the durable session id (the provider session id the node
        records). The prompt response is collected by `wait()`."""
        self.client.initialize()
        if self.session_id:
            res = self.client.request("session/load", {
                "sessionId": self.session_id, "cwd": self.cwd,
                "mcpServers": self.mcp_servers})
        else:
            res = self.client.request("session/new", {
                "cwd": self.cwd, "mcpServers": self.mcp_servers})
            sid = res.get("sessionId")
            if not sid:
                raise GeminiServerError(
                    f"session/new returned no sessionId: "
                    f"{json.dumps(res)[:300]}")
            self.session_id = str(sid)
        # ⚠ the anti-silent-fallback assertion: the CLI substitutes its
        # default model for an unknown id with NO warning (measured), and
        # both open verbs report the model actually serving this session.
        served = _current_model(res)
        if self.model and served and served != self.model:
            raise GeminiServerError(
                f"model pin refused: session is serving {served!r}, not the "
                f"pinned {self.model!r} — the id is not in this CLI's "
                f"registry (it silently substitutes its default)")
        prompt: list[dict[str, Any]] = [{"type": "text", "text": input_text}]
        prompt.extend(image_inputs or [])
        self._live = True
        self._prompt_rid = self.client.request_async("session/prompt", {
            "sessionId": self.session_id, "prompt": prompt})
        assert self.session_id is not None
        return self.session_id

    def steer(self, text: str) -> bool:
        """No mid-turn input verb exists on this wire — always False, and
        the caller's queue fallback delivers at the next turn boundary."""
        return False

    def interrupt(self) -> bool:
        """session/cancel is a NOTIFICATION; the in-flight prompt then
        resolves with stopReason "cancelled" (measured), which wait() maps
        to the interrupted-but-completed status."""
        if not (self.session_id and self._prompt_rid):
            return False
        try:
            self.client.notify("session/cancel",
                               {"sessionId": self.session_id})
            return True
        except OSError:
            return False

    def wait(self, timeout: float | None = None) -> dict[str, Any]:
        """Block until the prompt resolves; kill the process; return the
        normalized result the policy layer consumes."""
        deadline = time.time() + timeout if timeout else None
        resp: dict[str, Any] | None = None
        while self._prompt_rid is not None:
            resp = self.client.take_response(self._prompt_rid)
            if resp is not None:
                break
            if self.client.proc.poll() is not None:
                break
            if deadline and time.time() >= deadline:
                break
            time.sleep(0.05)
        if resp is None:
            self.status = STATUS_FAILED
        elif resp.get("error"):
            self.status = STATUS_FAILED
            self.stop_reason = json.dumps(resp["error"])[:300]
        else:
            result: dict[str, Any] = resp.get("result") or {}
            self.stop_reason = str(result.get("stopReason") or "end_turn")
            self.status = (STATUS_INTERRUPTED
                           if self.stop_reason in _STOP_INTERRUPTED
                           else STATUS_COMPLETED)
            self.token_usage = _normalize_usage(
                result.get("_meta") if isinstance(result.get("_meta"), dict)
                else None, self.model,
                requests=1 + len(self._tool_calls))
        self.client.close()
        return {
            "session_id": self.session_id,
            "status": self.status or STATUS_FAILED,
            "stop_reason": self.stop_reason,
            "agent_text": "".join(self.agent_text),
            "token_usage": self.token_usage,
            # parity with the codex result shape; gemini's api-key lane has
            # no window telemetry, so this is always None today
            "rate_limits": None,
        }
