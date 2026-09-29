<#
.SYNOPSIS
    Answers "what's today's date" / "what time is it" from this machine's
    own clock.

.DESCRIPTION
    Handler script for EKKO's intent routing layer, backing the
    current_datetime intent in routing/intents.yaml. Standalone on
    purpose, same reasoning as every other handler in this folder
    (scripts/README.md): can be run and tested directly, no
    listener/matcher dependency.

    Added specifically because this question used to fall through to
    llm_fallback/ (see llm_fallback/gemini/logs/fallback.jsonl,
    2026-08-21: "what is today" got answered "I don't have access to
    real-time clock or calendar information" -- true for an LLM with no
    search tool, but this machine has always had the actual answer sitting
    in its own clock. No model, local or cloud, paid or free, needed for
    this one -- Get-Date is exact, instant, and free in every sense
    'free' means in this project's other README notes.

.EXIT CODES
    0 - answered
    1 - unexpected failure

.OUTPUT CONTRACT
    Same as system_metric_query.ps1: full detail line to stdout, then a
    final "SPEAK: <text>" line. See scripts/README.md's exit-code table.
#>

$ErrorActionPreference = 'Stop'

try {
    $now = Get-Date
    # "Friday, August 21" rather than "8/21/2026": spoken text should read
    # the way a person would say it out loud, not the way a form would
    # print it -- same reasoning system_metric_query.ps1's $summary
    # strings use plain sentences instead of raw numbers.
    $datePart = $now.ToString('dddd, MMMM d')
    $timePart = $now.ToString('h:mm tt')
    Write-Output "Local time: $($now.ToString('yyyy-MM-dd HH:mm:ss zzz'))"

    $summary = "It's $datePart, and the time is $timePart."
    Write-Output "SPEAK: $summary"
    exit 0
}
catch {
    Write-Error "Failed to read the system clock: $_"
    exit 1
}
