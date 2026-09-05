"""Idle-agent reminders for unfinished docket items.

Run: python backend/tests/test_idle_docket_reminder.py

The clock is supplied to every scheduler pass and the wake is a fake, so no
sleep, provider process or real turn is involved. What is pinned here: the
default-off toggle, the 20-minute idle threshold measured from real activity
boundaries, every exclusion the user named (backlogged, done/archived,
attention flag, open attached question, other people's items) WITH a positive
control beside it, the mixed set, durable rate limiting across repeated ticks
and a restart, and the no-double-wake rule when the working checkup is on too.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import tempfile
from typing import Any

ROOT = tempfile.mkdtemp(prefix="orgtree-docket-reminder-")
os.environ["ORGTREE_DATA"] = ROOT
os.environ["HOME"] = os.path.join(ROOT, "home")
os.environ["USERPROFILE"] = os.path.join(ROOT, "home")
os.environ["ORGTREE_PORT"] = "7423"
os.makedirs(os.environ["HOME"], exist_ok=True)
with open(os.path.join(ROOT, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from orgtree import appsettings, store, supervisor as S      # noqa: E402
from orgtree.ledger import SYSTEM, USER                      # noqa: E402

PASS = FAIL = 0
BASE = 1_800_000_000.0
#: exactly twenty minutes of quiet — the user asked for MORE than that, so
#: this instant is still silent and BOUND + anything fires
BOUND = BASE + S.IDLE_DOCKET_REMINDER_AFTER_S
DUE = BOUND + 1


def check(label, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")
    except Exception as e:  # noqa: BLE001
        FAIL += 1
        import traceback
        print(f"  FAIL   {label}: {e}")
        traceback.print_exc(limit=6)


def iso(ts: float) -> str:
    return (dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc)
            .isoformat().replace("+00:00", "Z"))


def fixture(name: str, activity: float = BASE, status: str = "idle",
            peers: tuple[str, ...] = ()):
    """A live top-level agent that last did something at `activity`."""
    org = store.create_org(name)
    org.hire(USER, None, "haiku", 0, "agent")
    for p in peers:
        org.hire(USER, None, "haiku", 0, p)
    n = org.node("agent")
    n["last_status"] = {"status": status, "summary": "parked", "at": iso(activity)}
    n["turns"] = [{"at": iso(activity), "cost": 0.0, "ms": 1, "denials": 0}]
    store.save_org(org)
    return org.d["slug"], "agent"


def add_item(slug: str, title: str, *, owner: str | None = "agent",
             status: str = "open",
             participants: list[str] | None = None,
             reviewer: str | None = None) -> str:
    """One item. `blocked` and `waiting` carry the state information the ledger
    now requires; `reviewer` is planted directly because the field is
    codex-sandbox's and is not written by any verb here yet."""
    with store.DOC_LOCK:
        org = store.load_org(slug)
        r = org.work_create(USER, title,
                            objective="the problem; then the proposal",
                            owner=owner, status=status,
                            participants=participants,
                            blocked_reason=("the vendor has not answered; "
                                            "their support can unblock it"
                                            if status == "blocked" else None),
                            waiting_reason=("the nightly build finishes; the "
                                            "build watchdog mails me"
                                            if status == "waiting" else None))
        if reviewer is not None:
            it, _ = org._work_find(str(r["slug"]))
            it["reviewer"] = {"node": reviewer,
                              "generation": int(org.node(reviewer)
                                                .get("generation") or 0)}
        # ⚠ SETUP MAIL IS DROPPED, and it has to be. Since 2026-09-05 creating
        # an item FOR another agent NOTIFIES it, and that notification is
        # ordinary waking mail — which `_auto_wake_gates_clear` treats as "a
        # wake is already coming", so every reminder below would be suppressed
        # by the fixture's own hand-off. What this suite is about is a seat
        # that is IDLE with nothing pending and still owes an update, so the
        # fixture puts it in exactly that state.
        if owner:
            box = org.d.get("mail", {})
            box[owner] = [m for m in box.get(owner) or []
                          if not str(m.get("body") or "").startswith(
                              "[DOCKET ASSIGNMENT")]
        store.save_org(org)
    return str(r["slug"])


def ledger_do(slug: str, fn):
    with store.DOC_LOCK:
        org = store.load_org(slug)
        out = fn(org)
        store.save_org(org)
    return out


def runtime_clear(slug: str, nid: str) -> None:
    with S._state_lock:
        S._state.pop((slug, nid), None)


def park(slug: str, nid: str = "agent") -> None:
    with store.DOC_LOCK:
        org = store.load_org(slug)
        if nid in org.nodes:
            org.d.get("mail", {}).pop(nid, None)
            store.save_org(org)
    runtime_clear(slug, nid)


def accepted(calls: list[tuple[str, str, str]]):
    def wake(slug: str, nid: str, text: str) -> dict[str, Any]:
        calls.append((slug, nid, text))
        return {"accepted": True, "queued": 0}
    return wake


def reminders(slug: str, nid: str = "agent") -> list[dict[str, Any]]:
    return [m for m in (store.load_org(slug).d.get("mail") or {}).get(nid) or []
            if m.get("from") == SYSTEM
            and str(m.get("body") or "").startswith(S.IDLE_DOCKET_REMINDER_MARK)]


def mine(seen: list[tuple[str, str, str]], slug: str,
         nid: str = "agent") -> list[tuple[str, str, str]]:
    return [c for c in seen if c[0] == slug and c[1] == nid]


def fire(slug: str, now: float, calls: list[tuple[str, str, str]] | None = None,
         *, enabled: bool | None = True,
         nid: str = "agent") -> list[tuple[str, str, str]]:
    """One sweep with the switch forced on unless told otherwise, accumulating
    only the wakes for THIS seat: the pass is a FLEET sweep and also sees every
    org an earlier check left behind."""
    calls = [] if calls is None else calls
    seen: list[tuple[str, str, str]] = []
    S._idle_docket_reminder_pass(accepted(seen), now, mode_enabled=enabled)
    calls.extend(mine(seen, slug, nid))
    return calls


print("\n§1  the threshold and the message")


def threshold_and_internal_mail() -> None:
    assert S.IDLE_DOCKET_REMINDER_AFTER_S == 1200, \
        "the reminder must run at twenty minutes"
    slug, nid = fixture("zz-rem-threshold")
    wid = add_item(slug, "Ship the widget", status="in_progress")
    calls: list[tuple[str, str, str]] = []
    try:
        fire(slug, BOUND - 0.001, calls)
        assert calls == [], "before the boundary"
        fire(slug, BOUND, calls)
        assert calls == [], "MORE than 20 minutes: the boundary itself is quiet"
        assert not store.load_org(slug).waking_mail(nid)

        fire(slug, BOUND + 0.001, calls)
        assert [(s, n) for s, n, _ in calls] == [(slug, nid)], calls
        assert "idle-docket reminder" in calls[0][2]
        assert "1 unfinished docket item" in calls[0][2], calls[0][2]

        mail = reminders(slug)
        assert len(mail) == 1, mail
        body = mail[0]["body"]
        assert mail[0]["kind"] == "message" and mail[0]["model_only"] is True
        assert f"- {wid} (in_progress): Ship the widget" in body, body
        assert "orgtree_work" in body
        assert store.load_org(slug).waking_mail(nid), \
            "the reservation must be ordinary waking mail"

        # the persisted mail + the durable stamp make a later tick a no-op
        fire(slug, DUE + 60, calls)
        assert len(calls) == 1, calls
        assert len(reminders(slug)) == 1
    finally:
        park(slug)


check("fires only AFTER 20 minutes, never at the boundary, with one @system "
      "mail naming the item",
      threshold_and_internal_mail)


def nothing_owed_is_silent() -> None:
    slug, nid = fixture("zz-rem-empty")
    calls: list[tuple[str, str, str]] = []
    try:
        fire(slug, DUE, calls)
        fire(slug, DUE + 3600, calls)
        assert calls == [], calls
        assert store.load_org(slug).node(nid).get("docket_reminder_at") is None, \
            "an empty docket must not consume the idle clock"
        # positive control: the same seat, at the same instant, once it owes
        add_item(slug, "Now there is work")
        fire(slug, DUE + 3600, calls)
        assert len(calls) == 1, calls
    finally:
        park(slug)


check("no eligible items means no wake and no stamp; one item makes it fire",
      nothing_owed_is_silent)


print("\n§2  the toggle is separate and defaults off")


def default_off_then_on() -> None:
    slug, _nid = fixture("zz-rem-toggle")
    add_item(slug, "Unfinished")
    calls: list[tuple[str, str, str]] = []
    try:
        doc = appsettings.load(strict=True)
        assert "idle_docket_reminders" not in doc.get("runtime", {}), doc
        assert appsettings.idle_docket_reminders_enabled() is False, \
            "a newly introduced optional wake must default off"
        # the real setting, not a forced mode
        fire(slug, DUE, calls, enabled=None)
        assert calls == [], calls

        appsettings.set_idle_docket_reminders_enabled(True)
        assert appsettings.idle_docket_reminders_enabled() is True
        fire(slug, DUE, calls, enabled=None)
        assert len(calls) == 1, calls

        appsettings.set_idle_docket_reminders_enabled(False)
        assert appsettings.idle_docket_reminders_enabled() is False
        fire(slug, DUE + 2 * 1200, calls, enabled=None)
        assert len(calls) == 1, calls
    finally:
        appsettings.set_idle_docket_reminders_enabled(False)
        park(slug)


check("default off; an explicit switch turns the sweep on and back off",
      default_off_then_on)


def independent_of_the_working_checkup_switch() -> None:
    """An idle agent need not report working, and the checkup mode must not
    decide whether the docket is chased."""
    slug, nid = fixture("zz-rem-independent")     # last_status = idle
    add_item(slug, "Still open")
    checkups: list[tuple[str, str, str]] = []
    calls: list[tuple[str, str, str]] = []
    try:
        S._working_checkup_pass(accepted(checkups), DUE, mode_enabled=True)
        assert mine(checkups, slug) == [], "an idle status is not working"
        for checkup_mode in (True, False):
            appsettings.set_working_checkups_enabled(checkup_mode)
            park(slug, nid)                # the woken turn drained its mail
            fire(slug, DUE + (0 if checkup_mode else 2 * 1200), calls)
            assert len(calls) == (1 if checkup_mode else 2), (checkup_mode, calls)
    finally:
        appsettings.set_working_checkups_enabled(True)
        park(slug)


check("reminders run for a non-working seat under either checkup mode",
      independent_of_the_working_checkup_switch)


print("\n§3  exclusions, each beside a positive control")


def excluded(label: str, prepare, *, control: bool = True):
    """`prepare(slug)` sets up ONE ineligible item. The sweep must stay
    silent — and then an ordinary open item on the same seat must fire, or
    the silence proved nothing."""
    def run() -> None:
        slug, _nid = fixture(f"zz-rem-x-{label}", peers=("peer",))
        calls: list[tuple[str, str, str]] = []
        try:
            prepare(slug)
            fire(slug, DUE, calls)
            assert calls == [], (label, calls)
            assert reminders(slug) == []
            if control:
                add_item(slug, "An ordinary open item")
                fire(slug, DUE, calls)
                assert len(calls) == 1, (label, "positive control", calls)
        finally:
            park(slug)
    return run


def backlogged(slug: str) -> None:
    add_item(slug, "Not started yet", status="backlogged")


def done_item(slug: str) -> None:
    wid = add_item(slug, "Finished work")
    ledger_do(slug, lambda org: org.work_accept(USER, wid))


def archived_item(slug: str) -> None:
    wid = add_item(slug, "Finished and filed")
    ledger_do(slug, lambda org: org.work_accept(USER, wid))
    ledger_do(slug, lambda org: org.work_archive_now(USER, wid))
    assert not store.load_org(slug).d["work_items"], "the item must be filed"


def manual_attention(slug: str) -> None:
    wid = add_item(slug, "Waiting on the user")
    ledger_do(slug, lambda org: org.work_update(
        USER, wid, ["a"], [], attention=True,
        attention_reason="the user must decide"))
    assert store.load_org(slug).work_get(USER, wid)[
        "attention_sources"] == ["manual"]


def open_question(slug: str) -> None:
    wid = add_item(slug, "Question attached")
    r = ledger_do(slug, lambda org: org.ask_user(
        "agent", question="which way?", work_item=wid))
    assert r.get("asked"), r
    assert store.load_org(slug).work_get(USER, wid)[
        "attention_sources"] == ["question"]


def someone_elses(slug: str) -> None:
    add_item(slug, "Not mine", owner="peer")


def participant_only(slug: str) -> None:
    add_item(slug, "Peer's item, I collaborate", owner="peer",
             participants=["agent"])


def unowned(slug: str) -> None:
    add_item(slug, "Nobody owns this", owner=None)


def waiting_on_an_event(slug: str) -> None:
    add_item(slug, "Waits for the nightly build", status="waiting")


def reviewed_by_a_peer(slug: str) -> None:
    """The owner is NOT the next actor while somebody else has the review."""
    add_item(slug, "The peer is reviewing this", status="review",
             reviewer="peer")


for _label, _prep in (
        ("backlogged", backlogged),
        ("done", done_item),
        ("archived", archived_item),
        ("attention", manual_attention),
        ("question", open_question),
        ("otherowner", someone_elses),
        ("participant", participant_only),
        ("unowned", unowned),
        ("waiting", waiting_on_an_event),
        ("underpeerreview", reviewed_by_a_peer)):
    check(f"excluded: {_label} items never wake their agent (control fires)",
          excluded(_label, _prep))


def mixed_set_lists_only_the_eligible() -> None:
    slug, _nid = fixture("zz-rem-mixed", peers=("peer",))
    calls: list[tuple[str, str, str]] = []
    try:
        live = add_item(slug, "Alpha keeps moving", status="in_progress")
        blocked = add_item(slug, "Bravo is stuck", status="blocked")
        review = add_item(slug, "Charlie awaits agent review", status="review")
        add_item(slug, "Delta is not started", status="backlogged")
        flagged = add_item(slug, "Echo needs the user")
        ledger_do(slug, lambda org: org.work_update(
            USER, flagged, ["x"], [], attention=True, attention_reason="decide"))
        asked = add_item(slug, "Foxtrot has a question")
        ledger_do(slug, lambda org: org.ask_user(
            "agent", question="which way?", work_item=asked))
        finished = add_item(slug, "Golf is done")
        ledger_do(slug, lambda org: org.work_accept(USER, finished))
        add_item(slug, "Hotel belongs to a peer", owner="peer")

        fire(slug, DUE, calls)
        assert len(calls) == 1, calls
        body = reminders(slug)[0]["body"]
        listed = [ln for ln in body.splitlines() if ln.startswith("- ")]
        assert sorted(listed) == sorted([
            f"- {live} (in_progress): Alpha keeps moving",
            f"- {blocked} (blocked): Bravo is stuck",
            f"- {review} (review — NO REVIEWER NAMED: assign one, do not "
            f"review your own work): Charlie awaits agent review"]), listed
        assert "3 unfinished docket item" in calls[0][2], calls[0][2]
    finally:
        park(slug)


check("a mixed docket lists only the eligible items, and counts only those",
      mixed_set_lists_only_the_eligible)


def ownership_survives_a_generation_move() -> None:
    slug, nid = fixture("zz-rem-generation")
    calls: list[tuple[str, str, str]] = []
    try:
        wid = add_item(slug, "Owned before the compaction")
        ledger_do(slug, lambda org: org.node(nid).update(
            {"generation": int(org.node(nid).get("generation") or 0) + 1}))
        org = store.load_org(slug)
        assert org.work_get(USER, wid)["owner_current"] is False, \
            "the fixture must actually model a moved generation"
        assert [r["slug"] for r in org.work_idle_reminder_items(nid)] == [wid]
        fire(slug, DUE, calls)
        assert len(calls) == 1, calls
    finally:
        park(slug)


check("a rehired/compacted agent is still the owner and is still reminded",
      ownership_survives_a_generation_move)


print("\n§4  what counts as idle")


def runtime_busy_seats_are_skipped() -> None:
    slug, nid = fixture("zz-rem-busy")
    add_item(slug, "Unfinished")
    calls: list[tuple[str, str, str]] = []
    try:
        for key, value in (("busy", True), ("waiting", True),
                           ("responding", True), ("queue", ["real turn"]),
                           ("cache_keepalive", {"lease": True})):
            runtime_clear(slug, nid)
            S.state(slug, nid)[key] = value
            fire(slug, DUE, calls)
            assert calls == [], (key, calls)
        runtime_clear(slug, nid)                       # busy → idle
        fire(slug, DUE, calls)
        assert len(calls) == 1, calls
    finally:
        park(slug)


check("busy/waiting/responding/queued/keepalive seats wait; an idle one fires",
      runtime_busy_seats_are_skipped)


def a_recent_turn_beats_an_ancient_status() -> None:
    """The failure this exists to stop: a decades-old `last_status` read as
    twenty minutes of idleness right after a real turn."""
    slug, nid = fixture("zz-rem-ancient")
    add_item(slug, "Unfinished")
    calls: list[tuple[str, str, str]] = []
    try:
        ledger_do(slug, lambda org: org.node(nid).update({
            "last_status": {"status": "done", "summary": "long ago",
                            "at": "1999-01-01T00:00:00Z"},
            "turns": [{"at": iso(BASE), "cost": 0.0, "ms": 1, "denials": 0}]}))
        fire(slug, BOUND, calls)
        assert calls == [], "the finished turn is the activity boundary"
        fire(slug, DUE, calls)
        assert len(calls) == 1, calls
    finally:
        park(slug)

    # and the symmetric one: a long turn that FINISHED after its own wake
    slug, nid = fixture("zz-rem-longturn")
    add_item(slug, "Unfinished")
    calls = []
    try:
        ledger_do(slug, lambda org: org.node(nid)["turns"].append(
            {"at": iso(BASE + 900), "cost": 0.0, "ms": 1, "denials": 0}))
        fire(slug, DUE, calls)
        assert calls == [], "the clock starts when the turn ENDS"
        fire(slug, BASE + 900 + S.IDLE_DOCKET_REMINDER_AFTER_S, calls)
        assert calls == [], "and it too is quiet AT the boundary"
        fire(slug, BASE + 901 + S.IDLE_DOCKET_REMINDER_AFTER_S, calls)
        assert len(calls) == 1, calls
    finally:
        park(slug)


check("the idle clock is the newest real boundary, not the status stamp",
      a_recent_turn_beats_an_ancient_status)


def a_row_without_any_stamp_is_seeded() -> None:
    slug, nid = fixture("zz-rem-legacy")
    add_item(slug, "Unfinished")
    calls: list[tuple[str, str, str]] = []
    try:
        ledger_do(slug, lambda org: (org.node(nid).pop("last_status", None),
                                     org.node(nid).update({"turns": []})))
        fire(slug, BASE, calls)
        assert calls == [], "absence of evidence is not twenty idle minutes"
        assert store.load_org(slug).node(nid)["docket_reminder_at"] == iso(BASE)
        fire(slug, DUE, calls)
        assert len(calls) == 1, calls
    finally:
        park(slug)


check("a row with no timestamps at all is seeded, never fired on",
      a_row_without_any_stamp_is_seeded)


print("\n§5  durable gates, rate limiting and restarts")


def durable_gate_exclusions() -> None:
    cases = (
        ("frozen", {"at": iso(BASE), "resume_texts": []}),
        ("limit_locked", True),
        ("remote_controlled", {"at": iso(BASE), "pid": 1}),
        ("inflight", {"at": iso(BASE), "text": "real turn"}),
    )
    for i, (key, value) in enumerate(cases):
        slug, nid = fixture(f"zz-rem-gate-{i}")
        add_item(slug, "Unfinished")
        calls: list[tuple[str, str, str]] = []
        try:
            def seed(org, key=key, value=value):
                org.node(nid)[key] = value
                if key == "limit_locked":
                    org.d["fable_lock"] = {"no_reset": True}
            ledger_do(slug, seed)
            fire(slug, DUE, calls)
            assert calls == [], (key, calls)
            ledger_do(slug, lambda org: org.node(nid).pop(key, None))
            fire(slug, DUE, calls)
            assert len(calls) == 1, (key, "positive control", calls)
        finally:
            park(slug)

    slug, nid = fixture("zz-rem-waking-mail")
    add_item(slug, "Unfinished")
    calls = []
    try:
        ledger_do(slug, lambda org: org.post_mail(USER, nid, "real mail waits"))
        fire(slug, DUE, calls)
        assert calls == [], calls
        park(slug)                                     # drains the mailbox
        fire(slug, DUE, calls)
        assert len(calls) == 1, calls
    finally:
        park(slug)

    slug, nid = fixture("zz-rem-retired")
    add_item(slug, "Unfinished")
    calls = []
    try:
        ledger_do(slug, lambda org: org.node(nid).update({"state": "archived"}))
        fire(slug, DUE, calls)
        assert calls == [], calls
        ledger_do(slug, lambda org: org.node(nid).update({"state": "live"}))
        fire(slug, DUE, calls)
        assert len(calls) == 1, calls
    finally:
        park(slug)


check("frozen/locked/remote/inflight/retired seats and waiting mail are excluded",
      durable_gate_exclusions)


def repeated_ticks_and_restart() -> None:
    slug, nid = fixture("zz-rem-restart")
    add_item(slug, "Unfinished")
    calls: list[tuple[str, str, str]] = []
    driven: list[tuple[str, str, str]] = []
    real_send = S.send_message
    try:
        fire(slug, DUE, calls)
        assert len(calls) == 1
        for i in range(1, 20):                         # a scheduler tick storm
            fire(slug, DUE + i, calls)
        assert len(calls) == 1, calls
        runtime_clear(slug, nid)                       # the whole RAM state died
        fire(slug, DUE + 60, calls)
        assert len(calls) == 1, calls
        assert len(reminders(slug)) == 1

        S.send_message = lambda s, n, text, **kw: (                 # type: ignore[assignment]
            driven.append((s, n, text)), {"accepted": True})[1]
        S.reconcile(slug)
        assert [(s, n) for s, n, _ in driven] == [(slug, nid)], driven
        assert "waited across an orgtree restart" in driven[0][2]
    finally:
        S.send_message = real_send                                 # type: ignore[assignment]
        park(slug)

    # after a full cooldown the same seat is reminded again
    slug, nid = fixture("zz-rem-cooldown")
    add_item(slug, "Unfinished")
    calls = []
    try:
        fire(slug, DUE, calls)
        park(slug)                                     # the turn drained the mail
        fire(slug, DUE + S.IDLE_DOCKET_REMINDER_AFTER_S, calls)
        assert len(calls) == 1, "the cooldown boundary is quiet too"
        fire(slug, DUE + S.IDLE_DOCKET_REMINDER_AFTER_S + 1, calls)
        assert len(calls) == 2, calls
    finally:
        park(slug)


check("one reminder per 20 minutes across ticks and a restart; reconcile drives it",
      repeated_ticks_and_restart)


def a_refused_wake_withdraws_its_mail() -> None:
    slug, nid = fixture("zz-rem-refused")
    add_item(slug, "Unfinished")
    seen: list[str] = []

    def refuse(s: str, n: str, text: str) -> dict[str, Any]:
        seen.append(text)
        return {"accepted": True, "not_idle": True}

    try:
        S._idle_docket_reminder_pass(refuse, DUE, mode_enabled=True)
        assert len(seen) == 1, seen
        assert reminders(slug) == [], "a lost race must not leave stale mail"
        assert not store.load_org(slug).waking_mail(nid)
        # the reservation still cools down: a real turn just won this seat
        calls: list[tuple[str, str, str]] = []
        fire(slug, DUE + 1, calls)
        assert calls == [], calls
    finally:
        park(slug)


check("a wake refused by the real turn withdraws its mail and still cools down",
      a_refused_wake_withdraws_its_mail)


print("\n§6  living beside the working checkup")


def working_fixture(name: str):
    """A seat the CHECKUP is also due for: reported working, quiet since BASE."""
    slug, nid = fixture(name, status="working")
    ledger_do(slug, lambda org: org.node(nid).update(
        {"working_activity_at": iso(BASE)}))
    return slug, nid


def tick(sent: list[tuple[str, str, str]], now: float) -> None:
    """One REAL scheduler tick — both passes, through send_message. The stub
    deliberately does not take the seat busy, so only durable state can stop a
    second wake."""
    real_send = S.send_message
    S.send_message = lambda s, n, text, **kw: (                     # type: ignore[assignment]
        sent.append((s, n, text)), {"accepted": True})[1]
    try:
        S._auto_wake_keeper_pass(now)
    finally:
        S.send_message = real_send                                 # type: ignore[assignment]


def checkup_mail(slug: str, nid: str = "agent") -> list[dict[str, Any]]:
    return [m for m in (store.load_org(slug).d.get("mail") or {}).get(nid) or []
            if m.get("body") == S.WORKING_CHECKUP_PROMPT]


def the_reminder_wins_the_shared_tick() -> None:
    """Both switches on, both wakes due, one seat: the wake that ARRIVES must
    be the one that names the work."""
    slug, nid = working_fixture("zz-rem-both")
    wid = add_item(slug, "Unfinished")
    sent: list[tuple[str, str, str]] = []
    try:
        appsettings.set_working_checkups_enabled(True)
        appsettings.set_idle_docket_reminders_enabled(True)
        tick(sent, DUE)
        assert len(mine(sent, slug)) == 1, mine(sent, slug)
        assert "idle-docket reminder" in mine(sent, slug)[0][2]
        assert checkup_mail(slug) == [], "exactly one wake, and it is not the checkup"
        got = reminders(slug)
        assert len(got) == 1 and wid in got[0]["body"], got

        # SECOND IDLE CYCLE: the woken turn drained its mail and finished, so
        # both clocks moved. The seat goes quiet again and is due again.
        park(slug)
        ledger_do(slug, lambda org: org.node(nid).update(
            {"working_activity_at": iso(DUE),
             "turns": [{"at": iso(DUE), "cost": 0.0, "ms": 1, "denials": 0}]}))
        tick(sent, DUE + 600)
        assert len(mine(sent, slug)) == 1, "half an interval wakes nobody"
        # (the boundary itself is pinned in §1; at exactly 1200 the CHECKUP is
        # due by its own older `>=` rule, which this branch does not change)
        tick(sent, DUE + S.IDLE_DOCKET_REMINDER_AFTER_S + 1)
        assert len(mine(sent, slug)) == 2, mine(sent, slug)
        assert "idle-docket reminder" in mine(sent, slug)[1][2]
        assert checkup_mail(slug) == []
        assert wid in reminders(slug)[0]["body"]
    finally:
        appsettings.set_idle_docket_reminders_enabled(False)
        appsettings.set_working_checkups_enabled(True)
        park(slug)


def the_checkup_still_fires_when_the_reminder_passes() -> None:
    """The controls for the arbitration above: a working seat with nothing
    owed, and a working seat while the reminder switch is off."""
    slug, _nid = working_fixture("zz-rem-nothing-owed")   # no items at all
    sent: list[tuple[str, str, str]] = []
    try:
        appsettings.set_working_checkups_enabled(True)
        appsettings.set_idle_docket_reminders_enabled(True)
        tick(sent, DUE)
        assert len(mine(sent, slug)) == 1, mine(sent, slug)
        assert "20-minute working-status check" in mine(sent, slug)[0][2]
        assert len(checkup_mail(slug)) == 1 and reminders(slug) == []
    finally:
        appsettings.set_idle_docket_reminders_enabled(False)
        park(slug)

    slug, _nid = working_fixture("zz-rem-switch-off")
    add_item(slug, "Unfinished but the switch is off")
    sent = []
    try:
        appsettings.set_working_checkups_enabled(True)
        appsettings.set_idle_docket_reminders_enabled(False)
        tick(sent, DUE)
        assert len(mine(sent, slug)) == 1, mine(sent, slug)
        assert "20-minute working-status check" in mine(sent, slug)[0][2]
        assert reminders(slug) == []
    finally:
        park(slug)


def a_reserved_reminder_blocks_the_cache_read() -> None:
    """The disabled-checkup fallback must not fire a keepalive beside a real
    turn that is already reserved."""
    slug, nid = fixture("zz-rem-cachedue", status="working")
    add_item(slug, "Unfinished")
    real_tp = S.transcript_path
    try:
        S.transcript_path = lambda sid, root=None: "existing.jsonl"  # type: ignore[assignment]
        later = BASE + max(S.WORKING_CACHE_SUBSCRIPTION_S,
                           S.IDLE_DOCKET_REMINDER_AFTER_S) + 1
        org = store.load_org(slug)
        assert S._working_cache_due(org, nid, later) is True, \
            "positive control: without the reservation this seat IS due"
        got = S._idle_docket_reminder_reserve(slug, nid, later)
        assert got, "the reminder must actually be reserved for this check"
        assert S._working_cache_due(store.load_org(slug), nid, later) is False
    finally:
        S.transcript_path = real_tp                                # type: ignore[assignment]
        park(slug)


check("one wake per tick, and it is the reminder that names the work; the "
      "seat is due again after a second idle interval",
      the_reminder_wins_the_shared_tick)
check("the generic checkup still fires with nothing owed, or reminders off",
      the_checkup_still_fires_when_the_reminder_passes)
check("a reserved reminder stops the fallback cache read on that seat",
      a_reserved_reminder_blocks_the_cache_read)


print("\n§7  the clock belongs to whoever owes the next action")


def stamp(slug: str, nid: str, when: float) -> None:
    """Give one seat a real activity boundary of its own."""
    with store.DOC_LOCK:
        org = store.load_org(slug)
        n = org.node(nid)
        n["last_status"] = {"status": "idle", "summary": "parked",
                            "at": iso(when)}
        n["turns"] = [{"at": iso(when), "cost": 0.0, "ms": 1, "denials": 0}]
        store.save_org(org)


def the_reviewer_clock_decides_a_review_item() -> None:
    slug, _nid = fixture("zz-rem-reviewclock", peers=("peer",))
    calls: list[tuple[str, str, str]] = []
    try:
        wid = add_item(slug, "Charlie awaits agent review", status="review",
                       reviewer="peer")
        # the OWNER worked a moment ago; the REVIEWER has been idle for hours
        stamp(slug, "agent", DUE - 60)
        stamp(slug, "peer", BASE)
        fire(slug, DUE, calls, nid="peer")
        assert len(calls) == 1, calls
        assert reminders(slug, "agent") == [], "the busy owner was woken"
        body = reminders(slug, "peer")[0]["body"]
        assert f"- {wid} (review — awaiting YOUR review): " in body, body
    finally:
        park(slug, "peer")
        park(slug)


def the_owners_clock_does_not_decide_it() -> None:
    """The mirror image, and it is the check that makes the one above mean
    something: the same item, the same fleet pass, only the two clocks
    swapped — nobody is woken at all."""
    slug, _nid = fixture("zz-rem-ownerclock", peers=("peer",))
    calls: list[tuple[str, str, str]] = []
    try:
        under = add_item(slug, "Charlie awaits agent review", status="review",
                         reviewer="peer")
        stamp(slug, "agent", BASE)              # the owner is long idle
        stamp(slug, "peer", DUE - 60)           # the reviewer just worked
        fire(slug, DUE, calls)
        fire(slug, DUE, calls, nid="peer")
        assert calls == [], calls
        assert reminders(slug, "agent") == [] and reminders(slug, "peer") == []
        # POSITIVE CONTROL: give the idle owner work of its own and it fires
        own = add_item(slug, "Alpha is mine", status="in_progress")
        fire(slug, DUE, calls)
        assert len(calls) == 1, calls
        body = reminders(slug, "agent")[0]["body"]
        assert [ln for ln in body.splitlines() if ln.startswith("- ")] == \
            [f"- {own} (in_progress): Alpha is mine"], body
        assert under not in body, "the review item reached the owner"
    finally:
        park(slug, "peer")
        park(slug)


def one_wake_carries_both_kinds_of_work() -> None:
    slug, _nid = fixture("zz-rem-mixedrole", peers=("peer",))
    calls: list[tuple[str, str, str]] = []
    try:
        mine_ = add_item(slug, "Alpha is mine", status="in_progress")
        theirs = add_item(slug, "Bravo is the peer's", owner="peer",
                          status="review", reviewer="agent")
        add_item(slug, "Charlie is the peer's alone", owner="peer",
                 status="in_progress")
        fire(slug, DUE, calls)
        assert len(calls) == 1, calls           # ONE notification, not two
        body = reminders(slug)[0]["body"]
        listed = [ln for ln in body.splitlines() if ln.startswith("- ")]
        assert sorted(listed) == sorted([
            f"- {mine_} (in_progress): Alpha is mine",
            f"- {theirs} (review — awaiting YOUR review): "
            f"Bravo is the peer's"]), listed
        assert "2 unfinished docket item" in calls[0][2], calls[0][2]
    finally:
        park(slug)


def a_retired_reviewer_says_so_in_the_row() -> None:
    """The row the OWNER reads when the reviewer it named has been retired.

    The resolver hands the item back (that is checked at the ledger level);
    what is checked HERE is the sentence, because the sentence is the whole
    point of handing it back. Without it the row reads exactly like an
    ordinary owned item and the owner is nudged about somebody else's dead
    review with no way to tell what it wants.
    """
    slug, _nid = fixture("zz-rem-staleroles", peers=("peer",))
    calls: list[tuple[str, str, str]] = []
    try:
        wid = add_item(slug, "Charlie awaits agent review", status="review",
                       reviewer="peer")
        own = add_item(slug, "Alpha is mine", status="in_progress")
        # CONTROL, while the reviewer is still live: it is the REVIEWER's row,
        # the owner is not told about it, and the wording is the live one
        stamp(slug, "peer", BASE)
        fire(slug, DUE, calls, nid="peer")
        assert f"- {wid} (review — awaiting YOUR review): " \
            in reminders(slug, "peer")[0]["body"]
        assert wid not in reminders(slug, "agent")[0]["body"]

        ledger_do(slug, lambda org: org.retire(USER, "peer"))
        park(slug)
        # PAST THE COOLDOWN: the first sweep stamped `docket_reminder_at`, and
        # that stamp is part of the idle anchor, so a second wake needs another
        # whole interval — firing at DUE + 1 would test the rate limit instead
        fire(slug, DUE + S.IDLE_DOCKET_REMINDER_AFTER_S + 1, calls)
        body = reminders(slug, "agent")[-1]["body"]
        assert f"- {wid} (review — its named reviewer is no longer live: " \
            f"name another, do not review your own work): " in body, body
        # and the ordinary row beside it is untouched: the suffix belongs to
        # the role, not to the reminder
        assert f"- {own} (in_progress): Alpha is mine" in body, body
    finally:
        park(slug, "peer")
        park(slug)


check("a review item is gated by the REVIEWER's idle clock",
      the_reviewer_clock_decides_a_review_item)
check("a retired reviewer's row TELLS THE OWNER SO, in words "
      "(control: the live wording, on the reviewer's own row)",
      a_retired_reviewer_says_so_in_the_row)
check("an idle owner is not woken for an item under somebody else's review "
      "(control: its own work still fires)", the_owners_clock_does_not_decide_it)
check("own work and a review arrive in one wake, each row saying which",
      one_wake_carries_both_kinds_of_work)


shutil.rmtree(ROOT, ignore_errors=True)
print(f"\nALL {PASS} CHECKS PASS" if not FAIL else f"\n{FAIL} FAILED, {PASS} PASSED")
raise SystemExit(1 if FAIL else 0)
