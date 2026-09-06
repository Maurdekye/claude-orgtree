# pyright: strict
"""Turn records, the PURE half — contract in docs/turn-events.md.

The schema (event kinds, their typed fields, the closed vocabularies), the
coercion every leaf goes through, the shape helpers the capture sites use,
fixture-name validation and containment, and the OFFLINE readers: `load`,
`list_records`, `fixture_path`, `summarize`, `drift`. Imports nothing of
orgtree and nothing that touches a process, a socket, a thread or a
database: `tools/inspect_turn.py` runs on this module alone under the
purity import hook. The recorder that WRITES records is `turnlog.py`.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Mapping


SCHEMA = 1
HEAD = 64                    # events kept from the start of an attempt
TAIL = 176                   # events kept from the end
MAX_EVENTS = HEAD + TAIL
CAP_BYTES = 65536            # the on-disk record; over it the middle is cut
RING = 60                    # records per node, stubs included, at open AND close
LIST_MAX = 8                 # any list-valued field
STR_MAX = 48                 # a string leaf before vocabulary lookup
INT_MAX = 10 ** 15           # a typed count/duration beyond this is null

# --------------------------------------------------------------- vocabularies
LANES = frozenset({"claude", "openrouter", "codex", "antigravity"})
# ledger.TIERS' keys, copied: this module imports nothing of orgtree
TIERS = frozenset({"fable", "opus", "sonnet", "haiku", "sol", "terra",
                   "gpt-reserve", "luna", "astra", "flash", "pro"})
OUTCOMES = frozenset({"completed", "interrupted", "frozen", "killed",
                      "abandoned", "unrecoverable", "redriven", "failed",
                      "crashed", "unknown"})
ERROR_CLASSES = frozenset({"RuntimeError", "_ProviderTurnFailed",
                           "_CodexRouteRejected", "ValueError", "OSError",
                           "TimeoutError", "KeyError", "TypeError"})
# builtin claude CLI tool names — a STATIC reviewed vocabulary; every other
# name (MCP servers, custom tools) is "other", counts retained
TOOLS = frozenset({"Bash", "PowerShell", "Read", "Edit", "Write", "MultiEdit",
                   "Glob", "Grep", "LS", "Agent", "Task", "Skill", "ToolSearch",
                   "WebFetch", "WebSearch", "NotebookEdit", "TodoWrite",
                   "AskUserQuestion", "EnterWorktree", "ExitWorktree",
                   "Monitor", "TaskOutput", "TaskStop", "SendMessage",
                   "ListAgents", "Workflow", "ScheduleWakeup", "CronCreate",
                   "CronDelete", "CronList"})
PERMISSION_MODES = frozenset({"default", "acceptEdits", "bypassPermissions",
                              "plan", "dontAsk"})
RESULT_SUBTYPES = frozenset({"success", "error_during_execution",
                             "error_max_turns", "error"})
# the CLI's typed API-error vocabulary (failfix.CODE_WORDS, the same list)
API_CODES = frozenset({"authentication_failed", "oauth_org_not_allowed",
                       "account_on_hold", "billing_error", "rate_limit",
                       "model_not_found", "invalid_request", "server_error",
                       "max_output_tokens", "dlp_request_denied"})
WATCHDOG_WHY = frozenset({"idle", "budget", "leash", "ceiling"})
OWNERS = frozenset({"unrecoverable", "filter", "account_switch",
                    "limit_freeze", "net_retry", "net_exhausted", "terminal",
                    "provider_limit"})
FREEZE_KINDS = frozenset({"limit", "connection"})
SCHEDULES = frozenset({"observed-deadline", "probe", "backoff"})
DOORS = frozenset({"pre_model", "ran_then_failed", "killed"})
DISCARDS = frozenset({"limit-frozen", "turn-timeout", "stdin-closed",
                      "claim-died", "prompt-changed", "identity-changed"})
CODEX_POOLS = frozenset({"plan", "reserve"})
CODEX_ROUTES = frozenset({"reserve", "direct"})     # codex_route.Route.route
CODEX_SELECTIONS = frozenset({"preflight", "retry"})
CODEX_ITEMS = frozenset({"agent_message", "reasoning", "tool_call",
                         "tool_output", "plan", "other"})
CODEX_STATUS = frozenset({"completed", "failed", "interrupted", "in_progress"})
CODEX_KINDS = frozenset({"usage-limit", "rate-limit", "auth", "context",
                         "budget", "overloaded", "connection",
                         "usage-limit-prose", "other", "unknown"})
POOL_STATES = frozenset({"exhausted", "no-grant", "unexplained",
                         "unattributed", "n/a"})
AGY_STEPS = frozenset({"text", "tool", "thinking", "done"})
AGY_STATUS = frozenset({"completed", "failed", "interrupted"})
AGY_SCHEDULES = frozenset({"observed-deadline", "probe"})

# ---------------------------------------------------------------- the schema
# field type tags: B bool · I int · F float · S(vocab) string · L(vocab) list
B, I, F = "B", "I", "F"


def S(v: frozenset[str]) -> tuple[str, frozenset[str]]:
    return ("S", v)


def L(v: frozenset[str]) -> tuple[str, frozenset[str]]:
    return ("L", v)


FieldSpec = Any
FIELDS: dict[str, dict[str, FieldSpec]] = {
    "start": {"slot_wait_ms": I},
    "spawn": {"warm": B, "spawn_ms": I},
    "init": {"tools_n": I, "mcp_n": I, "mcp_failed_n": I,
             "mode": S(PERMISSION_MODES)},
    "delivered": {},
    "first_output": {"thinking": B},
    "assistant": {"text_n": I, "tool_n": I, "thinking": B, "synthetic": B,
                  "api_error": B, "tools": L(TOOLS)},
    "tool_result": {"n": I, "errors_n": I},
    "api_retry": {"code": S(API_CODES), "n": I},
    "result": {"boundary": B, "is_error": B, "subtype": S(RESULT_SUBTYPES),
               "status": I, "duration_ms": I, "num_turns": I,
               "result_len": I, "errors_n": I, "in_tokens": I,
               "out_tokens": I, "cache_read": I, "cache_create": I,
               "cost_known": B},
    "queued_next": {"n": I},
    "steer": {"n": I},
    "interrupt": {},
    "watchdog": {"why": S(WATCHDOG_WHY), "elapsed_ms": I},
    "exit": {"code": I, "parked": B, "exit_only": B, "stderr_len": I,
             "stderr_lines": I},
    "classify": {"limit": B, "net": B, "filtered": B, "typed": I,
                 "started": B, "boundary": B, "or_lane": B},
    "owner": {"branch": S(OWNERS), "handled": B},
    "freeze": {"freeze_kind": S(FREEZE_KINDS), "run": I, "delay_s": I,
               "schedule": S(SCHEDULES), "reset_known": B},
    "abandon": {"door": S(DOORS), "hard_fail_run": I},
    "fixture": {"written": B},
    "fold_back": {"undelivered_n": I, "uncertain_n": I},
    "teardown": {"parked": B, "discard": S(DISCARDS), "exited": B},
    "dispose": {"outcome": S(OUTCOMES)},
    "end": {"outcome": S(OUTCOMES), "outcome_ms": I},
    # codex
    "codex_route": {"pool": S(CODEX_POOLS), "route": S(CODEX_ROUTES),
                    "selection": S(CODEX_SELECTIONS)},
    "codex_account": {"ambiguous": B},
    "codex_item": {"type": S(CODEX_ITEMS), "n": I},
    "codex_rerouted": {"known": B},
    "codex_rate_limit": {"pool": S(CODEX_POOLS), "percent": I, "reset": I,
                         "folded": B},
    "codex_status": {"status": S(CODEX_STATUS), "rpc_code": I},
    "codex_decide": {"decision": S(CODEX_KINDS), "rejected": B, "redrive": B,
                     "pool_state": S(POOL_STATES), "reset_known": B},
    "codex_redrive": {"to": S(CODEX_POOLS)},
    # antigravity
    "agy_step": {"step": S(AGY_STEPS), "n": I},
    "agy_status": {"status": S(AGY_STATUS)},
    "agy_wall": {"walled": B, "reset_known": B, "reset_in_s": I,
                 "schedule": S(AGY_SCHEDULES)},
    "agy_ceiling": {"elapsed_s": I, "ceiling_s": I, "killed": B},
}
KINDS = frozenset(FIELDS)
assert not any(k in ("seq", "t_ms", "kind") for f in FIELDS.values() for k in f)
HEADER_FIELDS: dict[str, FieldSpec] = {
    "lane": S(LANES), "tier": S(TIERS), "run": I, "run_since_ms": I,
    "resumed": B, "cmd": B, "ping": B, "toks": I, "text_len": I,
    "images_n": I, "view_len": I, "warm": B,
}
_FIXTURE_RE = re.compile(
    r"^\d{13}-\d{4}-(admission|stream|result-error|teardown|unknown)-"
    r"(filtered|limit|net|none)\.json$")
_RECORD_RE = re.compile(r"^\d{13}-\d{4}-[a-z]+-[a-z]+\.json$")
_STUB_RE = re.compile(r"^\d{13}-\d{4}\.partial\.json$")


# ------------------------------------------------------------------ coercion


def _int(v: Any) -> int | None:
    """A typed count/duration: an int (not a bool) within INT_MAX, else None —
    no string coercion. A float that is whole is NOT accepted either."""
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v if -INT_MAX <= v <= INT_MAX else None


def _vocab(v: Any, allowed: frozenset[str]) -> str | None:
    if v is None or v == "":
        return None
    s = str(v).strip()[:STR_MAX]
    return s if s in allowed else "other"


def coerce(spec: FieldSpec, v: Any) -> Any:
    if spec == B:
        return v is True
    if spec == I:
        return _int(v)
    if spec == F:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return float(v) if v == v and abs(v) < float(INT_MAX) else None
    tag, allowed = spec
    if tag == "S":
        return _vocab(v, allowed)
    if not isinstance(v, (list, tuple)):
        return []
    out: list[str] = []
    for x in list(v)[:LIST_MAX]:                          # pyright: ignore[reportUnknownArgumentType]
        m = _vocab(x, allowed)
        if m is not None:
            out.append(m)
    return out


def window_of(snap: Any) -> tuple[int | None, int | None]:
    """(usedPercent, resetsAt) of a rate-limit notification's PRIMARY window,
    typed, or (None, None) — never raises, whatever the shape."""
    try:
        if not isinstance(snap, Mapping):
            return None, None
        inner = snap.get("rateLimits")
        src: Mapping[str, Any] = inner if isinstance(inner, Mapping) else snap  # pyright: ignore[reportUnknownVariableType]
        prim = src.get("primary")
        if not isinstance(prim, Mapping):
            return None, None
        pct, rst = prim.get("usedPercent"), prim.get("resetsAt")  # pyright: ignore[reportUnknownMemberType]
        pct_i = int(pct) if isinstance(pct, (int, float)) and not isinstance(pct, bool) else None
        rst_i = int(rst) if isinstance(rst, (int, float)) and not isinstance(rst, bool) else None
        return _int(pct_i), _int(rst_i)
    except Exception:                                        # noqa: BLE001
        return None, None


def seconds_of(value: Any) -> int | None:
    """A duration/instant as whole seconds: a positive number (not a bool),
    else None — never raises."""
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return _int(int(value)) if value > 0 else None
    except Exception:                                        # noqa: BLE001
        return None


def assistant_shape(ev: Any) -> dict[str, Any]:
    """The typed SHAPE of a stream `assistant` event: block counts, whether
    it thought, whether the engine (model "<synthetic>") rather than the
    model spoke, the CLI's api-error flag, and the builtin tool names it
    called (others "other"). Never the text. Never raises."""
    out: dict[str, Any] = {"text_n": 0, "tool_n": 0, "thinking": False,
                           "synthetic": False, "api_error": False,
                           "tools": []}
    try:
        if not isinstance(ev, Mapping):
            return out
        msg = ev.get("message")
        m: Mapping[str, Any] = msg if isinstance(msg, Mapping) else {}  # pyright: ignore[reportUnknownVariableType]
        out["synthetic"] = str(m.get("model") or "") == "<synthetic>"
        out["api_error"] = bool(ev.get("is_api_error_message")
                                or ev.get("isApiErrorMessage")
                                or m.get("is_api_error_message"))
        c = m.get("content")
        if isinstance(c, str):
            out["text_n"] = 1 if c else 0
            return out
        tools: list[str] = []
        for b in (c if isinstance(c, list) else []):  # pyright: ignore[reportUnknownVariableType]
            if not isinstance(b, Mapping):
                continue
            t = str(b.get("type") or "")  # pyright: ignore[reportUnknownMemberType]
            if t == "text":
                out["text_n"] += 1
            elif t == "tool_use":
                out["tool_n"] += 1
                tools.append(str(b.get("name") or ""))  # pyright: ignore[reportUnknownMemberType]
            elif t in ("thinking", "redacted_thinking"):
                out["thinking"] = True
        out["tools"] = tools
        return out
    except Exception:                                        # noqa: BLE001
        return out


def init_shape(ev: Any) -> dict[str, Any]:
    """The CLI's `system/init` resolution as counts: tools, MCP servers and
    how many of those failed, the permission mode. Never names."""
    out: dict[str, Any] = {"tools_n": 0, "mcp_n": 0, "mcp_failed_n": 0,
                           "mode": None}
    try:
        if not isinstance(ev, Mapping):
            return out
        tools = ev.get("tools")
        out["tools_n"] = len(tools) if isinstance(tools, list) else 0  # pyright: ignore[reportUnknownArgumentType]
        servers = ev.get("mcp_servers")
        sl: list[Any] = servers if isinstance(servers, list) else []  # pyright: ignore[reportUnknownVariableType]
        out["mcp_n"] = len(sl)
        out["mcp_failed_n"] = sum(
            1 for x in sl if isinstance(x, Mapping)
            and str(x.get("status") or "") not in ("", "connected"))  # pyright: ignore[reportUnknownMemberType]
        out["mode"] = ev.get("permissionMode")
        return out
    except Exception:                                        # noqa: BLE001
        return out


def tool_result_shape(ev: Any) -> dict[str, int]:
    """Counts of tool_result blocks on a stream `user` event and how many
    carried is_error. Never the content. Never raises."""
    n = errs = 0
    try:
        msg = ev.get("message") if isinstance(ev, Mapping) else None
        c = msg.get("content") if isinstance(msg, Mapping) else None  # pyright: ignore[reportUnknownMemberType]
        for b in (c if isinstance(c, list) else []):  # pyright: ignore[reportUnknownVariableType]
            if isinstance(b, Mapping) and b.get("type") == "tool_result":  # pyright: ignore[reportUnknownMemberType]
                n += 1
                if b.get("is_error") is True:  # pyright: ignore[reportUnknownMemberType]
                    errs += 1
    except Exception:                                        # noqa: BLE001
        pass
    return {"n": n, "errors_n": errs}


def result_shape(ev: Any, *, boundary: bool) -> dict[str, Any]:
    """The typed shape of a stream `result` event. Never the text."""
    out: dict[str, Any] = {"boundary": boundary, "is_error": False}
    try:
        if not isinstance(ev, Mapping):
            return out
        out["is_error"] = ev.get("is_error") is True
        out["subtype"] = ev.get("subtype")
        st = ev.get("api_error_status")
        if st is None:
            st = ev.get("apiErrorStatus")
        out["status"] = st if isinstance(st, int) and not isinstance(st, bool) else None
        out["duration_ms"] = ev.get("duration_ms")
        out["num_turns"] = ev.get("num_turns")
        out["result_len"] = len(str(ev.get("result") or ""))
        errs = ev.get("errors")
        out["errors_n"] = len(errs) if isinstance(errs, list) else 0  # pyright: ignore[reportUnknownArgumentType]
        u = ev.get("usage")
        um: Mapping[str, Any] = u if isinstance(u, Mapping) else {}  # pyright: ignore[reportUnknownVariableType]
        out["in_tokens"] = um.get("input_tokens")
        out["out_tokens"] = um.get("output_tokens")
        out["cache_read"] = um.get("cache_read_input_tokens")
        out["cache_create"] = um.get("cache_creation_input_tokens")
        out["cost_known"] = isinstance(ev.get("total_cost_usd"), (int, float))
        return out
    except Exception:                                        # noqa: BLE001
        return out


def is_fixture_name(name: Any) -> bool:
    """STRICT: a generated failfix basename and nothing else — no separators,
    no parent references, no other shape. This is what a RECORD's `fixture`
    field is checked against before any resolution."""
    return isinstance(name, str) and bool(_FIXTURE_RE.match(name))


def fixture_name(path: Any) -> str | None:
    """The basename of the path `failfix.record` RETURNED (the site's own
    write), only when it is a generated name. Site-side only; a reader
    uses `is_fixture_name` on the stored value."""
    if not path:
        return None
    base = os.path.basename(str(path))
    return base if _FIXTURE_RE.match(base) else None


def record_dir(root: str, org: str, node: str) -> str:
    return os.path.join(root, "turnlog", str(org), str(node))


# ------------------------------------------------------------------ reading


def load(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        rec = json.load(f)
    if not isinstance(rec, dict) or int(rec.get("schema") or 0) != SCHEMA:   # pyright: ignore[reportUnknownArgumentType]
        raise ValueError(f"turn record schema {rec.get('schema') if isinstance(rec, dict) else None!r}, expected {SCHEMA}")   # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
    return rec                                                # pyright: ignore[reportUnknownVariableType]


def list_records(root: str, org: str, node: str) -> list[str]:
    d = record_dir(root, org, node)
    try:
        return sorted(os.path.join(d, n) for n in os.listdir(d)
                      if _RECORD_RE.match(n) or _STUB_RE.match(n))
    except OSError:
        return []


def fixture_path(record_path: str, name: Any) -> str | None:
    """The failfix file a record names, resolved ONLY inside the sibling
    `failfix/<org>/<node>/` directory of the record's own `turnlog/` root.
    A name that is not a generated fixture name, or a resolution that leaves
    that directory, is refused (None)."""
    if not is_fixture_name(name):
        return None
    base = str(name)
    rd = os.path.dirname(os.path.abspath(record_path))
    node = os.path.basename(rd)
    org_d = os.path.dirname(rd)
    org = os.path.basename(org_d)
    root = os.path.dirname(org_d)
    if os.path.basename(root) != "turnlog":
        return None
    fd = os.path.abspath(os.path.join(os.path.dirname(root), "failfix", org, node))
    p = os.path.abspath(os.path.join(fd, base))
    if os.path.dirname(p) != fd:
        return None
    return p if os.path.isfile(p) else None


def summarize(rec: Mapping[str, Any]) -> dict[str, Any]:
    """What the EVENTS alone establish — never `outcome` or `end`.

    phase follows failfix.phase_of's table from the boundary events:
      admission     nothing was output, a boundary result was reached and it
                    carried a typed 401/402/429 (or codex_decide rejected)
      stream        output began, no boundary result, and the process exited
      result-error  a boundary result carried the error after output
      teardown      boundary clean, process exited nonzero
      unknown       everything else, INCLUDING an unfinalized or truncated
                    record whose gaps could hide the deciding event
    implied         what the events imply about the disposition: completed
                    (a clean boundary and no failure event after it), or one
                    of frozen/killed/abandoned/unrecoverable/interrupted
                    when the corresponding event is the LAST disposition-
                    bearing event, else unknown. `dispose`/`end` events are
                    ignored — copying them would be no derivation at all.
    evidence        "sufficient" | "insufficient" (partial or truncated or no
                    events): an insufficient summary asserts nothing and
                    drifts against nothing."""
    events = [e for e in (rec.get("events") or []) if isinstance(e, Mapping)]
    events = [e for e in events if e.get("kind") not in ("dispose", "end")]  # pyright: ignore[reportUnknownMemberType]
    partial = rec.get("partial") is True
    truncated = rec.get("truncated") is True
    insufficient = partial or truncated or not events
    kinds = [str(e.get("kind")) for e in events]  # pyright: ignore[reportUnknownMemberType]
    first_out = next((e for e in events if e.get("kind") == "first_output"), None)  # pyright: ignore[reportUnknownMemberType]
    started = first_out is not None or any(
        k in ("assistant", "codex_item", "agy_step") for k in kinds)
    boundary = next((e for e in events if e.get("kind") == "result"  # pyright: ignore[reportUnknownMemberType]
                     and e.get("boundary") is True), None)  # pyright: ignore[reportUnknownMemberType]
    exit_ev = next((e for e in events if e.get("kind") == "exit"), None)  # pyright: ignore[reportUnknownMemberType]
    codex_dec = next((e for e in events if e.get("kind") == "codex_decide"), None)  # pyright: ignore[reportUnknownMemberType]
    phase = "unknown"
    if not insufficient:
        typed = boundary.get("status") if boundary else None  # pyright: ignore[reportUnknownMemberType]
        if codex_dec is not None and codex_dec.get("rejected") is True:  # pyright: ignore[reportUnknownMemberType]
            phase = "admission"
        elif not started:
            if boundary and boundary.get("is_error") and typed in (401, 402, 429):  # pyright: ignore[reportUnknownMemberType]
                phase = "admission"
        elif boundary is None:
            if exit_ev is not None:
                phase = "stream"
        elif boundary.get("is_error") or typed is not None:  # pyright: ignore[reportUnknownMemberType]
            phase = "result-error"
        elif exit_ev is not None and (exit_ev.get("code") or 0) != 0:  # pyright: ignore[reportUnknownMemberType]
            phase = "teardown"
    implied = "unknown"
    if not insufficient:
        # the disposition the ORDER of disposition-bearing events implies —
        # the same precedence the sites dispose with: a kill stays a kill
        # even when the abandonment mail follows it; a freeze after a failed
        # status is the freeze; an owner branch names the class it claimed
        last: str | None = None
        killed = False
        for e in events:
            k = e.get("kind")  # pyright: ignore[reportUnknownMemberType]
            if k == "watchdog":
                killed, last = True, "killed"
            elif k == "freeze":
                last = "frozen"
            elif k == "abandon":
                last = "killed" if killed else "abandoned"
            elif k == "owner":
                br = e.get("branch")  # pyright: ignore[reportUnknownMemberType]
                if br == "unrecoverable":
                    last = "unrecoverable"
                elif br == "terminal" and e.get("handled") is not True:  # pyright: ignore[reportUnknownMemberType]
                    last = "failed"
                elif br == "account_switch":
                    last = "redriven"
            elif k == "interrupt":
                last = "interrupted"
            elif k in ("codex_status", "agy_status"):
                stv = e.get("status")  # pyright: ignore[reportUnknownMemberType]
                if stv == "completed":
                    last = "completed"
                elif stv == "interrupted":
                    last = "interrupted"
                elif stv == "failed":
                    last = "failed"
        if last is None and boundary is not None and not boundary.get("is_error"):  # pyright: ignore[reportUnknownMemberType]
            last = "completed"
        implied = last or "unknown"
    t_first = first_out.get("t_ms") if first_out else None  # pyright: ignore[reportUnknownMemberType]
    t_bound = boundary.get("t_ms") if boundary else None  # pyright: ignore[reportUnknownMemberType]
    seqs = [int(e.get("seq") or 0) for e in events]  # pyright: ignore[reportUnknownMemberType]
    ordered = all(b > a for a, b in zip(seqs, seqs[1:]))
    return {"phase": phase, "implied": implied,
            "evidence": "insufficient" if insufficient else "sufficient",
            "started": started, "boundary": boundary is not None,
            "first_output_ms": t_first, "boundary_ms": t_bound,
            "events": len(events), "ordered": ordered}


def drift(rec: Mapping[str, Any]) -> list[str]:
    """Names whose event-derived value disagrees with the recorded one. An
    insufficient summary yields NO drift — it asserts nothing."""
    s = summarize(rec)
    if s["evidence"] != "sufficient":
        return []
    out: list[str] = []
    if s["implied"] != "unknown" and rec.get("outcome") not in (None, "unknown") \
            and s["implied"] != rec.get("outcome"):
        out.append("outcome")
    if not s["ordered"]:
        out.append("order")
    return out
