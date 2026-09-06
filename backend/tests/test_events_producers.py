"""Step 3 — the producers, family by family: every migrated producer mints its typed
event and the row body is BYTE-IDENTICAL to what the pre-typed producer wrote (B4).

The oracles below are the OLD producer code, copied verbatim at the moment each site
was migrated (breadcrumbs: "capture before editing each site"). They are the parity
witness: a renderer that drifts from the old text fails here, not in the field.

    §M  family monitor — the watchdog fire (Org.watchdog_fire, typed path via
        supervisor._wd_fire) and the went-quiet alert (supervisor._wd_alert)

Hermetic: throwaway ORGTREE_DATA set BEFORE any orgtree import; `send_message` is
patched at the seam the fire path calls so no agent process is ever started.

    python backend/tests/test_events_producers.py
"""
from __future__ import annotations

import datetime as _dtm
import os
import sys
import tempfile
import traceback
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

_TMP = tempfile.mkdtemp(prefix="orgtree-evprod-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
os.environ["USERPROFILE"] = os.environ["HOME"] = _TMP

from orgtree import events, store                                # noqa: E402
from orgtree import supervisor as S                              # noqa: E402
from orgtree.ledger import USER, LedgerError, Org                # noqa: E402

assert store.DATA_ROOT.startswith(_TMP), store.DATA_ROOT

PASS = 0
FAIL: list[tuple[str, str]] = []
_n = [0]


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


class Quiet:
    """Patch `S.send_message` — the seam the fire/alert paths drive through — so
    nothing starts a turn; records what would have been woken."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def __enter__(self):
        self._orig = S.send_message

        def spy(slug, nid, text, wake=False, **kw):
            self.calls.append((nid, bool(wake)))
            return {"accepted": True, "queued": 0}      # the shape a route unpacks
        S.send_message = spy                                     # type: ignore
        return self

    def __exit__(self, *a):
        S.send_message = self._orig                              # type: ignore


def rig(*, once: bool = False, kind: str = "file") -> tuple[str, Org, str, dict[str, Any]]:
    _n[0] += 1
    slug = f"evprod{_n[0]}"
    o = Org.create(slug, dirs=[_TMP])
    o.hire(USER, None, "opus", 20, "boss")
    tgt = os.path.join(_TMP, f"{slug}.log")
    with open(tgt, "w", encoding="utf-8") as f:
        f.write("start\n")
    if kind == "file":
        r = o.watchdog_create("boss", "build-watch", "file", tgt, pattern="BOOM",
                              interval_s=15, once=once)
    else:
        r = o.watchdog_create("boss", "build-watch", "command", "echo R", "R", 15,
                              False, None, once)
    store.save_org(o)
    return slug, o, str(r["id"]), {"target": tgt}


def last_row(slug: str) -> dict[str, Any]:
    o = store.load_org(slug)
    return dict(o.d["mail"]["boss"][-1])


def decoded(row: dict[str, Any]) -> dict[str, Any]:
    d = events.decode(row.get("ev"), row)
    assert d["status"] == "ok", d
    return d["ev"]


# =========================================================================== oracles
# ⚠ VERBATIM copies of the pre-typed producers (supervisor.py @ f91e3ff). Do not
# "clean up": the point is that they are the old code.
_OLD_NOTE = (
    "\n\n— This was a ONE-SHOT dog: it fired once and has REMOVED ITSELF. "
    "It is gone from your list and will not fire again. Nothing is wrong "
    "and you need not remove it. If you want to watch for this again, "
    "arm a new one.")


def old_fire_body(name: str, lines: list[str], prefix: str, one_shot: bool) -> str:
    body = (f"[WATCHDOG {name}]{prefix} {len(lines)} event(s):\n"
            + "\n".join(x[:500] for x in lines[:20])
            + (f"\n… {len(lines) - 20} more" if len(lines) > 20 else ""))
    if one_shot:
        body = body[:8000 - len(_OLD_NOTE)].rstrip() + _OLD_NOTE
    return body[:8000]


def old_alert_body(w: dict[str, Any], lost: dict[str, Any]) -> str:
    tgt = str(w.get("target") or "")
    kind = str(w.get("kind") or "")
    quiet, since = S._wd_stale_ok(w)
    age = S._wd_age_s(w.get("at"))
    facts = [f"watching   : {kind} · {tgt}",
             f"armed      : {S._wd_hours(age)} ago" if age is not None
             else "armed      : (unknown)",
             f"checks run : {int(w.get('checks_run') or 0)}"
             f"  ·  fired so far: {int(w.get('fired') or 0)}",
             f"silent for : {quiet} consecutive checks"
             + (f", {S._wd_hours(since)}" if since is not None else ""),
             f"last check : {w.get('last_check') or '(never)'}"]
    if kind == "file":
        try:
            st = os.stat(tgt)
            facts.append(f"the file   : {st.st_size} bytes, last written "
                         + _dtm.datetime.fromtimestamp(
                             st.st_mtime, _dtm.timezone.utc)
                         .isoformat(timespec="seconds").replace("+00:00", "Z"))
        except OSError as e:
            facts.append(f"the file   : CANNOT BE READ — {e.strerror or e}")
    if w.get("last_output"):
        facts.append(f"it sees    : {str(w['last_output'])[:300]!r}")
    return (f"[WATCHDOG {w.get('name')}] ⚠ {lost['headline'].upper()}\n\n"
            + "\n".join(facts)
            + f"\n\n{lost['advice']}\n\n"
            + ("⚠ This is about the THING BEING WATCHED, not about orgtree. "
               "Restarts and deploys do not produce this message: the counter "
               "above only advances on checks that actually ran (D-176)."))


# ============================================================ §M family monitor
print("\n§M · monitor — watchdog fire and went-quiet alert")


def _fire_persistent():
    slug, o, wid, _ = rig()
    with Quiet() as q:
        S._wd_fire(slug, wid, "build-watch", ["BOOM one", "BOOM two"])
    assert q.calls == [("boss", True)], q.calls
    row = last_row(slug)
    assert row["body"] == old_fire_body("build-watch", ["BOOM one", "BOOM two"], "", False)
    ev = decoded(row)
    assert ev["variant"] == "monitor.watchdog_fired", ev
    assert ev["actor"] == {"kind": "watchdog", "id": wid}, ev["actor"]
    assert ev["object"] == {"kind": "watchdog", "org": slug, "id": wid,
                            "name": "build-watch", "owner": "boss"}, ev["object"]
    assert ev["lines"] == ["BOOM one", "BOOM two"] and ev["count"] == 2
    assert ev["once"] is False
    assert row["body"] == events.render_agent(ev), "body is the frozen rendering"
    assert "REMOVED ITSELF" not in row["body"]
    # the archive copy carries the same row
    log = store.load_org(slug).d["mail_log"]["boss"][-1]
    assert log["ev"] == row["ev"] and log["body"] == row["body"]


check("fire · persistent dog: body == old producer text; ev monitor.watchdog_fired "
      "with WatchdogRef, lines, count, once=False; archive copy identical",
      _fire_persistent)


def _fire_one_shot_long():
    """25 lines of 600 chars: exercises the per-line 500 cap, the 20-line window,
    the '… N more' tail AND the one-shot note surviving an over-long body."""
    slug, o, wid, _ = rig(once=True)
    lines = [f"L{i:02d} " + "x" * 600 for i in range(25)]
    with Quiet():
        S._wd_fire(slug, wid, "build-watch", lines, prefix=" STREAM EXITED —")
    row = last_row(slug)
    want = old_fire_body("build-watch", lines, " STREAM EXITED —", True)
    assert len(want) <= 8000 and "REMOVED ITSELF" in want, "oracle sanity"
    assert row["body"] == want, (row["body"][-200:], want[-200:])
    ev = decoded(row)
    assert ev["once"] is True and ev["count"] == 25 and ev["prefix"] == " STREAM EXITED —"
    assert ev["lines"] == lines, "the event keeps EVERY line untruncated; only the text is capped"
    assert row["body"] == events.render_agent(ev)
    o2 = store.load_org(slug)
    assert not any(w["id"] == wid for w in o2.d.get("watchdogs") or []), "the one-shot spent itself"


check("fire · one-shot dog, 25×600-char lines: body == old text incl. the self-removal "
      "note within 8000; ev.once True, all lines kept on the event",
      _fire_one_shot_long)


def _fire_raw_path_unchanged():
    """The positional-body path (ledger tests, pre-typed callers) is still the old
    behaviour, untyped: no ev, note appended by the shared helper."""
    slug, o, wid, _ = rig(once=True)
    o = store.load_org(slug)
    o.watchdog_fire(wid, "R", "X" * 12000)
    row = dict(o.d["mail"]["boss"][-1])
    assert "ev" not in row
    assert len(row["body"]) <= 8000 and row["body"].endswith(_OLD_NOTE)
    assert row["body"] == ("X" * 12000)[:8000 - len(_OLD_NOTE)].rstrip() + _OLD_NOTE
    assert events.decode(None, row)["status"] == "legacy"


check("fire · raw-body path: no ev (legacy row), note rule byte-identical",
      _fire_raw_path_unchanged)


def _fire_empty_refused():
    slug, o, wid, _ = rig()
    o = store.load_org(slug)
    try:
        o.watchdog_fire(wid, "R", lines=[])
    except ValueError as e:
        assert not isinstance(e, LedgerError), "must NOT be the swallowed LedgerError"
    else:
        raise AssertionError("an empty typed fire was accepted")


check("fire · typed path with no lines is a ValueError (not the LedgerError the "
      "engine swallows as 'dog changed')", _fire_empty_refused)


def _alert():
    slug, o, wid, extra = rig()
    lost = {"why": "quiet:file", "headline": "the file has gone quiet",
            "advice": "Look at the producer, not at orgtree.", "pause": False}
    o = store.load_org(slug)
    w = o._watchdog(wid)
    w["checks_run"] = 168
    w["last_check"] = "2026-09-06T21:20:00Z"
    w["last_output"] = "tail of what it saw"
    w["high_water"] = {"quiet": 7, "alive_at": "2026-09-06T20:00:00Z"}
    store.save_org(o)
    # oracle computed from the SAME persisted dog state the producer reads
    w_read = dict(store.load_org(slug)._watchdog(wid))
    want = old_alert_body(w_read, lost)
    with Quiet() as q:
        S._wd_alert(slug, wid, lost)
    assert q.calls == [("boss", True)], q.calls
    row = last_row(slug)
    assert row["body"] == want, (row["body"], want)
    ev = decoded(row)
    assert ev["variant"] == "monitor.watchdog_quiet"
    assert ev["object"]["id"] == wid and ev["object"]["owner"] == "boss"
    assert ev["headline"] == lost["headline"] and ev["advice"] == lost["advice"]
    assert any(f.startswith("checks run : 168") for f in ev["facts"]), ev["facts"]
    assert any(f.startswith("the file   : ") for f in ev["facts"]), ev["facts"]
    assert any("it sees    : " in f for f in ev["facts"]), ev["facts"]
    assert row["body"] == events.render_agent(ev)
    # the dog was NOT spent and `fired` did not move (an alert is not a fire)
    w2 = store.load_org(slug)._watchdog(wid)
    assert int(w2.get("fired") or 0) == 0 and w2["alerted_why"] == "quiet:file"


check("alert · went-quiet: body == old wd_alert_body text (file facts, it-sees, "
      "checks); ev monitor.watchdog_quiet; fired counter untouched", _alert)


def _alert_wrong_variant_refused():
    slug, o, wid, _ = rig()
    o = store.load_org(slug)
    w = o._watchdog(wid)
    fired = events.mint("monitor.watchdog_fired", {"kind": "watchdog", "id": wid},
                        o._watchdog_ref(w), prefix="", lines=["x"], count=1, once=False)
    try:
        o.watchdog_alert(wid, ev=fired)
    except LedgerError:
        pass
    else:
        raise AssertionError("watchdog_alert accepted a watchdog_fired event")
    assert not (o.d.get("mail") or {}).get("boss"), "nothing was posted"


check("alert · refuses any event but monitor.watchdog_quiet (control: nothing posted)",
      _alert_wrong_variant_refused)


def _public_projection():
    slug, o, wid, _ = rig()
    with Quiet():
        S._wd_fire(slug, wid, "build-watch", ["BOOM"])
    row = last_row(slug)
    ev = decoded(row)
    pub = events.public_event(ev)
    assert pub["projection"] == "public" and pub["variant"] == "monitor.watchdog_fired"
    assert "org" not in pub["object"], "the org slug is internal"
    assert pub["lines"] == ["BOOM"] and pub["once"] is False
    events.validate_public_event(pub)


check("public · a fired event projects to a PublicEvent (org withheld, lines kept)",
      _public_projection)


# ======================================================= §R family runtime_recovery
print("\n§R · runtime — the six engine writers")

# ⚠ VERBATIM copies of the pre-typed writers (supervisor.py @ 6497b15).
def old_terminal_agent(door, err):
    return (
        f"[TURN FAILED TERMINALLY — nothing will retry it]\n"
        f"How it died: {door}\n"
        f"Error: {err[:300] or 'no output'}\n\n"
        "orgtree classified this as NOT retryable and stopped. You were "
        "not driven for it — if the failure is in your CLI or your "
        "environment, another turn would die the same way — so this mail "
        "is waiting for you rather than waking you.\n\n"
        "⚠ WORK MAY BE UNFINISHED. Anything the dead turn had already "
        "done was NOT undone; anything it was about to do did not "
        "happen. Do not trust your own last message as a record of what "
        "ran — a turn can announce an edit in prose and die before the "
        "tool call. Check the disk.")[:8000]


def old_terminal_sup(name, nid, door, err):
    return (
        f"[REPORT STALLED — {name} ({nid}) is not running]\n"
        f"Its turn failed in a way orgtree does not retry, "
        f"and nothing will re-drive it.\n"
        f"How it died: {door}\n"
        f"Error: {err[:300] or 'no output'}\n\n"
        f"It has NOT been driven — if the fault is its CLI or "
        f"its environment, waking it would just kill another "
        f"turn. It is idle now and will stay idle until "
        f"something changes. It may also be holding "
        f"unfinished work from the turn that died.\n\n"
        f"You are the one who can act: fix the cause, or "
        f"message it once you have."
    )[:8000]


def old_terminal_user(name, nid, door, err):
    return (f"{name} ({nid}) stopped: its turn failed in a "
            f"way orgtree does not retry, and it has no "
            f"superior to tell.\nHow it died: {door}\n"
            f"Error: {err[:300] or 'no output'}\n"
            f"It is idle now and nothing will re-drive it. "
            f"It may be holding unfinished work.")[:2000]


def old_repeated_agent(run, kind, err):
    return (
        f"[TURN FAILED REPEATEDLY — {run} attempts, giving up]\n"
        f"Classified as: {kind}\n"
        f"Last error: {err[:300] or 'no output'}\n\n"
        "orgtree retried this turn automatically and has now stopped. "
        "You are no longer frozen, so this message is itself a live "
        "turn — you are running right now.\n\n"
        "⚠ WORK MAY BE UNFINISHED AND UNSAVED. A turn died part-way "
        "through, possibly more than once. Anything it had already done "
        "— files edited, mail sent, commands run — DID happen and was "
        "not undone; anything it was about to do did not. Before "
        "redoing work, CHECK THE ACTUAL STATE: your working folder, "
        "`git status` if you are in a repo, and your own last messages. "
        "Then finish what was interrupted, or report that you cannot.")[:8000]


def old_repeated_sup(name, nid, run, kind, err):
    return (
        f"[REPORT STALLED — {name} ({nid})]\n"
        f"Its turn failed {run} times in a row and orgtree "
        f"has stopped retrying.\n"
        f"Classified as: {kind}\n"
        f"Last error: {err[:300] or 'no output'}\n\n"
        "It has been told and driven, so it may recover on "
        "its own — but it may also be holding unfinished or "
        "uncommitted work from the turn that died. Nothing "
        "will retry it again automatically. Check on it."
    )[:8000]


def old_repeated_user(name, nid, run, kind, err):
    return (f"{name} ({nid}) is stuck: {run} turns in a row "
            f"failed and orgtree has stopped retrying. It "
            f"has no superior to tell.\nClassified as: "
            f"{kind}\nLast error: {err[:300] or 'no output'}\n"
            f"It has been told and driven, so it may recover "
            f"on its own — but nothing will retry it again "
            f"automatically.")[:2000]


def old_parked_sup(name, nid, headline, detail, lane, err):
    return (
        f"[REPORT STOPPED — {name} ({nid}) {headline}]\n"
        f"{detail}\n\n"
        f"Lane: {lane}\n"
        f"What it said: {err or 'no detail'}\n\n"
        "It is not frozen on a timer and orgtree will not re-drive it, "
        "so nothing changes until someone acts. It may also be holding "
        "unfinished work from the turn that stopped.\n\n"
        "You have NOT been woken for this, and you will not hear about "
        "it again until it has completed a turn and got stuck afresh."
    )[:8000]


def old_parked_user(name, nid, headline, lane, err):
    return (f"{name} ({nid}) {headline} and is stopped with "
            f"no reset time — nothing will wake it, and it "
            f"has no superior to tell.\nLane: {lane}\n"
            f"What it said: {err or 'no detail'}")[:2000]


def old_limited_sup(name, nid, lane, until, err):
    return (
        f"[REPORT LIMITED — {name} ({nid}) is out of provider "
        f"capacity]\n"
        f"Its provider refused the turn on a usage limit, so it "
        f"stopped mid-task and is now FROZEN.\n"
        f"Lane: {lane}\n"
        f"Limit lifts: {until}\n"
        f"Provider said: {err or 'no detail'}\n\n"
        "It is blocked, not broken — the work it was doing is held "
        "and will be replayed when it runs again. Whether it wakes by "
        "itself when the window lifts depends on this org's "
        "auto-resume setting; ▶ resume works either way.\n\n"
        "You have NOT been woken for this, and you will not hear "
        "about this wall again: it is one notice per episode, and the "
        "next one comes only after it has run a turn and been walled "
        "afresh. If the work cannot wait for the reset, move it to "
        "another agent or another lane."
    )[:8000]


def old_limited_user(name, nid, lane, until, err):
    return (f"{name} ({nid}) is out of provider capacity: "
            f"its provider refused the turn on a usage limit "
            f"and it is frozen. It has no superior to tell.\n"
            f"Lane: {lane}\nLimit lifts: {until}\n"
            f"Provider said: {err or 'no detail'}")[:2000]


def old_orphaned(orphans, why):
    lines, salvage = [], False
    for tid, desc, outf in orphans[:20]:
        salvage = salvage or bool(outf)
        lines.append(f"- \"{desc}\" (task {tid})"
                     + (f"\n  partial output: {outf}" if outf else ""))
    return (
        f"[SUBAGENT DIED — {len(orphans)} background subagent(s) were "
        f"killed before finishing]\n"
        f"Reason: {why}\n\n" + "\n".join(lines)
        + (f"\n… and {len(orphans) - 20} more" if len(orphans) > 20 else "")
        + "\n\nNo completion record exists for these — do NOT keep waiting "
          "on them, and do not assume their work landed."
        + (" The partial output files named above are real and may hold "
           "most of the work — READ THEM before redoing anything."
           if salvage else
           " Nothing usable was left on disk for these.")
        + " To retry, relaunch — and prefer run_in_background:false, which "
          "fails loudly instead of silently if it happens again.")[:8000]


def old_bg_stopped(task_id, desc, summary, output_file):
    return (
        f"[BACKGROUND TASK STOPPED — \"{desc}\" did not complete]\n"
        f"task id: {task_id}\n"
        + (f"CLI summary: {summary}\n" if summary else "")
        + (f"partial output: {output_file}\n" if output_file else "")
        + "\nThis was reported by the CLI itself while your process was "
          "still alive — it did not die and nothing killed it. Whatever "
          "you were waiting for did not finish. Do NOT assume the work "
          "landed; check the actual state before continuing.")[:8000]


def rig2():
    """boss (top-level) → kid; both live."""
    _n[0] += 1
    slug = f"evprod{_n[0]}"
    o = Org.create(slug, dirs=[_TMP])
    o.hire(USER, None, "opus", 20, "boss")
    o.hire("boss", "boss", "haiku", 5, "kid", add_dirs=[],
           tools={"bash": True, "web": False, "edit": False, "subagents": False, "mcp": []},
           org_visibility="team", charter="runtime fixture")
    store.save_org(o)
    return slug


def box_last(slug, nid):
    o = store.load_org(slug)
    return dict(o.d["mail"][nid][-1])


def user_last(slug):
    o = store.load_org(slug)
    rows = (o.d.get("user_mail_log") or []) + (o.d.get("user_inbox") or [])
    return dict(rows[-1])


def _terminal():
    slug = rig2()
    with Quiet():
        assert S._turn_abandoned(slug, "kid", "idle watchdog", "boom " * 100) is True
    err = "boom " * 100
    row = box_last(slug, "kid")
    assert row["body"] == old_terminal_agent("idle watchdog", err), row["body"]
    ev = decoded(row)
    assert ev["variant"] == "runtime.turn_failed_terminal"
    assert ev["object"]["kind"] == "session" and ev["object"]["node"] == "kid"
    assert ev["err"] == err, "the event keeps the FULL error; only the text is capped"
    assert row["body"] == events.render_agent(ev)
    sup = box_last(slug, "boss")
    assert sup["body"] == old_terminal_sup("kid", "kid", "idle watchdog", err), sup["body"]
    sev = decoded(sup)
    assert sev["variant"] == "runtime.report_stalled" and sev["cause"] == "terminal"
    assert sev["audience"] == "superior" and sev["object"]["id"] == "kid"
    assert sev["door"] == "idle watchdog" and sev["attempts"] is None
    assert sup["body"] == events.render_agent(sev)
    # top-level: the user's inbox gets the notice
    with Quiet():
        assert S._turn_abandoned(slug, "boss", "turn budget", "") is True
    u = user_last(slug)
    assert u["body"] == old_terminal_user("boss", "boss", "turn budget", ""), u["body"]
    uev = decoded(u)
    assert uev["variant"] == "runtime.report_stalled" and uev["audience"] == "user"
    assert u["body"] == events.render_agent(uev)


check("terminal · agent copy, superior copy and top-level user notice == old text; "
      "turn_failed_terminal / report_stalled(terminal) events", _terminal)


def _repeated():
    slug = rig2()
    err = "x" * 400
    with Quiet():
        S._retry_exhausted(slug, "kid", 3, err, "net")
    row = box_last(slug, "kid")
    assert row["body"] == old_repeated_agent(3, "net", err), row["body"]
    ev = decoded(row)
    assert ev["variant"] == "runtime.turn_failed_repeated" and ev["attempts"] == 3
    assert ev["classified"] == "net" and row["body"] == events.render_agent(ev)
    sup = box_last(slug, "boss")
    assert sup["body"] == old_repeated_sup("kid", "kid", 3, "net", err), sup["body"]
    sev = decoded(sup)
    assert sev["cause"] == "repeated" and sev["attempts"] == 3 and sev["door"] is None
    assert sup["body"] == events.render_agent(sev)
    with Quiet():
        S._retry_exhausted(slug, "boss", 2, "", "net")
    u = user_last(slug)
    assert u["body"] == old_repeated_user("boss", "boss", 2, "net", ""), u["body"]
    assert decoded(u)["audience"] == "user"


check("repeated · agent copy, superior copy and top-level user notice == old text; "
      "turn_failed_repeated / report_stalled(repeated) events", _repeated)


def _parked_and_limited():
    slug = rig2()
    kind = next(iter(S._PARKED_KINDS))
    headline, detail = S._PARKED_KINDS[kind]
    o = store.load_org(slug)
    o.node("kid")["frozen"] = {"cause": "auth", "auth": True, "error": "401 nope"}
    o.node("boss")["frozen"] = {"cause": "auth", "auth": True, "error": ""}
    store.save_org(o)
    with Quiet():
        assert S._parked_announce(slug, "kid", kind, "claude/primary") is True
        assert S._parked_announce(slug, "boss", kind, "claude/primary") is True
    sup = box_last(slug, "boss")
    assert sup["body"] == old_parked_sup("kid", "kid", headline, detail, "claude/primary",
                                         "401 nope"), sup["body"]
    sev = decoded(sup)
    assert sev["variant"] == "runtime.report_parked" and sev["err"] == "401 nope"
    assert sup["body"] == events.render_agent(sev)
    u = user_last(slug)
    assert u["body"] == old_parked_user("boss", "boss", headline, "claude/primary", ""), u["body"]
    uev = decoded(u)
    assert uev["audience"] == "user" and uev["err"] is None
    # limited
    slug = rig2()
    o = store.load_org(slug)
    o.node("kid")["frozen"] = {"limit": True, "until": "2026-09-07T00:00:00Z", "error": "429"}
    o.node("boss")["frozen"] = {"limit": True, "error": ""}
    store.save_org(o)
    with Quiet():
        assert S._limit_announce(slug, "kid", "claude/primary") is True
        assert S._limit_announce(slug, "boss", "codex/account") is True
    sup = box_last(slug, "boss")
    assert sup["body"] == old_limited_sup("kid", "kid", "claude/primary",
                                          "2026-09-07T00:00:00Z", "429"), sup["body"]
    sev = decoded(sup)
    assert sev["variant"] == "runtime.report_limited"
    assert sev["reset_at"] == "2026-09-07T00:00:00Z" and sev["err"] == "429"
    assert sup["body"] == events.render_agent(sev)
    u = user_last(slug)
    assert u["body"] == old_limited_user("boss", "boss", "codex/account", "not known", ""), u["body"]
    uev = decoded(u)
    assert uev["reset_at"] is None and uev["err"] is None and uev["audience"] == "user"


check("parked + limited · superior copy and top-level user notice == old text; "
      "report_parked / report_limited events (null reset/err round-trip)",
      _parked_and_limited)


def _orphans():
    slug = rig2()
    orphans = [(f"t{i}", f"desc {i}", (f"/out/{i}.txt" if i % 2 else "")) for i in range(23)]
    with Quiet() as q:
        S._bg_orphaned(slug, "kid", orphans, "process died", sid="sess-1")
    assert q.calls == [("kid", False)], q.calls
    row = box_last(slug, "kid")
    assert row["body"] == old_orphaned(orphans, "process died"), row["body"]
    ev = decoded(row)
    assert ev["variant"] == "runtime.subagent_died" and ev["count"] == 23
    assert len(ev["orphans"]) == 23, "every orphan on the event; the text shows 20"
    assert ev["orphans"][1] == {"id": "t1", "description": "desc 1", "output_file": "/out/1.txt"}
    assert ev["orphans"][0]["output_file"] is None
    assert ev["object"]["session_id"] == "sess-1"
    assert row["body"] == events.render_agent(ev)
    pub = events.public_event(ev)
    assert "output_file" not in pub["orphans"][1], "host paths are not public"
    # no salvage → the other sentence
    slug = rig2()
    with Quiet():
        S._bg_orphaned(slug, "kid", [("t9", "only", "")], "killed")
    row = box_last(slug, "kid")
    assert row["body"] == old_orphaned([("t9", "only", "")], "killed")
    assert "Nothing usable" in row["body"]


check("orphans · 23 subagents (20 shown, 3 counted, mixed salvage) == old text; "
      "subagent_died carries all; output paths withheld publicly", _orphans)


def _bg_stopped():
    slug = rig2()
    with Quiet() as q:
        S._bg_task_stopped(slug, "kid", "task-7", "run the tests", "killed", "/tmp/o.log")
    assert q.calls == [("kid", False)], q.calls
    row = box_last(slug, "kid")
    assert row["body"] == old_bg_stopped("task-7", "run the tests", "killed", "/tmp/o.log")
    ev = decoded(row)
    assert ev["variant"] == "runtime.background_task_stopped"
    assert ev["object"] == {"kind": "task", "org": slug, "id": "task-7", "node": "kid",
                            "description": "run the tests"}
    assert ev["summary"] == "killed" and ev["output_file"] == "/tmp/o.log"
    assert row["body"] == events.render_agent(ev)
    assert "output_file" not in events.public_event(ev)
    slug = rig2()
    with Quiet():
        S._bg_task_stopped(slug, "kid", "task-8", "quiet one", "", "")
    row = box_last(slug, "kid")
    assert row["body"] == old_bg_stopped("task-8", "quiet one", "", "")
    ev = decoded(row)
    assert ev["summary"] is None and ev["output_file"] is None


check("background task stopped · with and without summary/output == old text; "
      "TaskRef object; output path withheld publicly", _bg_stopped)


# ================================================ §R2 runtime — notices and engine mail
print("\n§R2 · runtime — restart notice, storage tiers, token expiry, late steer")

from orgtree import restart_wake                                 # noqa: E402


def old_restart_notice(current_commit, current_short, dirty_info, pid_info, started_at,
                       branch_info):
    return (
        f"[ORGTREE RESTART NOTICE] The backend was restarted. This is an informational "
        f"notice delivered to live agents so you know what code version went live.\n\n"
        f"Running build:\n"
        f"- Commit: {current_commit} (short: {current_short}){dirty_info}\n"
        f"- Backend PID: {pid_info}\n"
        f"- Started at: {started_at}{branch_info}\n\n"
        f"What you can do with this:\n"
        f"- If you were waiting on or verifying a deployed fix, check whether the running "
        f"commit contains your changes with:\n"
        f"  git merge-base --is-ancestor <your-commit> {current_commit}\n"
        f"- If you need to be woken immediately with a turn on the NEXT restart, call orgtree_restart_wake.\n"
        f"- Otherwise, no action is needed; this notice is for your awareness."
    )


def _restart_notice():
    import json as _json
    restart_wake._reset_startup_done_for_tests()
    wp = restart_wake._wakes_path()
    with open(wp, "w", encoding="utf-8") as f:
        _json.dump({"running_backend_pid": 9999, "running_commit": "0000000", "wakes": {}}, f)
    restart_wake._reset_boot_build_info_for_tests({
        "commit": "1fecd8b48f0e9112233445566778899aabbccdde", "commit_short": "1fecd8b",
        "branch": "fable/x", "dirty": True, "backend_pid": 12345,
        "started_at": "2026-09-04T12:00:00Z"})
    slug = rig2()
    try:
        res = restart_wake.on_backend_startup(dry_run=True)
        assert any(n["org"] == slug for n in res["notified"]), res
        row = box_last(slug, "boss")
        want = old_restart_notice("1fecd8b48f0e9112233445566778899aabbccdde", "1fecd8b",
                                  " [DIRTY - uncommitted changes present at boot]",
                                  "12345 (was: 9999)", "2026-09-04T12:00:00Z",
                                  ", branch: fable/x")
        assert row["body"] == want, (row["body"], want)
        ev = decoded(row)
        assert ev["variant"] == "runtime.restart_notice"
        assert ev["object"] == {"kind": "build", "commit": "1fecd8b48f0e9112233445566778899aabbccdde",
                                "short": "1fecd8b", "dirty": True, "pid": 12345}
        assert ev["prev_pid"] == 9999 and ev["branch"] == "fable/x"
        assert row["body"] == events.render_agent(ev)
        assert row.get("restart_notice") is True and row["kind"] == "notice"
        # a second boot SUPERSEDES by the typed variant (one restart row, the new one)
        restart_wake._reset_startup_done_for_tests()
        restart_wake._reset_boot_build_info_for_tests({
            "commit": "2222222222222222222222222222222222222222", "commit_short": "2222222",
            "branch": None, "dirty": False, "backend_pid": 12346,
            "started_at": "2026-09-04T13:00:00Z"})
        restart_wake.on_backend_startup(dry_run=True)
        o = store.load_org(slug)
        rows = [m for m in o.d["mail"]["boss"]
                if (events.decode(m.get("ev"), m).get("ev") or {}).get("variant")
                == "runtime.restart_notice"]
        assert len(rows) == 1 and rows[0]["body"] == old_restart_notice(
            "2222222222222222222222222222222222222222", "2222222", "", "12346 (was: 12345)",
            "2026-09-04T13:00:00Z", ""), [r["body"][:80] for r in rows]
        assert decoded(rows[0])["branch"] is None
    finally:
        restart_wake._reset_startup_done_for_tests()
        restart_wake._reset_boot_build_info_for_tests()
        try:
            os.remove(wp)
        except FileNotFoundError:
            pass


check("restart notice · body == old literal (dirty, was-pid, branch on the Started-at "
      "line); BuildRef; a later boot supersedes by typed variant", _restart_notice)


def old_disk(level, used, total):
    mb = 1
    if level == "over":
        return (f"⚠ The org disk is at {used / mb:.0f} of "
                f"{total / mb:.0f} MB (past the 90% soft cap). New "
                f"turns are PAUSED until usage drops under 85% — "
                f"the remaining space is the reserve that keeps "
                f"session journaling alive. Delete files (the admin "
                f"can also use the recovery browser or grow the "
                f"disk); at 100% every write fails with ENOSPC.")
    if level == "cleared":
        return (f"The org disk is back under the soft cap "
                f"({used / mb:.0f} / {total / mb:.0f} MB) — turns "
                f"resume.")
    return (f"Heads-up: the org disk is at {used / mb:.0f} of "
            f"{total / mb:.0f} MB (past 80%). Clean up or curb "
            f"file growth — at 90% new turns pause; at 100% "
            f"writes fail with ENOSPC.")


def old_storage(level, used_b, lim_mb):
    if level == "over":
        return (f"⚠ The org is OVER its storage limit "
                f"({used_b / 1048576:.1f} / {lim_mb} MB — workspace + "
                f"scratch + uploads together). File creation and "
                f"writes in the workspace and every scratch folder "
                f"are now BLOCKED at the OS level — new writes will "
                f"fail with permission errors. Deleting still works: "
                f"remove large files you created and the block lifts "
                f"automatically at the next check. Do NOT keep "
                f"generating files.")
    if level == "cleared":
        return (f"Storage is back under the limit "
                f"({used_b / 1048576:.1f} / {lim_mb or '∞'} MB) — "
                f"writes are unblocked.")
    return (f"Heads-up: the org is at {used_b / 1048576:.1f} of "
            f"{lim_mb} MB (past 90% of the storage limit). Clean "
            f"up or curb file growth — at the limit, workspace "
            f"AND scratch writes are blocked at the OS level.")


def _storage():
    slug = rig2()
    o = store.load_org(slug)
    # every tier × scope renders the old text
    for lvl in ("over", "cleared", "heads_up"):
        ev = S._storage_ev(o, lvl, "disk", 921.4, 1024.0)
        assert events.render_agent(ev) == old_disk(lvl, 921.4, 1024.0), lvl
        ev = S._storage_ev(o, lvl, "storage", 460.7 * 1048576 / 1048576, 500.0)
        assert events.render_agent(ev) == old_storage(lvl, 460.7 * 1048576, 500), lvl
    ev = S._storage_ev(o, "cleared", "storage", 3.25, None)
    assert events.render_agent(ev) == old_storage("cleared", 3.25 * 1048576, 0)
    assert ev["cap_mb"] is None
    # big integer caps print as ints (the old text used the int)
    assert "/ 1500000 MB" in events.render_agent(S._storage_ev(o, "over", "storage", 1.0, 1500000.0))
    # integration: the disk check at 95% blocks and notifies every live node, typed
    from orgtree import disk as dsk
    orig = dsk.usage
    dsk.usage = lambda slug_, max_age=5.0: (int(0.95 * 1024 * 1048576), 1024 * 1048576)
    try:
        with Quiet():
            assert S._storage_check_disk(slug, store.load_org(slug)) == "blocked"
    finally:
        dsk.usage = orig
    o = store.load_org(slug)
    for nid in ("boss", "kid"):
        row = dict(o.d["notices"][nid][-1])
        assert row["text"] == old_disk("over", 0.95 * 1024, 1024), row["text"]
        ev = decoded(row)
        assert ev["variant"] == "runtime.storage" and ev["level"] == "over"
        assert ev["scope"] == "disk" and abs(ev["used_mb"] - 0.95 * 1024) < 1e-6
        assert ev["object"] == {"kind": "org", "org": slug}
    assert o.d.get("storage_blocked") is True


check("storage · six tier/scope texts == old; ∞ cap; int cap; disk check at 95% "
      "notifies every live node with a typed runtime.storage row", _storage)


def _token():
    slug = rig2()
    o = store.load_org(slug)
    ev = events.mint("runtime.token_expiry", S._SYSTEM_ACTOR, S._org_ref(o), days=1.234)
    assert events.render_agent(ev) == (
        "⚠ The Claude subscription's refresh token expires in "
        f"~{max(0.0, 1.234):.1f} days. When it lapses, re-login "
        "is INTERACTIVE and every turn fails until someone signs in — "
        "open Claude Code on this machine soon, or give the org "
        "an API key (settings → autonomy).")
    assert "~0.0 days" in events.render_agent(
        events.mint("runtime.token_expiry", S._SYSTEM_ACTOR, S._org_ref(o), days=-2.0))


check("token expiry · rendering == old literal (negative days clamp to 0.0)", _token)


def old_late(nid, waited, boundary):
    return (
        f'Your mid-turn message to "{nid}" has NOT been read yet — it has '
        f'been waiting {S._dur(waited)} in its steer store. Mid-turn mail is '
        f'injected when the recipient\'s current tool call returns'
        + (f", and {nid} has been inside one call for {S._dur(boundary)}"
           if isinstance(boundary, (int, float)) else "")
        + f'. Nothing is lost — it is delivered at that boundary, or at '
          f'{nid}\'s next turn if the turn ends first. If it cannot wait '
          f'that long, orgtree_interrupt (⏸) on {nid} creates a boundary '
          f'immediately without ending its session.')


def _late_steer():
    import time as _time
    slug = rig2()
    # boss mailed kid; the drain journaled it under tok "t-1"; kid is mid-turn
    o = store.load_org(slug)
    o.d.setdefault("delivering", {})["kid"] = [{
        "tok": "t-1", "at": "2026-09-06T21:00:00Z", "via": "steer",
        "mail": [{"id": "abc123def456", "from": "boss", "kind": "message",
                  "body": "hi", "at": "2026-09-06T21:00:00Z"}], "notices": []}]
    store.save_org(o)
    now = _time.time()
    with S._state_lock:
        S._state[(slug, "kid")] = {"responding": True, "busy": True,
                                   "boundary_at": now - 400.0,
                                   "steer": [{"toks": ["t-1"], "text": "…", "view": "",
                                              "from": "boss", "at": now - 300.0}]}
    try:
        with Quiet() as q:
            due = S._steer_late_sweep(now)
        assert [(d[0], d[1], d[2]) for d in due] == [(slug, "boss", "kid")], due
        assert q.calls == [("boss", False)], q.calls
        o = store.load_org(slug)
        row = dict(o.d["notices"]["boss"][-1])
        bnd = S.steer_wait(slug, "kid")
        # the boundary is measured at alarm time; re-measure within the same second
        want_a = old_late("kid", 300.0, bnd)
        assert row["text"].split(", and kid has been inside")[0] == \
            want_a.split(", and kid has been inside")[0], row["text"]
        assert "inside one call for" in row["text"]
        ev = decoded(row)
        assert ev["variant"] == "runtime.delivery_unread" and ev["to"] == "kid"
        assert ev["waited"] == "5m00s" and ev["boundary_for"]
        assert ev["object"] == {"kind": "mail", "org": slug, "box": "node", "node": "kid",
                                "id": "abc123def456", "sender": "boss",
                                "at": "2026-09-06T21:00:00Z"}
        assert row["text"] == events.render_agent(ev)
        # second sweep: told once
        assert S._steer_late_sweep(now + 10) == []
        # a carrier whose mail the journal no longer holds: qualified ref, EMPTY id
        with S._state_lock:
            S._state[(slug, "kid")]["steer"].append(
                {"toks": ["gone"], "text": "…", "view": "", "from": "boss", "at": now - 700.0})
        with Quiet():
            S._steer_late_sweep(now)
        row = dict(store.load_org(slug).d["notices"]["boss"][-1])
        ev = decoded(row)
        assert ev["object"]["id"] == "" and ev["object"]["sender"] == "boss"
        assert ev["waited"] == "11m40s"
    finally:
        with S._state_lock:
            S._state.pop((slug, "kid"), None)


check("late steer · sender's notice == old text; runtime.delivery_unread on the journaled "
      "MailRef (id resolved by token); once per carrier; missing mail → empty id",
      _late_steer)


# ================================================================== §S family status
print("\n§S · status — orgtree_status done/blocked report to the superior")

from fastapi.testclient import TestClient                        # noqa: E402
from orgtree import api                                          # noqa: E402

_client = TestClient(api.app)


def _status_report():
    slug = rig2()
    with Quiet():
        r = _client.post("/api/agent", json={"org": slug, "node": "kid", "tool": "orgtree_status",
                                             "args": {"status": "done",
                                                      "summary": "shipped the thing"}})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res.get("reported_to") == "boss" and res.get("id"), res
    row = box_last(slug, "boss")
    assert row["id"] == res["id"]
    assert row["body"] == "[DONE] shipped the thing", row["body"]    # the old f-string
    assert row["kind"] == "status" and row["from"] == "kid"
    ev = decoded(row)
    assert ev["variant"] == "status.report" and ev["state"] == "done"
    assert ev["summary"] == "shipped the thing"
    assert ev["actor"] == {"kind": "agent", "id": "kid"}
    assert ev["object"]["kind"] == "node" and ev["object"]["id"] == "kid"
    assert row["body"] == events.render_agent(ev)
    with Quiet():
        r = _client.post("/api/agent", json={"org": slug, "node": "kid", "tool": "orgtree_status",
                                             "args": {"status": "blocked", "summary": "need X"}})
    assert r.status_code == 200, r.text
    row = box_last(slug, "boss")
    assert row["body"] == "[BLOCKED] need X" and decoded(row)["state"] == "blocked"
    # working/idle: no mail (unchanged)
    before = len(store.load_org(slug).d["mail"]["boss"])
    with Quiet():
        r = _client.post("/api/agent", json={"org": slug, "node": "kid", "tool": "orgtree_status",
                                             "args": {"status": "working", "summary": "…"}})
    assert r.status_code == 200 and len(store.load_org(slug).d["mail"]["boss"]) == before


check("status · done/blocked reports mint status.report ('[DONE] summary' byte-identical); "
      "working/idle send no mail", _status_report)


# ============================================================ §D docket producers
print("\n§D · docket — assignment, review request, review decisions, participation, "
      "attention dismissed")

# ⚠ VERBATIM copies of the pre-typed producers (ledger.py / api.py @ efb8d48).
def old_assign(actor, it, own, prev):
    return (
        f"[DOCKET ASSIGNMENT · {it['slug']} \"{str(it.get('title') or '')[:80]}\"] "
        f"You are now the ASSIGNMENT on this docket item — that is "
        f"OWNERSHIP: you hold its management rights, the user's replies on "
        f"it come to you, and you are who the docket names as responsible."
        f"\nAssigned by {'the user' if actor == USER else actor}"
        f"{f' (previously {prev})' if prev and prev != own else ''}."
        f"\nDescription: {str(it.get('objective') or '(none recorded)')[:600]}"
        f"\nLatest status — done so far: "
        f"{'; '.join(it.get('done_so_far') or []) or '(nothing recorded)'}"
        f"\nWorking on / next: "
        f"{'; '.join(it.get('working_on_next') or []) or '(nothing recorded)'}"
        f"\nRead it in full with orgtree_work get slug={it['slug']}, and "
        f"`update` it at the next meaningful boundary — your update is what "
        f"the user reads.")


def old_review_req(actor, it, owner_node):
    return (
        f"[DOCKET REVIEW REQUEST · {it['slug']} "
        f"\"{str(it.get('title') or '')[:80]}\"] "
        f"You are named as the REVIEWER of this docket item. THIS IS NOT "
        f"OWNERSHIP: {owner_node or 'its owner'} "
        f"keeps the work and the responsibility for delivering it. You "
        f"hold exactly three things — read it, add `evidence`, and record "
        f"ONE decision with orgtree_work action='review': `approve` (the "
        f"check passed — that COMPLETES the item) or `changes` (it goes "
        f"back to the owner as in_progress, and your note is what they act "
        f"on). Until you decide, the next action on this item is yours."
        f"\nRequested by {'the user' if actor == USER else actor}."
        f"\nDescription: {str(it.get('objective') or '(none recorded)')[:600]}"
        f"\nWhat the owner says is done: "
        f"{'; '.join(it.get('done_so_far') or []) or '(nothing recorded)'}")


def old_participation(actor, it, owner):
    return (
        f"[DOCKET PARTICIPATION · {it['slug']} "
        f"\"{str(it.get('title') or '')[:80]}\"] You are now a "
        f"PARTICIPANT on this docket item — not its assignment. "
        f"The item is owned by {owner or 'nobody (unassigned)'}; "
        f"you may read it, update it, add evidence and attach "
        f"questions, and the user's replies addressed to you on "
        f"it arrive as item-linked mail. Added by "
        f"{'the user' if actor == USER else actor}."
        f"\nDescription: {str(it.get('objective') or '(none recorded)')[:600]}"
        f"\nRead it with orgtree_work get slug={it['slug']} when "
        f"your work touches it; no reply is expected to this "
        f"notice.")


def old_review_head(it):
    return (f"[DOCKET REVIEW · {it['slug']} "
            f"\"{str(it.get('title') or '')[:80]}\"] ")


def old_approved(actor, note):
    return (f"REVIEW PASSED — {'the user' if actor == USER else actor} "
            f"approved this item and it is now DONE. Nothing further is "
            f"needed on it."
            + (f"\nReviewer's note: {str(note)[:500]}" if note else ""))


def old_changes(actor, note):
    return (f"CHANGES REQUESTED by "
            f"{'the user' if actor == USER else actor} — the item is "
            f"back with you as in_progress and the next action is "
            f"yours."
            + (f"\nWhat the reviewer asked for: {str(note)[:500]}"
               if note else
               "\nThe reviewer left no note; ask them what they want "
               "changed rather than guessing."))


def old_relay(actor):
    return (f"\n(This notice comes from the docket itself: "
            f"{actor} is the item's reviewer but cannot address you "
            f"directly under the mail rules. Reply to your own superior "
            f"if you need to reach them.)")


def old_dismiss(wid, reason, pending):
    return (f"[DOCKET · {wid}] The user DISMISSED your "
            f"attention flag (\"{str(reason or '')[:200]}\") "
            f"— the item is now BLOCKED. Do not re-raise the "
            f"same reason without material new information; "
            f"{pending} question(s) on "
            f"the item are still pending.")


def rig3():
    """boss → kid, kid2 (siblings under boss)."""
    slug = rig2()
    o = store.load_org(slug)
    o.hire("boss", "boss", "haiku", 5, "kid2", add_dirs=[],
           tools={"bash": True, "web": False, "edit": False, "subagents": False, "mcp": []},
           org_visibility="team", charter="docket fixture")
    store.save_org(o)
    return slug


def _assignment():
    slug = rig3()
    o = store.load_org(slug)
    r = o.work_create("boss", "Ship the widget", "widgets are missing; ship one",
                      owner="boss", done_so_far=["a", "b"], working_on_next=["c"])
    wid = r["created"]
    o.work_assign("boss", wid, "kid")
    store.save_org(o)
    it = dict(store.load_org(slug)._work_find(wid)[0])
    row = box_last(slug, "kid")
    assert row["body"] == old_assign("boss", it, "kid", "boss"), row["body"]
    assert row["kind"] == "request"
    ev = decoded(row)
    assert ev["variant"] == "docket.assigned" and ev["owner"] == "kid"
    assert ev["previous_owner"] == "boss" and ev["assigner"] == "boss"
    assert ev["object"] == {"kind": "work_item", "org": slug, "slug": wid,
                            "title": "Ship the widget"}
    assert ev["done_so_far"] == ["a", "b"] and ev["working_on_next"] == ["c"]
    assert ev["status"] == it["status"]
    assert row["body"] == events.render_agent(ev)
    # the user assigning: 'the user', no previous when unowned
    o = store.load_org(slug)
    r2 = o.work_create(USER, "Unowned thing", "nobody owns it; assign it")
    o.work_assign(USER, r2["created"], "kid2")
    store.save_org(o)
    it2 = dict(store.load_org(slug)._work_find(r2["created"])[0])
    row = box_last(slug, "kid2")
    assert row["body"] == old_assign(USER, it2, "kid2", None), row["body"]
    ev = decoded(row)
    assert ev["previous_owner"] is None and ev["actor"] == {"kind": "user", "id": "@user"}
    assert ev["done_so_far"] == [] and "(nothing recorded)" in row["body"]


check("assignment · agent- and user-assigned bodies == old text; docket.assigned with "
      "WorkItemRef, previous_owner null when unowned", _assignment)


def _review_flow():
    slug = rig3()
    o = store.load_org(slug)
    wid = o.work_create("kid", "Review me", "needs a check; review it",
                        done_so_far=["did x"])["created"]
    o.work_update("kid", wid, ["did x", "did y"], [], status="review", reviewer="boss")
    store.save_org(o)
    it = dict(store.load_org(slug)._work_find(wid)[0])
    row = box_last(slug, "boss")
    assert row["body"] == old_review_req("kid", it, "kid"), row["body"]
    ev = decoded(row)
    assert ev["variant"] == "docket.review_requested" and ev["reviewer"] == "boss"
    assert ev["requested_by"] == "kid" and ev["owner"] == "kid"
    assert ev["done_so_far"] == ["did x", "did y"]
    assert row["body"] == events.render_agent(ev)
    # changes, no note
    o = store.load_org(slug)
    o.work_review_decide("boss", wid, "changes")
    store.save_org(o)
    row = box_last(slug, "kid")
    assert row["body"] == old_review_head(it) + old_changes("boss", None), row["body"]
    ev = decoded(row)
    assert ev["variant"] == "docket.review_changes" and ev["note"] is None
    assert ev["relayed"] is False and ev["reviewer"] == "boss" and ev["owner"] == "kid"
    # back to review, then approve with a note
    o = store.load_org(slug)
    o.work_update("kid", wid, ["did x", "did y", "fixed"], [], status="review", reviewer="boss")
    o.work_review_decide("boss", wid, "approve", note="nice work")
    store.save_org(o)
    row = box_last(slug, "kid")
    assert row["body"] == old_review_head(it) + old_approved("boss", "nice work"), row["body"]
    ev = decoded(row)
    assert ev["variant"] == "docket.review_approved" and ev["note"] == "nice work"
    assert row["body"] == events.render_agent(ev)


check("review · request, changes (no note) and approval (with note) == old text; "
      "review_requested / review_changes / review_approved events", _review_flow)


def _review_relayed():
    """A reviewer that cannot address the owner: the docket's own voice, SAYING SO."""
    slug = rig3()
    o = store.load_org(slug)
    # grandkid (under kid2) owns; kid — its uncle — is named reviewer by boss. An
    # uncle is neither superior nor peer of the owner, so it cannot address it.
    o.hire("kid2", "kid2", "haiku", 2, "grandkid", add_dirs=[],
           tools={"bash": True, "web": False, "edit": False, "subagents": False, "mcp": []},
           org_visibility="team", charter="relay fixture")
    wid = o.work_create("grandkid", "Uncle review", "an uncle cannot mail; relay it")["created"]
    o.work_update("boss", wid, ["draft"], [], status="review", reviewer="kid", owner="grandkid")
    store.save_org(o)
    it = dict(store.load_org(slug)._work_find(wid)[0])
    o = store.load_org(slug)
    o.work_review_decide("kid", wid, "changes", note="please rename")
    store.save_org(o)
    row = box_last(slug, "grandkid")
    assert row["from"] == USER, row["from"]
    assert row["body"] == old_review_head(it) + old_changes("kid", "please rename") \
        + old_relay("kid"), row["body"]
    ev = decoded(row)
    assert ev["relayed"] is True and ev["reviewer"] == "kid"
    assert ev["actor"] == {"kind": "agent", "id": "kid"}, "the reviewer authored it, whoever carried it"
    assert row["body"] == events.render_agent(ev)


check("review · relayed decision (reviewer cannot address owner) == old text incl. the "
      "docket-voice suffix; relayed=True, actor stays the reviewer", _review_relayed)


def _participation_and_dismiss():
    slug = rig3()
    o = store.load_org(slug)
    wid = o.work_create("boss", "Shared item", "two hands needed; share it", owner="boss")["created"]
    o.work_participants("boss", wid, add=["kid"])
    store.save_org(o)
    it = dict(store.load_org(slug)._work_find(wid)[0])
    row = box_last(slug, "kid")
    assert row["body"] == old_participation("boss", it, "boss"), row["body"]
    assert row["kind"] == "notice"
    ev = decoded(row)
    assert ev["variant"] == "docket.participant_added" and ev["owner"] == "boss"
    assert ev["added_by"] == "boss" and row["body"] == events.render_agent(ev)
    # attention flag raised by kid, dismissed by the user through the route
    o = store.load_org(slug)
    o.work_update("kid", wid, ["x"], [], attention=True, attention_reason="please look")
    store.save_org(o)
    it = dict(store.load_org(slug)._work_find(wid)[0])
    rev = int(it["manual_attention_rev"])
    with Quiet():
        r = _client.post(f"/api/orgs/{slug}/work-items/{wid}/dismiss-attention",
                         json={"set_rev": rev})
    assert r.status_code == 200, r.text
    row = box_last(slug, "kid")
    assert row["body"] == old_dismiss(wid, "please look", 0), row["body"]
    assert row["kind"] == "status" and row["from"] == USER
    ev = decoded(row)
    assert ev["variant"] == "decision.attention_dismissed"
    assert ev["reason"] == "please look" and ev["pending_questions"] == 0
    assert ev["object"]["slug"] == wid
    assert row["body"] == events.render_agent(ev)
    assert "dismissed_by" not in events.public_event(ev)


check("participation notice and attention-dismissed status == old text; "
      "participant_added / attention_dismissed events", _participation_and_dismiss)


# ====================================================== §A family answer_decision
print("\n§A · answers — ask answer/dismiss, batch, credit, audience, routed question")

# ⚠ VERBATIM copies of the pre-typed producers (ledger.py @ e0d9a78).
def old_single(question, sel, txt):
    body = "[ANSWER to your question]\nQ: " + question
    if sel:
        body += "\nSelected: " + " · ".join(sel)
    if txt:
        body += ("\nAnswer: " if not sel else "\nAlso: ") + txt
    return body


def old_multi(qs, norm, txt):
    lines = ["[ANSWER to your questions]"]
    for i, (qd, v) in enumerate(zip(qs, norm)):
        label = qd.get("header") or f"Q{i + 1}"
        ans = " · ".join(v) if isinstance(v, list) else v
        lines.append(f"{label} — {qd['question']}\n→ {ans}")
    if txt:
        lines.append("Also: " + txt)
    return "\n".join(lines)


def old_dismiss(question):
    return ("[QUESTION DISMISSED] The user closed your question "
            "without answering:\nQ: " + question
            + "\nProceed on your best judgment, or re-ask later "
              "with a sharper framing.")


def old_batch_ask(qs, norm):
    answered = sum(1 for v in norm if v is not None)
    lines = []
    for i, (qd, v) in enumerate(zip(qs, norm)):
        label = qd.get("header") or f"Q{i + 1}"
        if v is None:
            lines.append(f"{label} — {qd['question']}\n→ (skipped — "
                         f"the user left this one unanswered)")
        else:
            ans = (" · ".join(str(x) for x in v) if isinstance(v, list) else str(v))
            lines.append(f"{label} — {qd['question']}\n→ {ans}")
    return (("[ANSWERS to your questions]\n" if answered else
             "[your questions were SKIPPED]\n") + "\n".join(lines))


def old_credit_skipped(old, new):
    return (f"[CREDIT REQUEST skipped] Your ask "
            f"({old:g} → {new:g}) was left "
            f"undecided — you may re-ask later.")


def old_credit(old, new, give, now_g):
    asked = f"you asked {old:g} → {new:g}"
    if give == new:
        return (f"The user APPROVED your credit request — your "
                f"grant is now {now_g:g}.")
    elif give > old:
        return (f"The user COUNTER-OFFERED: {asked}; granted "
                f"{old:g} → {give:g} ({give - old:+g}). You may take "
                f"this as-is, request more later, or find another "
                f"way within it.")
    elif give == old:
        return (f"The user DECLINED the increase — {asked}; your "
                f"grant stays {now_g:g}. You may re-ask with a "
                f"stronger case, or work within it.")
    return (f"The user REDUCED your grant: {asked}; your grant "
            f"is now {give:g} ({give - old:+g} — unused credits "
            f"reclaimed). You may re-ask, or work within it.")


def old_credit_denied(old, new):
    return (f"The user DENIED your credit request "
            f"({old:g} → {new:g}). Your grant stays "
            f"{old:g} — work within it, re-ask with a stronger "
            f"case, or escalate differently.")


def old_routed(batch):
    parts = []
    for qd in batch:
        p = str(qd.get("question"))
        if qd.get("header"):
            p = f"[{qd['header']}] {p}"
        if qd.get("work_item"):
            p = f"(docket item {qd['work_item']}) {p}"
        o = qd.get("options") or []
        if o:
            p += "\nOptions: " + " · ".join(x["label"] for x in o) \
                + (" (several may apply)" if qd.get("multi") else "")
        parts.append(p)
    return ("[QUESTION — needs an answer]\n"
            if len(batch) == 1 else
            f"[QUESTIONS — {len(batch)} need answers]\n") + "\n\n".join(parts)


def _asks():
    slug = rig2()
    # single, selected + text, via the route
    o = store.load_org(slug)
    o.ask_user("boss", "which db?", options=["sqlite", "pg"], header="DB")
    aid = [a for a in o.d["asks"] if a["status"] == "open"][0]["id"]
    store.save_org(o)
    with Quiet():
        r = _client.post(f"/api/orgs/{slug}/asks/{aid}/answer",
                         json={"selected": ["sqlite"], "text": "and keep WAL on"})
    assert r.status_code == 200, r.text
    row = box_last(slug, "boss")
    assert row["body"] == old_single("which db?", ["sqlite"], "and keep WAL on"), row["body"]
    ev = decoded(row)
    assert ev["variant"] == "answer.ask" and ev["single"] is True and ev["dismissed"] is False
    assert ev["questions"] == [{"label": "DB", "question": "which db?", "selected": ["sqlite"]}]
    assert ev["text"] == "and keep WAL on" and ev["object"] == {"kind": "ask", "org": slug,
                                                                 "id": aid, "node": "boss"}
    assert row["body"] == events.render_agent(ev) and row["from"] == USER
    # multi (three tabs, one multi-select), no text — ledger call, body returned too
    o = store.load_org(slug)
    qs = [{"question": "storage?", "header": "Storage", "options": ["sqlite", "pg"]},
          {"question": "flags?", "options": ["a", "b"], "multi": True},
          {"question": "who reviews?"}]
    o.ask_user("boss", questions=qs)
    aid = [a for a in o.d["asks"] if a["status"] == "open"][0]["id"]
    r2 = o.ask_answer(aid, selected=["sqlite", ["a", "b"], "opus"])
    stored = o.d["asks"][-1]["questions"]
    want = old_multi(stored, ["sqlite", ["a", "b"], "opus"], "")
    assert r2["body"] == want, (r2["body"], want)
    ev = r2["ev"]
    assert ev["single"] is False and ev["questions"][1] == {"label": None, "question": "flags?",
                                                           "selected": ["a", "b"]}
    assert ev["questions"][0]["label"] == "Storage" and ev["text"] is None
    assert events.render_agent(ev) == want
    # dismissed
    o.ask_user("boss", "later?", options=["y", "n"])
    aid = [a for a in o.d["asks"] if a["status"] == "open"][0]["id"]
    r3 = o.ask_dismiss(aid)
    assert r3["body"] == old_dismiss("later?"), r3["body"]
    assert r3["ev"]["dismissed"] is True and r3["ev"]["questions"][0]["selected"] == []
    assert events.render_agent(r3["ev"]) == r3["body"]


check("asks · single (selected+text via the route), multi (headers, multi-select) and "
      "dismissed bodies == old text; answer.ask events with AskRef", _asks)


def _batch():
    slug = rig2()
    o = store.load_org(slug)
    o.set_scope(USER, "boss", tools={"bash": False, "web": False, "edit": True,
                                     "subagents": False, "mcp": []})
    o.ask_user("boss", questions=[{"question": "a?", "header": "A"}, {"question": "b?"}])
    o.request_credits("boss", 30, "more compute")
    o.request_scope("boss", [{"kind": "dir", "path": "E:/data", "mode": "ro"},
                             {"kind": "tool", "tool": "web"}], "the dataset")
    card = o.node_ask("boss")
    store.save_org(o)
    o = store.load_org(slug)
    r = o.resolve_batch("boss", card["revs"], answers=["yes", None],
                        credits={"skip": True}, scope=["approve", "deny"])
    store.save_org(o)
    qs = o.d["asks"][0]["questions"]
    want_ask = old_batch_ask(qs, ["yes", None])
    want_cr = old_credit_skipped(20, 30)
    assert r["body"].startswith(want_ask + "\n\n" + want_cr + "\n\n[SCOPE REQUEST decided]\n"), \
        r["body"]
    assert "- folder E:/data (ro) → GRANTED — live from your next turn" in r["body"]
    assert "- tool: web → denied" in r["body"]
    ev = r["ev"]
    assert ev["variant"] == "answer.batch"
    assert [sct["kind"] for sct in ev["sections"]] == ["ask", "credit", "scope"]
    assert ev["sections"][0]["questions"] == [{"label": "A", "question": "a?", "answer": "yes"},
                                              {"label": None, "question": "b?", "answer": None}]
    assert ev["sections"][1] == {"kind": "credit", "outcome": "skipped", "old": 20.0,
                                 "asked": 30.0, "granted": None, "now": None}
    assert ev["sections"][2]["decisions"] == [
        {"label": "folder E:/data (ro)", "decision": "approve"},
        {"label": "tool: web", "decision": "deny"}]
    assert events.render_agent(ev) == r["body"]
    assert ev["object"]["kind"] == "batch" and ev["object"]["node"] == "boss"
    pub = events.public_event(ev)
    assert "label" not in pub["sections"][2]["decisions"][0], "scope labels carry paths"
    assert "ask_id" not in pub["sections"][0]
    # credit decided inside the batch: the credit section renders the credit text
    o = store.load_org(slug)
    o.ask_user("boss", "c?", options=["x"])
    o.request_credits("boss", 40, "even more")
    card = o.node_ask("boss")
    r = o.resolve_batch("boss", card["revs"], answers=["x"], credits={"granted": 25})
    assert r["body"].endswith("\n\n" + old_credit(20, 40, 25, 25)), r["body"]
    assert r["ev"]["sections"][1]["outcome"] == "counter"


check("batch · answers + skipped credit + scope verdicts == old text; answer.batch "
      "sections; credit decided in-batch renders the credit text", _batch)


def _credit_route():
    for give, expect_outcome in ((30, "approved"), (25, "counter"), (20, "declined"),
                                 (10, "reduced")):
        slug = rig2()
        o = store.load_org(slug)
        o.request_credits("boss", 30, "more")
        rid = o.d["credit_requests"][0]["id"]
        store.save_org(o)
        with Quiet():
            r = _client.post(f"/api/orgs/{slug}/credit-requests",
                             json={"id": rid, "action": "approve", "granted": give})
        assert r.status_code == 200, (give, r.text)
        assert "ev" not in r.json()
        now_g = store.load_org(slug).node("boss")["grant"]
        row = box_last(slug, "boss")
        assert row["body"] == old_credit(20, 30, give, now_g), (give, row["body"])
        ev = decoded(row)
        assert ev["variant"] == "decision.credit" and ev["outcome"] == expect_outcome
        assert ev["old"] == 20.0 and ev["asked"] == 30.0 and ev["granted"] == float(give)
        assert ev["object"] == {"kind": "credit_request", "org": slug, "id": rid, "node": "boss"}
        assert row["body"] == events.render_agent(ev)
    slug = rig2()
    o = store.load_org(slug)
    o.request_credits("boss", 30, "more")
    rid = o.d["credit_requests"][0]["id"]
    store.save_org(o)
    with Quiet():
        r = _client.post(f"/api/orgs/{slug}/credit-requests",
                         json={"id": rid, "action": "deny"})
    assert r.status_code == 200, r.text
    row = box_last(slug, "boss")
    assert row["body"] == old_credit_denied(20, 30), row["body"]
    ev = decoded(row)
    assert ev["outcome"] == "denied" and ev["granted"] is None and ev["now"] is None


check("credit · approved/counter/declined/reduced/denied via the route == old text; "
      "decision.credit with CreditReqRef", _credit_route)


def _audience():
    def rig_g():
        slug_ = rig2()
        o_ = store.load_org(slug_)
        o_.hire("kid", "kid", "haiku", 2, "grandkid", add_dirs=[],
                tools={"bash": True, "web": False, "edit": False, "subagents": False,
                       "mcp": []}, org_visibility="team", charter="audience fixture")
        store.save_org(o_)
        return slug_
    # grandkid asks for boss (its grandparent); boss — the target — grants: mail from boss
    slug = rig_g()
    o = store.load_org(slug)
    o.request_audience("grandkid", "boss", "need to talk")
    o.audience_forward("kid", "grandkid", "boss")
    o.audience_grant("boss", "grandkid", "boss")
    store.save_org(o)
    row = box_last(slug, "grandkid")
    assert row["body"] == "Audience granted: you may message boss directly until it is rescinded."
    assert row["kind"] == "decision" and row["from"] == "boss"
    ev = decoded(row)
    assert ev["variant"] == "decision.audience" and ev["granted"] is True
    assert ev["object"] == {"kind": "audience_request", "org": slug, "node": "grandkid",
                            "target": "boss"}
    assert row["body"] == events.render_agent(ev)
    # denied by the agent it currently awaits (kid, the first hop)
    slug = rig_g()
    o = store.load_org(slug)
    o.request_audience("grandkid", "boss", "again")
    o.audience_deny("kid", "grandkid", "boss")
    store.save_org(o)
    row = box_last(slug, "grandkid")
    assert row["body"] == "Your audience request to reach boss was declined at kid.", row["body"]
    ev = decoded(row)
    assert ev["granted"] is False and ev["decided_by"] == "kid" and ev["target"] == "boss"
    assert row["body"] == events.render_agent(ev)
    # denied by the user: a passive NOTICE row
    slug = rig_g()
    o = store.load_org(slug)
    o.request_audience("grandkid", USER, "please")
    o.audience_deny(USER, "grandkid", USER)
    store.save_org(o)
    n = dict(store.load_org(slug).d["notices"]["grandkid"][-1])
    assert n["text"] == "The user declined your audience request.", n
    ev = decoded(n)
    assert ev["granted"] is False and ev["decided_by"] == USER and ev["actor"]["kind"] == "user"
    assert n["text"] == events.render_agent(ev)


check("audience · grant by the target, deny by an agent (mail) and by the user (notice) "
      "== old text; decision.audience on the (node, target) ref", _audience)


def _routed():
    slug = rig2()
    o = store.load_org(slug)
    wid = o.work_create("kid", "Linked", "linked question; route it")["created"]
    batch = [{"question": "storage?", "header": "Storage", "options": ["sqlite", "pg"],
              "work_item": wid},
             {"question": "flags?", "options": ["a", "b"], "multi": True},
             {"question": "plain?"}]
    r = o.ask_user("kid", questions=batch)
    assert r.get("routed") == "boss", r
    store.save_org(o)
    normd = [{"question": "storage?", "header": "Storage",
              "options": [{"label": "sqlite"}, {"label": "pg"}], "work_item": wid},
             {"question": "flags?", "options": [{"label": "a"}, {"label": "b"}], "multi": True},
             {"question": "plain?"}]
    row = box_last(slug, "boss")
    assert row["body"] == old_routed(normd), row["body"]
    assert row["kind"] == "question" and row["from"] == "kid"
    ev = decoded(row)
    assert ev["variant"] == "ask.routed" and ev["object"]["kind"] == "node"
    assert ev["object"]["id"] == "kid" and ev["from_node"] == "kid"
    assert ev["questions"][0] == {"header": "Storage", "text": "storage?", "work_item": wid,
                                  "options": ["sqlite", "pg"], "multi": False}
    assert ev["questions"][1]["multi"] is True and ev["questions"][2]["options"] == []
    assert row["body"] == events.render_agent(ev)
    o = store.load_org(slug)
    r = o.ask_user("kid", "one?", options=["y"])
    store.save_org(o)
    row = box_last(slug, "boss")
    assert row["body"] == old_routed([{"question": "one?", "options": [{"label": "y"}]}])
    assert row["body"].startswith("[QUESTION — needs an answer]\n")


check("routed question · three tabs (header, docket link, multi) and a single == old text; "
      "ask.routed on the asker's NodeRef", _routed)


# ==================================================== §X family access_resources
print("\n§X · access — audience requests/changes, scope request/change, grants, kiosk")

from orgtree.ledger import EXTERN, SYSTEM                        # noqa: E402


# ⚠ VERBATIM copies of the pre-typed producers (ledger.py @ 59c96ea).
def old_aud_initial(actor, target, reason):
    return (f'AUDIENCE REQUEST: your report "{actor}" asks to speak directly with '
            f'{target}. Reason: "{reason[:300]}". You may forward it one hop up '
            f'(orgtree_audience action=forward), deny it (action=deny), or simply '
            f'handle the matter yourself and deny.')


def old_aud_forwarded(frm, target, reason):
    return (f'AUDIENCE REQUEST (forwarded): "{frm}" seeks {target}. '
            f'Reason: {reason}. Forward, deny, or handle it.')


def old_aud_target(frm, reason):
    return (f'AUDIENCE REQUEST reached you: "{frm}" asks to speak '
            f'with you directly. Reason: {reason}. Grant with '
            f'orgtree_audience action=grant, or deny.')


def old_aud_user(frm, reason):
    return (f'Audience request (forwarded up the chain): "{frm}" asks '
            f'to speak with you directly. Reason: {reason}. '
            f'Grant or deny it from the inbox panel.')


def old_scope_req(labels, reason):
    return ("[SCOPE REQUEST — needs a grant or an escalation]\n"
            + "\n".join("- " + x for x in labels)
            + f"\nReason: {str(reason).strip()}"
            + "\nIf you hold these, grant them directly with "
              "orgtree_retool; otherwise escalate up your chain — "
              "only the user can grant past your own scope.")


def notices(slug, nid):
    return [dict(r) for r in (store.load_org(slug).d.get("notices") or {}).get(nid) or []]


def rig4():
    """boss → kid → grandkid; boss2 top-level."""
    slug = rig2()
    o = store.load_org(slug)
    o.hire("kid", "kid", "haiku", 2, "grandkid", add_dirs=[],
           tools={"bash": True, "web": False, "edit": False, "subagents": False, "mcp": []},
           org_visibility="team", charter="fixture")
    o.hire(USER, None, "opus", 20, "boss2")
    store.save_org(o)
    return slug


def _audience_requests():
    slug = rig4()
    o = store.load_org(slug)
    o.request_audience("grandkid", "boss", "x" * 400)
    store.save_org(o)
    row = box_last(slug, "kid")
    assert row["body"] == old_aud_initial("grandkid", "boss", "x" * 400), row["body"]
    assert row["kind"] == "request" and row["from"] == "grandkid"
    ev = decoded(row)
    assert ev["variant"] == "access.audience_requested" and ev["stage"] == "initial"
    assert ev["object"] == {"kind": "audience_request", "org": slug, "node": "grandkid",
                            "target": "boss"}
    assert ev["reason"] == "x" * 300 and row["body"] == events.render_agent(ev)
    # forwarded one hop: kid → boss, and boss IS the target
    o = store.load_org(slug)
    o.audience_forward("kid", "grandkid", "boss")
    store.save_org(o)
    row = box_last(slug, "boss")
    assert row["body"] == old_aud_target("grandkid", "x" * 300), row["body"]
    assert decoded(row)["stage"] == "target"
    # a request for the user climbs: grandkid → kid (initial) → boss (forwarded) → user inbox
    slug = rig4()
    o = store.load_org(slug)
    o.request_audience("grandkid", USER, "please")
    o.audience_forward("kid", "grandkid", USER)
    store.save_org(o)
    row = box_last(slug, "boss")
    assert row["body"] == old_aud_forwarded("grandkid", USER, "please"), row["body"]
    assert decoded(row)["stage"] == "forwarded"
    o = store.load_org(slug)
    o.audience_forward("boss", "grandkid", USER)
    store.save_org(o)
    u = user_last(slug)
    assert u["body"] == old_aud_user("grandkid", "please"), u["body"]
    uev = decoded(u)
    assert uev["stage"] == "user" and u["body"] == events.render_agent(uev)


check("audience request · initial / forwarded / reached-target / reached-user stages == "
      "old text; access.audience_requested on the (node, target) ref", _audience_requests)


def _audience_changes():
    slug = rig4()
    o = store.load_org(slug)
    # user grants a user audience
    o.audience_grant(USER, "grandkid", USER)
    n = notices(slug if False else slug, "grandkid") or []
    store.save_org(o)
    n = notices(slug, "grandkid")[-1]
    assert n["text"] == ("The user granted you a USER AUDIENCE — you may write to them "
                         "directly until it is rescinded."), n["text"]
    ev = decoded(n)
    assert ev["variant"] == "access.audience_changed" and ev["outcome"] == "user_audience"
    assert ev["by"] == USER and ev["target"] == USER and n["text"] == events.render_agent(ev)
    # boss delegates its user audience to kid: kid's notice + the user's notice
    o = store.load_org(slug)
    o.audience_grant("boss", "kid", USER)
    store.save_org(o)
    n = notices(slug, "kid")[-1]
    assert n["text"] == ('"boss" granted you a direct USER AUDIENCE — you may write to the '
                         'user directly until it is rescinded.'), n["text"]
    u = user_last(slug)
    assert u["body"] == ('"boss" granted "kid" a direct audience to you — it may now write '
                         'to your inbox. Revoke it from the audience panel at will.'), u["body"]
    uev = decoded(u)
    assert uev["outcome"] == "user_audience_seen" and uev["other"] == "kid"
    assert uev["object"]["id"] == USER
    # audience between agents: both sides
    o = store.load_org(slug)
    o.audience_grant("boss", "grandkid", "boss2")
    store.save_org(o)
    n = notices(slug, "grandkid")[-1]
    assert n["text"] == ('"boss" granted you an audience with "boss2" — you may message '
                         'them directly until it is rescinded.'), n["text"]
    assert decoded(n)["outcome"] == "audience_with"
    n2 = notices(slug, "boss2")[-1]
    assert n2["text"] == ('"boss" granted "grandkid" an audience with you — it may now '
                          'message you directly; you may revoke it at will.'), n2["text"]
    ev2 = decoded(n2)
    assert ev2["outcome"] == "audience_from" and ev2["other"] == "grandkid"
    # rescind
    o = store.load_org(slug)
    o.audience_revoke("boss2", "grandkid")
    store.save_org(o)
    n = notices(slug, "grandkid")[-1]
    assert n["text"] == "Your audience with boss2 was rescinded — fall back to the parent chain."
    assert decoded(n)["outcome"] == "rescinded"
    o = store.load_org(slug)
    o.audience_revoke(USER, "kid", USER)
    store.save_org(o)
    n = notices(slug, "kid")[-1]
    assert n["text"] == "Your audience with the user was rescinded — fall back to the parent chain."
    # org inbox: grant, self-release, auto-grant
    o = store.load_org(slug)
    o.audience_grant(USER, "boss", "extern")
    store.save_org(o)
    n = notices(slug, "boss")[-1]
    assert n["text"].startswith("The user granted you audience with the ORG INBOX: you now "
                                "receive outside messages"), n["text"]
    ev = decoded(n)
    assert ev["outcome"] == "org_inbox" and ev["target"] == EXTERN
    assert n["text"] == events.render_agent(ev)
    o = store.load_org(slug)
    o.audience_revoke("boss", "boss")
    store.save_org(o)
    n = notices(slug, "boss")[-1]
    assert n["text"] == ("You gave up your ORG-INBOX audience — outside mail addressed to "
                         "the org no longer reaches you.")
    assert decoded(n)["outcome"] == "org_inbox_released"
    o = store.load_org(slug)
    o.post_external_mail("@org:other", "hello there")
    store.save_org(o)
    holders = [r for r in notices(slug, "boss") + notices(slug, "boss2")
               if "auto-granted" in r["text"]]
    assert holders, "the bootstrap auto-grant notice"
    ev = decoded(holders[-1])
    assert ev["outcome"] == "org_inbox_auto" and ev["actor"] == {"kind": "system", "id": SYSTEM}
    assert holders[-1]["text"] == events.render_agent(ev)


check("audience changes · user audience (direct, delegated + user's copy), agent audience "
      "(both sides), rescind (agent, user), org inbox grant/release/auto == old text",
      _audience_changes)


def _scope_request_and_change():
    slug = rig4()
    o = store.load_org(slug)
    r = o.request_scope("grandkid", [
        {"kind": "dir", "path": "E:/data", "mode": "rw"},
        {"kind": "tool", "tool": "web"},
        {"kind": "mcp", "server": "fs"},
        {"kind": "permission_mode", "mode": "bypassPermissions"}], "  need the dataset  ")
    assert r.get("routed") == "kid", r
    store.save_org(o)
    row = box_last(slug, "kid")
    labels = ["folder E:/data (rw)", "tool: web", "MCP server: fs",
              "permission mode → bypassPermissions ⚠ UNGUARDED — removes every prompt"]
    assert row["body"] == old_scope_req(labels, "  need the dataset  "), row["body"]
    ev = decoded(row)
    assert ev["variant"] == "access.scope_requested" and ev["object"]["id"] == "grandkid"
    assert ev["items"] == labels and ev["reason"] == "need the dataset"
    assert ev["wanted"] == {"folders": [{"path": "E:/data", "mode": "rw"}],
                            "tools": {"bash": None, "web": True, "edit": None,
                                      "subagents": None, "mcp": ["fs"]},
                            "permission_mode": "bypassPermissions", "org_visibility": None}
    assert row["body"] == events.render_agent(ev)
    pub = events.public_event(ev)
    assert "path" not in pub["wanted"]["folders"][0] and "mcp" not in pub["wanted"]["tools"]
    # scope change notices: by the user and by the superior
    o = store.load_org(slug)
    o.set_scope(USER, "kid", tools={"bash": True, "web": True, "edit": False,
                                    "subagents": False, "mcp": []})
    o.set_scope("boss", "kid", charter="new role")
    store.save_org(o)
    rows = notices(slug, "kid")[-2:]
    assert rows[0]["text"] == ("The user changed your configuration (folders, tools, charter, "
                               "or org visibility). Your current scope is stated in your "
                               "system prompt each turn."), rows[0]["text"]
    assert rows[1]["text"] == ('Your superior "boss" changed your configuration (folders, '
                               'tools, charter, or org visibility). Your current scope is '
                               'stated in your system prompt each turn.'), rows[1]["text"]
    e0, e1 = decoded(rows[0]), decoded(rows[1])
    assert e0["changed"] == ["tools"] and e1["changed"] == ["charter"] and e1["by"] == "boss"


check("scope · routed request (4 kinds, reason stripped) == old text with the wanted "
      "scope as data (paths/mcp withheld publicly); scope-changed notices by user/superior",
      _scope_request_and_change)


def _grants_and_kiosk():
    slug = rig4()
    o = store.load_org(slug)
    o.reallocate(USER, "kid", 3)
    store.save_org(o)
    n = notices(slug, "kid")[-1]
    fr = store.load_org(slug).free("kid")
    assert n["text"] == f"The user adjusted your grant by +3 (now 8, free {fr:g}).", n["text"]
    ev = decoded(n)
    assert ev["variant"] == "access.grant_changed" and ev["relation"] == "self"
    assert ev["delta"] == 3.0 and ev["now"] == 8.0 and ev["by"] == USER
    nb = notices(slug, "boss")[-1]
    assert nb["text"] == 'The user adjusted "kid"\'s grant by +3.', nb["text"]
    assert decoded(nb)["relation"] == "report"
    o = store.load_org(slug)
    o.reallocate("boss", "kid", -2)
    store.save_org(o)
    n = notices(slug, "kid")[-1]
    assert n["text"].startswith('"boss" adjusted your grant by -2 (now 6, free '), n["text"]
    assert decoded(n)["by"] == "boss" and n["text"] == events.render_agent(decoded(n))
    # kiosk ceiling minted on load → user notice; clamp → node notice
    o = store.load_org(slug)
    ev = events.mint("access.kiosk_ceiling", {"kind": "system", "id": SYSTEM}, o.org_ref())
    assert events.render_agent(ev).startswith("This kiosk now carries a PERMISSION CEILING")
    ev = events.mint("access.kiosk_clamped", {"kind": "user", "id": USER}, o.node_ref("kid"),
                     lost=["tool bash", "permission_mode bypassPermissions→acceptEdits"])
    assert events.render_agent(ev) == (
        "The kiosk permission ceiling was adjusted; your grants were clamped to fit: "
        "tool bash, permission_mode bypassPermissions→acceptEdits.")


check("grants · self and superior copies of a grant change (user / agent actor) == old "
      "text; kiosk ceiling and clamp renderings == old literals", _grants_and_kiosk)


# ============================================================= §L family lifecycle
print("\n§L · lifecycle — hires, retirements, switches, reorganisation, compaction, policies")


def last_notice(slug, nid):
    return notices(slug, nid)[-1]


def _ev_of(row):
    return decoded(row)


def rig5():
    """boss → kid → grandkid; boss → kid2 (kid's peer); boss2 top-level."""
    slug = rig4()
    o = store.load_org(slug)
    o.hire("boss", "boss", "haiku", 5, "kid2", add_dirs=[],
           tools={"bash": True, "web": False, "edit": False, "subagents": False, "mcp": []},
           org_visibility="team", charter="peer fixture")
    store.save_org(o)
    return slug


def _hire_retire_rehire_dissolve_delete():
    slug = rig5()
    o = store.load_org(slug)
    o.hire("kid", "kid", "haiku", 1, "newbie", add_dirs=[],
           tools={"bash": True, "web": False, "edit": False, "subagents": False, "mcp": []},
           org_visibility="team", charter="first line of the role\nsecond line")
    store.save_org(o)
    # the actor (kid) is the parent: it is NOT told; grandkid (newbie's peer) is
    n = last_notice(slug, "grandkid")
    assert n["text"] == ('"kid" hired "newbie" (haiku) alongside you, under kid. Role: first '
                         'line of the role'), n["text"]
    ev = _ev_of(n)
    assert ev["variant"] == "lifecycle.hired" and ev["relation"] == "peer"
    assert ev["why"] == "first line of the role" and ev["grant"] == 1.0 and ev["parent"] == "kid"
    assert ev["object"]["id"] == "newbie" and n["text"] == events.render_agent(ev)
    # user hires under kid: kid (parent) gets the report copy
    o = store.load_org(slug)
    o.hire(USER, "kid", "haiku", 2, "newbie2", add_dirs=[],
           tools={"bash": True, "web": False, "edit": False, "subagents": False, "mcp": []},
           org_visibility="team", charter="")
    store.save_org(o)
    n = last_notice(slug, "kid")
    assert n["text"] == 'The user hired "newbie2" (haiku, grant 2) under you.', n["text"]
    assert _ev_of(n)["relation"] == "report" and _ev_of(n)["why"] is None
    # retire by self, by the user
    o = store.load_org(slug)
    f1 = o.retire("newbie", "newbie")["freed"]
    f2 = o.retire(USER, "newbie2")["freed"]
    store.save_org(o)
    rows = notices(slug, "kid")[-2:]
    assert rows[0]["text"] == f'Your report "newbie" was retired by itself (self-retirement) (freed {f1:g} credits).', rows[0]["text"]
    assert rows[1]["text"] == f'Your report "newbie2" was retired by the user (freed {f2:g} credits).', rows[1]["text"]
    assert _ev_of(rows[0])["by"] == "newbie" and _ev_of(rows[1])["freed"] == float(f2)
    n = last_notice(slug, "grandkid")
    assert n["text"] == 'Your peer "newbie2" was retired by the user.', n["text"]
    # rehire: parent, peer and self copies
    o = store.load_org(slug)
    o.rehire("kid", "newbie")
    store.save_org(o)
    n = last_notice(slug, "newbie")
    assert n["text"] == '"kid" rehired you. You are live again; your prior context is intact.', n["text"]
    ev = _ev_of(n)
    assert ev["variant"] == "lifecycle.rehired" and ev["relation"] == "self"
    n = last_notice(slug, "grandkid")
    assert n["text"] == 'Your peer "newbie" was rehired by "kid".', n["text"]
    o = store.load_org(slug)
    o.rehire(USER, "newbie2")
    store.save_org(o)
    n = last_notice(slug, "kid")
    assert n["text"] == f'Your report "newbie2" was rehired by the user (grant {o.node("newbie2")["grant"]}).', n["text"]
    assert _ev_of(n)["grant"] == float(o.node("newbie2")["grant"])
    # dissolve kid's subtree by the user: boss (report copy), kid2 (peer copy)
    o = store.load_org(slug)
    r = o.dissolve(USER, "kid")
    store.save_org(o)
    n = last_notice(slug, "boss")
    assert n["text"] == (f'The user dissolved your report "kid" and its whole suborganization '
                         f'({len(r["nodes"])} node(s), freed {r["freed"]:g} credits).'), n["text"]
    ev = _ev_of(n)
    assert ev["variant"] == "lifecycle.dissolved" and ev["nodes"] == len(r["nodes"])
    n = last_notice(slug, "kid2")
    assert n["text"] == 'Your peer "kid" and its suborganization were dissolved by the user.'
    # delete kid2 (no subtree) and boss2's subtree-less peer text
    o = store.load_org(slug)
    o.delete(USER, "kid2")
    store.save_org(o)
    n = last_notice(slug, "boss")
    assert n["text"] == ('The user permanently DELETED your report "kid2". Its records are '
                         'gone from the org.'), n["text"]
    assert _ev_of(n)["extra"] == 0 and _ev_of(n)["variant"] == "lifecycle.deleted"


check("hire (agent/user, role gist), retire (self/user), rehire (self/peer/report), "
      "dissolve, delete == old text", _hire_retire_rehire_dissolve_delete)


def _switch_and_reorg():
    slug = rig5()
    o = store.load_org(slug)
    o.switch_model("boss", "kid", "sonnet")
    store.save_org(o)
    n = last_notice(slug, "kid")
    seat_h, seat_s = o.d["tiers"]["haiku"], o.d["tiers"]["sonnet"]
    assert n["text"] == (f'"boss" switched your model haiku→sonnet (seat {seat_h:g}→{seat_s:g}). '
                         f'Your context is intact — carry on.'), n["text"]
    ev = _ev_of(n)
    assert ev["variant"] == "lifecycle.model_switched" and ev["crossed"] is False
    assert ev["queued"] is False and ev["predecessor"] is None and ev["relation"] == "self"
    assert n["text"] == events.render_agent(ev)
    # the user switching: boss (parent) gets the report copy
    o = store.load_org(slug)
    o.switch_model(USER, "kid", "haiku")
    store.save_org(o)
    n = last_notice(slug, "boss")
    assert n["text"] == 'The user switched "kid" sonnet→haiku.', n["text"]
    assert _ev_of(n)["relation"] == "report"
    # queued switch on a busy node, then cancelled
    o = store.load_org(slug)
    o.switch_model(USER, "kid", "sonnet", busy=True)
    store.save_org(o)
    n = last_notice(slug, "boss")
    assert n["text"] == ('The user queued a model switch for "kid": haiku→sonnet, applied '
                         'when its current turn ends.'), n["text"]
    assert _ev_of(n)["variant"] == "lifecycle.switch_queued"
    o = store.load_org(slug)
    o.switch_model(USER, "kid", "haiku", busy=True)     # back to the current = cancel
    store.save_org(o)
    n = last_notice(slug, "boss")
    assert n["text"] == 'The user cancelled the queued switch of "kid" to sonnet.', n["text"]
    assert _ev_of(n)["variant"] == "lifecycle.switch_cancelled"
    # rename
    o = store.load_org(slug)
    o.rename(USER, "grandkid", "gk")
    store.save_org(o)
    n = last_notice(slug, "gk")
    assert n["text"] == ("You have been renamed: grandkid → gk (by the user). Sign and refer "
                         "to yourself as 'gk' from now on."), n["text"]
    assert _ev_of(n)["variant"] == "lifecycle.renamed" and _ev_of(n)["old"] == "grandkid"
    # move gk from kid to kid2 (user): old parent, new parent, self
    o = store.load_org(slug)
    o.move(USER, "gk", "kid2")
    store.save_org(o)
    assert last_notice(slug, "kid")["text"] == \
        'The user moved your report "gk" away — it now reports to kid2.'
    assert last_notice(slug, "kid2")["text"] == \
        'The user moved "gk" (from kid) to report to you.'
    n = last_notice(slug, "gk")
    assert n["text"] == ("The user moved you: you now report to kid2 (you were under kid). "
                         "Your entire suborganization moved with you."), n["text"]
    ev = _ev_of(n)
    assert ev["variant"] == "lifecycle.moved" and ev["role"] == "self"
    assert ev["from_parent"] == "kid" and ev["to_parent"] == "kid2" and ev["tail"] is None
    # insert kid2's report gk above kid2? no — insert kid (a report of boss) above kid2:
    # insert_parent(nid, target): nid must be a direct report of target. Use gk above...
    # gk is kid2's direct report: insert gk above kid2.
    o = store.load_org(slug)
    r = o.insert_parent(USER, "gk", "kid2")
    store.save_org(o)
    n = last_notice(slug, "boss")
    assert n["text"] == ('The user inserted "gk" above your report "kid2": "gk" now holds that '
                         'position and "kid2" reports to it, keeping its own team.'), n["text"]
    assert _ev_of(n)["role"] == "parent" and _ev_of(n)["variant"] == "lifecycle.inserted"
    n = last_notice(slug, "kid")
    assert n["text"] == '"gk" joined your team (inserted by the user above "kid2", which now reports to it).'
    n = last_notice(slug, "kid2")
    assert n["text"] == (f'The user inserted "gk" directly above you: you now report to "gk" '
                         f'instead of boss, and your entire team, scope and remaining grant '
                         f'({r["target_grant"]}) came with you.'), n["text"]
    n = last_notice(slug, "gk")
    assert n["text"].startswith('The user placed you in "kid2"\'s position: you report to boss, '
                                '"kid2" and its whole team now report to YOU, and you hold that '
                                f"seat's scope with a grant of {r['grant']} (of which "), n["text"]
    assert _ev_of(n)["role"] == "self" and n["text"] == events.render_agent(_ev_of(n))
    # seat swap (non-nested): kid (under boss) ↔ kid2 (under gk)
    o = store.load_org(slug)
    o.swap_seats(USER, "kid", "kid2")
    store.save_org(o)
    n = last_notice(slug, "kid")
    ev = _ev_of(n)
    assert ev["variant"] == "lifecycle.seat_swapped" and ev["role"] == "a" and ev["nested"] is False
    assert n["text"].startswith('The user swapped your seat with "kid2": you now report to "gk" and hold '
                                "that seat's team, grant ("), n["text"]
    n = last_notice(slug, "kid2")
    assert n["text"].startswith('The user seated you in "kid"\'s place: you now report to the top level'
                                if False else 'The user seated you in "kid"\'s place: you now report to "boss"'), n["text"]
    assert _ev_of(n)["role"] == "b" and n["text"] == events.render_agent(_ev_of(n))
    n = last_notice(slug, "gk")
    assert n["text"] == 'The user seated "kid" in "kid2"\'s place — "kid" now reports to you.', n["text"]
    assert _ev_of(n)["role"] == "parent_of_b"


check("model switch (self/report/queued/cancelled), rename, move, insert, seat swap == "
      "old text with per-audience roles", _switch_and_reorg)


def _lineage_and_policy():
    slug = rig5()
    o = store.load_org(slug)
    pred = o.compact_split("kid", "sess-2")
    store.save_org(o)
    n = last_notice(slug, "boss")
    assert n["text"] == (f'"kid" compacted (now generation 1). Its pre-compaction self is '
                         f'archived as "{pred}" — rehire it to consult the full detail the '
                         f'summary flattened.'), n["text"]
    ev = _ev_of(n)
    assert ev["variant"] == "lifecycle.compacted" and ev["auto"] is False and ev["lost"] is False
    n = last_notice(slug, "kid")
    assert n["text"].startswith("You were compacted: you are now generation 1, and the context")
    assert _ev_of(n)["relation"] == "self" and n["text"] == events.render_agent(_ev_of(n))
    # CLI compaction, bearer preserved (with size) and lost
    o = store.load_org(slug)
    pred2 = o.record_cli_compaction("kid", pre_tokens=123456, bearer_sid="sess-2")
    store.save_org(o)
    n = last_notice(slug, "boss")
    assert n["text"] == (f'"kid" was auto-compacted BY THE CLI (now generation 2; ~123k tokens '
                         f'summarized). Its pre-compaction self is preserved as "{pred2}" — '
                         f'rehire it to consult the full detail the summary flattened.'), n["text"]
    ev = _ev_of(n)
    assert ev["auto"] is True and ev["lost"] is False and ev["size_note"] == "; ~123k tokens summarized"
    o = store.load_org(slug)
    pred3 = o.record_cli_compaction("kid")
    store.save_org(o)
    n = last_notice(slug, "kid")
    assert n["text"] == (f'You were auto-compacted by the CLI: you are now generation 3 and the '
                         f'context you had before it survives only as your summary. There is NO '
                         f'consultable bearer in this case — "{pred3}" is a LOST generation and '
                         f'cannot be rehired, so anything the summary dropped is gone. Ask whoever '
                         f'gave you the work rather than hunting for a past self.'), n["text"]
    assert _ev_of(n)["lost"] is True
    # recover the lost generation
    o = store.load_org(slug)
    o.recover_lost_generation(pred3, "sess-x")
    store.save_org(o)
    n = last_notice(slug, "kid")
    assert n["text"] == (f'"{pred3}" is RECOVERED — the generation recorded as lost was never '
                         f'actually gone, and it is now a consultable knowledge bearer. Rehire '
                         f'it to reach the context that compaction summarized away.'), n["text"]
    assert _ev_of(n)["variant"] == "lifecycle.recovered"
    # cheap compact (self + parent), reseed
    o = store.load_org(slug)
    r = o.cheap_compact(USER, "kid")
    store.save_org(o)
    n = last_notice(slug, "kid")
    ev = _ev_of(n)
    assert ev["variant"] == "lifecycle.cheap_compacted" and ev["relation"] == "self"
    assert n["text"].startswith("You were CHEAP-COMPACTED: your seat, scope, team and budget are")
    assert n["text"].endswith('read the transcript before directing them.'), n["text"][-80:]
    assert "Your team (grandkid) is UNCHANGED" in n["text"]
    n = last_notice(slug, "boss")
    assert n["text"] == (f'Your report "kid" was cheap-compacted by the user: same seat and '
                         f'team, fresh session — its prior self is consultable as '
                         f'"{ev["predecessor"]}".'), n["text"]
    o = store.load_org(slug)
    o.mark_unrecoverable("grandkid", "session file missing")
    store.save_org(o)
    n = last_notice(slug, "kid")
    assert n["text"] == ('⚠ Your report "grandkid" is UNRECOVERABLE — its session failed to '
                         'resume (session file missing). Its seat is still held; rehire it to '
                         'RE-SEED it (fresh session, same identity and credits), or retire it '
                         'to free the credits.'), n["text"]
    o = store.load_org(slug)
    r = o.reseed("kid", "grandkid", "sess-new")
    store.save_org(o)
    n = last_notice(slug, "grandkid")
    assert n["text"] == ('"kid" re-seeded you after your previous session was lost. Your role, '
                         'charter, credits and reports are intact, but your memory starts fresh '
                         '— check your scratch CLAUDE.md and ask your chain to re-orient you.'), n["text"]
    assert _ev_of(n)["variant"] == "lifecycle.reseeded" and _ev_of(n)["relation"] == "self"
    # fable policy: halt (default) — parent + user copies; then the release by the user
    o = store.load_org(slug)
    o.node("kid2")["model"] = "fable"
    store.save_org(o)
    o = store.load_org(slug)
    assert o.fable_filter_hit("kid2", "d" * 300) == "halt"
    store.save_org(o)
    n = last_notice(slug, "boss")
    assert n["text"] == ('Your report "kid2" had a message FLAGGED by Fable\'s content filters — '
                         'its turn HALTED (org policy). Re-task it, or the user may switch the '
                         'org filter policy to auto-convert to opus.'), n["text"]
    assert _ev_of(n)["variant"] == "policy.fable_flagged" and _ev_of(n)["outcome"] == "halted"
    u = user_last(slug)
    assert u["body"] == (f'A Fable content filter flagged a message from "kid2" (org policy '
                         f'applied: halt). Detail: {"d" * 200}'), u["body"]
    assert decoded(u)["audience"] == "user" and u["kind"] == "decision"
    o = store.load_org(slug)
    o.fable_limit_hit("kid2", "weekly limit")
    store.save_org(o)
    assert last_notice(slug, "kid2")["text"] == ("Weekly Fable usage limit exhausted: you are "
                                                 "halted. Your reports remain active.")
    assert last_notice(slug, "boss")["text"] == (
        'Your report "kid2" has HALTED: weekly Fable usage limit exhausted. It holds its '
        'seat and will not run until the limit resets or the user intervenes — decide how '
        'to cover its work.')
    assert last_notice(slug, "kid")["text"] == 'Your peer "kid2" has halted (weekly Fable limit).'
    u = user_last(slug)
    assert u["body"] == ("Weekly Fable usage limit exhausted (detected at kid2; policy: halt). "
                         "Halted: ['kid2']. Dissolved (whole subtrees): none. Switched to opus: "
                         "none. Rehiring a fable yourself, or clearing the lock in settings, "
                         "lifts the freeze."), u["body"]
    uev = decoded(u)
    assert uev["variant"] == "policy.weekly_limit" and uev["relation"] == "user"
    assert uev["halted"] == "['kid2']" and uev["policy"] == "halt"
    o = store.load_org(slug)
    o.clear_fable_lock()
    store.save_org(o)
    assert last_notice(slug, "kid2")["text"] == ("The Fable lock was cleared by the user: you are "
                                                 "no longer halted. Carry on.")
    n = last_notice(slug, "kid")
    assert n["text"] == ('"kid2" is RELEASED from the weekly-Fable halt (the user cleared it). '
                         'It runs again; no need to keep covering its work.'), n["text"]
    assert _ev_of(n)["variant"] == "policy.unlocked" and _ev_of(n)["relation"] == "peer"
    o = store.load_org(slug)
    o.node("kid2")["limit_locked"] = True
    o.unstick(USER, "kid2")
    store.save_org(o)
    n = last_notice(slug, "kid2")
    assert n["text"] == ("The user manually UNSTUCK you (override) — any limit that held you "
                         "is released; continue.")
    assert _ev_of(n)["variant"] == "policy.unstuck"


check("compaction (split, CLI preserved/lost, recovered), cheap compact, unrecoverable, "
      "reseed, fable filter halt, weekly limit halt, unlock, unstick == old text",
      _lineage_and_policy)


def _kickoff_and_renders():
    slug = rig5()
    # the seat composite's kickoff step, driven directly (the /api/agent hire
    # route refuses without a signed-in Claude on this machine)
    o = store.load_org(slug)
    o.hire("boss", "boss", "haiku", 1, "hired1", add_dirs=[],
           tools={"bash": True, "web": False, "edit": False, "subagents": False, "mcp": []},
           org_visibility="team", charter="do x")
    nid = "hired1"
    res: dict = {}
    api._seat_finish(o, slug, "boss", nid, {"kickoff": "Start by reading the docket."}, res, [])
    store.save_org(o)
    row = box_last(slug, nid)
    assert row["body"] == "Start by reading the docket." and row["kind"] == "request"
    ev = decoded(row)
    assert ev["variant"] == "lifecycle.kickoff" and ev["reason"] == "hire"
    assert ev["hired_by"] == "boss" and ev["tier"] == "haiku" and ev["grant"] == 1.0
    assert ev["object"]["id"] == nid
    # rehire with a kickoff → reason rehire
    o = store.load_org(slug)
    o.retire("boss", nid)
    o.rehire("boss", nid)
    res = {}
    api._seat_finish(o, slug, "boss", nid, {"kickoff": "Welcome back."}, res, [],
                     fields=api._SEAT_SCOPE_REHIRE)
    store.save_org(o)
    row = box_last(slug, nid)
    assert row["body"] == "Welcome back." and decoded(row)["reason"] == "rehire"
    # render-only parity for the engine-side leaves (their producers need a live rig)
    o = store.load_org(slug)
    ev = events.mint("lifecycle.handoff_record", {"kind": "system", "id": SYSTEM},
                     o.node_ref("kid"), generation=3)
    assert events.render_agent(ev) == (
        "A handoff record for this boundary is at handoff-g3/record.md "
        "in your working folder: a citation index of instructions, tool "
        "calls, artifacts and mail built from files, with line refs into "
        "transcript.jsonl — not memory, and not evidence that any "
        "provider context carried over.")
    ev = events.mint("lifecycle.bearer_exhausted", {"kind": "system", "id": SYSTEM},
                     o.node_ref("kid"), bearer="kid@0")
    assert events.render_agent(ev) == (
        'Knowledge bearer "kid@0" has exhausted its headroom and is '
        'now a PRESERVING ORACLE — it still answers, but exchanges '
        'are no longer retained by it.')
    ev = events.mint("lifecycle.disk_migrated", {"kind": "system", "id": SYSTEM}, o.org_ref(),
                     floored_from="512")
    assert events.render_agent(ev) == (
        "Storage migration: this org's 512 MB "
        "limit was raised to the 4096 MB one-disk minimum "
        "(system seed + transcripts now count inside the "
        "cap). Its agents may consume up to 4 GB; the disk "
        "can be grown online or shrunk (staged) from the "
        "storage browser.")
    ev = events.mint("lifecycle.switch_dropped", {"kind": "user", "id": USER}, o.node_ref("kid"),
                     node="kid", target="opus", kept="haiku", reason="no seat")
    assert events.render_agent(ev) == (
        "the queued switch of kid to opus was DROPPED at the end of its turn: no seat. "
        "It stays on haiku; ask again once that is resolved.")
    ev = o._limit_reset_ev("kid", "user", ["a", "kid"])
    assert events.render_agent(ev) == ("Weekly Fable limit reset — halted fable agent(s) "
                                       "released: a, kid. Their superiors were told to stop covering.")
    # external mail with no live top-level: the user's inbox notice
    o = store.load_org(slug)
    for t in ("boss", "boss2"):
        pass
    o2 = Org.create(f"{slug}-empty", dirs=[_TMP])
    o2.hire(USER, None, "opus", 20, "solo")
    o2.retire(USER, "solo")
    o2.post_external_mail("@org:peer", "hello\nworld")
    u = (o2.d.get("user_mail_log") or []) + (o2.d.get("user_inbox") or [])
    u = dict(u[-1])
    assert u["body"] == ("Outside party @org:peer messaged this org, but no top-level agents "
                         "are live to receive it:\n\nhello\nworld"), u["body"]
    assert decoded(u)["variant"] == "runtime.external_unroutable"


check("kickoff via hire/rehire (body verbatim, reason), engine-side lifecycle renders == "
      "old literals, unroutable outside mail", _kickoff_and_renders)


# ====================================================== §C family context_change
print("\n§C · context — the turn's machine-state segments")

from orgtree import turnusage                                    # noqa: E402


def _state_segments():
    import time as _time
    slug = rig5()
    o = store.load_org(slug)
    facts: dict = {}
    state_text = S._envelope_state_block(o, "boss", _time.time(), {}, out=facts)
    usage_text = S.turn_usage_block(o, "boss", pending={})
    assert state_text.startswith("[ORG STATE") and "[PROVIDER USAGE" in usage_text
    segs = S._state_segments(o, "boss", state_text, facts, usage_text)
    assert [x["kind"] for x in segs] == ["state", "state"]
    st = events.decode_ev(segs[0]["event"])
    assert st["variant"] == "context.org_state" and segs[0]["text"] == state_text
    assert st["text"] == state_text, "the FULL block rides the segment, untouched"
    snap = st["snapshot"]
    assert {r["id"] for r in snap["reports"]} == {"kid", "kid2"}, snap["reports"]
    assert snap["reports"][0]["tier"] == "haiku" and snap["reports"][0]["state"] == "live"
    assert snap["peers"] == ["boss2"] and snap["credits"]["grant"] == 20.0
    assert (snap["chart"] is None) != (snap["chart_ref"] is None) or facts.get("seq") is None, \
        "D-223: exactly one of chart / chart_ref when a snapshot was recorded"
    pu = events.decode_ev(segs[1]["event"])
    assert pu["variant"] == "context.provider_usage" and pu["text"] == usage_text
    rows = pu["rows"]
    assert rows, "the board's rows are recorded at render (positive control below)"
    # identity, not parsing: every recorded row's provider/lane prefix opens a line
    # of the SAME text; and the recorder is per-thread, cleared per board
    for r in rows:
        assert any(ln.startswith(f"{r['provider']}/{r['lane']}") for ln in usage_text.splitlines()), r
        assert r["state"] and r["window"]
    assert turnusage.board_rows("nothing rendered here") == []
    # dispositions: both are machine-only → hidden from the human card; the agent
    # text is intact; the public projection carries structure only
    for v in ("context.org_state", "context.provider_usage"):
        assert v in events.human_hidden_variants()
    pub = events.public_event(st)
    assert "text" not in pub and "snapshot" not in pub and pub["variant"] == "context.org_state"
    assert events.render_agent(st) == state_text and events.render_agent(pu) == usage_text
    # the wire keeps them in place, both projections (mixed with a readable row)
    stored = segs + [{"kind": "mail", "rows": [{"id": "m", "from": "@user", "kind": "message",
                                                "body": "hi", "at": "t"}]}]
    for public in (False, True):
        out = events.wire_segments(stored, public=public)
        assert [x["kind"] for x in out] == ["state", "state", "mail"]
        key = "event_public" if public else "event"
        assert out[0][key]["variant"] == "context.org_state"


check("state segments · org_state (full text + roster/credit snapshot) and "
      "provider_usage (full text + rows recorded at render, never parsed) — "
      "machine-only, kept in place in both projections", _state_segments)


# ======================================================= §4 step 4 — reply routes
print("\n§4 · step 4 — qualified reply targets and the receipt on every send")

_pub = TestClient(api.PublicGateway(api.app))
_KTOK = "tok_" + "q" * 24


def _kiosk(slug):
    o = store.load_org(slug)
    o.d["kiosk"] = {"enabled": True, "token": _KTOK}
    store.save_org(o)
    api._token_cache["at"] = 0.0


def _post(slug, nid, body, *, public=False):
    with Quiet():
        if public:
            return _pub.post(f"/k/{_KTOK}/api/orgs/{slug}/nodes/{nid}/message", json=body)
        return _client.post(f"/api/orgs/{slug}/nodes/{nid}/message", json=body)


def _receipt_plain_and_legacy():
    slug = rig2()
    r = _post(slug, "boss", {"text": "plain hello"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["id"] and j["ref"] == f"@mail:{slug}/node/boss/{j['id']}", j
    assert j["ev"]["variant"] == "ordinary.message" and j["ev"]["body"] == "plain hello"
    assert "ev_public" not in j
    row = box_last(slug, "boss")
    assert row["id"] == j["id"] and decoded(row) == j["ev"], "receipt == the stored row's event"
    # legacy reply_to: still an ordinary message with the sanitised quote, receipt too
    r = _post(slug, "boss", {"text": "re", "reply_to": {"id": "x1", "from": "boss", "at": "t",
                                                        "gist": "old  words\nhere"}})
    assert r.status_code == 200, r.text
    j = r.json()
    row = box_last(slug, "boss")
    assert row["reply_to"]["gist"] == "old words here" and j["ev"]["variant"] == "ordinary.message"
    # both target and reply_to → refused, nothing written
    before = len(store.load_org(slug).d["mail"]["boss"])
    r = _post(slug, "boss", {"text": "x", "reply_to": {"gist": "g"},
                             "target": {"kind": "document", "org": slug, "id": "d"}})
    assert r.status_code == 422 and "one of" in r.text, r.text
    assert len(store.load_org(slug).d["mail"]["boss"]) == before
    # a kiosk visitor gets ev_public, never ev
    _kiosk(slug)
    r = _post(slug, "boss", {"text": "from the kiosk"}, public=True)
    assert r.status_code == 200, r.text
    j = r.json()
    assert "ev" not in j and j["ev_public"]["projection"] == "public"
    assert j["ev_public"]["variant"] == "ordinary.message" and j["id"]


check("receipt · plain and legacy-reply sends return {id, ref, ev}; target+reply_to "
      "refused; a visitor gets ev_public only", _receipt_plain_and_legacy)


def _target_mail_and_document():
    slug = rig2()
    o = store.load_org(slug)
    m = o.post_mail("boss", USER, "a question for you\nsecond line")
    d = o.present_document("boss", "The Plan", "# plan\nbody")
    d = {"id": d["presented"]}
    store.save_org(o)
    # reply to the user-inbox mail: server-fetched quote, legacy reply_to beside it
    r = _post(slug, "boss", {"text": "my answer",
                             "target": {"kind": "mail", "org": slug, "box": "user", "id": m["id"]}})
    assert r.status_code == 200, r.text
    j = r.json()
    ev = j["ev"]
    assert ev["variant"] == "reply.mail" and ev["body"] == "my answer"
    assert ev["object"] == {"kind": "mail", "org": slug, "box": "user", "node": None,
                            "id": m["id"], "sender": "boss", "at": ev["object"]["at"]}
    assert ev["quote"] == {"from": "boss", "at": ev["object"]["at"],
                           "gist": "a question for you second line"}
    row = box_last(slug, "boss")
    assert row["body"] == "my answer" and row["reply_to"]["gist"] == "a question for you second line"
    assert decoded(row) == ev
    # document reply
    r = _post(slug, "boss", {"text": "looks good",
                             "target": {"kind": "document", "org": slug, "id": d["id"]}})
    assert r.status_code == 200, r.text
    ev = r.json()["ev"]
    assert ev["variant"] == "reply.document" and ev["object"] == {
        "kind": "document", "org": slug, "id": d["id"], "title": "The Plan", "node": "boss"}
    assert box_last(slug, "boss")["body"] == "looks good"
    # refusals: wrong org, missing object, wrong recipient, client title — nothing written
    before = len(store.load_org(slug).d["mail"]["boss"])
    for tgt, frag in (
            ({"kind": "document", "org": "other", "id": d["id"]}, "target.org"),
            ({"kind": "document", "org": slug, "id": "nope"}, "no presented document"),
            ({"kind": "mail", "org": slug, "box": "user", "id": "nope"}, "no mail"),
            ({"kind": "document", "org": slug, "id": d["id"], "title": "spoof"}, "extra_field"),
            ({"kind": "mail", "org": slug, "box": "node", "id": m["id"]}, "missing_field")):
        r = _post(slug, "boss", {"text": "x", "target": tgt})
        assert r.status_code == 422 and frag in r.text, (tgt, r.status_code, r.text)
    r = _post(slug, "kid", {"text": "x", "target": {"kind": "document", "org": slug, "id": d["id"]}})
    assert r.status_code == 422 and "presented by" in r.text, r.text
    assert len(store.load_org(slug).d["mail"]["boss"]) == before
    # node-box mail: the reader of the box is a valid recipient
    o = store.load_org(slug)
    m2 = o.post_mail(USER, "kid", "for kid")
    store.save_org(o)
    r = _post(slug, "kid", {"text": "reply to my own inbox row",
                            "target": {"kind": "mail", "org": slug, "box": "node", "node": "kid",
                                       "id": m2["id"]}})
    assert r.status_code == 200, r.text
    assert r.json()["ev"]["object"]["node"] == "kid"


check("target · mail (user box, node box) and document replies mint reply.mail/"
      "reply.document with server-fetched refs and quote; six refusals write nothing",
      _target_mail_and_document)


def _target_docket_and_route():
    slug = rig3()
    o = store.load_org(slug)
    wid = o.work_create("kid", "Reply target item", "needs a reply; make one", owner="kid")["created"]
    o.work_participants("kid", wid, add=["kid2"])
    store.save_org(o)
    # via the node message route with a work_item target: owner and participant
    r = _post(slug, "kid", {"text": "owner text",
                            "target": {"kind": "work_item", "org": slug, "slug": wid}})
    assert r.status_code == 200, r.text
    ev = r.json()["ev"]
    assert ev["variant"] == "reply.docket" and ev["role"] == "owner" and ev["owner"] is None
    row = box_last(slug, "kid")
    assert row["body"] == (f'[DOCKET REPLY · {wid} "Reply target item"] (the user replied on this '
                           f'docket item — treat it as item-linked mail and update the item if '
                           f'it changes the work)\nowner text'), row["body"]
    assert decoded(row)["body"] == "owner text", "the event carries the user's text alone"
    r = _post(slug, "kid2", {"text": "participant text",
                             "target": {"kind": "work_item", "org": slug, "slug": wid}})
    assert r.status_code == 200, r.text
    ev = r.json()["ev"]
    assert ev["role"] == "participant" and ev["owner"] == "kid"
    r = _post(slug, "boss", {"text": "x", "target": {"kind": "work_item", "org": slug, "slug": wid}})
    assert r.status_code == 422 and "neither the owner" in r.text, r.text
    # the docket reply route: shape unchanged, mints reply.docket, receipt added
    with Quiet():
        r = _client.post(f"/api/orgs/{slug}/work-items/{wid}/reply",
                         json={"body": "via the docket route", "to": "kid2"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["role"] == "participant" and j["to"] == "kid2" and j["id"] and j["ref"]
    assert j["ev"]["variant"] == "reply.docket" and j["ev"]["role"] == "participant"
    row = box_last(slug, "kid2")
    assert row["id"] == j["id"]
    assert row["body"] == (f'[DOCKET REPLY · {wid} "Reply target item"] (the user replied on this '
                           f'docket item ADDRESSED TO YOU AS A PARTICIPANT — the item is owned by '
                           f'kid, not by you; treat this as item-linked mail, act on it, and '
                           f'coordinate any update with the owner)\nvia the docket route'), row["body"]
    assert decoded(row) == j["ev"]


check("target · work_item replies (owner / participant / refused) and the docket reply "
      "route mint reply.docket == old header text; receipt == stored row",
      _target_docket_and_route)


# =========================================================================== summary
print()
for label, tb in FAIL:
    print(f"── FAIL {label}\n{tb}")
print("══════════════════════════════════════════════════════════════════════")
print(f"{PASS} checks passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
