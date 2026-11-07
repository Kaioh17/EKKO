<#
.SYNOPSIS
    Speaker volume for EKKO: up / down / mute / unmute / a level 10-100.
    Thin wrapper over scripts/system_audio.py (pycaw), which owns the
    logic, logging and exit codes.

.PARAMETER Action
    up, down, mute, unmute, or a number 0-100.

.NOTES
    EXIT CODES: 0 done, 3 already at that level, 1 failed / pycaw missing.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^(up|down|mute|unmute|\d{1,3})$')]
    [string]$Action
)

$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$python = Join-Path $repoRoot 'pvenv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $python) {
    [Console]::Error.WriteLine('volume_control: no pvenv or python found')
    exit 1
}

& $python (Join-Path $repoRoot 'scripts\system_audio.py') $Action
exit $LASTEXITCODE
