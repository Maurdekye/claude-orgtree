"""D-186 supervisor dispatch: a gemini-tier node's turn runs the gemini leg.

    python backend/tests/test_gemini_dispatch.py   (no pytest; plain asserts)

Hermetic: drives `supervisor._run_one_turn` IN PROCESS against real org docs
on disk, with the gemini CLI resolved (via ORGTREE_GEMINI) to fakegemini.py —
the scripted ACP double test_geminirun.py already proves speaks the measured
wire. What THIS suite proves is the seam on top: dispatch on tier membership,
bookkeeping through `_after_turn`, session harvest + resume marker, identity
via GEMINI.md, org powers riding session verbs, the queue handoff through the
shared finally, interrupt through `interrupt_turn`, and the api hire gate.

ORGTREE_PORT is a PORT NOBODY SERVES (test-rig hygiene, measured on the
codex rig): this rig runs no backend, and an unset port would send a test's
mcptool traffic to the operator's live deployment.

Anti-vacuity: the signed-out plant and the wrongmodel plant must both be
SEEN by their detectors, and the queue-handoff proof is a follow carrier the
shared finally actually popped.
"""

import json
import os
import sys
import tempfile
import threading
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-gemdisp-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_PORT"] = "9"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

FAKEGEMINI = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fakegemini.py")
GEMINI_HOME = tempfile.mkdtemp(prefix="gemdisp-home-")
os.environ["ORGTREE_GEMINI"] = FAKEGEMINI
os.environ["ORGTREE_GEMINI_HOME"] = GEMINI_HOME
# hermetic on the codex axis too (the mirror of test_providers' new pin)
os.environ["ORGTREE_CODEX"] = os.path.join(DATA, "nowhere", "codex.exe")
os.environ["CODEX_HOME"] = os.path.join(DATA, "chome")


def sign_in(yes: bool) -> None:
    from orgtree import providers
    settings = os.path.join(GEMINI_HOME, "settings.json")
    if yes:
        with open(settings, "w", encoding="utf-8") as f:
            json.dump({"security": {"auth":
                                    {"selectedType": "gemini-api-key"}}}, f)
    elif os.path.exists(settings):
        os.remove(settings)
    providers._gemini_status_cache = None   # the 60s panel cache must not lie


sign_in(True)

from orgtree import store, supervisor                              # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []

STREAMED: list[dict] = []
supervisor.stream = lambda slug, nid, payload: STREAMED.append(dict(payload))
supervisor.CODEX_STEER_POLL = 0.2      # the pump must outrun the suite


def check(label, fn):
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def eq(got, want, what):
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


def mkorg(label: str, **tools_over) -> tuple[str, str]:
    org = store.create_org(f"zz gemdisp {label}")
    tools = {"bash": True, "web": False, "edit": True,
             "subagents": False, "mcp": []}
    tools.update(tools_over)
    r = org.hire(USER, None, "pro", 2, "gm", add_dirs=[], tools=tools,
                 org_visibility="team", charter="a gemini dispatch test agent")
    nid = r["node"]
    store.save_org(org)
    return org.d["slug"], nid


def run_turn(slug: str, nid: str, text: str, view: str | None = None):
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["busy"] = True
    return supervisor._run_one_turn(
        slug, nid, {"text": text, "view": text if view is None else view})


def node_doc(slug: str, nid: str) -> dict:
    return store.load_org(slug).d["nodes"][nid]


def journal_lines(slug: str, sid: str) -> list[dict]:
    p = os.path.join(supervisor.journal_store(), "projects", slug,
                     sid + ".jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def main():
    print("§1 dispatch + bookkeeping (the tier takes the gemini leg)")
    s1, n1 = mkorg("basic")
    os.environ["FAKEGEMINI_SCENARIO"] = "text"

    def t1():
        follow = run_turn(s1, n1, "hello gemini")
        eq(follow, None, "no queued follow-on")
        n = node_doc(s1, n1)
        eq(n["session_id"], "fake-gem-sess-0001", "session harvested")
        eq(n.get("gemini_session"), "fake-gem-sess-0001", "resume marker")
        eq("session_unrun" in n, False, "pardon spent by the harvest")
        # the fake's quota: main 8290in/48out + flash-lite 795in/36out —
        # the pro row prices the main model, the lite row the side model:
        # (8290·2 + 48·12 + 795·0.25 + 36·1.5) / 1e6 = 0.017409
        eq(round(float(n.get("cost_usd") or 0.0), 6), 0.017409, "cost booked")
        eq(n.get("occupancy"), 8290, "occupancy = main prompt")
        eq(supervisor.state(s1, n1).get("last_error"), None, "no error")
        deltas = "".join(p.get("text", "") for p in STREAMED
                         if p.get("kind") == "delta")
        eq("working… " in deltas and "done." in deltas, True,
           f"live deltas streamed ({deltas!r})")
    check("a pro-tier turn runs the gemini leg and books exactly", t1)

    def t1b():
        recs = journal_lines(s1, "fake-gem-sess-0001")
        kinds = [(r.get("type"),
                  (r.get("message") or {}).get("content")[0].get("type")
                  if isinstance((r.get("message") or {}).get("content"), list)
                  and (r.get("message") or {}).get("content") else None)
                 for r in recs]
        eq(kinds[0][0], "user", f"first record is the user row ({kinds[0]!r})")
        assert any(k == ("assistant", "thinking") for k in kinds), \
            f"thinking journaled: {kinds}"
        assert any(k == ("assistant", "text") for k in kinds), \
            f"agent text journaled: {kinds}"
        usage = [r for r in recs if (r.get("message") or {}).get("usage")]
        assert usage, "usage record present"
        u = usage[-1]["message"]["usage"]
        eq((u["input_tokens"], u["output_tokens"]), (8290, 48), "usage rec")
    check("the journal holds user, thinking, text and usage records", t1b)

    print("§2 tool events fold into the transcript vocabulary")
    s2, n2 = mkorg("tools")
    os.environ["FAKEGEMINI_SCENARIO"] = "toolevents"

    def t2():
        run_turn(s2, n2, "use your tool")
        # the tool round means TWO requests: occupancy is the divided
        # estimate, never the wire's raw per-turn sum (the 361% regression)
        eq(node_doc(s2, n2).get("occupancy"), 8290 // 2, "divided occupancy")
        recs = journal_lines(s2, "fake-gem-sess-0001")
        uses = [c for r in recs
                for c in (r.get("message") or {}).get("content") or []
                if isinstance(c, dict) and c.get("type") == "tool_use"]
        results = [c for r in recs
                   for c in (r.get("message") or {}).get("content") or []
                   if isinstance(c, dict) and c.get("type") == "tool_result"]
        eq([(u["name"], u["input"]) for u in uses],
           [("orgtree_ping", {"message": "hi"})], "tool_use fold")
        eq([(r["content"], r["is_error"]) for r in results],
           [("PONG:hi", False)], "tool_result fold")
    check("tool_call/tool_call_update become tool_use/tool_result", t2)

    print("§3 resume rides session/load with org powers; a re-mint starts "
          "fresh")
    probe = os.path.join(DATA, "mcpprobe.json")
    os.environ["FAKEGEMINI_MCPPROBE"] = probe
    os.environ["FAKEGEMINI_SCENARIO"] = "text"

    def t3():
        run_turn(s1, n1, "second turn")
        with open(probe, encoding="utf-8") as f:
            rec = json.load(f)
        eq([r["verb"] for r in rec], ["load"], "resumed via session/load")
        srv = {s["name"]: s for s in rec[0]["mcpServers"]}
        assert "orgtree" in srv, f"orgtree server on the wire: {srv.keys()}"
        env = {e["name"]: e["value"] for e in srv["orgtree"]["env"]}
        eq((env.get("ORGTREE_ORG"), env.get("ORGTREE_NODE"),
            env.get("ORGTREE_PORT")), (s1, n1, "9"),
           "per-agent identity in the server env, full set")
    check("a resumed turn loads the harvested session and re-attaches the "
          "org powers with per-agent identity", t3)

    def t3b():
        os.remove(probe)
        with store.DOC_LOCK:
            org = store.load_org(s1)
            org.node(n1)["session_id"] = "minted-foreign-uuid"
            org.node(n1)["session_unrun"] = True
            store.save_org(org)
        run_turn(s1, n1, "after a re-mint")
        with open(probe, encoding="utf-8") as f:
            rec = json.load(f)
        eq([r["verb"] for r in rec], ["new"], "fresh session, no load")
        eq(node_doc(s1, n1)["session_id"], "fake-gem-sess-0001",
           "harvest replaced the minted id")
        os.environ.pop("FAKEGEMINI_MCPPROBE", None)
    check("a minted/re-minted id is never loaded — the session starts fresh "
          "and the harvest takes over", t3b)

    print("§4 identity + env hygiene at the leg")

    def t4():
        gm = os.path.join(supervisor.scratch_dir(s1, n1), "GEMINI.md")
        assert os.path.exists(gm), "GEMINI.md written"
        body = open(gm, encoding="utf-8").read()
        assert n1 in body, "identity names the node"
        envp = os.path.join(DATA, "envprobe.json")
        os.environ["ANTHROPIC_API_KEY"] = "planted-anthropic"
        os.environ["OPENAI_API_KEY"] = "planted-openai"
        os.environ["FAKEGEMINI_ENVPROBE"] = (
            "ANTHROPIC_API_KEY,OPENAI_API_KEY,ORGTREE_ORG,ORGTREE_NODE")
        os.environ["FAKEGEMINI_ENVPROBE_PATH"] = envp
        try:
            run_turn(s1, n1, "probe the env")
            with open(envp, encoding="utf-8") as f:
                seen = json.load(f)
            eq((seen["ANTHROPIC_API_KEY"], seen["OPENAI_API_KEY"]),
               (None, None), "other providers stripped")
            eq((seen["ORGTREE_ORG"], seen["ORGTREE_NODE"]), (s1, n1),
               "own identity set")
        finally:
            for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                      "FAKEGEMINI_ENVPROBE", "FAKEGEMINI_ENVPROBE_PATH"):
                os.environ.pop(k, None)
    check("GEMINI.md carries the identity; the child env is hygienic", t4)

    print("§5 the planted faults the detectors must SEE")
    s5, n5 = mkorg("fault")

    def t5():
        sign_in(False)
        try:
            follow = run_turn(s5, n5, "doomed")
            eq(follow, None, "no follow")
            err = supervisor.state(s5, n5).get("last_error") or ""
            assert "not signed in" in err, f"error names the remedy: {err!r}"
            assert "gemini" in err.lower(), err
            n = node_doc(s5, n5)
            assert "gemini_session" not in n, "no session ever started"
        finally:
            sign_in(True)
    check("signed-out gemini fails loudly, never silently", t5)

    def t5b():
        os.environ["FAKEGEMINI_SCENARIO"] = "wrongmodel"
        try:
            run_turn(s5, n5, "wrong model")
            err = supervisor.state(s5, n5).get("last_error") or ""
            assert "model pin refused" in err, \
                f"the silent substitute must be refused: {err!r}"
        finally:
            os.environ["FAKEGEMINI_SCENARIO"] = "text"
    check("a session serving the wrong model fails the turn loudly "
          "(the CLI substitutes silently — measured)", t5b)

    print("§6 interrupt + the queue handoff through the shared finally")
    s6, n6 = mkorg("live")

    def t6():
        os.environ["FAKEGEMINI_SCENARIO"] = "interrupt"
        result: dict = {}

        def _run():
            result["follow"] = run_turn(s6, n6, "stall until cancelled")

        th = threading.Thread(target=_run, daemon=True)
        th.start()
        st = supervisor.state(s6, n6)
        deadline = time.time() + 10
        while time.time() < deadline and "gemini_turn" not in st:
            time.sleep(0.05)
        assert "gemini_turn" in st, "the live turn handle appeared"
        eq(st.get("responding"), True, "responding while live")
        ident_before = supervisor.identity_prompt(store.load_org(s6), n6)
        with store.DOC_LOCK:
            o = store.load_org(s6)
            o.node(n6)["charter"] = "identity changed while gemini is responding"
            store.save_org(o)
        ident_after = supervisor.identity_prompt(store.load_org(s6), n6)
        assert ident_after != ident_before, \
            "fixture failed to dirty the live turn's identity"
        # mid-turn mail: the pump pops it, the wire refuses (no steer verb),
        # and the text falls back to the queue for boundary delivery
        with supervisor._state_lock:
            st.setdefault("steer", []).append(
                {"text": "boundary mail", "view": "boundary mail"})
        def queued_boundary() -> bool:
            return any((m.get("text") if isinstance(m, dict) else m)
                       == "boundary mail" for m in (st.get("queue") or []))
        deadline = time.time() + 5
        while time.time() < deadline and not queued_boundary():
            time.sleep(0.05)
        assert queued_boundary(), "queued fallback"
        r = supervisor.interrupt_turn(s6, n6)
        eq(r.get("interrupted"), True, "interrupt accepted")
        th.join(timeout=20)
        assert not th.is_alive(), "the turn came back"
        # the shared finally popped the queued carrier into the follow —
        # the stranded-carrier proof (an early return would lose it)
        follow = result["follow"]
        text = follow.get("text") if isinstance(follow, dict) else follow
        eq(text, "boundary mail", "queue handoff via the shared finally")
        eq(supervisor.state(s6, n6).get("last_error"), None,
           "interrupted is a completed turn, not a failure")
        cost = float(node_doc(s6, n6).get("cost_usd") or 0.0)
        eq(cost, 0.0, "a cancelled turn carries no usage and books $0")
    check("identity dirtiness does not stop gemini's queued fallback; "
          "session/cancel hands it to the next turn", t6)

    print("§7 the connected-provider hire gate")
    from orgtree.api import provider_hire_gate
    from orgtree.ledger import LedgerError

    def expect_refusal(fn, needle):
        try:
            fn()
        except LedgerError as e:
            assert needle in str(e), f"said {e!r}, wanted {needle!r}"
            return
        raise AssertionError(f"no refusal ({needle!r} expected)")

    def t7():
        org = store.load_org(s1)
        provider_hire_gate(org, "pro")          # signed in: passes silently
        provider_hire_gate(org, "flash")
        provider_hire_gate(org, "fable")        # claude: never gated
        sign_in(False)
        try:
            expect_refusal(lambda: provider_hire_gate(org, "flash"),
                           "not signed in")
        finally:
            sign_in(True)
        org.d["kiosk"] = {"pin": "x"}
        expect_refusal(lambda: provider_hire_gate(org, "pro"), "kiosk")
        org.d.pop("kiosk")
        org.d["headless"] = True
        provider_hire_gate(org, "pro")   # api-key IS a keyed login: passes
        from orgtree import providers
        with open(os.path.join(GEMINI_HOME, "oauth_creds.json"), "w",
                  encoding="utf-8") as f:
            f.write("{}")
        with open(os.path.join(GEMINI_HOME, "settings.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"security": {"auth":
                                    {"selectedType": "oauth-personal"}}}, f)
        providers._gemini_status_cache = None
        expect_refusal(lambda: provider_hire_gate(org, "pro"), "headless")
        org.d.pop("headless")
        os.remove(os.path.join(GEMINI_HOME, "oauth_creds.json"))
        sign_in(True)
    check("gate: connected passes; signed-out, kiosk and headless-oauth "
          "refuse naming the remedy; api-key counts as keyed", t7)

    print()
    if FAIL:
        print(f"{PASS} passed, {len(FAIL)} FAILED")
        for label, tb in FAIL:
            print(f"\n--- {label}\n{tb}")
        sys.exit(1)
    print(f"{PASS} checks passed")


if __name__ == "__main__":
    main()
