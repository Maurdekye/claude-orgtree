"""Reset priority — the time IN THE LIMIT MESSAGE comes first, for every
provider; cached usage only when the message carries none, and then matched
to model, account lane and limit type (user ruling 2026-09-07 14:56Z;
coordinator decisions 15:03Z: an unnamed type keeps the 2026-08-18 shortest
rule, a named type takes matched evidence only and never silently borrows
another lane's reset; a message/provider deadline outranks the roster mark in
the API projection).

Every check here FAILED on main 7a210e8 before the correction (the audit's
G1–G7, feature-fable/reset-priority/audit.md) and each sits beside a positive
control proving the instrument can tell the two answers apart:

    §1  the parser reads the codex app-server's dated wording ("try again at
        Sep 6th, 2026 10:33 AM") — the one time that message carried was the
        one time nothing could read (G5)
    §2  the cached readout is matched to the node's MODEL and the named
        limit TYPE, and a borrowed or unnamed answer is scheduled as a probe,
        not an observed deadline (G3)
    §3  the freeze STAMP takes the message's time over the cached recovery
        deadline, end to end through the real turn loop with the CLI stand-in
        — and the roster mark records the same time (G1)
    §4  the off-lock correction pass does not rewrite a message-stamped
        freeze to the cache's time (G2)
    §5  codex: the turn's own notification outranks the cached board (G4),
        and a board-derived value ranks below the error's prose (G5)
    §6  the API projection shows a message/provider deadline over the roster
        mark, and still falls back to the roster without one (G7)

Not covered here, said plainly: the Luna double-rejection branch (G6) is
reached only through the codex wrapper's two-leg re-drive; its precedence is
the same `reset_from` mechanism §5 measures, but the branch itself has no
end-to-end control in this suite.

Hermetic: reuses test_limit_freeze's rig (throwaway ORGTREE_DATA + HOME, the
node stand-in CLI, a synthetic usage readout); no network, no real CLI. §3
needs `node` on PATH and is skipped with a note without it.

    python backend/tests/test_reset_priority.py [-v]
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import time
import traceback
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# ⚠ FIRST: this establishes the throwaway ORGTREE_DATA/HOME and the stand-in
# CLI before any orgtree import (store binds DATA_ROOT at import time)
import test_limit_freeze as rig  # noqa: E402

from orgtree import accounts, api, codex_route, limits, store, supervisor  # noqa: E402

assert "orgtree-limitfreeze-" in store.DATA_ROOT, ("INERT: wrong storage root",
                                                    store.DATA_ROOT)

PASS = 0
FAIL: list[tuple[str, str]] = []
NOTES: list[str] = []
VERBOSE = "-v" in sys.argv


def check(label: str, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def fixture(ok: bool, msg: str) -> None:
    """A precondition of the check itself (the rig), not the claim under
    test — a check whose fixture is broken has measured nothing."""
    if not ok:
        raise AssertionError("FIXTURE (not the claim): " + msg)


def eq(got: Any, want: Any, what: str) -> None:
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


def near(got: Any, want: float, what: str, tol: float = 2.0) -> None:
    if not isinstance(got, (int, float)) or abs(float(got) - want) > tol:
        raise AssertionError(f"{what}: got {got!r}, wanted ≈{want!r}")


def readout(*lanes) -> None:
    """(kind, resets_in_s, is_active, model) → the cached readout, and the
    fetch the correction pass makes answers with the SAME board (no
    network)."""
    rig._readout(*((k, "weekly" if k.startswith("weekly") else "session",
                    99 if act else 10, "critical" if act else "normal",
                    r, act, m) for k, r, act, m in lanes))
    limits.fetch = lambda force=False, max_age=None: limits._cache["data"]


# ══════════════════════════════════════════════════════════════════════ §1

def sec_parser() -> None:
    print("\n§1 the parser reads the codex app-server's dated wording (G5)")
    now = time.time()
    soon = dt.datetime.fromtimestamp(now + 2 * 3600).replace(second=0,
                                                              microsecond=0)
    label = soon.strftime("%b %-d" if os.name != "nt" else "%b %#d")
    # "Sep 6th, 2026 10:33 AM" — the measured specimen's shape, with a day
    # ordinal and a 12-hour clock
    day = int(soon.strftime("%d"))
    suffix = ("th" if 11 <= day <= 13
              else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th"))
    stated = (f"{soon.strftime('%b')} {day}{suffix}, {soon.year} "
              f"{soon.strftime('%I:%M %p').lstrip('0')}")
    del label

    def _dated() -> None:
        ts, how = supervisor._parse_limit_reset_ts_raw(
            f"You've hit your usage limit. Try again at {stated}.", now)
        eq(how, "date", "form")
        near(ts, soon.timestamp(), "the stated instant, in this machine's zone")
    check(f"'try again at {stated}' reads as that instant", _dated)

    def _long_form() -> None:
        long = (f"{soon.strftime('%B')} {day}, {soon.year} at "
                f"{soon.strftime('%H:%M')}")
        ts, how = supervisor._parse_limit_reset_ts_raw(
            f"usage limit reached — try again on {long}", now)
        eq(how, "date", "form (24-hour clock, full month, 'on')")
        near(ts, soon.timestamp(), "the stated instant")
    check("the long form (full month, 'on', 24-hour clock) reads too",
          _long_form)

    def _banded() -> None:
        # the banded parser refuses a dated time past its lane, like every
        # other prose form — a session limit does not lift in nine days
        far = dt.datetime.fromtimestamp(now + 9 * 86400)
        text = (f"You've hit your session limit. Try again at "
                f"{far.strftime('%b')} {far.day}, {far.year} "
                f"{far.strftime('%I:%M %p').lstrip('0')}")
        fixture(supervisor._parse_limit_reset_ts_raw(text, now)[0] is not None,
                "the raw parse must see the date for the band to matter")
        eq(supervisor._parse_limit_reset_ts(text, "session", now), None,
           "a dated reset nine days out for a session limit")
        # …and inside the lane it stands
        near(supervisor._parse_limit_reset_ts(
            f"You've hit your session limit. Try again at {stated}.",
            "session", now), soon.timestamp(), "inside the lane")
    check("the dated form is banded by the lane like the other prose forms",
          _banded)

    def _not_a_reset() -> None:
        eq(supervisor._parse_limit_reset_ts_raw(
            f"no change since {stated}, nothing to do", now), (None, ""),
           "a date in unrelated prose")
        eq(supervisor._parse_limit_reset_ts_raw(
            "resets Feb 30th, 2026 1:00 pm", now), (None, ""),
           "an impossible date")
    check("control · a date that is not a reset, and one that is not a date, "
          "read as nothing", _not_a_reset)

    def _codex_freeze_reads_prose() -> None:
        # the provider freeze with NO out-of-band value: until 2026-09-07 this
        # was the blind 5-minute floor for the measured codex wording
        ts, src = supervisor._provider_limit_until(
            f"You've hit your usage limit. Try again at {stated}.", None, now)
        eq(src, "text", "provenance")
        near(ts, soon.timestamp(), "the message's own time")
    check("a codex-shaped error with no notification freezes on the prose "
          "time, not the probe floor", _codex_freeze_reads_prose)


# ══════════════════════════════════════════════════════════════════════ §2

def sec_matching() -> None:
    print("\n§2 the cached readout is matched to model and limit type (G3)")
    now = time.time()
    # weekly_all resets soonest, Fable's scoped pool next, session last
    readout(("weekly_all", 3600, False, None),
            ("weekly_scoped", 2 * 3600, True, "Fable"),
            ("session", 3 * 3600, False, None))

    def _model_matched() -> None:
        ts, src = limits.reset_for("Claude AI usage limit reached", now,
                                   tier="fable")
        eq(src, "usage:weekly_scoped",
           "a Fable node is not answered from the pooled weekly lane")
        near(ts, now + 2 * 3600, "Fable's own pool")
        ts, src = limits.reset_for("Claude AI usage limit reached", now,
                                   tier="opus")
        eq(src, "usage:weekly_all",
           "a pooled node is not answered from Fable's scoped pool")
        near(ts, now + 3600, "the pooled weekly lane")
        # control: with no tier there is no filter — the soonest lane answers
        eq(limits.reset_for("Claude AI usage limit reached", now)[1],
           "usage:weekly_all", "no tier = no filter (the older behaviour)")
    check("an unnamed limit is answered from the lanes that describe THIS "
          "model's quota — shortest eligible, never the latest active",
          _model_matched)

    def _schedule_kinds() -> None:
        eq(supervisor._usage_schedule_kind("resets 1:40pm", "text"),
           "observed-deadline", "a parsed message time")
        eq(supervisor._usage_schedule_kind("x", "provider"),
           "observed-deadline", "a provider's stated value")
        eq(supervisor._usage_schedule_kind(
            "You've hit your session limit", "usage:session"),
           "observed-deadline", "the NAMED lane, matched")
        eq(supervisor._usage_schedule_kind(
            "You've hit your session limit", "usage:weekly_all"),
           "probe", "a named lane answered from ANOTHER lane is an estimate")
        eq(supervisor._usage_schedule_kind(
            "Claude AI usage limit reached", "usage:session"),
           "probe", "an unnamed type's cache answer is an estimate")
        eq(supervisor._usage_schedule_kind(
            "You've hit your session limit", "usage:session", trusted=False),
           "probe", "an untrusted blob names no lane")
        eq(supervisor._usage_schedule_kind("x", "probe"), "probe", "the floor")
    check("only a matched named lane is an observed deadline; a borrowed or "
          "unnamed cache answer is a bounded probe — the borrowing is never "
          "silent", _schedule_kinds)

    def _named_no_match_is_visible() -> None:
        # a "session" limit whose session entry is stale: the answer falls
        # through to the weekly lane inside the session reach, and the
        # record SAYS so (probe), instead of presenting it as session's reset
        readout(("session", -600, True, None),
                ("weekly_all", 3 * 3600, False, None))
        ts, src = limits.reset_for("You've hit your session limit", now,
                                   tier="haiku")
        eq(src, "usage:weekly_all", "the borrowed lane is named as itself")
        eq(supervisor._usage_schedule_kind(
            "You've hit your session limit", src), "probe",
           "…and scheduled as a probe")
    check("a named type with no matching evidence borrows explicitly: the "
          "lane is recorded as itself and the schedule is a probe",
          _named_no_match_is_visible)


# ══════════════════════════════════════════════════════════════════════ §3

def sec_stamp() -> None:
    print("\n§3 the freeze STAMP takes the message's time over the cache (G1)")
    now = time.time()
    stated = int(now) + 1800                     # the message: 30 min
    cache_reset = 3 * 3600                       # the cache: 3 h, active
    readout(("session", cache_reset, True, None),
            ("weekly_all", 4 * 86400, True, None))
    blob = f"You've hit your session limit · resets|{stated}"

    def _stamp() -> None:
        slug, nid = rig.probe_org()
        rig.set_mode("iserror", limit_text=blob)
        rig.run_turn(slug, nid, "go")
        fz = rig.node(slug, nid).get("frozen") or {}
        fixture(fz.get("limit") is True, f"the rig did not freeze: {fz}")
        near(fz.get("until_ts"), stated,
             "the freeze is stamped from the message's own time")
        eq(fz.get("reset_src"), "text", "provenance")
        eq(fz.get("schedule_kind"), "observed-deadline", "schedule")
        # the correction pass runs off-lock on its own thread: give it a
        # moment and re-read — it must not move a message-stamped freeze
        time.sleep(1.0)
        fz2 = rig.node(slug, nid).get("frozen") or {}
        near(fz2.get("until_ts"), stated,
             "…and the off-lock correction pass left it there")
        eq(fz2.get("reset_src"), "text", "provenance after the pass")
    check("a message naming its reset is stamped from that reset, not from "
          "the readout's active lane 3 h out", _stamp)

    def _control_cache_when_absent() -> None:
        # the same rig with a message carrying NO time: the cache answers —
        # proof the readout is wired and the previous check measured
        # precedence rather than an inert cache
        slug, nid = rig.probe_org()
        rig.set_mode("iserror", limit_text="You've hit your session limit")
        rig.run_turn(slug, nid, "go")
        fz = rig.node(slug, nid).get("frozen") or {}
        fixture(fz.get("limit") is True, f"the rig did not freeze: {fz}")
        near(fz.get("until_ts"), now + cache_reset,
             "no time in the message → the matched cached lane", tol=30.0)
        eq(fz.get("reset_src"), "usage:session", "provenance")
        eq(fz.get("schedule_kind"), "observed-deadline",
           "the named lane, matched, is an observed deadline")
    check("control · a message with no time is answered from the matched "
          "cached lane (the readout is live)", _control_cache_when_absent)

    def _roster_mark() -> None:
        # the roster mark written at freeze time carries the message's time
        # too — it is what the API projection and the account panel display.
        # ⚠ a mark is never SHORTENED (accounts.record_limit), so the control
        # above — which marked the pool from the cache's 3 h lane — is
        # cleared first, or this would measure that rule instead of the stamp
        doc = accounts.load()
        doc["usage_refreshes"] = {}
        accounts.save(doc)
        slug, nid = rig.probe_org()
        rig.set_mode("iserror", limit_text=blob)
        rig.run_turn(slug, nid, "go")
        # read the mark itself: `resolve` lists no lane in this rig (nobody
        # is signed in), so its answer would be vacuous either way
        marks = (accounts.load().get("usage_refreshes") or {}).get(
            accounts.PRIMARY) or {}
        fixture("haiku" in marks, f"the freeze did not mark the roster: {marks}")
        near(marks["haiku"], stated,
             "the roster mark follows the message, not the cache")
    check("the roster mark records the message's time, not the cache's",
          _roster_mark)


# ══════════════════════════════════════════════════════════════════════ §4

def sec_correction_pass() -> None:
    print("\n§4 the off-lock correction pass keeps a message-stamped freeze (G2)")
    now = time.time()
    stated = now + 1800
    readout(("session", 3 * 3600, True, None))
    blob = f"You've hit your session limit · resets|{int(stated)}"

    def _seed(src: str, kind: str, ts: float) -> tuple[str, str]:
        slug, nid = rig.probe_org()
        with store.DOC_LOCK:
            org = store.load_org(slug)
            org.node(nid)["frozen"] = {
                "limit": True, "until_ts": ts, "until": "x",
                "reset_src": src, "schedule_kind": kind, "provider": "claude",
                "account": "primary", "resource_pool": "haiku+sonnet+opus",
                "at": supervisor.now_iso(), "error": blob}
            store.save_org(org)
        return slug, nid

    def _kept() -> None:
        slug, nid = _seed("text", "observed-deadline", stated)
        moved = supervisor._refresh_freeze_reset(
            slug, nid, blob, stated, None, subscription=True, trusted=True,
            tier="haiku", stamped_kind="observed-deadline")
        eq(moved, False, "the pass rewrote a message-stamped freeze")
        fz = rig.node(slug, nid)["frozen"]
        near(fz["until_ts"], stated, "until_ts")
        eq(fz["reset_src"], "text", "provenance")
    check("a freeze stamped from the message is not rewritten to the cache's "
          "active lane", _kept)

    def _control_moves() -> None:
        # the SAME pass, on a freeze stamped from the probe floor with a
        # message carrying no time: the re-read cache answer must move it —
        # proof the pass is live and the previous check measured restraint
        floor = now + supervisor.PROBE_FLOOR
        slug, nid = _seed("probe", "probe", floor)
        moved = supervisor._refresh_freeze_reset(
            slug, nid, "You've hit your session limit", floor, None,
            subscription=True, trusted=True, tier="haiku",
            stamped_kind="probe")
        eq(moved, True, "the pass did nothing on a probe-floor freeze")
        fz = rig.node(slug, nid)["frozen"]
        near(fz["until_ts"], now + 3 * 3600, "the matched cached lane",
             tol=30.0)
        eq(fz["reset_src"], "usage:session", "provenance")
    check("control · the same pass moves a probe-floor freeze to the matched "
          "cached lane (the pass is live)", _control_moves)


# ══════════════════════════════════════════════════════════════════════ §5

def sec_codex() -> None:
    print("\n§5 codex: notification over board, board below prose (G4, G5)")
    now = 1_800_000_000.0
    iso = codex_route._iso   # pyright: ignore[reportPrivateUsage]

    def window(model, pct, resets):
        return {"model": model, "percent": float(pct), "resets_at": resets,
                "is_active": pct >= 100, "observed_at": now}

    board = {"available": True, "account": "acct-A", "age": 0.0,
             "stale": False,
             "limits": [window(None, 100, iso(now + 3 * 3600))]}
    # the turn's own notification: the plan pool's window resets in ONE hour
    snapshots = {"codex": {"limitName": "", "rateLimitReachedType": "usage",
                           "primary": {"usedPercent": 100,
                                       "resetsAt": now + 3600}}}
    direct = codex_route.direct_route("terra", "gpt-5.6-terra", "acct-A",
                                      reason="test", evidence="synthetic")
    plan = codex_route.PLAN_POOL

    def _notification_first() -> None:
        eq(codex_route.failure_schedule(direct, board, snapshots, plan, now=now),
           (now + 3600, "observed-deadline"),
           "the reset the provider stated for THIS turn")
        ts, kind, src = codex_route.failure_deadline(direct, board, snapshots,
                                                     plan, now=now)
        eq((ts, kind, src), (now + 3600, "observed-deadline",
                             codex_route.SRC_NOTIFICATION), "with provenance")
    check("the turn's own notification outranks a later board reset",
          _notification_first)

    def _board_when_absent() -> None:
        ts, kind, src = codex_route.failure_deadline(direct, board, None, plan,
                                                     now=now)
        eq((ts, kind, src), (now + 3 * 3600, "observed-deadline",
                             codex_route.SRC_BOARD),
           "no notification → the board, and it says so")
        # an exhausted notification WITHOUT a reset is a probe, not patched
        # over with the board's number
        blank = {"codex": {"limitName": "", "rateLimitReachedType": "usage",
                           "primary": {"usedPercent": 100}}}
        eq(codex_route.failure_deadline(direct, board, blank, plan, now=now),
           (None, "probe", ""), "an unknown stated reset is not the board's")
    check("control · the board answers only when the notification carries "
          "no reset, with its own provenance", _board_when_absent)

    def _luna_per_pool() -> None:
        luna = codex_route._route_for(  # pyright: ignore[reportPrivateUsage]
            "luna", codex_route.RESERVE_POOL, "acct-A", reason="test",
            evidence="synthetic")
        both = {**board, "limits": [
            window(None, 100, iso(now + 3 * 3600)),
            window(codex_route.RESERVE_MODEL, 100, iso(now + 5 * 3600))]}
        # sent to reserve; the notification says reserve resets in 4 h, the
        # board said 5 h for reserve and 3 h for plan → plan's board reset is
        # the earliest KNOWN pool (the message said nothing about plan)
        snaps = {"codex": {"limitName": "", "rateLimitReachedType": "usage",
                           "primary": {"usedPercent": 100,
                                       "resetsAt": now + 4 * 3600}}}
        ts, kind, src = codex_route.failure_deadline(
            luna, both, snaps, codex_route.RESERVE_POOL, now=now)
        eq((ts, kind, src), (now + 3 * 3600, "probe", codex_route.SRC_BOARD),
           "earliest pool, board for the pool the message did not describe")
        # …and when the board's plan reset is LATER than the stated reserve
        # reset, the stated one wins the min and carries its provenance
        later = {**both, "limits": [
            window(None, 100, iso(now + 6 * 3600)),
            window(codex_route.RESERVE_MODEL, 100, iso(now + 5 * 3600))]}
        ts, kind, src = codex_route.failure_deadline(
            luna, later, snaps, codex_route.RESERVE_POOL, now=now)
        eq((ts, kind, src), (now + 4 * 3600, "probe",
                             codex_route.SRC_NOTIFICATION),
           "the stated reserve reset, not the board's 5 h")
    check("Luna (both pools out): message first PER POOL, then the earliest "
          "pool", _luna_per_pool)

    def _board_below_prose() -> None:
        # the provider freeze: a board-derived value ranks BELOW the error's
        # own prose; a message-level value ranks above it. Real clock: the
        # relative prose form anchors on `time.time()`, not the fixed `now`.
        now = time.time()
        prose = "usage limit reached — try again in 20 minutes"
        ts, src = supervisor._provider_limit_until(
            prose, now + 3 * 3600, now, reset_from="board")
        eq(src, "text", "prose over a board value")
        near(ts, now + 20 * 60, "the prose's 20 minutes")
        ts, src = supervisor._provider_limit_until(
            prose, now + 3600, now, reset_from="message")
        eq((ts, src), (now + 3600, "provider"), "a stated value over prose")
        ts, src = supervisor._provider_limit_until(
            "usage limit reached", now + 3 * 3600, now, reset_from="board")
        eq((ts, src), (now + 3 * 3600, "usage:board"),
           "no prose time → the board, named as a cached lane")
        ts, src = supervisor._provider_limit_until(
            "usage limit reached", None, now)
        eq((ts, src), (now + supervisor.PROBE_FLOOR, "probe"), "the floor")
    check("provider freeze ranking: stated value > prose > board > floor",
          _board_below_prose)

    def _exception_carries_it() -> None:
        e = supervisor._ProviderTurnFailed("x", blob="y", reset_ts=now + 1,
                                           reset_from="board")
        eq(e.reset_from, "board", "reset_from")
        eq(supervisor._ProviderTurnFailed("x").reset_from, "message",
           "the default is the provider's own statement (antigravity, codex "
           "notification)")
    check("_ProviderTurnFailed carries where its reset came from",
          _exception_carries_it)


# ══════════════════════════════════════════════════════════════════════ §6

def sec_projection() -> None:
    print("\n§6 the API projection: message deadline over the roster mark (G7)")
    now = time.time()
    stated = now + 1800
    roster_later = now + 7200

    def node_with(src: str, kind: str, ts: float | None) -> dict[str, Any]:
        return {"tier": "haiku", "frozen": {
            "limit": True, "until_ts": ts, "until": "x", "reset_src": src,
            "schedule_kind": kind, "pool": "dry"}}

    unavailable = {"haiku": {"account": "primary", "available": False,
                             "refresh_at": roster_later}}

    def _message_wins() -> None:
        for src in ("text", "provider"):
            n = node_with(src, "observed-deadline", stated)
            api._rederive_freeze_reset(n, dict(unavailable))
            near(n["frozen"]["until_ts"], stated,
                 f"{src}: the freeze's own deadline is displayed")
            if not str(n["frozen"]["until"]).startswith("capacity resets "):
                raise AssertionError(n["frozen"]["until"])
        n = node_with("provider", "probe", stated)
        api._rederive_freeze_reset(n, dict(unavailable))
        if not str(n["frozen"]["until"]).startswith("capacity recheck "):
            raise AssertionError(("a probe keeps saying recheck",
                                  n["frozen"]["until"]))
    check("a text/provider deadline still ahead is displayed over the "
          "roster's later mark", _message_wins)

    def _roster_when_absent() -> None:
        # control: a cache-derived freeze, an expired message deadline and a
        # freeze with no time all still take the roster — the roster path is
        # live, so the previous check measured precedence
        for src, ts in (("usage:session", stated), ("text", now - 60),
                        ("probe", now + 300), ("text", None)):
            n = node_with(src, "probe", ts)
            api._rederive_freeze_reset(n, dict(unavailable))
            near(n["frozen"]["until_ts"], roster_later,
                 f"{src}/{ts}: the roster mark")
        n = node_with("text", "observed-deadline", stated)
        api._rederive_freeze_reset(
            n, {"haiku": {"account": "primary", "available": True,
                          "refresh_at": None}})
        eq(n["frozen"]["until_ts"], None,
           "capacity available elsewhere still says so (unchanged)")
    check("control · without an applicable message deadline the roster "
          "answers, as before", _roster_when_absent)


def main() -> None:
    print("═══ reset priority — the limit message's time comes first ═══")
    sec_parser()
    sec_matching()
    sec_correction_pass()
    if shutil.which("node"):
        sec_stamp()
    else:
        NOTES.append("node is not on PATH — §3 (the end-to-end stamp) skipped;"
                     " G1 is then covered only by §4's pass-level control")
    sec_codex()
    sec_projection()
    print(f"\n{'═' * 70}\n{PASS} checks passed, {len(FAIL)} failed")
    for label, tb in FAIL:
        print(f"\nFAIL  {label}\n{tb}")
    for m in NOTES:
        print(f"  · {m}")
    print(f"ALL {PASS} CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
