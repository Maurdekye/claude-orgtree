# orgtree autostart installer (Windows) -- F-06 §9, user-ruled.
#   powershell -ExecutionPolicy Bypass -File tools\install-autostart.ps1
#   powershell -ExecutionPolicy Bypass -File tools\install-autostart.ps1 -Uninstall
#
# Registers ONE scheduled task, "orgtree", with TWO triggers:
#   * at LOGON            -> the full deploy (update.ps1) -- boot-start
#   * every 5 MINUTES     -> update.ps1 -EnsureUp         -- crash-restart
# The split exists because update.ps1 DETACHES the backend and exits: Task
# Scheduler's own restart-on-failure watches a task that already succeeded,
# so it never notices the backend dying. The repeating -EnsureUp probe is the
# watchdog: listener alive -> silent exit 0; dead -> relaunch only.
#
# §9.1 specifics, set programmatically:
#   * runs ONLY when the user is logged on (the CLI reads
#     ~/.claude/.credentials.json from the USER profile; LocalSystem resolves
#     a different ~ and every turn fails, confusingly late)
#   * the default 3-day execution stop limit is REMOVED
# Still manual, still required for a truly unattended box:
#   * auto-login for this user (the trigger fires at logon)
#   * Docker Desktop set to start at login, if any org is sandboxed
param([switch]$Uninstall)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$updater = Join-Path $root 'update.ps1'
$name = 'orgtree'

if ($Uninstall) {
    foreach ($t in "$name-deploy", "$name-ensure") {
        Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
    }
    Write-Host "removed scheduled tasks '$name-deploy' and '$name-ensure'"
    exit 0
}
if (-not (Test-Path $updater)) { Write-Host "update.ps1 not found at $updater" -ForegroundColor Red; exit 1 }

$ps = 'powershell.exe'
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$every5 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$deploy = New-ScheduledTaskAction -Execute $ps `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$updater`""
$ensure = New-ScheduledTaskAction -Execute $ps `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$updater`" -EnsureUp"
# ExecutionTimeLimit 0 = the 3-day stop limit is OFF (an unattended backend
# would otherwise be killed silently on day three)
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive

# one task, two actions is wrong (actions run IN SEQUENCE) -- register two
# tasks sharing the name prefix instead: the logon deploy and the watchdog
Register-ScheduledTask -TaskName "$name-deploy" -Trigger $logon -Action $deploy `
    -Settings $settings -Principal $principal -Force | Out-Null
Register-ScheduledTask -TaskName "$name-ensure" -Trigger $every5 -Action $ensure `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "registered:"
Write-Host "  $name-deploy  at logon        -> full update.ps1 (boot-start)"
Write-Host "  $name-ensure  every 5 minutes -> update.ps1 -EnsureUp (crash-restart)"
Write-Host ""
Write-Host "still manual for a fully unattended box:" -ForegroundColor Yellow
Write-Host "  * enable auto-login for $env:USERNAME (the logon trigger fires then)"
Write-Host "  * Docker Desktop 'start at login', if any org is sandboxed"
Write-Host "uninstall: tools\install-autostart.ps1 -Uninstall (removes both)"
