"""Renders a system_diagnosis.ps1 / system_metric_query.ps1 report with
rich, in whatever console window is running it.

Not a handler itself, not named by any intent in routing/intents.yaml.
Launched by scripts/lib/system_metrics.ps1's Open-ReportWindow, in a new
wt.exe tab, alongside (not instead of) the spoken SPEAK: summary those
scripts already produce -- this is a visual companion, EKKO still answers
out loud either way, and this window failing to open (no wt.exe, no rich)
must never fail the voice pipeline, see Open-ReportWindow's try/catch.

Takes one argument: a path to a JSON file written by the PowerShell side,
shaped like:
    {"summary": "...", "metrics": [{"name": "CPU", "detail": "65%", "flagged": false}, ...]}
Reads it once, deletes it (it's a throwaway temp file under $env:TEMP, not
a log), and waits on a keypress at the end so the tab doesn't flash and
vanish the moment rendering finishes -- the whole point of a window you're
meant to look at. That wait is capped at WINDOW_TIMEOUT_SECONDS, though:
this window opened itself, unprompted, off the back of a voice command --
nobody necessarily sat down to watch it -- so it must not wait forever for
a keypress that may never come. It closes on its own instead.

Usage:
    python scripts/render_report.py %TEMP%\\ekko_report_....json
"""

import json
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

import ekko_ui

# How long this window waits for a keypress before closing itself. Ekko
# opened it unprompted; it shouldn't sit there forever unwatched. 90s is
# long enough to read a diagnosis table, short enough that a forgotten tab
# doesn't linger indefinitely.
WINDOW_TIMEOUT_SECONDS = 90.0


def wait_for_close(timeout: float = WINDOW_TIMEOUT_SECONDS) -> None:
    """Blocks until Enter is pressed or `timeout` seconds pass, whichever
    comes first.

    Plain input() can't be time-bounded on its own, and this needs to keep
    working with no extra dependency beyond the stdlib. Windows and POSIX
    have no shared "poll for a keypress with a timeout" primitive in the
    stdlib, so this branches on sys.platform rather than picking one: this
    window is launched by both scripts/windows/lib/system_metrics.ps1's
    Open-ReportWindow and scripts/linux/lib/system_metrics.sh's
    open_report_window, so it has to work under whichever one called it.
    """
    print(f"\nPress Enter to close (auto-closes in {int(timeout)}s)...")
    deadline = time.monotonic() + timeout

    if sys.platform == "win32":
        # Polling kbhit() rather than blocking is what makes the timeout
        # preemptable at all.
        import msvcrt

        while time.monotonic() < deadline:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    return
            time.sleep(0.05)
    else:
        # select() on stdin is the POSIX equivalent of kbhit()+block-with-
        # timeout: wait up to `remaining` seconds for a line to become
        # readable, re-checking the deadline each pass so a redirected/
        # closed stdin (immediate EOF, immediately "ready") can't spin.
        import select

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            ready, _, _ = select.select([sys.stdin], [], [], min(remaining, 0.5))
            if ready:
                sys.stdin.readline()
                return


def render(data: dict, console: Console) -> None:
    metrics = data.get("metrics", [])
    title = "EKKO — System Diagnosis" if len(metrics) > 1 else "EKKO — System Check"

    table = Table(title=title, show_lines=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    for metric in metrics:
        style = "bold red" if metric.get("flagged") else "green"
        table.add_row(metric.get("name", "?"), f"[{style}]{metric.get('detail', '?')}[/{style}]")
    console.print(table)

    summary = data.get("summary")
    if summary:
        any_flagged = any(metric.get("flagged") for metric in metrics)
        console.print(Panel(summary, title="Spoken summary", border_style="red" if any_flagged else "green"))

    # Mirror into ekko-ui, additive alongside this console render -- see
    # ekko_ui.push_report()'s docstring for why this never raises.
    ekko_ui.push_report("metrics", title, data)


def main() -> None:
    console = Console()
    if len(sys.argv) < 2:
        console.print("[red]usage: render_report.py <path-to-json>[/red]")
    else:
        path = Path(sys.argv[1])
        try:
            # utf-8-sig, not utf-8: Windows PowerShell 5.1's Set-Content
            # -Encoding utf8 always writes a BOM (there's no BOM-less utf8
            # option pre-PowerShell 6), which plain "utf-8" chokes on
            # decoding. utf-8-sig strips a BOM if present and reads
            # ordinary UTF-8 identically if not, so this is safe either
            # way, not just a workaround for this one writer.
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            render(data, console)
        except Exception as exc:  # noqa: BLE001 -- this window must show *something*, not crash silently
            console.print(f"[red]Failed to render report: {exc}[/red]")
        finally:
            # Best-effort cleanup of the throwaway temp file. Not the end
            # of the world if this fails (e.g. already gone), so it's not
            # worth its own try/except beyond the outer one already here.
            path.unlink(missing_ok=True)

    wait_for_close()


if __name__ == "__main__":
    main()
