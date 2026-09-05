"""Replay a redacted failure fixture OFFLINE (docs/failure-fixtures.md).

    python tools/replay_failure.py <fixture.json> [more.json …] [--assert]

Re-runs the pure classifiers (backend/orgtree/failclass.py — verbatim copies
of the supervisor's predicates, kept identical by the suite) on the
fixture's recorded tags and observed facts, and prints one JSON object per
fixture: the RECOMPUTED predicate verdict beside the RECORDED one and the
`drift` between them. With --assert the exit code is 1 when any fixture
drifts. It is a drift detector for classifier edits, not a statement of
which branch the supervisor would take.

PURE BY CONSTRUCTION: imports `orgtree.failfix` and `orgtree.failclass`
only — no `store`, no `supervisor`, no data root, no CLI, no provider. The
suite runs this tool under an import hook that would fail the run on any
attempt to import storage, provider or process modules.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))

from orgtree import codex_decide, failclass, failfix  # noqa: E402

PREDICATES: failfix.Predicates = {
    "limit": failclass._looks_like_usage_limit,
    "net": failclass._looks_like_connection_failure,
    "filtered": failclass._looks_like_filtered,
    "died_in_flight": failclass._died_in_flight,
    "typed": failclass._typed_api_status,
    # the codex lane is re-decided by the PRODUCTION core itself
    # (codex_route.classify_failure == decide(failure_evidence(...)))
    "codex_decide": codex_decide.decide,
    "codex_nothing_ran": codex_decide.nothing_ran,
}


def main(argv: list[str]) -> int:
    strict = "--assert" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 2
    rc = 0
    for p in paths:
        out = failfix.replay(failfix.load(p), PREDICATES)
        out["fixture"] = os.path.basename(p)
        print(json.dumps(out, ensure_ascii=False))
        if strict and out["drift"]:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
