"""The chat's ``starting...`` fact belongs only before first turn activity.

    python backend/tests/test_turn_activity.py

The turn runner is stubbed at its inner seam so no CLI or deploy can start.
The test reaches the real turn-loop reset, the real live-row recorder, and the
real chat projection.  Clearing the live tail after activity models the exact
gap that made the UI append a false ``starting...`` row after every event.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-turn-activity-")

import _no_deploy  # noqa: E402
from orgtree import store, supervisor  # noqa: E402
from orgtree.ledger import USER  # noqa: E402

_no_deploy.install()
_no_deploy.assert_isolated_data_root()


def main() -> int:
    org = store.create_org("zz turn activity")
    hired = org.hire(
        USER, None, "haiku", 2, "worker", add_dirs=[],
        tools={"bash": False, "web": False, "edit": False,
               "subagents": False, "mcp": []},
        org_visibility="team", charter="turn activity fixture")
    store.save_org(org)
    slug, node = org.d["slug"], hired["node"]
    st = supervisor.state(slug, node)
    st["busy"] = True
    st["turn_activity"] = True  # prior turn's completed activity

    observed: list[bool] = []
    real_one = supervisor._run_one_turn

    def fake_one(turn_slug, turn_node, text):
        observed.append(bool(supervisor.state(
            turn_slug, turn_node).get("turn_activity")))
        return None

    try:
        supervisor._run_one_turn = fake_one
        supervisor._run_turn(slug, node, "next turn")
        assert observed == [False], (
            "a new turn inherited the previous turn's activity marker")

        before = supervisor.read_chat(org, node)
        assert before["busy"] is True, before
        assert before["turn_activity"] is False, before

        supervisor.live_row(slug, node, {
            "kind": "tool", "text": "Read · canary.txt", "id": "canary",
        })
        after = supervisor.read_chat(org, node)
        assert after["turn_activity"] is True, after

        # Control for the reported failure: once the durable transcript has
        # caught up, the live row disappears while the same turn stays busy.
        # Activity must remain true; otherwise the UI calls this CLI startup.
        with supervisor._state_lock:
            st["live"] = []
        caught_up = supervisor.read_chat(org, node)
        assert caught_up["busy"] is True, caught_up
        assert st["live"] == [], st
        assert caught_up["turn_activity"] is True, caught_up
    finally:
        supervisor._run_one_turn = real_one
        try:
            store.delete_org(slug)
        except Exception:  # noqa: BLE001
            pass

    assert _no_deploy.installed(), "deploy interlock was not restored"
    print("1 check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
