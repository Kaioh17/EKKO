"""Pushes a system_diagnosis.ps1 / system_metric_query.ps1 report to
ekko-ui.

Not a handler itself, not named by any intent in routing/intents.yaml.
Launched hidden by scripts/lib/system_metrics.ps1's Open-ReportWindow (or
scripts/linux/lib/system_metrics.sh's open_report_window), alongside the
spoken SPEAK: summary those scripts already produce -- ekko-ui shows it,
EKKO still answers out loud either way, and this failing (no pvenv, backend
down) must never fail the voice pipeline.

Takes one argument: a path to a JSON file written by the shell side,
shaped like:
    {"summary": "...", "metrics": [{"name": "CPU", "detail": "65%", "flagged": false}, ...]}
Reads it once and deletes it (it's a throwaway temp file under $env:TEMP,
not a log).

Usage:
    python scripts/render_report.py %TEMP%/ekko_report_....json
"""

import json
import sys
from pathlib import Path

import ekko_ui


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: render_report.py <path-to-json>")
        return
    path = Path(sys.argv[1])
    try:
        # utf-8-sig, not utf-8: Windows PowerShell 5.1's Set-Content
        # -Encoding utf8 always writes a BOM (there's no BOM-less utf8
        # option pre-PowerShell 6), which plain "utf-8" chokes on
        # decoding. utf-8-sig strips a BOM if present and reads
        # ordinary UTF-8 identically if not.
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        title = "EKKO — System Diagnosis" if len(data.get("metrics", [])) > 1 else "EKKO — System Check"
        ekko_ui.push_report("metrics", title, data)
    finally:
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
