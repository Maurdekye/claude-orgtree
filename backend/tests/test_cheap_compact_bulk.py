"""Bulk cheap-compact — org-wide and subtree sweeps on top of the single-node
primitive (`Org.cheap_compact`).

    python backend/tests/test_cheap_compact_bulk.py    (no pytest; plain asserts)

Two halves:

**Hermetic ledger** drives `Org.cheap_compact_many` directly: skip-and-continue
past an ineligible node, top-down ordering, and the empty-list no-op.

**API** exercises the two HTTP entry points against the real FastAPI app (ASGI,
hand-built scope — same pattern as test_api_surface.py, own isolated fixtures
so nothing here touches that file's shared K/K2/SBX orgs):
  - `POST /api/orgs/{slug}/cheap-compact-all`
  - the `cheap_compact_subtree` op on `POST /api/orgs/{slug}/ops`
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import traceback

sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-ccbulk-")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as _f:
    _f.write('{"net_hub_address": "http://127.0.0.1:9"}')
os.environ["ORGTREE_PORT"] = "7411"
os.environ["ORGTREE_PUBLIC_PORT"] = "7411"
os.environ.pop("ORGTREE_EXPOSE_ADMIN", None)

from orgtree import api, sandbox, store, supervisor              # noqa: E402
from orgtree.ledger import USER, LedgerError, Org                # noqa: E402

supervisor.chatq_register_org = lambda slug: None
supervisor.chatq_deregister_org = lambda slug: None
supervisor.storage_check = lambda slug: None
sandbox.warm = lambda org: None
sandbox.vm_disk_cap_mib = lambda: None

PASS = 0
FAIL: list[tuple[str, str]] = []


def check(label: str, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


# ============================================================ hermetic ledger

def hspec(**over):
    s = dict(add_dirs=[], tools={"bash": True, "web": False, "edit": False,
                                 "subagents": False, "mcp": []},
             org_visibility="team", charter="test hire")
    s.update(over)
    return s


_hn = [0]


def horg(nodes: int = 1, grant: int = 20) -> tuple[Org, list[str]]:
    _hn[0] += 1
    org = store.create_org(f"zz ccbulk {_hn[0]}")
    ids = []
    for i in range(nodes):
        r = org.hire(USER, None, "haiku", grant, f"a{i}", **hspec())
        ids.append(r["node"])
    store.save_org(org)
    return org, ids


def _():
    org, (a, b, c) = horg(3)
    result = org.cheap_compact_many(USER, [a, b, c])
    assert len(result["compacted"]) == 3, result
    assert result["skipped"] == [], result
    for nid in (a, b, c):
        assert org.node(nid)["state"] == "live", "successor stays live"


check("all-eligible sweep compacts every node", _)


def _():
    org, (a, b) = horg(2)
    org.nodes[b]["bg_open"] = True
    result = org.cheap_compact_many(USER, [a, b])
    assert len(result["compacted"]) == 1 and result["compacted"][0]["node"] == a, result
    assert len(result["skipped"]) == 1 and result["skipped"][0]["node"] == b, result
    assert result["skipped"][0]["reason"], "reason must not be blank"
    # the eligible node is unaffected by the skip
    assert org.node(a)["state"] == "live"


check("a bg_open node is skipped, not fatal to the rest", _)


def _():
    org, (a,) = horg(1)
    org.retire(USER, a)
    result = org.cheap_compact_many(USER, [a])
    assert result["compacted"] == [], result
    assert len(result["skipped"]) == 1 and result["skipped"][0]["node"] == a, result


check("a non-live node is skipped, not fatal", _)


def _():
    org, (a, b) = horg(2)
    result = org.cheap_compact_many(USER, [])
    assert result == {"compacted": [], "skipped": []}, result
    assert org.node(a)["state"] == "live" and org.node(b)["state"] == "live"


check("empty node list is a no-op", _)


def _():
    org, (a,) = horg(1)
    kid = org.hire(USER, a, "haiku", 5, "kid", **hspec())["node"]
    grandkid = org.hire(USER, kid, "haiku", 0, "gk", **hspec())["node"]
    store.save_org(org)
    order = [a] + org.descendants(a, live_only=True)
    assert order == [a, kid, grandkid], order
    result = org.cheap_compact_many(USER, order)
    assert [r["node"] for r in result["compacted"]] == [a, kid, grandkid], result


check("descendants() + cheap_compact_many together sweep top-down", _)


def _():
    org, _ids = horg(1)
    nids = org.descendants(None, live_only=True)
    assert nids, "org-wide descendants(None) should find the root(s) hired above"
    result = org.cheap_compact_many(USER, nids)
    assert len(result["compacted"]) == len(nids), result


check("descendants(None) walks every root — the org-wide caller's own build", _)


# =================================================================== API

def call(app, method, path, body=None):
    payload = b"" if body is None else json.dumps(body).encode()
    hdrs = [(b"host", b"127.0.0.1:7411")]
    if body is not None:
        hdrs.append((b"content-type", b"application/json"))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "path": path, "raw_path": path.encode(),
             "query_string": b"", "root_path": "", "headers": hdrs,
             "scheme": "http", "client": ("127.0.0.1", 5555),
             "server": ("127.0.0.1", 7411)}
    status_box: dict = {}
    body_chunks: list[bytes] = []
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            status_box["status"] = message["status"]
        elif message["type"] == "http.response.body":
            body_chunks.append(message.get("body", b""))

    exc = None
    try:
        asyncio.run(app(scope, receive, send))
    except Exception as e:                                       # noqa: BLE001
        exc = e
    raw = b"".join(body_chunks)
    try:
        js = json.loads(raw)
    except Exception:                                             # noqa: BLE001
        js = None
    return status_box.get("status"), js, exc


ADMIN = api.app


def mkorg(name: str) -> str:
    st, js, exc = call(ADMIN, "POST", "/api/orgs", {"name": name})
    assert st == 200 and exc is None, (st, js, exc)
    return js["slug"]


def hire(slug: str, name: str, parent: str | None = None, grant: int = 5) -> str:
    body = {"op": "hire", "tier": "haiku", "name": name, "grant": grant}
    if parent:
        body["parent"] = parent
    st, js, exc = call(ADMIN, "POST", f"/api/orgs/{slug}/ops", body)
    assert st == 200 and exc is None, (st, js, exc)
    return js["node"]


def _():
    slug = mkorg("ccbulk api 1")
    a = hire(slug, "a")
    b = hire(slug, "b")
    c = hire(slug, "c", parent=a)
    st, js, exc = call(ADMIN, "POST", f"/api/orgs/{slug}/cheap-compact-all")
    assert st == 200 and exc is None, (st, js, exc)
    assert js["compacted"] == 3, js
    assert js["skipped"] == [], js
    assert js["warnings"] == [], js  # F2's export loop reports failures here; a clean sweep reports none
    org = store.load_org(slug)
    for nid in (a, b, c):
        assert org.node(nid)["state"] == "live", nid


check("POST cheap-compact-all: happy path compacts every live node", _)


def _():
    slug = mkorg("ccbulk api 2")
    a = hire(slug, "a")
    b = hire(slug, "b")
    org = store.load_org(slug)
    org.nodes[b]["bg_open"] = True
    store.save_org(org)
    st, js, exc = call(ADMIN, "POST", f"/api/orgs/{slug}/cheap-compact-all")
    assert st == 200 and exc is None, (st, js, exc)
    assert js["compacted"] == 1, js
    assert len(js["skipped"]) == 1 and js["skipped"][0]["node"] == b, js


check("POST cheap-compact-all: one bg_open node skipped, reported, rest proceed", _)


def _():
    slug = mkorg("ccbulk api 3")
    root = hire(slug, "root")
    kid = hire(slug, "kid", parent=root)
    sibling = hire(slug, "sib")   # NOT under root — must be untouched
    sibling_sid_before = store.load_org(slug).node(sibling)["session_id"]
    st, js, exc = call(ADMIN, "POST", f"/api/orgs/{slug}/ops",
                       {"op": "cheap_compact_subtree", "node": root})
    assert st == 200 and exc is None, (st, js, exc)
    assert len(js["compacted"]) == 2, js
    assert {r["node"] for r in js["compacted"]} == {root, kid}, js
    org = store.load_org(slug)
    assert org.node(sibling)["session_id"] == sibling_sid_before, \
        "unrelated sibling's session must be untouched by a subtree sweep"


check("cheap_compact_subtree op: only the target + its own descendants", _)


def _():
    slug = mkorg("ccbulk api 4")
    root = hire(slug, "root")
    st, js, exc = call(ADMIN, "POST", f"/api/orgs/{slug}/ops",
                       {"op": "cheap_compact_subtree", "node": root})
    assert st == 200 and exc is None, (st, js, exc)
    assert len(js["compacted"]) == 1 and js["compacted"][0]["node"] == root, js
    assert js["skipped"] == [], js


check("cheap_compact_subtree op: a leaf's subtree is just itself", _)


def _():
    """F1 (coordinator/code-review 2026-09-09): cheap_compact's own
    _require_authority raises the SAME LedgerError type as its not-live/
    bg_open refusals, so an unguarded cheap_compact_many would report an
    authority denial as a routine 'skip' — a permission violation must 422
    like every other authority-gated op, not come back as 200 housekeeping."""
    slug = mkorg("ccbulk api 6")
    root = hire(slug, "root")
    outsider = hire(slug, "outsider")   # a top-level sibling, NOT root's ancestor
    st, js, exc = call(ADMIN, "POST", f"/api/orgs/{slug}/ops",
                       {"op": "cheap_compact_subtree", "node": root, "actor": outsider})
    assert st == 422 and exc is None, (st, js, exc)
    org = store.load_org(slug)
    assert org.node(root)["state"] == "live", "an authority denial must compact nothing"


check("cheap_compact_subtree op: an authority denial 422s "
      "(not a silent 200 'skipped' housekeeping entry)", _)


def _():
    slug = mkorg("ccbulk api 5")
    st, js, exc = call(ADMIN, "POST", f"/api/orgs/{slug}/ops",
                       {"op": "cheap_compact_subtree", "node": "no-such-node"})
    assert st == 422 and exc is None, (st, js, exc)


check("cheap_compact_subtree op: an unknown node 422s, same as every other op "
      "(not a silent 200 with an empty skip list)", _)


def _():
    """Ruling #2 (coordinator, 2026-09-08): the bulk paths must not introduce
    interrupt-before-archive logic the single-node cheap_compact op does not
    already have. `_org_op_locked`'s pre-guard only fires for
    retire/dissolve/rescind — cheap_compact and cheap_compact_subtree are
    absent from that list, same as the single-node op already is."""
    import inspect
    src = inspect.getsource(api)
    marker = 'if body.op in ("retire", "dissolve", "rescind") and body.node:'
    idx = src.index(marker)
    # the very next non-comment op check after the interrupt guard's own
    # branch is the lock acquisition — cheap_compact/_subtree never appear
    # inside that guard's condition
    guard_line = src[idx:src.index("\n", idx)]
    assert "cheap_compact" not in guard_line, guard_line


check("neither bulk op is in the interrupt-before-archive guard "
      "(mirrors single-node cheap_compact, which is also absent)", _)


for _row in list(store.list_orgs()):
    try:
        store.delete_org(_row["slug"])
    except Exception:                                              # noqa: BLE001
        pass

print()
if FAIL:
    print(f"{len(FAIL)} FAILURE(S):")
    for label, tb in FAIL:
        print(f"--- {label} ---\n{tb}")
    sys.exit(1)
print(f"all {PASS} checks passed")
