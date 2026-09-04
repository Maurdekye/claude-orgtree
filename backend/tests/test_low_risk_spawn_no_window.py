"""Low-risk backend maintenance subprocesses must stay windowless on Windows.

These checks invoke each enclosing function with a recording ``subprocess.run``
stub.  That is deliberate: several nearby Popen sites receive creation flags
through ``**kwargs``, so source proximity alone cannot distinguish a real gap
from an already-protected call.

Run: ``python backend/tests/test_low_risk_spawn_no_window.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree import crashreports, disk, sandbox  # noqa: E402

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


def _completed(stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def crash_resolver_flags() -> None:
    seen: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        seen.append(kwargs)
        return _completed('{"ok": true, "stack": "resolved"}')

    with patch.object(crashreports.os.path, "isfile", return_value=True), \
            patch.object(crashreports.subprocess, "run", side_effect=fake_run):
        eq(crashreports.resolve_stack("raw"), "resolved")
    eq(len(seen), 1)
    eq(seen[0].get("creationflags"), WANT)


def disk_runner_flags() -> None:
    seen: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        seen.append(kwargs)
        return _completed()

    with patch.object(disk.subprocess, "run", side_effect=fake_run):
        disk._run(["wsl", "--version"])
    eq(len(seen), 1)
    eq(seen[0].get("creationflags"), WANT)


def sandbox_docker_flags() -> None:
    seen: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        seen.append(kwargs)
        return _completed()

    with patch.object(sandbox.subprocess, "run", side_effect=fake_run):
        sandbox._docker("version")
    eq(len(seen), 1)
    eq(seen[0].get("creationflags"), WANT)


def main() -> int:
    print("low-risk maintenance subprocesses never paint a window:")
    check("crashreports.resolve_stack node spawn", crash_resolver_flags)
    check("disk._run central WSL/docker runner", disk_runner_flags)
    check("sandbox._docker central Docker runner", sandbox_docker_flags)
    print(f"\n{PASS} ok, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
