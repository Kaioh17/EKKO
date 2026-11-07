"""Pre-roll for speech segments. Silero only fires `start` once a block is
already clearly speech, so a segment built from that block on loses the
first syllable ("pen Chrome"). PreRoll keeps the last few blocks heard
before speech and hands them over when the segment starts."""

from collections import deque

BLOCK_MS = 32  # one 512-sample block at 16 kHz, see vad_listener.CHUNK_SAMPLES
BLOCK_SECONDS = BLOCK_MS / 1000


class PreRoll:
    def __init__(self, ms: int):
        self.lead_blocks = max(0, ms) // BLOCK_MS
        # +1: the block VAD fires on is added before take() is called.
        self._blocks: deque = deque(maxlen=self.lead_blocks + 1)
        self._epoch = None

    def add(self, block, epoch=None) -> None:
        """`epoch` changes whenever the mic gate closed (EKKO spoke): what
        came before isn't continuous with what follows, and may be the wake
        phrase, so it's dropped."""
        if epoch != self._epoch:
            self._blocks.clear()
            self._epoch = epoch
        self._blocks.append(block)

    def take(self) -> tuple[list, int]:
        """(blocks for the new segment, how many of them are lead), and start over."""
        taken = list(self._blocks)
        self._blocks.clear()
        return taken, max(0, len(taken) - 1)

    def clear(self) -> None:
        self._blocks.clear()


def segment_seconds(blocks: int, lead_blocks: int) -> float:
    """Length of a segment without its pre-roll: duration gates (a click is
    shorter than a command) must judge what was spoken, not the lead."""
    return (blocks - lead_blocks) * BLOCK_SECONDS


class HoldToTalk:
    """Press-and-hold as a state machine, fed whether the key is down on every
    audio block. Reports edges, so a key held through a long request is one
    press, and a stuck key can't record forever."""

    def __init__(self, max_seconds: float = 30.0):
        self.max_seconds = max_seconds
        self.active = False
        self.voiced = False  # did VAD hear speech during this press
        self._started = 0.0
        self._wait_for_up = False  # released by the cap while still down

    def update(self, held: bool, now: float) -> str | None:
        """"press", "release" or None."""
        if self._wait_for_up:
            self._wait_for_up = held
            return None
        if not self.active:
            if held:
                self.active, self.voiced, self._started = True, False, now
                return "press"
            return None
        if not held:
            self.active = False
            return "release"
        if now - self._started > self.max_seconds:
            self.active = False
            self._wait_for_up = True
            return "release"
        return None

    def heard_speech(self) -> None:
        self.voiced = True

    def cancel(self) -> None:
        """Abandon the hold (hard stop). If the key is still down it is not a new press."""
        self._wait_for_up = self.active or self._wait_for_up
        self.active = False
