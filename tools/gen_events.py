"""Emit the generated artifacts of the canonical typed-message table.

    python tools/gen_events.py            # (re)write the files
    python tools/gen_events.py --check    # exit 1 if any checked-in file is stale

Source of truth: backend/orgtree/events_table.py (via events.py). Outputs — all owned by the
generator, never hand-edited (design §9 file ownership; feature-astra binds to them):

    frontend/src/generated/events.ts             Event / PublicEvent / Segment unions, FAMILY_OF,
                                                  MANIFEST, STRUCTURAL_KEYS, ELIDED_ROW_FIELDS
    frontend/src/generated/events.schema.json    JSON schema, private + public defs
    frontend/tests/fixtures/events/<variant>.json {private, public, body} per leaf, from the
                                                  same fixtures the backend tests use

Hermetic: no data root, no org. ORGTREE_DATA is pointed at a throwaway before any orgtree
import because the package may bind it (team rule).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

_TMP = tempfile.mkdtemp(prefix="orgtree-gen-events-")
os.environ.setdefault("ORGTREE_DATA", os.path.join(_TMP, "data"))
os.makedirs(os.environ["ORGTREE_DATA"], exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

from orgtree import events                       # noqa: E402
from orgtree.events_fixtures import FIXTURES     # noqa: E402

TS = os.path.join(ROOT, "frontend", "src", "generated", "events.ts")
SCHEMA = os.path.join(ROOT, "frontend", "src", "generated", "events.schema.json")
FIXDIR = os.path.join(ROOT, "frontend", "tests", "fixtures", "events")


def outputs() -> dict[str, str]:
    """path → content, exactly as the files should read (CRLF; the repo's .gitattributes)."""
    out: dict[str, str] = {
        TS: events.emit_typescript(),
        SCHEMA: json.dumps(events.emit_json_schema(), indent=2, ensure_ascii=False) + "\n",
    }
    for v, fx in FIXTURES.items():
        ev = events.mint(v, fx["actor"], fx.get("object"), **fx["fields"])
        doc = {"variant": v, "family": events.FAMILY_OF[v],
               "private": events.encode_ev(ev), "public": events.public_event(ev),
               "body": events.render_agent(ev)}
        out[os.path.join(FIXDIR, v + ".json")] = json.dumps(doc, indent=2,
                                                            ensure_ascii=False) + "\n"
    return {p: c.replace("\r\n", "\n").replace("\n", "\r\n") for p, c in out.items()}


def main(argv: list[str]) -> int:
    want = outputs()
    if "--check" in argv:
        stale = []
        for p, c in want.items():
            try:
                cur = open(p, "rb").read()
            except OSError:
                cur = None
            if cur != c.encode("utf-8"):
                stale.append(os.path.relpath(p, ROOT))
        extra = [f for f in (os.listdir(FIXDIR) if os.path.isdir(FIXDIR) else [])
                 if os.path.join(FIXDIR, f) not in want]
        if stale or extra:
            print("STALE generated files:", *stale, *[f"extra fixture {x}" for x in extra],
                  sep="\n  ")
            return 1
        print(f"generated files fresh ({len(want)} files)")
        return 0
    os.makedirs(os.path.dirname(TS), exist_ok=True)
    os.makedirs(FIXDIR, exist_ok=True)
    for p, c in want.items():
        with open(p, "wb") as fh:
            fh.write(c.encode("utf-8"))
    for f in os.listdir(FIXDIR):
        if os.path.join(FIXDIR, f) not in want:
            os.remove(os.path.join(FIXDIR, f))
    print(f"wrote {len(want)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
