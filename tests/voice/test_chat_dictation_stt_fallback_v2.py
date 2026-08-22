from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from src.voice.stt import LumenaSTT, STTUnavailableError


def test_importing_stt_does_not_load_faster_whisper_until_first_use():
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import src.voice.stt; "
                "raise SystemExit(1 if 'faster_whisper' in sys.modules else 0)"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr


class _CudaModelThatFailsDuringIteration:
    def transcribe(self, _audio, **_kwargs):
        def _segments():
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
            yield  # pragma: no cover

        return _segments(), SimpleNamespace()


class _CpuModel:
    def transcribe(self, _audio, **_kwargs):
        return iter([
            SimpleNamespace(
                text=" Bonjour Lumena.", start=0.0, end=1.2, avg_logprob=-0.1
            )
        ]), SimpleNamespace()


def _bare_stt(model, *, device="cuda"):
    stt = LumenaSTT.__new__(LumenaSTT)
    stt.model = model
    stt.model_size = "large-v3-turbo"
    stt.device = device
    stt.compute_type = "float16" if device == "cuda" else "int8"
    stt.language = "fr"
    stt.energy_threshold = 300
    stt.last_error = ""
    stt.runtime_fallback_used = False
    return stt


def test_cuda_inference_failure_retries_once_on_cpu_int8(monkeypatch):
    stt = _bare_stt(_CudaModelThatFailsDuringIteration())
    loads = []

    def _load_model():
        loads.append((stt.device, stt.compute_type))
        stt.model = _CpuModel()
        return True

    monkeypatch.setattr(stt, "load_model", _load_model)
    segments, _info = stt._transcribe_with_runtime_fallback("sample.webm")

    assert [segment.text for segment in segments] == [" Bonjour Lumena."]
    assert loads == [("cpu", "int8")]
    assert stt.device == "cpu"
    assert stt.compute_type == "int8"
    assert stt.runtime_fallback_used is True


def test_non_cuda_error_is_not_retried(monkeypatch):
    class _Broken:
        def transcribe(self, _audio, **_kwargs):
            raise RuntimeError("invalid model state")

    stt = _bare_stt(_Broken())
    monkeypatch.setattr(
        stt, "load_model", lambda: pytest.fail("fallback must not run")
    )
    with pytest.raises(RuntimeError, match="invalid model state"):
        stt._transcribe_with_runtime_fallback("sample.webm")


def test_cuda_fallback_load_failure_is_explicit(monkeypatch):
    stt = _bare_stt(_CudaModelThatFailsDuringIteration())
    monkeypatch.setattr(stt, "load_model", lambda: False)
    with pytest.raises(STTUnavailableError, match="Fallback STT CPU impossible"):
        stt._transcribe_with_runtime_fallback("sample.webm")


@pytest.mark.asyncio
async def test_detailed_transcription_exposes_segments_without_changing_text_api(
    monkeypatch, tmp_path
):
    stt = _bare_stt(_CpuModel(), device="cpu")
    monkeypatch.setattr(stt, "_calculate_energy", lambda _path: 1000)
    monkeypatch.setattr(stt, "load_model", lambda: True)
    monkeypatch.setattr(stt, "normalize_audio", lambda frames: frames)
    audio = tmp_path / "sample.webm"
    audio.write_bytes(b"fake-container")

    detailed = await stt.transcribe_file_detailed(str(audio), strict=True)
    text = await stt.transcribe_file(str(audio))

    assert detailed["status"] == "ok"
    assert detailed["text"] == "Bonjour Lumena."
    assert detailed["segments"][0]["text"] == "Bonjour Lumena."
    assert text == "Bonjour Lumena."
