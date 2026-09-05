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

    # ▶ THE PRIMARY PATH. On the pinned CLI the ONLY typed evidence is the
    # result event's own number, so this is the mutation that says whether the
    # OpenRouter classification is load-bearing in production at all. It must
    # die to a check driven in the EMITTED shape, not to a compatibility one.
    ("the RESULT event's typed status is ignored (only the stream slot is read)",
     SUP,
     "        status = _typed_status_field(res)\n        if status is not None:\n            return status",
     "        pass",
     "balance · …frozen cause=balance on the probe floor, run 1, no window, no pool"),

    ("the CLI's STDOUT flag spelling is not recognised (only the camelCase one)",
     SUP,
     "            or ev.get(\"is_api_error_message\") is True\n",
     "",
     "reader · engine authorship: the model id and BOTH flag spellings, nothing else"),

    ("a retried-past synthetic error is never cleared",
     SUP,
     "                        elif not sub:\n                            # a REAL top-level assistant message: any API",
     "                        elif False:\n                            # a REAL top-level assistant message: any API",
     "compat · status-bearing error → real output → EMPTY result is COMPLETED (the clearing)"),

    ("an UNTYPED latest engine error leaves the earlier status standing",
     SUP,
     "    _clear_synthetic_status(into)\n    status = _typed_status_field(ev)",
     "    status = _typed_status_field(ev)",
     "compat · typed 401 → UNTYPED engine error is terminal on the later error, not an auth park"),

    # ⚠ THE OBVIOUS FIRST-WINS MUTATION IS DEAD CODE NOW. Guarding the two
    # assignment lines with `if "status" in into: return` can never fire —
    # the clearing above them ran first — so it mutated NOTHING and survived
    # every check, which reads as a hole in the suite and is not one. Stated
    # against the current code, first-wins means: retire the slot as shipped,
    # but put the EARLIER value back whenever the later event is also typed.
    ("the synthetic slot is FIRST-wins (a stale 401 parks a 402 as auth)",
     SUP,
     "    _clear_synthetic_status(into)\n    status = _typed_status_field(ev)",
     "    _first = (into.get(\"status\"), into.get(\"status_text\"))\n"
     "    _clear_synthetic_status(into)\n"
     "    status = _typed_status_field(ev)\n"
     "    if _first[0] is not None and status is not None:\n"
     "        into[\"status\"], into[\"status_text\"] = _first\n"
     "        return",
     "compat · consecutive 401 → 402 is the 402 (latest), not a stale auth park"),

    ("the clean-empty-result adoption is dropped (the 402 books a completed turn)",
     SUP,
     "                    and stream_api_err.get(\"status\") is not None\n                    and res.get(\"is_error\") is not True",
     "                    and stream_api_err.get(\"status\") is None\n                    and res.get(\"is_error\") is not True",
     "compat · status-bearing error + clean empty result is a FAILURE (hypothetical shape)"),

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
