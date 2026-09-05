"""Mutation round for test_openrouter_typed_status.py — does the suite actually
guard the OpenRouter typed-status classification, or would it pass with the
code under test removed?

    python backend/tests/_mutate_or_typed.py

Same runner as `_mutate_harvest.py` (imported, not copied): each mutant is
applied to supervisor.py, the suite is run, the named check must go red, and
git restores the file. The worktree must be clean and `ORGTREE_DATA` is set
by the suite itself (a throwaway root).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _mutate_harvest as R                                        # noqa: E402

SUP = R.SUP
R.SUITE = R.ROOT / "backend" / "tests" / "test_openrouter_typed_status.py"

# (name, file, find, replace, must-kill-this-check-or-None-for-survive)
R.MUTANTS[:] = [
    ("NO-OP CONTROL: reword a comment beside the strict reader",
     SUP,
     "    if isinstance(value, bool) or not isinstance(value, int):\n        return None\n    return value if 400 <= value <= 599 else None",
     "    if isinstance(value, bool) or not isinstance(value, int):\n        return None\n    return value if 400 <= value <= 599 else None  # noqa",
     None),

    ("the strict reader accepts digit STRINGS (a coercion, not a type)",
     SUP,
     "    if isinstance(value, bool) or not isinstance(value, int):\n        return None\n    return value if 400 <= value <= 599 else None",
     "    if isinstance(value, str) and value.strip().isdigit():\n        value = int(value.strip())\n    if isinstance(value, bool) or not isinstance(value, int):\n        return None\n    return value if 400 <= value <= 599 else None",
     "reader · bool, digit string, float, out-of-range and containers are NOT statuses"),

    ("the OpenRouter lane asks the account resolver again (the measured re-drive)",
     SUP,
     "                    elif (_served in (\"api-key\", \"key:unattributed\",\n                                      OPENROUTER_IDENTITY) or not _tier):",
     "                    elif (_served in (\"api-key\", \"key:unattributed\")\n                          or not _tier):",
     "redrive · a typed OpenRouter 429 FREEZES with the login present"),

    ("a typed status is WIDENED by prose (the v2 contract's own bug)",
     SUP,
     "                _limit_class = _or_typed in (401, 402, 429)\n                _net_class = _or_typed >= 500",
     "                _limit_class = _or_typed in (401, 402, 429) \\\n                    or _looks_like_usage_limit(err_blob)\n                _net_class = _or_typed >= 500",
     "exclusive · typed 403 with limit wording stays TERMINAL, status in the door"),

    ("a retried-past synthetic error is never cleared",
     SUP,
     "                        elif not sub:\n                            # a REAL top-level assistant message: any API",
     "                        elif False:\n                            # a REAL top-level assistant message: any API",
     "coherence · synthetic 402 → real output → EMPTY result is a COMPLETED turn (the clearing)"),

    ("an UNTYPED latest engine error leaves the earlier status standing",
     SUP,
     "    _clear_synthetic_status(into)\n    status = _strict_http_status(ev.get(\"apiErrorStatus\"))",
     "    status = _strict_http_status(ev.get(\"apiErrorStatus\"))",
     "coherence · typed 401 → UNTYPED engine error is terminal on the later error, not an auth park"),

    ("the synthetic slot is FIRST-wins (a stale 401 parks a 402 as auth)",
     SUP,
     "    into[\"status\"] = status\n    into[\"status_text\"] = text.strip()[:300]",
     "    if \"status\" in into:\n        return\n    into[\"status\"] = status\n    into[\"status_text\"] = text.strip()[:300]",
     "coherence · consecutive 401 → 402 is the 402 (latest), not a stale auth park"),

    ("the clean-empty-result adoption is dropped (the 402 books a completed turn)",
     SUP,
     "                    and stream_api_err.get(\"status\") is not None\n                    and res.get(\"is_error\") is not True",
     "                    and stream_api_err.get(\"status\") is None\n                    and res.get(\"is_error\") is not True",
     "balance · the captured 402 ending on a clean empty result is a FAILURE"),

    ("a served turn no longer resets the balance run",
     SUP,
     "            n.pop(\"balance_probe_run\", None)",
     "            pass",
     "balance · ▶ after a top-up: the served turn clears the run (reset only on success)"),

    ("the timer keeps waking a CAPPED balance freeze on the org floor",
     SUP,
     "        if fz.get(\"cause\") == \"balance\" and fz.get(\"until_ts\") is None:\n            # an OpenRouter balance refusal that ran up to its cap",
     "        if False:\n            # an OpenRouter balance refusal that ran up to its cap",
     "balance · the cap parks it: no horizon, timer off, ▶ still on"),

    ("the balance cap is never announced",
     SUP,
     "                                    _parked = \"balance\"",
     "                                    pass",
     "balance · the cap is announced ONCE, and says a 402 is not proof of exhausted funds"),

    ("5xx falls out of the bounded retry into the terminal door",
     SUP,
     "                _net_class = _or_typed >= 500",
     "                _net_class = False",
     "exclusive · typed 503 with limit wording is a BOUNDED RETRY, not a limit"),
]

if __name__ == "__main__":
    R.main()
