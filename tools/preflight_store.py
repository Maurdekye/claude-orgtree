"""Will the backend this deploy is about to start actually START on this root?

    python tools/preflight_store.py --data <root> [--repo <checkout>]

WHY THIS EXISTS.  main defaults to `ORGTREE_STORE=sqlite` (2026-09-04).  An
install that was running the JSON format and pulls main gets a backend that
REFUSES TO START (`MigrationRefused`) against its own data root — and if that
install registered the autostart tasks, `orgtree-ensure` relaunches the refusing
build every five minutes forever.  A routine `git pull` becomes a permanent
outage.  This script is what the deploy scripts ask BEFORE they stop anything,
so the deploy can do something about it instead of discovering it afterwards.

USER RULING 2026-09-04 17:00Z/17:02Z: SQLite is the canonical backend, JSON is
deprecated and past LTS, and an existing JSON install must be migrated
AUTOMATICALLY the moment it updates — no prompt, no flag, no manual step.  So
this script's job is NOT to refuse the deploy.  It is to tell the deploy which
of five situations it is in, and the deploy migrates.

    exit 0   PROCEED   the backend will start: the root and the build agree
    exit 1   MIGRATE   unmigrated JSON documents and a SQLite build.  This is
                       the upgrade case.  The deploy runs the cutover.
    exit 2   MIXED     both .db and .json in orgs/.  NEITHER backend starts, by
                       design.  Stop; this needs a human.
    exit 3   MISMATCH  .db documents and a JSON-pinned build (`BackendMismatch`).
                       Stop; the fix is to stop pinning JSON, not to convert.
    exit 4   UNKNOWN   could not determine.  ⚠ The deploy PROCEEDS on this, on
                       purpose — see "WHY UNKNOWN IS NOT A REFUSAL" below.

WHAT ACTUALLY DECIDES, and therefore what this pins itself to.  Not a comment,
not the runbook, and not a filename convention retyped here: `store.py`'s own
`STORE_BACKEND`, `pending_migrations()` and `active_databases()` — the three
expressions `claim_data_root` consults to decide whether to raise.  This script
imports the store out of the checkout being deployed and asks those three.  If
they change, this changes with them.

WHY UNKNOWN IS NOT A REFUSAL.  The authoritative answer needs `orgtree.store`
to import, and its chain reaches `schema.py` → `typing_extensions`, a
third-party dependency.  On the deploy path this script runs BEFORE
`pip install -r requirements.txt`, so a checkout whose dependencies have moved
can fail to import for a reason that the deploy itself is about to fix.
Refusing there would be a guard a normal, correct deploy trips — worse than no
guard.  So the import failure falls back to a degraded read (the store default
parsed out of `store.py`'s source with `ast`, plus a plain listdir of `orgs/`),
which is reported as UNKNOWN with its best guess printed in full, and never
stops a deploy.  The residual is stated rather than hidden: on a checkout that
cannot import its own store, an unmigrated root gets a loud warning and the old
behaviour, not an automatic migration.

NOTHING HERE WRITES.  `ORGTREE_DATA` is pointed at a throwaway directory before
the store is imported and the target root is passed explicitly to every call,
which is the documented "claimed only — never migrated, never refused for JSON"
path and the same trick `cutover_deploy.py --probe-claim` uses.  A pre-flight
that could convert something is a pre-flight nobody could safely run.
"""
from __future__ import annotations

import argparse
import ast
import os
import shutil
import sys
import tempfile

# ⚠ THE OPERATOR-FACING TEXT BELOW IS DELIBERATELY PURE ASCII, and this is the
# belt to that braces.  A Windows PowerShell 5.1 console is cp1252/cp437, and
# printing a single "⚠" through it raised UnicodeEncodeError and killed this
# script mid-message (measured 2026-09-04, while writing it).  A pre-flight that
# CRASHES instead of telling an operator what is about to happen to their data
# is worse than one that never ran.  Comments and docstrings may use whatever
# they like -- they are never printed.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

PROCEED, MIGRATE, MIXED, MISMATCH, UNKNOWN = 0, 1, 2, 3, 4


# --------------------------------------------------------------- the reading

def _store_default_from_source(repo: str) -> str | None:
    """The `ORGTREE_STORE` default literal in `repo`'s `store.py`, by AST.

    The DEGRADED path only.  A regex over the line would also match the long
    comment above it, which in this file describes the very default it is
    documenting — the class of mistake this repo has now made several times.
    An AST walk can only see the assignment.
    """
    src = os.path.join(repo, "backend", "orgtree", "store.py")
    try:
        with open(src, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError):
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        # os.environ.get("ORGTREE_STORE", <default>)
        if not (isinstance(fn, ast.Attribute) and fn.attr == "get"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        if node.args[0].value != "ORGTREE_STORE":
            continue
        if len(node.args) < 2 or not isinstance(node.args[1], ast.Constant):
            return None
        val = node.args[1].value
        return val.strip().lower() if isinstance(val, str) else None
    return None


def _scan(root: str) -> tuple[list[str], list[str]]:
    """(pending, databases) by listdir — the DEGRADED mirror of
    `store.pending_migrations` / `store.active_databases`.

    ⚠ Deliberately NOT the slug-shape and containment checks the real
    functions do.  This runs only when the store could not be imported, and it
    is used only to WARN, so being marginally more inclusive than the real
    thing costs a warning and never a wrong decision.  `test_deploy_preflight`
    pins that the two agree on ordinary roots, so the drift stays visible.
    """
    d = os.path.join(root, "orgs")
    try:
        names = set(os.listdir(d))
    except OSError:
        return [], []
    dbs = sorted(n[:-3] for n in names if n.endswith(".db"))
    pend = sorted(n[:-5] for n in names
                  if n.endswith(".json") and f"{n[:-5]}.db" not in names)
    return pend, dbs


def inspect(root: str, repo: str) -> dict:
    """The verdict, as data.  `how` is 'store' (authoritative) or 'degraded'."""
    env = os.environ.get("ORGTREE_STORE", "").strip().lower()
    out: dict = {"root": os.path.abspath(root), "repo": os.path.abspath(repo)}

    # An unparseable ORGTREE_STORE is its own outage: `store.py` raises
    # ValueError at import and the backend never binds.  Named here because
    # "the backend exits instantly and says something about a value error" is
    # not a thing an operator connects to an environment variable they set
    # three weeks ago.
    if env and env not in ("json", "sqlite"):
        out.update(verdict=MISMATCH, how="env", backend=env,
                   why=(f"ORGTREE_STORE is set to {env!r}.  store.py accepts only "
                        "'json' or 'sqlite' and raises at import, so the backend "
                        "will not start at all."),
                   pending=[], databases=[])
        return out

    tmp = tempfile.mkdtemp(prefix="orgtree-preflight-")
    try:
        os.environ["ORGTREE_DATA"] = tmp          # never the target root
        sys.path.insert(0, os.path.join(os.path.abspath(repo), "backend"))
        try:
            from orgtree import store             # noqa: PLC0415
            # ⚠ WHICH store.py DID WE ACTUALLY GET?  `sys.path.insert(0, ...)`
            # only wins when the path it inserts EXISTS.  On a machine with
            # PYTHONPATH pointing at another orgtree checkout -- which is the
            # normal state of every agent shell on this fleet, measured
            # 2026-09-04 -- a bad `--repo` imports THAT checkout's store and
            # this script cheerfully reports the wrong build's default.  That
            # is the exact failure this whole file exists to prevent, one
            # level up.  So the module's provenance is checked, not assumed.
            want = os.path.join(os.path.abspath(repo), "backend") + os.sep
            got = os.path.abspath(getattr(store, "__file__", "") or "")
            if not got.lower().startswith(want.lower()):
                raise ImportError(
                    "imported %s, which is NOT under %s -- refusing to report "
                    "another checkout's store default as this one's" % (got, want))
            backend = store.STORE_BACKEND
            pending = store.pending_migrations(root)
            dbs = store.active_databases(root)
            how = "store"
        except Exception as e:                    # noqa: BLE001
            backend = env or _store_default_from_source(repo) or ""
            pending, dbs = _scan(root)
            how = "degraded"
            out["import_error"] = f"{type(e).__name__}: {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    out.update(how=how, backend=backend, pending=pending, databases=dbs,
               source=("ORGTREE_STORE in this deploy's environment" if env
                       else "the default in the code being deployed"))

    if how == "degraded" and not backend:
        out.update(verdict=UNKNOWN,
                   why="could not import the store and could not read its "
                       "default out of store.py either")
        return out
    if pending and dbs:
        out.update(verdict=MIXED,
                   why="orgs/ holds BOTH databases and documents")
    elif backend == "sqlite" and pending:
        out.update(verdict=MIGRATE,
                   why="a SQLite build and %d unmigrated JSON org(s)"
                       % len(pending))
    elif backend == "json" and dbs:
        out.update(verdict=MISMATCH,
                   why="a JSON build and %d SQLite database(s)" % len(dbs))
    else:
        out.update(verdict=PROCEED, why="the root and the build agree")
    if how == "degraded" and out["verdict"] != PROCEED:
        # A degraded read may only WARN.  The verdict is kept in `would_be` so
        # the deploy can print the whole recipe without acting on a guess.
        out["would_be"] = out["verdict"]
        out["verdict"] = UNKNOWN
    return out


# --------------------------------------------------------------- the telling
# ⚠ THE RECIPE IS WRITTEN HERE AND NOWHERE ELSE.  update.ps1 and update.sh
# print this script's output verbatim rather than restating it.  A procedure
# written in two places drifts, and the copy that drifts is the one nobody
# re-reads (docs/sqlite-cutover.md's own §"If you get the order wrong" was
# taught this in September 2026).  If the automatic path changes, it changes
# once, here, where the operator meets it.

BAR = "!" * 74


def explain(v: dict) -> str:
    root, pend, dbs = v["root"], v["pending"], v["databases"]
    verdict = v.get("would_be", v["verdict"])
    L = [""]
    if verdict == MIGRATE:
        L += [
            BAR,
            "  THIS INSTALL IS STILL ON THE JSON FORMAT AND IS BEING UPGRADED.",
            "",
            f"  data root : {root}",
            f"  to migrate: {', '.join(pend)}",
            f"  backend   : sqlite  (from {v['source']})",
            "",
            "  SQLite is orgtree's canonical format as of 2026-09-04; JSON is",
            "  deprecated and past LTS. This deploy converts the root for you:",
            "",
            "     stop the backend -> migrate -> export-verify -> start",
            "",
            "  WHAT IT DOES TO YOUR DATA. Each orgs/<slug>.json is rewritten as",
            "  orgs/<slug>.db, and the document it came from is KEPT, renamed to",
            "  orgs/<slug>.json.premigration. A full validated export is written",
            "  to exports/ before the new backend takes its first write; that",
            "  export is the rollback route (`tools/cutover.py rollback`), and",
            "  the .premigration files are a record, not a way back.",
            "",
            "  IF THE MIGRATION FAILS, the deploy brings your install back up on",
            "  the OLD format and says so. Nothing is converted half-way without",
            "  the run stopping and telling you.",
            "",
            "  To skip the automatic upgrade and deploy as you are today:",
            "     set ORGTREE_NO_AUTOCUTOVER=1  (and ORGTREE_STORE=json, or the",
            "     backend will refuse the root it is pointed at)",
            BAR]
    elif verdict == MIXED:
        L += [
            BAR,
            "  THIS DATA ROOT IS HALF-MIGRATED. NOTHING WILL BE STARTED.",
            "",
            f"  data root  : {root}",
            f"  databases  : {', '.join(dbs)}",
            f"  documents  : {', '.join(pend)}",
            "",
            "  NEITHER backend starts on this root, and that is deliberate, not a",
            "  malfunction: SQLite reads a document with no database beside it as",
            "  an unfinished migration and refuses; JSON refuses because a",
            "  database is present. Refusing is what stops an org silently",
            "  disappearing into a backend that came up carrying half the root.",
            "",
            "  NOTHING HAS BEEN LOST. Every converted org still has its",
            "  .json.premigration and every unconverted org still has its .json.",
            "",
            "  Run this, read the two lists it prints, and follow what it says:",
            f"     python tools/cutover.py rollback {root}",
            "",
            "  Do not start a backend by hand first.",
            BAR]
    elif verdict == MISMATCH and v.get("how") == "env":
        L += [BAR,
              f"  ORGTREE_STORE={v['backend']!r} IS NOT A VALUE THE BACKEND ACCEPTS.",
              "",
              "  store.py accepts 'json' or 'sqlite' and raises at import, so the",
              "  backend will not start at all. Unset it (SQLite is the default",
              "  and the canonical format) or set it to one of those two.",
              BAR]
    elif verdict == MISMATCH:
        L += [
            BAR,
            "  THIS ROOT IS ALREADY SQLITE, BUT THE BUILD IS PINNED TO JSON.",
            "",
            f"  data root : {root}",
            f"  databases : {', '.join(dbs)}",
            f"  backend   : json  (from {v['source']})",
            "",
            "  The backend would refuse this root with BackendMismatch. The fix",
            "  is to stop pinning JSON, not to convert anything: the root is",
            "  already on the canonical format.",
            "",
            "     Windows  [Environment]::SetEnvironmentVariable("
            "'ORGTREE_STORE',$null,'User')",
            "     POSIX    unset ORGTREE_STORE  (and remove it from your profile)",
            "",
            "  A pin like this is usually left behind by a cutover that aborted",
            "  before the migration. If yours did, it has already been reported.",
            BAR]
    if v.get("how") == "degraded":
        L += ["",
              "  !! THIS IS A DEGRADED READING. The store could not be imported out",
              f"    of {v['repo']}, so the above was worked out from store.py's",
              "    source and a plain directory listing instead of from the code",
              "    that actually decides:",
              f"      {v.get('import_error', 'unknown import failure')}",
              "    THE DEPLOY IS PROCEEDING ANYWAY, because refusing over a failed",
              "    probe would break deploys that are perfectly fine. If your",
              "    backend does not come up, the block above is the likely reason.",
              ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="pre-flight the store backend")
    ap.add_argument("--data", required=True, help="the data root to inspect")
    ap.add_argument("--repo", default=REPO,
                    help="the checkout being deployed (default: this script's)")
    ap.add_argument("--quiet", action="store_true",
                    help="print the one-line verdict only")
    a = ap.parse_args()

    v = inspect(a.data, a.repo)
    names = {PROCEED: "PROCEED", MIGRATE: "MIGRATE", MIXED: "MIXED",
             MISMATCH: "MISMATCH", UNKNOWN: "UNKNOWN"}
    print("store pre-flight: %s -- %s [%s: %s, %d db, %d pending]"
          % (names[v["verdict"]], v["why"], v["how"], v["backend"] or "?",
             len(v["databases"]), len(v["pending"])))
    if not a.quiet:
        text = explain(v)
        if text.strip():
            print(text)
    return int(v["verdict"])


if __name__ == "__main__":
    raise SystemExit(main())
