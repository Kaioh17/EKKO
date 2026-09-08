# Registers (or re-registers) the "EKKO Restart" scheduled task: runs
# restart.ps1 (stop.ps1 -> start.ps1, with restart_ui.py's popup and a
# system\logs\restart.log entry per run) every 3 hours, indefinitely,
# starting at first logon. Interactive, not SYSTEM -- restart.ps1 shells
# out to stop.ps1/start.ps1, which act on the interactive "EKKO
# Listener" task, and the popup itself needs a visible desktop session.
#
# Safe to re-run: Register-ScheduledTask -Force replaces any existing
# registration of the same name.

$ErrorActionPreference = "Stop"

$taskName = "EKKO Restart"
$repoRoot = Split-Path -Parent $PSScriptRoot
$wrapper  = Join-Path $repoRoot "system\restart.ps1"
$user     = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$wrapper`""

# Fires at next logon, then repeats every 3 hours for a year -- Task
# Scheduler has no "repeat forever," a long-but-finite duration is the
# standard workaround and gets re-upped every time this script re-runs.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Hours 3) `
    -RepetitionDuration (New-TimeSpan -Days 365)).Repetition

$settings = New-ScheduledTaskSettingsSet `
    -Hidden `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Restarts the EKKO listener (stop -> start) every 3 hours, logging to system\logs\restart.log." `
    -Force | Out-Null

Write-Host "Registered scheduled task '$taskName' for user $user, repeating every 3 hours."
Write-Host "Run it once right now to test: Start-ScheduledTask -TaskName '$taskName'"
Write-Host "Log: system\logs\restart.log"
