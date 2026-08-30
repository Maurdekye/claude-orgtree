"""A primed restart has a visible armed -> executing transition.

    python backend/tests/test_prime_restart_executing.py

The fake deploy child blocks until the test releases it.  While it is blocked,
the real org-tree API and the real prime-status tool handler must repeatedly
report the executing state, never the old armed state and never ``None``.
Releasing the child models a deploy helper that exited without killing this
backend; only then may the surviving process clear the progress state.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-prime-exec-")
with open(os.path.join(os.environ["ORGTREE_DATA"], "defaults.json"), "w",
          encoding="utf-8") as stream:
    stream.write('{"net_hub_address": "http://127.0.0.1:9"}')

import _no_deploy  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from orgtree import api, store, supervisor  # noqa: E402
from orgtree.ledger import USER  # noqa: E402

_no_deploy.install()
_no_deploy.assert_isolated_data_root()


class BlockingChild:
    def __init__(self) -> None:
        self.done = threading.Event()

    def wait(self, timeout=None):
        if not self.done.wait(timeout):
            raise TimeoutError("fake deploy child still running")
        return 0


def wait_until(fn, timeout=5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


def reset_machine() -> None:
    with supervisor._prime_lock:
        try:
            os.remove(supervisor._prime_path())
        except FileNotFoundError:
            pass
    with supervisor._state_lock:
        supervisor._state.clear()
    supervisor._deploy_done.set()
    supervisor._prime_idle_since[0] = 0.0
    supervisor._self_restart_at[0] = 0.0


def make_org() -> tuple[str, str]:
    org = store.create_org("zz prime executing")
    result = org.hire(
        USER, None, "haiku", 2, "boss", add_dirs=[],
        tools={"bash": False, "web": False, "edit": False,
               "subagents": False, "mcp": []},
        org_visibility="team", charter="prime executing fixture")
    store.save_org(org)
    return org.d["slug"], result["node"]


def main() -> int:
    reset_machine()
    slug, node = make_org()
    child = BlockingChild()
    states_at_spawn: list[dict | None] = []
    real_spawn = supervisor._detached_spawn

    def fake_spawn(args, cwd, logpath, env=None):
        states_at_spawn.append(supervisor.primed_restart())
        return child

    try:
        with TestClient(api.app) as client:
            armed = supervisor.arm_prime_restart(
                slug, node, "org", "state transition canary")["primed"]
            projected = client.get(f"/api/orgs/{slug}").json()[
                "primed_restart"]
            assert projected["state"] == "armed", projected
            assert projected["by_node"] == node, projected

            supervisor._detached_spawn = fake_spawn
            fired = supervisor._fire_prime(armed)
            assert fired["fired"] is True, fired
            assert states_at_spawn and states_at_spawn[-1] is not None, \
                "the prime vanished between armed and executing"
            assert states_at_spawn[-1]["state"] == "executing", \
                states_at_spawn[-1]

            # Repeated reads while the deploy helper is alive: this is the
            # shutdown window the old implementation exposed as idle.
            for _ in range(3):
                progress = client.get(f"/api/orgs/{slug}").json()[
                    "primed_restart"]
                assert progress is not None, \
                    "the UI API went idle after the prime triggered"
                assert progress["state"] == "executing", progress
                assert progress["target"] == "org", progress

            status = client.post("/api/agent", json={
                "org": slug, "node": node, "tool": "orgtree_prime_restart",
                "args": {"action": "status"},
            })
            assert status.status_code == 200, status.text
            assert status.json()["status"] == "restart in progress...", \
                status.json()

            # Control: if the helper exits and this backend is still alive,
            # then no shutdown happened; the progress state must not stick.
            child.done.set()
            assert wait_until(lambda: supervisor.primed_restart() is None), \
                "a failed/non-killing deploy left restart progress forever"

            # A successful restart kills the old backend before its watcher
            # can clear anything. The replacement process's startup must end
            # that old executing state before it serves a tree response.
            with supervisor._prime_lock:
                data = supervisor._prime_read()
                data["executing"] = {
                    **armed, "execution_id": "old-process",
                    "triggered_at": "2026-08-30T17:00:00.000Z",
                }
                supervisor._prime_write(data)
            supervisor._clear_stale_prime_execution_on_start()
            assert supervisor.primed_restart() is None, \
                "the replacement backend inherited stale restart progress"
            with supervisor._prime_lock:
                ended = supervisor._prime_read()["last_execution_ended"]
            assert ended["why"] == "backend process restarted", ended
    finally:
        supervisor._detached_spawn = real_spawn
        child.done.set()
        try:
            store.delete_org(slug)
        except Exception:  # noqa: BLE001
            pass
        reset_machine()
    assert _no_deploy.installed(), "deploy interlock was not restored"
    print("1 check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
