"""Focused coverage for the authenticated descendant unstick operation."""
import os
import sys
import tempfile
from types import SimpleNamespace

os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-agent-unstick-")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree import store  # noqa: E402
from orgtree import api  # noqa: E402
from orgtree.api import AgentCall  # noqa: E402
from orgtree.ledger import LedgerError, USER  # noqa: E402
from orgtree import mcptool  # noqa: E402
from orgtree import opreceipts  # noqa: E402


def spec():
    return {"add_dirs": [], "tools": {"bash": True, "web": False,
            "edit": False, "subagents": False, "mcp": []},
            "org_visibility": "team", "charter": "test"}


def main():
    org = store.create_org("agent unstick focused")
    org.hire(USER, None, "opus", 20, "boss", **spec())
    org.hire("boss", "boss", "fable", 10, "kid", **spec())
    org.hire(USER, None, "haiku", 0, "peer", **spec())
    org.node("kid")["frozen"] = {"limit": True, "resume_texts": ["resume"]}
    org.node("kid")["limit_locked"] = True
    org.d["spend_frozen"] = True
    attached = org.handle_attached_at("kid", "fixture-handle")
    assert attached == org.handle_attached_at("kid", "fixture-handle")

    try:
        org.unstick("boss", "boss")
    except LedgerError:
        pass
    else:
        raise AssertionError("self unstick was allowed")
    for actor in ("peer",):
        try:
            org.unstick(actor, "kid")
        except LedgerError:
            pass
        else:
            raise AssertionError("peer unstick was allowed")

    result = org.unstick("boss", "kid")
    assert result["released"] == ["frozen", "limit_locked"]
    assert org.node("kid")["unstuck"]["by"] == "boss"
    assert org.d["spend_frozen"] is True

    org.node("kid")["frozen"] = {"limit": True}
    user_result = org.unstick(USER, "kid")
    assert user_result["released"] == ["frozen"]
    assert org.node("kid")["unstuck"]["by"] == USER

    org.node("kid")["frozen"] = {"limit": True,
                                  "resume_texts": ["retained text"],
                                  "resume_views": ["retained view"]}
    store.save_org(org)
    sent = []
    old_send, old_notify = api.supervisor.send_message, api.supervisor.notify
    api.supervisor.send_message = lambda *args, **kwargs: sent.append((args, kwargs)) or {}
    api.supervisor.notify = lambda *args: sent.append((args, {}))
    dispatched = api.agent_call(
        AgentCall(org=org.d["slug"], node="boss", tool="orgtree_unstick",
                  args={"node": "kid"}),
        SimpleNamespace(state=SimpleNamespace(bridge_slug=None)))
    assert dispatched["released"] == ["frozen"]
    assert sent[0][0][2] == "retained text"
    assert sent[0][1]["view"] == "retained view"
    assert sent[-1][0][-1] == "turn_started"
    assert "frozen" not in store.load_org(org.d["slug"]).node("kid")

    # Keyed dispatch proves the save-before-resume ordering and the durable
    # TX_POST receipt: a transport retry is answered without a second drive.
    org.node("kid")["frozen"] = {"limit": True,
                                  "resume_texts": ["retry text"],
                                  "resume_views": ["retry view"]}
    store.save_org(org)
    epoch = api.agent_call(
        AgentCall(org=org.d["slug"], node="boss", tool="orgtree_op_epoch"),
        SimpleNamespace(state=SimpleNamespace(bridge_slug=None)))
    key = opreceipts.mint_key()
    wrapped = AgentCall(
        org=org.d["slug"], node="boss", tool="orgtree_op_call",
        args={"tool": "orgtree_unstick", "args": {"node": "kid"},
              "op_key": key, "op_epoch": epoch["epoch"]})
    before_retry = len(sent)
    first_keyed = api.agent_call(wrapped, SimpleNamespace(
        state=SimpleNamespace(bridge_slug=None)))
    assert first_keyed["released"] == ["frozen"]
    assert len(sent) == before_retry + 2
    assert "frozen" not in store.load_org(org.d["slug"]).node("kid")
    replay = api.agent_call(wrapped, SimpleNamespace(
        state=SimpleNamespace(bridge_slug=None)))
    assert replay["replayed"] is True
    assert len(sent) == before_retry + 2

    # A no-op must neither drive nor append an unstick receipt.
    before_noop = len(sent)
    noop = api.agent_call(
        AgentCall(org=org.d["slug"], node="boss", tool="orgtree_unstick",
                  args={"node": "kid"}),
        SimpleNamespace(state=SimpleNamespace(bridge_slug=None)))
    assert noop["released"] == [] and len(sent) == before_noop

    # An unauthorized target is refused before mutation or drive.
    peer_before = dict(store.load_org(org.d["slug"]).node("peer"))
    denied_before = len(sent)
    try:
        api.agent_call(
            AgentCall(org=org.d["slug"], node="peer", tool="orgtree_unstick",
                      args={"node": "kid"}),
            SimpleNamespace(state=SimpleNamespace(bridge_slug=None)))
    except Exception:
        pass
    else:
        raise AssertionError("unauthorized API unstick was allowed")
    assert len(sent) == denied_before
    assert store.load_org(org.d["slug"]).node("peer") == peer_before
    api.supervisor.send_message, api.supervisor.notify = old_send, old_notify

    names = {t["name"] for t in mcptool.TOOLS}
    assert "orgtree_unstick" in names
    print("agent unstick focused checks: PASS")


if __name__ == "__main__":
    main()
