"""Resolves scripts/ handler paths and builds the invocation command for the
current OS, and loads .env into the environment.

One gate, the UI's system.os setting ("auto" by default), decides which
of scripts/<os>/ EKKO runs handlers from. Everything that used to hardcode
"powershell" -- routing/execute.py, llm_fallback/brain/research.py,
scripts/daily_briefing.py, scripts/media_handler.py -- goes through
command() here instead, so there's one place that knows how each OS invokes
a handler script.

load_dotenv() lives here so importing any pipeline module makes the API
keys in .env visible. Stdlib-only KEY=value parser, "explicit env wins
over file"; see llm_fallback/gemini/client.py's history for why
python-dotenv was never added as a dependency.

Usage:
    python routing/host.py    # self-check: resolves a sample handler and
                               # builds its command for every supported OS
"""

import os
import platform
import sys
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from paths import data  # noqa: E402

# API keys only (see backend/routers/keys.py); user data, so under DATA_DIR.
DOTENV_PATH = data(".env")
SCRIPTS_ROOT = _PROJECT_ROOT / "scripts"
# Everything specific to one user or machine (extra intents, handler
# scripts, briefing watchlist) lives here, gitignored, so the public repo
# never carries it. Layout mirrors the shipped tree:
#   personal/intents.yaml            extra intents, merged by routing/config.py
#   personal/scripts/<os>/<name>.*   their handlers
#   personal/daily_briefing.yaml     overrides config/daily_briefing.yaml
PERSONAL_DIR = data("personal")

# mac has no scripts/mac/ tree yet (see scripts/README.md) -- it's listed
# here anyway so os=mac fails loudly at routing/config.py's handler
# validation ("no such file") instead of KeyError-ing inside this module
# first. Windows and linux are the two trees that actually exist.
_SUPPORTED_OS = {"windows", "linux", "mac"}
_SUFFIX = {"windows": ".ps1", "linux": ".sh", "mac": ".sh"}
_UNAME_TO_OS = {"Windows": "windows", "Linux": "linux", "Darwin": "mac"}


def load_dotenv(path: Path = DOTENV_PATH) -> None:
    """Stdlib-only .env parser: KEY=value, one per line, '#' comments,
    optional quotes. Never overwrites a value already set in the real
    environment -- an explicit `$env:GEMINI_API_KEY` for the current session wins
    over whatever the file says, same "explicit beats implicit" default
    most .env loaders use.
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


load_dotenv()


def current_os() -> str:
    """The UI's system.os setting, or "auto" to detect via
    platform.system(). The schema only allows supported values; an
    undetectable platform raises, same posture as routing/config.py's
    ConfigError, rather than silently guessing.
    """
    from backend.config import get  # lazy: backend.config never imports routing.host

    raw = get("system").os
    if raw == "auto":
        uname = platform.system()
        detected = _UNAME_TO_OS.get(uname)
        if detected is None:
            raise ValueError(f"os=auto couldn't map platform.system()={uname!r} to a supported OS")
        return detected
    return raw


def script_suffix(os_name: str | None = None) -> str:
    return _SUFFIX[os_name or current_os()]


def script_root(os_name: str | None = None) -> Path:
    """scripts/<os>/, the directory every handler path must resolve inside
    -- routing/config.py's _validate_handler and execute.py's
    resolve_handler both contain a handler to this, same permission
    boundary scripts/README.md describes.
    """
    return SCRIPTS_ROOT / (os_name or current_os())


def handler_roots(os_name: str | None = None) -> tuple[Path, Path]:
    """The only directories a handler may live in: the shipped
    scripts/<os>/ and the user's personal/scripts/<os>/."""
    os_name = os_name or current_os()
    return script_root(os_name), PERSONAL_DIR / "scripts" / os_name


def script(name: str, os_name: str | None = None) -> Path:
    """<root>/<name>.<ext> for the current (or given) OS, shipped scripts
    first, then personal ones. `name` is a bare stem -- no directory
    separators, no extension -- see routing/config.py's _validate_handler
    for why that's a hard requirement rather than a convention: it's what
    makes a config-file traversal impossible by construction.
    """
    os_name = os_name or current_os()
    roots = handler_roots(os_name)
    for root in roots:
        candidate = root / f"{name}{_SUFFIX[os_name]}"
        if candidate.is_file():
            return candidate
    return roots[0] / f"{name}{_SUFFIX[os_name]}"  # nonexistent: callers report "no such file"


def within_handler_roots(path: Path, os_name: str | None = None) -> bool:
    resolved = path.resolve()
    return any(resolved.is_relative_to(root.resolve()) for root in handler_roots(os_name))


def command(path: Path, slots: dict[str, str] | None = None, os_name: str | None = None) -> list[str]:
    """The full argv to run `path` as a subprocess, OS-appropriate. Always
    an argument list, never a shell string -- shell=False stays the
    subprocess default at every call site, same reasoning
    routing/execute.py's module docstring gives.

    windows: powershell -NoProfile -ExecutionPolicy Bypass -File <path> -Slot value ...
        -NoProfile keeps a user profile from changing how a handler
        behaves; -ExecutionPolicy Bypass is needed because these scripts
        are unsigned local files (scripts/README.md). Slot names become
        -PascalCase params, matching each script's declared param() casing.

    linux/mac: bash <path> --slot value ...
        `bash <path>`, not `./<path>` relying on an exec bit: this repo can
        be checked out on Windows, where exec bits don't survive, so the
        interpreter is always named explicitly. Slot names become
        --kebab-case flags, the convention each ported handler's own
        arg-parsing loop expects.
    """
    os_name = os_name or current_os()
    slots = slots or {}
    if os_name == "windows":
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)]
        for slot_name, value in slots.items():
            cmd += [f"-{slot_name[:1].upper()}{slot_name[1:]}", value]
        return cmd
    cmd = ["bash", str(path)]
    for slot_name, value in slots.items():
        cmd += [f"--{slot_name.replace('_', '-')}", value]
    return cmd


if __name__ == "__main__":
    print(f"os resolves to: {current_os()!r}")
    for os_name in sorted(_SUPPORTED_OS):
        sample = script("open_app", os_name=os_name)
        cmd = command(sample, {"app": "chrome"}, os_name=os_name)
        print(f"  {os_name:8s} {cmd}")
        assert str(sample).endswith(f"open_app{_SUFFIX[os_name]}")
        expected_tail = ["-App", "chrome"] if os_name == "windows" else ["--app", "chrome"]
        assert cmd[-2:] == expected_tail, cmd
    print("\nValid.")
