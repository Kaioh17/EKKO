"""PyInstaller entry point: `ekko-backend.exe` runs the backend, and
`ekko-backend.exe listener [flags]` runs the voice listener (see
backend/__main__.py)."""

import multiprocessing
import sys

if __name__ == "__main__":
    # Redirected stdout/stderr (both the backend's own and the listener
    # subprocess's, see backend/supervisor.py) are block-buffered by
    # default; a terminate() on restart/shutdown drops whatever's still
    # buffered, so logs lag or go missing. -u isn't a valid flag for a
    # frozen exe, so line-buffer explicitly instead.
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    multiprocessing.freeze_support()
    from backend.__main__ import main

    main(sys.argv[1:])
