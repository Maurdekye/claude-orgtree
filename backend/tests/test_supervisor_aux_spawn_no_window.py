"""Auxiliary Claude subprocesses must stay windowless on Windows.

Each check invokes the enclosing supervisor function and replaces Popen with
a recorder that raises immediately after argument capture.  This reaches the
real call boundary without starting a CLI or exercising unrelated post-spawn
state transitions.

Run: ``python backend/tests/test_supervisor_aux_spawn_no_window.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
import io
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree import supervisor  # noqa: E402

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


def compact_fork_flags() -> None:
    org = _Org()
    state: dict[str, object] = {}
    seen: list[dict[str, object]] = []
    with patch.object(supervisor.store, "load_org", return_value=org), \
            patch.object(supervisor, "claude_model_for", return_value="opus"), \
            patch.object(supervisor.sbx, "is_sandboxed", return_value=False), \
            patch.object(supervisor, "_claude_argv", return_value=["claude"]), \
            patch.object(supervisor, "api_fallback_active", return_value=False), \
            patch.object(supervisor, "spawn_env", return_value={}), \
            patch.object(supervisor, "scratch_dir", return_value="W:/scratch"), \
            patch.object(supervisor, "state", return_value=state), \
            patch.object(supervisor.subprocess, "Popen",
                         side_effect=_raising_recorder(seen)):
        supervisor._compact_split_body("demo", "agent")
    eq(len(seen), 1)
    eq(seen[0].get("creationflags"), WANT)
    eq("recorded spawn" in str(state.get("last_error")), True,
       "the recorder must have reached the handled spawn boundary")


def remote_control_flags() -> None:
    org = _Org()
    seen: list[dict[str, object]] = []
    with patch.object(supervisor.store, "load_org", return_value=org), \
            patch.object(supervisor.store, "save_org"), \
            patch.object(supervisor.sbx, "is_sandboxed", return_value=False), \
            patch.object(supervisor, "state", return_value={"busy": False}), \
            patch.object(supervisor, "scratch_dir", return_value="W:/scratch"), \
            patch.object(supervisor, "_claude_argv", return_value=["claude"]), \
            patch.object(supervisor, "_remote_unpark"), \
            patch("builtins.open", return_value=io.StringIO()), \
            patch.object(supervisor.subprocess, "Popen",
                         side_effect=_raising_recorder(seen)):
        result = supervisor.remote_control_start("demo", "agent")
    eq(len(seen), 1)
    eq(seen[0].get("creationflags"), WANT)
    eq("recorded spawn" in str(result.get("error")), True)


class _ImmediateThread:
    def __init__(self, *, target, daemon=False, **kwargs) -> None:
        self.target = target

    def start(self) -> None:
        self.target()


def immediate_command_flags() -> None:
    org = _Org()
    seen: list[dict[str, object]] = []
    with patch.object(supervisor.store, "load_org", return_value=org), \
            patch.object(supervisor, "_deployment_org_gate"), \
            patch.object(supervisor, "claude_model_for", return_value="opus"), \
            patch.object(supervisor, "_transcript_root", return_value="W:/tx"), \
            patch.object(supervisor, "transcript_path", return_value="present.jsonl"), \
            patch.object(supervisor.sbx, "is_sandboxed", return_value=False), \
            patch.object(supervisor, "_claude_argv", return_value=["claude"]), \
            patch.object(supervisor, "scratch_dir", return_value="W:/scratch"), \
            patch.object(supervisor, "spawn_env", return_value={}), \
            patch.object(supervisor, "live_row"), \
            patch.object(supervisor.threading, "Thread", _ImmediateThread), \
            patch.object(supervisor.subprocess, "Popen",
                         side_effect=_raising_recorder(seen)):
        eq(supervisor.immediate_command("demo", "agent", "/context"), True)
    eq(len(seen), 1)
    eq(seen[0].get("creationflags"), WANT)


def main() -> int:
    print("auxiliary Claude subprocesses never paint a window:")
    check("compaction fork", compact_fork_flags)
    check("remote-control server", remote_control_flags)
    check("immediate-command fork", immediate_command_flags)
    print(f"\n{PASS} ok, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
