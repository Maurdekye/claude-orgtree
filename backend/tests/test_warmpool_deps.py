"""D-201 dependency guard — psutil must exist wherever the backend runs.

Run: python tests/test_warmpool_deps.py

warmpool._keeper_pass imports psutil lazily and tolerates ImportError BY
DESIGN (the pool must keep running without it), which means a venv missing
psutil fails nothing — it journals rss_total_mb=0.0 / free_ram_mb=0.0 into
every warm.jsonl pool row: a blind instrument shaped like a working one.
Not hypothetical: the production venv lacked psutil on activation day
(2026-08-30) and journaled zeros for ~3.5 hours while every dev box
happened to have it. This file is the loud version of that absence, per
the requirements.txt rule that a direct use deserves a direct declaration.

Fault-plant (recorded in D-201 breadcrumbs): run under an interpreter
without psutil (fresh venv) → check A dies and the run exits non-zero.
Green requires the dependency, not luck.
"""
import sys
import traceback

PASS = 0
FAIL = 0


def check(label, fn):
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok  {label}")
    except Exception:                                        # noqa: BLE001
        FAIL += 1
        print(f"FAIL  {label}")
        traceback.print_exc()


def a_psutil_importable():
    import psutil                                            # noqa: PLC0415
    assert psutil.__version__, "psutil imported but reports no version"


def b_memory_witnesses_nonzero():
    # The zero-assert corollary: an instrument whose absent-arm journals 0.0
    # needs a witness that a measured value is non-zero — otherwise "0.0"
    # rows and "working" rows are indistinguishable by shape.
    import psutil                                            # noqa: PLC0415
    assert psutil.virtual_memory().available > 0
    assert psutil.Process().memory_info().rss > 0


check("A · psutil is importable in this environment", a_psutil_importable)
check("B · rss / free-RAM witnesses are non-zero when actually measured",
      b_memory_witnesses_nonzero)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
