def test_calibrate_imports():
    # listener/calibrate.py imported a constant nothing defined, so the
    # Voice page's calibrate endpoint failed on import.
    from listener.calibrate import calibrate, pick  # noqa: F401
    from listener.defaults import WHISPER_BEAM_SIZE

    assert isinstance(WHISPER_BEAM_SIZE, int) and WHISPER_BEAM_SIZE >= 1
