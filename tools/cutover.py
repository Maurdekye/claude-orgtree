"""The two offline operator steps of the SQLite cutover, and the rollback.

Run with the backend STOPPED. Every subcommand claims the data root, which is
what guarantees this process is the only writer rather than racing one.

    python tools/cutover.py migrate        <root>
    python tools/cutover.py export-verify  <root>
    python tools/cutover.py rollback       <root>

⚠ `migrate` DELIBERATELY DOES NOT SET `ORGTREE_MIGRATE` FOR YOU. Converting a
data root rewrites it, so it is an operator action and the authorisation has
to come from outside this file — otherwise the tool authorises itself and the
gate is decoration. Supply it scoped to this one command:

    Windows      cmd /c "set ORGTREE_MIGRATE=1&& python tools\\cutover.py migrate <root>"
    POSIX        ORGTREE_MIGRATE=1 python tools/cutover.py migrate <root>

Both forms put the variable in the CHILD's environment only, so it dies with
the process. Do not `$env:ORGTREE_MIGRATE = "1"` in a shell you keep using: a
variable that must be removed afterwards is a step someone eventually skips,
which is the whole reason the deployed backend never receives this flag.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "backend"))


def _boot(root: str, backend: str):
    """Point the store at `root` BEFORE importing it — DATA_ROOT and
    STORE_BACKEND are both resolved once, at import."""
    os.environ["ORGTREE_DATA"] = os.path.abspath(root)
    os.environ["ORGTREE_STORE"] = backend
    from orgtree import store                                  # noqa: PLC0415
    return store


def _slugs(store, ext: str) -> list[str]:
    d = os.path.join(store.DATA_ROOT, "orgs")
    n = len(ext)
    return sorted(os.path.basename(p)[:-n]
                  for p in glob.glob(os.path.join(d, "*" + ext)))


def cmd_migrate(root: str) -> int:
    store = _boot(root, "sqlite")
    if not store.migration_authorised():
        print("REFUSING: ORGTREE_MIGRATE is not set in this process's "
              "environment.\n"
              "  Windows  cmd /c \"set ORGTREE_MIGRATE=1&& python "
              "tools\\cutover.py migrate <root>\"\n"
              "  POSIX    ORGTREE_MIGRATE=1 python tools/cutover.py "
              "migrate <root>", file=sys.stderr)
        return 2
    pending = store.pending_migrations()
    print(f"root    {store.DATA_ROOT}")
    print(f"pending {pending or '(nothing to migrate)'}")
    t = time.perf_counter()
    store.claim_data_root()                    # this is what migrates
    ms = (time.perf_counter() - t) * 1000
    left = store.pending_migrations()
    dbs = _slugs(store, ".db")
    print(f"migrated in {ms:.0f} ms -> {len(dbs)} database(s): {dbs}")
    if left:
        print(f"STILL PENDING: {left}", file=sys.stderr)
        return 1
    print("nothing pending. Now run: export-verify")
    return 0


def cmd_export_verify(root: str) -> int:
    """Export EVERY org and prove EVERY export loads — before the first boot.

    This is step 3 and it is not optional: it is the step that makes a
    rollback possible at all. `<slug>.json.premigration` is a backup of the
    document as it stood BEFORE the migration; the moment SQLite accepts a
    write it is no longer a way back. A current, validated export is."""
    store = _boot(root, "sqlite")
    from orgtree.ledger import Org                             # noqa: PLC0415
    store.claim_data_root()
    slugs = _slugs(store, ".db")
    if not slugs:
        print("no databases in this root — nothing to export", file=sys.stderr)
        return 1
    bad = 0
    out = {}
    for slug in slugs:
        try:
            p = store.export_json(slug)
            doc = json.load(open(p, encoding="utf-8"))
            Org(doc)                            # raises if it cannot be read
            nodes = len(doc.get("nodes") or {})
            out[slug] = p
            print(f"  OK   {slug:<14} {os.path.getsize(p):>12,} bytes  "
                  f"{nodes} nodes  -> {p}")
        except Exception as e:                                 # noqa: BLE001
            bad += 1
            print(f"  FAIL {slug:<14} {type(e).__name__}: {str(e)[:160]}",
                  file=sys.stderr)
    if bad:
        print(f"\n{bad} org(s) did not export-and-reload. DO NOT START THE "
              f"FLIP BUILD. Investigate before going further.", file=sys.stderr)
        return 1
    print(f"\nall {len(slugs)} org(s) exported and re-read. Keep these files: "
          f"they are the only route back once SQLite takes a write.")
    return 0


def cmd_rollback(root: str) -> int:
    """Back to JSON, in the order that does not lose data.

    Export and validate EVERYTHING first, then move. Steps 3 and 4 are
    all-or-nothing across the whole root: a JSON process started part-way
    through silently omits every org whose database has been parked but whose
    export is not yet installed."""
    store = _boot(root, "sqlite")
    from orgtree.ledger import Org                             # noqa: PLC0415
    store.claim_data_root()
    orgs = os.path.join(store.DATA_ROOT, "orgs")
    slugs = _slugs(store, ".db")
    if not slugs:
        print("no databases in this root — nothing to roll back", file=sys.stderr)
        return 1

    print(f"1/3  exporting and validating all {len(slugs)} org(s) BEFORE "
          f"moving anything")
    exports = {}
    for slug in slugs:
        p = store.export_json(slug)
        Org(json.load(open(p, encoding="utf-8")))
        exports[slug] = p
        print(f"     OK  {slug}")

    parked = os.path.join(store.DATA_ROOT, "parked-"
                          + time.strftime("%Y%m%dT%H%M%S"))
    os.makedirs(parked, exist_ok=True)
    print(f"2/3  parking the databases in {parked} (moved, never deleted)")
    for slug in slugs:
        # ⚠ CHECKPOINT AND CLOSE FIRST. `export_json` above went through the
        # pool, so a connection to each database is still checked in and open
        # — and Windows will not rename a file that anything holds. Measured:
        # without this, the parking loop dies with WinError 32 PART-WAY
        # THROUGH, which is the exact half-moved state this procedure exists
        # to avoid, at the exact moment an operator can least afford it.
        # Checkpointing as well as closing so no committed frame is left in a
        # `-wal` that its database is about to be separated from.
        with store._POOL.acquire(slug) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        store._POOL.close_all(slug)
        for suf in (".db", ".db-wal", ".db-shm"):
            src = os.path.join(orgs, slug + suf)
            if os.path.exists(src):
                shutil.move(src, os.path.join(parked, slug + suf))
        print(f"     parked {slug}")

    print("3/3  installing the exports as the live documents")
    for slug, p in exports.items():
        shutil.copyfile(p, os.path.join(orgs, slug + ".json"))
        print(f"     installed {slug}.json")

    print("\ndone. Start the JSON build now.\n"
          "The parked databases are still there; delete them only once you "
          "are satisfied.")
    return 0


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in (
            "migrate", "export-verify", "rollback"):
        print(__doc__)
        return 2
    cmd, root = sys.argv[1], sys.argv[2]
    if not os.path.isdir(os.path.join(root, "orgs")):
        print(f"not a data root (no orgs/ under {root!r})", file=sys.stderr)
        return 2
    return {"migrate": cmd_migrate,
            "export-verify": cmd_export_verify,
            "rollback": cmd_rollback}[cmd](root)


if __name__ == "__main__":
    raise SystemExit(main())
