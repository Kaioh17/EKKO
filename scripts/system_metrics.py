"""Cross-platform CPU/RAM/disk/battery/GPU/network metrics, via psutil.

Not a handler itself -- see scripts/README.md's permission boundary, only a
.ps1/.sh under scripts/<os>/ can be named as one. This is the Linux/mac
counterpart to scripts/windows/lib/system_metrics.ps1's Get-*Metric
functions, which stay on their own native CIM implementation (it works,
this isn't a rewrite of that).

Unlike scripts/check_memory_activity.py (data only, the .ps1 builds the
SPEAK: line), this file also owns the threshold/summary/report-window-
payload logic behind --diagnosis/--briefing/--metric below. That's a
deliberate difference from the Windows split: bash has no native JSON
parser the way PowerShell's ConvertFrom-Json is, so pushing the full
output contract into Python (already a dependency, already portable)
avoids either adding `jq` as a new install requirement or reimplementing
this file's threshold math in bash, untested, on the one OS this project
has no machine to run it on. scripts/linux/lib/system_metrics.sh stays a
thin wrapper: call this, print what it printed, hand its --json-out file
to the same render_report.py / daily_briefing.py windows already use.

Every metric degrades to `null` fields with a "detail" note on failure or
unavailability (no battery, no nvidia-smi, ...) rather than raising -- same
"a missing metric is a real answer, not a crash" posture the .ps1 functions
document.

Modes (mutually exclusive; default with no flag is a bare JSON dump):
    python scripts/system_metrics.py
        {"cpu": {...}, "ram": {...}, "disk": {...}, "battery": {...},
         "gpu": {...}, "network": {...}, "thresholds": {...}}
    python scripts/system_metrics.py --pretty
        One human-readable detail line per metric.
    python scripts/system_metrics.py --diagnosis [--json-out PATH]
        Full report: six detail lines, then "SPEAK: <summary>" -- same
        contract as scripts/windows/system_diagnosis.ps1.
    python scripts/system_metrics.py --briefing [--json-out PATH]
        Same six metrics, briefing-flavored summary ("... Pulling up
        today's news and your stocks now.") -- same contract as
        scripts/windows/daily_briefing.ps1's fast system-status half.
    python scripts/system_metrics.py --metric cpu [--json-out PATH]
        One metric's detail line, then "SPEAK: <summary>" -- same
        contract as scripts/windows/system_metric_query.ps1.

--json-out writes {"summary": ..., "metrics": [{"name", "detail",
"flagged"}, ...]} to PATH, the same payload shape
scripts/render_report.py / scripts/daily_briefing.py already read from
Open-ReportWindow / Open-BriefingWindow on Windows.
"""

import argparse
import json
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import psutil

# Same hardcoded common-sense defaults as scripts/windows/lib/system_metrics.ps1's
# $DiskFreePercentLow etc. -- there's no enrollment set for "what counts as
# low disk space" here either. Adjust here if they turn out wrong in
# practice; this is the one definition both --diagnosis/--briefing/--metric
# below and the JSON output's "thresholds" key read from.
THRESHOLDS = {
    "disk_free_percent_low": 10,
    "battery_percent_low": 20,
    "cpu_percent_high": 90,
    "ram_percent_high": 90,
}

DISK_ROOT = "C:\\" if sys.platform == "win32" else "/"
_METRIC_KEYS = ("cpu", "ram", "disk", "battery", "gpu", "network")
_DISPLAY_NAME = {
    "cpu": "CPU", "ram": "Memory", "disk": f"Disk {DISK_ROOT}",
    "battery": "Battery", "gpu": "GPU", "network": "Network",
}


def get_cpu() -> dict:
    try:
        # A blocking sample (not the instant, meaningless 0.0 psutil returns
        # for the first call with interval=None) -- half a second is the
        # same order of latency the network check below already pays.
        load = round(psutil.cpu_percent(interval=0.5))
        return {"load": load, "detail": f"CPU load: {load}%"}
    except Exception as exc:  # noqa: BLE001 -- degrade, never crash the report
        return {"load": None, "detail": f"CPU load: unavailable ({exc})"}


def get_ram() -> dict:
    try:
        used_percent = round(psutil.virtual_memory().percent)
        return {"used_percent": used_percent, "detail": f"Memory used: {used_percent}%"}
    except Exception as exc:  # noqa: BLE001
        return {"used_percent": None, "detail": f"Memory used: unavailable ({exc})"}


def get_disk() -> dict:
    try:
        usage = psutil.disk_usage(DISK_ROOT)
        free_gb = usage.free / (1024 ** 3)
        free_percent = round(usage.free / usage.total * 100)
        return {
            "free_gb": round(free_gb, 1),
            "free_percent": free_percent,
            "detail": f"Disk {DISK_ROOT}: free {free_gb:.1f} GB ({free_percent}% free)",
        }
    except Exception as exc:  # noqa: BLE001
        return {"free_gb": None, "free_percent": None, "detail": f"Disk {DISK_ROOT}: unavailable ({exc})"}


def get_battery() -> dict:
    try:
        battery = psutil.sensors_battery()
        if battery is None:
            return {"percent": None, "charging": None, "detail": "Battery: unavailable (no battery reported)"}
        percent = round(battery.percent)
        charging = bool(battery.power_plugged)
        word = "charging" if charging else "unplugged"
        return {"percent": percent, "charging": charging, "detail": f"Battery: {percent}% ({word})"}
    except Exception as exc:  # noqa: BLE001
        return {"percent": None, "charging": None, "detail": f"Battery: unavailable ({exc})"}


def get_gpu() -> dict:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return {"util": None, "temp_c": None, "detail": "GPU: unavailable (nvidia-smi not found)"}
    try:
        output = subprocess.run(
            [nvidia_smi, "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        util, mem_used, mem_total, temp = (p.strip() for p in output.split(","))
        return {
            "util": int(util),
            "temp_c": int(temp),
            "detail": f"GPU: {util}% util, {mem_used}/{mem_total} MB, {temp} C",
        }
    except Exception as exc:  # noqa: BLE001
        return {"util": None, "temp_c": None, "detail": f"GPU: unavailable ({exc})"}


def get_network() -> dict:
    """Actual reachability, not just interface state -- same reasoning
    scripts/windows/lib/system_metrics.ps1's Get-NetworkMetric gives for
    using Test-NetConnection over GetIsNetworkAvailable(): a LAN with no
    internet (captive portal, ISP outage) should read as disconnected. Two
    targets, not one, so a single unreachable IP doesn't read as "no
    internet."
    """
    def reachable(host: str) -> bool:
        try:
            with socket.create_connection((host, 443), timeout=2):
                return True
        except OSError:
            return False

    connected = reachable("1.1.1.1") or reachable("8.8.8.8")
    return {"connected": connected, "detail": f"Network: {'connected' if connected else 'no connection'}"}


def collect() -> dict:
    return {
        "cpu": get_cpu(),
        "ram": get_ram(),
        "disk": get_disk(),
        "battery": get_battery(),
        "gpu": get_gpu(),
        "network": get_network(),
        "thresholds": THRESHOLDS,
    }


def render_table(report: dict) -> None:
    for key in _METRIC_KEYS:
        print(report[key]["detail"])


def build_flags(report: dict) -> tuple[list[str], dict[str, bool]]:
    """Same threshold checks as scripts/windows/lib/system_metrics.ps1's
    callers (system_diagnosis.ps1 / daily_briefing.ps1): a list of spoken
    clauses for whatever's out of range, plus which of the six rows to
    flag red in the report window. GPU has no threshold, same as there.
    """
    t = report["thresholds"]
    cpu, ram, disk, battery, network = (report[k] for k in ("cpu", "ram", "disk", "battery", "network"))
    flagged = dict.fromkeys(_METRIC_KEYS, False)
    flags = []

    if cpu["load"] is not None and cpu["load"] > t["cpu_percent_high"]:
        flags.append(f"CPU is under heavy load, {cpu['load']} percent")
        flagged["cpu"] = True
    if ram["used_percent"] is not None and ram["used_percent"] > t["ram_percent_high"]:
        flags.append(f"memory is nearly full, {ram['used_percent']} percent used")
        flagged["ram"] = True
    if disk["free_percent"] is not None and disk["free_percent"] < t["disk_free_percent_low"]:
        flags.append(f"disk space is low, {round(disk['free_gb'])} GB free")
        flagged["disk"] = True
    if battery["percent"] is not None and battery["percent"] < t["battery_percent_low"] and not battery["charging"]:
        flags.append(f"battery is low at {battery['percent']} percent and unplugged")
        flagged["battery"] = True
    if network["connected"] is False:
        flags.append("there's no network connection")
        flagged["network"] = True
    return flags, flagged


def build_numbers(report: dict) -> str:
    cpu, ram, disk, battery, gpu = (report[k] for k in ("cpu", "ram", "disk", "battery", "gpu"))
    parts = []
    if cpu["load"] is not None:
        parts.append(f"CPU at {cpu['load']}%")
    if ram["used_percent"] is not None:
        parts.append(f"memory at {ram['used_percent']}%")
    if disk["free_gb"] is not None:
        parts.append(f"{round(disk['free_gb'])} GB free on disk")
    if battery["percent"] is not None:
        word = "charging" if battery["charging"] else "unplugged"
        parts.append(f"battery at {battery['percent']}% and {word}")
    if gpu["util"] is not None:
        parts.append(f"GPU at {gpu['util']}% and {gpu['temp_c']} degrees")
    return ", ".join(parts)


def build_metric_rows(report: dict, flagged: dict[str, bool]) -> list[dict]:
    return [
        {"name": _DISPLAY_NAME[key], "detail": report[key]["detail"], "flagged": flagged[key]}
        for key in _METRIC_KEYS
    ]


def write_json_out(json_out: str, summary: str, metrics: list[dict]) -> None:
    Path(json_out).write_text(json.dumps({"summary": summary, "metrics": metrics}), encoding="utf-8")


def print_diagnosis(report: dict, json_out: str | None) -> None:
    render_table(report)
    flags, flagged = build_flags(report)
    numbers = build_numbers(report)
    if flags:
        summary = "Heads up, " + "; and ".join(flags) + ". Otherwise, " + numbers + "."
    else:
        summary = "System's fine. " + numbers + "."
    print(f"SPEAK: {summary}")
    if json_out:
        write_json_out(json_out, summary, build_metric_rows(report, flagged))


def print_briefing(report: dict, json_out: str | None) -> None:
    render_table(report)
    flags, flagged = build_flags(report)
    tail = "Pulling up today's news and your stocks now."
    summary = ("Heads up, " + "; and ".join(flags) + f". {tail}") if flags else f"System's fine. {tail}"
    print(f"SPEAK: {summary}")
    if json_out:
        write_json_out(json_out, summary, build_metric_rows(report, flagged))


def print_metric(report: dict, metric: str, json_out: str | None) -> None:
    m = report[metric]
    t = report["thresholds"]
    print(m["detail"])

    flagged = False
    if metric == "battery":
        summary = ("There's no battery reported on this machine." if m["percent"] is None
                   else f"Battery's at {m['percent']}%, {'charging' if m['charging'] else 'not charging'}.")
        flagged = m["percent"] is not None and m["percent"] < t["battery_percent_low"] and not m["charging"]
    elif metric == "cpu":
        summary = "CPU load isn't available right now." if m["load"] is None else f"CPU load is at {m['load']}%."
        flagged = m["load"] is not None and m["load"] > t["cpu_percent_high"]
    elif metric == "ram":
        summary = ("Memory usage isn't available right now." if m["used_percent"] is None
                   else f"Memory usage is at {m['used_percent']}%.")
        flagged = m["used_percent"] is not None and m["used_percent"] > t["ram_percent_high"]
    elif metric == "disk":
        summary = ("Disk space isn't available right now." if m["free_gb"] is None
                   else f"You've got {round(m['free_gb'])} gigabytes free on disk.")
        flagged = m["free_percent"] is not None and m["free_percent"] < t["disk_free_percent_low"]
    elif metric == "gpu":
        summary = ("GPU info isn't available right now." if m["util"] is None
                   else f"GPU is at {m['util']}% utilization, {m['temp_c']} degrees.")
        # No GPU threshold defined (see THRESHOLDS), so never flagged.
    else:  # network
        if m["connected"] is None:
            summary = "Network status isn't available right now."
        elif m["connected"]:
            summary = "Yes, you're connected to the internet."
        else:
            summary = "No, there's no internet connection right now."
        flagged = m["connected"] is False

    print(f"SPEAK: {summary}")
    if json_out:
        write_json_out(json_out, summary, [{"name": _DISPLAY_NAME[metric], "detail": m["detail"], "flagged": flagged}])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--pretty", action="store_true", help="Detail lines only, for manual runs.")
    mode.add_argument("--diagnosis", action="store_true", help="Full report + SPEAK: line (system_diagnosis contract).")
    mode.add_argument("--briefing", action="store_true", help="Full report + SPEAK: line (daily_briefing contract).")
    mode.add_argument("--metric", choices=_METRIC_KEYS, help="One metric + SPEAK: line (system_metric_query contract).")
    parser.add_argument(
        "--json-out", default=None,
        help="Write the {summary, metrics} report-window payload here (--diagnosis/--briefing/--metric only).",
    )
    args = parser.parse_args()

    report = collect()
    if args.diagnosis:
        print_diagnosis(report, args.json_out)
    elif args.briefing:
        print_briefing(report, args.json_out)
    elif args.metric:
        print_metric(report, args.metric, args.json_out)
    elif args.pretty:
        render_table(report)
    else:
        print(json.dumps(report))


if __name__ == "__main__":
    main()
