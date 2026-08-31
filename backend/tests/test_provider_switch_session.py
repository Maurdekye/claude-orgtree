"""D-196: switching an agent ACROSS providers must not orphan its session.

    python backend/tests/test_provider_switch_session.py   (no pytest; plain asserts)

The live incident: a `sol` agent was switched to `opus`; its next turn died with
"No conversation found with session ID: 01a04af0-…" and the node went
UNRECOVERABLE, transcript gone.

The mechanism, which is subtler than "switch_model forgets session_id" (true, but
not sufficient on its own). Each lane decides "may I resume?" differently:

  · codex  — `session_id == codex_thread`   (an explicit marker)
  · gemini — `session_id == gemini_session` (an explicit marker)
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
        assert str(n.get("session_id") or "") != str(n.get("gemini_session") or "") \
            or n.get("session_unrun"), "gemini would resume a codex thread"
        assert not n.get("codex_thread"), "the codex marker outlived its lane"
    check("codex→gemini leaves nothing the gemini leg would resume", t3b)

    def t3c():
        slug, nid = mkagent("gm2cl", "pro")
        ran_a_turn(slug, nid, "acp-session-2222", "gemini_session")
        with store.DOC_LOCK:
            o = store.load_org(slug)
            o.switch_model(USER, nid, "opus")
            store.save_org(o)
        assert resume_arg(slug, nid) is None, \
            "the claude lane would resume an ACP session id"
        assert not store.load_org(slug).node(nid).get("gemini_session"), \
            "the gemini marker outlived its lane"
    check("gemini→claude does not resume an ACP session id", t3c)

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

    print(f"\n{PASS} checks passed, {len(FAIL)} failed")
    for label, tb in FAIL:
        print(f"\n--- {label} ---\n{tb}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
