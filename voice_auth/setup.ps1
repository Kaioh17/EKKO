# One-time environment setup for the native Windows venv (`pvenv`, repo
# root). Safe to re-run: creates the venv only if it's missing, and pip
# install is idempotent.
#
# Run from anywhere; paths below are anchored to the repo root regardless
# of cwd.

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $repoRoot "pvenv"
$pythonExe = Join-Path $venvPath "Scripts\python.exe"
$requirements = Join-Path $repoRoot "requirements.txt"

if (-not (Test-Path $pythonExe)) {
    Write-Host "Creating venv at $venvPath ..."
    python -m venv $venvPath
} else {
    Write-Host "Reusing existing venv at $venvPath"
}

Write-Host "Upgrading pip ..."
& $pythonExe -m pip install --upgrade pip

# cu121 (the version originally targeted, see readme.md's Stack table)
# no longer has wheels published for current torch releases -- verified
# empty on the cu121 index for anything near current. cu128 is the
# newest channel with a matching torch+torchaudio CUDA build pair as of
# this writing (2.11.0), safely below the installed driver's CUDA
# ceiling. Pinned explicitly: the unpinned "latest" on this index skews
# older than PyPI's plain "torch" (CPU-only) latest, so leaving it
# unpinned would silently pick up an old release.
Write-Host "Installing CUDA-enabled PyTorch (cu128) ..."
& $pythonExe -m pip install "torch==2.11.0+cu128" "torchaudio==2.11.0+cu128" --index-url https://download.pytorch.org/whl/cu128

Write-Host "Installing the rest of requirements.txt ..."
& $pythonExe -m pip install -r $requirements

Write-Host ""
Write-Host "Checking GPU visibility to torch ..."
& $pythonExe -c "import torch; print('CUDA available:', torch.cuda.is_available()); print('Device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU only')"

Write-Host ""
Write-Host "Setup complete. If CUDA available is False above, torch fell"
Write-Host "back to CPU -- verification/transcription will still work, just slower."
