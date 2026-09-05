"""The `waiting` docket state, the information blocked and waiting owe, and
the next-action recipient resolver.

Run: python backend/tests/test_docket_waiting_state.py

Everything here is driven through the real ledger on a throwaway data root —
no scheduler, no provider, no clock to wait on. What is pinned: that `waiting`
is ACTIVE work and not a second backlog; that entering blocked or waiting
requires its own field while staying in the state does not; that a blank string
is refused rather than erasing what is recorded; that a refused transition
writes NOTHING; that the field is cleared on every way out of the state; and
that the recipient of an item is the reviewer while it is under review, the
owner otherwise, with the exclusions decided per item BEFORE the recipient is
asked for.

The `reviewer` field is codex-sandbox's and no verb here writes it yet, so the
fixtures plant it directly on the stored item — which is exactly the shape the
resolver reads.
"""
from __future__ import annotations

import os
import sys
import tempfile

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


print("\n§1  waiting is ACTIVE work, not a second backlog")


def waiting_is_active() -> None:
    slug = fixture()
    wid = item(slug, "Waits on a build")
    upd(slug, wid, status="waiting", waiting_reason=EVENT)
    back = item(slug, "Nobody has started this", status="backlogged")
    counts = store.load_org(slug).work_counts()
    assert counts["active"] == 1, counts        # the waiting item, not the backlog
    assert counts["backlogged"] == 1, counts
    lst = store.load_org(slug).work_list(USER, include_backlogged=True,
                                         include_archived=True)
    assert [r["slug"] for r in lst["items"]] == [wid], lst["items"]
    assert [r["slug"] for r in lst["backlogged"]] == [back], lst["backlogged"]
    assert not lst["archived"], lst["archived"]
    v = view(slug, wid)
    assert v["status"] == "waiting" and v["waiting_reason"] == EVENT, v


check("a waiting item counts as active and stays in the main list "
      "(a backlogged one does neither)", waiting_is_active)


def waiting_is_a_real_transition() -> None:
    """codex-checklist's status clock counts status TRANSITIONS: if the move
    into waiting does not record one, their sort silently misplaces the row."""
    slug = fixture()
    wid = item(slug, "Waits on a build", status="in_progress")
    upd(slug, wid, status="waiting", waiting_reason=EVENT)
    rows = [h for h in view(slug, wid)["history"]
            if (h.get("changes") or {}).get("status")]
    assert rows and rows[-1]["changes"]["status"] == {
        "from": "in_progress", "to": "waiting"}, rows


check("moving to waiting records a real status transition in the history",
      waiting_is_a_real_transition)


print("\n§2  the information blocked and waiting owe")


def entering_needs_the_field() -> None:
    slug = fixture()
    wid = item(slug, "Waits on a build", status="in_progress")
    msg = refused(lambda: upd(slug, wid, status="waiting"))
    assert "waiting_reason" in msg and "how you will learn" in msg, msg
    # REFUSED MEANS NOTHING WAS WRITTEN — not the status, not the lists
    v = view(slug, wid)
    assert v["status"] == "in_progress" and not v["waiting_reason"], v
    assert v["done_so_far"] == [], v["done_so_far"]
    msg = refused(lambda: upd(slug, wid, status="blocked"))
    assert "blocked_reason" in msg and "who can act" in msg, msg
    assert view(slug, wid)["status"] == "in_progress"
    # POSITIVE CONTROL: the same move with the field is accepted
    upd(slug, wid, status="waiting", waiting_reason=EVENT)
    assert view(slug, wid)["waiting_reason"] == EVENT


check("entering waiting or blocked without its field is refused, and writes "
      "nothing (control: with the field it goes through)",
      entering_needs_the_field)


def other_states_owe_nothing() -> None:
    slug = fixture()
    wid = item(slug, "Ordinary work")
    for st in ("open", "in_progress", "review", "backlogged"):
        upd(slug, wid, status=st)
        assert view(slug, wid)["status"] == st


check("no other status requires state information", other_states_owe_nothing)


def blank_does_not_erase() -> None:
    slug = fixture()
    wid = item(slug, "Waits on a build")
    upd(slug, wid, status="waiting", waiting_reason=EVENT)
    for blank in ("", "   ", "\n"):
        msg = refused(lambda b=blank: upd(slug, wid, waiting_reason=b))
        assert "blank" in msg and "does not erase" in msg, msg
        assert view(slug, wid)["waiting_reason"] == EVENT, "it was erased"
    wid2 = item(slug, "Stuck")
    upd(slug, wid2, status="blocked", blocked_reason=BLOCK)
    refused(lambda: upd(slug, wid2, blocked_reason=" "))
    assert view(slug, wid2)["blocked_reason"] == BLOCK


check("a blank reason is refused and leaves the recorded one standing",
      blank_does_not_erase)


def staying_may_omit_it() -> None:
    slug = fixture()
    wid = item(slug, "Waits on a build")
    upd(slug, wid, status="waiting", waiting_reason=EVENT)
    upd(slug, wid, done=["progress note"])          # no status, no reason
    v = view(slug, wid)
    assert v["status"] == "waiting" and v["waiting_reason"] == EVENT, v
    upd(slug, wid, status="waiting", done=["restated"])   # same status again
    assert view(slug, wid)["waiting_reason"] == EVENT
    # and a NEW value replaces it
    upd(slug, wid, waiting_reason="the deploy lands; astra mails me")
    assert view(slug, wid)["waiting_reason"] == "the deploy lands; astra mails me"


check("an item already in the state may be updated without restating its "
      "reason, and a new value replaces it", staying_may_omit_it)


def legacy_items_stay_editable() -> None:
    """An item blocked BEFORE the requirement existed carries no reason. It
    must not become un-updatable — the check is on the transition."""
    slug = fixture()
    wid = item(slug, "Blocked long ago")
    do(slug, lambda org: org._work_find(wid)[0].update(
        {"status": "blocked", "blocked_reason": None}))
    assert view(slug, wid)["blocked_reason"] is None, "fixture must be reasonless"
    upd(slug, wid, done=["still stuck"])
    v = view(slug, wid)
    assert v["status"] == "blocked" and v["blocked_reason"] is None, v
    assert v["done_so_far"] == ["still stuck"], v


check("a legacy blocked item with no reason is still updatable",
      legacy_items_stay_editable)


def leaving_clears_it() -> None:
    slug = fixture()
    wid = item(slug, "Waits on a build")
    upd(slug, wid, status="waiting", waiting_reason=EVENT)
    upd(slug, wid, status="in_progress")
    assert view(slug, wid)["waiting_reason"] is None
    upd(slug, wid, status="blocked", blocked_reason=BLOCK)
    v = view(slug, wid)
    assert v["blocked_reason"] == BLOCK and v["waiting_reason"] is None, v
    # blocked -> waiting swaps which field is set; neither survives the other
    upd(slug, wid, status="waiting", waiting_reason=EVENT)
    v = view(slug, wid)
    assert v["waiting_reason"] == EVENT and v["blocked_reason"] is None, v


check("a reason never survives the state it describes", leaving_clears_it)


def create_obeys_the_same_rule() -> None:
    slug = fixture()
    msg = refused(lambda: item(slug, "Born waiting", status="waiting"))
    assert "waiting_reason" in msg, msg
    refused(lambda: item(slug, "Born blocked", status="blocked"))
    # NOTHING STRANDED: the refusals left no item behind
    assert not (store.load_org(slug).d.get("work_items") or []), \
        "a refused create left an item behind"
    wid = item(slug, "Born waiting", status="waiting", waiting_reason=EVENT)
    assert view(slug, wid)["waiting_reason"] == EVENT
    assert "waiting" in refused(
        lambda: item(slug, "Born done", status="done"))


check("create refuses waiting/blocked without the field and strands nothing",
      create_obeys_the_same_rule)


def closing_clears_it() -> None:
    slug = fixture()
    wid = item(slug, "Waits on a build")
    upd(slug, wid, status="waiting", waiting_reason=EVENT)
    do(slug, lambda org: org.work_accept(USER, wid))
    v = view(slug, wid)
    assert v["status"] == "done" and v["waiting_reason"] is None, v
    other = item(slug, "The replacement")
    wid2 = item(slug, "Also waiting")
    upd(slug, wid2, status="waiting", waiting_reason=EVENT)
    do(slug, lambda org: org.work_supersede(USER, wid2, other))
    v = view(slug, wid2)
    assert v["status"] == "superseded" and v["waiting_reason"] is None, v


check("accept and supersede clear the state information too", closing_clears_it)


def the_users_dismissal_still_works() -> None:
    """The system's OWN transition into blocked carries its own real reason
    and must never fail for want of agent input."""
    slug = fixture()
    wid = item(slug, "Waits on a build")
    upd(slug, wid, status="waiting", waiting_reason=EVENT,
        attention=True, attention_reason="confirm the extra switch I added")
    rev = view(slug, wid)["manual_attention"]["set_rev"]
    r = do(slug, lambda org: org.work_dismiss_attention(wid, rev))
    assert r["status"] == "blocked", r
    v = view(slug, wid)
    assert v["status"] == "blocked", v
    assert "dismissed by the user" in (v["blocked_reason"] or ""), v
    assert v["waiting_reason"] is None, "the waiting reason outlived waiting"


check("the user's dismissal still blocks the item with its own reason",
      the_users_dismissal_still_works)


def it_survives_a_reload() -> None:
    slug = fixture()
    wid = item(slug, "Waits on a build")
    upd(slug, wid, status="waiting", waiting_reason=EVENT)
    store._CACHE.clear() if hasattr(store, "_CACHE") else None
    raw = store.load_org(slug)._work_find(wid)[0]
    assert raw["status"] == "waiting" and raw["waiting_reason"] == EVENT, raw
    v = store.load_org(slug).work_get(USER, wid)
    assert v["waiting_reason"] == EVENT, v


check("the state and its reason round-trip through storage", it_survives_a_reload)


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


print("\n§4  exclusions are per item, before anyone is grouped")


def waiting_excludes_only_itself() -> None:
    slug = fixture(peers=("peer",))
    waits = item(slug, "Waits on a build")
    upd(slug, waits, status="waiting", waiting_reason=EVENT)
    live = item(slug, "Still moving", status="in_progress")
    org = store.load_org(slug)
    rows = org.work_idle_reminder_items("agent")
    assert [r["slug"] for r in rows] == [live], rows
    # CONTROL: the waiting item comes back the moment its event happens
    upd(slug, waits, status="in_progress")
    assert sorted(r["slug"] for r in
                  store.load_org(slug).work_idle_reminder_items("agent")) \
        == sorted([live, waits])


check("a waiting item removes itself and nothing else (control: it returns "
      "when the state changes)", waiting_excludes_only_itself)


def a_waiting_review_is_excluded_from_the_reviewer_too() -> None:
    """The exclusion is decided on the ITEM, before the recipient is asked
    for — so it holds for a reviewer exactly as it does for an owner."""
    slug = fixture(peers=("peer",))
    wid = item(slug, "Under review", status="review")
    name_reviewer(slug, wid, "peer")
    assert store.load_org(slug).work_idle_reminder_items("peer"), "control"
    upd(slug, wid, status="waiting", waiting_reason=EVENT)
    org = store.load_org(slug)
    assert org.work_idle_reminder_items("peer") == []
    assert org.work_idle_reminder_items("agent") == []


check("a waiting item is excluded from its reviewer as well as its owner",
      a_waiting_review_is_excluded_from_the_reviewer_too)


def attention_still_excludes_before_grouping() -> None:
    slug = fixture(peers=("peer",))
    wid = item(slug, "Under review", status="review")
    name_reviewer(slug, wid, "peer")
    assert store.load_org(slug).work_idle_reminder_items("peer"), "control"
    upd(slug, wid, status="review", attention=True,
        attention_reason="the user must pick the export format")
    org = store.load_org(slug)
    assert org.work_idle_reminder_items("peer") == [], \
        "an item waiting on the user reached its reviewer"
    assert org.work_idle_reminder_items("agent") == []


check("an attention-holding review item reaches nobody, reviewer included",
      attention_still_excludes_before_grouping)


def one_agent_one_list() -> None:
    slug = fixture(peers=("peer",))
    mine = item(slug, "My own work", status="in_progress")
    theirs = item(slug, "Their work, my review", owner="peer", status="review")
    name_reviewer(slug, theirs, "agent")
    hidden = item(slug, "Their work, their problem", owner="peer",
                  status="in_progress")
    rows = store.load_org(slug).work_idle_reminder_items("agent")
    assert sorted((r["slug"], r["role"]) for r in rows) == sorted(
        [(mine, "owner"), (theirs, "reviewer")]), rows
    assert hidden not in [r["slug"] for r in rows], rows


check("own work and somebody else's review arrive in ONE list, each row "
      "saying which it is", one_agent_one_list)


print(f"\n{FAIL} FAILED, {PASS} PASSED")
sys.exit(1 if FAIL else 0)
