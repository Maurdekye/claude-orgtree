"""OpenRouter native per-request usage.cost vs catalog estimate and _cost_complete flag.

    python backend/tests/test_openrouter_cost.py
"""

import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATA = tempfile.mkdtemp(prefix="orgtree-orr-cost-")
os.environ["ORGTREE_DATA"] = DATA
os.environ.pop("ORGTREE_WARM", None)

from orgtree import openrouter as orr, store, supervisor as S  # noqa: E402
from orgtree.ledger import USER                                 # noqa: E402

PASS = 0


def check(label, fn):
    global PASS
    fn()
    PASS += 1
    print(f"  ok {PASS:2d}  {label}")


def main():
    # Setup test org and openrouter favorite tier
    org = store.create_org("zz-or-cost-test")
    model_id = "openai/gpt-5.6-sol"
    # Favorite pricing: $5.00 prompt, $30.00 completion per million tokens
    fav = {
        "id": model_id,
        "name": "GPT-5.6 Sol",
        "tier": "or-openai-gpt-5-6-sol",
        "prompt": 5.0,
        "completion": 30.0,
        "cache_read": 0.5,
        "cache_write": 5.0,
        "seat": 5.0,
        "added_at": "2026-09-03T00:00:00Z",
    }
    doc = orr._load_state()
    doc["favorites"].append(fav)
    orr._save_state(doc)
    org.d["tiers"][fav["tier"]] = 5.0
    org.d["models"]["agent"] = model_id
    org.hire(USER, None, fav["tier"], 0, "agent")
    store.save_org(org)

    st = {"interrupted": False}

    # Case 1: Native usage.cost present in stream-json result
    res_native = {
        "status": "success",
        "total_cost_usd": 0.99,  # bogus CLI list estimate
        "duration_ms": 1200,
        "usage": {
            "input_tokens": 1000,
            "output_tokens": 500,
            "cost": 0.004215,  # native billed cost from OpenRouter
        },
        "modelUsage": {
            model_id: {"costBasis": "unknown"}
        }
    }
    S._after_turn("zz-or-cost-test", "agent", org, res_native, st, occ=None)
    o_after1 = store.load_org("zz-or-cost-test")
    n1 = o_after1.node("agent")
    turn1 = n1["turns"][-1]
    assert turn1["cost"] == 0.004215, turn1
    assert turn1["cost_complete"] is True, turn1
    assert not turn1.get("estimated"), turn1
    print("  ok  1  native usage.cost adopts authoritative billed amount with cost_complete=True")

    # Case 2: Native cost chunk missing — falls back to catalog estimate with cost_complete=False
    res_missing = {
        "status": "success",
        "total_cost_usd": 0.99,
        "duration_ms": 1500,
        "usage": {
            "input_tokens": 10000,       # 10,000 * $5.00 / 1e6 = $0.05
            "output_tokens": 1000,       # 1,000 * $30.00 / 1e6 = $0.03
            # no "cost" key
        },
        "modelUsage": {
            model_id: {"costBasis": "unknown"}
        }
    }
    # Expected estimated cost: 0.05 + 0.03 = 0.08
    S._after_turn("zz-or-cost-test", "agent", o_after1, res_missing, st, occ=None)
    o_after2 = store.load_org("zz-or-cost-test")
    n2 = o_after2.node("agent")
    turn2 = n2["turns"][-1]
    assert turn2["cost"] == 0.08, turn2
    assert turn2["cost_complete"] is False, turn2
    assert turn2["estimated"] is True, turn2
    print("  ok  2  missing cost chunk falls back to catalog estimate with cost_complete=False and estimated=True")

    print(f"\nALL 2 CHECKS PASS — OpenRouter per-request cost accuracy")


if __name__ == "__main__":
    main()
