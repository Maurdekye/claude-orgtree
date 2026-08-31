"""Process lifecycle visibility: live is not warm, and stale says why.

Run: python backend/tests/test_process_lifecycle_fields.py
"""
import os
import sys
import tempfile
import threading
import types

ROOT = tempfile.mkdtemp(prefix="orgtree-proclife-")
os.environ["ORGTREE_DATA"] = ROOT
os.makedirs(ROOT, exist_ok=True)
with open(os.path.join(ROOT, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address":"http://127.0.0.1:9"}')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient                         # noqa: E402
from orgtree import api, store, supervisor as S, warmpool as W   # noqa: E402
from orgtree.ledger import USER                                  # noqa: E402


org = store.create_org("process lifecycle fields")
org.hire(USER, None, "haiku", 0, "agent", add_dirs=[],
         tools={"bash": False, "web": False, "edit": False,
                "subagents": False, "mcp": []},
         org_visibility="team", charter="old prompt")
store.save_org(org)
slug, nid = org.d["slug"], "agent"

old_parts = {"prompt": "old", "argv": "same", "cred": "same",
             "envov": "same"}
new_parts = {**old_parts, "prompt": "new"}
wp = types.SimpleNamespace(
    slug=slug, nid=nid, hash="old-hash", ident_components=old_parts,
    alive=lambda: True, _lk=threading.Lock(), identity_change=None)

saved = (W._busy, W.eligible, W.identity_snapshot, W._journal_exit_once)
with W._pool_lock:
    old_serving = dict(W._serving)
    W._serving[(slug, nid)] = wp
try:
    W._busy = lambda s, n: (s, n) == (slug, nid)
    W.eligible = lambda _org, _nid: (True, "")
    W.identity_snapshot = lambda _org, _nid, **_kw: ("new-hash", new_parts)
    W._set_proc_lifecycle(slug, nid, live=True, owner=wp)

    # Boundary-before-keeper: delivery's result boundary is itself an
    # identity observer. The remaining in-process deliveries must immediately
    # show that this generation will be replaced, even if the keeper has not
    # had a pass since the prompt edit.
    current, _label, why = W.boundary_check(slug, nid, "old-hash", wp)
    assert current is False and why == "identity-changed"
    st = S.state(slug, nid)
    assert st["proc_live"] is True
    assert st["proc_relaunch"] is True
    assert st["proc_relaunch_reason"] == \
        "identity-changed — system prompt changed"

    # Deterministic busy + identity-dirty case: the keeper cannot replace a
    # serving process yet, but must publish the scheduled replacement now.
    W._keeper_pass()
    st = S.state(slug, nid)
    assert st["proc_live"] is True
    assert st["proc_relaunch"] is True
    assert st["proc_relaunch_reason"] == \
        "identity-changed — system prompt changed"

    payload = TestClient(api.app).get(f"/api/orgs/{slug}")
    assert payload.status_code == 200, payload.text[:300]
    node = payload.json()["roots"][0]
    assert node["proc_warm"] is False, node
    assert node["proc_live"] is True, node
    assert node["proc_relaunch"] is True, node
    assert node["proc_relaunch_reason"] == \
        "identity-changed — system prompt changed", node

    # Stale-then-current: a later authoritative pass for the SAME serving
    # generation clears an earlier relaunch observation. Leaving it latched
    # would describe a replacement the backend no longer plans to make.
    W.identity_snapshot = lambda _org, _nid, **_kw: ("old-hash", old_parts)
    W._keeper_pass()
    st = S.state(slug, nid)
    assert (st["proc_live"], st["proc_relaunch"],
            st["proc_relaunch_reason"]) == (True, False, None)

    # Old-exit/new-process: the old pump's EOF arrives after a new generation
    # has become the current live owner. Its own exit is still journaled, but
    # it cannot clear the new process's UI liveness.
    newer = types.SimpleNamespace(
        slug=slug, nid=nid, hash="newer", ident_components=new_parts,
        alive=lambda: True, _lk=threading.Lock(), identity_change=None)
    wp.claimed = True
    W._set_proc_lifecycle(slug, nid, live=True, owner=newer, adopt=True)
    with W._pool_lock:
        W._serving[(slug, nid)] = newer
    W._journal_exit_once = lambda _wp, _reason=None: None
    W._on_proc_exit(wp)
    st = S.state(slug, nid)
    assert st["proc_live"] is True
    assert st["proc_lifecycle_owner"] is newer
    assert st["proc_relaunch"] is False
finally:
    W._busy, W.eligible, W.identity_snapshot, W._journal_exit_once = saved
    with W._pool_lock:
        W._serving.clear()
        W._serving.update(old_serving)

print("process lifecycle fields OK")
