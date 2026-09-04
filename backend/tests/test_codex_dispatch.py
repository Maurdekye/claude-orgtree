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

import base64
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
os.environ["ORGTREE_WARM"] = "1"
os.environ["ORGTREE_WARM_POLL"] = "3600"  # keeper passes are manual here
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

from orgtree import store, supervisor, warmpool                    # noqa: E402
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


def mkdeep_luna(label: str) -> tuple[str, str]:
    """Fresh manual-hire shape: a Luna below another agent, initially with
    no direct user audience. Both seats are fake app-servers, so this spends
    no provider call while still giving us real child PIDs."""
    org = store.create_org(f"zz codexdisp deep {label}")
    boss = org.hire(
        USER, None, "luna", 1, "boss", add_dirs=[],
        tools={"bash": True, "web": False, "edit": True,
               "subagents": False, "mcp": []},
        org_visibility="team", charter="deep Luna fixture parent")["node"]
    nid = org.hire(
        boss, boss, "luna", 0, "new-luna", add_dirs=[],
        tools={"bash": True, "web": False, "edit": True,
               "subagents": False, "mcp": []},
        org_visibility="team", charter="fresh manual Luna hire")["node"]
    store.save_org(org)
    return org.d["slug"], nid


def run_turn(slug: str, nid: str, text: str, view: str | None = None):
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["busy"] = True              # what _run_turn's callers always set
    return supervisor._run_one_turn(
        slug, nid, {"text": text, "view": text if view is None else view})


def node_doc(slug: str, nid: str) -> dict:
    return store.load_org(slug).node(nid)


def main() -> int:
    print("§0 first direct-user turn keeps its real pre-warmed PID")
    os.environ["FAKECODEX_SCENARIO"] = "delta_pause"
    deep_slug, deep_nid = mkdeep_luna("audience")
    warmpool.keeper_pass_now()
    with warmpool._pool_lock:
        before_wp = warmpool._pool.get((deep_slug, deep_nid))
    assert isinstance(before_wp, warmpool.CodexWarmProc)
    pid_before = before_wp.proc.pid
    stable_before = supervisor.identity_prompt(
        store.load_org(deep_slug), deep_nid)

    # Exact production mutation: the user's first direct message is persisted
    # in the same save as user_deep_reach's automatic audience grant.
    with store.DOC_LOCK:
        o = store.load_org(deep_slug)
        o.post_mail(USER, deep_nid, "first direct user prompt")
        o.user_deep_reach(deep_nid, "first direct user prompt")
        store.save_org(o)
    changed = store.load_org(deep_slug)
    assert changed._has_audience(deep_nid, USER)
    assert supervisor.identity_prompt(changed, deep_nid) == stable_before, (
        "the live audience grant rewrote the parked app-server identity")
    assert "you currently hold a USER AUDIENCE" in supervisor.org_state_block(
        changed, deep_nid), "the newly granted authority missed the turn envelope"
    warmpool.keeper_pass_now()
    with warmpool._pool_lock:
        after_grant_wp = warmpool._pool.get((deep_slug, deep_nid))
    assert isinstance(after_grant_wp, warmpool.CodexWarmProc)
    eq(after_grant_wp.proc.pid, pid_before,
       "PID after first-message audience grant, before claim")

    turn_error: list[BaseException] = []

    def first_turn() -> None:
        try:
            run_turn(deep_slug, deep_nid, "first direct user prompt")
        except BaseException as exc:                              # noqa: BLE001
            turn_error.append(exc)

    th0 = threading.Thread(target=first_turn, daemon=True)
    th0.start()
    deadline = time.time() + 5
    during_wp = None
    while time.time() < deadline:
        with warmpool._pool_lock:
            during_wp = warmpool._serving.get((deep_slug, deep_nid))
        if during_wp is not None:
            break
        time.sleep(0.01)
    assert isinstance(during_wp, warmpool.CodexWarmProc), (
        "first turn never claimed the pre-warmed app-server")
    pid_during = during_wp.proc.pid
    th0.join(10)
    assert not th0.is_alive() and not turn_error, turn_error
    with warmpool._pool_lock:
        after_wp = warmpool._pool.get((deep_slug, deep_nid))
    assert isinstance(after_wp, warmpool.CodexWarmProc)
    eq((pid_before, pid_during, after_wp.proc.pid),
       (pid_before, pid_before, pid_before),
       "PID before/during/after the first direct-user turn")
    warmpool.kill_org(deep_slug, "suite-teardown")
    check("fresh deep Luna stays on one PID across its first user turn",
          lambda: None)

    print("§1 dispatch + bookkeeping (scenario: tool)")
    os.environ["FAKECODEX_SCENARIO"] = "tool"
    slug, nid = mkorg("basic")
    # Exact production sequence from the regression report: backend startup
    # keeper pass, no prompt yet, first prompt, completed turn. The child is a
    # real fakecodex app-server process, so PID continuity proves process
    # persistence rather than a bookkeeping flag.
    warmpool.keeper_pass_now()
    with warmpool._pool_lock:
        boot_wp = warmpool._pool.get((slug, nid))
    assert isinstance(boot_wp, warmpool.CodexWarmProc), (
        "startup keeper did not prewarm the idle eligible Codex agent")
    boot_pid = boot_wp.proc.pid
    assert boot_wp.alive() and warmpool.is_warm(slug, nid)
    eq(supervisor.state(slug, nid)["turns_run"], 0,
       "prewarm starts no turn")

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
        with warmpool._pool_lock:
            parked = warmpool._pool.get((slug, nid))
        assert isinstance(parked, warmpool.CodexWarmProc), (
            "completed Codex turn was not returned to the warm pool")
        eq(parked.proc.pid, boot_pid,
           "post-turn park must retain the startup app-server PID")
        assert parked.alive() and warmpool.is_warm(slug, nid), (
            "startup app-server did not survive the turn boundary")
    check("codex tier takes the codex leg; books like a turn", t1)

    print("§1b thread-cumulative usage books per-turn deltas")
    os.environ["FAKECODEX_SCENARIO"] = "cumulative_usage"
    os.environ["FAKECODEX_THREAD_ID"] = "fake-cumulative-thread"
    cumulative_slug, cumulative_nid = mkorg("cumulative-usage")
    warmpool.keeper_pass_now()

    def t1c():
        run_turn(cumulative_slug, cumulative_nid, "first measured turn")
        after_first = float(node_doc(
            cumulative_slug, cumulative_nid).get("cost_usd") or 0.0)
        run_turn(cumulative_slug, cumulative_nid, "second measured turn")
        n = node_doc(cumulative_slug, cumulative_nid)
        ring = n.get("turns") or []
        eq([turn.get("cost") for turn in ring],
           [3.512059, 0.148005],
           "each ring row is one turn, not a growing session snapshot")
        eq(round(float(n.get("cost_usd") or 0.0) - after_first, 6),
           0.148005, "the node's second-turn increment is the delta")
        eq((n.get("codex_usage_total") or {}).get("totalTokens"), 6370404,
           "raw session counter is retained as the next-turn baseline")
        assert not n.get("codex_usage_reset"), (
            "an increasing counter was falsely marked as a reset")
    check("the exact $3.660064 second snapshot books only its $0.148005 "
          "delta", t1c)

    def t1d():
        run_turn(cumulative_slug, cumulative_nid, "counter reset turn")
        n = node_doc(cumulative_slug, cumulative_nid)
        ring = n.get("turns") or []
        eq(ring[-1].get("cost"), 0.000512,
           "reset books the current snapshot whole")
        marker = n.get("codex_usage_reset") or {}
        eq(marker.get("policy"), "book_current_snapshot",
           "reset policy is durable")
        assert "totalTokens" in (marker.get("fields") or []), marker
    check("a backwards counter is non-negative, non-zero and durably marked",
          t1d)
    os.environ["FAKECODEX_SCENARIO"] = "tool"
    os.environ.pop("FAKECODEX_THREAD_ID", None)

    def t1e():
        # Claude's supervisor boundary already supplies PER-TURN dollars.
        # Preserve the measured decreasing sequence: a generic "harmonizer"
        # that starts treating these as cumulative would subtract or discard
        # the second, smaller but entirely real turn.
        co = store.create_org("zz codexdisp claude-cost-control")
        cn = co.hire(
            USER, None, "opus", 0, "claude", add_dirs=[],
            tools={"bash": True, "web": False, "edit": True,
                   "subagents": False, "mcp": []},
            org_visibility="team", charter="Claude cost control")["node"]
        store.save_org(co)
        supervisor._after_turn(
            co.d["slug"], cn, co,
            {"status": "completed", "total_cost_usd": 2.092458,
             "duration_ms": 1, "usage": {}}, {}, 100)
        supervisor._after_turn(
            co.d["slug"], cn, co,
            {"status": "completed", "total_cost_usd": 0.470193,
             "duration_ms": 1, "usage": {}}, {}, 110)
        n = node_doc(co.d["slug"], cn)
        eq([t.get("cost") for t in n.get("turns") or []],
           [2.092458, 0.470193],
           "Claude's decreasing provider results remain per-turn")
        eq(n.get("cost_usd"), 2.562651,
           "Claude lifetime cost sums the two per-turn results")
    check("Claude's measured decreasing cost shape stays per-turn", t1e)

    def t1b():
        # Upgrade fixture: this node completed a turn under the old Codex
        # integration and persisted the app-server's 258.4k observation. The
        # desk must derive the current tier capability immediately instead of
        # waiting for another turn to happen to rewrite the document.
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.node(nid)["context_window"] = 258_400
            store.save_org(o)
        from starlette.requests import Request
        from orgtree.api import org_tree
        tree = org_tree(slug, Request({"type": "http", "headers": []}))
        row = tree["roots"][0]
        eq(row.get("context_window"), 1_050_000,
           "tree derives current Sol capability over stale stored value")
        # Prove the stale fixture really remained stale; otherwise this check
        # could pass because setup accidentally migrated the document.
        eq(node_doc(slug, nid).get("context_window"), 258_400,
           "anti-vacuity: stored value is still stale")
    check("existing Codex nodes show the current context window immediately",
          t1b)

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

    def t2d():
        # Exact user report: a screenshot is attached to the opening mail of a
        # Codex turn. Exercise the entire seam (mail drain -> validation ->
        # provider dispatch -> turn/start), not only the block converter.
        from PIL import Image
        os.environ["FAKECODEX_SCENARIO"] = "tool"
        os.environ["FAKECODEX_THREAD_ID"] = "fake-thread-image"
        si, ni = mkorg("image")
        upload_dir = os.path.join(supervisor.scratch_dir(si, ni), "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        image_path = os.path.join(upload_dir, "screenshot.png")
        Image.new("RGB", (2, 2), (45, 120, 115)).save(image_path)
        raw = open(image_path, "rb").read()
        with store.DOC_LOCK:
            oi = store.load_org(si)
            oi.post_mail(USER, ni, "please inspect the screenshot",
                         attachments=[{"name": "screenshot.png",
                                       "path": "uploads/screenshot.png",
                                       "bytes": len(raw)}])
            store.save_org(oi)
        input_probe = os.path.join(supervisor.scratch_dir(si, ni),
                                   "input-probe.json")
        os.environ["FAKECODEX_INPUTPROBE"] = input_probe
        try:
            run_turn(si, ni, "act on the user's message")
        finally:
            os.environ.pop("FAKECODEX_INPUTPROBE", None)
            os.environ.pop("FAKECODEX_THREAD_ID", None)
        sent = json.load(open(input_probe, encoding="utf-8"))
        assert len(sent) == 2, f"image was dropped at dispatch: {sent!r}"
        assert sent[0].get("type") == "text" \
            and "please inspect" in sent[0].get("text", ""), sent
        assert sent[1].get("type") == "image", sent
        url = str(sent[1].get("url") or "")
        assert url.startswith("data:image/png;base64,"), url[:80]
        eq(base64.b64decode(url.split(",", 1)[1]), raw,
           "the screenshot bytes reaching Codex")
    check("a user's opening-turn image reaches Codex as real image input", t2d)

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
                s3, n3, "[ORG NOTICES — 1 change]\n- peer status arrived",
                view="")))
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
        assert not any("peer status arrived" in (m.get("text") or "")
                       for m in running_chat["messages"]), running_chat
        ident_before = supervisor.identity_prompt(store.load_org(s3), n3)
        with store.DOC_LOCK:
            o = store.load_org(s3)
            o.node(n3)["charter"] = "identity changed while codex is responding"
            store.save_org(o)
        ident_after = supervisor.identity_prompt(store.load_org(s3), n3)
        assert ident_after != ident_before, \
            "fixture failed to dirty the live turn's identity"
        with supervisor._state_lock:
            st.setdefault("steer", []).append({
                "text": "FROM @user: mid-turn hello",
                "view": "FROM @user: mid-turn hello"})
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
        assert "peer status arrived" not in user_text, \
            f"machine-only opening notice leaked into history: {user_text!r}"
        assert "mid-turn hello" in user_text, \
            f"steered agent/user mail vanished from history: {user_text!r}"
        eq(st.get("steer"), [], "steer store drained")
        eq(st["turns_run"], 1, "steered turn completes normally")
    check("identity dirtiness does not stop live codex steering", t5)

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

    def t6b():
        # interrupt_before_archive (the retire/dissolve fix, 2026-09-03):
        # a busy node is interrupted AND waited on — the caller never learns
        # "archived" while the turn's own `finally` (cost, D-234's queued
        # switch) is still running. A queued follow-up must not chain behind
        # the interrupt either — the node is about to be archived.
        os.environ["FAKECODEX_SCENARIO"] = "interrupt"
        s6, n6 = mkorg("intr-arch")
        st = supervisor.state(s6, n6)
        with supervisor._state_lock:                        # noqa: SLF001
            st["queue"].append({"text": "should never run", "toks": []})
        done: list = []
        th = threading.Thread(
            target=lambda: done.append(run_turn(s6, n6, "stall for archive")))
        th.start()
        for _ in range(300):
            if st.get("responding"):
                break
            time.sleep(0.02)
        assert st.get("responding"), "turn never reached responding"
        org = store.load_org(s6)
        warnings = supervisor.interrupt_before_archive(s6, org, n6)
        eq(warnings, [], "the fake CLI settles well inside the timeout")
        eq(st["busy"], False,
           "interrupt_before_archive returned only after busy cleared")
        eq(st["queue"], [], "the queued follow-up was cleared, not chained")
        th.join(5)
        assert not th.is_alive(), "the interrupted turn's own thread lingered"
    check("interrupt_before_archive waits for the turn boundary and drops "
          "the queue", t6b)

    def t6c():
        # D-234 end-to-end, both interrupt paths: a switch queued mid-turn
        # actually APPLIES once the turn ends via interrupt. Neither path is
        # a second way to end a turn — both just wait on the SAME shared
        # `finally` (`_apply_pending_switch_locked` fires there
        # unconditionally on every exit) that a plain completed turn reaches;
        # this proves that by observing the model actually flip.
        os.environ["FAKECODEX_SCENARIO"] = "interrupt"

        def start_and_queue(label):
            slug, nid = mkorg(label)
            st = supervisor.state(slug, nid)
            done: list = []
            th = threading.Thread(
                target=lambda: done.append(
                    run_turn(slug, nid, "stall for switch")))
            th.start()
            for _ in range(300):
                if st.get("responding"):
                    break
                time.sleep(0.02)
            assert st.get("responding"), "turn never reached responding"
            with store.DOC_LOCK:
                o = store.load_org(slug)
                r = o.switch_model(USER, nid, "luna")
                store.save_org(o)
            assert r.get("queued") is True, r
            return slug, nid, th

        # path A: the bare ⏸ (what the standalone orgtree_interrupt tool calls)
        slug_a, nid_a, th_a = start_and_queue("d234-a")
        r = supervisor.interrupt_turn(slug_a, nid_a)
        eq(r.get("interrupted"), True, "path A (bare interrupt) accepted")
        th_a.join(20)
        assert not th_a.is_alive(), "path A turn never ended"
        eq(store.load_org(slug_a).node(nid_a)["model"], "luna",
           "path A: the queued switch applied through the bare interrupt")

        # path B: interrupt_before_archive (what retire/dissolve call first)
        slug_b, nid_b, th_b = start_and_queue("d234-b")
        org_b = store.load_org(slug_b)
        warnings = supervisor.interrupt_before_archive(slug_b, org_b, nid_b)
        eq(warnings, [], "path B settles well inside the timeout")
        th_b.join(5)
        assert not th_b.is_alive(), "path B turn never ended"
        eq(store.load_org(slug_b).node(nid_b)["model"], "luna",
           "path B: the queued switch applied through interrupt_before_archive")
    check("D-234: a queued switch applies through BOTH interrupt paths", t6c)

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

    print("§6 native Codex compaction preserves both resumable generations")

    def t7b():
        os.environ["FAKECODEX_SCENARIO"] = "tool"
        os.environ["FAKECODEX_THREAD_ID"] = "compact-source-thread"
        s6, n6 = mkorg("compact")
        try:
            run_turn(s6, n6, "history that must survive the split")
        finally:
            os.environ.pop("FAKECODEX_THREAD_ID", None)
        before = node_doc(s6, n6)
        eq(before.get("session_id"), "compact-source-thread", "source id")
        os.environ["FAKECODEX_SCENARIO"] = "compact"
        os.environ["FAKECODEX_FORK_ID"] = "compact-successor-thread"
        try:
            supervisor._compact_split_body(s6, n6)
        finally:
            os.environ.pop("FAKECODEX_FORK_ID", None)
            os.environ["FAKECODEX_SCENARIO"] = "tool"
        o = store.load_org(s6)
        live = o.node(n6)
        pred_id = live.get("predecessor")
        assert pred_id and pred_id in o.nodes, "knowledge bearer was not made"
        pred = o.node(pred_id)
        eq(live.get("session_id"), "compact-successor-thread",
           "live generic session id")
        eq(live.get("codex_thread"), "compact-successor-thread",
           "live Codex resume marker")
        eq(pred.get("session_id"), "compact-source-thread",
           "bearer source session id")
        eq(pred.get("codex_thread"), "compact-source-thread",
           "bearer remains resumable through Codex")
        eq(live.get("occupancy"), 44, "post-compact occupancy")
        eq(live.get("compacted_unrun"), True,
           "successor protected until its first turn")
        eq(live.get("cost_usd"), 0.000548,
           "turn plus compact usage both accounted")
        path = supervisor.transcript_path("compact-successor-thread")
        assert path and os.path.exists(path), "successor journal missing"
        chat = supervisor.read_chat(o, n6)
        assert any(m.get("role") == "system"
                   and "context compacted" in (m.get("text") or "")
                   for m in chat["messages"]), chat
        assert any("history that must survive" in (m.get("text") or "")
                   for m in chat["messages"]), \
            "copied visible history vanished from successor"
        eq(chat.get("occupancy"), 44,
           "journal and node agree immediately after compact")
        eq(supervisor.state(s6, n6).get("last_error"), None,
           "no Claude fork error on a Codex thread")
    check("app-server fork+compact replaces the Claude-only failure path", t7b)

    print("§7 the connected-provider hire gate (M4)")
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

    warmpool.kill_org(slug, "suite-teardown")
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
