# orgtree update script -- pull the latest changes and redeploy.
#   powershell -ExecutionPolicy Bypass -File update.ps1
# (or run update.cmd). Works in Windows PowerShell 5.1.
#
# Steps: git pull -> npm install + build the UI -> pip install -> restart the
# backend (which serves the built UI) -> health-check.

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

# -- 2 - frontend -----------------------------------------------------------
Write-Host "`n== building the UI =="
Set-Location (Join-Path $root 'frontend')
npm install --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { Write-Host "npm install failed" -ForegroundColor Red; exit 1 }
npm run build
if ($LASTEXITCODE -ne 0) { Write-Host "UI build failed" -ForegroundColor Red; exit 1 }
Set-Location $root

# -- 3 - backend deps -------------------------------------------------------
Write-Host "`n== python deps =="
python -m pip install -q -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "pip install failed" -ForegroundColor Red; exit 1 }

# -- 4 - restart the backend ------------------------------------------------
$dataRoot = $env:ORGTREE_DATA
if (-not $dataRoot) { $dataRoot = Join-Path $env:USERPROFILE 'orgtree' }
$port = '7360'
$portFile = Join-Path $dataRoot '.port'
if (Test-Path $portFile) { $port = (Get-Content $portFile -Raw).Trim() }

Write-Host "`n== restarting the backend (port $port) =="
$conn = Get-NetTCPConnection -LocalPort ([int]$port) -State Listen -ErrorAction SilentlyContinue
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
Start-Process -FilePath 'python' -ArgumentList '-m', 'orgtree.api' `
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
if ($ok) {
    Write-Host "`n== up: http://localhost:$port ($after) ==" -ForegroundColor Green
} else {
    Write-Host "`nbackend did not come up -- check $errLog" -ForegroundColor Red
    exit 1
}
