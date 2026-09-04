"""The detached cutover wrapper: `tools/cutover_deploy.py` + `.ps1`.

WHAT THIS SUITE IS AND IS NOT.  The wrapper's behaviour was established by
EXECUTION, not by this file -- a drill matrix on throwaway roots that stopped a
real backend, migrated a copy of the real orgs, forced each failure in turn and
watched the recovery bring a backend back up (2026-09-04, sqlite-review).  A
PowerShell script cannot be unit-tested from here, so what this suite does is
narrower and worth stating plainly: it PINS the handful of properties that were
found the hard way and that a later edit would silently undo.  Every check
below corresponds to a defect that actually existed in this wrapper during the
drills, or to a coupling with another file that has no other guard.

    §1  the detach is the shape supervisor.py already proved
    §2  the migrate flag never exists in a process that outlives the migration
    §3  the drill switches cannot reach a real run
    §4  the two couplings nothing else checks: ORGTREE_DATA and ORGTREE_PORT
    §5  the pipe that hung it, and the BOM that garbled it
    §6  controls -- what would make the above vacuous

    python backend/tests/test_cutover_deploy.py
"""

from __future__ import annotations

import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
PS1 = os.path.join(ROOT, "tools", "cutover_deploy.ps1")
PY = os.path.join(ROOT, "tools", "cutover_deploy.py")
SUPERVISOR = os.path.join(ROOT, "backend", "orgtree", "supervisor.py")
UPDATE = os.path.join(ROOT, "update.ps1")

NL = chr(10)

FAILED: list[str] = []
PASSED = 0


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


def code(p: str) -> str:
    """`p` with every whole-line comment removed.

    ⚠ NOT A TIDINESS MEASURE.  These checks are text searches, and the file
    they search DESCRIBES ITS OWN BUGS in comments -- so a search for the line
    that fixes a bug also matches the comment explaining it.  MEASURED while
    building this suite: commenting out `$env:ORGTREE_DATA = $Root` -- exactly
    the regression the check exists to catch -- left the check GREEN, because
    the disabled line still read as a match.  A guard that cannot tell live
    code from a description of it is a guard that cannot fail."""
    return "|".join(ln for ln in read(p).splitlines()
                    if not ln.lstrip().startswith("#"))


def call_site(p: str, needle: str) -> bool:
    """Is `needle` in `p` OUTSIDE a comment?

    ⚠ The same trap as `code()`, hit a THIRD time and therefore given its own
    name.  `tools/cutover_deploy.py` carries a comment reading
    `# supervisor.py _detached_spawn: CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`
    and the check for that flag pair matched it -- so deleting the flags from
    the actual Popen call left the check GREEN.  Assert against the call, not
    against the file's description of the call."""
    return needle in code(p)


# ------------------------------------------------ §1  the detach is copied --

def _flags_in(text: str) -> set[str]:
    """Every CREATE_* creation flag, as lowercase hex, in `text`."""
    return {m.lower() for m in re.findall(r"0x0[0-9a-fA-F]{7}", text)}


def the_detach_flags_are_the_ones_supervisor_proved() -> None:
    """`_detached_spawn` chose CREATE_NO_WINDOW over DETACHED_PROCESS after
    measuring that DETACHED_PROCESS loses every line the child writes -- and
    for this wrapper the log IS the deliverable, because the agent that asked
    for the cutover is dead while it runs.  Two files now carry the same two
    constants; pin them together so they cannot drift into one file being
    silently right and the other silently mute."""
    sup = read(SUPERVISOR)
    i = sup.index("def _detached_spawn")
    j = sup.index("def others_working", i)
    want = _flags_in(sup[i:j])
    assert want, "no creation flags found in supervisor._detached_spawn"
    got = _flags_in(read(PY))
    assert want <= got, (
        "tools/cutover_deploy.py does not use supervisor's creation flags: "
        "supervisor has %s, the launcher has %s" % (sorted(want), sorted(got)))


def the_launcher_does_not_reach_for_detached_process() -> None:
    """0x00000008 is DETACHED_PROCESS.  It is the obvious flag for a thing
    called a detached launcher and it is the wrong one: it costs the log and
    buys nothing, because a Windows child already outlives its parent."""
    assert "0x00000008" not in read(PY), (
        "the launcher uses DETACHED_PROCESS, which silently discards every "
        "line the cutover writes")


def the_child_is_spawned_with_a_file_not_a_pipe() -> None:
    """The launcher hands the child a real file handle.  A pipe would have to
    be drained by a process that is about to be killed."""
    py = read(PY)
    assert "stdout=lf" in py and "stderr=subprocess.STDOUT" in py, (
        "the launcher no longer redirects the cutover's output into the log "
        "file")


# --------------------------------- §2  the migrate flag is never inherited --

def the_wrapper_never_sets_the_migrate_flag_in_its_own_process() -> None:
    """ORGTREE_MIGRATE authorises converting a data root.  Set it in the
    wrapper's own environment and every later child inherits it -- including
    the backend a recovery relaunches, which would then migrate on startup.
    It lives in a one-shot .cmd file instead, so a wrapper that dies between
    setting and unsetting cannot exist."""
    ps1 = code(PS1)
    for bad in ("$env:ORGTREE_MIGRATE", "${env:ORGTREE_MIGRATE}"):
        assert bad not in ps1, (
            "the wrapper sets ORGTREE_MIGRATE in a process that outlives the "
            "migration (%s)" % bad)
    assert "set ORGTREE_MIGRATE=1" in ps1, (
        "the wrapper no longer authorises the migration at all")


def the_deployed_backend_is_never_started_with_the_migrate_flag() -> None:
    """The flag must not appear anywhere near the relaunch paths."""
    ps1 = read(PS1)
    i = ps1.index("function Invoke-EnsureUp")
    j = ps1.index("function Recover-PreMigration")
    assert "ORGTREE_MIGRATE" not in ps1[i:j], (
        "the relaunch path mentions ORGTREE_MIGRATE")


# ------------------------------------- §3  drill switches cannot ship live --

def every_drill_switch_is_gated_on_drill_mode() -> None:
    """A drill switch that works on a real run is a way to skip the migration,
    or the deploy, on the live install."""
    ps1 = code(PS1)
    switches = set(re.findall(r"\[switch\]\$(Drill\w+)", ps1))
    assert switches, "no drill switches found at all -- has the param block moved?"
    # `code()` joins lines with "|", so the gate's line continuation shows up
    # as a separator rather than a newline
    gate = re.search(r"if \(\((.*?)\)[\s`|]*-and -not \$DrillNoDeploy\)",
                     ps1, re.S)
    assert gate, "the drill-switch gate is gone"
    for s in switches - {"DrillNoDeploy"}:
        assert s in gate.group(1), (
            "-%s is not gated on -DrillNoDeploy: it would be honoured on a "
            "real cutover" % s)


def drill_mode_refuses_the_default_data_root() -> None:
    """The drill exists so the live install is not the thing being
    experimented on."""
    ps1 = read(PS1)
    i = ps1.index("if ($DrillNoDeploy) {")
    window = ps1[i:i + 1200]
    assert "USERPROFILE" in window and "exit 4" in window, (
        "drill mode no longer refuses the default data root")


def drill_mode_refuses_a_root_with_no_port_of_its_own() -> None:
    """A throwaway root with no .port falls back to 7360 -- the LIVE install's
    port -- and step 2 then stops the live backend.  The port default is a
    property of the code, not of the root being drilled."""
    ps1 = code(PS1)
    assert re.search(r"drill root .* has no \.port", ps1), (
        "a drill root without its own .port no longer refuses, so a drill "
        "would stop the live backend")


# ---------------------------------- §4  the couplings nothing else checks --

def the_data_root_is_pinned_for_every_child() -> None:
    """update.ps1 resolves the data root from $env:ORGTREE_DATA and falls back
    to the LIVE one.  Without this line every recovery -- and the deploy --
    operates on the live install regardless of the -Root it was handed.  On
    the real cutover -Root is that same path, so this bug is invisible exactly
    where it does no harm and fatal everywhere else."""
    ps1 = code(PS1)
    assert re.search(r"\$env:ORGTREE_DATA\s*=\s*\$Root", ps1), (
        "the wrapper no longer pins ORGTREE_DATA, so update.ps1 will use the "
        "live root whatever -Root said")


def the_port_the_backend_binds_matches_the_port_that_is_checked() -> None:
    """update.ps1 reads <root>\\.port to decide what to stop and what to
    health-check, but the backend it starts binds ORGTREE_PORT (api.py's
    default 7360).  Nothing connects the two; while a root's .port says 7360
    they agree by coincidence."""
    ps1 = code(PS1)
    assert re.search(r"\$env:ORGTREE_PORT\s*=\s*\$script:port", ps1), (
        "the wrapper no longer forces ORGTREE_PORT to the root's own port")
    api = read(os.path.join(ROOT, "backend", "orgtree", "api.py"))
    assert 'os.environ.get("ORGTREE_PORT"' in api, (
        "api.py no longer reads ORGTREE_PORT -- the coupling this pins has "
        "moved and the wrapper's line may now be inert")


def ensure_up_does_not_pull() -> None:
    """Both recoveries relaunch with `update.ps1 -EnsureUp`, on the assumption
    that it is relaunch-only.  If the pull ever escapes its guard, a recovery
    would deploy new code in the middle of a failed cutover."""
    up = read(UPDATE)
    guard = up.index("if (-not $EnsureUp) {")
    pull = up.index("git pull --ff-only")
    assert guard < pull, (
        "`git pull --ff-only` is no longer inside an `if (-not $EnsureUp)` "
        "block -- -EnsureUp is not relaunch-only any more and the recovery "
        "paths would deploy")


def the_mutex_is_the_same_one_update_ps1_takes() -> None:
    """Holding it is what stops the 5-minute `orgtree-ensure` task relaunching
    a backend into the middle of the migration.  A different name would read
    identically and protect nothing."""
    name = "Global\\orgtree-update"
    assert name in code(UPDATE), "update.ps1's mutex name has changed"
    assert name in code(PS1), (
        "the wrapper does not take the mutex update.ps1 takes, so the ensure "
        "task can start a backend mid-migration")


# ------------------------- §4b  no launch here allocates a new console ----

def no_launch_in_the_wrapper_allocates_a_visible_console() -> None:
    """MEASURED by window-cert, 2026-09-04, through its window hook: on this
    machine Windows Terminal is the default terminal application, so anything
    that allocates a NEW console gets a VISIBLE Windows-Terminal-hosted window
    on the user's desktop.  The trigger is the console allocation, not the
    command -- `cmd /c timeout` was tested and cleared.

        Start-Process (default)      new console, WT-hosted   POPS
        Start-Process -WindowStyle Hidden   new console       hidden
        Start-Process -NoNewWindow          none, inherited   hidden

    A bare `Start-Process` anywhere in this wrapper therefore paints a window
    during a deploy -- and the deploy runs unattended, so nobody would see it
    happen and nobody could fix it afterwards.  Every launch is checked, not
    just the top-level detach."""
    body = code(PS1)
    calls = [m for m in re.finditer(r"Start-Process\b", body)]
    assert calls, "no Start-Process at all -- has Run been rewritten?"
    for m in calls:
        window = body[m.start():m.start() + 400]
        # `code()` joined lines with "|", so a continuation is still in reach
        assert ("-NoNewWindow" in window
                or "-WindowStyle Hidden" in window), (
            "a Start-Process in the wrapper allocates a new console and will "
            "paint a Windows Terminal window on the user's desktop mid-deploy:"
            " ...%s..." % window[:120])


def the_launcher_allocates_no_console_either() -> None:
    """The top-level detach is the one launch that is not a Start-Process.
    CREATE_NO_WINDOW is what makes it windowless AND keeps its output; the
    console-allocating alternative would both pop and, as DETACHED_PROCESS,
    lose the log."""
    assert call_site(PY, "creationflags=CREATE_NO_WINDOW "
                         "| CREATE_NEW_PROCESS_GROUP"), (
        "the launcher no longer spawns the cutover with CREATE_NO_WINDOW at "
        "the actual call site, so it may allocate a console and paint a "
        "window (a comment naming the flags is not a spawn using them)")


def the_wrapper_registers_no_scheduled_task() -> None:
    """It has no business registering one, and a task is where the two really
    expensive mistakes live: a launch shape that pops a console, and an S4U
    principal, which looks like the tidy choice for unattended work and
    silently lands the relaunched backend in session 0 -- away from the
    profile whose ~/.claude credentials every agent turn needs.  This wrapper
    detaches with a process handle instead and touches no task at all."""
    for src in (code(PS1), code(PY)):
        for bad in ("Register-ScheduledTask", "New-ScheduledTask", "schtasks",
                    "-LogonType", "S4U"):
            assert bad not in src, (
                "the wrapper now touches Task Scheduler (%s) -- the principal "
                "must stay InteractiveToken and the action must be hosted the "
                "way tools/install-autostart.ps1 hosts it" % bad)


# ------------------------------------ §5  the pipe, and the missing BOM ----

def no_child_output_goes_through_a_pipeline() -> None:
    """MEASURED 2026-09-04: written as `& powershell ... | Out-Host`, the
    wrapper hung forever after a successful relaunch.  update.ps1 starts the
    backend with Start-Process -Redirect..., which forces handle inheritance,
    so the long-lived backend inherits the pipeline's write handle; update.ps1
    exits, the pipe never closes, and the wrapper waits on a dead script --
    never running its final health check and never reaching either recovery."""
    # comments in the wrapper NAME the bug, so only real code is scanned --
    # a check that reads its own warning label and fires is worse than none
    ps1 = read(PS1)
    assert "Out-Host" not in code(PS1), (
        "a child's output is being piped again; the backend it starts will "
        "hold the pipe open and the wrapper will never return")
    assert "-Wait" not in code(PS1).split("function Run(")[1].split("|}")[0], (
        "Run uses Start-Process -Wait, which is documented to wait for the "
        "process AND ITS DESCENDANTS -- including the backend")


def the_script_keeps_its_utf8_bom() -> None:
    """PowerShell 5.1 reads a BOM-less script as the ANSI code page, so every
    non-ASCII character -- the warning markers an operator reads at 3am --
    reaches the log double-encoded.  Silent, and only visible in the artifact
    nobody looks at until something has gone wrong."""
    with open(PS1, "rb") as f:
        assert f.read(3) == b"\xef\xbb\xbf", (
            "tools/cutover_deploy.ps1 has lost its UTF-8 BOM; PowerShell 5.1 "
            "will mangle every marker in the operator log")


def the_exit_code_is_read_the_way_that_actually_works() -> None:
    """`Start-Process -PassThru` returns an object whose `.ExitCode` is $null
    after WaitForExit unless the handle was cached first.  Without the
    `$p.Handle` touch the wrapper scores every successful step as a failure --
    safely, but on every run."""
    body = code(PS1).split("function Run(")[1].split("|}")[0]
    assert "$p.Handle" in body, (
        "Run no longer touches $p.Handle, so $p.ExitCode comes back $null and "
        "every step reads as a failure")


# ------------------------------------------------------------- §6 controls --

def the_files_under_test_exist_and_are_not_empty() -> None:
    for p in (PS1, PY, SUPERVISOR, UPDATE):
        assert os.path.getsize(p) > 200, "%s is missing or trivial" % p


def the_flag_scanner_can_actually_find_flags() -> None:
    """§1 would pass vacuously if `_flags_in` matched nothing."""
    assert _flags_in("creationflags = 0x08000000 | 0x00000200") == {
        "0x08000000", "0x00000200"}
    assert _flags_in("nothing here") == set()


def the_comment_stripper_actually_strips() -> None:
    """Every code-shape check searches `code()`.  If it stopped removing
    comment lines, all of them would go back to matching disabled code and
    would stop being able to fail -- which is how two of them were caught."""
    live = "|".join(ln for ln in ("live" + NL + "# dead").splitlines()
                    if not ln.lstrip().startswith("#"))
    assert live == "live", live
    stripped = code(PS1)
    assert "# ⚠" not in stripped and "# ----" not in stripped, (
        "code() is leaving comment lines in, so every text check can match "
        "disabled code")
    assert "$script:migrated" in stripped, "code() has eaten the actual code"


def the_switch_scanner_can_actually_find_switches() -> None:
    """§3 would pass vacuously if the param block stopped matching."""
    found = set(re.findall(r"\[switch\]\$(Drill\w+)", code(PS1)))
    assert len(found) >= 4, (
        "only found %s -- the switch scanner has stopped seeing the param "
        "block, so §3 proves nothing" % sorted(found))


print("== §1  the detach is the shape supervisor.py proved ==")
check("the creation flags are supervisor's own",
      the_detach_flags_are_the_ones_supervisor_proved)
check("it does not reach for DETACHED_PROCESS",
      the_launcher_does_not_reach_for_detached_process)
check("the child writes to a file, not a pipe",
      the_child_is_spawned_with_a_file_not_a_pipe)

print("\n== §2  the migrate flag is never inherited ==")
check("ORGTREE_MIGRATE never lives in the wrapper's own process",
      the_wrapper_never_sets_the_migrate_flag_in_its_own_process)
check("no relaunch path mentions it",
      the_deployed_backend_is_never_started_with_the_migrate_flag)

print("\n== §3  drill switches cannot reach a real run ==")
check("every drill switch is gated on -DrillNoDeploy",
      every_drill_switch_is_gated_on_drill_mode)
check("drill mode refuses the default data root",
      drill_mode_refuses_the_default_data_root)
check("drill mode refuses a root with no port of its own",
      drill_mode_refuses_a_root_with_no_port_of_its_own)

print("\n== §4  the couplings nothing else checks ==")
check("ORGTREE_DATA is pinned for every child",
      the_data_root_is_pinned_for_every_child)
check("the bound port and the checked port are the same port",
      the_port_the_backend_binds_matches_the_port_that_is_checked)
check("-EnsureUp does not pull", ensure_up_does_not_pull)
check("the mutex is the one update.ps1 takes",
      the_mutex_is_the_same_one_update_ps1_takes)

print("\n== §4b  no launch allocates a visible console ==")
check("no Start-Process in the wrapper allocates a new console",
      no_launch_in_the_wrapper_allocates_a_visible_console)
check("the launcher allocates no console either",
      the_launcher_allocates_no_console_either)
check("the wrapper registers no scheduled task",
      the_wrapper_registers_no_scheduled_task)

print("\n== §5  the pipe that hung it, the BOM that garbled it ==")
check("no child output goes through a pipeline",
      no_child_output_goes_through_a_pipeline)
check("the script keeps its UTF-8 BOM", the_script_keeps_its_utf8_bom)
check("the exit code is read the way that works",
      the_exit_code_is_read_the_way_that_actually_works)

print("\n== §6  controls ==")
check("the files under test exist", the_files_under_test_exist_and_are_not_empty)
check("the flag scanner can find flags", the_flag_scanner_can_actually_find_flags)
check("the switch scanner can find switches",
      the_switch_scanner_can_actually_find_switches)
check("the comment stripper actually strips",
      the_comment_stripper_actually_strips)

print("\n%s" % ("=" * 60))
if FAILED:
    print("FAILED %d / %d" % (len(FAILED), PASSED + len(FAILED)))
    for f in FAILED:
        print("  - %s" % f)
    sys.exit(1)
print("all %d checks passed" % PASSED)
