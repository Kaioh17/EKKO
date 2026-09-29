"""Per-process memory breakdown: the top RSS consumers on this machine.

Not a handler itself -- .ps1 only, see scripts/README.md's permission
boundary -- this is a plain data source invoked by
scripts/check_memory_activity.ps1, the same relationship
render_report.py has to system_diagnosis.ps1: the .ps1 is what
routing/config.py validates as the handler, this just does the actual
query so the logic doesn't get reimplemented in PowerShell. Specifically,
psutil.process_iter() gives per-process resilience (a process can exit or
become unreadable mid-scan without taking the whole report down) that
Get-Process doesn't hand you for free.

Different question from system_metric_query.ps1 -Metric ram: that
answers "how full is memory overall," one percentage. This answers
"what's using it," a ranked list of processes -- see the distinction
called out on the check_memory_activity intent in routing/intents.yaml.

Prints ONE line of JSON to stdout by default, meant to be machine-read by
the PowerShell handler via ConvertFrom-Json:
    {"processes": [{"name": "chrome.exe", "pid": 1234, "mb": 812.4}, ...],
     "total_mb": 6031.2, "count": 143}
`processes` is sorted descending by mb and capped at --limit (default
15). `total_mb` and `count` are summed/counted over every process that
was actually readable, not just the ones in the top slice, so "143
processes" doesn't secretly mean "143 that a permissions error didn't
skip." total_mb is a sum of RSS across processes, not a measure of
system memory used -- shared pages (DLLs, mapped files) get counted once
per process that maps them, so it will run higher than the RAM-used
percentage system_metric_query.ps1 reports, same physical memory,
different question.

Usage:
    python scripts/check_memory_activity.py
    python scripts/check_memory_activity.py --limit 5
    python scripts/check_memory_activity.py --pretty   # human-readable table, for manual runs
    python scripts/check_memory_activity.py --speak --json-out PATH
        Detail lines + a "SPEAK: <text>" line (the check_memory_activity
        contract scripts/windows/check_memory_activity.ps1 builds itself
        from the raw JSON above via PowerShell's native ConvertFrom-Json).
        --speak exists for scripts/linux/check_memory_activity.sh, which
        has no equivalent native JSON parser -- see scripts/system_metrics.py's
        own docstring for the fuller reasoning on why that script's
        formatting lives in Python rather than bash. Purely additive: the
        default (no --speak) behaviour above is unchanged, so
        check_memory_activity.ps1 keeps doing its own formatting exactly
        as before.
"""

import argparse
import json
from pathlib import Path

import psutil

# Same hardcoded default as scripts/windows/check_memory_activity.ps1's
# $MemoryHogMB -- a process above this gets named individually and pushes
# the flagged count into the spoken summary. --speak mode only.
DEFAULT_HOG_MB = 1024


def top_processes(limit: int) -> dict:
    processes = []
    total_mb = 0.0
    count = 0
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            mem_info = proc.info["memory_info"]
            if mem_info is None:
                continue
            mb = mem_info.rss / (1024 ** 2)
            processes.append({"name": proc.info["name"] or "?", "pid": proc.info["pid"], "mb": round(mb, 1)})
            total_mb += mb
            count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # process_iter()'s documented failure mode: a process can
            # exit, or be unreadable (a system process this account
            # can't query), between being listed and its .info being
            # read. Skip it rather than letting one vanished process
            # take the whole report down, same "don't throw, degrade"
            # posture as lib\system_metrics.ps1's Get-*Metric functions.
            continue

    processes.sort(key=lambda p: p["mb"], reverse=True)
    return {
        "processes": processes[:limit],
        "total_mb": round(total_mb, 1),
        "count": count,
    }


def render_table(report: dict) -> None:
    for p in report["processes"]:
        print(f"{p['name']:<30} PID {p['pid']:<8} {p['mb']:.1f} MB")
    print(f"\n{report['count']} processes scanned, {report['total_mb']:.1f} MB total RSS")


def print_speak(report: dict, hog_mb: float, json_out: str | None) -> None:
    """The check_memory_activity output contract:
    scripts/windows/check_memory_activity.ps1's stdout shape, built here
    instead so scripts/linux/check_memory_activity.sh can just relay it --
    see this module's docstring and scripts/system_metrics.py's for why.
    """
    processes = report["processes"]
    for p in processes:
        print(f"{p['name']:<30} PID {p['pid']:<8} {p['mb']:.1f} MB")
    print(f"{report['count']} processes scanned, {report['total_mb']:.1f} MB total RSS")

    top3 = processes[:3]
    named = [f"{p['name']} at {round(p['mb'])} megabytes" for p in top3]
    summary = "Top memory users: " + ", ".join(named) + "."

    hogs = [p for p in processes if p["mb"] > hog_mb]
    if hogs:
        verb = "is" if len(hogs) == 1 else "are"
        plural = "" if len(hogs) == 1 else "es"
        summary += f" {len(hogs)} process{plural} {verb} over a gigabyte."

    print(f"SPEAK: {summary}")

    if json_out:
        rows = [
            {"name": p["name"], "detail": f"PID {p['pid']}, {p['mb']:.1f} MB", "flagged": p["mb"] > hog_mb}
            for p in processes
        ]
        Path(json_out).write_text(json.dumps({"summary": summary, "metrics": rows}), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=15, help="How many top processes to report (default: 15)."
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Human-readable table instead of JSON, for manual runs. The "
        "PowerShell handler always calls this without --pretty.",
    )
    parser.add_argument(
        "--speak", action="store_true",
        help="Detail lines + SPEAK: line (see module docstring). scripts/linux/"
        "check_memory_activity.sh's mode; the .ps1 handler never passes this.",
    )
    parser.add_argument(
        "--hog-mb", type=float, default=DEFAULT_HOG_MB,
        help=f"--speak only: a process above this many MB gets named and counted (default: {DEFAULT_HOG_MB}).",
    )
    parser.add_argument(
        "--json-out", default=None,
        help="--speak only: write the {summary, metrics} report-window payload here.",
    )
    args = parser.parse_args()

    report = top_processes(args.limit)
    if args.speak:
        print_speak(report, args.hog_mb, args.json_out)
    elif args.pretty:
        render_table(report)
    else:
        print(json.dumps(report))


if __name__ == "__main__":
    main()
