"""The D-205 fallback-key probe must never let a spawned CLI child paint a
console window on Windows -- it runs on a bare hourly clock (not in response
to a turn), so it is the one spawn in this file most likely to eventually run
under a parent with no console to inherit at all.

Run: ``python backend/tests/test_fallback_probe_no_window.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree import fallback_probe  # noqa: E402

PASS = 0
FAIL: list[tuple[str, str]] = []


def check(label: str, fn) -> None:
    global PASS
    try:
        fn()
    except Exception as e:  # noqa: BLE001 -- report all checks in one run
        FAIL.append((label, str(e)))
        print(f"  FAIL  {label}: {e}")
    else:
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")


def eq(got, want, detail="") -> None:
    if got != want:
        raise AssertionError(f"got {got!r}, want {want!r} {detail}")


def probe_sets_create_no_window_on_windows() -> None:
    real_run = fallback_probe.subprocess.run
    seen: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        seen["creationflags"] = kwargs.get("creationflags")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    try:
        fallback_probe.subprocess.run = fake_run
        fallback_probe.probe("unit-token", ["resolved-cli"])
    finally:
        fallback_probe.subprocess.run = real_run

    want = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]
    check("probe passes CREATE_NO_WINDOW on Windows (0 elsewhere)", lambda: eq(
        seen["creationflags"], want))


def main() -> int:
    print("§1 fallback probe never paints a window")
    probe_sets_create_no_window_on_windows()
    print(f"\n{PASS} ok, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
