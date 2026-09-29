"""Headless update check: tells a user whose ekko window is closed (start-at-
sign-in) that a new version exists, since the in-window Tauri updater
(ekko-ui/src/components/UpdateBanner.tsx) never gets a chance to run for
them. Notification only -- installing the update still goes through the
signed Tauri updater, so there is no second updater to attack.

Checks the same GitHub Releases `latest.json` the app's updater uses
(LATEST_URL, kept equal to tauri.conf.json's plugins.updater.endpoints by
tests/test_updates.py), once at backend startup and once a day after
(see `run`, started by backend/main.py in the installed build). Notifies at most once per
version, tracked in the settings DB under a section the UI never reads.
"""

import json
import logging
import re
import subprocess
import time
import urllib.request
from collections.abc import Callable

from backend import db

logger = logging.getLogger(__name__)

LATEST_URL = "https://github.com/Kaioh17/EKKO/releases/latest/download/latest.json"
CHECK_INTERVAL_S = 24 * 60 * 60

_VERSION_RE = re.compile(r"^v?(\d+\.\d+\.\d+)$")


def _parse(version: str) -> tuple[int, ...] | None:
    m = _VERSION_RE.match(version.strip())
    return tuple(int(part) for part in m.group(1).split(".")) if m else None


def is_newer(candidate: str, current: str) -> bool:
    """False for anything that isn't a plain X.Y.Z (optionally v-prefixed),
    not just an older one -- a malformed version off the network should
    never look "newer" and trigger a notification."""
    c, b = _parse(candidate), _parse(current)
    return c is not None and b is not None and c > b


def toast_command(version: str) -> list[str]:
    """argv for a one-shot Windows notification. Raises ValueError for
    anything that isn't a plain version -- `version` comes off the
    network (fetch_latest), so this is what makes embedding it in a
    PowerShell command string safe: only digits and dots survive.
    """
    m = _VERSION_RE.match(version.strip())
    if not m:
        raise ValueError(f"not a plain version: {version!r}")
    text = f"ekko {m.group(1)} is available. Open ekko to update."
    script = f"Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.MessageBox]::Show('{text}', 'ekko update') | Out-Null"
    return ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script]


def fetch_latest(url: str = LATEST_URL, timeout: float = 5.0) -> str | None:
    """The version named in url's latest.json, or None on any network,
    HTTP or shape problem."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 -- constant GitHub Releases URL in prod, loopback in tests
            body = json.loads(resp.read())
    except (OSError, ValueError):
        return None
    version = body.get("version") if isinstance(body, dict) else None
    return version if isinstance(version, str) else None


def notify(version: str) -> None:
    subprocess.Popen(toast_command(version))  # noqa: S603 -- argv list, version pre-validated


def check_once(fetch: Callable[[], str | None], toast: Callable[[str], object], window_open: bool, current: str) -> str | None:
    """One check. `fetch` and `toast` are parameters (rather than calls to
    fetch_latest/notify) so every branch is cheap to test without a network
    or a real Windows notification. `window_open` means a WebSocket client
    is connected (backend/status_bus.py), where the in-app banner already
    covers this.
    """
    latest = fetch()
    if latest is None or not is_newer(latest, current) or window_open:
        return None
    if db.get_section("updates").get("notified") != latest:
        toast(latest)
        db.put_section("updates", {"notified": latest})
    return latest


def run(client_count: Callable[[], int], current: str) -> None:
    """Check forever, once per CHECK_INTERVAL_S. Meant for a daemon thread,
    and only started in the installed build -- a dev checkout has no
    version to update."""
    while True:
        try:
            check_once(fetch_latest, notify, client_count() > 0, current)
        except Exception:
            logger.exception("update check failed")
        time.sleep(CHECK_INTERVAL_S)


if __name__ == "__main__":
    assert is_newer("0.2.0", "0.1.0")
    assert not is_newer("0.1.0", "0.1.0")
    assert not is_newer("garbage", "0.1.0")
    assert toast_command("0.2.0")
    try:
        toast_command("0.2.0'; calc; '")
    except ValueError:
        pass
    else:
        raise AssertionError("toast_command accepted an unsafe version")
    print("updates self-check passed")
