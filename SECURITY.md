# Security policy

## Reporting a vulnerability

Please report security problems privately, not in a public issue.
Use GitHub's "Report a vulnerability" button on the repository's Security tab (private vulnerability reporting).
Include what you found, how to reproduce it, and which version you tested.
You can expect an acknowledgement within a few days.

## Supported versions

Only the latest release receives security fixes.

## How ekko is built to limit risk

- The API listens on `127.0.0.1` only, on a random port chosen at each launch.
- Every request needs a random per-launch token, sent in a header (WebSocket: subprotocol), never in a URL.
- Browser-origin requests must come from the app itself.
- The desktop app has no shell, file-system or network permissions beyond talking to that local API.
- API keys are stored only in the per-user data folder, are set-only through the API, and are never returned.
- Voice command handlers run only from `scripts/<os>/` and `personal/scripts/<os>/`, by bare name, without a shell.
- Releases are built in CI from pinned, hash-locked dependencies, and updates are signature-checked before they install.
