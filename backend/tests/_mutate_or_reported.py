"""Mutation round for test_openrouter_reported.py — does the suite actually
guard the reported-metadata capture and the C-2 lookup-key record, or would it
pass with the code under test removed?

    python backend/tests/_mutate_or_reported.py

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
R.SUITE = R.ROOT / "backend" / "tests" / "test_openrouter_reported.py"

# (name, file, find, replace, must-kill-this-check-or-None-for-survive)
R.MUTANTS[:] = [
    ("NO-OP CONTROL: reword a comment beside the allowlist",
     SUP,
     "_REPORTED_FIELDS = ((\"model\", \"msg\", 80), (\"provider\", \"msg\", 40),",
     "_REPORTED_FIELDS = ((\"model\", \"msg\", 80), (\"provider\", \"msg\", 40),  # noqa",
     None),

    ("the provider is not collected (only the model, which is an ECHO of the request)",
     SUP,
     "_REPORTED_FIELDS = ((\"model\", \"msg\", 80), (\"provider\", \"msg\", 40),\n"
     "                    (\"id\", \"msg\", 64), (\"request_id\", \"ev\", 64))",
     "_REPORTED_FIELDS = ((\"model\", \"msg\", 80),\n"
     "                    (\"id\", \"msg\", 64), (\"request_id\", \"ev\", 64))",
     "collect · exactly the four allowlisted scalars, nothing else off the message"),

    ("the fields are unbounded (one turn can grow the document)",
     SUP,
     "            rec[key] = val.strip()[:limit]",
     "            rec[key] = val.strip()",
     "collect · every field is bounded, so one turn cannot grow the document"),

    ("the cap stops counting instead of stopping storing (truncated never set)",
     SUP,
     "    if len(rows) < _REPORTED_CAP:\n        rows.append(rec)\n    else:\n        acc[\"truncated\"] = True",
     "    if len(rows) < _REPORTED_CAP:\n        rows.append(rec)",
     "collect · past the cap it stops storing, says truncated, and still counts every request"),

    ("the summary keeps THE LAST value instead of every distinct one",
     SUP,
     "    models = sorted({r[\"model\"] for r in rows if r.get(\"model\")})\n"
     "    providers = sorted({r[\"provider\"] for r in rows if r.get(\"provider\")})",
     "    models = [r[\"model\"] for r in rows if r.get(\"model\")][-1:]\n"
     "    providers = [r[\"provider\"] for r in rows if r.get(\"provider\")][-1:]",
     "summary · a turn served by more than one reported model OR provider says mixed"),

    ("a SUBAGENT's message counts as this agent's upstream",
     SUP,
     "                        elif not sub:\n"
     "                            # a REAL top-level assistant message: any API",
     "                        elif True:\n"
     "                            # a REAL top-level assistant message: any API",
     "turn · a subagent's message is not this agent's upstream"),

    # the exclusion of engine-authored records is STRUCTURAL — they are
    # handled by the branch above the collection site and never reach it. The
    # mutation that expresses "the exclusion is gone" is therefore the one
    # that stops recognising them as engine-authored at all.
    ("an ENGINE-AUTHORED error record is treated as an ordinary message",
     SUP,
     "                        if _is_engine_error_event(ev, _msg):",
     "                        if False and _is_engine_error_event(ev, _msg):",
     "turn · an engine-authored error record is NOT an upstream and is never reported"),

    ("the capture is not gated on the OpenRouter lane (every lane's ring changes)",
     SUP,
     "                            if _report_lane:\n"
     "                                _note_reported(reported_acc, ev, _msg)",
     "                            if True:\n"
     "                                _note_reported(reported_acc, ev, _msg)",
     "scope · a haiku turn books its ring entry with neither block"),

    ("the CLI's own modelUsage keys go into the document at full length",
     SUP,
     "            _mu_probe[\"keys\"] = [str(k)[:80] for k in sorted(_mu)][:4]",
     "            _mu_probe[\"keys\"] = [str(k) for k in sorted(_mu)][:4]",
     "C-2 · the CLI's own keys are bounded by count AND by length"),

    ("the modelUsage lookup is always recorded as a HIT (audit C-2's whole point)",
     SUP,
     "        _mu_probe = {\"asked\": _model_id, \"matched\": _model_id in _mu}",
     "        _mu_probe = {\"asked\": _model_id, \"matched\": True}",
     "C-2 · the CLI keying modelUsage differently is recorded as a MISS, with its own keys"),
]

if __name__ == "__main__":
    R.main()
