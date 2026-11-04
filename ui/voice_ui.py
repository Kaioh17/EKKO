"""
ui/voice_ui.py

Voice bar popup (design: ekko Bubble v4) for EKKO.

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
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from enum import Enum

from ui import theme

BAR_H = 40  # voice bar height, design v4 (ekko Bubble v4)
PAPER = "#F6F4EE"  # text card fill
MAX_SLOTS = 14     # wave slots: bars plus the trailing dots
WAVE_H = [8, 14, 10, 18, 12, 20, 9, 16, 12]  # live bar heights, px


def _blend(fg, bg, a):
    """fg over bg at opacity a (Tk has no alpha colors)."""
    f, g = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in (fg, bg))
    return "#%02x%02x%02x" % tuple(round(x * a + y * (1 - a)) for x, y in zip(f, g))


class VoiceUIState(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ERROR = "error"


# (label, bar fill, text/bars, meta) per state: cyan / yellow / slate / orange from the v4 design
_STATE_STYLE = {
    VoiceUIState.LISTENING: ("Listening", "#C9FAFF", "#0B2F33", "#0E7C86"),
    VoiceUIState.PROCESSING: ("Thinking", "#FFFBA8", "#3A3500", "#7A7000"),
    VoiceUIState.SPEAKING: ("Speaking", "#3A3D45", "#FFFFFF", "#FFFFFF"),
    VoiceUIState.ERROR: ("Couldn\u2019t do it", "#FFB86B", "#3D1D00", "#8A2F00"),
    VoiceUIState.IDLE: ("Ask ekko", "#FFFFFF", "#111111", "#6B6B6B"),
}

IDLE_POLL_MS = 120       # queue check rate while hidden, cheap
ACTIVE_POLL_MS = 33      # ~30fps while visible, smooth without being wasteful
AUTO_HIDE_AFTER_S = 1.5  # hide this long after returning to IDLE, unless a
                         # response panel is holding the window open (see below)

# How much taller the popup grows to fit a response panel, and how long that
# panel stays up by default before it closes itself. This is a guessed
# starting point, not a measured one -- same caveat this project already
# attaches to every other uncalibrated default (see routing/matcher.py's
# threshold, each provider's DEFAULT_TIMEOUT). There's
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
        root.configure(bg=PAPER)

        width = px(self._width)
        bar_h = px(BAR_H)
        font_label = tkfont.Font(root, family=F["sans"], size=11)
        font_label_bold = tkfont.Font(root, family=F["sans"], size=11, weight="bold")
        font_meta = tkfont.Font(root, family=F["mono"], size=9)
        font_text = tkfont.Font(root, family=F["sans"], size=10)

        def geometry_for(height):
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            x = sw - width - px(self._margin)
            y = sh - height - px(self._margin) - px(48)  # clear of the taskbar
            return x, y

        total_height = bar_h
        x, y = geometry_for(total_height)
        root.geometry(f"{width}x{total_height}+{x}+{y}")

        canvas = tk.Canvas(root, width=width, height=total_height, bg=PAPER, highlightthickness=0)
        canvas.pack(fill="both", expand=True)

        # ---- v4 layout: state-colored voice bar on top, text card below ----
        card = canvas.create_rectangle(0, bar_h, width, bar_h, fill=PAPER, outline="#D6D6D6", state="hidden")
        card_text = canvas.create_text(
            px(14), bar_h + px(10), anchor="nw", fill="#111111", font=font_text,
            text="", width=width - px(28), justify="left", state="hidden",
        )
        bar = canvas.create_rectangle(0, 0, width, bar_h, outline="")
        label = canvas.create_text(px(16), bar_h / 2, anchor="w", text="")
        meta = canvas.create_text(width - px(16), bar_h / 2, anchor="e", font=font_meta, text="")

        bar_w, pitch = px(3), px(9)  # 3px bar + 6px gap, as in the design
        slots = [canvas.create_line(0, 0, 0, 0, width=bar_w, capstyle="butt") for _ in range(MAX_SLOTS)]
        slot_h = [1.5] * MAX_SLOTS  # current half-heights, eased toward targets
        cy = bar_h / 2

        state = {"current": VoiceUIState.IDLE, "level": 0.0, "visible": False,
                 "idle_since": None, "phase": 0.0, "response_deadline": None,
                 "since": time.monotonic(), "wave_x": 0, "transcript": "", "response": "",
                 "after": None}

        def layout():
            """Show the transcript (or the response, which wins) in the card and resize to fit."""
            nonlocal total_height
            text = state["response"] or state["transcript"]
            canvas.itemconfigure(card_text, text=text)
            if text:
                canvas.itemconfigure(card_text, state="normal")  # bbox is empty while hidden
                canvas.update_idletasks()
                bb = canvas.bbox(card_text)
                height = bar_h + (bb[3] - bb[1]) + px(20)
                vis = "normal"
            else:
                height, vis = bar_h, "hidden"
            height = min(height, px(RESPONSE_PANEL_MAX_HEIGHT))
            canvas.itemconfigure(card, state=vis)
            canvas.itemconfigure(card_text, state=vis)
            if height == total_height:
                return
            total_height = height
            nx, ny = geometry_for(height)
            root.geometry(f"{width}x{height}+{nx}+{ny}")
            canvas.config(height=height)
            canvas.coords(card, 0, bar_h, width, height)

        def apply_state(s: VoiceUIState):
            text, bg, fg, _ = _STATE_STYLE[s]
            canvas.itemconfigure(bar, fill=bg)
            canvas.itemconfigure(label, text=text, fill=fg, font=font_label_bold if s == VoiceUIState.SPEAKING else font_label)
            canvas.update_idletasks()
            lb = canvas.bbox(label)
            state["wave_x"] = lb[2] + px(14)
            state["current"] = s
            state["since"] = time.monotonic()
            state["idle_since"] = time.monotonic() if s == VoiceUIState.IDLE else None

        def draw_frame():
            s = state["current"]
            _, bg, fg, meta_fg = _STATE_STYLE[s]
            phase = state["phase"]
            # (half-height, opacity, live?) per slot; dots are 3px squares, opacity blends toward the bar fill
            if s == VoiceUIState.LISTENING:
                pat = [(1.5, .8, 0)] + [(h / 2, 1, 1) for h in WAVE_H] + [(1.5, .6, 0), (1.5, .4, 0)]
            elif s == VoiceUIState.PROCESSING:
                pat = [(7, .3 + .7 * (math.sin(phase * 5 - i * .75) + 1) / 2, 0) for i in range(8)] + [(1.5, .4, 0), (1.5, .3, 0)]
            elif s == VoiceUIState.SPEAKING:
                pat = [(1.5, .9, 0)] * 2 + [(h / 2, 1, 1) for h in WAVE_H] + [(1.5, .8, 0), (1.5, .6, 0), (1.5, .4, 0)]
            elif s == VoiceUIState.ERROR:
                pat = [(5, 1, 0), (7, 1, 0), (4, 1, 0), (1.5, .9, 0), (1.5, .7, 0), (1.5, .5, 0), (1.5, .3, 0)]
            else:
                pat = []
            for i, item in enumerate(slots):
                if i >= len(pat):
                    canvas.itemconfigure(item, state="hidden")
                    continue
                half, alpha, live = pat[i]
                if live:
                    if s == VoiceUIState.LISTENING:
                        target = 1.5 + random.random() * state["level"] * px(half) * 1.6
                    else:
                        target = 1.5 + abs(math.sin(phase * 6 + i * 0.35)) * px(half)
                    slot_h[i] += (target - slot_h[i]) * 0.25  # ease, no jitter
                    half_px = slot_h[i]
                else:
                    half_px = px(half)
                bx = state["wave_x"] + i * pitch + bar_w / 2
                canvas.coords(item, bx, cy - half_px, bx, cy + half_px)
                canvas.itemconfigure(item, fill=_blend(fg, bg, alpha), state="normal")
            # meta: seconds left while a response is up, elapsed while listening
            m = ""
            deadline = state["response_deadline"]
            if deadline is not None:
                m = f"{int(max(0, deadline - time.monotonic())) + 1}s"
            elif s == VoiceUIState.LISTENING:
                e = int(time.monotonic() - state["since"])
                m = f"{e // 60}:{e % 60:02d}"
            canvas.itemconfigure(meta, text=m, fill=meta_fg)
            state["phase"] += 0.12

        def do_show():
            if not state["visible"]:
                root.deiconify()
                state["visible"] = True

        def do_hide():
            if state["visible"]:
                root.withdraw()
                state["visible"] = False

        def show_response(text, timeout_s):
            state["response"] = text
            state["response_deadline"] = time.monotonic() + timeout_s
            layout()
            do_show()

        def clear_response():
            state["response"] = ""
            state["response_deadline"] = None
            layout()

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
                        state["transcript"] = msg.value or ""
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
