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

import contextlib
import glob
import json
import os
import shutil
import sys
import tempfile
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


def _install(src: str, dst: str) -> None:
    """Put `src`'s bytes at `dst` atomically and durably.

    A half-written `<slug>.json` is the one artifact nothing downstream can
    detect: it is not a database, so the mismatch wall ignores it, and it is
    not valid JSON, so the org simply fails to load. Temp file in the same
    directory, fsync, then `os.replace` — which is atomic on both platforms."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dst), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            with open(src, "rb") as r:
                shutil.copyfileobj(r, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dst)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def cmd_rollback(root: str, stop_before_parking: bool = False) -> int:
    """Back to JSON, in the order where every interruption fails CLOSED.

    ⚠ THE ORDER HERE IS THE SAFETY PROPERTY, AND IT IS NOT THE OBVIOUS ONE.
    The obvious order — park the databases, then install the exports — was
    what this tool did, and phase1-audit proved it fails OPEN: hold a read
    handle on the second database, and the parking loop dies with the first
    org already parked and NO exports installed. Start the default SQLite
    backend on that root and it comes up cleanly reporting only the surviving
    org. The parked one has SILENTLY VANISHED, because a slug with only a
    `.json.premigration` is not "pending" and the migration wall never sees
    it. The pooled-connection bug was one cause; an external lock, an I/O
    error or process death anywhere in that loop reaches the same state.

    Installing FIRST is safe because a `.json` sitting beside a live `.db` is
    inert — SQLite reads the database and ignores it — and it is only safe to
    rely on that because the mismatch wall now refuses on ANY active database.
    So:

      after step 1   nothing has moved at all
      after step 2   every slug has BOTH a .json and a .db. SQLite still works
                     (databases are authoritative); JSON refuses.
      DURING step 3  parked slugs are .json-only; unparked are .json + .db.
                     SQLite refuses (JSON-without-DB is pending); JSON refuses
                     (a database is present). NEITHER BACKEND STARTS until the
                     last database moves.
      after step 3   .json only. JSON starts.

    There is no window in which an org can quietly disappear."""
    store = _boot(root, "sqlite")
    from orgtree.ledger import Org                             # noqa: PLC0415
    orgs = os.path.join(store.DATA_ROOT, "orgs")

    # ⚠ LOOK AT THE ROOT BEFORE CLAIMING IT. A rollback killed mid-parking
    # leaves a MIXED root — some slugs `.json`-only, others still holding a
    # database — and `claim_data_root` refuses that with `MigrationRefused`
    # before this function can do anything. That refusal is correct and stays.
    # What was wrong was the advice: the runbook said "fix the blocker and
    # re-run", and plain re-running cannot work. An operator was left holding
    # a root the tool would not touch and no documented way forward.
    # (phase1-audit, cutover_tool_resume.py, 2026-09-04.)
    have_db = set(_slugs(store, ".db"))
    have_json = {f[:-5] for f in os.listdir(orgs) if f.endswith(".json")}
    parked_only = sorted(have_json - have_db)          # already moved out

    # ⚠ THREE STATES, AND THEY MUST NOT BE CONFUSED WITH EACH OTHER. Answered
    # BEFORE the claim, because claiming a root with any JSON-without-DB slug
    # raises `MigrationRefused` first and buries whichever of these it is.
    #   no databases at all  -> the rollback already finished
    #   some parked, some not -> killed part-way (below)
    #   all databases         -> not begun; the normal path
    if not have_db:
        print(f"\nThe rollback on this root is already COMPLETE — there are "
              f"no databases left in orgs/.\n"
              f"  documents: {', '.join(sorted(have_json)) or '(none)'}\n"
              f"\nNothing to do. Start the JSON build.", file=sys.stderr)
        return 1
    if have_db and parked_only and not store.migration_authorised():
        me = os.path.join("tools", "cutover.py")
        print(
            f"\nTHIS ROOT IS PART-WAY THROUGH A ROLLBACK, not at the start of "
            f"one.\n"
            f"  already parked : {', '.join(parked_only)}\n"
            f"  still database : {', '.join(sorted(have_db))}\n"
            f"\nEvery export is already installed, so nothing is lost — and "
            f"both backends\nrefuse this root, which is why you are seeing "
            f"this rather than a half-empty org.\n"
            f"\nPlain re-running cannot proceed: the parked slugs now look "
            f"like unmigrated\nJSON, so claiming the root refuses first.\n"
            f"\nTo finish it, authorise the one operation that reverses the "
            f"partial move —\nrebuilding the parked slugs' databases FROM "
            f"THEIR INSTALLED EXPORTS, which\nrestores whole-root SQLite "
            f"authority — and this command then completes the\nrollback "
            f"normally:\n"
            f"\n  Windows  cmd /c \"set ORGTREE_MIGRATE=1&& python "
            f"{me} rollback {root}\"\n"
            f"  POSIX    ORGTREE_MIGRATE=1 python {me} rollback {root}\n"
            f"\n⚠ That is deliberately NOT automatic. Reconstructing an org's "
            f"authority from\nan export is exactly the operation that must "
            f"never happen because a tool decided\nit was probably fine. Read "
            f"the two lists above and confirm they are what you\nexpect before "
            f"you run it.", file=sys.stderr)
        return 2

    store.claim_data_root()
    # re-read AFTER the claim: with the opt-in set, claiming is what rebuilds
    # a part-way root's parked slugs from their installed exports, so the list
    # here can legitimately be longer than the one above.
    slugs = _slugs(store, ".db")

    print(f"1/3  exporting and validating all {len(slugs)} org(s) BEFORE "
          f"anything moves")
    exports = {}
    for slug in slugs:
        p = store.export_json(slug)
        Org(json.load(open(p, encoding="utf-8")))
        exports[slug] = p
        print(f"     OK  {slug}")

    print("2/3  installing the exports while every database is STILL "
          "authoritative")
    for slug, p in exports.items():
        dst = os.path.join(orgs, slug + ".json")
        _install(p, dst)
        # re-read the bytes that actually landed, not the ones we meant to
        # write: this is the last moment at which backing out costs nothing.
        Org(json.load(open(dst, encoding="utf-8")))
        print(f"     installed and re-read {slug}.json")

    if stop_before_parking:
        print("\nstopped before parking, as asked. The databases are still "
              "authoritative and SQLite still runs this root; JSON will "
              "refuse until they are parked. Re-run without the flag to "
              "finish.")
        return 0

    parked = os.path.join(store.DATA_ROOT, "parked-"
                          + time.strftime("%Y%m%dT%H%M%S"))
    os.makedirs(parked, exist_ok=True)
    print(f"3/3  parking the databases in {parked} (moved, never deleted)")
    moved: list[tuple[str, str]] = []
    try:
        for slug in slugs:
            # ⚠ checkpoint and close first: `export_json` went through the
            # pool, so a connection to each database is still open, and
            # Windows will not rename a file anything holds. Without this the
            # loop dies part way — measured.
            with store._POOL.acquire(slug) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            store._POOL.close_all(slug)
            for suf in (".db", ".db-wal", ".db-shm"):
                src = os.path.join(orgs, slug + suf)
                if os.path.exists(src):
                    dst = os.path.join(parked, slug + suf)
                    shutil.move(src, dst)
                    moved.append((dst, src))
            print(f"     parked {slug}")
    except BaseException as e:                                 # noqa: BLE001
        print(f"\nPARKING FAILED on this root: {type(e).__name__}: "
              f"{str(e)[:200]}", file=sys.stderr)
        back = 0
        for dst, src in reversed(moved):
            try:
                shutil.move(dst, src)
                back += 1
            except OSError:
                pass
        print(f"put {back} of {len(moved)} moved file(s) back.\n"
              f"⚠ Whether or not that succeeded, this root is SAFE: with a "
              f"database still present JSON refuses, and with any org reduced "
              f"to .json-only SQLite refuses. Nothing can start on it and "
              f"nothing has been lost — the exports are already installed. "
              f"Clear whatever held the file and re-run.", file=sys.stderr)
        return 1

    print("\ndone. Start the JSON build now.\n"
          "The parked databases are still there; delete them only once you "
          "are satisfied.")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) != 2 or args[0] not in (
            "migrate", "export-verify", "rollback"):
        print(__doc__)
        return 2
    cmd, root = args
    if not os.path.isdir(os.path.join(root, "orgs")):
        print(f"not a data root (no orgs/ under {root!r})", file=sys.stderr)
        return 2
    if cmd == "rollback":
        return cmd_rollback(root, "--stop-before-parking" in flags)
    return {"migrate": cmd_migrate,
            "export-verify": cmd_export_verify}[cmd](root)


if __name__ == "__main__":
    raise SystemExit(main())
