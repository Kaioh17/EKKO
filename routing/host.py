"""Resolves scripts/ handler paths and builds the invocation command for the
current OS, and loads .env into the environment.

One gate, read from EKKO_OS in .env, decides which of scripts/<os>/ EKKO
runs handlers from. Everything that used to hardcode "powershell" --
routing/execute.py, llm_fallback/claude_code/fallback.py,
scripts/daily_briefing.py, scripts/media_handler.py -- goes through
command() here instead, so there's one place that knows how each OS invokes
a handler script.

load_dotenv() lives here, not in llm_fallback/gemini/client.py where it used
to, because EKKO_OS needs to be readable before routing/config.py resolves
HANDLER_ROOT below, which happens at import time in a module client.py never
imports. Moved verbatim -- same stdlib-only KEY=value parser, same "explicit
env wins over file" rule -- not rewritten; see client.py's own history for
why python-dotenv was never added as a dependency.

Usage:
    python routing/host.py    # self-check: resolves a sample handler and
                               # builds its command for every supported OS
"""

import os
import platform
from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parent

DOTENV_PATH = _PROJECT_ROOT / ".env"
SCRIPTS_ROOT = _PROJECT_ROOT / "scripts"

# mac has no scripts/mac/ tree yet (see scripts/README.md) -- it's listed
# here anyway so EKKO_OS=mac fails loudly at routing/config.py's handler
# validation ("no such file") instead of KeyError-ing inside this module
# first. Windows and linux are the two trees that actually exist.
_SUPPORTED_OS = {"windows", "linux", "mac"}
_SUFFIX = {"windows": ".ps1", "linux": ".sh", "mac": ".sh"}
_UNAME_TO_OS = {"Windows": "windows", "Linux": "linux", "Darwin": "mac"}


def load_dotenv(path: Path = DOTENV_PATH) -> None:
    """Stdlib-only .env parser: KEY=value, one per line, '#' comments,
    optional quotes. Never overwrites a value already set in the real
    environment -- an explicit `$env:EKKO_OS` for the current session wins
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
    """EKKO_OS from .env/the environment, or "auto"/unset to detect via
    platform.system(). Raises on anything else -- a typo'd EKKO_OS should
    fail loudly at startup, same posture as routing/config.py's ConfigError,
    not silently fall back to guessing.
    """
    raw = os.environ.get("EKKO_OS", "auto").strip().lower()
    if not raw or raw == "auto":
        uname = platform.system()
        detected = _UNAME_TO_OS.get(uname)
        if detected is None:
            raise ValueError(f"EKKO_OS=auto couldn't map platform.system()={uname!r} to a supported OS")
        return detected
    if raw not in _SUPPORTED_OS:
        raise ValueError(f"EKKO_OS={raw!r} is not one of {sorted(_SUPPORTED_OS)} (or 'auto')")
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


def script(name: str, os_name: str | None = None) -> Path:
    """scripts/<os>/<name>.<ext> for the current (or given) OS. `name` is a
    bare stem -- no directory separators, no extension -- see
    routing/config.py's _validate_handler for why that's a hard requirement
    rather than a convention: it's what makes a config-file traversal
    impossible by construction.
    """
    os_name = os_name or current_os()
    return script_root(os_name) / f"{name}{_SUFFIX[os_name]}"


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
    print(f"EKKO_OS resolves to: {current_os()!r}")
    for os_name in sorted(_SUPPORTED_OS):
        sample = script("open_app", os_name=os_name)
        cmd = command(sample, {"app": "chrome"}, os_name=os_name)
        print(f"  {os_name:8s} {cmd}")
        assert str(sample).endswith(f"open_app{_SUFFIX[os_name]}")
        expected_tail = ["-App", "chrome"] if os_name == "windows" else ["--app", "chrome"]
        assert cmd[-2:] == expected_tail, cmd
    print("\nValid.")
