"""
system/restart_ui.py

Standalone popup shown while system/restart.ps1 cycles the listener.
Deliberately a separate process from ui/voice_ui.py: that overlay lives
inside vad_listener.py's own process and dies the moment restart.ps1
stops the listener, which is exactly the window this needs to cover, so
it can't be the thing displaying it. This has no dependency on the rest
of the repo (same posture ui/voice_ui.py documents for itself) -- it
just draws a message and animates for as long as it's left running.

Lifecycle is owned entirely by the caller: restart.ps1 launches this
with pythonw.exe (no console) as a background process, does the actual
stop/start work, then kills this process (Stop-Process) once the
listener is confirmed back up. The SELF_CLOSE_AFTER_S failsafe below is
just insurance in case that kill is ever missed -- normal runs never
reach it.

Usage: pythonw.exe restart_ui.py [subtitle text]
"""

import sys
import time
import tkinter as tk

SELF_CLOSE_AFTER_S = 120  # failsafe only; restart.ps1 normally kills this well before

WIDTH, HEIGHT = 360, 130

TRANSPARENT = "#010101"  # keyed out via -transparentcolor so only the rounded shape shows (Windows)
RADIUS = 16


def round_rect(canvas, w, h, r=RADIUS, **kw):
    """Rounded rectangle as a smoothed polygon inset 1px so the outline isn't clipped."""
    x0, y0, x1, y1 = 1, 1, w - 1, h - 1
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, smooth=True, **kw)


subtitle = sys.argv[1] if len(sys.argv) > 1 else "sequential restart"


def main():
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=TRANSPARENT)
    try:
        root.attributes("-transparentcolor", TRANSPARENT)
        root.attributes("-alpha", 0.96)
    except tk.TclError:
        pass

    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    x, y = (sw - WIDTH) // 2, (sh - HEIGHT) // 2
    root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

    canvas = tk.Canvas(
        root, width=WIDTH, height=HEIGHT,
        bg=TRANSPARENT, highlightthickness=0,
    )
    canvas.pack(fill="both", expand=True)
    round_rect(canvas, WIDTH, HEIGHT, fill="#0d1117", outline="#30363d")

    canvas.create_text(
        WIDTH / 2, 38, fill="#58a6ff",
        font=("Cascadia Code", 15, "bold"), text="EKKO",
    )
    subtitle_item = canvas.create_text(
        WIDTH / 2, 68, fill="#d29922",
        font=("Consolas", 11), text=subtitle,
    )
    elapsed_item = canvas.create_text(
        WIDTH / 2, HEIGHT - 20, fill="#6e7681",
        font=("Consolas", 9), text="",
    )

    dot_positions = [(WIDTH / 2 - 24, 96), (WIDTH / 2, 96), (WIDTH / 2 + 24, 96)]
    dots = [
        canvas.create_oval(dx - 4, dy - 4, dx + 4, dy + 4, fill="#21262d", outline="")
        for dx, dy in dot_positions
    ]

    started = time.monotonic()

    def tick():
        now = time.monotonic()
        elapsed = now - started

        # pulse the three dots left-to-right, one full cycle every 0.9s
        phase = (elapsed % 0.9) / 0.9
        for i, dot in enumerate(dots):
            lit = phase > i / 3 and phase < (i + 1) / 3 + 0.15
            canvas.itemconfigure(dot, fill="#3fb950" if lit else "#21262d")

        canvas.itemconfigure(elapsed_item, text=f"{elapsed:0.0f}s")

        if elapsed >= SELF_CLOSE_AFTER_S:
            root.destroy()
            return

        root.after(60, tick)

    tick()
    root.mainloop()


if __name__ == "__main__":
    main()
