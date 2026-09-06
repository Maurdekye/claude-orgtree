"""Inspect a turn record OFFLINE (docs/turn-events.md).

    python tools/inspect_turn.py <record.json> [more.json …] [--json] [--assert]

Prints, per record: the header (lane, tier, outcome, correlation, bounds
indicators), the ORDERED timeline (`seq  t_ms  kind  fields`), and the
SUMMARY `turnlog.summarize` derives from the events alone — phase, the
disposition the events imply, first-output and boundary latencies — beside
the recorded outcome, with the `drift` between them. A `partial` (never
finalized), `truncated` or gapped (`dropped` > 0) record yields an
INSUFFICIENT summary that asserts nothing about the outcome; the retained
events' ORDER is still checked and an inversion is drift whatever the
evidence. When the record names a failure fixture
and it sits in the sibling `failfix/<org>/<node>/` directory (resolved by
`turnlog.fixture_path`, never from an arbitrary path), the fixture is
re-decided through `tools/replay_failure.py`'s predicates and that drift is
printed too. `--json` prints one JSON object per record instead; `--assert`
exits 1 on any drift. A record the reader cannot parse (wrong schema, events
not a list, an event without integer seq/t_ms or a string kind) is reported
as one `malformed record …` line on stderr and exit 2 — never a traceback.

WHAT THIS IS NOT: nothing is re-executed. No provider, CLI or supervisor
runs; no branch the supervisor WOULD take is computed (that reads the retry
counter, the lane policy and the pause state, none of which a record holds).

PURE BY CONSTRUCTION: imports `orgtree.turnread` (the readers — never
`turnlog`, the recorder, which needs a lock), `orgtree.failfix`,
`orgtree.failclass` and `orgtree.codex_decide` only — no `store`, no
`supervisor`, no data root. The suite runs it under the import hook that
refuses storage, provider and process modules and every file write.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "backend"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orgtree import failfix, turnread as turnlog  # noqa: E402
from replay_failure import PREDICATES  # noqa: E402

HEADER_KEYS = ("schema", "at", "attempt", "lane", "tier", "run",
               "run_since_ms", "resumed", "cmd", "ping", "toks", "text_len",
               "images_n", "view_len", "warm", "outcome", "outcome_ms",
               "error_class", "fixture", "paid_booked", "cost_usd",
               "cost_known", "partial", "events_n", "dropped",
               "dropped_kinds", "truncated", "recorder_errors")


class Malformed(Exception):
    """A record the reader cannot make sense of — reported as a diagnostic
    line and a nonzero exit, never a traceback."""


def inspect(path: str) -> dict:
    try:
        rec = turnlog.load(path)
        evs = rec.get("events") or []
        for i, e in enumerate(evs):
            if not isinstance(e, dict) or not isinstance(e.get("kind"), str)                     or isinstance(e.get("seq"), bool)                     or not isinstance(e.get("seq"), int)                     or isinstance(e.get("t_ms"), bool)                     or not isinstance(e.get("t_ms"), int):
                raise Malformed(f"event {i} is not {{seq:int, t_ms:int, kind:str, ...}}")
    except Malformed:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        raise Malformed(f"{type(e).__name__}: {e}") from None
    out: dict = {"record": os.path.basename(path),
                 "header": {k: rec.get(k) for k in HEADER_KEYS},
                 "events": rec.get("events") or [],
                 "summary": turnlog.summarize(rec),
                 "drift": turnlog.drift(rec),
                 "fixture": None}
    fp = turnlog.fixture_path(path, rec.get("fixture"))
    if rec.get("fixture") and fp is None:
        out["fixture"] = {"named": rec.get("fixture"), "resolved": False}
    elif fp is not None:
        rp = failfix.replay(failfix.load(fp), PREDICATES)
        out["fixture"] = {"named": rec.get("fixture"), "resolved": True,
                          "replay": rp}
        out["drift"] = list(out["drift"]) + ["fixture." + d for d in rp["drift"]]
    return out


def render(o: dict) -> str:
    h = o["header"]
    lines = [f"== {o['record']}"]
    lines.append("   " + "  ".join(f"{k}={h[k]!r}" for k in (
        "lane", "tier", "outcome", "outcome_ms", "error_class", "run",
        "run_since_ms", "resumed", "warm", "cmd", "ping", "toks")))
    lines.append("   " + "  ".join(f"{k}={h[k]!r}" for k in (
        "partial", "truncated", "events_n", "dropped", "recorder_errors",
        "paid_booked", "cost_usd", "cost_known", "fixture")))
    if h.get("partial"):
        lines.append("   !! PARTIAL: this record was never finalized (a live "
                     "attempt, a finalization that raised, a write that "
                     "failed, or a backend that died) - no events below")
    if h.get("truncated") or h.get("dropped"):
        lines.append(f"   !! {h.get('dropped')} event(s) dropped "
                     f"{dict(h.get('dropped_kinds') or {})}; truncated="
                     f"{h.get('truncated')} - the gap may hide the deciding event")
    for e in o["events"]:
        rest = {k: v for k, v in e.items() if k not in ("seq", "t_ms", "kind")}
        lines.append(f"   {str(e.get('seq')):>4}  {str(e.get('t_ms')):>8}  "
                     f"{str(e.get('kind')):<16} "
                     + "  ".join(f"{k}={v!r}" for k, v in rest.items()))
    s = o["summary"]
    lines.append(f"   summary: evidence={s['evidence']} phase={s['phase']} "
                 f"implied={s['implied']} recorded={h.get('outcome')!r} "
                 f"first_output_ms={s['first_output_ms']} "
                 f"boundary_ms={s['boundary_ms']} ordered={s['ordered']}")
    fx = o["fixture"]
    if fx:
        if fx["resolved"]:
            rp = fx["replay"]
            lines.append(f"   fixture {fx['named']}: recomputed verdict "
                         f"{rp['recomputed'].get('verdict')!r} recorded "
                         f"{rp['recorded'].get('verdict')!r} phase {rp['phase']!r}"
                         f"/{rp['phase_recorded']!r} drift {rp['drift']}")
        else:
            lines.append(f"   fixture {fx['named']}: not found beside this "
                         "record (or not a generated name) - not replayed")
    lines.append(f"   drift: {o['drift']}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    strict = "--assert" in argv
    as_json = "--json" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__)
        return 2
    rc = 0
    for p in paths:
        try:
            o = inspect(p)
        except Malformed as m:
            print(f"malformed record {os.path.basename(p)}: {m}", file=sys.stderr)
            rc = 2
            continue
        print(json.dumps(o, ensure_ascii=False) if as_json else render(o))
        if strict and o["drift"] and rc == 0:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
