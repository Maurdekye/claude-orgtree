"""Codex and Antigravity turn runners must spawn windowlessly on Windows.

Run: ``python backend/tests/test_provider_turn_spawn_no_window.py``.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree import antigravityrun, codexrun  # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
WANT = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]


def check(label: str, fn) -> None:
    global PASS
    try:
        fn()
    except Exception as e:  # noqa: BLE001 -- report both provider sites
        FAIL.append((label, str(e)))
        print(f"  FAIL  {label}: {e}")
    else:
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")


def eq(got, want, detail: str = "") -> None:
    if got != want:
        raise AssertionError(f"got {got!r}, want {want!r} {detail}")


class _NoopThread:
    def __init__(self, *, target, daemon=False, **kwargs) -> None:
        self.target = target

    def start(self) -> None:
        pass


class _ImmediateThread(_NoopThread):
    def start(self) -> None:
        self.target()


class _CodexProc:
    def __init__(self) -> None:
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO()
        self.stderr = io.BytesIO()


class _AntigravityProc:
    def __init__(self, model: str) -> None:
        self.stdin = io.BytesIO()
        self.stdout = [(
            '{"event":"init","conversation_id":"cid-1",'
            f'"init":{{"model":"{model}"}}}}\n').encode()]
        self.stderr: list[bytes] = []

    def poll(self):
        return None


def codex_app_server_flags() -> None:
    seen: list[dict[str, object]] = []

    def fake_popen(*args, **kwargs):
        seen.append(kwargs)
        return _CodexProc()

    with patch.object(codexrun.threading, "Thread", _NoopThread), \
            patch.object(codexrun.subprocess, "Popen", side_effect=fake_popen):
        client = codexrun.AppServerClient(["codex"], cwd="W:/scratch")
    eq(client.proc.stdin is not None, True,
       "the constructor must reach and retain the recorded process")
    eq(len(seen), 1)
    eq(seen[0].get("creationflags"), WANT)


def antigravity_turn_flags() -> None:
    model = "gemini-test"
    seen: list[dict[str, object]] = []

    def fake_popen(*args, **kwargs):
        seen.append(kwargs)
        return _AntigravityProc(model)

    turn = antigravityrun.AntigravityTurn(
        ["agy"], cwd="W:/scratch", model=model, effort=None,
        conversation_id=None)
    with patch.object(antigravityrun.providers, "antigravity_env",
                      return_value={}), \
            patch.object(antigravityrun.threading, "Thread", _ImmediateThread), \
            patch.object(antigravityrun.subprocess, "Popen",
                         side_effect=fake_popen):
        eq(turn.start("hello"), "cid-1")
    eq(len(seen), 1)
    eq(seen[0].get("creationflags"), WANT)


def main() -> int:
    print("provider turn runners never paint a window:")
    check("Codex app-server", codex_app_server_flags)
    check("Antigravity turn", antigravity_turn_flags)
    print(f"\n{PASS} ok, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
