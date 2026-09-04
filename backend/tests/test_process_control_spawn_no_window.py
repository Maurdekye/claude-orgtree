"""Windows maintenance process-control commands must not paint consoles.

The watchdog tree-kill path is intentionally absent: it already passes
``CREATE_NO_WINDOW`` directly.  These checks cover the three genuine misses.

Run: ``python backend/tests/test_process_control_spawn_no_window.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree import antigravityrun, supervisor  # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
WANT = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]


def check(label: str, fn) -> None:
    global PASS
    try:
        fn()
    except Exception as e:  # noqa: BLE001 -- report every independent site
        FAIL.append((label, str(e)))
        print(f"  FAIL  {label}: {e}")
    else:
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")


def eq(got, want, detail: str = "") -> None:
    if got != want:
        raise AssertionError(f"got {got!r}, want {want!r} {detail}")


def _completed() -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout="", stderr="")


def supervisor_icacls_flags() -> None:
    if os.name != "nt":
        return
    seen: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        seen.append(kwargs)
        return _completed()

    org = SimpleNamespace(d={"slug": "demo", "workspace": "W:/workspace"})
    with patch.object(supervisor.subprocess, "run", side_effect=fake_run), \
            patch.object(supervisor.sbx, "on_disk", return_value=False), \
            patch.object(supervisor.store, "scratch_root", return_value="W:/scratch"), \
            patch.object(supervisor.os.path, "isdir", return_value=True):
        supervisor._org_write_acl(org, True)
        supervisor._org_write_acl(org, False)
    eq(len(seen), 4, "two targets, block and unblock")
    eq([kwargs.get("creationflags") for kwargs in seen], [WANT] * 4)


class _FakeProc:
    pid = 12345

    def poll(self):
        return None

    def kill(self):
        pass

    def wait(self, timeout=None):
        pass


def antigravity_taskkill_flags() -> None:
    if os.name != "nt":
        return
    seen: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        seen.append(kwargs)
        return _completed()

    with patch.object(antigravityrun.subprocess, "run", side_effect=fake_run):
        antigravityrun.kill_tree(_FakeProc())
    eq(len(seen), 1)
    eq(seen[0].get("creationflags"), WANT)


class _ReconcileOrg:
    def __init__(self) -> None:
        self.nodes = {"n": {
            "session_id": "sid", "state": "live",
            "remote_controlled": {"pid": 12345},
        }}
        self.d: dict[str, object] = {}

    def waking_mail(self, nid: str) -> bool:
        return False


def reconcile_taskkill_flags() -> None:
    if os.name != "nt":
        return
    seen: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        seen.append(kwargs)
        return _completed()

    org = _ReconcileOrg()
    with patch.object(supervisor.store, "load_org", return_value=org), \
            patch.object(supervisor.store, "save_org"), \
            patch.object(supervisor, "_transcript_evidence", return_value=set()), \
            patch.object(supervisor, "_condemnable", return_value=False), \
            patch.object(supervisor.subprocess, "run", side_effect=fake_run):
        eq(supervisor.reconcile("demo"), [])
    eq(len(seen), 1)
    eq(seen[0].get("creationflags"), WANT)


def main() -> int:
    print("Windows maintenance process controls never paint a window:")
    check("supervisor storage icacls block/unblock", supervisor_icacls_flags)
    check("antigravity turn-tree taskkill", antigravity_taskkill_flags)
    check("supervisor reconcile stale remote-control taskkill",
          reconcile_taskkill_flags)
    print(f"\n{PASS} ok, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
