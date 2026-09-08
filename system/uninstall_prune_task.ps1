# Stops and removes the "EKKO Prune Captures" scheduled task. Does not
# touch logs, listener\captures\, or prune_captures.flags.txt.

$ErrorActionPreference = "SilentlyContinue"

$taskName = "EKKO Prune Captures"

Stop-ScheduledTask -TaskName $taskName
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false

$ErrorActionPreference = "Stop"

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Write-Host "Task '$taskName' still present -- removal may have failed."
} else {
    Write-Host "Task '$taskName' removed."
}
