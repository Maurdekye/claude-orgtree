"""D-186 supervisor dispatch: an antigravity-tier node's turn runs the
antigravity leg.

    python backend/tests/test_antigravity_dispatch.py  (no pytest; asserts)

Hermetic: drives `supervisor._run_one_turn` IN PROCESS against real org docs
on disk, with the Antigravity CLI resolved (via ORGTREE_ANTIGRAVITY) to
fakeantigravity.py — the scripted print-mode double test_antigravityrun.py
already proves speaks the measured wire. What THIS suite proves is the seam
on top: dispatch on tier membership, bookkeeping through `_after_turn`,
conversation harvest + resume marker, identity via AGENTS.md, org powers
riding the workspace plugin, the ⚙-rights hook, the queue handoff through
the shared finally, interrupt through `interrupt_turn`, and the api hire
gate.

ORGTREE_PORT is a PORT NOBODY SERVES (test-rig hygiene, measured on the
codex rig): this rig runs no backend, and an unset port would send a test's
mcptool traffic to the operator's live deployment.

Anti-vacuity: the signed-out plant, the wrong-model plant and the
unknown-model plant must all be SEEN by their detectors, and the
queue-handoff proof is a follow carrier the shared finally actually popped.
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

DATA = tempfile.mkdtemp(prefix="orgtree-agydisp-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_PORT"] = "9"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "fakeantigravity.py")
os.environ["ORGTREE_ANTIGRAVITY"] = FAKE
# hermetic on the codex axis too (the mirror of test_providers' new pin)
os.environ["ORGTREE_CODEX"] = os.path.join(DATA, "nowhere", "codex.exe")
os.environ["CODEX_HOME"] = os.path.join(DATA, "chome")


def sign_in(yes: bool) -> None:
    from orgtree import providers
    if yes:
        os.environ.pop("FAKEANTIGRAVITY_SIGNED_OUT", None)
    else:
        os.environ["FAKEANTIGRAVITY_SIGNED_OUT"] = "1"
    providers._antigravity_status_cache = None   # the 60s panel cache must not lie


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


def mkorg(label: str, tier: str = "pro", **tools_over) -> tuple[str, str]:
    org = store.create_org(f"zz agydisp {label}")
    # FULL RIGHTS by default, so "a full-rights node carries no rights hook"
    # (§4) stays a real check. `web`/`subagents` used to be off here and cost
    # nothing, because this lane ignored them; since 2026-09-05 they narrow a
    # node exactly like bash and edit, and a fixture that is quietly narrowed
    # would make §4's control vacuous.
    tools = {"bash": True, "web": True, "edit": True,
             "subagents": True, "mcp": []}
    tools.update(tools_over)
    r = org.hire(USER, None, tier, 2, "ag", add_dirs=[], tools=tools,
                 org_visibility="team",
                 charter="an antigravity dispatch test agent")
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


def scratch_file(slug: str, nid: str, *rel: str) -> str:
    return os.path.join(supervisor.scratch_dir(slug, nid), *rel)


def main():
    print("§1 dispatch + bookkeeping (the tier takes the antigravity leg)")
    s1, n1 = mkorg("basic")
    os.environ["FAKEANTIGRAVITY_SCENARIO"] = "text"

    def t1():
        follow = run_turn(s1, n1, "hello antigravity")
        eq(follow, None, "no queued follow-on")
        n = node_doc(s1, n1)
        eq(n["session_id"], "fake-agy-conv-0001", "conversation harvested")
        eq(n.get("antigravity_conversation"), "fake-agy-conv-0001",
           "resume marker")
        eq("session_unrun" in n, False, "pardon spent by the harvest")
        # the fake's turn totals: 16690 in (uncached) / 1200 cached / 60 out
        # at the pro row: (16690·2 + 1200·.2 + 60·12) / 1e6 = 0.03434
        eq(round(float(n.get("cost_usd") or 0.0), 6), 0.03434, "cost booked")
        eq(n.get("occupancy"), 8400, "occupancy = the last request's prompt")
        eq(supervisor.state(s1, n1).get("last_error"), None, "no error")
        deltas = "".join(p.get("text", "") for p in STREAMED
                         if p.get("kind") == "delta")
        eq("working… " in deltas and "done." in deltas, True,
           f"live deltas streamed ({deltas!r})")
    check("a pro-tier turn runs the antigravity leg and books exactly", t1)

    def t1b():
        recs = journal_lines(s1, "fake-agy-conv-0001")
        kinds = [(r.get("type"),
                  (r.get("message") or {}).get("content")[0].get("type")
                  if isinstance((r.get("message") or {}).get("content"), list)
                  and (r.get("message") or {}).get("content") else None)
                 for r in recs]
        eq(kinds[0][0], "user", f"first record is the user row ({kinds[0]!r})")
        assert any(k == ("assistant", "text") for k in kinds), \
            f"agent text journaled: {kinds}"
        usage = [r for r in recs if (r.get("message") or {}).get("usage")]
        assert usage, "usage record present"
        u = usage[-1]["message"]["usage"]
        eq((u["input_tokens"], u["cache_read_input_tokens"],
            u["output_tokens"]), (16690, 1200, 60), "usage rec")
        eq(usage[-1]["message"].get("last_prompt_tokens"), 8400,
           "synthetic usage row carries final-request occupancy separately")
        eq(supervisor.read_chat(store.load_org(s1), n1)["occupancy"], 8400,
           "chat reads the final request, not the multi-request turn total")
    check("the journal holds user, text and usage records", t1b)

    def t1c():
        # THE DRAFT'S HANDOVER (user report 2026-09-04: "i see double messages
        # in antigravity agents"). The desk renders the streamed deltas as a
        # grey draft, and `{kind:"text"}` is the ONE signal that retires it
        # mid-turn: convo.ts marks the draft superseded and the fetch it nudges
        # clears it in the same patch that installs the durable row. The claude
        # and codex legs both emit it; this leg emitted only delta/tool/journal,
        # so its draft had no retirement but `turn_done` and the reply sat on
        # screen twice — once grey, once as its own transcript row — for the
        # rest of the turn.
        texts = [p for p in STREAMED if p.get("kind") == "text"]
        assert texts, (
            "the antigravity leg streamed no `text` frame: kinds seen = "
            + repr(sorted({str(p.get("kind")) for p in STREAMED}))
            + ". The desk's draft then has no mid-turn retirement and the "
              "reply renders twice beside its own transcript row.")
        # …and it must be a REPLACEMENT, not a gap: the frames TOGETHER carry
        # exactly what the deltas put on screen, so nothing is retired that
        # the transcript does not already hold (D-50). One frame per
        # completed text step since D4 (2026-09-05): the fixture's two
        # priced requests are two rows in chronological place, not one row
        # joined at the end of the turn — see test_antigravity_stream_order.
        deltas = "".join(p.get("text", "") for p in STREAMED
                         if p.get("kind") == "delta")
        eq("".join(str(p.get("text") or "") for p in texts), deltas,
           "the text frames carry exactly what the deltas streamed")
        eq(len(texts), 2, "one handover frame per completed text step")
        # and each frame's durable twin is already on disk when it goes out
        recs = journal_lines(s1, "fake-agy-conv-0001")
        durable = [b.get("text") for r in recs
                   if r.get("type") == "assistant"
                   and isinstance((r.get("message") or {}).get("content"), list)
                   for b in r["message"]["content"]
                   if b.get("type") == "text"]
        eq(durable, [str(p.get("text") or "") for p in texts],
           "each text frame's replacement row is in the journal, in order")
    check("the streamed draft is handed over to a durable row, not left up",
          t1c)

    print("§2 tool events fold into the transcript vocabulary")
    s2, n2 = mkorg("tools")
    os.environ["FAKEANTIGRAVITY_SCENARIO"] = "toolevents"

    def t2():
        run_turn(s2, n2, "use your tool")
        # the tool round means TWO priced requests: occupancy is the LAST
        # one's prompt (4563 + 12175 cached), never the wire's summed input
        eq(node_doc(s2, n2).get("occupancy"), 16738, "last-request occupancy")
        recs = journal_lines(s2, "fake-agy-conv-0001")
        uses = [c for r in recs
                for c in (r.get("message") or {}).get("content") or []
                if isinstance(c, dict) and c.get("type") == "tool_use"]
        results = [c for r in recs
                   for c in (r.get("message") or {}).get("content") or []
                   if isinstance(c, dict) and c.get("type") == "tool_result"]
        # an MCP call is journaled under the TOOL'S bare name (the form the
        # download-card / mail-link readers match), a built-in under its own
        eq([(u["name"], u["input"]) for u in uses],
           [("orgtree_ping", {"message": "hi"}),
            ("run_command", {"CommandLine": "echo HOOK-CMD"})], "tool_use fold")
        eq([(r["content"], r["is_error"]) for r in results],
           [("PONG:hi", False), ("HOOK-CMD\r\n", False)], "tool_result fold")
    check("call_mcp_tool / built-in steps become tool_use/tool_result", t2)

    print("§2b the live cumulative-session shape: billing, occupancy and legacy")
    s2b, n2b = mkorg("session-cumulative", tier="flash")
    os.environ["FAKEANTIGRAVITY_SCENARIO"] = "sessioncumulative"
    os.environ["FAKEANTIGRAVITY_CONVERSATION_ID"] = \
        "fake-agy-session-cumulative"

    def t2b():
        run_turn(s2b, n2b, "live cumulative result")
        n = node_doc(s2b, n2b)
        eq(round(float(n.get("cost_usd") or 0.0), 6), 0.036545,
           "booked cost is current-turn request sums")
        eq(n.get("occupancy"), 63829, "node occupancy is final request")
        recs = journal_lines(s2b, "fake-agy-session-cumulative")
        usage = [r for r in recs if (r.get("message") or {}).get("usage")][-1]
        m = usage["message"]
        eq((m["usage"]["input_tokens"],
            m["usage"]["cache_read_input_tokens"],
            m["usage"]["output_tokens"], m["last_prompt_tokens"]),
           (17709, 232176, 1560, 63829),
           "journal separates turn billing from final-request occupancy")
        eq(supervisor.read_chat(store.load_org(s2b), n2b)["occupancy"],
           63829, "chat never renders the exact live 1,314,610 session total")
    check("session-cumulative result books one turn and displays one request",
          t2b)

    def t2c():
        marked = supervisor._OccTracker(1_000_000)
        supervisor._occ_record(marked, {
            "type": "assistant", "message": {
                "id": "agy-live-current-conversation-usage",
                "role": "assistant", "model": "gemini-3.8-flash",
                "content": [], "last_prompt_tokens": 63829, "usage": {
                    "input_tokens": 17709,
                    "cache_read_input_tokens": 232176,
                    "output_tokens": 1560}}})
        eq((marked.value, marked.estimated), (63829, False),
           "marked aggregate uses its dedicated final-request measurement")
        fill = supervisor._OccTracker(1_000_000)
        supervisor._occ_record(fill, {
            "type": "assistant", "message": {
                "id": "agy-live-prior-conversation-usage",
                "role": "assistant", "model": "gemini-3.8-flash",
                "content": [], "usage": {
                    "input_tokens": 132605,
                    "cache_read_input_tokens": 1182005,
                    "output_tokens": 17559}}})
        eq((fill.value, fill.estimated), (None, False),
           "unmarked legacy aggregate is unknown, so the desk uses node occupancy")
    check("legacy Antigravity aggregate rows are ignored immediately", t2c)

    os.environ["FAKEANTIGRAVITY_SCENARIO"] = "text"
    os.environ.pop("FAKEANTIGRAVITY_CONVERSATION_ID", None)

    print("§3 resume rides --conversation with org powers; a re-mint starts "
          "fresh")
    probe = os.path.join(DATA, "wsprobe.json")
    os.environ["FAKEANTIGRAVITY_WSPROBE"] = probe
    os.environ["FAKEANTIGRAVITY_SCENARIO"] = "text"

    def t3():
        run_turn(s1, n1, "second turn")
        with open(probe, encoding="utf-8") as f:
            ws = json.load(f)
        argv = ws["argv"]
        eq(argv[argv.index("--conversation") + 1], "fake-agy-conv-0001",
           "resumed via --conversation")
        srv = ws["mcp_config"]["mcpServers"]
        assert "orgtree" in srv, f"orgtree server in the plugin: {srv.keys()}"
        env = srv["orgtree"]["env"]
        eq((env.get("ORGTREE_ORG"), env.get("ORGTREE_NODE"),
            env.get("ORGTREE_PORT"), bool(env.get("PYTHONPATH"))),
           (s1, n1, "9", True),
           "per-agent identity in the server env, full set")
        eq(argv[argv.index("--add-dir") + 1], supervisor.scratch_dir(s1, n1),
           "the scratch is the workspace")
        eq((argv[argv.index("--model") + 1], argv[argv.index("--effort") + 1]),
           ("gemini-3.1-pro", "high"), "base model + effort")
    check("a resumed turn hands the harvested conversation back and "
          "re-attaches the org powers with per-agent identity", t3)

    def t3b():
        os.remove(probe)
        with store.DOC_LOCK:
            org = store.load_org(s1)
            org.node(n1)["session_id"] = "minted-foreign-uuid"
            org.node(n1)["session_unrun"] = True
            store.save_org(org)
        run_turn(s1, n1, "after a re-mint")
        with open(probe, encoding="utf-8") as f:
            ws = json.load(f)
        eq("--conversation" in ws["argv"], False, "fresh conversation")
        eq(node_doc(s1, n1)["session_id"], "fake-agy-conv-0001",
           "harvest replaced the minted id")
        os.environ.pop("FAKEANTIGRAVITY_WSPROBE", None)
    check("a minted/re-minted id is never resumed — the conversation starts "
          "fresh and the harvest takes over", t3b)

    def t3c():
        # THE RESUME THAT IS NOT THERE ANY MORE. Measured shape: the CLI
        # warns on stderr and hands back a FRESH conversation, so the agent's
        # earlier context on this provider is gone. The leg is supposed to
        # say so in the conversation, at the moment it happened, and adopt
        # the new id — an agent that silently forgot everything looks to its
        # superior like an agent that stopped cooperating.
        s3c, n3c = mkorg("resumelost")
        run_turn(s3c, n3c, "first turn")            # harvest an id to resume
        first = node_doc(s3c, n3c)["session_id"]
        assert first, "the first turn harvested a conversation id"
        os.environ["FAKEANTIGRAVITY_SCENARIO"] = "resumelost"
        os.environ["FAKEANTIGRAVITY_CONVERSATION_ID"] = "fake-agy-conv-NEW"
        try:
            run_turn(s3c, n3c, "second turn")
        finally:
            os.environ["FAKEANTIGRAVITY_SCENARIO"] = "text"
            os.environ.pop("FAKEANTIGRAVITY_CONVERSATION_ID", None)
        n = node_doc(s3c, n3c)
        rows = (store.load_org(s3c).d.get("turn_error_log") or {}).get(n3c, [])
        said = [r for r in rows if "could not resume" in str(r.get("text"))]
        eq((n["session_id"], n.get("antigravity_conversation"),
            len(said) == 1, supervisor.state(s3c, n3c).get("last_error")),
           ("fake-agy-conv-NEW", "fake-agy-conv-NEW", True, None),
           f"node={n.get('session_id')} rows={rows}")
    check("a resume the CLI could not honour is SAID in the conversation, "
          "the fresh id is adopted, and the turn still completes", t3c)

    def t3d():
        # the control: the same two turns with the resume HONOURED say
        # nothing about a lost conversation. Without this, a leg that logged
        # the line on every turn would pass the check above.
        s3d, n3d = mkorg("resumekept")
        run_turn(s3d, n3d, "first turn")
        run_turn(s3d, n3d, "second turn")
        rows = (store.load_org(s3d).d.get("turn_error_log") or {}).get(n3d, [])
        said = [r for r in rows if "could not resume" in str(r.get("text"))]
        eq((node_doc(s3d, n3d)["session_id"], said),
           ("fake-agy-conv-0001", []), f"rows={rows}")
    check("…and an honoured resume says nothing about it (control)", t3d)

    # ── the image translation, which had NO check at all ──────────────────
    # The mail builder validates a user's image and hands the leg an inline
    # block. This lane's print-mode stdin takes TEXT content only, so those
    # blocks are not deliverable and the leg announces them instead (D-180).
    # Announcing and DROPPING look identical from the agent's side unless
    # something reads the prompt, so this reads the prompt.
    def _img_org(kind: str):
        from PIL import Image
        s, n = mkorg(f"img{kind}")
        name = "shot.png" if kind == "image" else "notes.txt"
        up = os.path.join(supervisor.scratch_dir(s, n), "uploads")
        os.makedirs(up, exist_ok=True)
        dst = os.path.join(up, name)
        if kind == "image":
            Image.new("RGB", (8, 8), (255, 0, 0)).save(dst)
        else:
            with open(dst, "wb") as f:
                f.write(b"plain text, not an image")
        with store.DOC_LOCK:
            o2 = store.load_org(s)
            o2.post_mail(USER, n, f"look at {name}",
                         attachments=[{"name": name, "path": f"uploads/{name}",
                                       "bytes": os.path.getsize(dst)}])
            store.save_org(o2)
        probe_i = os.path.join(DATA, f"wsprobe-{kind}.json")
        os.environ["FAKEANTIGRAVITY_WSPROBE"] = probe_i
        try:
            run_turn(s, n, "go")
            return str(json.load(open(probe_i, encoding="utf-8")
                                 ).get("prompt") or "")
        finally:
            os.environ.pop("FAKEANTIGRAVITY_WSPROBE", None)

    def t4i():
        p = _img_org("image")
        # the mail reached the prompt at all — without this, an empty prompt
        # would satisfy "no image was dropped silently" for the wrong reason
        assert "[ATTACHED FILE:" in p and "shot.png" in p, p[-400:]
        assert "could not be inlined on this provider" in p, p[-400:]
        assert "view_file" in p, "the note names the tool that CAN see it"
    check("a user's image is ANNOUNCED in the prompt on this lane, not "
          "dropped, and the note names view_file", t4i)

    def t4j():
        p = _img_org("text")
        assert "[ATTACHED FILE:" in p and "notes.txt" in p, p[-400:]
        assert "could not be inlined" not in p, \
            "a non-image attachment must not fire the image note"
    check("…and an ordinary attachment fires no image note (control)", t4j)

    def t4k():
        note = supervisor._antigravity_image_note
        blk = [{"type": "image"}, {"type": "image"}, {"type": "text"}]
        eq((note([]), "2 image attachments" in note(blk),
            "1 image attachment " in note(blk[:1])),
           ("", True, True), f"{note(blk)!r}")
    check("the note counts only image blocks, is plural-correct, and is "
          "EMPTY when there are none", t4k)

    print("§4 identity + env hygiene at the leg")

    def t4():
        am = scratch_file(s1, n1, "AGENTS.md")
        assert os.path.exists(am), "AGENTS.md written"
        body = open(am, encoding="utf-8").read()
        assert n1 in body, "identity names the node"
        assert os.path.exists(scratch_file(
            s1, n1, ".agents", "plugins", "orgtree", "plugin.json")), \
            "the orgtree plugin marker exists"
        assert not os.path.exists(scratch_file(s1, n1, ".agents",
                                               "hooks.json")), \
            "a full-rights node carries no rights hook"
        envp = os.path.join(DATA, "envprobe.json")
        os.environ["ANTHROPIC_API_KEY"] = "planted-anthropic"
        os.environ["OPENAI_API_KEY"] = "planted-openai"
        os.environ["FAKEANTIGRAVITY_ENVPROBE"] = (
            "ANTHROPIC_API_KEY,OPENAI_API_KEY,ORGTREE_ORG,ORGTREE_NODE")
        os.environ["FAKEANTIGRAVITY_ENVPROBE_PATH"] = envp
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
                      "FAKEANTIGRAVITY_ENVPROBE",
                      "FAKEANTIGRAVITY_ENVPROBE_PATH"):
                os.environ.pop(k, None)
    check("AGENTS.md carries the identity; the child env is hygienic", t4)

    print("§5 the ⚙-rights seam: a narrowed node gets the hook")
    s5r, n5r = mkorg("rights", bash=False)
    os.environ["FAKEANTIGRAVITY_SCENARIO"] = "hookdeny"

    def t5r():
        run_turn(s5r, n5r, "try a command")
        hooks = scratch_file(s5r, n5r, ".agents", "hooks.json")
        assert os.path.exists(hooks), "hooks.json written for bash-off"
        doc = json.load(open(hooks, encoding="utf-8"))
        cmd = doc["orgtree-rights"]["PreToolUse"][0]["hooks"][0]["command"]
        assert os.path.exists(cmd.strip('"')), f"wrapper exists: {cmd}"
        rp = open(scratch_file(s5r, n5r, ".agents", "orgtree-rights.py"),
                  encoding="utf-8").read()
        assert '"run_command"' in rp and '"write_to_file"' not in rp, \
            "only the shell class is denied for bash-off"
        eq(supervisor.state(s5r, n5r).get("last_error"), None,
           "a hook denial is not a failed turn")
    check("bash-off writes the PreToolUse hook denying exactly the shell "
          "class, and a denied call leaves the turn completed", t5r)

    # the same seam for the other two switches and for the plan seat, AT THE
    # LEG — write_workspace is tested directly in test_antigravityrun §6b;
    # what this pins is that the leg actually HANDS it all four, which is
    # where `web`/`subagents` were dropped and where `plan` never arrived.
    s5w, n5w = mkorg("webless", web=False)
    s5s, n5s = mkorg("subless", subagents=False)
    s5p, n5p = mkorg("planseat")
    _org_p = store.load_org(s5p)
    _org_p.d["nodes"][n5p]["scope"]["permission_mode"] = "plan"
    store.save_org(_org_p)

    def rights_source(slug: str, nid: str) -> str:
        # a narrowed node that writes NO hook at all is the exact failure
        # these three checks exist for, so say that instead of dying on a
        # bare FileNotFoundError from open()
        p = scratch_file(slug, nid, ".agents", "orgtree-rights.py")
        assert os.path.exists(p), \
            f"a narrowed node must carry a rights hook, none written: {p}"
        return open(p, encoding="utf-8").read()

    def t5w():
        run_turn(s5w, n5w, "look something up")
        rp = rights_source(s5w, n5w)
        # measured names first: these two appear in real Antigravity turns
        assert '"search_web"' in rp and '"read_url_content"' in rp, \
            "the web class is denied for a web-off node"
        assert '"run_command"' not in rp and '"write_to_file"' not in rp, \
            "and nothing else is"
    check("web-off reaches the leg's hook: the web class is denied, the "
          "shell and edit classes are untouched", t5w)

    def t5s():
        run_turn(s5s, n5s, "delegate this")
        rp = rights_source(s5s, n5s)
        assert '"invoke_subagent"' in rp and '"manage_subagents"' in rp, \
            "the subagent class is denied for a subagents-off node"
        assert '"run_command"' not in rp and '"search_web"' not in rp \
            and '"write_to_file"' not in rp, "and nothing else is"
    check("subagents-off reaches the leg's hook: the subagent class is "
          "denied, every other class is untouched", t5s)

    def t5p():
        run_turn(s5p, n5p, "plan something")
        rp = rights_source(s5p, n5p)
        # the plan seat loses the ability to change files — the same door
        # `_codex_may_write` closes on the codex lane, with the edit switch
        # still ON here — and on THIS lane the terminal goes with it, because
        # a shell is a write tool the moment you redirect and this CLI has no
        # verified read-only shell to hold one honest
        assert '"write_to_file"' in rp and '"replace_file_content"' in rp, \
            "a plan-mode node cannot write"
        assert '"run_command"' in rp and '"send_command_input"' in rp, \
            "…and cannot reach the same write through the shell"
        assert '"search_web"' not in rp and '"view_file"' not in rp, \
            "plan mode closes the write door and the shell, nothing wider"
    check("permission_mode=plan reaches this lane at all: the edit class AND "
          "the shell class are denied with the edit switch still on", t5p)

    def t5pi():
        # the seat is also TOLD, in the identity the leg writes for it: a
        # terminal promised to a seat whose every shell call is denied is the
        # failure the codex read-only prompt line was rewritten to avoid
        ident = open(scratch_file(s5p, n5p, "AGENTS.md"),
                     encoding="utf-8").read()
        assert "Terminal: CLOSED for this seat" in ident, \
            "the plan seat is told its terminal is shut"
        assert "Terminal: Bash and PowerShell" not in ident, \
            "and is NOT also promised one"
        # the control: an ordinary writable node on this lane still gets the
        # promise, so the line above is not simply missing everywhere
        other = open(scratch_file(s5w, n5w, "AGENTS.md"),
                     encoding="utf-8").read()
        assert "Terminal: Bash and PowerShell" in other and \
            "Terminal: CLOSED" not in other, "the writable seat keeps its shell"
    check("the plan seat's identity says the terminal is closed, and a "
          "writable seat on the same lane is still promised one", t5pi)

    print("§6 the planted faults the detectors must SEE")
    s6, n6 = mkorg("fault")
    os.environ["FAKEANTIGRAVITY_SCENARIO"] = "text"

    def t6():
        sign_in(False)
        try:
            follow = run_turn(s6, n6, "doomed")
            eq(follow, None, "no follow")
            err = supervisor.state(s6, n6).get("last_error") or ""
            assert "not signed in" in err, f"error names the remedy: {err!r}"
            assert "antigravity" in err.lower(), err
            n = node_doc(s6, n6)
            assert "antigravity_conversation" not in n, "no turn ever started"
        finally:
            sign_in(True)
    check("signed-out antigravity fails loudly, never silently", t6)

    def t6b():
        os.environ["FAKEANTIGRAVITY_SCENARIO"] = "wrongmodel"
        try:
            run_turn(s6, n6, "wrong model")
            err = supervisor.state(s6, n6).get("last_error") or ""
            assert "model pin refused" in err, \
                f"the substituted model must be refused: {err!r}"
        finally:
            os.environ["FAKEANTIGRAVITY_SCENARIO"] = "text"
    check("an init serving the wrong model fails the turn loudly", t6b)

    def t6c():
        os.environ["FAKEANTIGRAVITY_SCENARIO"] = "unknownmodel"
        try:
            run_turn(s6, n6, "unknown model")
            err = supervisor.state(s6, n6).get("last_error") or ""
            assert "invalid model selection" in err, \
                f"the CLI's refusal must surface in its words: {err!r}"
        finally:
            os.environ["FAKEANTIGRAVITY_SCENARIO"] = "text"
    check("the CLI's own refusal of an unknown model is the turn's error",
          t6c)

    print("§7 interrupt + the queue handoff through the shared finally")
    s7, n7 = mkorg("live")

    def t7():
        os.environ["FAKEANTIGRAVITY_SCENARIO"] = "interrupt"
        result: dict = {}

        def _run():
            result["follow"] = run_turn(s7, n7, "stall until killed")

        th = threading.Thread(target=_run, daemon=True)
        th.start()
        st = supervisor.state(s7, n7)
        deadline = time.time() + 10
        while time.time() < deadline and "antigravity_turn" not in st:
            time.sleep(0.05)
        assert "antigravity_turn" in st, "the live turn handle appeared"
        eq(st.get("responding"), True, "responding while live")
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
        r = supervisor.interrupt_turn(s7, n7)
        eq(r.get("interrupted"), True, "interrupt accepted")
        th.join(timeout=20)
        assert not th.is_alive(), "the turn came back"
        follow = result["follow"]
        text = follow.get("text") if isinstance(follow, dict) else follow
        eq(text, "boundary mail", "queue handoff via the shared finally")
        eq(supervisor.state(s7, n7).get("last_error"), None,
           "interrupted is a completed turn, not a failure")
        # the request the CLI priced BEFORE the kill is billed: 8290 in /
        # 1200 cached / 48 out at the pro row = 0.017396
        cost = float(node_doc(s7, n7).get("cost_usd") or 0.0)
        eq(round(cost, 6), 0.017396, "a killed turn books the usage it saw")
        eq(node_doc(s7, n7).get("antigravity_conversation"),
           "fake-agy-conv-0001", "the conversation survives the kill")
    check("mid-turn mail falls back to the queue and the kill hands it to "
          "the next turn, with the partial usage booked", t7)

    print("§8 the connected-provider hire gate")
    from orgtree.api import provider_hire_gate
    from orgtree.ledger import LedgerError

    def expect_refusal(fn, needle):
        try:
            fn()
        except LedgerError as e:
            assert needle in str(e), f"said {e!r}, wanted {needle!r}"
            return
        raise AssertionError(f"no refusal ({needle!r} expected)")

    def t8():
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
        # the CLI has no keyed login at all, so headless ALWAYS refuses
        expect_refusal(lambda: provider_hire_gate(org, "pro"), "headless")
        org.d.pop("headless")
    check("gate: connected passes; signed-out, kiosk and headless refuse "
          "naming the remedy (no keyed login exists on this provider)", t8)

    print("§9 the MEASURED usage wall through the real leg: frozen on the "
          "CLI's own reset, and the account standing follows (D-209)")
    from orgtree import antigravity_limits, turnusage       # noqa: PLC0415
    antigravity_limits.invalidate()
    WALL_SECS = 165 * 3600 + 21 * 60 + 54                    # "165h21m54s"
    s9, n9 = mkorg("wall")
    os.environ["FAKEANTIGRAVITY_SCENARIO"] = "usage_limit"
    os.environ.pop("FAKEANTIGRAVITY_RESET_IN", None)

    def t9():
        t0 = time.time()
        run_turn(s9, n9, "hit the wall")
        fz = node_doc(s9, n9).get("frozen") or {}
        eq(fz.get("limit"), True, "frozen as a usage limit")
        eq(fz.get("reset_src"), "provider",
           "timed by the reset the CLI itself named — not the probe floor")
        until = float(fz.get("until_ts") or 0)
        assert abs(until - (t0 + WALL_SECS)) < 30, \
            f"until_ts {until} vs {t0 + WALL_SECS} (a probe floor would be +300)"
        assert "probing" not in str(fz.get("until")), fz.get("until")
        assert "Individual quota reached" in str(fz.get("error")), fz
        # the replay is the CONSUMED prompt — the whole turn envelope (org
        # state + usage board + the text), which is what the CLI was handed
        assert str((fz.get("resume_texts") or [""])[-1]).endswith("hit the wall"),             "the consumed prompt replays on thaw"
        err = supervisor.state(s9, n9).get("last_error") or ""
        assert "Individual quota reached" in err, err
        snap = antigravity_limits.snapshot()
        eq((snap["available"], snap["limits"][0]["percent"],
            snap["limits"][0]["label"]),
           (True, 100.0, "individual quota"), "the standing holds the wall")
        assert abs(float(supervisor.time.time()) - t0) < 60   # sanity
        block = turnusage.render(store.load_org(s9), n9,
                                 selected_provider="google")
        line = [l for l in block.splitlines()
                if l.startswith("antigravity/account*")][0]
        assert "provider_window | 100% |" in line and line.endswith("| frozen"), \
            line
    check("a wall freezes the node on its own reset (~6d21h, source "
          "'provider'), records the standing, and the board row reads it", t9)

    def t9b():
        os.environ["FAKEANTIGRAVITY_SCENARIO"] = "text"
        sb, nb = mkorg("thaw")
        run_turn(sb, nb, "hello again")
        eq(node_doc(sb, nb).get("frozen"), None, "a plain turn is not frozen")
        eq(antigravity_limits.snapshot()["available"], False,
           "a completed turn clears the wall from the standing")
    check("a completed turn on the same account clears the wall", t9b)

    def t9c():
        os.environ["FAKEANTIGRAVITY_SCENARIO"] = "usage_limit"
        os.environ["FAKEANTIGRAVITY_RESET_IN"] = ""       # no reset named
        sc, nc = mkorg("noreset")
        t0 = time.time()
        try:
            run_turn(sc, nc, "a wall that names no reset")
        finally:
            os.environ.pop("FAKEANTIGRAVITY_RESET_IN", None)
            os.environ["FAKEANTIGRAVITY_SCENARIO"] = "text"
        fz = node_doc(sc, nc).get("frozen") or {}
        eq((fz.get("limit"), fz.get("reset_src")), (True, "probe"),
           "no reset named → the honest probe floor, as before")
        assert abs(float(fz["until_ts"]) - (t0 + supervisor.PROBE_FLOOR)) < 30
        snap = antigravity_limits.snapshot()
        eq((snap["available"], snap["limits"][0]["resets_at"]),
           (True, None), "the standing records the wall, undated")
        antigravity_limits.invalidate()
    check("a wall that names no reset still freezes — on the probe floor — "
          "and stands undated", t9c)

    print()
    if FAIL:
        print(f"{PASS} passed, {len(FAIL)} FAILED")
        for label, tb in FAIL:
            print(f"\n--- {label}\n{tb}")
        sys.exit(1)
    print(f"{PASS} checks passed")


if __name__ == "__main__":
    main()
