"""Ticket participants and the addressed reply (user approval 2026-09-06 13:29).

The user may reply to a chosen collaborator on a docket item instead of always
addressing its assignee. Three backend facts are pinned here, each through
the INSTALLED routes (`/api/orgs/{slug}/work-items*`, `/api/agent`) with
`supervisor.send_message` stubbed to RECORD:

    §1  the item serves `reply_recipients` — owner first, then participants,
        each with a server-derived state (live | retired | missing)
    §2  POST .../reply {body, to?}: `to` is validated against the STORED
        owner + participants at send time; a refusal sends nothing and
        substitutes nobody; ownership never moves; a participant is told it
        is addressed as a participant and who owns the item
    §3  an agent-driven `participants add` tells each ACTUALLY new member with
        a passive notice (never a wake); re-adds, self-adds, removals and the
        owner send nothing; an unreachable member is reported, not refused

Every "nothing was sent" assertion is measured against a count taken before
the call, and the positive case next to it proves the same counter moves.

    python backend/tests/test_work_reply_participants.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
_TMP = tempfile.mkdtemp(prefix="orgtree-workreply-")
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
from orgtree import api, store, supervisor                   # noqa: E402
from orgtree.ledger import USER                              # noqa: E402

assert store.DATA_ROOT.startswith(_TMP), store.DATA_ROOT   # bound to the throwaway root

DRIVEN: list[tuple[str, str, str, bool]] = []   # (slug, node, nudge, wake)


def _fake_send(slug, nid, text, command=False, wake=True, **kw):
    DRIVEN.append((slug, nid, text, wake))
    return {"accepted": True, "queued": 0, **({"parked": True} if not wake else {})}


supervisor.send_message = _fake_send
api.supervisor.send_message = _fake_send
api.provider_hire_gate = lambda *a, **k: None

client = TestClient(api.app)
PASSED = 0
FAILED: list[str] = []


def check(label, fn) -> None:
    global PASSED
    try:
        fn()
        PASSED += 1
        print(f"  ok  {label}")
    except Exception:                                            # noqa: BLE001
        FAILED.append(label)
        print(f"  FAIL {label}")
        traceback.print_exc()


_n = [0]


def fresh_org():
    """boss (top) > mid > worker; peer (top-level); stranger (top-level)."""
    _n[0] += 1
    org = store.create_org(f"reply-{_n[0]}", [])
    org.hire(USER, None, "opus", 60, "boss")
    org.hire(USER, "boss", "haiku", 20, "mid")
    org.hire(USER, "mid", "haiku", 0, "worker")
    org.hire(USER, None, "haiku", 5, "peer")
    org.hire(USER, None, "haiku", 5, "stranger")
    store.save_org(org)
    return org.d["slug"]


def agent(org, node, tool, **args):
    r = client.post("/api/agent", json={"org": org, "node": node,
                                        "tool": tool, "args": args})
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text)


def ok(org, node, action, **args):
    st, js = agent(org, node, "orgtree_work", action=action, **args)
    assert st == 200, (action, st, js)
    return js


def create(org, node="boss", **kw):
    kw.setdefault("title", "ship the thing")
    kw.setdefault("objective", "the thing is not shipped; ship it")
    kw.setdefault("done_so_far", ["read the spec"])
    kw.setdefault("working_on_next", ["write it"])
    return ok(org, node, "create", **kw)["created"]


def get_item(slug, wid):
    r = client.get(f"/api/orgs/{slug}/work-items/{wid}")
    assert r.status_code == 200, (r.status_code, r.text)
    return r.json()["item"]


def reply(slug, wid, body="please", to=None):
    js = {"body": body}
    if to is not None:
        js["to"] = to
    return client.post(f"/api/orgs/{slug}/work-items/{wid}/reply", json=js)


def mailbox(slug, nid):
    return list(store.load_org(slug).d.get("mail", {}).get(nid) or [])


def recipients(slug, wid):
    return [(x["node"], x["role"], x["state"]) for x in get_item(slug, wid)["reply_recipients"]]


# ================================================= §1 reply_recipients view
print("§1 reply_recipients")


def recipients_owner_first_then_participants_with_states():
    slug = fresh_org()
    wid = create(slug, node="boss", participants=["peer", "stranger"])
    assert recipients(slug, wid) == [("boss", "owner", "live"),
                                     ("peer", "participant", "live"),
                                     ("stranger", "participant", "live")]
    # the list view carries the same field
    r = client.get(f"/api/orgs/{slug}/work-items")
    row = [x for x in r.json()["items"] if x["slug"] == wid][0]
    assert row["reply_recipients"] == get_item(slug, wid)["reply_recipients"]
    # retired → 'retired', selectable; deleted from the org → 'missing'
    org = store.load_org(slug)
    org.retire(USER, "peer")
    del org.d["nodes"]["stranger"]
    store.save_org(org)
    assert recipients(slug, wid) == [("boss", "owner", "live"),
                                     ("peer", "participant", "retired"),
                                     ("stranger", "participant", "missing")]
    # the owner's OWN state uses the same word: a retired owner is 'retired'
    org = store.load_org(slug)
    org.retire(USER, "boss")
    store.save_org(org)
    assert recipients(slug, wid)[0] == ("boss", "owner", "retired")
    # ⚠ DEDUPE ON READ: a document that carries the owner inside its own
    # participants (older writers, a hand edit) serves the owner ONCE
    org = store.load_org(slug)
    it, _ = org._work_find(wid)
    it["participants"] = ["boss", "peer", "peer"]
    store.save_org(org)
    assert recipients(slug, wid) == [("boss", "owner", "retired"),
                                     ("peer", "participant", "retired")]
    # no owner: participants only; nothing at all: empty
    org = store.load_org(slug)
    it, _ = org._work_find(wid)
    it["owner"] = None
    it["participants"] = ["peer"]
    store.save_org(org)
    assert recipients(slug, wid) == [("peer", "participant", "retired")]
    org = store.load_org(slug)
    it, _ = org._work_find(wid)
    it["participants"] = []
    store.save_org(org)
    assert recipients(slug, wid) == []


check("reply_recipients: owner first, participants in stored order, deduped, "
      "state derived from the node table on every read",
      recipients_owner_first_then_participants_with_states)


# ======================================================= §2 addressed reply
print("§2 addressed reply")


def addressed_reply_reaches_the_chosen_member_and_only_a_member():
    slug = fresh_org()
    wid = create(slug, node="boss", title="Addressed", participants=["peer"])
    # omitted `to` → the assignment, exactly as before
    r = reply(slug, wid, "for the owner")
    assert r.status_code == 200 and r.json()["to"] == "boss" and r.json()["role"] == "owner", r.text
    assert "ASSIGNED TO YOU" in DRIVEN[-1][2] and DRIVEN[-1][1] == "boss" and DRIVEN[-1][3] is True
    # explicit `to` = owner → identical routing and wording
    r = reply(slug, wid, "for the owner again", to="boss")
    assert r.status_code == 200 and r.json()["role"] == "owner"
    assert "ASSIGNED TO YOU" in DRIVEN[-1][2]
    assert "ADDRESSED TO YOU AS A PARTICIPANT" not in mailbox(slug, "boss")[-1]["body"]
    # `to` = participant → reaches the participant, says so, names the owner
    n_boss = len(mailbox(slug, "boss"))
    r = reply(slug, wid, "peer, please look", to="peer")
    assert r.status_code == 200, r.text
    js = r.json()
    assert js["to"] == "peer" and js["role"] == "participant" and js["deferred"] is False, js
    m = mailbox(slug, "peer")[-1]
    assert m["from"] == USER and m["body"].startswith(f'[DOCKET REPLY · {wid} "Addressed"]'), m["body"]
    assert "ADDRESSED TO YOU AS A PARTICIPANT" in m["body"] and "owned by boss" in m["body"]
    assert "peer, please look" in m["body"]
    assert DRIVEN[-1][1] == "peer" and DRIVEN[-1][3] is True
    assert "PARTICIPATE in (owner: boss)" in DRIVEN[-1][2], DRIVEN[-1][2]
    assert len(mailbox(slug, "boss")) == n_boss, "the owner was mailed too"
    # ownership did not move
    it = get_item(slug, wid)
    assert it["owner"]["node"] == "boss" and it["participants"] == ["peer"]
    # the participant can answer the user (deep reach was granted)
    org = store.load_org(slug)
    assert org._has_audience("peer", USER) or org.node("peer")["parent"] is None
    # ⚠ NOT A MEMBER → refused, nothing sent, nothing saved, nobody substituted
    before = {n: len(mailbox(slug, n)) for n in ("boss", "peer", "stranger", "mid")}
    rev = get_item(slug, wid)["rev"]
    n_driven = len(DRIVEN)
    r = reply(slug, wid, "stranger?", to="stranger")
    assert r.status_code == 422 and "neither the assigned agent nor a participant" in r.text, r.text
    r = reply(slug, wid, "mid?", to="mid")
    assert r.status_code == 422, r.text
    r = reply(slug, wid, "nobody?", to="no-such-node")
    assert r.status_code == 422, r.text
    assert {n: len(mailbox(slug, n)) for n in before} == before, "a refusal mailed somebody"
    assert get_item(slug, wid)["rev"] == rev and len(DRIVEN) == n_driven
    # ⚠ THE STALE-PANEL RACE: a participant removed after the panel rendered
    # is refused at send time — the owner is NOT chosen in its place
    ok(slug, "boss", "participants", slug=wid, remove=["peer"])
    n_boss = len(mailbox(slug, "boss"))
    r = reply(slug, wid, "peer still?", to="peer")
    assert r.status_code == 422 and "neither" in r.text, r.text
    assert len(mailbox(slug, "boss")) == n_boss, "the owner was substituted for a removed participant"
    # a participant that no longer EXISTS in the org: refused, no substitute
    ok(slug, "boss", "participants", slug=wid, add=["stranger"])
    org = store.load_org(slug)
    del org.d["nodes"]["stranger"]
    store.save_org(org)
    assert recipients(slug, wid)[-1] == ("stranger", "participant", "missing")
    r = reply(slug, wid, "gone?", to="stranger")
    assert r.status_code == 422 and "no longer exists" in r.text, r.text
    assert len(mailbox(slug, "boss")) == n_boss
    # a RETIRED participant: selectable, the mail is deferred, nobody is driven
    ok(slug, "boss", "participants", slug=wid, add=["peer"])
    org = store.load_org(slug)
    org.retire(USER, "peer")
    store.save_org(org)
    n_driven = len(DRIVEN)
    r = reply(slug, wid, "retired peer", to="peer")
    assert r.status_code == 200, r.text
    assert r.json() == {**r.json(), "to": "peer", "role": "participant",
                        "deferred": True, "node_state": "archived"}
    assert mailbox(slug, "peer")[-1]["body"].endswith("retired peer")
    assert len(DRIVEN) == n_driven, "an archived recipient was driven"
    # blank `to` behaves as omitted; empty body still refused
    assert reply(slug, wid, "x", to="   ").json()["role"] == "owner"
    assert reply(slug, wid, "  ", to="peer").status_code == 422


check("an addressed reply reaches the chosen owner/participant, says which, "
      "moves no ownership, and refuses every non-member with no side effects",
      addressed_reply_reaches_the_chosen_member_and_only_a_member)


def ownerless_item_can_still_reach_a_participant():
    slug = fresh_org()
    wid = create(slug, node="boss", participants=["peer"])
    org = store.load_org(slug)
    it, _ = org._work_find(wid)
    it["owner"] = None
    store.save_org(org)
    assert recipients(slug, wid) == [("peer", "participant", "live")]
    # no `to` → the old refusal, unchanged
    r = reply(slug, wid, "anyone?")
    assert r.status_code == 422 and "no assignment" in r.text
    # `to` = the participant → delivered, and the mail says nobody owns it
    r = reply(slug, wid, "peer then", to="peer")
    assert r.status_code == 200 and r.json()["role"] == "participant", r.text
    assert "owned by nobody (unassigned)" in mailbox(slug, "peer")[-1]["body"]
    # `to` = the former owner (no longer a member) → refused
    assert reply(slug, wid, "boss?", to="boss").status_code == 422


check("an unassigned item with participants: the old refusal without `to`, "
      "a delivery with it, and the ex-owner is not a back door",
      ownerless_item_can_still_reach_a_participant)


# ================================================= §3 participation notice
print("§3 participation notice")


def new_participants_get_a_passive_notice_only_when_actually_new():
    slug = fresh_org()
    wid = create(slug, node="boss", title="Told")
    n_peer, n_driven = len(mailbox(slug, "peer")), len(DRIVEN)
    js = ok(slug, "boss", "participants", slug=wid, add=["peer"])
    assert js["noticed"] == ["peer"] and js["participants"] == ["peer"], js
    m = mailbox(slug, "peer")[-1]
    assert len(mailbox(slug, "peer")) == n_peer + 1
    assert m["kind"] == "notice" and m["from"] == "boss", m
    assert m["body"].startswith(f'[DOCKET PARTICIPATION · {wid} "Told"]'), m["body"]
    assert "not its assignment" in m["body"] and "owned by boss" in m["body"]
    assert "Added by boss" in m["body"]
    # nudged like a send_notice: wake=False, never a drive
    assert len(DRIVEN) == n_driven + 1 and DRIVEN[-1][1] == "peer" and DRIVEN[-1][3] is False, DRIVEN[-1]
    assert "participant" in DRIVEN[-1][2]
    assert "peer" in (js.get("notice_delivery") or {}), js
    # ⚠ NOT AGAIN: re-adding a member, adding the owner, adding nobody
    n_peer, n_boss, n_driven = len(mailbox(slug, "peer")), len(mailbox(slug, "boss")), len(DRIVEN)
    js = ok(slug, "boss", "participants", slug=wid, add=["peer", "boss", ""])
    assert js["noticed"] == [] and js["participants"] == ["peer"], js
    assert len(mailbox(slug, "peer")) == n_peer and len(mailbox(slug, "boss")) == n_boss
    assert len(DRIVEN) == n_driven
    # a removal is silent
    js = ok(slug, "boss", "participants", slug=wid, remove=["peer"])
    assert js["noticed"] == [] and js["participants"] == []
    assert len(mailbox(slug, "peer")) == n_peer and len(DRIVEN) == n_driven
    # the actor adding ITSELF is not told (it would be mailing itself)
    org = store.load_org(slug)
    it, _ = org._work_find(wid)
    it["owner"] = None
    store.save_org(org)
    n_boss = len(mailbox(slug, "boss"))
    js = ok(slug, "boss", "participants", slug=wid, add=["boss"])
    assert js["noticed"] == [] and js["participants"] == ["boss"]
    assert len(mailbox(slug, "boss")) == n_boss
    # the user as actor: the notice says so
    org = store.load_org(slug)
    r = org.work_participants(USER, wid, add=["peer"])
    store.save_org(org)
    assert r["noticed"] == ["peer"]
    assert "Added by the user" in mailbox(slug, "peer")[-1]["body"]
    assert "owned by nobody (unassigned)" in mailbox(slug, "peer")[-1]["body"]


check("a participant is told once, passively, only when actually added",
      new_participants_get_a_passive_notice_only_when_actually_new)


def notice_is_best_effort_and_membership_stands():
    slug = fresh_org()
    # an archived member: the notice is stored, deferred, and nothing nudges it
    org = store.load_org(slug)
    org.retire(USER, "peer")
    store.save_org(org)
    wid = create(slug, node="boss", title="Best effort")
    n_driven = len(DRIVEN)
    js = ok(slug, "boss", "participants", slug=wid, add=["peer"])
    assert js["noticed"] == ["peer"] and js.get("noticed_deferred") == ["peer"], js
    assert mailbox(slug, "peer")[-1]["kind"] == "notice"
    assert len(DRIVEN) == n_driven, "an archived member was nudged"
    assert "notice_delivery" not in js
    # a member the actor may not ADDRESS (peer, top-level, adding worker two
    # levels down another tree): membership stands, the notice is reported
    # as refused, and the call succeeds — as it always has
    wid2 = create(slug, node="stranger", title="Unreachable")
    n_worker = len(mailbox(slug, "worker"))
    js = ok(slug, "stranger", "participants", slug=wid2, add=["worker"])
    assert js["participants"] == ["worker"] and js["noticed"] == [], js
    assert js["notice_refused"] and js["notice_refused"][0]["node"] == "worker", js
    assert "may not address" in js["notice_refused"][0]["reason"]
    assert len(mailbox(slug, "worker")) == n_worker
    assert get_item(slug, wid2)["participants"] == ["worker"]
    # POSITIVE CONTROL for the refusal path: the same add from an actor that
    # CAN address worker is told normally
    wid3 = create(slug, node="boss", title="Reachable")
    js = ok(slug, "boss", "participants", slug=wid3, add=["worker"])
    assert js["noticed"] == ["worker"] and "notice_refused" not in js, js
    assert len(mailbox(slug, "worker")) == n_worker + 1


check("the notice is best-effort — deferred for an archived member, reported "
      "when unaddressable — and membership never depends on it",
      notice_is_best_effort_and_membership_stands)


print(f"\n{PASSED} passed, {len(FAILED)} failed")
if FAILED:
    for f in FAILED:
        print("  FAILED:", f)
    sys.exit(1)
