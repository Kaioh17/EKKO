# PyInstaller spec for the bundled backend + listener (one exe, onedir).
#   pyinstaller packaging/ekko-backend.spec --noconfirm --distpath dist-backend --workpath build-backend
# Output: dist-backend/ekko-backend/, which tauri.release.conf.json ships as
# a resource next to the desktop app.
#
# Not bundled (downloaded or created at first run, or private):
#   listener/models/*.onnx, feedback/models/*.onnx   model weights
#   personal/, chatbot/, control_center/             user-specific / private
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = Path(SPECPATH).parent

datas = []
for pkg in ("openwakeword", "silero_vad", "piper", "faster_whisper", "sentence_transformers", "certifi"):
    datas += collect_data_files(pkg)
# speechbrain lists its own package directory at import (lazy exports), so
# the .py sources have to be on disk, not only in the archive.
datas += collect_data_files("speechbrain", include_py_files=True)
binaries = []
for pkg in ("ctranslate2", "onnxruntime", "sounddevice", "_sounddevice_data"):
    try:
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass

# Files the code reads relative to paths.ROOT.
for rel in ("routing/intents.yaml", "routing/README.md", "domains", "config", "llm_fallback", "scripts", "ui/fonts", "fonts"):
    src = ROOT / rel
    if src.exists():
        datas.append((str(src), rel if src.is_dir() else str(Path(rel).parent)))
# Task Scheduler scripts the app runs for "start at sign-in".
for name in ("run_listener.ps1", "install_task.ps1", "uninstall_task.ps1"):
    datas.append((str(ROOT / "system" / name), "system"))
# Shipped tree only: never the user's data, caches, logs or weights.
datas = [d for d in datas if not any(x in Path(d[0]).parts for x in ("logs", "__pycache__", "personal"))]

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("llm_fallback")
    + collect_submodules("backend")
    + collect_submodules("speechbrain.inference")
    + ["listener.vad_listener", "keyboard", "tkinter", "sqlite3"]
)

a = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["chatbot", "control_center", "matplotlib", "IPython", "notebook", "pytest", "playwright", "pandas.tests", "torch.utils.tensorboard"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="ekko-backend", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="ekko-backend", upx=False)
