# Host protocol

This is the plan for putting ekko on other people's computers.
It covers who it is for, how a non-technical person installs it, how updates arrive, what works and what doesn't yet, and what a maintainer does to ship a release.
It is written so someone can follow it without knowing how ekko works inside.

## What a user gets

One installer file, `ekko_<version>_x64-setup.exe`, from the GitHub Releases page.
It is about 250 MB, because it carries its own Python runtime so the user installs nothing else.
Installed, it takes about 1 GB.

- No admin rights are needed. It installs for the current user only, in `%LOCALAPPDATA%\ekko`.
- No Python, Node or Rust is needed.
- It adds a Start menu entry and an uninstaller.
- Nothing runs in the background until the user turns on "Start ekko listening when I sign in".

## Requirements

- Windows 10 or 11, 64-bit.
- 4 GB of RAM (8 GB is comfortable), and about 1.5 GB of free disk space.
- A microphone.
- Internet for the first launch (voice model download) and for any cloud LLM.
- No graphics card is needed. If an NVIDIA GPU is present, speech recognition uses it.

## Installing

1. Download the installer from the [latest release](https://github.com/Kaioh17/EKKO/releases/latest).
2. Double-click it.
   Windows may show a blue "Windows protected your PC" (SmartScreen) box, because the installer is not yet signed with a paid code-signing certificate.
   Click **More info**, then **Run anyway**.
   This goes away once a signing certificate is bought (see "Open items").
3. Follow the installer.
   It takes under a minute.
4. Open **ekko** from the Start menu.

### First launch

1. A banner reads "Setting up ekko: downloading ...".
   ekko is fetching its voice models (about 65 MB) and checking their checksums.
   This takes a minute or two on a normal connection.
2. Windows asks whether ekko may use the microphone.
   Choose **Yes**.
   If you said no by accident, turn it on under Settings > Privacy > Microphone.
3. Open **Models** and paste an API key for the assistant you want to use.
   Gemini has a free key at aistudio.google.com/apikey.
   ekko keeps keys only on this computer and never shows them again.
4. Say "hey ekko".

Everything except the answers to open-ended questions works without an API key.
Those questions need a key (or a local model) for whichever provider you choose.

## Start at sign-in (the `system/` install)

By default ekko listens only while its window is open.
To keep it listening all the time, open **General** and turn on **Start ekko listening when I sign in to Windows**.

What that switch does, in order:

1. The app runs `system\install_task.ps1`, which registers one per-user Task Scheduler task named **EKKO Listener**.
2. The task runs at every sign-in, hidden, with no time limit, and restarts itself if it fails.
3. The task runs `system\run_listener.ps1`, which starts the backend (`ekko-backend.exe`), and the backend starts and supervises the voice listener.
4. If the backend or listener crashes, the wrapper and the backend restart them, waiting longer between repeated failures (5 seconds up to 5 minutes).
5. When you open the ekko window, it finds the running backend through `runtime.json` and attaches to it.
   There is never a second copy.
   Closing the window leaves the listener running.

Turning the switch off runs `system\uninstall_task.ps1` and removes the task.
Uninstalling ekko removes the task too, and stops the backend so the files can be deleted.
Updating stops the backend for the moment the new version installs, then it comes back at the next sign-in or when you open the app.

No admin rights are needed, because the task belongs to the user, runs in the user's own session, and needs the user's microphone.
It is deliberately off by default: an always-listening microphone is something people should opt into.

For developers and support, the same scripts work by hand from the install folder or the repo:

| Command | What it does |
|---|---|
| `.\system\install_task.ps1` | Registers the sign-in task |
| `.\system\start.ps1` | Starts it now, without signing out |
| `.\system\status.ps1` | Shows whether it is running, and the log tail |
| `.\system\stop.ps1` | Stops it |
| `.\system\uninstall_task.ps1` | Removes the task |

## Updates

ekko updates itself from GitHub Releases.

1. Each time the app opens, it asks GitHub for `latest.json`.
2. If a newer version exists, a bar appears at the top: "ekko 0.2.0 is available. Update and restart".
3. Clicking it downloads the update, checks its signature against the public key built into the app, installs it, and restarts.
   An update that fails the signature check is never installed.
4. If the computer is offline, nothing happens and the app keeps working.

Users are never updated without clicking.
Settings, keys and memory live in the data folder, so they survive updates.

### If the window is never opened

A user who turned on "Start ekko listening when I sign in" and never reopens the app window would never see the update bar, since that lives in the window.
For that case, the backend itself checks for an update once at startup and once a day after, and shows a plain Windows message box naming the new version if one exists and no ekko window is open.
It notifies at most once per version.
That message box is a notice, not an installer: clicking it does nothing but close it.
The user (or a script) still opens ekko and clicks **Update and restart** to actually update, exactly as above.

The backend also refuses to attach to a stale copy of itself.
Each running backend writes its own version to `runtime.json`.
If the app finds one there whose version doesn't match its own, for example the sign-in task's backend, still on the old files in the moment right after an update replaced them, it asks that backend to stop and starts a fresh one instead of attaching to the old one.

### Known problems with this approach, not yet fixed

- **The backend doesn't restart itself right after an update.**
  The installer stops it so the update can replace its files, and it only comes back at the next sign-in or the next time someone opens ekko.
  Between those, if start-at-sign-in is on, ekko isn't listening.
  The fix is one more line in the installer hook, to start the sign-in task again once the update finishes, so it doesn't have to wait for a sign-in or an open.
- **No partial updates.**
  Every update downloads the full ~250 MB installer again, mostly the bundled Python runtime, which rarely changes between releases.
  A smaller update would need a different build (a thin launcher that always downloads its own runtime), which is a bigger change than this project needs yet.
- **Voice models don't update with the app.**
  They're pinned by checksum to one release (see `models.py`); shipping a new model needs a new app release, not a way to update just the models.

## Where things live

| What | Where |
|---|---|
| The app | `%LOCALAPPDATA%\ekko` |
| Settings, API keys, memory, logs, recordings, models | `%APPDATA%\io.usemaison.ekko` |
| Backend log | `%APPDATA%\io.usemaison.ekko\backend\logs\backend.log` |
| Listener log | `%APPDATA%\io.usemaison.ekko\backend\logs\listener.log` |
| Your own commands and scripts | `%APPDATA%\io.usemaison.ekko\personal\` |

To reset ekko completely, uninstall it and delete the data folder.
To back up your settings, copy `backend\ekko.db` and `.env` from the data folder.

## What ekko sends off your computer

- Audio never leaves the computer.
- When ekko can't handle a request itself, the text of that request, plus your last few turns, goes to the LLM you picked.
- If you choose OpenAI for speech, each spoken reply is sent to OpenAI to be turned into audio.
- The update check contacts GitHub.
- The first launch downloads models from GitHub and Hugging Face.

## What is possible today, and what isn't

Works:

- Windows 10 and 11, on a laptop with no GPU.
- Wake word, voice commands, spoken replies, chat in the app, live status, and the settings UI.
- Six LLM providers, with automatic failover between them, spend caps for the paid ones, and a live order you can change.
- Starting at sign-in.
- One-click updates.

Not yet:

- **macOS and Linux installers.**
  The code runs on both (`./dev.sh`), but there is no installer, and the sign-in switch is Windows-only.
- **Choosing Ollama by hardware.**
  Ollama is in the failover list, but ekko doesn't yet check whether the computer can run it well.
  A low-RAM laptop should leave it off.
- **Speaker verification set-up in the app.**
  Enrollment is still a command-line step (`voice_auth/auedio.md`).
  Until someone enrolls, ekko answers to the wake word from any voice.
- **English only.**
  Speech recognition is set to English.
- **Windows on ARM.** Untested.
- **A signed installer.**
  SmartScreen warns until a code-signing certificate is bought.
- **Custom voice commands without editing files.**
  They go in `personal/` as a script plus a YAML entry.

## Support checklist

1. Is the microphone allowed? (Windows Settings > Privacy > Microphone)
2. Does the top of the app show "Setting up ekko"? Wait for it, and check the internet connection.
3. Does the General panel say **Backend: Connected**? If not, close and reopen ekko.
4. Does a spoken answer need an API key? Look under Models > API keys.
5. Open `listener.log` and `backend.log` (paths above) and look at the last lines.
6. Turn off "Start at sign-in", sign out and back in, and try again from a fresh launch.
7. As a last resort, uninstall, delete the data folder, and reinstall.

## Shipping a release (maintainers)

One-time setup:

1. Generate the updater key: `pnpm tauri signer generate -w ~/.tauri/ekko-updater.key -p <password>`.
   Keep the private key and password out of the repo, and back them up: losing the key means installed copies can never update again.
2. Put the public key in `ekko-ui/src-tauri/tauri.conf.json` under `plugins.updater.pubkey`.
3. In the repo's Settings > Secrets, add `TAURI_SIGNING_PRIVATE_KEY` (the key file's contents) and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`.
4. Create a release tagged `models-v1` and attach `hey_ekko.onnx` (the wake-word model).
   Its SHA-256 is pinned in `models.py`, so the file must be the exact one.
   Untick "Set as the latest release", or the updater's `latest.json` address stops finding the app releases.
   The repository must be public, or the download fails for users.
5. Turn on branch protection for `main`, private vulnerability reporting, Dependabot alerts, and secret scanning with push protection.

For each release:

1. Bump the version in `ekko-ui/src-tauri/tauri.conf.json` and `Cargo.toml` (and `ekko-ui/package.json`).
2. `git tag v0.2.0 && git push --tags`.
3. The Release workflow freezes the backend, builds the installer, signs the update, and creates a draft release with `latest.json`.
4. Test the installer on a clean Windows user account.
5. Publish the draft.
   Users' apps see the update as soon as it is published.

## Open items

- Buy a code-signing certificate (Azure Trusted Signing is the cheapest route) so SmartScreen stops warning.
- Publish the `models-v1` wake-word asset (see above).
- Ship the shared command scripts (`scripts/`) in the public repo after review, so a fresh clone has commands to run.
- Add speaker enrollment to the app.
- Add a macOS and Linux installer and a systemd/launchd equivalent of the sign-in switch.
- Choose Ollama automatically from the computer's RAM and GPU.
