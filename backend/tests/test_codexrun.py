"""codexrun: the codex turn runner, hermetically, against fakecodex.py.

    python backend/tests/test_codexrun.py      (no pytest; plain asserts)

Every leg drives a REAL child process speaking real NDJSON JSON-RPC — the
same wire the production codex app-server speaks (shapes verified live,
design doc Appendix B/C) — so what's proven is the runner's actual plumbing:
spawn, initialize, thread open, dynamic-tool answering on the reader thread,
steer/interrupt on the live session, normalized fold, process teardown.

Anti-vacuity: §1 asserts the tool dispatcher was CALLED with the planted
arguments and that its answer surfaced in the agent text — a runner that
never wired the dispatcher would fail both, not pass by silence.
"""

import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-codexrun-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
# an unreachable hub, or every org this rig creates registers against the
# operator's REAL roster (test_external_mail §1 guards exactly this)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import codexrun                                       # noqa: E402

FAKE = [sys.executable,
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "fakecodex.py")]
PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


def main():
    tmp = tempfile.mkdtemp(prefix="codexrun-t-")

    print("§1 a full turn with a dynamic tool answered in-process")
    calls: list[tuple[str, dict]] = []

    def dispatch(tool, args):
        calls.append((tool, args))
        return f"ack:{args.get('message')}"

    turn = codexrun.CodexTurn(
        FAKE, cwd=tmp, model="gpt-5.6-terra", effort="low", thread_id=None,
        dynamic_tools=[codexrun._dyn_tool(
            "orgtree_ping", "ping the supervisor",
            {"type": "object", "properties": {"message": {"type": "string"}},
             "required": ["message"]})],
        tool_dispatch=dispatch,
        env_extra={"FAKECODEX_SCENARIO": "tool"})
    tid = turn.start("do the thing")
    res = turn.wait(timeout=20)
    check("the thread id is harvested and durable",
          lambda: eq(tid, "fake-thread-0001", "thread id"))
    check("the dispatcher was CALLED with the planted arguments",
          lambda: eq(calls, [("orgtree_ping", {"message": "from-fake"})],
                     "dispatch calls"))
    check("…and its answer surfaced in the model's text",
          lambda: eq("tool said: ack:from-fake" in res["agent_text"], True,
                     f"agent text {res['agent_text']!r}"))
    check("the turn normalizes to completed",
          lambda: eq(res["status"], codexrun.STATUS_COMPLETED, "status"))
    check("token usage folded from the notification stream",
          lambda: eq((res["token_usage"] or {}).get("total", {})
                     .get("totalTokens"), 42, "tokens"))
    check("rate-limit standing folded too",
          lambda: eq((res["rate_limits"] or {}).get("limitId"), "codex",
                     "limits"))

    print("§2 steer lands on the live session")
    turn2 = codexrun.CodexTurn(
        FAKE, cwd=tmp, model=None, effort=None, thread_id=None,
        env_extra={"FAKECODEX_SCENARIO": "steer"})
    turn2.start("long thing")
    check("steer is accepted while the turn runs",
          lambda: eq(turn2.steer("new orders"), True, "steer ack"))
    res2 = turn2.wait(timeout=20)
    check("…and the steered text reached the turn",
          lambda: eq("STEERED[new orders]" in res2["agent_text"], True,
                     f"text {res2['agent_text']!r}"))

    print("§3 graceful interrupt normalizes to 'interrupted'")
    turn3 = codexrun.CodexTurn(
        FAKE, cwd=tmp, model=None, effort=None, thread_id=None,
        env_extra={"FAKECODEX_SCENARIO": "interrupt"})
    turn3.start("never-ending thing")
    check("interrupt is accepted",
          lambda: eq(turn3.interrupt(), True, "interrupt ack"))
    res3 = turn3.wait(timeout=20)
    check("the turn ends with the normalized interrupted status",
          lambda: eq(res3["status"], codexrun.STATUS_INTERRUPTED, "status"))

    print("§4 credential hygiene: the child sees NO cross-provider secrets")
    probe_path = os.path.join(tmp, "envprobe.json")
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-FAKE-must-not-leak"
    os.environ["OPENAI_API_KEY"] = "sk-proj-FAKE-must-not-leak"
    try:
        turn4 = codexrun.CodexTurn(
            FAKE, cwd=tmp, model=None, effort=None, thread_id=None,
            codex_home=os.path.join(tmp, "home-x"),
            env_extra={"FAKECODEX_SCENARIO": "tool",
                       "FAKECODEX_ENVPROBE":
                           "ANTHROPIC_API_KEY,OPENAI_API_KEY,CODEX_HOME",
                       "FAKECODEX_ENVPROBE_PATH": probe_path})
        turn4.start("probe env")
        turn4.wait(timeout=20)
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ.pop("OPENAI_API_KEY", None)
    seen = json.load(open(probe_path, encoding="utf-8"))
    check("ANTHROPIC_API_KEY was stripped (one credential per spawn)",
          lambda: eq(seen.get("ANTHROPIC_API_KEY"), None, "anthropic key"))
    check("a stray OPENAI_API_KEY was stripped (it would silently flip the "
          "billing lane off the subscription login)",
          lambda: eq(seen.get("OPENAI_API_KEY"), None, "openai key"))
    check("…while CODEX_HOME override reached the child (anti-vacuity: the "
          "probe demonstrably reports env it DOES see)",
          lambda: eq(seen.get("CODEX_HOME"), os.path.join(tmp, "home-x"),
                     "codex home"))

    print("§5 resume path re-enters an existing thread")
    turn5 = codexrun.CodexTurn(
        FAKE, cwd=tmp, model=None, effort=None,
        thread_id="carried-thread-77",
        env_extra={"FAKECODEX_SCENARIO": "tool"})
    tid5 = turn5.start("again")
    turn5.wait(timeout=20)
    check("thread/resume keeps the recorded session id",
          lambda: eq(tid5, "carried-thread-77", "resumed id"))

    print("§6 image UserInput rides beside the text")
    input_probe = os.path.join(tmp, "input-probe.json")
    turn6 = codexrun.CodexTurn(
        FAKE, cwd=tmp, model=None, effort=None, thread_id=None,
        env_extra={"FAKECODEX_SCENARIO": "tool",
                   "FAKECODEX_INPUTPROBE": input_probe})
    turn6.start("look at this", [{
        "type": "image", "url": "data:image/png;base64,AA=="}])
    turn6.wait(timeout=20)
    image_input = json.load(open(input_probe, encoding="utf-8"))
    check("text and image are both sent in the turn/start input",
          lambda: eq(image_input, [
              {"type": "text", "text": "look at this"},
              {"type": "image", "url": "data:image/png;base64,AA=="}],
              "turn input"))

    print("§7 native fork + compaction waits for durable completion")
    compact = codexrun.compact_fork(
        FAKE, cwd=tmp, model="gpt-5.6-sol",
        thread_id="source-thread-88", timeout=20,
        env_extra={"FAKECODEX_SCENARIO": "compact",
                   "FAKECODEX_FORK_ID": "successor-thread-89"})
    check("the provider minted a distinct fork for the successor",
          lambda: eq(compact.get("thread_id"), "successor-thread-89",
                     "forked id"))
    check("the completed compact turn's usage is returned for accounting",
          lambda: eq((compact.get("token_usage") or {}).get("total", {})
                     .get("inputTokens"), 44, "compact input tokens"))

    def compact_failure_seen():
        try:
            codexrun.compact_fork(
                FAKE, cwd=tmp, model="gpt-5.6-sol",
                thread_id="source-thread-fail", timeout=20,
                env_extra={"FAKECODEX_SCENARIO": "compact_fail",
                           "FAKECODEX_FORK_ID": "failed-successor"})
        except codexrun.CodexServerError as e:
            if "compact turn failed" not in str(e):
                raise AssertionError(f"wrong compact failure: {e}")
            return
        raise AssertionError("a failed compact turn was reported successful")

    check("a planted failed compact turn is rejected (anti-vacuity)",
          compact_failure_seen)

    print("§8 a warm client serves two turns on one initialized process")
    class CountingClient(codexrun.AppServerClient):
        def __init__(self, *args, **kwargs):
            self.init_calls = []
            super().__init__(*args, **kwargs)

        def request(self, method, params, timeout=codexrun.REQUEST_TIMEOUT):
            if method == "initialize":
                self.init_calls.append(method)
            return super().request(method, params, timeout)

    client = CountingClient(
        FAKE, cwd=tmp, env_extra={"FAKECODEX_SCENARIO": "tool"})
    pid8 = client.proc.pid
    try:
        warm1 = codexrun.CodexTurn(
            FAKE, cwd=tmp, model=None, effort=None, thread_id=None,
            client=client)
        warm1.start("warm first")
        warm1.wait(timeout=20, close_client=False)
        client.unbind()
        warm2 = codexrun.CodexTurn(
            FAKE, cwd=tmp, model=None, effort=None,
            thread_id="fake-thread-0001", client=client)
        warm2.start("warm second")
        warm2.wait(timeout=20, close_client=False)
        client.unbind()
        check("both claims retain the same live app-server PID",
              lambda: eq((client.proc.pid, client.proc.poll()),
                         (pid8, None), "warm process"))
        check("JSON-RPC initialize is issued exactly once per warm process",
              lambda: eq(client.init_calls, ["initialize"],
                         "initialize calls"))
    finally:
        client.close()

    print("§9 close() reaps the WHOLE process tree (2026-08-30 orphan-lock)")

    def tree_teardown():
        # the real app-server forks a native engine child + a code-mode-host
        # child; fakecodex forks one long-sleep grandchild and records its pid.
        # A bare parent-kill orphans it and it keeps the ~/.codex thread lock,
        # which is what broke `thread/resume` on every second codex turn.
        import time
        pidfile = os.path.join(tmp, "child.pid")
        cl = codexrun.AppServerClient(
            list(FAKE), cwd=tmp,
            env_extra={"FAKECODEX_CHILD_PIDFILE": pidfile})
        cl.initialize()
        for _ in range(100):
            if os.path.exists(pidfile):
                break
            time.sleep(0.02)
        child_pid = int(open(pidfile, encoding="utf-8").read().strip())

        def alive(p):
            if os.name == "nt":
                r = __import__("subprocess").run(
                    ["tasklist", "/FI", f"PID eq {p}"],
                    capture_output=True, text=True)
                return str(p) in r.stdout
            try:
                os.kill(p, 0)
                return True
            except OSError:
                return False

        eq(alive(child_pid), True, "the forked child is running before close()")
        cl.close()
        gone = False
        for _ in range(50):
            if not alive(child_pid):
                gone = True
                break
            time.sleep(0.1)
        if not gone:
            try:
                (os.kill(child_pid, 9) if os.name != "nt"
                 else __import__("subprocess").run(
                     ["taskkill", "/F", "/PID", str(child_pid)],
                     capture_output=True))
            except OSError:
                pass
        eq(gone, True, "close() left NO orphan (parent-only kill would)")

    check("close() taskkills the tree and waits — no orphan holds the lock",
          tree_teardown)

    print(f"\n{PASS} checks passed")


if __name__ == "__main__":
    main()
