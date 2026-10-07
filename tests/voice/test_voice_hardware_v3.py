from types import SimpleNamespace

from src.voice.v2 import prewarm
from src.voice.v2.supervisor import resolve_voice_runtime_options


def _completed(stdout="", returncode=0):
    return SimpleNamespace(stdout=stdout, returncode=returncode)


def test_nvidia_profile_requires_driver_libraries_and_free_vram(monkeypatch):
    prewarm.detect_voice_hardware.cache_clear()
    monkeypatch.setattr(prewarm, "_cuda_runtime_files", lambda: {"cublas": True, "cudnn": True})
    monkeypatch.setattr(prewarm.subprocess, "run", lambda *a, **k: _completed(
        "RTX Test, 12288, 8192, 999.1\n"
    ))
    hardware = prewarm.detect_voice_hardware()
    assert hardware["profile"] == "nvidia"
    assert hardware["cuda_ready"] is True
    assert hardware["gpu"]["vram_free_gb"] == 8.0
    prewarm.detect_voice_hardware.cache_clear()


def test_busy_gpu_selects_cpu_profile_without_cuda_attempt(monkeypatch):
    prewarm.detect_voice_hardware.cache_clear()
    monkeypatch.setattr(prewarm, "_cuda_runtime_files", lambda: {"cublas": True, "cudnn": True})
    monkeypatch.setattr(prewarm.subprocess, "run", lambda *a, **k: _completed(
        "RTX Busy, 12288, 512, 999.1\n"
    ))
    monkeypatch.setenv("LUMENA_STT_DEVICE", "cuda")
    monkeypatch.setenv("LUMENA_STT_COMPUTE", "float16")
    hardware = prewarm.detect_voice_hardware()
    assert hardware["cuda_ready"] is False
    options = resolve_voice_runtime_options()
    assert options["device"] == "cpu"
    assert options["compute"] == "int8"
    prewarm.detect_voice_hardware.cache_clear()


def test_missing_cuda_dll_selects_cpu_even_with_visible_gpu(monkeypatch):
    prewarm.detect_voice_hardware.cache_clear()
    monkeypatch.setattr(prewarm, "_cuda_runtime_files", lambda: {"cublas": False, "cudnn": False})
    monkeypatch.setattr(prewarm.subprocess, "run", lambda *a, **k: _completed(
        "RTX Test, 8192, 7000, 999.1\n"
    ))
    monkeypatch.setenv("LUMENA_STT_DEVICE", "cuda")
    assert resolve_voice_runtime_options()["device"] == "cpu"
    prewarm.detect_voice_hardware.cache_clear()


def test_no_gpu_command_never_raises_and_exposes_cpu_profile(monkeypatch):
    prewarm.detect_voice_hardware.cache_clear()
    monkeypatch.setattr(prewarm, "_cuda_runtime_files", lambda: {"cublas": False, "cudnn": False})

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(prewarm.subprocess, "run", missing)
    hardware = prewarm.detect_voice_hardware()
    assert hardware["cuda_ready"] is False
    assert hardware["profile"] in {"cpu_low", "cpu_fast", "npu"}
    prewarm.detect_voice_hardware.cache_clear()

