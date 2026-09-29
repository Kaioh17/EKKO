"""PyInstaller entry point: `ekko-backend.exe` runs the backend, and
`ekko-backend.exe listener [flags]` runs the voice listener (see
backend/__main__.py)."""

import multiprocessing
import sys

from backend.__main__ import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main(sys.argv[1:])
