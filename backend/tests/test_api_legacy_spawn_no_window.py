"""Legacy-volume inspect/removal subprocesses must be windowless on Windows.

Run: ``python backend/tests/test_api_legacy_spawn_no_window.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree import api, disk  # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []
WANT = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]


def check(label: str, fn) -> None:
    global PASS
    try:
        fn()
    except Exception as e:  # noqa: BLE001 -- report both paths together
        FAIL.append((label, str(e)))
        print(f"  FAIL  {label}: {e}")
    else:
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")


def eq(got, want, detail: str = "") -> None:
    if got != want:
        raise AssertionError(f"got {got!r}, want {want!r} {detail}")


def inspect_flags() -> None:
    seen: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(api.subprocess, "run", side_effect=fake_run), \
            patch.object(api.os.path, "isdir", return_value=False):
        vols, dirs = api._legacy_targets("demo")
    eq(len(vols), 6)
    eq(dirs, [])
    eq(len(seen), 6)
    eq([kwargs.get("creationflags") for kwargs in seen], [WANT] * 6)


def removal_flags() -> None:
    seen: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    request = SimpleNamespace(state=SimpleNamespace())
    with patch.object(api.subprocess, "run", side_effect=fake_run), \
            patch.object(api, "_disk_org",
                         return_value=SimpleNamespace(d={"slug": "demo"})), \
            patch.object(disk, "is_mounted", return_value=True), \
            patch.object(api, "_legacy_targets", return_value=(["vol"], [])):
        result = api.sweep_legacy("demo", request)
    eq(result["removed_volumes"], ["vol"])
    eq(len(seen), 1)
    eq(seen[0].get("creationflags"), WANT)


def main() -> int:
    print("legacy-volume subprocesses never paint a window:")
    check("docker volume inspect", inspect_flags)
    check("docker volume removal", removal_flags)
    print(f"\n{PASS} ok, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
