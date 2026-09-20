"""
ui/voice_ui.py

Terminal-styled voice UI popup for EKKO.

Design goal: lowest possible CPU/memory footprint. Rather than spawning a new
console process every time the wake word fires (process creation overhead,
new interpreter, new conhost each time), this runs a single Tkinter overlay
in a background thread inside the SAME process as listener/vad_listener.py.
The window sits hidden and idle (near-zero CPU) until you call show() (or
set_state() with anything other than IDLE, which shows it implicitly), then
animates only while visible. Cross-thread updates go through a
queue.Queue, which is cheap (no serialization, no IPC) -- vad_listener.py's
audio callback and main loop both call into this from threads other than
Tkinter's own, so nothing here may touch the widgets directly.

Deliberately free of any dependency on the rest of the repo (same posture
as feedback/, see that package's README): this only knows how to draw
states and levels it's told about, never why the pipeline is in them. That
includes show_response() below -- it has no idea what an "ambiguous
request" or a "session" is, it just displays text for as long as it's told
to and reports nothing back. Whether a session is active, and what counts
as one, is entirely listener/vad_listener.py's call (see _handle_command's
session_until there); this module never branches the pipeline's behavior.

Integration is a handful of calls from vad_listener.py's listen():
    ui.set_state(VoiceUIState.LISTENING)   # active listening armed
    ui.set_audio_level(rms)                # from the mic callback, 0.0-1.0
    ui.set_transcript(partial_text)        # once Whisper has a transcript
    ui.show_response(text, timeout_s=20)   # a spoken response, also shown
    ui.clear_response()                    # end the response panel early

See the __main__ block at the bottom for a standalone preview:
    python -m ui.voice_ui
"""

import math
import queue
import random
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from enum import Enum

from ui import theme

PAD = 20  # px of space between content and the window edge
TRANSPARENT = "#010101"  # keyed out via -transparentcolor so only the rounded shape shows (Windows)
RADIUS = 16


def round_rect(canvas, w, h, r=RADIUS, **kw):
    """Rounded rectangle as a smoothed polygon inset 1px so the outline isn't clipped."""
    x0, y0, x1, y1 = 1, 1, w - 1, h - 1
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return canvas.create_polygon(pts, smooth=True, **kw)

class VoiceUIState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ERROR = "error"


_STATE_STYLE = {
    VoiceUIState.LISTENING: ("LISTENING", theme.GREEN),
    VoiceUIState.PROCESSING: ("PROCESSING", theme.AMBER),
    VoiceUIState.SPEAKING: ("SPEAKING", theme.GREEN),
    VoiceUIState.ERROR: ("ERROR", theme.RED),
    VoiceUIState.IDLE: ("IDLE", theme.GREEN),
}

IDLE_POLL_MS = 120       # queue check rate while hidden, cheap
ACTIVE_POLL_MS = 33      # ~30fps while visible, smooth without being wasteful
AUTO_HIDE_AFTER_S = 1.5  # hide this long after returning to IDLE, unless a
                         # response panel is holding the window open (see below)

# How much taller the popup grows to fit a response panel, and how long that
# panel stays up by default before it closes itself. This is a guessed
# starting point, not a measured one -- same caveat this project already
# attaches to every other uncalibrated default (see routing/matcher.py's
# threshold, llm_fallback/claude_code/fallback.py's DEFAULT_TIMEOUT). There's
# nothing to calibrate this against the way routing/calibrate.py calibrates a
# similarity threshold; it's "how long does a sentence or two take to read,"
# adjust by feel if it's cutting responses off or lingering too long.
#
# This is a floor, not a fixed size: show_response() below measures the
# actual wrapped text and grows the panel past this when a response is
# longer than a sentence or two, up to RESPONSE_PANEL_MAX_HEIGHT, rather
# than letting the canvas clip it. Below that cap, more of a long
# response is visible at once; past it, the extra spills off the bottom
# of the screen instead, which is still a smaller cutoff than before.
RESPONSE_PANEL_MAX_HEIGHT = 420
SESSION_DEFAULT_TIMEOUT_S = 20.0


@dataclass
class _Msg:
    kind: str   # "state" | "level" | "transcript" | "response" | "clear_response" | "show" | "hide" | "stop"
    value: object = None


class VoiceUI:
    def __init__(self, width=340, height=110, margin=24):
        self._width = width
        self._height = height  # the normal (no response panel) height
        self._margin = margin
        self._q: "queue.Queue[_Msg]" = queue.Queue()
        self._thread = None
        self._running = False

    # ---- public API, safe to call from any thread ----

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._q.put(_Msg("stop"))

    def set_state(self, state: VoiceUIState):
        self._q.put(_Msg("state", state))

    def set_audio_level(self, level: float):
        # clamp, cheap, called often so keep this trivial
        self._q.put(_Msg("level", max(0.0, min(1.0, level))))

    def set_transcript(self, text: str):
        self._q.put(_Msg("transcript", text))

    def show_response(self, text: str, timeout_s: float = SESSION_DEFAULT_TIMEOUT_S):
        """Shows `text` in a panel below the normal popup content, and grows
        the window to fit it. Stays up for `timeout_s` seconds (from this
        call, not from whenever the panel first opened) and then closes
        itself -- calling this again before that resets the clock and
        swaps in the new text, which is how a caller keeps a multi-turn
        session's responses landing in the same panel instead of opening a
        fresh one each time. Purely visual: this module has no concept of
        a "session" beyond "how long until this text disappears," the
        caller decides when to call this again.
        """
        self._q.put(_Msg("response", (text, timeout_s)))

    def clear_response(self):
        """Closes the response panel immediately, before its timeout. Not
        currently called by vad_listener.py (a session is left to expire on
        its own), but exposed for a caller that wants to end one early, e.g.
        on an explicit decline.
        """
        self._q.put(_Msg("clear_response"))

    def show(self):
        self._q.put(_Msg("show"))

    def hide(self):
        self._q.put(_Msg("hide"))

    # ---- internal, runs on the UI thread only ----

    def _run(self):
        theme.prepare()  # Windows DPI awareness, must precede Tk()
        root = tk.Tk()
        F = theme.init(root)
        scale = F["scale"]

        def px(n):
            return int(round(n * scale))

        root.overrideredirect(True)          # no titlebar/border, popup feel
        root.attributes("-topmost", True)
        root.configure(bg=theme.BG)
        rounded = False
        if sys.platform == "win32":
            # Key out a color so the drawn rounded shape is the window outline.
            # Windows only; elsewhere the fallback is a plain bordered rectangle.
            try:
                root.configure(bg=TRANSPARENT)
                root.attributes("-transparentcolor", TRANSPARENT)
                rounded = True
            except tk.TclError:
                root.configure(bg=theme.BG)
        try:
            root.attributes("-alpha", 0.96)  # slight transparency, cheap
        except tk.TclError:
            pass

        width = px(self._width)
        pad = px(PAD)
        inner_w = width - 2 * pad
        font_status = tkfont.Font(root, family=F["mono"], size=9)
        font_transcript = tkfont.Font(root, family=F["sans"], size=10)
        font_response = tkfont.Font(root, family=F["sans"], size=12)
        font_countdown = tkfont.Font(root, family=F["sans"], size=8)

        def geometry_for(height):
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            x = sw - width - px(self._margin)
            y = sh - height - px(self._margin) - px(48)  # clear of the taskbar
            return x, y

        total_height = px(self._height)
        x, y = geometry_for(total_height)
        root.geometry(f"{width}x{total_height}+{x}+{y}")

        canvas = tk.Canvas(
            root, width=width, height=total_height,
            bg=TRANSPARENT if rounded else theme.BG,
            highlightthickness=0 if rounded else 1, highlightbackground=theme.BORDER,
        )
        canvas.pack(fill="both", expand=True)

        def draw_bg(h):
            if rounded:
                return round_rect(canvas, width, h, px(RADIUS), fill=theme.BG, outline=theme.BORDER)
            return canvas.create_rectangle(0, 0, 0, 0)  # placeholder so bg_shape is always an item

        bg_shape = draw_bg(total_height)

        # ---- fixed top block: dot + label, bars, then transcript ----
        header_h = px(16)
        label_y = pad + header_h / 2
        dot_r = px(4)
        dot_cx = pad + dot_r
        dot = canvas.create_oval(0, 0, 0, 0, fill=theme.MUTED, outline="")
        status_text = canvas.create_text(
            pad + 2 * dot_r + px(8), label_y, anchor="w", fill=theme.MUTED,
            font=font_status, text="IDLE",
        )

        bar_w, bar_gap = px(3), px(3)
        pitch = bar_w + bar_gap
        bar_count = max(1, (inner_w + bar_gap) // pitch)
        bars_x0 = (width - (bar_count * pitch - bar_gap)) / 2 + bar_w / 2
        bar_max = px(16)  # max half-height, bars grow up and down from center
        center_y = pad + header_h + px(12) + bar_max
        bars = [
            canvas.create_line(0, 0, 0, 0, width=bar_w, capstyle="round", fill=theme.BORDER)
            for _ in range(bar_count)
        ]
        bar_h = [1.0] * bar_count  # current half-heights, eased toward targets

        transcript_top = center_y + bar_max + px(16)
        transcript_text = canvas.create_text(
            pad, transcript_top, anchor="nw", fill=theme.MUTED,
            font=font_transcript, text="", width=inner_w, justify="left",
        )

        # Response panel: divider, wrapped response text and a countdown, all
        # hidden until show_response(). layout() places them below whatever
        # height the transcript currently has and sizes the window to fit.
        separator = canvas.create_line(
            pad, 0, width - pad, 0, fill=theme.BORDER, width=1, state="hidden",
        )
        response_text = canvas.create_text(
            pad, 0, anchor="nw", fill=theme.TEXT, font=font_response,
            text="", width=inner_w, justify="left", state="hidden",
        )
        countdown_text = canvas.create_text(
            width - pad, 0, anchor="ne", fill=theme.MUTED, font=font_countdown,
            text="", state="hidden",
        )

        state = {"current": VoiceUIState.IDLE, "level": 0.0, "visible": False,
                 "idle_since": None, "phase": 0.0, "response_deadline": None,
                 "panel": False, "after": None}

        def text_h(item, font):
            canvas.update_idletasks()
            bbox = canvas.bbox(item)
            return max(bbox[3] - bbox[1] if bbox else 0, font.metrics("linespace"))

        def layout():
            """Stack the content top to bottom and resize the window to it."""
            nonlocal total_height, bg_shape
            bottom = transcript_top + text_h(transcript_text, font_transcript)
            if state["panel"]:
                sep_y = bottom + px(12)
                resp_top = sep_y + px(12)
                canvas.coords(separator, pad, sep_y, width - pad, sep_y)
                canvas.coords(response_text, pad, resp_top)
                bottom = resp_top + text_h(response_text, font_response) + px(8)
                canvas.coords(countdown_text, width - pad, bottom)
                bottom += font_countdown.metrics("linespace")
            height = min(max(int(bottom + pad), px(self._height)), px(RESPONSE_PANEL_MAX_HEIGHT))
            if height == total_height:
                return
            total_height = height
            nx, ny = geometry_for(height)
            root.geometry(f"{width}x{height}+{nx}+{ny}")
            canvas.config(height=height)
            canvas.delete(bg_shape)
            bg_shape = draw_bg(height)
            canvas.tag_lower(bg_shape)

        def apply_state(s: VoiceUIState):
            label, color = _STATE_STYLE[s]
            canvas.itemconfigure(status_text, text=label, fill=color)
            canvas.itemconfigure(dot, fill=color)
            state["current"] = s
            if s == VoiceUIState.IDLE:
                state["idle_since"] = time.monotonic()
            else:
                state["idle_since"] = None

        def draw_frame():
            s = state["current"]
            phase = state["phase"]
            color = _STATE_STYLE[s][1] if s != VoiceUIState.IDLE else theme.BORDER
            for i, bar in enumerate(bars):
                if s == VoiceUIState.LISTENING:
                    target = 1.5 + random.random() * state["level"] * bar_max
                elif s == VoiceUIState.PROCESSING:
                    target = 1.5 + (math.sin(phase * 4 + i * 0.5) + 1) * 0.3 * bar_max
                elif s == VoiceUIState.SPEAKING:
                    target = 1.5 + abs(math.sin(phase * 6 + i * 0.35)) * 0.8 * bar_max
                else:
                    target = 1.5
                bar_h[i] += (target - bar_h[i]) * 0.25  # ease, no jitter
                bx = bars_x0 + i * pitch
                canvas.coords(bar, bx, center_y - bar_h[i], bx, center_y + bar_h[i])
                canvas.itemconfigure(bar, fill=color)
            # pulse the status dot while processing, fixed size otherwise
            r = dot_r * (1 + 0.3 * math.sin(phase * 5)) if s == VoiceUIState.PROCESSING else dot_r
            canvas.coords(dot, dot_cx - r, label_y - r, dot_cx + r, label_y + r)
            state["phase"] += 0.12

        def do_show():
            if not state["visible"]:
                root.deiconify()
                state["visible"] = True

        def do_hide():
            if state["visible"]:
                root.withdraw()
                state["visible"] = False

        def set_panel_visible(visible):
            state["panel"] = visible
            item_state = "normal" if visible else "hidden"
            for item in (separator, response_text, countdown_text):
                canvas.itemconfigure(item, state=item_state)
            layout()

        def show_response(text, timeout_s):
            canvas.itemconfigure(response_text, text=text)
            state["response_deadline"] = time.monotonic() + timeout_s
            set_panel_visible(True)
            do_show()

        def clear_response():
            state["response_deadline"] = None
            set_panel_visible(False)

        root.withdraw()  # start hidden
        apply_state(VoiceUIState.IDLE)
        draw_frame()
        layout()

        def poll():
            try:
                while True:
                    msg = self._q.get_nowait()
                    if msg.kind == "stop":
                        if state["after"]:
                            root.after_cancel(state["after"])
                        root.destroy()
                        return
                    elif msg.kind == "state":
                        apply_state(msg.value)
                        if msg.value != VoiceUIState.IDLE:
                            do_show()
                    elif msg.kind == "level":
                        state["level"] = msg.value
                    elif msg.kind == "transcript":
                        canvas.itemconfigure(transcript_text, text=msg.value or "")
                        layout()
                    elif msg.kind == "response":
                        show_response(*msg.value)
                    elif msg.kind == "clear_response":
                        clear_response()
                    elif msg.kind == "show":
                        do_show()
                    elif msg.kind == "hide":
                        do_hide()
            except queue.Empty:
                pass

            deadline = state["response_deadline"]
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    clear_response()
                else:
                    canvas.itemconfigure(countdown_text, text=f"closes in {int(remaining) + 1}s")

            # auto-hide a beat after going idle, so the popup doesn't linger
            # -- but not while a response panel is up for a reason the caller
            # gave a real timeout for, that timeout (not this one) decides
            # when the window goes away.
            if (state["idle_since"] is not None and state["visible"]
                    and state["response_deadline"] is None
                    and time.monotonic() - state["idle_since"] > AUTO_HIDE_AFTER_S):
                do_hide()

            if state["visible"]:
                draw_frame()
                state["after"] = root.after(ACTIVE_POLL_MS, poll)
            else:
                state["after"] = root.after(IDLE_POLL_MS, poll)

        poll()
        root.mainloop()


# ---------------------------------------------------------------------------
# Standalone preview. Run this module directly to see the popup cycle through
# states with simulated audio levels, no EKKO pipeline required.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    ui = VoiceUI()
    ui.start()
    time.sleep(0.3)

    def demo():
        ui.set_state(VoiceUIState.LISTENING)
        for _ in range(40):
            ui.set_audio_level(random.random())
            time.sleep(0.05)

        ui.set_transcript("what's on my calendar today")
        ui.set_state(VoiceUIState.PROCESSING)
        time.sleep(1.5)

        ui.set_state(VoiceUIState.SPEAKING)
        ui.show_response(
            "The capital of France is Paris. Anything else you want to know?",
            timeout_s=8,
        )
        time.sleep(1.5)

        ui.set_state(VoiceUIState.IDLE)
        time.sleep(8)
        ui.stop()

    threading.Thread(target=demo, daemon=True).start()

    # keep main thread alive until the UI thread exits
    while ui._thread.is_alive():
        time.sleep(0.2)
