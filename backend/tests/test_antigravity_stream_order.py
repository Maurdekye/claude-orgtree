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
     draft — the timer flush and the handover are serialized (§10);
  7. a tool step still open when the process goes away is closed under its
     own id, exactly once, with a row that claims only that no result was
     obtained — never that the tool failed (§11, §12).

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
import threading
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


def _leg_functions(names):
    """The named closures of `_antigravity_leg`, AST-extracted from the
    supervisor THIS TEST IMPORTED — so a mutated copy of the tree is what
    gets exercised, not a fixed path."""
    import ast
    src = open(supervisor.__file__, encoding="utf-8").read()
    leg = next(n for n in ast.parse(src).body
               if isinstance(n, ast.FunctionDef)
               and n.name == "_antigravity_leg")
    fns = [n for n in leg.body if isinstance(n, ast.FunctionDef)
           and n.name in names]
    got = {f.name for f in fns}
    if got != set(names):
        raise AssertionError(f"seam: wanted {sorted(names)}, found "
                             f"{sorted(got)}")
    return fns


class GateLock:
    """A `jlock` that pauses ONE named thread just before ONE chosen BLOCKING
    acquisition, so a two-thread interleaving can be pinned instead of hoped
    for. Non-blocking probes (`acquire(False)`, which `Condition` uses to ask
    whether it owns the lock) are not counted. The wait is BOUNDED: a gate
    that is never released must not hang the suite, it must let the run
    finish and be judged on what it produced."""

    def __init__(self, thread_name=None, index=0):
        self._inner = threading.Lock()
        self._name, self._index = thread_name, index
        self._count = 0
        self.reached = threading.Event()
        self.go = threading.Event()

    def acquire(self, blocking=True, timeout=-1):
        if blocking and self._name                 and threading.current_thread().name == self._name:
            self._count += 1
            if self._count == self._index:
                self.reached.set()
                self.go.wait(8.0)
        return self._inner.acquire(blocking, timeout)

    def release(self):
        self._inner.release()

    __enter__ = acquire

    def __exit__(self, *a):
        self.release()


def protocol_env(gate: "GateLock", rows: list, *, tool_open=None,
                 flush_draft=None, journal_records=None):
    """The leg's ACTUAL event door, admitted body, claim and sweep, executed
    over a controlled namespace. Only the pause points are fakes."""
    fns = _leg_functions(["_d", "_item_id", "_committed", "_mark_committed",
                          "_claim_tool", "_tool_identity", "_on_event",
                          "_on_event_admitted", "_commit_unfinished_tools"])
    import ast
    from typing import Any, cast
    logged: list = []
    env = dict(
        cast=cast, Any=Any, time=time, jlock=gate,
        drained=threading.Condition(gate),
        jstate={"sid": "seam", "pending": [], "held": [],
                "text_open": {}, "text_order": [], "item_ids": set(),
                "tool_open": dict(tool_open or {}), "agent_items": 0,
                "inflight": 0, "finalized": False},
        turn_token="seamtoken", model_id="seam", slug="seam", nid="seam",
        now_iso=supervisor.now_iso, _tool_arg=supervisor._tool_arg,
        _journal_records=journal_records or (lambda recs: rows.extend(recs)),
        _log_turn_error=lambda s, n, text: logged.append(text),
        _visible_stream=lambda payload: None,
        _visible_live_row=lambda payload: None,
        _flush_draft=flush_draft or (lambda: None),
        _queue_delta=lambda body: None,
        _commit_text=lambda *a: None)
    exec(compile(ast.Module(body=fns, type_ignores=[]),
                 "<antigravity-leg-bodies>", "exec"), env)
    env["_logged"] = logged
    return env


def step_msg(state, output=None, index=3):
    info = {"name": "run_command", "parameters": {"CommandLine": "echo x"}}
    if output is not None:
        info["output"] = output
    return {"event": "step_update", "step_update": {
        "step_type": "tool", "state": state, "step_index": index,
        "tool_name": "run_command", "tool_info": info}}


def rows_shape(rows):
    out = []
    for r in rows:
        for b in ((r.get("message") or {}).get("content") or []):
            if isinstance(b, dict) and b.get("type") in ("tool_use",
                                                         "tool_result"):
                out.append((b["type"], str(b.get("content") or
                                          b.get("id") or "")[:38]))
    return out


def held_callback_seam(pause_at: str, msg: dict, *, tool_open=None):
    """A wire callback ADMITTED and then held mid-body — at `_flush_draft`
    (after its checks, before registration), inside `_journal_records`
    (after registration, before the row lands) or at its `_committed` check
    (a DONE, before its claim) — while the sweep runs on another thread.
    Returns what the sweep did while the callback was held, and the rows
    once both finished."""
    rows: list = []
    entered = threading.Event()
    release = threading.Event()

    def pause():
        entered.set()
        release.wait(8.0)

    gate = GateLock("wire", 2) if pause_at == "committed" else GateLock()
    if pause_at == "committed":
        gate.reached = entered
        gate.go = release

    def journal(recs):
        if pause_at == "journal" and rows_shape(recs)[:1] == [
                ("tool_use", "agy-seamtoken-3")]:
            pause()
        rows.extend(recs)

    env = protocol_env(gate, rows, tool_open=tool_open,
                       flush_draft=pause if pause_at == "flush" else None,
                       journal_records=journal)
    wire = threading.Thread(target=lambda: env["_on_event"](msg),
                            name="wire", daemon=True)
    sweep = threading.Thread(target=env["_commit_unfinished_tools"],
                             name="sweep", daemon=True)
    wire.start()
    reached = entered.wait(5)
    sweep.start()
    sweep.join(0.6)
    sweep_waited = sweep.is_alive()
    rows_while_held = rows_shape(rows)
    release.set()
    wire.join(10)
    sweep.join(10)
    return {"reached": reached, "sweep_waited": sweep_waited,
            "rows_while_held": rows_while_held, "rows": rows_shape(rows),
            "open_after": dict(env["jstate"]["tool_open"]),
            "drain_timed_out": list(env["_logged"]),
            "stuck": wire.is_alive() or sweep.is_alive()}


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

    print("§11 a tool step still open when the process goes away is CLOSED "
          "— exactly once, saying only what is known")

    def tool_rows(slug_):
        """(tool_use id → name, tool_use_id → [result bodies]) from disk."""
        uses, results = {}, {}
        for rec in journal_lines(slug_):
            content = (rec.get("message") or {}).get("content")
            for part in content if isinstance(content, list) else []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_use":
                    uses[part.get("id")] = part.get("name")
                elif part.get("type") == "tool_result":
                    results.setdefault(part.get("tool_use_id"), []).append(
                        {"body": str(part.get("content") or ""),
                         "is_error": bool(part.get("is_error"))})
        return uses, results

    def chips(slug_, nid_):
        out = []
        for m in supervisor.read_chat(store.load_org(slug_), nid_)["messages"]:
            for t in m.get("tools") or []:
                out.append({"name": t.get("name"), "id": t.get("id"),
                            "error": t.get("error"), "result": t.get("result")})
        return out

    UNAVAILABLE = "[orgtree: result unavailable]"

    # ── the positive control: a tool that DOES complete is untouched ────────
    scenario("toolevents", "agy-close-control")
    slug11, nid11 = mkorg("closecontrol")
    run_turn(slug11, nid11, "finish your tools")
    uses_c, res_c = tool_rows(slug11)
    check("positive control: both completed tools kept their own real "
          "results, none marked an error, none rewritten by the sweep",
          lambda: eq((len(uses_c), sorted(len(v) for v in res_c.values()),
                      [r["is_error"] for v in res_c.values() for r in v],
                      [UNAVAILABLE in r["body"]
                       for v in res_c.values() for r in v]),
                     (2, [1, 1], [False, False], [False, False]),
                     "completed-tool results"))
    check("positive control: every completed chip renders an outcome",
          lambda: eq([c["name"] for c in chips(slug11, nid11)
                      if not c["result"] and not c["error"]], [],
                     "chips with no outcome"))

    # ── a crash with one tool open ─────────────────────────────────────────
    scenario("diesmidtool", "agy-close-crash")
    slug12, nid12 = mkorg("closecrash")
    run_turn(slug12, nid12, "die holding a tool")
    uses_x, res_x = tool_rows(slug12)
    open_x = [i for i in uses_x if i not in res_x]
    check("anti-vacuity: the crash really left a tool open on the wire "
          "(two tool_use rows, and the run_command DONE never arrived)",
          lambda: eq((len(uses_x), sorted(uses_x.values())), (2, sorted(
              ["orgtree_ping", "run_command"])), f"tool_use rows {uses_x}"))
    check("every tool_use has a result — none is left dangling",
          lambda: eq(open_x, [], f"dangling tool ids {open_x}"))
    closed_x = [r for i, v in res_x.items() for r in v
                if UNAVAILABLE in r["body"]]
    check("the open tool got EXACTLY ONE generated result, and the tool that "
          "did complete kept its own",
          lambda: eq((len(closed_x), sorted(len(v) for v in res_x.values())),
                     (1, [1, 1]), f"results {res_x}"))
    check("the generated row is marked an error and says only that no result "
          "was obtained — it claims no CLI report and no failed side effect",
          lambda: eq((closed_x[0]["is_error"] if closed_x else None,
                      "outcome is unknown" in (closed_x[0]["body"]
                                               if closed_x else "")),
                     (True, True),
                     f"generated row {closed_x}"))
    check("the desk can tell the unfinished tool from a silent success",
          lambda: eq([bool(c["error"]) for c in chips(slug12, nid12)],
                     [False, True], "chip outcomes"))
    check("the turn itself is still booked as failed (the close does not "
          "launder a crash into a success)",
          lambda: truthy("without a result" in
                         str(supervisor.state(slug12, nid12).get("last_error")),
                         f"last_error {supervisor.state(slug12, nid12).get('last_error')}"))

    # ── several open ids, and a partial text block beside them ─────────────
    scenario("diesmidtools", "agy-close-many")
    slug13, nid13 = mkorg("closemany")
    run_turn(slug13, nid13, "die holding two tools")
    uses_m, res_m = tool_rows(slug13)
    check("anti-vacuity: the wire really left TWO tools open",
          lambda: eq(len(uses_m), 2, f"tool_use rows {uses_m}"))
    check("both open tools are closed, one generated result each",
          lambda: eq((sorted(len(v) for v in res_m.values()),
                      sum(1 for v in res_m.values() for r in v
                          if UNAVAILABLE in r["body"])), ([1, 1], 2),
                     f"results {res_m}"))
    order_m = [(k, v) for k, _, v in shape(journal_lines(slug13))
               if k in ("text", "tool_use", "tool_result")]
    check("the closes land after the tool_use rows they answer, and the "
          "partial text stays the last assistant block of the turn",
          lambda: eq(([k for k, _ in order_m],
                      order_m[-1][0] if order_m else None),
                     (["text", "tool_use", "tool_use", "tool_result",
                       "tool_result", "text"], "text"),
                     f"journal order {order_m}"))
    check("the partial text block survived beside the closed tools",
          lambda: truthy(any(v.startswith("partial words")
                             for k, v in order_m if k == "text"),
                         f"blocks {order_m}"))

    # ── the ⏸ path: a controlled interrupt with a tool open ────────────────
    scenario("interruptmidtool", "agy-close-interrupt")
    slug14, nid14 = mkorg("closeinterrupt")
    stopped: dict = {}

    def _pause_when_open():
        # the JOURNAL, not the live rows: `_sweep_live` retires a live tool
        # row the moment its durable twin is on disk, and the twin is written
        # at ACTIVE — so a poll of `live` can miss the whole open window
        for _ in range(200):
            if tool_rows(slug14)[0]:
                stopped["at"] = supervisor.interrupt_turn(slug14, nid14)
                return
            time.sleep(0.05)
        stopped["at"] = {"interrupted": False, "reason": "never saw the tool"}

    waiter = threading.Thread(target=_pause_when_open, daemon=True)
    waiter.start()
    run_turn(slug14, nid14, "hold a tool until paused")
    waiter.join(10)
    uses_i, res_i = tool_rows(slug14)
    check("anti-vacuity: the ⏸ really fired while the tool was open",
          lambda: eq(stopped.get("at"), {"interrupted": True},
                     f"interrupt_turn returned {stopped.get('at')}"))
    check("the interrupted turn's open tool is closed too, once",
          lambda: eq((len(uses_i), sorted(len(v) for v in res_i.values()),
                      sum(1 for v in res_i.values() for r in v
                          if UNAVAILABLE in r["body"])), (1, [1], 1),
                     f"uses {uses_i} results {res_i}"))

    # ── a repeated DONE must not produce a second row either ───────────────
    scenario("dupdone", "agy-close-dup")
    slug15, nid15 = mkorg("closedup")
    run_turn(slug15, nid15, "say it once")
    uses_d, res_d = tool_rows(slug15)
    check("a repeated DONE still yields one result per tool, and the sweep "
          "adds nothing on a turn that closed everything itself",
          lambda: eq((len(uses_d), sorted(len(v) for v in res_d.values()),
                      sum(1 for v in res_d.values() for r in v
                          if UNAVAILABLE in r["body"])), (2, [1, 1], 0),
                     f"uses {uses_d} results {res_d}"))

    # ── the exit paths that SKIP everything after the try/finally ──────────
    # `wait()` and `close()` can both raise (the D-229 note names close), and
    # a call placed after the try/finally would never run then. These two
    # cases are why the sweep is an inner `finally` rather than a statement
    # further down.
    def closes_despite(what: str, patch_name: str, label: str):
        scenario("interruptmidtool", f"agy-close-{what}")
        slug_, nid_ = mkorg(f"close{what}")
        original = getattr(antigravityrun.AntigravityTurn, patch_name)
        fired: dict = {}

        def boom(self, *a, **kw):
            if patch_name == "wait":
                for _ in range(200):        # let the tool actually open
                    if tool_rows(slug_)[0]:
                        break
                    time.sleep(0.05)
            else:
                original(self, *a, **kw)
            fired["raised"] = True
            raise RuntimeError(f"planted {patch_name} failure")

        setattr(antigravityrun.AntigravityTurn, patch_name, boom)
        try:
            run_turn(slug_, nid_, f"raise from {patch_name}")
        except Exception:                                    # noqa: BLE001
            pass
        finally:
            setattr(antigravityrun.AntigravityTurn, patch_name, original)
        uses_, res_ = tool_rows(slug_)
        check(f"anti-vacuity: the planted {patch_name}() failure really fired "
              f"with a tool open ({label})",
              lambda: eq((fired.get("raised"), len(uses_)), (True, 1),
                         f"fired {fired}, tool_use rows {uses_}"))
        check(f"the open tool is closed even when {patch_name}() raises on "
              f"the way out ({label})",
              lambda: eq((sorted(len(v) for v in res_.values()),
                          sum(1 for v in res_.values() for r in v
                              if UNAVAILABLE in r["body"])), ([1], 1),
                         f"uses {uses_} results {res_}"))

    closes_despite("waitraise", "wait", "the turn thread never reaches the "
                                        "code after the try")
    closes_despite("closeraise", "close", "the teardown itself fails")

    # ── the per-message ceiling ────────────────────────────────────────────
    scenario("interruptmidtool", "agy-close-timeout")
    slug17, nid17 = mkorg("closetimeout")
    _orig_timeout = supervisor.TURN_TIMEOUT
    supervisor.TURN_TIMEOUT = 1.0
    try:
        run_turn(slug17, nid17, "hold a tool past the ceiling")
    finally:
        supervisor.TURN_TIMEOUT = _orig_timeout
    uses_t, res_t = tool_rows(slug17)
    check("anti-vacuity: the turn really died on the ceiling, not on its own",
          lambda: truthy("ceiling" in
                         str(supervisor.state(slug17, nid17).get("last_error")),
                         f"last_error "
                         f"{supervisor.state(slug17, nid17).get('last_error')}"))
    check("a tool open when the per-message ceiling kills the turn is closed "
          "too",
          lambda: eq((len(uses_t), sorted(len(v) for v in res_t.values()),
                      sum(1 for v in res_t.values() for r in v
                          if UNAVAILABLE in r["body"])), (1, [1], 1),
                     f"uses {uses_t} results {res_t}"))

    print("§12 the finalization protocol: a callback already ADMITTED when "
          "the turn ends finishes first; one arriving after is refused")
    # Astra's seam on 517d1ae (2026-09-05): the claim covered DONE-vs-sweep
    # only. An ACTIVE held after its checks but before registration let the
    # sweep see nothing open and the row dangled; one held inside its journal
    # write let the sweep answer a call not yet written (tool_result before
    # tool_use). Neither `close()` (kill only) nor `wait()` (bounded join)
    # establishes quiescence, so the sweep now finalizes and drains.
    UID = "agy-seamtoken-3"
    UNAV = "[orgtree: result unavailable] The turn"

    # positive control: the protocol changes nothing for a normal step
    rows0: list = []
    env0 = protocol_env(GateLock(), rows0)
    env0["_on_event"](step_msg("ACTIVE"))
    env0["_on_event"](step_msg("DONE", output="the tool DID report this"))
    check("positive control: ACTIVE then DONE through the door still journals "
          "tool_use then the CLI's real result, inflight back to zero",
          lambda: eq((rows_shape(rows0), env0["jstate"]["inflight"],
                      env0["jstate"]["finalized"]),
                     ([("tool_use", UID),
                       ("tool_result", "the tool DID report this")],
                      0, False), f"rows {rows0}"))

    held_flush = held_callback_seam("flush", step_msg("ACTIVE"))
    check("anti-vacuity: the ACTIVE was held after its checks, before "
          "registration, and neither thread hung",
          lambda: eq((held_flush["reached"], held_flush["stuck"]),
                     (True, False), f"{held_flush}"))
    check("the sweep WAITED for the admitted ACTIVE (still running, nothing "
          "written, while the callback was held)",
          lambda: eq((held_flush["sweep_waited"],
                      held_flush["rows_while_held"]), (True, []),
                     f"{held_flush}"))
    check("…then closed it: tool_use before its result, nothing left open, "
          "and the drain did not time out",
          lambda: eq((held_flush["rows"], held_flush["open_after"],
                      held_flush["drain_timed_out"]),
                     ([("tool_use", UID), ("tool_result", UNAV)], {}, []),
                     f"{held_flush}"))

    held_journal = held_callback_seam("journal", step_msg("ACTIVE"))
    check("an ACTIVE held INSIDE its journal write (registered, row not yet "
          "landed): the sweep waited and the call precedes its close",
          lambda: eq((held_journal["reached"], held_journal["sweep_waited"],
                      held_journal["rows"], held_journal["open_after"],
                      held_journal["drain_timed_out"]),
                     (True, True, [("tool_use", UID), ("tool_result", UNAV)],
                      {}, []), f"{held_journal}"))

    held_done = held_callback_seam(
        "committed", step_msg("DONE", output="the tool DID report this"),
        tool_open={UID: "run_command"})
    check("a DONE held before its claim: the sweep waited, the CLI's real "
          "result is the one on disk, and there is exactly one row",
          lambda: eq((held_done["reached"], held_done["sweep_waited"],
                      held_done["rows"], held_done["open_after"]),
                     (True, True, [("tool_result", "the tool DID report this")],
                      {}), f"{held_done}"))

    # after finalization the door is shut
    rows_late: list = []
    env_late = protocol_env(GateLock(), rows_late,
                            tool_open={UID: "run_command"})
    env_late["_commit_unfinished_tools"]()
    closed_first = rows_shape(rows_late)
    # a step the turn NEVER saw before finalization: the `_committed` guard
    # cannot know it, so only the door stands between it and a tool_use row
    # nothing would ever close (the `no_finalize_gate` mutant reopens this)
    env_late["_on_event"](step_msg("ACTIVE", index=4))
    env_late["_on_event"](step_msg("DONE", output="too late", index=4))
    env_late["_on_event"](step_msg("ACTIVE"))
    env_late["_on_event"](step_msg("DONE", output="too late"))
    check("anti-vacuity: the sweep closed the open step before the late "
          "events arrived",
          lambda: eq(closed_first, [("tool_result", UNAV)], f"{closed_first}"))
    check("an ACTIVE or DONE arriving AFTER finalization is refused at the "
          "door: nothing registered, nothing journaled, inflight zero",
          lambda: eq((rows_shape(rows_late), env_late["jstate"]["tool_open"],
                      env_late["jstate"]["inflight"]),
                     (closed_first, {}, 0), f"{rows_late}"))

    print("§13 when the drain EXPIRES the sweep proceeds, says so, and the "
          "atomic claim is what keeps the row count at one")
    # The bounded wait is a promise not to hang; this is what happens past
    # it. A DONE held at its claim for longer than the drain: the sweep goes
    # ahead, closes the step and records that it may have run early; the
    # late DONE then finds the step claimed and writes nothing. (The old
    # split check/mark/pop would write a second, contradicting row here.)
    rows_x: list = []
    gate_x = GateLock("wire", 3)          # door, _committed, then the claim
    env_x = protocol_env(gate_x, rows_x, tool_open={UID: "run_command"})
    wire_x = threading.Thread(
        target=lambda: env_x["_on_event"](step_msg("DONE", output="late")),
        name="wire", daemon=True)
    sweep_x = threading.Thread(target=env_x["_commit_unfinished_tools"],
                               name="sweep", daemon=True)
    wire_x.start()
    reached_x = gate_x.reached.wait(5)
    t_sweep = time.monotonic()
    sweep_x.start()
    sweep_x.join(9)
    sweep_took = time.monotonic() - t_sweep
    rows_at_expiry = rows_shape(rows_x)
    gate_x.go.set()
    wire_x.join(10)
    check("anti-vacuity: the DONE was held at its claim past the drain, the "
          "sweep finished on its own after ~5s and recorded the expiry",
          lambda: eq((reached_x, not sweep_x.is_alive(), 4.5 < sweep_took < 9,
                      len(env_x["_logged"])), (True, True, True, 1),
                     f"took {sweep_took:.1f}s logged {env_x['_logged']}"))
    check("the sweep closed the step at expiry; the late DONE then wrote "
          "NOTHING — one row, not a contradicting pair",
          lambda: eq((rows_at_expiry, rows_shape(rows_x), wire_x.is_alive()),
                     ([("tool_result", UNAV)], [("tool_result", UNAV)], False),
                     f"{rows_x}"))
    # and the sweep honours a claim it LOST between snapshot and claim
    rows_y: list = []
    gate_y = GateLock("sweep", 2)         # finalize+snapshot, then the claim
    env_y = protocol_env(gate_y, rows_y, tool_open={UID: "run_command"})
    sweep_y = threading.Thread(target=env_y["_commit_unfinished_tools"],
                               name="sweep", daemon=True)
    sweep_y.start()
    reached_y = gate_y.reached.wait(5)
    taken = env_y["_claim_tool"](UID)     # someone else won it meanwhile
    gate_y.go.set()
    sweep_y.join(10)
    check("a step claimed away between the sweep's snapshot and its own "
          "claim is left alone — the sweep writes for what it WON, only",
          lambda: eq((reached_y, taken, rows_shape(rows_y)),
                     (True, "run_command", []), f"{rows_y}"))

    print("§14 the whole leg, with the wire's callback really held while the "
          "process dies: the close still answers the call")
    # `draftthentool`: a text delta with no DONE sits in the draft buffer, so
    # the tool's ACTIVE path flushes it — and THAT emission is where the
    # reader is held, on the real code path, before the tool is registered.
    # The process is already gone when we release; the sweep must have waited.
    scenario("draftthentool", "agy-close-heldleg")
    slug18, nid18 = mkorg("closeheldleg")
    held_leg: dict = {"paused_on": None}
    go = threading.Event()
    _orig_stream = supervisor.stream

    def holding_stream(slug_, nid_, payload):
        if (slug_ == slug18 and payload.get("kind") == "delta"
                and held_leg["paused_on"] is None):
            held_leg["paused_on"] = threading.current_thread().name
            go.wait(8.0)
        _orig_stream(slug_, nid_, payload)

    def releaser():
        for _ in range(200):
            # release only once the turn thread has torn down (responding
            # cleared in the finally, before the sweep) and the process is
            # gone: the sweep is now either waiting on the drain or has
            # already run without it
            if (held_leg["paused_on"] and TURNS
                    and TURNS[-1].poll() is not None
                    and not supervisor.state(slug18, nid18).get("responding")):
                time.sleep(0.3)
                held_leg["released_after_exit"] = True
                go.set()
                return
            time.sleep(0.05)
        held_leg["released_after_exit"] = False
        go.set()

    supervisor.stream = holding_stream
    TURNS.clear()
    rel = threading.Thread(target=releaser, daemon=True)
    rel.start()
    try:
        run_turn(slug18, nid18, "hold the wire while you die")
    finally:
        supervisor.stream = _orig_stream
        go.set()
    rel.join(5)
    uses_h, res_h = tool_rows(slug18)
    order_h = [(k, v) for k, _, v in shape(journal_lines(slug18))
               if k in ("text", "tool_use", "tool_result")]
    check("anti-vacuity: the reader really was held on the draft flush "
          "before registering the tool, and released only after the "
          "process had exited and the turn had torn down",
          lambda: eq((bool(held_leg["paused_on"]),
                      held_leg.get("released_after_exit")), (True, True),
                     f"{held_leg}"))
    check("the tool opened by the held callback is registered, journaled AND "
          "closed — one call, one generated result, in that order",
          lambda: eq((len(uses_h), [(k, v[:18]) for k, v in order_h
                                    if k != "text"]),
                     (1, [("tool_use", "run_command"),
                          ("tool_result", "[orgtree: result u")]),
                     f"uses {uses_h} order {order_h}"))
    check("the unfinished text block is still the last assistant block",
          lambda: eq(order_h[-1][0] if order_h else None, "text",
                     f"{order_h}"))

    print()
    for label, tb in FAIL:
        print(f"--- FAILED: {label}\n{tb}")
    print(f"{PASS} checks passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
