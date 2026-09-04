"""Claude cache-maintenance subprocesses must stay windowless on Windows.

Run: ``python backend/tests/test_cache_spawn_no_window.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree import supervisor, warmpool  # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
WANT = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]


def check(label: str, fn) -> None:
    global PASS
    try:
        fn()
    except Exception as e:  # noqa: BLE001 -- report both independent sites
        FAIL.append((label, str(e)))
        print(f"  FAIL  {label}: {e}")
    else:
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")


def eq(got, want, detail: str = "") -> None:
    if got != want:
        raise AssertionError(f"got {got!r}, want {want!r} {detail}")


class _Org:
    def __init__(self) -> None:
        self.d = {"slug": "demo"}
        self.nodes = {"agent": {
            "state": "live", "session_id": "old-session", "model": "opus",
        }}

    def node(self, nid: str):
        return self.nodes[nid]


def _raising_recorder(seen: list[dict[str, object]]):
    def fake_popen(*args, **kwargs):
        seen.append(kwargs)
        raise OSError("recorded spawn")
    return fake_popen


def working_cache_read_flags() -> None:
    org = _Org()
    seen: list[dict[str, object]] = []
    with patch.object(supervisor.store, "load_org", return_value=org), \
            patch.object(supervisor.appsettings, "working_checkups_enabled",
                         return_value=False), \
            patch.object(supervisor, "_working_cache_due", return_value=True), \
            patch.object(supervisor, "_working_cache_retry_due", return_value=True), \
            patch.object(supervisor, "api_fallback_active", return_value=False), \
            patch.object(supervisor, "bills_the_key", return_value=False), \
            patch.object(supervisor, "spawn_env", return_value={}), \
            patch.object(supervisor, "_working_cache_cmd", return_value=["claude"]), \
            patch.object(supervisor, "spawn_argv", return_value=["claude"]), \
            patch.object(supervisor, "_cache_snapshot", return_value={}), \
            patch.object(supervisor, "_cache_persistable", return_value={}), \
            patch.object(supervisor, "_transcript_root", return_value="W:/tx"), \
            patch.object(supervisor, "scratch_dir", return_value="W:/scratch"), \
            patch.object(supervisor, "state", return_value={}), \
            patch.object(supervisor, "_working_cache_note_failure"), \
            patch.object(supervisor.subprocess, "Popen",
                         side_effect=_raising_recorder(seen)):
        supervisor._working_cache_read("demo", "agent")
    eq(len(seen), 1)
    eq(seen[0].get("creationflags"), WANT)


def parked_claude_flags() -> None:
    org = _Org()
    seen: list[dict[str, object]] = []
    with patch.object(supervisor, "_build_cmd", return_value=["claude"]), \
            patch.object(supervisor, "spawn_env", return_value={}), \
            patch.object(supervisor, "env_overrides", return_value={}), \
            patch.object(supervisor, "identity_in_env", return_value="lane"), \
            patch.object(supervisor, "spawn_argv", return_value=["claude"]), \
            patch.object(supervisor, "scratch_dir", return_value="W:/scratch"), \
            patch.object(warmpool, "identity_snapshot",
                         return_value=("identity-hash", {})), \
            patch.object(warmpool, "_journal_proc"), \
            patch.object(warmpool, "_POPEN", _raising_recorder(seen)):
        eq(warmpool._spawn_for(org, "agent", "test"), None)
    eq(len(seen), 1)
    eq(seen[0].get("creationflags"), WANT)


def main() -> int:
    print("Claude cache-maintenance subprocesses never paint a window:")
    check("working-cache disposable fork", working_cache_read_flags)
    check("warmpool parked Claude process", parked_claude_flags)
    print(f"\n{PASS} ok, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
