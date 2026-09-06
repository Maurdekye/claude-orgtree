"""The checked-in generated artifacts (frontend/src/generated/events.ts,
events.schema.json, frontend/tests/fixtures/events/*.json) must equal a fresh generation
from backend/orgtree/events_table.py — a hand edit or a stale regeneration fails here.

Positive control: the check is run once against the real tree (must be fresh) and once
against a copy with one fixture byte changed (must report stale).

    python backend/tests/test_events_generated.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="orgtree-evgen-")
os.environ["ORGTREE_DATA"] = os.path.join(_TMP, "data")
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GEN = os.path.join(ROOT, "tools", "gen_events.py")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASSED = 0
FAILED: list[str] = []


def check(label, fn) -> None:
    global PASSED
    try:
        fn()
    except Exception as e:                                   # noqa: BLE001
        FAILED.append(f"{label}\n      {type(e).__name__}: {e}")
        print(f"  x {label}")
    else:
        PASSED += 1
        print(f"  ok {label}")


def _run_check() -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, GEN, "--check"], capture_output=True, text=True,
                          cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def _fresh():
    r = _run_check()
    assert r.returncode == 0, r.stdout + r.stderr


check("generated · checked-in events.ts / schema / fixtures equal a fresh generation", _fresh)


def _stale_detected():
    fx = os.path.join(ROOT, "frontend", "tests", "fixtures", "events", "status.report.json")
    orig = open(fx, "rb").read()
    try:
        with open(fx, "wb") as fh:
            fh.write(orig.replace(b'"family": "status"', b'"family": "STALE"'))
        r = _run_check()
        assert r.returncode == 1 and "status.report.json" in r.stdout, r.stdout
    finally:
        with open(fx, "wb") as fh:
            fh.write(orig)
    assert _run_check().returncode == 0


check("generated · positive control: one changed byte in a fixture is reported stale",
      _stale_detected)


def _crlf():
    for rel in ("frontend/src/generated/events.ts", "frontend/src/generated/events.schema.json",
                "frontend/tests/fixtures/events/status.report.json"):
        b = open(os.path.join(ROOT, rel), "rb").read()
        assert b.count(b"\r\n") == b.count(b"\n"), f"{rel}: not pure CRLF"


check("generated · files are CRLF by byte count (.gitattributes)", _crlf)

print(f"\n{PASSED} checks passed, {len(FAILED)} failed")
for f in FAILED:
    print("\nFAIL:", f)
sys.exit(1 if FAILED else 0)
