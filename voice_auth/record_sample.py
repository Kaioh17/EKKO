"""
Records a single voice sample from your laptop mic and saves it as a
16kHz mono WAV file, the format the speaker verification model expects.

Usage:
    python record_sample.py enrollment/sample_01.wav
    python record_sample.py enrollment/sample_01.wav --seconds 4
"""

import argparse
import os

import numpy as np
import sounddevice as sd
from scipy.io.wavfile import write

SAMPLE_RATE = 16000  # required by the pretrained speaker model


def record(path: str, seconds: float) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    print(f"Recording for {seconds} seconds. Speak now.")
    audio = sd.rec(
        int(seconds * SAMPLE_RATE),
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
    )
    sd.wait()
    print("Done recording.")

    # Convert float32 [-1, 1] to int16 PCM, standard WAV format
    audio_int16 = np.int16(audio * 32767)
    write(path, SAMPLE_RATE, audio_int16)
    print(f"Saved to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output_path", help="Where to save the WAV file")
    parser.add_argument(
        "--seconds", type=float, default=4.0, help="Recording length in seconds"
    )
    args = parser.parse_args()
    record(args.output_path, args.seconds)
