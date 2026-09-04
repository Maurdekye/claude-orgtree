"""Post-deploy health check: did the backend come up carrying the state it is
supposed to have?

WHY THIS EXISTS.  `update.ps1` used to end by asking `/api/orgs` for an HTTP
200 and calling that healthy.  **An empty list is a perfectly good 200.**  So
this sequence reported success: the SQLite cutover ships, someone rolls the
code back without migrating the data back, the backend starts fine against a
migrated root, presents ZERO orgs -- and the deploy script blesses it.  The
whole install appears to have vanished while every automated signal says green.
The storage layer refuses that particular mismatch loudly now, but "it
answered" was never a statement about state, and any number of other faults
land in the same place.

WHAT "EXPECTED" MEANS, AND WHY IT IS A FILESYSTEM SCAN.
The expectation is derived from the data root's own contents: a slug is
expected if `orgs/` holds ANY of `<slug>.json`, `<slug>.db` or
`<slug>.json.premigration*`.

  * NOT a hardcoded list -- that would be wrong on every machine but one.
  * NOT `store.list_orgs()`.  That is the most tempting option and it is the
    trap: under the rollback above, the deployed JSON build looks for
    `<slug>.json`, finds only `<slug>.db` + `<slug>.json.premigration`, and
    honestly reports zero.  A check that asks the code under test what to
    expect agrees with the bug.  So this module imports NOTHING from orgtree
    and reads only the stdlib -- it is a genuine second source of truth, which
    docs/test-baseline.md notes is the only thing that catches an instrument
    that never contradicts itself.
  * The union of the three suffixes is deliberately BACKEND-AGNOSTIC.  Under
    JSON the live document is `<slug>.json`; under SQLite it is `<slug>.db`
    with the pre-cutover copy parked at `<slug>.json.premigration`.  An org
    caught mid-migration has the premigration and no `.json` at all.  In every
    one of those states the org EXISTS and a healthy backend must serve it.
  * `delete_org` moves every one of an org's files -- premigration included --
    into `deleted/`, so `orgs/` holds live orgs only and a deleted org does
    not haunt the expectation.

THE FAILURE MODES THIS IS SHAPED AROUND.

  * **"I could not tell" is not "healthy."**  If the expectation cannot be
    determined the check FAILS (exit 3).  It never falls back to passing.
  * **A legitimately empty install is a real state.**  A data root with no
    `orgs/` directory -- a machine on its first-ever deploy -- expects zero
    orgs and passes when it serves zero.  It does not fail forever.
  * **"Not up yet" and "up and wrong" are different answers**, and the check
    reports them as different verdicts (exit 2 vs exit 1) because the operator
    does entirely different things about them.
  * **It must not hang the deploy.**  Two bounded budgets, never one open
    wait: `--up-deadline` for the first 200, then `--state-deadline` for the
    state to settle.  The worst case is their sum, always.
  * **It must not cry wolf.**  The state comparison RETRIES for the whole
    second budget and passes the moment it matches, so a backend that answers
    before it has finished loading costs nothing.  And the pre-restart served
    set is captured as diagnostic context ONLY -- a machine that was already
    in a strange state before this deploy is not failed for it here.

USAGE (see update.ps1 section 5):

    python tools/deploy_health.py snapshot --data ROOT --port P --out FILE
    python tools/deploy_health.py verify  --port P --state FILE

`snapshot` runs BEFORE the backend is stopped: it works out the expectation
while the old process is still there to be asked, and records both.  `verify`
runs after the new one is started.  Splitting them is not decoration -- it
means an unreadable data root is reported before anything is killed, and it
gives the failure message the one sentence that makes it actionable ("the old
backend served the same three; this one serves none").
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

# On Windows a redirected stream is cp1252, and the operator deploy IS
# redirected (`operator-deploy-*.log` in the data root).  `print()` of a
# character cp1252 lacks raises UnicodeEncodeError -- which, in a script whose
# whole job is to be believed, would turn a red verdict into a crash or, worse,
# into nothing at all.  api.py does the same thing for the same reason.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
    except Exception:
        pass

OK = 0              # up, and serving exactly the expected orgs
WRONG_STATE = 1     # up, and NOT serving the expected orgs
NO_ANSWER = 2       # nothing answered on the port inside the budget
NO_EXPECTATION = 3  # could not work out what to expect -- never a pass

_BAD_CHARS = set("/\\:*?\"<>|")


def slug_ok(slug: str) -> bool:
    """The shape half of `store._safe_slug`, replayed here rather than
    imported (see the module docstring: this file imports no orgtree).  It is
    a filter against junk in `orgs/`, not a security boundary -- nothing here
    opens a path built from a slug."""
    if not slug or len(slug) > 128:
        return False
    if slug in (".", "..") or slug.startswith((".", "-")):
        return False
    if slug != slug.strip():
        return False
    if any(c in _BAD_CHARS for c in slug):
        return False
    return not any(ord(c) < 0x20 or ord(c) == 0x7f for c in slug)


def expected_orgs(data_root: str) -> tuple[list[str], str]:
    """(slugs, note) for the orgs this data root says must exist.

    Raises OSError if `orgs/` exists but cannot be read -- the caller turns
    that into NO_EXPECTATION rather than into an empty list, because an
    unreadable directory is the one case where guessing is how a check goes
    quietly dark.

    A MISSING `orgs/` (or a missing data root) is NOT that case: it is a
    machine that has never had an org, which is a real and healthy state.
    """
    d = os.path.join(data_root, "orgs")
    if not os.path.isdir(d):
        return [], ("no orgs directory under %s -- expecting an EMPTY install "
                    "(a first deploy, or a data root nothing has used yet)"
                    % data_root)
    names = os.listdir(d)     # OSError propagates on purpose
    out = set()
    for n in names:
        if n.endswith(".json"):
            slug = n[:-5]
        elif n.endswith(".db"):
            slug = n[:-3]
        else:
            # `<slug>.json.premigration`, and the hand-annotated variants of it
            # that live on real machines (`.premigration.STALE-...`).
            i = n.find(".json.premigration")
            if i <= 0:
                # everything else -- a `.tmp` from a mkstemp that died, a
                # `.db.migrating` (which never appears without its
                # premigration, so its org is counted anyway), a stray note.
                continue
            slug = n[:i]
        if slug_ok(slug):
            out.add(slug)
    return sorted(out), ""


class BadBody(Exception):
    """A 200 whose body is not the list of org rows the desk parses.

    Its own type because the poll loop must treat it as **answered and
    wrong**, not as "still starting".  A backend serving `index.html` at
    `/api/orgs` because its routing broke is up; it is just not serving orgs,
    and reporting that as "never came up" would send the operator to the wrong
    log."""


def served_orgs(port: int, timeout: float) -> list[str]:
    """Slugs `/api/orgs` is presenting, sorted.

    Transport failures and non-2xx (refused, timed out, a 503 from a backend
    still warming) raise out of urllib; a 200 that cannot be read as org rows
    raises `BadBody`.  The caller depends on that split."""
    url = "http://127.0.0.1:%d/api/orgs" % port
    with urllib.request.urlopen(url, timeout=timeout) as r:   # noqa: S310
        if r.status != 200:
            raise ValueError("HTTP %s" % r.status)
        raw = r.read()
    try:
        rows = json.loads(raw.decode("utf-8"))
        if not isinstance(rows, list):
            raise TypeError("not a list")
        return sorted(str(o["slug"]) for o in rows)
    except Exception as e:
        raise BadBody("%s: %s" % (type(e).__name__, e)) from e


def _bar() -> str:
    return "!" * 74


def _fmt(slugs) -> str:
    if slugs is None:
        return "(unknown)"
    return ", ".join(slugs) if slugs else "(none)"


# ---------------------------------------------------------------- snapshot --

def cmd_snapshot(args: argparse.Namespace) -> int:
    state = {
        "data_root": args.data,
        "port": args.port,
        "expected": None,
        "expected_ok": False,
        "expected_error": "",
        "note": "",
        "served_before": None,
    }
    rc = OK
    try:
        exp, note = expected_orgs(args.data)
        state["expected"] = exp
        state["expected_ok"] = True
        state["note"] = note
        if note:
            print("deploy-health: %s" % note)
        print("deploy-health: expecting %d org(s) from %s: %s"
              % (len(exp), args.data, _fmt(exp)))
    except OSError as e:
        state["expected_error"] = (
            "could not read %s (%s)" % (os.path.join(args.data, "orgs"), e))
        rc = NO_EXPECTATION
        print("deploy-health: %s -- the post-deploy check will have no "
              "expectation to assert, and will FAIL rather than pass on it."
              % state["expected_error"])

    # Diagnostic only.  Never fatal: a machine that was already in a strange
    # state before this deploy must not be failed for it by THIS deploy's
    # check -- but knowing what the outgoing process served is the difference
    # between "the deploy lost them" and "it was already like that".
    try:
        got = served_orgs(args.port, args.http_timeout)
        state["served_before"] = got
        print("deploy-health: the running backend currently serves %d org(s): %s"
              % (len(got), _fmt(got)))
    except Exception as e:
        print("deploy-health: nothing readable on port %d right now (%s: %s) -- "
              "no before/after comparison will be available"
              % (args.port, type(e).__name__, e))

    # The file is written even when the expectation failed, so `verify` can
    # tell "the snapshot said it could not tell" from "the snapshot never ran".
    # Both are failures; they are different failures.
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(state, f)
    return rc


# ------------------------------------------------------------------ verify --

def cmd_verify(args: argparse.Namespace) -> int:
    try:
        with open(args.state, encoding="utf-8") as f:
            state = json.load(f)
    except Exception as e:
        # Not a soft landing.  If the snapshot is gone the check has no
        # expectation at all, and a check with no expectation that passes is
        # exactly the defect this file exists to close.
        state = {"expected_ok": False,
                 "expected_error": "the pre-restart snapshot %s could not be "
                                   "read (%s: %s)" % (args.state, type(e).__name__, e)}

    expected = sorted(state.get("expected") or []) if state.get("expected_ok") else None
    before = state.get("served_before")

    # Poll on two separate budgets.  The first buys the backend time to bind
    # and answer; the second buys it time to be RIGHT, which is what stops a
    # backend answering before it has finished loading from being called a
    # failure.  Neither is unbounded and the worst case is their sum.
    up_deadline = time.monotonic() + args.up_deadline
    state_deadline = None
    answered = False
    last = None
    last_err = ""

    while True:
        now = time.monotonic()
        if state_deadline is not None:
            if now >= state_deadline:
                break
        elif now >= up_deadline:
            break
        time.sleep(args.interval)
        got = None
        try:
            got = served_orgs(args.port, args.http_timeout)
        except BadBody as e:
            # It ANSWERED.  Wrong, but answered -- so this starts the state
            # budget and ends up in the "up and presenting the wrong state"
            # verdict rather than in "it never came up".
            last_err = "200 with an unreadable body (%s)" % e
        except Exception as e:          # refused, timed out, non-2xx
            last_err = "%s: %s" % (type(e).__name__, e)
            if not answered:
                continue
        if not answered:
            answered = True
            state_deadline = time.monotonic() + args.state_deadline
        if got is None:
            continue
        last = got
        if expected is not None and got == expected:
            print("deploy-health: up and carrying its state -- %d org(s): %s"
                  % (len(got), _fmt(got)))
            return OK

    # ---- something went wrong; say which thing, loudly and specifically ----
    if expected is None:
        print("")
        print(_bar())
        print("  DEPLOY HEALTH: COULD NOT DETERMINE THE EXPECTED STATE")
        print("")
        print("  %s" % (state.get("expected_error") or "(no reason recorded)"))
        print("")
        if answered:
            print("  The backend IS answering on port %d, serving: %s"
                  % (args.port, _fmt(last)))
        else:
            print("  The backend is NOT answering on port %d." % args.port)
        print("")
        print("  This is deliberately a FAILURE and not a pass.  The check")
        print("  cannot say whether this deploy came up carrying its orgs, and")
        print("  a health check that shrugs is worth less than none at all.")
        print("  Fix the data root path or its permissions and deploy again.")
        print(_bar())
        return NO_EXPECTATION

    if not answered:
        print("")
        print(_bar())
        print("  DEPLOY HEALTH: THE BACKEND NEVER ANSWERED")
        print("")
        print("  Nothing served /api/orgs on port %d within %.0fs."
              % (args.port, args.up_deadline))
        print("  last error: %s" % (last_err or "(none recorded)"))
        print("")
        print("  This is 'it did not start', NOT 'it started wrong'.  Read the")
        print("  backend error log named in the deploy output above.")
        print(_bar())
        return NO_ANSWER

    print("")
    print(_bar())
    print("  DEPLOY HEALTH: THE BACKEND IS UP AND PRESENTING THE WRONG STATE")
    print("")
    print("  data root : %s" % state.get("data_root", "(unrecorded)"))
    print("  expected  : %d org(s) -- %s" % (len(expected), _fmt(expected)))
    if last is None:
        # It answered, but never once with something readable as org rows.
        print("  serving   : it answers, but never with a readable org list")
        print("  last read : %s" % (last_err or "(none recorded)"))
    else:
        missing = [s for s in expected if s not in last]
        extra = [s for s in last if s not in expected]
        print("  serving   : %d org(s) -- %s" % (len(last), _fmt(last)))
        if missing:
            print("  MISSING   : %s" % _fmt(missing))
        if extra:
            print("  UNEXPECTED: %s" % _fmt(extra))
    print("")
    if before is not None:
        if sorted(before) == expected:
            print("  Before the restart the OLD backend served exactly these %d,"
                  % len(expected))
            print("  so THIS DEPLOY lost them.  Roll the code back, or start the")
            print("  backend with the store backend that matches what is on disk.")
        else:
            print("  Before the restart the old backend served: %s" % _fmt(before))
            print("  It did not match the data root either, so this predates the")
            print("  deploy -- but it is still wrong and still not healthy.")
    else:
        print("  Nothing was answering before the restart, so there is no")
        print("  before/after comparison to offer.")
    print("")
    print("  The org documents are still ON DISK -- this is a serving fault,")
    print("  not a deletion.  Nothing in this check has written anything.")
    print(_bar())
    return WRONG_STATE


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="assert a deployed backend came up carrying its orgs")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot",
                       help="before the restart: work out what to expect")
    s.add_argument("--data", required=True, help="the data root")
    s.add_argument("--port", required=True, type=int)
    s.add_argument("--out", required=True, help="where to write the state file")
    s.add_argument("--http-timeout", type=float, default=5.0)
    s.set_defaults(fn=cmd_snapshot)

    v = sub.add_parser("verify",
                       help="after the restart: assert it came up with that state")
    v.add_argument("--port", required=True, type=int)
    v.add_argument("--state", required=True, help="the file snapshot wrote")
    v.add_argument("--interval", type=float, default=0.5)
    v.add_argument("--http-timeout", type=float, default=5.0)
    v.add_argument("--up-deadline", type=float, default=20.0,
                   help="seconds to wait for the FIRST answer")
    v.add_argument("--state-deadline", type=float, default=20.0,
                   help="seconds after the first answer for the state to match")
    v.set_defaults(fn=cmd_verify)

    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
