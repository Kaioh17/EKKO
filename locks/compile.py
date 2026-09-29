"""Regenerates the hash-locked requirement files (needs `uv` on PATH):

    python locks/compile.py

Per-platform because PyTorch's CPU wheels differ from PyPI's, and a
universal lock can't hash both. Install a lock with uv, same flags, so it
resolves torch from the same index it was locked against:

    uv pip install --require-hashes -r locks/windows.txt \\
        --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CPU = ["--index-url", "https://download.pytorch.org/whl/cpu", "--extra-index-url", "https://pypi.org/simple"]

JOBS = [
    ("windows", "requirements.txt", CPU),
    ("linux", "requirements.txt", CPU),
    ("dev", "requirements-dev.in", []),
]

for platform, source, index in JOBS:
    target = "" if platform == "dev" else platform
    cmd = ["uv", "pip", "compile", source, "--generate-hashes", "--python-version", "3.12", "-o", f"locks/{platform}.txt", *index]
    cmd += ["--python-platform", target] if target else ["--universal"]
    print(" ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)
