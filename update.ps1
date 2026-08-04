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
    [switch]$ExposeAdmin
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$before = (git rev-parse --short HEAD).Trim()
Write-Host "== orgtree update (currently $before) =="

# -- 1 - pull ---------------------------------------------------------------
git pull --ff-only
if ($LASTEXITCODE -ne 0) {
    Write-Host "git pull failed -- resolve manually (local changes?)" -ForegroundColor Red
    exit 1
}
$after = (git rev-parse --short HEAD).Trim()
if ($after -eq $before) {
    Write-Host "already up to date ($after) -- redeploying anyway"
} else {
    Write-Host "updated $before -> $after"
    git --no-pager log --oneline "$before..$after"
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

# -- 2 - frontend -----------------------------------------------------------
Write-Host "`n== building the UI =="
Set-Location (Join-Path $root 'frontend')
npm install --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { Write-Host "npm install failed" -ForegroundColor Red; exit 1 }

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
