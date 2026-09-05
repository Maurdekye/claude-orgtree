"""THE ORDERING INVARIANT AND THE IDENTITY CONTRACT, on the antigravity lane
(audit D4, reset-action-plan item 5).

    python backend/tests/test_antigravity_stream_order.py   (no pytest)

INVARIANTS (the Codex leg's, transposed — see test_codex_stream_order.py):
  1. no assistant output for a turn becomes VISIBLE before that turn's user
     message is DURABLE in the journal;
  2. the journal holds each completed text step and each tool in the order
     the wire delivered them — text before a tool stays before it;
  3. every turn and every item carries an identity no other turn of the
     same conversation reuses;
  4. a repeated completion for an item already committed writes nothing
     and shows nothing;
  5. blocks completed before a failure are on disk when the failure is
     reported, and an unfinished step's streamed text is kept under its own
     identity rather than lost;
  6. a draft `delta` never follows the `text` handover that retired the
     draft — the timer flush and the handover are serialized (§10).

Measured on main f217d94 (2026-09-05, `probe_d4.py`): the antigravity leg
kept none of these. `AntigravityTurn._pump` dispatches every wire event from
the moment of spawn, and the leg journaled the user row only after `start()`
returned, so deltas and tool rows reached the desk with ZERO user rows on
disk; text was joined across the turn and written once at the end, after
both tools; the final text row's id was the conversation id, so every
resumed turn reused it, and tool ids `agy-<cid8>-<step>` repeated on every
resume as well.

Hermetic: `supervisor._run_one_turn` in process against fakeantigravity.py.
ORGTREE_PORT is a PORT NOBODY SERVES; ORGTREE_DATA is a throwaway.

Anti-vacuity, per section: §1 proves the race actually happened (step
events were already in the turn object when `start()` returned); §4 proves
the fixture really emitted duplicated completions (counted on the wire);
§5/§6 prove the failure was a real failure (`last_error` set); §7's
startup-failure org then runs a good turn as its positive control; §9 feeds
the instrument a synthetic violation and requires it to be reported; every
section asserts a non-zero count of visible payloads.
"""

import json
import os
import sys
import tempfile
import time
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-agyorder-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_PORT"] = "9"
os.environ["ORGTREE_WARM"] = "0"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
FAKE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "fakeantigravity.py")
os.environ["ORGTREE_ANTIGRAVITY"] = FAKE
os.environ["ORGTREE_CODEX"] = os.path.join(DATA, "nowhere", "codex.exe")
os.environ["CODEX_HOME"] = os.path.join(DATA, "chome")
os.environ.pop("FAKEANTIGRAVITY_SIGNED_OUT", None)

from orgtree import antigravityrun, providers, store, supervisor   # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

providers._antigravity_status_cache = None
supervisor.CODEX_STEER_POLL = 0.2

PASS = 0
FAIL: list[tuple[str, str]] = []
VISIBLE_KINDS = {"delta", "text", "tool", "thought"}
#: the conversation id the CURRENT section's fixture reports — see scenario()
CID = "fake-agy-conv-0001"


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


def truthy(cond, what):
    if not cond:
        raise AssertionError(what)


# ── the instrument ──────────────────────────────────────────────────────────
def journal_lines(slug: str, sid: str | None = None) -> list[dict]:
    p = os.path.join(supervisor.journal_store(), "projects", slug,
                     (sid or CID) + ".jsonl")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(x) for x in f if x.strip()]


def is_user_prompt(rec: dict) -> bool:
    msg = rec.get("message")
    return (rec.get("type") == "user" and isinstance(msg, dict)
            and isinstance(msg.get("content"), str))


def user_prompts(slug: str) -> int:
    return sum(1 for r in journal_lines(slug) if is_user_prompt(r))


def shape(recs: list[dict]) -> list[tuple[str, str, str]]:
    """(kind, message id, text-or-tool-name) for every text / tool_use /
    tool_result block, in file order."""
    rows = []
    for rec in recs:
        msg = rec.get("message") or {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                rows.append(("text", str(msg.get("id")), str(part.get("text"))))
            elif part.get("type") == "tool_use":
                rows.append(("tool_use", str(part.get("id")), str(part.get("name"))))
            elif part.get("type") == "tool_result":
                rows.append(("tool_result", str(part.get("tool_use_id")),
                             str(part.get("content"))))
    return rows


def draft_seam() -> dict:
    """§10's instrument. Executes the leg's ACTUAL `_flush_draft` and
    `_commit_text` (AST-extracted from the imported supervisor's source) in a
    controlled namespace: the draft timer thread is held INSIDE its emission
    after it has taken the buffer, a second thread then runs the handover,
    and the instrument records whether that handover waited. Locks and state
    are the real names the bodies close over; `_visible` is the open-journal
    path (the barrier is §1's business, not this seam's)."""
    import ast
    import threading
    src = open(supervisor.__file__, encoding="utf-8").read()
    leg = next(n for n in ast.parse(src).body
               if isinstance(n, ast.FunctionDef)
               and n.name == "_antigravity_leg")
    fns = [n for n in leg.body if isinstance(n, ast.FunctionDef)
           and n.name in ("_flush_draft", "_commit_text")]
    if len(fns) != 2:
        raise AssertionError(f"seam: found {[f.name for f in fns]}")

    def run(hold_timer: bool) -> dict:
        frames: list[str] = []
        journal: list[dict] = []
        extracted = threading.Event()
        release = threading.Event()

        def visible_stream(payload):
            if hold_timer and threading.current_thread().name == "draft-timer":
                extracted.set()
                if not release.wait(5):
                    frames.append("timer-timeout")
            frames.append(payload["kind"])

        env = dict(dlock=threading.Lock(), jlock=threading.Lock(),
                   dstate={"buf": "hello", "timer": None},
                   jstate={"agent_items": 0},
                   _visible_stream=visible_stream, _visible=lambda f: f(),
                   _journal_records=lambda rows: journal.extend(rows),
                   _text_became_durable=lambda *a: None,
                   stream=lambda s, n, payload: frames.append(payload["kind"]),
                   slug="seam", nid="seam", model_id="seam")
        exec(compile(ast.Module(body=fns, type_ignores=[]),
                     "<antigravity-leg-bodies>", "exec"), env)
        out: dict = {"extracted": False, "handover_waiting": None,
                     "frames_before_release": None}
        if hold_timer:
            timer = threading.Thread(target=env["_flush_draft"],
                                     name="draft-timer", daemon=True)
            timer.start()
            out["extracted"] = extracted.wait(5)
            handover = threading.Thread(
                target=env["_commit_text"], args=("item", "hello", "stamp"),
                name="reader", daemon=True)
            handover.start()
            handover.join(0.5)           # a WAITING handover is still alive
            out["handover_waiting"] = handover.is_alive()
            out["frames_before_release"] = list(frames)
            release.set()
            timer.join(5)
            handover.join(5)
            if timer.is_alive() or handover.is_alive():
                frames.append("deadlock")
        else:
            env["_commit_text"]("item", "hello", "stamp")
        out.update(frames=frames, durable_rows=len(journal))
        return out

    ctl = run(False)
    result = run(True)
    result["control"] = {"frames": ctl["frames"],
                         "durable_rows": ctl["durable_rows"]}
    return result


SEEN: list[dict] = []
WATCH: dict[str, str] = {"slug": ""}


def recording_stream(slug: str, nid: str, payload: dict) -> None:
    if WATCH["slug"] and slug == WATCH["slug"]:
        rec = {**payload, "_rows": user_prompts(slug)}
        if payload.get("kind") == "text":
            # a handover frame retires the desk's draft: its replacement row
            # must ALREADY be on disk when the frame goes out (D-50)
            rec["_durable"] = any(
                v == payload.get("text")
                for k, _, v in shape(journal_lines(slug)) if k == "text")
        SEEN.append(rec)


supervisor.stream = recording_stream


def visible(seen=None) -> list[dict]:
    return [p for p in (SEEN if seen is None else seen)
            if p.get("kind") in VISIBLE_KINDS]


def violations(seen=None, need: int = 1) -> list[tuple[str, int]]:
    return [(p["kind"], p["_rows"]) for p in visible(seen) if p["_rows"] < need]


TURNS: list[antigravityrun.AntigravityTurn] = []
_orig_init = antigravityrun.AntigravityTurn.__init__


def _capturing_init(self, *a, **kw):
    _orig_init(self, *a, **kw)
    TURNS.append(self)


antigravityrun.AntigravityTurn.__init__ = _capturing_init


# ── fixtures ────────────────────────────────────────────────────────────────
def mkorg(label: str) -> tuple[str, str]:
    org = store.create_org(f"zz agyorder {label}")
    r = org.hire(USER, None, "pro", 2, "ag", add_dirs=[],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": False, "mcp": []},
                 org_visibility="team", charter="an antigravity ordering agent")
    nid = r["node"]
    store.save_org(org)
    return org.d["slug"], nid


def run_turn(slug: str, nid: str, text: str):
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["busy"] = True
    return supervisor._run_one_turn(slug, nid, {"text": text, "view": text})


def scenario(name: str, cid: str) -> None:
    """Pick the fixture's behaviour AND give this section its own
    conversation id. `transcript_path` globs `projects/*/<session>.jsonl`
    across ALL projects (a session id is unique in production), and the
    fixture's default id is a constant — so two test orgs sharing it would
    hand the second org the FIRST one's journal. A fixture collision, not a
    product fact (the codex ordering suite learned the same lesson)."""
    global CID
    CID = cid
    os.environ["FAKEANTIGRAVITY_SCENARIO"] = name
    os.environ["FAKEANTIGRAVITY_CONVERSATION_ID"] = cid


def main() -> int:
    print("§1 stream-before-commit: the wire outruns start()'s return")
    scenario("toolevents", "agy-order-early")
    slug, nid = mkorg("early")
    SEEN.clear()
    WATCH["slug"] = slug
    raced: dict = {}
    original_start = antigravityrun.AntigravityTurn.start

    def slow_return(self, text):
        cid = original_start(self, text)
        time.sleep(0.4)          # widen the LEGAL window, change nothing else
        with self._lock:
            raced["steps_at_return"] = sum(
                1 for e in self.events if e.get("event") == "step_update")
        return cid

    antigravityrun.AntigravityTurn.start = slow_return
    try:
        run_turn(slug, nid, "does the answer wait for the question?")
    finally:
        antigravityrun.AntigravityTurn.start = original_start
    early = list(SEEN)
    check("anti-vacuity: step events had ALREADY arrived when start() "
          "returned, so the race is real, not arranged",
          lambda: truthy(raced.get("steps_at_return", 0) > 0,
                         f"steps at return: {raced}"))
    check("the fixture produced assistant-visible output",
          lambda: truthy(visible(early), "no visible payloads"))
    check("delta, tool and text frames were all exercised",
          lambda: eq(sorted({p["kind"] for p in visible(early)}),
                     ["delta", "text", "tool"], "visible kinds"))
    check("NO assistant output reached the desk before the user's row was "
          "durable",
          lambda: eq(violations(early), [], "pre-commit emissions"))
    check("the turn itself completed (the barrier did not swallow it)",
          lambda: eq(supervisor.state(slug, nid).get("last_error"), None,
                     "last_error"))

    print("§2 the journal is chronological: text → tool → tool → text")
    recs = journal_lines(slug)
    check("the turn's user row is the FIRST record",
          lambda: truthy(recs and is_user_prompt(recs[0]),
                         f"first record: {recs[:1]}"))
    check("exactly one user prompt row for one turn",
          lambda: eq(user_prompts(slug), 1, "user prompt rows"))
    check("text said before the tools is journaled BEFORE them, and the "
          "closing text after — four blocks, not three",
          lambda: eq([(k, v) for k, _, v in shape(recs)
                      if k in ("text", "tool_use")],
                     [("text", "working… "), ("tool_use", "orgtree_ping"),
                      ("tool_use", "run_command"), ("text", "done.\n")],
                     "durable order"))
    check("every text handover frame found its durable twin on disk at the "
          "instant it was emitted (the row precedes the frame)",
          lambda: eq([p.get("_durable") for p in early
                      if p.get("kind") == "text"], [True, True],
                     "durable-at-emission per text frame"))
    check("each tool_result names the tool_use it answers, in order",
          lambda: eq([(v) for k, v, _ in shape(recs) if k == "tool_result"],
                     [v for k, v, _ in shape(recs) if k == "tool_use"],
                     "tool_result ids follow tool_use ids"))
    check("the desk got one text handover frame PER completed text step, "
          "and together they carry exactly what the deltas streamed",
          lambda: eq(("".join(p.get("text", "") for p in early
                              if p.get("kind") == "text"),
                      sum(1 for p in early if p.get("kind") == "text")),
                     ("".join(p.get("text", "") for p in early
                              if p.get("kind") == "delta"), 2),
                     "text frames vs deltas"))

    print("§3 identities are unique across a RESUMED conversation")
    SEEN.clear()
    run_turn(slug, nid, "second question")
    second = list(SEEN)
    # a THIRD turn, so two RESUMED turns stand side by side: an id built
    # from the conversation id and the step index differs between a fresh
    # turn and its first resume by accident (no id vs an id), and only
    # repeats from the second resume on — a two-turn check would let that
    # wrong fix through (a mutant did, 2026-09-05)
    run_turn(slug, nid, "third question")
    recs2 = journal_lines(slug)
    rows2 = shape(recs2)
    text_ids = [i for k, i, _ in rows2 if k == "text"]
    tool_ids = [i for k, i, _ in rows2 if k == "tool_use"]
    usage_ids = [str((r.get("message") or {}).get("id"))
                 for r in recs2 if (r.get("message") or {}).get("usage")]
    check("the second turn really resumed the conversation (the fixture "
          "prefixes a resumed turn's first text)",
          lambda: truthy(any(v.startswith(f"RESUMED:{CID}")
                             for k, _, v in rows2 if k == "text"),
                         f"no RESUMED marker in {rows2}"))
    check("turn 2 emitted nothing before ITS OWN (second) user row",
          lambda: eq(violations(second, need=2), [], "turn 2 pre-commit"))
    check("six text rows over three turns, six distinct message ids",
          lambda: eq((len(text_ids), len(set(text_ids))), (6, 6),
                     f"text ids {text_ids}"))
    check("six tool_use rows over three turns, six distinct item ids "
          "(`_sweep_live` treats tool_use_id as globally unique)",
          lambda: eq((len(tool_ids), len(set(tool_ids))), (6, 6),
                     f"tool ids {tool_ids}"))
    check("no text id collides with a tool id, and none is the bare "
          "conversation id",
          lambda: eq((set(text_ids) & set(tool_ids),
                      f"agy-{CID}" in text_ids), (set(), False), "id classes"))
    check("three usage rows, distinct, all keeping the `agy-…-usage` shape "
          "the occupancy reader keys on",
          lambda: eq((len(usage_ids), len(set(usage_ids)),
                      all(u.startswith("agy-") and u.endswith("-usage")
                          for u in usage_ids)), (3, 3, True),
                     f"usage ids {usage_ids}"))
    check("the live tool rows carried the SAME ids the journal holds",
          lambda: eq([p.get("id") for p in early + second
                      if p.get("kind") == "tool"], tool_ids[:4],
                     "live tool ids vs durable tool_use ids"))
    check("three turns, exactly three user rows",
          lambda: eq(user_prompts(slug), 3, "user rows"))

    print("§4 a repeated completion is journaled once and shown once")
    scenario("dupdone", "agy-order-dup")
    slug4, nid4 = mkorg("dup")
    SEEN.clear()
    WATCH["slug"] = slug4
    TURNS.clear()
    run_turn(slug4, nid4, "say it once")
    dup = list(SEEN)
    rows4 = shape(journal_lines(slug4))
    wire_done = [e for t in TURNS for e in t.events
                 if e.get("event") == "step_update"
                 and (e.get("step_update") or {}).get("state") == "DONE"]
    check("anti-vacuity: the wire really carried every DONE twice",
          lambda: truthy(wire_done and len(wire_done) == 2 * len({
              (d["step_update"]["step_index"]) for d in wire_done}),
              f"DONE events on the wire: {len(wire_done)}"))
    check("the turn completed",
          lambda: eq(supervisor.state(slug4, nid4).get("last_error"), None,
                     "last_error"))
    check("each text step is journaled ONCE",
          lambda: eq([v for k, _, v in rows4 if k == "text"],
                     ["working… ", "done.\n"], "text rows"))
    check("each tool is journaled ONCE, with one result",
          lambda: eq(([v for k, _, v in rows4 if k == "tool_use"],
                      len([1 for k, _, _ in rows4 if k == "tool_result"])),
                     (["orgtree_ping", "run_command"], 2), "tool rows"))
    check("…and the desk saw exactly two text handovers, not four",
          lambda: eq(sum(1 for p in dup if p.get("kind") == "text"), 2,
                     "text frames"))
    check("the rendered transcript shows the answer once per step",
          lambda: eq(sum(1 for m in supervisor.read_chat(
              store.load_org(slug4), nid4)["messages"]
              if "done." in str(m.get("text") or "")), 1, "rendered copies"))
    check("the replayed turn kept the ordering invariant",
          lambda: eq(violations(dup), [], "pre-commit emissions"))

    print("§5 failure AFTER completed blocks keeps them")
    scenario("diesafterstep", "agy-order-dies")
    slug5, nid5 = mkorg("dies")
    SEEN.clear()
    WATCH["slug"] = slug5
    run_turn(slug5, nid5, "die after the tools")
    died = list(SEEN)
    rows5 = shape(journal_lines(slug5))
    err5 = str(supervisor.state(slug5, nid5).get("last_error") or "")
    check("anti-vacuity: the turn really failed, in the CLI's exit words",
          lambda: truthy("rc=1" in err5, f"last_error: {err5!r}"))
    check("the completed text step and both tools are on disk despite the "
          "failure",
          lambda: eq([(k, v) for k, _, v in rows5 if k != "tool_result"],
                     [("text", "working… "), ("tool_use", "orgtree_ping"),
                      ("tool_use", "run_command")], "durable blocks"))
    check("both tool results are on disk too",
          lambda: eq([v for k, _, v in rows5 if k == "tool_result"],
                     ["PONG:hi", "HOOK-CMD\r\n"], "tool results"))
    check("the completed text was handed over to the desk before the "
          "failure (a text frame, after the user row)",
          lambda: eq([(p.get("text"), p["_rows"]) for p in died
                      if p.get("kind") == "text"], [("working… ", 1)],
                     "text frames"))
    check("nothing was invented: no closing text row, no usage row",
          lambda: eq((any(v == "done.\n" for k, _, v in rows5 if k == "text"),
                      any((r.get("message") or {}).get("usage")
                          for r in journal_lines(slug5))),
                     (False, False), "invented rows"))

    print("§6 partial output: an unfinished step's text is kept under its "
          "own identity")
    scenario("diesmidstep", "agy-order-partial")
    slug6, nid6 = mkorg("partial")
    SEEN.clear()
    WATCH["slug"] = slug6
    run_turn(slug6, nid6, "die mid-sentence")
    rows6 = shape(journal_lines(slug6))
    err6 = str(supervisor.state(slug6, nid6).get("last_error") or "")
    check("anti-vacuity: the turn failed",
          lambda: truthy("rc=1" in err6, f"last_error: {err6!r}"))
    check("the completed step AND the partial step are both on disk, in "
          "order, under two different ids",
          lambda: eq(([(k, v) for k, _, v in rows6],
                      len({i for _, i, _ in rows6})),
                     ([("text", "working… "),
                       ("text", "partial words before death ")], 2),
                     "rows"))
    check("the partial text reached the desk as a handover frame too",
          lambda: truthy(any(p.get("kind") == "text"
                             and p.get("text") == "partial words before death "
                             for p in SEEN), "no partial text frame"))

    print("§7 startup failure: nothing shows, nothing is journaled")
    scenario("wrongmodel", "agy-order-startfail")
    slug7, nid7 = mkorg("startfail")
    SEEN.clear()
    WATCH["slug"] = slug7
    run_turn(slug7, nid7, "a turn the pin refuses")
    startfail = list(SEEN)
    err7 = str(supervisor.state(slug7, nid7).get("last_error") or "")
    check("anti-vacuity: the pin refusal is the turn's error",
          lambda: truthy("model pin refused" in err7, f"last_error: {err7!r}"))
    check("no assistant output reached the desk for a turn that never "
          "started (held output is never released)",
          lambda: eq(visible(startfail), [], "visible frames"))
    check("no journal exists for the refused conversation",
          lambda: eq(journal_lines(slug7), [], "journal records"))
    scenario("text", "agy-order-startfail")
    SEEN.clear()
    run_turn(slug7, nid7, "and now a good turn")
    check("positive control: the same node then runs a good turn, journals "
          "it and shows it",
          lambda: eq((user_prompts(slug7),
                      [v for k, _, v in shape(journal_lines(slug7))
                       if k == "text"],
                      bool(visible())),
                     (1, ["working… ", "done.\n"], True), "good turn"))

    print("§8 reconnect: a fresh read of the transcript tells the live "
          "stream's story")
    scenario("toolevents", "agy-order-reconnect")
    slug8, nid8 = mkorg("reconnect")
    SEEN.clear()
    WATCH["slug"] = slug8
    epoch0 = int(supervisor.state(slug8, nid8).get("draft_epoch") or 0)
    run_turn(slug8, nid8, "reconnect me")
    live_story = [(p["kind"], p.get("text") if p["kind"] == "text"
                   else p.get("id")) for p in SEEN
                  if p.get("kind") in ("text", "tool")]
    payload = supervisor.read_chat(store.load_org(slug8), nid8)
    durable_story: list = []
    for m in payload["messages"]:
        if m.get("role") != "assistant":
            continue
        if m.get("text"):
            durable_story.append(("text", m["text"]))
        for t in m.get("tools") or []:
            durable_story.append(("tool", t.get("id")))
    check("the fresh projection lists the same text/tool sequence the live "
          "stream emitted — a client that reconnects sees the same story",
          lambda: eq(durable_story, live_story, "durable vs live story"))
    check("no live row is stranded after the turn (every tool row found its "
          "durable twin)",
          lambda: eq([r for r in payload["live"] if not r.get("sticky")], [],
                     "stranded live rows"))
    check("the draft epoch advanced once per durable text row, so a client "
          "that missed every `text` frame still retires its draft on the "
          "next poll",
          lambda: eq(int(supervisor.state(slug8, nid8).get("draft_epoch") or 0)
                     - epoch0, 2, "epoch advance"))
    check("the rendered transcript opens with the user and never opens a "
          "turn with the answer",
          lambda: eq([i for i, m in enumerate(payload["messages"])
                      if m.get("role") == "assistant"
                      and not any(x.get("role") == "user"
                                  for x in payload["messages"][:i])],
                     [], "assistant rows with no user row above"))

    print("§8b a SLOW wire: steps arrive after start() returned, so frames "
          "go out live rather than from the held queue")
    scenario("toolevents", "agy-order-slow")
    os.environ["FAKEANTIGRAVITY_STEP_DELAY"] = "0.08"
    slug9, nid9 = mkorg("slow")
    SEEN.clear()
    WATCH["slug"] = slug9
    returned: dict = {}

    def noting_return(self, text):
        cid = original_start(self, text)
        returned["at"] = time.time()
        return cid

    antigravityrun.AntigravityTurn.start = noting_return
    try:
        _orig_rec = supervisor.stream

        def timed_stream(slug_, nid_, payload):
            _orig_rec(slug_, nid_, payload)
            if WATCH["slug"] and slug_ == WATCH["slug"] and SEEN:
                SEEN[-1]["_at"] = time.time()
        supervisor.stream = timed_stream
        run_turn(slug9, nid9, "take your time")
    finally:
        supervisor.stream = _orig_rec
        antigravityrun.AntigravityTurn.start = original_start
        os.environ.pop("FAKEANTIGRAVITY_STEP_DELAY", None)
    slow = list(SEEN)
    check("anti-vacuity: visible frames were emitted AFTER start() returned "
          "(live, not released from the held queue)",
          lambda: truthy("at" in returned and any(
              p.get("_at", 0) > returned["at"] for p in visible(slow)),
              f"start returned {returned}; frames {[p.get('_at') for p in slow]}"))
    check("the slow turn kept the ordering invariant",
          lambda: eq(violations(slow), [], "pre-commit emissions"))
    check("every LIVE text frame found its durable twin on disk at the "
          "instant it went out — the row precedes the frame on the live path "
          "too, not only on release",
          lambda: eq([p.get("_durable") for p in slow
                      if p.get("kind") == "text"], [True, True],
                     "durable-at-emission per live text frame"))
    check("the slow turn journaled text → tool → tool → text",
          lambda: eq([(k, v) for k, _, v in shape(journal_lines(slug9))
                      if k in ("text", "tool_use")],
                     [("text", "working… "), ("tool_use", "orgtree_ping"),
                      ("tool_use", "run_command"), ("text", "done.\n")],
                     "durable order"))

    print("§9 anti-vacuity: the instrument can SEE a violation")
    check("a synthetic pre-commit emission is reported",
          lambda: eq(violations([{"kind": "delta", "_rows": 0},
                                 {"kind": "text", "_rows": 1}], need=1),
                     [("delta", 0)], "detected violations"))

    print("§10 the draft timer cannot overtake the text handover (Astra's "
          "seam review of 6ca27ad, 2026-09-05)")
    # A controlled-thread seam on the leg's ACTUAL `_flush_draft` and
    # `_commit_text` bodies (extracted by AST from the supervisor this test
    # imported — the tree under test, not a fixed path). The timer thread is
    # held INSIDE its emission after it took the buffer; the handover then
    # runs on a second thread. Before the amendment the handover's own flush
    # found an empty buffer, emitted `text`, and the timer's late `delta`
    # followed it (frames text→delta, one durable row): a stale draft revived
    # on the desk after its durable replacement. Now the handover must WAIT.
    seam = draft_seam()
    check("anti-vacuity: the timer thread really took the buffer and was "
          "held inside its emission (the seam was reached)",
          lambda: truthy(seam["extracted"], "timer never reached emission"))
    check("the handover BLOCKED while the timer's emission was in flight: "
          "its thread was still alive and no frame had gone out when the "
          "timer was released",
          lambda: eq((seam["handover_waiting"], seam["frames_before_release"]),
                     (True, []), "(handover alive, frames) while timer held"))
    check("frames after release: delta THEN text — the late timer delta "
          "cannot follow the draft's retirement",
          lambda: eq(seam["frames"], ["delta", "text"], "frame order"))
    check("exactly one durable text row for the step",
          lambda: eq(seam["durable_rows"], 1, "durable rows"))
    check("normal control (no preemption) is the same order with one row",
          lambda: eq(seam["control"], {"frames": ["delta", "text"],
                                       "durable_rows": 1}, "control"))

    print()
    for label, tb in FAIL:
        print(f"--- FAILED: {label}\n{tb}")
    print(f"{PASS} checks passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
