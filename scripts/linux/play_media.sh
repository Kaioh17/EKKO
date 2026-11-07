#!/usr/bin/env bash
# Native Spotify (Apple Music has no Linux app): launch, or search a request.
# Thin wrapper over scripts/media_player.py, which owns the parsing,
# detection, logic and exit codes.
#
# --request: the raw utterance (play_media's `transcript` slot). Free text,
#   passed as one argv element; media_player.py only ever puts it in a
#   percent-encoded spotify: URI for xdg-open, never on a shell line.
#
# EXIT CODES
#   0 - done
#   1 - bad args or no python
#   4 - failed with a spoken reason (not installed, nothing found, launch error)
set -euo pipefail

request=""
while [ $# -gt 0 ]; do
    case "$1" in
        --request) request="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done
[ -n "$request" ] || { echo "play_media: --request is required" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="$REPO_ROOT/venv/bin/python"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
    echo "play_media: no venv/bin/python or python3 found" >&2
    exit 1
fi

exec "$PYTHON_BIN" "$REPO_ROOT/scripts/media_player.py" "$request"
