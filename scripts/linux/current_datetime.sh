#!/usr/bin/env bash
# Answers "what's today's date" / "what time is it" straight from the
# system clock. Linux counterpart to scripts/windows/current_datetime.ps1
# -- see that file's header for why this exists at all (a question that
# used to reach the LLM fallback and get "I don't have access to
# real-time clock information," when the answer was always sitting in
# this machine's own clock).
#
# GNU date (coreutils, the Linux default) -- %-d/%-I drop the leading
# zero, matching how a person would actually say the date/time out loud.
#
# EXIT CODES
#   0 - answered
#   1 - unexpected failure reading the clock
#
# OUTPUT CONTRACT
#   Same as scripts/windows/current_datetime.ps1: a detail line to
#   stdout, then a final "SPEAK: <text>" line.
set -euo pipefail

if ! local_time="$(date '+%Y-%m-%d %H:%M:%S %z')"; then
    echo "Failed to read the system clock." >&2
    exit 1
fi
echo "Local time: $local_time"

date_part="$(date '+%A, %B %-d')"
time_part="$(date '+%-I:%M %p')"
echo "SPEAK: It's $date_part, and the time is $time_part."
exit 0
