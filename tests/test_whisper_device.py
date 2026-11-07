from listener.defaults import pick_whisper as pick


def test_auto_cpu():
    assert pick("auto", cuda_devices=0) == ("small", "cpu", "int8")


def test_auto_gpu():
    assert pick("auto", cuda_devices=1) == ("medium", "cuda", "float16")


def test_processor_cpu_ignores_the_gpu():
    assert pick("auto", cuda_devices=1, device="cpu") == ("small", "cpu", "int8")
    assert pick("medium", cuda_devices=1, device="cpu") == ("medium", "cpu", "int8")


def test_processor_gpu_and_auto_use_it_when_there_is_one():
    assert pick("auto", cuda_devices=1, device="gpu") == ("medium", "cuda", "float16")
    assert pick("auto", cuda_devices=1, device="auto") == ("medium", "cuda", "float16")
    # asking for the GPU on a machine without one still lands on CPU
    assert pick("auto", cuda_devices=0, device="gpu") == ("small", "cpu", "int8")


def test_cpu_threads_setting_wins_over_auto():
    from listener.defaults import whisper_cpu_threads

    assert whisper_cpu_threads(6) == 6
    assert whisper_cpu_threads(0) >= 1


def test_explicit_model_kept():
    assert pick("tiny", cuda_devices=0) == ("tiny", "cpu", "int8")
    assert pick("large-v3", cuda_devices=2) == ("large-v3", "cuda", "float16")
