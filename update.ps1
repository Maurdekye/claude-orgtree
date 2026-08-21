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
    # it shipped; never passed by the self-update path.
    [switch]$AllowDirty
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

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
$dirty = (git status --porcelain) | Out-String
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
$lockDirtyBefore = ((git status --porcelain -- frontend/package-lock.json) | Out-String).Trim()
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
node -e "require('esbuild').transformSync('let x=1')" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "esbuild is not usable -- clean reinstall (npm optional-deps bug)" -ForegroundColor Yellow
    Remove-Item -Recurse -Force 'node_modules' -ErrorAction SilentlyContinue
    npm install --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { Write-Host "npm install failed" -ForegroundColor Red; exit 1 }
    node -e "require('esbuild').transformSync('let x=1')" 2>$null
    if ($LASTEXITCODE -ne 0) {
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

# -- 3b - CLI version (No.44) ----------------------------------------------
# Sandbox images are tagged with the host CLI's version and rebuild on demand
# when it changes -- nothing to do here beyond reporting it.
try {
    $cliVer = (& claude --version 2>$null | Select-Object -First 1)
    if ($cliVer) { Write-Host "Claude CLI: $cliVer (sandbox images rebuild automatically when this changes)" }
} catch {}
}   # end -not $EnsureUp (sections 2-3b)

# -- 4 - restart the backend ------------------------------------------------
$dataRoot = $env:ORGTREE_DATA
if (-not $dataRoot) { $dataRoot = Join-Path $env:USERPROFILE 'orgtree' }
$port = '7360'
$portFile = Join-Path $dataRoot '.port'
if (Test-Path $portFile) { $port = (Get-Content $portFile -Raw).Trim() }

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
$ok = $false
foreach ($i in 1..20) {
    Start-Sleep -Milliseconds 500
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 "http://127.0.0.1:$port/api/orgs"
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch {}
}
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
if ($ok) {
    Write-Host "`n== up: http://localhost:$port ($after) ==" -ForegroundColor Green
} else {
    Write-Host "`nbackend did not come up -- check $errLog" -ForegroundColor Red
    exit 1
}

} finally {
    if ($mutexHeld) { [void]$mutex.ReleaseMutex() }
    $mutex.Dispose()
}
