"""antigravityrun: the print-mode turn adapter, hermetic against
fakeantigravity.

    python backend/tests/test_antigravityrun.py    (no pytest; plain asserts)

Every scenario speaks the wire shapes measured live 2026-09-02 (probe logs
banked in the implementing agent's scratch). The planted faults matter
most: a fake whose init reports the WRONG served model must be refused, an
unknown model's lone ERROR result must surface as the CLI's own words, and
a killed turn must come back interrupted WITH the usage it had already
reported — an instrument that cannot see its planted fault proves nothing.
"""

import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-agyrun-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import antigravityrun, providers                      # noqa: E402

FAKE = os.path.join(os.path.dirname(__file__), "fakeantigravity.py")
HEAD = [sys.executable, FAKE]
PASS = 0
FAIL: list[tuple[str, str]] = []


def check(label, fn):
    # catching, like the other two antigravity suites: a failure has to
    # print a FAIL line and let the rest run, so a mutant run can tell a
    # CHECK that fired from the suite dying on its own before any check
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        import traceback
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


def _run(scenario, *, conversation_id=None, model="gemini-3.8-flash",
         effort="high", cwd=None, env_extra=None, on_event=None,
         interrupt_after=None, text="hello from the suite", yolo=True):
    os.environ["FAKEANTIGRAVITY_SCENARIO"] = scenario
    turn = antigravityrun.AntigravityTurn(
        HEAD, cwd=cwd or tempfile.mkdtemp(prefix="orgtree-agycwd-"),
        model=model, effort=effort, conversation_id=conversation_id,
        yolo=yolo, on_event=on_event, env_extra=env_extra)
    cid = turn.start(text)
    if interrupt_after is not None:
        time.sleep(interrupt_after)
        assert turn.interrupt(), "interrupt() refused"
    res = turn.wait(timeout=20)
    return cid, res, turn


def main():
    print("§1 a full turn against the measured wire")
    events = []
    cid, res, turn = _run("text", on_event=lambda m: events.append(m))
    check("the conversation id is harvested from init",
          lambda: eq(cid, "fake-agy-conv-0001", "cid"))
    check("the turn completes with the agent text folded in order",
          lambda: eq((res["status"], res["agent_text"]),
                     ("completed", "working… done.\n"), "result"))
    check("usage normalizes to ONE document: totals from the result "
          "(input excludes cached, output includes thinking), last_prompt "
          "from the final priced request",
          lambda: eq(res["token_usage"],
                     {"model": "gemini-3.8-flash", "input": 16690,
                      "cached": 1200, "output": 60, "thinking": 30,
                      "last_prompt": 8400, "requests": 2}, "usage"))
    check("…and the cost fold prices it at the 3.8-flash row "
          "(16690·.75 + 1200·.075 + 60·3.75 per M)",
          lambda: eq(providers.antigravity_cost(res["token_usage"]),
                     0.012833, "cost"))
    check("occupancy is the LAST request's prompt (input + cached), "
          "never the turn's summed input",
          lambda: eq(providers.antigravity_occupancy(res["token_usage"]),
                     8400, "occ"))

    _, live, _ = _run("sessioncumulative",
                       conversation_id="live-prior-conversation")
    check("a resumed result's exact 1,314,610-token SESSION snapshot does not "
          "overwrite this turn's per-request accounting",
          lambda: eq(live["token_usage"], {
              "model": "gemini-3.8-flash", "input": 17709,
              "cached": 232176, "output": 1560, "thinking": 943,
              "last_prompt": 63829, "requests": 4}, "live cumulative usage"))
    check("the live multi-request turn bills only its own arithmetic",
          lambda: eq(providers.antigravity_cost(live["token_usage"]),
                     0.036545, "per-turn cost"))
    check("the same live turn's occupancy is its final 63,829-token request, "
          "not its 249,885-token turn total or 1,314,610-token session total",
          lambda: eq(providers.antigravity_occupancy(live["token_usage"]),
                     63829, "live occupancy"))

    _, fallback, _ = _run("resultonly")
    check("result usage remains the non-zero fallback when no priced step "
          "was observed, without inventing a last-request occupancy",
          lambda: eq(fallback["token_usage"], {
              "model": "gemini-3.8-flash", "input": 7000,
              "cached": 11000, "output": 300, "thinking": 100,
              "last_prompt": 0, "requests": 1}, "result fallback"))
    check("the argv carries the measured print-mode surface: stdin prompt, "
          "stream-json both ways, --add-dir cwd, base model + --effort, "
          "skip-permissions",
          lambda: eq((turn.argv[2:6], turn.argv[6:8],
                      "--add-dir" in turn.argv,
                      turn.argv[turn.argv.index("--model") + 1],
                      turn.argv[turn.argv.index("--effort") + 1],
                      "--dangerously-skip-permissions" in turn.argv),
                     (["-p=", "--input-format", "stream-json",
                       "--output-format"], ["stream-json", "--add-dir"],
                      True, "gemini-3.8-flash", "high", True), "argv"))
    check("every wire event reached the observer, init first, result last",
          lambda: eq((events[0]["event"], events[-1]["event"]),
                     ("init", "result"), "events"))

    print("§2 resume + the workspace the CLI discovers")
    tmp = tempfile.mkdtemp(prefix="orgtree-agyws-")
    probe = os.path.join(tmp, "ws.json")
    os.environ["FAKEANTIGRAVITY_WSPROBE"] = probe
    cwd = tempfile.mkdtemp(prefix="orgtree-agycwd2-")
    orgtree_srv = {"orgtree": {"command": sys.executable,
                               "args": ["-m", "orgtree.mcptool"],
                               "env": {"ORGTREE_ORG": "o", "ORGTREE_NODE": "n",
                                       "ORGTREE_PORT": "9"}}}
    ws = antigravityrun.write_workspace(
        cwd, identity="# I am node n of org o", mcp_servers=orgtree_srv,
        rights={"bash": True, "edit": True})
    cid2, res2, _ = _run("text", conversation_id="fake-agy-conv-0001",
                         cwd=cwd)
    with open(probe, encoding="utf-8") as f:
        seen = json.load(f)
    check("a resumed turn keeps the durable conversation id and the fake "
          "saw --conversation (the RESUMED marker leads the text)",
          lambda: eq((cid2, res2["agent_text"].startswith(
              "RESUMED:fake-agy-conv-0001 ")), ("fake-agy-conv-0001", True),
              "resume"))
    check("the identity rides AGENTS.md at the workspace root",
          lambda: eq(seen["agents_md"], "# I am node n of org o", "AGENTS.md"))
    check("org powers ride the orgtree workspace plugin's mcp_config.json "
          "with the FULL identity env named",
          lambda: eq((seen["plugin"],
                      seen["mcp_config"]["mcpServers"]["orgtree"]["env"]),
                     ({"name": "orgtree"},
                      {"ORGTREE_ORG": "o", "ORGTREE_NODE": "n",
                       "ORGTREE_PORT": "9"}), "plugin"))
    check("a full-rights node has NO hooks file and no wrapper",
          lambda: eq((ws, seen["hooks"], seen["wrapper_present"]),
                     ({"hooks": False, "denied": []}, None, False),
                     "no hooks"))
    check("the prompt arrived on stdin intact",
          lambda: eq(seen["prompt"], "hello from the suite", "prompt"))
    long_text = ("The quick brown fox jumps over the lazy dog. " * 3000)[:120_000]
    _run("text", cwd=cwd, text=long_text)
    with open(probe, encoding="utf-8") as f:
        seen_long = json.load(f)
    check("a 120K-character prompt rides stdin whole — the argv lane would "
          "have died at Windows' 32K command-line cap",
          lambda: eq(len(seen_long["prompt"]), 120_000, "long prompt"))
    os.environ.pop("FAKEANTIGRAVITY_WSPROBE", None)

    _, res_t, _ = _run("toolevents")
    check("a tool round adds a priced request: occupancy reads the LAST "
          "request (4563 + 12175 cached), cost bills the whole sum",
          lambda: eq((res_t["token_usage"]["requests"],
                      providers.antigravity_occupancy(res_t["token_usage"]),
                      res_t["token_usage"]["input"],
                      res_t["token_usage"]["cached"]),
                     (2, 16738, 12853, 13375), "tool round"))

    print("§3 the planted faults the guards must SEE")

    def wrong_model():
        try:
            _run("wrongmodel")
        except antigravityrun.AntigravityError as e:
            eq("model pin refused" in str(e)
               and "fake-default-model" in str(e), True, f"message {e!r}")
            return
        raise AssertionError("a substituted model was accepted")
    check("an init serving the WRONG model is refused loudly", wrong_model)

    def unknown_model():
        try:
            _run("unknownmodel", model="nope-9.9-model")
        except antigravityrun.AntigravityError as e:
            eq("invalid model selection" in str(e), True, f"message {e!r}")
            return
        raise AssertionError("an unknown model's refusal was swallowed")
    check("the CLI's own refusal of an unknown model surfaces in ITS words "
          "(the measured lone ERROR result, rc=1)", unknown_model)

    def limit():
        _, r, _ = _run("usage_limit")
        eq((r["status"], "Individual quota reached" in str(r["stop_reason"]),
            "Resets in 165h21m54s" in str(r["stop_reason"])),
           ("failed", True, True), "limit")
    check("an ERROR result after init is a FAILED turn carrying the error "
          "text (the D-209 classifier's input)", limit)

    def canceled():
        _, r, _ = _run("canceled", yolo=False)
        eq((r["status"], "CANCELED" in str(r["stop_reason"])
            or "no output" in str(r["stop_reason"])), ("failed", True),
           f"canceled: {r['stop_reason']!r}")
    check("a CANCELED result (the headless auto-deny outcome) fails loudly, "
          "never reads as an empty success", canceled)

    print("§4 interrupt = kill, booked from the usage already seen")
    cid4, res4, _ = _run("interrupt", interrupt_after=1.0)
    check("a killed turn is an INTERRUPTED completed turn",
          lambda: eq((res4["status"], res4["stop_reason"]),
                     ("interrupted", "interrupted"), "status"))
    check("…and it BILLS the request the CLI had already priced before the "
          "kill (never $0 for work that was done)",
          lambda: eq((res4["token_usage"]["input"],
                      res4["token_usage"]["requests"],
                      providers.antigravity_cost(res4["token_usage"]) > 0),
                     (8290, 1, True), "partial usage"))
    check("…with the text streamed up to the kill kept",
          lambda: eq(res4["agent_text"],
                     "working… stalling until killed… ", "partial text"))
    check("steer() always refuses — the wire has no verb, the caller queues",
          lambda: eq(_run("text")[2].steer("x"), False, "steer"))

    print("§5 credential hygiene, proven by the env probe")
    os.environ["ANTHROPIC_API_KEY"] = "planted-anthropic"
    os.environ["OPENAI_API_KEY"] = "planted-openai"
    os.environ["CLAUDECODE"] = "1"
    os.environ["CLAUDE_CODE_ENTRYPOINT"] = "planted"
    os.environ["GOOGLE_CLOUD_PROJECT"] = "keep-me"
    os.environ["AGY_CLI_DISABLE_AUTO_UPDATE"] = "1"
    probe_env = os.path.join(tmp, "env.json")
    os.environ["FAKEANTIGRAVITY_ENVPROBE"] = (
        "ANTHROPIC_API_KEY,OPENAI_API_KEY,CLAUDECODE,CLAUDE_CODE_ENTRYPOINT,"
        "GOOGLE_CLOUD_PROJECT,ORGTREE_ORG,AGY_CLI_DISABLE_AUTO_UPDATE")
    os.environ["FAKEANTIGRAVITY_ENVPROBE_PATH"] = probe_env
    _run("text", env_extra={"ORGTREE_ORG": "proof",
                             "AGY_CLI_DISABLE_AUTO_UPDATE": "1"})
    with open(probe_env, encoding="utf-8") as f:
        seen_env = json.load(f)
    check("the other providers' material is STRIPPED at spawn "
          "(anthropic, openai, claude-code marks)",
          lambda: eq((seen_env["ANTHROPIC_API_KEY"], seen_env["OPENAI_API_KEY"],
                      seen_env["CLAUDECODE"], seen_env["CLAUDE_CODE_ENTRYPOINT"]),
                     (None, None, None, None), "stripped"))
    check("…this provider's own env and the caller's extras pass through, "
          "and the CLI's self-update is switched off for the child",
          lambda: eq((seen_env["GOOGLE_CLOUD_PROJECT"], seen_env["ORGTREE_ORG"],
                      seen_env["AGY_CLI_DISABLE_AUTO_UPDATE"]),
                     ("keep-me", "proof", "true"), "kept"))
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDECODE",
              "CLAUDE_CODE_ENTRYPOINT", "GOOGLE_CLOUD_PROJECT",
              "AGY_CLI_DISABLE_AUTO_UPDATE",
              "FAKEANTIGRAVITY_ENVPROBE", "FAKEANTIGRAVITY_ENVPROBE_PATH"):
        os.environ.pop(k, None)

    print("§6 the ⚙-rights seam: the PreToolUse hook")
    cwd6 = tempfile.mkdtemp(prefix="orgtree-agyrights-")
    ws6 = antigravityrun.write_workspace(
        cwd6, identity="x", mcp_servers={}, rights={"bash": False,
                                                     "edit": True})
    hooks = json.load(open(os.path.join(cwd6, ".agents", "hooks.json"),
                           encoding="utf-8"))
    cmd = hooks["orgtree-rights"]["PreToolUse"][0]["hooks"][0]["command"]
    check("a bash-off node gets a hooks.json whose ONE command is the "
          "absolute wrapper path, matcher *, and the shell-class tools "
          "listed as denied",
          lambda: eq((ws6, hooks["orgtree-rights"]["PreToolUse"][0]["matcher"],
                      os.path.isabs(cmd.strip('"')),
                      cmd.strip('"').endswith(
                          "orgtree-rights.cmd" if os.name == "nt"
                          else "orgtree-rights.sh")),
                     ({"hooks": True, "denied": sorted(
                         antigravityrun.TOOLS_BASH)}, "*", True, True),
                     "hooks.json"))

    def hook_runs():
        """Run the hook exactly as the CLI would: the wrapper, payload on
        stdin, decision on stdout — a denied tool and an allowed one."""
        import subprocess
        wrapper = cmd.strip('"')
        out = []
        for name in ("run_command", "view_file"):
            r = subprocess.run(
                [wrapper] if os.name == "nt" else ["sh", wrapper],
                input=json.dumps({"toolCall": {"name": name, "args": {}},
                                  "stepIdx": 1}).encode(),
                capture_output=True, timeout=30,
                cwd=os.path.join(cwd6, ".agents"))
            out.append(json.loads(r.stdout.decode()))
        eq((out[0]["decision"], out[0]["reason"].startswith("orgtree: "),
            out[1]["decision"]), ("deny", True, "allow"), f"hook: {out}")
    check("the wrapper actually runs and answers deny/allow by tool class",
          hook_runs)
    # §6b the other two switches. Until 2026-09-05 `web` and `subagents`
    # reached this lane as nothing at all, while the (lane-independent)
    # identity prompt told those nodes the capability was disabled — a wall
    # in the prose and none on the wire. The check RUNS the wrapper for the
    # two web tools this machine's own journals show real Antigravity turns
    # calling (`search_web`, `read_url_content`), so it is a behaviour check,
    # not a spelling check.
    cwd6w = tempfile.mkdtemp(prefix="orgtree-agyweb-")
    ws6w = antigravityrun.write_workspace(
        cwd6w, identity="x", mcp_servers={},
        rights={"bash": True, "edit": True, "web": False, "subagents": False})
    hooks_w = json.load(open(os.path.join(cwd6w, ".agents", "hooks.json"),
                             encoding="utf-8"))
    cmd_w = hooks_w["orgtree-rights"]["PreToolUse"][0]["hooks"][0]["command"]

    def decide(wrapper, name, args=None):
        import subprocess
        r = subprocess.run(
            [wrapper] if os.name == "nt" else ["sh", wrapper],
            input=json.dumps({"toolCall": {"name": name,
                                           "args": dict(args or {})},
                              "stepIdx": 1}).encode(),
            capture_output=True, timeout=30,
            cwd=os.path.dirname(wrapper))
        return json.loads(r.stdout.decode())

    def web_off_runs():
        w = cmd_w.strip('"')
        got = {n: decide(w, n)["decision"]
               for n in ("search_web", "read_url_content", "open_browser_url",
                         "browser_input", "invoke_subagent",
                         "manage_subagents",
                         # untouched classes: this node keeps its terminal,
                         # its edits and its ordinary reads
                         "run_command", "write_to_file", "view_file")}
        eq(got, {"search_web": "deny", "read_url_content": "deny",
                 "open_browser_url": "deny", "browser_input": "deny",
                 "invoke_subagent": "deny", "manage_subagents": "deny",
                 "run_command": "allow", "write_to_file": "allow",
                 "view_file": "allow"}, f"decisions {got}")
    check("a web-off, subagents-off node denies the web and subagent tools "
          "for real through the wrapper, and keeps every other class",
          web_off_runs)
    check("…and the denied set is exactly those two classes, nothing wider",
          lambda: eq(ws6w, {"hooks": True,
                            "denied": sorted(set(antigravityrun.TOOLS_WEB) |
                                             set(antigravityrun.TOOLS_SUBAGENT))},
                     f"{ws6w}"))

    def reason_names_the_switch():
        w = cmd_w.strip('"')
        web = decide(w, "search_web")["reason"]
        sub = decide(w, "invoke_subagent")["reason"]
        # `browser_subagent` is in BOTH classes: the first switch off supplies
        # the reason, so it must not read as a subagent-only denial here
        both = decide(w, "browser_subagent")["reason"]
        eq((("web access" in web), ("subagents are off" in sub),
            ("web access" in both)), (True, True, True),
           f"web={web!r} sub={sub!r} both={both!r}")
    check("each denial names the switch that closed it, and a tool in two "
          "classes keeps the first one's reason",
          reason_names_the_switch)

    # §6c THE READ-ONLY SEAT'S TERMINAL, attacked. An edit-off seat used to
    # keep `run_command`, which is a write tool the moment you redirect: the
    # operator's own journal 9b9a1f71-5ef2-477b-8f3a-a14a414e2c11 (2026-09-04)
    # has this hook DENYING `write_to_file` for a real edit-off node and 89
    # successful `run_command` calls in the same session. Below, the SAME
    # write is attempted through the shell against the real wrapper, and the
    # control proves the attempt is not a straw man: the identical command,
    # allowed for a writable seat, is RUN and does create the file.
    # (What is not exercised here is the CLI obeying the wrapper — that is
    # measured, in the journal denial cited above.)
    cwd6r = tempfile.mkdtemp(prefix="orgtree-agyro-")
    ws6r = antigravityrun.write_workspace(
        cwd6r, identity="x", mcp_servers={},
        rights={"bash": True, "edit": False, "web": True, "subagents": True})
    cmd_r = json.load(open(os.path.join(cwd6r, ".agents", "hooks.json"),
                           encoding="utf-8")
                      )["orgtree-rights"]["PreToolUse"][0]["hooks"][0]["command"]
    # a NARROWED BUT WRITABLE seat — a full-rights one carries no hook at all
    cwd6c = tempfile.mkdtemp(prefix="orgtree-agyrw-")
    antigravityrun.write_workspace(
        cwd6c, identity="x", mcp_servers={},
        rights={"bash": True, "edit": True, "web": False, "subagents": True})
    cmd_c = json.load(open(os.path.join(cwd6c, ".agents", "hooks.json"),
                           encoding="utf-8")
                      )["orgtree-rights"]["PreToolUse"][0]["hooks"][0]["command"]

    def _attack(cwd):
        victim = os.path.join(cwd, "victim.txt")
        line = (f'cmd /c echo pwned> "{victim}"' if os.name == "nt"
                else f'echo pwned > "{victim}"')
        return victim, line

    def read_only_shell_is_shut():
        import subprocess
        w = cmd_r.strip('"')
        victim, line = _attack(cwd6r)
        got = decide(w, "run_command", {"CommandLine": line})
        if got["decision"] != "deny":              # the CLI would now run it
            subprocess.run(line, shell=True, timeout=30)
        eq((got["decision"], os.path.exists(victim)), ("deny", False),
           f"the shell write was {got} and victim exists="
           f"{os.path.exists(victim)}")
        # the reason has to say what closed it and what still works, or the
        # seat retries the only tool it thinks it has
        r = got["reason"]
        assert "may not change files" in r and "view_file" in r, r

    def writable_shell_still_writes():
        import subprocess
        w = cmd_c.strip('"')
        victim, line = _attack(cwd6c)
        got = decide(w, "run_command", {"CommandLine": line})
        if got["decision"] != "deny":
            subprocess.run(line, shell=True, timeout=30)
        eq((got["decision"], os.path.exists(victim)), ("allow", True),
           f"control: {got}, victim exists={os.path.exists(victim)}")
    check("a read-only seat's shell write is DENIED at the wrapper and the "
          "file never appears", read_only_shell_is_shut)
    check("…and the control proves the attempt writes: the same command on a "
          "narrowed but WRITABLE seat is allowed, runs, and creates the file",
          writable_shell_still_writes)
    check("an edit-off seat denies the edit class AND the shell class, and "
          "nothing wider",
          lambda: eq(ws6r, {"hooks": True,
                            "denied": sorted(set(antigravityrun.TOOLS_EDIT) |
                                             set(antigravityrun.TOOLS_BASH))},
                     f"{ws6r}"))

    def bash_off_reason_wins():
        # both classes closed at once: the bash switch is the FIRST entry, so
        # a bash-off seat is told the switch is off, not that it may not write
        cwd6x = tempfile.mkdtemp(prefix="orgtree-agyboth-")
        antigravityrun.write_workspace(
            cwd6x, identity="x", mcp_servers={},
            rights={"bash": False, "edit": False})
        wx = json.load(open(os.path.join(cwd6x, ".agents", "hooks.json"),
                            encoding="utf-8")
                       )["orgtree-rights"]["PreToolUse"][0]["hooks"][0][
                           "command"].strip('"')
        r = decide(wx, "run_command")["reason"]
        assert "bash is off" in r, r
    check("a seat with BOTH switches off is told the shell switch is off, "
          "not the write door", bash_off_reason_wins)

    ws6b = antigravityrun.write_workspace(
        cwd6, identity="x", mcp_servers={}, rights={"bash": True,
                                                     "edit": True})
    check("restoring full rights REMOVES the hook files at the next spawn",
          lambda: eq((ws6b["hooks"],
                      os.path.exists(os.path.join(cwd6, ".agents",
                                                  "hooks.json")),
                      os.path.exists(os.path.join(cwd6, ".agents",
                                                  "orgtree-rights.py"))),
                     (False, False, False), "removed"))
    _, res6, _ = _run("hookdeny")
    check("a hook denial on the wire is folded into permission_denials "
          "(and only a denial with orgtree's own mark counts)",
          lambda: eq((res6["status"],
                      [(d["tool_name"], d["tool_input"]) for d in res6["denials"]]),
                     ("completed", [("run_command",
                                     {"CommandLine": "echo HOOK-CMD"})]),
                     "denials"))

    print("§7 the mcp builders")
    okd, dropped = antigravityrun.deliverable_mcp({
        "good": {"command": "x"}, "url_one": {"url": "http://h"},
        "junk": {"note": "no transport"}})
    check("deliverable_mcp keeps command/url shapes and NAMES the rest",
          lambda: eq((sorted(okd), dropped),
                     (["good", "url_one"], ["junk"]), "split"))
    spec = antigravityrun.mcp_config({
        "s": {"command": "py", "args": ["-m", "x"], "env": {"B": 2, "A": "1"}},
        "h": {"url": "https://h/mcp", "headers": {"Authorization": "Bearer t"}}})
    check("stdio specs carry command/args/env, http specs serverUrl/headers "
          "— the shapes `agy mcp add` itself writes (measured)",
          lambda: eq(spec, {"mcpServers": {
              "h": {"serverUrl": "https://h/mcp",
                    "headers": {"Authorization": "Bearer t"}},
              "s": {"command": "py", "args": ["-m", "x"],
                    "env": {"A": "1", "B": "2"}}}}, "spec"))

    if FAIL:
        print(f"\n{PASS} passed, {len(FAIL)} FAILED")
        for label, tb in FAIL:
            print(f"\n--- {label}\n{tb}")
        return 1
    print(f"\n{PASS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
