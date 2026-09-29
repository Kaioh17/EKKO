"""One version number, everywhere, and the backend reports it so the desktop
app can refuse to attach to a stale backend."""

import json
import re

from conftest import AUTH
from paths import ROOT

from version import VERSION


def test_version_matches_every_manifest():
    tauri = json.loads((ROOT / "ekko-ui" / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))["version"]
    package = json.loads((ROOT / "ekko-ui" / "package.json").read_text(encoding="utf-8"))["version"]
    cargo = re.search(
        r'(?m)^version\s*=\s*"([^"]+)"', (ROOT / "ekko-ui" / "src-tauri" / "Cargo.toml").read_text(encoding="utf-8")
    ).group(1)
    assert VERSION == tauri == package == cargo


def test_health_reports_version(client):
    assert client.get("/api/health", headers=AUTH).json() == {"ok": True, "version": VERSION}
