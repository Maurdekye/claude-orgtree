"""Run due fallback-key liveness checks without exposing credentials.

The same durable hourly gate used by the backend is used here; this script is
not an escape hatch that can repeatedly bill a fallback account.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from orgtree import accounts  # noqa: E402


def main() -> int:
    results = accounts.probe_fallback_keys()
    if not results:
        print("no fallback key is due for a liveness probe")
        return 0
    for item in results:
        # Account row IDs are already the non-secret identifiers used by the
        # Accounts panel. Never add raw CLI response or token-derived detail.
        print(f"{item['id']}: {item['state']}")
    return 1 if any(item["state"] in ("dead", "unknown") for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
