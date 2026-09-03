"""The gallery (user request 2026-09-03) — one flat, org-wide, newest-first
list of every presented-document card, `GET /api/orgs/{slug}/documents`.

Reads `documents` directly rather than the tree (the tree hides a rehired
predecessor's cards via `org_children`'s successor-hiding), and rides
`present_evicted` log lines so a card the user already saw does not just
vanish once the retention prune drops it — it shows with `evicted: true`
and no body, distinct from a card the user dismissed on purpose.

Run directly:
    python backend/tests/test_documents_gallery.py
"""
from __future__ import annotations

import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="orgtree-docgallery-")
os.environ["ORGTREE_DATA"] = _TMP
os.environ.pop("ORGTREE_WARM", None)
os.makedirs(_TMP, exist_ok=True)
with open(os.path.join(_TMP, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address":"http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient                    # noqa: E402
from orgtree import api, store                               # noqa: E402
from orgtree.ledger import USER                               # noqa: E402

FAILED: list[str] = []
PASSED = 0
client = TestClient(api.app)


def check(label, fn) -> None:
    global PASSED
    try:
        fn()
    except Exception as e:                                   # noqa: BLE001
        FAILED.append(f"{label}\n      {type(e).__name__}: {e}")
        print(f"  x {label}")
    else:
        PASSED += 1
        print(f"  ok {label}")


_n = [0]


def fresh_org():
    """A top-level agent, not USER itself — `present_document` puts a card
    IN FRONT OF the user, so the presenter is always an agent node; USER is
    the audience, never a valid `nid`."""
    _n[0] += 1
    org = store.create_org(f"docgallery-{_n[0]}", [])
    org.hire(USER, None, "haiku", 2, "boss")
    return org, org.d["slug"]


def empty_org_returns_empty_list() -> None:
    _org, slug = fresh_org()
    r = client.get(f"/api/orgs/{slug}/documents")
    assert r.status_code == 200, r.text
    assert r.json()["documents"] == []


check("GET .../documents · a fresh org has an empty list", empty_org_returns_empty_list)


def live_docs_come_back_newest_first() -> None:
    org, slug = fresh_org()
    org.present_document("boss", "first", "body one")
    org.present_document("boss", "second", "body two")
    store.save_org(org)
    rows = client.get(f"/api/orgs/{slug}/documents").json()["documents"]
    assert [r["title"] for r in rows] == ["second", "first"], rows
    assert all(r["node_state"] == "live" and not r["evicted"] for r in rows)
    assert "body" not in rows[0], "the list must stay metadata-only (body fetched on open)"


check("GET .../documents · live cards come back newest-first, metadata only",
      live_docs_come_back_newest_first)


def dismissed_card_disappears_without_an_evicted_row() -> None:
    org, slug = fresh_org()
    did = org.present_document("boss", "read me", "body")["presented"]
    org.dismiss_document(did)
    store.save_org(org)
    rows = client.get(f"/api/orgs/{slug}/documents").json()["documents"]
    assert rows == [], (
        "a card the user dismissed on purpose must not resurface — that is "
        "not a retention eviction")


check("GET .../documents · a plain dismiss does not resurface as an evicted "
      "row (dismiss != eviction)", dismissed_card_disappears_without_an_evicted_row)


def evicted_card_still_shows_with_no_body() -> None:
    org, slug = fresh_org()
    did = org.present_document("boss", "the one being read", "body")["presented"]
    for i in range(10):
        org.present_document("boss", f"later {i}", "body")
    store.save_org(org)
    rows = client.get(f"/api/orgs/{slug}/documents").json()["documents"]
    evicted = [r for r in rows if r["id"] == did]
    assert len(evicted) == 1, (
        "an evicted card vanished from the gallery instead of showing with "
        "its surviving log line")
    assert evicted[0]["evicted"] is True
    assert evicted[0]["title"] == "the one being read"
    assert "body" not in evicted[0]
    # …and opening it hits the reader's existing, already-correct 404
    body = client.get(f"/api/orgs/{slug}/documents/{did}")
    assert body.status_code == 404
    assert "evicted" in body.text.lower()


check("GET .../documents · an evicted card still shows (title + evicted "
      "flag, no body) instead of silently vanishing", evicted_card_still_shows_with_no_body)


def node_state_badges_archived_and_deleted() -> None:
    org, slug = fresh_org()
    org.hire(USER, None, "haiku", 2, "kid")
    kid_doc = org.present_document("kid", "kid's plan", "body")["presented"]
    org.retire(USER, "kid")
    store.save_org(org)
    rows = client.get(f"/api/orgs/{slug}/documents").json()["documents"]
    kid_row = next(r for r in rows if r["id"] == kid_doc)
    assert kid_row["node_state"] == "archived", kid_row

    org2 = store.load_org(slug)
    org2.delete(USER, "kid")
    store.save_org(org2)
    rows2 = client.get(f"/api/orgs/{slug}/documents").json()["documents"]
    kid_row2 = next(r for r in rows2 if r["id"] == kid_doc)
    assert kid_row2["node_state"] == "deleted", (
        "a permanently deleted node's surviving card must badge as deleted, "
        "not be dropped or crash the endpoint")


check("GET .../documents · node_state badges retired (archived) and "
      "permanently deleted presenting agents", node_state_badges_archived_and_deleted)


def no_extra_gallery_cap_hides_evicted() -> None:
    """Live `documents` is already 100 org-wide. A second gallery-side
    `rows[:100]` would drop the evicted log line the user is hunting for."""
    org, slug = fresh_org()
    names = ["boss"]
    for i in range(9):
        name = f"a{i}"
        org.hire(USER, None, "haiku", 2, name)
        names.append(name)
    first = org.present_document("boss", "the one being read", "body")["presented"]
    for name in names:
        n_already = 1 if name == "boss" else 0
        for i in range(10 - n_already):
            org.present_document(name, f"{name} later {i}", "body")
    org.present_document("boss", "the overflow", "body")
    store.save_org(org)
    rows = client.get(f"/api/orgs/{slug}/documents").json()["documents"]
    assert len(rows) == 101, len(rows)
    evicted = next(r for r in rows if r["id"] == first)
    assert evicted["evicted"] is True
    assert evicted["title"] == "the one being read"


check("GET .../documents · no extra 100-row gallery cap hiding evicted "
      "log lines", no_extra_gallery_cap_hides_evicted)


print(f"\n{PASSED} passed, {len(FAILED)} failed")
for f in FAILED:
    print(f"\nFAIL  {f}")
sys.exit(1 if FAILED else 0)
