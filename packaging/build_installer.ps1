# Builds the installed app from this repo and prints the installer path.
#   .\packaging\build_installer.ps1            # build only
#   .\packaging\build_installer.ps1 -Install   # build, then run the installer over your installed copy
# Same steps as .github/workflows/release.yml, minus updater signing (the
# local build is installed by hand, not served to the auto-updater).
param([switch]$Install)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$py = "pvenv\Scripts\python.exe"
& $py -c "import importlib.util, sys; sys.exit(importlib.util.find_spec('PyInstaller') is None)"
if ($LASTEXITCODE -ne 0) { & $py -m pip install pyinstaller }

& $py -m PyInstaller packaging/ekko-backend.spec --noconfirm --distpath dist-backend --workpath build-backend
if ($LASTEXITCODE) { throw "PyInstaller failed" }

if (-not (Test-Path "ekko-ui\node_modules")) { pnpm -C ekko-ui install }
$local = Join-Path $env:TEMP "ekko-tauri-local.json"
'{"bundle":{"createUpdaterArtifacts":false}}' | Set-Content $local -Encoding ascii
pnpm -C ekko-ui tauri build --config src-tauri/tauri.release.conf.json --config $local
if ($LASTEXITCODE) { throw "tauri build failed" }

$exe = Get-ChildItem ekko-ui\src-tauri\target\release\bundle\nsis\*-setup.exe |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host "Installer: $($exe.FullName)"
if ($Install) { Start-Process $exe.FullName }
