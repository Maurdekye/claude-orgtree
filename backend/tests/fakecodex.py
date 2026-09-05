"""fakecodex — a scripted `codex app-server` impostor for hermetic tests.

The codex analog of fakecli.js: speaks just enough app-server NDJSON JSON-RPC
over stdio for backend/orgtree/codexrun.py to run a full turn against it, with
scenarios selected by FAKECODEX_SCENARIO:

    tool       (default) the model "calls" the first registered dynamic tool
               (server-request item/tool/call) and echoes the client's answer
               into its agent text — proves the round trip codexrun relies on
    approval   the turn asks for BOTH approvals the ⚙-rights seam decides —
               item/fileChange/requestApproval and
               item/commandExecution/requestApproval — and records what the
               client answered to FAKECODEX_APPROVALPROBE. The two come from
               different rules in `_approve`, so both are asked in one turn
    steer      the turn stalls until a turn/steer arrives (≤8s), then emits
               STEERED[<text>] BEFORE acknowledging the request and completes
               — notifications and responses use the real independent paths
    steer_refuse waits until turn/steer is in flight, emits turn/completed,
               and only THEN answers that request with a JSON-RPC error — the
               real `expectedTurnId` guard's answer when the turn ends between
               the supervisor's fetch and the app-server's acceptance.
               The refusal must leave NOTHING claimed: no durable steered row,
               no confirmed journal batch, and the whole carrier back on the
               queue so the NEXT turn delivers it exactly once
    slow_tool_then_steer (audit D2) the model calls the first dynamic tool
               and, WHILE that call is unanswered, a helper thread waits for a
               turn/steer and acknowledges it at once, emitting STEERED[…]
               first. The ack is therefore on the pipe behind the tool call:
               a client that answers tools on its reader thread cannot see
               it until the tool returns. Completes after the tool answers
    steer_ack_late (audit D3) the turn accepts a turn/steer — emits
               STEERED[…] immediately — but delays the JSON-RPC ack by
               FAKECODEX_ACK_DELAY_S (default 2.0) before completing. The
               provider ACCEPTED; only the acknowledgement is late. A client
               that reads its own timeout as a refusal re-delivers the text
    steer_ack_never (audit D3) accepts a turn/steer the same way, emits
               STEERED[…], NEVER acks it, and completes FAKECODEX_STALL_S
               (default 1.0) later — the outcome is unknowable from the wire
    tool_inflight_at_end the model calls the first dynamic tool and the turn
               completes WITHOUT waiting for the answer. The impostor keeps
               listening and writes the late answer (if any) to
               FAKECODEX_LATEPROBE — a turn boundary must not lose a tool
               result a worker is still producing
    delta_pause emits one short agent-message delta, then pauses long enough
                to prove the client's time-based live flush actually fires
    replay     the same `item/completed` is sent TWICE for one message and
               once more for one reasoning item — what a reconnecting or
               retrying app-server replays. One copy must reach the desk
    early_stream the whole turn — reasoning, delta, agent message, completion
                — is notified BEFORE `turn/start` is answered. JSON-RPC does
                not order notifications against responses, and the client
                reads them on a different thread from the one awaiting the
                reply, so this is the worst case a correct runner must
                survive: it makes the stream-before-commit race certain
                instead of merely likely (see test_codex_stream_order.py)
    interrupt  the turn stalls until turn/interrupt, then completes with
               status "interrupted"
    stall      the turn stays open for FAKECODEX_STALL_S seconds (default
               1.0) making no tool call and then completes on its own — a
               turn whose end is not caused by anything the supervisor
               sends. It is the deterministic shape of "the user's message
               arrived in the last two seconds of the turn": with the steer
               poll set longer than the stall, the pump never polls again
               and the carrier is exactly where the live coordinator lost
               one (test_midturn_mail_ingress.py, D-229)
    usage_limit (D-209) the turn ends the way a REAL subscription wall ends
               one, replayed from captured bytes — see below
    reserve_wall / plan_wall / both_wall (item 12, reserve-first luna) the
               wall is POOL-SPECIFIC: a turn/start whose `model` is
               `gpt-reserve` (reserve_wall, both_wall) or `gpt-5.6-luna`
               (plan_wall, both_wall) ends as `usage_limit` does — the
               serving pool's window at 100% on the wire under the UNNAMED
               `codex` id (measured 2026-09-05: a per-turn notification
               never names the pool; only the full board read does), then
               turn/completed status "failed" with the 0.153.3 schema's
               camelCase tag `usageLimitExceeded` — and a turn on the other
               model completes normally. RESERVE_RESET_IN / PLAN_RESET_IN
               set the two resets apart so a test can tell which one a
               freeze was timed from
    reserve_wall_after_output  as reserve_wall, but an agent message is
               emitted BEFORE the wall: the turn ran, so it must NOT be
               re-driven on the other pool
    reserve_disconnect  on `gpt-reserve` the turn fails with the schema's
               object-form `{"responseStreamDisconnected": {...}}` tag —
               an UNKNOWN outcome that must never be replayed
    reroute_direct / reroute_then_wall / reroute_unknown_then_wall  a
               `gpt-reserve` turn gets the schema's `model/rerouted`
               notification (onto `gpt-5.6-luna`, or onto a model no pool
               is known for) and then completes / meets the PLAN wall —
               see the note in `run_turn`; no live reroute has been
               observed, only the schema shape
    plan_updated (FR-17) three successive `turn/plan/updated` notifications
               on one turn: pending → in_progress → a two-step list with an
               explanation — only the LAST should be what a caller shows
    plan_cleared (FR-17) a real checklist, then an EXPLICIT empty one — the
               "cleared" case, distinct from a notification never arriving
    plan_wrong_ids (FR-17) one legitimate snapshot, then one on a foreign
               thread and one on a foreign turn of the real thread — both
               must be discarded, and must not overwrite the legitimate one
    plan_early (FR-17) the checklist notification goes out from inside
               `turn/start`'s own handler, before that request's reply is
               sent — the measured turn_id race a correct runner must
               buffer through rather than drop (see supervisor._on_plan)
    plan_missing_ids (FR-17) a real snapshot, then one with NO threadId/
               turnId at all — a missing required id is not evidence of
               ownership and must not be accepted as trivially current
    plan_null_plan (FR-17) a real snapshot, then one with valid ids but
               `plan: null` — malformed, required-field-missing, and must
               NOT read as the model's own explicit `[]` clear
    plan_long_step (FR-17) a step text past the 2000-char cap — must be
               flagged `truncated`, never silently cut with no trace
    plan_failed_turn / plan_interrupted_turn (FR-17) a checklist with an
               unfinished step, then the turn ends badly — nothing about
               that ending may mark the step done

Board probe: FAKECODEX_BOARD shapes `account/rateLimits/read` (the COMPLETE
board): "default" (no reserve bucket — the withdrawn-grant shape),
"reserve-ok" (a `gpt-reserve` bucket with room), "reserve-exhausted" (that
bucket at 100%, resetting in RESERVE_RESET_IN), "plan-exhausted" (the codex
bucket at 100%, reserve with room), "both-exhausted".

Model probe: FAKECODEX_MODELPROBE names a file that collects one JSON line
per thread/start and turn/start recording the `model` the client sent — how
test_luna_reserve_route.py proves which POOL each attempt went to.

Env probe: whatever FAKECODEX_ENVPROBE names (comma-separated env keys) is
written as JSON to <cwd>/envprobe.json at turn start — how the suite proves
credential hygiene without the impostor ever seeing a real credential.

Sandbox probe: FAKECODEX_SANDBOXPROBE names a file that collects one JSON
line per thread/start and thread/resume recording the `sandbox` the client
sent — how test_codex_sandbox_mode.py proves the OS privilege level on the
wire, for the FIRST turn and every resumed one (see `sandbox_probe`).

Invoked as `python fakecodex.py app-server` (codexrun passes an argv head).
"""
import json
import os
import sys
import threading
import time

SCENARIO = os.environ.get("FAKECODEX_SCENARIO", "tool")
TURN_COUNT = 0

# ── the measured usage-limit ending (D-209) ──────────────────────────────────
# Transcribed from the Codex CLI's OWN rollout for the incident that started
# this work — cache-structural, thread 01a0547f-6578-7eb1-b025-74b8250b8ef0,
# 2026-08-30T22:41:41Z, in ~/.codex/sessions/2026/08/31/. Three facts about the
# real ending, all of which the fixture reproduces and each of which the
# production code got wrong in its own way:
#
#   · there is NO `turn/failed` notification. codex-cli 0.150.1 does not have
#     one — the literal string is absent from the binary. The wall arrives as
#     `turn/completed` carrying status "failed" and a `turn.error`.
#   · the reason is ON THE WIRE, not on stderr. stderr was empty.
#   · the machine-readable reset comes on a rate-limit notification 298 ms
#     EARLIER, and a SECOND, useless snapshot lands after it. That ordering is
#     not incidental: last-wins retention kept the useless one.
#
# `resetsAt` is stamped relative to now rather than transcribed (the captured
# value, 1788680032, is a real instant that will fall into the past), but the
# 10080-minute window and the 100% exhaustion are verbatim.
LIMIT_MESSAGE = ("You've hit your usage limit. Visit "
                 "https://chatgpt.com/codex/settings/usage to purchase more "
                 "credits or try again at Sep 6th, 2026 10:33 AM.")
LIMIT_ERROR_INFO = "usage_limit_exceeded"
#: seconds from now for the exhausted window's resetsAt. Six days: inside the
#: 7-day window it belongs to, far outside anything a prose parser could invent
#: and far outside the 5-minute probe floor, so a test can tell the three apart.
LIMIT_RESET_IN = 6 * 24 * 3600
#: item 12 — the two pools' resets, deliberately different and both inside
#: the horizon: reserve four days out, the plan two days out
RESERVE_RESET_IN = 4 * 24 * 3600
PLAN_RESET_IN = 2 * 24 * 3600
RESERVE_MODEL = "gpt-reserve"
DIRECT_LUNA_MODEL = "gpt-5.6-luna"
#: the v2 schema's spelling (codex-cli 0.153.3, `CodexErrorInfo`), beside the
#: 0.150.1 specimen's `usage_limit_exceeded` above — the classifier takes both
LIMIT_ERROR_INFO_V2 = "usageLimitExceeded"

_out_lock = threading.Lock()
_requests: list[dict] = []
_responses: dict[int, dict] = {}
_next_server_id = 1000


def send(obj):
    with _out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def notify(method, params):
    send({"jsonrpc": "2.0", "method": method, "params": params})


def reply(rid, result):
    send({"jsonrpc": "2.0", "id": rid, "result": result})


def reply_error(rid, message, code=-32602):
    send({"jsonrpc": "2.0", "id": rid,
          "error": {"code": code, "message": message}})


_id_lock = threading.Lock()


def server_request(method, params, timeout=10.0):
    global _next_server_id
    with _id_lock:                   # tool_flood issues these concurrently
        rid = _next_server_id
        _next_server_id += 1
    send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    deadline = time.time() + timeout
    while time.time() < deadline:
        if rid in _responses:
            return _responses.pop(rid)
        time.sleep(0.01)
    return None


def wait_request(method, timeout=8.0):
    deadline = time.time() + timeout
    seen = 0
    while time.time() < deadline:
        while seen < len(_requests):
            r = _requests[seen]
            seen += 1
            if r.get("method") == method:
                return r
        time.sleep(0.01)
    return None


def sandbox_probe(method, params):
    """FAKECODEX_SANDBOXPROBE names a file that receives one JSON line per
    `thread/start` and `thread/resume`: the `sandbox` value the client put on
    the wire, and whether it sent the key at all.

    The OS sandbox mode is a security boundary, and the real app-server takes
    it on BOTH calls — measured against codex-cli 0.153.3, which answers a
    misspelt value on either with "unknown variant `…`, expected one of
    `read-only`, `workspace-write`, `danger-full-access`". A resumed thread
    does NOT inherit what it was born with; it comes back at the server's own
    default. So a runner that sends the mode on start and forgets it on resume
    silently runs every turn after an agent's first at the wrong privilege
    level, and this double has to be able to show that.

    Recorded as separate rows rather than last-wins: the defect is a
    DIFFERENCE between the two calls, which a single value cannot express.
    """
    path = os.environ.get("FAKECODEX_SANDBOXPROBE")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"method": method,
                            "present": "sandbox" in params,
                            "sandbox": params.get("sandbox")}) + "\n")


def _model_probe(method, model):
    path = os.environ.get("FAKECODEX_MODELPROBE")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"method": method, "model": model}) + "\n")


def _pool_wall(thread_id, turn_id, model):
    """The pool-specific wall (item 12): the exhausted bucket of the pool
    `model` names, then the failed completion. Mirrors `usage_limit`."""
    if model == RESERVE_MODEL:
        # ⚠ MEASURED 2026-09-05T01:20Z (live control, codex-cli 0.153.0): a
        # RESERVE turn's notification carries `limitId: "codex"` and NO
        # `limitName` — the reserve window's numbers under the generic id
        # (27% / the reserve weekly's resetsAt on that run). The pool is
        # told apart only by the numbers and by knowing which model the
        # turn was sent as. A wall on reserve therefore looks like THIS:
        notify("account/rateLimits/updated", {
            "rateLimits": {
                "limitId": "codex", "limitName": None,
                "primary": {"usedPercent": 100.0,
                            "windowDurationMins": 10080,
                            "resetsAt": int(time.time()) + RESERVE_RESET_IN},
                "secondary": None, "planType": "prolite",
                "rateLimitReachedType": "rate_limit_reached"}})
    else:
        notify("account/rateLimits/updated", {
            "rateLimits": {
                "limitId": "codex", "limitName": None,
                "primary": {"usedPercent": 100.0,
                            "windowDurationMins": 10080,
                            "resetsAt": int(time.time()) + PLAN_RESET_IN},
                "secondary": None, "planType": "prolite",
                "rateLimitReachedType": None}})
    notify("turn/completed", {
        "threadId": thread_id,
        "turn": {"id": turn_id, "status": "failed",
                 "error": {"message": LIMIT_MESSAGE,
                           "codexErrorInfo": LIMIT_ERROR_INFO_V2,
                           "additionalDetails": None}}})


def run_turn(thread_id, turn_id, dyn_tools, model=None):
    global TURN_COUNT
    TURN_COUNT += 1
    turn_number = TURN_COUNT
    notify("turn/started", {"threadId": thread_id, "turn": {"id": turn_id}})
    probe = os.environ.get("FAKECODEX_ENVPROBE", "")
    probe_path = os.environ.get("FAKECODEX_ENVPROBE_PATH", "envprobe.json")
    if probe:
        with open(probe_path, "w", encoding="utf-8") as f:
            json.dump({k: os.environ.get(k) for k in probe.split(",")}, f)
    def item_event(phase, item):
        now = int(time.time() * 1000)
        notify(f"item/{phase}", {
            "threadId": thread_id, "turnId": turn_id, "item": item,
            ("startedAtMs" if phase == "started" else "completedAtMs"): now})

    def agent_message(iid, text):
        base = {"id": iid, "type": "agentMessage", "text": ""}
        item_event("started", base)
        notify("item/agentMessage/delta", {
            "threadId": thread_id, "turnId": turn_id,
            "itemId": iid, "delta": text})
        item_event("completed", {**base, "text": text})

    # item 12, parent review 2026-09-05: the server REROUTES a reserve turn.
    # `model/rerouted` is the 0.153.3 schema's notification {fromModel,
    # toModel, reason, threadId, turnId}; its shape is read from the schema,
    # its timing (before any item) is this fixture's choice — no live
    # reroute has been observed. Three endings:
    #   reroute_direct          → onto gpt-5.6-luna, then completes normally
    #   reroute_then_wall       → onto gpt-5.6-luna, then the PLAN wall
    #   reroute_unknown_then_wall → onto a model no pool is known for, then
    #                             a wall — attribution must stay unknown
    if SCENARIO.startswith("reroute") and model == RESERVE_MODEL:
        to = ("gpt-9-mystery" if SCENARIO == "reroute_unknown_then_wall"
              else DIRECT_LUNA_MODEL)
        notify("model/rerouted", {
            "threadId": thread_id, "turnId": turn_id,
            "fromModel": RESERVE_MODEL, "toModel": to,
            "reason": "fake: reserve pool unavailable for this request"})
        if SCENARIO in ("reroute_then_wall", "reroute_unknown_then_wall"):
            # the wall of the pool that SERVED: the plan's reset, under the
            # unnamed id, exactly as a direct turn's wall arrives
            _pool_wall(thread_id, turn_id, DIRECT_LUNA_MODEL)
            return
        model = DIRECT_LUNA_MODEL          # served direct; complete below

    # item 12: is THIS attempt's model the one the scenario walls? Decided
    # up front because a walled request meets the wall FIRST — no preamble
    # message, no item, nothing ran (the shape a rejected request has on the
    # wire). `reserve_wall_after_output` deliberately keeps the preamble.
    pool_walled = (SCENARIO in ("reserve_wall", "plan_wall", "both_wall",
                                "reserve_wall_after_output",
                                "reserve_disconnect")
                   and ((model == RESERVE_MODEL and SCENARIO != "plan_wall")
                        or (model == DIRECT_LUNA_MODEL
                            and SCENARIO in ("plan_wall", "both_wall"))))
    if pool_walled and SCENARIO != "reserve_wall_after_output":
        pass                       # the wall is the first thing the request meets
    elif SCENARIO == "replay":
        # the SAME completion twice, ids and all — a reconnecting or retrying
        # app-server replaying what it already sent. Both the journal and the
        # live tail must end up with ONE copy, or the agent's answer stands on
        # the desk twice for the rest of the turn.
        think = {"id": "think-replay", "type": "reasoning",
                 "summary": [{"text": "thinking once"}]}
        base = {"id": "msg-replay", "type": "agentMessage",
                "text": "said exactly once"}
        item_event("completed", think)
        item_event("started", {**base, "text": ""})
        item_event("completed", base)
        item_event("completed", base)        # the replay…
        item_event("completed", think)       # …of both kinds
    elif SCENARIO == "early_stream":
        # every VISIBLE kind the codex leg can emit — a thought row, a token
        # delta, and a durable text row — all of them before the caller can
        # possibly have seen `turn/start`'s reply
        item_event("completed", {"id": "think-early", "type": "reasoning",
                                 "summary": [{"text": "planning the answer"}]})
        base = {"id": "msg-early", "type": "agentMessage", "text": ""}
        item_event("started", base)
        notify("item/agentMessage/delta", {
            "threadId": thread_id, "turnId": turn_id,
            "itemId": "msg-early", "delta": "answering before the reply"})
        item_event("completed", {**base, "text": "answering before the reply"})
    elif SCENARIO == "delta_pause":
        base = {"id": "msg-paused", "type": "agentMessage", "text": ""}
        item_event("started", base)
        notify("item/agentMessage/delta", {
            "threadId": thread_id, "turnId": turn_id,
            "itemId": "msg-paused", "delta": "short live fragment"})
        # The supervisor's latency target is 120 ms. Keep the item open well
        # beyond that so the test cannot pass via the item/completed flush.
        time.sleep(0.45)
        item_event("completed", {**base, "text": "short live fragment"})
    elif SCENARIO == "plan_updated":
        # FR-17: successive snapshots on the SAME turn — pending →
        # in_progress → a two-step list carrying an explanation. Only the
        # LAST one should be what a caller ends up showing.
        def _plan(steps, explanation=None):
            notify("turn/plan/updated", {
                "threadId": thread_id, "turnId": turn_id,
                "explanation": explanation, "plan": steps})
        _plan([{"step": "a", "status": "pending"}])
        _plan([{"step": "a", "status": "inProgress"}])
        _plan([{"step": "a", "status": "completed"},
               {"step": "b", "status": "pending"}], "steady progress")
    elif SCENARIO == "plan_cleared":
        # a real checklist, then an EXPLICIT empty one — "cleared", not "the
        # notification never arrived" (that distinction is the whole point)
        notify("turn/plan/updated", {
            "threadId": thread_id, "turnId": turn_id, "explanation": None,
            "plan": [{"step": "a", "status": "pending"}]})
        notify("turn/plan/updated", {
            "threadId": thread_id, "turnId": turn_id, "explanation": None,
            "plan": []})
    elif SCENARIO == "plan_wrong_ids":
        # ONE legitimate snapshot, then two that are not this turn's to
        # report — a foreign thread, and a foreign turn on the REAL thread.
        # Both must be discarded: the legitimate snapshot must survive them,
        # not be overwritten by whichever arrived last.
        notify("turn/plan/updated", {
            "threadId": thread_id, "turnId": turn_id, "explanation": None,
            "plan": [{"step": "real", "status": "pending"}]})
        notify("turn/plan/updated", {
            "threadId": "fake-thread-elsewhere", "turnId": turn_id,
            "explanation": None,
            "plan": [{"step": "WRONG THREAD", "status": "completed"}]})
        notify("turn/plan/updated", {
            "threadId": thread_id, "turnId": "fake-turn-elsewhere",
            "explanation": None,
            "plan": [{"step": "WRONG TURN", "status": "completed"}]})
    elif SCENARIO == "plan_early":
        # THE RACE (see supervisor._on_plan's docstring): this notification
        # goes out from inside `turn/start`'s own handler, before that
        # request's reply is even sent — the caller cannot possibly have
        # `turn.turn_id` set yet. A runner that rejects on that technicality
        # drops real data instead of buffering it.
        notify("turn/plan/updated", {
            "threadId": thread_id, "turnId": turn_id, "explanation": None,
            "plan": [{"step": "early", "status": "pending"}]})
        agent_message("msg-early-plan", "done")
    elif SCENARIO == "plan_missing_ids":
        # ONE legitimate snapshot, then a MALFORMED notification — no
        # threadId/turnId at all, which the schema marks `required`. Absence
        # of identity is not evidence of ownership; a runner that treats a
        # missing id as "trivially this turn's own" accepts a notification
        # it has no actual basis for attributing to this turn.
        notify("turn/plan/updated", {
            "threadId": thread_id, "turnId": turn_id, "explanation": None,
            "plan": [{"step": "real", "status": "pending"}]})
        notify("turn/plan/updated", {
            "explanation": None,
            "plan": [{"step": "NO IDENTITY AT ALL", "status": "completed"}]})
    elif SCENARIO == "plan_null_plan":
        # ONE legitimate snapshot, then a notification with valid ids but a
        # MALFORMED `plan` (missing/null, not the schema's array). This must
        # never read as the model's own explicit `[]` clear — the schema
        # marks `plan` required too, so a missing one is a garbled
        # notification, not a checklist the model chose to empty.
        notify("turn/plan/updated", {
            "threadId": thread_id, "turnId": turn_id, "explanation": None,
            "plan": [{"step": "real", "status": "pending"}]})
        notify("turn/plan/updated", {
            "threadId": thread_id, "turnId": turn_id, "explanation": None,
            "plan": None})
    elif SCENARIO == "plan_long_step":
        # a step text past the cap — must be flagged, not silently cut with
        # no trace (review finding, 2026-09-05)
        notify("turn/plan/updated", {
            "threadId": thread_id, "turnId": turn_id, "explanation": None,
            "plan": [{"step": "x" * 2500, "status": "pending"}]})
    else:
        agent_message("msg-working", "working… ")
    if SCENARIO == "tool" and dyn_tools:
        tool = dyn_tools[0].get("name", "tool0")
        tool_item = {"id": "c1", "type": "dynamicToolCall",
                     "tool": tool, "arguments": {"message": "from-fake"},
                     "status": "inProgress", "success": None,
                     "contentItems": None, "durationMs": None,
                     "namespace": None}
        item_event("started", tool_item)
        ans = server_request("item/tool/call", {
            "threadId": thread_id, "turnId": turn_id, "callId": "c1",
            "tool": tool, "arguments": {"message": "from-fake"}})
        items = ((ans or {}).get("result") or {}).get("contentItems") or []
        text = items[0].get("text", "") if items else "NO ANSWER"
        item_event("completed", {**tool_item, "status": "completed",
                                  "success": True, "contentItems": items})
        agent_message("msg-tool", f"tool said: {text}")
    elif SCENARIO == "approval":
        # The ⚙-RIGHTS SEAM. The app-server ASKS before a file change or a
        # shell command and orgtree's `_codex_leg._approve` answers. Nothing
        # in this repo exercised that callback before 2026-09-04, so a
        # permission that lived only there was enforced by an untested line —
        # which is how `plan` came to be approved like acceptEdits.
        #
        # Both kinds are asked in one turn on purpose: the file answer and the
        # command answer come from different rules, and a scenario that asked
        # only one could not show them diverging.
        decisions = []
        for meth, extra in (
                ("item/fileChange/requestApproval",
                 {"callId": "a-file", "fileChange": {"path": "probe.txt"}}),
                ("item/commandExecution/requestApproval",
                 {"callId": "a-cmd", "command": "echo probe"})):
            ans = server_request(meth, {"threadId": thread_id,
                                        "turnId": turn_id, **extra})
            decisions.append({
                "method": meth,
                # None, not "decline": a client that never answered and one
                # that answered "decline" are different failures
                "decision": ((ans or {}).get("result") or {}).get("decision")})
        probe = os.environ.get("FAKECODEX_APPROVALPROBE")
        if probe:
            with open(probe, "w", encoding="utf-8") as f:
                json.dump(decisions, f)
        agent_message("msg-approval", json.dumps(decisions))
    elif SCENARIO == "steer":
        st = wait_request("turn/steer")
        if st:
            text = ""
            for part in (st.get("params", {}).get("input") or []):
                text += str(part.get("text", ""))
            # Adversarial but protocol-legal: the reader receives the answer
            # notification before the request waiter receives acceptance.
            # The supervisor must not expose or journal this above the steer.
            agent_message("msg-steer", f"STEERED[{text}]")
            reply(st["id"], {"turnId": turn_id})
        else:
            agent_message("msg-nosteer", "no steer arrived")
    elif SCENARIO == "steer_refuse":
        # Force the exact production race instead of merely hoping the 2 s
        # poll loses it: the request is already in flight (and therefore owns
        # a drained carrier), then the turn boundary arrives, then the guard
        # refuses the stale expectedTurnId. The supervisor must wait for that
        # ownership decision before it tears the turn down.
        st = wait_request("turn/steer")
        if st:
            notify("turn/completed", {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": "completed"}})
            time.sleep(0.05)
            reply_error(st["id"], "no such active turn: fake-turn-0001")
            return
        agent_message("msg-nosteer", "no steer arrived")
    elif SCENARIO == "slow_tool_then_steer":
        # ── audit D2 ── the steer ack sits BEHIND an unanswered tool call.
        # The helper thread is the app-server's own concurrency: a real
        # server keeps reading stdin and answering while a tool is pending.
        tool = (dyn_tools[0].get("name", "tool0") if dyn_tools else "tool0")
        steered = []

        def _ack_steer():
            st = wait_request("turn/steer", timeout=20.0)
            if st:
                text = "".join(str(p.get("text", ""))
                               for p in (st.get("params", {}).get("input") or []))
                agent_message("msg-steer", f"STEERED[{text}]")
                reply(st["id"], {"turnId": turn_id})
                steered.append(text)
        helper = threading.Thread(target=_ack_steer, daemon=True)
        helper.start()
        tool_item = {"id": "c-slow", "type": "dynamicToolCall",
                     "tool": tool, "arguments": {"message": "slow"},
                     "status": "inProgress", "success": None,
                     "contentItems": None, "durationMs": None,
                     "namespace": None}
        item_event("started", tool_item)
        ans = server_request("item/tool/call", {
            "threadId": thread_id, "turnId": turn_id, "callId": "c-slow",
            "tool": tool, "arguments": {"message": "slow"}}, timeout=60.0)
        items = ((ans or {}).get("result") or {}).get("contentItems") or []
        text = items[0].get("text", "") if items else "NO ANSWER"
        item_event("completed", {**tool_item, "status": "completed",
                                  "success": True, "contentItems": items})
        helper.join(timeout=0.5)
        agent_message("msg-tool", f"tool said: {text}; steered={len(steered)}")
    elif SCENARIO in ("steer_ack_late", "steer_ack_never"):
        # ── audit D3 ── ACCEPTED, but the acknowledgement is late or lost.
        st = wait_request("turn/steer")
        if st:
            text = "".join(str(p.get("text", ""))
                           for p in (st.get("params", {}).get("input") or []))
            # the provider has the text: this is what "accepted" means
            agent_message("msg-steer", f"STEERED[{text}]")
            if SCENARIO == "steer_ack_late":
                time.sleep(float(os.environ.get("FAKECODEX_ACK_DELAY_S", "2.0")))
                reply(st["id"], {"turnId": turn_id})
            else:
                # never acked; the turn ends on its own clock — or on a
                # turn/interrupt, the way a real one does (C.3)
                irr = wait_request("turn/interrupt", timeout=float(
                    os.environ.get("FAKECODEX_STALL_S", "1.0")))
                if irr:
                    reply(irr["id"], {})
                    notify("turn/completed", {
                        "threadId": thread_id,
                        "turn": {"id": turn_id, "status": "interrupted",
                                 "error": None}})
                    return
        else:
            agent_message("msg-nosteer", "no steer arrived")
    elif SCENARIO == "steer_ack_late_error":
        # accepts (STEERED echoed), then FAKECODEX_ACK_DELAY_S later answers
        # the request with an INTERNAL error — the ambiguous late reply: an
        # internal failure can follow the append, so the client must keep
        # the outcome unknown, never read it as a refusal (review 2026-09-05)
        st = wait_request("turn/steer")
        if st:
            text = "".join(str(p.get("text", ""))
                           for p in (st.get("params", {}).get("input") or []))
            agent_message("msg-steer", f"STEERED[{text}]")
            time.sleep(float(os.environ.get("FAKECODEX_ACK_DELAY_S", "2.0")))
            reply_error(st["id"], "Persistence failed after expectedTurnId "
                                  "validation", code=-32603)
            time.sleep(0.5)
        else:
            agent_message("msg-nosteer", "no steer arrived")
    elif SCENARIO == "steer_ack_wrong_turn":
        # a prompt JSON-RPC result that does NOT name the steered turn:
        # FAKECODEX_ACK_TURNID (default "some-other-turn"; empty = omit the
        # field). Not an acknowledgement of THIS input → unknown.
        st = wait_request("turn/steer")
        if st:
            tid = os.environ.get("FAKECODEX_ACK_TURNID", "some-other-turn")
            reply(st["id"], {"turnId": tid} if tid else {})
        else:
            agent_message("msg-nosteer", "no steer arrived")
    elif SCENARIO == "steer_ack_after_end":
        # accepts, COMPLETES THE TURN, and only then (FAKECODEX_ACK_DELAY_S
        # later) acks — the reply lands after the client's turn-end decision.
        # Only a parked (warm) process can still deliver it.
        st = wait_request("turn/steer")
        if st:
            text = "".join(str(p.get("text", ""))
                           for p in (st.get("params", {}).get("input") or []))
            agent_message("msg-steer", f"STEERED[{text}]")
            notify("turn/completed", {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": "completed", "error": None}})
            time.sleep(float(os.environ.get("FAKECODEX_ACK_DELAY_S", "2.0")))
            reply(st["id"], {"turnId": turn_id})
            return
        agent_message("msg-nosteer", "no steer arrived")
    elif SCENARIO == "tool_replay_id":
        # the SAME server request id twice (a reconnecting server replaying
        # its outstanding request): the tool must run ONCE and both copies
        # must be answered — the second from the record.
        tool = (dyn_tools[0].get("name", "tool0") if dyn_tools else "tool0")
        params = {"threadId": thread_id, "turnId": turn_id, "callId": "c-dup",
                  "tool": tool, "arguments": {"message": "dup"}}
        rid = _next_server_id
        ans = server_request("item/tool/call", params)
        send({"jsonrpc": "2.0", "id": rid, "method": "item/tool/call",
              "params": params})                      # the replay, same id
        deadline = time.time() + 5.0
        again = None
        while time.time() < deadline and again is None:
            again = _responses.pop(rid, None)
            time.sleep(0.01)
        t1 = (((ans or {}).get("result") or {}).get("contentItems") or [{}])[0].get("text")
        t2 = (((again or {}).get("result") or {}).get("contentItems") or [{}])[0].get("text")
        agent_message("msg-dup", f"first={t1!r} replay={t2!r}")
    elif SCENARIO == "tool_flood":
        # more concurrent tool calls than the client's workers + queue: the
        # overflow must be answered AT ONCE with an explicit overload while
        # the reader keeps acknowledging a steer sent in the middle of it.
        tool = (dyn_tools[0].get("name", "tool0") if dyn_tools else "tool0")
        n = int(os.environ.get("FAKECODEX_FLOOD_N", "25"))
        answers = [None] * n

        def _one(i):
            answers[i] = server_request("item/tool/call", {
                "threadId": thread_id, "turnId": turn_id, "callId": f"c-f{i}",
                "tool": tool, "arguments": {"message": f"flood-{i}"}},
                timeout=30.0)
        ths = [threading.Thread(target=_one, args=(i,), daemon=True)
               for i in range(n)]
        for t in ths:
            t.start()
        st = wait_request("turn/steer", timeout=10.0)
        steer_at = time.time()
        if st:
            reply(st["id"], {"turnId": turn_id})
        for t in ths:
            t.join(35)
        texts = [(((a or {}).get("result") or {}).get("contentItems") or [{}])[0].get("text")
                 for a in answers]
        overload = sum(1 for t in texts if t and "overload" in t)
        ran = sum(1 for t in texts if t and t.startswith("ran"))
        agent_message("msg-flood", f"flood: n={n} ran={ran} overload={overload} "
                                   f"unanswered={texts.count(None)} "
                                   f"steer={'acked' if st else 'none'}")
    elif SCENARIO == "tool_inflight_at_end":
        # ── a tool call the turn does not wait for ──
        # (the late answer, if any, is recorded by main()'s response branch
        # into FAKECODEX_LATEPROBE the instant it is read)
        tool = (dyn_tools[0].get("name", "tool0") if dyn_tools else "tool0")
        threading.Thread(target=server_request, args=(
            "item/tool/call", {"threadId": thread_id, "turnId": turn_id,
                               "callId": "c-inflight", "tool": tool,
                               "arguments": {"message": "inflight"}}),
            kwargs={"timeout": 8.0}, daemon=True).start()
        time.sleep(0.05)      # the request is on the wire before the end
    elif SCENARIO == "stall":
        # no tool call, no steer wait: the turn simply takes this long. The
        # supervisor's pump polls on its own clock, so whether it sees a
        # message posted during the stall is decided by CODEX_STEER_POLL
        # alone — which is what lets a test make the miss CERTAIN.
        time.sleep(float(os.environ.get("FAKECODEX_STALL_S", "1.0")))
    elif SCENARIO in ("reserve_wall", "plan_wall", "both_wall",
                      "reserve_wall_after_output", "reserve_disconnect"):
        walled = pool_walled
        if walled and SCENARIO == "reserve_disconnect":
            # the schema's object-form tag: a transport failure mid-turn
            notify("turn/completed", {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": "failed",
                         "error": {"message": "stream disconnected",
                                   "codexErrorInfo": {
                                       "responseStreamDisconnected": {
                                           "httpStatusCode": None}},
                                   "additionalDetails": None}}})
            return
        if walled:
            # (`reserve_wall_after_output` already emitted the preamble
            # message above — that IS the output the wall must not erase)
            _pool_wall(thread_id, turn_id, model)
            return
        # the other pool serves: fall through to the ordinary completion
    elif SCENARIO == "usage_limit":
        # the exhausted bucket FIRST, carrying the only machine reset anyone
        # will ever get for this wall…
        notify("account/rateLimits/updated", {
            "rateLimits": {
                "limitId": "codex", "limitName": None,
                "primary": {"usedPercent": 100.0,
                            "windowDurationMins": 10080,
                            "resetsAt": int(time.time()) + LIMIT_RESET_IN},
                "secondary": None, "planType": "prolite",
                "rateLimitReachedType": None}})
        # …then the second, empty one that a last-wins field would keep
        notify("account/rateLimits/updated", {
            "rateLimits": {"limitId": "premium", "limitName": None,
                           "primary": None, "secondary": None,
                           "planType": "prolite",
                           "rateLimitReachedType": None}})
        # …and the wall itself: a COMPLETED notification whose status is
        # "failed". Nothing is written to stderr, exactly as measured.
        notify("turn/completed", {
            "threadId": thread_id,
            "turn": {"id": turn_id, "status": "failed",
                     "error": {"message": LIMIT_MESSAGE,
                               "codexErrorInfo": LIMIT_ERROR_INFO,
                               "additionalDetails": None}}})
        return
    elif SCENARIO == "turn_failed_notification":
        # the shape codex-cli 0.150.1 does NOT send, kept live so the retained
        # `turn/failed` branch cannot rot into a lie about a lane we claim to
        # handle. Same wall, announced the other way.
        notify("turn/failed", {
            "threadId": thread_id,
            "turn": {"id": turn_id,
                     "error": {"message": LIMIT_MESSAGE,
                               "codexErrorInfo": LIMIT_ERROR_INFO}}})
        return
    elif SCENARIO == "plain_failure":
        # a failed turn that is NOT a limit — the containment control. It must
        # surface as an ordinary turn error and must NOT freeze anything.
        notify("turn/completed", {
            "threadId": thread_id,
            "turn": {"id": turn_id, "status": "failed",
                     "error": {"message": "the sandbox denied a write",
                               "codexErrorInfo": "sandbox_error"}}})
        return
    elif SCENARIO in ("plan_failed_turn", "plan_interrupted_turn"):
        # FR-17: a checklist with an unfinished step, then the turn ends
        # badly. Nothing about that ending may mark the step done — the
        # checklist just stops updating, honestly, at whatever it last said.
        notify("turn/plan/updated", {
            "threadId": thread_id, "turnId": turn_id, "explanation": None,
            "plan": [{"step": "not done yet", "status": "inProgress"}]})
        if SCENARIO == "plan_failed_turn":
            notify("turn/completed", {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": "failed",
                         "error": {"message": "planted failure — FR-17's "
                                              "checklist-survives-failure "
                                              "control"}}})
        else:
            notify("turn/completed", {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": "interrupted",
                         "error": None}})
        return
    elif SCENARIO == "interrupt":
        irr = wait_request("turn/interrupt")
        if irr:
            reply(irr["id"], {})
            notify("thread/tokenUsage/updated", {
                "threadId": thread_id,
                "tokenUsage": {"total": {"totalTokens": 5}}})
            notify("turn/completed", {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": "interrupted",
                         "error": None}})
            return
    if SCENARIO == "cumulative_usage":
        # Exact first two snapshots measured on nowindow-spawn.  Turn two's
        # full counter prices to $3.660064, but its NEW work is only $0.148005.
        first = {"totalTokens": 6057714, "inputTokens": 6032418,
                 "cachedInputTokens": 5867648, "outputTokens": 25296,
                 "reasoningOutputTokens": 7126}
        if turn_number == 1:
            notify("thread/tokenUsage/updated", {
                "threadId": thread_id,
                "tokenUsage": {"last": dict(first), "total": dict(first)}})
        elif turn_number == 2:
            interim = {"totalTokens": 6158880, "inputTokens": 6133484,
                       "cachedInputTokens": 5968128, "outputTokens": 25396,
                       "reasoningOutputTokens": 7166}
            notify("thread/tokenUsage/updated", {
                "threadId": thread_id,
                "tokenUsage": {
                    "last": {"totalTokens": 101166, "inputTokens": 101066,
                             "cachedInputTokens": 100480,
                             "outputTokens": 100,
                             "reasoningOutputTokens": 40},
                    "total": interim}})
            notify("thread/tokenUsage/updated", {
                "threadId": thread_id,
                "tokenUsage": {
                    "last": {"totalTokens": 211524, "inputTokens": 210879,
                             "cachedInputTokens": 209152,
                             "outputTokens": 645,
                             "reasoningOutputTokens": 80},
                    "total": {"totalTokens": 6370404,
                              "inputTokens": 6344363,
                              "cachedInputTokens": 6177280,
                              "outputTokens": 26041,
                              "reasoningOutputTokens": 7246}}})
        else:
            reset = {"totalTokens": 120, "inputTokens": 100,
                     "cachedInputTokens": 80, "outputTokens": 20,
                     "reasoningOutputTokens": 3}
            notify("thread/tokenUsage/updated", {
                "threadId": thread_id,
                "tokenUsage": {"last": dict(reset), "total": dict(reset)}})
        notify("turn/completed", {"threadId": thread_id,
                                  "turn": {"id": turn_id,
                                           "status": "completed",
                                           "error": None}})
        return
    unit = {"totalTokens": 42, "inputTokens": 30,
            "cachedInputTokens": 10, "outputTokens": 12,
            "reasoningOutputTokens": 0}
    notify("thread/tokenUsage/updated", {
        "threadId": thread_id,
        # The real app-server reports a thread-cumulative `total` and one
        # request in `last`. A repeated literal total made resumed fake turns
        # claim that no new work happened under the real normalization rule.
        "tokenUsage": {"last": unit,
                       "total": {key: value * turn_number
                                 for key, value in unit.items()}}})
    notify("account/rateLimits/updated", {
        "rateLimits": {"limitId": "codex",
                       "primary": {"usedPercent": 1,
                                   "windowDurationMins": 10080}}})
    notify("turn/completed", {"threadId": thread_id,
                              "turn": {"id": turn_id, "status": "completed",
                                       "error": None}})


def run_compact(thread_id):
    """The native manual-compaction lifecycle: request acknowledgement is
    not completion; the turn and item events that follow are."""
    turn_id = "fake-compact-turn-0001"
    notify("turn/started", {"threadId": thread_id,
                            "turn": {"id": turn_id}})
    item = {"id": "fake-compact-item", "type": "contextCompaction"}
    notify("item/started", {"threadId": thread_id, "turnId": turn_id,
                            "item": item})
    if SCENARIO == "compact_fail":
        notify("turn/completed", {
            "threadId": thread_id,
            "turn": {"id": turn_id, "status": "failed",
                     "error": {"message": "planted compact failure"}}})
        return
    notify("item/completed", {"threadId": thread_id, "turnId": turn_id,
                              "item": item})
    notify("thread/tokenUsage/updated", {
        "threadId": thread_id,
        "tokenUsage": {
            "last": {"totalTokens": 50, "inputTokens": 44,
                     "cachedInputTokens": 20, "outputTokens": 6,
                     "reasoningOutputTokens": 0},
            "total": {"totalTokens": 50, "inputTokens": 44,
                      "cachedInputTokens": 20, "outputTokens": 6,
                      "reasoningOutputTokens": 0}}})
    notify("thread/compacted", {"threadId": thread_id,
                                "turnId": turn_id})
    notify("turn/completed", {"threadId": thread_id,
                              "turn": {"id": turn_id,
                                       "status": "completed", "error": None}})


def main():
    dyn_tools = []
    thread_id = os.environ.get("FAKECODEX_THREAD_ID", "fake-thread-0001")
    for raw in sys.stdin:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "method" not in msg:            # a response to OUR server-request
            if "id" in msg:
                _responses[int(msg["id"])] = msg
                late_probe = os.environ.get("FAKECODEX_LATEPROBE")
                if late_probe:
                    # written HERE, on the reader, not from a watcher thread:
                    # the client may kill this process right after answering
                    with open(late_probe, "a", encoding="utf-8") as f:
                        f.write(json.dumps(msg) + "\n")
            continue
        _requests.append(msg)
        method, rid = msg["method"], msg.get("id")
        params = msg.get("params") or {}
        # wire probe: append every method this impostor receives, in order —
        # how a suite proves WHAT was (and was not) sent during prewarm
        probe = os.environ.get("FAKECODEX_WIREPROBE")
        if probe:
            with open(probe, "a", encoding="utf-8") as f:
                f.write(json.dumps({"method": method, "pid": os.getpid()}) + "\n")
        if method == "initialize":
            # plantable prewarm faults: a server that never answers its
            # handshake, and one that dies on it
            mode = os.environ.get("FAKECODEX_INIT_MODE", "answer")
            if mode == "die":
                sys.exit(3)
            if mode == "mute":
                continue
            reply(rid, {"serverInfo": {"name": "fakecodex", "version": "0"}})
        elif method == "initialized":
            pass
        elif method == "mcpServerStatus/list":
            # a resolved runtime inventory, the shape codexrun paginates
            reply(rid, {"data": [{"name": "fakesrv",
                                  "tools": {"toolA": {}, "toolB": {}}}]})
        elif method == "account/rateLimits/read":
            # The real 0.150.1 protocol's full snapshot: one canonical bucket
            # plus a named/model bucket with two windows.  `codex` is repeated
            # in the map on purpose — the production normalizer must dedupe it.
            board = os.environ.get("FAKECODEX_BOARD", "default")
            plan_out = board in ("plan-exhausted", "both-exhausted")
            codex = {
                "limitId": "codex", "limitName": None,
                "primary": {"usedPercent": 100 if plan_out else 12,
                            "windowDurationMins": 10080,
                            "resetsAt": (int(time.time()) + PLAN_RESET_IN
                                         if plan_out else 1_900_000_000)},
                "secondary": None, "planType": "prolite",
                "rateLimitReachedType": None,
            }
            # item 12: the reserve pool's bucket, named after the MODEL —
            # the shape measured 2026-09-03 (`codex_limits.grants`)
            reserve_rows = {}
            if board in ("reserve-ok", "reserve-exhausted", "plan-exhausted",
                         "both-exhausted"):
                res_out = board in ("reserve-exhausted", "both-exhausted")
                reserve_rows["base_model_inference"] = {
                    "limitId": "base_model_inference",
                    "limitName": RESERVE_MODEL,
                    "primary": {"usedPercent": 100 if res_out else 8,
                                "windowDurationMins": 10080,
                                "resetsAt": (int(time.time()) + RESERVE_RESET_IN
                                             if res_out else 1_900_000_300)},
                    "secondary": None, "planType": "prolite",
                    "rateLimitReachedType": ("rate_limit_reached"
                                             if res_out else None),
                }
            reply(rid, {
                "rateLimits": codex,
                "rateLimitsByLimitId": {
                    "codex": codex,
                    **reserve_rows,
                    "codex_spark": {
                        "limitId": "codex_spark",
                        "limitName": "GPT-Spark",
                        "primary": {"usedPercent": 81,
                                    "windowDurationMins": 300,
                                    "resetsAt": 1_900_000_100},
                        "secondary": {"usedPercent": 93,
                                      "windowDurationMins": 10080,
                                      "resetsAt": 1_900_000_200},
                        "planType": "prolite",
                        "rateLimitReachedType": None,
                    },
                },
                "rateLimitResetCredits": {"availableCount": 0,
                                           "credits": []},
            })
        elif method == "thread/start":
            sandbox_probe(method, params)
            _model_probe(method, params.get("model"))
            dyn_tools = params.get("dynamicTools") or []
            # the 0.153.3 schema's response carries the thread's `model`
            # (ThreadStartResponse.model) — the provider-reported echo
            reply(rid, {"thread": {"id": thread_id},
                        "model": params.get("model") or "fake-default"})
        elif method == "thread/resume":
            sandbox_probe(method, params)
            thread_id = str(params.get("threadId") or thread_id)
            # the real server takes dynamicTools on resume too (measured,
            # probe_resume_dyntools.py) — mirror it, so a runner that stops
            # passing them on resume fails the tool scenario here first
            dyn_tools = params.get("dynamicTools") or []
            reply(rid, {"thread": {"id": thread_id},
                        "model": params.get("model") or "fake-resumed"})
        elif method == "thread/fork":
            thread_id = os.environ.get("FAKECODEX_FORK_ID",
                                       "fake-forked-thread-0002")
            reply(rid, {"thread": {"id": thread_id}})
        elif method == "thread/compact/start":
            reply(rid, {})
            threading.Thread(target=run_compact, args=(thread_id,),
                             daemon=True).start()
        elif method == "turn/start":
            input_probe = os.environ.get("FAKECODEX_INPUTPROBE")
            if input_probe:
                with open(input_probe, "w", encoding="utf-8") as f:
                    json.dump(params.get("input") or [], f)
            turn_id = "fake-turn-0001"
            turn_model = params.get("model")
            _model_probe(method, turn_model)
            if SCENARIO in ("early_stream", "plan_early"):
                # the whole turn on the wire BEFORE the reply — see the
                # scenario note at the top of this file
                run_turn(thread_id, turn_id, dyn_tools, turn_model)
                reply(rid, {"turn": {"id": turn_id}})
            else:
                reply(rid, {"turn": {"id": turn_id}})
                threading.Thread(target=run_turn,
                                 args=(thread_id, turn_id, dyn_tools,
                                       turn_model),
                                 daemon=True).start()
        elif method in ("turn/steer", "turn/interrupt"):
            pass                            # the scenario thread answers it
        elif rid is not None:
            reply(rid, {})


def _spawn_orphan_child():
    """Fork a long-lived grandchild the way the real `codex app-server` forks
    its native engine + code-mode-host children. FAKECODEX_CHILD_PIDFILE gets
    the child's pid so a test can prove AppServerClient.close() reaps the
    whole TREE, not just this parent (the 2026-08-30 orphan-lock bug)."""
    import subprocess
    pidfile = os.environ.get("FAKECODEX_CHILD_PIDFILE")
    if not pidfile:
        return
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(600)"])
    with open(pidfile, "w", encoding="utf-8") as f:
        f.write(str(child.pid))


if __name__ == "__main__":
    _spawn_orphan_child()
    main()
