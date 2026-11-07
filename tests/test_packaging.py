"""The PyInstaller spec can't be run here, so check that it asks for what the
listener imports lazily: PyInstaller only bundles what it can see."""

import re
from pathlib import Path

SPEC = (Path(__file__).resolve().parents[1] / "packaging" / "ekko-backend.spec").read_text(encoding="utf-8")


def data_packages() -> str:
    """The `for pkg in (...)` line whose packages get their data files collected."""
    return next(line for line in SPEC.splitlines() if line.startswith("for pkg in") and "faster_whisper" in line)


def submodule_packages() -> set[str]:
    return set(re.findall(r'collect_submodules\("([\w.]+)"\)', SPEC))


def test_spec_bundles_the_parakeet_runtime():
    # listener/stt.py imports onnx_asr inside load_parakeet_model; without
    # these the installed app falls back to Whisper with "No module named onnx_asr".
    assert "onnx_asr" in submodule_packages()
    assert '"onnx_asr"' in data_packages()  # its bundled preprocessing models


def test_spec_bundles_the_model_downloader():
    # backend/routers/stt.py -> listener/stt_models.py imports huggingface_hub inside a function
    assert "huggingface_hub" in submodule_packages()


def test_spec_keeps_the_hotkey_library():
    assert '"keyboard"' in SPEC
