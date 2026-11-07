import numpy as np

from feedback.speech import chime_audio


def test_chime_is_short_quiet_and_click_free():
    a = chime_audio(24000)
    assert a.dtype == np.float32 and a.ndim == 1
    seconds = len(a) / 24000
    assert 0.15 < seconds < 0.4  # about a fifth of a second, against ~2.25 s of speech
    assert 0.05 < np.abs(a).max() <= 0.5  # audible, never loud
    assert abs(a[0]) < 0.01 and abs(a[-1]) < 0.01  # faded at both ends: no pop


def test_chime_rises_in_pitch():
    a = chime_audio(24000)
    half = len(a) // 2

    def pitch(x):  # dominant frequency
        spectrum = np.abs(np.fft.rfft(x))
        return np.fft.rfftfreq(len(x), 1 / 24000)[spectrum.argmax()]

    assert pitch(a[half:]) > pitch(a[:half])
