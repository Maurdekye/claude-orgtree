# orgtree update script -- pull the latest changes and redeploy.
#   powershell -ExecutionPolicy Bypass -File update.ps1
#   powershell -ExecutionPolicy Bypass -File update.ps1 -ExposeAdmin   # DANGEROUS
#   $env:ORGTREE_EXPOSE_ADMIN='1'                                    # same, for services
# (or run update.cmd). Works in Windows PowerShell 5.1.
#
# Steps: git pull -> npm install + build the UI -> pip install -> restart the
# backend (which serves the built UI) -> health-check.
#
# ORGTREE_EXPOSE_ADMIN=1 binds the ADMIN api to 0.0.0.0 instead of loopback
# (-ExposeAdmin is a convenience switch that sets it). The admin
# api has no password, no token and no login -- reaching the port IS the
# credential -- so this hands anyone who finds it full control of every org and
# of any folder an agent has been granted. It is a switch you type, never a
# setting: nothing in the app, the org docs or the environment can turn it on,
# which means no agent can either. To share ONE org with someone, make it a
# kiosk instead (secret URL, hard limits) and run expose.ps1.
param(
    [switch]$ExposeAdmin,
    # -EnsureUp (F-06 autostart, user-ruled): the crash-restart half. The
    # at-logon full deploy detaches and exits, so Task Scheduler's own
    # restart-on-failure never sees the backend die -- a 5-minute repeating
    # trigger runs THIS mode instead: listener alive -> silent no-op; dead ->
    # relaunch only (no pull, no build, no pip). install-autostart.ps1
    # registers both triggers.
    [switch]$EnsureUp,
    # -OnlyIfBehind (peer report 2026-08-09): exit BEFORE the rebuild+restart
    # when the pull advanced nothing.
    # ⚠ NO CALLER IN THIS REPO PASSES THIS ANY MORE (D-142, 2026-08-21).
    # orgtree_self_restart used to, and that made the tool unable to deploy a
    # commit made on this machine -- silently. See the branch that reads it.
    # Kept declared for operators and scheduled "only if there is something
    # new" jobs: it is four lines, and PowerShell hard-errors on an undeclared
    # switch, so removing it would break such a caller loudly for no gain.
    [switch]$OnlyIfBehind,
    # -AllowDirty (redteam hazard flag 2026-08-11): override the dirty-tree
    # refusal and deploy the working tree exactly as it stands, uncommitted
    # edits included. For the operator who KNOWS the dirt is theirs and wants
    # it shipped; never passed by the self-restart path.
    [switch]$AllowDirty
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---- AM I ACTUALLY STANDING IN THE ORGTREE CHECKOUT? ----------------------
# $root is derived from the script's own location and nothing checked that it
# landed anywhere real (peer report relayed by the coordinator 2026-08-27: two
# of their probes had been resolving "repo root" to a directory holding no
# project file, had not run for DAYS, and the harness then reported the fault
# against the healthy component -- so the reader went and investigated the
# wrong thing).
# This script pulls, force-kills whatever holds a port, rebuilds a tree and
# restarts a service. Pointed at the wrong directory it does all of that to
# the WRONG tree, and the first thing that would notice is an incidental
# failure much later, phrased as a git or pip problem rather than as a root
# problem. Worse, a wrong root that happens to BE a git checkout with an
# upstream would pull and build that repo and report success.
# So: anchor it, and NAME the directory when it is wrong. The failure being
# guarded against here is precisely a message that sends the reader elsewhere.
if (-not $root) {
    Write-Host "REFUSING to deploy: this script could not determine its own directory (`$MyInvocation.MyCommand.Path was empty -- run it with -File, not piped to powershell -Command). Nothing was pulled, rebuilt or restarted." -ForegroundColor Red
    exit 1
}
# Scoped to what the MODE actually uses, deliberately. -EnsureUp is the
# 5-minute crash-restart net: it builds nothing and only relaunches the
# backend, so gating it on frontend/requirements files would newly refuse to
# recover a downed backend over a file that leg never touches -- a guard a
# correct run can trip is worse than no guard (the same argument the
# dirty-tree pass-list below is built on).
$anchors = if ($EnsureUp) { , 'backend\orgtree\api.py' }
           else { 'requirements.txt', 'backend\orgtree\api.py', 'frontend\package.json' }
foreach ($anchor in $anchors) {
    if (-not (Test-Path (Join-Path $root $anchor))) {
        Write-Host "REFUSING to deploy: resolved the repo root to" -ForegroundColor Red
        Write-Host "    $root" -ForegroundColor Red
        Write-Host "and that directory has no '$anchor', so it is not an orgtree checkout." -ForegroundColor Red
        Write-Host "Nothing was pulled, rebuilt or restarted." -ForegroundColor Red
        exit 1
    }
}
Set-Location $root

# ---- READING THE TREE MUST BE ABLE TO FAIL --------------------------------
# `git status --porcelain` returns an EMPTY string two ways: the tree is clean,
# or git could not read it at all. Every dirty-tree guard below tests the
# capture for emptiness, so the second case was read as the first -- the guard
# did not fire, printed nothing, and the deploy walked straight past it.
# Measured 2026-08-27 (ps-guards audit): in a repo git refuses to open, the
# capture is '' with exit 128, `if ($dirty.Trim())` is false, and execution
# continues to the pull. An unreadable tree is not a clean tree, so refuse.
# There is no `2>` here on purpose: git's own message is the operator's only
# clue, and under PS 5.1 redirecting a native stderr stream turns it into a
# terminating NativeCommandError (see the esbuild note further down).
function Get-Porcelain {
    param([string[]]$PathSpec = @())
    $out = (git status --porcelain @PathSpec) | Out-String
    if ($LASTEXITCODE -ne 0) {
        Write-Host "git status FAILED (exit $LASTEXITCODE) -- the working tree could not be read, so the dirty-tree guards cannot run and would pass on the empty result. REFUSING: an unreadable tree is not a clean tree. Nothing was rebuilt and nothing was restarted." -ForegroundColor Red
        exit 1
    }
    return $out
}

if ($EnsureUp) {
    $dr = $env:ORGTREE_DATA
    if (-not $dr) { $dr = Join-Path $env:USERPROFILE 'orgtree' }
    $pt = '7360'
    $pf = Join-Path $dr '.port'
    if (Test-Path $pf) { $pt = (Get-Content $pf -Raw).Trim() }
    $alive = Get-NetTCPConnection -LocalPort ([int]$pt) -State Listen -ErrorAction SilentlyContinue
    if ($alive) { exit 0 }          # up -- the ensure is a no-op
    Write-Host "== orgtree ensure-up: backend is DOWN, relaunching =="
}

# ---- ONE DEPLOY AT A TIME -------------------------------------------------
# Nothing serialized these until 2026-08-09 (peer question, neoja: "is
# CONCURRENT update.ps1 safe?"). It is not: two runs race on the git index,
# on npm's node_modules, and on stopping/starting the same port. Task
# Scheduler's -MultipleInstances IgnoreNew only stops a task racing ITSELF —
# it says nothing about the logon deploy overlapping the 5-minute watchdog,
# an agent's orgtree_self_restart, or an operator running this by hand.
# A named system mutex, because the racers are separate processes and may be
# separate sessions. Held for the whole run; released when the process exits
# however it exits.
$mutex = New-Object System.Threading.Mutex($false, 'Global\orgtree-update')
$mutexHeld = $mutex.WaitOne(0)
if (-not $mutexHeld) {
    Write-Host "another orgtree update is already running -- this one exits rather than racing it on git, npm and the port." -ForegroundColor Yellow
    exit 0
}
# The release must be EXPLICIT (leak postmortem 2026-08-20): "released when the
# process exits" is only true when the script IS the process (-File, update.cmd,
# the scheduled tasks). Run as `.\update.ps1` in an interactive shell, every
# `exit` above and below ends the SCRIPT while the shell keeps the acquired
# mutex alive indefinitely -- and every later deploy, including the 5-minute
# -EnsureUp relaunch net, refuses "already running" against a deploy that is
# not running. try/finally because `exit` and EAP='Stop' errors both unwind
# through finally; the body keeps its original indentation on purpose.
try {

$before = (git rev-parse --short HEAD).Trim()
if (-not $EnsureUp) {
Write-Host "== orgtree update (currently $before) =="

# -- 1 - pull ---------------------------------------------------------------
# A DIRTY TREE is reported before the pull, always (peer report 2026-08-09,
# neoja): their self-update restarted every org and advanced nothing, and the
# log said nothing at all about why. Whatever stopped that particular pull,
# an operator reading a log has to be able to SEE the working-tree state --
# `--ff-only` refuses on some dirt and sails past the rest, and "which was
# it?" should never require going to the machine to find out.
$dirty = Get-Porcelain
if ($dirty.Trim()) {
    Write-Host "-- working tree is DIRTY (the pull may refuse):" -ForegroundColor Yellow
    Write-Host $dirty.TrimEnd()
    # ⚠ REFUSE, don't just report (redteam hazard flag 2026-08-11): this
    # script builds the WORKING TREE, not HEAD. A deploy over someone's
    # half-finished edits ships a backend that exists in no commit, and the
    # only symptom afterwards is behaviour nobody can reproduce from the
    # repo. Printing the dirt (2026-08-09) was necessary and not sufficient:
    # the operator reading it is usually not the one who made the edits.
    # Doc-only dirt (docs/, *.md) is the curator's normal working state and
    # builds nothing, so it passes. So does dirt THE BUILD ITSELF WRITES
    # (external report via redteam 2026-08-12): some npm versions recompute
    # frontend/package-lock.json on every install, and that write lands
    # AFTER this guard runs — so deploy N's install would trip deploy N+1's
    # guard forever, training operators to reach for -AllowDirty. A guard a
    # normal, correct deploy can trip is worse than no guard.
    $building = @($dirty.TrimEnd() -split "`r?`n" | Where-Object {
        $p = $_.Substring(3)
        ($p -notmatch '^docs/') -and ($p -notmatch '\.md$') -and
        ($p -notmatch '^frontend/package-lock\.json$')
    })
    if ($building.Count -gt 0 -and -not $AllowDirty) {
        Write-Host "REFUSING to deploy: uncommitted changes in files this build would ship:" -ForegroundColor Red
        $building | ForEach-Object { Write-Host "    $_" }
        Write-Host "Commit or revert them first -- or pass -AllowDirty to ship the tree exactly as it stands." -ForegroundColor Red
        exit 1
    }
}
# The LOCKFILE leg of the guard (redteam objection to the 4b2729e pass-list):
# a lockfile is the ONE pass-listed file that changes what the build ships, so
# its exemption is verified rather than trusted. Snapshot its dirty hash now;
# after `npm install` runs, dirt the install did not itself reproduce -- a
# hand edit npm rewrites -- refuses BEFORE the restart (nothing has shipped
# yet). Residual, on the record: a hand edit npm reproduces verbatim passes
# both this and any before/after shape; package.json edits (the way dependency
# changes actually arrive) are caught by the main guard above.
$lockPath = Join-Path $root 'frontend\package-lock.json'
$lockDirtyBefore = (Get-Porcelain @('--', 'frontend/package-lock.json')).Trim()
$lockHashBefore = if ($lockDirtyBefore) { (Get-FileHash $lockPath -Algorithm SHA256).Hash } else { $null }
git pull --ff-only
if ($LASTEXITCODE -ne 0) {
    Write-Host "git pull FAILED (exit $LASTEXITCODE) -- resolve manually. Nothing was rebuilt and nothing was restarted." -ForegroundColor Red
    exit 1
}
}
$after = (git rev-parse --short HEAD).Trim()
if (-not $EnsureUp) {
if ($after -eq $before) {
    # ⚠ -OnlyIfBehind is an OPT-IN for callers who genuinely only want new
    # remote code. It is NOT the self-restart's flag any more (D-142,
    # 2026-08-21). It used to be, and the half of the comment below that was
    # always true is exactly what broke it: a deploy of the commit you just
    # made locally NEVER moves HEAD during the pull, so "HEAD advanced" is not
    # a test for "is there anything to ship". The agent tool asks for the
    # repo's current state now and passes nothing; an operator deploy always
    # did. The flag survives for a scheduled job that wants the old meaning
    # and is willing to accept that a local commit will not deploy under it.
    if ($OnlyIfBehind) {
        Write-Host "already up to date ($after) -- NOT restarting: a self-update with nothing to deploy would cut every org's turn for no gain"
        exit 0
    }
    Write-Host "already up to date ($after) -- redeploying anyway"
} else {
    Write-Host "updated $before -> $after"
    git --no-pager log --oneline "$before..$after"
}
}

# -- 1b - the interpreter ---------------------------------------------------
# Run from a VIRTUALENV by default (repo-local .venv), created on first use.
# Until now orgtree installed into whatever `python` was on PATH -- a
# system-wide interpreter shared with every other project, which makes the
# dependency set unknowable. That is the exact condition behind the
# missing-websockets bug: the app worked on the dev box and not elsewhere
# because this box had the library for unrelated reasons. A venv makes "what is
# installed" equal to "what requirements.txt says".
#
# Escape hatches, in precedence order:
#   $env:ORGTREE_PYTHON = 'C:\path\python.exe'   use exactly that, no venv logic
#   $env:ORGTREE_NO_VENV = '1'                    stay on the system interpreter
# A venv that cannot be created warns and falls back rather than breaking a
# deployment that was working a minute ago.
$venvDir = Join-Path $root '.venv'
$venvPy = Join-Path $venvDir 'Scripts\python.exe'
if ($env:ORGTREE_PYTHON) {
    $py = $env:ORGTREE_PYTHON
} elseif ($env:ORGTREE_NO_VENV -eq '1') {
    $py = 'python'
} elseif (Test-Path $venvPy) {
    $py = $venvPy
} else {
    Write-Host "creating the virtualenv at .venv (first run) ..." -ForegroundColor Yellow
    python -m venv $venvDir
    if ($LASTEXITCODE -eq 0 -and (Test-Path $venvPy)) {
        $py = $venvPy
    } else {
        Write-Host "could not create .venv -- falling back to the system interpreter" -ForegroundColor Yellow
        Write-Host "(set ORGTREE_NO_VENV=1 to silence this)" -ForegroundColor Yellow
        if (Test-Path $venvDir) { Remove-Item -Recurse -Force $venvDir -ErrorAction SilentlyContinue }
        $py = 'python'
    }
}
$pyKind = if ($py -eq $venvPy) { ' [.venv]' } else { ' [system -- deps shared with every other project]' }
$pyVer = (& $py -c "import sys; print(sys.version.split()[0])")
Write-Host "python: $py ($pyVer)$pyKind"

# -- 1c - the store pre-flight, and the automatic upgrade off JSON ----------
# USER RULING 2026-09-04 (17:00Z and 17:02Z, relayed through the coordinator):
# SQLite is orgtree's canonical format, JSON is DEPRECATED AND PAST LTS, and an
# existing JSON install must be migrated AUTOMATICALLY the moment it updates --
# no prompt, no flag, nothing for the operator to know or type.
#
# THE DEFECT THIS CLOSES. main defaults to ORGTREE_STORE=sqlite. An install
# still on the JSON format that pulls main gets a backend that REFUSES to start
# (MigrationRefused) against its own data root; and if that install registered
# the autostart tasks, `orgtree-ensure` relaunches the refusing build every five
# minutes forever. A routine `git pull` became a permanent outage.
#
# ⚠⚠ WHY THIS IS *HERE* AND NOT AT THE TOP OF THE FILE, which is the whole
# point: the thing being asked about is THE CODE THIS RUN IS ABOUT TO DEPLOY.
# An old install's store.py still defaults to `json`, so a check placed before
# the pull reads "JSON code, JSON root -- all fine" and does nothing, on exactly
# the population this exists for. It has to be AFTER the pull (line ~193) and
# it has to be BEFORE THE STOP (section 4, ~line 390): an install that hands
# over here has not been stopped, has not been rebuilt, and is still serving.
# It is also after 1b because it needs an interpreter, and before the frontend
# build because the wrapper's own deploy does that build.
#
# WHAT IT DOES NOT DO: decide anything itself. tools/preflight_store.py asks
# store.py's own STORE_BACKEND / pending_migrations() / active_databases() --
# the three expressions claim_data_root consults when it decides whether to
# raise -- so this cannot drift into checking something adjacent to the truth.
$dataRoot = $env:ORGTREE_DATA
if (-not $dataRoot) { $dataRoot = Join-Path $env:USERPROFILE 'orgtree' }

$preflight = Join-Path $root 'tools\preflight_store.py'
# 4 is UNKNOWN, and UNKNOWN PROCEEDS. A missing pre-flight script, an
# interpreter that will not run it, a probe that cannot import the store -- all
# of those leave the deploy behaving exactly as it did before this section
# existed. A guard a normal, correct deploy can trip is worse than no guard.
$pfRc = 4
if (Test-Path $preflight) {
    Write-Host "`n== store pre-flight =="
    try {
        & $py $preflight --data $dataRoot --repo $root
        $pfRc = $LASTEXITCODE
        if ($null -eq $pfRc) { $pfRc = 4 }
    } catch {
        Write-Host "the store pre-flight could not run ($_) -- deploying as before." -ForegroundColor Yellow
        $pfRc = 4
    }
} else {
    Write-Host "no tools\preflight_store.py in this checkout -- deploying as before." -ForegroundColor Yellow
}

if ($pfRc -eq 2) {
    # MIXED. Both formats present: NEITHER backend starts, by design, and this
    # is the one state the ruling explicitly does not extend "seamless" to.
    # Stopping here leaves the install exactly as it is -- which for a mixed
    # root means down, and it was already down before this ran.
    Write-Host "REFUSING to deploy: this data root is half-migrated (see above)." -ForegroundColor Red
    Write-Host "Nothing was stopped, rebuilt or started. This needs a person." -ForegroundColor Red
    exit 1
}
if ($pfRc -eq 3) {
    # MISMATCH. The root is SQLite and something is pinning this build to JSON
    # (usually a User-scope ORGTREE_STORE left by an aborted cutover). The fix
    # is to stop pinning, and it is NOT this script's to make silently:
    # unsetting an operator's environment variable behind their back is how a
    # machine ends up running one thing and reporting another.
    Write-Host "REFUSING to deploy: the backend would refuse this root (see above)." -ForegroundColor Red
    Write-Host "Nothing was stopped, rebuilt or started." -ForegroundColor Red
    exit 1
}
if ($pfRc -eq 1) {
    # MIGRATE -- the upgrade case, and the reason this section exists.
    if ($env:ORGTREE_NO_AUTOCUTOVER) {
        # The escape hatch, and the recursion guard. tools/cutover_deploy.ps1
        # sets this for every child it spawns, so the update.ps1 IT runs at its
        # own step 5 can never hand back to it. An operator may set it too.
        Write-Host "ORGTREE_NO_AUTOCUTOVER is set -- NOT upgrading this root automatically." -ForegroundColor Yellow
        Write-Host "The backend will refuse this root unless ORGTREE_STORE=json is also set." -ForegroundColor Yellow
    } elseif ($EnsureUp) {
        # ⚠ THE 5-MINUTE WATCHDOG DOES NOT RUN A CUTOVER. Its job is to get a
        # dead backend serving again in seconds; a multi-minute pull-build-
        # migrate sequence fired from a repeating timer is the opposite of
        # that, and it would fire again 5 minutes later whether or not the
        # first one finished. So this leg does the one thing that restores
        # service immediately: start the backend in the format the root is
        # ACTUALLY in. The full deploy (at logon, or run by hand) does the
        # upgrade. Reaching here at all means an earlier upgrade did not
        # complete, so it is a fallback, not the path.
        #
        # ⚠ FOR THIS LAUNCH ONLY -- deliberately NOT the User-scope pin
        # cutover_deploy.ps1's Set-JsonPin writes. A persistent pin set by a
        # watchdog is a pin nobody knows exists, and it would then make the
        # next full deploy read "JSON build, JSON root, all fine" and never
        # upgrade anything. This one dies with the process.
        $env:ORGTREE_STORE = 'json'
        Write-Host "-EnsureUp: starting this backend on JSON for THIS LAUNCH so the install" -ForegroundColor Yellow
        Write-Host "is serving again now. The upgrade to SQLite happens on the next full deploy." -ForegroundColor Yellow
        # Invisible is not the same as undisclosed (ruling, 17:00Z). The ensure
        # task runs under `conhost --headless` with NO output redirection --
        # verified by reading tools\install-autostart.ps1 -- so everything
        # printed above is LOST. A file in the data root is the only place an
        # operator can find this afterwards. Overwritten, never appended: this
        # runs every 5 minutes.
        try {
            $note = Join-Path $dataRoot 'UPGRADE-PENDING.txt'
            @("orgtree: this install is still on the JSON format.",
              "",
              "Written by update.ps1 -EnsureUp at $((Get-Date).ToUniversalTime().ToString('u')).",
              "The backend was relaunched with ORGTREE_STORE=json for that one launch,",
              "so your install is serving normally -- but on the deprecated format.",
              "",
              "SQLite is orgtree's canonical format (user ruling 2026-09-04) and the",
              "upgrade is automatic. To take it now, run a full deploy:",
              "    powershell -ExecutionPolicy Bypass -File `"$root\update.ps1`"",
              "",
              "Reaching this file means an earlier automatic upgrade did not complete.",
              "Look for cutover-*.log in this folder for what happened.",
              "This file is rewritten every 5 minutes while the install stays on JSON,",
              "and is safe to delete."
            ) | Set-Content -Path $note -Encoding ascii
        } catch {
            Write-Host "(could not write the UPGRADE-PENDING note: $_)" -ForegroundColor Yellow
        }
    } else {
        # THE HANDOFF. tools/cutover_deploy.{py,ps1} already sequences exactly
        # what is needed here -- mutex, stop-and-PROVE-stopped, migrate,
        # export-verify, deploy -- and it has been drilled against forced
        # failures at each step. It is not reimplemented here: a procedure
        # written in two places drifts, and the copy that drifts is the one
        # nobody re-reads. Its step 5 runs THIS script again, which is why the
        # handoff is a spawn-and-exit rather than a call.
        #
        # ⚠ THE MUTEX IS RELEASED FIRST, AND THAT IS LOAD-BEARING. The wrapper
        # takes the SAME named mutex at its step 0 with WaitOne(0); holding it
        # here means the wrapper it just launched exits 3 ("another deploy is
        # running") two seconds later, and the launcher reports the cutover as
        # having died instantly. Verified by reading both files.
        if ($mutexHeld) { [void]$mutex.ReleaseMutex(); $mutexHeld = $false }
        Write-Host "`n== handing this deploy to the automatic upgrade ==" -ForegroundColor Cyan
        $cdRc = 1
        try {
            & $py (Join-Path $root 'tools\cutover_deploy.py') $dataRoot --repo $root
            $cdRc = $LASTEXITCODE
            if ($null -eq $cdRc) { $cdRc = 1 }
        } catch {
            Write-Host "could not launch the upgrade: $_" -ForegroundColor Red
            $cdRc = 1
        }
        if ($cdRc -ne 0) {
            Write-Host "`nthe automatic upgrade could not be LAUNCHED (exit $cdRc)." -ForegroundColor Red
            Write-Host "NOTHING WAS STOPPED, REBUILT OR STARTED -- this install is exactly as it" -ForegroundColor Red
            Write-Host "was a minute ago, still serving its orgs on the JSON format." -ForegroundColor Red
            Write-Host "Deploying past this point would start a backend that refuses this root," -ForegroundColor Red
            Write-Host "so this run stops here instead. Re-run update.ps1 to try again." -ForegroundColor Red
            exit 1
        }
        Write-Host "`nthe upgrade is now running DETACHED and owns the rest of this deploy:" -ForegroundColor Green
        Write-Host "  stop -> migrate -> export-verify -> pull, build, restart -> health-check" -ForegroundColor Green
        Write-Host "It survives this process exiting, which is the point -- watch the log named" -ForegroundColor Green
        Write-Host "above. This run's job is done." -ForegroundColor Green
        exit 0
    }
}

# -- 2 - frontend (skipped under -EnsureUp: relaunch what is built) ---------
if (-not $EnsureUp) {
Write-Host "`n== building the UI =="
Set-Location (Join-Path $root 'frontend')
npm install --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { Write-Host "npm install failed" -ForegroundColor Red; exit 1 }
if ($lockHashBefore -and -not $AllowDirty) {
    $lockHashAfter = (Get-FileHash $lockPath -Algorithm SHA256).Hash
    if ($lockHashAfter -ne $lockHashBefore) {
        Write-Host "REFUSING to continue: frontend/package-lock.json was dirty BEFORE the install and the install rewrote it -- that dirt was a hand edit, not npm's own recomputation. Nothing was restarted." -ForegroundColor Red
        Write-Host "Commit or checkout the lockfile, then redeploy (or -AllowDirty to override)." -ForegroundColor Red
        exit 1
    }
    Write-Host "note: package-lock.json carries npm's own recomputation (stable under this npm) -- tolerated; consider committing it once"
}

# esbuild self-heal. Vite builds with esbuild, whose binary ships as an
# OPTIONAL per-platform package (@esbuild/<os>-<cpu>) rather than a postinstall
# download. npm has a long-standing bug where a tree installed once can end up
# missing those optional packages (npm/cli#4828), and the symptom is an opaque
# build failure that has repeatedly been misdiagnosed -- most recently as npm
# blocking postinstall scripts, which is NOT the cause: esbuild is fine with
# --ignore-scripts (measured 2026-08-03 on npm 11.6.2, esbuild 0.25.12 and
# 0.28.1 both transform successfully with scripts fully blocked).
# The reliable fix is a clean reinstall, so do that automatically instead of
# leaving the next person to guess. Never edit package.json to "allow scripts":
# it fixes nothing here, and rewriting the lockfile can DROP other platforms'
# optional entries and break the very machines it was meant to help.
# ⚠ THE PROBE RUNS UNDER EAP=Continue, AND THAT IS LOAD-BEARING (ps-guards
# audit 2026-08-27, measured -- not tidiness, do not "simplify" it back).
# Under Windows PowerShell 5.1 a native command whose stderr is REDIRECTED has
# each stderr line wrapped in an ErrorRecord, and with the script's global
# ErrorActionPreference='Stop' that record is a TERMINATING NativeCommandError.
# node prints a stack trace to stderr EXACTLY when esbuild is broken. So as
# written before today, `node -e ... 2>$null` killed the deploy on that line
# and everything below it -- the entire clean-reinstall self-heal, the retry,
# the diagnostic -- was unreachable in the only situation it exists for. It
# looked perfect on every run because a HEALTHY node prints nothing at all:
# the abstention was indistinguishable from a pass. expose.ps1 records the same
# mechanism for cloudflared's banner; this file had not applied it.
function Test-Esbuild {
    $was = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # $LASTEXITCODE is CLEARED first, and that is not decoration. It is a
    # leftover global: if the probe below does not actually run, the variable
    # still holds the PREVIOUS command's 0 (`npm install`'s), so "no answer"
    # would read as "esbuild is fine" -- the same abstention-reads-as-a-pass
    # bug this whole function exists to remove, reintroduced by the fix.
    # Cleared and re-checked, an unanswered probe reports broken instead.
    # A `node` missing ENTIRELY is a different case and is already safe:
    # PowerShell raises CommandNotFoundException as TERMINATING whatever the
    # preference is, so the deploy aborts loudly on "'node' is not recognized"
    # and never reaches the restart (measured 2026-08-27). Fail-closed and
    # correct -- a missing toolchain is not an esbuild fault to reinstall
    # around, and pretending otherwise would burn a clean reinstall to
    # rediscover it.
    $global:LASTEXITCODE = $null
    try {
        node -e "require('esbuild').transformSync('let x=1')" 2>$null
        if ($null -eq $LASTEXITCODE) { return 127 }
        return $LASTEXITCODE
    } finally { $ErrorActionPreference = $was }
}
if ((Test-Esbuild) -ne 0) {
    Write-Host "esbuild is not usable -- clean reinstall (npm optional-deps bug)" -ForegroundColor Yellow
    Remove-Item -Recurse -Force 'node_modules' -ErrorAction SilentlyContinue
    npm install --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { Write-Host "npm install failed" -ForegroundColor Red; exit 1 }
    if ((Test-Esbuild) -ne 0) {
        Write-Host "esbuild still broken after a clean reinstall." -ForegroundColor Red
        Write-Host "Check that node/npm match your platform (nvm switches can leave" -ForegroundColor Red
        Write-Host "a tree built for another arch), then delete package-lock.json too." -ForegroundColor Red
        exit 1
    }
    Write-Host "esbuild repaired" -ForegroundColor Green
}

npm run build
if ($LASTEXITCODE -ne 0) { Write-Host "UI build failed" -ForegroundColor Red; exit 1 }
Set-Location $root

# -- 3 - backend deps -------------------------------------------------------
Write-Host "`n== python deps =="
& $py -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "pip install failed" -ForegroundColor Red; exit 1 }

}   # end -not $EnsureUp (sections 2-3)

# -- 4 - restart the backend ------------------------------------------------
# ($dataRoot is resolved in section 1c, which needs it before this point and
# must not derive it a second time -- two copies of "where is the data root"
# is how a script stops a backend in one place and health-checks another.)
$port = '7360'
$portFile = Join-Path $dataRoot '.port'
if (Test-Path $portFile) { $port = (Get-Content $portFile -Raw).Trim() }

# -- 4a - what this backend must be carrying when it comes back -------------
# Taken BEFORE the stop, deliberately. The expectation is read from the data
# root's own contents (see tools/deploy_health.py, which explains why it is a
# filesystem scan and not `store.list_orgs()`), and this is also the last
# moment the OUTGOING process can be asked what it was serving -- which is the
# difference between "this deploy lost them" and "it was already like that".
#
# It NEVER blocks the restart. If it cannot run, the check after the restart
# has no expectation and FAILS on that; it does not pass by default, and a
# broken snapshot must not be the thing that stops a backend coming back up.
$healthCheck = Join-Path $root 'tools\deploy_health.py'
$healthState = Join-Path ([IO.Path]::GetTempPath()) "orgtree-deploy-health-$PID.json"
Write-Host "`n== what this install should be carrying =="
try {
    & $py $healthCheck snapshot --data $dataRoot --port $port --out $healthState
} catch {
    Write-Host "deploy-health: the pre-restart snapshot could not run ($_) -- the check after the restart will FAIL rather than pass on an expectation it does not have." -ForegroundColor Yellow
}

Write-Host "`n== restarting the backend (port $port) =="
$conn = Get-NetTCPConnection -LocalPort ([int]$port) -State Listen -ErrorAction SilentlyContinue
$oldPids = @($conn | Select-Object -ExpandProperty OwningProcess -Unique)
if ($conn) {
    $pids = $conn | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($p in $pids) {
        Write-Host "stopping old backend (pid $p)"
        try { Stop-Process -Id $p -Force -ErrorAction Stop } catch {}
    }
    Start-Sleep -Milliseconds 800
}

# -- 4b - the Claude Code CLI pin (No.44, D-222) -----------------------------------
# WHY IT IS *HERE*, between the stop and the start, and not up with the other
# dependency steps: on Windows a running process holds its own image open, so
# `npm install` cannot overwrite bin\claude.exe while a turn is in flight --
# it fails with EPERM/EBUSY on the one file the whole upgrade is about. The
# backend has just been killed and nothing has been started yet, so this is
# the only window in the deploy where the pin is not in use. The cost is that
# an upgrade extends the restart gap by one npm install; it is paid once, on
# the deploy that moves the pin, and every later deploy skips this in
# milliseconds.
#
# It is a FLOOR, not an equality. A pin OLDER than the target is upgraded; a
# pin NEWER is left exactly where it is and merely reported. An operator who
# installed something ahead of us did so on purpose, and a deploy script that
# silently rolls a machine backwards is worse than one that says nothing.
#
# It NEVER blocks the restart. Every failure below warns and falls through to
# the start: the old pin still runs turns (`supervisor.claude_model_for`
# downgrades the one model id it cannot name), so a CLI that would not update
# is a degraded install, while a backend that never came back up is an outage.
if (-not $EnsureUp) {
Write-Host "`n== claude cli =="
$pinDir  = Join-Path $dataRoot 'cli'
$pinPkgJ = Join-Path $pinDir 'node_modules\@anthropic-ai\claude-code\package.json'
$pinBin  = Join-Path $pinDir 'node_modules\@anthropic-ai\claude-code\bin\claude.exe'

# The target version is READ FROM THE CODE, never retyped here -- one literal
# in backend\orgtree\clipin.py, which is also what the backend compares
# against at runtime. A version written down twice is a machine that reports
# one number and runs another. clipin imports nothing, so this cannot fail for
# a reason unrelated to the pin; if it fails anyway we do not GUESS a version,
# we leave the pin alone and say so.
$wantVer = $null
try {
    $wantVer = (& $py -c "import sys; sys.path.insert(0, sys.argv[1]); from orgtree import clipin; print(clipin.PIN)" (Join-Path $root 'backend') | Select-Object -First 1)
    if ($wantVer) { $wantVer = $wantVer.Trim() }
} catch { $wantVer = $null }
if ($wantVer -notmatch '^\d+\.\d+\.\d+') {
    Write-Host "could not read the pinned CLI version from backend\orgtree\clipin.py -- LEAVING THE CLI ALONE (guessing a version is how a machine ends up running one thing and reporting another)." -ForegroundColor Yellow
    $wantVer = $null
}

function Get-PinVersion {
    if (-not (Test-Path $pinPkgJ)) { return $null }
    try { return (Get-Content $pinPkgJ -Raw | ConvertFrom-Json).version } catch { return $null }
}
# "2.1.220" -> [version] for an ordering comparison. Returns $null on anything
# unparseable so callers can tell "older" from "no idea".
function ConvertTo-Ver([string]$v) {
    if (-not $v) { return $null }
    $m = [regex]::Match($v, '^(\d+)\.(\d+)\.(\d+)')
    if (-not $m.Success) { return $null }
    return [version]::new([int]$m.Groups[1].Value, [int]$m.Groups[2].Value, [int]$m.Groups[3].Value)
}

if ($env:ORGTREE_CLAUDE) {
    # The override wins at runtime (supervisor resolves ORGTREE_CLAUDE first),
    # so installing the pin underneath it would build something nothing runs.
    # Report the truth instead, including when the override is behind.
    $ovVer = $null
    try { $ovVer = (& $env:ORGTREE_CLAUDE --version 2>$null | Select-Object -First 1) } catch {}
    Write-Host "ORGTREE_CLAUDE is set -- the pin is NOT what this machine runs. Leaving it untouched."
    Write-Host "  running: $env:ORGTREE_CLAUDE ($(if ($ovVer) { $ovVer } else { 'version unreadable' }))"
    $ov = ConvertTo-Ver $ovVer
    $wv = ConvertTo-Ver $wantVer
    if ($ov -and $wv -and $ov -lt $wv) {
        Write-Host "  ⚠ that is OLDER than the pinned $wantVer -- fable agents fall back to Claude Fable 5, and other new model ids may not resolve. Point ORGTREE_CLAUDE at a newer CLI or unset it to use the managed pin." -ForegroundColor Yellow
    }
} elseif ($wantVer) {
    $haveVer = Get-PinVersion
    $have = ConvertTo-Ver $haveVer
    $want = ConvertTo-Ver $wantVer
    $needs = (-not (Test-Path $pinBin)) -or (-not $have) -or ($have -lt $want)
    if (-not $needs) {
        if ($have -gt $want) {
            Write-Host "Claude CLI: $haveVer (pin) -- NEWER than this build's $wantVer, left as it is"
        } else {
            Write-Host "Claude CLI: $haveVer (pin) -- already current"
        }
    } else {
        $from = if ($haveVer) { $haveVer } elseif (Test-Path $pinDir) { 'present but unreadable' } else { 'not installed' }
        Write-Host "Claude CLI: $from -> $wantVer (installing into $pinDir)" -ForegroundColor Cyan
        # --save-exact so the prefix's package.json records the exact version
        # rather than a caret range. The pre-existing installs on this fleet
        # carry exactly such a range, from a hand-run `npm install
        # @anthropic-ai/claude-code`, which means the version a re-install
        # lands on drifts with the registry -- the opposite of a pin.
        # Rewriting it to an exact spec is most of what makes this migration
        # reproducible.
        function Install-Pin {
            # ⚠ `| Out-Host`, not a bare call: a native command's stdout goes to
            # the FUNCTION'S OUTPUT STREAM, so without this the return value is
            # npm's log lines followed by the boolean, and `(Install-Pin) -and
            # (Test-Pin)` then tests a non-empty array (always true) instead of
            # the exit code. Out-Host keeps the operator's log and leaves the
            # function returning exactly one thing.
            npm install --prefix $pinDir "@anthropic-ai/claude-code@$wantVer" `
                --no-audit --no-fund --save-exact | Out-Host
            return ($LASTEXITCODE -eq 0)
        }
        # VERIFY, never trust the exit code: npm's long-standing optional-deps
        # bug (the same one the esbuild block above works around) can report
        # success having left the platform-specific package behind, and the
        # claude-code package IS a native binary delivered that way. So the
        # test is the two things that actually have to be true afterwards.
        function Test-Pin {
            $v = Get-PinVersion
            return ((Test-Path $pinBin) -and $v -and (ConvertTo-Ver $v) -ge $want)
        }
        $ok = (Install-Pin) -and (Test-Pin)
        if (-not $ok) {
            # A dirty upgrade over an older tree is the one case the operator
            # would otherwise have to fix by hand -- which is exactly what this
            # deploy is not allowed to require. Two things cause it: the npm
            # optional-deps bug, and a claude.exe still held open by an agent
            # process that outlived the backend. Wait out the second, then
            # remove the tree and install clean. Only the managed pin directory
            # is ever deleted, and it holds nothing but this package.
            Write-Host "the in-place upgrade did not take -- clean reinstall of the pin" -ForegroundColor Yellow
            Start-Sleep -Seconds 3
            Remove-Item -Recurse -Force (Join-Path $pinDir 'node_modules') -ErrorAction SilentlyContinue
            Remove-Item -Force (Join-Path $pinDir 'package-lock.json') -ErrorAction SilentlyContinue
            Remove-Item -Force (Join-Path $pinDir 'package.json') -ErrorAction SilentlyContinue
            $ok = (Install-Pin) -and (Test-Pin)
        }
        if ($ok) {
            Write-Host "Claude CLI: now $(Get-PinVersion) (pin) -- sandbox images rebuild automatically on the next sandboxed turn" -ForegroundColor Green
        } else {
            # Loud, specific, and NOT fatal. See the section header.
            Write-Host ""
            Write-Host "the Claude CLI pin could NOT be updated to $wantVer." -ForegroundColor Red
            Write-Host "  the backend is still being started and turns still run: agents on the" -ForegroundColor Red
            Write-Host "  fable tier fall back to Claude Fable 5 until this is fixed." -ForegroundColor Red
            Write-Host "  most likely a claude.exe still running from $pinDir, or npm could not reach the registry." -ForegroundColor Red
            Write-Host "  to retry by hand:  npm install --prefix `"$pinDir`" @anthropic-ai/claude-code@$wantVer --save-exact" -ForegroundColor Red
            Write-Host ""
        }
    }
} else {
    # No override and no target: report what the backend will resolve, which is
    # what section 3b used to do. Probing PATH instead printed 2.1.31 on a
    # machine whose runtime was the 2.1.220 pin, so the log contradicted
    # /api/host and read like the broken fallback was live.
    $cliExe = if (Test-Path $pinBin) { $pinBin } else { 'claude' }
    $cliWhich = if ($cliExe -eq 'claude') { 'PATH fallback -- the pin is MISSING' } else { 'pin' }
    try {
        $cliVer = (& $cliExe --version 2>$null | Select-Object -First 1)
        if ($cliVer) { Write-Host "Claude CLI: $cliVer [$cliWhich] $cliExe" }
    } catch {}
}
}   # end -not $EnsureUp (section 4b)

$logDir = $dataRoot
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force $logDir | Out-Null }
$out = Join-Path $logDir 'backend.log'
$errLog = Join-Path $logDir 'backend.err.log'
# the kiosk public listener is on by default (it serves nothing unless a
# kiosk org exists, and nothing reaches it from outside without a tunnel --
# run expose.ps1 to open one); set ORGTREE_PUBLIC_PORT yourself to override
if (-not $env:ORGTREE_PUBLIC_PORT) { $env:ORGTREE_PUBLIC_PORT = '7361' }
$apiArgs = @('-m', 'orgtree.api')
# ORGTREE_EXPOSE_ADMIN is what the backend reads (user ruling 2026-08-04, was
# an argv flag). -ExposeAdmin is kept as a convenience that sets it for this
# launch; a service definition sets the variable directly and needs no switch.
if ($ExposeAdmin) { $env:ORGTREE_EXPOSE_ADMIN = '1' }
if ($env:ORGTREE_EXPOSE_ADMIN -and
    $env:ORGTREE_EXPOSE_ADMIN.Trim().ToLower() -in @('1', 'true', 'yes', 'on')) {
    Write-Host ''
    Write-Host ('!' * 74) -ForegroundColor Red
    Write-Host '  ORGTREE_EXPOSE_ADMIN: the ADMIN api will listen on 0.0.0.0 with NO auth.' -ForegroundColor Red
    Write-Host '  Anyone who can reach this port controls every org and can make' -ForegroundColor Red
    Write-Host '  agents run commands on this machine. VPN/SSH tunnel only.' -ForegroundColor Red
    Write-Host ('!' * 74) -ForegroundColor Red
    Write-Host ''
}
Start-Process -FilePath $py -ArgumentList $apiArgs `
    -WorkingDirectory (Join-Path $root 'backend') -WindowStyle Hidden `
    -RedirectStandardOutput $out -RedirectStandardError $errLog

# -- 5 - health check -------------------------------------------------------
# This used to be twenty tries at `/api/orgs` looking for an HTTP 200, and an
# EMPTY LIST IS A PERFECTLY GOOD 200. So a backend that came up carrying none
# of this install's orgs deployed green: the SQLite cutover ships, the code is
# rolled back without the data, the JSON build finds no `<slug>.json` (only
# `<slug>.db` + `<slug>.json.premigration`), honestly presents zero orgs, and
# this script blessed it. The whole install appeared to have vanished while
# every automated signal said healthy.
#
# So the assertion is now that the backend came up carrying the state the data
# root says it should have, not merely that it answers. The verdicts are
# distinct because the operator does different things about them:
#   0 up and carrying its orgs        3 could not determine what to expect --
#   1 up and presenting WRONG state     which is a FAILURE, never a pass
#   2 nothing ever answered
# tools/deploy_health.py carries the reasoning and the bounded budgets; it is
# proved to go red in backend/tests/test_deploy_health.py.
$healthRc = 3
try {
    & $py $healthCheck verify --port $port --state $healthState
    $healthRc = $LASTEXITCODE
} catch {
    Write-Host "deploy-health: the health check itself could not run ($_)." -ForegroundColor Red
    $healthRc = 3
}
Remove-Item -Force $healthState -ErrorAction SilentlyContinue
# 0 and 1 are the two verdicts that mean SOMETHING answered on the port, which
# is what the stale-pid comparison below needs to be meaningful.
$ok = ($healthRc -eq 0 -or $healthRc -eq 1)
# "something answers on the port" is NOT proof the restart happened: if the old
# process was never killed the health check passes against the very code we were
# trying to replace, and the script reports success. So compare pids.
$newPids = @(Get-NetTCPConnection -LocalPort ([int]$port) -State Listen -ErrorAction SilentlyContinue |
             Select-Object -ExpandProperty OwningProcess -Unique)
$stale = $ok -and $oldPids.Count -and $newPids.Count -and
         -not ($newPids | Where-Object { $oldPids -notcontains $_ })
if ($stale) {
    Write-Host "`nthe OLD backend is still serving (pid $($newPids -join ',')) -- the restart did not take." -ForegroundColor Red
    exit 1
}
if ($healthRc -eq 0) {
    Write-Host "`n== up: http://localhost:$port ($after) ==" -ForegroundColor Green
} elseif ($healthRc -eq 2) {
    Write-Host "`nbackend did not come up -- check $errLog" -ForegroundColor Red
    exit 1
} elseif ($healthRc -eq 1) {
    Write-Host "`nthe backend is UP but is not carrying this install's orgs (see above)." -ForegroundColor Red
    Write-Host "the org documents are still on disk; this is a serving fault." -ForegroundColor Red
    Write-Host "backend log: $out" -ForegroundColor Red
    exit 1
} else {
    Write-Host "`nthe deploy health check could not establish that this backend came up" -ForegroundColor Red
    Write-Host "carrying its orgs (see above). That is reported as a FAILURE on purpose:" -ForegroundColor Red
    Write-Host "'I could not tell' is not 'healthy'. Backend log: $out" -ForegroundColor Red
    exit 1
}

} finally {
    if ($mutexHeld) { [void]$mutex.ReleaseMutex() }
    $mutex.Dispose()
}
