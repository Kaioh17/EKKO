# system/

Process lifecycle for the always-on listener: starts `listener\vad_listener.py`
at logon, keeps it alive, logs it, and gives you commands to check on it,
stop it, or change its execution mode.

Separate from `scripts\`, which is the *execution* boundary (intent handlers
the router may run — see `scripts\README.md`). Nothing here is reachable from
a voice command; this directory only starts and stops the listener process.

**Native Windows only** (`pvenv`, not the WSL `venv\`). Always-on mic access
under WSL2/WSLg was unresolved; running the listener as a background task
forced the decision, and native Windows won — no WSLg PulseAudio-over-RDP
bridge. Runs at logon in your interactive session, not as a SYSTEM service,
since SYSTEM services can't see per-user audio devices.

Windows-only is a current constraint, not a permanent one — making this
OS-agnostic (Linux/Mac) is planned, see `readme.md`.

## Prerequisites (once)

1. Install deps + CUDA torch: `.\voice_auth\setup.ps1`
2. Complete voice enrollment (`voice_auth\auedio.md`) — the listener needs a
   reference embedding to mean anything.
3. Run once in the foreground before backgrounding it:
   ```powershell
   pvenv\Scripts\python.exe listener\vad_listener.py --no-execute
   ```
   First run downloads openWakeWord's ONNX models and surfaces any
   mic/permissions issue in a visible terminal. Confirm it loads, hears you,
   and responds before moving on.

## Usage

```powershell
.\system\install_task.ps1     # register the "EKKO Listener" scheduled task
.\system\start.ps1            # trigger it now, without logging off
.\system\status.ps1           # task state, live PID, current mode, log tail
.\system\stop.ps1             # stop it
.\system\uninstall_task.ps1   # remove the scheduled task
```

After `install_task.ps1`, the listener starts automatically every logon.
`start.ps1` only exercises the action, not the logon trigger — confirm that
separately with a real logoff/logon.

## Execution mode: `listener.flags.txt`

Contents get passed straight through to `vad_listener.py` as CLI flags.
Add `--no-execute` (routes, logs, and speaks outcomes, but never runs the
matched handler) to pull back to routing-only, or any other flag, e.g.
`--verify-threshold 0.7`. Then:

```powershell
.\system\stop.ps1
.\system\start.ps1
```

Flags are read once at wrapper start, not hot-reloaded — a restart is
required to pick up a change.

## Dev mode: watching it live

```powershell
.\system\dev.ps1                     # everything, in a new Windows Terminal tab
.\system\dev.ps1 -Only route,capture # just what got said and how it routed
.\system\dev.ps1 -NoWindow           # run here instead of opening a tab
```

`status.ps1` is a snapshot; `dev.ps1` is a live, color-coded tail merging the
listener's stdout/stderr (`logs\listener.out.log` / `.err.log`), routing
decisions (`routing\logs\routing.jsonl`), and accepted transcripts
(`listener\captures\transcripts.jsonl`). Read-only — safe to run whether the
listener is running (background or foreground) or not.

Rendering is `dev_tail.py` (`rich`, via `pvenv`); `dev.ps1` just opens the
window for it (`wt.exe`, falls back to the current console). See
`dev_tail.py`'s header for polling/partial-line details.

## How it works

- `run_listener.ps1` is what the scheduled task runs: launches
  `pvenv\Scripts\python.exe -u listener\vad_listener.py` with
  `listener.flags.txt`'s flags, redirects output to `logs\listener.out.log` /
  `.err.log`, writes the PID to `logs\listener.pid`, and restarts on exit with
  exponential backoff (5s, doubling, capped at 300s; resets after a run
  survives a minute). This loop is what's actually always-on; the scheduled
  task just starts it at logon.
- `install_task.ps1` / `uninstall_task.ps1` register/remove a Task Scheduler
  task, "EKKO Listener": triggers at logon, runs interactively (not SYSTEM),
  hidden window, no time limit, plus Task Scheduler's own coarser
  restart-on-failure as a second safety net.
- `stop.ps1` calls `Stop-ScheduledTask`, killing the whole process tree via
  its Job Object, with a PID-file `Stop-Process` fallback for orphans.
- Logs rotate once, capped at 10MB: an oversized log is renamed to `.old`
  (overwriting any previous one) on the next wrapper start.

## Periodic restart

The listener runs best fresh, so `restart.ps1` cycles it (`stop.ps1` then
`start.ps1`) on a schedule instead of running indefinitely:

```powershell
.\system\install_restart_task.ps1     # register the "EKKO Restart" scheduled task
.\system\restart.ps1                  # trigger a cycle now
.\system\uninstall_restart_task.ps1   # remove the scheduled task
```

`install_restart_task.ps1` registers a task firing at logon and every 3
hours after. Each run: shows an always-on-top popup (`restart_ui.py`, via
`pythonw` — no console window) for the cycle's duration, calls `stop.ps1`
then `start.ps1`, confirms the listener came back up (PID file), closes the
popup, and logs every step to `logs\restart.log`. `status.ps1` shows the
task's state and log tail. The popup is a separate process from
`ui\voice_ui.py`'s in-process overlay — deliberately, since that overlay dies
the instant `stop.ps1` kills it, and this needs to stay visible through that.

## Pruning captures

`vad_listener.py` saves a WAV to `listener\captures\` for every detected
speech segment, wake word or not — left running, that directory grows
unbounded. `listener\prune_captures.py` trims it to the newest N;
`install_prune_task.ps1` registers a scheduled task to run it automatically:

```powershell
.\system\install_prune_task.ps1     # register the prune task
.\system\uninstall_prune_task.ps1   # remove it
```

Two triggers, whichever fires first: every logon, and daily at 4:00 AM (a
backstop for a laptop that stays logged in for days). Unlike the listener
task, this is a single quick run — no PID file, no retry loop, one line per
run in `logs\prune.log`. `status.ps1` reports its state too. Extra flags
(e.g. `--keep 25`) go in `system\prune_captures.flags.txt`, read fresh each
run.

## Files

- `run_listener.ps1` — wrapper the listener's scheduled task runs
- `install_task.ps1` / `uninstall_task.ps1` — register/remove the listener's scheduled task
- `start.ps1` / `stop.ps1` — manually trigger/stop the listener
- `status.ps1` — listener + prune task state, live PID, current mode, log tails
- `dev.ps1` — opens `dev_tail.py` in a new terminal tab for a live log view
- `dev_tail.py` — live multi-source log dashboard (`rich`); what `dev.ps1` runs
- `listener.flags.txt` — extra CLI flags for `vad_listener.py`; controls `--no-execute` vs live
- `prune_captures.ps1` — what the prune task runs; wraps `listener\prune_captures.py`
- `install_prune_task.ps1` / `uninstall_prune_task.ps1` — register/remove the prune scheduled task
- `prune_captures.flags.txt` — extra CLI flags for `prune_captures.py` (e.g. `--keep`)
- `restart.ps1` — cycles the listener (stop → start) with a popup and a `restart.log` entry per run
- `restart_ui.py` — the popup `restart.ps1` shows during a cycle (`pythonw`, `tkinter`)
- `install_restart_task.ps1` / `uninstall_restart_task.ps1` — register/remove the restart scheduled task (every 3 hours)
- `logs\` — `listener.out.log`, `listener.err.log`, `listener.pid`, `prune.log`, `restart.log` (created at runtime, not tracked)
