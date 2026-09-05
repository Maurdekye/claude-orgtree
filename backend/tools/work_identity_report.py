"""Which orgs still need the docket identity conversion. READ ONLY.

Prints machine-readable JSON. It opens documents and writes nothing — no
save, no export, no migration. Run it before converting anything, to know
what the conversion will touch and what it will refuse.

    python backend/tools/work_identity_report.py            # $ORGTREE_DATA
    python backend/tools/work_identity_report.py --data DIR

Output:

    {"data_root": "...", "orgs": [
       {"slug": "alpha", "state": "legacy", "items": 33,
        "with_old_key": 12, "unnamed": 0, "duplicate_names": [],
        "old_shaped_pointers": ["wdeadbeef"], "needs_migration": true,
        "will_refuse": false, "refusal": null}, ...],
     "needs_migration": ["alpha"], "will_refuse": []}

`will_refuse` is the one to read first: a document with two items already
answering to one name is REFUSED rather than renamed, because either answer
breaks a reference somebody has written down. Those orgs need a hand edit
before the conversion can run, and this says so in advance instead of at the
moment you try.

`old_shaped_pointers` is REPORTING, NOT A VERDICT. A pointer shaped like a
retired id may be perfectly canonical — an item can legally be named
`w1234abcd` — so it never decides `needs_migration`. It is listed so a human
can eyeball what the conversion will and will not be able to resolve.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", help="data root (default: $ORGTREE_DATA)")
    args = ap.parse_args()
    if args.data:
        os.environ["ORGTREE_DATA"] = args.data
    if not os.environ.get("ORGTREE_DATA"):
        print("refusing: set ORGTREE_DATA or pass --data. This tool never "
              "guesses which install it is looking at.", file=sys.stderr)
        return 2

    # ⚠ imported AFTER the root is set: `store.DATA_ROOT` binds at import.
    sys.path.insert(0, BACKEND)
    from orgtree import store                                     # noqa: PLC0415
    from orgtree.ledger import Org                                # noqa: PLC0415

    out: list[dict[str, object]] = []
    # `list_orgs()` returns SUMMARY ROWS, not slugs
    slugs = sorted(str(row.get("slug") or "") for row in store.list_orgs())
    for slug in [x for x in slugs if x]:
        org = store.load_org(slug)
        items = org._work_all()
        names: list[str] = []
        dupes: list[str] = []
        old_ptrs: set[str] = set()
        with_old_key = unnamed = 0
        for it in items:
            if "id" in it:
                with_old_key += 1
            name = str(it.get("slug") or "")
            if not name:
                unnamed += 1
            elif name in names:
                dupes.append(name)
            else:
                names.append(name)
            for ref in ([it.get("parent"), it.get("superseded_by")]
                        + list(it.get("dependencies") or [])):
                r = str(ref or "")
                if r and r not in names and Org._WORK_OLD_ID.match(r):
                    old_ptrs.add(r)
        state = org.work_identity_state()
        out.append({
            "slug": slug,
            "state": state,
            "items": len(items),
            "with_old_key": with_old_key,
            "unnamed": unnamed,
            "duplicate_names": sorted(set(dupes)),
            "old_shaped_pointers": sorted(old_ptrs),
            "marker": org.d.get("work_identity"),
            "needs_migration": state != Org.WORK_IDENTITY_SLUG,
            "will_refuse": bool(dupes),
            "refusal": (f"two items already carry the slug "
                        f"{sorted(set(dupes))[0]!r}") if dupes else None,
        })

    print(json.dumps({
        "data_root": store.DATA_ROOT,
        "orgs": out,
        "needs_migration": [o["slug"] for o in out if o["needs_migration"]],
        "will_refuse": [o["slug"] for o in out if o["will_refuse"]],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
