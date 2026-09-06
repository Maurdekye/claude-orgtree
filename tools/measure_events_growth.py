"""B10 — storage growth of the canonical `ev` key, measured on a READ-ONLY COPY of a real
data root (design typed-message-architecture-backend.md v5 §5/B10; Opus E3 discipline).

    python tools/measure_events_growth.py <copy-of-ORGTREE_DATA> [--json out.json]

INPUT FORMAT (what the coordinator supplies): a directory that is a consistent copy of an
ORGTREE_DATA root — the same layout the store writes (per-org SQLite documents or JSON
documents, plus defaults.json) — placed OUTSIDE the live root. This script never opens
the live root: it sets ORGTREE_DATA to the given path BEFORE importing orgtree, then
asserts `store.DATA_ROOT` is that path and does not resolve under the live root, and
aborts otherwise. It writes nothing into the copy.

WHAT IS MEASURED. Migration adds `ev` only to rows minted AFTER it; existing rows stay
legacy and do not grow. So the number that matters is prospective: "if every row this
org has accumulated had been minted typed, how much larger would each section be?" For
each row in mail / mail_log / notices / notice_log / user_inbox:
  * an authored row (kind in the ordinary set, or an agent/user sender) is charged the
    ORDINARY envelope as the ROW encoder writes it (body elided, §5): the smallest `ev`;
  * every other row is charged the LARGEST fixture `ev` of any system leaf (worst case:
    the measurement does not classify rows by their text — it takes the ceiling).
Both a "typical" (ordinary envelope everywhere) and the "worst case" total are reported,
per section, in JSON bytes as stored, against the section's current size.
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

ORDINARY = {"message", "question", "request", "decision", "status", "notice"}
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


# envelope sizes from the real codec
_ord_fx = FIXTURES["ordinary.message"]
_ord_ev = events.mint("ordinary.message", {"kind": "agent", "id": "x"}, None, **_ord_fx["fields"])
ORDINARY_ROW_BYTES = _size({"ev": events.encode_row_ev(_ord_ev, {"body": _ord_fx["fields"]["body"]})})
_sys_sizes = {}
for v, fx in FIXTURES.items():
    if v.startswith(("ordinary.", "reply.")):
        continue
    ev = events.mint(v, fx["actor"], fx.get("object"), **fx["fields"])
    _sys_sizes[v] = _size({"ev": events.encode_row_ev(ev, {"body": events.render_agent(ev)})})
WORST_LEAF, WORST_BYTES = max(_sys_sizes.items(), key=lambda kv: kv[1])
MEDIAN_BYTES = sorted(_sys_sizes.values())[len(_sys_sizes) // 2]

report = {"copy": COPY, "ordinary_row_ev_bytes": ORDINARY_ROW_BYTES,
          "system_leaf_median_bytes": MEDIAN_BYTES, "worst_leaf": WORST_LEAF,
          "worst_leaf_bytes": WORST_BYTES, "orgs": []}
for o, org in store.list_orgs_with_docs():
    slug = o["slug"]
    doc = org.d
    per = {}
    for sec in SECTIONS:
        rows = _rows(doc, sec)
        if not rows:
            continue
        cur = _size(doc.get(sec))
        # notices carry no kind and are ALL system-typed after migration; mail rows are
        # authored unless the engine signed them
        n_ord = 0 if sec in ("notices", "notice_log") else sum(
            1 for r in rows if str(r.get("kind") or "message") in ORDINARY
            and str(r.get("from") or "") not in ("@system", "orgtree"))
        n_sys = len(rows) - n_ord
        per[sec] = {"rows": len(rows), "ordinary_rows": n_ord, "system_rows": n_sys,
                    "bytes_now": cur,
                    "typical_growth": n_ord * ORDINARY_ROW_BYTES + n_sys * MEDIAN_BYTES,
                    "worst_growth": n_ord * ORDINARY_ROW_BYTES + n_sys * WORST_BYTES}
        per[sec]["typical_pct"] = round(100.0 * per[sec]["typical_growth"] / max(cur, 1), 1)
        per[sec]["worst_pct"] = round(100.0 * per[sec]["worst_growth"] / max(cur, 1), 1)
    report["orgs"].append({"slug": slug, "doc_bytes": _size(doc), "sections": per})

out = json.dumps(report, indent=2, ensure_ascii=False)
if "--json" in sys.argv:
    with open(sys.argv[sys.argv.index("--json") + 1], "w", encoding="utf-8") as fh:
        fh.write(out)
print(out)
