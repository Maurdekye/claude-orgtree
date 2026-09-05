"""FR-17: native Codex `turn/plan/updated` checklists, end to end.

    python backend/tests/test_codex_plan_events.py   (no pytest; plain asserts)

Assignment 17: consume Codex's own structured checklist notification and show
it in the existing progress panel, durably. This is the PROTOCOL-TO-STORE
half of the acceptance list (codex-todo-feasibility.md); frontend/tests/
progress.test.tsx §1e/§4b holds the STORE-TO-RENDER half on real payload
shapes. Both run through the real `supervisor._codex_leg`/`_codex_leg_attempt`
and `read_chat` — nothing here reads `_apply_plan` or `_codex_plan_steps`
directly, on the same principle test_codex_stream_order.py states: the
instrument is the journal on disk and `stream()`'s payloads, not an internal
function invoked in isolation.

§1 successive snapshots: only the LAST one is what read_chat renders
§2 all three statuses render, mapped from codex's own camelCase
§3 explicit clearing (`plan: []`) is a CLEARED list, not an absence — and a
   turn that never sent one at all leaves nothing to read
§4 wrong-thread and wrong-turn notifications are discarded — the legitimate
   snapshot survives them, is not overwritten by whichever arrived last
§5 THE RACE: a notification arriving before turn/start's own reply (this
   turn's id not yet resolved) is buffered and still applied, not dropped
§6 a failed / interrupted turn does not mark its last unfinished step done
§7 reconnect/restart: read_chat reconstructs the same checklist from the
   journal FILE alone, with no live/in-memory state at all
§8 the live wire carries the checklist AS the turn runs, not one poll behind
§9 anti-vacuity: the instrument can see a violation planted on purpose

ANTI-VACUITY, per behavioural claim (verified red while writing — see the
commit message for the mutations run):
  · §1/§2 mapping: reading the FIRST notification instead of the LAST, or
    leaving `inProgress` unmapped, → red
  · §4 validation: dropping the threadId/turnId check in `_apply_plan` → red
    (the wrong-thread/wrong-turn step's text would appear instead)
  · §5 buffering: `_on_plan` rejecting instead of buffering when turn_id is
    unresolved → red (the early scenario's checklist never appears at all)
  · §6 no-auto-complete: any code path deriving plan state from turn status
    → red (the checklist would read "completed" instead of "in_progress")
  · §7 restart: read_chat depending on any in-memory `state()` field for the
    checklist itself (not just busy/live) → red once that field is cleared
"""

import glob
import json
import os
import sys
import tempfile
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-codexplan-")
os.environ["ORGTREE_DATA"] = DATA
os.environ["ORGTREE_WARM"] = "0"       # cold spawns only: one process per turn
# a PORT NOBODY SERVES — the codex leg's tool dispatcher POSTs /api/agent, and
# left unset it would default to 7360 and reach the operator's LIVE backend
os.environ["ORGTREE_PORT"] = "9"
with open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')

FAKECODEX = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fakecodex.py")
CODEX_HOME = tempfile.mkdtemp(prefix="codexplan-home-")
os.environ["ORGTREE_CODEX"] = FAKECODEX
os.environ["CODEX_HOME"] = CODEX_HOME
with open(os.path.join(CODEX_HOME, "auth.json"), "w", encoding="utf-8") as _f:
    _f.write('{"tokens": {}}')

from orgtree import providers, store, supervisor                   # noqa: E402
from orgtree.ledger import USER                                    # noqa: E402

providers._status_cache = None

PASS = 0
FAIL: list[tuple[str, str]] = []


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


# ── the instrument ──────────────────────────────────────────────────────────
def journal_records(slug: str) -> list[dict]:
    out: list[dict] = []
    pat = os.path.join(supervisor.journal_store(), "projects", slug, "*.jsonl")
    for path in sorted(glob.glob(pat)):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rec, dict):
                        out.append(rec)
        except OSError:
            pass
    return out


def plan_records(slug: str) -> list[dict]:
    return [r for r in journal_records(slug) if r.get("type") == "codex_plan_updated"]


#: every 'plan' payload `_visible_live_row` actually pushed onto the live
#: wire — `_visible_live_row` calls the module-global `live_row`, so
#: replacing that name here (the same trick test_codex_stream_order.py plays
#: on `stream`) intercepts it without touching production code.
SEEN_LIVE: list[dict] = []
WATCH: dict[str, str] = {"slug": ""}
_orig_live_row = supervisor.live_row


def recording_live_row(slug: str, nid: str, payload: dict) -> None:
    if WATCH["slug"] and slug == WATCH["slug"] and payload.get("kind") == "plan":
        SEEN_LIVE.append(dict(payload))
    _orig_live_row(slug, nid, payload)


supervisor.live_row = recording_live_row


# ── fixtures ────────────────────────────────────────────────────────────────
def mkorg(label: str) -> tuple[str, str]:
    org = store.create_org(f"zz codexplan {label}")
    r = org.hire(USER, None, "luna", 0, "cx", add_dirs=[],
                 tools={"bash": True, "web": False, "edit": True,
                        "subagents": False, "mcp": []},
                 org_visibility="team", charter="a codex plan-events test agent")
    nid = r["node"]
    store.save_org(org)
    return org.d["slug"], nid


def run_turn(slug: str, nid: str, text: str):
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["busy"] = True
    return supervisor._run_one_turn(slug, nid, {"text": text, "view": text})


def scenario(name: str, thread: str) -> None:
    # thread id matters: transcript_path globs across ALL projects, and
    # fakecodex's default thread id is a constant (test_codex_stream_order.py
    # documents the exact collision this avoids)
    os.environ["FAKECODEX_SCENARIO"] = name
    os.environ["FAKECODEX_THREAD_ID"] = thread


def steps_of(rec: dict) -> list[tuple[str, str]]:
    return [(s.get("step"), s.get("status")) for s in (rec.get("plan") or [])]


def main() -> int:
    print("§1/§2 successive snapshots: only the last renders, all three "
          "statuses map from codex's camelCase")
    scenario("plan_updated", "fake-thread-plan-updated")
    slug, nid = mkorg("updated")
    WATCH["slug"] = slug
    run_turn(slug, nid, "make a plan")
    org = store.load_org(slug)      # AFTER the turn: session_id is only durable then

    recs = plan_records(slug)
    check("every notification this scenario sent was journaled (3)",
          lambda: eq(len(recs), 3, "codex_plan_updated records"))
    chat = supervisor.read_chat(org, nid)
    plans = [m for m in chat["messages"] if m.get("codexPlan")]
    check("read_chat surfaces exactly the journaled snapshots, in order",
          lambda: eq(len(plans), 3, "codexPlan messages"))
    last = plans[-1]["codexPlan"] if plans else {}
    check("the LAST snapshot is what a reader ends up with: two steps",
          lambda: eq(steps_of({"plan": last.get("steps")}),
                     [("a", "completed"), ("b", "pending")], "final steps"))
    check("…and its explanation is preserved",
          lambda: eq(last.get("explanation"), "steady progress", "explanation"))
    check("all three raw statuses were actually exercised across the "
          "snapshots (anti-vacuity: a mapper that only ever sees one status "
          "cannot prove the map)",
          lambda: eq(sorted({s for r in recs for _, s in steps_of(r)}),
                     ["completed", "in_progress", "pending"], "statuses seen"))
    check("codex's camelCase never reaches a reader RAW — the wire's "
          "'inProgress' was mapped, not passed through",
          lambda: (_ for _ in ()).throw(AssertionError("inProgress leaked raw"))
          if any(s == "inProgress" for r in recs for _, s in steps_of(r)) else None)

    print("§3 explicit clearing vs never having observed one")
    scenario("plan_cleared", "fake-thread-plan-cleared")
    slug2, nid2 = mkorg("cleared")
    run_turn(slug2, nid2, "make then clear a plan")
    chat2 = supervisor.read_chat(store.load_org(slug2), nid2)
    plans2 = [m["codexPlan"] for m in chat2["messages"] if m.get("codexPlan")]
    check("both the checklist and its clearing were journaled",
          lambda: eq(len(plans2), 2, "codexPlan messages"))
    check("the CLEARED snapshot is an empty list, not a missing field",
          lambda: eq(plans2[-1]["steps"], [], "cleared steps"))
    slug3, nid3 = mkorg("never")
    scenario("tool", "fake-thread-plan-never")
    run_turn(slug3, nid3, "do something else entirely")
    chat3 = supervisor.read_chat(store.load_org(slug3), nid3)
    check("a turn that never sent turn/plan/updated leaves NO codexPlan "
          "record at all — distinct from an explicit empty one",
          lambda: eq([m for m in chat3["messages"] if m.get("codexPlan")], [],
                     "codexPlan messages"))

    print("§4 wrong-thread / wrong-turn notifications are discarded")
    scenario("plan_wrong_ids", "fake-thread-plan-wrongids")
    slug4, nid4 = mkorg("wrongids")
    run_turn(slug4, nid4, "one real plan, two impostors")
    recs4 = plan_records(slug4)
    check("only the ONE legitimate snapshot was ever journaled — the wrong-"
          "thread and wrong-turn ones never landed at all",
          lambda: eq(len(recs4), 1, "codex_plan_updated records"))
    check("the surviving snapshot is the real one, not overwritten by "
          "whichever impostor arrived last",
          lambda: eq(steps_of(recs4[0]), [("real", "pending")], "steps"))
    check("the surviving snapshot carries the REAL thread/turn id, not "
          "either impostor's",
          lambda: (eq(recs4[0]["threadId"], "fake-thread-plan-wrongids", "threadId"),
                   eq(recs4[0]["turnId"], "fake-turn-0001", "turnId")) and None)
    chat4 = supervisor.read_chat(store.load_org(slug4), nid4)
    plans4 = [m["codexPlan"] for m in chat4["messages"] if m.get("codexPlan")]
    check("read_chat agrees: exactly one checklist, the real one — an "
          "impostor never reaches the reader through this path either",
          lambda: (eq(len(plans4), 1, "codexPlan messages"),
                   eq(steps_of({"plan": plans4[0]["steps"]}),
                      [("real", "pending")], "steps")) and None)

    print("§4b a notification with NO identity at all is rejected, not "
          "accepted as trivially current (review finding, 2026-09-05)")
    scenario("plan_missing_ids", "fake-thread-plan-missingids")
    slug4b, nid4b = mkorg("missingids")
    run_turn(slug4b, nid4b, "one real plan, one with no identity")
    recs4b = plan_records(slug4b)
    check("the identity-less notification never landed at all",
          lambda: eq(len(recs4b), 1, "codex_plan_updated records"))
    check("the surviving snapshot is the real one",
          lambda: eq(steps_of(recs4b[0]), [("real", "pending")], "steps"))

    print("§4c a malformed `plan: null` is rejected, NOT read as the "
          "model's own explicit `[]` clear (review finding, 2026-09-05)")
    scenario("plan_null_plan", "fake-thread-plan-nullplan")
    slug4c, nid4c = mkorg("nullplan")
    run_turn(slug4c, nid4c, "one real plan, one malformed")
    recs4c = plan_records(slug4c)
    check("the malformed notification never landed at all — a real "
          "checklist must survive it untouched, not be replaced by an "
          "empty one",
          lambda: eq(len(recs4c), 1, "codex_plan_updated records"))
    check("the surviving snapshot still has its real step — not cleared",
          lambda: eq(steps_of(recs4c[0]), [("real", "pending")], "steps"))

    print("§5 THE RACE: a notification before turn/start's own reply is "
          "buffered, not dropped")
    scenario("plan_early", "fake-thread-plan-early")
    slug5, nid5 = mkorg("early")
    run_turn(slug5, nid5, "does the checklist survive the race?")
    recs5 = plan_records(slug5)
    check("the fixture actually raced and produced a result at all "
          "(anti-vacuity: prove something happened before trusting its "
          "content)",
          lambda: eq(len(recs5) > 0, True, "any codex_plan_updated record"))
    check("the early snapshot was journaled exactly once — buffered and "
          "applied, not dropped for lacking a turn id at the instant it "
          "arrived",
          lambda: eq(len(recs5), 1, "codex_plan_updated records"))
    check("…with its real step content",
          lambda: eq(steps_of(recs5[0]), [("early", "pending")], "steps"))
    check("…and the RESOLVED turn id, not something invented for the gap",
          lambda: eq(recs5[0]["turnId"], "fake-turn-0001",
                     "the buffered event's turnId, once resolved"))

    print("§6 a failed/interrupted turn does not mark its last step done")
    for scen, label in (("plan_failed_turn", "failed"),
                        ("plan_interrupted_turn", "interrupted")):
        scenario(scen, f"fake-thread-plan-{label}")
        s, n = mkorg(label)
        run_turn(s, n, "start something you will not finish")
        recs6 = plan_records(s)
        check(f"({label}) the unfinished checklist was journaled before the "
              "turn ended badly",
              lambda recs6=recs6: (_ for _ in ()).throw(
                  AssertionError("no plan record at all")) if not recs6 else None)
        check(f"({label}) the step is STILL in_progress — nothing about a "
              "bad ending marked it done",
              lambda recs6=recs6: eq(steps_of(recs6[-1]),
                                     [("not done yet", "in_progress")], "steps"))

    print("§7 reconnect/restart: read_chat rebuilds the SAME checklist from "
          "the journal file alone")
    # simulate a fresh process: the in-memory state() cache for this node is
    # gone, exactly as it would be after a real restart (state() itself
    # documents this — "In memory, so it restarts at 0")
    key = (slug, nid)
    saved = supervisor._state.pop(key, None)
    try:
        chat_restarted = supervisor.read_chat(org, nid)
        restarted_plans = [m["codexPlan"] for m in chat_restarted["messages"]
                           if m.get("codexPlan")]
        check("the FULL sequence of snapshots survives a simulated restart, "
              "unchanged",
              lambda: eq(len(restarted_plans), 3, "codexPlan messages"))
        check("the last one is still the last one",
              lambda: eq(steps_of({"plan": restarted_plans[-1]["steps"]}),
                         [("a", "completed"), ("b", "pending")], "final steps"))
        check("codex_turn_id reads back null after the simulated restart — "
              "no live turn is running, so there is no current identity",
              lambda: eq(chat_restarted.get("codex_turn_id"), None,
                         "codex_turn_id"))
    finally:
        if saved is not None:
            supervisor._state[key] = saved

    print("§8 the live wire carries the checklist as the turn runs")
    check("every plan_updated snapshot from §1 reached the live wire too, "
          "not just the journal (anti-vacuity: the recorder itself must see "
          "something, or this whole section is vacuous)",
          lambda: (_ for _ in ()).throw(AssertionError("no live 'plan' rows seen at all"))
          if not SEEN_LIVE else None)
    check("the live rows' step content matches the journal, not an older "
          "runner's stale-then-corrected shape",
          lambda: eq([r.get("plan") for r in SEEN_LIVE if r.get("turnId") == "fake-turn-0001"
                     and r.get("threadId") == "fake-thread-plan-updated"],
                     [rec.get("plan") for rec in recs], "live vs journal plan payloads"))

    print("§8b a long step's text is preserved WHOLE — no invented, "
          "unmeasured cap (review finding, 2026-09-05)")
    scenario("plan_long_step", "fake-thread-plan-longstep")
    slug8b, nid8b = mkorg("longstep")
    run_turn(slug8b, nid8b, "one step, very long")
    recs8b = plan_records(slug8b)
    check("the long snapshot was journaled",
          lambda: eq(len(recs8b) > 0, True, "any codex_plan_updated record"))
    step8b = (recs8b[0].get("plan") or [{}])[0]
    check("its full 2500-character text survives untouched",
          lambda: eq(step8b.get("step"), "x" * 2500, "step text"))
    check("no truncation marker is invented for it either",
          lambda: eq("truncated" in step8b, False, "no truncated key"))

    print("§8c a `plan` array that is a list but has ONE malformed element "
          "is rejected WHOLE, not degraded to its valid elements (review "
          "finding, 2026-09-05)")
    scenario("plan_mixed_shape", "fake-thread-plan-mixedshape")
    slug8c, nid8c = mkorg("mixedshape")
    run_turn(slug8c, nid8c, "one real plan, two mixed-shape impostors")
    recs8c = plan_records(slug8c)
    check("both malformed notifications ([null] alongside a real step, and "
          "a step object missing its own `step` key) were rejected whole — "
          "only the ONE fully-valid snapshot ever landed",
          lambda: eq(len(recs8c), 1, "codex_plan_updated records"))
    check("the surviving snapshot is the original real one, not the "
          "'still valid' entry that arrived alongside the null",
          lambda: eq(steps_of(recs8c[0]), [("real", "pending")], "steps"))

    print("§9 anti-vacuity: duplicate identical snapshots do not bloat the "
          "journal")
    scenario("plan_updated", "fake-thread-plan-dup-check")
    # plan_updated's own three snapshots are already pairwise distinct
    # (§1 proved exactly 3 journaled for 3 sent) — this restates it as its
    # own explicit dedupe claim so a regression here fails ON THIS LABEL
    check("3 distinct snapshots in, 3 journaled out — no silent drop AND no "
          "silent duplication for genuinely different content",
          lambda: eq(len(recs), 3, "codex_plan_updated records"))

    print("§10 a live 'plan' row survives until its durable twin lands — it "
          "is not retired on sight the way an unrecognized live-row kind "
          "would be")
    # `_sweep_live`'s generic fallback for a kind it does not special-case is
    # `return True` — instant retirement, whether or not a durable twin
    # exists. FR-17's `_apply_plan` writes the durable record and the live
    # row together, so this can only be observed by inserting a live row
    # WITHOUT its twin directly (state() is same-process here) and reading
    # chat before the twin exists.
    row_at = supervisor.now_iso()
    st = supervisor.state(slug, nid)
    with supervisor._state_lock:
        st["live"] = [{"kind": "plan", "text": "checklist updated", "at": row_at,
                       "threadId": "fake-thread-plan-updated",
                       "turnId": "fake-turn-0001", "explanation": None,
                       "plan": [{"step": "brand new, no twin yet", "status": "pending"}]}]
    chat10 = supervisor.read_chat(store.load_org(slug), nid)
    check("with NO durable twin at all yet, the live row is KEPT, not swept",
          lambda: eq([r for r in chat10["live"] if r.get("kind") == "plan"] != [],
                     True, "live 'plan' row present"))
    # now its durable twin actually lands (the real path: _apply_plan writes
    # both together; simulated here as the direct journal append it is) —
    # stamped safely past the chronology backstop's 2 s jitter window so the
    # ORDER evidence (not a content match a plan snapshot has no id for) is
    # unambiguous
    sid = str(store.load_org(slug).node(nid)["session_id"])
    supervisor._codex_journal(slug, sid, [{
        "type": "codex_plan_updated",
        "timestamp": supervisor._iso_ts(supervisor.time.time() + 5),
        "threadId": "fake-thread-plan-updated", "turnId": "fake-turn-0001",
        "explanation": None,
        "plan": [{"step": "brand new, no twin yet", "status": "pending"}]}])
    chat10b = supervisor.read_chat(store.load_org(slug), nid)
    check("once the durable twin exists (and is NEWER), the live row is "
          "swept — this is the chronology backstop, not a content match, "
          "which a plan snapshot has no stable id for",
          lambda: eq([r for r in chat10b["live"] if r.get("kind") == "plan"], [],
                     "live 'plan' rows remaining"))

    print(f"\n{PASS} checks passed" + (f", {len(FAIL)} FAILED" if FAIL else ""))
    if FAIL:
        print()
        for label, tb in FAIL:
            print(f"--- {label} ---\n{tb}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
