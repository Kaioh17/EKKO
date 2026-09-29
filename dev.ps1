# One command for development: the desktop app plus its backend (and the
# voice listener, unless -NoListener). `tauri dev` starts the backend from
# pvenv\ itself and stops it when the window closes.
#   .\dev.ps1               # app + backend + listener
#   .\dev.ps1 -NoListener   # app + backend only (no microphone)
param([switch]$NoListener)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path "pvenv\Scripts\python.exe")) {
    py -3 -m venv pvenv
    pvenv\Scripts\python.exe -m pip install -r requirements.txt
}
if (-not (Test-Path "ekko-ui\node_modules")) { pnpm -C ekko-ui install }

if ($NoListener) { $env:EKKO_NO_LISTENER = "1" }
pnpm -C ekko-ui tauri dev
