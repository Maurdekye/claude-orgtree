"""D-201/D-203: the runtime toggle is one fresh, atomic warm.flag state.

Run directly::

    python backend/tests/test_warm_toggle.py

The planted race is the point: cache exclude-list A, atomically replace the
file with newer list B, then toggle. A writer that preserves its cached view
silently resurrects A and destroys B even though both individual writes are
atomic. The toggle must re-read the durable file before its read/modify/write.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile


RIG = tempfile.mkdtemp(prefix="d201-warm-toggle-")
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["ORGTREE_DATA"] = RIG
with open(os.path.join(RIG, "defaults.json"), "w", encoding="utf-8") as f:
    f.write('{"net_hub_address": "http://127.0.0.1:9"}')
sys.path.insert(0, BACKEND)

from orgtree import warmpool as W  # noqa: E402


FLAG = os.path.join(RIG, "warm.flag")
OLD = "org/old-exclude"
NEW = "org/new-exclude"


def atomic_external_write(doc: dict) -> None:
    tmp = FLAG + ".external"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, FLAG)


try:
    W.set_flag(json.dumps({"enabled": True, "exclude": [OLD]}))
    W._FLAG_CACHE["at"] = 0.0
    assert W.node_excluded("org", "old-exclude")

    # New durable state arrives inside the cache TTL. This is the input that
    # makes the pre-fix implementation fail: set_enabled() consults cached A
    # and writes it back over newer B.
    atomic_external_write({"enabled": True, "exclude": [NEW]})
    pokes: list[bool] = []
    real_poke = W.poke
    W.poke = lambda: pokes.append(True)
    try:
        W.set_enabled(False)
    finally:
        W.poke = real_poke

    # No manual cache reset here: the toggle contract says its value is
    # authoritative for the very next decision and needs no restart.
    assert W.warm_decision() == (False, False)
    assert W.node_excluded("org", "new-exclude"), (
        "toggle overwrote the newer durable exclude list with its cached view")
    assert not W.node_excluded("org", "old-exclude"), (
        "stale exclude list was resurrected by the toggle")
    assert pokes, "toggle did not wake the keeper for prompt runtime effect"
    print("PASS: toggle preserves the latest durable excludes and is immediate")
finally:
    shutil.rmtree(RIG, ignore_errors=True)
