# expose.ps1 - expose the kiosk public listener to the internet via a
# Cloudflare quick tunnel (TryCloudflare). No account, no port forwarding,
# no static URL: the tunnel lives while this window is open, and the random
# *.trycloudflare.com hostname dies with it. Kiosk share URLs on the admin
# dashboard automatically pick up the live hostname.
#
# Usage:  .\expose.ps1            (public listener on the default port 7361)
#         .\expose.ps1 -Port 7362
#
# The backend must be running with ORGTREE_PUBLIC_PORT set (update.ps1 does
# this by default).
param([int]$Port = 7361)
$ErrorActionPreference = "Stop"

$data = if ($env:ORGTREE_DATA) { $env:ORGTREE_DATA } else { Join-Path $env:USERPROFILE "orgtree" }
if (-not (Test-Path $data)) { New-Item -ItemType Directory -Force $data | Out-Null }
$cfd = Join-Path $data "cloudflared.exe"
$originFile = Join-Path $data ".public_origin"

if (-not (Test-Path $cfd)) {
    Write-Host "downloading cloudflared (one time)..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing `
        -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" `
        -OutFile $cfd
}

# is the public listener up?
$up = $false
try {
    $c = New-Object Net.Sockets.TcpClient
    $c.Connect("127.0.0.1", $Port); $up = $c.Connected; $c.Close()
} catch {}
if (-not $up) {
    Write-Host "the public listener is not up on port $Port."
    Write-Host "start the backend with the public port enabled, e.g.:"
    Write-Host "  `$env:ORGTREE_PUBLIC_PORT = `"$Port`"; python -m orgtree.api"
    Write-Host "(or just run update.ps1, which sets it)"
    exit 1
}

Write-Host "opening tunnel -> http://localhost:$Port   (Ctrl+C closes it; the URL dies with it)"
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
            Set-Content -Path $originFile -Value $url -Encoding Ascii
            Write-Host ""
            Write-Host "==============================================================="
            Write-Host "  PUBLIC ORIGIN:  $url"
            Write-Host "  kiosk share URLs on the dashboard now use this host."
            Write-Host "  a kiosk link looks like:  $url/k/<token>"
            Write-Host "==============================================================="
            Write-Host ""
        }
        $line
    }
} finally {
    $ErrorActionPreference = "Stop"
    Remove-Item -Path $originFile -ErrorAction SilentlyContinue
}
