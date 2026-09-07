"""The REMOVED `waiting` docket state (user 2026-09-07 06:33Z: "remove waiting as
a task state, and make blocked avoid periodic status nudges"), the compatibility
the rows already stored under it are owed, the information `blocked` owes, and
the next-action recipient resolver.

Run: python backend/tests/test_docket_waiting_state.py

Everything here is driven through the real ledger on a throwaway data root — no
scheduler, no provider, no clock to wait on. What is pinned:

  §1  `waiting` can no longer be asserted or created — the refusal names
      `blocked` — and a row STORED as waiting (planted raw, the way the live
      documents hold it) is READ as blocked everywhere: served status, served
      reason (falling back to the recorded waiting reason), `legacy_status`
      saying what it was stored as, the active count, the archive predicate
      (never ages out by itself; a physically archived one stays archived and
      reopens), and history untouched. A read never writes.
  §2  the item's own next update CONVERTS it — stored status becomes blocked,
      the waiting reason is carried into blocked_reason, the conversion is
      recorded in history, and no fresh reason is demanded of a row that had
      none — while every other verb that leaves the stored state clears the
      leftover field.
  §3  the information `blocked` owes: entering needs the field, staying does
      not, a blank never erases, a refused transition writes nothing.
  §4  the next-action recipient: the reviewer while under review, the owner
      otherwise, with exclusions decided per item before anyone is grouped —
      and BLOCKED IS NEVER A REMINDER ROW (the policy's second half), while
      the actionable items beside it still are.

The `reviewer` field is codex-sandbox's and no verb here writes it yet, so the
fixtures plant it directly on the stored item — which is exactly the shape the
resolver reads.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

ROOT = tempfile.mkdtemp(prefix="orgtree-waiting-state-")
os.environ["ORGTREE_DATA"] = ROOT
os.environ["HOME"] = os.path.join(ROOT, "home")
os.environ["USERPROFILE"] = os.path.join(ROOT, "home")
os.environ["ORGTREE_PORT"] = "7424"
os.makedirs(os.environ["HOME"], exist_ok=True)
with open(os.path.join(ROOT, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from orgtree import store                                     # noqa: E402
from orgtree.ledger import LedgerError, USER                   # noqa: E402

PASS = FAIL = 0
EVENT = "the nightly build finishes; the build watchdog mails me"
BLOCK = "the vendor has not sent the key; their support can send it"


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


_N = [0]


def fixture(peers: tuple[str, ...] = ()):
    """A fresh org with a live `agent`, plus any peers asked for."""
    _N[0] += 1
    org = store.create_org(f"zz-wait-{_N[0]:03d}")
    org.hire(USER, None, "haiku", 0, "agent")
    for p in peers:
        org.hire(USER, None, "haiku", 0, p)
    store.save_org(org)
    return org.d["slug"]


def do(slug: str, fn):
    with store.DOC_LOCK:
        org = store.load_org(slug)
        out = fn(org)
        store.save_org(org)
    return out


def item(slug: str, title: str = "an item", *, owner: str | None = "agent",
         status: str = "open", **kw) -> str:
    return str(do(slug, lambda org: org.work_create(
        USER, title, objective="the problem; then the proposal",
        owner=owner, status=status, **kw))["slug"])


def view(slug: str, wid: str) -> dict:
    return store.load_org(slug).work_get(USER, wid)


def refused(fn) -> str:
    try:
        fn()
    except LedgerError as e:
        return str(e)
    raise AssertionError("that was accepted; it had to be refused")


def upd(slug: str, wid: str, **kw):
    return do(slug, lambda org: org.work_update(
        USER, wid, kw.pop("done", ["a step"]), kw.pop("next", []), **kw))

def backdate(slug: str, wid: str, seconds: int) -> float:
    """Push the item's docket clock into the past. Returns the `now` it has
    been aged against, so the caller never compares against a second clock."""
    with store.DOC_LOCK:
        org = store.load_org(slug)
        it, _ = org._work_find(wid)
        dt = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        it["docket_at"] = dt.isoformat()
        store.save_org(org)
    return dt.timestamp() + seconds


def sweep(slug: str, at: float) -> list[str]:
    with store.DOC_LOCK:
        org = store.load_org(slug)
        moved = org._work_sweep(at)
        store.save_org(org)
    return moved


def plant_waiting(slug: str, wid: str, reason: str | None = EVENT) -> None:
    """Store the row EXACTLY as the live documents hold a pre-removal item:
    status `waiting`, its reason in `waiting_reason`, nothing in
    `blocked_reason`. No verb can produce this shape any more, which is the
    point — it is the shape compatibility has to read."""
    with store.DOC_LOCK:
        org = store.load_org(slug)
        it, _ = org._work_find(wid)
        it["status"] = "waiting"
        it["waiting_reason"] = reason
        it["blocked_reason"] = None
        store.save_org(org)


def raw(slug: str, wid: str) -> dict:
    it, _ = store.load_org(slug)._work_find(wid)
    return it


print("\n§1  waiting cannot be asserted; a stored one is READ as blocked")


def waiting_is_refused_on_update_and_create() -> None:
    slug = fixture()
    wid = item(slug, "Moving", status="in_progress")
    msg = refused(lambda: upd(slug, wid, status="waiting", waiting_reason=EVENT))
    assert "no longer a task state" in msg and "`blocked`" in msg, msg
    assert raw(slug, wid)["status"] == "in_progress", "a refused transition wrote"
    assert raw(slug, wid).get("waiting_reason") in (None, "")
    msg = refused(lambda: item(slug, "New", status="waiting", waiting_reason=EVENT))
    assert "waiting" not in msg.split("starts", 1)[-1], msg     # the menu no longer lists it
    # CONTROL: the replacement goes through, with its field
    upd(slug, wid, status="blocked", blocked_reason=BLOCK)
    assert view(slug, wid)["status"] == "blocked"
    # and reopen's own menu does not offer it either
    org = store.load_org(slug)
    assert "waiting" not in org.WORK_STATUSES and "waiting" not in org.WORK_AGENT_STATUSES
    assert org.WORK_LEGACY_STATUSES == {"waiting": "blocked"}


check("`waiting` is refused on update and create, naming blocked; nothing is written; "
      "blocked still goes through (control)", waiting_is_refused_on_update_and_create)


def a_stored_waiting_row_reads_as_blocked() -> None:
    slug = fixture()
    wid = item(slug, "Recorded as waiting", status="in_progress")
    plant_waiting(slug, wid)
    v = view(slug, wid)
    assert v["status"] == "blocked" and v["legacy_status"] == "waiting", (v["status"], v.get("legacy_status"))
    assert v["blocked_reason"] == EVENT, "the recorded reason is served as the blocked reason"
    assert v["waiting_reason"] == EVENT, "and the field it was recorded in is still served"
    # the list agrees: served as blocked, counted as active (blocked is), on the main list
    lst = store.load_org(slug).work_list(USER, include_archived=True)
    row = [r for r in lst["items"] if r["slug"] == wid][0]
    assert row["status"] == "blocked" and row["legacy_status"] == "waiting"
    assert lst["counts"]["active"] == 1 and lst["archived"] == [], lst["counts"]
    # the READ wrote nothing: the document still holds the stored word
    assert raw(slug, wid)["status"] == "waiting" and raw(slug, wid)["blocked_reason"] is None
    # CONTROL: an ordinary blocked row carries no legacy marker
    plain = item(slug, "Plainly blocked", status="blocked", blocked_reason=BLOCK)
    assert view(slug, plain)["legacy_status"] is None and view(slug, plain)["blocked_reason"] == BLOCK


check("a row stored as waiting is served as blocked with its reason and legacy_status; "
      "counted active; the read writes nothing; plain blocked has no marker (control)",
      a_stored_waiting_row_reads_as_blocked)


def an_own_blocked_reason_wins_over_the_recorded_one() -> None:
    slug = fixture()
    wid = item(slug, "Both reasons", status="in_progress")
    plant_waiting(slug, wid)
    with store.DOC_LOCK:
        org = store.load_org(slug)
        org._work_find(wid)[0]["blocked_reason"] = BLOCK
        store.save_org(org)
    assert view(slug, wid)["blocked_reason"] == BLOCK, "a stored blocked reason is never overridden"


check("a stored blocked_reason beside a legacy waiting reason is the one served",
      an_own_blocked_reason_wins_over_the_recorded_one)


def a_legacy_row_never_ages_out_by_itself() -> None:
    """Blocked never archives itself, and a legacy row IS blocked now — so the
    hour that used to archive `waiting` no longer does. CONTROL: `done` still
    ages out on the same fixture."""
    slug = fixture()
    wid = item(slug, "Recorded as waiting", status="in_progress")
    plant_waiting(slug, wid)
    fine = item(slug, "Finished", status="review")
    do(slug, lambda org: org.work_accept(USER, fine))
    at = max(backdate(slug, wid, 360000), backdate(slug, fine, 3601))
    lst = store.load_org(slug).work_list(USER, include_archived=True, now_ts=at)
    assert [r["slug"] for r in lst["items"]] == [wid], lst["items"]
    assert [r["slug"] for r in lst["archived"]] == [fine], lst["archived"]
    moved = sweep(slug, at)
    assert moved == [fine], moved


check("a legacy waiting row never ages out by itself (blocked does not); done still "
      "does (control)", a_legacy_row_never_ages_out_by_itself)


def a_physically_archived_legacy_row_stays_archived_and_reopens() -> None:
    """The live documents hold rows that were archived AS waiting by the old
    hourly sweep. They stay in the archive — read as blocked — and come back
    with reopen=true like any archived item, converted on that write."""
    slug = fixture()
    wid = item(slug, "Archived as waiting", status="in_progress")
    plant_waiting(slug, wid)
    with store.DOC_LOCK:
        org = store.load_org(slug)
        it, _ = org._work_find(wid)
        org._work_active().remove(it)
        it["archived_at"] = "2026-09-06T20:00:00Z"
        org.d.setdefault("work_items_archive", []).append(it)
        store.save_org(org)
    v = view(slug, wid)
    assert v["archived"] is True and v["status"] == "blocked" and v["legacy_status"] == "waiting"
    assert v["blocked_reason"] == EVENT
    lst = store.load_org(slug).work_list(USER, include_archived=True)
    assert [r["slug"] for r in lst["archived"]] == [wid] and lst["items"] == []
    msg = refused(lambda: upd(slug, wid, done=["poking it"]))
    assert "ARCHIVED (blocked" in msg, msg          # named by what it READS as
    upd(slug, wid, status="in_progress", reopen=True, done=["resuming"])
    v = view(slug, wid)
    assert v["status"] == "in_progress" and v["archived"] is False and v["legacy_status"] is None
    assert v["waiting_reason"] in (None, "") and v["blocked_reason"] in (None, "")
    assert any(h.get("op") == "reopen" for h in v["history"])


check("a legacy row archived as waiting stays archived (read as blocked) and reopens",
      a_physically_archived_legacy_row_stays_archived_and_reopens)


def history_is_untouched_by_reading() -> None:
    slug = fixture()
    wid = item(slug, "Recorded as waiting", status="in_progress")
    plant_waiting(slug, wid)
    before = raw(slug, wid)["history"]
    view(slug, wid)
    store.load_org(slug).work_list(USER, include_archived=True)
    assert raw(slug, wid)["history"] == before, "a read rewrote history"


check("reading a legacy row rewrites nothing in its history", history_is_untouched_by_reading)


print("\n§2  the item's next write converts it; every other exit clears the field")


def the_next_update_converts_in_place() -> None:
    slug = fixture()
    wid = item(slug, "Recorded as waiting", status="in_progress")
    plant_waiting(slug, wid)
    upd(slug, wid, done=["still stuck on the build"])       # no status named
    it = raw(slug, wid)
    assert it["status"] == "blocked", it["status"]
    assert it["blocked_reason"] == EVENT, "the waiting reason was carried into blocked_reason"
    assert it.get("waiting_reason") in (None, ""), "the leftover field was cleared"
    last = it["history"][-1]
    assert last["op"] == "update" and last["changes"]["legacy_status"] == {"from": "waiting", "read_as": "blocked"}
    assert last["changes"]["status"] == {"from": "waiting", "to": "blocked"}, last["changes"]
    v = view(slug, wid)
    assert v["status"] == "blocked" and v["legacy_status"] is None, "converted rows carry no marker"


check("the item's own next update converts a stored waiting row to blocked, carries the "
      "reason, and records the conversion", the_next_update_converts_in_place)


def an_update_naming_a_status_converts_to_that_status() -> None:
    slug = fixture()
    wid = item(slug, "Recorded as waiting", status="in_progress")
    plant_waiting(slug, wid)
    upd(slug, wid, status="in_progress", done=["the build finished"])
    it = raw(slug, wid)
    assert it["status"] == "in_progress"
    assert it.get("waiting_reason") in (None, "") and it.get("blocked_reason") in (None, "")
    assert it["history"][-1]["changes"]["legacy_status"]["from"] == "waiting"


check("an update that names a status moves the legacy row there and clears both reasons",
      an_update_naming_a_status_converts_to_that_status)


def a_legacy_row_with_no_reason_converts_without_being_refused() -> None:
    slug = fixture()
    wid = item(slug, "Recorded as waiting, no reason", status="in_progress")
    plant_waiting(slug, wid, reason=None)
    upd(slug, wid, done=["poking it"])
    it = raw(slug, wid)
    assert it["status"] == "blocked" and it.get("blocked_reason") in (None, ""), it
    # CONTROL: a genuine entry into blocked still owes the field
    fresh = item(slug, "Fresh", status="in_progress")
    msg = refused(lambda: upd(slug, fresh, status="blocked"))
    assert "blocked_reason" in msg, msg


check("a legacy row that stored no reason converts without a refusal and without "
      "invented prose; a real entry into blocked still owes its field (control)",
      a_legacy_row_with_no_reason_converts_without_being_refused)


def other_exits_clear_the_leftover_field() -> None:
    for verb in ("accept", "supersede", "dismiss", "sendback"):
        slug = fixture(peers=("peer",))
        wid = item(slug, f"Left by {verb}", status="in_progress")
        plant_waiting(slug, wid)
        if verb == "accept":
            do(slug, lambda o: o.work_accept(USER, wid))
        elif verb == "supersede":
            other = item(slug, "The replacement", status="in_progress")
            do(slug, lambda o: o.work_supersede(USER, wid, other))
        elif verb == "dismiss":
            with store.DOC_LOCK:
                o = store.load_org(slug)
                it, _ = o._work_find(wid)
                it["manual_attention_rev"] = 1
                it["manual_attention"] = {"reason": "look", "at": "x", "by": "agent", "set_rev": 1}
                store.save_org(o)
            do(slug, lambda o: o.work_dismiss_attention(wid, 1))
        else:
            do(slug, lambda o: o.work_review_decide(USER, wid, "changes", "redo"))
        it = raw(slug, wid)
        assert it["status"] != "waiting", (verb, it["status"])
        assert it.get("waiting_reason") in (None, ""), (verb, "leftover waiting reason")


check("accept, supersede, the user's dismissal and a review sendback all leave the "
      "stored state with the leftover field cleared", other_exits_clear_the_leftover_field)


print("\n§3  the information blocked owes")


def entering_blocked_needs_the_field() -> None:
    slug = fixture()
    wid = item(slug, "Moving", status="in_progress")
    msg = refused(lambda: upd(slug, wid, status="blocked"))
    assert "blocked_reason" in msg, msg
    assert raw(slug, wid)["status"] == "in_progress", "a refused transition wrote"
    upd(slug, wid, status="blocked", blocked_reason=BLOCK)
    assert view(slug, wid)["blocked_reason"] == BLOCK


check("entering blocked without its field is refused and writes nothing; with it it "
      "goes through", entering_blocked_needs_the_field)


def staying_may_omit_it_and_blank_never_erases() -> None:
    slug = fixture()
    wid = item(slug, "Stuck", status="blocked", blocked_reason=BLOCK)
    upd(slug, wid, done=["still stuck"])
    assert view(slug, wid)["blocked_reason"] == BLOCK
    msg = refused(lambda: upd(slug, wid, blocked_reason="   "))
    assert "blank" in msg and view(slug, wid)["blocked_reason"] == BLOCK
    upd(slug, wid, blocked_reason="a newer reason")
    assert view(slug, wid)["blocked_reason"] == "a newer reason"
    upd(slug, wid, status="in_progress")
    assert view(slug, wid)["blocked_reason"] in (None, ""), "a reason survived its state"


check("staying blocked may omit the reason; a blank never erases it; a new value replaces "
      "it; leaving clears it", staying_may_omit_it_and_blank_never_erases)



print("\n§4  who owes the next action; blocked is never a reminder row")

print("\n§3  who owes the next action")


def name_reviewer(slug: str, wid: str, who: str | None) -> None:
    def plant(org):
        it, _ = org._work_find(wid)
        it["reviewer"] = (None if who is None else
                          {"node": who,
                           "generation": int(org.node(who).get("generation") or 0)})
    do(slug, plant)


def review_goes_to_the_reviewer() -> None:
    slug = fixture(peers=("peer",))
    wid = item(slug, "Under review", status="review")
    name_reviewer(slug, wid, "peer")
    org = store.load_org(slug)
    assert [r["slug"] for r in org.work_idle_reminder_items("peer")] == [wid]
    assert [r["role"] for r in org.work_idle_reminder_items("peer")] == ["reviewer"]
    # THE OWNER IS NOT REMINDED OF IT — the next move is not theirs
    assert org.work_idle_reminder_items("agent") == []


check("an item under review is owed by its reviewer, not its owner",
      review_goes_to_the_reviewer)


def missing_reviewer_falls_back_to_the_owner() -> None:
    slug = fixture(peers=("peer",))
    wid = item(slug, "Under review", status="review")
    org = store.load_org(slug)
    rows = org.work_idle_reminder_items("agent")
    assert [r["slug"] for r in rows] == [wid], rows
    assert rows[0]["role"] == "unassigned_review", rows
    assert org.work_idle_reminder_items("peer") == []


check("a review item with no reviewer falls back to the owner, marked as a "
      "missing review assignment", missing_reviewer_falls_back_to_the_owner)


def a_reviewer_is_read_only_off_review() -> None:
    """The reviewer only owes the next action WHILE the item is under review.
    Changes requested returns it to in_progress, and the owner owes it again."""
    slug = fixture(peers=("peer",))
    wid = item(slug, "Was under review", status="review")
    name_reviewer(slug, wid, "peer")
    upd(slug, wid, status="in_progress")           # changes requested
    org = store.load_org(slug)
    rows = org.work_idle_reminder_items("agent")
    assert [r["slug"] for r in rows] == [wid] and rows[0]["role"] == "owner", rows
    assert org.work_idle_reminder_items("peer") == []


check("off review the owner owes it again, reviewer field or not",
      a_reviewer_is_read_only_off_review)


def reviewership_ignores_generation() -> None:
    slug = fixture(peers=("peer",))
    wid = item(slug, "Under review", status="review")
    name_reviewer(slug, wid, "peer")
    do(slug, lambda org: org.node("peer").update(
        {"generation": int(org.node("peer").get("generation") or 0) + 1}))
    org = store.load_org(slug)
    stored = org._work_find(wid)[0]["reviewer"]["generation"]
    assert stored != int(org.node("peer")["generation"]), "fixture is inert"
    assert [r["slug"] for r in org.work_idle_reminder_items("peer")] == [wid]


check("a compacted or rehired reviewer is still the reviewer",
      reviewership_ignores_generation)


def a_retired_reviewer_hands_it_back() -> None:
    """A reviewer that is gone names a recipient the reminder pass never
    wakes, so the item would stop reaching anybody at all. It falls back to
    the owner under its own role instead."""
    slug = fixture(peers=("peer",))
    wid = item(slug, "Under review", status="review")
    name_reviewer(slug, wid, "peer")
    org = store.load_org(slug)
    assert [r["role"] for r in org.work_idle_reminder_items("peer")] == \
        ["reviewer"], "control: a live reviewer owes it"
    do(slug, lambda org: org.retire(USER, "peer"))
    org = store.load_org(slug)
    assert org.node("peer")["state"] != "live", "fixture must really retire it"
    assert org.work_idle_reminder_items("peer") == []
    rows = org.work_idle_reminder_items("agent")
    assert [(r["slug"], r["role"]) for r in rows] == \
        [(wid, "stale_reviewer")], rows
    # and the reviewer is still RECORDED — nothing was rewritten behind it
    assert org._work_find(wid)[0]["reviewer"]["node"] == "peer"


check("a retired reviewer hands the item back to the owner rather than "
      "leaving it silent", a_retired_reviewer_hands_it_back)




def blocked_is_never_a_reminder_row_but_its_neighbours_are() -> None:
    """THE POLICY'S SECOND HALF (user 2026-09-07): a blocked item never wakes
    its owner; an owner whose only items are blocked (stored so, or legacy
    waiting) gets an empty list; an owner with actionable work beside a
    blocked item is still listed for that work and nothing else."""
    slug = fixture()
    org = store.load_org(slug)
    assert org.work_idle_reminder_items("agent") == []
    stuck = item(slug, "Stuck on the vendor", status="blocked", blocked_reason=BLOCK)
    legacy = item(slug, "Recorded as waiting", status="in_progress")
    plant_waiting(slug, legacy)
    org = store.load_org(slug)
    assert org.work_idle_reminder_items("agent") == [], "a blocked-only owner was listed"
    moving = item(slug, "Alpha keeps moving", status="in_progress")
    org = store.load_org(slug)
    rows = org.work_idle_reminder_items("agent")
    assert [r["slug"] for r in rows] == [moving], rows
    assert stuck not in {r["slug"] for r in rows} and legacy not in {r["slug"] for r in rows}
    # CONTROL: unblocking puts it back on the list
    upd(slug, stuck, status="in_progress", done=["the vendor answered"])
    org = store.load_org(slug)
    assert sorted(r["slug"] for r in org.work_idle_reminder_items("agent")) == sorted([moving, stuck])


check("blocked (stored or legacy waiting) is never a reminder row; actionable neighbours "
      "still are; unblocking restores it (control)",
      blocked_is_never_a_reminder_row_but_its_neighbours_are)


def the_working_checkup_skips_a_blocked_only_owner() -> None:
    """THE OTHER PERIODIC NUDGE, exercised at its ACTUAL decision
    (`_working_checkup_eligible`, the durable half of admission the reserve
    step consults) rather than pinned by source absence (coordinator
    2026-09-07). The agent's last report stays `working` throughout — that is
    the case the idle-docket exclusion alone could not cover.
      · no docket at all         → eligible (control: the report keeps its check)
      · only blocked work        → NOT eligible (stored blocked and legacy waiting)
      · actionable work beside it → eligible again
      · unblocked                → eligible again"""
    from orgtree import supervisor as S
    slug = fixture()
    with store.DOC_LOCK:
        org = store.load_org(slug)
        org.node("agent")["last_status"] = {"status": "working", "summary": "grinding",
                                            "at": "2026-09-07T06:00:00Z"}
        store.save_org(org)
    def quiet(s: str) -> None:
        """Drain the assignment mail a create/update leaves for the owner: a
        waking mail is its own (correct) reason not to run a checkup, and this
        check is about the DOCKET's reason, not the mailbox's."""
        with store.DOC_LOCK:
            o = store.load_org(s)
            (o.d.get("mail") or {}).pop("agent", None)
            (o.d.get("notices") or {}).pop("agent", None)
            store.save_org(o)
    quiet(slug)
    org = store.load_org(slug)
    assert S._reported_working(org.node("agent")) and S._auto_wake_gates_clear(org, "agent"), \
        "fixture: the agent must be a checkup candidate on its own account"
    assert org.work_blocked_only("agent") is False
    assert S._working_checkup_eligible(org, "agent") is True, "no docket: the checkup stands"
    stuck = item(slug, "Stuck on the vendor", status="blocked", blocked_reason=BLOCK)
    quiet(slug)
    org = store.load_org(slug)
    assert org.work_blocked_only("agent") is True
    assert S._working_checkup_eligible(org, "agent") is False, \
        "a blocked-only owner reporting `working` was still due a checkup"
    legacy = item(slug, "Recorded as waiting", status="in_progress")
    plant_waiting(slug, legacy)
    quiet(slug)
    org = store.load_org(slug)
    assert org.work_blocked_only("agent") is True, "a legacy waiting row is blocked here too"
    assert S._working_checkup_eligible(org, "agent") is False
    moving = item(slug, "Alpha keeps moving", status="in_progress")
    quiet(slug)
    org = store.load_org(slug)
    assert org.work_blocked_only("agent") is False
    assert S._working_checkup_eligible(org, "agent") is True, \
        "actionable work beside the blocked ones must keep the checkup"
    upd(slug, moving, status="dropped", dropped_reason="cancelled: not needed")
    quiet(slug)
    org = store.load_org(slug)
    assert S._working_checkup_eligible(org, "agent") is False, "back to blocked-only"
    upd(slug, stuck, status="in_progress", done=["the vendor answered"])
    quiet(slug)
    org = store.load_org(slug)
    assert S._working_checkup_eligible(org, "agent") is True, "unblocking restores the check"
    # somebody ELSE's blocked item does not count against this agent, and an
    # item this agent merely reviews is owed by it (review → reviewer)
    peer_slug = fixture(peers=("peer",))
    with store.DOC_LOCK:
        o = store.load_org(peer_slug)
        o.node("agent")["last_status"] = {"status": "working", "summary": "x", "at": "2026-09-07T06:00:00Z"}
        store.save_org(o)
    item(peer_slug, "Peer's stuck item", owner="peer", status="blocked", blocked_reason=BLOCK)
    quiet(peer_slug)
    o = store.load_org(peer_slug)
    assert o.work_blocked_only("agent") is False and S._working_checkup_eligible(o, "agent") is True


check("the working-status checkup's real decision: a blocked-only owner (stored or legacy "
      "waiting) reporting `working` is NOT due; no-docket and mixed-actionable owners still "
      "are; unblocking restores it", the_working_checkup_skips_a_blocked_only_owner)


print(f"\nALL {PASS} CHECKS PASS" if not FAIL else f"\n{FAIL} FAILED, {PASS} PASSED")
sys.exit(1 if FAIL else 0)
