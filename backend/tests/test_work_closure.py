"""`dropped` as the TERMINAL NON-SUCCESS outcome: the reason it owes, the
archive it earns, and the Done it must never become.

Run: python backend/tests/test_work_closure.py

The problem this pins, as it stood before: `dropped` existed and closed an item,
but only `done` was ever swept into the archive, so cancelled and unrecoverably
failed work sat on the main docket list for good; nothing recorded WHY the work
ended; and the doctrine never named the status at all, which left "walk it
through review and accept it" as the only way to get dead work off the list —
a completion written into the record that never happened.

Everything runs through the real ledger and the real route table on a throwaway
data root. No clock is waited on: the hour is crossed by backdating `docket_at`,
which is the same instrument test_work_items uses for the done edge.

CONTROLS, because a check that cannot fail is not a check: a `done` item must
still archive on the same clock, a `dropped` item under the hour must NOT, a
`superseded` item must still never archive by itself (that exemption is
deliberate — see §2), and an item dropped BEFORE this rule existed must stay
reopenable without anyone inventing a reason for it.

THE RENDERING IS COVERED ELSEWHERE, not here: frontend/tests/docket.test.tsx
§36 (extended) proves the pane picks the reason by STATUS with all three
fields planted, and §38 proves the row reads as an outcome that is not Done.
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = tempfile.mkdtemp(prefix="orgtree-work-closure-")
os.environ["ORGTREE_DATA"] = ROOT
os.environ["HOME"] = os.path.join(ROOT, "home")
os.environ["USERPROFILE"] = os.path.join(ROOT, "home")
os.environ["ORGTREE_PORT"] = "7431"
os.makedirs(os.environ["HOME"], exist_ok=True)
with open(os.path.join(ROOT, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from datetime import datetime, timedelta, timezone                 # noqa: E402

from orgtree import store                                          # noqa: E402
from orgtree.ledger import LedgerError, USER                       # noqa: E402

# ⚠ ASSERT the root rather than trusting the assignment above: `store` binds
# DATA_ROOT at import time, and a stray earlier import would have bound it to
# the operator's live tree instead.
assert os.path.abspath(str(store.DATA_ROOT)) == os.path.abspath(ROOT), \
    f"store bound {store.DATA_ROOT}, not the throwaway root {ROOT}"

PASS = FAIL = 0
WHY_CANCELLED = ("CANCELLED by the user 2026-09-05: the export format was "
                 "dropped from the product; worth resuming only if it returns")
WHY_FAILED = ("FAILED UNRECOVERABLY: the vendor retired the API this was "
              "built on and published no replacement")


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


def fixture() -> str:
    _N[0] += 1
    org = store.create_org(f"zz-closure-{_N[0]:03d}")
    org.hire(USER, None, "haiku", 0, "agent")
    store.save_org(org)
    return str(org.d["slug"])


def do(slug: str, fn):
    with store.DOC_LOCK:
        org = store.load_org(slug)
        out = fn(org)
        store.save_org(org)
    return out


def item(slug: str, title: str = "an item", *, status: str = "open", **kw) -> str:
    return str(do(slug, lambda org: org.work_create(
        USER, title, objective="the problem; then the proposal",
        owner="agent", status=status, **kw))["slug"])


def upd(slug: str, wid: str, **kw):
    return do(slug, lambda org: org.work_update(
        USER, wid, kw.pop("done", ["a step"]), kw.pop("next", []), **kw))


def view(slug: str, wid: str) -> dict:
    return store.load_org(slug).work_get(USER, wid)


def raw(slug: str, wid: str) -> dict:
    it, _ = store.load_org(slug)._work_find(wid)
    return it


def refused(fn) -> str:
    try:
        fn()
    except LedgerError as e:
        return str(e)
    raise AssertionError("that was accepted; it had to be refused")


def backdate(slug: str, wid: str, seconds: int) -> float:
    """Push the item's docket clock into the past. Returns the `now` the item
    has been aged against."""
    with store.DOC_LOCK:
        org = store.load_org(slug)
        it, _ = org._work_find(wid)
        dt = datetime.now(timezone.utc) - timedelta(seconds=seconds)
        it["docket_at"] = dt.isoformat()
        store.save_org(org)
    return dt.timestamp() + seconds


OLD = "2020-01-01T00:00:00.000Z"


def stamp(slug: str, wid: str, at: str) -> None:
    """Plant a known status clock. Read back by identity rather than by
    comparing two `now()` calls, which can land in the same millisecond."""
    with store.DOC_LOCK:
        org = store.load_org(slug)
        it, _ = org._work_find(wid)
        it["status_at"] = at
        store.save_org(org)


def plant_legacy_drop(slug: str, wid: str) -> None:
    """An item dropped BEFORE the reason was required: closed, with no reason
    field at all. Written straight onto the stored item because no verb can
    produce this shape any more — which is exactly why it must be tested."""
    with store.DOC_LOCK:
        org = store.load_org(slug)
        it, _ = org._work_find(wid)
        it["status"] = "dropped"
        it.pop("dropped_reason", None)
        store.save_org(org)


print("\n§1  a drop has to say why")


def dropping_needs_a_reason() -> None:
    slug = fixture()
    wid = item(slug, "Doomed", status="in_progress")
    msg = refused(lambda: upd(slug, wid, status="dropped"))
    assert "dropped_reason" in msg and "CANCELLED" in msg, msg
    # AND THE REFUSAL WROTE NOTHING — not the status, not the lists
    it = raw(slug, wid)
    assert it["status"] == "in_progress", it["status"]
    assert it.get("dropped_reason") in (None, ""), it


check("ending an item as dropped without a reason is refused, and the refusal "
      "leaves the item exactly where it was", dropping_needs_a_reason)


def a_blank_reason_is_refused() -> None:
    slug = fixture()
    wid = item(slug, "Doomed", status="in_progress")
    msg = refused(lambda: upd(slug, wid, status="dropped", dropped_reason="   "))
    assert "blank" in msg, msg
    assert raw(slug, wid)["status"] == "in_progress"


check("a blank dropped_reason is refused rather than accepted as an answer",
      a_blank_reason_is_refused)


def the_reason_is_stored_and_served() -> None:
    slug = fixture()
    wid = item(slug, "Doomed", status="in_progress")
    upd(slug, wid, status="dropped", dropped_reason=WHY_FAILED)
    v = view(slug, wid)
    assert v["status"] == "dropped", v["status"]
    assert v["dropped_reason"] == WHY_FAILED, v.get("dropped_reason")
    # and the transition is a real one the history can be read for
    rows = [h for h in v["history"] if (h.get("changes") or {}).get("status")]
    assert rows[-1]["changes"]["status"] == {"from": "in_progress",
                                             "to": "dropped"}, rows[-1]


check("a drop stores its reason, serves it on the item, and records the "
      "transition", the_reason_is_stored_and_served)


def the_outcome_outlives_the_field() -> None:
    """The live field is cleared when the item leaves the state — so if the
    history did not keep the reason, reopening would erase the only record
    that the work was ever ended, and why."""
    slug = fixture()
    wid = item(slug, "Doomed", status="in_progress")
    upd(slug, wid, status="dropped", dropped_reason=WHY_CANCELLED)
    assert raw(slug, wid)["dropped_reason"] == WHY_CANCELLED   # not vacuous
    drops = [h for h in view(slug, wid)["history"]
             if (h.get("changes") or {}).get("dropped_reason")]
    assert drops and drops[-1]["changes"]["dropped_reason"] == WHY_CANCELLED, \
        "the drop itself did not record its reason in history"
    upd(slug, wid, reopen=True, status="in_progress")
    assert raw(slug, wid).get("dropped_reason") in (None, ""), \
        "the reason survived the state it describes"
    hist = view(slug, wid)["history"]
    reopens = [h for h in hist if h.get("op") == "reopen"]
    assert reopens and reopens[-1]["dropped_reason_was"] == WHY_CANCELLED, \
        "reopening threw the reason away instead of keeping it in history"


check("the reason is cleared when the item leaves dropped, and BOTH the drop "
      "and the reopen keep it in history", the_outcome_outlives_the_field)


def legacy_drops_stay_reopenable() -> None:
    """CONTROL for the requirement that nothing is demanded retroactively and
    no reason is invented for an item that never had one."""
    slug = fixture()
    wid = item(slug, "Dropped long ago", status="in_progress")
    plant_legacy_drop(slug, wid)
    assert "dropped_reason" not in raw(slug, wid)
    upd(slug, wid, reopen=True, status="in_progress", done=["picked it back up"])
    v = view(slug, wid)
    assert v["status"] == "in_progress", v["status"]
    assert v["dropped_reason"] in (None, ""), "a reason was invented for it"
    reopens = [h for h in v["history"] if h.get("op") == "reopen"]
    assert reopens[-1]["dropped_reason_was"] is None, reopens[-1]


check("an item dropped before the rule existed is still reopenable, with no "
      "reason demanded and none invented", legacy_drops_stay_reopenable)


def superseding_a_dropped_item_clears_the_reason() -> None:
    slug = fixture()
    dead = item(slug, "Dead", status="in_progress")
    live = item(slug, "Its replacement", status="in_progress")
    upd(slug, dead, status="dropped", dropped_reason=WHY_FAILED)
    assert raw(slug, dead)["dropped_reason"] == WHY_FAILED     # not vacuous
    do(slug, lambda org: org.work_supersede(USER, dead, live))
    v = view(slug, dead)
    assert v["status"] == "superseded" and v["superseded_by"] == live, v
    assert v["dropped_reason"] in (None, ""), \
        "a superseded item still reads as if it had been abandoned"


check("superseding a dropped item clears the drop reason with the state it "
      "described", superseding_a_dropped_item_clears_the_reason)


def dismissing_a_flag_on_a_dropped_item_clears_the_reason() -> None:
    slug = fixture()
    wid = item(slug, "Doomed but flagged", status="in_progress")
    upd(slug, wid, status="dropped", dropped_reason=WHY_CANCELLED,
        attention=True, attention_reason="I ended this; confirm that is right")
    rev = int(raw(slug, wid)["manual_attention"]["set_rev"])
    assert raw(slug, wid)["dropped_reason"] == WHY_CANCELLED   # not vacuous
    do(slug, lambda org: org.work_dismiss_attention(wid, rev))
    it = raw(slug, wid)
    assert it["status"] == "blocked", it["status"]
    assert it.get("dropped_reason") in (None, ""), \
        "the drop reason outlived the drop"
    assert "dismissed" in (it.get("blocked_reason") or ""), it.get("blocked_reason")


check("the user's dismissal takes an item out of dropped and takes the drop "
      "reason with it", dismissing_a_flag_on_a_dropped_item_clears_the_reason)


def a_review_sending_work_back_clears_the_state_it_left() -> None:
    """INTEGRATION, found at the rebase onto the staffing branch.
    `work_review_decide('changes')` sets `in_progress` directly and cleared
    `blocked_reason` BY NAME. It does not require the item to be at `review`,
    so a superior may send back an item that is `waiting` — and a field
    cleared by name stops being cleared the moment the map grows. It now goes
    through the shared clear."""
    slug = fixture()
    org = store.load_org(slug)
    org.hire(USER, "agent", "haiku", 0, "junior")
    store.save_org(org)
    wid = item(slug, "Waits on a build", status="in_progress")
    do(slug, lambda o: o.work_assign(USER, wid, "junior"))
    upd(slug, wid, status="waiting",
        waiting_reason="the nightly build finishes; the watchdog mails me")
    assert raw(slug, wid)["waiting_reason"], "fixture planted nothing"  # not vacuous
    stamp(slug, wid, OLD)                       # a status clock we can read
    do(slug, lambda o: o.work_review_decide(USER, wid, "changes", "redo it"))
    it = raw(slug, wid)
    assert it["status"] == "in_progress", it["status"]
    assert it.get("waiting_reason") in (None, ""), \
        "the item left `waiting` still carrying why it was waiting"
    # THE SAME MOVE IS ALSO A STATUS CHANGE, so it belongs in the status clock
    assert it["status_at"] != OLD, \
        "a sendback changed the status without stamping the status clock"
    # CONTROL 1: a sendback that does NOT move the value must not restamp, or
    # "most recently changed state" quietly becomes "most recently touched"
    stamp(slug, wid, OLD)
    do(slug, lambda o: o.work_review_decide(USER, wid, "changes", "still no"))
    assert raw(slug, wid)["status_at"] == OLD, \
        "in_progress was reassigned over in_progress and restamped anyway"
    # CONTROL 2: the field that was always cleared here is still cleared
    upd(slug, wid, status="blocked", blocked_reason="the vendor has not replied")
    assert raw(slug, wid)["blocked_reason"]
    do(slug, lambda o: o.work_review_decide(USER, wid, "changes", "again"))
    assert raw(slug, wid).get("blocked_reason") in (None, "")


check("a review decision that sends work back clears EVERY reason, not just "
      "the one it was written beside",
      a_review_sending_work_back_clears_the_state_it_left)


def the_map_is_the_single_source_of_truth() -> None:
    """The failure this exists to stop is SILENT: a verb that clears the two
    fields it was written beside and not the third leaves an item reading
    "ended because…" while sitting in some other state, and nothing fails."""
    org = store.load_org(fixture())
    fields = set(org.WORK_STATE_INFO.values())
    assert "dropped_reason" in fields, org.WORK_STATE_INFO
    missing = fields - set(org.WORK_STATE_INFO_ASKS)
    assert not missing, f"no sentence tells the writer what to say: {missing}"
    unknown = set(org.WORK_STATE_INFO) - set(org.WORK_STATUSES)
    assert not unknown, f"state information for statuses that do not exist: {unknown}"
    planted = {f: "stale" for f in fields}
    it = dict(planted)
    org._work_clear_state_info(it)               # type: ignore[arg-type]
    left = {k: v for k, v in it.items() if v is not None}
    assert not left, f"the clear left {left} standing"


check("every state-information field has a sentence, names a real status, and "
      "is cleared by the shared clear", the_map_is_the_single_source_of_truth)


print("\n§2  it archives itself, the way done does")


def a_dropped_item_ages_out() -> None:
    slug = fixture()
    wid = item(slug, "Doomed", status="in_progress")
    upd(slug, wid, status="dropped", dropped_reason=WHY_CANCELLED)
    at = backdate(slug, wid, 3601)
    lst = store.load_org(slug).work_list(USER, include_archived=True, now_ts=at)
    assert lst["items"] == [], lst["items"]
    assert [r["slug"] for r in lst["archived"]] == [wid], lst["archived"]
    assert lst["counts"]["archived"] == 1 and lst["counts"]["active"] == 0, lst["counts"]
    # the derived read did NOT write, and the next mutation sweeps it for real
    assert store.load_org(slug).d.get("work_items_archive") in (None, []), \
        "a read moved the item"
    item(slug, "an unrelated write")
    org = store.load_org(slug)
    _, phys = org._work_find(wid)
    assert phys, "the sweep at the head of a mutation left it in the active list"
    assert org.work_get(USER, wid)["dropped_reason"] == WHY_CANCELLED, \
        "the archived row lost the reason it was archived with"


check("a dropped item over an hour old archives itself, and the archived row "
      "still carries its reason", a_dropped_item_ages_out)


def the_log_does_not_call_it_done() -> None:
    """The sweep writes a DURABLE org-log row. It used to assert one reason
    for the whole batch — "done for over an hour" — which was true while only
    done archived itself and becomes a lie about work that was cancelled or
    failed. Raised by checklist-evidence's review of cd9d637."""
    slug = fixture()
    dead = item(slug, "Ended", status="in_progress")
    fine = item(slug, "Finished", status="review")
    upd(slug, dead, status="dropped", dropped_reason=WHY_CANCELLED)
    do(slug, lambda org: org.work_accept(USER, fine))
    at = max(backdate(slug, dead, 3601), backdate(slug, fine, 3601))
    with store.DOC_LOCK:
        org = store.load_org(slug)
        org._work_sweep(at)
        store.save_org(org)
    rows = [r for r in (store.load_org(slug).d.get("events") or [])
            if r.get("op") == "work_archived"]
    assert rows, "the sweep archived items and logged nothing"
    d = rows[-1]["detail"]
    assert sorted(d["items"]) == sorted([dead, fine]), d
    # the CONTROL is in the same row: the accepted item still reads `done`, so
    # this is about telling the two apart and not about erasing the word
    assert d["outcomes"][dead] == "dropped", d["outcomes"]
    assert d["outcomes"][fine] == "done", d["outcomes"]
    assert "done for over an hour" not in str(d), d


check("the durable archive log records each item's OWN outcome, so cancelled "
      "work is never logged as done", the_log_does_not_call_it_done)


def the_refusal_does_not_call_it_done_either() -> None:
    """The SAME defect as the log line, one line away and lower stakes: the
    archived-item refusal used to say "(done for over an hour)" whatever the
    item's outcome was. It is a claim the product makes to an agent about work
    that was never completed. Caught unpinned by checklist-evidence on
    0020428 — the only mutant of four that survived."""
    slug = fixture()
    dead = item(slug, "Ended", status="in_progress")
    fine = item(slug, "Finished", status="review")
    upd(slug, dead, status="dropped", dropped_reason=WHY_FAILED)
    do(slug, lambda org: org.work_accept(USER, fine))
    backdate(slug, dead, 3601)
    backdate(slug, fine, 3601)
    msg = refused(lambda: upd(slug, dead, done=["picking it back up"]))
    assert "ARCHIVED (dropped for over an hour)" in msg, msg
    # the CONTROL: the accepted item is told `done`, so this is about naming
    # the outcome and not about deleting the word from the message
    ctrl = refused(lambda: upd(slug, fine, done=["more"]))
    assert "ARCHIVED (done for over an hour)" in ctrl, ctrl


check("the archived-item refusal names the item's OWN outcome, so a cancelled "
      "item is never told it is done", the_refusal_does_not_call_it_done_either)


def the_hour_edge_holds_for_dropped() -> None:
    """CONTROL: the archive is earned by the CLOCK, not by the status alone —
    without this, 'dropped archives' could be true the instant it is set."""
    slug = fixture()
    wid = item(slug, "Doomed", status="in_progress")
    upd(slug, wid, status="dropped", dropped_reason=WHY_CANCELLED)
    at = backdate(slug, wid, 3600)
    lst = store.load_org(slug).work_list(USER, include_archived=True, now_ts=at)
    assert [r["slug"] for r in lst["items"]] == [wid], "archived at exactly an hour"
    assert lst["archived"] == [] and lst["counts"]["archived"] == 0, lst["counts"]


check("at EXACTLY one hour a dropped item is still on the list — the same "
      "strict edge done has", the_hour_edge_holds_for_dropped)


def done_still_archives() -> None:
    """CONTROL: the change must not have traded one status for the other."""
    slug = fixture()
    wid = item(slug, "Finished", status="review")
    do(slug, lambda org: org.work_accept(USER, wid))
    at = backdate(slug, wid, 3601)
    lst = store.load_org(slug).work_list(USER, include_archived=True, now_ts=at)
    assert [r["slug"] for r in lst["archived"]] == [wid], lst
    assert lst["archived"][0]["status"] == "done", lst["archived"][0]["status"]


check("CONTROL: an accepted item still archives on the same clock",
      done_still_archives)


def superseded_still_never_ages_out() -> None:
    """CONTROL, and an attack on this change's own exemption: `superseded` was
    deliberately left out of the sweep, so that has to be visible rather than
    assumed. If a later change sweeps it too, this check says so out loud."""
    slug = fixture()
    dead = item(slug, "Replaced", status="in_progress")
    live = item(slug, "The replacement", status="in_progress")
    do(slug, lambda org: org.work_supersede(USER, dead, live))
    at = backdate(slug, dead, 360000)              # a hundred hours
    lst = store.load_org(slug).work_list(USER, include_archived=True, now_ts=at)
    assert dead in [r["slug"] for r in lst["items"]], \
        "superseded started archiving itself; that was not this change"
    assert lst["archived"] == [], lst["archived"]


check("CONTROL: a superseded item still never archives by itself, however old",
      superseded_still_never_ages_out)


def attention_keeps_a_dropped_item_visible() -> None:
    slug = fixture()
    quiet = item(slug, "Ended quietly", status="in_progress")
    loud = item(slug, "Ended with a flag", status="in_progress")
    upd(slug, quiet, status="dropped", dropped_reason=WHY_CANCELLED)
    upd(slug, loud, status="dropped", dropped_reason=WHY_FAILED,
        attention=True, attention_reason="I ended this; confirm that is right")
    at = max(backdate(slug, quiet, 3601), backdate(slug, loud, 3601))
    lst = store.load_org(slug).work_list(USER, include_archived=True, now_ts=at)
    assert [r["slug"] for r in lst["archived"]] == [quiet], lst["archived"]
    assert [r["slug"] for r in lst["items"]] == [loud], lst["items"]


check("a dropped item holding an attention flag stays visible while its "
      "unflagged twin archives", attention_keeps_a_dropped_item_visible)


print("\n§3  dropped is never Done")


def a_dropped_item_cannot_be_accepted() -> None:
    slug = fixture()
    wid = item(slug, "Doomed", status="in_progress")
    upd(slug, wid, status="dropped", dropped_reason=WHY_FAILED)
    msg = refused(lambda: do(slug, lambda org: org.work_accept(USER, wid)))
    assert "already dropped" in msg, msg
    at = backdate(slug, wid, 3601)
    lst = store.load_org(slug).work_list(USER, include_archived=True, now_ts=at)
    assert lst["archived"][0]["status"] == "dropped", \
        "archiving turned a non-success outcome into something else"
    assert lst["archived"][0]["accepted"] is None, lst["archived"][0]["accepted"]


check("a dropped item cannot be accepted, and archiving never relabels it as "
      "done", a_dropped_item_cannot_be_accepted)


print("\n§4  the surfaces an agent actually reads")


def the_tool_card_offers_the_reason() -> None:
    from orgtree import mcptool
    card = next(c for c in mcptool.TOOLS if c["name"] == "orgtree_work")
    props = card["inputSchema"]["properties"]
    assert "dropped_reason" in props, sorted(props)
    d = props["dropped_reason"]["description"]
    assert "REQUIRED" in d and "CANCELLED" in d and "UNRECOVERABLY" in d, d
    assert "dropped_reason" in card["description"], \
        "the tool's own prose never names the field"
    assert "never Done" in props["status"]["description"], \
        "the status list does not say dropped is not a completion"


check("the orgtree_work card offers dropped_reason and says what it is for",
      the_tool_card_offers_the_reason)


def the_doctrine_names_the_outcome() -> None:
    from orgtree import supervisor
    t = supervisor.DOCKET_DOCTRINE
    assert "dropped" in t.split("(4)")[1].split("(5)")[0], \
        "clause (4) still lists the honest statuses without dropped"
    assert "TERMINAL NON-SUCCESS" in t and "dropped_reason" in t, t[:0]
    assert "NEVER Done" in t, "the doctrine never says dropped is not done"


check("DOCKET_DOCTRINE names dropped as the terminal non-success outcome",
      the_doctrine_names_the_outcome)


def the_route_passes_the_reason() -> None:
    """The MCP tool and the UI both arrive through this one function; a field
    the ledger accepts but the route drops is invisible from outside."""
    from orgtree.api import _work_mutate_action
    slug = fixture()
    wid = item(slug, "Doomed", status="in_progress")
    with store.DOC_LOCK:
        org = store.load_org(slug)
        _work_mutate_action(org, USER, {"action": "update", "slug": wid,
                                        "status": "dropped",
                                        "dropped_reason": WHY_CANCELLED,
                                        "done_so_far": ["a step"],
                                        "working_on_next": []}, "update", wid)
        store.save_org(org)
    assert view(slug, wid)["dropped_reason"] == WHY_CANCELLED
    # and the same call WITHOUT the field is refused through the route too
    wid2 = item(slug, "Also doomed", status="in_progress")
    with store.DOC_LOCK:
        org = store.load_org(slug)
        msg = refused(lambda: _work_mutate_action(
            org, USER, {"action": "update", "slug": wid2, "status": "dropped",
                        "done_so_far": ["a step"], "working_on_next": []},
            "update", wid2))
    assert "dropped_reason" in msg, msg


check("the work route carries dropped_reason through, and refuses a drop "
      "without one", the_route_passes_the_reason)


print(f"\nALL {PASS} CHECKS PASS" if not FAIL else f"\n{FAIL} FAILED, {PASS} PASSED")
sys.exit(1 if FAIL else 0)
