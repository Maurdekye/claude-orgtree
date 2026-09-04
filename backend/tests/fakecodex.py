"""fakecodex — a scripted `codex app-server` impostor for hermetic tests.

The codex analog of fakecli.js: speaks just enough app-server NDJSON JSON-RPC
over stdio for backend/orgtree/codexrun.py to run a full turn against it, with
scenarios selected by FAKECODEX_SCENARIO:

    tool       (default) the model "calls" the first registered dynamic tool
               (server-request item/tool/call) and echoes the client's answer
               into its agent text — proves the round trip codexrun relies on
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

Env probe: whatever FAKECODEX_ENVPROBE names (comma-separated env keys) is
written as JSON to <cwd>/envprobe.json at turn start — how the suite proves
credential hygiene without the impostor ever seeing a real credential.

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


def server_request(method, params, timeout=10.0):
    global _next_server_id
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


def run_turn(thread_id, turn_id, dyn_tools):
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

    if SCENARIO == "replay":
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
    elif SCENARIO == "stall":
        # no tool call, no steer wait: the turn simply takes this long. The
        # supervisor's pump polls on its own clock, so whether it sees a
        # message posted during the stall is decided by CODEX_STEER_POLL
        # alone — which is what lets a test make the miss CERTAIN.
        time.sleep(float(os.environ.get("FAKECODEX_STALL_S", "1.0")))
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
            codex = {
                "limitId": "codex", "limitName": None,
                "primary": {"usedPercent": 12,
                            "windowDurationMins": 10080,
                            "resetsAt": 1_900_000_000},
                "secondary": None, "planType": "prolite",
                "rateLimitReachedType": None,
            }
            reply(rid, {
                "rateLimits": codex,
                "rateLimitsByLimitId": {
                    "codex": codex,
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
            dyn_tools = params.get("dynamicTools") or []
            reply(rid, {"thread": {"id": thread_id}})
        elif method == "thread/resume":
            thread_id = str(params.get("threadId") or thread_id)
            # the real server takes dynamicTools on resume too (measured,
            # probe_resume_dyntools.py) — mirror it, so a runner that stops
            # passing them on resume fails the tool scenario here first
            dyn_tools = params.get("dynamicTools") or []
            reply(rid, {"thread": {"id": thread_id}})
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
            if SCENARIO == "early_stream":
                # the whole turn on the wire BEFORE the reply — see the
                # scenario note at the top of this file
                run_turn(thread_id, turn_id, dyn_tools)
                reply(rid, {"turn": {"id": turn_id}})
            else:
                reply(rid, {"turn": {"id": turn_id}})
                threading.Thread(target=run_turn,
                                 args=(thread_id, turn_id, dyn_tools),
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
