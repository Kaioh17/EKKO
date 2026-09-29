# Contributing

## Setup

```powershell
.\dev.ps1            # Windows: creates pvenv, installs deps, starts app + backend + listener
.\dev.ps1 -NoListener
```

```bash
./dev.sh             # Linux / macOS / WSL
```

## Before you open a pull request

```bash
python -m pytest                       # backend tests
ruff check .                           # lint
pnpm -C ekko-ui lint && pnpm -C ekko-ui test
cargo clippy --all-targets -- -D warnings && cargo test   # in ekko-ui/src-tauri
```

CI runs the same checks on Windows and Linux.

## Ground rules

- Settings a user might change belong in the settings database (`backend/schemas/settings.py`), not in environment variables or module constants.
- `.env` holds API keys only.
- Anything specific to one person or machine goes in `personal/` (gitignored), never in the tracked tree.
- Keep dependencies locked: change `requirements.txt`, then run `python locks/compile.py`.
