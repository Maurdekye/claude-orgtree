"""Mutation round for test_openrouter_failcost.py — does the suite actually
guard the failure path's source selection, or would it pass with the code
under test removed?

    python backend/tests/_mutate_or_failcost.py

Same runner as `_mutate_harvest.py` (imported, not copied): each mutant is
applied to supervisor.py, the suite is run, the named check must go red, and
git restores the file. The worktree must be clean and `ORGTREE_DATA` is set by
the suite itself (a throwaway root).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mutate_harvest as R                                        # noqa: E402

SUP = R.SUP
R.SUITE = R.ROOT / "backend" / "tests" / "test_openrouter_failcost.py"

# (name, file, find, replace, must-kill-this-check-or-None-for-survive)
R.MUTANTS[:] = [
    ("NO-OP CONTROL: reword a comment beside the ladder",
     SUP,
     "    if native is not None:\n        return native, \"provider-native-unscoped\", False, [\"scope\"]",
     "    if native is not None:  # noqa\n        return native, \"provider-native-unscoped\", False, [\"scope\"]",
     None),

    ("a provider-reported cost is ignored (the CLI's guess wins anyway)",
     SUP,
     "    if native is not None:\n        return native, \"provider-native-unscoped\", False, [\"scope\"]",
     "    if False:\n        return native, \"provider-native-unscoped\", False, [\"scope\"]",
     "ladder · a provider-reported cost outranks the CLI's figure and the catalogue"),

    ("an UNOBSERVED provider field is believed as a complete TURN total",
     SUP,
     "        return native, \"provider-native-unscoped\", False, [\"scope\"]",
     "        return native, \"provider-native\", True, []",
     "ladder · …but it is INCOMPLETE with `scope` unresolved — no invented turn contract"),

    ("consumed-but-unpriced returns quietly, leaving the lifetime total confident",
     SUP,
     "                if or_lane and _consumed_anything(usage, out_tokens):",
     "                if False:",
     "doubt · consumed but UNPRICED: the doubt is persisted, no dollar is invented, no row is written"),

    ("consumption is ASSUMED rather than observed (the flag becomes a decoration)",
     SUP,
     "    if out_tokens > 0:\n        return True",
     "    if True:\n        return True",
     "doubt · …and a turn with nothing observed marks nothing (the negative that keeps it honest)"),

    ("a native ZERO reads as 'nothing reported' (truthiness, not None)",
     SUP,
     "    if native is not None:\n        return native, \"provider-native-unscoped\", False, [\"scope\"]",
     "    if native:\n        return native, \"provider-native-unscoped\", False, [\"scope\"]",
     "ladder · a native ZERO is the amount, not a miss (free models are the ordinary case)"),

    ("the failure-path catalogue estimate is not marked partial",
     SUP,
     "        unknown = list(dict.fromkeys([*detail[\"unknown_fields\"], \"usage\"]))",
     "        unknown = list(dict.fromkeys([*detail[\"unknown_fields\"]]))",
     "ladder · with no native cost the catalogue prices the turn, INCOMPLETE, usage unresolved"),

    ("the catalogue rung is skipped, so the CLI's figure books unchallenged",
     SUP,
     "        if detail[\"amount\"] > 0.0:",
     "        if False:",
     "booked · the failed turn's entry carries the amount, the source and what is unresolved"),

    ("the failure entry carries no provenance at all (the old behaviour)",
     SUP,
     "            if source:\n                paid_entry[\"cost_source\"] = source",
     "            if False:\n                paid_entry[\"cost_source\"] = source",
     "booked · the failed turn's entry carries the amount, the source and what is unresolved"),

    ("an incomplete failure charge does not raise the node's doubt flag",
     SUP,
     "                    n[\"cost_usd_unknown\"] = True\n            _stamp_ran_as(paid_entry, slug, nid)",
     "                    pass\n            _stamp_ran_as(paid_entry, slug, nid)",
     "doubt · an incomplete failure charge raises cost_usd_unknown on the node"),

    ("the OpenRouter ladder runs on every lane",
     SUP,
     "            or_lane = openrouter.is_tier(str(n.get(\"model\") or \"\"))",
     "            or_lane = True",
     "scope · a haiku failure books the CLI figure with no stamps, exactly as before"),

    # the SUCCESS path's limit, pinned so a later change to it is deliberate
    ("one list-priced row promotes the whole turn to COMPLETE",
     SUP,
     "        elif str(_row.get(\"costBasis\") or \"\") == \"list\" and cost > 0.0:\n            res = {**res, \"_cost_complete\": False,",
     "        elif str(_row.get(\"costBasis\") or \"\") == \"list\" and cost > 0.0:\n            res = {**res, \"_cost_complete\": True,",
     "basis · a list-priced row keeps the CLI total but is NOT complete"),
]

if __name__ == "__main__":
    R.main()
