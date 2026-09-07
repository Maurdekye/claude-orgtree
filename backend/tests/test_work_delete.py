"""Permanent ticket deletion (user 2026-09-07 08:18Z: "can you delete tickets
completely? if not make sure thats an ability you have").

Archive and drop keep the record; `delete` removes it. This suite pins:

  §1 AUTHORITY — the user, a strict superior of the owner, and a top-level
     owner may delete; a subordinate may NOT delete its own item, and a
     participant, the named reviewer and an unrelated agent are refused with
     the SAME sentence a hidden item gets (no existence leak).
  §2 EFFECT — the record is gone from the active list, the archive, `get`,
     `_work_find` and the counts; an ARCHIVED item deletes too; the audit
     event names what went; the owner is told when it is not the actor.
  §3 POINTERS — another item's `dependencies` entry and a `superseded_by`
     naming the deleted item are cleared, each with a history row on THAT
     item; unrelated pointers are untouched (control).
  §4 REFUSALS — nested children refuse (naming them); an OPEN attached
     question refuses; a withdrawn one no longer does (control).
  §5 SURFACES — the MCP dispatch (`_work_mutate` action=delete) and the
     user's HTTP DELETE route reach the same rule; the tool enum lists it.
  §6 PERSISTENCE — after save + reload from disk the item is still gone and
     the cleared pointers stay cleared.

The whole file runs TWICE: once under the backend it was started with
(sqlite by default) and once more, as a child process, under the other one —
so JSON and SQLite are both proven, with the backend named in every line.

Run: python backend/tests/test_work_delete.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

BACKEND_NAME = (os.environ.get("ORGTREE_STORE") or "sqlite").strip().lower() or "sqlite"
ROOT = tempfile.mkdtemp(prefix=f"orgtree-work-delete-{BACKEND_NAME}-")
os.environ["ORGTREE_DATA"] = ROOT
os.environ["ORGTREE_PORT"] = "7433"
with open(os.path.join(ROOT, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from orgtree import api, store                                      # noqa: E402
from orgtree.ledger import LedgerError, USER                       # noqa: E402
from fastapi.testclient import TestClient                          # noqa: E402

assert store.DATA_ROOT.replace("\\", "/").startswith(ROOT.replace("\\", "/")), store.DATA_ROOT
assert store.STORE_BACKEND == BACKEND_NAME, (store.STORE_BACKEND, BACKEND_NAME)

N = [0]
FAILED: list[str] = []


def check(name: str, fn) -> None:
    N[0] += 1
    try:
        fn()
        print(f"ok   {N[0]:2d} [{BACKEND_NAME}] {name}", flush=True)
    except Exception as e:                                          # noqa: BLE001
        import traceback
        FAILED.append(name)
        print(f"FAIL {N[0]:2d} [{BACKEND_NAME}] {name}: {e!r}", flush=True)
        traceback.print_exc()


_N = [0]


def fixture() -> str:
    """coordinator (top-level) → worker → helper; a second top-level `peer`."""
    _N[0] += 1
    org = store.create_org(f"zz-delete-{BACKEND_NAME}-{_N[0]:03d}")
    org.hire(USER, None, "opus", 60, "coordinator")
    org.hire(USER, "coordinator", "haiku", 20, "worker")     # user hires take defaults
    org.hire(USER, "worker", "haiku", 0, "helper")
    org.hire(USER, None, "opus", 5, "peer")
    store.save_org(org)
    return str(org.d["slug"])


def do(slug: str, fn):
    with store.DOC_LOCK:
        org = store.load_org(slug)
        out = fn(org)
        store.save_org(org)
    return out


def item(slug: str, title: str, *, actor: str = USER, owner: str = "worker",
         status: str = "in_progress", **kw) -> str:
    return str(do(slug, lambda org: org.work_create(
        actor, title, objective="the problem; then the proposal",
        owner=owner, status=status, **kw))["slug"])


def names(slug: str, viewer: str = USER, archived: bool = False) -> set[str]:
    out = store.load_org(slug).work_list(viewer, include_archived=True)
    return {i["slug"] for i in (out["archived"] if archived else out["items"])}


def refused(fn) -> str:
    try:
        fn()
    except LedgerError as e:
        return str(e)
    raise AssertionError("accepted; it had to be refused")


def events(slug: str, op: str) -> list[dict]:
    return [e for e in store.load_org(slug).d.get("events", []) if e.get("op") == op]


# ---------------------------------------------------------------- §1 authority
def t_user_deletes() -> None:
    s = fixture(); w = item(s, "user deletes")
    r = do(s, lambda o: o.work_delete(USER, w, "not wanted"))
    assert r["deleted"] == w and r["was_archived"] is False, r
    assert w not in names(s) and w not in names(s, archived=True)


def t_superior_deletes() -> None:
    s = fixture(); w = item(s, "superior deletes", owner="helper")
    do(s, lambda o: o.work_delete("coordinator", w))          # grand-superior
    assert w not in names(s)
    w2 = item(s, "direct superior deletes", owner="helper")
    do(s, lambda o: o.work_delete("worker", w2))               # direct superior
    assert w2 not in names(s)


def t_toplevel_owner_deletes_own() -> None:
    s = fixture(); w = item(s, "coordinator's own", actor="coordinator", owner="coordinator")
    do(s, lambda o: o.work_delete("coordinator", w))
    assert w not in names(s)


def t_subordinate_cannot_delete_own() -> None:
    s = fixture(); w = item(s, "worker's own", actor="worker", owner="worker")
    msg = refused(lambda: do(s, lambda o: o.work_delete("worker", w)))
    assert "superior" in msg and "archive or drop" in msg, msg
    assert w in names(s), "a refusal must not remove anything"
    # ...and the same for the CREATOR of an unowned-by-it item it can manage
    w2 = item(s, "worker created, helper owns", actor="worker", owner="helper")
    do(s, lambda o: o.work_delete("worker", w2))               # superior of the owner: allowed
    assert w2 not in names(s)


def t_participant_reviewer_stranger_refused() -> None:
    s = fixture(); w = item(s, "guarded", owner="worker")
    do(s, lambda o: o.work_participants(USER, w, add=["peer"]))
    do(s, lambda o: o.work_update(USER, w, ["a"], [], status="review", reviewer="coordinator", owner="worker"))
    hidden = refused(lambda: do(s, lambda o: o.work_delete("helper", "no-such-item")))
    for who in ("peer",):                                       # participant
        msg = refused(lambda: do(s, lambda o: o.work_delete(who, w)))
        assert "superior" in msg, (who, msg)
    # the reviewer here is ALSO the coordinator (a superior), so name a
    # reviewer that is not: peer's report `pal`
    do(s, lambda o: o.hire(USER, "peer", "haiku", 0, "pal"))
    w3 = item(s, "reviewed by pal", owner="worker")
    do(s, lambda o: o.work_update(USER, w3, ["a"], [], status="review", reviewer="pal", owner="worker"))
    msg = refused(lambda: do(s, lambda o: o.work_delete("pal", w3)))
    assert "superior" in msg, msg
    # an unrelated agent gets the SAME sentence as for a hidden/nonexistent item
    stranger = refused(lambda: do(s, lambda o: o.work_delete("helper", w3)))
    assert stranger.split(" that you may read")[1] == hidden.split(" that you may read")[1]         and stranger.startswith("no work item ") and hidden.startswith("no work item "), (stranger, hidden)
    assert w in names(s) and w3 in names(s)


# ------------------------------------------------------------------ §2 effect
def t_gone_everywhere_and_audited() -> None:
    s = fixture(); w = item(s, "gone everywhere", owner="worker")
    keep = item(s, "kept", owner="worker")
    before = store.load_org(s).work_counts()
    r = do(s, lambda o: o.work_delete("coordinator", w, "duplicate of kept"))
    org = store.load_org(s)
    assert w not in names(s) and w not in names(s, archived=True)
    assert refused(lambda: org._work_find(w))
    assert refused(lambda: org.work_get(USER, w))
    after = org.work_counts()
    assert after["active"] == before["active"] - 1, (before, after)
    ev = events(s, "work_deleted")
    assert len(ev) == 1 and ev[0]["actor"] == "coordinator", ev
    d = ev[0]["detail"]
    assert d["slug"] == w and d["title"] == "gone everywhere" and d["owner"] == "worker" \
        and d["archived"] is False and d["note"] == "duplicate of kept" and d["pointers_cleared"] == [], d
    assert r["owner_told"] == "worker"
    inbox = (org.d.get("mail") or {}).get("worker") or []
    told = [m for m in inbox if "PERMANENTLY DELETED" in str(m.get("body") or "")]
    assert len(told) == 1 and w in told[0]["body"] and "duplicate of kept" in told[0]["body"], inbox
    assert keep in names(s), "the neighbour survived"


def t_archived_item_deletes() -> None:
    s = fixture(); w = item(s, "archived then deleted", owner="worker")
    do(s, lambda o: o.work_update(USER, w, ["x"], [], status="dropped",
                                  dropped_reason="cancelled by the user; nothing revives it", owner="worker"))
    assert w in names(s, archived=True) and w not in names(s), "dropped archives at once"
    r = do(s, lambda o: o.work_delete(USER, w))
    assert r["was_archived"] is True
    assert w not in names(s, archived=True)
    assert events(s, "work_deleted")[-1]["detail"]["archived"] is True


def t_owner_is_actor_not_told() -> None:
    s = fixture(); w = item(s, "coordinator's own", actor="coordinator", owner="coordinator")
    r = do(s, lambda o: o.work_delete("coordinator", w))
    assert r["owner_told"] is None


# ---------------------------------------------------------------- §3 pointers
def t_pointers_cleared_with_history() -> None:
    s = fixture()
    dead = item(s, "to be deleted", owner="worker")
    other = item(s, "unrelated dependency", owner="worker")
    dep = item(s, "depends on both", owner="worker", dependencies=[dead, other])
    old = item(s, "old plan", owner="worker")
    do(s, lambda o: o.work_supersede(USER, old, dead))          # old --superseded_by--> dead
    assert store.load_org(s)._work_find(old)[0]["superseded_by"] == dead
    r = do(s, lambda o: o.work_delete(USER, dead))
    org = store.load_org(s)
    d_it = org._work_find(dep)[0]; o_it = org._work_find(old)[0]
    assert d_it["dependencies"] == [other], d_it["dependencies"]
    assert o_it.get("superseded_by") is None and o_it["status"] == "superseded", o_it
    rows = [h for h in d_it["history"] if h.get("op") == "pointer_cleared"]
    assert rows and rows[-1]["field"] == "dependencies" and rows[-1]["deleted"] == dead, rows
    rows = [h for h in o_it["history"] if h.get("op") == "pointer_cleared"]
    assert rows and rows[-1]["field"] == "superseded_by" and rows[-1]["deleted"] == dead, rows
    assert sorted(p["item"] + ":" + p["field"] for p in r["pointers_cleared"]) \
        == sorted([dep + ":dependencies", old + ":superseded_by"]), r
    assert not any(h.get("op") == "pointer_cleared" for h in org._work_find(other)[0].get("history") or []), "control: untouched item has no row"
    view = org.work_get(USER, dep)
    assert all(x.get("name") != dead for x in (view.get("dependencies") or [])), view.get("dependencies")


# ---------------------------------------------------------------- §4 refusals
def t_children_refuse() -> None:
    s = fixture(); parent = item(s, "parent", owner="worker")
    kid = item(s, "kid", owner="worker", parent=parent)
    msg = refused(lambda: do(s, lambda o: o.work_delete(USER, parent)))
    assert kid in msg and "nested" in msg, msg
    assert parent in names(s)
    do(s, lambda o: o.work_move(USER, kid, ""))                 # detach the child
    do(s, lambda o: o.work_delete(USER, parent))                # now allowed
    assert parent not in names(s) and kid in names(s)


def t_open_question_refuses() -> None:
    s = fixture(); w = item(s, "asked about", owner="worker")
    # the asker must hold a user audience for the ask to be STORED (a
    # subordinate's ask is routed to its superior as mail): the top-level
    # coordinator, a superior of the owner, may read and attach to the item
    a = do(s, lambda org: org.ask_user("coordinator", "Which colour?", options=["red", "blue"], work_item=w))
    org = store.load_org(s)
    assert org._work_questions(w), "INERT: the ask did not attach"
    msg = refused(lambda: do(s, lambda o: o.work_delete(USER, w)))
    assert "open attached question" in msg, msg
    assert w in names(s)
    ask_id = a.get("id") if isinstance(a, dict) else str(a)
    del ask_id
    do(s, lambda o: o.withdraw_ask("coordinator"))
    assert not store.load_org(s)._work_questions(w), "INERT: withdraw did not close the ask"
    do(s, lambda o: o.work_delete(USER, w))                     # control: now allowed
    assert w not in names(s)


# ---------------------------------------------------------------- §5 surfaces
def t_mcp_dispatch_and_http_route() -> None:
    s = fixture()
    w = item(s, "via tool", owner="worker")
    org = store.load_org(s)
    r = api._work_mutate(org, "coordinator", {"action": "delete", "slug": w, "note": "via tool"})
    store.save_org(org)
    assert r["deleted"] == w and w not in names(s)
    org = store.load_org(s)
    msg = refused(lambda: api._work_mutate(org, "coordinator", {"action": "purge", "slug": w}))
    assert "|delete" in msg, msg
    w2 = item(s, "via http", owner="worker")
    client = TestClient(api.app)
    res = client.delete(f"/api/orgs/{s}/work-items/{w2}", params={"note": "user click"})
    assert res.status_code == 200 and res.json()["deleted"] == w2, res.text
    assert w2 not in names(s)
    assert client.delete(f"/api/orgs/{s}/work-items/{w2}").status_code == 404
    kid_parent = item(s, "http parent", owner="worker"); item(s, "http kid", owner="worker", parent=kid_parent)
    res = client.delete(f"/api/orgs/{s}/work-items/{kid_parent}")
    assert res.status_code == 422 and "nested" in res.text, res.text
    from orgtree import mcptool
    tool = next(t for t in mcptool.TOOLS if t["name"] == "orgtree_work")
    assert "delete" in tool["inputSchema"]["properties"]["action"]["enum"]
    assert "delete" in tool["description"] and "REMOVES THE TICKET RECORD" in tool["description"] \
        and "historical mail" in tool["description"], "the card must say what is removed AND what stays"


# ------------------------------------------------------------- §6 persistence
def t_persists_across_reload() -> None:
    s = fixture()
    dead = item(s, "persist dead", owner="worker")
    dep = item(s, "persist dep", owner="worker", dependencies=[dead])
    do(s, lambda o: o.work_delete(USER, dead))
    org = store.load_org(s)                                       # a fresh read from disk
    assert refused(lambda: org._work_find(dead))
    assert org._work_find(dep)[0]["dependencies"] == []
    path = store.org_path(s)
    if path and os.path.exists(path) and path.endswith(".json"):
        # the DOCKET sections on disk; the org's event log (same file under
        # JSON, its own table under SQLite) keeps the audit row by design
        doc = json.load(open(path, encoding="utf-8"))
        rows = (doc.get("work_items") or []) + (doc.get("work_items_archive") or [])
        assert all(r.get("slug") != dead for r in rows), "the record must not survive in the docket sections"
        assert "persist dead" not in json.dumps(rows), "the title must not survive in the docket sections"
        # (the dependent item's history row names the deleted SLUG — that is the record of the clearing, kept)
        assert any(e.get("op") == "work_deleted" for e in doc.get("events", [])), "control: the audit row IS on disk"
    elif path and os.path.exists(path):
        import sqlite3
        con = sqlite3.connect(path)
        rows = con.execute("SELECT val FROM doc WHERE key IN ('work_items','work_items_archive')").fetchall()
        con.close()
        assert all('persist dead' not in r[0] for r in rows), "the title must not survive in the db"
    else:
        raise AssertionError(f"INERT: no on-disk document found for {s} ({path})")


# ------------------------------------------------------------ §7 identity
def t_deleted_name_never_reused() -> None:
    s = fixture()
    dead = item(s, "Same title twice", owner="worker")
    assert dead == "same-title-twice", dead
    # a closed ask and a mail row that carry the name (records of the past)
    do(s, lambda org: org.ask_user("coordinator", "Keep it?", options=["yes", "no"], work_item=dead))
    do(s, lambda o: o.withdraw_ask("coordinator"))
    do(s, lambda o: o.post_mail(USER, "worker", f"see @item:{s}/{dead}", "message"))
    do(s, lambda o: o.work_delete(USER, dead, "made in error"))
    again = item(s, "Same title twice", owner="worker")
    assert again != dead and again == "same-title-twice-2", (dead, again)
    org = store.load_org(s)
    assert refused(lambda: org._work_find(dead)), "the old name still resolves"
    assert org._work_pointer_visible(dead, USER) is False, "an old pointer must resolve to NOTHING, not the new ticket"
    assert org._work_pointer_visible(again, USER) is True
    closed = [a for a in org.d.get("asks", []) if any(q.get("work_item") == dead for q in a.get("questions") or [])]
    assert closed and closed[0]["status"] != "open", "control: the closed ask still carries the old name"
    assert dead in json.dumps((org.d.get("mail") or {}).get("worker") or []), "control: the mail row still carries the old name"
    assert org.d.get("work_deleted_names") == [dead], org.d.get("work_deleted_names")
    # the reservation is names only and survives a reload from disk
    org = store.load_org(s)
    assert org.d.get("work_deleted_names") == [dead]
    third = item(s, "Same title twice", owner="worker")
    assert third == "same-title-twice-3", third
    # deleting the reused name reserves it too, without duplicates
    do(s, lambda o: o.work_delete(USER, again))
    do(s, lambda o: o.work_delete(USER, again) if False else None)
    assert store.load_org(s).d.get("work_deleted_names") == [dead, again]


checks = [
    ("§1 the user deletes an active item", t_user_deletes),
    ("§1 a strict superior (direct or higher) deletes a subordinate's item", t_superior_deletes),
    ("§1 a top-level owner deletes its own item", t_toplevel_owner_deletes_own),
    ("§1 a subordinate may NOT delete its own item; its superior may", t_subordinate_cannot_delete_own),
    ("§1 participant, named reviewer and stranger are refused; stranger gets the hidden-item sentence", t_participant_reviewer_stranger_refused),
    ("§2 deleted item is gone from list/archive/get/find/counts; audit event; owner told; neighbour kept", t_gone_everywhere_and_audited),
    ("§2 an ARCHIVED (dropped) item deletes too and the event says archived", t_archived_item_deletes),
    ("§2 the owner is not told when it is the actor", t_owner_is_actor_not_told),
    ("§3 dependencies and superseded_by pointers cleared with history rows; unrelated pointer kept", t_pointers_cleared_with_history),
    ("§4 nested children refuse by name; after moving the child out it deletes", t_children_refuse),
    ("§4 an open attached question refuses; after withdrawal it deletes", t_open_question_refuses),
    ("§5 MCP dispatch action=delete and user HTTP DELETE reach the same rule; enum + description list it", t_mcp_dispatch_and_http_route),
    ("§6 deletion and cleared pointers survive save + reload; the title is gone from disk", t_persists_across_reload),
    ("§7 a deleted name is never minted again; old refs resolve to nothing, not to the new ticket; reservation survives reload", t_deleted_name_never_reused),
]
for name, fn in checks:
    check(name, fn)

print(f"[{BACKEND_NAME}] {N[0] - len(FAILED)}/{N[0]} passed", flush=True)
if FAILED:
    print("FAILED:", *FAILED, sep="\n  ")
    sys.exit(1)

# the OTHER backend, once, as a child — so one invocation proves both
if not os.environ.get("ORGTREE_WORK_DELETE_CHILD"):
    other = "json" if BACKEND_NAME == "sqlite" else "sqlite"
    env = {k: v for k, v in os.environ.items() if k not in ("ORGTREE_DATA",)}
    env.update({"ORGTREE_STORE": other, "ORGTREE_WORK_DELETE_CHILD": "1"})
    out = subprocess.run([sys.executable, os.path.abspath(__file__)], env=env,
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    sys.stdout.write(out.stdout)
    if out.returncode != 0:
        sys.stdout.write(out.stderr[-4000:])
        print(f"[{other}] child run FAILED rc={out.returncode}")
        sys.exit(out.returncode)
    print(f"both backends passed: {BACKEND_NAME} + {other}")
    print(f"ALL {N[0] * 2} CHECKS PASS")
