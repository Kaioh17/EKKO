# scripts/

System-level action scripts, called as handlers by the intent routing
layer (`routing/`, see `routing/README.md`). Each script does one
system-level thing and can be run and tested directly, independently of
the listener or the matcher.

Split by OS: `windows/` (`.ps1`) and `linux/` (`.sh`). `routing/host.py`
picks which tree at run time from `EKKO_OS` in `.env` (`windows`, `linux`,
`mac`, or `auto` to detect via `platform.system()` -- `mac` has no tree
yet, see "OS support" below). Anything genuinely cross-platform -- the
Python payloads a handler on either OS shells out to -- lives directly in
`scripts/`, not under either tree.

Every handler is named by an intent in `routing/intents.yaml` as a bare
stem (`handler: open_app`, not a path or extension), and
`routing/config.py` refuses to load a config naming a handler that isn't
an existing script under `scripts/<os>/` for the current `EKKO_OS`. Those
two files together are the whole permission boundary: nothing outside
`scripts/<os>/` can be executed, and the handler name can't contain a
path separator at all (`routing/config.py`'s `_BARE_NAME_RE`), so a
config-file traversal is impossible by construction, not just checked for.

## What's here

Cross-platform (`scripts/`, not under either OS tree):

- `system_metrics.py` -- CPU/RAM/disk/battery/GPU/network via `psutil`,
  the Linux/mac counterpart to `windows/lib/system_metrics.ps1`'s
  Get-\*Metric functions (which keep their own native CIM implementation,
  untouched). Also owns the threshold/summary/report-window-payload logic
  behind its `--diagnosis`/`--briefing`/`--metric` modes -- see its own
  docstring for why that split isn't symmetric with Windows (bash has no
  native JSON parser the way PowerShell's `ConvertFrom-Json` is, so the
  full output contract lives in Python rather than in
  `linux/lib/system_metrics.sh`, untested, on the one OS this project has
  no machine to run it on).
- `check_memory_activity.py` -- per-process memory scan (`psutil`,
  already portable). Its default JSON-only behaviour is unchanged from
  before the OS split; `--speak`/`--json-out` are additions for
  `linux/check_memory_activity.sh`'s benefit, building the same
  detail-lines-plus-`SPEAK:` contract `windows/check_memory_activity.ps1`
  builds itself via PowerShell's native JSON parsing.
- `watchlist_report.py` -- stock quotes (`yfinance`), same `--speak`/
  `--json-out` addition as `check_memory_activity.py`, same reasoning.
- `render_report.py` / `daily_briefing.py` -- rich-rendered report/briefing
  windows, launched by both OS trees' report-window helper (see below).
  `wait_for_close()` branches on `sys.platform` (`msvcrt` on Windows,
  `select()` on POSIX) since the two have no shared stdlib primitive for
  "poll for a keypress with a timeout."
- `media_handler.py` -- Python wrapper around `<os>/media_control`,
  parses its JSON result. Not named by any `routing/intents.yaml` handler
  today; resolves its script path directly via `routing/host.py`.

`windows/` (PowerShell, matching the native execution environment on that
OS -- `routing/execute.py` invokes handlers as
`powershell -NoProfile -ExecutionPolicy Bypass -File`, see `routing/host.py`'s
`command()`):

- `open_task_manager.ps1` -- opens Windows Task Manager (`taskmgr.exe`,
  built into Windows).
- `open_app.ps1` -- opens one of a fixed set of applications, named by
  `-App`. Targets Start Menu shortcuts rather than exe paths, since none
  of these apps are on PATH and their install directories move with
  updates.
- `open_apple_music.ps1` -- opens the Apple Music web player
  (music.apple.com) in Brave. No parameters, fixed URL.
- `web_search.ps1` -- opens a Brave Search results page for `-Query`.
  The one handler in this folder whose parameter isn't checked against a
  `[ValidateSet(...)]`, a search query can't be a closed vocabulary, see
  its own header comment and `routing/intents.yaml`'s `web_search` for
  why that's still safe.
- `open_research_tabs.ps1` -- opens a Brave Search tab plus up to 3
  curated URLs. The one script in this folder **not** named by any
  `routing/intents.yaml` handler -- it's called directly by
  `llm_fallback/claude_code/fallback.py`'s `open_research_tabs()` via
  `routing/host.py`'s `script()`/`command()`, alongside a spoken
  open-ended `answer`, never through `routing/execute.py` or a matched
  `IntentBundle`. `-Query` works the same as `web_search.ps1`; `-Urls` is
  `|`-delimited (not comma -- see its own header comment for why) and
  independently re-validated as absolute http/https before anything
  reaches `Start-Process`, regardless of what already filtered them
  upstream.
- `open_briefing_tabs.ps1` -- opens exactly the given `-Urls` in Brave,
  no search tab. Also not named by any `routing/intents.yaml` handler --
  called directly by `scripts/daily_briefing.py`'s `open_top_news_tabs()`
  (via `routing/host.py`) to open the top narrated stories' own sites.
  Distinct from `open_research_tabs.ps1` above rather than reusing it:
  that script always opens a Brave Search tab first, which isn't wanted
  here. Same `|`-delimited/re-validated `-Urls` handling as
  `open_research_tabs.ps1`.
- `system_diagnosis.ps1` -- reports on the laptop itself (CPU, RAM, disk,
  battery, GPU, network), not on EKKO's own listener process (that's
  `system\status.ps1`, a separate concern). No parameters, always reports
  all six metrics with threshold flags. Its confirmation EKKO speaks is
  dynamic text rather than one of `feedback.speech.RESPONSES`' fixed
  variants, see its own header comment and the `SPEAK:` convention below.
- `system_metric_query.ps1` -- answers a question about ONE of those same
  six metrics, named by `-Metric` (`battery`/`cpu`/`ram`/`disk`/`gpu`/
  `network`). Shares its queries with `system_diagnosis.ps1` via
  `lib\system_metrics.ps1` rather than duplicating them. Also uses the
  `SPEAK:` convention.
- `check_memory_activity.ps1` -- reports which processes are using the
  most memory right now, a ranked list rather than
  `system_metric_query.ps1 -Metric ram`'s single percentage. No
  parameters. Shells out to the shared `../check_memory_activity.py` for
  the actual scan (default JSON mode, parsed here via
  `ConvertFrom-Json`) and uses the `SPEAK:` convention, same shape as
  `system_diagnosis.ps1`.
- `current_datetime.ps1` -- answers "what's today's date" / "what time is
  it" straight from `Get-Date`. No parameters, no external calls. Added
  after `llm_fallback/gemini/logs/fallback.jsonl` showed this question
  reaching the LLM fallback and getting "I don't have access to real-time
  clock or calendar information" -- an honest answer for a model with no
  search tool, but the wrong home for this question entirely: the answer
  was always sitting in this machine's own clock. Uses the `SPEAK:`
  convention like `system_metric_query.ps1`.
- `lib\system_metrics.ps1` -- not a handler, not named by any intent.
  Dot-sourced by `system_diagnosis.ps1`, `system_metric_query.ps1`, and
  `daily_briefing.ps1`; one function per metric, each returning `$null`
  fields (not throwing) when that metric is unavailable on this machine
  (no battery, no nvidia-smi, ...), so "GPU is unavailable" is a real
  answer, not a crash. Also holds the shared threshold defaults every
  caller flags against, `Open-ReportWindow` (see below), and
  `Open-BriefingWindow`, its sibling for `daily_briefing.ps1` --
  same launch shape (JSON temp file, new `wt.exe` tab), pointed at
  `../../daily_briefing.py` instead of `../../render_report.py`.
- `daily_briefing.ps1` -- "what's happening today"/"what do I need to
  know" and similar. Reuses `system_diagnosis.ps1`'s metric/threshold
  logic verbatim (same `lib\system_metrics.ps1` dot-source) for an
  immediate spoken system-health summary, then calls
  `Open-BriefingWindow` (above) to launch a separate window covering tech
  news and a stock watchlist. Deliberately does none of the slow work
  itself -- see its own header comment for why that's the whole latency
  story for this command.
- `watchlist_report.ps1` -- "pull up my watch list" / "how are my stocks
  doing". Just the stock watchlist (`config\daily_briefing.yaml`'s
  `tickers`), price and % change, spoken out loud via a `SPEAK:` line and
  shown in the same `Open-ReportWindow` table the system reports use. The
  fetch is in the shared `../watchlist_report.py` (default JSON mode).
  Distinct from `daily_briefing.ps1`: no news, no LLM commentary, no
  detached window -- it does its work inline and answers with real
  numbers, fast enough to finish inside `routing\execute.py`'s handler
  timeout.
- `media_control.ps1` -- global media control via simulated OS media keys
  (`keybd_event`). Player-agnostic: the OS routes the key to whatever app
  currently holds media focus.

`linux/` (bash, invoked as `bash <path> --slot value ...` --
`routing/host.py`'s `command()`; POSIX flag names are the slot name
kebab-cased, e.g. `app` -> `--app`):

- `open_task_manager.sh` -- first found of `gnome-system-monitor` /
  `ksysguard` / `xfce4-taskmanager`, falling back to `top` in a terminal.
  No single built-in the way `taskmgr.exe` is, so this tries the common
  desktop-environment monitors in order.
- `open_app.sh` -- same `--app` slot as `open_app.ps1`, binary names
  (`google-chrome`, `code`, `discord`, `steam`, `brave-browser`, `cursor`)
  checked via `command -v` rather than Start Menu shortcuts.
- `web_search.sh` / `open_apple_music.sh` / `open_research_tabs.sh` /
  `open_briefing_tabs.sh` -- `brave-browser` (falling back to `brave`,
  and to `xdg-open` where there's just one URL) instead of Brave's
  Windows install-path search. URL percent-encoding and http/https
  validation both go through a `python3 -c` one-liner (stdlib
  `urllib.parse`) rather than a bash reimplementation -- same
  "stdlib does it" reasoning as everywhere else these scripts shell out
  to Python for anything past simple string handling.
- `system_diagnosis.sh` / `system_metric_query.sh` / `daily_briefing.sh`
  -- thin wrappers around `../system_metrics.py`'s `--diagnosis`/
  `--metric`/`--briefing` modes (see that file's docstring for why almost
  all the logic lives there, not here) plus `lib/system_metrics.sh`'s
  `open_report_window`/`open_briefing_window`.
- `check_memory_activity.sh` / `watchlist_report.sh` -- thin wrappers
  around the shared `../check_memory_activity.py` / `../watchlist_report.py`,
  called with `--speak --json-out` (see "What's here" above).
- `current_datetime.sh` -- GNU `date`, `%-d`/`%-I` dropping the leading
  zero the same way the spoken text should sound.
- `media_control.sh` -- `playerctl` (MPRIS) for play/pause/next/previous/
  stop; `wpctl` (PipeWire) falling back to `amixer` (ALSA) for volume/mute,
  since those have no MPRIS equivalent. `ponytail:` only the two most
  common volume tools are tried, see the script's own comment for the
  upgrade path if neither is what a given distro uses.
- `lib/system_metrics.sh` -- not a handler, sourced by the three scripts
  above. Finds a terminal emulator (`gnome-terminal` / `konsole` /
  `x-terminal-emulator` / `xterm`, first found) and launches
  `../../render_report.py` / `../../daily_briefing.py` in it, the Linux
  counterpart to `windows/lib/system_metrics.ps1`'s `Open-ReportWindow`/
  `Open-BriefingWindow`. `ponytail:` `x-terminal-emulator` and bare
  `xterm` both get xterm-style args, which isn't guaranteed for every
  possible target of that symlink; upgrade path is in the script's own
  comment.

## OS support

Two trees exist today: `windows/` and `linux/`. `mac` is a name
`routing/host.py` and `routing/intents.yaml`'s `os:` key both already
understand, but no `scripts/mac/` tree has been written -- setting
the `mac` OS setting fails loudly at config load (`no such file`) rather than
silently doing the wrong thing.

Handlers specific to one person or machine (a vendor tray utility, a private
WSL session) don't belong here: put the script in `personal/scripts/<os>/` and
its intent in `personal/intents.yaml` (both gitignored). `routing/config.py`
merges them in, with the same bare-name containment rules. An intent can carry
`os: [windows]` to drop out entirely elsewhere, so on other systems it simply
falls through to the LLM fallback instead of matching and then failing.

## Conventions

- Each script writes a one-line message describing what happened to
  stdout (or stderr on failure), and exits with one of:

  | code | meaning | what EKKO says |
  |---|---|---|
  | 0 | did the thing | "Done." |
  | 3 | already in the desired state, nothing to do | "That's already done." |
  | anything else | failed | "Something went wrong running that." |

  3 exists so `routing/execute.py` can tell "launched it" from "it was
  already up" without parsing stdout. Use the remaining non-zero codes to
  distinguish failure kinds for whoever is reading the log; the response
  layer treats them all the same.
- **The `SPEAK:` line.** A handler that wants EKKO to speak dynamic text
  instead of one of `feedback.speech.RESPONSES`' fixed confirmations
  writes a line reading `SPEAK: <text>` to stdout. `routing/execute.py`'s
  `spoken_override()` picks up the LAST such line (only on exit 0) and
  `listener/vad_listener.py` speaks it verbatim -- it doesn't have to be
  the literal final line of stdout, a handler is free to log more after
  it (e.g. `Open-ReportWindow`/`open_report_window` below logging whether
  the visual window opened). Everything else on stdout is still just the
  log detail; only a line with that exact prefix is treated specially.
  `system_diagnosis`, `system_metric_query`, `daily_briefing`, and
  `check_memory_activity`/`watchlist_report`'s `--speak` modes are
  currently the only handlers that use this, see their own header
  comments. Not something every handler needs, most confirmations
  ("Opened chrome.") are fully known ahead of time and belong in
  `RESPONSES`; reach for this only when the thing worth saying was
  computed at run time and can't be a fixed string.
- Mostly no parameters. `open_app` takes an `app` slot,
  `system_metric_query` takes a `metric` slot, since each is one intent
  with a slot rather than one intent per value. Any handler backing a
  `closed_vocabulary` slot must constrain its values (PowerShell's
  `[ValidateSet(...)]`, bash's own `case` statement) mirroring the
  vocabulary in `routing/intents.yaml`. That's defence in depth, not
  redundancy: `routing/slots.py` already guarantees only a canonical
  config value gets that far, so this should be unreachable, and a bug
  upstream should fail as a parameter/case error rather than launch
  something unintended. `web_search` and `hands_on` are the two
  exceptions, backing a `free_text` and a `transcript` slot respectively
  (see `routing/README.md`), so there's no fixed vocabulary to validate
  against; both are still safe for the reasons in their own header
  comments, and in both cases it comes down to never putting the value on
  a shell line -- `web_search` puts it in a URL parameter, `hands_on`
  hands it to a parser whose output `control_center/hands_on/file_gateway/policy.py`
  re-derives before anything touches disk. A new handler taking one of
  these slot types has to earn that the same way.
- No shared state between scripts (within one OS tree -- Brave-path/
  binary-name lists are duplicated per script on purpose, see each
  script's own header comment).

## Dependencies

`daily_briefing.py` (and `watchlist_report.py`, which imports it) need
`yfinance` alongside `requests`/`rich` (already used by `render_report.py`)
and Piper/sounddevice (the listener's own TTS deps, used via
`feedback/speech.py`). Not in the root `requirements.txt` -- see
`daily_briefing.py`'s header comment for the venv split and why.

- **Windows**: `pvenv\Scripts\pip install yfinance` (pvenv already has
  the rest).
- **Linux**: `venv/bin/pip install yfinance`.

`linux/media_control.sh` needs `playerctl` for playback control, and
either `wpctl` (PipeWire, most current distros) or `amixer` (ALSA) for
volume/mute -- install whichever your distro ships.

## Config

`config\daily_briefing.yaml` (repo root) -- the stock tickers, news
topics, and `extra_tabs` (fixed URLs opened in Brave every briefing
alongside the top news stories) `daily_briefing.py` reads. Hand-editable,
no code change needed to add a ticker, topic, or tab, see the file's own
header comment. `watchlist_report.py` reads the same `tickers` list (and
the same `ticker_names` friendly-name map -- so a symbol like `GC=F` reads
as "gold" aloud and on screen), so "add gold to my watch list" and the
daily briefing's Stocks section stay in sync by construction.

`EKKO_OS` in `.env` (repo root) -- `windows`, `linux`, `mac`, or `auto`
(detect via `platform.system()`). See `routing/host.py`.

## Running directly

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows\open_task_manager.ps1
powershell -ExecutionPolicy Bypass -File scripts\windows\open_app.ps1 -App vscode
powershell -ExecutionPolicy Bypass -File scripts\windows\watchlist_report.ps1
```

```bash
bash scripts/linux/open_task_manager.sh
bash scripts/linux/open_app.sh --app vscode
bash scripts/linux/watchlist_report.sh
```

Or through the router, which is what actually happens at runtime (reads
`EKKO_OS` from `.env` either way):

```
python routing\execute.py --dry-run "pull up task manager"
python routing\execute.py "pull up task manager"
```
