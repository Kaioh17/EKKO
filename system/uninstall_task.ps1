# Stops and removes the "EKKO Listener" scheduled task. Does not touch
# logs or settings -- re-running install_task.ps1 later picks them back up.
$ErrorActionPreference = "SilentlyContinue"

$taskName = "EKKO Listener"

Stop-ScheduledTask -TaskName $taskName
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false

$ErrorActionPreference = "Stop"

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Write-Host "Task '$taskName' still present -- removal may have failed."
} else {
    Write-Host "Task '$taskName' removed."
}
