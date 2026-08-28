"""fakecodex — a scripted `codex app-server` impostor for hermetic tests.

The codex analog of fakecli.js: speaks just enough app-server NDJSON JSON-RPC
over stdio for backend/orgtree/codexrun.py to run a full turn against it, with
scenarios selected by FAKECODEX_SCENARIO:

    tool       (default) the model "calls" the first registered dynamic tool
               (server-request item/tool/call) and echoes the client's answer
               into its agent text — proves the round trip codexrun relies on
    steer      the turn stalls until a turn/steer arrives (≤8s), then echoes
               STEERED[<text>] into the agent text and completes
    interrupt  the turn stalls until turn/interrupt, then completes with
               status "interrupted"

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
    notify("turn/started", {"threadId": thread_id, "turn": {"id": turn_id}})
    probe = os.environ.get("FAKECODEX_ENVPROBE", "")
    probe_path = os.environ.get("FAKECODEX_ENVPROBE_PATH", "envprobe.json")
    if probe:
        with open(probe_path, "w", encoding="utf-8") as f:
            json.dump({k: os.environ.get(k) for k in probe.split(",")}, f)
    notify("item/agentMessage/delta",
           {"threadId": thread_id, "delta": "working… "})
    if SCENARIO == "tool" and dyn_tools:
        tool = dyn_tools[0].get("name", "tool0")
        ans = server_request("item/tool/call", {
            "threadId": thread_id, "turnId": turn_id, "callId": "c1",
            "tool": tool, "arguments": {"message": "from-fake"}})
        items = ((ans or {}).get("result") or {}).get("contentItems") or []
        text = items[0].get("text", "") if items else "NO ANSWER"
        notify("item/agentMessage/delta",
               {"threadId": thread_id, "delta": f"tool said: {text}"})
    elif SCENARIO == "steer":
        st = wait_request("turn/steer")
        if st:
            reply(st["id"], {"turnId": turn_id})
            text = ""
            for part in (st.get("params", {}).get("input") or []):
                text += str(part.get("text", ""))
            notify("item/agentMessage/delta",
                   {"threadId": thread_id, "delta": f"STEERED[{text}]"})
        else:
            notify("item/agentMessage/delta",
                   {"threadId": thread_id, "delta": "no steer arrived"})
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
    notify("thread/tokenUsage/updated", {
        "threadId": thread_id,
        "tokenUsage": {"total": {"totalTokens": 42, "inputTokens": 30,
                                 "cachedInputTokens": 10,
                                 "outputTokens": 12,
                                 "reasoningOutputTokens": 0}}})
    notify("account/rateLimits/updated", {
        "rateLimits": {"limitId": "codex",
                       "primary": {"usedPercent": 1,
                                   "windowDurationMins": 10080}}})
    notify("turn/completed", {"threadId": thread_id,
                              "turn": {"id": turn_id, "status": "completed",
                                       "error": None}})


def main():
    dyn_tools = []
    thread_id = "fake-thread-0001"
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
        if method == "initialize":
            reply(rid, {"serverInfo": {"name": "fakecodex", "version": "0"}})
        elif method == "initialized":
            pass
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
        elif method == "turn/start":
            turn_id = "fake-turn-0001"
            reply(rid, {"turn": {"id": turn_id}})
            threading.Thread(target=run_turn,
                             args=(thread_id, turn_id, dyn_tools),
                             daemon=True).start()
        elif method in ("turn/steer", "turn/interrupt"):
            pass                            # the scenario thread answers it
        elif rid is not None:
            reply(rid, {})


if __name__ == "__main__":
    main()
