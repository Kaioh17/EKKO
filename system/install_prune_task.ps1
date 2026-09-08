# Registers (or re-registers) the "EKKO Prune Captures" scheduled task:
# runs prune_captures.ps1 (which wraps listener\prune_captures.py) once a
# day and at every logon, whichever comes first, so listener\captures\
# doesn't grow unbounded from the always-on listener saving one WAV per
# speech segment.
#
# Two triggers rather than one: AtLogOn is the reliable one on a laptop
# that gets shut down/rebooted regularly, but if it stays logged in
# across several days without a fresh logon, the Daily trigger is what
# still catches it.
#
# Safe to re-run: Register-ScheduledTask -Force replaces any existing
# registration of the same name.

$ErrorActionPreference = "Stop"

$taskName = "EKKO Prune Captures"
$repoRoot = Split-Path -Parent $PSScriptRoot
$wrapper  = Join-Path $repoRoot "system\prune_captures.ps1"
$user     = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$wrapper`""

$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $user),
    (New-ScheduledTaskTrigger -Daily -At 4:00AM)
)

$settings = New-ScheduledTaskSettingsSet `
    -Hidden `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName `
    -Action $action -Trigger $triggers -Settings $settings -Principal $principal `
    -Description "Prunes listener\captures\ down to the newest N speech segments (listener\prune_captures.py)." `
    -Force | Out-Null

Write-Host "Registered scheduled task '$taskName' for user $user, triggers: at logon and daily at 4:00 AM."
Write-Host "Run it once right now to test: Start-ScheduledTask -TaskName '$taskName'"
Write-Host "Log: system\logs\prune.log"
