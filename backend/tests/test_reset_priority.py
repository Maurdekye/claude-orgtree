"""Reset priority — the time IN THE LIMIT MESSAGE comes first, for every
provider; cached usage only when the message carries none, and then matched
to model, account lane and limit type (user ruling 2026-09-07 14:56Z;
coordinator decisions 15:03Z, 15:32Z and 15:36Z: an unnamed type keeps the
2026-08-18 shortest rule; a named type takes matched evidence only and never
borrows another lane's reset, not even as a probe; a trusted explicit
timestamp — epoch or dated — keeps only the global guards and is never cut
down by an inferred lane; a stated zone is honoured or the form declined; a
message/provider deadline outranks the roster in the API projection whatever
the roster says; the Claude readout describes Claude tiers only).

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

    §7  the Luna double rejection — the ACTUAL selection path in
        `_codex_leg` with both legs rejecting — takes each leg's stated reset
        for its own pool ahead of the cached board (G6)

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
from zoneinfo import ZoneInfo as _zi

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

    def _dated_text(at: dt.datetime, lead: str = "Try again at") -> str:
        return (f"{lead} {at.strftime('%b')} {at.day}, {at.year} "
                f"{at.strftime('%I:%M %p').lstrip('0')}")

    def _stated_fact() -> None:
        # a dated time ~19 h out, for an UNNAMED and for a SESSION-named
        # limit: until 2026-09-07 the lane band declined both and the freeze
        # fell through to the cache (redteam F1); the stated time now wins
        # and only the global guards remain (coordinator decision 15:36Z)
        later = dt.datetime.fromtimestamp(now + 19 * 3600).replace(
            second=0, microsecond=0)
        for kind, text in ((None, "You've hit your usage limit. "
                                  + _dated_text(later) + "."),
                           ("session", "You've hit your session limit. "
                                       + _dated_text(later) + ".")):
            fixture(limits.classify(text)[0] == kind, f"classify: {text}")
            near(supervisor._parse_limit_reset_ts(text, kind, now),
                 later.timestamp(), f"{kind}: the stated time, 19 h out")
            ts, src = supervisor._limit_reset_ts(text)
            eq(src, "text", f"{kind}: through the freeze seam")
            near(ts, later.timestamp(), f"{kind}: the stated time")
        # the global guards stand: nine days out, and the past
        far = dt.datetime.fromtimestamp(now + 9 * 86400)
        text = "You've hit your session limit. " + _dated_text(far)
        fixture(supervisor._parse_limit_reset_ts_raw(text, now)[0] is not None,
                "the raw parse must see the date for the guard to matter")
        eq(supervisor._parse_limit_reset_ts(text, "session", now), None,
           "nine days out is not a reset")
        past = dt.datetime.fromtimestamp(now - 3600)
        eq(supervisor._parse_limit_reset_ts(
            "usage limit. " + _dated_text(past), None, now), None, "the past")
        # …and an UNTRUSTED blob keeps the lane band (provenance, not form)
        eq(supervisor._parse_limit_reset_ts(
            "You've hit your session limit. " + _dated_text(later),
            "session", now, trusted=False), None, "untrusted stays banded")
    check("a stated dated time ~19 h out is honoured for an unnamed AND a "
          "session-named limit; nine days / the past / untrusted are refused",
          _stated_fact)

    def _codex_specimen() -> None:
        # the real app-server wording, measured 2026-09-06: what does the
        # lane classifier make of it, and does the whole seam honour it?
        specimen = ("You've hit your usage limit. Try again at Sep 6th, 2026 "
                    "10:33 AM.")
        eq(limits.classify(specimen), (None, None),
           "the specimen names no lane (so no lane could have banded it)")
        # the same shape, re-dated ~19 h ahead so it is a live reset
        later = dt.datetime.fromtimestamp(now + 19 * 3600).replace(
            second=0, microsecond=0)
        live = "You've hit your usage limit. " + _dated_text(later) + "."
        ts, src = supervisor._provider_limit_until(live, None, now)
        eq(src, "text", "the codex freeze reads the message")
        near(ts, later.timestamp(), "…at the stated instant")
    check("the real codex specimen names no lane and freezes on its stated "
          "time", _codex_specimen)

    def _zones() -> None:
        utc = dt.timezone.utc
        at = dt.datetime.fromtimestamp(now + 19 * 3600, utc).replace(
            second=0, microsecond=0)
        clock = at.strftime('%I:%M %p').lstrip('0')
        base = f"Try again at {at.strftime('%b')} {at.day}, {at.year} {clock}"
        # a stated zone is READ in that zone (coordinator correction 3;
        # redteam F3 measured "(UTC)" and "UTC+3" read as local)
        for suffix, want in ((" (UTC)", at), (" UTC", at), (" Z", at),
                             (" UTC+3", at - dt.timedelta(hours=3)),
                             (" GMT-05:30", at + dt.timedelta(hours=5,
                                                              minutes=30)),
                             (" +02:00", at - dt.timedelta(hours=2)),
                             (" (Asia/Jerusalem)",
                              at.astimezone(_zi("Asia/Jerusalem")).replace(
                                  tzinfo=None) and None)):
            ts, how = supervisor._parse_limit_reset_ts_raw(base + suffix, now)
            eq(how, "date", f"form for {suffix!r}")
            if want is not None:
                near(ts, want.timestamp(), f"instant for {suffix!r}")
            else:
                # an IANA zone: the wall clock in THAT zone
                local = dt.datetime(at.year, at.month, at.day, at.hour,
                                    at.minute, tzinfo=_zi("Asia/Jerusalem"))
                near(ts, local.timestamp(), "Asia/Jerusalem wall clock")
        # an unsupported zone DECLINES rather than reads as local
        for suffix in (" PST", " IDT", " (Mars/Olympus)", " (CEST)"):
            eq(supervisor._parse_limit_reset_ts_raw(base + suffix, now),
               (None, ""), f"unsupported zone {suffix!r} declines")
        # …but ORDINARY PROSE after the clock, in any case, is not a zone
        # (redteam R1: "NEXT", "BUT", "NOT", "AT" were declining the time)
        for tail in (" NEXT week", " BUT retry sooner", " NOT before then",
                     " AT the earliest", " (estimated)", " WAIT for it",
                     " THEN retry"):
            ts, how = supervisor._parse_limit_reset_ts_raw(base + tail, now)
            eq(how, "date", f"prose {tail!r} is not a zone")
            want = dt.datetime(at.year, at.month, at.day, at.hour,
                               at.minute).timestamp()       # local wall clock
            near(ts, want, f"read as local for {tail!r}")
        # …and the bare-clock claude wording gets the same treatment
        ts, how = supervisor._parse_limit_reset_ts_raw(
            "You've hit your limit · resets 12:40am (Asia/Jerusalem)", now)
        eq(how, "clock", "the measured claude wording")
        ref = dt.datetime.fromtimestamp(now, _zi("Asia/Jerusalem"))
        want = ref.replace(hour=0, minute=40, second=0, microsecond=0)
        if want <= ref:
            want += dt.timedelta(days=1)
        near(ts, want.timestamp(), "12:40am IN Asia/Jerusalem")
        eq(supervisor._parse_limit_reset_ts_raw("resets 12:40am PST", now),
           (None, ""), "an abbreviation on the clock form declines")
        # control: prose after the clock is not a zone
        ts2, how2 = supervisor._parse_limit_reset_ts_raw(
            "resets 12:40am today and more", now)
        eq(how2, "clock", "'today' is not a zone")
        near(ts2, dt.datetime.fromtimestamp(now).replace(
            hour=0, minute=40, second=0, microsecond=0).timestamp()
            + (86400 if dt.datetime.fromtimestamp(now).hour > 0
               or dt.datetime.fromtimestamp(now).minute >= 40 else 0),
            "read as local")
    check("a stated zone is honoured (UTC/GMT/Z/offset/IANA) or the form "
          "declines (abbreviations, unknown names) — never read as local",
          _zones)

    def _hours() -> None:
        at = dt.datetime.fromtimestamp(now + 19 * 3600)
        head = f"Try again at {at.strftime('%b')} {at.day}, {at.year} "
        for bad in ("13:33 AM", "13:33 PM", "0:10 AM", "25:33"):
            eq(supervisor._parse_limit_reset_ts_raw(head + bad, now),
               (None, ""), f"{bad!r} declines")
        for bad in ("13:40pm", "0:40am"):
            eq(supervisor._parse_limit_reset_ts_raw("resets " + bad, now),
               (None, ""), f"clock {bad!r} declines")
        # controls: the edges that are real times
        for good, h in (("12:33 AM", 0), ("12:33 PM", 12), ("1:05 pm", 13),
                        ("23:33", 23)):
            ts, how = supervisor._parse_limit_reset_ts_raw(head + good, now)
            eq(how, "date", f"{good!r} reads")
            eq(dt.datetime.fromtimestamp(ts).hour, h, f"{good!r} hour")
    check("an impossible clock hour declines instead of shifting (13 AM); "
          "12 AM/PM and 24-hour edges read correctly", _hours)

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

    def _named_no_match() -> None:
        # a "session" limit whose session entry is stale, with the weekly
        # lane right there inside the session reach: NOT borrowed (the user's
        # matching rule, literal — coordinator 15:32Z); the seam falls to
        # nothing and the freeze takes its probe floor
        readout(("session", -600, True, None),
                ("weekly_all", 3 * 3600, False, None))
        eq(limits.reset_for("You've hit your session limit", now, tier="haiku"),
           (None, ""), "no matching evidence → no cache answer")
        eq(supervisor._limit_reset_ts("You've hit your session limit",
                                      tier="haiku"), (None, ""),
           "…through the seam")
        # control: the same board answers an UNNAMED limit (shortest eligible)
        eq(limits.reset_for("Claude AI usage limit reached", now,
                            tier="haiku")[1], "usage:weekly_all",
           "an unnamed limit still takes the soonest eligible lane")
    check("a named type with no matching cache evidence gets NO cache answer "
          "— the floor, not another lane's reset", _named_no_match)

    def _claude_only() -> None:
        # the Claude readout describes Claude tiers only (coordinator
        # decision 15:36Z on redteam F5): a codex or other tier gets nothing
        # from it, however the message reads
        readout(("session", 3600, True, None),
                ("weekly_all", 2 * 3600, False, None))
        for tier in ("luna", "gpt-reserve", "agy", "or-x"):
            eq(limits.reset_for("Claude AI usage limit reached", now,
                                tier=tier), (None, ""), f"{tier}: unnamed")
            eq(limits.reset_for("You've hit your session limit", now,
                                tier=tier), (None, ""), f"{tier}: named")
        eq(limits.CLAUDE_TIERS, accounts.TIERS, "the tier list is the roster's")
        # control: a Claude tier is answered
        eq(limits.reset_for("You've hit your session limit", now,
                            tier="sonnet")[1], "usage:session", "sonnet")
    check("the Claude readout never answers a non-Claude tier", _claude_only)

    def _exhausted_still_loses_to_a_stated_time() -> None:
        # issue #4: a 429 that ALSO carries a stated time is not the shape
        # `exhausted_reset` exists for — the ruling order holds regardless
        readout(("session", 3 * 3600, True, None))
        limits._cache["data"]["limits"][0]["percent"] = 100  # measured spent
        ts, src = supervisor._limit_reset_ts(
            "API Error: 429 rate_limit_error: rate limit exceeded, try "
            "again in 2 minutes", tier="haiku")
        eq(src, "text", "the message's own time still outranks the board, "
                        "exhausted or not")
        near(ts, now + 120, "two minutes, as stated")
    check("issue #4 · a 429 that carries its own stated time is not answered "
          "from the exhausted-lane fallback — text still wins",
          _exhausted_still_loses_to_a_stated_time)

    def _exhausted_answers_a_bare_429() -> None:
        # …and the control: strip the stated time, and the same exhausted
        # board is what timed this fix in the first place
        readout(("session", 3 * 3600, True, None))
        limits._cache["data"]["limits"][0]["percent"] = 100
        ts, src = supervisor._limit_reset_ts(
            "API Error: 429 rate_limit_error: per-minute rate limit",
            tier="haiku")
        eq(src, "usage:exhausted:session", "the exhausted lane answers a "
                                           "bare 429")
        near(ts, now + 3 * 3600, "the lane's own reset")
    check("control · …and with no stated time in the SAME 429, the "
          "exhausted lane is what answers", _exhausted_answers_a_bare_429)


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

    def _relative_and_clock_forms() -> None:
        # coordinator review 16:01Z: a trusted RELATIVE duration and a bare
        # CLOCK are message times too. Until then `_parse_limit_reset_ts`
        # clipped both to the inferred/named lane, so "try again in 19
        # hours" on a session wall was parsed and discarded and the freeze
        # took the cache's 3 h. Through the REAL stamp path, cache disagreeing.
        readout(("session", cache_reset, True, None))
        # relative, session-named
        slug, nid = rig.probe_org()
        rig.set_mode("iserror", limit_text="You've hit your session limit. "
                                           "Try again in 19 hours.")
        rig.run_turn(slug, nid, "go")
        fz = rig.node(slug, nid).get("frozen") or {}
        fixture(fz.get("limit") is True, f"the rig did not freeze: {fz}")
        near(fz.get("until_ts"), time.time() + 19 * 3600,
             "19 hours, as the message said — not the cache's 3 h", tol=30.0)
        eq((fz.get("reset_src"), fz.get("schedule_kind")),
           ("text", "observed-deadline"), "provenance")
        # clock, unnamed, ~23 h ahead (the named hour is 1 h in the past, so
        # it rolls to tomorrow — the live-caught 2026-08-18 shape, now the
        # message's own answer)
        at = dt.datetime.fromtimestamp(time.time() - 3600).replace(
            second=0, microsecond=0)
        clock = at.strftime("%I:%M%p").lstrip("0").lower()
        slug, nid = rig.probe_org()
        rig.set_mode("iserror", limit_text=f"You've hit your limit · resets {clock}")
        rig.run_turn(slug, nid, "go")
        fz = rig.node(slug, nid).get("frozen") or {}
        fixture(fz.get("limit") is True, f"the rig did not freeze: {fz}")
        near(fz.get("until_ts"), (at + dt.timedelta(days=1)).timestamp(),
             "tomorrow at that clock, as the message said", tol=90.0)
        eq(fz.get("reset_src"), "text", "provenance")
    check("a trusted 'try again in 19 hours' and a bare clock ~23 h out are "
          "stamped as said, not clipped to the lane and handed to the cache",
          _relative_and_clock_forms)

    def _untrusted_stays_banded() -> None:
        # the SAME wordings from the agent's own answer: banded to the
        # session lane, so the 19-hour claim is refused and the untrusted
        # freeze gets the cache/floor path (redteam 2026-08-18 safeguard)
        eq(supervisor._parse_limit_reset_ts(
            "You've hit your session limit. Try again in 19 hours.", None,
            trusted=False), None, "untrusted relative 19 h is banded")
        eq(supervisor._parse_limit_reset_ts(
            "usage limit reached — try again in 2 hours", None,
            trusted=False) is not None, True,
           "…while an untrusted 2 h is inside the band")
        eq(supervisor._parse_limit_reset_ts(
            "try again in 9 days", "weekly_all"), None,
           "a trusted relative past the global bound is still refused")
    check("control · untrusted relative/clock wordings keep the lane band; "
          "the global bound holds for trusted ones", _untrusted_stays_banded)


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

    def _exhausted_lane_moves_a_rate_limit_probe() -> None:
        # issue #4: a probe-floor freeze stamped from a BARE 429 (no lane
        # word at all — `reset_for` would refuse it) moves once the readout
        # reports the session lane MEASURED SPENT
        readout(("session", 3 * 3600, True, None))
        limits._cache["data"]["limits"][0]["percent"] = 100
        floor = now + supervisor.PROBE_FLOOR
        slug, nid = _seed("probe", "probe", floor)
        rate_blob = ("API Error: 429 rate_limit_error: per-minute rate "
                    "limit")
        moved = supervisor._refresh_freeze_reset(
            slug, nid, rate_blob, floor, None, subscription=True,
            trusted=True, tier="haiku", stamped_kind="probe")
        eq(moved, True, "the pass did nothing on a probe-floor 429 freeze")
        fz = rig.node(slug, nid)["frozen"]
        near(fz["until_ts"], now + 3 * 3600, "the exhausted session lane",
             tol=30.0)
        eq(fz["reset_src"], "usage:exhausted:session", "provenance")
        eq(fz["schedule_kind"], "probe", "still an inference, not an "
                                         "observed deadline")
    check("issue #4 · the correction pass moves a rate-limit probe to an "
          "exhausted lane the same way it moves an ordinary one",
          _exhausted_lane_moves_a_rate_limit_probe)

    def _relative_kept() -> None:
        # the correction pass on a freeze stamped from "try again in 19
        # hours": the re-read cache (session 3 h, active) must not move it
        rel = "You've hit your session limit. Try again in 19 hours."
        ts0 = now + 19 * 3600
        slug, nid = _seed("text", "observed-deadline", ts0)
        moved = supervisor._refresh_freeze_reset(
            slug, nid, rel, ts0, None, subscription=True, trusted=True,
            tier="haiku", stamped_kind="observed-deadline")
        eq(moved, False, "the pass rewrote a relative-stamped freeze")
        fz = rig.node(slug, nid)["frozen"]
        near(fz["until_ts"], ts0, "until_ts", tol=5.0)
    check("a freeze stamped from a relative duration is not rewritten by the "
          "correction pass either", _relative_kept)


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
        # an exhausted notification WITHOUT a reset is "no time in the
        # message": the matching pool's cached reset answers (coordinator
        # correction 4), and says it came from the board
        blank = {"codex": {"limitName": "", "rateLimitReachedType": "usage",
                           "primary": {"usedPercent": 100}}}
        eq(codex_route.failure_deadline(direct, board, blank, plan, now=now),
           (now + 3 * 3600, "observed-deadline", codex_route.SRC_BOARD),
           "no stated reset → the board for the same pool")
        # …and with no board either, the floor (probe)
        eq(codex_route.failure_deadline(direct, {"available": False}, blank,
                                        plan, now=now),
           (None, "probe", ""), "nothing anywhere → probe")
        # …and the board for ANOTHER pool never answers this pool
        other = {**board, "limits": [window(codex_route.RESERVE_MODEL, 100,
                                            iso(now + 3 * 3600))]}
        eq(codex_route.failure_deadline(direct, other, blank, plan, now=now),
           (None, "probe", ""), "the reserve board is not plan's reset")
    check("control · the board answers only when the notification carries "
          "no reset — for the same pool, with its own provenance",
          _board_when_absent)

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
        # M7 (redteam on f9e2ac4): the SAME pool's board EARLIER than its own
        # stated reset — the one direction where "message first" is
        # load-bearing per pool. Reserve states 4 h; the board says reserve
        # 1 h and plan 6 h. The stated 4 h must win for reserve (the board's
        # 1 h for the same pool is not offered), and the answer is reserve's
        # stated value, not plan's 6 h.
        earlier = {**both, "limits": [
            window(None, 100, iso(now + 6 * 3600)),
            window(codex_route.RESERVE_MODEL, 100, iso(now + 3600))]}
        ts, kind, src = codex_route.failure_deadline(
            luna, earlier, snaps, codex_route.RESERVE_POOL, now=now)
        eq((ts, kind, src), (now + 4 * 3600, "probe",
                             codex_route.SRC_NOTIFICATION),
           "a pool's stated reset is not undercut by its OWN earlier board")
        # …and the single-route shape of the same case
        eq(codex_route.failure_deadline(
            direct, {**board, "limits": [window(None, 100, iso(now + 600))]},
            snapshots, plan, now=now),
           (now + 3600, "observed-deadline", codex_route.SRC_NOTIFICATION),
           "single pool: stated 1 h over its own board's 10 min")
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
        # an UNKNOWN provenance manufactures neither attribution: prose if it
        # parses, else the floor — never "provider", never "usage:board"
        # (coordinator review 16:24Z; redteam robustness note)
        ts, src = supervisor._provider_limit_until(
            prose, now + 3 * 3600, now, reset_from="mystery")
        eq(src, "text", "unknown provenance: the prose answers")
        near(ts, now + 20 * 60, "…the prose's 20 minutes")
        eq(supervisor._provider_limit_until(
            "usage limit reached", now + 3 * 3600, now, reset_from="mystery"),
           (now + supervisor.PROBE_FLOOR, "probe"),
           "unknown provenance, no prose: the floor, not the board")
        eq(supervisor._provider_limit_until(
            "usage limit reached", now + 3 * 3600, now, reset_from=""),
           (now + supervisor.PROBE_FLOOR, "probe"), "empty provenance too")
        # …and the default is still the provider's own statement
        eq(supervisor._provider_limit_until("usage limit reached",
                                            now + 3600, now),
           (now + 3600, "provider"), "default = message")
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
        # without a message deadline, capacity available elsewhere still
        # says so (unchanged)
        n = node_with("usage:session", "probe", stated)
        api._rederive_freeze_reset(
            n, {"haiku": {"account": "primary", "available": True,
                          "refresh_at": None}})
        eq(n["frozen"]["until_ts"], None,
           "no message deadline + capacity available → the roster's word")
    check("control · without an applicable message deadline the roster "
          "answers, as before", _roster_when_absent)

    def _available_does_not_erase() -> None:
        # coordinator correction 2: a future message deadline SURVIVES a
        # resolver that reports capacity available (another account, an
        # unmarked fallback) — routing is a separate fact and must not erase
        # the stated time
        for src in ("text", "provider"):
            n = node_with(src, "observed-deadline", stated)
            api._rederive_freeze_reset(
                n, {"haiku": {"account": "primary", "available": True,
                              "refresh_at": None}})
            near(n["frozen"]["until_ts"], stated,
                 f"{src}: the stated time survives 'available'")
            if not str(n["frozen"]["until"]).startswith("capacity resets "):
                raise AssertionError(n["frozen"]["until"])
    check("a text/provider deadline is not erased by a roster that says "
          "capacity is available elsewhere", _available_does_not_erase)


# ══════════════════════════════════════════════════════════════════════ §7

def sec_luna_two_legs() -> None:
    print("\n§7 the Luna double rejection takes each leg's stated reset (G6)")
    now = time.time()
    R, P = codex_route.RESERVE_POOL, codex_route.PLAN_POOL

    def route(pool: str) -> codex_route.Route:
        return codex_route._route_for(  # pyright: ignore[reportPrivateUsage]
            "luna", pool, "acct-A", reason="test", evidence="synthetic")

    def rejection(pool: str, other: str, *, stated: float | None,
                  cached: float | None) -> supervisor._CodexRouteRejected:
        """A terminal usage rejection on `pool`, as `_codex_leg_attempt`
        raises it: `stated` = this leg's own notification reset (None = the
        notification carried none); `cached` = what the board said."""
        ev = {"snap_exhausted": stated is not None, "snap_reset": stated,
              "board_fresh": cached is not None, "board_complete": True,
              "cap_state": "exhausted" if cached is not None else "usable",
              "cap_reset": cached, "served": pool, "code": "usage_limit",
              "usage_prose": False}
        cls = {"kind": codex_route.KIND_USAGE_LIMIT, "code": "usage_limit",
               "rejected": True, "attributed": pool, "redrive": True,
               "pool_state": "exhausted", "why": f"{pool} exhausted (test)",
               "reset_ts": stated if stated is not None else cached}
        return supervisor._CodexRouteRejected(
            route(pool), cls, "usage limit", "sid-test", route(other),
            evidence=ev)

    def drive(first: supervisor._CodexRouteRejected,
              second: supervisor._CodexRouteRejected,
              board_resets: dict[str, float]) -> supervisor._ProviderTurnFailed:
        """Run the REAL `_codex_leg` with both attempts rejecting, and a
        cached board that says `board_resets` per pool."""
        slug, nid = rig.probe_org()
        org = store.load_org(slug)
        legs = iter((first, second))

        def attempt(*a, **k):
            raise next(legs)

        def board():
            return {"available": True, "account": "acct-A", "age": 0.0,
                    "stale": False, "complete": True, "limits": [
                        {"model": (codex_route.RESERVE_MODEL if p == R
                                   else None),
                         "percent": 100.0, "is_active": True,
                         "resets_at": codex_route._iso(t),  # pyright: ignore[reportPrivateUsage]
                         "observed_at": now}
                        for p, t in board_resets.items()]}
        saved = (supervisor._codex_leg_attempt, supervisor.codex_limits.snapshot)
        supervisor._codex_leg_attempt = attempt
        supervisor.codex_limits.snapshot = board
        try:
            supervisor._codex_leg(slug, nid, org, {}, "go", [])
        except supervisor._ProviderTurnFailed as e:
            return e
        finally:
            supervisor._codex_leg_attempt, supervisor.codex_limits.snapshot = saved
        raise AssertionError("both legs rejected and nothing was raised")

    def _stated_over_board() -> None:
        # both legs STATE a reset (reserve 4 h, plan 2 h); the cached board
        # says 6 h and 8 h. Until 2026-09-07 this path woke off the board
        # alone (snapshots were not passed): 6 h. Now: the earliest stated
        # reset, plan's 2 h, and the freeze knows it is a message value.
        e = drive(rejection(R, P, stated=now + 4 * 3600, cached=now + 8 * 3600),
                  rejection(P, R, stated=now + 2 * 3600, cached=now + 6 * 3600),
                  {R: now + 8 * 3600, P: now + 6 * 3600})
        near(e.reset_ts, now + 2 * 3600, "plan's STATED reset, the earliest")
        eq((e.reset_from, e.schedule_kind), ("message", "probe"),
           "a stated value, and a probe (both pools are out)")
    check("both legs state a reset: the earliest stated one wins over a "
          "later cached board", _stated_over_board)

    def _per_pool() -> None:
        # reserve's notification states 4 h; plan's carried NO reset but the
        # board holds 2 h for plan: per pool message-first, then the
        # earliest — plan's cached 2 h, marked as a board value
        e = drive(rejection(R, P, stated=now + 4 * 3600, cached=None),
                  rejection(P, R, stated=None, cached=now + 2 * 3600),
                  {R: now + 4 * 3600, P: now + 2 * 3600})
        near(e.reset_ts, now + 2 * 3600, "plan's cached reset (no stated one)")
        eq(e.reset_from, "board", "…and it says so")
        # the other way round: reserve stated 2 h, plan cached 4 h → stated
        e = drive(rejection(R, P, stated=now + 2 * 3600, cached=None),
                  rejection(P, R, stated=None, cached=now + 4 * 3600),
                  {R: now + 2 * 3600, P: now + 4 * 3600})
        near(e.reset_ts, now + 2 * 3600, "reserve's stated reset")
        eq(e.reset_from, "message", "…a message value")
    check("a pool whose notification carried no time takes its OWN cached "
          "reset; the earliest pool wins with its own provenance", _per_pool)

    def _control_board_only() -> None:
        # neither leg stated or cached anything: the board fallback in the
        # handler answers (the path is live), else the floor
        e = drive(rejection(R, P, stated=None, cached=None),
                  rejection(P, R, stated=None, cached=None),
                  {R: now + 5 * 3600, P: now + 3 * 3600})
        near(e.reset_ts, now + 3 * 3600, "the board's earliest pool")
        eq(e.reset_from, "board", "a board value")
        e = drive(rejection(R, P, stated=None, cached=None),
                  rejection(P, R, stated=None, cached=None), {})
        eq(e.reset_ts, None, "nothing anywhere → the freeze takes its floor")
    check("control · with nothing stated the handler's board fallback answers, "
          "and with nothing at all the floor", _control_board_only)


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
    sec_luna_two_legs()
    print(f"\n{'═' * 70}\n{PASS} checks passed, {len(FAIL)} failed")
    for label, tb in FAIL:
        print(f"\nFAIL  {label}\n{tb}")
    for m in NOTES:
        print(f"  · {m}")
    print(f"ALL {PASS} CHECKS PASS" if not FAIL else f"{len(FAIL)} FAILED")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
