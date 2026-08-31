"""Process lifecycle visibility: live is not warm, and stale says why.

Run: python backend/tests/test_process_lifecycle_fields.py
"""
import os
import sys
import tempfile
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
    alive=lambda: True)

saved = (W._busy, W.eligible, W.identity_snapshot)
with W._pool_lock:
    old_serving = dict(W._serving)
    W._serving[(slug, nid)] = wp
try:
    W._busy = lambda s, n: (s, n) == (slug, nid)
    W.eligible = lambda _org, _nid: (True, "")
    W.identity_snapshot = lambda _org, _nid, **_kw: ("new-hash", new_parts)
    W._set_proc_lifecycle(slug, nid, live=True)

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

    W._set_proc_lifecycle(slug, nid, live=False)
    st = S.state(slug, nid)
    assert (st["proc_live"], st["proc_relaunch"],
            st["proc_relaunch_reason"]) == (False, False, None)
finally:
    W._busy, W.eligible, W.identity_snapshot = saved
    with W._pool_lock:
        W._serving.clear()
        W._serving.update(old_serving)

print("process lifecycle fields OK")
