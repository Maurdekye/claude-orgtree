"""`supervisor.cli_version()`'s subprocess fallback and `supervisor.build_info()`'s
two `git rev-parse` probes must never let a spawned child paint a console
window on Windows -- same class of gap as `test_provider_probe_no_window.py`,
found live: a passive window watcher caught a `WindowsTerminal.exe -Embedding`
window at the exact moment `/api/providers` (which calls `cli_version()`) took
2.77s instead of its usual single-digit milliseconds -- the signature of an
unprotected spawn racing Windows' default-terminal delegation.

Run: ``python backend/tests/test_supervisor_probe_no_window.py``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orgtree import supervisor  # noqa: E402

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


def cli_version_sets_create_no_window() -> None:
    real_run = supervisor.subprocess.run
    real_cache = supervisor._cli_version_cache
    real_cli_js = supervisor.CLAUDE_CLI_JS
    seen: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(returncode=0, stdout="9.9.9", stderr="")

    try:
        supervisor.subprocess.run = fake_run
        supervisor._cli_version_cache = None  # force past both caches
        # a package.json six directories above a bin/cli.js that does not
        # exist can never be found -- forces the walk-up to fall all the way
        # through to the subprocess probe under test
        supervisor.CLAUDE_CLI_JS = os.path.join(
            "Z:", "does-not-exist", "a", "b", "c", "d", "e", "bin", "cli.js")
        supervisor.cli_version()
    finally:
        supervisor.subprocess.run = real_run
        supervisor._cli_version_cache = real_cache
        supervisor.CLAUDE_CLI_JS = real_cli_js

    check("cli_version() reached the subprocess fallback",
          lambda: eq(len(seen), 1, "the package.json lookup must have hit "
                     "first, or this never ran the path under test"))
    check("cli_version passes CREATE_NO_WINDOW on Windows (0 elsewhere)",
          lambda: eq(seen[0].get("creationflags"), WANT))


def build_info_sets_create_no_window() -> None:
    real_run = supervisor.subprocess.run
    real_cache = supervisor._build_info_cache
    seen: list[dict[str, object]] = []

    def fake_run(*args, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(returncode=0, stdout="abc1234", stderr="")

    try:
        supervisor.subprocess.run = fake_run
        supervisor._build_info_cache = None  # this cache is a one-shot per process
        supervisor.build_info()
    finally:
        supervisor.subprocess.run = real_run
        supervisor._build_info_cache = real_cache

    check("build_info() called git rev-parse twice (commit + branch)",
          lambda: eq(len(seen), 2))
    for i, kwargs in enumerate(seen):
        check(f"build_info git call #{i + 1} passes CREATE_NO_WINDOW on "
              "Windows (0 elsewhere)",
              lambda kwargs=kwargs: eq(kwargs.get("creationflags"), WANT))


def main() -> int:
    print("§1 supervisor's version/build-info probes never paint a window")
    cli_version_sets_create_no_window()
    build_info_sets_create_no_window()
    print(f"\n{PASS} ok, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
