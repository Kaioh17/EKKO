#!/usr/bin/env bash
# Opens one of a fixed set of applications. Linux counterpart to
# scripts/windows/open_app.ps1 -- see that file's header for the slot
# reasoning (routing/intents.yaml's open_app intent: one intent with an
# `app` slot, not one intent per program).
#
# The case statement below is deliberate duplication of the slot
# vocabulary in routing/intents.yaml, same defence-in-depth reasoning
# open_app.ps1's ValidateSet documents: routing/slots.py already
# guarantees only a canonical value reaches here, so an unmatched --app
# should be unreachable, and a bug upstream should fail as a parameter
# error rather than launch something unintended. Keep this list in sync
# with intents.yaml when adding an app.
#
# Binary names, not .desktop files or absolute paths: unlike Windows'
# Start Menu .lnk shortcuts, a binary on PATH is the stable pointer here
# across distros.
#
# EXIT CODES
#   0 - launched successfully
#   1 - unknown --app (should be unreachable, see above)
#   2 - no matching binary found on PATH
set -euo pipefail

app=""
while [ $# -gt 0 ]; do
    case "$1" in
        --app) app="$2"; shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

case "$app" in
    chrome)  bin=google-chrome ;;
    discord) bin=discord ;;
    vscode)  bin=code ;;
    cursor)  bin=cursor ;;
    steam)   bin=steam ;;
    brave)   bin=brave-browser ;;
    *)
        echo "Unknown app: '$app'. Must be one of chrome, discord, vscode, cursor, steam, brave." >&2
        exit 1
        ;;
esac

if ! command -v "$bin" >/dev/null 2>&1; then
    echo "No '$bin' binary on PATH for app '$app'. It may not be installed, or installed under a different binary name -- update this script's case statement if so." >&2
    exit 2
fi

nohup "$bin" >/dev/null 2>&1 &
disown
echo "$app opened."
exit 0
