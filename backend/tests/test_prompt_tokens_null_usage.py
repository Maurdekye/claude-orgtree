"""A present-but-NULL usage counter must not kill the turn (live bug 2026-09-03).

    python backend/tests/test_prompt_tokens_null_usage.py

THE BUG, as it actually happened. The user hired `grok-gallery` on the
OpenRouter tier `or-x-ai-grok-4-6` and every attempt to drive it died with

    TypeError: unsupported operand type(s) for +: 'int' and 'NoneType'

rendered twice above the composer, so the agent could not be messaged at all.
The failing expression was `supervisor._run_one_turn`'s per-message occupancy
sum, written as three `u.get(key, 0)` adds. **A `default` fires only when the
key is ABSENT.** A key that is PRESENT and null returns `None`, and the add
raises. Anthropic's own endpoint omits a counter it has nothing to say about,
which is why five weeks of Claude, Codex and Antigravity turns never saw it;
OpenRouter's Anthropic-compatible shim emits the whole usage shape and NULLS
the fields the served model has no accounting for, and grok-4.6 has no prompt
cache. Of the two agents ever driven on an `or-` tier, both died this way;
they are the only two unexpected TypeErrors in the entire backend log.

WHAT THIS SUITE IS CAREFUL ABOUT. §1 does not merely watch the fixed helper
succeed — an absent check and a check that cannot fail are the same thing. It
re-creates the ORIGINAL expression and proves it still raises on the same
payload, so the test documents the defect rather than the patch. §3 guards the
call site, because a correct helper nobody calls fixes nothing, and the crash
site is buried inside a function no unit test can reach.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from typing import Any, Callable

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

os.environ["ORGTREE_DATA"] = tempfile.mkdtemp(prefix="orgtree-nullusage-")

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

from orgtree import supervisor as S                       # noqa: E402

PASS = FAIL = 0


def check(label: str, fn: Callable[[], None]) -> None:
    global PASS, FAIL
    try:
        fn()
        PASS += 1
        print(f"  ok {PASS:2d}  {label}")
    except Exception as e:                                # noqa: BLE001
        FAIL += 1
        print(f"  FAIL    {label}: {e}")
        import traceback
        traceback.print_exc()


def eq(got: Any, want: Any, what: str = "") -> None:
    if got != want:
        raise AssertionError(f"{what}: got {got!r}, wanted {want!r}")


#: the shape OpenRouter's Anthropic-compatible endpoint returns for a model
#: with no prompt cache — the counters are DECLARED and null, not omitted
OPENROUTER_NO_CACHE: dict[str, Any] = {
    "input_tokens": 4211,
    "cache_read_input_tokens": None,
    "cache_creation_input_tokens": None,
    "output_tokens": 96,
}

#: what Anthropic's own endpoint sends — the same absent counters, omitted
ANTHROPIC_NO_CACHE: dict[str, Any] = {"input_tokens": 4211, "output_tokens": 96}


def _old_expression(u: dict[str, Any]) -> int:
    """The pre-fix line, verbatim. Kept so §1's anti-vacuity leg is a real
    reproduction of the defect and not a description of one."""
    return (u.get("input_tokens", 0)
            + u.get("cache_read_input_tokens", 0)
            + u.get("cache_creation_input_tokens", 0))


print("§1 the payload that killed the turn — and proof it really did")

def _the_old_line_still_raises() -> None:
    try:
        _old_expression(dict(OPENROUTER_NO_CACHE))
    except TypeError as e:
        # the user saw this exact string on screen, twice
        assert "unsupported operand type(s) for +" in str(e), str(e)
        assert "NoneType" in str(e), str(e)
        return
    raise AssertionError(
        "the original expression did NOT raise on a null cache counter — "
        "this suite's premise is gone, so re-derive the bug before trusting "
        "anything below it")

check("ANTI-VACUITY: the original `u.get(k, 0)` sum raises on the real "
      "OpenRouter payload", _the_old_line_still_raises)

check("…and the fixed helper answers instead of raising",
      lambda: eq(S._prompt_tokens(dict(OPENROUTER_NO_CACHE)), 4211,
                 "null cache counters contribute nothing"))
check("…the same number Anthropic's omitted-counter shape produces, because "
      "'declined to report' and 'not sent' mean the same thing here",
      lambda: eq(S._prompt_tokens(dict(ANTHROPIC_NO_CACHE)),
                 S._prompt_tokens(dict(OPENROUTER_NO_CACHE)), "shapes agree"))

print("§2 the ordinary case is untouched, and 'nothing measured' stays 0")

check("a full Claude usage block sums input + cache reads + cache writes",
      lambda: eq(S._prompt_tokens({"input_tokens": 1209,
                                   "cache_read_input_tokens": 168704,
                                   "cache_creation_input_tokens": 2048,
                                   "output_tokens": 1056}),
                 1209 + 168704 + 2048, "full block"))
# the caller's guard is `if t and not sub:`, so 0 must mean "do not touch the
# recorded occupancy" — never "the context is empty". This pins the 0, not the
# interpretation, which lives in the caller (§3 pins that the caller is the
# one asking).
check("every counter null ⇒ 0, so the caller's `if t` leaves occupancy alone",
      lambda: eq(S._prompt_tokens({"input_tokens": None,
                                   "cache_read_input_tokens": None,
                                   "cache_creation_input_tokens": None}), 0,
                 "all null"))
check("no usage at all ⇒ 0, by the same route",
      lambda: eq((S._prompt_tokens({}), S._prompt_tokens(None),
                  S._prompt_tokens("not a mapping"), S._prompt_tokens([])),
                 (0, 0, 0, 0), "empty/None/garbage"))
# `_finite`'s own warning: json.loads mints Infinity and NaN by default, and
# int(inf) raises OverflowError, which is in nobody's except tuple by habit.
check("a non-finite counter is dropped rather than raising OverflowError",
      lambda: eq(S._prompt_tokens({"input_tokens": float("inf"),
                                   "cache_read_input_tokens": float("nan"),
                                   "cache_creation_input_tokens": 512}), 512,
                 "inf/nan dropped"))
check("a bool is not a token count (True is an int in Python, and 1 here "
      "would be a fabricated token)",
      lambda: eq(S._prompt_tokens({"input_tokens": True,
                                   "cache_read_input_tokens": 7}), 7, "bool"))
check("a float counter is taken, truncated to a whole token",
      lambda: eq(S._prompt_tokens({"input_tokens": 10.9}), 10, "float"))

print("§3 the crash site actually calls it — a helper nobody calls fixes nothing")

_SRC = open(os.path.join(BACKEND, "orgtree", "supervisor.py"),
            encoding="utf-8").read()


def _no_unguarded_sum_survives() -> None:
    # the exact defective idiom, in any spacing: `.get("<counter>", 0)` used
    # without an `or 0` fallback. `_prompt_tokens` is now the only place these
    # three counters are read for a sum, so ANY match is a regression.
    bad = [m.group(0) for m in re.finditer(
        r'\.get\(\s*"(?:input_tokens|cache_read_input_tokens'
        r'|cache_creation_input_tokens)"\s*,\s*0\s*\)(?!\s*or\b)', _SRC)]
    if bad:
        raise AssertionError(
            f"{len(bad)} prompt-counter read(s) still use `.get(k, 0)` as if "
            f"it guarded against null: {bad}. It does not — the default only "
            f"fires when the key is ABSENT. Route it through _prompt_tokens.")


check("no `.get(<counter>, 0)` sum survives anywhere in supervisor.py",
      _no_unguarded_sum_survives)

check("the stream loop's occupancy line calls the helper",
      lambda: eq(bool(re.search(r"\n\s*t = _prompt_tokens\(u\)\s*\n\s*if t and "
                                r"not sub:", _SRC)), True,
                 "occupancy call site"))
check("…and so does the transcript occupancy filler, so one rule serves both",
      lambda: eq(_SRC.count("_prompt_tokens("), 3,
                 "one definition + two call sites"))

if FAIL:
    print(f"\n{FAIL} FAILED, {PASS} PASSED")
    raise SystemExit(1)
print(f"\nALL {PASS} CHECKS PASS")
