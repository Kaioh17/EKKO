"""
ui/theme.py

Colors, font resolution and per-OS setup for the Tk overlay, kept in one place
so other windows can reuse them. Colors mirror the settings UI's dark tokens
(ekko-ui/src/styles/theme.css). Every OS-specific call is guarded so importing
and using this is safe on Windows, macOS and Linux.
"""

import sys
import tkinter.font as tkfont
from pathlib import Path

BG = "#17171b"        # --color-bg (dark)
SURFACE = "#1f1f24"   # --color-bg-elevated
BORDER = "#2c2c31"    # --color-border over BG, flattened (Tk has no alpha colors)
TEXT = "#ededf0"
MUTED = "#9a9aa2"
AMBER = "#d29922"     # processing
GREEN = "#3ECF8E"     # idle / listening / speaking
RED = "#f85149"       # error

SANS = ("Inter", "Segoe UI Variable Text", "Segoe UI", "SF Pro Text", "Helvetica Neue",
        "Cantarell", "DejaVu Sans")
MONO = ("JetBrains Mono", "Cascadia Mono", "Consolas", "SF Mono", "Menlo", "DejaVu Sans Mono")

_FONT_DIRS = (Path(__file__).parent / "fonts", Path(__file__).resolve().parent.parent / "fonts")
_cache: dict = {}


def prepare():
    """Call before the first Tk(): makes Windows render at native DPI instead of bitmap-scaling."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass


def _load_bundled_fonts():
    try:
        from tkextrafont import Font as ExtraFont  # optional dependency
        for d in _FONT_DIRS:
            for f in list(d.glob("*.ttf")) + list(d.glob("*.otf")):
                ExtraFont(file=str(f))
    except Exception:
        pass


def _pick(installed, stack, default):
    for family in stack:
        if family.lower() in installed:
            return family
    return default


def init(root):
    """Resolve fonts and UI scale once; later calls return the same dict:
    {"sans": family, "mono": family, "scale": float}. Needs a live root."""
    if _cache:
        return _cache
    _load_bundled_fonts()
    installed = {f.lower() for f in tkfont.families(root)}
    scale = 1.0
    if sys.platform != "darwin":  # macOS points already equal logical px
        try:
            dpi = root.winfo_fpixels("1i")
            root.tk.call("tk", "scaling", dpi / 72)
            scale = max(1.0, dpi / 96)
        except Exception:
            pass
    _cache.update(
        sans=_pick(installed, SANS, tkfont.nametofont("TkDefaultFont").actual("family")),
        mono=_pick(installed, MONO, tkfont.nametofont("TkFixedFont").actual("family")),
        scale=scale,
    )
    return _cache
