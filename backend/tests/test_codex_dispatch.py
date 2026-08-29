"""M1b supervisor dispatch: a codex-tier node's turn runs the codex leg.

    python backend/tests/test_codex_dispatch.py    (no pytest; plain asserts)

Hermetic: drives `supervisor._run_one_turn` IN PROCESS against real org docs
on disk, with the codex CLI resolved (via ORGTREE_CODEX) to fakecodex.py —
the scripted app-server test double test_codexrun.py already proves speaks
the measured wire. What THIS suite proves is the seam on top: that a node
whose tier is codex takes the codex leg (spawn → thread → turn → normalized
bookkeeping) while rejoining `_run_one_turn`'s shared queue-handoff finally,
and that the claude machinery never runs for it.

The tier is planted by editing the org doc directly — the hire guard still
(correctly, pre-M4) refuses codex tiers, and this suite deliberately tests
the layer BENEATH that guard.

Anti-vacuity: §5 plants a known fault (signed-out codex) and requires the
failure detector to SEE it — per the working rule that an instrument
reporting "nothing found" must first prove it can find something.
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

DATA = tempfile.mkdtemp(prefix="orgtree-codexdisp-")
os.environ["ORGTREE_DATA"] = DATA
# a PORT NOBODY SERVES: the codex leg's tool dispatcher POSTs /api/agent on
# ORGTREE_PORT, and this rig runs no backend — left unset it would default to
# 7360 and the tool calls of a TEST would land on the operator's LIVE
# deployment (measured: the live backend answered "no such org"). Refused
# fast on loopback, so the tool answer becomes the unreachable text, which
# is exactly what §1 asserts on.
os.environ["ORGTREE_PORT"] = "9"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

# the codex "CLI" is the test double; the codex home is a throwaway dir whose
# auth.json holds an EMPTY chatgpt-shaped token doc — connect-state detection
# needs the key's existence, never its content
FAKECODEX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fakecodex.py")
CODEX_HOME = tempfile.mkdtemp(prefix="codexdisp-home-")
os.environ["ORGTREE_CODEX"] = FAKECODEX
os.environ["CODEX_HOME"] = CODEX_HOME


def sign_in(yes: bool) -> None:
    from orgtree import providers
    auth = os.path.join(CODEX_HOME, "auth.json")
    if yes:
        with open(auth, "w", encoding="utf-8") as f:
            f.write('{"tokens": {}}')
    elif os.path.exists(auth):
        os.remove(auth)
    providers._status_cache = None      # the 60s panel cache must not lie here


sign_in(True)

from orgtree import store, supervisor                              # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []

#: every stream() payload the turn emitted, recorded in place of the
#: websocket the API layer would inject
STREAMED: list[dict] = []
supervisor.stream = lambda slug, nid, payload: STREAMED.append(dict(payload))
supervisor.CODEX_STEER_POLL = 0.2      # the suite must outrun fakecodex's 8s


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


def mkorg(label: str) -> tuple[str, str]:
    """One org, one sol-tier node — a plain ledger hire since M4 put the
    codex tiers in the budget-bearing tables (the connected-provider gate is
    the API layer's, exercised in §6)."""
    org = store.create_org(f"zz codexdisp {label}")
    r = org.hire(USER, None, "sol", 2, "cx", add_dirs=[],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": False, "mcp": []},
                 org_visibility="team", charter="a codex dispatch test agent")
    nid = r["node"]
    store.save_org(org)
    return org.d["slug"], nid


def run_turn(slug: str, nid: str, text: str):
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["busy"] = True              # what _run_turn's callers always set
    return supervisor._run_one_turn(slug, nid, text)


def node_doc(slug: str, nid: str) -> dict:
    return store.load_org(slug).node(nid)


def main() -> int:
    print("§1 dispatch + bookkeeping (scenario: tool)")
    os.environ["FAKECODEX_SCENARIO"] = "tool"
    slug, nid = mkorg("basic")

    def t1():
        minted = node_doc(slug, nid)["session_id"]
        assert minted != "fake-thread-0001", "fixture: hire mints its own id"
        follow = run_turn(slug, nid, "hello codex")
        st = supervisor.state(slug, nid)
        n = node_doc(slug, nid)
        eq(follow, None, "no queued follow-up")
        eq(st["busy"], False, "busy cleared by the shared finally")
        eq(st["turns_run"], 1, "turn counted")
        eq(st["last_error"], None, "no error banner")
        eq(n["session_id"], "fake-thread-0001",
           "session id = harvested threadId, not the minted uuid")
        eq(n.get("codex_thread"), "fake-thread-0001",
           "…and marked as a REAL codex thread (the resume gate)")
        assert "session_unrun" not in n, "any never-run pardon is spent"
        # cost from the fake's tokenUsage total {in 30 incl cached 10, out 12}
        # at sol's CURRENT prices (4.00 / 0.40 / 20.00 per M)
        eq(n.get("cost_usd"), 0.000324, "dollars priced from tokens")
        eq(n.get("occupancy"), 30, "occupancy = input incl cached")
        eq(n.get("context_window"), 1_050_000, "codex window pinned")
        ring = n.get("turns") or []
        eq(len(ring), 1, "one turn ring entry")
        eq(ring[0].get("toks"), 12, "output tokens ride the ring")
        assert not n.get("inflight"), "inflight popped by the shared finally"
    check("codex tier takes the codex leg; books like a turn", t1)

    def t2():
        # the fake's tool scenario calls the first dynamic tool and echoes the
        # answer; with no backend listening the answer is the unreachable
        # text — which STILL proves item/tool/call round-tripped through the
        # supervisor's dispatcher (the live /api/agent door is test_mcptool's)
        text = "".join(p.get("text", "") for p in STREAMED
                       if p.get("kind") == "delta")
        assert "tool said:" in text, f"agent text never streamed: {text!r}"
    check("dynamic tool call answered through the seam", t2)

    def t2b():
        # second turn on the same node: the harvested threadId RESUMES
        # (fakecodex echoes a resumed id verbatim; a fresh start would mint
        # fake-thread-0001 again — indistinguishable — so plant a different
        # stored id and require it to SURVIVE, which only thread/resume does)
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.node(nid)["session_id"] = "fake-thread-resumed"
            o.node(nid)["codex_thread"] = "fake-thread-resumed"
            store.save_org(o)
        run_turn(slug, nid, "second turn")
        eq(node_doc(slug, nid)["session_id"], "fake-thread-resumed",
           "the stored codex thread was resumed, not replaced")
    check("a harvested thread id resumes on the next turn", t2b)

    def t2c():
        # M3: the supervisor's journal IS a transcript — found by the same
        # lookup, rendered by the same projection, occupancy by the same fold
        p = supervisor.transcript_path("fake-thread-resumed")
        assert p and "journals" in p.replace("\\", "/"), f"not found: {p!r}"
        assert supervisor.transcript_index().get("fake-thread-resumed") is not None, \
            "the one-walk index misses the journal store"
        chat = supervisor.read_chat(store.load_org(slug), nid)
        texts = [m.get("text", "") for m in chat["messages"]]
        assert any("second turn" in t for t in texts), \
            f"user bubble missing: {texts!r}"
        assert any("tool said:" in t for t in texts), \
            f"assistant bubble missing: {texts!r}"
        tools = [t for m in chat["messages"] for t in (m.get("tools") or [])]
        assert tools, f"dynamic tool chip missing: {tools!r}"
        hit = tools[0]
        assert "unreachable" in (hit.get("result") or ""), \
            f"tool result never attached to its chip: {hit!r}"
        eq(chat["occupancy"], 30, "occupancy folded from the journal")
    check("read_chat renders the codex turn from the journal", t2c)

    print("§2 spawn env hygiene (M5)")

    def t3():
        os.environ["FAKECODEX_ENVPROBE"] = (
            "ANTHROPIC_API_KEY,CLAUDE_CODE_ENTRYPOINT,CLAUDECODE,"
            "OPENAI_API_KEY,ORGTREE_ORG,ORGTREE_NODE")
        os.environ["ANTHROPIC_API_KEY"] = "planted-a"
        os.environ["CLAUDE_CODE_ENTRYPOINT"] = "planted-b"
        os.environ["CLAUDECODE"] = "planted-c"
        os.environ["OPENAI_API_KEY"] = "planted-d"
        s2, n2 = mkorg("env")
        try:
            run_turn(s2, n2, "probe the env")
        finally:
            for k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_ENTRYPOINT",
                      "CLAUDECODE", "OPENAI_API_KEY",
                      "FAKECODEX_ENVPROBE"):
                os.environ.pop(k, None)
        probe_p = os.path.join(supervisor.scratch_dir(s2, n2),
                               "envprobe.json")
        probe = json.load(open(probe_p, encoding="utf-8"))
        eq(probe.get("ANTHROPIC_API_KEY"), None, "anthropic key stripped")
        eq(probe.get("CLAUDE_CODE_ENTRYPOINT"), None, "claude-code var stripped")
        eq(probe.get("CLAUDECODE"), None, "claudecode flag stripped")
        eq(probe.get("OPENAI_API_KEY"), None,
           "stray api key stripped (billing lane stays the login)")
        eq(probe.get("ORGTREE_ORG"), s2, "org identity present")
        eq(probe.get("ORGTREE_NODE"), n2, "node identity present")
    check("codex child sees no cross-provider credentials", t3)

    print("§3 identity (M7)")

    def t4():
        ident_p = os.path.join(supervisor.scratch_dir(slug, nid), "AGENTS.md")
        ident = open(ident_p, encoding="utf-8").read()
        assert "cx" in ident, "identity prompt names the agent"
    check("AGENTS.md regenerated in the scratch cwd", t4)

    print("§4 steer + interrupt on the live session")

    def t5():
        os.environ["FAKECODEX_SCENARIO"] = "steer"
        # Fake processes otherwise reuse one globally-colliding thread id;
        # production ids are globally unique. Give this in-flight journal its
        # own id so read_chat cannot find §1's completed fixture first.
        os.environ["FAKECODEX_THREAD_ID"] = "fake-thread-steer"
        s3, n3 = mkorg("steer")
        STREAMED.clear()
        st = supervisor.state(s3, n3)
        done: list = []
        th = threading.Thread(
            target=lambda: done.append(run_turn(
                s3, n3, "[ORG NOTICES — 1 change]\n- peer status arrived")))
        th.start()
        for _ in range(300):
            if st.get("responding"):
                break
            time.sleep(0.02)
        assert st.get("responding"), "turn never reached responding"
        os.environ.pop("FAKECODEX_THREAD_ID", None)
        # The journal is opened at turn START. A running Codex turn must not
        # show streamed prose under a contradictory "no conversation yet".
        running_chat = supervisor.read_chat(store.load_org(s3), n3)
        assert any(m.get("role") == "user"
                   and "peer status arrived" in (m.get("text") or "")
                   for m in running_chat["messages"]), running_chat
        with supervisor._state_lock:
            st.setdefault("steer", []).append("FROM @user: mid-turn hello")
        th.join(20)
        assert not th.is_alive(), "steer turn never ended"
        text = "".join(p.get("text", "") for p in STREAMED
                       if p.get("kind") == "delta")
        assert "STEERED[" in text, f"steer never reached the turn: {text!r}"
        assert "mid-turn hello" in text, "steered body delivered verbatim"
        assert "[ORGTREE MAIL — delivered mid-task]" in text, \
            "steered mail wears the delivery envelope"
        finished_chat = supervisor.read_chat(store.load_org(s3), n3)
        user_text = "\n".join(m.get("text") or ""
                              for m in finished_chat["messages"]
                              if m.get("role") == "user")
        assert "peer status arrived" in user_text, \
            f"opening org notice vanished from history: {user_text!r}"
        assert "mid-turn hello" in user_text, \
            f"steered agent/user mail vanished from history: {user_text!r}"
        eq(st.get("steer"), [], "steer store drained")
        eq(st["turns_run"], 1, "steered turn completes normally")
    check("mid-turn mail steers into the live codex turn", t5)

    def t5b():
        os.environ["FAKECODEX_SCENARIO"] = "delta_pause"
        os.environ["FAKECODEX_THREAD_ID"] = "fake-thread-delta-pause"
        s3b, n3b = mkorg("delta-pause")
        STREAMED.clear()
        st = supervisor.state(s3b, n3b)
        done: list = []
        th = threading.Thread(
            target=lambda: done.append(run_turn(s3b, n3b, "show it live")))
        th.start()
        for _ in range(300):
            if st.get("responding"):
                break
            time.sleep(0.01)
        assert st.get("responding"), "turn never reached responding"
        os.environ.pop("FAKECODEX_THREAD_ID", None)
        time.sleep(0.25)
        assert th.is_alive(), "fixture completed before the live-flush check"
        text = "".join(p.get("text", "") for p in STREAMED
                       if p.get("kind") == "delta")
        assert "short live fragment" in text, \
            f"a short delta remained buffered until another event: {text!r}"
        th.join(20)
        assert not th.is_alive(), "paused-delta turn never ended"
    check("a short Codex delta flushes while the item is still open", t5b)

    def t6():
        os.environ["FAKECODEX_SCENARIO"] = "interrupt"
        s4, n4 = mkorg("intr")
        st = supervisor.state(s4, n4)
        done: list = []
        th = threading.Thread(
            target=lambda: done.append(run_turn(s4, n4, "stall for ⏸")))
        th.start()
        for _ in range(300):
            if st.get("responding"):
                break
            time.sleep(0.02)
        assert st.get("responding"), "turn never reached responding"
        r = supervisor.interrupt_turn(s4, n4)
        eq(r.get("interrupted"), True, "interrupt accepted")
        th.join(20)
        assert not th.is_alive(), "interrupted turn never ended"
        eq(st["turns_run"], 1, "an interrupted turn is a completed turn")
        eq(st["last_error"], None, "…not an error")
        eq(st.get("codex_turn"), None, "live turn ref dropped")
        eq(st["busy"], False, "busy cleared")
    check("⏸ interrupts the live codex turn gracefully", t6)

    print("§5 the planted fault — failure must be SEEN (anti-vacuity)")

    def t7():
        os.environ["FAKECODEX_SCENARIO"] = "tool"
        sign_in(False)                                 # the planted fault
        s5, n5 = mkorg("fault")
        try:
            follow = run_turn(s5, n5, "should fail")
        finally:
            sign_in(True)
        st = supervisor.state(s5, n5)
        eq(follow, None, "failed turn hands nothing on")
        assert st["last_error"] and "not signed in" in st["last_error"], \
            f"the fault was not seen: {st['last_error']!r}"
        eq(st["turns_run"], 0, "a failed turn is not counted")
        eq(st["busy"], False, "busy cleared on the failure path too")
        n = node_doc(s5, n5)
        assert "codex_thread" not in n, "no thread ever started"
    check("signed-out codex fails loudly, never silently", t7)

    print("§6 the connected-provider hire gate (M4)")
    from orgtree.api import provider_hire_gate
    from orgtree.ledger import LedgerError

    def expect_refusal(fn, needle):
        try:
            fn()
        except LedgerError as e:
            assert needle in str(e), f"error said {e!r}, wanted {needle!r}"
            return
        raise AssertionError(f"no refusal ({needle!r} expected)")

    def t8():
        org = store.load_org(slug)
        provider_hire_gate(org, "sol")          # signed in: passes silently
        provider_hire_gate(org, "fable")        # claude: never gated
        provider_hire_gate(org, None)           # no tier: not this gate's job
        sign_in(False)
        try:
            expect_refusal(lambda: provider_hire_gate(org, "luna"),
                           "not signed in")
            provider_hire_gate(org, "fable")    # claude still ungated
        finally:
            sign_in(True)
        org.d["kiosk"] = {"pin": "x"}
        expect_refusal(lambda: provider_hire_gate(org, "terra"), "kiosk")
        org.d.pop("kiosk")
        org.d["headless"] = True
        # this rig's auth is a subscription-shaped login, not an API key
        expect_refusal(lambda: provider_hire_gate(org, "sol"), "headless")
        provider_hire_gate(org, "fable")
        org.d.pop("headless")
    check("gate: connected passes; signed-out, kiosk and headless-"
          "subscription refuse, naming the remedy; claude ungated", t8)

    print()
    if FAIL:
        for label, tb in FAIL:
            print(f"FAILED: {label}\n{tb}")
        print(f"{PASS} passed, {len(FAIL)} FAILED")
        return 1
    print(f"{PASS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
