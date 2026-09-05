"""fakeantigravity — a scripted `agy` impostor for hermetic tests.

The antigravity analog of fakecodex.py: speaks just enough of the CLI's
print-mode stream-json wire (shapes copied from the probe logs banked
2026-09-02, Antigravity CLI 1.1.24) for backend/orgtree/antigravityrun.py to
run a full turn against it, with scenarios selected by
FAKEANTIGRAVITY_SCENARIO:

    text         (default) two agent_response steps with deltas, the second
                 carrying per-request usage, then a SUCCESS result whose
                 usage sums the turn (input EXCLUDES cache_read, output
                 INCLUDES thinking — the measured semantics)
    toolevents   adds a call_mcp_tool step (orgtree/orgtree_ping) and a
                 run_command step between two priced requests, so the
                 dispatch suite can prove journal/live-row folding and the
                 last-request occupancy rule
    sessioncumulative
                 four priced requests copied from a live resumed turn, then a
                 result whose usage is the whole session's cumulative total
                 (the measured 2026-09-04 shape)
    resultonly   no priced step, only result usage — the accounting fallback
    interrupt    one priced request, then a stall until killed — the
                 measured kill-mid-turn shape (no result event ever comes)
    wrongmodel   init.model reports "fake-default-model" regardless of
                 --model — the PLANTED FAULT the pin assertion must see
    unknownmodel the measured refusal: a lone result event, status ERROR,
                 "invalid model selection…", empty conversation_id, rc=1
    hookdeny     a run_command step ERRORs with the measured pre-tool-hook
                 denial message, and the run continues to SUCCESS
    resumelost   asked to resume with --conversation, warns on stderr and
                 starts a FRESH conversation with a new id instead — the
                 measured lost-resume shape (the old context is gone). With
                 no --conversation it behaves exactly like `text`
    canceled     the measured headless auto-deny outcome: result CANCELED,
                 empty response, the "no output produced" stderr line
    usage_limit  the MEASURED wall (2026-09-03, agy 1.1.24): a lone result
                 ERROR after init, "Individual quota reached … Resets in
                 165h21m54s." — FAKEANTIGRAVITY_RESET_IN overrides the
                 duration text; empty means no reset named (D-209)
    plain_error  a lone result ERROR after init whose text is NOT a wall:
                 FAKEANTIGRAVITY_ERROR (default "Internal error: the model
                 returned no response."), rc=1 — the ordinary failure that
                 obeys the per-message ceiling rather than freezing
    dupdone      `toolevents` with every DONE step_update (text and tool)
                 emitted TWICE — the repeated-completion control for the
                 journal contract (D4). ⚠ SYNTHETIC: no live log has shown
                 the CLI repeating a DONE; this is the shape a replay or a
                 duplicated notification would take, not a measured one
    diesafterstep
                 `toolevents` up to and including the run_command DONE, then
                 the process exits rc=1 with NO result event — the failure-
                 after-completed-blocks control (D4). Synthetic as above
    diesmidstep  a completed text step, then a second text step that streams
                 a delta and dies rc=1 mid-step, no DONE, no result — the
                 partial-output control (D4). Synthetic as above
    diesmidtool  `toolevents` up to the run_command ACTIVE, then rc=1 with no
                 DONE for it and no result — ONE tool step left open by a
                 crash. Synthetic as above
    diesmidtools two tools opened and neither completed, then a text step
                 streams a partial delta and the process dies rc=1 — several
                 open ids plus partial text, for the order the closes take
    interruptmidtool
                 a tool opens and the process then stalls until it is killed
                 (the `interrupt` shape with a tool left open) — the ⏸ path
    draftthentool
                 a text delta with no DONE, then a tool opens, then rc=1 —
                 the tool's ACTIVE path must flush the draft first, a real
                 emission a suite can hold the reader on before registration

`--conversation <id>` is honoured as a RESUME: the init echoes that id and
the first delta is "RESUMED:<id> " so a suite can tell resume from fresh.

Probes (credential hygiene and workspace discovery proven without any real
credential or CLI near the tests):
  FAKEANTIGRAVITY_ENVPROBE   comma-separated env keys → written as JSON to
                             FAKEANTIGRAVITY_ENVPROBE_PATH at startup
  FAKEANTIGRAVITY_WSPROBE    path → JSON of what the CLI would DISCOVER in
                             --add-dir: AGENTS.md, the orgtree plugin's
                             mcp_config.json, hooks.json, plus the prompt
                             text received on stdin and the argv
  FAKEANTIGRAVITY_STEP_DELAY seconds to sleep before every step_update
                             (default 0). A slow wire, so a suite can make
                             steps arrive AFTER start() has returned as
                             surely as the default makes them arrive before
  FAKEANTIGRAVITY_INIT_DELAY seconds to sleep BEFORE the init event (default
                             0): a slow start, spent inside start()'s own
                             INIT_TIMEOUT, so a suite can put a turn past a
                             small per-message ceiling before its result
                             arrives — without wait() timing out first

Subcommands the registry probe needs: `--version` prints 1.1.24; `models`
prints the measured registry (tab-separated id/label rows) unless
FAKEANTIGRAVITY_SIGNED_OUT=1, and writes the `--log-file` with the CLI's
own auth line either way — "OAuth: authenticated successfully as
fake-agy@example.test" when signed in, "You are not logged into
Antigravity." when not (both measured wordings).
"""
import json
import os
import sys
import time

SCENARIO = os.environ.get("FAKEANTIGRAVITY_SCENARIO", "text")
REGISTRY = [
    ("gemini-3.8-flash-high", "Gemini 3.8 Flash (High)"),
    ("gemini-3.8-flash-medium", "Gemini 3.8 Flash (Medium)"),
    ("gemini-3.8-flash-low", "Gemini 3.8 Flash (Low)"),
    ("gemini-3.7-flash-high", "Gemini 3.7 Flash (High)"),
    ("gemini-3.7-flash-medium", "Gemini 3.7 Flash (Medium)"),
    ("gemini-3.7-flash-low", "Gemini 3.7 Flash (Low)"),
    ("gemini-3.6-flash-high", "Gemini 3.6 Flash (High)"),
    ("gemini-3.6-flash-medium", "Gemini 3.6 Flash (Medium)"),
    ("gemini-3.6-flash-low", "Gemini 3.6 Flash (Low)"),
    ("gemini-3.1-pro-high", "Gemini 3.1 Pro (High)"),
    ("gemini-3.1-pro-low", "Gemini 3.1 Pro (Low)"),
    ("claude-sonnet-4-6", "Claude Sonnet 4.6 (Thinking)"),
    ("claude-opus-4-6-thinking", "Claude Opus 4.6 (Thinking)"),
    ("gpt-oss-120b-medium", "GPT-OSS 120B (Medium)"),
]


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def flag(name, default=None):
    """`--name value` or `--name=value`, first occurrence."""
    for i, a in enumerate(sys.argv):
        if a == name and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(name + "="):
            return a[len(name) + 1:]
    return default


def has(name):
    return name in sys.argv or any(a.startswith(name + "=") for a in sys.argv)


def write_log(path, signed_in):
    if not path:
        return
    email = os.environ.get("FAKEANTIGRAVITY_EMAIL", "fake-agy@example.test")
    with open(path, "w", encoding="utf-8") as f:
        f.write("I0902 server.go:1491] Starting language server process\n")
        f.write("E0902 errorreport.go:223] error getting token source: "
                "You are not logged into Antigravity.\n")
        if signed_in:
            f.write("I0902 server_oauth.go:197] OAuth: authenticated "
                    f"successfully as {email}\n")


def main_models():
    signed_in = os.environ.get("FAKEANTIGRAVITY_SIGNED_OUT") != "1"
    write_log(flag("--log-file"), signed_in)
    sys.stderr.write("Fetching available models...\n")
    if signed_in:
        for mid, label in REGISTRY:
            sys.stdout.write(f"{mid}\t{label}\n")
    sys.stdout.flush()


def _ws_probe(add_dir, prompt):
    probe = os.environ.get("FAKEANTIGRAVITY_WSPROBE")
    if not probe:
        return

    def read(rel, as_json=False):
        p = os.path.join(add_dir, *rel.split("/"))
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            body = f.read()
        return json.loads(body) if as_json else body

    doc = {"add_dir": add_dir, "argv": sys.argv[1:], "prompt": prompt,
           "agents_md": read("AGENTS.md"),
           "plugin": read(".agents/plugins/orgtree/plugin.json", True),
           "mcp_config": read(".agents/plugins/orgtree/mcp_config.json", True),
           "hooks": read(".agents/hooks.json", True),
           "wrapper_present": os.path.exists(os.path.join(
               add_dir, ".agents", "orgtree-rights.cmd")) or os.path.exists(
               os.path.join(add_dir, ".agents", "orgtree-rights.sh"))}
    with open(probe, "w", encoding="utf-8") as f:
        json.dump(doc, f)


def _usage(inp, out, think, cached):
    return {"input_tokens": inp, "output_tokens": out,
            "thinking_tokens": think, "cache_read_tokens": cached,
            "total_tokens": inp + out}


STEP_DELAY = float(os.environ.get("FAKEANTIGRAVITY_STEP_DELAY") or 0)


def _step(cid, idx, state, kind, **more):
    d = {"conversation_id": cid, "step_index": idx, "state": state,
         "step_type": kind}
    d.update(more)
    if STEP_DELAY:
        time.sleep(STEP_DELAY)
    emit({"event": "step_update", "step_update": d})
    if SCENARIO == "dupdone" and state == "DONE":
        # the repeated-completion plant: the SAME completion, verbatim, a
        # second time (a replay carries the same usage too — a consumer that
        # re-prices it is a separate question this fixture does not answer)
        emit({"event": "step_update", "step_update": d})


def _die(rc):
    """The process vanishes mid-wire: no result event, stdout closed by the
    exit itself. `os._exit` so no atexit flush adds anything after this."""
    sys.stdout.flush()
    os._exit(rc)


def main_turn():
    envprobe = os.environ.get("FAKEANTIGRAVITY_ENVPROBE", "")
    if envprobe:
        path = os.environ.get("FAKEANTIGRAVITY_ENVPROBE_PATH", "envprobe.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({k: os.environ.get(k) for k in envprobe.split(",")}, f)
    write_log(flag("--log-file"), True)
    model = flag("--model") or ""
    add_dir = flag("--add-dir") or os.getcwd()
    resume = flag("--conversation")
    # the prompt: one NDJSON user event on stdin, then EOF
    prompt = ""
    for raw in sys.stdin:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if msg.get("event") == "user":
            content = (msg.get("message") or {}).get("content")
            prompt = content if isinstance(content, str) else json.dumps(content)
        break
    _ws_probe(add_dir, prompt)
    if SCENARIO == "unknownmodel":
        emit({"event": "result", "result": {
            "conversation_id": "", "status": "ERROR", "response": "",
            "error": (f"invalid model selection (--model \"{model}\" "
                      f"--effort \"{flag('--effort') or ''}\"): model {model} "
                      "is not recognized as a known model or custom model in "
                      "settings\nAvailable models:\n  " + REGISTRY[0][1]),
            "duration_seconds": 0, "num_turns": 0,
            "usage": _usage(0, 0, 0, 0)}})
        sys.exit(1)
    if SCENARIO == "resumelost" and resume:
        # the MEASURED lost-resume shape: a warning on stderr, and a FRESH
        # conversation with a NEW id — the asked-for one is simply not there
        # any more and its context is gone with it
        sys.stderr.write(f"warning: conversation {resume} not found; "
                         "starting a new conversation\n")
        sys.stderr.flush()
        resume = ""
    cid = resume or os.environ.get("FAKEANTIGRAVITY_CONVERSATION_ID",
                                   "fake-agy-conv-0001")
    init_delay = float(os.environ.get("FAKEANTIGRAVITY_INIT_DELAY") or 0)
    if init_delay > 0:
        time.sleep(init_delay)
    served = "fake-default-model" if SCENARIO == "wrongmodel" else model
    yolo = has("--dangerously-skip-permissions")
    emit({"event": "init", "conversation_id": cid, "init": {
        "cwd": add_dir, "model": served,
        "permission_mode": "always-proceed" if yolo else "request-review",
        "tools": ["call_mcp_tool", "run_command", "write_to_file",
                  "view_file"]}})
    _step(cid, 0, "DONE", "user_input")
    if SCENARIO == "resultonly":
        emit({"event": "result", "result": {
            "conversation_id": cid, "status": "SUCCESS", "response": "done",
            "duration_seconds": 1.0, "num_turns": 1,
            "usage": _usage(7000, 300, 100, 11000)}})
        return
    if SCENARIO == "sessioncumulative":
        # Exact request arithmetic from window-verify's second live turn.  The
        # result is the session snapshot the CLI actually emits: the preceding
        # turn plus these four requests, not these requests alone.
        reqs = [(7105, 579, 516, 52954), (5625, 284, 226, 57031),
                (2242, 400, 138, 61099), (2737, 297, 63, 61092)]
        for idx, (inp, out, think, cached) in enumerate(reqs, 1):
            _step(cid, idx, "ACTIVE", "agent_response",
                  text_delta="done" if idx == len(reqs) else "")
            _step(cid, idx, "DONE", "agent_response", text_delta="",
                  usage=_usage(inp, out, think, cached))
        emit({"event": "result", "result": {
            "conversation_id": cid, "status": "SUCCESS", "response": "done",
            "duration_seconds": 3.2, "num_turns": 2,
            "usage": _usage(132605, 17559, 15999, 1182005)}})
        return
    if SCENARIO == "usage_limit":
        # MEASURED 2026-09-03 02:36 local (agy 1.1.24, the account's weekly
        # wall): a lone ERROR result after init, `usage` all zeros, rc=1,
        # and the reset stated as a DURATION at the end of the sentence.
        # FAKEANTIGRAVITY_RESET_IN overrides the duration text (a suite
        # pins the parse, and "no reset named" is a scenario too).
        reset_in = os.environ.get("FAKEANTIGRAVITY_RESET_IN", "165h21m54s")
        emit({"event": "result", "result": {
            "conversation_id": cid, "status": "ERROR", "response": "",
            "error": ("Individual quota reached. Please upgrade your "
                      "subscription to increase your limits."
                      + (f" Resets in {reset_in}." if reset_in else "")),
            "duration_seconds": 3.08, "num_turns": 1,
            "usage": _usage(0, 0, 0, 0)}})
        sys.exit(1)
    if SCENARIO == "plain_error":
        # the wall's shape (lone ERROR result after init, zero usage, rc=1)
        # with a sentence that is NOT a wall — the ordinary failure the
        # per-message ceiling applies to. SYNTHETIC: the wording is a
        # placeholder, overridable; no live log has been banked for it
        emit({"event": "result", "result": {
            "conversation_id": cid, "status": "ERROR", "response": "",
            "error": (os.environ.get("FAKEANTIGRAVITY_ERROR")
                      or "Internal error: the model returned no response."),
            "duration_seconds": 0.4, "num_turns": 1,
            "usage": _usage(0, 0, 0, 0)}})
        sys.exit(1)
    if SCENARIO == "canceled":
        _step(cid, 1, "DONE", "agent_response", duration_seconds=1.2,
              usage=_usage(14073, 185, 0, 0))
        _step(cid, 2, "ACTIVE", "tool", tool_name="run_command",
              tool_info={"name": "run_command",
                         "parameters": {"CommandLine": "echo x"}})
        _step(cid, 2, "ERROR", "tool", tool_name="run_command",
              tool_info={"name": "run_command",
                         "parameters": {"CommandLine": "echo x"},
                         "error": {"type": "TOOL_ERROR", "message":
                                   "permission check failed for command "
                                   "\"echo x\": user denied permission to "
                                   "run command:\necho x"}})
        emit({"event": "result", "result": {
            "conversation_id": cid, "status": "CANCELED", "response": "",
            "duration_seconds": 1.3, "num_turns": 1,
            "usage": _usage(14073, 185, 0, 0)}})
        sys.stderr.write(
            'jetski: no output produced — a tool required the "command" '
            "permission that headless mode cannot prompt for, so it was "
            "auto-denied.\n")
        sys.exit(0)
    first = (f"RESUMED:{cid} " if resume else "") + "working… "
    _step(cid, 1, "ACTIVE", "agent_response", text_delta=first)
    _step(cid, 1, "DONE", "agent_response", text_delta="",
          duration_seconds=1.6, usage=_usage(8290, 48, 30, 1200))
    total_in, total_out, total_think, total_cached = 8290, 48, 30, 1200
    if SCENARIO == "interrupt":
        _step(cid, 2, "ACTIVE", "agent_response",
              text_delta="stalling until killed… ")
        time.sleep(8.0)
        return
    if SCENARIO == "diesmidstep":
        # a second text step opens, says something, and the process is gone
        # before that step's DONE — no result event follows
        _step(cid, 2, "ACTIVE", "agent_response",
              text_delta="partial words before death ")
        _die(1)
    if SCENARIO == "diesmidtools":
        # two tools open and NEITHER completes, then a text step says
        # something and the process is gone: several open ids and a partial
        # text block, so a suite can pin the order their closes take
        _step(cid, 2, "ACTIVE", "tool", tool_name="call_mcp_tool",
              tool_info={"name": "call_mcp_tool", "parameters": {
                  "Arguments": {"message": "hi"}, "ServerName": "orgtree",
                  "ToolName": "orgtree_ping"}})
        _step(cid, 3, "ACTIVE", "tool", tool_name="run_command",
              tool_info={"name": "run_command",
                         "parameters": {"CommandLine": "echo HOOK-CMD"}})
        _step(cid, 4, "ACTIVE", "agent_response",
              text_delta="partial words before death ")
        _die(1)
    if SCENARIO == "draftthentool":
        # a text delta with NO DONE, then a tool opens, then the process is
        # gone: the tool's ACTIVE path has to flush that draft first, which
        # gives a suite a real emission to hold the reader on before the
        # tool is registered (the held-callback control)
        _step(cid, 2, "ACTIVE", "agent_response",
              text_delta="thinking aloud before the tool ")
        _step(cid, 3, "ACTIVE", "tool", tool_name="run_command",
              tool_info={"name": "run_command",
                         "parameters": {"CommandLine": "echo HELD"}})
        _die(1)
    if SCENARIO == "interruptmidtool":
        # the measured kill-mid-turn shape, with a tool left open: the step
        # opens and nothing more is ever said until the tree is killed
        _step(cid, 2, "ACTIVE", "tool", tool_name="run_command",
              tool_info={"name": "run_command",
                         "parameters": {"CommandLine": "sleep forever"}})
        time.sleep(8.0)
        return
    if SCENARIO in ("toolevents", "hookdeny", "dupdone", "diesafterstep",
                    "diesmidtool"):
        if SCENARIO != "hookdeny":
            _step(cid, 2, "ACTIVE", "tool", tool_name="call_mcp_tool",
                  tool_info={"name": "call_mcp_tool", "parameters": {
                      "Arguments": {"message": "hi"}, "ServerName": "orgtree",
                      "ToolName": "orgtree_ping"}})
            _step(cid, 2, "DONE", "tool", tool_name="call_mcp_tool",
                  duration_seconds=0.01,
                  tool_info={"name": "call_mcp_tool", "parameters": {
                      "Arguments": {"message": "hi"}, "ServerName": "orgtree",
                      "ToolName": "orgtree_ping"}, "output": "PONG:hi"})
        params = {"CommandLine": "echo HOOK-CMD"}
        _step(cid, 3, "ACTIVE", "tool", tool_name="run_command",
              tool_info={"name": "run_command", "parameters": params})
        if SCENARIO == "diesmidtool":
            # the run_command step is open and the process is gone before its
            # DONE — no result event follows either
            _die(1)
        if SCENARIO == "hookdeny":
            _step(cid, 3, "ERROR", "tool", tool_name="run_command",
                  duration_seconds=0.2,
                  tool_info={"name": "run_command", "parameters": params,
                             "error": {"type": "TOOL_ERROR", "message":
                                       "tool call denied by pre-tool hook: "
                                       "orgtree: this agent has no shell "
                                       "rights (bash is off in its orgtree "
                                       "scope) — do not retry the command"}})
        else:
            _step(cid, 3, "DONE", "tool", tool_name="run_command",
                  duration_seconds=0.3,
                  tool_info={"name": "run_command", "parameters": params,
                             "output": "HOOK-CMD\r\n"})
        if SCENARIO == "diesafterstep":
            # two completed blocks (the first text step and both tools) are
            # on the wire; the process dies before the closing text and
            # before any result event
            _die(1)
        # the second priced request: a SMALLER uncached input and a cache
        # hit — the last request is what occupancy must read (measured
        # shape: 4563 + 12175 for a ~16.7K context)
        _step(cid, 4, "ACTIVE", "agent_response", text_delta="done.")
        _step(cid, 4, "DONE", "agent_response", text_delta="\n",
              duration_seconds=1.2, usage=_usage(4563, 110, 100, 12175))
        total_in += 4563
        total_out += 110
        total_think += 100
        total_cached += 12175
        text = first + "done.\n"
    else:
        _step(cid, 2, "ACTIVE", "agent_response", text_delta="done.")
        _step(cid, 2, "DONE", "agent_response", text_delta="\n",
              duration_seconds=0.9, usage=_usage(8400, 12, 0, 0))
        total_in += 8400
        total_out += 12
        text = first + "done.\n"
    emit({"event": "result", "result": {
        "conversation_id": cid, "status": "SUCCESS", "response": text,
        "duration_seconds": 3.2, "num_turns": 2 if resume else 1,
        "usage": _usage(total_in, total_out, total_think, total_cached)}})


def main():
    if "--version" in sys.argv:
        sys.stdout.write("1.1.24\n")
        return
    if "models" in sys.argv:
        main_models()
        return
    main_turn()


if __name__ == "__main__":
    main()
