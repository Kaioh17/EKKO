"""Intent routing: transcribed text in, validated intent bundle out.

Stage 3 of the EKKO pipeline (see readme.md), sitting between
listener/vad_listener.py's Whisper transcription and command execution.
Deliberately free of any dependency on the rest of the repo, so it can
be exercised standalone with a plain string rather than a live mic.
"""
