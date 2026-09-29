"""Two research mechanisms, shared across every provider:

1. `open_research_tabs()` -- launches scripts/open_research_tabs.ps1 to pop
   real Brave tabs for a `urls` list a provider's `answer` came back with.
2. `wants_research()`/`_run_research()` -- EKKO's own live-web research
   pipeline (control_center/hands_on/research), which reads a handful of
   pages and folds the excerpts into the prompt as `web_research` context
   before the model ever answers.

Both are provider-agnostic: neither cares which model the caller is about
to use, only what `urls`/`transcript`/`domain` it was given.
"""

import re
import subprocess
import sys
import threading
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from routing import host  # noqa: E402

# Generous relative to a provider's own call timeout: this only ever waits
# on the interpreter (powershell.exe / bash) long enough for it to launch
# Brave and return, not on Brave itself to open, so it should return in
# well under a second in practice. Wide margin here is cheap insurance
# against a slow process start, not an expectation of it actually taking
# this long.
DEFAULT_TABS_TIMEOUT = 10.0


def _reap_research_tabs(process: subprocess.Popen, timeout: float) -> None:
    """The completion half of open_research_tabs(), split out onto its own
    daemon thread so the launch half can return the moment the process
    exists rather than once the script (and Brave) have actually finished.
    Only ever prints -- there is no caller left to hand a result to by the
    time this runs, the turn that launched it already moved on.

    Never lets an exception escape onto the thread: a background thread
    that dies from an uncaught exception just prints a traceback to stderr
    and vanishes, which is a worse silent failure than the thing it's
    trying to report.
    """
    try:
        _, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"[llm_fallback] research tabs: did not finish within {timeout}s")
        return
    except Exception as exc:  # noqa: BLE001 - see docstring, this must not propagate
        print(f"[llm_fallback] research tabs: reaper failed: {exc}")
        return
    if process.returncode != 0:
        print(f"[llm_fallback] research tabs: exited {process.returncode}: {stderr.strip()[:300]}")


def open_research_tabs(
    query: str,
    urls: list[str],
    timeout: float = DEFAULT_TABS_TIMEOUT,
) -> str | None:
    """Launches scripts/open_research_tabs.ps1 and returns as soon as the
    process exists, without waiting for it (or Brave) to finish. Opens a
    Brave Search tab for `query` plus up to 3 curated `urls` a provider's
    answer already found. The model's `urls` are not trusted blindly even
    here: the script itself re-checks each one is an absolute http/https
    URI before it ever reaches Start-Process, same "re-validate at the
    boundary that acts on it" reasoning validate.py's validate_pick()
    applies to an intent pick.

    Deliberately doesn't wait: Popen returns as soon as the process exists;
    _reap_research_tabs() picks up watching it from a daemon thread so a
    real failure (Brave missing, a bad exit code) still gets logged, just
    after the fact instead of gating the speech.

    Never raises -- this is a nice-to-have alongside the spoken answer, not
    something that should ever crash or block the caller (the always-on
    listening loop) if Brave is missing, the script errors, or powershell
    fails to start at all. Returns an error string only for a failure this
    function can detect *synchronously* -- the script not existing, or the
    process failing to even start; None means "launched", not "succeeded".

    query is passed as-is to -File's -Query argument, which subprocess
    passes as a single argv entry (shell=False) -- there is no shell for
    it to inject into. urls is joined with '|' into a single -Urls/--urls
    argv entry, not passed as several separate ones -- a comma-joined
    string (the delimiter PowerShell's CLI binder actually splits on for a
    [string[]] parameter) isn't safe here since a URL's query string can
    itself contain a literal comma.
    """
    # scripts/<os>/open_research_tabs.{ps1,sh} via routing/host.py's script(),
    # resolved per call so the UI's OS setting applies. Not a validated
    # IntentBundle handler (no intent names it), so it skips execute.py's
    # build_command()/resolve_handler(), which are shaped around a bundle.
    script = host.script("open_research_tabs")
    if not script.is_file():
        return f"open_research_tabs script not found at {script}"

    slots = {"query": query}
    if urls:
        slots["urls"] = "|".join(urls)
    command = host.command(script, slots)

    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        return f"{command[0]} not found on PATH"

    threading.Thread(target=_reap_research_tabs, args=(process, timeout), daemon=True).start()
    return None


# Live-web research for current-events questions, done by EKKO itself rather
# than by any model: control_center/hands_on/research searches, fetches and
# reads a handful of pages (BeautifulSoup) and the excerpts ride along in
# the prompt. The UI's llm.research_enabled turns it off;
# RESEARCH_BUDGET_SECONDS is its wall-clock cap, kept well under any
# provider's own call timeout.
def research_enabled() -> bool:
    from backend.config import get  # lazy, same as brain/core.py

    return get("llm").research_enabled


RESEARCH_BUDGET_SECONDS = 10.0
_RESEARCH_WORDS = re.compile(
    r"\b(what('?s| is| are| was) (going on|happening|new|up)|what happened|news|latest|lately|recent(ly)?|"
    r"update on|updates on|catch me up|any(thing)? new|headlines|this week|right now|"
    r"tell me about|who is|who are|how is .+ doing|how are .+ doing)\b",
    re.I,
)


def wants_research(transcript: str) -> bool:
    """Cheap gate: only current-events / "tell me about X" shaped questions
    pay for a web round trip; chit-chat and maths don't."""
    return bool(_RESEARCH_WORDS.search(transcript))


def run_research(transcript: str, domain: str | None = None):
    """Returns (ResearchResult, max_chars) or None. Never raises; None when
    research is unavailable (missing bs4/httpx, nothing found). Imported
    lazily so a listener starts without them.

    A domain with a domains/<key>/research.yaml (finance's: ticker lookup
    via yfinance, news and SEC filings, 6k-char cap) gets that pipeline
    first; it declines when nothing in the question applies (no ticker),
    and then the generic current-events path runs if the transcript looks
    like one."""
    try:
        if domain:
            from control_center.hands_on.research.domain import research_for_domain
            found = research_for_domain(domain, transcript)
            if found:
                return found
        if wants_research(transcript):
            from control_center.hands_on.research import research
            return research(transcript, max_pages=5, budget=RESEARCH_BUDGET_SECONDS), 9000
    except Exception as exc:
        print(f"[llm_fallback] research unavailable: {type(exc).__name__}: {exc}")
    return None
