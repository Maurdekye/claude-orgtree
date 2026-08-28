# expose-full.ps1 - expose the orgtree ADMIN listener (normal operating
# mode, NOT kiosk) to the internet via a Cloudflare quick tunnel
# (TryCloudflare). No account, no port forwarding, no static URL: the tunnel
# lives while this window is open, and the random *.trycloudflare.com
# hostname dies with it.
#
# ⚠ THE ADMIN LISTENER HAS NO PASSWORD, NO TOKEN AND NO LOGIN OF ANY KIND.
# Reaching the port IS the credential. Anyone who has the tunnel URL this
# script prints can do anything an operator can do here: read and write
# every org, hire agents, grant folders on this machine to them, and run
# commands through their turns. This script does not add any authentication
# in front of that — it only makes the existing, already-unauthenticated
# listener reachable from the internet instead of just this machine.
#
# This is deliberately different from expose.ps1, which tunnels the KIOSK
# gateway instead: that listener only serves preauthenticated /k/<token>
# links for one org at a time, with hard credit/spend/scope limits. This
# script exposes everything, with no limits. Only run it if that is what you
# mean to do, and only for as long as you mean to do it.
#
# Usage:  .\expose-full.ps1            (tunnels the admin port, default 7360)
#         .\expose-full.ps1 -Port 7362
#
# Unlike the kiosk gateway, the admin listener does not need
# ORGTREE_PUBLIC_PORT set - it's up whenever the backend is running at all
# (update.ps1 starts it by default). It normally binds to loopback
# (127.0.0.1) only; that is NOT a blocker here, because cloudflared runs on
# this same machine and reaches 127.0.0.1 directly - the tunnel does not
# need ORGTREE_EXPOSE_ADMIN=1 or a 0.0.0.0 bind to work.
param([int]$Port = 7360)
$ErrorActionPreference = "Stop"

$data = if ($env:ORGTREE_DATA) { $env:ORGTREE_DATA } else { Join-Path $env:USERPROFILE "orgtree" }
if (-not (Test-Path $data)) { New-Item -ItemType Directory -Force $data | Out-Null }
$cfd = Join-Path $data "cloudflared.exe"

if (-not (Test-Path $cfd)) {
    Write-Host "downloading cloudflared (one time)..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing `
        -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" `
        -OutFile $cfd
}

# is the admin listener up?
$up = $false
try {
    $c = New-Object Net.Sockets.TcpClient
    $c.Connect("127.0.0.1", $Port); $up = $c.Connected; $c.Close()
} catch {}
if (-not $up) {
    Write-Host "the admin listener is not up on port $Port."
    Write-Host "start the backend, e.g.:  python -m orgtree.api"
    Write-Host "(or just run update.ps1)"
    exit 1
}

Write-Host ""
Write-Host "==============================================================="
Write-Host "  WARNING: this exposes the ADMIN interface (normal mode), not"
Write-Host "  kiosk. It has NO password, NO token, NO login. Whoever holds"
Write-Host "  the URL below has full control of every org on this machine,"
Write-Host "  including running commands through its agents."
Write-Host "  Ctrl+C closes the tunnel; the URL dies with it."
Write-Host "==============================================================="
Write-Host ""
Write-Host "opening tunnel -> http://localhost:$Port"
try {
    # cloudflared logs EVERYTHING to stderr, even its greeting banner. Under
    # Windows PowerShell 5.1, 2>&1 wraps each stderr line in an ErrorRecord,
    # and with ErrorActionPreference=Stop the FIRST banner line became a
    # terminating NativeCommandError that killed the tunnel instantly.
    # Continue lets the records flow down the pipeline, where "$_" below
    # stringifies them like any other output line.
    $ErrorActionPreference = "Continue"
    & $cfd tunnel --url "http://localhost:$Port" 2>&1 | ForEach-Object {
        $line = "$_"
        if ($line -match "https://[a-z0-9-]+\.trycloudflare\.com") {
            $url = $Matches[0]
            Write-Host ""
            Write-Host "==============================================================="
            Write-Host "  PUBLIC ORIGIN (ADMIN, NO AUTH):  $url"
            Write-Host "  anyone with this URL has full control of this machine's orgs."
            Write-Host "==============================================================="
            Write-Host ""
        }
        $line
    }
} finally {
    $ErrorActionPreference = "Stop"
}
