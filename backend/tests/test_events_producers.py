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


# =========================================================================== summary
print()
for label, tb in FAIL:
    print(f"── FAIL {label}\n{tb}")
print("══════════════════════════════════════════════════════════════════════")
print(f"{PASS} checks passed, {len(FAIL)} failed")
sys.exit(1 if FAIL else 0)
