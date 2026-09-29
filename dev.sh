#!/usr/bin/env bash
# One command for development: the desktop app plus its backend (and the
# voice listener, unless --no-listener). `tauri dev` starts the backend from
# venv/ itself and stops it when the window closes.
#   ./dev.sh                 # app + backend + listener
#   ./dev.sh --no-listener   # app + backend only (no microphone)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x venv/bin/python ]; then
    python3 -m venv venv
    venv/bin/python -m pip install -r requirements.txt
fi
[ -d ekko-ui/node_modules ] || pnpm -C ekko-ui install

[ "${1:-}" = "--no-listener" ] && export EKKO_NO_LISTENER=1
exec pnpm -C ekko-ui tauri dev
