"""geminirun (D-185): the ACP turn adapter, hermetic against fakegemini.

    python backend/tests/test_geminirun.py      (no pytest; plain asserts)

Every scenario speaks the wire shapes measured live 2026-08-29 (probe logs in
the implementing agent's scratch). The planted faults matter most: a fake
that reports the WRONG served model must be refused (the real CLI substitutes
its default silently), and a cancelled turn must come back interrupted with
no usage — an instrument that cannot see its planted fault proves nothing.
"""

import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-gemrun-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import geminirun, providers                           # noqa: E402

FAKE = os.path.join(os.path.dirname(__file__), "fakegemini.py")
HEAD = [sys.executable, FAKE]
PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


def _run(scenario, *, session_id=None, model="gemini-3.5-flash",
         mcp=None, env_extra=None, permission=None, on_event=None,
         interrupt_after=None):
    os.environ["FAKEGEMINI_SCENARIO"] = scenario
    turn = geminirun.GeminiTurn(
        HEAD, cwd=tempfile.mkdtemp(prefix="orgtree-gemcwd-"), model=model,
        session_id=session_id, mcp_servers=mcp or [],
        permission_decide=permission, on_event=on_event,
        env_extra=env_extra)
    sid = turn.start("hello from the suite")
    if interrupt_after is not None:
        time.sleep(interrupt_after)
        assert turn.interrupt(), "interrupt() refused"
    res = turn.wait(timeout=20)
    return sid, res, turn


def main():
    print("§1 a full turn against the measured wire")
    tmp = tempfile.mkdtemp(prefix="orgtree-gemmcp-")
    probe = os.path.join(tmp, "mcp.json")
    os.environ["FAKEGEMINI_MCPPROBE"] = probe
    orgtree_srv = {"orgtree": {"command": sys.executable,
                               "args": ["-m", "orgtree.mcptool"],
                               "env": {"ORGTREE_ORG": "o", "ORGTREE_NODE": "n"}}}
    sid, res, _ = _run("text", mcp=geminirun.acp_mcp_servers(orgtree_srv))
    check("the session id is harvested from session/new",
          lambda: eq(sid, "fake-gem-sess-0001", "sid"))
    check("the turn completes with the agent text folded in order",
          lambda: eq((res["status"], res["agent_text"]),
                     ("completed", "working… done."), "result"))
    check("usage normalizes PER MODEL with the main model tagged",
          lambda: eq((sorted(res["token_usage"]["models"]),
                      res["token_usage"]["main"]),
                     (["gemini-3.1-flash-lite", "gemini-3.5-flash"],
                      "gemini-3.5-flash"), "usage"))
    check("…and the cost fold prices BOTH models at their own rows",
          lambda: eq(providers.gemini_cost(res["token_usage"]), 0.01312,
                     "cost"))
    check("occupancy is the main model's prompt, not the side model's",
          lambda: eq(providers.gemini_occupancy(res["token_usage"]), 8290,
                     "occ"))

    print("§2 resume: session/load, replay gate, org powers on the wire")
    events = []
    sid2, res2, _ = _run("text", session_id="fake-gem-sess-0001",
                         mcp=geminirun.acp_mcp_servers(orgtree_srv),
                         on_event=lambda m: events.append(m))
    check("a resumed turn keeps the durable session id",
          lambda: eq(sid2, "fake-gem-sess-0001", "sid"))
    check("REPLAYED history is folded into neither the agent text nor the "
          "caller's event stream (the measured session/load replay)",
          lambda: eq(("REPLAYED-OLD-ANSWER" in res2["agent_text"],
                      any("REPLAYED-OLD-ANSWER" in json.dumps(e)
                          for e in events)), (False, False), "replay gate"))
    with open(probe, encoding="utf-8") as f:
        record = json.load(f)
    check("mcpServers rode session/new AND session/load — a resume that "
          "drops them strips every later turn of its org powers (D-180)",
          lambda: eq([(r["verb"], r["mcpServers"][0]["name"],
                       {e["name"]: e["value"]
                        for e in r["mcpServers"][0]["env"]}["ORGTREE_ORG"])
                      for r in record],
                     [("new", "orgtree", "o"), ("load", "orgtree", "o")],
                     "mcp probe"))
    os.environ.pop("FAKEGEMINI_MCPPROBE", None)

    print("§3 the planted faults the guards must SEE")

    def wrong_model():
        try:
            _run("wrongmodel")
        except geminirun.GeminiServerError as e:
            eq("model pin refused" in str(e)
               and "gemini-fake-default" in str(e), True, f"message {e!r}")
            return
        raise AssertionError("a silently substituted model was accepted")
    check("a session serving the WRONG model is refused loudly (the real "
          "CLI substitutes its default with no warning — measured)",
          wrong_model)

    print("§4 graceful interrupt (session/cancel)")
    sid4, res4, _ = _run("interrupt", interrupt_after=0.6)
    check("cancel resolves the prompt as an INTERRUPTED completed turn",
          lambda: eq((res4["status"], res4["stop_reason"]),
                     ("interrupted", "cancelled"), "status"))
    check("…with no usage document — an interrupted gemini turn books $0 "
          "(the measured wire carries no _meta on cancel)",
          lambda: eq((res4["token_usage"],
                      providers.gemini_cost(res4["token_usage"])),
                     (None, 0.0), "usage"))
    check("steer() always refuses — the wire has no verb, the caller queues",
          lambda: eq(geminirun.GeminiTurn.steer.__doc__ is not None
                     and _run("text")[2].steer("x"), False, "steer"))

    print("§5 credential hygiene, proven by the env probe")
    os.environ["ANTHROPIC_API_KEY"] = "planted-anthropic"
    os.environ["OPENAI_API_KEY"] = "planted-openai"
    os.environ["CLAUDECODE"] = "1"
    os.environ["CLAUDE_CODE_ENTRYPOINT"] = "planted"
    os.environ["GOOGLE_CLOUD_PROJECT"] = "keep-me"
    probe_env = os.path.join(tmp, "env.json")
    os.environ["FAKEGEMINI_ENVPROBE"] = (
        "ANTHROPIC_API_KEY,OPENAI_API_KEY,CLAUDECODE,CLAUDE_CODE_ENTRYPOINT,"
        "GOOGLE_CLOUD_PROJECT,ORGTREE_ORG")
    os.environ["FAKEGEMINI_ENVPROBE_PATH"] = probe_env
    _run("text", env_extra={"ORGTREE_ORG": "proof"})
    with open(probe_env, encoding="utf-8") as f:
        seen = json.load(f)
    check("the other providers' material is STRIPPED at spawn "
          "(anthropic, openai, claude-code marks)",
          lambda: eq((seen["ANTHROPIC_API_KEY"], seen["OPENAI_API_KEY"],
                      seen["CLAUDECODE"], seen["CLAUDE_CODE_ENTRYPOINT"]),
                     (None, None, None, None), "stripped"))
    check("…this provider's own env and the caller's extras pass through",
          lambda: eq((seen["GOOGLE_CLOUD_PROJECT"], seen["ORGTREE_ORG"]),
                     ("keep-me", "proof"), "kept"))
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CLAUDECODE",
              "CLAUDE_CODE_ENTRYPOINT", "GOOGLE_CLOUD_PROJECT",
              "FAKEGEMINI_ENVPROBE", "FAKEGEMINI_ENVPROBE_PATH"):
        os.environ.pop(k, None)

    print("§6 the ⚙-rights seam: permission requests")
    _, res6, _ = _run("permission", permission=lambda p: "allow_once")
    check("a policy that allows picks the option and the turn proceeds",
          lambda: eq("permission:selected:allow_once" in res6["agent_text"],
                     True, res6["agent_text"]))

    def broken(p):
        raise RuntimeError("policy exploded")
    _, res6b, _ = _run("permission", permission=broken)
    check("a BROKEN policy fails CLOSED (cancelled outcome), never a hang",
          lambda: eq("permission:cancelled" in res6b["agent_text"], True,
                     res6b["agent_text"]))
    _, res6c, _ = _run("permission", permission=None)
    check("no policy at all fails CLOSED too",
          lambda: eq("permission:cancelled" in res6c["agent_text"], True,
                     res6c["agent_text"]))

    print("§7 the mcp spec builders")
    okd, dropped = geminirun.deliverable_mcp({
        "good": {"command": "x"}, "url_one": {"url": "http://h"},
        "junk": {"note": "no transport"}})
    check("deliverable_mcp keeps command/url shapes and NAMES the rest",
          lambda: eq((sorted(okd), dropped),
                     (["good", "url_one"], ["junk"]), "split"))
    spec = geminirun.acp_mcp_servers({"s": {
        "command": "py", "args": ["-m", "x"], "env": {"B": "2", "A": "1"}}})
    check("stdio specs carry env as the measured ARRAY of {name,value}, "
          "sorted",
          lambda: eq(spec, [{"name": "s", "command": "py",
                             "args": ["-m", "x"],
                             "env": [{"name": "A", "value": "1"},
                                     {"name": "B", "value": "2"}]}], "spec"))

    print(f"\n{PASS} checks passed")


if __name__ == "__main__":
    main()
