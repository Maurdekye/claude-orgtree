"""`tools/run_tests.py` must refuse to run at all against a SQLite-defaulting
checkout when ORGTREE_DATA was not explicitly given -- deliberately redundant
with store.py's own ORGTREE_MIGRATE gate (see docs/test-baseline.md, "THE
RUNNER STRIPS ORGTREE_DATA"). child_env() strips ORGTREE_DATA from every
suite's environment on purpose, for isolation; on a checkout whose store.py
defaults STORE_BACKEND to sqlite, a suite that forgets to mint its own
throwaway root falls straight through to the live default and
claim_data_root() migrates whatever it is pointed at. That happened once,
2026-09-03.

This suite shells out to the real runner against a THROWAWAY COPY of it and a
fabricated backend/orgtree/store.py -- never the real one, so a bug in the
test itself cannot touch anything real. Four cases:

    §1  sqlite-defaulting store.py, ORGTREE_DATA unset  -> REFUSES  (rc=2)
    §2  sqlite-defaulting store.py, ORGTREE_DATA set     -> proceeds past the
        guard (reaches suite discovery, not the refusal text)
    §3  json-defaulting store.py (legacy / fallback), ORGTREE_DATA unset
        -> proceeds past the guard (the guard is a no-op on a checkout with
        no sqlite backend at all)
    §4  sqlite-defaulting store.py, ORGTREE_DATA unset, --list  -> --list is
        exempt (it runs nothing), so the plan still prints

    python backend/tests/test_sqlite_rig_guard.py [-v]
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import traceback

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RUNNER = os.path.join(REPO, "tools", "run_tests.py")
VERBOSE = "-v" in sys.argv

PASS = 0
FAIL: list[tuple[str, str]] = []


def check(label, fn) -> None:
    global PASS
    try:
        fn()
    except Exception:                                            # noqa: BLE001
        FAIL.append((label, traceback.format_exc()))
        print(f"  FAIL     {label}")
        return
    PASS += 1
    print(f"  ok {PASS:3d}  {label}")


SQLITE_DEFAULT_LINE = (
    'STORE_BACKEND: str = os.environ.get("ORGTREE_STORE", "sqlite")'
    '.strip().lower() or "sqlite"\n'
)
JSON_DEFAULT_LINE = (
    'STORE_BACKEND: str = os.environ.get("ORGTREE_STORE", "json")'
    '.strip().lower() or "json"\n'
)


def _fake_repo(store_line: str | None) -> str:
    """A throwaway repo shape holding only what the guard needs to read
    before it decides whether to refuse: tools/run_tests.py itself (copied
    verbatim) and a fabricated backend/orgtree/store.py. Nothing under it is
    the real checkout, so nothing a bug here does can reach real data."""
    root = tempfile.mkdtemp(prefix="orgtree-rigguard-fake-")
    os.makedirs(os.path.join(root, "tools"))
    os.makedirs(os.path.join(root, "backend", "orgtree"))
    os.makedirs(os.path.join(root, "backend", "tests"))
    shutil.copyfile(RUNNER, os.path.join(root, "tools", "run_tests.py"))
    with open(os.path.join(root, "backend", "orgtree", "store.py"),
              "w", encoding="utf-8") as fh:
        fh.write("import os\n\n")
        if store_line is not None:
            fh.write(store_line)
    return root


def _run(root: str, extra_args: list[str], drop_data: bool) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ORGTREE_")}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    if not drop_data:
        env["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-rigguard-data-")
    return subprocess.run(
        [sys.executable, os.path.join(root, "tools", "run_tests.py"), *extra_args],
        cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30)


REFUSAL = "REFUSING TO RUN: this checkout's store.py defaults STORE_BACKEND to sqlite"


def sqlite_no_data_refuses() -> None:
    root = _fake_repo(SQLITE_DEFAULT_LINE)
    try:
        r = _run(root, [], drop_data=True)
        assert r.returncode == 2, f"rc={r.returncode}, want 2\n{r.stdout}"
        assert REFUSAL in r.stdout, r.stdout
        # ⚠ the point of the guard: nothing was spawned to look for suites in
        assert "plan ·" not in r.stdout, \
            f"guard fired too late -- suite discovery already ran\n{r.stdout}"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def sqlite_with_data_proceeds() -> None:
    root = _fake_repo(SQLITE_DEFAULT_LINE)
    try:
        r = _run(root, [], drop_data=False)
        assert REFUSAL not in r.stdout, \
            f"guard fired even though ORGTREE_DATA was set\n{r.stdout}"
        assert "NOTHING TO RUN — no suites were found." in r.stdout, r.stdout
    finally:
        shutil.rmtree(root, ignore_errors=True)


def json_default_never_refuses() -> None:
    """Today's actual main: no sqlite backend at all. The guard must be a
    total no-op here -- this is the case every existing run of this runner
    hits, and it must behave exactly as it did before the guard existed."""
    root = _fake_repo(JSON_DEFAULT_LINE)
    try:
        r = _run(root, [], drop_data=True)
        assert REFUSAL not in r.stdout, \
            f"guard fired on a json-defaulting checkout\n{r.stdout}"
        assert "NOTHING TO RUN — no suites were found." in r.stdout, r.stdout
    finally:
        shutil.rmtree(root, ignore_errors=True)


def no_store_backend_line_never_refuses() -> None:
    """No STORE_BACKEND literal at all (a store.py that predates sqlite
    support, or one shaped differently than expected) must fail SAFE --
    the guard finding nothing to match is not evidence of danger."""
    root = _fake_repo(None)
    try:
        r = _run(root, [], drop_data=True)
        assert REFUSAL not in r.stdout, r.stdout
    finally:
        shutil.rmtree(root, ignore_errors=True)


def list_is_exempt() -> None:
    root = _fake_repo(SQLITE_DEFAULT_LINE)
    try:
        r = _run(root, ["--list"], drop_data=True)
        assert REFUSAL not in r.stdout, \
            f"--list should run nothing but was itself refused\n{r.stdout}"
        assert "plan · 0 to run" in r.stdout, r.stdout
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    print("§1 the sqlite rig guard fires exactly when it should, and only then")
    check("sqlite-defaulting store.py + no ORGTREE_DATA -> REFUSES before "
          "discovering any suite", sqlite_no_data_refuses)
    check("sqlite-defaulting store.py + explicit ORGTREE_DATA -> proceeds "
          "past the guard", sqlite_with_data_proceeds)
    check("json-defaulting store.py (legacy fallback) -> guard is a no-op",
          json_default_never_refuses)
    check("no STORE_BACKEND literal at all -> fails safe, guard is a no-op",
          no_store_backend_line_never_refuses)
    check("--list is exempt even on a sqlite-defaulting checkout with no "
          "ORGTREE_DATA", list_is_exempt)
    print(f"\n{PASS} ok, {len(FAIL)} failed")
    if VERBOSE:
        for label, tb in FAIL:
            print(f"\n--- {label} ---\n{tb}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
