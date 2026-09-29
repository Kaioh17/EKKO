"""Tracked files must not carry this machine's identity or secrets."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    re.compile(r"[A-Za-z]:\\\\?Users\\\\?(?!<|%|\$|Public|yourname)[A-Za-z0-9_.-]+", re.I),
    re.compile(r"/home/(?!<|runner\b|\$|yourname)[a-z0-9_-]+"),
    re.compile(r"\bmubsk\b", re.I),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\bsk-(ant-)?[A-Za-z0-9_-]{20,}"),
]
SKIP = {"tests/test_no_leaks.py"}
BINARY = {".png", ".ico", ".icns", ".woff", ".woff2", ".ttf", ".otf", ".onnx", ".pt", ".wav", ".jpg", ".jpeg", ".gif"}


def test_no_personal_paths_or_keys():
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
    hits = []
    for rel in filter(None, files):
        path = ROOT / rel
        if rel in SKIP or path.suffix.lower() in BINARY or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pat in PATTERNS:
            for m in pat.finditer(text):
                hits.append(f"{rel}: {m.group(0)}")
    assert not hits, "\n".join(hits)
