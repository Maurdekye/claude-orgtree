"""fakegemini — a scripted `gemini --acp` impostor for hermetic tests.

The gemini analog of fakecodex.py: speaks just enough ACP JSON-RPC over
stdio for backend/orgtree/geminirun.py to run a full turn against it, with
scenarios selected by FAKEGEMINI_SCENARIO:

    text        (default) a thought chunk + two message chunks, then
                end_turn with the measured multi-model quota shape
    toolevents  emits tool_call → tool_call_update around the message,
                so the dispatch suite can prove journal/live-row folding
    interrupt   the turn stalls until session/cancel arrives, then the
                prompt resolves {stopReason: "cancelled"} with NO _meta —
                exactly the measured wire (an interrupted turn costs $0)
    wrongmodel  session/new//load report currentModelId "gemini-fake-default"
                regardless of -m — the PLANTED FAULT geminirun's pin
                assertion must see (the real CLI substitutes silently)
    permission  a session/request_permission server-request mid-turn; the
                chosen outcome is echoed into the agent text

Env probe: whatever FAKEGEMINI_ENVPROBE names (comma-separated env keys) is
written as JSON to FAKEGEMINI_ENVPROBE_PATH at prompt time — credential
hygiene proven without any real credential near the tests. MCP probe:
FAKEGEMINI_MCPPROBE captures the mcpServers param of session/new AND
session/load (a lane that stops passing them on resume strips every
post-first turn of its org powers — the codex lane's D-180 lesson).

session/load REPLAYS two stored chunks BEFORE its response, mirroring the
measured wire — a runner without the replay gate re-streams old history and
the suite catches it.

Invoked as `python fakegemini.py --acp -m <model> --approval-mode <mode>`.
"""
import json
import os
import sys
import threading
import time

SCENARIO = os.environ.get("FAKEGEMINI_SCENARIO", "text")

_out_lock = threading.Lock()
_responses: dict[int, dict] = {}
_cancelled = threading.Event()
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


def _argv_flag(flag):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


MODEL = _argv_flag("-m")
MODE = _argv_flag("--approval-mode") or "default"
SESSION = os.environ.get("FAKEGEMINI_SESSION_ID", "fake-gem-sess-0001")


def _served_model():
    if SCENARIO == "wrongmodel":
        return "gemini-fake-default"
    return MODEL or "gemini-fake-default"


def _open_result(with_sid):
    res = {
        "modes": {"availableModes": [
            {"id": "default", "name": "Default"},
            {"id": "yolo", "name": "YOLO"}], "currentModeId": MODE},
        "models": {"availableModels": [
            {"modelId": _served_model(), "name": _served_model()}],
            "currentModelId": _served_model()},
    }
    if with_sid:
        res["sessionId"] = SESSION
    return res


def _capture_mcp(params, verb):
    probe = os.environ.get("FAKEGEMINI_MCPPROBE")
    if not probe:
        return
    record = []
    if os.path.exists(probe):
        with open(probe, encoding="utf-8") as f:
            record = json.load(f)
    record.append({"verb": verb, "mcpServers": params.get("mcpServers")})
    with open(probe, "w", encoding="utf-8") as f:
        json.dump(record, f)


def _update(sid, upd):
    notify("session/update", {"sessionId": sid, "update": upd})


def _chunk(sid, kind, text):
    _update(sid, {"sessionUpdate": kind,
                  "content": {"type": "text", "text": text}})


def run_prompt(rid, sid):
    probe = os.environ.get("FAKEGEMINI_ENVPROBE", "")
    if probe:
        path = os.environ.get("FAKEGEMINI_ENVPROBE_PATH", "envprobe.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({k: os.environ.get(k) for k in probe.split(",")}, f)
    if SCENARIO == "interrupt":
        _chunk(sid, "agent_message_chunk", "working until cancelled… ")
        if _cancelled.wait(8.0):
            reply(rid, {"stopReason": "cancelled"})
        else:
            reply(rid, {"stopReason": "end_turn"})
        return
    _chunk(sid, "agent_thought_chunk", "planning the reply")
    _chunk(sid, "agent_message_chunk", "working… ")
    if SCENARIO == "toolevents":
        _update(sid, {"sessionUpdate": "tool_call",
                      "toolCallId": "mcp_orgtree_ping__call_1",
                      "status": "in_progress",
                      "title": "orgtree_ping (orgtree MCP Server)",
                      "kind": "other",
                      "content": [{"type": "content", "content": {
                          "type": "text", "text": "{\"message\":\"hi\"}"}}]})
        _update(sid, {"sessionUpdate": "tool_call_update",
                      "toolCallId": "mcp_orgtree_ping__call_1",
                      "status": "completed",
                      "title": "orgtree_ping (orgtree MCP Server)",
                      "kind": "other",
                      "content": [{"type": "content", "content": {
                          "type": "text", "text": "PONG:hi"}}]})
    if SCENARIO == "permission":
        ans = server_request("session/request_permission", {
            "sessionId": sid,
            "toolCall": {"toolCallId": "shell-1", "title": "run something",
                         "kind": "execute"},
            "options": [
                {"optionId": "allow_once", "name": "Allow", "kind": "allow_once"},
                {"optionId": "reject_once", "name": "Reject",
                 "kind": "reject_once"}]})
        outcome = ((ans or {}).get("result") or {}).get("outcome") or {}
        _chunk(sid, "agent_message_chunk",
               f"permission:{outcome.get('outcome')}:"
               f"{outcome.get('optionId')}")
    _chunk(sid, "agent_message_chunk", "done.")
    reply(rid, {"stopReason": "end_turn", "_meta": {"quota": {
        "token_count": {"input_tokens": 8290, "output_tokens": 48},
        "model_usage": [
            {"model": _served_model(),
             "token_count": {"input_tokens": 8290, "output_tokens": 48}},
            {"model": "gemini-3.1-flash-lite",
             "token_count": {"input_tokens": 795, "output_tokens": 36}},
        ]}}})


def main():
    for raw in sys.stdin:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "method" not in msg:            # a response to OUR server-request
            if "id" in msg:
                _responses[int(msg["id"])] = msg
            continue
        method, rid = msg["method"], msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            reply(rid, {"protocolVersion": 1,
                        "agentInfo": {"name": "fakegemini", "version": "0"},
                        "agentCapabilities": {
                            "loadSession": True,
                            "mcpCapabilities": {"http": True, "sse": True}}})
        elif method == "session/new":
            _capture_mcp(params, "new")
            reply(rid, _open_result(with_sid=True))
        elif method == "session/load":
            _capture_mcp(params, "load")
            sid = str(params.get("sessionId") or SESSION)
            # the measured replay: stored history re-emitted BEFORE the
            # response — a runner without the replay gate folds these
            _chunk(sid, "user_message_chunk", "old question")
            _chunk(sid, "agent_message_chunk", "REPLAYED-OLD-ANSWER")
            reply(rid, _open_result(with_sid=False))
        elif method == "session/prompt":
            sid = str(params.get("sessionId") or SESSION)
            threading.Thread(target=run_prompt, args=(rid, sid),
                             daemon=True).start()
        elif method == "session/cancel":
            _cancelled.set()               # a NOTIFICATION — nothing to reply
        elif rid is not None:
            reply(rid, {})


if __name__ == "__main__":
    main()
