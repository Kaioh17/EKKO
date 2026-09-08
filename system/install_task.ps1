# Registers (or re-registers) the "EKKO Listener" Windows scheduled task:
# starts run_listener.ps1 at logon, in the interactive user session (mic
# access needs that -- a SYSTEM-level service can't see per-user audio
# devices), hidden, with no time limit.
#
# Safe to re-run: Register-ScheduledTask -Force replaces any existing
# registration of the same name.
#
# Run this in a normal (non-admin) PowerShell as the user who will be
# logged in when EKKO should be listening.

$ErrorActionPreference = "Stop"

$taskName = "EKKO Listener"
$repoRoot = Split-Path -Parent $PSScriptRoot
$wrapper  = Join-Path $repoRoot "system\run_listener.ps1"
$user     = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$wrapper`""

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user

$settings = New-ScheduledTaskSettingsSet `
    -Hidden `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "EKKO always-on voice listener (VAD -> wake word -> speaker verification -> transcription -> routing)." `
    -Force | Out-Null

Write-Host "Registered scheduled task '$taskName' for user $user, trigger: at logon."
Write-Host "It will start automatically at your next logon."
Write-Host "To start it right now without logging off: system\start.ps1"
Write-Host "Current mode (system\listener.flags.txt):"
Get-Content (Join-Path $repoRoot "system\listener.flags.txt") | Where-Object { $_ -and -not $_.StartsWith("#") }
