# Stops and removes the "EKKO Restart" scheduled task. Does not touch
# system\logs\restart.log or the listener itself (whatever state it was
# in when this runs is left alone).

$ErrorActionPreference = "SilentlyContinue"

$taskName = "EKKO Restart"

Stop-ScheduledTask -TaskName $taskName
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false

$ErrorActionPreference = "Stop"

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Write-Host "Task '$taskName' still present -- removal may have failed."
} else {
    Write-Host "Task '$taskName' removed."
}
