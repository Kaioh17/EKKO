"""The single version number for ekko: the app, the backend and the
installer are one signed unit (see docs/ARCHITECTURE.md), so they share
one number. Bump it here and in ekko-ui/{package.json,src-tauri/Cargo.toml,
src-tauri/tauri.conf.json} together -- tests/test_version.py checks they
match.
"""

VERSION = "0.1.0"
