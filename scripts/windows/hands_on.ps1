<#
.SYNOPSIS
    Opens a terminal window that asks permission for a file operation.

.DESCRIPTION
    Handler for the file_operation intent. This script does NOT touch the
    filesystem and does not decide anything. It opens a Windows Terminal tab
    running control_center/hands_on/file_gateway with the utterance, and that process --
    in a window a human is looking at -- parses it, resolves every path
    against control_center/hands_on/file_gateway/policy.yaml, and blocks on "Approve?
    [y/N]" before anything reaches disk.

    So a successful exit here means "a window opened", never "a file
    changed". That's why the SPEAK: line below says so rather than letting
    EKKO claim "Done." for something that hasn't happened and may well be
    denied. See control_center/hands_on/file_gateway/README.md.

    Why a popup at all: the confirmation prompt needs stdin from a terminal,
    and routing/execute.py runs handlers with subprocess.run(capture_output)
    from a listener that usually has no console of its own. Same
    wt.exe-new-tab pattern as scripts/windows/lib/system_metrics.ps1's
    Open-ReportWindow, including its argument-quoting fix.

    Parsing in that window is --backend gemini, not nl.py's regex table:
    nl.py doesn't parse the orderings people actually speak ("a folder in
    documents called dune"). The model only ever proposes; policy.py and the
    prompt are what bound the outcome.

.PARAMETER Instruction
    The whole utterance, verbatim, from the transcript slot of the same name
    (routing/slots.py's find_transcript).

.EXIT CODES
    0 - a window opened, or the failure was logged and swallowed
    1 - the utterance was empty
#>
param(
    [Parameter(Mandatory = $true)][string]$Instruction
)

$ErrorActionPreference = 'Stop'

if (-not $Instruction.Trim()) {
    Write-Error 'No instruction given.'
    exit 1
}

# Two levels up from scripts\windows is the repo root.
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonExe = Join-Path $repoRoot 'pvenv\Scripts\python.exe'

if (-not (Test-Path $pythonExe)) {
    Write-Output "hands_on: skipped (pvenv not found at $pythonExe)"
    Write-Output 'SPEAK: I could not open the approval window.'
    exit 0
}

# -m control_center.hands_on.file_gateway needs the repo root as the working directory,
# which is what wt's -d does (and Start-Process's -WorkingDirectory below).
$pyArgs = @('-m', 'control_center.hands_on.file_gateway', '--llm', '--backend', 'gemini', '--pause', $Instruction)

try {
    $wt = Get-Command wt.exe -ErrorAction SilentlyContinue
    if ($wt) {
        # Start-Process -ArgumentList given an array joins on plain spaces
        # without quoting elements that contain one -- it is not .NET's
        # ProcessStartInfo.ArgumentList despite the name. $Instruction
        # always contains spaces, so quoting here is load-bearing rather
        # than defensive. See system\dev.ps1's Quote-Arg for the fuller
        # explanation and the bug that produced it.
        $wtArgs = @('new-tab', '-d', $repoRoot, '--title', 'EKKO - approve?', $pythonExe) + $pyArgs
        $argString = ($wtArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
        Start-Process -FilePath $wt.Source -ArgumentList $argString
    }
    else {
        # No Windows Terminal. A console app started this way still gets its
        # own console window, so the prompt is reachable -- it's just a
        # plainer one. -ArgumentList takes the same quoting treatment.
        $argString = ($pyArgs | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
        Start-Process -FilePath $pythonExe -ArgumentList $argString -WorkingDirectory $repoRoot
    }
    Write-Output "hands_on: window opened for $Instruction"
    Write-Output 'SPEAK: I have opened a terminal for you to approve that.'
    exit 0
}
catch {
    # Degrade and log, same posture as Open-ReportWindow: a window that
    # failed to open is worth saying out loud, but it is not a reason to
    # return a failing exit code and have EKKO report a broken command.
    Write-Output "hands_on: failed to open window ($_)"
    Write-Output 'SPEAK: I could not open the approval window.'
    exit 0
}
