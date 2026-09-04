"""The automatic upgrade off JSON: `tools/preflight_store.py` and its wiring.

WHAT THIS SUITE IS.  Two kinds of check, and the difference matters.

  * §1-§3 RUN THE THING.  `tools/preflight_store.py` is executed as a
    subprocess against data roots this file builds, and its exit code is the
    assertion.  These are not text searches; they would catch a rewrite that
    kept every keyword and changed every answer.
  * §4-§5 are TEXT PINS on `update.ps1` / `update.sh` / `cutover_deploy.ps1`,
    because a PowerShell deploy script cannot be executed from here without
    stopping a backend.  They pin the handful of properties that were
    established by reading and that a later edit would silently undo -- above
    all THE ORDER: the pre-flight must sit after the pull and before the stop.

WHY THE ORDER IS THE THING WORTH PINNING.  main defaults to
`ORGTREE_STORE=sqlite`.  The install this whole feature exists for is one that
is still on JSON, and *its* checkout still defaults to `json` until the pull
lands.  A pre-flight moved to the top of the file would therefore read "JSON
code, JSON root, all fine", do nothing, and be perfectly green here forever --
present, plausible and inert, on exactly the population it was written for.
So §4 asserts positions, not presence.

    python backend/tests/test_deploy_preflight.py

Hermetic: builds its own roots under a temp dir, never reads or writes any
real data root, starts no backend and opens no port.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
PREFLIGHT = os.path.join(ROOT, "tools", "preflight_store.py")
UPDATE_PS1 = os.path.join(ROOT, "update.ps1")
UPDATE_SH = os.path.join(ROOT, "update.sh")
CUTOVER_PS1 = os.path.join(ROOT, "tools", "cutover_deploy.ps1")
STORE_PY = os.path.join(ROOT, "backend", "orgtree", "store.py")

PROCEED, MIGRATE, MIXED, MISMATCH, UNKNOWN = 0, 1, 2, 3, 4

FAILED: list[str] = []
PASSED = 0
TMP = tempfile.mkdtemp(prefix="orgtree-preflight-suite-")


def check(label, fn) -> None:
    global PASSED
    try:
        fn()
    except Exception as e:                                     # noqa: BLE001
        import traceback
        FAILED.append("%s\n      %s: %s\n%s"
                      % (label, type(e).__name__, e,
                         "".join("        " + ln for ln in
                                 traceback.format_exc().splitlines(True))))
        print("  x %s" % label)
    else:
        PASSED += 1
        print("  ok %s" % label)


def read(p: str) -> str:
    with open(p, encoding="utf-8-sig") as f:
        return f.read()


def code(p: str, marks=("#",)) -> str:
    """`p` with every whole-line comment removed.

    ⚠ NOT TIDINESS -- copied from `test_cutover_deploy.py`, where it was
    learned the hard way.  Every text check below searches this, because the
    files being searched DESCRIBE THEIR OWN BEHAVIOUR in comments: a search for
    the line that does a thing also matches the paragraph explaining it, so
    commenting the line out leaves the check green.  `update.ps1` in particular
    now carries a long comment block about the pre-flight sitting directly
    above the pre-flight.
    """
    out = []
    for ln in read(p).splitlines():
        s = ln.lstrip()
        if any(s.startswith(m) for m in marks):
            continue
        out.append(ln)
    return "\n".join(out)


# ⚠ THE NEEDLES BELOW ARE THE INVOCATIONS, NOT THE FILENAME, AND THAT WAS
# MEASURED.  §4 first searched for "preflight_store.py" -- which also appears
# in the line that merely COMPUTES the script's path.  Commenting out the
# actual call left all four order checks GREEN (mutation-tested 2026-09-04
# while writing this suite).  Pin against the thing that actually runs.
PS_CALL = "& $py $preflight --data"
SH_CALL = '"$PY" "$PREFLIGHT" --data'


def at(text: str, needle: str) -> int:
    """Index of `needle`, or -1.  Positions are what §4 asserts."""
    return text.find(needle)


# ---------------------------------------------------------- root fixtures --

def make_root(kind: str) -> str:
    """A throwaway data root in one of the shapes the deploy meets.

    Shapes, not real databases: `pending_migrations` and `active_databases`
    are documented as EXISTENCE checks that open nothing (store.py:612), so a
    zero-byte `.db` is exactly as decisive here as a real one -- and building
    real ones would make this suite depend on the migration it is testing the
    detection of.  §3 proves the shapes agree with the real functions.
    """
    d = os.path.join(TMP, kind + "-" + str(len(os.listdir(TMP))))
    orgs = os.path.join(d, "orgs")
    os.makedirs(orgs)
    if kind in ("json", "mixed"):
        for slug in ("acme", "beta"):
            with open(os.path.join(orgs, slug + ".json"), "w") as f:
                json.dump({"org": {}}, f)
    if kind in ("sqlite", "mixed"):
        for slug in ("gamma", "delta"):
            open(os.path.join(orgs, slug + ".db"), "wb").close()
            # the parked source a real migration leaves behind, plus the WAL
            # sidecars SQLite writes.  Neither may be mistaken for a document
            # or a database -- a root that read as `mixed` because of its own
            # backups would refuse every deploy forever.
            with open(os.path.join(orgs, slug + ".json.premigration"), "w") as f:
                json.dump({"org": {}}, f)
            open(os.path.join(orgs, slug + ".db-wal"), "wb").close()
            open(os.path.join(orgs, slug + ".db-shm"), "wb").close()
    return d


def run_preflight(root: str, store: str | None = None,
                  repo: str = ROOT) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("ORGTREE_STORE", None)
    env.pop("ORGTREE_MIGRATE", None)
    # ⚠ PYTHONPATH IS CLEARED, and that is not hygiene.  Every agent shell on
    # this fleet has PYTHONPATH pointing at the deployed checkout (measured
    # 2026-09-04), so a child that inherited it could import THAT store and
    # report another build's default as this one's -- which is the exact class
    # of bug this suite exists to prevent.  §2 proves the tool catches it.
    env.pop("PYTHONPATH", None)
    if store is not None:
        env["ORGTREE_STORE"] = store
    return subprocess.run(
        [sys.executable, PREFLIGHT, "--data", root, "--repo", repo],
        capture_output=True, text=True, env=env, encoding="utf-8",
        errors="replace")


# ------------------------------------- §1  the five verdicts, by execution --

def a_json_root_under_a_sqlite_build_says_migrate() -> None:
    r = run_preflight(make_root("json"))
    assert r.returncode == MIGRATE, (r.returncode, r.stdout, r.stderr)
    assert "acme, beta" in r.stdout, r.stdout


def a_migrated_root_under_a_sqlite_build_proceeds() -> None:
    r = run_preflight(make_root("sqlite"))
    assert r.returncode == PROCEED, (r.returncode, r.stdout, r.stderr)


def an_empty_root_proceeds() -> None:
    """A brand-new install has no orgs at all and must deploy in silence.
    Anything else makes the first run of a fresh install look broken."""
    r = run_preflight(make_root("empty"))
    assert r.returncode == PROCEED, (r.returncode, r.stdout, r.stderr)


def a_half_migrated_root_says_mixed() -> None:
    r = run_preflight(make_root("mixed"))
    assert r.returncode == MIXED, (r.returncode, r.stdout, r.stderr)
    assert "NOTHING WILL BE STARTED" in r.stdout, r.stdout


def a_sqlite_root_under_a_json_pin_says_mismatch() -> None:
    r = run_preflight(make_root("sqlite"), store="json")
    assert r.returncode == MISMATCH, (r.returncode, r.stdout, r.stderr)


def a_json_root_under_a_json_pin_proceeds() -> None:
    """⚠ THE GUARD A CORRECT DEPLOY MUST NOT TRIP.  An install that has
    deliberately stayed on JSON -- including the one `cutover_deploy.ps1`'s
    pre-migration recovery creates by pinning ORGTREE_STORE=json -- deploys
    exactly as it did before any of this existed.  If this check ever goes
    red, the automatic upgrade has started firing at installs that did not
    need it."""
    r = run_preflight(make_root("json"), store="json")
    assert r.returncode == PROCEED, (r.returncode, r.stdout, r.stderr)


def an_unusable_store_value_is_named_rather_than_left_to_the_backend() -> None:
    r = run_preflight(make_root("json"), store="sqlite3")
    assert r.returncode == MISMATCH, (r.returncode, r.stdout, r.stderr)
    assert "sqlite3" in r.stdout, r.stdout


# ------------------------------------------------ §2  it degrades honestly --

def a_repo_whose_store_cannot_be_imported_never_refuses() -> None:
    """UNKNOWN proceeds.  The probe runs before `pip install`, so an import
    failure can be something the deploy itself is about to fix; refusing there
    would be a guard a normal, correct deploy trips."""
    r = run_preflight(make_root("json"), repo=os.path.join(TMP, "no-such-repo"))
    assert r.returncode == UNKNOWN, (r.returncode, r.stdout, r.stderr)
    assert "DEGRADED" in r.stdout, r.stdout


def the_degraded_path_still_prints_the_whole_recipe() -> None:
    """Proceeding quietly would be the worst of both.  The operator whose
    backend then refuses must still have been told what happened and why.

    ⚠ THE REPO HERE IS REAL BUT UNIMPORTABLE, which is the case a deploy
    actually meets: `store.py` is present and readable but its import chain
    reaches a dependency `pip install` has not put there yet.  The first
    version of this check pointed at a directory that did not exist at all,
    where nothing can be read and no recipe CAN be printed -- so it was
    testing the one degraded case where silence is the honest answer, and it
    would have gone green on a tool that never printed a recipe at all."""
    fake = os.path.join(TMP, "degraded-repo")
    os.makedirs(os.path.join(fake, "backend", "orgtree"), exist_ok=True)
    shutil.copy(STORE_PY, os.path.join(fake, "backend", "orgtree",
                                       "store.py"))
    r = run_preflight(make_root("json"), repo=fake)
    assert r.returncode == UNKNOWN, (r.returncode, r.stdout)
    assert "DEGRADED" in r.stdout, r.stdout
    assert "STILL ON THE JSON FORMAT" in r.stdout, (
        "a deploy whose store would not import gets no recipe at all: %s"
        % r.stdout)
    assert "cutover.py" in r.stdout or "premigration" in r.stdout, r.stdout


def another_checkouts_store_is_not_reported_as_this_ones() -> None:
    """The defect found while writing this tool, on this machine: with
    PYTHONPATH pointing at a different orgtree checkout, `sys.path.insert`
    loses (the inserted path does not exist) and `from orgtree import store`
    silently returns the OTHER build's module.  The tool then reports that
    build's default as the one about to be deployed."""
    fake = os.path.join(TMP, "fake-repo")
    os.makedirs(fake, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.path.join(ROOT, "backend")
    env.pop("ORGTREE_STORE", None)
    r = subprocess.run(
        [sys.executable, PREFLIGHT, "--data", make_root("json"),
         "--repo", fake],
        capture_output=True, text=True, env=env, encoding="utf-8",
        errors="replace")
    assert r.returncode == UNKNOWN, (
        "the tool reported a verdict from a store it did not load out of the "
        "repo it was given: %s\n%s" % (r.returncode, r.stdout))
    assert "is NOT under" in r.stdout, r.stdout


# ---------------------------------- §3  the degraded scan agrees with store --

def the_fallback_scan_agrees_with_the_functions_that_actually_decide() -> None:
    """The degraded reading uses its own listdir instead of
    `store.pending_migrations` / `store.active_databases`.  Two
    implementations of one question drift; this is where the drift shows."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    import preflight_store                                   # noqa: PLC0415
    from orgtree import store                                # noqa: PLC0415
    for kind in ("json", "sqlite", "mixed", "empty"):
        r = make_root(kind)
        pend, dbs = preflight_store._scan(r)
        assert pend == store.pending_migrations(r), (kind, pend)
        assert dbs == store.active_databases(r), (kind, dbs)


def the_root_shapes_are_the_shapes_a_real_migration_leaves() -> None:
    """A positive control on the FIXTURES, not on the code.  If `make_root`
    stopped producing the sidecars and the parked source that a real migration
    writes, every §1 check would still pass while proving nothing about a real
    root."""
    r = make_root("sqlite")
    names = set(os.listdir(os.path.join(r, "orgs")))
    for want in ("gamma.db", "gamma.json.premigration", "gamma.db-wal"):
        assert want in names, (want, sorted(names))


# ------------------------------------------- §4  WHERE the call site sits --

def the_windows_preflight_runs_after_the_pull_and_before_the_stop() -> None:
    t = code(UPDATE_PS1)
    pull = at(t, "git pull --ff-only")
    pre = at(t, PS_CALL)
    stop = at(t, "Stop-Process -Id $p -Force")
    assert pull >= 0 and pre >= 0 and stop >= 0, (pull, pre, stop)
    assert pull < pre, (
        "the pre-flight runs BEFORE the pull, so on an install still on the "
        "old JSON-defaulting code it reads the OLD default, finds nothing to "
        "do, and is inert on exactly the population it exists for")
    assert pre < stop, (
        "the pre-flight runs AFTER the backend is stopped. An install that "
        "stops first and then discovers it cannot deploy is DOWN; one that "
        "decides first is still serving")


def the_windows_build_does_not_run_before_the_decision() -> None:
    """Refusing or handing off after `npm run build` leaves a still-running
    old backend serving a freshly rebuilt UI it was not built against."""
    t = code(UPDATE_PS1)
    assert at(t, PS_CALL) >= 0 and at(t, PS_CALL) < at(t, "npm run build")


def the_posix_preflight_runs_after_the_pull_and_before_the_stop() -> None:
    t = code(UPDATE_SH)
    pull = at(t, "git pull --ff-only")
    pre = at(t, SH_CALL)
    stop = at(t, 'echo "stopping old backend')
    assert pull >= 0 and pre >= 0 and stop >= 0, (pull, pre, stop)
    assert pull < pre < stop, (pull, pre, stop)


def the_posix_migration_happens_between_the_stop_and_the_start() -> None:
    """A data root may only be converted while nothing is holding it."""
    t = code(UPDATE_SH)
    stop = at(t, 'echo "stopping old backend')
    mig = at(t, 'ORGTREE_MIGRATE=1 "$PY" "$CUT" migrate')
    start = at(t, 'nohup "$PY" "${API_ARGS[@]}"')
    assert stop >= 0 and mig >= 0 and start >= 0, (stop, mig, start)
    assert stop < mig < start, (stop, mig, start)


# ------------------------------------------ §5  the couplings and the loop --

def the_windows_handoff_releases_the_mutex_before_launching() -> None:
    """`cutover_deploy.ps1` takes the SAME `Global\\orgtree-update` mutex with
    WaitOne(0).  Handing off while still holding it means the wrapper exits 3
    two seconds later and the launcher reports the cutover as having died."""
    t = code(UPDATE_PS1)
    rel = at(t, "$mutex.ReleaseMutex(); $mutexHeld = $false")
    spawn = at(t, "tools\\cutover_deploy.py")
    assert rel >= 0, "the handoff no longer releases the mutex explicitly"
    assert spawn >= 0 and rel < spawn, (rel, spawn)


def the_wrapper_stops_its_children_handing_back() -> None:
    """`cutover_deploy.ps1` step 5 runs `update.ps1`, and `update.ps1` hands
    JSON roots to `cutover_deploy.py`.  The cycle is broken by construction,
    not by reasoning about what the root looks like by then."""
    assert "$env:ORGTREE_NO_AUTOCUTOVER = '1'" in code(CUTOVER_PS1), (
        "the wrapper no longer disarms the hand-off in its children")
    assert "$env:ORGTREE_NO_AUTOCUTOVER" in code(UPDATE_PS1), (
        "update.ps1 no longer honours the recursion guard the wrapper sets")
    assert "ORGTREE_NO_AUTOCUTOVER" in code(UPDATE_SH), (
        "update.sh offers no way to opt out of the automatic upgrade")


def the_five_minute_watchdog_never_starts_a_cutover() -> None:
    """`-EnsureUp` exists to get a dead backend serving again in seconds, and
    it fires every five minutes whether or not the last one finished.  Its leg
    brings the install up in the format the root is actually in; the full
    deploy does the upgrade."""
    t = code(UPDATE_PS1)
    ensure = at(t, "} elseif ($EnsureUp) {")
    handoff = at(t, "tools\\cutover_deploy.py")
    assert ensure >= 0 and handoff >= 0, (ensure, handoff)
    assert ensure < handoff, (
        "the -EnsureUp arm no longer precedes the hand-off arm -- check that "
        "the watchdog cannot reach the cutover")
    assert "$env:ORGTREE_STORE = 'json'" in t, (
        "the -EnsureUp arm no longer brings a JSON root up on JSON, so the "
        "install stays DOWN until someone runs a full deploy")


def the_watchdogs_only_disclosure_is_a_file() -> None:
    """`tools/install-autostart.ps1` runs the ensure task under
    `conhost --headless` with NO output redirection, so everything that arm
    prints is lost.  A file in the data root is the only place an operator can
    find out afterwards that their install is still on the old format."""
    assert "--headless" in code(os.path.join(ROOT, "tools",
                                             "install-autostart.ps1"))
    assert "UPGRADE-PENDING.txt" in code(UPDATE_PS1), (
        "the ensure arm no longer leaves anything an operator can find")


def the_deployed_backend_still_never_receives_the_migrate_flag() -> None:
    """UNCHANGED by the 2026-09-04 ruling, and the thing most likely to be
    eroded by it.  The deploy now supplies ORGTREE_MIGRATE on the operator's
    behalf -- but still only into the one child that migrates, never into a
    process that goes on to start a backend.  A backend that can convert a
    root as a side effect of being pointed at it is the 2026-09-03 incident."""
    sh = code(UPDATE_SH)
    assert "export ORGTREE_MIGRATE" not in sh, (
        "update.sh EXPORTS the migrate flag -- the backend it starts at the "
        "bottom of the same script would inherit it")
    for ln in sh.splitlines():
        if "ORGTREE_MIGRATE" not in ln:
            continue
        assert "cutover.py" in ln or '"$CUT"' in ln, (
            "ORGTREE_MIGRATE appears on a line that is not the migrate "
            "command: %s" % ln.strip())
    assert "ORGTREE_MIGRATE" not in code(UPDATE_PS1), (
        "update.ps1 mentions the migrate flag; on Windows it belongs only in "
        "cutover_deploy.ps1's one-shot .cmd file")


# ------------------------- §5b  "no way back" is said, not left to be found --
# An install whose export-verify failed still COMES UP -- there is no validated
# export to roll back to whatever we do, so refusing to start would be an
# outage with nothing bought by it (coordinator ruling, 2026-09-04).  What must
# not happen is that it comes up looking normal.  The state is recorded three
# times; the file in the data root is the only one still there next week, so it
# is the one pinned here.

MARKER = "NO-ROLLBACK-ROUTE.txt"


def both_implementations_record_a_missing_rollback_route() -> None:
    for path, name in ((CUTOVER_PS1, "cutover_deploy.ps1"), (UPDATE_SH, "update.sh")):
        t = code(path)
        assert MARKER in t, (
            "%s no longer writes the no-rollback marker, so an install that "
            "came up after a failed export-verify looks entirely normal" % name)


def the_marker_is_written_where_the_fact_becomes_true() -> None:
    """Written at the export-verify failure, not in the recovery arm that
    happens to bring the install back up: the fact has to survive the recovery
    going wrong, this script dying, or the backend never coming back."""
    t = code(CUTOVER_PS1)
    exp = at(t, 'Say "EXPORT-VERIFY FAILED')
    write = at(t, "Set-NoRollbackMarker ")
    recov = at(t, 'Recover "cutover.py export-verify failed')
    assert exp >= 0 and write >= 0 and recov >= 0, (exp, write, recov)
    assert exp < write < recov, (
        "the marker is no longer written between the failure and the "
        "recovery: %s" % ((exp, write, recov),))


def the_marker_is_cleared_when_an_export_does_verify() -> None:
    """⚠ A marker that can only ever be written stops meaning anything the
    first time it goes stale.  An operator who retried the cutover
    successfully would still find a file telling them they have no way back,
    and would learn to ignore it -- and then it is worth nothing to the
    install that really has none."""
    ps = code(CUTOVER_PS1)
    ok = at(ps, "every org exported and re-read")
    # the LAST call site, because the function's own definition comes first
    call = ps.rfind("Clear-NoRollbackMarker")
    assert ok >= 0 and call >= 0, (ok, call)
    assert ok < call, (
        "cutover_deploy.ps1 clears the marker somewhere other than the "
        "export-verify SUCCESS path, so a stale warning outlives the run "
        "that disproved it")
    sh = code(UPDATE_SH)
    assert 'rm -f "$NO_ROLLBACK"' in sh, "update.sh never clears the marker"


def a_recovered_install_with_no_route_back_is_not_reported_as_plain_recovered() -> None:
    """The green 'RECOVERED: orgtree is UP on SQLite' banner is the only way an
    install comes back after a failed export-verify.  It must not swallow the
    fact that a whole safety net is missing."""
    t = code(CUTOVER_PS1)
    banner = at(t, "RECOVERED: orgtree is UP on SQLite, carrying its orgs.")
    guard = at(t, "if ($script:noRollbackRoute) {")
    assert banner >= 0 and guard >= 0, (banner, guard)
    assert banner < guard, (
        "the no-rollback warning no longer follows the recovered banner")
    assert "$script:rc = 24" in t, (
        "'recovered' and 'recovered with no way back' report the same exit "
        "code, so a caller and a log cannot tell them apart")


def neither_implementation_claims_premigration_is_a_rollback() -> None:
    """The `.json.premigration` files sit right beside the databases and look
    exactly like a backup.  They predate every write since the migration.  An
    operator reading a no-rollback warning is precisely the person about to
    reach for them."""
    for path in (CUTOVER_PS1, UPDATE_SH):
        t = code(path)
        i = t.find(MARKER)
        assert i >= 0
        # ⚠ QUOTES AND COMMAS ARE STRIPPED TOO, NOT JUST WHITESPACE, and that
        # is not cosmetic.  This check first searched for the literal "NOT a
        # rollback" and failed against cutover_deploy.ps1, where the message is
        # a PowerShell string ARRAY and the sentence wraps mid-phrase across two
        # elements: `are NOT a",` / `"  rollback: they predate`.  Collapsing
        # whitespace alone still leaves `a", "rollback` between the words.  The
        # code was right both times and the needle was wrong -- a needle
        # sensitive to how a message happens to be line-wrapped fails for the
        # wrong reason today and passes for the wrong reason tomorrow.
        near = re.sub(r'[",]+', " ",
                      t[max(0, i - 4000):i + 4000])
        near = " ".join(near.split())
        assert "premigration" in near and "NOT a rollback" in near, (
            "%s writes the marker without warning off the premigration files"
            % path)


# ------------------------------------------------------------- §6 controls --

def the_files_under_test_exist_and_are_not_trivial() -> None:
    for p in (PREFLIGHT, UPDATE_PS1, UPDATE_SH, CUTOVER_PS1, STORE_PY):
        assert os.path.getsize(p) > 500, "%s is missing or trivial" % p


def the_comment_stripper_actually_strips() -> None:
    """Every §4/§5 check searches `code()`.  If it stopped removing comment
    lines they would all go back to matching the paragraphs that DESCRIBE the
    behaviour, and none of them could fail."""
    assert code.__doc__
    stripped = code(UPDATE_PS1)
    assert "# ⚠⚠ WHY THIS IS *HERE*" not in stripped, (
        "code() is leaving comments in, so every text check can match a "
        "description of the code instead of the code")
    assert PS_CALL in stripped, "code() has eaten the actual code"


def the_position_finder_can_actually_order_things() -> None:
    """§4 is entirely `a < b`.  If `at()` returned -1 for everything the
    comparisons would still be arithmetic and would still pass."""
    assert at("aXbY", "X") == 1 and at("aXbY", "Y") == 3
    assert at("aXbY", "Z") == -1
    t = code(UPDATE_PS1)
    assert at(t, "git pull --ff-only") > 0 and at(t, PS_CALL) > 0


def the_verdict_check_can_actually_fail() -> None:
    """The strongest control available: feed the tool a root of the OPPOSITE
    shape and prove the exit code moves.  An instrument that reports 'nothing
    found' must prove it can find something."""
    a = run_preflight(make_root("json")).returncode
    b = run_preflight(make_root("sqlite")).returncode
    assert a == MIGRATE and b == PROCEED and a != b, (a, b)


def the_order_pin_would_notice_a_reordering() -> None:
    """A control on §4 itself: reorder a copy of the real file and prove the
    assertion the checks make actually goes false.  Pinning positions is only
    worth anything if a move is detectable."""
    t = code(UPDATE_PS1)
    pull, pre = at(t, "git pull --ff-only"), at(t, PS_CALL)
    assert pull < pre
    swapped = t[:pull] + PS_CALL + t[pull:]
    assert not (at(swapped, "git pull --ff-only")
                < at(swapped, PS_CALL)), (
        "moving the pre-flight above the pull did NOT break the ordering "
        "assertion, so §4 cannot fail")


print("== §1  the five verdicts, by execution ==")
check("JSON root + SQLite build -> MIGRATE",
      a_json_root_under_a_sqlite_build_says_migrate)
check("migrated root + SQLite build -> PROCEED",
      a_migrated_root_under_a_sqlite_build_proceeds)
check("empty root -> PROCEED (a fresh install deploys in silence)",
      an_empty_root_proceeds)
check("half-migrated root -> MIXED", a_half_migrated_root_says_mixed)
check("SQLite root + JSON pin -> MISMATCH",
      a_sqlite_root_under_a_json_pin_says_mismatch)
check("JSON root + JSON pin -> PROCEED (the guard must not trip)",
      a_json_root_under_a_json_pin_proceeds)
check("an unusable ORGTREE_STORE is named, not left to the backend",
      an_unusable_store_value_is_named_rather_than_left_to_the_backend)

print("\n== §2  it degrades honestly ==")
check("an unimportable store never refuses a deploy",
      a_repo_whose_store_cannot_be_imported_never_refuses)
check("the degraded path still prints the whole recipe",
      the_degraded_path_still_prints_the_whole_recipe)
check("another checkout's store is not reported as this one's",
      another_checkouts_store_is_not_reported_as_this_ones)

print("\n== §3  the fallback agrees with what decides ==")
check("the degraded scan matches store.pending_migrations/active_databases",
      the_fallback_scan_agrees_with_the_functions_that_actually_decide)
check("the fixtures carry what a real migration leaves behind",
      the_root_shapes_are_the_shapes_a_real_migration_leaves)

print("\n== §4  WHERE the call site sits ==")
check("windows: after the pull, before the stop",
      the_windows_preflight_runs_after_the_pull_and_before_the_stop)
check("windows: before the UI build",
      the_windows_build_does_not_run_before_the_decision)
check("posix: after the pull, before the stop",
      the_posix_preflight_runs_after_the_pull_and_before_the_stop)
check("posix: the migration is between the stop and the start",
      the_posix_migration_happens_between_the_stop_and_the_start)

print("\n== §5  the couplings and the loop ==")
check("the handoff releases the mutex before launching the wrapper",
      the_windows_handoff_releases_the_mutex_before_launching)
check("the wrapper stops its children handing back",
      the_wrapper_stops_its_children_handing_back)
check("the 5-minute watchdog never starts a cutover",
      the_five_minute_watchdog_never_starts_a_cutover)
check("the watchdog's only disclosure is a file",
      the_watchdogs_only_disclosure_is_a_file)
check("the deployed backend still never receives ORGTREE_MIGRATE",
      the_deployed_backend_still_never_receives_the_migrate_flag)

print("\n== §5b  a missing rollback route is said, not left to be found ==")
check("both implementations record it",
      both_implementations_record_a_missing_rollback_route)
check("it is written where the fact becomes true",
      the_marker_is_written_where_the_fact_becomes_true)
check("it is cleared when an export does verify",
      the_marker_is_cleared_when_an_export_does_verify)
check("a recovered install with no way back says so",
      a_recovered_install_with_no_route_back_is_not_reported_as_plain_recovered)
check("neither claims .premigration is a rollback",
      neither_implementation_claims_premigration_is_a_rollback)

print("\n== §6  controls ==")
check("the files under test exist", the_files_under_test_exist_and_are_not_trivial)
check("the comment stripper actually strips", the_comment_stripper_actually_strips)
check("the position finder can actually order things",
      the_position_finder_can_actually_order_things)
check("the verdict check can actually fail", the_verdict_check_can_actually_fail)
check("the order pin would notice a reordering",
      the_order_pin_would_notice_a_reordering)

shutil.rmtree(TMP, ignore_errors=True)
print("\n%s" % ("=" * 60))
if FAILED:
    print("FAILED %d / %d" % (len(FAILED), PASSED + len(FAILED)))
    for f in FAILED:
        print("  - %s" % f)
    sys.exit(1)
print("all %d checks passed" % PASSED)
