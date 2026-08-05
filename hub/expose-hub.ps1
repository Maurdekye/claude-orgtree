# expose-hub.ps1 - expose the mail hub's PUBLIC listener (FR-10) to the
# internet via a Cloudflare quick tunnel, so remote orgtree instances and
# hubtool chats can register and exchange mail across the open internet.
#
# ⚠ This tunnels the API-ONLY listener (host port 7378 by default), never
# the full hub port 7370: the full port serves the UNAUTHENTICATED all-mail
# UI at "/", and tunnelling it would publish every org's correspondence to
# anyone holding the URL. Every /api/* route is gated on the caller's own
# org secret, which is what makes the public listener safe to expose.
#
# Setup (one time): start the hub with the public listener enabled -
#     cd hub
#     $env:HUB_PUBLIC = "1"; docker compose up -d --build
# Then:  .\expose-hub.ps1          (tunnels host port 7378)
#        .\expose-hub.ps1 -Port <host port>   if you remapped it
#
# Remote clients use the printed https URL as their hub address (an org's
# settings -> mailserver, or hubtool's HUB env var).
param([int]$Port = 7378)
$ErrorActionPreference = "Stop"

if ($Port -in 7370) {
    Write-Host "refusing to tunnel port $Port - that is the FULL hub, whose"
    Write-Host "'/' is an unauthenticated view of every org's mail. Enable"
    Write-Host "the API-only public listener instead (HUB_PUBLIC=1, host"
    Write-Host "port 7378) and tunnel that."
    exit 1
}

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

# is the public listener up?
$up = $false
try {
    $c = New-Object Net.Sockets.TcpClient
    $c.Connect("127.0.0.1", $Port); $up = $c.Connected; $c.Close()
} catch {}
if (-not $up) {
    Write-Host "nothing is listening on port $Port."
    Write-Host "start the hub with its public listener enabled:"
    Write-Host "  cd hub; `$env:HUB_PUBLIC = `"1`"; docker compose up -d --build"
    exit 1
}

Write-Host "opening tunnel -> http://localhost:$Port   (Ctrl+C closes it; the URL dies with it)"
try {
    # cloudflared logs its banner to stderr; under PS 5.1 + Stop that first
    # line becomes a terminating NativeCommandError (see expose.ps1)
    $ErrorActionPreference = "Continue"
    & $cfd tunnel --url "http://localhost:$Port" 2>&1 | ForEach-Object {
        $line = "$_"
        if ($line -match "https://[a-z0-9-]+\.trycloudflare\.com") {
            $url = $Matches[0]
            Write-Host ""
            Write-Host "==============================================================="
            Write-Host "  HUB PUBLIC ADDRESS:  $url"
            Write-Host "  remote orgs: settings -> mailserver -> add this address"
            Write-Host "  remote chats: `$env:MAILHUB_URL = `"$url`" before hubtool"
            Write-Host "  (API only - the hub's own mail UI stays private)"
            Write-Host "==============================================================="
            Write-Host ""
        }
        $line
    }
} finally {
    $ErrorActionPreference = "Stop"
}
