"""FR-1 · the org chart carries each agent's STATUS, and how old it is.

WHY THIS EXISTS. On 2026-09-04 17:07Z a coordinator was asked to retire every
agent with no remaining role. `orgtree_chart` returned names, tiers and
hierarchy and no status, so finished, mid-flight and blocked were
indistinguishable; asking nine agents would have cost nine turns, and the
workaround was reading the tail of each agent's breadcrumbs.md off disk.

⚠ THE AGE IS THE FEATURE, NOT DECORATION, AND THAT IS WHAT THIS SUITE GUARDS.
A status is SELF-REPORTED — written only when an agent chooses to call
`orgtree_status`. An agent that said "working" and then died reads "working"
for ever. So a chart row that prints the word without its age is not a smaller
version of this feature, it is a CONFIDENT LIE told to the one reader who has
no other view of the org. Every check below that asserts a status is present
also asserts its age is present, and §2 exists solely to prove that a stale
status renders DIFFERENTLY from a fresh one.

    §1  the shapes — one row per state an agent can be in
    §2  stale vs fresh — the property most likely to rot
    §3  objective vs self-reported — busy, and the orphaned inflight marker
    §4  the chart survives it — old anchors, line structure, hostile summaries
    §5  positive controls — every check above is proven able to FAIL

Hermetic: in-memory orgs, no data root of consequence, no port, no CLI, no
network. Time is injected (`_render_chart(..., now=)`), never slept for.

    python backend/tests/test_chart_status.py [-v]
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-chartstatus-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
# ⚠ same hub trap as test_present §1: a throwaway ORGTREE_DATA does NOT isolate
# the mail hub — net._default_address falls back to the operator's real one
# when this root has no defaults.json. Point it at the discard port.
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
os.environ["USERPROFILE"] = os.environ["HOME"] = _TMP

from orgtree import supervisor as S                              # noqa: E402
from orgtree.ledger import Org, USER                             # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []

NOW = 1_800_000_000.0            # fixed clock; nothing here reads the wall
MIN = 60.0
HOUR = 3600.0


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def _true(cond, msg) -> None:
    if not cond:
        raise AssertionError(msg)


def iso(ago_s: float) -> str:
    """A stamp `ago_s` seconds before NOW, in the format ledger.now() writes.

    ⚠ ANCHORED ON THE REAL PRODUCER. `ledger.now()` emits a millisecond UTC
    stamp ending in "Z" while `restart_wake.now_iso()` emits "+00:00"; both
    reach these fields, and a parser that handled only one would put every age
    out by the machine's UTC offset while still rendering a plausible number.
    §5 pins both spellings.
    """
    import datetime as dt
    d = dt.datetime.fromtimestamp(NOW - ago_s, dt.timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"


_n = [0]


def rig(**states):
    """An org whose reports are named for the state they are in.

    Each kwarg is `name=(status_dict_or_None, kind)` where kind is "last",
    "prev", "inflight" or "none". Returns (org, row_fn).
    """
    _n[0] += 1
    o = Org.create(f"cs{_n[0]}", dirs=["E:/w"])
    o.hire(USER, None, "opus", 60, "boss")
    # ⚠ `hire` NORMALISES the name it is given (an underscore does not survive)
    # and returns {"node": nid, …}, not the id. Keep the id it actually minted,
    # or every lookup below aims at a node that does not exist.
    ids: dict[str, str] = {}
    for name, (rec, kind) in states.items():
        nid = str(o.hire(USER, "boss", "haiku", 1, name)["node"])
        ids[name] = nid
        n = o.node(nid)
        # ⚠ `hire` SEEDS last_status = idle "hired — awaiting work" (ledger,
        # user ruling 2026-08-02). Clear it or every fixture below silently
        # tests the preset instead of the state it is named for.
        n.pop("last_status", None)
        if kind == "last":
            n["last_status"] = rec
        elif kind == "prev":
            n["prev_status"] = rec
        elif kind == "inflight":
            n["inflight"] = rec

    def row(name: str) -> str:
        nid = ids[name]
        lines = S._render_chart(o, ["boss"], "", 0, True, None, NOW)
        hits = [ln for ln in lines if f"- {nid} [" in ln]
        _true(len(hits) == 1,
              f"expected exactly one row for {nid}, got {hits}")
        return hits[0]

    o.nid_of = ids                                # type: ignore[attr-defined]
    return o, row


def nd(o, name):
    """The node dict behind a rig kwarg, via the id `hire` actually minted."""
    return o.node(o.nid_of[name])                # type: ignore[attr-defined]


def sid(o, name):
    return o.nid_of[name]                        # type: ignore[attr-defined]


def st(status, summary, ago):
    return {"status": status, "summary": summary, "at": iso(ago)}


# ───────────────────────────────────────────────────────── §1 the shapes
def sec_shapes() -> None:
    print("\n§1  the shapes — one row per state an agent can be in")

    o, row = rig(
        wk=(st("working", "tracing the dup path", 12 * MIN), "last"),
        bl=(st("blocked", "needs a ruling", 40 * MIN), "last"),
        idl=(st("idle", "archived 12 lessons", 2 * HOUR), "last"),
        nothing=(None, "none"),
    )

    check("a reported status prints its WORD, its SUMMARY and its AGE",
          lambda: _true(
              all(x in row("wk") for x in ("working", "tracing the dup path",
                                           "10m")),
              row("wk")))
    check("blocked is not collapsed into idle — a coordinator acts on it",
          lambda: _true("blocked" in row("bl") and "needs a ruling"
                        in row("bl"), row("bl")))
    check("a stored 'done' (recorded as idle) still carries what it did",
          lambda: _true("idle" in row("idl") and "archived 12 lessons"
                        in row("idl") and "2h" in row("idl"), row("idl")))
    check("an agent that has never reported says so, and does not go blank",
          lambda: _true("no status reported" in row("nothing"), row("nothing")))

    # ⚠ THE SILENT-AGENT CASE THE OLD CHART GOT WRONG. `hire` seeds a status
    # and the first turn pops it into prev_status, so an agent that has NEVER
    # called orgtree_status ends up wearing the word "idle" — which, printed
    # bare, states the opposite of the truth about one grinding through a task.
    o2, row2 = rig(quiet=(st("working", "mid-way through the audit",
                             3 * HOUR), "prev"))
    check("a status that pre-dates the agent's last turn is LABELLED as such",
          lambda: _true("BEFORE its last turn" in row2("quiet")
                        and "nothing reported since" in row2("quiet"),
                        row2("quiet")))

    o3, row3 = rig(seeded=(None, "none"))
    nd(o3, "seeded")["prev_status"] = {
        "status": "idle", "summary": "hired — awaiting work", "at": iso(4 * HOUR)}
    check("the HIRE PRESET, once stale, cannot read as a genuine 'idle'",
          lambda: _true("BEFORE its last turn" in row3("seeded")
                        and "4h" in row3("seeded"), row3("seeded")))


# ─────────────────────────────────────────────── §2 stale vs fresh
def sec_stale() -> None:
    print("\n§2  stale vs fresh — the property most likely to rot")

    # ⚠ THE CENTRAL PROPERTY OF THIS FEATURE. Two agents, identical in every
    # respect except WHEN they last spoke, must not render the same row. If
    # this pair ever renders equal, the chart has gone back to vouching for
    # a status it cannot vouch for, and the 17:07Z incident is live again.
    o, row = rig(
        fresh=(st("working", "same words", 2 * MIN), "last"),
        stale=(st("working", "same words", 5 * HOUR), "last"),
    )

    def _differ():
        f = row("fresh").split("· ", 1)[1]
        s = row("stale").split("· ", 1)[1]
        _true(f != s, f"a 2-minute and a 5-hour 'working' render IDENTICALLY: "
                      f"{f!r}")

    check("a fresh and a stale 'working' DO NOT render the same", _differ)
    check("the stale one is visually marked; the fresh one is not",
          lambda: _true("⚠" in row("stale") and "⚠" not in row("fresh"),
                        f"stale={row('stale')!r} fresh={row('fresh')!r}"))
    check("the stale mark says WHY it is suspect, not just that it is",
          lambda: _true("no report since" in row("stale"), row("stale")))

    # The threshold is the checkup clock's own constant, not a second copy of
    # the number. Pin the BOUNDARY from both sides so a silent redefinition
    # (or a stray unit change from seconds to minutes) fails here.
    thr = S.WORKING_CHECKUP_AFTER_S
    o2, row2 = rig(under=(st("working", "w", thr - 60), "last"),
                   over=(st("working", "w", thr + 60), "last"))
    check("the staleness threshold IS WORKING_CHECKUP_AFTER_S, both sides",
          lambda: _true("⚠" not in row2("under") and "⚠" in row2("over"),
                        f"under={row2('under')!r} over={row2('over')!r}"))

    # ⚠ 'working' is the ONLY status that asserts something about the PRESENT.
    # idle/blocked describe a settled fact that stays true while nothing
    # happens; only 'working' is silently converted into a lie by a death.
    o3, row3 = rig(old_idle=(st("idle", "finished", 9 * HOUR), "last"),
                   old_blocked=(st("blocked", "stuck on a ruling", 9 * HOUR),
                                "last"))
    check("an old 'idle'/'blocked' still shows its age but is not alarmed",
          lambda: _true("9h" in row3("old_idle") and "9h" in row3("old_blocked")
                        and "⚠" not in row3("old_idle")
                        and "⚠" not in row3("old_blocked"),
                        f"{row3('old_idle')!r} {row3('old_blocked')!r}"))

    check("NO branch renders a status word without an age beside it",
          _no_bare_status)


_AGE_TOKENS = ("<10m", "m)", "h)", "d)", "ago", "in the future?",
               "age unknown", "no status reported", "mid-turn")


def _no_bare_status() -> None:
    """Sweep every state a row can be in and require an age in each.

    This is the check that would have caught "bolt the word on and ship it".
    It is written as a SWEEP rather than five assertions so that a new status
    branch added later is covered the day it is written, not the day someone
    remembers to extend this file.
    """
    cases = {
        "a": (st("working", "s", 1 * MIN), "last"),
        "b": (st("working", "s", 9 * HOUR), "last"),
        "c": (st("idle", "s", 3 * HOUR), "last"),
        "d": (st("blocked", "s", 3 * HOUR), "last"),
        "e": (st("working", "s", 3 * HOUR), "prev"),
        "f": (None, "none"),
        "g": ({"at": iso(20 * MIN), "text": "x"}, "inflight"),
        "h": ({"status": "working", "summary": "s",
               "at": "not-a-timestamp"}, "last"),                 # unparseable
    }
    o, row = rig(**cases)
    for name in cases:
        line = row(name)
        note = line.split("· ", 1)[1] if "· " in line else ""
        _true(note, f"row for {name} carries no status note at all: {line!r}")
        _true(any(t in note for t in _AGE_TOKENS),
              f"row for {name} states a status with NO age: {note!r}")


# ──────────────────────────────────── §3 objective vs self-reported
def sec_objective() -> None:
    print("\n§3  objective vs self-reported — busy, and the orphaned marker")

    # `busy` is the one fact here the agent does not author. It comes from
    # supervisor.state(), is set in the drive path, and resets to False on a
    # backend restart — which is the CORRECT answer after one.
    o, row = rig(runner=(st("working", "old words", 6 * HOUR), "last"))
    nd(o, "runner")["inflight"] = {"at": iso(4 * MIN), "text": "x"}
    S.state(o.d["slug"], sid(o, "runner"))["busy"] = True

    check("an agent mid-turn is reported as mid-turn, with how long",
          lambda: _true("mid-turn" in row("runner"), row("runner")))
    check("mid-turn OVERRIDES a stale self-report — evidence beats a claim",
          lambda: _true("old words" not in row("runner"), row("runner")))

    # ⚠ THE ORPHAN. `inflight` is written at turn start and popped in exactly
    # ONE place — the turn's `finally`. There is NO boot-time reconciliation,
    # so a backend killed mid-turn leaves the marker in the doc for ever.
    # Reading `inflight` alone as "mid-turn" would therefore reproduce the very
    # defect this feature exists to remove: a permanent, confident, wrong
    # "it's running". The pair (busy=False, inflight present) must read as a
    # DEATH, and this is the check that holds that line.
    o2, row2 = rig(dead=({"at": iso(3 * HOUR), "text": "x"}, "inflight"))
    S.state(o2.d["slug"], sid(o2, "dead"))["busy"] = False

    check("busy=False + a left-behind inflight marker is NOT called mid-turn",
          lambda: _true("▶ mid-turn" not in row2("dead"), row2("dead")))
    check("...it is called out as a turn that never finished, with its age",
          lambda: _true("never finished" in row2("dead")
                        and "3h" in row2("dead") and "⚠" in row2("dead"),
                        row2("dead")))

    # The same node, with busy flipped, must flip the row. This is the
    # differential that proves the row is driven by `busy` and not by the
    # marker alone (a reader of `inflight` only would print both identically).
    def _busy_is_load_bearing():
        before = row2("dead")
        S.state(o2.d["slug"], sid(o2, "dead"))["busy"] = True
        after = row2("dead")
        S.state(o2.d["slug"], sid(o2, "dead"))["busy"] = False
        _true("never finished" in before and "▶ mid-turn" in after,
              f"flipping busy did not change the row: {before!r} -> {after!r}")

    check("flipping ONLY the busy bit flips the row (busy is load-bearing)",
          _busy_is_load_bearing)


# ────────────────────────────────── §4 the chart survives it
def sec_chart_survives() -> None:
    print("\n§4  the chart survives it — anchors, line structure, hostility")

    # ⚠ A SUMMARY IS FREE TEXT AND NOTHING STOPS A NEWLINE IN IT. The chart is
    # LINE-STRUCTURED and every agent in the org reads it line by line, so one
    # embedded newline would split a row in two and let an agent FORGE a chart
    # entry for a colleague that does not exist. Flattening is a security
    # property of this feature, not tidiness.
    forged = "real\n  - ghost-agent [opus] · idle \"ready for anything\""
    o, row = rig(liar=(st("working", forged, 1 * MIN), "last"))

    def _no_forgery():
        lines = S._render_chart(o, ["boss"], "", 0, True, None, NOW)
        _true(not any("ghost-agent [" in ln and "- liar [" not in ln
                      for ln in lines),
              f"a summary forged its own chart row: {lines}")
        _true(len([ln for ln in lines if "ghost-agent" in ln]) <= 1, lines)

    check("a newline in a summary CANNOT forge an extra chart row",
          _no_forgery)
    check("the forged text is flattened onto the liar's own row",
          lambda: _true("\n" not in row("liar") and "ghost-agent"
                        in row("liar"), row("liar")))

    o2, row2 = rig(windy=(st("working", "z" * 400, 1 * MIN), "last"))
    check("a runaway summary is clipped, so N rows cannot blow up the chart",
          lambda: _true(len(row2("windy")) < 200,
                        f"row is {len(row2('windy'))} chars"))

    # The row's existing anatomy must survive: name, tier, and the "← you"
    # marker still at END of line, because that is how it reads at a glance
    # and how existing readers find themselves.
    o3 = Org.create("cs-anat", dirs=["E:/w"])
    o3.hire(USER, None, "opus", 10, "boss")
    o3.hire(USER, "boss", "haiku", 1, "kid")
    o3.node("kid")["last_status"] = st("working", "s", 1 * MIN)

    def _anatomy():
        lines = S._render_chart(o3, ["boss"], "kid", 0, True, None, NOW)
        kid = next(ln for ln in lines if "- kid [" in ln)
        _true(kid.startswith("  - kid [haiku]"),
              f"the name/tier prefix moved: {kid!r}")
        _true(kid.endswith("← you"),
              f"the ← you marker is no longer at end of line: {kid!r}")

    check("name, tier and the trailing '← you' marker keep their places",
          _anatomy)

    # An archived agent is not running and never will be. Printing a status on
    # it would be noise on the rehire shortlist at best, and a stale
    # "working (3d)" — about an agent that is definitively not working — at
    # worst. Existing archived annotations must be untouched.
    o4 = Org.create("cs-arch", dirs=["E:/w"])
    o4.hire(USER, None, "opus", 10, "boss")
    o4.hire(USER, "boss", "haiku", 1, "gone")
    o4.node("gone")["last_status"] = st("working", "never finished this",
                                        10 * MIN)
    o4.retire(USER, "gone")

    def _archived_silent():
        lines = S._render_chart(o4, ["boss"], "", 0, True, None, NOW)
        g = next(ln for ln in lines if "- gone [" in ln)
        _true("never finished this" not in g and "·" not in g,
              f"an archived agent still advertises a status: {g!r}")
        _true("archived" in g, f"the archived tag was lost: {g!r}")

    check("an ARCHIVED agent carries no status, and keeps its archived tag",
          _archived_silent)

    # The legend has to survive tidying: without it the reader takes the word
    # as fact rather than as the agent's own claim.
    def _legend():
        blk = S.org_state_block(o3, "kid")
        _true("self-reported" in blk,
              "the chart no longer tells the reader the status is a CLAIM")
        _true("orgtree_status" in blk.split("Credits:")[0],
              "the chart no longer names where the status comes from")

    check("the chart says out loud that the status is self-reported",
          _legend)


# ─────────────────────────────── §5 positive controls
def sec_controls() -> None:
    print("\n§5  positive controls — proving the checks above can FAIL")

    # ⚠ THIS SECTION IS THE POINT. This repo has produced six guards that read
    # correctly and meant nothing, including a test anchored to a string that
    # also appeared in a line merely computing a path — commenting out the real
    # call left it green. So: neuter the real function and require §1-§4's
    # anchors to VANISH. If they survive a neutered `_status_note`, they were
    # never reading it.
    o, row = rig(x=(st("working", "tracing the dup path", 5 * HOUR), "last"))

    def _mutation():
        live = row("x")
        _true("working" in live and "⚠" in live and "5h" in live, live)
        orig = S._status_note
        try:
            S._status_note = lambda *a, **k: ""                   # the mutant
            dead = row("x")
        finally:
            S._status_note = orig
        for token in ("working", "tracing the dup path", "5h", "⚠", "·"):
            _true(token not in dead,
                  f"a NEUTERED _status_note still produced {token!r} in "
                  f"{dead!r} — the checks above are not reading it")
        _true(row("x") == live, "the mutant was not cleanly reverted")

    check("neutering _status_note makes every status anchor DISAPPEAR",
          _mutation)

    # A clock that does not move would make every age check vacuous: all rows
    # would say the same thing and §2's differential would still pass on two
    # rows that happen to differ for another reason. Prove `now` moves the row.
    def _clock_is_wired():
        o2, _ = rig(y=(st("working", "s", 1 * MIN), "last"))

        def at(now):
            return next(ln for ln in S._render_chart(o2, ["boss"], "", 0,
                                                     True, None, now)
                        if "- y [" in ln)
        young, old = at(NOW), at(NOW + 6 * HOUR)
        _true(young != old,
              f"advancing the clock 6h did not change the row: {young!r}")
        _true("⚠" not in young and "⚠" in old, f"{young!r} / {old!r}")

    check("advancing the injected clock alone ages a row (the clock is wired)",
          _clock_is_wired)

    # Both stamp spellings that actually reach these fields must parse. A
    # parser that handled only one would put every age out by the machine's
    # UTC offset and STILL render a plausible-looking number — the exact shape
    # of a check that reads correctly and means nothing.
    def _both_stamp_formats():
        import datetime as dt
        d = dt.datetime.fromtimestamp(NOW - 2 * HOUR, dt.timezone.utc)
        z = d.strftime("%Y-%m-%dT%H:%M:%S.") + f"{d.microsecond // 1000:03d}Z"
        off = d.isoformat()                    # restart_wake.now_iso() spelling
        _true(off.endswith("+00:00"), off)
        for spelling in (z, off):
            o3, row3 = rig(w=(None, "none"))
            nd(o3, "w")["last_status"] = {"status": "working", "summary": "s",
                                           "at": spelling}
            _true("2h" in row3("w"),
                  f"stamp spelling {spelling!r} did not age to 2h: {row3('w')!r}")

    check("both real stamp spellings ('…Z' and '…+00:00') age identically",
          _both_stamp_formats)

    # An unparseable or absent stamp must degrade LOUDLY. Silently printing
    # the status word with no age is precisely the failure this feature is for.
    def _bad_stamp_is_loud():
        o4, row4 = rig(b=(None, "none"))
        nd(o4, "b")["last_status"] = {"status": "working", "summary": "s",
                                       "at": "yesterday-ish"}
        _true("age unknown" in row4("b"), row4("b"))

    check("an unparseable stamp reads 'age unknown', never a bare status",
          _bad_stamp_is_loud)

    # A stamp from the future is a clock adjustment or a doc moved between
    # machines. It must not read as fresh — but a sub-second overshoot on a
    # status reported microseconds ago is the commonest case of all and must
    # not cry wolf.
    def _future_stamp():
        o5, row5 = rig(f1=(None, "none"), f2=(None, "none"))
        nd(o5, "f1")["last_status"] = st("working", "s", -2 * HOUR)
        nd(o5, "f2")["last_status"] = st("working", "s", -0.25)
        _true("in the future?" in row5("f1"), row5("f1"))
        _true("in the future?" not in row5("f2"),
              f"a sub-second overshoot cried wolf: {row5('f2')!r}")

    check("a future stamp is flagged; a sub-second overshoot is not",
          _future_stamp)

    # The renderer must not take the chart down if the live-process lookup
    # misbehaves: the roster is not optional. Prove the fallback is real by
    # making state() raise.
    def _state_failure_degrades():
        o6, row6 = rig(s=(st("idle", "finished up", 30 * MIN), "last"))
        orig = S.state
        try:
            def boom(*a, **k):
                raise RuntimeError("no supervisor here")
            S.state = boom
            line = row6("s")
        finally:
            S.state = orig
        _true("idle" in line and "finished up" in line and "30m" in line,
              f"a failing state() lost the self-reported half: {line!r}")

    check("a failing state() degrades to the self-reported half, not to blank",
          _state_failure_degrades)

    # D-223 digests the chart text and suppresses it while that digest holds.
    # Coarse buckets are what keep a quiet org's suppression alive; if someone
    # later swaps them for a live "7m ago", the chart re-sends EVERY turn.
    def _buckets_are_coarse():
        o7, _ = rig(q=(st("idle", "done", 2 * HOUR), "last"))

        def at(now):
            return "\n".join(S._render_chart(o7, ["boss"], "", 0, True, None,
                                             now))
        base = at(NOW)
        _true(at(NOW + 60) == base and at(NOW + 20 * MIN) == base,
              "the chart text changed within one age bucket — D-223 chart "
              "suppression would now miss on every turn")
        _true(at(NOW + 90 * MIN) != base,
              "crossing an age bucket did NOT change the chart — the age is "
              "not actually being rendered")

    check("age buckets are coarse enough to keep D-223 suppression alive",
          _buckets_are_coarse)


def main() -> None:
    print("═══ FR-1 · the chart carries status, and how old it is ═══")
    sec_shapes()
    sec_stale()
    sec_objective()
    sec_chart_survives()
    sec_controls()

    print(f"\n{'═' * 70}\n{PASS} checks passed, {len(FAIL)} failed")
    for label, tb in FAIL:
        print(f"\nFAIL  {label}\n{tb}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
