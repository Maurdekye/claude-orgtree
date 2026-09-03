"""The codex/antigravity version and account probes in providers.py must
never let a spawned CLI child paint a console window on Windows -- same class
of gap test_fallback_probe_no_window.py covers, in the one file that shipped
without it: `_codex_version`, `_antigravity_version` and `_antigravity_account`
each ran `subprocess.run` with no `creationflags`, so a spawn with no console
to inherit (this backend's own hidden console) gets Windows' default terminal
delegation and paints a Windows Terminal window on the interactive desktop --
caught live via a parent-chain trace back to this backend's own pid.

Run: ``python backend/tests/test_provider_probe_no_window.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree import providers  # noqa: E402

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


WANT = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]


def _fake_run(seen: dict[str, object]):
    def fake_run(*args, **kwargs):
        seen["creationflags"] = kwargs.get("creationflags")
        return SimpleNamespace(returncode=0, stdout="1.2.3", stderr="")
    return fake_run


def codex_version_sets_create_no_window() -> None:
    real_run = providers.subprocess.run
    seen: dict[str, object] = {}
    try:
        providers.subprocess.run = _fake_run(seen)
        providers._codex_version("does-not-matter.exe")
    finally:
        providers.subprocess.run = real_run
    check("_codex_version passes CREATE_NO_WINDOW on Windows (0 elsewhere)",
          lambda: eq(seen["creationflags"], WANT))


def antigravity_version_sets_create_no_window() -> None:
    real_run = providers.subprocess.run
    seen: dict[str, object] = {}
    try:
        providers.subprocess.run = _fake_run(seen)
        providers._antigravity_version("does-not-matter.exe")
    finally:
        providers.subprocess.run = real_run
    check("_antigravity_version passes CREATE_NO_WINDOW on Windows (0 elsewhere)",
          lambda: eq(seen["creationflags"], WANT))


def antigravity_account_sets_create_no_window() -> None:
    real_run = providers.subprocess.run
    seen: dict[str, object] = {}
    try:
        providers.subprocess.run = _fake_run(seen)
        providers._antigravity_account("does-not-matter.exe")
    finally:
        providers.subprocess.run = real_run
    check("_antigravity_account passes CREATE_NO_WINDOW on Windows (0 elsewhere)",
          lambda: eq(seen["creationflags"], WANT))


def main() -> int:
    print("§1 codex/antigravity probes never paint a window")
    codex_version_sets_create_no_window()
    antigravity_version_sets_create_no_window()
    antigravity_account_sets_create_no_window()
    print(f"\n{PASS} ok, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
