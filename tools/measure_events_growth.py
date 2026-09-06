"""B10 — storage growth of the canonical `ev` key, measured on a READ-ONLY COPY of a real
data root (design typed-message-architecture-backend.md v5 §5/B10; Opus E3 discipline).

    python tools/measure_events_growth.py <copy-of-ORGTREE_DATA> [--json out.json]

INPUT FORMAT (what the coordinator supplies): a directory that is a consistent copy of an
ORGTREE_DATA root — the same layout the store writes (per-org SQLite documents or JSON
documents, plus defaults.json) — placed OUTSIDE the live root. This script never opens
the live root: it sets ORGTREE_DATA to the given path BEFORE importing orgtree, then
asserts `store.DATA_ROOT` is that path and does not resolve under the live root, and
aborts otherwise. It writes nothing into the copy.

WHAT IS MEASURED — two separate facts (coordinator 19:19):

1. EXISTING ROWS GROW BY ZERO. Migration adds `ev` only to rows minted after it; there is
   no backfill and no tag-on-read (design I5). Reported as `legacy_growth_bytes: 0` per
   section, with the current section size beside it, so the number is on the record.

2. PROSPECTIVE COST, as explicit SCENARIOS — no row is classified by its text or by its
   kind/sender (kind=request/status/notice and a non-@system sender do not identify an
   authored row: docket, review, generated status and engine-authored USER-routed rows
   share them). Each scenario charges EVERY row of the section the same `ev` size:
     * all_ordinary        — every row an ordinary.* envelope as the ROW encoder writes it
                             (body elided): the floor.
     * all_median_fixture  — every row the MEDIAN system-leaf fixture `ev`.
     * all_largest_fixture — every row the LARGEST system-leaf fixture `ev`. This is the
                             largest FIXTURE, not an upper bound: real payloads are
                             variable-length (a docket objective, a watchdog's lines).
   Each is reported in bytes and as % of the section's current size. The truth for a
   given org lies between all_ordinary and an all-largest-fixture-sized load; which one
   it is closer to depends on the org's mix, which this script deliberately does not
   guess.
"""
from __future__ import annotations

import json
import os
import sys

if len(sys.argv) < 2 or sys.argv[1].startswith("--"):
    print(__doc__)
    sys.exit(2)

COPY = os.path.realpath(sys.argv[1])
LIVE = os.path.realpath(os.path.join(os.path.expanduser("~"), "orgtree"))
if COPY == LIVE or COPY.startswith(LIVE + os.sep):
    print(f"REFUSED: {COPY} is the live root or inside it")
    sys.exit(3)
os.environ["ORGTREE_DATA"] = COPY
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from orgtree import events, store                       # noqa: E402
from orgtree.events_fixtures import FIXTURES            # noqa: E402

assert os.path.realpath(store.DATA_ROOT) == COPY, ("DATA_ROOT bound elsewhere", store.DATA_ROOT)
assert not os.path.realpath(store.DATA_ROOT).startswith(LIVE), store.DATA_ROOT

SECTIONS = ("mail", "mail_log", "notices", "notice_log", "user_inbox", "user_mail_log")


def _size(o) -> int:
    return len(json.dumps(o, ensure_ascii=False).encode("utf-8"))


def _rows(doc: dict, section: str) -> list[dict]:
    v = doc.get(section)
    if isinstance(v, dict):
        return [r for lst in v.values() if isinstance(lst, list) for r in lst if isinstance(r, dict)]
    if isinstance(v, list):
        return [r for r in v if isinstance(r, dict)]
    return []


# `ev` sizes from the real codec, on the real fixtures — as the ROW encoder writes them
_ord_fx = FIXTURES["ordinary.message"]
_ord_ev = events.mint("ordinary.message", {"kind": "agent", "id": "x"}, None, **_ord_fx["fields"])
ORDINARY_BYTES = _size({"ev": events.encode_row_ev(_ord_ev, {"body": _ord_fx["fields"]["body"]})})
_sys_sizes: dict[str, int] = {}
for v, fx in FIXTURES.items():
    if v.startswith(("ordinary.", "reply.")):
        continue
    ev = events.mint(v, fx["actor"], fx.get("object"), **fx["fields"])
    _sys_sizes[v] = _size({"ev": events.encode_row_ev(ev, {"body": events.render_agent(ev)})})
LARGEST_LEAF, LARGEST_BYTES = max(_sys_sizes.items(), key=lambda kv: kv[1])
_sorted = sorted(_sys_sizes.items(), key=lambda kv: kv[1])
MEDIAN_LEAF, MEDIAN_BYTES = _sorted[len(_sorted) // 2]

SCENARIOS = {
    "all_ordinary": ORDINARY_BYTES,
    "all_median_fixture": MEDIAN_BYTES,
    "all_largest_fixture": LARGEST_BYTES,
}

report = {
    "copy": COPY,
    "method": "no row is classified; each scenario charges every row the same ev size",
    "ev_bytes_per_row": {"all_ordinary": ORDINARY_BYTES,
                         "all_median_fixture": {"leaf": MEDIAN_LEAF, "bytes": MEDIAN_BYTES},
                         "all_largest_fixture": {"leaf": LARGEST_LEAF, "bytes": LARGEST_BYTES,
                                                 "note": "largest FIXTURE, not an upper bound "
                                                         "on variable-length real payloads"}},
    "orgs": [],
}
totals = {"doc_bytes": 0, "sections": {}}
for o, org in store.list_orgs_with_docs():
    doc = org.d
    per = {}
    for sec in SECTIONS:
        rows = _rows(doc, sec)
        if not rows:
            continue
        cur = _size(doc.get(sec))
        entry = {"rows": len(rows), "bytes_now": cur, "legacy_growth_bytes": 0,
                 "prospective": {}}
        for name, per_row in SCENARIOS.items():
            g = len(rows) * per_row
            entry["prospective"][name] = {"bytes": g, "pct_of_now": round(100.0 * g / max(cur, 1), 1)}
        per[sec] = entry
        t = totals["sections"].setdefault(sec, {"rows": 0, "bytes_now": 0,
                                                "prospective": {k: 0 for k in SCENARIOS}})
        t["rows"] += len(rows)
        t["bytes_now"] += cur
        for name in SCENARIOS:
            t["prospective"][name] += entry["prospective"][name]["bytes"]
    db = _size(doc)
    totals["doc_bytes"] += db
    report["orgs"].append({"slug": o["slug"], "doc_bytes": db, "sections": per})
for sec, t in totals["sections"].items():
    t["pct_of_now"] = {k: round(100.0 * v / max(t["bytes_now"], 1), 1)
                       for k, v in t["prospective"].items()}
report["totals"] = totals

out = json.dumps(report, indent=2, ensure_ascii=False)
if "--json" in sys.argv:
    with open(sys.argv[sys.argv.index("--json") + 1], "w", encoding="utf-8") as fh:
        fh.write(out)
print(out)
