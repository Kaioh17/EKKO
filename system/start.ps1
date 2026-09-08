# Manually triggers the "EKKO Listener" scheduled task right now, without
# waiting for logoff/logon. Useful for testing the wrapper/action itself
# -- it does NOT exercise the AtLogOn trigger, do at least one real
# logon cycle before trusting that part.

$ErrorActionPreference = "Stop"

Start-ScheduledTask -TaskName "EKKO Listener"
Write-Host "Start requested. Check status with system\status.ps1 in a few seconds."
