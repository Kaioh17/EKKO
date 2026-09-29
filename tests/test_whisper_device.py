from listener.defaults import pick_whisper as pick


def test_auto_cpu():
    assert pick("auto", cuda_devices=0) == ("small", "cpu", "int8")


def test_auto_gpu():
    assert pick("auto", cuda_devices=1) == ("medium", "cuda", "float16")


def test_explicit_model_kept():
    assert pick("tiny", cuda_devices=0) == ("tiny", "cpu", "int8")
    assert pick("large-v3", cuda_devices=2) == ("large-v3", "cuda", "float16")
