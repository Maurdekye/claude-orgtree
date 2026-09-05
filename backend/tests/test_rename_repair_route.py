"""`POST /api/orgs/{slug}/repair-rename` — the supported route for records a
rename left behind, and the reason it is a ROUTE at all.

`store.DOC_LOCK` is a `threading.RLock`: per process. Only code running inside
the backend can take the lock that every other load→mutate→save cycle takes,
so an out-of-process script — however carefully it compare-and-swaps the
stored row — can still have its write overwritten by a backend that loaded the
document earlier and saves later. That is the whole argument for this
endpoint, so §2 below asserts the handler really does hold the lock rather
than trusting the `with` statement to have been written.

The authority rules, the allowlist and the old-value checks live in
`Org.repair_rename_identity` and are covered by test_rename.py §7. This file
covers the parts only the HTTP layer has: the lock, the save, the status
codes, and that a refusal leaves the stored document untouched.

    python backend/tests/test_rename_repair_route.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading

_TMP = tempfile.mkdtemp(prefix="orgtree-renamerepair-")
os.environ["ORGTREE_DATA"] = _TMP
os.environ.pop("ORGTREE_WARM", None)
os.makedirs(_TMP, exist_ok=True)
with open(os.path.join(_TMP, "defaults.json"), "w", encoding="utf-8") as _f:
    _f.write('{"net_hub_address":"http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient                     # noqa: E402
from orgtree import api, store                                # noqa: E402
from orgtree.ledger import USER                               # noqa: E402

FAILED: list[str] = []
PASSED = 0
client = TestClient(api.app)


def check(label, fn) -> None:
    global PASSED
    try:
        fn()
    except Exception as e:                                    # noqa: BLE001
        FAILED.append(f"{label}\n      {type(e).__name__}: {e}")
        print(f"  x {label}")
    else:
        PASSED += 1
        print(f"  ok {label}")


_n = [0]


def stranded_org():
    """An org in exactly the state the live document was in: a rename that
    happened, and records still filed under the name it replaced.

    The stranding is planted AFTER the rename on purpose — that is what a
    rename from before the re-key left behind, and it is the only honest way
    to reproduce it now that `rename` carries these fields itself."""
    _n[0] += 1
    org = store.create_org(f"renamerepair-{_n[0]}", [])
    org.hire(USER, None, "opus", 20, "root")
    org.hire("root", "root", "haiku", 2, "worker",
             add_dirs=[], tools={"bash": True, "web": True, "edit": True,
                                 "subagents": True, "mcp": []},
             org_visibility="team", charter="t")
    w = org.work_create("root", "a job the root owns", "problem; solution",
                        owner="root")
    slug_ref = str(w["slug"])
    org.present_document("root", "a plan", "the body")
    did = next(d["id"] for d in org.d["documents"])
    org.rename(USER, "root", "root-renamed")
    at = [e["at"] for e in org.d["events"] if e.get("op") == "rename"][-1]
    it, _a = org._work_find(slug_ref)
    it["owner"] = {"node": "root", "generation": 0}
    it["last_updater"] = {"node": "root", "generation": 0}
    next(d for d in org.d["documents"] if d["id"] == did)["node"] = "root"
    store.save_org(org)
    return org.d["slug"], at, did, slug_ref


class _CountingLock:
    """Delegates to the real lock and records that the handler took it."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.entered = 0
        self.held_at_exit = None

    def __enter__(self):
        self.entered += 1
        return self.inner.__enter__()

    def __exit__(self, *a):
        return self.inner.__exit__(*a)


def repairs_and_persists() -> None:
    slug, at, did, ref = stranded_org()
    r = client.post(f"/api/orgs/{slug}/repair-rename",
                    json={"rename_at": at, "documents": [did],
                          "work_items": [ref]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] and body["old"] == "root" and body["new"] == "root-renamed"
    assert {w["field"] for w in body["work_items"]} == {"owner", "last_updater"}

    # from DISK, not from the response — the point is that save_org ran
    org = store.load_org(slug)
    assert next(d for d in org.d["documents"] if d["id"] == did)["node"] \
        == "root-renamed"
    it, _a = org._work_find(ref)
    assert it["owner"]["node"] == "root-renamed", it["owner"]
    assert it["created_by"]["node"] == "root", \
        "authorship must survive the route as well as the ledger call"
    # and the symptom is gone through the reader the agent itself uses
    lists = org.work_list("root-renamed", include_archived=True,
                          include_backlogged=True)
    refs = {x.get("slug") for v in lists.values() if isinstance(v, list)
            for x in v if isinstance(x, dict)}
    assert ref in refs, refs


check("POST .../repair-rename repairs the named records and SAVES them",
      repairs_and_persists)


def takes_the_doc_lock() -> None:
    slug, at, did, _ref = stranded_org()
    real = store.DOC_LOCK
    spy = _CountingLock(real)
    store.DOC_LOCK = spy                                      # type: ignore[assignment]
    try:
        r = client.post(f"/api/orgs/{slug}/repair-rename",
                        json={"rename_at": at, "documents": [did]})
    finally:
        store.DOC_LOCK = real                                 # type: ignore[assignment]
    assert r.status_code == 200, r.text
    assert spy.entered == 1, (
        f"the handler entered store.DOC_LOCK {spy.entered} times — the whole "
        f"reason this repair is an endpoint is that it runs under the same "
        f"per-process lock as every other load/mutate/save")
    assert isinstance(real, type(threading.RLock())), \
        "if DOC_LOCK stops being a per-process lock, revisit this argument"


check("the handler runs under the real store.DOC_LOCK", takes_the_doc_lock)


def refusal_is_422_and_writes_nothing() -> None:
    slug, at, did, ref = stranded_org()
    before = store.load_org(slug)
    doc_before = next(d for d in before.d["documents"] if d["id"] == did)["node"]

    r = client.post(f"/api/orgs/{slug}/repair-rename",
                    json={"rename_at": "2020-01-01T00:00:00.000Z",
                          "documents": [did]})
    assert r.status_code == 422 and "exactly one logged rename" in r.text, r.text

    r = client.post(f"/api/orgs/{slug}/repair-rename",
                    json={"rename_at": at, "documents": ["d-nope"]})
    assert r.status_code == 422 and "no document" in r.text, r.text

    r = client.post(f"/api/orgs/{slug}/repair-rename",
                    json={"rename_at": at})
    assert r.status_code == 422 and "allowlist" in r.text, r.text

    after = store.load_org(slug)
    assert next(d for d in after.d["documents"] if d["id"] == did)["node"] \
        == doc_before == "root", "a refused repair must write nothing"
    it, _a = after._work_find(ref)
    assert it["owner"]["node"] == "root"


check("a refused repair is 422 and leaves the stored document untouched",
      refusal_is_422_and_writes_nothing)


def actor_is_checked_by_the_ledger() -> None:
    slug, at, did, _ref = stranded_org()
    r = client.post(f"/api/orgs/{slug}/repair-rename",
                    json={"rename_at": at, "documents": [did],
                          "actor": "worker"})
    assert r.status_code == 422 and "only the user" in r.text, r.text
    assert next(d for d in store.load_org(slug).d["documents"]
                if d["id"] == did)["node"] == "root"
    # the renamed identity itself may, and the default actor is the user
    r = client.post(f"/api/orgs/{slug}/repair-rename",
                    json={"rename_at": at, "documents": [did],
                          "actor": "root-renamed"})
    assert r.status_code == 200, r.text


check("the actor travels to the ledger and an unauthorised one is refused",
      actor_is_checked_by_the_ledger)


# "checks passed" is the phrase tools/run_tests.py counts (_CHECKS); without
# it the suite runs but reports a blank total in the summary table
def _make_legacy(slug: str) -> None:
    """Put the stored document back into old-style docket identity: an opaque
    `id` on every item and no `slug`. That is what a document written before
    the slug migration looks like, and the state this route has to convert."""
    org = store.load_org(slug)
    for i, it in enumerate(org.d.get("work_items") or []):
        it["id"] = f"w{i:08x}"
        it.pop("slug", None)
    store.save_org(org)
    assert store.load_org(slug).work_identity_state() == "legacy", \
        "the fixture did not actually produce a legacy document"


def first_request_on_a_legacy_document_converts_and_repairs() -> None:
    """The repair writes item fields DIRECTLY, so it never passes through
    `_work_sweep` and the ledger's identity guard does not fire for it. The
    route therefore converts in its own save. Without that it would leave a
    converted-looking repair on an unconverted document, and the next read
    would 409."""
    slug, at, did, ref = stranded_org()
    _make_legacy(slug)
    r = client.post(f"/api/orgs/{slug}/repair-rename",
                    json={"rename_at": at, "documents": [did]})
    assert r.status_code == 200, r.text
    assert r.json()["migrated"], \
        "the response must say it converted — a silent conversion is a lie " \
        "of omission about what the request did"
    org = store.load_org(slug)
    assert org.work_identity_state() == "slug", \
        "the document is still legacy after a successful repair"
    assert next(d for d in org.d["documents"] if d["id"] == did)["node"] \
        == "root-renamed"
    # the read path the missing conversion used to break
    assert client.get(f"/api/orgs/{slug}/work-items").status_code == 200
    _ = ref


check("a first repair on a LEGACY document converts it and repairs, in one "
      "save", first_request_on_a_legacy_document_converts_and_repairs)


def a_refusal_leaves_the_pending_migration_pending() -> None:
    """A refused repair must write nothing — including the conversion the
    route would have done. Asserted from DISK, not from the response."""
    slug, at, did, _ref = stranded_org()
    _make_legacy(slug)
    before = store.load_org(slug)
    doc_before = next(d for d in before.d["documents"] if d["id"] == did)["node"]

    for body, why in (
        ({"rename_at": "2020-01-01T00:00:00.000Z", "documents": [did]},
         "no such rename"),
        ({"rename_at": at, "documents": [did, did]}, "duplicate"),
        ({"rename_at": at, "documents": [did], "actor": "worker"}, "actor"),
    ):
        r = client.post(f"/api/orgs/{slug}/repair-rename", json=body)
        assert r.status_code == 422, f"{why}: {r.status_code} {r.text}"

    after = store.load_org(slug)
    assert after.work_identity_state() == "legacy", \
        "a refused repair converted the document anyway — the migration was " \
        "pending and must still be pending"
    assert next(d for d in after.d["documents"] if d["id"] == did)["node"] \
        == doc_before == "root"


check("a refused repair leaves the stored document untouched, INCLUDING a "
      "pending migration", a_refusal_leaves_the_pending_migration_pending)


print(f"\n{PASSED} checks passed, {len(FAILED)} failed")
for f in FAILED:
    print(f"\nFAIL  {f}")
sys.exit(1 if FAILED else 0)
