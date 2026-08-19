"""Turn-lifecycle suite — the supervisor's stateful core, attacked adversarially.

    Mail is AT-LEAST-ONCE. A message the user (or an agent) posted is, at
    every instant and after every crash, still carried by SOMETHING that will
    deliver it: the mailbox, the delivery journal, an in-flight turn record, a
    freeze's resume text, or the agent's own transcript. Duplicates are the
    price and are fine. Losses are not.

Run:  .venv/Scripts/python.exe backend/tests/test_turn_lifecycle.py
      --quick        one repetition / a short sweep per configuration
      --only <sub>   only checks whose label contains <sub>
      --hermetic     skip the live half entirely (seconds, no processes)
      --port N       bind the throwaway backend here (default 7401)
      --keep         keep the rig directory and print its path

WHAT IT DOES
------------
Two halves, same file, same style as `test_ledger.py` (plain asserts, `ok N`
lines, no pytest — still not installed).

**Hermetic** drives the journal primitives directly against real org docs on
disk: `_envelope` → `_journal_drain` → `_confirm_delivered` /
`_fold_back_undelivered` / `pop_steer`, plus the pure predicates
(`_looks_like_usage_limit`, `_parse_limit_reset_ts`, `_ensure_frozen`). These
are the pieces every crash path lands on, and they are cheap enough to sweep
exhaustively.

**Live** runs a real uvicorn backend on its own port and data dir with a fake
CLI, and then kills things: the backend at seven points across one turn, the
CLI at five, the turn by timeout, the turn by interrupt. After every death the
suite asks the same question — WHERE IS THE MAIL — and refuses to accept
"nowhere".

THE CLI STAND-IN
----------------
`fakecli.js` (the substitution `ORGTREE_CLAUDE_CLI` already allows) does the
timing dials. Three turn shapes it has no dial for are added by a WRAPPER
generated into the rig dir at runtime (`wrapcli.js`) which delegates to
`fakecli.js` for everything else — so fakecli.js itself is untouched:

  json        `--output-format json` (the compaction fork and the /context
              one-shot) — fakecli only speaks stream-json, so `_compact_split`
              could never succeed against it
  die         exit before reading stdin at all (the "child dies on argv"
              case C1 exists for)
  errresult   consume the message, write NOTHING to the transcript, and answer
              with an is_error `result` — the shape that turned out to lose
              mail (§ defect 1)

Nothing here touches the user's data: its own ORGTREE_DATA, its own HOME, its
own port (7401 by default), every org deleted at the end.
"""

from __future__ import annotations

import glob
import io
import json
import os
import re
import time as _time
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
sys.path.insert(0, os.path.join(_REPO, "backend"))

QUICK = "--quick" in sys.argv
ONLY = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else ""
HERMETIC_ONLY = "--hermetic" in sys.argv
KEEP = "--keep" in sys.argv
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 7401

PASS = 0
FAIL: list[tuple[str, str]] = []
#: measured behaviours that break an invariant for a reason outside this
#: suite's remit — reported loudly, never silently tolerated
EXCEPTIONS: list[tuple[str, str]] = []
NOTES: list[str] = []


def check(label: str, fn) -> None:
    global PASS
    if ONLY and ONLY not in label:
        return
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


def note(msg: str) -> None:
    NOTES.append(msg)
    print(f"       · {msg}")


def token() -> str:
    return "TL" + os.urandom(5).hex()


# ============================================================ the rig (paths)

TMP = tempfile.mkdtemp(prefix="orgtree-turnlife-")
HDATA = os.path.join(TMP, "hermetic")     # the in-process half's data root
DATA = os.path.join(TMP, "data")          # the live backend's data root
HOME = os.path.join(TMP, "home")          # transcripts land here

# ⚠ THE MAIL HUB IS NOT ISOLATED BY ORGTREE_DATA (user report 2026-08-06:
# "hundreds of disconnected orgs … crowding the connected client list").
# Every org this rig creates is born with a `local` hub entry at
# net.DEFAULT_HUB_ADDRESS — 127.0.0.1:7370, the user's REAL hub — and the
# backend's net daemon registers it there. A fresh identity per run means one
# new roster row per fixture per run, kept for 30 days, unregisterable.
# `net_autoconnect` cannot be turned off from here (orgs_create reads it from
# the request body, default True); `net_hub_address` CAN — defaults.json is
# read out of THIS data root — so the local entry is pointed at a dead port
# and registration fails harmlessly into the backoff.
# Same spirit as ORGTREE_BRIDGE_PORT=0 below: never touch anything the user's
# real install owns.
DEAD_HUB = "http://127.0.0.1:9"     # discard port: refuses instantly
os.makedirs(DATA, exist_ok=True)
with io.open(os.path.join(DATA, "defaults.json"), "w", encoding="utf-8") as _f:
    json.dump({"net_hub_address": DEAD_HUB}, _f)

CFG = os.path.join(TMP, "fakecli.json")
WRAP = os.path.join(TMP, "wrapcli.js")
LOG = os.path.join(TMP, "backend.log")
for d in (HDATA, DATA, HOME):
    os.makedirs(d, exist_ok=True)

os.environ["ORGTREE_DATA"] = HDATA        # BEFORE importing orgtree (store
                                          # resolves it at import time)

from orgtree import store, supervisor
from orgtree import api as _apimod                            # noqa: E402
from orgtree.ledger import USER, Org                             # noqa: E402


# =========================================================== hermetic helpers

def hspec(**over):
    s = dict(add_dirs=[], tools={"bash": True, "web": False, "edit": False,
                                 "subagents": False, "mcp": []},
             org_visibility="team", charter="test hire")
    s.update(over)
    return s


_hn = [0]


def fixture(ok, msg) -> None:
    """A PRECONDITION inside a gap body — raised as a RuntimeError so `gap`
    below re-reports it as a broken check instead of swallowing it as the
    finding.

    ⚠ Learned the expensive way (2026-08-06, test_batched_asks). A gap
    body's whole contract is "this assert fails", so a fixture assert and the
    assert that measures the defect are indistinguishable: gap() catches the
    first AssertionError it meets and files it as the finding. A credit
    request for 8 against a grant of 20 took the at-or-below no-op branch, so
    no row ever existed — the gap fired on its own scaffolding while the
    defect it named was real but unexercised. Use fixture(...) for every setup
    precondition in a gap body; keep a bare `assert` for the property under
    test."""
    if not ok:
        raise RuntimeError(f"fixture: {msg}")


GAPS: list[tuple[str, str, str]] = []


def gap(label, why, fn) -> None:
    """SHOULD hold, currently does not — asserts the SAFE property, is expected
    to FAIL today, keeps the suite green, and turns RED the day it is fixed.

    ⚠ Set preconditions with `fixture(...)`, never a bare assert — see there."""
    global PASS
    try:
        fn()
    except AssertionError as e:
        GAPS.append((label, why, str(e).split("\n")[0][:300]))
        print(f"  ⚑ GAP    {label}")
        return
    except Exception:                                            # noqa: BLE001
        FAIL.append((label + " (gap check errored)", traceback.format_exc()))
        print(f"  FAIL     {label} — the gap check itself broke")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}  ← FIXED: promote this out of gap()")


def horg(nodes: int = 1) -> tuple[str, list[str]]:
    """A saved org with N top-level nodes, in the hermetic data root."""
    _hn[0] += 1
    org = store.create_org(f"zz herm {_hn[0]}")
    ids = []
    for i in range(nodes):
        r = org.hire(USER, None, "haiku", 2, f"a{i}", **hspec())
        ids.append(r["node"])
    store.save_org(org)
    return org.d["slug"], ids


def hpost(slug: str, nid: str, body: str, sender: str = USER) -> str:
    with store.DOC_LOCK:
        org = store.load_org(slug)
        r = org.post_mail(sender, nid, body)
        store.save_org(org)
    return r["id"]


def hbox(slug: str, nid: str) -> list[str]:
    d = store.load_org(slug).d
    return [m["body"] for m in (d.get("mail") or {}).get(nid, [])]


def hjournal(slug: str, nid: str) -> list[dict]:
    return list((store.load_org(slug).d.get("delivering") or {}).get(nid, []))


# ============================================================ hermetic checks

def hermetic() -> None:
    print("\nusage-limit detection (the phrasing has silently changed before):")

    # Phrasings actually seen from the CLI/API, including the two the
    # supervisor's own comments cite as having silently broken the freeze
    # machinery when the wording changed under it.
    LIMIT_YES = [
        "Claude AI usage limit reached|1753898400",
        "You've hit your usage limit. Your limit will reset at 3pm.",
        "You've hit your session limit — resets 1:40pm",
        "Weekly limit reached. Try again in 3 hours.",
        "5-hour limit reached ∙ resets 9pm",
        "usage limit exceeded",
        "You have reached your limit for Opus. Try again at 11:30pm.",
        "Error: rate limit exceeded, resets in 42 minutes",
    ]
    LIMIT_NO = [
        "API Error: 500 Internal Server Error",
        "Error: connection reset by peer",
        "No conversation found with session ID abc",
        "Credit balance is too low",
        "permission denied writing /etc/hosts",
        "TypeError: cannot read property 'x' of undefined",
        "input length and max_tokens exceed context limit: 205000 > 200000",
        "",
    ]
    for blob in LIMIT_YES:
        check(f"limit-detect · YES {blob[:44]!r}",
              lambda b=blob: (_ for _ in ()).throw(AssertionError(b))
              if not supervisor._looks_like_usage_limit(b) else None)
    for blob in LIMIT_NO:
        check(f"limit-detect · no  {blob[:44]!r}",
              lambda b=blob: (_ for _ in ()).throw(AssertionError(b))
              if supervisor._looks_like_usage_limit(b) else None)

    # CHARACTERISATION, not endorsement: the predicate is `"limit" in blob AND
    # one of {usage, weekly, reached, exceeded, quota, hit your, resets,
    # session}`. Both halves of that shape have a sharp edge, and pinning them
    # here is what makes a future change to the word list visible.
    check("limit-detect · characterised: 'quota exceeded' WITHOUT the word "
          "'limit' is NOT detected", lambda: (
              None if not supervisor._looks_like_usage_limit(
                  "Your quota has been exceeded for this model")
              else (_ for _ in ()).throw(AssertionError(
                  "the guard changed — quota/exceeded now fire on their own"))))
    check("limit-detect · characterised: any 'limit' + 'exceeded' error reads "
          "as a usage limit", lambda: (
              None if supervisor._looks_like_usage_limit(
                  "The request body exceeded the maximum size limit of 32 MB")
              else (_ for _ in ()).throw(AssertionError(
                  "narrowed — re-check the freeze path's false positives"))))
    note("_looks_like_usage_limit is 'limit' AND a keyword: 'quota exceeded' "
         "with no 'limit' does not freeze, and any 'limit'+'exceeded' error "
         "does. No measured occurrence of either — left as-is, reported.")

    print("\nreset-time parsing (what auto_resume schedules on):")

    def _ts(blob, lo, hi):
        v = supervisor._parse_limit_reset_ts(blob)
        assert v is not None, f"no ts from {blob!r}"
        assert lo <= v <= hi, f"{blob!r} → {v} not in [{lo}, {hi}]"

    # the epoch the CLI prints is a FUTURE one; a stamp already behind us is
    # not a horizon, and since the 2026-08-18 banding it is declined so the
    # caller can ask the usage readout instead of waking into the same wall
    _future = int(time.time()) + 3 * 3600
    check("reset-ts · epoch marker wins", lambda: _ts(
        f"Claude AI usage limit reached|{_future}", _future, _future))
    check("reset-ts · a past epoch is declined", lambda: (
        None if supervisor._parse_limit_reset_ts(
            "Claude AI usage limit reached|1753898400") is None
        else (_ for _ in ()).throw(AssertionError("believed a stale epoch"))))
    check("reset-ts · an absurd epoch is declined (money: it would price "
          "an api_fallback window in the fifth millennium)", lambda: (
        None if supervisor._parse_limit_reset_ts(
            "Claude AI usage limit reached|99999999999") is None
        else (_ for _ in ()).throw(AssertionError("believed a junk epoch"))))
    # a bare clock time carries no date, so "resets 1:40pm" with 1:40pm
    # already past rolls to tomorrow — 23 hours out. Legal for a weekly lane,
    # impossible for a 5-hour session lane, and believing it priced a 23-hour
    # key-billing window (live-caught 2026-08-18). Pinned against a `now`
    # placed 23 h before whatever the roll produced, so the real clock at test
    # time cannot decide the outcome.
    _clockts, _how = supervisor._parse_limit_reset_ts_raw("resets 1:40pm")
    _now23 = _clockts - 23 * 3600
    check("reset-ts · the clock form is reported as a guess", lambda: (
        None if _how == "clock"
        else (_ for _ in ()).throw(AssertionError(_how))))
    check("reset-ts · 23 h out is refused for a 5-hour session lane", lambda: (
        None if supervisor._parse_limit_reset_ts(
            "resets 1:40pm", "session", now=_now23) is None
        else (_ for _ in ()).throw(AssertionError("a 5-hour lane, 23 h out"))))
    check("reset-ts · the same guess stands for a weekly lane", lambda: (
        None if supervisor._parse_limit_reset_ts(
            "resets 1:40pm", "weekly_all", now=_now23) == _clockts
        else (_ for _ in ()).throw(AssertionError("weekly lane refused"))))
    check("reset-ts · 'try again in 2 hours' ≈ now+7200", lambda: _ts(
        "Try again in 2 hours", time.time() + 7100, time.time() + 7300))
    # a clock time is a GUESS (it carries no date), so with no lane named it
    # is banded by the SHORTEST lane — user ruling 2026-08-18. Inside that
    # reach it stands; past it the account's usage readout is asked instead.
    _soon = (time.strftime("%I:%M%p", time.localtime(time.time() + 3600))
             .lstrip("0").lower())
    check("reset-ts · a clock time inside the session lane lands in the "
          "future", lambda: _ts(
        "resets " + _soon, time.time(), time.time() + 6 * 3600 + 60))
    check("reset-ts · …and one 23 h out is refused when no lane is named "
          "(the shortest lane is the default)", lambda: (
        None if supervisor._parse_limit_reset_ts(
            "resets 1:40pm", None,
            now=supervisor._parse_limit_reset_ts_raw("resets 1:40pm")[0]
            - 23 * 3600) is None
        else (_ for _ in ()).throw(AssertionError("23 h on an unnamed lane"))))
    check("reset-ts · no time at all → None", lambda: (
        None if supervisor._parse_limit_reset_ts("usage limit reached") is None
        else (_ for _ in ()).throw(AssertionError("invented a time"))))

    print("\nthe delivery journal (drain → confirm / fold back):")

    slug, (nid,) = horg()
    t1, t2 = token(), token()
    hpost(slug, nid, f"first {t1}")
    hpost(slug, nid, f"second {t2}")
    txt, tok = supervisor._envelope(slug, nid, "nudge", via="turn")
    check("journal · drain empties the mailbox and journals one batch", lambda: (
        None if hbox(slug, nid) == [] and len(hjournal(slug, nid)) == 1
        and len(hjournal(slug, nid)[0]["mail"]) == 2
        else (_ for _ in ()).throw(AssertionError(hjournal(slug, nid)))))
    check("journal · both bodies ride the enveloped text", lambda: (
        None if t1 in txt and t2 in txt and "[MAIL" in txt
        else (_ for _ in ()).throw(AssertionError(txt[:200]))))
    check("journal · via='turn' is recorded (it decides the UI carrier)", lambda: (
        None if hjournal(slug, nid)[0]["via"] == "turn"
        else (_ for _ in ()).throw(AssertionError(hjournal(slug, nid)))))
    check("journal · a second envelope on the same node finds nothing", lambda: (
        lambda r: None if r[1] is None and r[0] == "again"
        else (_ for _ in ()).throw(AssertionError(r))
    )(supervisor._envelope(slug, nid, "again", via="turn")))
    check("journal · fold-back restores BOTH, in order, ahead of new mail",
          lambda: (
              hpost(slug, nid, f"later {token()}"),
              supervisor._fold_back_undelivered(slug, nid),
              None if [b[:5] for b in hbox(slug, nid)][:2] == ["first", "secon"]
              and len(hbox(slug, nid)) == 3
              else (_ for _ in ()).throw(AssertionError(hbox(slug, nid))))[-1])
    check("journal · fold-back clears the journal entry", lambda: (
        None if hjournal(slug, nid) == []
        else (_ for _ in ()).throw(AssertionError(hjournal(slug, nid)))))

    slug, (nid,) = horg()
    hpost(slug, nid, f"a {token()}")
    _t, tokA = supervisor._envelope(slug, nid, "n", via="turn")
    hpost(slug, nid, f"b {token()}")
    _t, tokB = supervisor._envelope(slug, nid, "n", via="turn")
    check("journal · two batches queue in drain order", lambda: (
        None if [b["tok"] for b in hjournal(slug, nid)] == [tokA, tokB]
        else (_ for _ in ()).throw(AssertionError(hjournal(slug, nid)))))
    check("journal · confirm drops exactly one batch", lambda: (
        supervisor._confirm_delivered(slug, nid, [tokA]),
        None if [b["tok"] for b in hjournal(slug, nid)] == [tokB]
        else (_ for _ in ()).throw(AssertionError(hjournal(slug, nid))))[-1])
    check("journal · confirming an unknown token is a no-op", lambda: (
        supervisor._confirm_delivered(slug, nid, ["deadbeef"]),
        None if [b["tok"] for b in hjournal(slug, nid)] == [tokB]
        else (_ for _ in ()).throw(AssertionError(hjournal(slug, nid))))[-1])
    check("journal · keep_toks leaves a still-carried batch journaled", lambda: (
        supervisor._fold_back_undelivered(slug, nid, keep_toks=[tokB]),
        None if [b["tok"] for b in hjournal(slug, nid)] == [tokB]
        and hbox(slug, nid) == []
        else (_ for _ in ()).throw(AssertionError(hjournal(slug, nid))))[-1])
    check("journal · fold-back with no keep restores it", lambda: (
        supervisor._fold_back_undelivered(slug, nid),
        None if len(hbox(slug, nid)) == 1 and hjournal(slug, nid) == []
        else (_ for _ in ()).throw(AssertionError(hbox(slug, nid))))[-1])

    # notices ride the same journal and must survive the same way
    slug, (nid,) = horg()
    with store.DOC_LOCK:
        org = store.load_org(slug)
        org._notify([nid], "org changed under you")
        store.save_org(org)
    txt, tok = supervisor._envelope(slug, nid, "n", via="turn")
    check("journal · notices drain into the batch and fold back", lambda: (
        None if "ORG NOTICES" in txt
        and len(hjournal(slug, nid)[0]["notices"]) == 1
        else (_ for _ in ()).throw(AssertionError(txt[:120]))))
    check("journal · folded-back notices land back in the notices box", lambda: (
        supervisor._fold_back_undelivered(slug, nid),
        None if len((store.load_org(slug).d.get("notices") or {}).get(nid)) == 1
        else (_ for _ in ()).throw(AssertionError(
            store.load_org(slug).d.get("notices"))))[-1])

    print("\ndelivering_mail — the in-flight carrier the desk renders:")
    slug, (nid,) = horg()
    tk = token()
    hpost(slug, nid, f"body {tk}")
    _t, _tok = supervisor._envelope(slug, nid, "n", via="turn")
    org = store.load_org(slug)
    check("delivering · surfaced with no evidence test", lambda: (
        None if len(supervisor.delivering_mail(org, nid)) == 1
        else (_ for _ in ()).throw(AssertionError())))
    check("delivering · retired when the transcript is shown to carry it",
          lambda: (
              None if supervisor.delivering_mail(org, nid, lambda m: True) == []
              else (_ for _ in ()).throw(AssertionError())))
    check("delivering · via='turn' rides out to the caller", lambda: (
        None if supervisor.delivering_mail(org, nid)[0].get("via") == "turn"
        else (_ for _ in ()).throw(AssertionError())))

    # ---- USER REPORT 2026-08-06 (after the FABLE-1/2 fix) -----------------
    # "already-halted fable agents don't seem to be unfreezable; sending them
    # a message doesn't do anything."
    #
    # Reproduced end to end against a PRE-FIX lock shape (fable_lock with no
    # until_ts, which is what every lock written before d40dd82 looks like on
    # disk). Every route a user would try, in the order they would try it:
    #   1. a fresh load (the timed release d40dd82 added) → still locked
    #   2. sending it a message                           → "accepted", nothing happens
    #   3. the ▶ resume button                            → returns [], still locked
    #   4. the auto-resume timer                          → nothing to schedule on
    #   5. clear_fable_lock (settings ⚙)                  → releases
    # So the fix is correct and forward-only: it carries until_ts onto NEW
    # locks and leaves every lock already on disk timeless, which the load
    # hook deliberately does not touch ("A TIMELESS lock still waits for
    # clear_fable_lock").
    def _a_pre_fix_lock_releases_itself_like_a_new_one():
        org = store.create_org("zz stuck lock")
        org.hire(USER, None, "fable", 20, "f1", **hspec())
        store.save_org(org)
        org.fable_limit_hit("f1", "weekly limit reached",
                            until_ts=_time.time() - 1)
        org.d["fable_lock"].pop("until_ts", None)   # the pre-d40dd82 shape
        store.save_org(org)
        # ⚠ guard on the RAW FILE, not a loaded Org: since the STUCK-1
        # migration `store.load_org` is the very thing that releases a
        # timeless lock, so loading to check the precondition would consume
        # the state under test and the check would pass vacuously.
        raw = json.load(open(os.path.join(
            os.environ["ORGTREE_DATA"], "orgs", org.d["slug"] + ".json"),
            encoding="utf-8"))
        fixture(bool((raw.get("fable_lock") or {}))
                and not (raw.get("fable_lock") or {}).get("until_ts")
                and bool(raw["nodes"]["f1"].get("limit_locked")),
                f"the pre-fix shape was not written to disk: "
                f"{raw.get('fable_lock')}")
        back = store.load_org(org.d["slug"])
        assert not back.node("f1").get("limit_locked"), (
            "a fable_lock written before d40dd82 carries no until_ts, so the "
            "timed release never fires and the node is halted forever — the "
            "▶ button skips limit_locked by design, the auto-resume timer "
            "has no timestamp to schedule on, and only clear_fable_lock "
            "releases it. Every agent halted before the fix is still halted "
            "after it")
    # ← FIXED (promoted out of gap(), 2026-08-06, same day): the load hook
    # releases TIMELESS locks too — release over back-date, per this
    # finding's own argument (a timeless lock is by construction a pre-fix
    # artifact or the misread itself; since d40dd82 every new lock carries
    # until_ts because the freeze stamps it BEFORE fable_limit_hit runs —
    # if that ordering ever changes, a legitimate lock could be born
    # timeless and this release would fire on it: the invariant is
    # load-bearing).
    check("stuck · a fable lock written BEFORE the timed-release fix still "
          "releases itself (user report 2026-08-06)",
          _a_pre_fix_lock_releases_itself_like_a_new_one)

    # ---- USER RULING 2026-08-06 -------------------------------------------
    # "I should be able to, as the user, manually locate and unstick any agent
    #  frozen for any reason, overriding built-in locks that might prevent
    #  other agents from unsticking it, such as session limits, weekly limits,
    #  or fable specific limits."
    #
    # Today there is no such verb. Every gate has its OWN release and each one
    # refuses on behalf of a mechanism rather than a person:
    #   node.frozen (limit/connection)  → ▶ resume / auto-resume own it
    #   node.frozen (any other kind)    → resume SKIPS it ("that kind's clear")
    #   node.limit_locked               → clear_fable_lock ONLY, and the UI
    #                                     gates that control on tree.fable_lock
    #   org.fable_lock                  → clear_fable_lock (org-level, ⚙ only)
    # A node can hold several at once — neoja's field data has one node with a
    # past-due freeze UNDER a lock — so releasing any single gate is not
    # enough, and the user has no per-node control for any of them.
    #
    # These pin the RULING, not an implementation. They fail until a
    # user-authority unstick exists; the shape is argued in the gap texts.
    def _the_user_can_unstick_a_node_holding_every_gate_at_once():
        org = store.create_org("zz unstick all")
        org.hire(USER, None, "fable", 20, "f1", **hspec())
        store.save_org(org)
        n = org.node("f1")
        fz = supervisor._ensure_frozen(n)
        fz["limit"] = True                    # a usage-limit freeze…
        fz["spend"] = True                    # …plus a kind ▶ refuses to own
        fz["until_ts"] = _time.time() - 3600  # …whose reset has passed
        n["limit_locked"] = True              # …under a fable halt
        # ⚠ the halt needs a LIVE org lock behind it, with a future until_ts:
        # a timeless lock is released at load (STUCK-1) and an ORPHANED
        # limit_locked with no lock behind it is now swept too (the veto
        # finding's fix (a), ledger.py:381). This check is about a node that
        # is genuinely stuck under CURRENT code, not a stale artifact.
        org.d["fable_lock"] = {"at": "2026-08-06T00:00:00.000Z", "policy": "halt",
                               "detail": "weekly limit reached",
                               "until_ts": _time.time() + 3600}
        store.save_org(org)
        slug = org.d["slug"]
        fixture(bool(store.load_org(slug).node("f1").get("frozen"))
                and bool(store.load_org(slug).node("f1").get("limit_locked")),
                "the fixture did not persist a multiply-gated node")
        unstick = getattr(Org, "unstick", None)
        assert callable(unstick), (
            "there is no user-authority unstick verb. Every gate has its own "
            "release, each owned by a mechanism rather than a person: ▶ "
            "resume owns the limit/connection freeze kinds and SKIPS any "
            "other kind and any limit_locked node; clear_fable_lock owns the "
            "halt but is org-level and its control is gated on a lock that "
            "may not be there. A node holding several gates at once — which "
            "the field data shows is the normal stuck shape — cannot be "
            "released by any single existing action")
    gap("unstick · the user can release a node holding every gate at once "
        "(user ruling 2026-08-06)",
        "SHAPE. One verb, `Org.unstick(actor, nid)`, USER AUTHORITY ONLY — "
        "never an agent verb and never reachable from the MCP tool surface. "
        "That restriction is the whole safety story: an agent that could "
        "unstick itself would walk straight through a spend cap, and the "
        "existing gates are exactly the ones written to stop it. The ruling "
        "says the USER overrides them, not that the locks stop being locks.\n"
        "IT MUST CLEAR, in one action: `frozen` regardless of which kind "
        "flags it carries (limit, connection, spend, or a kind added later — "
        "the check must be 'the user said so', not a kind allowlist, or the "
        "next kind reintroduces this bug); `limit_locked`; and the org's "
        "`fable_lock` if the released node was its last holder.\n"
        "IT MUST RECORD, not erase: keep the released record as "
        "`unstuck: {by: USER, at, was: <the freeze>}` so the history stays "
        "honest and a support question six weeks later can still be answered. "
        "The current ▶ deliberately leaves records intact for whoever can "
        "act; an override should leave MORE evidence, not less.\n"
        "REACHABLE FROM THE CARD. The field evidence is unambiguous that an "
        "org-level-only control fails: neoja's user is looking at a frozen "
        "card with no clickable release, and the ⚙ control they were told to "
        "use is gated on a field that is not the one holding their node. The "
        "ruling says 'locate and unstick ANY agent', so the control belongs "
        "where the user finds the agent — on the frozen card — with the "
        "org-wide ▶ kept as the bulk action it already is.",
        _the_user_can_unstick_a_node_holding_every_gate_at_once)

    def _unstick_is_never_an_agent_capability():
        """The constraint that keeps the ruling safe rather than a hole, and
        it holds TODAY (vacuously — there is no verb) and must keep holding
        the moment one lands. The user overriding a spend cap is a decision;
        an AGENT overriding one is the failure mode every gate here exists to
        prevent."""
        unstick = getattr(Org, "unstick", None)
        if unstick is None:
            # nothing to abuse yet — but pin the SURFACE too, so the verb
            # cannot be born reachable from the agent catalogue
            src = open(os.path.join(_REPO, "backend", "orgtree",
                                    "mcptool.py"), encoding="utf-8").read()
            assert "unstick" not in src, (
                "an agent-facing `unstick` appeared in mcptool's catalogue. "
                "The user ruling grants this override to the USER; an agent "
                "that can clear its own spend freeze makes the cap advisory")
            return
        org = store.create_org("zz unstick authority")
        org.hire(USER, None, "opus", 20, "boss", **hspec())
        org.hire("boss", "boss", "fable", 10, "f1", **hspec())
        store.save_org(org)
        try:
            unstick(org, "boss", "f1")            # a superior, not the user
        except Exception as e:                    # noqa: BLE001
            assert "user" in str(e).lower(), (
                f"unstick refused a non-user actor, but not on authority "
                f"grounds: {e}")
            return
        raise AssertionError(
            "an AGENT unstuck another agent — the override must be the "
            "user's alone")
    check("unstick · the override is never an agent capability (holds now, "
          "and pins the agent catalogue so it cannot become one)",
          _unstick_is_never_an_agent_capability)

    def _breadcrumbs_line_reaches_writing_agents_only():
        """FR-24c (user ruling 2026-08-12): agents write their own compaction
        log in realtime — breadcrumbs.md in the working folder — because a
        cheap-compact successor starts with NOTHING and that file is the
        first thing its notice points it at. The line only renders for an
        agent that can actually write (edit or bash); telling a read-only
        seat to keep a file is an instruction it cannot follow."""
        org = store.create_org("zz bc prompt")
        org.hire(USER, None, "haiku", 0, "w")
        store.save_org(org)
        p = supervisor.identity_prompt(org, "w")
        assert "breadcrumbs.md" in p and "realtime" in p, p[-500:]
        org.set_scope(USER, "w", tools={"bash": False, "web": False,
                                        "edit": False, "subagents": False,
                                        "mcp": []})
        store.save_org(org)
        p2 = supervisor.identity_prompt(org, "w")
        assert "breadcrumbs.md" not in p2, (
            "a read-only seat is told to maintain a file it cannot write")
    check("breadcrumbs · the realtime-log doctrine reaches every seat that "
          "can write, and only those", _breadcrumbs_line_reaches_writing_agents_only)

    def _unstick_is_not_reachable_from_the_kiosk_gateway():
        """⚠ THE AUTHORITY IS AIRTIGHT AT THE LEDGER AND OPEN AT THE DOOR.

        `Org.unstick` refuses any actor but the user — I pin that above and it
        holds. But `node_unstick` (api.py) hardcodes `org.unstick(USER, nid)`,
        so authority is decided by WHO CAN REACH THE ENDPOINT, and the public
        gateway is a DENYLIST (`_public_denied`'s `frozen_config`), not an
        allowlist: every /api/orgs/<slug>/… path not explicitly frozen is
        reachable by a kiosk visitor holding only a share token. `/unstick`
        is not on that list. Its docstring says "this endpoint is
        loopback-admin like every other user control" — that is an assertion,
        not an enforcement.

        Measured, not argued: _public_denied returns None (allow) for it,
        while the neighbouring /settings returns 403."""
        denied = _apimod._public_denied(
            "POST", "/api/orgs/demo/nodes/f1/unstick", "demo")
        control = _apimod._public_denied("POST", "/api/orgs/demo/settings", "demo")
        fixture(control is not None,
                "the /settings control is no longer frozen — the denylist "
                "moved and this check is measuring the wrong thing")
        assert denied is not None, (
            "a KIOSK VISITOR can call the user's per-node override. The "
            "handler passes USER as the actor unconditionally, so the "
            "ledger's user-only authority — the whole safety story of this "
            "ruling — is decided entirely by who can reach the door, and the "
            "gateway lets them. A visitor can clear a fable halt and a "
            "usage-limit freeze on any node of the org whose token they hold, "
            "and the same call re-drives the node. (Bounded, and worth "
            "saying: the ORG-level spend_frozen flag is checked separately at "
            "turn start and unstick does not touch it, so this is not a way "
            "past the spend cap itself — it is a way past every OTHER lock "
            "the owner relies on.)")
    # ← FIXED 2026-08-06 under the redteam bug-fix grant (implementer
    # limit-halted): `or rest.endswith("/unstick")` added to
    # frozen_config. The route IS the authority boundary — node_unstick
    # passes USER unconditionally, so Org.unstick's user-only check can
    # say nothing about a request that already arrives wearing the
    # user's name. Promoted out of gap().
    check("public · the user's unstick override is not reachable from "
          "the kiosk gateway",
          _unstick_is_not_reachable_from_the_kiosk_gateway)

    def _a_card_promising_a_reset_can_actually_reset():
        """PEER EVIDENCE 2026-08-06 (neoja, two user screenshots): a stuck
        fable agent's card carries BOTH chips at once —
          ❄ "usage limit · resumes 3pm (Asia/Jerusalem)"   (cards.tsx:841, node.frozen)
          🔒 "limit"                                        (cards.tsx:846, node.limit_locked)
        The first advertises a recovery on a clock. The second silently
        VETOES it: resume_frozen skips any node with limit_locked, and the
        auto-resume timer resumes through the same function. So the user
        reads "resumes 3pm", waits past 3pm, and nothing happens — with no
        per-node control anywhere (the only clear is org-level and gated on
        tree.fable_lock, so it is not even rendered unless a lock survives).

        This is not the timeless-lock case (STUCK-1, fixed): the freeze here
        has a perfectly good until_ts. It is the node FLAG outliving whatever
        set it."""
        org = store.create_org("zz veto")
        org.hire(USER, None, "fable", 20, "f1", **hspec())
        store.save_org(org)
        n = org.node("f1")
        fz = supervisor._ensure_frozen(n)
        fz["limit"] = True
        fz["until"] = "3pm"
        fz["until_ts"] = _time.time() - 3600        # the reset has PASSED
        n["limit_locked"] = True                    # …and the veto is set
        org.d.pop("fable_lock", None)               # nothing org-level to clear
        store.save_org(org)
        slug = org.d["slug"]
        # ⚠ fixture on the RAW FILE (implementer, on promotion): the (a) fix
        # releases an orphaned flag AT LOAD, so loading to check the
        # precondition would consume the state under test — the same trap
        # the STUCK-1 repair named, third appearance today. The disk carries
        # both states; the first load IS the release.
        with open(os.path.join(store.DATA_ROOT, "orgs", slug + ".json"),
                  encoding="utf-8") as f:
            _raw = json.load(f)["nodes"]["f1"]
        fixture(bool(_raw.get("limit_locked")) and bool(_raw.get("frozen")),
                "the fixture did not persist both states")
        supervisor.resume_frozen(slug)
        back = store.load_org(slug)
        assert not back.node("f1").get("frozen"), (
            "the card says 'usage limit · resumes 3pm' and the reset has "
            "passed, but ▶ resume skipped the node because limit_locked is "
            "also set — and with no fable_lock on the org, the only control "
            "that clears limit_locked is not rendered at all. The card "
            "promises a recovery the app cannot perform and offers nothing "
            "to click")
    # ← FIXED (promoted out of gap(), 2026-08-06, same day): (a) and (b)
    # shipped exactly as prescribed — the load hook clears an ORPHANED
    # limit_locked whenever no fable_lock exists (same artifact class as
    # the timeless lock; the freeze underneath then resumes through its own
    # machinery), and while a REAL lock holds a node the freeze chip reads
    # HALTED with no reset clock (desk + card). (c) deliberately not built:
    # a per-node control for a state that is now rare-by-construction.
    # Fixture moved to the raw file on promotion — see the note in the body.
    check("veto · a node whose freeze reset has passed either resumes or "
          "stops advertising that it will (peer screenshots 2026-08-06)",
          _a_card_promising_a_reset_can_actually_reset)

    def _driving_a_halted_node_says_it_is_halted():
        """The other half of "doesn't do anything". Every parked state
        announces itself in send_message's RETURN — frozen → {"frozen": True},
        archived → {"deferred": …}, remote-controlled → {"remote": True}.
        limit_locked is the one that does not: the node is not `frozen`, so
        the early guards fall through, a turn starts, and it dies inside
        _run_turn on the limit_locked check. The caller is told `accepted`."""
        org = store.create_org("zz stuck reply")
        org.hire(USER, None, "fable", 20, "f1", **hspec())
        store.save_org(org)
        # ⚠ the lock needs a FUTURE until_ts to survive the reload: since the
        # STUCK-1 migration a timeless lock is treated as a pre-fix artifact
        # and released at load. This check is about a LEGITIMATELY halted
        # node, so it must be one the release does not reach. (First draft
        # used a timeless lock and the migration landed under it mid-run —
        # `fixture` reported it as a broken check rather than filing it as
        # the finding, which is exactly what that helper is for.)
        org.fable_limit_hit("f1", "weekly limit reached",
                            until_ts=_time.time() + 3600)
        store.save_org(org)
        slug = org.d["slug"]
        fixture(bool(store.load_org(slug).node("f1").get("limit_locked")),
                "the fixture node is not halted")
        r = supervisor.send_message(slug, "f1", "please carry on")
        assert any(k in r for k in ("halted", "limit_locked", "frozen")), (
            f"send_message answered {r} for a node that CANNOT act — the "
            f"same shape it returns for a healthy node. frozen, archived and "
            f"remote-controlled nodes all name their state here; the halted "
            f"one does not, which is why the reported experience is "
            f"'nothing happens' rather than 'it is halted'")
    # ← FIXED (promoted out of gap(), 2026-08-06, same day): a fourth guard
    # beside frozen/archived/remote — a limit_locked node answers
    # {accepted: true, limit_locked: true}, mail boxed as ever. Still
    # matters for a REAL weekly halt, exactly per the finding's own note.
    check("stuck · sending mail to a halted node SAYS it is halted, the way "
          "every other parked state does",
          _driving_a_halted_node_says_it_is_halted)

    # ---- USER REPORT 2026-08-06 ------------------------------------------
    # "network interruptions appear to halt chats in the middle of a turn;
    # they should restart automatically once connectivity resumes."
    #
    # Traced, and the shape is a MISSING CLASS rather than a broken path. A
    # turn's error blob is sorted into exactly three buckets:
    #   · _looks_like_filtered      → the fable content-filter policy
    #   · _looks_like_usage_limit   → a FREEZE with until_ts, which
    #                                 start_auto_resume_loop then restarts —
    #                                 including the reset-less case, probed on
    #                                 a 5-minute floor rather than left for a
    #                                 human (redteam gap 2026-08-05)
    #   · everything else           → `raise RuntimeError("turn failed: …")`
    # A dropped connection lands in the third bucket. What happens next is
    # correct as far as it goes and stops one step short:
    #   ✔ the drained mail FOLDS BACK into the mailbox (nothing is lost)
    #   ✔ last_error + a durable row put the failure in the conversation
    #   ✘ no `frozen` record is written, and start_auto_resume_loop iterates
    #     ONLY nodes carrying one — so nothing ever re-drives the node
    # The node then sits idle until a human nudges it or new mail arrives.
    # Restart durability exists (reconcile() re-drives on boot), but a backend
    # that keeps running never retries, which is exactly the reported case.
    def _a_transient_failure_is_classified_and_retried():
        src = open(os.path.join(_REPO, "backend", "orgtree", "supervisor.py"),
                   encoding="utf-8").read()
        fixture("_looks_like_usage_limit" in src and "_looks_like_filtered" in src,
                "the failure-classification predicates moved — re-read this")
        assert re.search(r"def _looks_like_(transient|connection|network|offline)",
                         src), (
            "there is no predicate for a connection-class failure, so a "
            "dropped network lands in the terminal `turn failed` bucket "
            "beside a bad argv and a crashed CLI. The two buckets that DO "
            "exist are both positively classified from the blob; this is the "
            "third case and it has no classifier at all")
    # ← FIXED (promoted out of gap(), 2026-08-06, same day): the exact
    # prescribed shape — `_looks_like_connection_failure`, narrow and
    # positive (node/undici + errno spellings, never a catch-all), REUSING
    # the freeze machinery: fz["connection"]=True with an exponential
    # 30s→300s until_ts, NET_RETRY_MAX consecutive attempts (counter reset
    # by any completed turn), then a terminal failure with an honest
    # resume-manually label. resume_texts/fold-back correctness inherited.
    check("transient · a connection-class turn failure is classified, not "
          "swept into the terminal bucket (user report 2026-08-06)",
          _a_transient_failure_is_classified_and_retried)

    def _the_resume_loop_can_see_a_transiently_failed_node():
        """The second half: even given a classifier, the restarter has to be
        able to SEE the node. The selection is on a `frozen` record alone, so
        a node parked any other way is invisible to it — which is why the fix
        belongs in the freeze shape rather than beside it.

        (2026-08-10: the selection moved OUT of start_auto_resume_loop into
        `auto_resume_ready` + `_resumable` when readiness became per-node.
        Same question, two smaller functions — this reads them where they
        now live.)"""
        src = open(os.path.join(_REPO, "backend", "orgtree", "supervisor.py"),
                   encoding="utf-8").read()
        i = src.index("def auto_resume_ready")
        seg = "\n".join(ln for ln in src[i:i + 3000].splitlines()
                        if not ln.lstrip().startswith("#"))
        j = src.index("def _resumable")
        pick = src[j:j + 800]
        fixture('n.get("frozen")' in pick and "_resumable(n)" in seg,
                "the resume loop's selection moved — re-read this check")
        assert re.search(r"transient|connection|last_error", seg), (
            "the auto-resume loop selects exclusively on a `frozen` record, "
            "so a node halted by a network drop — which writes last_error "
            "and no freeze — is invisible to the one mechanism that could "
            "restart it")
    # ← FIXED (promoted out of gap(), 2026-08-06, same day): closed by the
    # first-of-the-two-ways — the transient case IS a freeze, so the
    # selector sees it unchanged (fz["connection"] rides beside "limit" in
    # resume_frozen's owned-kinds exemption and the timer's timeless
    # branch). The auto_resume toggle is respected exactly as flagged: with
    # it off, a network-frozen node waits for ▶, never auto-restarted.
    check("transient · the auto-resume loop can see a node halted by "
          "something other than a limit",
          _the_resume_loop_can_see_a_transiently_failed_node)

    # ---- USER REPORT 2026-08-06 ------------------------------------------
    # "system thinks that session limit hit counts as a fable limit hit and
    # perma-freezes fable agents when hit."
    #
    # Confirmed by reading, and the two halves sit 1,350 lines apart:
    #
    #   · `_looks_like_usage_limit` is deliberately broad enough to catch the
    #     ORDINARY session limit — its own comment says so: "the CLI's
    #     session-limit phrasing is 'You've hit your session limit — resets
    #     1:40pm', which matched NONE of the original second set". It matches
    #     on "session", "hit your", "resets". Correct for its job: ANY model's
    #     usage limit should freeze the agent.
    #   · the escalation right after it reads, in full,
    #         if o2.node(nid)["model"] == "fable":
    #             o2.fable_limit_hit(nid, err_blob)
    #     — the ONLY test is the node's tier. Nothing asks whether the limit
    #     was the WEEKLY FABLE QUOTA that `fable_limit_hit` documents itself as
    #     handling ("Weekly Fable usage limit exhausted").
    #
    # So a fable agent hitting the same five-hour session limit every tier
    # shares is recorded as org-wide Fable exhaustion.
    # ---- USER REPORT 2026-08-07: a REAL Fable-tier limit at neoja --------
    # The captured message, verbatim, from their running org doc (both their
    # fable nodes identical):
    #
    #   "You've reached your Fable 5 limit. Run /usage-credits to continue
    #    or switch models with /model."
    #
    # It refuted TWO of my beliefs at once, and both were written down here
    # as if settled:
    #  ① the gate was `"weekly" in blob`. The real message never says
    #    "weekly", so the escalation did NOT fire on a genuine tier limit —
    #    their nodes froze independently 55 s apart instead of halting
    #    together under the org policy. The false negative I had recorded as
    #    "deliberate, fails safe" was the COMMON case.
    #  ② I had assumed a weekly reset would be DAYS OUT and merely parsed
    #    wrong. There is no reset in the message AT ALL — the CLI offers an
    #    action ("run /usage-credits, or switch models"), not a time.
    # The gate is now the model name (`_looks_like_fable_tier_limit`), and a
    # tier limit with no parseable reset marks its lock `no_reset` instead of
    # inheriting the 300-second probe floor.
    def _a_tier_limit_with_no_reset_does_not_take_the_probe_floor():
        """The floor means 'no reset known, retry soon' — right for the
        rate-limit class it was written for, catastrophic as a tier-quota
        horizon: the lock would self-release five minutes into a week-long
        limit, un-halt every fable node, announce a reset that did not
        happen, re-hit the wall and re-halt — roughly 288 cycles a day, each
        one notifying the parent, the peers, the node AND the user inbox."""
        real = ("You've reached your Fable 5 limit. Run /usage-credits to "
                "continue or switch models with /model.")
        assert supervisor._looks_like_fable_tier_limit(real), (
            "the captured Fable-tier message no longer reads as one")
        assert supervisor._parse_limit_reset_ts(real) is None, (
            "the fixture assumes this message carries no reset — if the "
            "parser learned it, re-read this check")
        org = store.create_org("zz tier noreset")
        org.hire(USER, None, "fable", 20, "f1", **hspec())
        store.save_org(org)
        org.fable_limit_hit("f1", real,
                            until_ts=supervisor._parse_limit_reset_ts(real))
        store.save_org(org)
        lock = store.load_org(org.d["slug"]).d.get("fable_lock") or {}
        assert lock.get("no_reset") is True and not lock.get("until_ts"), (
            f"a tier limit with no published reset took a horizon anyway: "
            f"{lock}. If that horizon is the 300 s probe floor the lock "
            f"self-releases in five minutes and the release-storm is back")

    def _a_no_reset_lock_survives_the_artifact_sweep():
        """…and the marker is what keeps it alive. A bare timeless lock is
        the pre-fix ARTIFACT shape, released on the next load (STUCK-1); a
        lock that positively says its reset is unknown must not be swept
        with it, or the escalation would undo itself on the very next read."""
        real = ("You've reached your Fable 5 limit. Run /usage-credits to "
                "continue or switch models with /model.")
        org = store.create_org("zz tier survives")
        org.hire(USER, None, "fable", 20, "f1", **hspec())
        org.hire(USER, None, "fable", 20, "f2", **hspec())
        store.save_org(org)
        org.fable_limit_hit("f1", real, until_ts=None)
        store.save_org(org)
        slug = org.d["slug"]
        for _ in range(3):                     # three separate reads
            store.load_org(slug)
        back = store.load_org(slug)
        assert back.d.get("fable_lock"), (
            "the no_reset lock was swept as a pre-fix artifact — the "
            "escalation now undoes itself on the next load, which is worse "
            "than never firing")
        assert sorted(k for k, v in back.nodes.items()
                      if v.get("limit_locked")) == ["f1", "f2"], (
            "the org-wide halt did not survive with its lock")
    check("tier-limit · a Fable-tier limit with no published reset marks its "
          "lock no_reset instead of taking the 5-minute probe floor",
          _a_tier_limit_with_no_reset_does_not_take_the_probe_floor)
    check("tier-limit · …and that lock survives the pre-fix artifact sweep",
          _a_no_reset_lock_survives_the_artifact_sweep)

    def _the_fable_escalation_needs_a_weekly_marker():
        src = open(os.path.join(_REPO, "backend", "orgtree", "supervisor.py"),
                   encoding="utf-8").read()
        # ⚠ STRIP FIRST, SLICE AFTER (2026-08-18). Slicing raw source and
        # stripping comments afterwards makes the window's reach depend on how
        # much COMMENTARY sits above the call: it was widened 500 → 1200 on
        # 2026-08-07 for exactly that reason, and the same fixture tripped
        # again the next time the escalation gained a comment block. Stripping
        # first makes the guard measure code distance, which is what it means.
        code = "\n".join(ln for ln in src.splitlines()
                         if not ln.lstrip().startswith("#"))
        i = code.find("fable_limit_hit(")
        fixture(i > 0, "the fable escalation moved — re-read this check")
        seg = code[max(0, i - 1200):i + 120]
        fixture('"fable"' in seg, "the tier test is not where this check expects")
        assert re.search(r"_looks_like_fable_tier_limit", seg), (
            "the ONLY condition for declaring org-wide Fable exhaustion is "
            "that the node's tier is fable. An ordinary session limit — which "
            "_looks_like_usage_limit matches on purpose, by its own comment — "
            "is therefore read as the weekly Fable quota running out")
    # ← FIXED (promoted out of gap(), 2026-08-06, same day): the escalation
    # is gated on `_looks_like_fable_tier_limit` (positive WEEKLY wording),
    # exactly the prescribed second predicate — the ordinary freeze is
    # untouched, so a fable agent hitting a session limit freezes with an
    # until_ts and auto-resumes like any other tier.
    check("fable · declaring the weekly Fable limit needs positive evidence, "
          "not just a fable-tier node hitting some limit",
          _the_fable_escalation_needs_a_weekly_marker)

    def _a_halted_fable_node_can_be_released_by_time():
        """The second half — the one that makes a misclassification PERMANENT
        rather than merely wrong for five hours.

        The ordinary limit freeze carries `until_ts` and clears itself
        (auto_resume / ▶). `fable_limit_hit`'s halt policy instead sets
        `limit_locked` on EVERY live fable node org-wide, and the only writer
        that removes it is `clear_fable_lock` — a user action. The resume path
        skips a `limit_locked` node by design. So the node holds a freeze that
        WOULD have expired and a lock that never does."""
        org = store.create_org("zz fable perma")
        org.hire(USER, None, "fable", 20, "f1", **hspec())
        store.save_org(org)
        # deterministic fixture (implementer, on promotion): the original
        # passed a '1:40pm' phrasing whose parsed reset landed before or
        # after NOW depending on the wall clock the suite ran at — the
        # outcome changed with the time of day. The reset now rides the
        # explicit `until_ts` argument the fix added, set firmly in the past.
        org.fable_limit_hit("f1", "Weekly Fable usage limit exhausted",
                            until_ts=time.time() - 60)
        store.save_org(org)          # ⚠ or the reload below reads a doc that
                                     # never saw the lock and the check passes
                                     # for a reason that is not the finding —
                                     # measured, first draft did exactly that
        fixture(bool(org.node("f1").get("limit_locked")),
                "the halt policy did not lock the node")
        # (the old second fixture — "the lock survives the reload" — is GONE:
        # the fix makes the reload the release, which is the property under
        # test, not a precondition)
        org2 = store.load_org(org.d["slug"])
        assert not org2.node("f1").get("limit_locked"), (
            "a fable node halted by a limit that carries a RESET TIME stays "
            "halted forever: `limit_locked` has no time-based release, the "
            "resume path skips any node that has it, and only "
            "clear_fable_lock (a user action, or a user fable-hire) removes "
            "it. Combined with the misclassification above, one session "
            "limit on one fable agent perma-freezes every fable agent in the "
            "org")
        assert not org2.d.get("fable_lock"), \
            "the org-wide lock outlived its own reset time"
    # ← FIXED (promoted out of gap(), 2026-08-06, same day): the reset rides
    # onto the lock (fable_limit_hit's new until_ts argument, fed from the
    # already-parsed fz["until_ts"] at the escalation site) and the RELEASE
    # lives in the ledger's load hook — every load of an org whose lock has
    # expired clears fable_lock AND every node's limit_locked, silently like
    # the pre-№41 retag (the auto-resume timer also wakes on the lock's
    # time). A TIMELESS lock still waits for clear_fable_lock. Fixture made
    # deterministic on promotion — see the note in the body.
    check("fable · a halt whose limit has a known reset time is releasable "
          "without the user's hand",
          _a_halted_fable_node_can_be_released_by_time)

    def _the_lock_release_tells_the_people_the_halt_told():
        """FABLE-3, on d40dd82's own fix. The halt is loud and the release is
        silent, so the org is reorganised around a state nobody is told has
        ended.

        `fable_limit_hit` notifies three parties per halted node — the parent
        ("decide how to cover its work"), the peers, and the node itself — and
        writes a user-inbox row. The timed release (`Org.__init__`'s load hook)
        pops the lock and clears every `limit_locked` in a loop with no
        notification at all. The load-hook comment gives the reason and it is
        a good one: it runs on EVERY read, and notifying there would spam. But
        the conclusion drawn from it — notify nobody — is not the only option.

        Measured: halt = 1 user-inbox row + notices to parent/peers/self;
        release = 0 rows, no notices, on the load that performs it."""
        org = store.create_org("zz fable quiet")
        org.hire(USER, None, "opus", 20, "boss", **hspec())
        org.hire("boss", "boss", "fable", 10, "f1", **hspec())
        store.save_org(org)
        org.fable_limit_hit("f1", "weekly limit reached",
                            until_ts=_time.time() - 1)
        store.save_org(org)
        # ⚠ baseline from the HELD org object, never a load (implementer, on
        # promotion): the fix announces from the load hook itself — the same
        # place that releases, idempotent because the lock is consumed in
        # the same mutation — so EVERY load after expiry carries the
        # announcement row, and a load-derived `seen` would already include
        # it, hiding the very delta this check measures. The org we called
        # fable_limit_hit on predates its own lock, so it holds exactly the
        # halt's rows.
        fixture(bool(org.d.get("user_inbox")),
                "the halt itself told nobody — wrong fixture, not the finding")
        seen = len(org.d.get("user_inbox") or [])
        # the load that RELEASES it (the lock is already expired above)
        rel = store.load_org(org.d["slug"])
        fixture(not rel.node("f1").get("limit_locked"),
                "the lock did not release — this check tests the wrong thing")
        after = len(rel.d.get("user_inbox") or [])
        assert after > seen, (
            "a fable halt announces itself to the parent ('decide how to "
            "cover its work'), the peers and the node, and writes a "
            "user-inbox row — and then un-halts in total silence. Everyone "
            "who reorganised around the halt is left believing it still "
            "holds, and the agent itself was told 'you are halted' with no "
            "matching 'you are not'")
    # ← FIXED (promoted out of gap(), 2026-08-06, same day): both exits now
    # announce — the timed release notifies parent+peers+node and writes a
    # user-inbox row FROM THE LOAD HOOK ITSELF, and clear_fable_lock (the
    # manual exit) does the same. Deviation from the finding's notify-from-
    # the-resuming-path suggestion, deliberate: the resuming path only runs
    # when auto_resume is on or ▶ is pressed, so a release could still go
    # unannounced for hours; the load-hook announcement is idempotent for
    # the same reason the release is (the lock — the trigger — is consumed
    # in the same mutation, so one save persists exactly one copy and every
    # unsaved load re-derives the identical announcement). Baseline read
    # from the raw saved doc on promotion — see the note in the body.
    check("fable · the timed lock release is announced to the parties the "
          "halt was announced to",
          _the_lock_release_tells_the_people_the_halt_told)

    # ---- USER REPORT 2026-08-06 ------------------------------------------
    # "new org inbox mail didn't arrive at an agent until its turn ended, not
    # at its next post-event hook."
    #
    # Bounded by measurement before being written down, so the report is not
    # guessed at:
    #   · the inbound path is UNIFORM — every outside route (hub poll, @org:,
    #     @mcp:) reaches supervisor.deliver_org_inbox → send_message, the same
    #     function node-to-node mail uses, and takes the same steer branch;
    #   · steering demonstrably WORKS on this machine — the live resonite
    #     org's `steered_log` carries 17 mid-turn deliveries.
    # So org-inbox mail is not riding a second-class path, and the parking
    # decision itself is right (a PostToolUse hook is the soonest delivery
    # that does not interrupt — user ruling).
    #
    # What is missing is the ability to tell the two OUTCOMES apart. A message
    # accepted while a node is responding is answered {"steering": true} and
    # parked in st["steer"], where only a hook can collect it. If the turn
    # makes no further tool call, the result boundary folds the leftovers into
    # the FRONT of the queue (`st["queue"][0:0] = leftover`) and they arrive
    # at the next turn — which is exactly "it waited for the turn to end".
    # That fold is silent.
    def _the_steer_fold_back_leaves_a_record():
        src = open(os.path.join(_REPO, "backend", "orgtree", "supervisor.py"),
                   encoding="utf-8").read()
        folds = [m.start() for m in re.finditer(
            r'st\["queue"\]\[0:0\] = leftover', src)]
        fixture(len(folds) >= 1,
                f"the steer fold-back site moved (found {len(folds)}) — "
                f"re-read this check before trusting it")
        for i in folds:
            seg = "\n".join(ln for ln in src[max(0, i - 700):i + 400].splitlines()
                            if not ln.lstrip().startswith("#"))
            assert re.search(r"steered_log|_log\(|node_event|_emit", seg), (
                "a message parked for steering is folded back into the queue "
                "with no record: the API answered {\"steering\": true}, no "
                "hook ever collected it, and it will now arrive at the NEXT "
                "turn instead. Nothing in the org record, the node events or "
                "the steered log separates 'delivered mid-turn' from 'waited "
                "for the boundary' — so the reported experience is invisible "
                "to anyone trying to confirm it from the durable record")
    # ← FIXED (promoted out of gap(), 2026-08-06, same day): both fold sites
    # now call `_steer_fold_log` (outside _state_lock, pop_steer's lock
    # order) — a `fold`-marked steered_log row that read_chat renders as a
    # dim SYSTEM line ("N mid-turn message(s) missed the steer window —
    # delivered at the next turn"), never a user-message impersonation. The
    # steer window itself is unchanged, exactly per this finding's own
    # not-asking note; only the silence is gone. steered_log now records
    # misses beside successes, so reading it no longer overstates steering.
    check("steer · a message parked for steering leaves a record when it "
          "falls back to the queue undelivered",
          _the_steer_fold_back_leaves_a_record)

    print("\npop_steer — one write, or the message is briefly homeless:")
    slug, (nid,) = horg()
    tk = token()
    hpost(slug, nid, f"steered {tk}")
    etext, stok = supervisor._envelope(slug, nid, "n")        # via defaults to steer
    st = supervisor.state(slug, nid)
    st["steer"] = [{"toks": [stok], "text": etext}]
    out = supervisor.pop_steer(slug, nid)
    d = store.load_org(slug).d
    check("steer · pop returns the text and empties the list", lambda: (
        None if len(out) == 1 and tk in out[0] and not st.get("steer")
        else (_ for _ in ()).throw(AssertionError(out))))
    check("steer · the confirm and the durable log are ONE doc state", lambda: (
        None if (d.get("delivering") or {}).get(nid) is None
        and tk in (d.get("steered_log") or {}).get(nid, [{}])[0].get("text", "")
        else (_ for _ in ()).throw(AssertionError(d.get("steered_log")))))
    check("steer · popping an empty list writes nothing", lambda: (
        None if supervisor.pop_steer(slug, nid) == []
        else (_ for _ in ()).throw(AssertionError())))

    print("\nfreeze record:")
    check("freeze · _ensure_frozen mints over an explicit None", lambda: (
        lambda n: (n.__setitem__("frozen", None),
                   supervisor._ensure_frozen(n).__setitem__("until", "3pm"),
                   None if n["frozen"]["until"] == "3pm"
                   else (_ for _ in ()).throw(AssertionError(n)))[-1]
    )({"frozen": None}))
    check("freeze · _ensure_frozen preserves an existing record", lambda: (
        lambda n: None if supervisor._ensure_frozen(n) is n["frozen"]
        else (_ for _ in ()).throw(AssertionError(n))
    )({"frozen": {"at": "x", "resume_texts": ["keep me"]}}))

    print("\nthe freeze record vs ledger's pre-№41 migration:")

    def _reload_frozen(fz: dict) -> dict:
        slug, (nid,) = horg()
        with store.DOC_LOCK:
            org = store.load_org(slug)
            cast_n = org.node(nid)
            cast_n["frozen"] = fz              # type: ignore[typeddict-item]
            store.save_org(org)
        return dict(store.load_org(slug).node(nid).get("frozen") or {})

    ts = time.time() + 3600
    check("freeze-shape · a usage-limit freeze WITH a human `until` survives a "
          "reload as itself", lambda: (
              lambda got: None if not got.get("spend") and got.get("error")
              else (_ for _ in ()).throw(AssertionError(got))
          )(_reload_frozen({"at": "2026-08-04T00:00:00.000Z", "resume_texts": [],
                            "until": "3pm", "until_ts": ts,
                            "error": "Claude AI usage limit reached|1753898400"})))
    # CHARACTERISATION of a defect in ledger.py (not this suite's territory):
    # the migration that re-tags pre-№41 spend freezes matches on {error, no
    # until, no resume_texts, nothing True} — which a REAL usage-limit freeze
    # also produces whenever the reset time is unparseable and no replay text
    # was kept (a slash-command turn, or an unconfirmed batch). It then becomes
    # a kiosk "spend" freeze, and `resume_frozen` skips those forever.
    got = _reload_frozen({"at": "2026-08-04T00:00:00.000Z", "resume_texts": [],
                          "error": "usage limit exceeded"})
    check("freeze-shape · characterised: a usage-limit freeze with NO reset "
          "time and no replay text is re-tagged as a kiosk SPEND freeze",
          lambda: (
              None if got.get("spend") is True
              else (_ for _ in ()).throw(AssertionError(
                  "the ledger migration narrowed — re-check the report"))))
    note("ledger.py's pre-№41 freeze migration re-tags a genuine usage-limit "
         "freeze as a kiosk spend freeze when the reset time is unparseable "
         "AND no replay text was kept; ▶ resume then skips the node forever. "
         "Mitigated supervisor-side (an `until` is now always derived from a "
         "known until_ts) — the migration itself is another agent's file.")

    print("\nfold-back edge cases:")
    slug, (nid,) = horg()
    hpost(slug, nid, f"orphan {token()}")
    _t, _tok = supervisor._envelope(slug, nid, "n", via="turn")
    with store.DOC_LOCK:
        org = store.load_org(slug)
        org.nodes.pop(nid)                      # the node was deleted mid-turn
        store.save_org(org)
    check("fold-back · a deleted node's batch is dropped, not resurrected",
          lambda: (
              supervisor._fold_back_undelivered(slug, nid),
              None if hjournal(slug, nid) == []
              and (store.load_org(slug).d.get("mail") or {}).get(nid) is None
              else (_ for _ in ()).throw(AssertionError()))[-1])

    # The suggested principled fix for the known exception is to give
    # _fold_back_undelivered the same transcript-evidence test node_chat uses.
    # This is what it would COST, made concrete: the fold-back is the only
    # thing that puts a consumed-but-unanswered message back where the NEXT
    # envelope will present it again. Drop the batch and the message survives
    # only as a line of session history — visible, but never re-delivered and
    # never re-asked-for.
    slug, (nid,) = horg()
    tk = token()
    hpost(slug, nid, f"received, never answered {tk}")
    supervisor._envelope(slug, nid, "n", via="turn")
    supervisor._fold_back_undelivered(slug, nid)          # the CLI died
    nxt, _t = supervisor._envelope(slug, nid, "next turn", via="turn")
    check("foldback-evidence · a folded-back message IS re-presented by the "
          "next envelope (what evidence-dropping would give up)", lambda: (
              None if tk in nxt and "[MAIL" in nxt
              else (_ for _ in ()).throw(AssertionError(nxt[:200]))))
    note("evaluated: applying node_chat's transcript-evidence test inside "
         "_fold_back_undelivered removes the duplicate by weakening delivery "
         "(the agent is never re-asked). The display-layer fix removes it "
         "without that cost — see the report.")

    slug, (nid,) = horg()
    check("fold-back · nothing journaled is a no-op", lambda: (
        supervisor._fold_back_undelivered(slug, nid),
        None if hjournal(slug, nid) == [] else
        (_ for _ in ()).throw(AssertionError()))[-1])
    check("envelope · unknown node returns the text untouched", lambda: (
        lambda r: None if r == ("plain", None)
        else (_ for _ in ()).throw(AssertionError(r))
    )(supervisor._envelope(slug, "nope", "plain")))

    # ---- USER RULING 2026-08-07 (D-104): an agent that learns this install
    # is behind updates it ITSELF — but only when nobody else is mid-turn.
    # That precondition cannot live in prose: the deciding agent cannot see
    # another ORG's nodes at all, and the blast radius IS machine-wide (the
    # org leg restarts the shared backend and cuts every in-flight turn). So
    # it is a refusal in `launch_self_update`, and these pin the refusal
    # rather than the instruction.
    su_slug, (su_nid,) = horg()
    other_slug, (other_nid,) = horg()

    def _sees_a_busy_peer_in_another_org():
        supervisor.state(other_slug, other_nid)["busy"] = True
        try:
            got = supervisor.others_working(exclude=(su_slug, su_nid))
            assert f"{other_slug}/{other_nid}" in got, got
            # …and never the caller itself, or a lone agent could never update
            assert f"{su_slug}/{su_nid}" not in got, got
        finally:
            supervisor.state(other_slug, other_nid)["busy"] = False
    check("selfupdate · a busy agent in ANOTHER org is visible machine-wide",
          _sees_a_busy_peer_in_another_org)

    def _a_queued_agent_counts_as_working():
        # queued, not yet started, is still work the restart would disrupt
        supervisor.state(other_slug, other_nid)["queue"] = ["pending job"]
        try:
            assert f"{other_slug}/{other_nid}" in supervisor.others_working()
        finally:
            supervisor.state(other_slug, other_nid)["queue"] = []
    check("selfupdate · …and so is one with a QUEUE but no live turn",
          _a_queued_agent_counts_as_working)

    def _org_target_refuses_while_busy():
        supervisor.state(other_slug, other_nid)["busy"] = True
        try:
            r = supervisor.launch_self_update(su_slug, su_nid, "org")
            assert r.get("refused") and not r["launched"], r
            assert f"{other_slug}/{other_nid}" in r["busy"], r
            # the refusal must NAME who, or the agent cannot judge the wait
            assert other_nid in r["status"], r["status"]
        finally:
            supervisor.state(other_slug, other_nid)["busy"] = False
    check("☠ selfupdate · target 'org' REFUSES while another agent is "
          "mid-turn, and names them", _org_target_refuses_while_busy)

    def _spawn_flags() -> int:
        """The creationflags _detached_spawn would pass on Windows, read from
        the source: the value never reaches a return, and a probe alone would
        not catch DETACHED_PROCESS coming back on a POSIX dev box."""
        src = open(supervisor.__file__, encoding="utf-8").read()
        m = re.search(r'kwargs\["creationflags"\]\s*=\s*([0-9xA-Fa-f]+)\s*\|'
                      r'\s*([0-9xA-Fa-f]+)', src)
        assert m, "creationflags are no longer set the way this check reads them"
        return int(m.group(1), 16) | int(m.group(2), 16)

    def _self_update_asks_for_only_if_behind():
        """Peer report 2026-08-09 (neoja): a self-update restarted every org on
        their machine and advanced HEAD by nothing. An operator deploy MUST
        keep redeploying an unmoved HEAD (that is how a locally-made commit
        ships) — so the difference has to travel with the CALL, not live in
        the script. Pinned as argv/env, because a silent revert here is
        exactly the disruption the peer reported."""
        seen: list[tuple[list[str], dict[str, str] | None]] = []
        real = supervisor._detached_spawn
        real_busy = supervisor.others_working
        supervisor._detached_spawn = (                       # type: ignore[assignment]
            lambda args, cwd, logpath, env=None: seen.append((args, env)))
        # by now this suite has left other nodes busy, so the D-104 refusal
        # would fire before the spawn — that gate has its own checks above
        supervisor.others_working = lambda exclude=None: []   # type: ignore[assignment]
        try:
            supervisor._self_update_at[0] = 0.0
            supervisor.launch_self_update(su_slug, su_nid, "org")
        finally:
            supervisor._detached_spawn = real                # type: ignore[assignment]
            supervisor.others_working = real_busy             # type: ignore[assignment]
            supervisor._self_update_at[0] = 0.0
        assert seen, "nothing was spawned at all"
        args, env = seen[0]
        if os.name == "nt":
            assert "-OnlyIfBehind" in args, args
        else:
            assert (env or {}).get("ORGTREE_ONLY_IF_BEHIND") == "1", env
    check("selfupdate · the launch asks the script NOT to restart when the "
          "pull advances nothing", _self_update_asks_for_only_if_behind)

    def _the_scripts_honour_that_flag():
        """…and the flag has to MEAN something on the other side. Both scripts
        are read as text: the branch that exits before the rebuild must exist
        and must be reached from the same name the launch passes."""
        repo = os.path.normpath(os.path.join(
            os.path.dirname(os.path.abspath(supervisor.__file__)), "..", ".."))
        ps1 = open(os.path.join(repo, "update.ps1"), encoding="utf-8").read()
        sh = open(os.path.join(repo, "update.sh"), encoding="utf-8").read()
        assert "$OnlyIfBehind" in ps1 and "[switch]$OnlyIfBehind" in ps1, \
            "update.ps1 does not declare/read the switch the launch passes"
        assert "ORGTREE_ONLY_IF_BEHIND" in sh, \
            "update.sh does not read the env var the launch sets"
        # the BRANCH, not the first mention — the param block names it too
        for name, src, needle in (
                ("update.ps1", ps1, "if ($OnlyIfBehind) {"),
                ("update.sh", sh, 'ORGTREE_ONLY_IF_BEHIND:-')):
            i = src.find(needle)
            assert i > 0, f"{name} has no branch on the flag ({needle!r})"
            assert "exit 0" in src[i:i + 400], \
                f"{name} branches on the flag but does not EXIT — it would " \
                f"fall through to the rebuild and restart anyway"
        # and a dirty tree is reported rather than silently changing the answer
        assert "porcelain" in ps1 and "porcelain" in sh, \
            "neither script reports a dirty working tree; the peer's log " \
            "could not say why the pull did nothing"
    check("selfupdate · …and both scripts read that flag and exit on it",
          _the_scripts_honour_that_flag)

    def _detached_spawn_keeps_the_childs_output():
        """☠ THE PEER'S ACTUAL BUG (neoja 2026-08-09), root-caused here rather
        than on their machine: every Windows self-update logged NOTHING but the
        Python-written banner, because DETACHED_PROCESS detaches the child from
        the console and takes the redirected stdout handle with it. Measured
        0/4 lines against CREATE_NO_WINDOW's 4/4.

        No local deploy exercises this path — an operator runs update.ps1
        through a shell that has a console — which is exactly why it survived
        this long. So the flag is pinned, and on Windows the behaviour is
        re-measured for real rather than asserted from the constant."""
        assert not (0x00000008 & _spawn_flags()), \
            "DETACHED_PROCESS is back — the child's output will vanish again"
        if os.name != "nt":
            return
        probe = os.path.join(TMP, "spawnprobe.ps1")
        with open(probe, "w", encoding="utf-8") as f:
            f.write('Write-Host "H"\nWrite-Output "O"\n'
                    '& cmd /c echo N\n')
        log = os.path.join(TMP, "spawnprobe.log")
        open(log, "wb").close()
        supervisor._detached_spawn(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", probe], TMP, log)
        for _ in range(60):                       # the child is detached
            time.sleep(0.25)
            body = open(log, encoding="utf-8", errors="replace").read()
            if all(x in body for x in ("H", "O", "N")):
                return
        raise AssertionError(
            f"a spawned child's output never reached the log — a self-update "
            f"would report nothing at all. Log: {body!r}")
    check("☠ selfupdate · a detached child's output actually reaches the log",
          _detached_spawn_keeps_the_childs_output)

    def _refusal_launches_nothing_and_burns_no_rate_limit():
        # a refusal that consumed the 5-minute machine-wide slot would leave
        # an idle machine unable to update for five minutes over a no-op
        supervisor.state(other_slug, other_nid)["busy"] = True
        try:
            supervisor.launch_self_update(su_slug, su_nid, "org")
        finally:
            supervisor.state(other_slug, other_nid)["busy"] = False
        assert supervisor._self_update_at[0] == 0.0, \
            "the refused call started the rate-limit clock"
    check("selfupdate · a refusal spends nothing — the rate limit is untouched",
          _refusal_launches_nothing_and_burns_no_rate_limit)


# ================================================================= live rig

BASE = f"http://127.0.0.1:{PORT}"
PROC: subprocess.Popen[str] | None = None
_orgs: list[str] = []

WRAP_JS = r"""
'use strict'
// wrapcli.js — three turn shapes fakecli.js has no dial for, delegating to it
// for everything else. Generated by test_turn_lifecycle.py; fakecli.js is
// deliberately left untouched.
const fs = require('fs')
const argv = process.argv.slice(2)
function arg(n) { const i = argv.indexOf(n); return i >= 0 ? argv[i + 1] : null }
let cfg = {}
try {
  const f = JSON.parse(fs.readFileSync(process.env.FAKECLI_CONFIG, 'utf8'))
  const node = process.env.ORGTREE_NODE || ''
  cfg = Object.assign({}, (f.wrap || {}).default || {}, (f.wrap || {})[node] || {})
} catch (e) { cfg = {} }
// a FUTURE epoch — _parse_limit_reset_ts declines one already behind us, so a
// hardcoded stamp would make the limit fixture rot into a no-op
const LIMIT_EPOCH = Math.floor(Date.now() / 1000) + 3 * 3600

// the compaction fork and the /context one-shot ask for --output-format json;
// fakecli only speaks stream-json, so _compact_split could never succeed
if (arg('--output-format') === 'json') {
  const sid = require('crypto').randomUUID()
  process.stdout.write(JSON.stringify({
    type: 'result', subtype: 'success', session_id: sid,
    result: 'compacted.', total_cost_usd: 0.0002 }) + '\n')
  process.exit(0)
}
if (cfg.mode === 'die') {           // dies on argv — never reads stdin (C1)
  process.stderr.write((cfg.errText || 'fatal: bad flag') + '\n')
  process.exit(cfg.code || 1)
}
// TWO result events for ONE user message. The CLI emits a top-level result
// per message, but it also has out-of-band result paths (the stream-json
// writer's own `error_during_execution`, error_max_turns) that can land after
// the boundary result has already been sent — at which point orgtree has
// CLOSED this process's stdin. If a message was queued in between, the second
// boundary tries to feed it down that closed pipe: `ValueError: I/O operation
// on closed file.` (user report 2026-08-19). Answers the first message
// normally, then re-emits a result after `secondMs`.
if (cfg.mode === 'dupresult') {
  process.stdout.write(JSON.stringify({ type: 'system', subtype: 'init',
    model: 'fake', cwd: process.cwd(), tools: [], mcp_servers: [] }) + '\n')
  const os_ = require('os'), path = require('path')
  const home = process.env.USERPROFILE || process.env.HOME || os_.homedir()
  const dir = path.join(home, '.claude', 'projects',
    process.cwd().replace(/[\\/:]+/g, '-').replace(/^-+/, ''))
  fs.mkdirSync(dir, { recursive: true })
  const sid = arg('--session-id') || arg('--resume') || 'no-session'
  const tpath = path.join(dir, sid + '.jsonl')
  const rec = (o) => fs.appendFileSync(tpath, JSON.stringify(
    Object.assign({ timestamp: new Date().toISOString() }, o)) + '\n')
  const result = (extra) => process.stdout.write(JSON.stringify(Object.assign({
    type: 'result', subtype: 'success', is_error: false, result: 'ok',
    usage: { input_tokens: 1200 }, total_cost_usd: 0.0001 }, extra || {})) + '\n')
  let served = 0
  let buf = ''
  process.stdin.setEncoding('utf8')
  process.stdin.on('data', (d) => {
    buf += d
    let i
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1)
      if (!line) continue
      let ev; try { ev = JSON.parse(line) } catch (e) { continue }
      if (ev.type !== 'user') continue
      const c = ev.message && ev.message.content
      const text = typeof c === 'string' ? c
        : (c || []).map((b) => b.text || '').join('')
      served += 1
      rec({ type: 'user', message: { role: 'user', content: text } })
      // the reply is NUMBERED so a test can poll for "the Nth boundary has
      // been reached" instead of sleeping a fixed 0.6 s and hoping. Written
      // immediately before result(), so its appearance means the boundary is
      // microseconds away (redteam 2026-08-19: a fixed sleep is a false-RED
      // flake source under the full suite's load)
      const msg = { role: 'assistant', model: 'fake',
                    content: [{ type: 'text', text: 'ack-' + served + '.' }],
                    usage: { input_tokens: 1200 } }
      process.stdout.write(JSON.stringify({ type: 'assistant', message: msg }) + '\n')
      rec({ type: 'assistant', message: msg })
      // A subagent finishing MID-TURN, while stdin is still open — the only
      // shape that isolates the parent_tool_use_id guard. A LATE sidechain
      // result is already refused by stdin_open, so testing only that one
      // left guard 2 unpinned (it passed with the guard reverted).
      if (cfg.sidechainMid && served === 1) {
        result({ parent_tool_use_id: 'toolu_midturn',
                 total_cost_usd: 9.99, duration_ms: 424242,
                 usage: { input_tokens: 900000 },
                 permission_denials: [{ tool_name: 'Bash',
                                        tool_input: { command: 'rm -rf /' } }] })
      }
      // `slowSecondMs`: hold message №2's result back so the straggler
      // scheduled after №1 lands while №2 is STILL being answered — the
      // boundary that FEEDS, where stdin is never closed and `stdin_open`
      // cannot discriminate (redteam round 2, measured: two paid messages
      // booked $0). Both messages still get a real result eventually.
      if (cfg.slowSecondMs && served === 2) {
        const cum = 0.0002   // total_cost_usd is session-CUMULATIVE
        setTimeout(() => result({ total_cost_usd: cum }), cfg.slowSecondMs)
        continue
      }
      result()
      if (served === 1) {
        // The straggler, well after orgtree closed stdin. POISONED NUMBERS on
        // the sidechain variant: identical numbers to the boundary result made
        // the "not a boundary" checks unfalsifiable — they passed with the
        // guard reverted, because part 1 caught the ValueError and the message
        // still arrived (redteam 2026-08-19). These are numbers the turn must
        // never book. And `is_error: true` on the out-of-band variant, which is
        // the REAL shape of the CLI's error_during_execution — with is_error
        // false the straggler clobbered `res` harmlessly and hid the finding
        // that a successful paid turn books zero cost.
        setTimeout(() => {
          if (cfg.resultlessStraggler) {
            // ⚠ written RAW, not through result(): that helper's base carries
            // `result: 'ok'`, and Object.assign only overrides keys the extra
            // names — so a "resultless" straggler built through it still had a
            // result string, `err_blob` was non-empty, and the fixture quietly
            // tested the failure path instead of the success path it was
            // written for. (Caught by measuring the org doc when the check
            // failed with the fix IN place: "turn failed: ok".)
            process.stdout.write(JSON.stringify({
              type: 'result', subtype: 'error_during_execution',
              is_error: true, duration_ms: 0, num_turns: 0,
              total_cost_usd: 0, usage: {}, modelUsage: {},
              permission_denials: [],
              errors: ['stream closed unexpectedly'] }) + '\n')
          } else result(cfg.sidechain
            ? { parent_tool_use_id: 'toolu_straggler',
                total_cost_usd: 9.99, duration_ms: 424242,
                usage: { input_tokens: 900000 },
                permission_denials: [{ tool_name: 'Bash',
                                       tool_input: { command: 'rm -rf /' } }] }
            : cfg.limitStraggler
              // the limit reported ONLY on the out-of-band result: refusing
              // the event as a boundary must not discard what it REPORTS
              ? { subtype: 'error_during_execution', is_error: true,
                  result: 'Claude AI usage limit reached|' + LIMIT_EPOCH }
              : { subtype: 'error_during_execution', is_error: true,
                  result: 'Error during execution' })
          // …and, for the real shape, the CLI's own `$3(1)`: a non-zero exit
          // with NOTHING written to stderr. `stragglerExit` overrides it so
          // one variant can exit ZERO — that is the only way to isolate the
          // success-path `turn_paid` fold, since a non-zero exit is rescued
          // by the exit-code fallback instead (both guards protect the same
          // dollars, and a check that cannot tell them apart pins neither).
          setTimeout(() => process.exit(
            cfg.stragglerExit != null ? cfg.stragglerExit
              : (cfg.resultlessStraggler ? 1 : 0)), cfg.exitMs || 1500)
        }, cfg.secondMs || 900)
      }
    }
  })
  return
}
if (cfg.mode === 'errresult' || cfg.mode === 'errecho') {
  // consumes the message, writes NOTHING to the transcript, answers with an
  // is_error result — the shape that loses mail (suite defect 1)
  process.stdout.write(JSON.stringify({ type: 'system', subtype: 'init',
    model: 'fake', cwd: process.cwd(), tools: [], mcp_servers: [] }) + '\n')
  let buf = ''
  process.stdin.setEncoding('utf8')
  process.stdin.on('data', (d) => {
    buf += d
    let i
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1)
      if (!line) continue
      let ev; try { ev = JSON.parse(line) } catch (e) { continue }
      if (ev.type !== 'user') continue
      if (cfg.mode === 'errecho') {
        // the CLI wrote the user record to its transcript, THEN failed
        const c = ev.message && ev.message.content
        const text = typeof c === 'string' ? c
          : (c || []).map((b) => b.text || '').join('')
        const os_ = require('os'), path = require('path')
        const home = process.env.USERPROFILE || process.env.HOME || os_.homedir()
        const dir = path.join(home, '.claude', 'projects',
          process.cwd().replace(/[\\/:]+/g, '-').replace(/^-+/, ''))
        fs.mkdirSync(dir, { recursive: true })
        const sid = arg('--session-id') || arg('--resume') || 'no-session'
        fs.appendFileSync(path.join(dir, sid + '.jsonl'), JSON.stringify({
          type: 'user', message: { role: 'user', content: text },
          timestamp: new Date().toISOString() }) + '\n')
      }
      setTimeout(() => {
        process.stdout.write(JSON.stringify({ type: 'result',
          subtype: 'error_during_execution', is_error: true,
          result: cfg.errText || 'API Error: 500 overloaded_error',
          usage: {}, total_cost_usd: 0 }) + '\n')
        process.exit(0)
      }, cfg.delayMs || 60)
    }
  })
  return
}
require(process.env.FAKECLI_REAL)
"""


def write_wrapper() -> None:
    with open(WRAP, "w", encoding="utf-8") as f:
        f.write(WRAP_JS)


def set_cfg(default: dict | None = None, wrap: dict | None = None,
            **per_node) -> None:
    """Reprogram the fake CLI for the NEXT launch (it re-reads every launch)."""
    cfg: dict = {"default": dict(default or {})}
    cfg.update(per_node)
    if wrap:
        cfg["wrap"] = wrap
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f)


def port_free(p: int, tries: int = 100) -> None:
    for _ in range(tries):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return
        except OSError:
            s.close()
            time.sleep(0.1)
    raise RuntimeError(f"port {p} never freed")


#: the pre-fix queue drain, restored at runtime by monkeypatch so the suite can
#: measure before AND after without touching git (the `--legacy-client` idiom
#: from the message-visibility suite). `_run_one_turn` is unchanged; only who
#: calls the follow-up, and how, differs.
LEGACY_DRAIN = (
    "from orgtree import supervisor as s\n"
    "_one = s._run_one_turn\n"
    "def legacy(slug, nid, text):\n"
    "    nxt = _one(slug, nid, text)\n"
    "    if nxt is not None:\n"
    "        legacy(slug, nid, nxt)\n"
    "s._run_turn = legacy\n")


def start_backend(max_turns: int = 16, steer_hook: str = "0",
                  turn_timeout: int = 60, recursion: int = 0,
                  legacy_drain: bool = False) -> None:
    global PROC
    stop_backend()
    port_free(PORT)
    env = dict(os.environ)
    env.update({
        "ORGTREE_DATA": DATA, "USERPROFILE": HOME, "HOME": HOME,
        "ORGTREE_PORT": str(PORT),
        "FAKECLI_CONFIG": CFG,
        "FAKECLI_REAL": os.path.join(_HERE, "fakecli.js").replace("\\", "/"),
        "ORGTREE_CLAUDE_CLI": WRAP,
        "ORGTREE_MAX_TURNS": str(max_turns),
        "ORGTREE_STEER_HOOK": steer_hook,
        # a hung fake CLI is IDLE — since the 2026-08-04 reshape the idle
        # watchdog is the bound that fires; the ceiling rides along
        "ORGTREE_TURN_TIMEOUT": str(turn_timeout),
        "ORGTREE_TURN_IDLE": str(turn_timeout),
        "PYTHONPATH": os.path.join(_REPO, "backend"),
        "PYTHONIOENCODING": "utf-8",
        # ⚠ never claim anything the user's real backend holds: the sandbox
        # bridge listener defaults to 0.0.0.0:7362 and a second bind kills the
        # whole process at startup.
        "ORGTREE_BRIDGE_PORT": "0",
    })
    env.pop("ORGTREE_PUBLIC_PORT", None)
    env.pop("ORGTREE_EXPOSE_ADMIN", None)
    log = open(LOG, "a", encoding="utf-8")
    argv = [sys.executable, "-m", "orgtree.api"]
    if recursion or legacy_drain:
        # the tail-recursive queue drain is only reachable at depth ~900 with
        # the stock limit; lowering it makes the same shape observable in a
        # test that finishes this decade
        # ⚠ import the module under its real name and call main() — NOT
        # runpy.run_module(run_name="__main__"), which re-registers the
        # pydantic models under "__main__" and makes every POST body fail to
        # validate ("is not fully defined"), a 500 that looks like the very
        # recursion this is testing.
        argv = [sys.executable, "-c",
                (f"import sys; sys.setrecursionlimit({recursion})\n"
                 if recursion else "")
                + (LEGACY_DRAIN if legacy_drain else "")
                + "from orgtree import api; api.main()"]
    PROC = subprocess.Popen(argv, cwd=os.path.join(_REPO, "backend"),
                            env=env, stdout=log, stderr=log, text=True)
    for _ in range(300):
        if PROC.poll() is not None:
            raise RuntimeError(f"backend exited {PROC.returncode} at startup:\n"
                               + log_tail())
        try:
            urllib.request.urlopen(BASE + "/api/orgs", timeout=1).read()
            return
        except Exception:                                        # noqa: BLE001
            time.sleep(0.1)
    raise RuntimeError(f"backend never came up on {PORT}:\n" + log_tail())


def log_tail(n: int = 3000) -> str:
    try:
        return open(LOG, encoding="utf-8", errors="replace").read()[-n:]
    except OSError:
        return "(no log)"


def stop_backend(hard: bool = False) -> None:
    """hard=True is the crash: TerminateProcess, no cleanup, no atexit — and
    the job object reaps the CLI children exactly as it would in the wild."""
    global PROC
    if PROC is None:
        return
    try:
        PROC.kill() if hard else PROC.terminate()
        try:
            PROC.wait(timeout=10)
        except subprocess.TimeoutExpired:
            PROC.kill()
            PROC.wait(timeout=10)
    except OSError:
        pass
    PROC = None


def api(method: str, path: str, body=None, timeout: float = 30):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw else {}


def make_org(label: str, agents: int = 1, grant: int = 3) -> tuple[str, list[str]]:
    name = f"zz turnlife {len(_orgs)} {label}"[:60]
    r = api("POST", "/api/orgs", {"name": name})
    slug = r.get("slug") or r.get("org", {}).get("slug")
    _orgs.append(slug)
    nids = []
    for i in range(agents):
        h = api("POST", f"/api/orgs/{slug}/ops",
                {"op": "hire", "actor": USER, "parent": None, "tier": "haiku",
                 "grant": grant, "name": f"agent{i}", "charter": "a test agent",
                 "tools": {"bash": True, "web": False, "edit": False,
                           "subagents": False, "mcp": []},
                 "org_visibility": "team", "add_dirs": []})
        nids.append(h.get("node") or f"agent{i}")
    return slug, nids


def drop_orgs() -> None:
    for slug in list(_orgs):
        try:
            api("DELETE", f"/api/orgs/{slug}")
        except Exception:                                        # noqa: BLE001
            pass
    _orgs.clear()


def send(slug: str, nid: str, text: str) -> dict:
    return api("POST", f"/api/orgs/{slug}/nodes/{nid}/message", {"text": text})


def doc(slug: str) -> dict:
    p = os.path.join(DATA, "orgs", slug + ".json")
    for _ in range(30):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            time.sleep(0.05)
    raise RuntimeError(f"could not read {p}")


def transcript_text() -> str:
    """Everything every fake CLI has ever written, in this rig's HOME."""
    out = []
    for p in glob.glob(os.path.join(HOME, ".claude", "projects", "*", "*.jsonl")):
        try:
            out.append(open(p, encoding="utf-8", errors="replace").read())
        except OSError:
            pass
    return "\n".join(out)


def carriers(slug: str, nid: str, tok: str) -> dict[str, bool]:
    """Every place a posted mail can legitimately be waiting. The invariant is
    that at least one of these is true — always, after anything."""
    d = doc(slug)
    node = (d.get("nodes") or {}).get(nid) or {}
    fz = node.get("frozen") or {}
    return {
        "mailbox": any(tok in m.get("body", "")
                       for m in (d.get("mail") or {}).get(nid, [])),
        "journal": any(tok in m.get("body", "")
                       for b in (d.get("delivering") or {}).get(nid, [])
                       for m in (b.get("mail") or [])),
        "inflight": tok in ((node.get("inflight") or {}).get("text") or ""),
        "frozen": any(tok in t for t in (fz.get("resume_texts") or [])),
        "steered": any(tok in e.get("text", "")
                       for e in (d.get("steered_log") or {}).get(nid, [])),
        "transcript": tok in transcript_text(),
    }


def assert_carried(slug: str, nid: str, tok: str, where: str) -> dict[str, bool]:
    c = carriers(slug, nid, tok)
    if not any(c.values()):
        raise AssertionError(f"MAIL LOST at {where}: {tok} is in no carrier "
                             f"({c}); mail_log only = forensics, not delivery")
    return c


def wait_idle(slug: str, nid: str, secs: float = 45) -> bool:
    end = time.time() + secs
    while time.time() < end:
        try:
            c = api("GET", f"/api/orgs/{slug}/nodes/{nid}/chat?last=3")
        except Exception:                                        # noqa: BLE001
            time.sleep(0.2)
            continue
        if not c.get("busy"):
            return True
        time.sleep(0.2)
    return False


def wait_delivered(tok: str, secs: float = 40) -> bool:
    end = time.time() + secs
    while time.time() < end:
        if tok in transcript_text():
            return True
        time.sleep(0.25)
    return False


def wait_for(pred, secs: float = 20, step: float = 0.2) -> bool:
    end = time.time() + secs
    while time.time() < end:
        try:
            if pred():
                return True
        except Exception:                                        # noqa: BLE001
            pass
        time.sleep(step)
    return False


FAST = {"echoMs": 120, "firstEventMs": 220, "resultMs": 20}


# =============================================================== live checks

def count_in_transcript(tok: str) -> int:
    return transcript_text().count(tok)


def served_messages(session_id: str) -> int:
    """How many user messages the CLI was asked to answer in this session —
    the fake CLI writes one `type:"user"` record per message it serves."""
    n = 0
    for p in glob.glob(os.path.join(HOME, ".claude", "projects", "*",
                                    session_id + ".jsonl")):
        for line in open(p, encoding="utf-8", errors="replace"):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") == "user" and isinstance(
                    (rec.get("message") or {}).get("content"), str):
                n += 1
    return n


def live_subagents() -> None:
    """USER REPORT 2026-08-11: "when an agent spawns ephemeral subagents (not
    hires), their message fragments visually stack up in the UI and don't go
    away until the turn ends, flooding the output with misordered greyed-out
    tool usages and messages."

    The two halves of the live/durable contract disagreed. `read_chat` drops
    `isSidechain` rows, so a subagent fragment NEVER gets a durable twin —
    and `_sweep_live` retires a live row only when its twin appears. So every
    subagent fragment was unretirable by construction and sat on the desk
    until the end-of-turn clear. Parallel subagents interleave: the misorder.

    The fake CLI now emits the real shape (assistant events carrying
    `parent_tool_use_id`, recorded as isSidechain), with a 900k-token usage
    that a parent's context could never have — which is how the occupancy
    half is measured too."""
    print("\nephemeral subagents (user report 2026-08-11):")
    start_backend()
    # slow enough that the turn is still running when we look at it
    # `tools: 1` is the POSITIVE CONTROL. Without an agent-owned live row to
    # find, "no subagent rows" would also be satisfied by a filter that
    # suppressed everything, and the check would pass on a broken feed.
    set_cfg({**FAST, "subagents": 3, "tools": 1, "toolMs": 250,
             "resultMs": 2500})
    slug, (nid,) = make_org("subag")
    tok = token()
    send(slug, nid, f"go wide {tok}")

    seen: dict[str, object] = {"rows": [], "looked": 0}

    def _watch() -> None:
        """Poll the payload WHILE the turn runs — after it ends the live list
        is cleared either way, so a check that only looked afterwards would
        pass on the broken code."""
        for _ in range(120):
            c = api("GET", f"/api/orgs/{slug}/nodes/{nid}/chat?last=20")
            live = c.get("live") or []
            if live:
                seen["looked"] = int(seen["looked"]) + 1        # type: ignore[call-overload]
                for r in live:
                    cast_rows = seen["rows"]
                    assert isinstance(cast_rows, list)
                    cast_rows.append(r.get("text") or "")
            if not c.get("busy") and seen["looked"]:
                break
            time.sleep(0.25)
        rows = seen["rows"]
        assert isinstance(rows, list)
        # THE STIMULUS HAS TO BE PROVEN, or "no subagent rows" is satisfied
        # by a stand-in that never emitted any. The agent's own tool row is
        # useless as a control here — the stand-in records its durable twin in
        # the same breath, so _sweep_live retires it within milliseconds and a
        # poll rarely catches it. The transcript is the durable proof that the
        # subagent events really happened: the stand-in writes them as
        # isSidechain, exactly as the real CLI does.
        fixture("SUBAGENT-CHATTER" in transcript_text(),
                "the stand-in never emitted subagent events, so this run "
                "proves nothing about what the live feed filters")
        chatter = [t for t in rows if "SUBAGENT-CHATTER" in str(t)
                   or str(t).startswith("Grep")]
        assert not chatter, (
            f"{len(chatter)} subagent fragment(s) reached the live feed. They "
            f"have no durable twin (read_chat drops isSidechain), so _sweep_live "
            f"can never retire them and they pile up until the turn ends: "
            f"{chatter[:5]}")
    check("subagents · a subagent's fragments never enter the live feed",
          _watch)

    wait_idle(slug, nid, 30)
    check("subagents · …and the agent's own reply still arrives", lambda: (
        None if wait_delivered(tok, 30)
        else (_ for _ in ()).throw(AssertionError(carriers(slug, nid, tok)))))

    def _no_spurious_compaction() -> None:
        """⚠ Measure the COMPACTION, not the occupancy reading. Occupancy is
        the obvious field and it is the wrong one: a 900k reading crosses the
        threshold, the split runs, and the split RESETS the successor's
        occupancy — so the broken code ends with a small number and a check on
        it passes for the wrong reason, its evidence erased by the damage.
        The generation and the bearer are durable."""
        n = doc(slug)["nodes"][nid]
        assert not n.get("predecessor") and (n.get("generation") or 0) == 0, (
            f"the agent was COMPACTED by its subagent's context size — "
            f"generation {n.get('generation')}, bearer {n.get('predecessor')}. "
            f"Occupancy drives the split, and a subagent has its own window, "
            f"so a big one compacts a parent nowhere near its own limit")
    check("subagents · …and a subagent's context never compacts the AGENT",
          _no_spurious_compaction)
    set_cfg(FAST)


def live_kill_sweep() -> None:
    """Kill the BACKEND at eight points across ONE turn — before the drain,
    between drain and hand-off, before the echo, after the echo, after the
    confirm, mid-response, and after the result. After every death the mail
    must still be carried by something, and if the turn had not finished the
    restart must actually deliver it AGAIN (a token already sitting in the
    transcript proves nothing on its own — that check has to count)."""
    print("\nbackend death, swept across one turn (at-least-once):")
    # the fake CLI's clock here: transcript echo at 400 ms, the confirming
    # stdout event at 700 ms, the result at 1900 ms. Every delay below 1.9 s
    # therefore kills a turn that is genuinely in flight.
    delays = [0.0, 0.08, 0.20, 0.35, 0.55, 0.75, 1.20, 2.40]
    if QUICK:
        delays = [0.08, 0.35, 0.75, 2.40]
    for delay in delays:
        start_backend()
        set_cfg({"echoMs": 400, "firstEventMs": 700, "resultMs": 1200})
        slug, (nid,) = make_org(f"kill{int(delay * 100)}")
        tok = token()
        send(slug, nid, f"do the thing {tok}")
        time.sleep(delay)
        stop_backend(hard=True)
        c = assert_carried(slug, nid, tok, f"t+{delay:.2f}s")
        held = ",".join(k for k, v in c.items() if v)
        before = count_in_transcript(tok)
        # was a turn actually in flight when the process died?
        inflight = bool((doc(slug)["nodes"][nid].get("inflight") or {}))

        def _still(slug=slug, nid=nid, tok=tok, held=held, before=before,
                   inflight=inflight) -> None:
            start_backend()             # reconcile runs at startup
            if not inflight:
                # the turn had already ended — the contract is only that the
                # mail is somewhere, and it is (asserted above)
                return
            if not wait_for(lambda: count_in_transcript(tok) > before, 40):
                raise AssertionError(
                    f"the interrupted turn was never re-delivered (held at "
                    f"kill: {held}, {before} transcript copies before, "
                    f"{count_in_transcript(tok)} after); carriers now "
                    f"{carriers(slug, nid, tok)}")
        check(f"kill · t+{delay:.2f}s → carried by [{held}]"
              + (", replayed on restart" if inflight else ", turn already done"),
              _still)
        wait_idle(slug, nid, 25)
        drop_orgs()


def live_leash() -> None:
    """The child-process leash (№29): a CLI must not outlive the backend. An
    orphan keeps appending to the same transcript a restarted backend resumes
    — two writers, one file. Checked by the only portable evidence there is:
    the transcript stops growing the moment the backend dies."""
    print("\nthe child-process leash:")
    start_backend()
    # a turn that keeps WRITING for several seconds after the kill point
    set_cfg({"echoMs": 50, "firstEventMs": 120, "tools": 8, "toolMs": 400,
             "resultMs": 20})
    slug, (nid,) = make_org("leash")
    send(slug, nid, f"long chatty turn {token()}")
    time.sleep(1.2)                       # mid-turn, records being written
    size0 = len(transcript_text())
    stop_backend(hard=True)
    time.sleep(0.4)
    size1 = len(transcript_text())
    time.sleep(3.0)                       # the turn had ~2 s of writing left
    size2 = len(transcript_text())

    def _leashed() -> None:
        if size2 != size1:
            raise AssertionError(
                f"an orphaned CLI kept writing after the backend died: "
                f"{size1} → {size2} bytes (+{size2 - size1}) — a restarted "
                f"backend resumes this same session id, so that is two "
                f"writers on one transcript")
        if size0 == 0:
            raise AssertionError("the turn never wrote anything — the probe "
                                 "measured nothing")
    check(f"leash · the CLI dies with the backend (transcript frozen at "
          f"{size1} bytes)", _leashed)
    drop_orgs()


def live_cli_death() -> None:
    """Kill the CLI (not the backend) at five points. The turn dies; the mail
    must fold back and the node must be usable again."""
    print("\nCLI death mid-turn (fold-back):")
    start_backend()
    for crash_at, label in [(1, "before it reads stdin"),
                            (200, "before the transcript echo"),
                            (500, "after the echo, before the confirm"),
                            (900, "after the confirm, before the result")]:
        set_cfg({"echoMs": 400, "firstEventMs": 700, "resultMs": 30,
                 "crashAtMs": crash_at})
        slug, (nid,) = make_org(f"crash{crash_at}")
        tok = token()
        send(slug, nid, f"crashy {tok}")
        wait_idle(slug, nid, 30)
        time.sleep(0.4)
        c = carriers(slug, nid, tok)
        held = ",".join(k for k, v in c.items() if v) or "NOTHING"

        def _carried(c=c, crash_at=crash_at) -> None:
            if not any(c.values()):
                raise AssertionError(f"MAIL LOST (crashAtMs={crash_at}): {c}")
        check(f"clicrash · {label} → carried by [{held}]", _carried)

        if c["mailbox"] and c["transcript"]:
            # THE DOCUMENTED KNOWN EXCEPTION (message-visibility suite §5): the
            # CLI died after echoing the message into its transcript and before
            # its first stdout event, so the unconfirmed batch folded back into
            # the mailbox while the transcript already shows it. Measured here
            # rather than assumed: the desk renders it TWICE, and — unlike
            # every other duplicate in that family — it never resolves.
            ch = api("GET", f"/api/orgs/{slug}/nodes/{nid}/chat?last=300")
            bubbles = sum(1 for m in ch["messages"]
                          if m.get("role") == "user" and tok in (m.get("text") or ""))
            rows = sum(1 for m in (ch.get("pending_mail") or [])
                       if tok in (m.get("body") or ""))

            # PROMOTED 2026-08-04. This was pinned as a known exception —
            # the CLI died after echoing the message and before its first
            # stdout event, the unconfirmed batch folded back into the
            # mailbox, and the desk rendered bubble AND pending row forever.
            # The recommendation this suite made was taken: `node_chat` now
            # applies its `_in_transcript` evidence test to the MAILBOX rows
            # as well as the journal's, so the duplicate is removed at the
            # display layer while the fold-back — the only thing that gets
            # the agent re-asked — is untouched. Delivery is still
            # at-least-once; only the second RENDER is gone.
            def _dup(bubbles=bubbles, rows=rows) -> None:
                if bubbles + rows != 1:
                    raise AssertionError(
                        f"expected exactly one copy on screen, saw {bubbles} "
                        f"bubble(s) + {rows} pending row(s)")
            check(f"clicrash · {label} → exactly one copy on screen "
                  f"({bubbles} bubble + {rows} pending row)", _dup)

        # and it must actually reach the agent on the next nudge
        set_cfg(FAST)
        if c["mailbox"]:
            tok2 = token()
            send(slug, nid, f"followup {tok2}")

            def _redelivered(tok=tok, nid=nid, slug=slug) -> None:
                if not wait_delivered(tok, 30):
                    raise AssertionError(
                        f"folded-back mail never reached the agent: "
                        f"{carriers(slug, nid, tok)}")
            check(f"clicrash · {label} → the next turn delivers it",
                  _redelivered)
        wait_idle(slug, nid, 20)
    drop_orgs()


def live_error_result() -> None:
    """A turn that ends in an is_error `result` with nothing in the transcript.
    The result event confirms the journal (C1: any non-system event), so the
    drained batch is dropped — and nothing folds it back."""
    print("\na turn that fails with an error result:")
    start_backend()
    set_cfg(FAST, wrap={"default": {"mode": "errresult",
                                    "errText": "API Error: 500 "
                                               "overloaded_error"}})
    slug, (nid,) = make_org("errresult")
    tok = token()
    send(slug, nid, f"please do X {tok}")
    wait_idle(slug, nid, 30)
    time.sleep(0.5)
    c = carriers(slug, nid, tok)
    held = ",".join(k for k, v in c.items() if v) or "NOTHING"

    def _carried() -> None:
        if not any(c.values()):
            raise AssertionError(
                f"MAIL LOST: a failed turn confirmed the journal on its own "
                f"error result and nothing folded it back ({c})")
    check(f"errresult · error-result turn keeps the mail [{held}]", _carried)

    def _driveable() -> None:
        set_cfg(FAST)
        if not wait_delivered(tok, 5):
            send(slug, nid, f"ping {token()}")
            if not wait_delivered(tok, 30):
                raise AssertionError(f"never delivered: {carriers(slug, nid, tok)}")
    check("errresult · and it still reaches the agent afterwards", _driveable)
    wait_idle(slug, nid, 20)

    # the same failure one notch later: the CLI DID echo the message into its
    # transcript before erroring. The mail still leaves every delivery carrier,
    # but the record survives — a weaker loss, and the one worth separating,
    # because a resumed session carries that record back into context.
    set_cfg({"echoMs": 60, "firstEventMs": 100000, "resultMs": 20,
             "crashAtMs": 0, "hang": False, "replyText": "x"},
            wrap={"default": {"mode": "errecho", "delayMs": 400,
                              "errText": "API Error: 500 overloaded_error"}})
    slug2, (nid2,) = make_org("errecho")
    tok2 = token()
    send(slug2, nid2, f"echoed then failed {tok2}")
    wait_idle(slug2, nid2, 30)
    time.sleep(0.5)
    c2 = carriers(slug2, nid2, tok2)
    held2 = ",".join(k for k, v in c2.items() if v) or "NOTHING"
    check(f"errresult · echoed-then-failed keeps the mail [{held2}]", lambda: (
        None if any(c2.values())
        else (_ for _ in ()).throw(AssertionError(c2))))
    wait_idle(slug2, nid2, 20)
    drop_orgs()


def live_second_result() -> None:
    """USER REPORT 2026-08-19: "I/O operation on closed file" appears at an
    agent's turn end, and the agent has undelivered mail from a subordinate.

    The mechanism: orgtree closes the CLI's stdin at a result boundary that
    finds the queue empty. A SECOND result event for the same turn (the CLI
    has out-of-band result paths — the stream-json writer's own
    error_during_execution, error_max_turns) then re-enters the boundary
    branch; if anything was queued in the interval, it is fed down the closed
    pipe. `TextIOWrapper.write` raises `ValueError: I/O operation on closed
    file.`, NOT the OSError the branch caught — so it escaped to the turn's
    catch-all, became that cryptic banner, and took the in-memory carrier with
    it, folding the drained mail back into the mailbox undelivered. The mail
    is not lost (§ the at-least-once invariant holds) but it stops moving:
    exactly "an unreceived mail from one of its subordinates".

    And the deeper half, which the first version of this check MISSED: the
    straggler is a TOP-LEVEL result, so it also clobbered `res`. Carrying the
    CLI's real `is_error: true`, that turned a SUCCESSFUL, paid turn into
    `turn failed: …` — `_after_turn` never ran, so its `total_cost_usd` was
    never booked, no `turns` ring entry, and a permanent turn_error_log row on
    a turn that worked (redteam 2026-08-19 measured 0 turns booked, costs []).
    Hence `stdin_open`: the closed pipe is what tells a boundary from a
    straggler, because the boundary is what closed it.

    ⚠ Every check here asserts the MECHANISM ran, not merely that nothing bad
    happened — `send()` must report the message QUEUED (not steered), or the
    race was never entered and the check is vacuous."""
    print("\na second result event after stdin was closed (user 2026-08-19):")
    start_backend()
    set_cfg(FAST, wrap={"default": {"mode": "dupresult", "secondMs": 900}})
    slug, (nid,) = make_org("dupresult")
    tok = token()
    send(slug, nid, f"first message {tok}")
    # wait for the FIRST boundary to have closed stdin, then queue behind it —
    # the turn is still busy because the process has not exited
    time.sleep(0.6)
    tok2 = token()
    resp = send(slug, nid, f"queued behind the boundary {tok2}")
    wait_idle(slug, nid, 40)
    time.sleep(0.5)

    ch = api("GET", f"/api/orgs/{slug}/nodes/{nid}/chat?last=8")
    err = str(ch.get("last_error") or "")
    rows = [str(m.get("text") or "") for m in (ch.get("messages") or [])]
    banner = err + " | " + " ".join(r for r in rows if r.startswith("⚠"))
    node = (doc(slug).get("nodes") or {}).get(nid) or {}

    def _the_race_was_actually_entered() -> None:
        # the positive control: on a slow box the second send can land BEFORE
        # the boundary and take the steer lane, in which case nothing below
        # measures the bug at all
        assert not resp.get("steering"), \
            f"the second message steered instead of queueing — the boundary " \
            f"race was never entered, every check below is vacuous: {resp}"
    check("dupresult · the queued-at-the-boundary race was entered",
          _the_race_was_actually_entered)

    def _no_closed_file_banner() -> None:
        assert "closed file" not in banner, \
            f"the closed-file ValueError reached the desk as a turn failure: {banner!r}"
    check("dupresult · a second result event is not a turn failure",
          _no_closed_file_banner)

    def _the_turn_was_booked() -> None:
        # THE MONEY CHECK. A straggler carrying is_error clobbered `res` and
        # raised "turn failed", so a turn that really ran and really billed was
        # never accounted: no cost, no ring entry (measured 0 turns, costs []).
        turns = node.get("turns") or []
        assert turns, \
            f"a completed turn was never booked — a straggler result made it " \
            f"raise instead: last_error={err!r}, banner={banner!r}"
        assert float(node.get("cost_usd") or 0) > 0, \
            f"the turn ran and billed but booked $0: {node.get('cost_usd')!r}, " \
            f"turns={turns!r}"
        assert "turn failed" not in banner, \
            f"a successful turn was recorded as a failure: {banner!r}"
    check("dupresult · the successful turn is still booked and billed",
          _the_turn_was_booked)

    def _second_message_still_delivered() -> None:
        if wait_delivered(tok2, 5):
            return
        # requeued rather than delivered in-process is fine — but then a
        # follow-up turn must actually carry it
        set_cfg(FAST)
        send(slug, nid, f"ping {token()}")
        if not wait_delivered(tok2, 30):
            raise AssertionError(
                f"the queued message never reached the agent: "
                f"{carriers(slug, nid, tok2)}")
    check("dupresult · the message queued at the boundary still arrives",
          _second_message_still_delivered)
    wait_idle(slug, nid, 20)

    # …and the sidechain shape of the same event: a result carrying
    # parent_tool_use_id is a SUBAGENT's result, never the turn boundary.
    # Adopting it as `res` books a subagent's cost/duration/denials as the
    # turn's own. The straggler here carries POISONED numbers ($9.99, 900k
    # tokens, 424242 ms, a denial) precisely so this can be asserted: with
    # numbers equal to the boundary's, "not a boundary" is unfalsifiable.
    set_cfg(FAST, wrap={"default": {"mode": "dupresult", "secondMs": 900,
                                    "sidechain": True}})
    slug2, (nid2,) = make_org("subresult")
    tok3 = token()
    send(slug2, nid2, f"sidechain result {tok3}")
    time.sleep(0.6)
    tok4 = token()
    resp2 = send(slug2, nid2, f"queued behind a sidechain result {tok4}")
    wait_idle(slug2, nid2, 40)
    time.sleep(0.5)
    ch2 = api("GET", f"/api/orgs/{slug2}/nodes/{nid2}/chat?last=8")
    banner2 = str(ch2.get("last_error") or "") + " | " + " ".join(
        str(m.get("text") or "") for m in (ch2.get("messages") or [])
        if str(m.get("text") or "").startswith("⚠"))
    node2 = (doc(slug2).get("nodes") or {}).get(nid2) or {}
    check("dupresult · the sidechain race was entered too", lambda: (
        None if not resp2.get("steering")
        else (_ for _ in ()).throw(AssertionError(
            f"steered, not queued — check is vacuous: {resp2}"))))
    check("dupresult · a subagent's result is not a turn boundary", lambda: (
        None if "closed file" not in banner2
        else (_ for _ in ()).throw(AssertionError(banner2))))

    def _no_subagent_numbers_booked() -> None:
        cost = float(node2.get("cost_usd") or 0)
        assert cost < 1.0, \
            f"the subagent's $9.99 was booked as the turn's cost: {cost}"
        assert int(node2.get("occupancy") or 0) < 500000, \
            f"a subagent's 900k-token context became the node's occupancy: " \
            f"{node2.get('occupancy')!r} — a compaction split follows"
        assert not (node2.get("last_denials") or []), \
            f"a subagent's permission denial was attributed to the turn: " \
            f"{node2.get('last_denials')!r}"
        ring = node2.get("turns") or []
        assert ring, "the real turn was never booked at all"
        assert not any((t.get("ms") or 0) > 400000 for t in ring), \
            f"the subagent's 424242 ms was booked as a turn duration: {ring!r}"
    check("dupresult · none of the subagent's numbers reach the node",
          _no_subagent_numbers_booked)

    # …and the guard that the two scenarios above CANNOT see: a subagent
    # finishing MID-TURN, while stdin is still open. There `stdin_open` is
    # True, so `parent_tool_use_id` is the only thing standing between a
    # subagent's result and the turn boundary — reverting the guard closes a
    # live agent's stdin mid-turn and books $9.99 / 900k tokens / 424242 ms as
    # the turn's own. (Verified by reverting it: without this case all four
    # sidechain checks stayed green.)
    set_cfg(FAST, wrap={"default": {"mode": "dupresult", "sidechainMid": True,
                                    "secondMs": 900}})
    slug3, (nid3,) = make_org("midsub")
    tok5 = token()
    send(slug3, nid3, f"subagent finishes mid-turn {tok5}")
    wait_idle(slug3, nid3, 40)
    time.sleep(0.5)
    node3 = (doc(slug3).get("nodes") or {}).get(nid3) or {}
    ch3 = api("GET", f"/api/orgs/{slug3}/nodes/{nid3}/chat?last=8")
    banner3 = str(ch3.get("last_error") or "") + " | " + " ".join(
        str(m.get("text") or "") for m in (ch3.get("messages") or [])
        if str(m.get("text") or "").startswith("⚠"))

    def _midturn_subagent_result_is_not_the_boundary() -> None:
        assert wait_delivered(tok5, 20), \
            f"the message never reached the agent: {carriers(slug3, nid3, tok5)}"
        ring = node3.get("turns") or []
        assert ring, f"the turn was never booked: {banner3!r}"
        assert float(node3.get("cost_usd") or 0) < 1.0, \
            f"a mid-turn subagent's $9.99 became the turn's cost: " \
            f"{node3.get('cost_usd')!r}"
        assert int(node3.get("occupancy") or 0) < 500000, \
            f"a mid-turn subagent's 900k context became the occupancy: " \
            f"{node3.get('occupancy')!r}"
        assert not (node3.get("last_denials") or []), \
            f"a mid-turn subagent's denial was attributed to the turn: " \
            f"{node3.get('last_denials')!r}"
        assert not any((t.get("ms") or 0) > 400000 for t in ring), \
            f"the subagent's 424242 ms was booked as the turn's: {ring!r}"
    check("dupresult · a subagent finishing MID-TURN is not the boundary "
          "(stdin still open — the parent_tool_use_id guard alone)",
          _midturn_subagent_result_is_not_the_boundary)
    wait_idle(slug3, nid3, 20)

    # ── the boundary that FEEDS, where no flag can discriminate ──────────
    # Round 2 of the loop measured the round-1 fix as only PARTIALLY right:
    # `stdin_open` identifies a straggler only at the boundary that CLOSES
    # stdin. At a boundary that feeds the next queued message the pipe stays
    # open, so a straggler is indistinguishable from that message's own
    # result — and it clobbered `res`, so TWO real paid messages booked $0
    # and 0 turns. The fix is not a better guess: it is that reported spend
    # is booked however the turn ends.
    # ⚠ Both messages are sent BEFORE the first boundary, deliberately. Racing
    # a poll against the boundary made this a false RED under the full suite's
    # load (whichever lane the second send takes is a coin flip). Sent early it
    # takes the steer lane, the boundary folds it into the queue and FEEDS it
    # — the path under test — every time, on any machine.
    set_cfg({**FAST, "startMs": 250},
            wrap={"default": {"mode": "dupresult", "secondMs": 400,
                              "slowSecondMs": 2500}})
    slug4, (nid4,) = make_org("feedstrag")
    tok6, tok7 = token(), token()
    send(slug4, nid4, f"first of two {tok6}")
    send(slug4, nid4, f"fed at the boundary {tok7}")
    wait_idle(slug4, nid4, 60)
    time.sleep(0.5)
    node4 = (doc(slug4).get("nodes") or {}).get(nid4) or {}
    sid4 = str(node4.get("session_id") or "")

    def _fed_boundary_spend_survives() -> None:
        # the positive control, and it is about the MECHANISM: two messages
        # answered by ONE CLI process is exactly "the boundary fed the next
        # one", which is the case `stdin_open` cannot discriminate
        assert served_messages(sid4) >= 2, \
            f"the second message was not fed into the same process — the " \
            f"feeding boundary was never exercised: " \
            f"{served_messages(sid4)} served in {sid4}"
        assert wait_delivered(tok7, 20), \
            f"the fed message never arrived: {carriers(slug4, nid4, tok7)}"
        # THE MONEY. Whatever the straggler did to `res`, the dollars the CLI
        # reported must be on the node.
        assert float(node4.get("cost_usd") or 0) > 0, \
            f"two paid messages booked $0 — a straggler erased the turn's " \
            f"earnings: cost_usd={node4.get('cost_usd')!r}, " \
            f"turns={node4.get('turns')!r}"
        assert node4.get("turns"), \
            f"no turn was booked at all: {node4!r}"
    check("dupresult · a straggler at a FEEDING boundary cannot erase the "
          "turn's spend", _fed_boundary_spend_survives)

    # ── the straggler shape the real CLI actually emits ──────────────────
    # Every straggler above carries a `result` STRING, which makes `err_blob`
    # non-empty and sends the turn down the FAILURE path — where the spend was
    # already rescued. cli.js's stream-json catch block emits none: no
    # `result` key (the text rides `errors: []`), `total_cost_usd: 0`, and it
    # only sets an exit code, writing nothing to stderr. `err_blob` then came
    # out EMPTY, the turn took the SUCCESS path, and `_after_turn` booked the
    # straggler's $0 over a message that had really billed — recorded as a
    # clean completed turn costing nothing, with a CLI that had died and a
    # queued message unanswered. Round 3 of the loop found this still live
    # after two rounds; the fixture shape is why it survived them.
    set_cfg({**FAST, "startMs": 250},
            wrap={"default": {"mode": "dupresult", "secondMs": 400,
                              "slowSecondMs": 2500,
                              "resultlessStraggler": True}})
    slug6, (nid6,) = make_org("nullstrag")
    tok9, tok10 = token(), token()
    send(slug6, nid6, f"first of two {tok9}")
    send(slug6, nid6, f"fed at the boundary {tok10}")
    wait_idle(slug6, nid6, 60)
    time.sleep(0.5)
    node6 = (doc(slug6).get("nodes") or {}).get(nid6) or {}
    ch6 = api("GET", f"/api/orgs/{slug6}/nodes/{nid6}/chat?last=8")

    def _resultless_straggler_keeps_the_spend() -> None:
        assert served_messages(str(node6.get("session_id") or "")) >= 2, \
            "the feeding boundary was never exercised"
        assert float(node6.get("cost_usd") or 0) > 0, \
            f"a paid message booked $0 — the straggler's total_cost_usd:0 " \
            f"was booked over it: cost_usd={node6.get('cost_usd')!r}, " \
            f"turns={node6.get('turns')!r}"
    check("dupresult · the CLI's real (resultless, $0) straggler cannot erase "
          "the spend", _resultless_straggler_keeps_the_spend)

    def _a_dead_cli_is_not_a_clean_turn() -> None:
        # the other half, and the worse-presenting one: the CLI exited 1 with
        # an unanswered message, and nothing said so
        banner6 = str(ch6.get("last_error") or "") + " | " + " ".join(
            str(m.get("text") or "") for m in (ch6.get("messages") or [])
            if str(m.get("text") or "").startswith("⚠"))
        assert banner6.strip(" |"), \
            "the CLI exited non-zero with a message unanswered and the turn " \
            "was recorded as a clean success — silence is not success"
    check("dupresult · a CLI that exits non-zero in silence is still a failure",
          _a_dead_cli_is_not_a_clean_turn)

    # the same straggler, but the process exits ZERO — so the exit-code
    # fallback cannot rescue it and the SUCCESS-path `turn_paid` fold is the
    # only thing standing between a paid message and a $0 booking. Two guards
    # covering the same dollars is defence in depth; a check that cannot tell
    # them apart pins neither of them.
    set_cfg({**FAST, "startMs": 250},
            wrap={"default": {"mode": "dupresult", "secondMs": 400,
                              "slowSecondMs": 2500,
                              "resultlessStraggler": True,
                              "stragglerExit": 0}})
    slug7, (nid7,) = make_org("foldonly")
    tok11, tok12 = token(), token()
    send(slug7, nid7, f"first of two {tok11}")
    send(slug7, nid7, f"fed at the boundary {tok12}")
    wait_idle(slug7, nid7, 60)
    time.sleep(0.5)
    node7 = (doc(slug7).get("nodes") or {}).get(nid7) or {}

    def _success_path_fold_keeps_the_spend() -> None:
        assert served_messages(str(node7.get("session_id") or "")) >= 2, \
            "the feeding boundary was never exercised"
        ring = node7.get("turns") or []
        assert ring, f"no turn was booked: {node7!r}"
        # ⚠ the sharp assertion is the $0 ENTRY, not the total: if the message
        # the dead CLI never answered is ever redelivered by a follow-up turn,
        # that turn's dollars would satisfy a bare `cost_usd > 0` whether or
        # not this turn's were rescued. A ring entry sitting at exactly $0 is
        # the straggler's zero, and nothing else produces one.
        assert not any(float(t.get("cost") or 0) == 0 for t in ring), \
            f"a turn was booked at exactly $0 — `res` carried the straggler's " \
            f"total_cost_usd:0 and turn_paid was never folded in: {ring!r}"
        assert float(node7.get("cost_usd") or 0) > 0, \
            f"the turn's reported spend never reached the node: " \
            f"cost_usd={node7.get('cost_usd')!r}, turns={ring!r}"
    check("dupresult · a $0 straggler on a turn that ENDS CLEAN still books "
          "the reported spend", _success_path_fold_keeps_the_spend)

    # ── a usage limit that rides ONLY the refused straggler ──────────────
    # Refusing the straggler as a boundary must not throw away what it
    # REPORTS. Round 2 measured the first cut of this fix dropping a real
    # usage limit: node not frozen, turn booked as a clean success, and the
    # next turn burns against a live limit.
    set_cfg(FAST, wrap={"default": {"mode": "dupresult", "secondMs": 700,
                                    "limitStraggler": True}})
    slug5, (nid5,) = make_org("stragglimit")
    tok8 = token()
    send(slug5, nid5, f"limit rides the straggler {tok8}")
    wait_idle(slug5, nid5, 40)
    time.sleep(0.6)
    node5 = (doc(slug5).get("nodes") or {}).get(nid5) or {}

    def _limit_on_a_straggler_still_freezes() -> None:
        assert node5.get("frozen"), \
            f"a usage limit reported on the out-of-band result was dropped — " \
            f"the node is not frozen and will burn against a live limit " \
            f"next turn: frozen={node5.get('frozen')!r}"
    check("dupresult · a usage limit carried only by a straggler still freezes",
          _limit_on_a_straggler_still_freezes)

    def _sidechain_msg_delivered() -> None:
        if wait_delivered(tok4, 5):
            return
        set_cfg(FAST)
        send(slug2, nid2, f"ping {token()}")
        if not wait_delivered(tok4, 30):
            raise AssertionError(carriers(slug2, nid2, tok4))
    check("dupresult · and its queued message arrives too",
          _sidechain_msg_delivered)
    wait_idle(slug2, nid2, 20)
    drop_orgs()


def live_die_on_argv() -> None:
    """The C1 case in the flesh: a CLI that never reads stdin."""
    print("\na CLI that dies on argv (never reads stdin):")
    start_backend()
    set_cfg(FAST, wrap={"default": {"mode": "die",
                                    "errText": "error: unknown option --effort"}})
    slug, (nid,) = make_org("argvdie")
    tok = token()
    send(slug, nid, f"never seen {tok}")
    wait_idle(slug, nid, 30)
    time.sleep(0.4)
    c = carriers(slug, nid, tok)
    check("argvdie · the drained batch folds back into the mailbox", lambda: (
        None if c["mailbox"] and not c["transcript"]
        else (_ for _ in ()).throw(AssertionError(c))))
    check("argvdie · the failure is reported on the node", lambda: (
        lambda ch: None if (ch.get("last_error") or "")
        else (_ for _ in ()).throw(AssertionError(ch))
    )(api("GET", f"/api/orgs/{slug}/nodes/{nid}/chat?last=3")))
    set_cfg(FAST)
    send(slug, nid, f"retry {token()}")
    check("argvdie · a working CLI then delivers it", lambda: (
        None if wait_delivered(tok, 30)
        else (_ for _ in ()).throw(AssertionError(carriers(slug, nid, tok)))))
    wait_idle(slug, nid, 20)
    drop_orgs()


def live_ordering() -> None:
    print("\nordering and interleaving:")
    start_backend()
    set_cfg({"echoMs": 200, "firstEventMs": 350, "resultMs": 20})
    slug, (nid,) = make_org("order")
    toks = [token() for _ in range(6)]
    # concurrent senders, one node
    errs: list[str] = []

    def _one(i: int) -> None:
        try:
            send(slug, nid, f"msg{i} {toks[i]}")
        except Exception as e:                                   # noqa: BLE001
            errs.append(str(e))
    ths = [threading.Thread(target=_one, args=(i,)) for i in range(6)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    check("order · six concurrent sends all accepted", lambda: (
        None if not errs else (_ for _ in ()).throw(AssertionError(errs))))

    def _all_there() -> None:
        if not wait_for(lambda: all(t in transcript_text() for t in toks), 45):
            missing = [t for t in toks if t not in transcript_text()]
            raise AssertionError(f"{len(missing)} never delivered: {missing} "
                                 f"({[carriers(slug, nid, m) for m in missing]})")
    check("order · every one of the six reaches the agent", _all_there)

    def _in_order() -> None:
        # ⚠ NOT the order the threads were started in — six concurrent POSTs
        # have no such order. The property is that delivery preserves whatever
        # order `post_mail` actually recorded, which mail_log keeps verbatim.
        log = (doc(slug).get("mail_log") or {}).get(nid, [])
        posted = [t for e in log for t in toks if t in (e.get("body") or "")]
        if sorted(posted) != sorted(toks):
            raise AssertionError(f"mail_log lost some: {posted}")
        blob = transcript_text()
        pos = [blob.index(t) for t in posted]
        if pos != sorted(pos):
            raise AssertionError(
                f"posted {posted} but delivered in order "
                f"{[p for _, p in sorted(zip(pos, posted))]}")
    check("order · delivery preserves the order the mail was posted in",
          _in_order)
    wait_idle(slug, nid, 30)

    # a burst at a node that cannot run: all of it must survive
    slug2, (nid2,) = make_org("burst")
    api("POST", f"/api/orgs/{slug2}/ops",
        {"op": "retire", "actor": USER, "node": nid2})
    btoks = [token() for _ in range(25)]
    for i, t in enumerate(btoks):
        send(slug2, nid2, f"burst{i} {t}")
    check("order · 25 messages to an archived node all sit in its mailbox",
          lambda: (
              lambda box: None if all(any(t in b for b in box) for t in btoks)
              else (_ for _ in ()).throw(AssertionError(
                  f"{sum(1 for t in btoks if not any(t in b for b in box))} lost"))
          )([m["body"] for m in (doc(slug2).get("mail") or {}).get(nid2, [])]))
    api("POST", f"/api/orgs/{slug2}/ops",
        {"op": "rehire", "actor": USER, "node": nid2})
    check("order · rehire drives them and all 25 arrive", lambda: (
        None if wait_for(lambda: all(t in transcript_text() for t in btoks), 45)
        else (_ for _ in ()).throw(AssertionError(
            [t for t in btoks if t not in transcript_text()][:5]))))
    wait_idle(slug2, nid2, 30)
    drop_orgs()


def live_steer_race() -> None:
    """Steering that races the result boundary: a steer still pending when the
    response ends folds into the queue and is delivered as a user event."""
    print("\nsteering across the result boundary:")
    start_backend(steer_hook="1")
    set_cfg({"echoMs": 100, "firstEventMs": 200, "tools": 3, "toolMs": 350,
             "resultMs": 20})
    slug, (nid,) = make_org("steer")
    send(slug, nid, f"warm {token()}")
    time.sleep(0.6)
    toks = [token() for _ in range(4)]
    for i, t in enumerate(toks):
        send(slug, nid, f"mid{i} {t}")
        time.sleep(0.22)
    wait_idle(slug, nid, 45)
    time.sleep(0.5)

    def _no_loss() -> None:
        lost = []
        for t in toks:
            c = carriers(slug, nid, t)
            if not any(c.values()):
                lost.append((t, c))
        if lost:
            raise AssertionError(f"{len(lost)} steered messages lost: {lost}")
    check("steerrace · nothing sent during a response is lost", _no_loss)

    def _reached() -> None:
        d = doc(slug)
        blob = transcript_text() + json.dumps(d.get("steered_log") or {})
        missing = [t for t in toks if t not in blob]
        if missing:
            raise AssertionError(f"never reached the agent: {missing}")
    check("steerrace · each one reached the agent (hook context or a turn)",
          _reached)

    def _really_steered() -> None:
        log = (doc(slug).get("steered_log") or {}).get(nid) or []
        if not log:
            raise AssertionError(
                "no steered_log entries — the PostToolUse hook never fired, "
                "so this section measured the QUEUE path and not the steer "
                "path (check ORGTREE_STEER_HOOK and the fake CLI's `tools`)")
    check("steerrace · the steering hook really fired (not a queue test in "
          "disguise)", _really_steered)
    wait_idle(slug, nid, 30)
    drop_orgs()


def live_slots() -> None:
    print("\nturn-slot saturation (ORGTREE_MAX_TURNS=1):")
    start_backend(max_turns=1)
    set_cfg({"echoMs": 150, "firstEventMs": 300, "resultMs": 20})
    slug, nids = make_org("slots", agents=4, grant=2)
    toks = {n: token() for n in nids}
    for n in nids:
        send(slug, n, f"work {toks[n]}")

    def _drained() -> None:
        if not wait_for(lambda: all(t in transcript_text()
                                    for t in toks.values()), 60):
            missing = [n for n, t in toks.items() if t not in transcript_text()]
            raise AssertionError(f"starved: {missing}")
    check("slots · four agents, one slot — nothing starves", _drained)

    def _all_idle() -> None:
        for n in nids:
            if not wait_idle(slug, n, 30):
                raise AssertionError(f"{n} never went idle — a slot leaked")
    check("slots · every agent goes idle again (no slot leak, no deadlock)",
          _all_idle)

    # the slot is a RESOURCE bound, not a serialiser: a second round on the
    # same four agents must not wedge on state left over from the first
    toks2 = {n: token() for n in nids}
    for n in nids:
        send(slug, n, f"again {toks2[n]}")
    check("slots · a second round through the same single slot also drains",
          lambda: (
              None if wait_for(lambda: all(t in transcript_text()
                                           for t in toks2.values()), 60)
              else (_ for _ in ()).throw(AssertionError(
                  [n for n, t in toks2.items() if t not in transcript_text()]))))
    for n in nids:
        wait_idle(slug, n, 30)
    drop_orgs()


def live_queue_order_under_saturation() -> None:
    print("\nqueued work drains in order while the slot is held:")
    start_backend(max_turns=1, turn_timeout=12)
    # agent0 hangs and holds the only slot; agent1's messages all queue
    set_cfg({"echoMs": 80, "firstEventMs": 160, "resultMs": 20},
            agent0={"hang": True, "echoMs": 80, "firstEventMs": 160})
    slug, nids = make_org("qorder", agents=2, grant=2)
    hog, worker = nids
    send(slug, hog, f"hang {token()}")
    time.sleep(1.0)
    toks = [token() for _ in range(5)]
    for i, t in enumerate(toks):
        send(slug, worker, f"q{i} {t}")
        time.sleep(0.05)
    check("qorder · the blocked node reports waiting, not running", lambda: (
        lambda ch: None if ch.get("busy") and not ch.get("responding")
        else (_ for _ in ()).throw(AssertionError(ch))
    )(api("GET", f"/api/orgs/{slug}/nodes/{worker}/chat?last=3")))
    check("qorder · every queued message is still in the mailbox", lambda: (
        lambda box: None
        if sum(1 for t in toks if any(t in b for b in box)) == 5
        else (_ for _ in ()).throw(AssertionError(box)))
        ([m["body"] for m in (doc(slug).get("mail") or {}).get(worker, [])]))
    # the hog times out (12 s) and releases the slot
    check("qorder · the slot is released and all five drain", lambda: (
        None if wait_for(lambda: all(t in transcript_text() for t in toks), 60)
        else (_ for _ in ()).throw(AssertionError(
            [t for t in toks if t not in transcript_text()]))))

    def _order() -> None:
        blob = transcript_text()
        pos = [blob.index(t) for t in toks]
        if pos != sorted(pos):
            raise AssertionError(f"out of order: {pos}")
    check("qorder · in posting order", _order)
    wait_idle(slug, worker, 30)
    drop_orgs()


def live_deep_queue() -> None:
    """`_run_turn` drains the queue by CALLING ITSELF from its own `finally`,
    so every queued message a separate CLI process consumes costs one stack
    frame that never unwinds. Under the stock 1000-frame limit that is ~900
    messages; the backend is started here with a lowered limit so the same
    shape is observable in a test that finishes.

    ⚠ The failure is not an error the user ever sees: the RecursionError is
    raised inside the `finally`, so it escapes the turn's own `except`, kills
    the worker thread, and leaves `busy=True` with a non-empty queue — the
    node is wedged for the life of the backend and every later message just
    joins the queue."""
    print("\nqueue depth (the tail-recursive drain):")
    if QUICK:
        note("deepqueue skipped under --quick (~190 turns)")
        return
    # headroom in a fresh worker thread ≈ `limit`; queue comfortably MORE than
    # that, or the stack happens to run out on the very last item (measured:
    # at exactly 190 the final frame emptied the queue first, so the node went
    # idle and the wedge hid itself)
    limit, n_msgs = 200, 215

    def build(legacy: bool) -> tuple[str, str, int | None]:
        start_backend(max_turns=1, recursion=limit, turn_timeout=8,
                      legacy_drain=legacy)
        set_cfg({"echoMs": 1, "firstEventMs": 1, "resultMs": 5},
                agent0={"hang": True, "echoMs": 1, "firstEventMs": 1},
                agent1={"echoMs": 1, "firstEventMs": 1, "resultMs": 5,
                        # one CLI process PER queued message — the in-process
                        # boundary feed never recurses, a dying child does
                        "crashAfter": 1})
        slug, (hog, worker) = make_org("deep" + ("L" if legacy else "F"),
                                       agents=2, grant=2)
        send(slug, hog, f"hold the only slot {token()}")
        time.sleep(1.0)
        # blocked on the semaphore ⇒ busy but NOT responding ⇒ all of it QUEUES
        for i in range(n_msgs):
            send(slug, worker, f"deep{i} {token()}")
        q = api("GET", f"/api/orgs/{slug}/nodes/{worker}/chat?last=3").get("queued")
        return slug, worker, q

    def drained(slug: str, worker: str, secs: float = 200) -> bool:
        # the hog times out at 8 s and frees the slot; the queue then drains
        return wait_for(lambda: not api(
            "GET", f"/api/orgs/{slug}/nodes/{worker}/chat?last=3").get("busy"),
            secs)

    # ① the PRE-FIX shape, restored by monkeypatch (never git) — the
    #    measurement the fix is against
    mark = len(log_tail(10_000_000))
    slug, worker, q = build(legacy=True)
    check(f"deepqueue · {n_msgs} messages queue behind the held slot "
          f"(queued={q})", lambda: (
              None if (q or 0) >= n_msgs - 3
              else (_ for _ in ()).throw(AssertionError(f"queued={q}"))))
    wedged = not drained(slug, worker, 200)
    left = api("GET", f"/api/orgs/{slug}/nodes/{worker}/chat?last=3")
    rec = "RecursionError" in log_tail(10_000_000)[mark:]

    def _legacy_wedges(wedged=wedged, left=left, rec=rec) -> None:
        if not (wedged and rec):
            raise AssertionError(
                f"the pre-fix drain did NOT wedge here (busy={left.get('busy')}, "
                f"queued={left.get('queued')}, RecursionError={rec}) — either "
                f"the recursion limit no longer bites at this depth or the "
                f"monkeypatch stopped matching the old shape")
    check(f"deepqueue · PRE-FIX: the tail-recursive drain wedges the node "
          f"(busy={left.get('busy')}, {left.get('queued')} still queued, "
          f"RecursionError in the log)", _legacy_wedges)
    drop_orgs()

    # ② the shipped shape, same depth, same limit
    mark = len(log_tail(10_000_000))
    slug, worker, q = build(legacy=False)
    check(f"deepqueue · a {n_msgs}-deep queue drains without wedging the node",
          lambda: (
              None if drained(slug, worker, 200)
              else (_ for _ in ()).throw(AssertionError(
                  api("GET",
                      f"/api/orgs/{slug}/nodes/{worker}/chat?last=3")))))
    check("deepqueue · …and without a RecursionError in the log", lambda: (
        None if "RecursionError" not in log_tail(10_000_000)[mark:]
        else (_ for _ in ()).throw(AssertionError(
            "the drain recursed until the stack ran out"))))
    drop_orgs()


def live_command_queue() -> None:
    """A slash command queued behind mail: it must run VERBATIM (no envelope
    — the '/' has to be the first character the CLI sees) and must not drain
    the mailbox on the way past."""
    print("\na command queued behind mail:")
    start_backend(max_turns=1, turn_timeout=10)
    set_cfg({"echoMs": 30, "firstEventMs": 80, "resultMs": 10},
            agent0={"hang": True, "echoMs": 20, "firstEventMs": 60},
            agent1={"echoMs": 30, "firstEventMs": 80, "resultMs": 10})
    slug, (hog, nid) = make_org("cmdq", agents=2, grant=2)
    send(slug, hog, f"hold the slot {token()}")
    time.sleep(1.0)
    tok = token()
    send(slug, nid, f"real mail {tok}")             # queues (slot held)
    cmdtok = "/help"
    r = api("POST", f"/api/orgs/{slug}/nodes/{nid}/message", {"text": cmdtok})
    check("cmdq · the command is accepted and queued behind the mail", lambda: (
        None if r.get("command") and (r.get("queued") or 0) >= 1
        else (_ for _ in ()).throw(AssertionError(r))))
    check("cmdq · a command posts no mail", lambda: (
        lambda box: None if not any("/help" in m.get("body", "") for m in box)
        else (_ for _ in ()).throw(AssertionError(box))
    )((doc(slug).get("mail") or {}).get(nid, [])))
    check("cmdq · both run once the slot frees", lambda: (
        None if wait_for(lambda: tok in transcript_text()
                         and "/help" in transcript_text(), 90)
        else (_ for _ in ()).throw(AssertionError(
            f"mail={tok in transcript_text()} "
            f"cmd={'/help' in transcript_text()}"))))

    def _verbatim() -> None:
        # the command must reach the CLI un-enveloped: its own user record,
        # with "/" at position 0
        for p in glob.glob(os.path.join(HOME, ".claude", "projects", "*",
                                        "*.jsonl")):
            for line in open(p, encoding="utf-8", errors="replace"):
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                c = (rec.get("message") or {}).get("content")
                if isinstance(c, str) and c.strip() == "/help":
                    return
        raise AssertionError("the command was never delivered verbatim — it "
                             "arrived wrapped in an envelope")
    check("cmdq · the command arrived verbatim, '/' at position 0", _verbatim)
    for n in (hog, nid):
        wait_idle(slug, n, 30)
    drop_orgs()


def live_timeout() -> None:
    print("\nturn timeout:")
    start_backend(turn_timeout=6)
    set_cfg({"echoMs": 80, "firstEventMs": 160, "hang": True})
    slug, (nid,) = make_org("timeout")
    tok = token()
    send(slug, nid, f"hang forever {tok}")
    # either watchdog may win the race to kill a hung turn (implementer note
    # on landing): the 6 s turn ceiling says "timed out", the idle watchdog
    # says "turn killed: no CLI output" — both are the kill-and-report path
    # this check exists to prove, so it accepts both phrasings
    check("timeout · the turn is killed and reported", lambda: (
        None if wait_for(lambda: any(w in str(
            api("GET", f"/api/orgs/{slug}/nodes/{nid}/chat?last=3")
            .get("last_error") or "") for w in ("timed out", "turn killed")),
            30)
        else (_ for _ in ()).throw(AssertionError(
            api("GET", f"/api/orgs/{slug}/nodes/{nid}/chat?last=3")))))
    check("timeout · the mail is not lost with the killed turn", lambda: (
        lambda c: None if any(c.values())
        else (_ for _ in ()).throw(AssertionError(c))
    )(carriers(slug, nid, tok)))
    set_cfg(FAST)
    check("timeout · a later turn delivers it", lambda: (
        send(slug, nid, f"again {token()}"),
        None if wait_delivered(tok, 30)
        else (_ for _ in ()).throw(AssertionError(carriers(slug, nid, tok))))[-1])
    wait_idle(slug, nid, 20)
    drop_orgs()


def live_freeze() -> None:
    print("\nusage-limit freeze and resume:")
    start_backend()
    set_cfg({**FAST, "usageLimit": True})
    slug, (nid,) = make_org("freeze")
    tok = token()
    send(slug, nid, f"work {tok}")
    check("freeze · a usage-limit result freezes the node", lambda: (
        None if wait_for(lambda: bool(
            (doc(slug)["nodes"][nid].get("frozen") or {})), 30)
        else (_ for _ in ()).throw(AssertionError(doc(slug)["nodes"][nid]))))
    check("freeze · the reset time is parsed onto the record", lambda: (
        lambda fz: None if fz.get("until") or fz.get("until_ts")
        else (_ for _ in ()).throw(AssertionError(fz))
    )(doc(slug)["nodes"][nid].get("frozen") or {}))
    check("freeze · the interrupted turn's text is kept for replay", lambda: (
        lambda c: None if c["frozen"] or c["mailbox"] or c["journal"]
        else (_ for _ in ()).throw(AssertionError(c))
    )(carriers(slug, nid, tok)))
    tok2 = token()
    check("freeze · mail to a frozen node stays boxed, undrained", lambda: (
        lambda r: None if r.get("frozen") else
        (_ for _ in ()).throw(AssertionError(r))
    )(send(slug, nid, f"while frozen {tok2}")))
    check("freeze · …and is really in the mailbox", lambda: (
        lambda c: None if c["mailbox"]
        else (_ for _ in ()).throw(AssertionError(c))
    )(carriers(slug, nid, tok2)))
    set_cfg(FAST)
    api("POST", f"/api/orgs/{slug}/resume")
    check("freeze · ▶ resume replays the interrupted work", lambda: (
        None if wait_delivered(tok, 30)
        else (_ for _ in ()).throw(AssertionError(carriers(slug, nid, tok)))))
    check("freeze · …and the mail that waited rides along", lambda: (
        None if wait_delivered(tok2, 30)
        else (_ for _ in ()).throw(AssertionError(carriers(slug, nid, tok2)))))
    check("freeze · the freeze record is cleared", lambda: (
        None if not doc(slug)["nodes"][nid].get("frozen")
        else (_ for _ in ()).throw(AssertionError(doc(slug)["nodes"][nid]))))
    wait_idle(slug, nid, 20)

    # re-freeze: the limit is still live when the resume lands
    set_cfg({**FAST, "usageLimit": True})
    slug2, (nid2,) = make_org("refreeze")
    tok3 = token()
    send(slug2, nid2, f"first {tok3}")
    wait_for(lambda: bool(doc(slug2)["nodes"][nid2].get("frozen")), 30)
    api("POST", f"/api/orgs/{slug2}/resume")
    check("freeze · a failed resume attempt re-freezes the node", lambda: (
        None if wait_for(lambda: bool(
            doc(slug2)["nodes"][nid2].get("frozen")), 30)
        else (_ for _ in ()).throw(AssertionError(doc(slug2)["nodes"][nid2]))))
    check("freeze · and the work is still carried after the re-freeze", lambda: (
        lambda c: None if any(c.values())
        else (_ for _ in ()).throw(AssertionError(c))
    )(carriers(slug2, nid2, tok3)))
    set_cfg(FAST)
    wait_idle(slug2, nid2, 20)

    # a usage limit hit on the FIRST API call: the CLI answers with an
    # is_error result and never emits anything else. Post-fix the batch is
    # unconfirmed, so the mail folds back to the mailbox instead of riding
    # `resume_texts` — either is fine, having neither is not.
    set_cfg(FAST, wrap={"default": {
        "mode": "errresult", "delayMs": 60,
        "errText": "Claude AI usage limit reached|" + str(int(time.time()) - 30)}})
    slug3, (nid3,) = make_org("limitfirst")
    tok4 = token()
    send(slug3, nid3, f"never seen by the model {tok4}")
    check("freeze · a limit on the first call still freezes the node", lambda: (
        None if wait_for(lambda: bool(
            doc(slug3)["nodes"][nid3].get("frozen")), 30)
        else (_ for _ in ()).throw(AssertionError(doc(slug3)["nodes"][nid3]))))
    check("freeze · …and the unseen message is still carried", lambda: (
        lambda c: None if c["mailbox"] or c["frozen"] or c["journal"]
        else (_ for _ in ()).throw(AssertionError(c))
    )(carriers(slug3, nid3, tok4)))
    set_cfg(FAST)
    api("POST", f"/api/orgs/{slug3}/resume")
    check("freeze · ▶ resume then actually delivers it", lambda: (
        None if wait_delivered(tok4, 30)
        else (_ for _ in ()).throw(AssertionError(carriers(slug3, nid3, tok4)))))
    wait_idle(slug3, nid3, 20)
    drop_orgs()


def live_frozen_queue() -> None:
    """A usage-limit freeze must stop the node — including work ALREADY in its
    in-memory queue. `send_message` refuses to drive a frozen node, but the
    queue is drained by `_run_turn`'s own `finally`, which never re-checks."""
    print("\na freeze with work already queued:")
    start_backend(max_turns=1, turn_timeout=10)
    set_cfg({**FAST, "usageLimit": True},
            agent0={"hang": True, "echoMs": 20, "firstEventMs": 60})
    slug, (hog, nid) = make_org("frozenq", agents=2, grant=2)
    send(slug, hog, f"hold the slot {token()}")
    time.sleep(1.0)
    toks = [token() for _ in range(3)]
    for i, t in enumerate(toks):
        send(slug, nid, f"job{i} {t}")            # all three queue
        time.sleep(0.05)
    # the hog times out, the worker runs, the first turn hits the usage limit
    if not wait_for(lambda: bool(doc(slug)["nodes"][nid].get("frozen")), 60):
        raise AssertionError("the worker never froze")
    wait_idle(slug, nid, 60)
    time.sleep(0.6)
    fz = doc(slug)["nodes"][nid].get("frozen") or {}
    n_replays = len(fz.get("resume_texts") or [])
    served = served_messages(doc(slug)["nodes"][nid]["session_id"])

    def _stops(served=served) -> None:
        # The observable is how many messages the CLI was ASKED to answer. The
        # boundary feed hands the next queued message to the SAME session the
        # moment a result arrives — and it never looks at `is_error`, so a
        # session that just answered "usage limit reached" is immediately
        # given the next one, and the next. Each is a real API attempt against
        # a live limit.
        if served > 1:
            raise AssertionError(
                f"the usage limit did not stop the node: {served} messages "
                f"were fed to the CLI after it reported the limit (one per "
                f"queued message). The result-boundary feed does not check "
                f"is_error, and the queue drain in _run_turn does not check "
                f"`frozen` — `send_message`'s frozen guard covers neither.")
    check(f"frozenq · a usage limit stops the queued work too "
          f"({served} message(s) served, {n_replays} replay text(s))", _stops)

    def _nothing_lost() -> None:
        lost = [t for t in toks if not any(carriers(slug, nid, t).values())]
        if lost:
            raise AssertionError(f"lost while freezing: {lost}")
    check("frozenq · and nothing queued behind the freeze is lost",
          _nothing_lost)
    set_cfg(FAST)
    api("POST", f"/api/orgs/{slug}/resume")
    check("frozenq · ▶ resume delivers all of it", lambda: (
        None if wait_for(lambda: all(t in transcript_text() for t in toks), 60)
        else (_ for _ in ()).throw(AssertionError(
            [t for t in toks if t not in transcript_text()]))))
    wait_idle(slug, nid, 30)
    drop_orgs()


def live_auto_resume() -> None:
    """The auto_resume timer: frozen agents restart on their own one minute
    after the reported reset time. Slow by construction (a 30 s loop), so it
    is skipped under --quick."""
    print("\nauto_resume timer:")
    if QUICK:
        note("autoresume skipped under --quick (the loop ticks every 30 s)")
        return
    start_backend()
    set_cfg({**FAST, "usageLimit": True})
    slug, (nid,) = make_org("autoresume")
    tok = token()
    send(slug, nid, f"auto {tok}")
    if not wait_for(lambda: bool(doc(slug)["nodes"][nid].get("frozen")), 30):
        raise AssertionError("never froze")
    wait_idle(slug, nid, 20)
    stop_backend()
    # a reset time that has already passed, and the org toggle on
    d = doc(slug)
    d["auto_resume"] = True
    d["nodes"][nid]["frozen"]["until_ts"] = time.time() - 600
    with open(os.path.join(DATA, "orgs", slug + ".json"), "w",
              encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    set_cfg(FAST)
    start_backend()
    check("autoresume · the timer un-freezes the node on its own", lambda: (
        None if wait_for(lambda: not doc(slug)["nodes"][nid].get("frozen"), 75)
        else (_ for _ in ()).throw(AssertionError(doc(slug)["nodes"][nid]))))
    check("autoresume · …and the work is replayed", lambda: (
        None if wait_delivered(tok, 40)
        else (_ for _ in ()).throw(AssertionError(carriers(slug, nid, tok)))))
    wait_idle(slug, nid, 20)

    # a reset time still in the future must NOT fire
    set_cfg({**FAST, "usageLimit": True})
    slug2, (nid2,) = make_org("autowait")
    send(slug2, nid2, f"later {token()}")
    wait_for(lambda: bool(doc(slug2)["nodes"][nid2].get("frozen")), 30)
    wait_idle(slug2, nid2, 20)
    stop_backend()
    d = doc(slug2)
    d["auto_resume"] = True
    d["nodes"][nid2]["frozen"]["until_ts"] = time.time() + 3600
    with open(os.path.join(DATA, "orgs", slug2 + ".json"), "w",
              encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    set_cfg(FAST)
    start_backend()
    time.sleep(40)
    check("autoresume · a reset time in the future is left alone", lambda: (
        None if doc(slug2)["nodes"][nid2].get("frozen")
        else (_ for _ in ()).throw(AssertionError("resumed too early"))))
    drop_orgs()


def live_auto_cheap_compact() -> None:
    """FR-24b (user request 2026-08-12): a wake past BOTH thresholds swaps
    the session in place BEFORE the resume pays the cold-context reload —
    same seat, same team, mailbox untouched; the successor's first turn
    carries the compact notice AND the waking mail together. Disabled by
    default; the per-node override outranks the org setting."""
    print("\nauto cheap-compact on wake (FR-24b):")
    start_backend()
    set_cfg(FAST)
    slug, (nid,) = make_org("acc")
    tok0 = token()
    send(slug, nid, f"warm up {tok0}")
    wait_idle(slug, nid, 30)
    stop_backend()
    d = doc(slug)
    n = d["nodes"][nid]
    sid0 = n["session_id"]
    if not n.get("turns"):
        raise AssertionError("fixture: the warm-up turn left no ring entry")
    # arm: org config on with thresholds the next wake trivially crosses,
    # plus the two facts the hook reads — a high occupancy high-water and a
    # last-turn stamp already past the idle window (idle_s 0)
    d["auto_cheap_compact"] = {"enabled": True, "occ": 0.5, "idle_s": 0}
    n["occupancy"] = 150_000
    n["context_window"] = 200_000
    with open(os.path.join(DATA, "orgs", slug + ".json"), "w",
              encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    set_cfg(FAST)
    start_backend()
    tok = token()
    send(slug, nid, f"wake up {tok}")
    check("acc · the wake swapped the session IN PLACE (same id, fresh "
          "session)", lambda: (
        None if wait_for(lambda:
            doc(slug)["nodes"][nid]["session_id"] != sid0, 30)
        else (_ for _ in ()).throw(AssertionError(doc(slug)["nodes"][nid]))))
    wait_idle(slug, nid, 30)

    def _lineage() -> None:
        d2 = doc(slug)
        n2 = d2["nodes"][nid]
        bearer = d2["nodes"].get(nid + "@0") or {}
        assert n2.get("generation") == 1 and n2.get("predecessor") \
            == nid + "@0", n2.get("generation")
        assert bearer.get("state") == "archived" \
            and bearer.get("bearer_state") == "knowledge" \
            and bearer.get("session_id") == sid0 \
            and bearer.get("successor") == nid, bearer
    check("acc · compact_split's exact lineage shape: bearer nid@0 holds "
          "the OLD session", _lineage)
    check("acc · the waking mail still reached the successor's first turn",
          lambda: (None if wait_delivered(tok, 30)
                   else (_ for _ in ()).throw(AssertionError(tok))))
    check("acc · …alongside the compact notice, in the same envelope",
          lambda: (None if "CHEAP-COMPACTED" in transcript_text()
                   else (_ for _ in ()).throw(AssertionError(
                       "the successor was never told what happened"))))

    # the per-node override OUTRANKS the org setting — and off means off
    stop_backend()
    d3 = doc(slug)
    d3["nodes"][nid]["scope"]["auto_cheap_compact"] = {"enabled": False}
    d3["nodes"][nid]["occupancy"] = 150_000
    d3["nodes"][nid]["context_window"] = 200_000
    with open(os.path.join(DATA, "orgs", slug + ".json"), "w",
              encoding="utf-8") as f:
        json.dump(d3, f, indent=2)
    set_cfg(FAST)
    start_backend()
    send(slug, nid, f"wake again {token()}")
    wait_idle(slug, nid, 30)
    check("acc · the per-node OFF override wins over the org's ON", lambda: (
        None if doc(slug)["nodes"][nid].get("generation") == 1
        else (_ for _ in ()).throw(AssertionError(
            doc(slug)["nodes"][nid].get("generation")))))
    drop_orgs()


def live_watchdogs() -> None:
    """FR-18 end-to-end: a FILE dog's event lands as mail and drives a real
    turn (draining into the envelope); a PORT dog fires on the DOWN edge
    only; a STREAM dog surfaces its command's lines in realtime and marks
    itself exited when the command dies. The engine ticks every ~5s, so the
    waits here are its cadence, not slack."""
    print("\nwatchdogs (FR-18):")
    start_backend()
    set_cfg(FAST)
    slug, (nid,) = make_org("dogs")
    logp = os.path.join(DATA, "dogged.log").replace("\\", "/")
    with open(logp, "w", encoding="utf-8") as f:
        f.write("calm before\n")
    # the file dog watches a path OUTSIDE the node's roots → a 422 refusal
    # (capability containment at the api boundary)
    import urllib.error as _uerr
    try:
        api("POST", "/api/agent", {"org": slug, "node": nid,
                                   "tool": "orgtree_watchdog",
                                   "args": {"action": "create",
                                            "name": "outside",
                                            "kind": "file",
                                            "target": logp,
                                            "pattern": "BOOM"}})
        raise AssertionError("an out-of-roots file target was accepted")
    except _uerr.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        assert e.code == 422 and "cannot watch" in detail, (e.code, detail)
    # …so grant the folder, then create for real
    api("POST", f"/api/orgs/{slug}/nodes/{nid}/scope",
        {"add_dirs": [{"path": os.path.dirname(logp), "mode": "ro"}]})
    r = api("POST", "/api/agent", {"org": slug, "node": nid,
                                   "tool": "orgtree_watchdog",
                                   "args": {"action": "create",
                                            "name": "log-dog",
                                            "kind": "file",
                                            "target": logp,
                                            "pattern": "BOOM",
                                            "interval_s": 15}})
    assert "armed" in json.dumps(r), r
    # let the engine take its baseline high-water, THEN append the event —
    # content that predates the first check must not fire retroactively here
    time.sleep(8)
    with open(logp, "a", encoding="utf-8") as f:
        f.write("BOOM the build fell over\n")
    tok = "BOOM the build fell over"
    check("dogs · file: the event fires, mails the owner, and DRIVES a turn",
          lambda: (None if wait_delivered(tok, 45)
                   else (_ for _ in ()).throw(AssertionError(
                       doc(slug).get("watchdogs")))))
    check("dogs · file: the ring + counters recorded it", lambda: (
        lambda w: None if (w["fired"] >= 1 and w["events"]
                           and "BOOM" in w["events"][-1]["gist"])
        else (_ for _ in ()).throw(AssertionError(w))
    )(next(w for w in doc(slug)["watchdogs"] if w["name"] == "log-dog")))
    wait_idle(slug, nid, 30)

    # PORT dog: fires on the DOWN edge, not on being down from birth
    import socket as _socket
    srv = _socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    api("POST", "/api/agent", {"org": slug, "node": nid,
                               "tool": "orgtree_watchdog",
                               "args": {"action": "create",
                                        "name": "port-dog",
                                        "kind": "process",
                                        "target": f"port:{port}",
                                        "interval_s": 15}})
    time.sleep(8)          # one tick observes it UP (arms the edge)
    srv.close()            # …and now it goes down
    check("dogs · port: the DOWN edge fires", lambda: (
        None if wait_for(lambda: any(
            w["name"] == "port-dog" and w.get("fired", 0) >= 1
            for w in doc(slug)["watchdogs"]), 60)
        else (_ for _ in ()).throw(AssertionError(doc(slug)["watchdogs"]))))
    wait_idle(slug, nid, 30)

    # STREAM dog: a live command's lines surface without any cadence rerun,
    # and its death is announced + marked
    pyexe = sys.executable.replace("\\", "/")
    emit = (f'"{pyexe}" -u -c "import time; print(\'evt one\', flush=True); '
            f'time.sleep(1); print(\'evt two\', flush=True)"')
    api("POST", "/api/agent", {"org": slug, "node": nid,
                               "tool": "orgtree_watchdog",
                               "args": {"action": "create",
                                        "name": "streamer",
                                        "kind": "stream",
                                        "target": emit,
                                        "pattern": "evt",
                                        "interval_s": 5}})
    check("dogs · stream: realtime lines fire without a rerun cadence",
          lambda: (None if wait_delivered("evt one", 45)
                   else (_ for _ in ()).throw(AssertionError(
                       doc(slug).get("watchdogs")))))
    check("dogs · stream: the command's death is marked `exited`", lambda: (
        None if wait_for(lambda: any(
            w["name"] == "streamer" and w.get("state") == "exited"
            for w in doc(slug)["watchdogs"]), 60)
        else (_ for _ in ()).throw(AssertionError(doc(slug)["watchdogs"]))))
    drop_orgs()


def live_reconcile() -> None:
    print("\nreconcile at startup:")
    start_backend()
    set_cfg(FAST)

    # 1 — mail waiting for a node that never ran
    slug, (nid,) = make_org("recmail")
    api("POST", f"/api/orgs/{slug}/ops", {"op": "retire", "actor": USER,
                                          "node": nid})
    tok = token()
    send(slug, nid, f"waited across a restart {tok}")
    api("POST", f"/api/orgs/{slug}/ops", {"op": "rehire", "actor": USER,
                                          "node": nid})
    # rehire drives it; kill before it can land
    stop_backend(hard=True)
    # (this pin was briefly inverted 2026-08-06 on a misread of a user
    # ruling; the user clarified same day — the drain-on-start STAYS. The
    # ruling is about mail never being LOST in program state across a
    # refresh, which the doc + journal carriers guarantee.)
    check("reconcile · mail waiting at startup is driven", lambda: (
        start_backend(),
        None if wait_delivered(tok, 40)
        else (_ for _ in ()).throw(AssertionError(carriers(slug, nid, tok))))[-1])
    wait_idle(slug, nid, 20)

    # 2 — a frozen node must NOT be woken by reconcile
    slug2, (nid2,) = make_org("recfrozen")
    set_cfg({**FAST, "usageLimit": True})
    tokf = token()
    send(slug2, nid2, f"freeze me {tokf}")
    wait_for(lambda: bool(doc(slug2)["nodes"][nid2].get("frozen")), 30)
    tok2 = token()
    send(slug2, nid2, f"post-freeze mail {tok2}")
    stop_backend(hard=True)
    set_cfg(FAST)
    start_backend()
    time.sleep(2.5)
    check("reconcile · a frozen node is left frozen and undriven", lambda: (
        None if doc(slug2)["nodes"][nid2].get("frozen")
        and tok2 not in transcript_text()
        else (_ for _ in ()).throw(AssertionError(doc(slug2)["nodes"][nid2]))))
    check("reconcile · its waiting mail is still in the mailbox", lambda: (
        lambda c: None if c["mailbox"]
        else (_ for _ in ()).throw(AssertionError(c))
    )(carriers(slug2, nid2, tok2)))

    # 3 — a missing transcript on a node that has demonstrably run
    slug3, (nid3,) = make_org("recmissing")
    send(slug3, nid3, f"one turn {token()}")
    wait_idle(slug3, nid3, 30)
    stop_backend()
    sid = doc(slug3)["nodes"][nid3]["session_id"]
    for p in glob.glob(os.path.join(HOME, ".claude", "projects", "*",
                                    sid + ".jsonl")):
        os.remove(p)
    # cost_usd > 0 is the "has demonstrably run" test
    check("reconcile · a node with a vanished transcript is unrecoverable",
          lambda: (
              start_backend(),
              time.sleep(1.5),
              None if doc(slug3)["nodes"][nid3]["state"] == "unrecoverable"
              else (_ for _ in ()).throw(AssertionError(
                  doc(slug3)["nodes"][nid3])))[-1])

    # 4 — an unconfirmed journal batch alone on the doc
    slug4, (nid4,) = make_org("recjournal")
    stop_backend()
    d = doc(slug4)
    tokj = token()
    d.setdefault("delivering", {})[nid4] = [{
        "tok": "deadbeefdeadbeef", "at": "2026-08-04T00:00:00.000Z",
        "mail": [{"id": "aa", "from": USER, "kind": "message",
                  "body": f"journaled only {tokj}",
                  "at": "2026-08-04T00:00:00.000Z"}],
        "notices": [], "via": "turn"}]
    with open(os.path.join(DATA, "orgs", slug4 + ".json"), "w",
              encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    check("reconcile · an unconfirmed batch folds back and is delivered",
          lambda: (
              start_backend(),
              None if wait_delivered(tokj, 40)
              else (_ for _ in ()).throw(AssertionError(
                  carriers(slug4, nid4, tokj))))[-1])
    wait_idle(slug4, nid4, 20)

    # 5 — an inflight command is dropped, not degraded
    slug5, (nid5,) = make_org("reccmd")
    stop_backend()
    d = doc(slug5)
    d["nodes"][nid5]["inflight"] = {"at": "2026-08-04T00:00:00.000Z",
                                    "text": "/context", "cmd": True}
    with open(os.path.join(DATA, "orgs", slug5 + ".json"), "w",
              encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    start_backend()
    time.sleep(2.0)
    check("reconcile · an inflight COMMAND is dropped, never replayed as prose",
          lambda: (
              None if "/context" not in transcript_text()
              else (_ for _ in ()).throw(AssertionError(
                  "a command was replayed as prose"))))
    check("reconcile · …and its inflight marker is cleared", lambda: (
        None if not doc(slug5)["nodes"][nid5].get("inflight")
        else (_ for _ in ()).throw(AssertionError(doc(slug5)["nodes"][nid5]))))
    drop_orgs()


def live_compaction() -> None:
    print("\ncompaction split:")
    start_backend()
    set_cfg(FAST)
    slug, (nid,) = make_org("compact")
    send(slug, nid, f"prime {token()}")
    wait_idle(slug, nid, 30)
    if not doc(slug)["nodes"][nid].get("occupancy"):
        note("compaction skipped — the fake CLI reported no occupancy")
        drop_orgs()
        return
    old_sid = doc(slug)["nodes"][nid]["session_id"]
    api("POST", f"/api/orgs/{slug}/nodes/{nid}/compact")
    time.sleep(0.3)
    tok = token()
    send(slug, nid, f"sent during the split {tok}")
    check("compact · the split produces a successor session", lambda: (
        None if wait_for(lambda: doc(slug)["nodes"][nid]["session_id"] != old_sid,
                         30)
        else (_ for _ in ()).throw(AssertionError(doc(slug)["nodes"][nid]))))
    check("compact · mail sent during the split is not lost", lambda: (
        lambda c: None if any(c.values())
        else (_ for _ in ()).throw(AssertionError(c))
    )(carriers(slug, nid, tok)))
    check("compact · …and is delivered to the SUCCESSOR", lambda: (
        None if wait_delivered(tok, 40)
        else (_ for _ in ()).throw(AssertionError(carriers(slug, nid, tok)))))
    check("compact · the predecessor is kept as a knowledge bearer", lambda: (
        lambda nodes: None if any(n.get("bearer_state") == "knowledge"
                                  for n in nodes.values())
        else (_ for _ in ()).throw(AssertionError(list(nodes)))
    )(doc(slug)["nodes"]))
    wait_idle(slug, nid, 30)
    drop_orgs()


def live_interrupt() -> None:
    print("\ninterrupt and killswitch:")
    start_backend()
    set_cfg({"echoMs": 100, "firstEventMs": 200, "tools": 6, "toolMs": 400,
             "resultMs": 20})
    slug, (nid,) = make_org("interrupt")
    send(slug, nid, f"long job {token()}")
    time.sleep(1.0)
    tok = token()
    send(slug, nid, f"during the job {tok}")
    r = api("POST", f"/api/orgs/{slug}/killswitch")
    check("killswitch · reports what it interrupted", lambda: (
        None if isinstance(r, dict) else (_ for _ in ()).throw(AssertionError(r))))
    wait_idle(slug, nid, 30)
    time.sleep(0.5)
    check("killswitch · the message caught by the stop is not lost", lambda: (
        lambda c: None if any(c.values())
        else (_ for _ in ()).throw(AssertionError(c))
    )(carriers(slug, nid, tok)))
    set_cfg(FAST)
    send(slug, nid, f"resume {token()}")
    check("killswitch · and it reaches the agent afterwards", lambda: (
        None if wait_delivered(tok, 30)
        else (_ for _ in ()).throw(AssertionError(carriers(slug, nid, tok)))))
    wait_idle(slug, nid, 20)
    drop_orgs()


# ==================================================================== main

def main() -> None:
    print(f"rig: {TMP}\n")
    print("HERMETIC — the journal primitives and the pure predicates")
    hermetic()
    if not HERMETIC_ONLY:
        print("\nLIVE — a real backend on "
              f"127.0.0.1:{PORT}, a fake CLI, real threads")
        write_wrapper()
        # section key == the label prefix its checks carry, so --only picks
        # BOTH the section that runs and the checks that report
        sections = [
            ("subag", live_subagents),
            ("kill", live_kill_sweep),
            ("leash", live_leash),
            ("clicrash", live_cli_death),
            ("errresult", live_error_result),
            ("dupresult", live_second_result),
            ("argvdie", live_die_on_argv),
            ("order", live_ordering),
            ("steerrace", live_steer_race),
            ("slots", live_slots),
            ("qorder", live_queue_order_under_saturation),
            ("deepqueue", live_deep_queue),
            ("cmdq", live_command_queue),
            ("timeout", live_timeout),
            ("freeze", live_freeze),
            ("frozenq", live_frozen_queue),
            ("autoresume", live_auto_resume),
            ("acc", live_auto_cheap_compact),
            ("dogs", live_watchdogs),
            ("reconcile", live_reconcile),
            ("compact", live_compaction),
            ("killswitch", live_interrupt),
        ]
        try:
            for key, fn in sections:
                if ONLY and ONLY not in key:
                    continue
                try:
                    fn()
                except Exception:                                # noqa: BLE001
                    # a section that dies OUTSIDE a check (a rig failure, a
                    # precondition that never arrived) must not take the rest
                    # of the run with it
                    FAIL.append((f"{key} · section aborted",
                                 traceback.format_exc()))
                    print(f"  FAIL     {key} · section aborted")
                    try:
                        drop_orgs()
                    except Exception:                            # noqa: BLE001
                        pass
        finally:
            try:
                drop_orgs()
            except Exception:                                    # noqa: BLE001
                pass
            stop_backend()
    if NOTES:
        print(f"\n{len(NOTES)} NOTE(S) — characterised behaviour, not failures:")
        for m in NOTES:
            print(f"  · {m}")
    if EXCEPTIONS:
        print(f"\n{len(EXCEPTIONS)} MEASURED EXCEPTION(S) — real, outside this "
              f"suite's remit:")
        for label, why in EXCEPTIONS:
            print(f"  · {label}\n      {why}")
    if FAIL:
        print(f"\n{len(FAIL)} FAILURE(S):")
        for label, tb in FAIL:
            print(f"\n--- {label} ---\n{tb}")
        print(f"\n{PASS} passed, {len(FAIL)} FAILED   (rig kept: {TMP})")
        sys.exit(1)
    if KEEP:
        print(f"\nrig kept: {TMP}")
    else:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\nALL {PASS} CHECKS PASS")


if __name__ == "__main__":
    main()
