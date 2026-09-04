# The SQLite cutover, run DETACHED from whatever asked for it.
#
# WHY THIS EXISTS.  docs/sqlite-cutover.md step 1 is "stop the backend", and
# every agent on this machine runs INSIDE that backend.  A cutover driven from
# an agent's own shell kills the shell at step 1 and nothing performs steps
# 2-5; the root is left mid-flight with nobody watching.  So the whole
# sequence is handed to a process that has no parent to lose:
# `tools/cutover_deploy.py` spawns this script the way supervisor.py spawns
# update.ps1, and the caller's turn ending is then irrelevant.
#
# ⚠ DO NOT RUN THIS DIRECTLY FOR A REAL CUTOVER.  Started from a live shell it
# is a child of that shell and dies with it at the stop.  Go through the
# launcher.  (Running it directly against a THROWAWAY root is how it is
# drilled, which is why it is not blocked outright.)
#
#   python tools\cutover_deploy.py <data-root>
#
# WHAT IT DOES, with the state of the root named at every step:
#   0  claim the machine-wide deploy mutex, so the 5-minute `orgtree-ensure`
#      task cannot relaunch a backend into the middle of the migration
#   1  snapshot what this install should be carrying (tools/deploy_health.py)
#   2  stop the backend and PROVE it stopped -- port free AND the data root's
#      owner lock actually acquirable, which is the thing that decides
#   3  migrate           <- the root becomes SQLite here, and only here
#   4  export-verify     <- the step that makes a rollback possible at all
#   5  deploy (update.ps1: pull, build, restart, health-check)
#
# AND TWO DIFFERENT RECOVERIES, because there are two different failures:
#   before step 3 succeeds  the root is still JSON.  Bring a JSON backend back
#                           up -- PINNED, because the checkout on disk now
#                           defaults to sqlite and would refuse the root.
#   after step 3 succeeds   the root holds databases.  A JSON backend REFUSES
#                           it (BackendMismatch) and must not be attempted.
#                           The way back is `cutover.py rollback`, which needs
#                           step 4's exports.
param(
    # the data root to cut over
    [Parameter(Mandatory = $true)][string]$Root,
    # the orgtree checkout that is deployed and whose tools are used.  Passed
    # explicitly and anchor-checked rather than derived from this script's own
    # location: this script is edited in worktrees, and a worktree that
    # deployed ITSELF would build a tree nobody signed off on.
    [Parameter(Mandatory = $true)][string]$Repo,
    # drill: run steps 0-4 and the recoveries, never step 5.  REFUSES the
    # default data root outright -- a drill that can point at the live install
    # is one typo away from being a deploy.
    [switch]$DrillNoDeploy,
    # drill: treat export-verify as having failed, to exercise the recovery
    # that follows it.  Only honoured with -DrillNoDeploy, so it cannot exist
    # on a real run.
    [switch]$DrillForceExportFail,
    # drill: skip the FIRST rung of the post-migration recovery (relaunch the
    # SQLite build) so the second rung -- the real rollback -- is the thing
    # under test.  There is no natural way to make a correct SQLite build
    # refuse a correct migrated root, which is the point; a rung that has
    # never fired is not a rung.  Only honoured with -DrillNoDeploy.
    [switch]$DrillSkipFirstRelaunch,
    # drill: treat `cutover.py migrate` as having failed WITHOUT running it, so
    # the pre-migration abort is exercised against a root that really is
    # untouched.  Only honoured with -DrillNoDeploy.
    [switch]$DrillForceMigrateFail
)

$ErrorActionPreference = 'Stop'
# ⚠ THIS FILE MUST KEEP ITS UTF-8 BOM. PowerShell 5.1 reads a BOM-less script
# as the ANSI code page, so every non-ASCII character below -- the markers an
# operator reads under pressure -- arrives in the log double-encoded.
# The launcher captures this script's stdout as raw bytes.  Without this the
# console's OEM code page mangles every non-ASCII character in the log an
# operator is meant to read under pressure.
try {
    [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
} catch { }

# ---- the log IS the deliverable -------------------------------------------
# This whole run happens while the agent that asked for it is dead.  Nothing
# is interactive and nobody is watching, so every line carries a UTC timestamp
# and every decision says which of the two states the root is in.  The
# launcher redirects stdout+stderr into the log file, so Write-Host IS the log.
function Say([string]$m, [string]$c = 'Gray') {
    $t = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ')
    Write-Host "$t  $m" -ForegroundColor $c
}
function Banner([string]$m, [string]$c = 'Cyan') {
    Say ('=' * 70) $c
    Say $m $c
    Say ('=' * 70) $c
}

# ---- state ----------------------------------------------------------------
# $script:migrated records what the wrapper BELIEVES -- it flips when
# `cutover.py migrate` returns 0 -- and it is logged, but it does NOT choose
# the recovery.  THE ROOT DOES.  A migration that converts two orgs and fails
# on the third returns non-zero, so a boolean would call that root "still
# JSON", print "nothing was migrated", and try to start a JSON backend against
# a directory that now holds databases -- which refuses.  The root has THREE
# states, so the recovery reads three.
$script:migrated = $false
$script:mutex = $null
$script:mutexHeld = $false
$script:port = '7360'
$script:healthState = ''
$script:py = 'python'
# ⚠ EVERY helper below is VOID and reports through these.  A PowerShell
# function that `return`s also returns everything its body wrote to the
# success stream -- a native command's stdout included -- so `$rc = Helper ...`
# silently becomes an array whose last element is the number you wanted.  That
# is a bug that reads perfectly and fires only when the child says something.
$script:rc = 0
$script:health = 3
# every child's stdout/stderr lands here before being copied into the log
$script:childDir = ''
$script:childN = 0

Banner "orgtree SQLite cutover -- detached wrapper"
Say "root  $Root"
Say "repo  $Repo"
Say "drill $DrillNoDeploy"
Say "pid   $PID"

# ---- anchors: am I standing in the things I am about to operate on? -------
# Same argument as update.ps1's own anchor check: this script stops a backend,
# rewrites a data root and starts a build.  Pointed at the wrong directory it
# does all of that to the wrong tree, and the first thing to notice would be
# an unrelated-looking error much later.
foreach ($a in @('update.ps1', 'tools\cutover.py', 'tools\deploy_health.py',
                 'tools\cutover_deploy.py', 'backend\orgtree\api.py')) {
    if (-not (Test-Path (Join-Path $Repo $a))) {
        Say "NOT AN ORGTREE CHECKOUT: $Repo has no $a. Nothing was touched." Red
        exit 4
    }
}
if (-not (Test-Path (Join-Path $Root 'orgs'))) {
    Say "NOT A DATA ROOT: $Root has no orgs\. Nothing was touched." Red
    exit 4
}
if ($DrillNoDeploy) {
    $rr = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $ll = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE 'orgtree')).TrimEnd('\')
    if ($rr -eq $ll) {
        Say "REFUSING: -DrillNoDeploy was pointed at the DEFAULT data root ($ll)." Red
        Say "A drill exists so that the live install is not the thing being" Red
        Say "experimented on. Point it at a throwaway copy." Red
        exit 4
    }
}
if (($DrillForceExportFail -or $DrillSkipFirstRelaunch -or $DrillForceMigrateFail) `
        -and -not $DrillNoDeploy) {
    Say "REFUSING: the -Drill* switches are drill-only and need -DrillNoDeploy." Red
    exit 4
}
# ⚠ A DRILL ROOT WITH NO .port FILE WOULD RESOLVE TO 7360 AND STOP THE LIVE
# BACKEND.  The port default is a property of the CODE, not of the root being
# drilled, so a throwaway root silently inherits the live install's port and
# step 2 kills the wrong process.  In drill mode the port must therefore be
# stated by the root itself and must differ from the default install's.
if ($DrillNoDeploy) {
    $dpf = Join-Path $Root '.port'
    if (-not (Test-Path $dpf)) {
        Say "REFUSING: drill root $Root has no .port file, so the port would fall back" Red
        Say "to 7360 -- the live install's -- and step 2 would stop the LIVE backend." Red
        exit 4
    }
    $livePf = Join-Path (Join-Path $env:USERPROFILE 'orgtree') '.port'
    if (Test-Path $livePf) {
        $lp = (Get-Content $livePf -Raw).Trim()
        $dp = (Get-Content $dpf -Raw).Trim()
        if ($lp -eq $dp) {
            Say "REFUSING: drill root's port ($dp) is the live install's port. Step 2" Red
            Say "would stop the LIVE backend. Give the drill root its own port." Red
            exit 4
        }
    }
}

# ---- the interpreter: the SAME one the backend will run under -------------
# Mirrors update.ps1's precedence, minus the venv CREATION leg -- a cutover is
# not the moment to find out whether a virtualenv can be built.  A missing
# venv is said out loud rather than refused: leaving the backend stopped over
# a dependency question is worse than deploying with the interpreter that is
# there.
$venvPy = Join-Path $Repo '.venv\Scripts\python.exe'
if ($env:ORGTREE_PYTHON) {
    $script:py = $env:ORGTREE_PYTHON
} elseif ($env:ORGTREE_NO_VENV -eq '1') {
    $script:py = 'python'
} elseif (Test-Path $venvPy) {
    $script:py = $venvPy
} else {
    Say "no .venv in $Repo -- using the system interpreter, which is NOT the one" Yellow
    Say "update.ps1 would use once a venv exists" Yellow
    $script:py = 'python'
}
Say "python $($script:py)"

# Children write their output to files here and it is copied into the log; see
# the note on Run for why this is not a pipeline.
$script:childDir = Join-Path ([IO.Path]::GetTempPath()) "orgtree-cutover-$PID"
New-Item -ItemType Directory -Force $script:childDir | Out-Null
Say "child output $($script:childDir)"

# ⚠ update.ps1 FINDS THE DATA ROOT FOR ITSELF, and its fallback is the live
# one.  Every leg of this wrapper that shells out to update.ps1 -- both
# recoveries and the deploy -- would otherwise operate on
# $env:USERPROFILE\orgtree regardless of the -Root it was given: stop THAT
# backend, health-check THAT install, and start a backend carrying it.  On the
# real cutover -Root IS that path and the bug is invisible; on any other root
# it points the whole recovery at the live install.  So the root this wrapper
# was given is made the root every child sees, once, here.
$env:ORGTREE_DATA = $Root
Say "ORGTREE_DATA pinned to $Root for every child (update.ps1 defaults to the"
Say "  live root otherwise, whatever -Root said)"

# ⚠ AND THE RECURSION GUARD, for the same reason and in the same place.
# update.ps1 section 1c now HANDS OFF to this wrapper when it finds an
# unmigrated JSON root under a SQLite build (user ruling 2026-09-04: the
# upgrade is automatic, not something an operator asks for).  Step 5 of this
# wrapper runs update.ps1.  Root state alone would break the cycle -- by step 5
# the root holds databases, so the pre-flight says PROCEED -- but "it cannot
# recurse because of what the data looks like by then" is exactly the kind of
# reasoning this repo has been burned by.  Every child this wrapper spawns is
# told not to hand back, so the cycle is impossible by construction rather than
# by argument.  Both recoveries relaunch through update.ps1 too, and neither
# may turn into a second cutover attempt.
$env:ORGTREE_NO_AUTOCUTOVER = '1'
Say "ORGTREE_NO_AUTOCUTOVER=1 for every child (update.ps1 hands JSON roots to"
Say "  THIS script; a child that handed back would loop)"

# ---- helpers (all void; results land in $script:rc / $script:health) -------
function Get-BackendPort {
    $p = '7360'
    $pf = Join-Path $Root '.port'
    if (Test-Path $pf) { $p = (Get-Content $pf -Raw).Trim() }
    $script:port = $p
}

function Get-Listeners([string]$p) {
    return @(Get-NetTCPConnection -LocalPort ([int]$p) -State Listen `
             -ErrorAction SilentlyContinue |
             Select-Object -ExpandProperty OwningProcess -Unique)
}

# Read a file that a still-running process may be holding open for writing.
# The backend `update.ps1` starts inherits the redirected handles, so a plain
# Get-Content can be refused; FileShare::ReadWrite says "I know someone else
# has it, let me read anyway".
function Slurp([string]$p) {
    if (-not (Test-Path $p)) { return '' }
    try {
        $fs = [IO.File]::Open($p, [IO.FileMode]::Open, [IO.FileAccess]::Read,
                              [IO.FileShare]::ReadWrite)
        $sr = New-Object IO.StreamReader($fs)
        $t = $sr.ReadToEnd()
        $sr.Close(); $fs.Close()
        return $t
    } catch {
        return "(could not read $p : $_)"
    }
}

# Run a child to completion, copy everything it said into the log, and put its
# exit code in $script:rc.
#
# ⚠⚠ THE CHILD'S OUTPUT GOES TO FILES, NEVER TO A PIPELINE, AND THIS IS THE
# WHOLE REASON THE FUNCTION LOOKS LIKE THIS.  Written as `& powershell ... |
# Out-Host`, this HANGS FOREVER on the one call that matters: `update.ps1`
# starts the backend with `Start-Process -Redirect...`, which forces handle
# inheritance, so the long-lived backend inherits the pipeline's write handle.
# update.ps1 itself exits; the pipe never closes because the BACKEND still
# holds it; and the wrapper waits on a dead script forever -- never running
# its final health check, never reaching either recovery, never logging a
# verdict.  Measured here on 2026-09-04: the drill sat wedged after a
# successful relaunch with the log ending mid-step.
#
# `$p.WaitForExit()` and not `Start-Process -Wait` for the same family of
# reason: `-Wait` is documented to wait for the process AND ITS DESCENDANTS,
# which is precisely the backend we just deliberately started.
function Run([string]$what, [string]$exe, [string]$argline) {
    Say "---- $what" Cyan
    Say "     $exe $argline"
    $script:childN++
    $o = Join-Path $script:childDir ("child-$($script:childN).out")
    $e = Join-Path $script:childDir ("child-$($script:childN).err")
    $script:rc = 99
    try {
        $p = Start-Process -FilePath $exe -ArgumentList $argline -PassThru `
             -NoNewWindow -RedirectStandardOutput $o -RedirectStandardError $e
        # ⚠ TOUCHING .Handle IS NOT DEAD CODE.  Without it the object
        # Start-Process -PassThru returns never caches the process handle, and
        # after WaitForExit `.ExitCode` comes back $null -- not 0, $null.  The
        # wrapper then reads a perfectly successful step as a failure and
        # refuses (measured here on 2026-09-04: the deploy_health snapshot
        # printed both of its success lines and was scored as a failed step).
        # It fails SAFE, but it fails EVERY run.
        $null = $p.Handle
        $p.WaitForExit()
        $script:rc = $p.ExitCode
        if ($null -eq $script:rc) {
            Say "$what gave no exit code at all -- treating as a failure" Red
            $script:rc = 98
        }
    } catch {
        Say "$what could not be started: $_" Red
        $script:rc = 99
    }
    $so = (Slurp $o).TrimEnd()
    if ($so) { Write-Host $so }
    $se = (Slurp $e).TrimEnd()
    if ($se) { Write-Host $se }
    if ($script:rc -eq 0) { Say "---- $what exited 0" Green }
    else { Say "---- $what exited $($script:rc)" Red }
}

# Quote an argument for a Windows command line only when it needs it.
function Q([string]$s) {
    if ($s -match '[\s"]') { return '"' + $s.Replace('"', '\"') + '"' }
    return $s
}

# Is a backend up AND carrying what this root says it should?  Uses
# tools/deploy_health.py, not "something answered on the port": an empty list
# is a perfectly good HTTP 200, and a backend that came up carrying none of
# this install's orgs is the exact failure this cutover could cause.
#   0 up and correct   1 up and WRONG   2 nothing answered   3 undeterminable
# 1, 2 and 3 are all failures.
function Test-BackendUp([string]$why) {
    if (-not (Test-Path $script:healthState)) {
        Say "no pre-stop snapshot exists, so '$why' cannot be judged -- NOT up" Yellow
        $script:health = 3
        return
    }
    Run "health check ($why)" $script:py `
        ((Q (Join-Path $Repo 'tools\deploy_health.py')) + " verify --port " +
         $script:port + " --state " + (Q $script:healthState))
    $script:health = $script:rc
}

# ---- the JSON pin ----------------------------------------------------------
# The checkout on disk defaults to ORGTREE_STORE=sqlite (main, 2026-09-04), so
# "just start the backend again" no longer means "start the backend that was
# running".  A JSON root needs the pin said out loud.
#
# It is set in TWO scopes and they answer different questions:
#   process  the backend this script is about to launch inherits it
#   User     the 5-minute `orgtree-ensure` task and the at-logon deploy launch
#            their own processes later and inherit nothing from here.  Without
#            this, the first crash after an aborted cutover brings up a SQLite
#            build against a JSON root, it refuses, and the machine stays down
#            retrying every five minutes forever.
# A persistent variable someone must remember to remove is a step someone
# eventually skips, so the removal is printed here in full AND performed
# automatically by the success path (Clear-JsonPin).
function Set-JsonPin {
    $env:ORGTREE_STORE = 'json'
    try {
        [Environment]::SetEnvironmentVariable('ORGTREE_STORE', 'json', 'User')
        Say "PINNED ORGTREE_STORE=json for this launch AND persistently (User scope)." Yellow
    } catch {
        Say "PINNED ORGTREE_STORE=json for THIS LAUNCH ONLY -- the persistent pin FAILED ($_)." Red
        Say "⚠ If this backend ever dies, the 5-minute ensure task relaunches it as SQLite" Red
        Say "  against a JSON root, it refuses, and orgtree stays down. Set it by hand:" Red
        Say "  [Environment]::SetEnvironmentVariable('ORGTREE_STORE','json','User')" Red
    }
    Say "  To remove the pin once the root is SQLite again:" Yellow
    Say "  [Environment]::SetEnvironmentVariable('ORGTREE_STORE',`$null,'User')" Yellow
}

function Clear-JsonPin {
    $cur = [Environment]::GetEnvironmentVariable('ORGTREE_STORE', 'User')
    if ($cur -eq 'json') {
        [Environment]::SetEnvironmentVariable('ORGTREE_STORE', $null, 'User')
        Say "removed the persistent ORGTREE_STORE=json pin an earlier aborted cutover left" Yellow
        Say "  (leaving it would start a JSON backend against a root that now holds" Yellow
        Say "   databases -- BackendMismatch, i.e. down, not degraded)" Yellow
    } elseif ($cur) {
        Say "⚠ ORGTREE_STORE is pinned to '$cur' in the User environment and this script" Red
        Say "  did not set it. LEAVING IT ALONE -- but anything other than 'sqlite' will" Red
        Say "  refuse a migrated root. Check it by hand." Red
    }
    $env:ORGTREE_STORE = $null
}

function Release-Mutex {
    if ($script:mutexHeld -and $null -ne $script:mutex) {
        try { $script:mutex.ReleaseMutex() } catch { }
        $script:mutexHeld = $false
        Say "released the machine-wide deploy mutex"
    }
}

# ---- relaunch, without a pull or a build ----------------------------------
# update.ps1 -EnsureUp is the relaunch-only mode: the pull, the frontend build
# and pip are each inside `if (-not $EnsureUp)` (verified by reading), so it
# stops whatever holds the port and starts the backend and nothing else.  It
# exits 0 immediately when the port is ALREADY listening, so calling it when
# unsure is safe.
# ⚠ It takes the deploy mutex itself, so ours is released first.
function Invoke-EnsureUp([string]$why) {
    Release-Mutex
    Say "relaunching the backend: $why" Yellow
    Run "update.ps1 -EnsureUp" 'powershell' `
        ("-NoProfile -ExecutionPolicy Bypass -File " +
         (Q (Join-Path $Repo 'update.ps1')) + " -EnsureUp")
}

# ---- the two recoveries ----------------------------------------------------
function Recover-PreMigration([string]$why) {
    Banner "ABORTING -- NOTHING WAS MIGRATED" Red
    Say "WHY: $why" Red
    Say "" Red
    Say "STATE OF THE DATA ROOT: unchanged. It is still JSON, exactly as it was" Red
    Say "before this run started. No org was converted, nothing was deleted, and" Red
    Say "no export was needed or made." Red
    Say "" Red
    Say "WHAT HAPPENS NEXT: the backend comes back up as a JSON backend. The code" Red
    Say "in $Repo now defaults to SQLite, so it is started with ORGTREE_STORE=json" Red
    Say "pinned -- without that pin it meets the JSON root, refuses with" Red
    Say "MigrationRefused, and orgtree stays down." Red
    Say "" Red
    Say "THE CUTOVER DID NOT HAPPEN. It can be retried; fix the cause first." Red
    Set-JsonPin
    Invoke-EnsureUp "post-abort, JSON pinned"
    Test-BackendUp "after the pre-migration abort"
    if ($script:health -eq 0) {
        Banner "RECOVERED: orgtree is UP on JSON, carrying its orgs. The cutover did not happen." Green
        $script:rc = 10
        return
    }
    Banner "⚠ NOT RECOVERED: the JSON relaunch did not come up clean (health $($script:health))." Red
    Say "The root is still JSON and intact -- this is a process problem, not a data" Red
    Say "problem. Start the backend by hand, from $Repo\backend:" Red
    Say "  cmd /c `"set ORGTREE_STORE=json&& $($script:py) -m orgtree.api`"" Red
    $script:rc = 11
}

function Recover-PostMigration([string]$why) {
    Banner "DEPLOY FAILED AFTER THE MIGRATION SUCCEEDED" Red
    Say "WHY: $why" Red
    Say "" Red
    Say "STATE OF THE DATA ROOT: MIGRATED. orgs\ holds <slug>.db files and the old" Red
    Say "documents are parked as <slug>.json.premigration. This is NOT the same" Red
    Say "failure as an aborted cutover and it does NOT have the same fix: a JSON" Red
    Say "backend meeting this root refuses with BackendMismatch. DO NOT pin" Red
    Say "ORGTREE_STORE=json here." Red
    Say "" Red
    Say "Trying the cheapest correct thing first: the root and the code on disk" Red
    Say "AGREE (both SQLite), so a failed deploy does not by itself mean the store" Red
    Say "is wrong. Relaunching the build that is already on disk." Red

    if ($DrillSkipFirstRelaunch) {
        Say "DRILL: skipping the SQLite relaunch so the rollback rung is exercised" Yellow
        $script:health = 2
    } else {
        Invoke-EnsureUp "post-deploy-failure, SQLite (root and code agree)"
        Test-BackendUp "after relaunching the SQLite build"
    }
    if ($script:health -eq 0) {
        Banner "RECOVERED: orgtree is UP on SQLite, carrying its orgs." Green
        Say "THE CUTOVER STANDS. What failed was the deploy step, not the store." Green
        Say "Read the update.ps1 output above for the actual cause." Green
        $script:rc = 20
        return
    }

    Banner "SQLite would not come up either (health $($script:health)) -- ROLLING BACK" Red
    Say "This is the documented route home and it needs step 4's exports." Red
    Run "cutover.py rollback" $script:py `
        ((Q (Join-Path $Repo 'tools\cutover.py')) + " rollback " + (Q $Root))
    if ($script:rc -ne 0) {
        Banner "⚠⚠ ROLLBACK DID NOT COMPLETE (exit $($script:rc)). STOPPING." Red
        Say "NOTHING ELSE WILL BE STARTED. Read the rollback output above -- it names" Red
        Say "the exact state of the root and what to do about it." Red
        Say "It is written to fail CLOSED: while a database and a JSON document are" Red
        Say "both present NEITHER backend starts, and that is deliberate. Nothing has" Red
        Say "been lost. Do not start a backend by hand until the rollback finishes." Red
        $script:rc = 22
        return
    }
    Say "rollback complete: the root is JSON again and the databases are parked" Yellow
    Set-JsonPin
    Invoke-EnsureUp "post-rollback, JSON pinned"
    Test-BackendUp "after the rollback"
    if ($script:health -eq 0) {
        Banner "RECOVERED BY ROLLBACK: orgtree is UP on JSON. The cutover was undone." Green
        $script:rc = 21
        return
    }
    Banner "⚠ ROLLED BACK BUT NOT UP (health $($script:health))." Red
    Say "The data is safe and in JSON form; the process is simply not running." Red
    $script:rc = 23
}

# WHICH OF THE THREE STATES IS THIS ROOT IN?  Read from orgs\ itself, at the
# moment the recovery needs to know, because that is the thing that decides
# which backends will start:
#   json    only documents          -> JSON starts; SQLite refuses (pending)
#   sqlite  only databases          -> SQLite starts; JSON refuses (mismatch)
#   mixed   both                    -> NEITHER starts, by design
# `*.json` does not match `<slug>.json.premigration` or its stamped variants,
# which is what makes the migrated case read as `sqlite` and not as `mixed`.
function Get-RootState {
    $orgs = Join-Path $Root 'orgs'
    $dbs = @(Get-ChildItem (Join-Path $orgs '*.db') -ErrorAction SilentlyContinue)
    $docs = @(Get-ChildItem (Join-Path $orgs '*.json') -ErrorAction SilentlyContinue)
    Say "root state: $($dbs.Count) database(s), $($docs.Count) document(s) in orgs\"
    if ($dbs.Count -eq 0) { return 'json' }
    if ($docs.Count -eq 0) { return 'sqlite' }
    return 'mixed'
}

function Recover-Mixed([string]$why) {
    Banner "⚠⚠ THE MIGRATION STOPPED PART-WAY. NOTHING WILL BE STARTED." Red
    Say "WHY: $why" Red
    Say "" Red
    Say "STATE OF THE DATA ROOT: MIXED. orgs\ holds BOTH databases and documents." Red
    Say "Some orgs were converted and at least one was not." Red
    Say "" Red
    Say "NEITHER BACKEND WILL START ON THIS ROOT, and that is deliberate, not a" Red
    Say "malfunction: SQLite refuses because a document without a database looks" Red
    Say "like an unfinished migration, and JSON refuses because a database is" Red
    Say "present. Refusing is what stops an org silently disappearing from a" Red
    Say "backend that came up carrying only half the root." Red
    Say "" Red
    Say "NOTHING HAS BEEN LOST. Every converted org still has its .json.premigration" Red
    Say "and every unconverted org still has its .json." Red
    Say "" Red
    Say "Running the rollback tool now -- NOT to fix this by itself, but because it" Red
    Say "reads the root and names the exact two lists an operator needs. It will" Red
    Say "refuse to reconstruct anything without an explicit authorisation, which is" Red
    Say "correct: rebuilding an org's authority from an export must never be" Red
    Say "something a script decided was probably fine." Red
    Run "cutover.py rollback (diagnosis only)" $script:py `
        ((Q (Join-Path $Repo 'tools\cutover.py')) + " rollback " + (Q $Root))
    Banner "ORGTREE IS DOWN AND WILL STAY DOWN UNTIL AN OPERATOR ACTS." Red
    Say "Read the two lists above, confirm they are what you expect, then follow" Red
    Say "the command the tool printed. Do not start a backend by hand first." Red
    $script:rc = 30
}

function Recover([string]$why) {
    $st = Get-RootState
    Say "the wrapper believed migrated=$($script:migrated); the root says '$st'" `
        $(if (($st -eq 'sqlite') -eq $script:migrated) { 'Gray' } else { 'Yellow' })
    switch ($st) {
        'json'   { Recover-PreMigration $why }
        'sqlite' { Recover-PostMigration $why }
        default  { Recover-Mixed $why }
    }
}

# ===========================================================================
$exitCode = 0
try {
    # -- 0 - the machine-wide deploy mutex ---------------------------------
    # The SAME named mutex update.ps1 takes.  Holding it is not politeness:
    # `orgtree-ensure` fires every 5 minutes, sees a dead port, and would
    # launch a backend into the middle of the migration -- which would either
    # lose the race for the data root or, worse, win it.  -EnsureUp's early
    # "already up" exit happens before the mutex, but a DEAD port falls
    # straight through to it, which is exactly the case that matters here.
    Banner "step 0 -- claiming the machine-wide deploy mutex"
    $script:mutex = New-Object System.Threading.Mutex($false, 'Global\orgtree-update')
    $script:mutexHeld = $script:mutex.WaitOne(0)
    if (-not $script:mutexHeld) {
        Say "ANOTHER DEPLOY IS RUNNING. Refusing to race it on git, npm and the port." Red
        Say "Nothing was touched. Wait for it to finish, then run this again." Red
        $exitCode = 3
        exit $exitCode
    }
    Say "mutex held -- the 5-minute ensure task and any manual update.ps1 will now" Green
    Say "exit immediately rather than restarting the backend under us" Green

    Get-BackendPort
    Say "backend port $($script:port) (from $Root\.port, else the 7360 default)"
    # ⚠ TWO DIFFERENT THINGS DECIDE "THE PORT" AND update.ps1 USES BOTH.
    # It reads <root>\.port to decide which listener to stop and which port to
    # health-check, but the backend it starts binds `ORGTREE_PORT` (api.py:911,
    # default 7360) and nothing connects the two.  While a root's .port says
    # 7360 they agree by coincidence.  The moment it does not, update.ps1
    # stops the right process, starts the backend somewhere else, checks the
    # port the backend is not on, and reports the deploy failed -- with a
    # stray backend left running.  Said out loud, and made true by
    # construction, rather than relied on.
    $env:ORGTREE_PORT = $script:port
    if ($script:port -ne '7360') {
        Say "⚠ this root's .port is $($script:port), not the 7360 default. Setting" Yellow
        Say "  ORGTREE_PORT=$($script:port) so the backend update.ps1 starts binds the port" Yellow
        Say "  update.ps1 then health-checks. Without this they diverge silently." Yellow
    }

    # -- 1 - what should this install be carrying? -------------------------
    # Taken BEFORE the stop, for update.ps1's reason: this is the last moment
    # the outgoing process can be asked what it was serving, which is the
    # difference between "this cutover lost them" and "it was already like
    # that".  Kept rather than deleted -- it is evidence.
    Banner "step 1 -- snapshotting what this install should be carrying"
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
    $script:healthState = Join-Path $Root "cutover-health-$stamp.json"
    Run "deploy_health.py snapshot" $script:py `
        ((Q (Join-Path $Repo 'tools\deploy_health.py')) + " snapshot --data " +
         (Q $Root) + " --port " + $script:port + " --out " +
         (Q $script:healthState))
    if ($script:rc -ne 0) {
        Say "The snapshot failed, so nothing later can judge whether the backend came" Red
        Say "back carrying its orgs. That is a blind cutover. REFUSING; the backend is" Red
        Say "untouched and still running." Red
        Release-Mutex
        $exitCode = 5
        exit $exitCode
    }

    # -- 2 - stop the backend, and PROVE it stopped ------------------------
    Banner "step 2 -- stopping the backend"
    $oldPids = Get-Listeners $script:port
    if ($oldPids.Count -eq 0) {
        Say "nothing is listening on $($script:port) -- the backend is already down"
    } else {
        foreach ($p in $oldPids) {
            Say "stopping backend pid $p"
            try { Stop-Process -Id $p -Force -ErrorAction Stop } catch {
                Say "  Stop-Process on $p said: $_" Yellow
            }
        }
    }
    # ⚠ THE PORT GOING QUIET IS NOT PROOF.  A process can have released its
    # listener and still hold the data root's owner lock, and the migration
    # would then refuse; or the timing goes the other way and the migration
    # runs against something still writing.  The lock is the thing that
    # DECIDES, so the lock is what gets tested: acquire it in a throwaway
    # process and let go.  A check pinned to the port instead would keep
    # reading TRUE long after it stopped meaning anything.
    $deadline = (Get-Date).AddSeconds(45)
    $proved = $false
    while ((Get-Date) -lt $deadline) {
        $now = Get-Listeners $script:port
        if ($now.Count -eq 0) {
            Run "owner-lock probe" $script:py `
                ((Q (Join-Path $Repo 'tools\cutover_deploy.py')) +
                 " --probe-claim " + (Q $Root) + " --repo " + (Q $Repo))
            if ($script:rc -eq 0) { $proved = $true; break }
            Say "the port is free but the data root is still HELD -- waiting" Yellow
        } else {
            Say "still listening on $($script:port): $($now -join ', ') -- waiting" Yellow
        }
        Start-Sleep -Milliseconds 1000
    }
    if (-not $proved) {
        Say "COULD NOT PROVE THE BACKEND STOPPED within 45s. Not migrating." Red
        Recover "the backend would not stop, or something else still holds the data root"
        $exitCode = $script:rc
        Release-Mutex
        exit $exitCode
    }
    Say "PROVED STOPPED: nothing listens on $($script:port), and the data root's owner" Green
    Say "lock was acquired and released by a throwaway process" Green

    # -- 3 - migrate -------------------------------------------------------
    # ⚠ THE ROOT CHANGES HERE.  ORGTREE_MIGRATE lives in the CHILD's
    # environment and nowhere else: `cmd /c "set X=1&& ..."` dies with the
    # process, so the deployed backend can never inherit it and there is no
    # flag anyone has to remember to remove.
    Banner "step 3 -- MIGRATING $Root (the root changes here)"
    $cutover = Join-Path $Repo 'tools\cutover.py'
    # ⚠ ORGTREE_MIGRATE IS NEVER SET IN THIS PROCESS.  It lives in a one-shot
    # .cmd file's own environment and dies with that process, so no child this
    # wrapper spawns later -- no recovery relaunch, no deployed backend -- can
    # inherit it.  Setting it here and unsetting it afterwards would work right
    # up until the run that crashes in between, and that run would hand a
    # migrate-authorised environment to a backend.  The file is written into
    # the log so the operator can see the exact command that ran.
    $mcmd = Join-Path $script:childDir 'migrate.cmd'
    Set-Content -Path $mcmd -Encoding ascii -Value @(
        '@echo off',
        'set ORGTREE_MIGRATE=1',
        ('"' + $script:py + '" "' + $cutover + '" migrate "' + $Root + '"'))
    Say "the migrate command, verbatim:"
    foreach ($ln in (Get-Content $mcmd)) { Say "    $ln" }
    if ($DrillForceMigrateFail) {
        Say "DRILL: treating migrate as FAILED without running it" Yellow
        $script:rc = 1
    } else {
        Run "cutover.py migrate" 'cmd.exe' ('/c ' + (Q $mcmd))
    }
    if ($script:rc -ne 0) {
        # A migration that fails part-way leaves a MIXED root, and a mixed root
        # starts under NEITHER backend (SQLite calls the leftover .json pending
        # and refuses; JSON sees a .db and refuses).  Finishing the job is the
        # cheap correct move, and a plain re-run does exactly that -- it
        # re-attempts only what is still pending.
        Say "migrate exited $($script:rc) -- retrying ONCE, because a part-way root" Yellow
        Say "starts under neither backend and finishing the migration is the only" Yellow
        Say "cheap way out of that state" Yellow
        if ($DrillForceMigrateFail) {
            Say "DRILL: the retry fails too" Yellow
            $script:rc = 1
        } else {
            Run "cutover.py migrate (retry)" 'cmd.exe' ('/c ' + (Q $mcmd))
        }
    }
    if ($script:rc -ne 0) {
        Recover "cutover.py migrate failed twice (exit $($script:rc))"
        $exitCode = $script:rc
        Release-Mutex
        exit $exitCode
    }
    $script:migrated = $true
    Say "MIGRATED. From this line on the root holds databases, a JSON backend will" Green
    Say "REFUSE it, and the only route home is cutover.py rollback." Green

    # -- 4 - export-verify -------------------------------------------------
    Banner "step 4 -- export-verify (this is what makes a rollback possible)"
    if ($DrillForceExportFail) {
        Say "DRILL: treating export-verify as FAILED" Yellow
        $script:rc = 1
    } else {
        Run "cutover.py export-verify" $script:py `
            ((Q $cutover) + " export-verify " + (Q $Root))
    }
    if ($script:rc -ne 0) {
        Say "EXPORT-VERIFY FAILED. Per the runbook the flip build DOES NOT START." Red
        Recover "cutover.py export-verify failed (exit $($script:rc)) -- an org did not survive a round trip out of SQLite"
        $exitCode = $script:rc
        Release-Mutex
        exit $exitCode
    }
    Say "every org exported and re-read. The exports are in $Root\exports\." Green

    if ($DrillNoDeploy) {
        Banner "DRILL: stopping before the deploy, as asked" Yellow
        Say "The root IS migrated and IS exported. Nothing was pulled, built or started." Yellow
        Release-Mutex
        $exitCode = 0
        exit $exitCode
    }

    # -- 5 - deploy --------------------------------------------------------
    # update.ps1 owns the pull, the build and the restart, and takes the mutex
    # itself, so ours is released first.  Its own health check is the verdict,
    # and it is checked again here independently.
    Banner "step 5 -- deploying (update.ps1: pull, build, restart, health-check)"
    Clear-JsonPin
    Release-Mutex
    Run "update.ps1" 'powershell' `
        ("-NoProfile -ExecutionPolicy Bypass -File " +
         (Q (Join-Path $Repo 'update.ps1')))
    if ($script:rc -ne 0) {
        Recover "update.ps1 exited $($script:rc)"
        $exitCode = $script:rc
        exit $exitCode
    }
    Test-BackendUp "after the deploy"
    if ($script:health -ne 0) {
        Recover "update.ps1 exited 0 but the independent health check said $($script:health)"
        $exitCode = $script:rc
        exit $exitCode
    }

    Banner "CUTOVER COMPLETE -- orgtree is UP on SQLite, carrying its orgs" Green
    Say "root      $Root   (now <slug>.db; the old documents are parked as" Green
    Say "          <slug>.json.premigration and are NOT a rollback route)" Green
    Say "exports   $Root\exports\  -- THIS is the rollback route. Keep it." Green
    Say "rollback  $($script:py) $cutover rollback $Root" Green
    $exitCode = 0
} catch {
    Say "UNHANDLED ERROR: $_" Red
    Say "$($_.ScriptStackTrace)" Red
    Recover "the wrapper itself hit an unhandled error: $_"
    $exitCode = $script:rc
} finally {
    Release-Mutex
    Say "wrapper exiting $exitCode"
}
exit $exitCode
