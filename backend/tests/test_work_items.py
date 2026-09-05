"""The docket — durable work items (docs/work-items.md, docket-final-spec.md).

Every check here drives the INSTALLED routes — `/api/orgs/{slug}/work-items*`
for the user surface and `/api/agent` for `orgtree_work` / `orgtree_ask` —
through the FastAPI test client, and reads results back through the same
routes or a reload from the store. Helper dictionaries are never asserted on
their own shape. `supervisor.send_message` is stubbed to RECORD (nothing here
may launch a model); everything else is the shipped code.

Sections:
    §1  storage — old documents, round trip, tree summary
    §2  authority — owner/ancestor/creator/participant/user, hidden ids
    §3  the status update — both lists, both-empty refused, done via accept
    §4  archive — the exact-hour edge, derived vs physical, attention holds
    §5  questions — two askers on one item, answer one, withdraw, refusal
    §6  manual attention — set/clear/dismiss CAS/blocked/exact repeat
    §7  reply routing — last updater, exactly; failures are explicit
    §8  delivery — claim/verify three-valued, rev revalidation
    §9  caps — evidence refusal, history fold, active cap
    §10 the standing instructions reach the identity prompt (every lane)

    python backend/tests/test_work_items.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
_TMP = tempfile.mkdtemp(prefix="orgtree-workitems-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.environ.pop("ORGTREE_WARM", None)
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address":"http://127.0.0.1:9"}')
os.environ["USERPROFILE"] = os.environ["HOME"] = os.path.join(_TMP, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient                    # noqa: E402
from orgtree import api, mcptool, store, supervisor, workitems   # noqa: E402
from orgtree.ledger import LedgerError, USER                  # noqa: E402

assert store.DATA_ROOT.startswith(_TMP), store.DATA_ROOT   # bound to the throwaway root

DRIVEN: list[tuple[str, str, str, bool]] = []   # (slug, node, nudge, wake)


def _fake_send(slug, nid, text, command=False, wake=True, **kw):
    DRIVEN.append((slug, nid, text, wake))
    return {"accepted": True, "queued": 0, **({"parked": True} if not wake else {})}


supervisor.send_message = _fake_send
api.supervisor.send_message = _fake_send

client = TestClient(api.app)
PASSED = 0
FAILED: list[str] = []


def check(label, fn) -> None:
    global PASSED
    try:
        fn()
    except Exception:                                        # noqa: BLE001
        FAILED.append(f"{label}\n{traceback.format_exc()}")
        print(f"  x {label}")
    else:
        PASSED += 1
        print(f"  ok {label}")


_n = [0]


def fresh_org():
    """boss (top) > mid > worker; peer (top-level, unrelated); stranger
    (top-level, unrelated). Top-level agents hold the user audience, which is
    what makes an orgtree_ask a CARD rather than mail to a superior."""
    _n[0] += 1
    org = store.create_org(f"docket-{_n[0]}", [])
    org.hire(USER, None, "opus", 60, "boss")
    org.hire(USER, "boss", "haiku", 20, "mid")       # user hires take defaults
    org.hire(USER, "mid", "haiku", 0, "worker")
    org.hire(USER, None, "haiku", 5, "peer")
    org.hire(USER, None, "haiku", 5, "stranger")
    store.save_org(org)
    return org.d["slug"]


def agent(org, node, tool, **args):
    # ⚠ the first parameter is the ORG's slug and the tool argument is the
    # ITEM's slug. They are both called "slug" in the product, so the helper
    # renames its own parameter rather than shadowing the kwarg.
    r = client.post("/api/agent", json={"org": org, "node": node,
                                        "tool": tool, "args": args})
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def work(org, node, action, **args):
    return agent(org, node, "orgtree_work", action=action, **args)


def ok(org, node, action, **args):
    st, js = work(org, node, action, **args)
    assert st == 200, (action, st, js)
    return js


def refused(org, node, action, **args):
    st, js = work(org, node, action, **args)
    assert st == 422, (action, "should have been refused", st, js)
    return str(js.get("detail") if isinstance(js, dict) else js)


def create(org, node="boss", **kw):
    kw.setdefault("title", "ship the thing")
    # the description is MANDATORY since 2026-09-05 (user): problem first,
    # then the proposed solution. §11 proves the guard; every other section
    # only needs a valid one, so it rides the helper.
    kw.setdefault("objective", "the thing is not shipped; ship it")
    kw.setdefault("done_so_far", ["read the spec"])
    kw.setdefault("working_on_next", ["write it"])
    return ok(org, node, "create", **kw)["created"]


def get_item(slug, wid, expect=200):
    r = client.get(f"/api/orgs/{slug}/work-items/{wid}")
    assert r.status_code == expect, (r.status_code, r.text)
    return r.json()["item"] if expect == 200 else r.json()


def listing(slug, archived=False):
    r = client.get(f"/api/orgs/{slug}/work-items" + ("?archived=1" if archived else ""))
    assert r.status_code == 200, r.text
    return r.json()


def backdate(slug, wid, seconds):
    """Push the item's docket clock into the past by `seconds` — the only
    honest way to test the hour edge without sleeping an hour."""
    from datetime import datetime, timedelta, timezone
    org = store.load_org(slug)
    it, _ = org._work_find(wid)
    dt = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    it["docket_at"] = dt.isoformat()
    store.save_org(org)
    return dt.timestamp() + seconds     # "now" as the clock the item was aged against


# ============================================================== §1 storage
print("§1 storage")


def old_doc_reads_as_empty_docket():
    slug = fresh_org()
    org = store.load_org(slug)
    assert "work_items" not in org.d and "work_items_archive" not in org.d, \
        "a fresh doc must not grow docket keys until something writes one"
    js = listing(slug, archived=True)
    assert js["items"] == [] and js["archived"] == []
    assert js["counts"] == {"attention": 0, "active": 0, "archived": 0,
                            "backlogged": 0}
    assert "archived" not in listing(slug), "the archived group is served only with ?archived=1"
    tree = client.get(f"/api/orgs/{slug}").json()
    assert tree["work_items_summary"] == {"attention": 0, "active": 0}, tree.get("work_items_summary")
    # a READ never wrote the keys
    assert "work_items" not in store.load_org(slug).d


check("an old document without docket keys lists empty, counts zero, summary present, and stays unwritten",
      old_doc_reads_as_empty_docket)


def round_trip_through_the_store():
    slug = fresh_org()
    wid = create(slug, objective="so the user can see it", kind="code",
                 acceptance=["it renders", "it saves"], participants=["peer"])
    org = store.load_org(slug)            # a genuine reload, not the same object
    it, phys = org._work_find(wid)
    assert not phys and it["title"] == "ship the thing" and it["rev"] == 1
    assert it["participants"] == ["peer"] and len(it["acceptance"]) == 2
    assert it["delivery"] is not None and set(it["delivery"]) == set(workitems.STAGES)
    view = get_item(slug, wid)
    assert view["done_so_far"] == ["read the spec"] and view["working_on_next"] == ["write it"]
    assert view["last_updater"] == {"node": "boss", "generation": 0}, view["last_updater"]
    assert view["docket_at"] == view["at"]
    assert view["archived"] is False and view["archived_at"] is None
    assert view["owner"] == {"node": "boss", "generation": 0} and view["owner_current"] is True
    tree = client.get(f"/api/orgs/{slug}").json()
    assert tree["work_items_summary"] == {"attention": 0, "active": 1}


check("create → save → reload keeps the item; the view carries lists, updater, owner and clocks",
      round_trip_through_the_store)


def non_code_has_no_delivery():
    slug = fresh_org()
    wid = create(slug, kind="non-code")
    assert get_item(slug, wid)["delivery"] is None
    assert "non-code" in refused(slug, "boss", "claim", slug=wid, stage="implemented")


check("a non-code item carries no delivery stages and refuses a claim", non_code_has_no_delivery)


# ============================================================ §2 authority
print("§2 authority")


def hidden_ids_and_readers():
    slug = fresh_org()
    wid = create(slug, node="worker", title="worker's item")
    # ancestors read; the unrelated peer and stranger do not
    for who in ("worker", "mid", "boss"):
        st, js = work(slug, who, "get", slug=wid)
        assert st == 200 and js["item"]["slug"] == wid, (who, st, js)
    st_real, js_real = work(slug, "peer", "get", slug=wid)
    st_fake, js_fake = work(slug, "peer", "get", slug="w00000000")
    assert st_real == 422 and st_fake == 422
    assert js_real["detail"].replace(wid, "X")[:60] == js_fake["detail"].replace("w00000000", "X")[:60], \
        "a hidden id must be refused with the SAME message as a nonexistent one"
    assert wid not in [x["slug"] for x in ok(slug, "peer", "list")["items"]]
    assert wid in [x["slug"] for x in ok(slug, "boss", "list")["items"]]
    # the user route sees everything
    assert get_item(slug, wid)["title"] == "worker's item"


check("owner, creator and their superiors read; unrelated agents get one indistinguishable refusal",
      hidden_ids_and_readers)


def participants_collaborate_narrowly():
    slug = fresh_org()
    wid = create(slug, node="boss")
    refused(slug, "peer", "update", slug=wid, done_so_far=["x"], working_on_next=[])
    # only owner-level actors may add participants
    refused(slug, "peer", "participants", slug=wid, add=["peer"])
    ok(slug, "boss", "participants", slug=wid, add=["peer"])
    it = get_item(slug, wid)
    assert it["participants"] == ["peer"]
    # a participant may read, update, add evidence
    assert work(slug, "peer", "get", slug=wid)[0] == 200
    ok(slug, "peer", "update", slug=wid, done_so_far=["peer helped"], working_on_next=[])
    ok(slug, "peer", "evidence", slug=wid, kind="note", ref="peer's note")
    assert get_item(slug, wid)["last_updater"]["node"] == "peer"
    # …but may not assign, accept, archive, supersede, or edit participants
    refused(slug, "peer", "assign", slug=wid, owner="peer")
    refused(slug, "peer", "accept", slug=wid)
    refused(slug, "peer", "participants", slug=wid, remove=["peer"])
    # removal takes the right away again
    ok(slug, "boss", "participants", slug=wid, remove=["peer"])
    assert work(slug, "peer", "get", slug=wid)[0] == 422


check("participants get read + update + evidence + nothing else; membership is explicit and revocable",
      participants_collaborate_narrowly)


def acceptance_authority():
    slug = fresh_org()
    wid = create(slug, node="worker")
    ok(slug, "worker", "update", slug=wid, status="review", done_so_far=["all of it"], working_on_next=[])
    assert "review" in refused(slug, "worker", "update", slug=wid, status="done",
                               done_so_far=["x"], working_on_next=[])
    assert "superior" in refused(slug, "worker", "accept", slug=wid)          # the owner, never
    refused(slug, "peer", "accept", slug=wid)                                  # unrelated
    r = ok(slug, "mid", "accept", slug=wid, note="looks right")                # a strict ancestor
    assert r["accepted"] == wid
    it = get_item(slug, wid)
    assert it["status"] == "done" and it["accepted"]["by"] == {"node": "mid", "generation": 0}
    assert it["last_updater"]["node"] == "worker", "acceptance must not steal the reply recipient"
    assert "already done" in refused(slug, "boss", "accept", slug=wid)
    # the user's route
    wid2 = create(slug, node="boss")
    r = client.post(f"/api/orgs/{slug}/work-items/{wid2}/accept", json={"note": "ok"})
    assert r.status_code == 200 and get_item(slug, wid2)["accepted"]["by"] == USER


check("done is reached only through accept, by a strict ancestor or the user — never the owner",
      acceptance_authority)


def assignment_rules():
    slug = fresh_org()
    wid = create(slug, node="boss")
    assert "subordinate" in refused(slug, "boss", "assign", slug=wid, owner="peer")
    ok(slug, "boss", "assign", slug=wid, owner="worker")
    it = get_item(slug, wid)
    assert it["owner"]["node"] == "worker" and it["last_updater"]["node"] == "boss", \
        "assignment is not a docket update"
    # the new owner and its chain read; the old owner stays as creator
    assert work(slug, "worker", "get", slug=wid)[0] == 200
    assert work(slug, "boss", "get", slug=wid)[0] == 200
    # DEPENDENCY MASKING, and it got STRICTER when the opaque id was retired.
    # It used to serve {id, visible:false} — safe, because an opaque id says
    # nothing. The name is derived from the TITLE, so serving it here would
    # disclose the title of an item this viewer may not read. The pointer is
    # now anonymous: it exists, it is not yours, and that is all.
    other = create(slug, node="peer", title="peer secret")
    wid3 = create(slug, node="boss", dependencies=[other])
    dep = ok(slug, "boss", "get", slug=wid3)["item"]["dependencies"][0]
    assert dep == {"visible": False}, dep
    assert other not in json.dumps(ok(slug, "boss", "get", slug=wid3)),         "the hidden dependency's NAME reached a viewer who may not read it"
    dep_u = get_item(slug, wid3)["dependencies"][0]
    assert dep_u["visible"] is True and dep_u["title"] == "peer secret"


check("assign only to self or a subordinate, never moves the updater; hidden dependencies leak nothing",
      assignment_rules)


def hidden_items_do_not_leak_through_counts():
    """Astra review 2026-09-05 (reproduced red on the first WIP: an outsider's
    `active` went 1→2 while its visible items stayed at 1)."""
    slug = fresh_org()
    mine = create(slug, node="peer", title="peer's own")
    before = ok(slug, "peer", "list", include_archived=True)
    assert [x["slug"] for x in before["items"]] == [mine]
    assert before["counts"] == {"attention": 1 - 1, "active": 1, "archived": 0,
                                "backlogged": 0}
    # a hidden owner adds an item, flags it, and gets a question attached
    hidden = create(slug, node="boss", title="boss secret")
    ok(slug, "boss", "update", slug=hidden, attention=True, attention_reason="see me",
       done_so_far=["x"], working_on_next=[])
    agent(slug, "boss", "orgtree_ask", question="q", work_item=hidden)
    after = ok(slug, "peer", "list", include_archived=True)
    assert [x["slug"] for x in after["items"]] == [mine]
    assert after["counts"] == before["counts"], (before["counts"], after["counts"])
    # the user's counts and the toolbar summary are the org's
    assert listing(slug)["counts"] == {"attention": 1, "active": 2, "archived": 0,
                                       "backlogged": 0}
    assert client.get(f"/api/orgs/{slug}").json()["work_items_summary"] == {"attention": 1, "active": 2}
    # positive control: an item the outsider CAN read moves its counts
    ok(slug, "boss", "participants", slug=hidden, add=["peer"])
    assert ok(slug, "peer", "list")["counts"] == {"attention": 1, "active": 2,
                                                  "archived": 0, "backlogged": 0}


check("an agent's list counts cover only its readable set; hidden items move nothing (user counts are org-wide)",
      hidden_items_do_not_leak_through_counts)


def participants_cannot_close_claim_or_check():
    """Astra review 2026-09-05 (reproduced red: participant `dropped` 200, `claim` 200)."""
    slug = fresh_org()
    wid = create(slug, node="boss", participants=["peer"], acceptance=["works"])
    ok(slug, "peer", "update", slug=wid, status="blocked",                                          # positive
       blocked_reason="the vendor's key has not arrived; their support can send it",
       done_so_far=["p"], working_on_next=[])
    ok(slug, "peer", "evidence", slug=wid, kind="link", ref="http://x")                              # positive
    assert "owner-level" in refused(slug, "peer", "update", slug=wid, status="dropped",
                                    done_so_far=["p"], working_on_next=[])
    assert "owner-level" in refused(slug, "peer", "claim", slug=wid, stage="implemented")
    assert "owner-level" in refused(slug, "peer", "check", slug=wid, index=0, evidence_ref="x")
    assert "retitle" in refused(slug, "peer", "update", slug=wid, title="mine now",
                                done_so_far=["p"], working_on_next=[])
    ok(slug, "boss", "claim", slug=wid, stage="committed", ref="abc1234")
    assert "owner-level" in refused(slug, "peer", "verify", slug=wid, stage="committed")
    it = get_item(slug, wid)
    assert it["status"] == "blocked" and it["title"] == "ship the thing"
    assert it["delivery"]["implemented"] is None and it["acceptance"][0]["checked"] is None
    # the same calls by the owner succeed (the refusals are about WHO, not WHAT)
    ok(slug, "boss", "check", slug=wid, index=0, evidence_ref="x")
    ok(slug, "boss", "update", slug=wid, status="dropped", done_so_far=["p"], working_on_next=[])
    assert "owner-level" in refused(slug, "peer", "update", slug=wid, reopen=True,
                                    done_so_far=["p"], working_on_next=[])


check("a participant may update and add evidence, but not drop, reopen, retitle, claim, verify or check",
      participants_cannot_close_claim_or_check)


def supersede_is_honest():
    slug = fresh_org()
    # ⚠ DISTINCTIVE TITLES ON PURPOSE. The names below are searched for across
    # the WHOLE payload to prove a hidden item's name does not leak, and a
    # one-character name ("a", "b") matches inside unrelated strings — "boss"
    # contains "b" — so the leak control fired on five innocent fields and said
    # nothing true. A control that cannot tell a hit from a coincidence is not
    # a control.
    a = create(slug, node="boss", title="alpha superseded item")
    b = create(slug, node="boss", title="beta replacement item")
    hidden = create(slug, node="peer", title="peer's")
    ok(slug, "peer", "participants", slug=hidden, add=["boss"])       # boss may READ it, not manage it
    assert "not manage" in refused(slug, "boss", "supersede", slug=a, by=hidden)
    ok(slug, "boss", "supersede", slug=a, by=b)
    assert "already superseded" in refused(slug, "boss", "supersede", slug=a, by=b)
    assert "cycle" in refused(slug, "boss", "supersede", slug=b, by=a) or \
        "superseded" in refused(slug, "boss", "supersede", slug=b, by=a)
    c = create(slug, node="boss", title="C")
    ok(slug, "boss", "update", slug=c, status="dropped", done_so_far=["no"], working_on_next=[])
    assert "open work" in refused(slug, "boss", "supersede", slug=b, by=c)
    # SAME RULE AS `dependencies`, for the same reason: the pointer is a
    # title-derived name now, so a viewer who may not open it is not told what
    # it is called — only that it is there.
    v = get_item(slug, a)
    assert v["superseded_by"] == b and v["superseded_by_visible"] is True
    ok(slug, "boss", "participants", slug=a, add=["peer"])
    pv = ok(slug, "peer", "get", slug=a)["item"]
    assert pv["superseded_by"] is None and pv["superseded_by_visible"] is False
    hit = [k for k, v in pv.items() if b in json.dumps(v)]
    assert not hit,         f"the superseding item's NAME reached a viewer who may not read it, in {hit}"
    assert work(slug, "peer", "get", slug=b)[0] == 422


check("supersede needs owner-level right on both items, refuses re-supersede, closed targets and cycles; pointer visibility is explicit",
      supersede_is_honest)


def reopen_clears_the_stale_acceptance():
    slug = fresh_org()
    wid = create(slug)
    client.post(f"/api/orgs/{slug}/work-items/{wid}/accept", json={"note": "v1 accepted"})
    assert get_item(slug, wid)["accepted"]["note"] == "v1 accepted"
    ok(slug, "boss", "update", slug=wid, reopen=True, done_so_far=["v1"], working_on_next=["v2"])
    it = get_item(slug, wid)
    assert it["accepted"] is None and it["status"] == "in_progress"
    assert any(h.get("op") == "reopen" and h.get("accepted_was", {}).get("note") == "v1 accepted"
               for h in it["history"]), "the record of the earlier acceptance lives in history"


check("reopen clears a stale acceptance and keeps it in history", reopen_clears_the_stale_acceptance)


# ==================================================== §3 the status update
print("§3 the status update")


def both_lists_always_and_never_both_empty():
    slug = fresh_org()
    wid = create(slug)
    for bad in ({"done_so_far": [], "working_on_next": []},
                {"done_so_far": ["  "], "working_on_next": ["\t"]},
                {"done_so_far": [], "working_on_next": [None, ""]},
                {"status": "in_progress"},                        # no lists at all
                {"attention": True, "attention_reason": "look"}):  # flag-only
        st, js = work(slug, "boss", "update", slug=wid, **bad)
        assert st == 422, (bad, st, js)
        assert "done_so_far" in js["detail"], js["detail"]
    st, js = work(slug, "boss", "update", slug=wid, done_so_far="a paragraph", working_on_next=[])
    assert st == 422 and "LIST" in js["detail"], js
    before = get_item(slug, wid)
    assert before["rev"] == 1, "refused updates must write nothing"
    # either list alone is fine; blank entries are dropped, not counted
    ok(slug, "boss", "update", slug=wid, done_so_far=["", " one ", ""], working_on_next=[])
    it = get_item(slug, wid)
    assert it["done_so_far"] == ["one"] and it["working_on_next"] == []
    assert it["rev"] == 2 and it["docket_at"] > before["docket_at"]
    ok(slug, "boss", "update", slug=wid, done_so_far=[], working_on_next=["two"], status="blocked",
       blocked_reason="waiting on peer")
    it = get_item(slug, wid)
    assert it["status"] == "blocked" and it["blocked_reason"] == "waiting on peer"
    assert it["done_so_far"] == [] and it["working_on_next"] == ["two"], "lists are replaced, not merged"
    # create with both lists blank is allowed (nothing claimed yet) but an explicit both-empty is not
    wid2 = ok(slug, "boss", "create", title="bare",
                objective="nothing is claimed yet; claim it later")["created"]
    assert get_item(slug, wid2)["done_so_far"] == []
    st, js = work(slug, "boss", "create", title="bare2",
                    objective="p; s", done_so_far=[""], working_on_next=[])
    assert st == 422


check("every update carries both lists; both empty (incl. whitespace-only) is refused; no bypass",
      both_lists_always_and_never_both_empty)


def dropped_and_superseded():
    slug = fresh_org()
    a = create(slug, title="A")
    b = create(slug, title="B")
    ok(slug, "boss", "supersede", slug=a, by=b)
    it = get_item(slug, a)
    assert it["status"] == "superseded" and it["superseded_by"] == b
    assert "itself" in refused(slug, "boss", "supersede", slug=b, by=b)
    ok(slug, "boss", "update", slug=b, status="dropped", done_so_far=["nothing"], working_on_next=[])
    js = listing(slug)
    assert js["counts"]["active"] == 0, js["counts"]
    assert {x["slug"] for x in js["items"]} == {a, b}, "closed-but-not-done items stay listed (only done ages out)"


check("supersede and dropped close an item without archiving it; active count excludes them",
      dropped_and_superseded)


# ============================================================== §4 archive
print("§4 archive")


def exact_hour_edge():
    slug = fresh_org()
    wid = create(slug)
    ok(slug, "boss", "update", slug=wid, status="review", done_so_far=["done"], working_on_next=[])
    client.post(f"/api/orgs/{slug}/work-items/{wid}/accept", json={})
    org = store.load_org(slug)
    it, _ = org._work_find(wid)
    from datetime import datetime
    stamp = datetime.fromisoformat(it["docket_at"].replace("Z", "+00:00")).timestamp()
    # at EXACTLY one hour: not archived
    v = org.work_list(USER, include_archived=True, now_ts=stamp + 3600)
    assert [x["slug"] for x in v["items"]] == [wid] and v["archived"] == [], v["counts"]
    assert v["counts"] == {"attention": 0, "active": 0, "archived": 0,
                           "backlogged": 0}
    # one second past: archived (derived), and the store is untouched by the read
    v = org.work_list(USER, include_archived=True, now_ts=stamp + 3601)
    assert v["items"] == [] and [x["slug"] for x in v["archived"]] == [wid]
    assert v["archived"][0]["archived"] is True and v["archived"][0]["archived_at"] is None, \
        "derived before the physical move"
    assert v["counts"]["archived"] == 1
    assert store.load_org(slug).d.get("work_items_archive") in (None, []), "a read never moves"
    # a done item that is NOT yet old stays in the active group of the route
    assert [x["slug"] for x in listing(slug, archived=True)["items"]] == [wid]


check("done + docket age exactly 3600 s is NOT archived; 3601 s is (derived, read-only)",
      exact_hour_edge)


def physical_move_on_next_write_and_reopen():
    slug = fresh_org()
    wid = create(slug)
    client.post(f"/api/orgs/{slug}/work-items/{wid}/accept", json={})
    backdate(slug, wid, 3601)
    js = listing(slug, archived=True)
    assert [x["slug"] for x in js["archived"]] == [wid] and js["items"] == []
    # an update on an archived item is refused without reopen…
    msg = refused(slug, "boss", "update", slug=wid, done_so_far=["more"], working_on_next=[])
    assert "reopen" in msg
    # a refused call writes nothing (the sweep it ran was discarded with the doc)…
    org = store.load_org(slug)
    _, phys = org._work_find(wid)
    assert not phys, "a refused mutation must persist nothing, not even the sweep"
    # …the next SUCCESSFUL write runs the sweep: the item is now PHYSICALLY archived
    create(slug, title="unrelated write")
    org = store.load_org(slug)
    it, phys = org._work_find(wid)
    assert phys and it["archived_at"], "the sweep at the head of a mutation moves eligible items"
    assert get_item(slug, wid)["archived"] is True, "archived ids still resolve on the user route"
    # reopen brings it back, open, with the new lists
    ok(slug, "boss", "update", slug=wid, reopen=True, done_so_far=["more"], working_on_next=["again"])
    it = get_item(slug, wid)
    assert it["archived"] is False and it["archived_at"] is None and it["status"] == "in_progress"
    org = store.load_org(slug)
    _, phys = org._work_find(wid)
    assert not phys
    assert listing(slug)["counts"]["active"] == 2      # the reopened one + "unrelated write"
    # explicit archive of a closed item, early
    ok(slug, "boss", "update", slug=wid, status="dropped", done_so_far=["no"], working_on_next=[])
    ok(slug, "boss", "archive", slug=wid)
    assert listing(slug, archived=True)["archived"][0]["slug"] == wid
    assert "done|superseded|dropped" in refused(slug, "boss", "archive", slug=create(slug))


check("the physical move happens on the next write, records kept; reopen returns the item",
      physical_move_on_next_write_and_reopen)


def attention_holds_an_item_active():
    slug = fresh_org()
    wid = create(slug)
    client.post(f"/api/orgs/{slug}/work-items/{wid}/accept", json={})
    backdate(slug, wid, 4000)
    assert listing(slug, archived=True)["archived"][0]["slug"] == wid
    # a pending question lands on the (derived-archived) done item
    st, js = agent(slug, "peer", "orgtree_ask", question="is this really done?", work_item=wid)
    assert st == 422, "peer has no read right and must be refused"
    st, js = agent(slug, "boss", "orgtree_ask", question="user, is this really done?", work_item=wid)
    assert st == 200 and js.get("asked"), js
    js = listing(slug, archived=True)
    assert [x["slug"] for x in js["items"]] == [wid] and js["archived"] == [], \
        "an item holding attention is shown in the ACTIVE list, never hidden in the archive"
    row = js["items"][0]
    assert row["archived"] is False and row["effective_attention"] is True
    assert row["attention_sources"] == ["question"] and row["status"] == "done"
    assert js["counts"] == {"attention": 1, "active": 0, "archived": 0,
                            "backlogged": 0}
    assert client.get(f"/api/orgs/{slug}").json()["work_items_summary"] == {"attention": 1, "active": 0}
    # a write cannot sweep it away while attention holds
    create(slug, title="another")
    org = store.load_org(slug)
    _, phys = org._work_find(wid)
    assert not phys
    # the question is withdrawn → attention gone → it ages out again
    agent(slug, "boss", "orgtree_withdraw_ask")
    js = listing(slug, archived=True)
    assert [x["slug"] for x in js["archived"]] == [wid] and js["counts"]["attention"] == 0


check("a pending attached question keeps a done/aged item in the active list and the badge; withdrawal releases it",
      attention_holds_an_item_active)


# ============================================================ §5 questions
print("§5 questions")


def two_askers_one_item():
    slug = fresh_org()
    wid = create(slug, node="boss", participants=["peer"])
    st, a1 = agent(slug, "boss", "orgtree_ask", question="boss asks: which colour?",
                   options=[{"label": "red"}, {"label": "blue"}], work_item=wid)
    assert st == 200 and a1["asked"], a1
    st, a2 = agent(slug, "peer", "orgtree_ask",
                   questions=[{"question": "peer asks: deadline?", "work_item": wid},
                              {"question": "peer asks: unrelated?"}])
    assert st == 200 and a2["asked"], a2
    # the stranger has no right to attach — refused, and NOTHING recorded
    st, a3 = agent(slug, "stranger", "orgtree_ask", question="stranger asks", work_item=wid)
    assert st == 422 and "may read" in a3["detail"], a3
    asks = store.load_org(slug).d["asks"]
    assert {a["node"] for a in asks if a["status"] == "open"} == {"boss", "peer"}, \
        "a refused attach must not leave an ask behind"
    it = get_item(slug, wid)
    assert it["effective_attention"] and it["attention_sources"] == ["question"]
    qs = {q["node"]: q for q in it["questions"]}
    assert set(qs) == {"boss", "peer"}, it["questions"]
    assert qs["boss"]["ask_id"] == a1["asked"] and qs["boss"]["tabs"][0]["options"][0]["label"] == "red"
    assert [t["index"] for t in qs["peer"]["tabs"]] == [0], "only the LINKED tab of a batch is attached"
    # the ask entries carry the linkage the desk filters on
    tree = client.get(f"/api/orgs/{slug}").json()
    open_asks = [a for a in tree["asks"] if a["status"] == "open"]
    assert all(a["work_items"] == [wid] for a in open_asks), open_asks
    peer_entry = next(a for a in open_asks if a["node"] == "peer")
    assert peer_entry["questions"][0]["work_item"] == wid and "work_item" not in peer_entry["questions"][1]
    # counts: ONE item, not two questions
    assert listing(slug)["counts"]["attention"] == 1
    # answering boss's question through the EXISTING route resolves boss's only
    r = client.post(f"/api/orgs/{slug}/asks/{a1['asked']}/answer", json={"selected": ["red"], "rev": 1})
    assert r.status_code == 200, r.text
    assert DRIVEN and DRIVEN[-1][1] == "boss", "the answer drives the asker, as always"
    it = get_item(slug, wid)
    assert [q["node"] for q in it["questions"]] == ["peer"] and it["effective_attention"]
    # peer's batch resolves through the batch route (both tabs, positional); the linkage did not change it
    peer_rev = next(a for a in store.load_org(slug).d["asks"] if a["node"] == "peer")["rev"]
    r = client.post(f"/api/orgs/{slug}/nodes/peer/batch",
                    json={"revs": {"ask": peer_rev}, "answers": ["friday", None]})
    assert r.status_code == 200, r.text
    assert DRIVEN[-1][1] == "peer"
    it = get_item(slug, wid)
    assert it["questions"] == [] and it["effective_attention"] is False and it["attention_sources"] == []
    assert listing(slug)["counts"]["attention"] == 0


check("two distinct askers attach to one item; each resolves through its own existing route; one refusal records nothing",
      two_askers_one_item)


def attach_appends_to_open_batch_and_deep_agents_route_as_mail():
    slug = fresh_org()
    wid = create(slug, node="boss")
    agent(slug, "boss", "orgtree_ask", question="first, unlinked")
    st, js = agent(slug, "boss", "orgtree_ask", question="second, linked", work_item=wid)
    assert st == 200 and "appended" in js["status"], js
    it = get_item(slug, wid)
    assert len(it["questions"]) == 1 and it["questions"][0]["tabs"][0]["index"] == 1
    entry = next(a for a in store.load_org(slug).d["asks"] if a["node"] == "boss" and a["status"] == "open")
    assert entry["work_items"] == [wid] and entry["rev"] == 2
    # a deep agent without a user audience: the question is MAIL to its superior, not a card —
    # the item is named in the text and nothing attaches
    wid2 = create(slug, node="worker", title="deep")
    st, js = agent(slug, "worker", "orgtree_ask", question="deep question", work_item=wid2)
    assert st == 200 and js.get("routed") == "mid", js
    body = store.load_org(slug).d["mail"]["mid"][-1]["body"]
    assert f"(docket item {wid2})" in body, body
    assert get_item(slug, wid2)["questions"] == []


check("a linked question appends to the open batch; a deep agent's question routes as mail naming the item",
      attach_appends_to_open_batch_and_deep_agents_route_as_mail)


# ===================================================== §6 manual attention
print("§6 manual attention")


def flag_set_clear_dismiss():
    slug = fresh_org()
    wid = create(slug)
    assert "attention_reason" in refused(slug, "boss", "update", slug=wid, attention=True,
                                         done_so_far=["x"], working_on_next=[])
    r = ok(slug, "boss", "update", slug=wid, attention=True, attention_reason="Need the API key",
           done_so_far=["built"], working_on_next=["deploy"])
    assert r["manual_attention"] is True
    it = get_item(slug, wid)
    assert it["manual_attention"]["reason"] == "Need the API key" and it["manual_attention"]["set_rev"] == 1
    assert it["effective_attention"] and it["attention_sources"] == ["manual"]
    assert listing(slug)["counts"]["attention"] == 1
    # an ordinary update CLEARS it, and says so
    r = ok(slug, "boss", "update", slug=wid, done_so_far=["built", "got key"], working_on_next=["deploy"])
    assert "CLEARED" in (r.get("note") or "")
    it = get_item(slug, wid)
    assert it["manual_attention"] is None and it["effective_attention"] is False
    assert any(h.get("op") == "update" and "cleared_set_rev" in str(h.get("changes")) for h in it["history"])
    # re-raise mints set_rev 2
    ok(slug, "boss", "update", slug=wid, attention=True, attention_reason="Prod is down",
       done_so_far=["built"], working_on_next=["fix"])
    it = get_item(slug, wid)
    assert it["manual_attention"]["set_rev"] == 2
    # DISMISS: stale rev refused (409), nothing changes
    r = client.post(f"/api/orgs/{slug}/work-items/{wid}/dismiss-attention", json={"set_rev": 1})
    assert r.status_code == 409, r.text
    assert get_item(slug, wid)["manual_attention"]["set_rev"] == 2
    # correct rev: cleared, Blocked, recorded, lists + updater untouched, notice to the updater
    before = get_item(slug, wid)
    n_driven = len(DRIVEN)
    r = client.post(f"/api/orgs/{slug}/work-items/{wid}/dismiss-attention", json={"set_rev": 2})
    assert r.status_code == 200, r.text
    it = get_item(slug, wid)
    assert it["manual_attention"] is None and it["status"] == "blocked"
    assert it["dismissals"] == [{**it["dismissals"][0], "set_rev": 2, "reason": "Prod is down", "by": USER}]
    assert it["done_so_far"] == before["done_so_far"] and it["working_on_next"] == before["working_on_next"]
    assert it["last_updater"] == before["last_updater"] and it["docket_at"] == before["docket_at"]
    assert it["effective_attention"] is False
    assert len(DRIVEN) == n_driven + 1 and DRIVEN[-1][1] == "boss" and DRIVEN[-1][3] is False, \
        "the flag's author gets a PASSIVE notice"
    assert "DISMISSED" in store.load_org(slug).d["mail"]["boss"][-1]["body"]
    # a second dismiss: nothing to dismiss → 409
    assert client.post(f"/api/orgs/{slug}/work-items/{wid}/dismiss-attention", json={"set_rev": 2}).status_code == 409
    # exact repeat of the dismissed reason is refused (case/whitespace-insensitive); a different one is not
    msg = refused(slug, "boss", "update", slug=wid, attention=True, attention_reason="  prod IS down ",
                  done_so_far=["built"], working_on_next=["fix"])
    assert "exact repeat" in msg
    ok(slug, "boss", "update", slug=wid, attention=True, attention_reason="Prod is down: disk full since 10:00",
       done_so_far=["built"], working_on_next=["fix"])
    assert get_item(slug, wid)["manual_attention"]["set_rev"] == 3


check("flag: set needs a reason; an ordinary update clears it; dismiss is CAS, sets Blocked, records, notifies; exact repeat refused",
      flag_set_clear_dismiss)


def dismiss_keeps_pending_questions_orange():
    slug = fresh_org()
    wid = create(slug)
    ok(slug, "boss", "update", slug=wid, attention=True, attention_reason="decide X",
       done_so_far=["a"], working_on_next=["b"])
    agent(slug, "peer", "orgtree_ask", question="unrelated")          # peer has no right: not attached
    ok(slug, "boss", "participants", slug=wid, add=["peer"])
    st, js = agent(slug, "peer", "orgtree_ask", question="peer: which?", work_item=wid)
    assert st == 200 and "appended" in js["status"]
    it = get_item(slug, wid)
    assert sorted(it["attention_sources"]) == ["manual", "question"]
    assert listing(slug)["counts"]["attention"] == 1, "one item, two sources, counted once"
    r = client.post(f"/api/orgs/{slug}/work-items/{wid}/dismiss-attention", json={"set_rev": 1})
    assert r.status_code == 200 and r.json()["pending_questions"] == 1
    it = get_item(slug, wid)
    assert it["manual_attention"] is None and it["status"] == "blocked"
    assert it["effective_attention"] is True and it["attention_sources"] == ["question"]
    assert len(it["questions"]) == 1 and it["questions"][0]["node"] == "peer"
    open_peer = [a for a in store.load_org(slug).d["asks"] if a["node"] == "peer" and a["status"] == "open"]
    assert len(open_peer) == 1, "dismissing the flag must not answer, withdraw or moot any question"


check("dismissing the manual flag leaves pending questions untouched and the item orange",
      dismiss_keeps_pending_questions_orange)


# ======================================================= §7 reply routing
print("§7 reply routing")


def reply_goes_to_the_last_updater_exactly():
    slug = fresh_org()
    wid = create(slug, node="boss", title="Reply target", participants=["peer"])
    ok(slug, "peer", "update", slug=wid, done_so_far=["peer did it"], working_on_next=[])
    # owner change, a question from boss, and a dismissal do not touch the recipient
    ok(slug, "boss", "assign", slug=wid, owner="mid")
    assert get_item(slug, wid)["last_updater"]["node"] == "peer", "assignment stamped the updater"
    agent(slug, "boss", "orgtree_ask", question="q?", work_item=wid)
    assert get_item(slug, wid)["last_updater"]["node"] == "peer", "a question attach stamped the updater"
    ok(slug, "peer", "update", slug=wid, attention=True, attention_reason="r", done_so_far=["x"], working_on_next=[])
    client.post(f"/api/orgs/{slug}/work-items/{wid}/dismiss-attention", json={"set_rev": 1})
    assert get_item(slug, wid)["last_updater"]["node"] == "peer"
    r = client.post(f"/api/orgs/{slug}/work-items/{wid}/reply", json={"body": "please also do Y"})
    assert r.status_code == 200, r.text
    js = r.json()
    assert js["to"] == "peer" and js["deferred"] is False and js["accepted"] is True
    mail = store.load_org(slug).d["mail"]["peer"][-1]
    assert mail["from"] == USER and mail["body"].startswith(f'[DOCKET REPLY · {wid} "Reply target"]'), mail["body"]
    assert "please also do Y" in mail["body"]
    assert DRIVEN[-1][1] == "peer" and DRIVEN[-1][3] is True, "the recipient is driven"
    assert client.post(f"/api/orgs/{slug}/work-items/{wid}/reply", json={"body": "  "}).status_code == 422
    # no updater yet → explicit 422, nobody chosen instead
    org = store.load_org(slug)
    org.work_create(USER, "user-made", "the user made this; it has no updater",
                    owner="mid")     # owned, but no agent ever updated it
    store.save_org(org)
    wid_u = [x["slug"] for x in listing(slug)["items"] if x["title"] == "user-made"][0]
    r = client.post(f"/api/orgs/{slug}/work-items/{wid_u}/reply", json={"body": "hi"})
    assert r.status_code == 422 and "nobody to reply to" in r.text, \
        "the owner is NOT a fallback recipient — the failure is shown instead"
    assert not store.load_org(slug).d.get("mail", {}).get("mid"), "nothing was mailed to the owner"
    # an archived recipient: the mail is deferred, the response says so, no reroute
    org = store.load_org(slug)
    org.retire(USER, "peer")
    store.save_org(org)
    r = client.post(f"/api/orgs/{slug}/work-items/{wid}/reply", json={"body": "still you"})
    assert r.status_code == 200 and r.json() == {**r.json(), "to": "peer", "deferred": True, "node_state": "archived"}
    assert client.post(f"/api/orgs/{slug}/work-items/w0000dead/reply", json={"body": "x"}).status_code == 404


check("the general reply reaches the last updater exactly; every failure is explicit, never a substitute",
      reply_goes_to_the_last_updater_exactly)


# ============================================================ §8 delivery
print("§8 delivery")


def claim_and_verify_three_valued():
    slug = fresh_org()
    wid = create(slug)
    assert "hex sha" in refused(slug, "boss", "claim", slug=wid, stage="committed", ref="main")
    assert "stage" in refused(slug, "boss", "claim", slug=wid, stage="shipped", ref="abc1234")
    ok(slug, "boss", "claim", slug=wid, stage="implemented", note="in my worktree")
    r = ok(slug, "boss", "claim", slug=wid, stage="committed", ref="abc1234")
    assert r["verifiable"] is True
    st = get_item(slug, wid)["delivery"]["committed"]
    assert st["method"] == "unverified" and st["verified"] is None and st["ref"] == "abc1234"
    assert "verifiable" in refused(slug, "boss", "verify", slug=wid, stage="implemented")
    calls: list[list[str]] = []

    def unknown_sha(argv):
        calls.append(argv)
        return 1, ""
    workitems.set_runner_for_tests(unknown_sha)
    try:
        r = ok(slug, "boss", "verify", slug=wid, stage="committed")
        assert r["verified"] is None and "does not resolve" in r["detail"], r
        assert calls and calls[0][:3] == ["rev-parse", "--verify", "--quiet"], calls
        st = get_item(slug, wid)["delivery"]["committed"]
        assert st["verified"] is None and st["method"] == "object-exists" and st["fetched_at"] is None

        def resolves(argv):
            if argv[0] == "rev-parse":
                return 0, "abc1234" + "0" * 33
            return 0, ""
        workitems.set_runner_for_tests(resolves)
        r = ok(slug, "boss", "verify", slug=wid, stage="committed")
        assert r["verified"] is True
        st = get_item(slug, wid)["delivery"]["committed"]
        assert st["resolved_oid"] == "abc1234" + "0" * 33 and "not a statement about main" in st["detail"]
        # pushed: the tracking ref is read first; ancestry False is a real answer, None is not
        ok(slug, "boss", "claim", slug=wid, stage="pushed", ref="abc1234")

        def not_ancestor(argv):
            if argv[0] == "rev-parse":
                return 0, ("f" * 40) if argv[-1] == workitems.REMOTE_REF else ("abc1234" + "0" * 33)
            return 1, ""                                  # merge-base: not an ancestor
        workitems.set_runner_for_tests(not_ancestor)
        r = ok(slug, "boss", "verify", slug=wid, stage="pushed")
        assert r["verified"] is False and "fetch time unknown" in r["detail"], r
        st = get_item(slug, wid)["delivery"]["pushed"]
        assert st["target"] == "f" * 40 and st["ref_as_of"] == "local tracking ref"
    finally:
        workitems.set_runner_for_tests(None)
    # the claim fields are never caller-writable
    it = get_item(slug, wid)
    assert it["delivery"]["implemented"]["method"] == "self-report"
    assert it["delivery"]["implemented"]["verified"] is None


check("claim records; verify is three-valued through git (unknown / true / false) and never a functional check",
      claim_and_verify_three_valued)


def verify_writes_nothing_after_a_concurrent_mutation():
    slug = fresh_org()
    wid = create(slug)
    ok(slug, "boss", "claim", slug=wid, stage="committed", ref="abc1234")
    org = store.load_org(slug)
    cap = org.work_verify_capture("boss", wid, "committed")
    # the item changes while git is (would be) running
    org.work_update("boss", wid, ["moved on"], [], status="in_progress")
    res = {"verified": True, "method": "object-exists", "detail": "d", "resolved_oid": "x",
           "target": "", "ref_as_of": "", "fetched_at": None, "observed_at": "t"}
    r = org.work_verify_commit(wid, "committed", cap["rev"], res)
    assert r["stale"] is True
    it, _ = org._work_find(wid)
    assert it["delivery"]["committed"]["verified"] is None, "a stale verify must write nothing"
    # and with the current rev it writes
    r = org.work_verify_commit(wid, "committed", it["rev"], res)
    assert r["stale"] is False and it["delivery"]["committed"]["verified"] is True


check("verify revalidates the item rev: a concurrent mutation makes it write nothing",
      verify_writes_nothing_after_a_concurrent_mutation)


def acceptance_checks_are_separate_evidence():
    slug = fresh_org()
    wid = create(slug, acceptance=["renders", "saves"])
    assert "out of range" in refused(slug, "boss", "check", slug=wid, index=2, evidence_ref="x")
    assert "evidence_ref" in refused(slug, "boss", "check", slug=wid, index=0, evidence_ref="")
    ok(slug, "boss", "check", slug=wid, index=1, evidence_ref="tests/x.log")
    acc = get_item(slug, wid)["acceptance"]
    assert acc[0]["checked"] is None and acc[1]["checked"]["evidence_ref"] == "tests/x.log"


check("acceptance conditions are checked one at a time with an evidence ref, apart from delivery",
      acceptance_checks_are_separate_evidence)


def in_build_receipt_is_marked_against_the_current_boot():
    """Astra final review: the receipt is history; whether it still describes
    the RUNNING build is derived on read from the frozen boot identity — no
    git on the read path (the fake runner records every call)."""
    from orgtree import restart_wake
    slug = fresh_org()
    wid = create(slug)
    sha_a = "a" * 40
    saved = restart_wake._boot_info_cache
    calls: list[list[str]] = []

    def git(argv):
        calls.append(argv)
        if argv[0] == "rev-parse":
            return 0, sha_a
        return 0, ""                       # merge-base: ancestor
    workitems.set_runner_for_tests(git)
    try:
        restart_wake._boot_info_cache = {"commit": sha_a, "commit_short": sha_a[:7],
                                         "branch": None, "dirty": False}
        ok(slug, "boss", "claim", slug=wid, stage="in_build", ref=sha_a[:12])
        r = ok(slug, "boss", "verify", slug=wid, stage="in_build")
        assert r["verified"] is True, r
        st = get_item(slug, wid)["delivery"]["in_build"]
        assert st["target"] == sha_a and st["evaluated_against_current_build"] is True
        assert st["verified_current"] is True
        n_calls = len(calls)
        # the backend restarts on a NEW commit: the receipt stays, the marker flips
        restart_wake._boot_info_cache = {"commit": "b" * 40, "commit_short": "b" * 7,
                                         "branch": None, "dirty": False}
        st = get_item(slug, wid)["delivery"]["in_build"]
        assert st["verified"] is True and st["target"] == sha_a, "the historical receipt is verbatim"
        assert st["evaluated_against_current_build"] is False and st["verified_current"] is None
        # the SAME sha booted dirty is a different build for evidence
        restart_wake._boot_info_cache = {"commit": sha_a, "commit_short": sha_a[:7],
                                         "branch": None, "dirty": True}
        st = get_item(slug, wid)["delivery"]["in_build"]
        assert st["evaluated_against_current_build"] is False and st["verified_current"] is None
        # back on the clean build it was measured against
        restart_wake._boot_info_cache = {"commit": sha_a, "commit_short": sha_a[:7],
                                         "branch": None, "dirty": False}
        st = get_item(slug, wid)["delivery"]["in_build"]
        assert st["evaluated_against_current_build"] is True and st["verified_current"] is True
        assert len(calls) == n_calls, "reads ran git"
        # the marker is in_build-only; other stages say None
        ok(slug, "boss", "claim", slug=wid, stage="committed", ref=sha_a[:12])
        ok(slug, "boss", "verify", slug=wid, stage="committed")
        d = get_item(slug, wid)["delivery"]
        assert d["committed"]["verified"] is True and d["committed"]["evaluated_against_current_build"] is None
        assert d["implemented"] is None
        # …and the tool's own get shows the same derived fields
        assert ok(slug, "boss", "get", slug=wid)["item"]["delivery"]["in_build"]["evaluated_against_current_build"] is True
    finally:
        workitems.set_runner_for_tests(None)
        restart_wake._boot_info_cache = saved


check("an in_build receipt is marked stale on read after a new boot or a same-sha dirty boot, without git",
      in_build_receipt_is_marked_against_the_current_boot)


def leaving_done_needs_reopen_even_before_archive():
    slug = fresh_org()
    wid = create(slug)
    client.post(f"/api/orgs/{slug}/work-items/{wid}/accept", json={"note": "accepted v1"})
    msg = refused(slug, "boss", "update", slug=wid, status="in_progress",
                  done_so_far=["more"], working_on_next=["v2"])
    assert "reopen=true" in msg
    it = get_item(slug, wid)
    assert it["status"] == "done" and it["accepted"]["note"] == "accepted v1", "the refusal changed nothing"
    # evidence on a finished item is still allowed without resuming it
    ok(slug, "boss", "evidence", slug=wid, kind="note", ref="post-mortem")
    ok(slug, "boss", "update", slug=wid, reopen=True, done_so_far=["more"], working_on_next=["v2"])
    it = get_item(slug, wid)
    assert it["status"] == "in_progress" and it["accepted"] is None
    # a superseded item resumed by reopen drops its pointer (kept in history)
    a = create(slug, title="A2")
    b = create(slug, title="B2")
    ok(slug, "boss", "supersede", slug=a, by=b)
    refused(slug, "boss", "update", slug=a, status="open", done_so_far=["x"], working_on_next=[])
    ok(slug, "boss", "update", slug=a, reopen=True, status="open", done_so_far=["x"], working_on_next=[])
    v = get_item(slug, a)
    assert v["status"] == "open" and v["superseded_by"] is None
    assert any(h.get("op") == "reopen" and h.get("superseded_by_was") == b for h in v["history"])


check("a recently done (not yet archived) item cannot leave done without reopen; reopen clears acceptance",
      leaving_done_needs_reopen_even_before_archive)


# ================================================================= §9 caps
print("§9 caps")


def evidence_cap_refuses_never_truncates():
    slug = fresh_org()
    wid = create(slug)
    for i in range(50):
        ok(slug, "boss", "evidence", slug=wid, kind="note", ref=f"n{i}")
    msg = refused(slug, "boss", "evidence", slug=wid, kind="note", ref="n50")
    assert "50" in msg and "truncated" in msg
    ev = get_item(slug, wid)["evidence"]
    assert len(ev) == 50 and ev[0]["ref"] == "n0" and ev[-1]["ref"] == "n49"
    assert "kind" in refused(slug, "boss", "evidence", slug=wid, kind="rumour", ref="x")


check("the 51st evidence row is refused with the earlier 50 intact", evidence_cap_refuses_never_truncates)


def history_folds_with_a_visible_count():
    slug = fresh_org()
    org = store.load_org(slug)
    wid = org.work_create("boss", "hist", "history grows unbounded; fold it")["created"]
    for i in range(130):
        org.work_update("boss", wid, [f"step {i}"], [])
    it, _ = org._work_find(wid)
    h = it["history"]
    assert len(h) == 100 and h[0]["kind"] == "folded", (len(h), h[0])
    assert h[0]["count"] == 31 and h[0]["first_at"] <= h[0]["last_at"]
    assert h[-1]["op"] == "update" and it["rev"] == 131
    assert "omission" in h[0]["note"]


check("past 100 history rows the oldest fold into ONE disclosure row with a count", history_folds_with_a_visible_count)


def active_cap_refuses_creation():
    slug = fresh_org()
    org = store.load_org(slug)
    for i in range(200):
        org.work_create("boss", f"item {i}", "the cap is untested; fill it")
    try:
        org.work_create("boss", "one too many", "one past the cap; refuse it")
    except LedgerError as e:
        assert "200" in str(e) and "archive" in str(e)
    else:
        raise AssertionError("the 201st active item must be refused")
    assert len(org.d["work_items"]) == 200


check("the 201st active item is refused, naming the cap and the archive", active_cap_refuses_creation)


# ================================================ §10 standing instructions
print("§10 standing instructions")


def doctrine_rides_the_identity_prompt_on_every_lane():
    slug = fresh_org()
    org = store.load_org(slug)
    for nid in ("boss", "worker"):
        p = supervisor.identity_prompt(org, nid)
        assert "THE DOCKET" in p and "orgtree_work" in p, nid
        assert "done_so_far" in p and "working_on_next" in p
        assert "LAST UPDATER" in p and "reopen=true" in p and "exact repeat" in p
        assert "work_item" in p, "the ask linkage must be taught, not just present in the card"
    # the same string is what every lane renders (source-level: the claude identity
    # file, the codex AGENTS.md and the antigravity developer_instructions all call it)
    src = open(os.path.join(os.path.dirname(__file__), "..", "orgtree", "supervisor.py"),
               encoding="utf-8").read()
    assert src.count("identity_prompt(org, nid)") >= 4, "the lanes no longer share identity_prompt"
    assert "developer_instructions=identity_prompt(org, nid)" in src
    # the tool card exists and its required args are what the dispatch reads
    card = next(t for t in mcptool.TOOLS if t["name"] == "orgtree_work")
    assert card["inputSchema"]["required"] == ["action"]
    acts = set(card["inputSchema"]["properties"]["action"]["enum"])
    assert acts == {"list", "get", "create", "update", "assign", "participants", "evidence",
                    "claim", "verify", "check", "accept", "archive", "supersede",
                    "move"}
    for a in sorted(acts - {"list", "get", "verify", "create"}):
        # `move` needs a destination to get as far as resolving the item; the
        # probe is about DISPATCH (422, not "unknown action"), so give it one
        extra = {"parent": ""} if a == "move" else {}
        st, js = work(slug, "boss", a, slug="w00000000", **extra)
        assert st == 422, (a, st, js)       # every action is dispatched (unknown id, not unknown action)
        assert "action must be" not in js["detail"], (a, js)
    ask = next(t for t in mcptool.TOOLS if t["name"] == "orgtree_ask")
    assert "work_item" in ask["inputSchema"]["properties"]
    assert "work_item" in ask["inputSchema"]["properties"]["questions"]["items"]["properties"]
    assert "action must be" in refused(slug, "boss", "dance")


check("the docket doctrine is in identity_prompt for every agent; the card and dispatch agree",
      doctrine_rides_the_identity_prompt_on_every_lane)


def json_export_survives():
    """JSON compatibility: the doc round-trips through json with the new keys."""
    import json
    slug = fresh_org()
    create(slug)
    org = store.load_org(slug)
    blob = json.dumps(org.d)
    d2 = json.loads(blob)
    assert len(d2["work_items"]) == 1 and d2["work_items"][0]["docket_at"]


check("the docket keys are plain JSON in the org document", json_export_survives)


# ================================== §11 backlog + the mandatory description
print("§11 backlog and description")


def backlog_is_its_own_group_and_out_of_the_active_count():
    slug = fresh_org()
    live = create(slug, title="under way")
    ok(slug, "boss", "update", slug=live, done_so_far=["started"],
       working_on_next=["finish"], status="in_progress")
    back = create(slug, title="not started", status="backlogged")

    js = listing(slug)
    ids = [x["slug"] for x in js["items"]]
    assert ids == [live], f"the backlog must not ride the main list: {ids}"
    assert "backlogged" not in js, "the group is served only with ?backlogged=1"
    # the number the user reads as "work in flight" excludes it
    assert js["counts"]["active"] == 1, js["counts"]
    assert js["counts"]["backlogged"] == 1, js["counts"]
    tree = client.get(f"/api/orgs/{slug}").json()
    assert tree["work_items_summary"]["active"] == 1, tree["work_items_summary"]

    r = client.get(f"/api/orgs/{slug}/work-items?backlogged=1")
    assert r.status_code == 200, r.text
    js2 = r.json()
    assert [x["slug"] for x in js2["backlogged"]] == [back]
    assert [x["slug"] for x in js2["items"]] == [live], \
        "revealing the backlog APPENDS a group; it never re-sorts the main list"
    # POSITIVE CONTROL for the whole check: the same item as `open` IS active
    ok(slug, "boss", "update", slug=back, done_so_far=[], working_on_next=["go"],
       status="open")
    js3 = listing(slug)
    assert js3["counts"]["active"] == 2 and js3["counts"]["backlogged"] == 0, js3["counts"]
    assert back in [x["slug"] for x in js3["items"]]


check("backlogged items are a separate group, hidden by default, and out of the active count",
      backlog_is_its_own_group_and_out_of_the_active_count)


def backlogged_with_attention_stays_findable():
    """The badge must never point at a row the user cannot reach. An item the
    attention count is counting stays in the MAIN list even while backlogged
    — but it still is not `active`, because visible is not the same as in
    flight."""
    slug = fresh_org()
    wid = create(slug, title="parked but flagged", status="backlogged")
    ok(slug, "boss", "update", slug=wid, done_so_far=[], working_on_next=["ask"],
       attention=True, attention_reason="needs a decision before anyone starts")

    js = listing(slug)
    assert [x["slug"] for x in js["items"]] == [wid], \
        "an attention-holding backlog row must not be hidden behind a checkbox"
    assert js["counts"]["attention"] == 1, js["counts"]
    assert js["counts"]["active"] == 0, ("still not in flight", js["counts"])
    assert js["counts"]["backlogged"] == 0, \
        ("the hidden-group count must match what the checkbox would reveal",
         js["counts"])
    r = client.get(f"/api/orgs/{slug}/work-items?backlogged=1")
    assert r.json()["backlogged"] == [], \
        "it is in the main list, so it must not ALSO be in the backlog group"
    # NEGATIVE CONTROL: clear the flag and it drops back out of sight
    ok(slug, "boss", "update", slug=wid, done_so_far=[], working_on_next=["wait"])
    js2 = listing(slug)
    assert js2["items"] == [] and js2["counts"]["backlogged"] == 1, js2["counts"]


check("a backlogged item holding attention stays in the main list but never counts as active",
      backlogged_with_attention_stays_findable)


def backlog_never_archives_and_open_work_is_not_reclassified():
    slug = fresh_org()
    old_back = create(slug, title="parked for a day", status="backlogged")
    old_open = create(slug, title="open and old")
    backdate(slug, old_back, 90000)          # 25 hours
    backdate(slug, old_open, 90000)
    js = client.get(f"/api/orgs/{slug}/work-items?archived=1&backlogged=1").json()
    assert js["archived"] == [], "the sweep is DONE-only; age alone archives nothing"
    assert [x["slug"] for x in js["backlogged"]] == [old_back]
    # the migration hazard: an aged, untouched `open` item stays open and active
    assert [x["slug"] for x in js["items"]] == [old_open]
    assert js["items"][0]["status"] == "open", js["items"][0]["status"]
    assert js["counts"]["active"] == 1, js["counts"]


check("a backlogged item never archives by age, and old open work is never reclassified into the backlog",
      backlog_never_archives_and_open_work_is_not_reclassified)


def order_is_total_so_a_tie_cannot_shuffle():
    """Two items stamped in the same clock tick must come back in the SAME
    order on every poll, or the row under the user's cursor changes between
    two five-second refreshes."""
    slug = fresh_org()
    a = create(slug, title="tie A")
    b = create(slug, title="tie B")
    c = create(slug, title="tie C")
    org = store.load_org(slug)
    for wid in (a, b, c):                    # one identical instant for all three
        it, _ = org._work_find(wid)
        it["docket_at"] = "2026-09-05T10:00:00+00:00"
    # ⚠ THE VACUOUS PASS THIS AVOIDS. Item ids are random uuid hex, so with the
    # rows left in creation order a stable sort would "happen" to be right one
    # time in six and the check would prove nothing about the tie-break. So the
    # stored order is forced to ASCENDING id — the exact opposite of the order
    # a working tie-break must return — and a build without one is then wrong
    # every single run, not sometimes.
    org.d["work_items"].sort(key=lambda it: it["slug"])
    store.save_org(org)
    expected = sorted([a, b, c], reverse=True)
    first = [x["slug"] for x in listing(slug)["items"]]
    assert first == expected, (first, expected)
    for _ in range(5):
        assert [x["slug"] for x in listing(slug)["items"]] == first, \
            "a docket_at tie must break deterministically, not on list position"


check("items tied on docket_at come back in one deterministic order on every read",
      order_is_total_so_a_tie_cannot_shuffle)


def the_description_is_mandatory_and_cannot_be_erased():
    slug = fresh_org()
    detail = refused(slug, "boss", "create", title="no description",
                     done_so_far=[], working_on_next=["go"])
    assert "objective" in detail and "PROBLEM" in detail, detail
    # whitespace is not a description
    assert "objective" in refused(slug, "boss", "create", title="blank",
                                  objective="   ")
    # POSITIVE CONTROL: the same call with one succeeds and serves it back
    wid = create(slug, title="real one",
                 objective="agents create items with no stated problem; "
                           "require the problem first, then the solution")
    assert get_item(slug, wid)["objective"].startswith("agents create items")
    # it may be rewritten
    ok(slug, "boss", "update", slug=wid, done_so_far=[], working_on_next=["x"],
       objective="the problem restated; the new solution")
    assert get_item(slug, wid)["objective"] == "the problem restated; the new solution"
    # but not emptied — otherwise the create guard is trivially bypassable
    d2 = refused(slug, "boss", "update", slug=wid, done_so_far=[],
                 working_on_next=["x"], objective="  ")
    assert "emptied" in d2, d2
    assert get_item(slug, wid)["objective"] == "the problem restated; the new solution"


check("a work item cannot be created without a description, nor have it erased later",
      the_description_is_mandatory_and_cannot_be_erased)


def the_new_rules_reach_the_agents():
    slug = fresh_org()
    org = store.load_org(slug)
    p = supervisor.identity_prompt(org, "worker")
    assert "backlogged" in p, "agents are never told the new status exists"
    assert "PROBLEM" in p and "objective" in p, \
        "agents are never told the description is required, or how to frame it"
    card = next(t for t in mcptool.TOOLS if t["name"] == "orgtree_work")
    props = card["inputSchema"]["properties"]
    assert "include_backlogged" in props
    assert "backlogged" in props["status"]["description"]
    assert "REQUIRED" in props["objective"]["description"]
    # the list action really honours the new flag through the agent route
    back = create(slug, title="agent-visible backlog", status="backlogged")
    assert [x["slug"] for x in ok(slug, "boss", "list").get("backlogged", [])] == []
    assert [x["slug"] for x in
            ok(slug, "boss", "list", include_backlogged=True)["backlogged"]] == [back]


check("the backlog state and the description rule reach agents through the prompt and the tool card",
      the_new_rules_reach_the_agents)


# ============================================ §12 human-readable item slugs
print("§12 slugs")


def a_slug_is_derived_unique_and_stable():
    slug = fresh_org()
    a = create(slug, title="Git review workspace")
    it = get_item(slug, a)
    assert it["slug"] == "git-review-workspace", it["slug"]
    # a second item with the SAME title gets a suffix, not a duplicate name
    b = create(slug, title="Git review workspace")
    assert get_item(slug, b)["slug"] == "git-review-workspace-2"
    c = create(slug, title="Git review workspace")
    assert get_item(slug, c)["slug"] == "git-review-workspace-3"
    assert len({get_item(slug, x)["slug"] for x in (a, b, c)}) == 3
    # STABLE: renaming the item does not re-point a name people have copied
    ok(slug, "boss", "update", slug=a, done_so_far=[], working_on_next=["x"],
       title="Something else entirely")
    after = get_item(slug, a)
    assert after["title"] == "Something else entirely"
    assert after["slug"] == "git-review-workspace", after["slug"]
    # punctuation, unicode and length are handled without producing a blank
    d = create(slug, title="   ***   ")
    assert get_item(slug, d)["slug"] == "item", get_item(slug, d)["slug"]
    e = create(slug, title="x" * 200)
    assert 0 < len(get_item(slug, e)["slug"]) <= 48


check("an item's slug is derived from its title, unique, and fixed once assigned",
      a_slug_is_derived_unique_and_stable)


def a_slug_works_wherever_an_id_does():
    slug = fresh_org()
    wid = create(slug, title="Reply routing check")
    s = get_item(slug, wid)["slug"]
    assert s == "reply-routing-check"
    # every agent action that takes an id takes the slug and hits the SAME item
    ok(slug, "boss", "update", slug=s, done_so_far=["by slug"],
       working_on_next=["more"], status="in_progress")
    assert get_item(slug, wid)["done_so_far"] == ["by slug"]
    assert ok(slug, "boss", "get", slug=s)["item"]["slug"] == wid
    ok(slug, "boss", "evidence", slug=s, kind="note", ref="notes.md",
       note="found by name")
    assert len(get_item(slug, wid)["evidence"]) == 1
    # a dependency GIVEN as a slug is STORED as the opaque id
    dep = create(slug, title="Depends on it", dependencies=[s])
    assert get_item(slug, dep)["dependencies"][0]["slug"] == wid
    # an ask attached by slug still lands on the item (it normalises to the id)
    st, js = agent(slug, "boss", "orgtree_ask",
                   questions=[{"question": "which way?", "work_item": s,
                               "options": [{"label": "left"}, {"label": "right"}]}])
    assert st == 200, js
    assert [q["ask_id"] for q in get_item(slug, wid)["questions"]], \
        "an ask attached by slug must reach the item, not vanish"
    # AUTHORITY IS UNCHANGED: a stranger naming the slug is refused exactly as
    # it is when naming the id, and the refusal does not confirm either exists
    d1 = refused(slug, "stranger", "get", slug=s)
    d2 = refused(slug, "stranger", "get", slug=wid)
    assert "may read" in d1 and "may read" in d2, (d1, d2)


check("a slug is accepted anywhere the opaque id is, without changing authority",
      a_slug_works_wherever_an_id_does)


def a_name_collision_is_resolved_not_lost():
    """Two items asking for the same name. THE PROMISE IS THAT EVERY NAME AN
    ITEM IS GIVEN REACHES THAT ITEM — a collision must be resolved into a
    second usable name, never silently dropped or minted unreachable. Since
    the slug is the only identity, an unreachable name is an unreachable
    ITEM."""
    slug = fresh_org()
    first = create(slug, title="victim")
    second = create(slug, title="victim")
    assert first != second, "two items collapsed onto one name"
    assert ok(slug, "boss", "get", slug=first)["item"]["slug"] == first
    assert ok(slug, "boss", "get", slug=second)["item"]["slug"] == second

    # ...and the same holds once a name has moved into the ARCHIVE, where it
    # lives on and must still not be reused
    org = store.load_org(slug)
    it, _ = org._work_find(first)
    it["status"] = "done"
    store.save_org(org)
    backdate(slug, first, 7200)
    ok(slug, "boss", "update", slug=second, done_so_far=["sweep"], working_on_next=[])
    third = create(slug, title="victim")
    assert third not in (first, second), f"reused a taken name: {third!r}"
    assert ok(slug, "boss", "get", slug=third)["item"]["slug"] == third


check("a name collision is resolved into a reachable second name, archive included",
      a_name_collision_is_resolved_not_lost)


def a_name_shaped_like_an_old_id_still_resolves():
    """⚠ THE TRAP IN RETIRING THE OPAQUE ID. Old references are refused with
    guidance, and the cheap way to do that is to reject anything matching
    `^w[0-9a-f]{8}$` on sight. That stranding is real: a title of "W1234abcd"
    slugifies to exactly `w1234abcd`, which is a perfectly legal name for a
    perfectly real item.

    The rule that makes both work is ORDER — resolve the name first, and only
    consult the shape once the lookup has already failed. This test is the
    control on that ordering (Astra review 2026-09-05)."""
    slug = fresh_org()
    victim = create(slug, title="W1234abcd")
    assert victim == "w1234abcd",         f"the fixture is pointless unless the name really looks like an id: {victim!r}"

    # it resolves, on every route, exactly as any other name does
    assert ok(slug, "boss", "get", slug="w1234abcd")["item"]["slug"] == victim
    assert ok(slug, "boss", "update", slug="w1234abcd",
              done_so_far=["still reachable"], working_on_next=[])["updated"] == victim

    # ...while a reference of the same SHAPE that names nothing is refused, and
    # says why rather than reporting a bare miss
    st, js = work(slug, "boss", "get", slug="wdeadbeef")
    assert st != 200, js
    detail = str(js.get("detail") or js)
    assert "retired" in detail and "readable slug" in detail, detail


check("a name that looks like an old opaque id still resolves; only a name "
      "that resolves to nothing gets the stale-id guidance",
      a_name_shaped_like_an_old_id_still_resolves)


def _forge_legacy_doc(slug, pairs):
    """Rewrite a fresh org into the shape a PRE-MIGRATION document has: every
    item carries an opaque `id`, and an item may have no slug at all, which is
    how one that predates slugs is really stored. `pairs` is
    [(current_name, keep_slug)]. Returns {current_name: old_id}."""
    org = store.load_org(slug)
    old = {}
    for i, (name, keep) in enumerate(pairs):
        it, _ = org._work_find(name)
        wid = "w%08x" % (0xdead0000 + i)
        it["id"] = wid          # type: ignore[typeddict-unknown-key]
        old[name] = wid
        if not keep:
            del it["slug"]      # type: ignore[misc]
    store.save_org(org)
    return old


def a_legacy_document_converts_once_and_only_once():
    """THE MIGRATION, end to end, on a forged pre-migration document.

    What is pinned here is not "it renames things" — it is the four properties
    that make a one-way identity change safe to run on somebody's real data: a
    read never serves a half-converted document, existing names are preserved
    rather than reassigned, pointers follow, and a second run does nothing."""
    slug = fresh_org()
    keep = create(slug, title="Keeps its name")
    lost = create(slug, title="Predates slugs")
    dep = create(slug, title="Depends on the other two")
    old = _forge_legacy_doc(slug, [(keep, True), (lost, False), (dep, True)])

    # the dependency pointers as an old document stored them: by id
    org = store.load_org(slug)
    it, _ = org._work_find(dep)      # by its NAME — old ids never resolve
    it["dependencies"] = [old[keep], old[lost]]
    store.save_org(org)

    # (1) A READ REFUSES rather than serving two kinds of name
    r = client.get("/api/orgs/%s/work-items" % slug)
    assert r.status_code == 409, (r.status_code, r.text)
    assert "readable name" in r.text, r.text
    st, js = work(slug, "boss", "list")
    assert st != 200 and "readable name" in str(js), js

    # (2) THE CONVERSION
    r = client.post("/api/orgs/%s/migrate-work-identity" % slug)
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["already"] is False and rep["items"] == 3, rep
    assert rep["dangling"] == [], rep

    doc = store.load_org(slug)
    names = {str(x["slug"]) for x in doc._work_all()}
    assert keep in names, "an EXISTING name was not preserved"
    assert "predates-slugs" in names, "a missing name was not minted from the title"
    assert all("id" not in x for x in doc._work_all()), "an opaque key survived"

    # (3) POINTERS FOLLOW. The dependency named two items by id; both now name
    #     them by the name those items actually carry.
    it, _ = doc._work_find(dep)
    assert it["dependencies"] == [keep, "predates-slugs"], it["dependencies"]
    listed = get_item(slug, dep)["dependencies"]
    assert [d["visible"] for d in listed] == [True, True], listed

    # (4) THE OLD ID IS DEAD, and says so rather than reporting a bare miss
    st, js = work(slug, "boss", "get", slug=old[keep])
    assert st != 200, js
    assert "retired" in str(js) and "readable slug" in str(js), js

    # (5) A SECOND PASS IS A NO-OP — not "harmless", literally nothing
    before = json.dumps(store.load_org(slug).d, sort_keys=True, default=str)
    r2 = client.post("/api/orgs/%s/migrate-work-identity" % slug)
    assert r2.status_code == 200 and r2.json() == {"already": True}, r2.text
    after = json.dumps(store.load_org(slug).d, sort_keys=True, default=str)
    assert before == after, "a second migration pass rewrote the document"


check("a legacy document converts once: reads refuse first, existing names "
      "survive, pointers follow, old ids die loudly, and a second pass is a no-op",
      a_legacy_document_converts_once_and_only_once)


def a_refused_migration_leaves_the_stored_document_untouched():
    """FAIL BEFORE SAVE. Two items already carrying the same name is refused
    rather than resolved — either answer (rename one, or let one shadow the
    other) silently breaks a reference somebody has written down. What this
    check is really for is the ATOMICITY claim: the transform edits memory
    only and the caller saves, so a refusal must leave storage untouched."""
    slug = fresh_org()
    a = create(slug, title="Twin")
    b = create(slug, title="Twin")           # -> twin-2
    assert a != b
    _forge_legacy_doc(slug, [(a, True), (b, True)])
    # force the collision an old document could genuinely contain
    org = store.load_org(slug)
    it, _ = org._work_find(b)
    it["slug"] = a
    store.save_org(org)

    before = json.dumps(store.load_org(slug).d, sort_keys=True, default=str)
    r = client.post("/api/orgs/%s/migrate-work-identity" % slug)
    assert r.status_code == 422, (r.status_code, r.text)
    assert "already carry the slug" in r.text, r.text
    after = json.dumps(store.load_org(slug).d, sort_keys=True, default=str)
    assert before == after, "a REFUSED migration still wrote to the document"

    # POSITIVE CONTROL: the same document converts once the collision is gone,
    # so the refusal above is about the collision and not about the fixture
    org = store.load_org(slug)
    target = None
    for x in org._work_all():
        if x.get("slug") == a:
            target = x if target is not None else target or None
    seen = False
    for x in org._work_all():
        if x.get("slug") == a:
            if seen:
                x["slug"] = "twin-2"
            seen = True
    store.save_org(org)
    r = client.post("/api/orgs/%s/migrate-work-identity" % slug)
    assert r.status_code == 200 and r.json()["already"] is False, r.text


check("a refused migration leaves the stored document byte-identical, and the "
      "same document converts once the collision is gone",
      a_refused_migration_leaves_the_stored_document_untouched)


def the_backup_is_written_before_the_first_migrating_save():
    """The rollback route is a JSON export of the document AS IT STOOD. It has
    to be taken from committed state BEFORE the converting save, or it is a
    backup of the very thing you would be undoing."""
    slug = fresh_org()
    wid = create(slug, title="Backed up")
    old = _forge_legacy_doc(slug, [(wid, True)])

    exports = os.path.join(store.DATA_ROOT, "exports")
    before = set(os.listdir(exports)) if os.path.isdir(exports) else set()
    r = client.post("/api/orgs/%s/migrate-work-identity" % slug)
    assert r.status_code == 200, r.text
    made = sorted(set(os.listdir(exports)) - before)
    assert len(made) == 1, made
    with open(os.path.join(exports, made[0]), encoding="utf-8") as fh:
        dump = json.load(fh)
    saved = (dump.get("work_items") or []) + (dump.get("work_items_archive") or [])
    assert [x.get("id") for x in saved] == [old[wid]], \
        "the backup does not hold the PRE-migration shape"
    assert "work_identity" not in dump, \
        "the backup was taken after the conversion, not before it"


check("the pre-migration backup holds the document as it stood, not as it "
      "became", the_backup_is_written_before_the_first_migrating_save)


def a_write_that_skipped_the_conversion_is_refused_by_the_ledger():
    """⚠ THE GUARD THAT EXISTS BECAUSE ONE LINE IS EASY TO MISS. The
    conversion has to run inside the mutating route's own lock and save. A
    route that forgets writes a document half in the old identity and half in
    the new, which then refuses the very next read — codex-delivery hit
    exactly that with its own repair route on a trial rebase: 200, mutated,
    saved, and the next GET 409'd, with every one of its tests still green.

    So the refusal lives at the head of every docket mutation in the LEDGER,
    where no route can route around it. This calls the ledger directly,
    bypassing the API helper, because that is precisely the mistake."""
    slug = fresh_org()
    wid = create(slug, title="Predates the conversion")
    _forge_legacy_doc(slug, [(wid, True)])

    org = store.load_org(slug)
    assert org.work_identity_state() == "legacy", "the fixture did not forge one"
    try:
        org.work_update("boss", wid, ["a sneaky write"], [])
    except LedgerError as e:
        assert "converted" in str(e), e
    else:
        raise AssertionError(
            "a docket write went through on an unconverted document")

    # POSITIVE CONTROL: the identical write succeeds once the conversion has
    # run, so the refusal is about the identity state and not about the write
    org.work_identity_migrate()
    store.save_org(org)
    ok(slug, "boss", "update", slug=wid, done_so_far=["fine now"],
       working_on_next=[])


check("a docket write on an unconverted document is refused by the ledger, "
      "not left to each route to remember",
      a_write_that_skipped_the_conversion_is_refused_by_the_ledger)


def sub_items_are_independent_items_in_a_tree():
    """The parent relation, and the three things it deliberately is NOT.

    The approved design says children stay independent: their own owner,
    status, name and authority. So this pins the SHAPE (a tree, one parent,
    no cycles) and then pins the absences, because "nesting quietly became a
    permission edge" is the failure that would not look like a failure."""
    slug = fresh_org()
    parent = create(slug, title="Docket improvements")
    child = create(slug, title="Grouping and filters", parent=parent)
    assert get_item(slug, child)["parent"] == parent
    assert get_item(slug, parent)["parent"] is None, "a root grew a parent"

    # move to the top, and back under
    assert ok(slug, "boss", "move", slug=child, parent="")["parent"] is None
    assert get_item(slug, child)["parent"] is None
    assert ok(slug, "boss", "move", slug=child, parent=parent)["parent"] == parent

    # ⚠ NESTING IS NOT A LIFECYCLE EDGE. Accepting the parent must not touch
    # the child, and vice versa — a parent is completed explicitly, after the
    # whole outcome is delivered.
    ok(slug, "boss", "update", slug=child, status="review",
       done_so_far=["done"], working_on_next=[])
    assert get_item(slug, parent)["status"] != "review", \
        "the parent's status followed its child's"


def a_move_needs_an_explicit_destination():
    """⚠ ABSENT AND EMPTY ARE DIFFERENT. `parent: ""` says "put this at the
    top"; omitting it is a caller that forgot, and promoting an item to the
    top because a field was missing is a data change nobody asked for."""
    slug = fresh_org()
    parent = create(slug, title="Parent item")
    child = create(slug, title="Child item", parent=parent)
    assert "needs `parent`" in refused(slug, "boss", "move", slug=child)
    assert get_item(slug, child)["parent"] == parent, \
        "a move with no destination moved the item anyway"


def a_cycle_is_refused_at_every_depth():
    """A cycle makes every item on the ring unreachable from the top of the
    list and hangs any renderer that walks the tree. Refused on write, at
    depth, and including the one-item case."""
    slug = fresh_org()
    a = create(slug, title="Grandparent")
    b = create(slug, title="Parent", parent=a)
    c = create(slug, title="Child", parent=b)

    assert "its own parent" in refused(slug, "boss", "move", slug=a, parent=a)
    assert "own subtree" in refused(slug, "boss", "move", slug=a, parent=c), \
        "a three-deep cycle was allowed"
    assert "own subtree" in refused(slug, "boss", "move", slug=a, parent=b)
    # POSITIVE CONTROL: a move that is NOT a cycle still works, so the refusals
    # above are about the cycle and not about moving at all
    d = create(slug, title="Elsewhere")
    assert ok(slug, "boss", "move", slug=d, parent=c)["parent"] == c


def a_bad_parent_refuses_the_creation_outright():
    """Resolved BEFORE the item is appended: a create naming a parent that
    does not exist must leave nothing behind, or the docket accumulates
    stranded items from failed calls."""
    slug = fresh_org()
    before = len(listing(slug)["items"])
    st, js = work(slug, "boss", "create", title="Orphan",
                  objective="a problem; a solution", parent="no-such-item")
    assert st != 200, js
    assert len(listing(slug)["items"]) == before, \
        "a refused create left a stranded item behind"


def nesting_needs_manage_on_the_child_and_read_on_the_parent():
    """READ on the parent is the bar, not manage: requiring manage would stop
    a subordinate filing its own item under its coordinator's, which is the
    ordinary case. Requiring nothing would let an agent attach work under an
    item it cannot see."""
    # the fixture's tree is boss > mid > worker, with `peer` top-level and
    # unrelated — so the boss is NOT the peer's superior and holds nothing
    # over the peer's items. That is what makes this a real test.
    slug = fresh_org()
    mine = create(slug, node="peer", title="Peer own work")
    secret = create(slug, node="boss", title="Boss only work")
    # peer cannot even read the boss's item, so it cannot nest under it — and
    # the refusal is the same indistinguishable one a nonexistent name gets
    msg = refused(slug, "peer", "move", slug=mine, parent=secret)
    assert "may read" in msg, msg

    # POSITIVE CONTROL, and the case the READ-not-MANAGE bar exists for: make
    # the peer a PARTICIPANT on the boss's item. It may now read that item but
    # still not manage it — and it manages its own — so the nesting succeeds.
    ok(slug, "boss", "participants", slug=secret, add=["peer"])
    assert ok(slug, "peer", "move", slug=mine, parent=secret)["parent"] == secret
    # ...and it still cannot manage the parent, so this is read right and not
    # something the move quietly granted
    assert "owner-level" in refused(slug, "peer", "update", slug=secret,
                                    status="dropped", done_so_far=["x"],
                                    working_on_next=[])


def a_parent_you_may_not_read_is_named_to_nobody():
    """The disclosure rule, once more: a parent's name is derived from its
    title, so a viewer who may not read the parent learns that one EXISTS —
    the row must not read as top-level work — and nothing else."""
    slug = fresh_org()
    secret = create(slug, node="boss", title="Boss only parent")
    child = create(slug, node="boss", title="Visible child", parent=secret)
    ok(slug, "boss", "participants", slug=child, add=["peer"])

    seen = ok(slug, "peer", "get", slug=child)["item"]
    assert seen["parent"] is None, "the hidden parent's NAME reached a reader"
    assert seen["parent_visible"] is False, \
        "the reader cannot tell a parent exists at all, so the row reads as top-level"
    hit = [k for k, v in seen.items() if secret in json.dumps(v)]
    assert not hit, f"the hidden parent's name leaked through {hit}"

    # POSITIVE CONTROL: the same field IS served to someone who may read it
    full = get_item(slug, child)
    assert full["parent"] == secret and full["parent_visible"] is True


check("sub-items are independent items in a tree, and nesting is not a "
      "lifecycle edge", sub_items_are_independent_items_in_a_tree)
check("a move needs an explicit destination — absent is not empty",
      a_move_needs_an_explicit_destination)
check("a cycle is refused at every depth, and ordinary moves still work",
      a_cycle_is_refused_at_every_depth)
check("a create naming a parent that does not exist leaves nothing behind",
      a_bad_parent_refuses_the_creation_outright)
check("nesting needs manage on the child and read on the parent",
      nesting_needs_manage_on_the_child_and_read_on_the_parent)
check("a parent you may not read is named to nobody, but its existence is "
      "still admitted", a_parent_you_may_not_read_is_named_to_nobody)


def the_marker_is_not_evidence_the_records_are():
    """⚠ COUNTEREXAMPLE EXECUTED BY coordinator-astra, 2026-09-05.

    `work_identity_state` used to check the durable marker FIRST and return
    early, which made the marker the only thing that mattered: a document
    whose marker was set while a child still carried an opaque key reported
    `slug` and was served as MIXED IDENTITY. The docstring on that very
    function claimed the marker was not the sole evidence. It was.

    An old-build round trip or a partially restored document produces exactly
    that shape, so this is not a hypothetical. The state is now derived from
    the RECORDS and the marker is not consulted at all."""
    slug = fresh_org()
    parent = create(slug, title="Marked parent")
    child = create(slug, title="Unconverted child")

    org = store.load_org(slug)
    it, _ = org._work_find(child)
    it["id"] = "wdeadbeef"          # type: ignore[typeddict-unknown-key]
    org.d["work_identity"] = "slug"  # the marker LIES
    store.save_org(org)

    assert store.load_org(slug).work_identity_state() == "legacy", \
        "a set marker was taken as proof while a record still held an old key"
    # the read path must refuse it rather than serve two kinds of name
    assert client.get(f"/api/orgs/{slug}/work-items").status_code == 409

    # POSITIVE CONTROL 1: with the record actually clean, the same document is
    # slug-keyed whether or not the marker is there
    org = store.load_org(slug)
    it, _ = org._work_find(child)
    del it["id"]                    # type: ignore[misc]
    del org.d["work_identity"]      # type: ignore[misc]
    store.save_org(org)
    assert store.load_org(slug).work_identity_state() == "slug", \
        "clean records were called legacy because the marker was missing"
    assert client.get(f"/api/orgs/{slug}/work-items").status_code == 200

    # ⚠ AND A POINTER IS NOT EVIDENCE EITHER. My first correction judged
    # pointers by SHAPE and made this legacy — the same mistake `_work_find`
    # exists to avoid, one function away: an item can legally be named
    # `w1234abcd`, so a pointer at it would read as unconverted forever.
    # Canonical-or-dead; the two dedicated checks below drive both.
    org = store.load_org(slug)
    it, _ = org._work_find(parent)
    it["dependencies"] = ["wdeadbeef"]
    store.save_org(org)
    assert store.load_org(slug).work_identity_state() == "slug", \
        "a dangling pointer was mistaken for old identity"


def an_empty_org_needs_no_migration():
    """The other end of the same rule: a document with no work items has
    nothing to convert, and refusing to serve its empty docket until someone
    ran a migration would answer a question the document already satisfies."""
    slug = fresh_org()
    assert store.load_org(slug).work_identity_state() == "slug"
    assert client.get(f"/api/orgs/{slug}/work-items").status_code == 200


def a_move_records_the_parent_it_actually_had():
    """⚠ COUNTEREXAMPLE EXECUTED BY coordinator-astra, 2026-09-05: moving
    gamma from alpha to beta recorded `from: null`, claiming it had been at
    the top level. The prior parent was read AFTER it had been overwritten.
    History that reads correctly and says something false."""
    slug = fresh_org()
    alpha = create(slug, title="Alpha")
    beta = create(slug, title="Beta")
    gamma = create(slug, title="Gamma", parent=alpha)

    ok(slug, "boss", "move", slug=gamma, parent=beta)
    moves = [h for h in get_item(slug, gamma)["history"] if h.get("op") == "move"]
    assert moves, "the move was not recorded at all"
    assert moves[-1]["from"] == alpha and moves[-1]["to"] == beta, moves[-1]

    # and the root case still records honestly in both directions
    ok(slug, "boss", "move", slug=gamma, parent="")
    last = [h for h in get_item(slug, gamma)["history"] if h.get("op") == "move"][-1]
    assert last["from"] == beta and last["to"] is None, last


def passing_the_retired_argument_is_refused_even_alongside_slug():
    """⚠ COUNTEREXAMPLE EXECUTED BY coordinator-astra, 2026-09-05.

    `_work_ref` refused `id` only when `slug` was ABSENT, so a call sending
    BOTH was quietly served from `slug` while its `id` said something else —
    the contract the function's own docstring states, contradicted by the
    function. Refused by PRESENCE now."""
    slug = fresh_org()
    wid = create(slug, title="Gamma item")

    st, js = work(slug, "boss", "get", id="other", slug=wid)
    assert st != 200, ("both arguments were accepted", js)
    assert "Drop the `id`" in str(js), js
    # ...including an explicitly null one, which is still the caller saying it
    st, js = work(slug, "boss", "get", id=None, slug=wid)
    assert st != 200, ("a null `id` alongside `slug` was accepted", js)

    # POSITIVE CONTROL: `slug` alone works, and a canonical name that happens
    # to be SHAPED like a retired id is still perfectly valid as `slug`
    assert ok(slug, "boss", "get", slug=wid)["item"]["slug"] == wid
    odd = create(slug, title="W1234abcd")
    assert odd == "w1234abcd", odd
    assert ok(slug, "boss", "get", slug=odd)["item"]["slug"] == odd


check("the marker is not evidence — the records are",
      the_marker_is_not_evidence_the_records_are)
check("an empty org needs no migration", an_empty_org_needs_no_migration)
check("a move records the parent it actually had",
      a_move_records_the_parent_it_actually_had)
check("passing the retired `id` argument is refused even alongside `slug`",
      passing_the_retired_argument_is_refused_even_alongside_slug)


def a_canonical_name_shaped_like_an_old_id_is_usable_as_a_POINTER():
    """⚠ COUNTEREXAMPLE EXECUTED BY coordinator-astra, 2026-09-05 — the SAME
    ordering mistake `_work_find` exists to avoid, reintroduced one function
    away. `work_identity_state` judged pointers by SHAPE, so an item legally
    named `w1234abcd` made every item pointing AT it read as unconverted: the
    move succeeded, the next read 409'd, and the next mutation drove a
    migration that had nothing to convert. An unusable docket, forever.

    A pointer is only ever canonical-or-dead. Membership decides, never shape.
    Driven through the real routes end to end, because that loop is a
    route-level behaviour."""
    slug = fresh_org()
    odd = create(slug, title="W1234abcd")
    assert odd == "w1234abcd", odd
    child = create(slug, title="Child of the odd one", parent=odd)
    dependant = create(slug, title="Depends on the odd one", dependencies=[odd])

    # the pointers are stored, and the document is NOT legacy because of them
    assert store.load_org(slug).work_identity_state() == "slug", \
        "an item pointing at a legally w-hex-named item read as unconverted"

    # THE NEXT READ, over the real route
    r = client.get(f"/api/orgs/{slug}/work-items")
    assert r.status_code == 200, (r.status_code, r.text)
    assert get_item(slug, child)["parent"] == odd
    assert get_item(slug, dependant)["dependencies"][0]["slug"] == odd

    # THE NEXT MUTATION, which is where the loop closed
    ok(slug, "boss", "update", slug=child, done_so_far=["still fine"],
       working_on_next=[])
    ok(slug, "boss", "move", slug=child, parent="")
    ok(slug, "boss", "move", slug=child, parent=odd)
    assert client.get(f"/api/orgs/{slug}/work-items").status_code == 200

    # POSITIVE CONTROL: an ordinary name behaves identically, so nothing above
    # passes merely because the checks stopped noticing anything
    plain = create(slug, title="Ordinary parent")
    ok(slug, "boss", "move", slug=child, parent=plain)
    assert get_item(slug, child)["parent"] == plain


def two_items_answering_to_one_name_is_legacy_and_refuses():
    """⚠ ALSO FROM THAT REVIEW: a document with duplicate names and NO opaque
    keys reported `slug`, so it was served as converted and never reached the
    migration's duplicate refusal. Ambiguity is exactly what must not be
    served — two items answering to one name means every reference to it is a
    coin toss."""
    slug = fresh_org()
    a = create(slug, title="Twin")
    b = create(slug, title="Twin")          # -> twin-2
    org = store.load_org(slug)
    it, _ = org._work_find(b)
    it["slug"] = a                          # forge the collision, no ids at all
    store.save_org(org)

    assert all("id" not in x for x in store.load_org(slug)._work_all()), \
        "the fixture is pointless unless there are no opaque keys left"
    assert store.load_org(slug).work_identity_state() == "legacy", \
        "a duplicate name was served as a converted document"
    assert client.get(f"/api/orgs/{slug}/work-items").status_code == 409

    # and the migration refuses it rather than picking a winner — twice, with
    # nothing written either time
    before = json.dumps(store.load_org(slug).d, sort_keys=True, default=str)
    for _ in range(2):
        r = client.post(f"/api/orgs/{slug}/migrate-work-identity")
        assert r.status_code == 422 and "already carry the slug" in r.text, r.text
    assert json.dumps(store.load_org(slug).d, sort_keys=True,
                      default=str) == before, \
        "a refused migration wrote to the document"


def a_dangling_pointer_never_loops_the_document():
    """A pointer naming nothing is a DATA DEFECT, not an identity state. If it
    made the document legacy, the read would 409, the mutation would drive a
    migration, the migration would find nothing to convert — and the next read
    would 409 again, forever. `_work_view` already reports an unresolvable
    dependency as an invisible one; that is the honest handling."""
    slug = fresh_org()
    wid = create(slug, title="Points at a ghost")
    org = store.load_org(slug)
    it, _ = org._work_find(wid)
    it["dependencies"] = ["wdeadbeef", "also-not-a-real-name"]
    store.save_org(org)

    assert store.load_org(slug).work_identity_state() == "slug", \
        "a dangling pointer was mistaken for old identity"
    assert client.get(f"/api/orgs/{slug}/work-items").status_code == 200
    # served honestly: it exists, it is not readable, and it is not named
    deps = get_item(slug, wid)["dependencies"]
    assert deps == [{"visible": False}, {"visible": False}], deps
    # ...and the document stays usable rather than cycling through migrations
    ok(slug, "boss", "update", slug=wid, done_so_far=["no loop"],
       working_on_next=[])
    r = client.post(f"/api/orgs/{slug}/migrate-work-identity")
    assert r.status_code == 200 and r.json() == {"already": True}, r.text


check("a canonical name shaped like an old id works as a POINTER, through "
      "the real routes, on the next read and the next mutation",
      a_canonical_name_shaped_like_an_old_id_is_usable_as_a_POINTER)
check("two items answering to one name is legacy, and the migration refuses "
      "it twice without writing",
      two_items_answering_to_one_name_is_legacy_and_refuses)
check("a dangling pointer never loops the document",
      a_dangling_pointer_never_loops_the_document)


def agents_are_told_to_use_the_slug():
    slug = fresh_org()
    org = store.load_org(slug)
    p = supervisor.identity_prompt(org, "worker")
    assert "SLUG" in p and "git-review-workspace" in p, \
        "agents are never told the readable name IS the identity"
    assert "retired" in p and "w########" in p, \
        "agents are not warned that an id carried from an older context is dead"
    card = next(t for t in mcptool.TOOLS if t["name"] == "orgtree_work")
    props = card["inputSchema"]["properties"]
    # THE ARGUMENT IS `slug`, AND THERE IS NO `id` TO FALL BACK ON. A card
    # offering both would put the retired identity back in front of every
    # agent that reads the catalogue.
    assert "id" not in props, "the retired `id` argument is still on the card"
    assert "readable name" in props["slug"]["description"], props["slug"]
    # create hands the name straight back, in the payload and in the words
    js = ok(slug, "boss", "create", title="Name me back",
            objective="agents cannot cite an item they were not told the name of; return it")
    assert js["slug"] == "name-me-back", js
    assert "name-me-back" in js["status"], js["status"]


check("the slug reaches agents: in the prompt, on the tool card, and in create's own answer",
      agents_are_told_to_use_the_slug)


# ====================== §13 `review` is the AGENT check; the user's is attention
print("§13 agent review vs user attention")


def an_item_waiting_only_on_the_user_is_accepted_where_it_stands():
    """`review` means review BY AGENTS (user ruling 2026-09-05), so nothing may
    demand it before `accept`. An item that was only ever waiting on the user —
    blocked, flag raised — is accepted from where it stands.

    ⚠ THE CONTROLS ARE THE POINT. "accept succeeded" is worth nothing on its own
    unless `accept` still refuses the things it is supposed to refuse, so the
    two live guards are fired in the same check: the owner may never accept its
    own item, and a closed one is refused."""
    slug = fresh_org()
    wid = create(slug, node="mid", owner="worker")
    ok(slug, "worker", "update", slug=wid, status="blocked",
       blocked_reason="waiting on the user's call about the extra switch",
       attention=True,
       attention_reason="asked for CSV; delivered CSV plus a TSV switch I added "
                        "— confirm the extra or I strip it",
       done_so_far=["exporter written"], working_on_next=["your call"])
    it = get_item(slug, wid)
    assert it["status"] == "blocked" and it["effective_attention"]
    # it has NEVER been in review — no history row moved it there
    assert not [h for h in it["history"]
                if (h.get("changes") or {}).get("status", {}).get("to") == "review"], \
        it["history"]
    r = client.post(f"/api/orgs/{slug}/work-items/{wid}/accept",
                    json={"note": "keep the switch"})
    assert r.status_code == 200, r.text
    it = get_item(slug, wid)
    assert it["status"] == "done" and it["accepted"]["by"] == USER
    # CONTROL 1 — the owner is still refused, from the same blocked status
    wid2 = create(slug, node="mid", owner="worker")
    ok(slug, "worker", "update", slug=wid2, status="blocked",
       blocked_reason="same shape", done_so_far=["x"], working_on_next=["y"])
    assert "superior" in refused(slug, "worker", "accept", slug=wid2)
    # CONTROL 2 — an ancestor may, and only once
    assert ok(slug, "mid", "accept", slug=wid2)["accepted"] == wid2
    assert "already done" in refused(slug, "mid", "accept", slug=wid2)


check("an item waiting only on the user is accepted without ever entering `review`",
      an_item_waiting_only_on_the_user_is_accepted_where_it_stands)


def the_attention_reason_holds_the_specifics_it_must_now_carry():
    """The reason has to name requested-against-delivered, the decision or edge
    case added, and the confirmation wanted (user 2026-09-05) — three things at
    once, inside the 500-character field. Both halves of that matter, so both
    are measured: a real three-part reason survives whole, INCLUDING the line
    breaks the detail pane renders, and one character past the cap is still
    cut — otherwise "it came back whole" would only be saying that nothing
    truncates anything."""
    slug = fresh_org()
    wid = create(slug)
    cap = store.load_org(slug).WORK_ATTENTION_REASON_MAX
    assert cap == 500, "the cap the reason has to fit inside changed"
    three = ("REQUESTED: a CSV export.\n"
             "DELIVERED: a CSV export, plus a TSV switch.\n"
             "BEYOND SPEC: the TSV switch is mine, not yours — the importer "
             "here rejects commas inside quoted fields, so CSV alone loses "
             "rows on the only file you gave me, and re-quoting them changes "
             "what the downstream sheet reads back for every free-text "
             "column.\n"
             "CONFIRM: keep the TSV switch, or strip it back to CSV only and "
             "accept the dropped rows?")
    # near the ceiling on purpose: a short reason would fit under any cap and
    # prove nothing about whether all three parts can be written at once
    assert 300 < len(three) <= cap, len(three)
    ok(slug, "boss", "update", slug=wid, attention=True, attention_reason=three,
       done_so_far=["exporter written"], working_on_next=["your call"])
    got = get_item(slug, wid)["manual_attention"]["reason"]
    assert got == three, (len(got), len(three))
    assert got.count("\n") == 3, "the line structure the pane renders is kept verbatim"
    # CONTROL — the cap is real, so the assert above is not passing because
    # nothing truncates at all
    ok(slug, "boss", "update", slug=wid, attention=True,
       attention_reason="z" * (cap + 1),
       done_so_far=["x"], working_on_next=["y"])
    assert len(get_item(slug, wid)["manual_attention"]["reason"]) == cap
    # blank is still refused, and the refusal now says what the field must hold
    d = refused(slug, "boss", "update", slug=wid, attention=True,
                attention_reason="   ", done_so_far=["x"], working_on_next=["y"])
    assert "what was asked" in d and "confirmation" in d, d


check("a three-part attention reason survives whole, line breaks included, inside the 500 cap",
      the_attention_reason_holds_the_specifics_it_must_now_carry)


#: every sentence of the 2026-09-05 rulings that has to reach an agent, as it
#: must read in the prompt. The control below blanks the doctrine and requires
#: ALL of them to disappear — a phrase that survives is a phrase this check was
#: never testing.
POLICY_PHRASES = (
    "TO DOCK SOMETHING",                 # the user's verb, recorded verbatim
    "PUT A NEW FEATURE ON THE DOCKET",
    "REVIEW BY AGENTS",
    "NEVER THE `review` STATUS",
    "a QUESTION is an attached orgtree_ask",   # not the same door as the flag
    "is NOT a question",
    "BEYOND the stated spec",            # trigger 1
    "specialized edge case",             # trigger 2
    "definition gap",                    # trigger 3
    "Being visible in the UI is NOT a reason",
    "neither is who owns the item",
    "no further acceptance round is needed",
    "Never CLAIM an exact match",
    "`Ready for review`",
    "ASK BEFORE YOU BUILD ON THE ANSWER",
    "the wrong order",
)


def the_ruling_reaches_agents_where_they_read_it():
    slug = fresh_org()
    org = store.load_org(slug)
    for nid in ("boss", "worker"):
        p = supervisor.identity_prompt(org, nid)
        for phrase in POLICY_PHRASES:
            assert phrase in p, (nid, phrase)
    # ⚠ POSITIVE CONTROL. Every assert above is a presence check, and a phrase
    # that happens to occur elsewhere in the prompt would satisfy one without
    # the doctrine teaching anything. Blank the doctrine and require the whole
    # list to vanish: whatever survives, this check was never measuring.
    keep = supervisor.DOCKET_DOCTRINE
    try:
        supervisor.DOCKET_DOCTRINE = ""
        blank = supervisor.identity_prompt(org, "boss")
    finally:
        supervisor.DOCKET_DOCTRINE = keep
    for phrase in POLICY_PHRASES:
        assert phrase not in blank, \
            f"{phrase!r} is in the prompt without the doctrine — that assert is inert"
    # the tool card teaches the same thing to an agent reading the catalogue
    card = next(t for t in mcptool.TOOLS if t["name"] == "orgtree_work")
    d = card["description"]
    assert "REVIEW BY AGENTS" in d and "ATTENTION mechanism" in d, d
    assert "does not pass through `review` first" in d, d
    props = card["inputSchema"]["properties"]
    assert "REVIEW BY AGENTS" in props["status"]["description"]
    assert "not enough" in props["attention_reason"]["description"]


check("the agent-review ruling is in every agent's prompt and on the tool card",
      the_ruling_reaches_agents_where_they_read_it)
# ------------------------------------------------- §14 the status clock
#
# `updated_at` moves on any mutation and `docket_at` on any docket update, so
# neither can answer "what has actually MOVED?" — a progress note, a retitle
# or an attention flag advances both without a state changing at all. These
# checks pin the third clock: it moves on a real transition and ONLY on one.


def _status_at(slug, wid):
    return get_item(slug, wid)["status_at"]


def the_status_clock_moves_only_on_a_real_transition():
    slug = fresh_org()
    wid = create(slug)
    born = _status_at(slug, wid)
    assert born, "an item is created with a status, so it has a status time"
    assert born == get_item(slug, wid)["at"],         "creation IS the first status change — the item has held that status "         "since it existed"

    # a PROGRESS update: both lists rewritten, the same status restated
    time.sleep(0.01)
    ok(slug, "boss", "update", slug=wid, status="open",
       done_so_far=["still reading"], working_on_next=["still writing"])
    after_note = get_item(slug, wid)
    assert after_note["status_at"] == born,         "a progress note that RESTATES the status moved the status clock"
    # …and the control: the ordinary clocks DID move, so the check above is
    # not passing because the update never landed
    assert after_note["docket_at"] > born, "the docket clock did not move at all"

    # a real transition
    time.sleep(0.01)
    ok(slug, "boss", "update", slug=wid, status="blocked",
       blocked_reason="waiting on review",
       done_so_far=["read"], working_on_next=["wait"])
    moved = _status_at(slug, wid)
    assert moved > born, "a genuine status change did not move the status clock"

    # a retitle changes no state
    time.sleep(0.01)
    ok(slug, "boss", "update", slug=wid, title="a better title",
       done_so_far=["read"], working_on_next=["wait"])
    assert _status_at(slug, wid) == moved, "a retitle moved the status clock"


check("the status clock moves on a transition and not on a note or a retitle",
      the_status_clock_moves_only_on_a_real_transition)


def accept_reopen_and_dismissal_all_count_as_transitions():
    """The three transitions nobody types a status to reach. A clock that
    only watched `update` would sit still through all of them."""
    slug = fresh_org()
    wid = create(slug, node="boss", owner="mid")
    ok(slug, "mid", "update", slug=wid, status="review",
       done_so_far=["built it"], working_on_next=["await acceptance"])
    at_review = _status_at(slug, wid)

    time.sleep(0.01)
    ok(slug, "boss", "accept", slug=wid)
    at_done = _status_at(slug, wid)
    assert at_done > at_review, "accept did not move the status clock"

    time.sleep(0.01)
    ok(slug, "boss", "update", slug=wid, reopen=True, status="in_progress",
       done_so_far=["built it"], working_on_next=["more of it"])
    at_reopen = _status_at(slug, wid)
    assert at_reopen > at_done, "reopen did not move the status clock"

    # the user's dismissal of an attention flag really does change the state
    time.sleep(0.01)
    ok(slug, "boss", "update", slug=wid, attention=True,
       attention_reason="the disk is full",
       done_so_far=["built it"], working_on_next=["more of it"])
    at_flag = _status_at(slug, wid)
    assert at_flag == at_reopen, "raising an attention flag is not a transition"
    set_rev = get_item(slug, wid)["manual_attention"]["set_rev"]
    r = client.post(f"/api/orgs/{slug}/work-items/{wid}/dismiss-attention",
                    json={"set_rev": set_rev})
    assert r.status_code == 200, r.text
    assert get_item(slug, wid)["status"] == "blocked"
    assert _status_at(slug, wid) > at_flag,         "a dismissal leaves the item BLOCKED — a state change with no status typed"


check("accept, reopen and a user dismissal all move the status clock",
      accept_reopen_and_dismissal_all_count_as_transitions)


def a_legacy_item_derives_its_status_time_honestly():
    """An item written before the field existed must derive it from retained
    history — and must NOT inherit a recent timestamp from an unrelated edit,
    which would move untouched work to the top of "recently changed"."""
    slug = fresh_org()
    old = create(slug, title="legacy one")
    never = create(slug, title="legacy two")
    ok(slug, "boss", "update", slug=old, status="blocked",
       blocked_reason="stuck", done_so_far=["a"], working_on_next=["b"])
    time.sleep(0.01)
    # a much later edit that changes NO state
    ok(slug, "boss", "update", slug=old, title="legacy one, renamed",
       done_so_far=["a"], working_on_next=["b"])
    # ⚠ AND THE SECOND ITEM IS EDITED TOO, NEVER TRANSITIONED. Without this an
    # item that was never touched has `updated_at == at`, so a derivation that
    # wrongly fell back to `updated_at` would return the right answer by
    # coincidence and the check would prove nothing. (Measured: that mutant
    # SURVIVED a full run before this edit existed.)
    time.sleep(0.01)
    ok(slug, "boss", "update", slug=never, title="legacy two, renamed",
       done_so_far=["a"], working_on_next=["b"])

    org = store.load_org(slug)
    for wid in (old, never):
        it, _ = org._work_find(wid)
        it.pop("status_at", None)            # exactly what an older item has
    store.save_org(org)

    o = get_item(slug, old)
    n = get_item(slug, never)
    hist_change = [h for h in o["history"]
                   if isinstance(h.get("changes"), dict) and "status" in h["changes"]]
    assert hist_change, "positive control: the history really records the change"
    assert o["status_at"] == hist_change[-1]["at"],         "the derived time is not the newest recorded status change"
    # ⚠ THE LIE THIS PREVENTS, stated as a check
    assert o["status_at"] != o["updated_at"],         "it fell back to a clock that moves for edits — the retitle would "         "then read as a state change"
    assert o["status_at"] < o["docket_at"],         "the derived time is not older than the unrelated update after it"
    # an item that never changed status dates from its creation, not from now
    assert n["at"] != n["updated_at"],         "positive control: the second item really was edited after creation"
    assert n["status_at"] == n["at"],         "an item that never changed status must date from its CREATION — not "         "from the edit clock, which would date a transition that never happened"


check("a legacy item derives its status time from history, never from an edit clock",
      a_legacy_item_derives_its_status_time_honestly)


def a_folded_history_row_is_not_a_status_change():
    """Past the history cap the oldest rows collapse into ONE summary row that
    carries no `op`. Reading it as a transition would date a state change to
    whenever the fold happened to run."""
    slug = fresh_org()
    wid = create(slug)
    ok(slug, "boss", "update", slug=wid, status="blocked", blocked_reason="x",
       done_so_far=["a"], working_on_next=["b"])
    # ⚠ THE HISTORY ROW'S OWN TIME, not the stored stamp. The two are separate
    # `now()` reads a fraction of a millisecond apart, so comparing the derived
    # value against the stored one fails whenever they straddle a millisecond —
    # which is how this check flaked once before it was written this way.
    real = [h for h in get_item(slug, wid)["history"]
            if isinstance(h.get("changes"), dict) and "status" in h["changes"]][-1]["at"]
    org = store.load_org(slug)
    it, _ = org._work_find(wid)
    it.pop("status_at", None)
    # a fold row, newer than the real change, exactly as _work_hist writes it
    it["history"].append({"kind": "folded", "count": 3, "first_at": real,
                          "last_at": now_iso(), "at": now_iso(),
                          "note": "older history rows summarised"})
    store.save_org(org)
    assert get_item(slug, wid)["status_at"] == real,         "the summary row was read as a status change"


def now_iso():
    from orgtree.ledger import now
    return now()


check("a folded history row is never read as a status change",
      a_folded_history_row_is_not_a_status_change)


def a_dismissal_that_moved_nothing_is_not_a_transition():
    """⚠ THE COUNTEREXAMPLE (Astra, 2026-09-05). A dismissal assigns `blocked`,
    so on an item that was ALREADY blocked it changes nothing — and stamping it
    would make "most recently changed state" mean "most recently touched",
    which is the confusion this clock exists to remove. Driven through the real
    route, with the open -> blocked case beside it as the control that proves
    the check can move."""
    slug = fresh_org()

    # CONTROL: open -> blocked. A real transition, and it MUST advance.
    moving = create(slug, title="flag on open work")
    ok(slug, "boss", "update", slug=moving, status="open", attention=True,
       attention_reason="the disk is full",
       done_so_far=["a"], working_on_next=["b"])
    before_move = _status_at(slug, moving)
    time.sleep(0.01)
    rev = get_item(slug, moving)["manual_attention"]["set_rev"]
    r = client.post(f"/api/orgs/{slug}/work-items/{moving}/dismiss-attention",
                    json={"set_rev": rev})
    assert r.status_code == 200, r.text
    assert get_item(slug, moving)["status"] == "blocked"
    assert _status_at(slug, moving) > before_move, \
        "control: a dismissal that MOVED the value must advance the clock"

    # THE DEFECT: already blocked. Same route, same dismissal, no transition.
    stuck = create(slug, title="flag on blocked work")
    ok(slug, "boss", "update", slug=stuck, status="blocked",
       blocked_reason="waiting", done_so_far=["a"], working_on_next=["b"])
    ok(slug, "boss", "update", slug=stuck, attention=True,
       attention_reason="the disk is still full",
       done_so_far=["a"], working_on_next=["b"])
    before_stuck = _status_at(slug, stuck)
    time.sleep(0.01)
    rev2 = get_item(slug, stuck)["manual_attention"]["set_rev"]
    r2 = client.post(f"/api/orgs/{slug}/work-items/{stuck}/dismiss-attention",
                     json={"set_rev": rev2})
    assert r2.status_code == 200, r2.text
    after = get_item(slug, stuck)
    assert after["status"] == "blocked"
    assert after["status_at"] == before_stuck, \
        "a dismissal on an ALREADY BLOCKED item moved the status clock"
    # …and the ordinary clocks did move, so the assertion above is not passing
    # because the dismissal failed to land
    assert after["docket_at"] >= before_stuck
    assert after["dismissals"], "positive control: the dismissal was recorded"


check("a dismissal that moved nothing is not a transition; one that moved the "
      "value is", a_dismissal_that_moved_nothing_is_not_a_transition)


def a_legacy_dismissal_derives_the_same_way():
    """The derivation for items written before the field existed has to make
    the SAME distinction from history alone. The row records what it moved
    from, so this is decidable rather than guessed."""
    slug = fresh_org()
    moved = create(slug, title="legacy dismissal from open")
    stayed = create(slug, title="legacy dismissal from blocked")
    for wid, frm in ((moved, "open"), (stayed, "blocked")):
        ok(slug, "boss", "update", slug=wid, status=frm,
           **({"blocked_reason": "waiting"} if frm == "blocked" else {}),
           done_so_far=["a"], working_on_next=["b"])
        ok(slug, "boss", "update", slug=wid, attention=True,
           attention_reason="look at this",
           done_so_far=["a"], working_on_next=["b"])
        rev = get_item(slug, wid)["manual_attention"]["set_rev"]
        assert client.post(
            f"/api/orgs/{slug}/work-items/{wid}/dismiss-attention",
            json={"set_rev": rev}).status_code == 200

    org = store.load_org(slug)
    for wid in (moved, stayed):
        it, _ = org._work_find(wid)
        it.pop("status_at", None)          # exactly what an older item has
    store.save_org(org)

    m = get_item(slug, moved)
    s = get_item(slug, stayed)
    dism = [h for h in m["history"] if h.get("op") == "dismiss_attention"]
    assert dism, "positive control: the dismissal really is in the history"
    assert m["status_at"] == dism[-1]["at"], \
        "a legacy dismissal FROM OPEN is a transition and must date from it"
    # ⚠ THE SECOND ITEM HAS A REAL TRANSITION OF ITS OWN — the update that
    # blocked it — so the honest answer is THAT row, not its creation and not
    # the newer dismissal. (My first version of this check asserted creation
    # and was simply wrong about the fixture; the code was right.)
    real = [h for h in s["history"]
            if isinstance(h.get("changes"), dict) and "status" in h["changes"]]
    assert real, "positive control: the blocking update is in the history"
    s_dism = [h for h in s["history"] if h.get("op") == "dismiss_attention"]
    assert s_dism and s_dism[-1]["at"] > real[-1]["at"], \
        "positive control: the dismissal is NEWER than the real transition, " \
        "so a rule that took the newest row would be visibly wrong here"
    assert s["status_at"] == real[-1]["at"], \
        "a legacy dismissal from ALREADY BLOCKED moved nothing, so it must " \
        "not displace the last transition that did"
    # the two items are otherwise identical, so the difference is the rule
    assert m["status_at"] != s["status_at"]


check("a legacy dismissal counts only when it moved the value",
      a_legacy_dismissal_derives_the_same_way)



# ---------------------------------------------------------------- summary
print(f"\n{PASSED} passed, {len(FAILED)} failed")
for f in FAILED:
    print("\nFAIL", f)
sys.exit(1 if FAILED else 0)
