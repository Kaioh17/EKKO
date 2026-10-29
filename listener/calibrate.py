"""Finds the CPU thread count Whisper transcribes fastest with on this
machine. More threads is not always faster (hybrid P/E-core laptops,
hyperthreads), so it is measured instead of guessed: the same clip is
transcribed once per candidate count and the timings compared.

The app's Voice page runs this through POST /api/stt/calibrate; the result
goes into the "CPU threads" setting.

Usage:
    python -m listener.calibrate [model size]
"""

import glob
import os
import sys
import time
import wave

import numpy as np

from listener.defaults import (
    AUTO_CPU_MODEL,
    DEFAULT_INITIAL_PROMPT,
    DEFAULT_SAVE_DIR,
    WHISPER_BEAM_SIZE,
)

SAMPLE_RATE = 16000


def candidates(cores: int) -> list[int]:
    return sorted({n for n in (2, 4, 6, 8, 12, 16, cores // 2, cores) if 1 <= n <= cores})


def pick(timings: dict[int, float]) -> int:
    """Fewest threads within 5% of the fastest: the rest of the pipeline
    (VAD, wake word, TTS) needs cores too."""
    best = min(timings.values())
    return min(n for n, t in timings.items() if t <= best * 1.05)


def _clip(save_dir: str = DEFAULT_SAVE_DIR):
    """The newest saved capture of command length, so the decoder does
    real work."""
    for path in sorted(glob.glob(os.path.join(save_dir, "speech_*.wav")), reverse=True):
        with wave.open(path) as w:
            if w.getnframes() / w.getframerate() >= 1.5:
                return path
    # ponytail: no capture yet (fresh install, or saving clips is off), so
    # time 3s of noise. Whisper's encoder always runs a full 30s window and
    # dominates on CPU, so the ranking still holds; record a real sample
    # here if that stops being true.
    return np.random.default_rng(0).normal(0, 0.01, SAMPLE_RATE * 3).astype(np.float32)


def calibrate(model_size: str = AUTO_CPU_MODEL, runs: int = 2) -> dict[int, float]:
    """{threads: seconds per transcription}, best of `runs` each."""
    from faster_whisper import WhisperModel

    clip = _clip()
    timings = {}
    for threads in candidates(os.cpu_count() or 1):
        model = WhisperModel(model_size, device="cpu", compute_type="int8", cpu_threads=threads)

        def once() -> float:
            started = time.perf_counter()
            segments, _info = model.transcribe(
                clip, language="en", beam_size=WHISPER_BEAM_SIZE, temperature=0.0, initial_prompt=DEFAULT_INITIAL_PROMPT
            )
            list(segments)
            return time.perf_counter() - started

        once()  # warm-up: first call pays for lazy allocations
        timings[threads] = min(once() for _ in range(runs))
        del model
    return timings


if __name__ == "__main__":
    assert candidates(1) == [1]
    assert candidates(4) == [2, 4]
    assert candidates(22) == [2, 4, 6, 8, 11, 12, 16, 22]
    assert pick({2: 1.0, 4: 0.5, 8: 0.49, 16: 0.7}) == 4  # 8 is within 5% of 4, so 4 wins
    assert pick({1: 2.0}) == 1
    print("calibrate self-check passed")

    size = sys.argv[1] if len(sys.argv) > 1 else AUTO_CPU_MODEL
    print(f"Timing {size} on CPU...")
    results = calibrate(size)
    for n, seconds in results.items():
        print(f"  {n:>3} threads: {seconds * 1000:.0f} ms")
    print(f"Best: {pick(results)} threads")
