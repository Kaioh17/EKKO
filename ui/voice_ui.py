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

import queue
import random
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from enum import Enum


class VoiceUIState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ERROR = "error"


_STATE_STYLE = {
    VoiceUIState.LISTENING: ("● LISTENING", "#3fb950"),
    VoiceUIState.PROCESSING: ("◐ PROCESSING", "#d29922"),
    VoiceUIState.SPEAKING: ("▶ SPEAKING", "#58a6ff"),
    VoiceUIState.ERROR: ("✕ ERROR", "#f85149"),
    VoiceUIState.IDLE: ("IDLE", "#6e7681"),
}

BAR_COUNT = 24
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
RESPONSE_PANEL_HEIGHT = 130
RESPONSE_PANEL_MAX_HEIGHT = 420
_RESPONSE_TEXT_TOP_PAD = 14   # response_text's y offset from self._height
_RESPONSE_BOTTOM_PAD = 34    # room left below the wrapped text for the countdown line
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
        root = tk.Tk()
        root.overrideredirect(True)          # no titlebar/border, popup feel
        root.attributes("-topmost", True)
        root.configure(bg="#0d1117")
        try:
            root.attributes("-alpha", 0.94)  # slight transparency, cheap
        except tk.TclError:
            pass

        total_height = self._height  # grows to self._height + RESPONSE_PANEL_HEIGHT while a response is shown

        def geometry_for(height):
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            x = sw - self._width - self._margin
            y = sh - height - self._margin - 48  # clear of the taskbar
            return x, y

        x, y = geometry_for(total_height)
        root.geometry(f"{self._width}x{total_height}+{x}+{y}")

        canvas = tk.Canvas(
            root, width=self._width, height=total_height,
            bg="#0d1117", highlightthickness=1, highlightbackground="#30363d",
        )
        canvas.pack(fill="both", expand=True)

        status_text = canvas.create_text(
            14, 16, anchor="w", fill="#6e7681",
            font=("Cascadia Code", 11, "bold"), text="IDLE",
        )
        transcript_text = canvas.create_text(
            14, self._height - 16, anchor="w", fill="#8b949e",
            font=("Consolas", 9), text="", width=self._width - 28,
        )

        # Response panel: a separator, wrapped response text, and a
        # countdown, all below the normal-height content and hidden
        # (state="hidden") until show_response() is called. Fixed
        # coordinates relative to self._height rather than the current
        # total_height, so they never need recomputing when the window
        # resizes -- only the window/canvas height itself changes.
        separator = canvas.create_line(
            10, self._height + 4, self._width - 10, self._height + 4,
            fill="#30363d", state="hidden",
        )
        response_text = canvas.create_text(
            14, self._height + 14, anchor="nw", fill="#c9d1d9",
            font=("Consolas", 10), text="", width=self._width - 28,
            state="hidden",
        )
        countdown_text = canvas.create_text(
            self._width - 14, self._height + RESPONSE_PANEL_HEIGHT - 14, anchor="se",
            fill="#6e7681", font=("Consolas", 8), text="", state="hidden",
        )

        bar_w = (self._width - 28) / BAR_COUNT
        bars = []
        base_y = self._height / 2 + 6
        for i in range(BAR_COUNT):
            bx = 14 + i * bar_w
            bar = canvas.create_rectangle(
                bx, base_y, bx + bar_w - 2, base_y,
                fill="#30363d", outline="",
            )
            bars.append(bar)

        state = {"current": VoiceUIState.IDLE, "level": 0.0, "visible": False,
                  "idle_since": None, "phase": 0.0, "response_deadline": None}

        def apply_state(s: VoiceUIState):
            label, color = _STATE_STYLE[s]
            canvas.itemconfigure(status_text, text=label, fill=color)
            state["current"] = s
            if s == VoiceUIState.IDLE:
                state["idle_since"] = time.monotonic()
            else:
                state["idle_since"] = None

        def draw_bars():
            s = state["current"]
            phase = state["phase"]
            for i, bar in enumerate(bars):
                if s == VoiceUIState.LISTENING:
                    jitter = random.random() * state["level"]
                    h = 4 + jitter * 34
                    color = "#3fb950"
                elif s == VoiceUIState.PROCESSING:
                    import math
                    h = 4 + (math.sin(phase * 4 + i * 0.5) + 1) * 10
                    color = "#d29922"
                elif s == VoiceUIState.SPEAKING:
                    import math
                    h = 4 + abs(math.sin(phase * 6 + i * 0.35)) * 28
                    color = "#58a6ff"
                else:
                    h = 3
                    color = "#21262d"
                bx = 14 + i * bar_w
                canvas.coords(bar, bx, base_y - h, bx + bar_w - 2, base_y + 6)
                canvas.itemconfigure(bar, fill=color)
            state["phase"] += 0.12

        def do_show():
            if not state["visible"]:
                root.deiconify()
                state["visible"] = True

        def do_hide():
            if state["visible"]:
                root.withdraw()
                state["visible"] = False

        def set_panel_visible(visible, panel_height=RESPONSE_PANEL_HEIGHT):
            nonlocal total_height
            height = self._height + panel_height if visible else self._height
            item_state = "normal" if visible else "hidden"
            canvas.itemconfigure(separator, state=item_state)
            canvas.itemconfigure(response_text, state=item_state)
            canvas.itemconfigure(countdown_text, state=item_state)
            if visible:
                # countdown_text sits at the bottom-right of the panel, which
                # moves when the panel grows to fit a longer response -- see
                # show_response() below for how panel_height is measured.
                canvas.coords(countdown_text, self._width - 14, height - 14)
            if height == total_height:
                return
            total_height = height
            nx, ny = geometry_for(total_height)
            root.geometry(f"{self._width}x{total_height}+{nx}+{ny}")
            canvas.config(height=total_height)

        def show_response(text, timeout_s):
            canvas.itemconfigure(response_text, text=text)
            # Measure the actual wrapped height of this text (it's already
            # word-wrapped to width=self._width - 28 above) and grow the
            # panel to fit it instead of clipping at a fixed height -- see
            # RESPONSE_PANEL_HEIGHT's comment. update_idletasks() forces
            # Tkinter to lay the item out now so bbox() below reflects the
            # text just set, not whatever was there before.
            canvas.update_idletasks()
            bbox = canvas.bbox(response_text)
            text_height = (bbox[3] - bbox[1]) if bbox else 0
            needed = _RESPONSE_TEXT_TOP_PAD + text_height + _RESPONSE_BOTTOM_PAD
            panel_height = max(RESPONSE_PANEL_HEIGHT, min(needed, RESPONSE_PANEL_MAX_HEIGHT))
            state["response_deadline"] = time.monotonic() + timeout_s
            set_panel_visible(True, panel_height)
            do_show()

        def clear_response():
            state["response_deadline"] = None
            set_panel_visible(False)

        root.withdraw()  # start hidden

        def poll():
            drained = False
            try:
                while True:
                    msg = self._q.get_nowait()
                    drained = True
                    if msg.kind == "stop":
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
                draw_bars()
                root.after(ACTIVE_POLL_MS, poll)
            else:
                root.after(IDLE_POLL_MS, poll)

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
