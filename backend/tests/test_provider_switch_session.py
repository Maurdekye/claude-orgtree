"""D-196: switching an agent ACROSS providers must not orphan its session.

    python backend/tests/test_provider_switch_session.py   (no pytest; plain asserts)

The live incident: a `sol` agent was switched to `opus`; its next turn died with
"No conversation found with session ID: 01a04af0-…" and the node went
UNRECOVERABLE, transcript gone.

The mechanism, which is subtler than "switch_model forgets session_id" (true, but
not sufficient on its own). Each lane decides "may I resume?" differently:

  · codex  — `session_id == codex_thread`   (an explicit marker)
  · antigravity — `session_id == antigravity_conversation` (an explicit marker)
  · claude — `transcript_path(sid) is not None`, i.e. DOES A FILE EXIST

and `transcript_path` deliberately falls back to the supervisor's own journal
store, because a codex thread's record is meant to be as real a transcript as
the CLI's file (supervisor.py:815-818). So a codex node that has run leaves a
journal at exactly the path the CLAUDE lane's resume test looks at. The claude
lane then believes the session is resumable, emits `--resume <codex threadId>`,
and the Claude CLI has never heard of it.

That is a BLINDED DETECTOR (D-181's hazard, one field over): the check does not
error, it silently answers "yes, resumable" about a session from another
provider. §1 pins the blinding itself, so a future reader can see WHY the guard
is needed and not delete it as redundant.

§6 (2026-09-03) pins the SECOND shape of this fix. The first shape minted
the fresh id IN PLACE, and that is how the desk went blank ("no conversation
yet") on two live agents whose sessions were real — hours of transcript on
disk, a turn still running on it — while the old session dropped out of the
ledger for good: no node held its id, so no desk, no orgtree_read_transcript
and no rehire could reach it. A crossing is now a LINEAGE SPLIT, the same
in-place archive cheap_compact performs: the pre-switch self becomes
`<node>@<gen>` on its OLD tier, and the successor starts fresh.

Anti-vacuity: §5 is the load-bearing counterweight. A fix that simply reset the
session on EVERY switch would pass §1-§4 and quietly destroy the feature the
ledger exists to provide — switching a model mid-life while KEEPING the
conversation. §5 requires a same-lane switch to preserve the session, so
"reset everything" cannot pass this suite.
"""

import os
import sys
import tempfile
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-d196-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_PORT"] = "9"          # never the live 7360
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

from orgtree import store, supervisor                              # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []

ALL_TOOLS = {"bash": True, "web": True, "edit": True, "subagents": True,
             "mcp": []}


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


def mkagent(label, tier):
    org = store.create_org(f"zz d196 {label}")
    r = org.hire(USER, None, tier, 4, "a1", add_dirs=[], tools=dict(ALL_TOOLS),
                 org_visibility="team", charter="a d196 switch test agent")
    store.save_org(org)
    return org.d["slug"], r["node"]


def ran_a_turn(slug, nid, sid, marker):
    """Put the node in the state a COMPLETED turn on that lane leaves behind:
    the harvested provider session id, its lane marker, no never-run pardon,
    and a journal the readers can find."""
    with store.DOC_LOCK:
        o = store.load_org(slug)
        n = o.node(nid)
        n["session_id"] = sid
        n.pop("session_unrun", None)
        if marker:
            n[marker] = sid
        store.save_org(o)
    supervisor._codex_journal(slug, sid, [
        {"type": "user", "timestamp": "2026-08-29T00:00:00Z",
         "message": {"role": "user", "content": "hello"}}])


def resume_arg(slug, nid):
    """What the claude lane would resume, or None if it starts fresh."""
    cmd = supervisor._build_cmd(store.load_org(slug), nid)
    return cmd[cmd.index("--resume") + 1] if "--resume" in cmd else None


def session_of(slug, nid):
    return store.load_org(slug).node(nid).get("session_id")


CODEX_TID = "01a04af0-54e5-7733-84aa-87978ac2d230"    # the live incident's id


def main() -> int:
    print("§1 the blinded detector (why the guard is needed at all)")

    def t1():
        slug, nid = mkagent("blind", "sol")
        ran_a_turn(slug, nid, CODEX_TID, "codex_thread")
        # ☠ the claude lane's "is this resumable" test answers YES about a
        # codex thread, because the journal sits where it looks
        assert supervisor.transcript_path(CODEX_TID) is not None, \
            "fixture is not reproducing the blinding at all"
    check("☠ a codex journal satisfies the claude lane's resume test", t1)

    print("\n§2 the incident: codex → claude must not resume a codex thread")

    def t2():
        slug, nid = mkagent("cx2cl", "sol")
        ran_a_turn(slug, nid, CODEX_TID, "codex_thread")
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "opus")
            store.save_org(o)
        got = resume_arg(slug, nid)
        assert got != CODEX_TID, (
            f"☠ REPRODUCED: the claude lane would run --resume {got}, a codex "
            f"threadId the Claude CLI has never heard of")
        assert got is None, f"expected a fresh session, got --resume {got}"
    check("☠ codex→claude does not resume the codex threadId", t2)

    def t2b():
        # the session must be REPLACED, not merely un-resumed: a stale foreign
        # id left in the field is a trap for every later reader
        slug, nid = mkagent("cx2cl2", "sol")
        ran_a_turn(slug, nid, CODEX_TID, "codex_thread")
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "opus")
            store.save_org(o)
        n = store.load_org(slug).node(nid)
        assert n.get("session_id") != CODEX_TID, "the codex id was left behind"
        assert not n.get("codex_thread"), "the codex marker outlived its lane"
    check("the foreign session id and its lane marker are cleared", t2b)

    print("\n§3 the reverse direction and the third provider")

    def t3():
        slug, nid = mkagent("cl2cx", "opus")
        ran_a_turn(slug, nid, "claude-uuid-1111", None)
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "sol")
            store.save_org(o)
        n = store.load_org(slug).node(nid)
        # the codex leg resumes only when session_id == codex_thread
        assert str(n.get("session_id") or "") != str(n.get("codex_thread") or "") \
            or n.get("session_unrun"), "codex would resume a claude session id"
    check("claude→codex leaves nothing the codex leg would resume", t3)

    def t3b():
        slug, nid = mkagent("cx2gm", "sol")
        ran_a_turn(slug, nid, CODEX_TID, "codex_thread")
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "pro")
            store.save_org(o)
        n = store.load_org(slug).node(nid)
        assert str(n.get("session_id") or "") != str(n.get("antigravity_conversation") or "") \
            or n.get("session_unrun"), "antigravity would resume a codex thread"
        assert not n.get("codex_thread"), "the codex marker outlived its lane"
    check("codex→antigravity leaves nothing the antigravity leg would resume", t3b)

    def t3c():
        slug, nid = mkagent("gm2cl", "pro")
        ran_a_turn(slug, nid, "agy-conv-2222", "antigravity_conversation")
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "opus")
            store.save_org(o)
        assert resume_arg(slug, nid) is None, \
            "the claude lane would resume a conversation id"
        assert not store.load_org(slug).node(nid).get("antigravity_conversation"), \
            "the antigravity marker outlived its lane"
    check("antigravity→claude does not resume a conversation id", t3c)

    print("\n§4 the agent is TOLD, rather than failing two minutes later")

    def t4():
        slug, nid = mkagent("told", "sol")
        ran_a_turn(slug, nid, CODEX_TID, "codex_thread")
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "opus")
            store.save_org(o)
        notices = store.load_org(slug).d.get("notices", {}).get(nid, [])
        body = " ".join(str(x.get("text") or "") for x in notices)
        # the box is READ CORRECTLY — proven by a value that must be in it, so
        # an empty read can never masquerade as "nothing was promised"
        assert "sol" in body and "opus" in body, \
            f"instrument is not reading the switch notice: {body!r}"
        assert "intact" not in body.lower(), (
            "☠ the switch still promises 'Your context is intact' across a "
            "provider change, which is the D-180 failure in another field")
    check("☠ a cross-provider switch never claims the context is intact", t4)

    def t4b():
        # The agent is told the COST, not only the context loss. Told just
        # "your conversation does not carry over" it can read the switch as
        # cheap; the warm process and the whole prompt cache go too, so the
        # next turn is a full cold open.
        slug, nid = mkagent("cost", "sol")
        ran_a_turn(slug, nid, CODEX_TID, "codex_thread")
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "opus")
            store.save_org(o)
        notices = store.load_org(slug).d.get("notices", {}).get(nid, [])
        body = " ".join(str(x.get("text") or "") for x in notices).lower()
        assert "sol" in body and "opus" in body,             f"instrument is not reading the switch notice: {body!r}"
        assert "cache" in body,             "the switched agent is not told its prompt cache is gone"
        assert "cold" in body,             "the switched agent is not told its next turn is a cold open"
    check("☠ a cross-provider switch names the cache/cold-open COST", t4b)

    def t4c():
        # ACTIONABLE, and reusing the re-seed wording rather than inventing a
        # second phrasing for the same situation: the agent has to re-orient
        # itself, and its scratch CLAUDE.md is how.
        slug, nid = mkagent("reorient", "sol")
        ran_a_turn(slug, nid, CODEX_TID, "codex_thread")
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "opus")
            store.save_org(o)
        notices = store.load_org(slug).d.get("notices", {}).get(nid, [])
        body = " ".join(str(x.get("text") or "") for x in notices).lower()
        assert "claude.md" in body,             "the switched agent is not pointed at its scratch CLAUDE.md"
        assert "breadcrumb" in body, "…nor at its breadcrumbs"
    check("☠ a cross-provider switch tells the agent HOW to re-orient", t4c)

    def t4d():
        # the ACTOR is warned about the cost too, not just the switched agent
        slug, nid = mkagent("actorcost", "sol")
        ran_a_turn(slug, nid, CODEX_TID, "codex_thread")
        with store.DOC_LOCK:
            o = store.load_org(slug)
            r = o.switch_model(USER, nid, "opus")
            store.save_org(o)
        warned = " ".join(r.get("warnings") or []).lower()
        # provider_of("sol") is "openai", not "codex" — the instrument reads
        # the provider NAMES, so anchor on a value that must really be there.
        assert "openai" in warned and "claude" in warned,             f"instrument is not reading the actor warning: {warned!r}"
        assert "cache" in warned and "cold" in warned,             "the ACTOR is told the context is lost but not that it costs"
    check("☠ the actor is warned about the cache cost, not just the context",
          t4d)

    print("\n§5 ☠ ANTI-VACUITY: a same-lane switch still keeps its session")

    def t5():
        slug, nid = mkagent("same", "opus")
        ran_a_turn(slug, nid, "claude-uuid-3333", None)
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "sonnet")
            store.save_org(o)
        assert session_of(slug, nid) == "claude-uuid-3333", \
            "a claude→claude switch must PRESERVE the session (№16)"
        assert resume_arg(slug, nid) == "claude-uuid-3333", \
            "…and must still resume it"
    check("☠ claude→claude preserves and resumes the session", t5)

    def t5b():
        slug, nid = mkagent("samecx", "sol")
        ran_a_turn(slug, nid, CODEX_TID, "codex_thread")
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "terra")
            store.save_org(o)
        n = store.load_org(slug).node(nid)
        assert n.get("session_id") == CODEX_TID, \
            "a codex→codex switch must PRESERVE the thread"
        assert n.get("codex_thread") == CODEX_TID, "…and its marker"
    check("☠ codex→codex preserves the thread and its marker", t5b)

    def t5c():
        # ANTI-VACUITY for the cost wording: a SAME-LANE switch keeps its
        # session, so it must NOT be told its cache is gone. Without this a
        # blanket "you lost your cache" on every switch would pass t4b while
        # being wrong half the time.
        slug, nid = mkagent("samecost", "opus")
        ran_a_turn(slug, nid, "claude-uuid-4444", None)
        with store.DOC_LOCK:
            o = store.load_org(slug)
            r = o.switch_model(USER, nid, "sonnet")
            store.save_org(o)
        notices = store.load_org(slug).d.get("notices", {}).get(nid, [])
        body = " ".join(str(x.get("text") or "") for x in notices).lower()
        assert "intact" in body,             "a same-lane switch must still say the context is intact"
        assert "cold open" not in body,             "a same-lane switch must NOT claim a cold open — it keeps its session"
        assert not any("cache" in w.lower() for w in (r.get("warnings") or [])),             "a same-lane switch must not warn the actor about a lost cache"
    check("☠ a SAME-LANE switch claims no cost and keeps saying 'intact'", t5c)

    print("\n§6 ☠ THE SESSION IS KEPT, NOT DROPPED: a crossing is a lineage split")

    def t6():
        # the 2026-09-03 specimens: a REAL session (a journal on disk, a turn
        # possibly still running on it) crossed providers and its id was
        # overwritten in place — every reader keyed on session_id answered
        # "no conversation yet", and the session left the ledger for good
        slug, nid = mkagent("keep", "opus")
        ran_a_turn(slug, nid, "claude-uuid-6666", None)
        with store.DOC_LOCK:
            o = store.load_org(slug)
            r = o.switch_model(USER, nid, "sol")
            store.save_org(o)
        o = store.load_org(slug)
        holders = [k for k, x in o.nodes.items()
                   if x.get("session_id") == "claude-uuid-6666"]
        assert holders == [f"{nid}@0"], (
            f"☠ REPRODUCED: the pre-switch session is held by "
            f"{holders or 'NO node'} — it dropped out of the ledger, so no "
            f"desk and no orgtree_read_transcript can reach it")
        b = o.node(f"{nid}@0")
        assert b["state"] == "archived", b["state"]
        assert b.get("bearer_state") == "knowledge", b.get("bearer_state")
        assert b.get("successor") == nid and b["grant"] == 0
        n = o.node(nid)
        assert n.get("predecessor") == f"{nid}@0", n.get("predecessor")
        assert n.get("generation") == 1, n.get("generation")
        assert n.get("session_unrun") and n["session_id"] != "claude-uuid-6666"
        assert r.get("bearer") == f"{nid}@0", r
        assert r.get("old_session") == "claude-uuid-6666", r
    check("☠ the pre-switch session is archived in place as <node>@<gen>", t6)

    def t6b():
        # THE DESK. The bearer's desk still renders the conversation and the
        # successor's is honestly empty — the two readers the specimens broke
        slug, nid = mkagent("desk", "opus")
        ran_a_turn(slug, nid, "claude-uuid-7777", None)
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "sol")
            store.save_org(o)
        o = store.load_org(slug)
        old = supervisor.read_chat(o, f"{nid}@0")["messages"]
        assert any("hello" in (m.get("text") or "") for m in old), \
            f"the bearer's desk does not show the pre-switch conversation: {old}"
        assert supervisor.read_chat(o, nid)["messages"] == [], \
            "the successor's desk must be empty — its session has not run"
    check("☠ the bearer's desk renders the conversation; the successor's is empty", t6b)

    def t6c():
        # the bearer is recorded on the OLD provider's tier, so D-197 offers
        # it the right family: consult on claude, refuse codex
        slug, nid = mkagent("family", "opus")
        ran_a_turn(slug, nid, "claude-uuid-8888", None)
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "sol")
            store.save_org(o)
        o = store.load_org(slug)
        assert o.node(f"{nid}@0")["model"] == "opus", \
            f"the bearer wears the successor's tier: {o.node(f'{nid}@0')['model']}"
        assert o.node(nid)["model"] == "sol"
        try:
            o.rehire(USER, f"{nid}@0", tier="terra")
        except Exception as e:                                   # noqa: BLE001
            assert "provider" in str(e).lower(), e
        else:
            raise AssertionError("a claude bearer was rehired onto codex")
        o.rehire(USER, f"{nid}@0", tier="haiku")     # its own provider: fine
        assert o.node(f"{nid}@0")["state"] == "live"
    check("the bearer keeps the OLD tier — rehireable on its own provider only", t6c)

    def t6d():
        # the lane marker travels with the bearer, not the successor, and
        # the lineage lists the bearer where the desk's panel reads it
        slug, nid = mkagent("marker", "sol")
        ran_a_turn(slug, nid, CODEX_TID, "codex_thread")
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "opus")
            store.save_org(o)
        o = store.load_org(slug)
        assert o.node(f"{nid}@0").get("codex_thread") == CODEX_TID
        assert o.node(f"{nid}@0")["session_id"] == CODEX_TID
        assert not o.node(nid).get("codex_thread")
        assert o.lineage_stack(nid) == [f"{nid}@0"], o.lineage_stack(nid)
    check("the codex thread stays with its bearer; the lineage lists it", t6d)

    def t6e():
        # the agent, the actor and the event log all say WHERE it went
        slug, nid = mkagent("named", "sol")
        ran_a_turn(slug, nid, CODEX_TID, "codex_thread")
        with store.DOC_LOCK:
            o = store.load_org(slug)
            r = o.switch_model(USER, nid, "opus")
            store.save_org(o)
        notices = store.load_org(slug).d.get("notices", {}).get(nid, [])
        body = " ".join(str(x.get("text") or "") for x in notices)
        assert f'"{nid}@0"' in body, "the agent is not told its predecessor's id"
        assert "transcript.jsonl" in body, "…nor where the transcript copy is"
        warn = " ".join(r.get("warnings") or [])
        assert f'"{nid}@0"' in warn and "NOT lost" in warn, warn
        ev = [e for e in store.load_org(slug).d["events"]
              if e["op"] == "switch_model"][-1]["detail"]
        assert ev.get("bearer") == f"{nid}@0", ev
        assert ev.get("old_session") == CODEX_TID, ev
    check("the agent, the actor and the event log all name the bearer", t6e)

    def t6f():
        # the switch's transcript copy lands where cheap_compact's does
        slug, nid = mkagent("export", "sol")
        ran_a_turn(slug, nid, CODEX_TID, "codex_thread")
        with store.DOC_LOCK:
            o = store.load_org(slug)
            r = o.switch_model(USER, nid, "opus")
            store.save_org(o)
        dst = supervisor.export_predecessor_transcript(
            store.load_org(slug), nid, old_sid=r["old_session"])
        assert dst and os.path.isfile(dst), "no transcript.jsonl was exported"
        assert "hello" in open(dst, encoding="utf-8").read()
    check("the pre-switch transcript is exported beside the breadcrumbs", t6f)

    def t6g():
        # ANTI-VACUITY: a same-lane switch splits NOTHING — no bearer, no
        # generation bump, the session untouched (§5's counterpart)
        slug, nid = mkagent("nosplit", "opus")
        ran_a_turn(slug, nid, "claude-uuid-9999", None)
        with store.DOC_LOCK:
            o = store.load_org(slug)
            r = o.switch_model(USER, nid, "sonnet")
            store.save_org(o)
        o = store.load_org(slug)
        assert f"{nid}@0" not in o.nodes, "a same-lane switch minted a bearer"
        assert o.node(nid).get("generation", 0) == 0
        assert "bearer" not in r and o.node(nid)["session_id"] == "claude-uuid-9999"
    check("☠ a same-lane switch archives nothing", t6g)

    def t6h():
        # a second crossing archives the second session too: @0 claude, @1 codex
        slug, nid = mkagent("twice", "opus")
        ran_a_turn(slug, nid, "claude-uuid-aaaa", None)
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "sol")
            store.save_org(o)
        ran_a_turn(slug, nid, CODEX_TID, "codex_thread")
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "opus")
            store.save_org(o)
        o = store.load_org(slug)
        assert o.lineage_stack(nid) == [f"{nid}@1", f"{nid}@0"], o.lineage_stack(nid)
        assert o.node(f"{nid}@0")["model"] == "opus"
        assert o.node(f"{nid}@1")["model"] == "sol"
        assert o.node(f"{nid}@1")["session_id"] == CODEX_TID
        assert o.node(nid).get("generation") == 2
    check("every crossing leaves its own generation", t6h)

    print("\n§7 D-234: a switch asked for MID-TURN is queued behind the turn")

    def mid_turn(slug, nid, cmd=False):
        """What `_run_one_turn` persists before any lane speaks."""
        with store.DOC_LOCK:
            o = store.load_org(slug)
            inf = {"at": "2026-09-03T10:00:00Z", "text": "working",
                   "view": "working"}
            if cmd:
                inf["cmd"] = True
            o.node(nid)["inflight"] = inf
            store.save_org(o)

    def boundary(slug, nid):
        """The finally block's doc half: inflight popped, the queue applied."""
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.node(nid).pop("inflight", None)
            changed = supervisor._apply_pending_switch_locked(o, slug, nid)
            store.save_org(o)
        return changed

    def tree_node(o, nid):
        stack = list(o.tree()["roots"])
        while stack:
            x = stack.pop()
            if x["id"] == nid:
                return x
            stack.extend(x.get("children") or [])
        raise AssertionError(f"{nid} not in tree()")

    def queue(slug, nid, tier, actor=USER):
        with store.DOC_LOCK:
            o = store.load_org(slug)
            r = o.switch_model(actor, nid, tier)
            store.save_org(o)
        return r

    def t7():
        slug, nid = mkagent("queue", "opus")
        ran_a_turn(slug, nid, "claude-uuid-q001", None)
        mid_turn(slug, nid)
        r = queue(slug, nid, "sol")
        o = store.load_org(slug)
        n = o.node(nid)
        assert r.get("queued") is True and r.get("tier") == "sol", r
        assert n["model"] == "opus", "☠ the model changed under a running turn"
        assert n["session_id"] == "claude-uuid-q001" and f"{nid}@0" not in o.nodes, \
            "☠ the session was split under a running turn"
        assert (n.get("pending_switch") or {}).get("tier") == "sol", n.get("pending_switch")
        assert n["pending_switch"]["crossing"] is True
        w = " ".join(r["warnings"])
        assert "QUEUED, not switched" in w and "interrupt" in w.lower(), w
        assert [e for e in o.d["events"] if e["op"] == "switch_queued"], \
            "no switch_queued event"
        assert tree_node(o, nid)["pending_switch"]["tier"] == "sol", \
            "the tree payload does not carry the queue"
    check("☠ mid-turn: nothing applies, the queue is recorded, the actor is told QUEUED", t7)

    def t7b():
        slug, nid = mkagent("boundary", "opus")
        ran_a_turn(slug, nid, "claude-uuid-q002", None)
        mid_turn(slug, nid)
        queue(slug, nid, "sol")
        assert boundary(slug, nid), "the boundary reported no change"
        o = store.load_org(slug)
        n = o.node(nid)
        assert n["model"] == "sol" and "pending_switch" not in n, n.get("pending_switch")
        assert o.node(f"{nid}@0")["session_id"] == "claude-uuid-q002", \
            "the pre-switch session is not the bearer's"
        assert o.node(f"{nid}@0")["model"] == "opus"
        assert n.get("session_unrun") and n["session_id"] != "claude-uuid-q002"
        ev = [e for e in o.d["events"] if e["op"] == "switch_model"][-1]["detail"]
        assert ev.get("queued_at") and ev.get("bearer") == f"{nid}@0", ev
        assert os.path.exists(os.path.join(supervisor.scratch_dir(slug, nid),
                                           "transcript.jsonl")), \
            "the crossing's transcript copy did not land in the successor's scratch"
        assert tree_node(o, nid)["pending_switch"] is None
        told = " ".join(str(x.get("text") or "")
                        for x in o.d.get("notices", {}).get(nid, []))
        assert "queued while you were mid-turn" in told, told
    check("☠ the boundary applies it: bearer, fresh session, transcript copy, flag cleared", t7b)

    def t7c():
        # a SAME-provider switch queues too, and at the boundary keeps its session
        slug, nid = mkagent("samelane", "opus")
        ran_a_turn(slug, nid, "claude-uuid-q003", None)
        mid_turn(slug, nid)
        r = queue(slug, nid, "sonnet")
        assert r.get("queued") is True and r.get("crossing") is False, r
        assert store.load_org(slug).node(nid)["model"] == "opus", \
            "☠ a same-lane mid-turn switch changed the chart under the turn"
        boundary(slug, nid)
        o = store.load_org(slug)
        assert o.node(nid)["model"] == "sonnet"
        assert o.node(nid)["session_id"] == "claude-uuid-q003", \
            "a same-lane switch must keep its session"
        assert f"{nid}@0" not in o.nodes, "a same-lane switch minted a bearer"
    check("☠ a same-provider mid-turn switch queues too, and keeps its session when applied", t7c)

    def t7d():
        slug, nid = mkagent("replace", "opus")
        ran_a_turn(slug, nid, "claude-uuid-q004", None)
        mid_turn(slug, nid)
        queue(slug, nid, "sol")
        r2 = queue(slug, nid, "flash")
        assert r2.get("queued") and r2.get("replaced") == "sol", r2
        o = store.load_org(slug)
        assert o.node(nid)["pending_switch"]["tier"] == "flash", \
            "the second request did not replace the target"
        assert "Replaces the queued switch to sol" in " ".join(r2["warnings"])
        r3 = queue(slug, nid, "opus")        # the CURRENT tier: cancel
        assert r3.get("cancelled") == "flash" and r3.get("queued") is False, r3
        o = store.load_org(slug)
        assert "pending_switch" not in o.node(nid)
        assert [e for e in o.d["events"] if e["op"] == "switch_queue_cancelled"]
        assert boundary(slug, nid) is False, \
            "a cancelled queue must be nothing at the boundary"
        assert store.load_org(slug).node(nid)["model"] == "opus"
    check("a second request REPLACES the target; asking for the current tier CANCELS", t7d)

    def t7e():
        # a refusal the immediate path would raise fires at the REQUEST
        slug, nid = mkagent("cap", "fable")
        ran_a_turn(slug, nid, "claude-uuid-q005", None)
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.node(nid)["grant"] = 45          # fable seat 10: the melt to
            o.d["max_top_grant"] = 50          # haiku would push it to 54
            store.save_org(o)
        mid_turn(slug, nid)
        try:
            queue(slug, nid, "haiku")
        except Exception:                                            # noqa: BLE001
            pass
        else:
            raise AssertionError("an unaffordable switch was queued instead of refused")
        n = store.load_org(slug).node(nid)
        assert "pending_switch" not in n and n["model"] == "fable"
    check("☠ a refusal fires at the request — nothing is queued that cannot apply", t7e)

    def t7f():
        # the node leaves `live` before its turn ends: dropped WITH a record
        slug, boss = mkagent("boss", "opus")
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.node(boss)["grant"] = 20         # room to fund the kid's crossing
            r = o.hire(USER, boss, "haiku", 0, "kid", add_dirs=[],
                       tools=dict(ALL_TOOLS), org_visibility="team",
                       charter="a report")
            kid = r["node"]
            store.save_org(o)
        ran_a_turn(slug, kid, "claude-uuid-q006", None)
        mid_turn(slug, kid)
        queue(slug, kid, "sol", actor=boss)
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.retire(USER, kid)
            store.save_org(o)
        assert boundary(slug, kid), "the drop must still count as a change (the flag clears)"
        o = store.load_org(slug)
        n = o.node(kid)
        assert n["model"] == "haiku" and "pending_switch" not in n
        assert f"{kid}@0" not in o.nodes, "a dropped queue minted a bearer"
        ev = [e for e in o.d["events"] if e["op"] == "switch_queue_dropped"]
        assert ev and "not live" in ev[-1]["detail"]["reason"], ev
        told = " ".join(str(x.get("text") or "")
                        for x in o.d.get("notices", {}).get(boss, []))
        assert "DROPPED" in told, "the requester was not told"
    check("☠ a node that leaves live mid-turn: the queue is dropped WITH an event and a notice", t7f)

    def t7g():
        # the backend died mid-turn: reconcile applies the queue at startup
        slug, nid = mkagent("restart", "opus")
        ran_a_turn(slug, nid, "claude-uuid-q007", None)
        mid_turn(slug, nid, cmd=True)          # a command turn is dropped, not replayed
        queue(slug, nid, "sol")
        supervisor.reconcile(slug)
        o = store.load_org(slug)
        n = o.node(nid)
        assert n["model"] == "sol" and "pending_switch" not in n and "inflight" not in n
        assert o.node(f"{nid}@0")["session_id"] == "claude-uuid-q007"
    check("☠ a queue that outlived the backend applies at startup, not never", t7g)

    def t7h():
        # ANTI-VACUITY: an IDLE node still switches at once
        slug, nid = mkagent("idle", "opus")
        ran_a_turn(slug, nid, "claude-uuid-q008", None)
        r = queue(slug, nid, "sol")
        assert r.get("queued") is False and r.get("bearer") == f"{nid}@0", r
        assert store.load_org(slug).node(nid)["model"] == "sol"
    check("☠ an idle node switches immediately — the queue is for busy nodes only", t7h)

    def t7i():
        # the supervisor's live `busy` answer queues even before the marker lands
        slug, nid = mkagent("busyflag", "opus")
        ran_a_turn(slug, nid, "claude-uuid-q009", None)
        with store.DOC_LOCK:
            o = store.load_org(slug)
            r = o.switch_model(USER, nid, "sonnet", busy=True)
            store.save_org(o)
        assert r.get("queued") is True
        assert store.load_org(slug).node(nid)["model"] == "opus"
    check("the API doors' live busy answer queues too", t7i)

    print(f"\n{PASS} checks passed, {len(FAIL)} failed")
    for label, tb in FAIL:
        print(f"\n--- {label} ---\n{tb}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
