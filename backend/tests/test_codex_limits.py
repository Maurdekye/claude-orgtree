"""Codex usage limits: real app-server wire, normalization, cache and glow.

    python backend/tests/test_codex_limits.py

The fake server returns a planted 12/81/93 board.  Assertions require all
three values and the named bucket, so an empty parser cannot pass as a clean
account.  The rolling-update check then replaces 12 with 91; it proves the
cache path reads the notification rather than merely executing it.
"""

import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

root = tempfile.mkdtemp(prefix="orgtree-codex-limits-")
home = os.path.join(root, "codex-home")
os.makedirs(home)
# A throwaway ORGTREE_DATA does not isolate the machine's mail hub.  This
# suite creates no org today, but every private data root in this directory
# carries the same dead-hub invariant so a future fixture cannot register
# against the operator's real roster merely by importing the net daemon.
with open(os.path.join(root, "defaults.json"), "w", encoding="utf-8") as f:
    json.dump({"net_hub_address": "http://127.0.0.1:9"}, f)
with open(os.path.join(home, "auth.json"), "w", encoding="utf-8") as f:
    json.dump({"tokens": {}}, f)
os.environ["ORGTREE_DATA"] = root
os.environ["CODEX_HOME"] = home
os.environ["ORGTREE_CODEX"] = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fakecodex.py")

from orgtree import codex_limits  # noqa: E402


def main():
    codex_limits.invalidate()
    usage = codex_limits.fetch(force=True)
    assert usage["available"], usage
    assert usage["provider"] == "Codex", usage
    assert usage["plan"] == "Pro Lite", usage
    limits = usage["limits"]
    assert [x["percent"] for x in limits] == [12.0, 81.0, 93.0], limits
    assert [x["label"] for x in limits] == [
        "7 days", "GPT-Spark · 5 hours", "GPT-Spark · 7 days"], limits
    assert [x["severity"] for x in limits] == [
        "normal", "warning", "critical"], limits
    assert len([x for x in limits if x["group"] == "codex"]) == 1, limits
    print("  ok  1  full app-server snapshot becomes three distinct bars")

    cached = codex_limits.fetch()
    assert [x["percent"] for x in cached["limits"]] == [12.0, 81.0, 93.0]
    print("  ok  2  modal polling reuses the fresh cache")

    codex_limits.observe({
        "limitId": "codex", "primary": {"usedPercent": 91},
        # Nullable metadata in a sparse update must not erase the snapshot.
        "limitName": None, "planType": None,
    })
    peek = codex_limits.peek()
    assert peek["available"], peek
    assert peek["provider"] == "Codex", peek
    got = peek["limits"]
    assert got[0]["percent"] == 91.0, got
    assert got[0]["label"] == "7 days", got
    assert got[0]["severity"] == "critical", got
    assert [x["percent"] for x in got[1:]] == [81.0, 93.0], got
    print("  ok  3  sparse turn update changes one value without erasing windows")

    codex_limits._cache["at"] -= codex_limits.MAX_EVIDENCE_AGE + 1
    stale = codex_limits.peek()
    assert not stale["available"], stale
    print("  ok  4  stale Codex evidence cannot keep the warning glow lit")
    print("\nPASS — Codex usage limits")


if __name__ == "__main__":
    main()
