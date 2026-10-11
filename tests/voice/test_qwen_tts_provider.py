from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import wave

import pytest

from src.voice.v2.providers.base import CancelToken
from src.voice.v2.providers.qwen3_tts import Qwen3TTSProvider
from src.voice.v2.qwen_worker_client import QwenWorkerClient
from src.voice.v2.voice_profile import VoiceProfile


class FakeWorker:
    def __init__(self):
        self.calls = []
        self.closed = False

    async def synthesize(self, **kwargs):
        self.calls.append(kwargs)
        path = Path(kwargs["output_path"])
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            output.writeframes(b"\0\1" * 2400)
        return {"ok": True, "path": str(path)}

    async def aclose(self):
        self.closed = True

    def status(self):
        return {"alive": True, "restarts": 0, "last_error_code": ""}


def _voice(tmp_path: Path, *, consent=True):
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"fixture")
    voice = VoiceProfile(reference_consent_confirmed=consent)
    voice.local.qwen_reference = str(reference)
    voice.local.qwen_reference_text = "Bonjour, je suis Lumena."
    return voice


@pytest.mark.asyncio
async def test_provider_synthesizes_local_wav_with_declared_metadata(tmp_path):
    worker = FakeWorker()
    provider = Qwen3TTSProvider(worker, cache_dir=tmp_path / "audio")
    result = await provider.synthesize("Bonjour.", _voice(tmp_path))
    assert result.ok is True and result.provider == "qwen3-tts"
    assert result.sample_rate == 24000 and result.channels == 1
    assert result.duration_ms == 100
    assert worker.calls[0]["reference_text"] == "Bonjour, je suis Lumena."


@pytest.mark.asyncio
async def test_provider_requires_identity_consent_and_honours_cancellation(tmp_path):
    worker = FakeWorker()
    provider = Qwen3TTSProvider(worker, cache_dir=tmp_path / "audio")
    assert not (await provider.synthesize("Bonjour.", _voice(tmp_path, consent=False))).ok
    token = CancelToken()
    token.cancel()
    assert not (await provider.synthesize("Bonjour.", _voice(tmp_path), token)).ok
    assert worker.calls == []


@pytest.mark.asyncio
async def test_provider_stream_is_segmented_and_truthful(tmp_path):
    provider = Qwen3TTSProvider(FakeWorker(), cache_dir=tmp_path / "audio")
    chunks = [chunk async for chunk in provider.stream(
        "Première phrase assez claire. Deuxième phrase qui continue la réponse.",
        _voice(tmp_path),
    )]
    assert chunks and [item.sequence for item in chunks] == list(range(len(chunks)))
    status = provider.runtime_status()
    assert status["tts_streaming_mode"] == "segmented_files"
    assert status["tts_native_pcm_streaming"] is False


@pytest.mark.asyncio
async def test_provider_stream_releases_short_first_chunk_and_bounds_all_chunks(tmp_path):
    worker = FakeWorker()
    provider = Qwen3TTSProvider(worker, cache_dir=tmp_path / "audio")
    text = "Réponse finale " + ("avec beaucoup de détails importants, " * 12) + "terminée."
    chunks = [chunk async for chunk in provider.stream(text, _voice(tmp_path))]
    spoken = [call["text"] for call in worker.calls]
    assert chunks and len(spoken[0]) <= 32
    assert all(len(segment) <= 70 for segment in spoken)
    assert " ".join(spoken).replace("  ", " ") == text.replace("  ", " ")


@pytest.mark.asyncio
async def test_provider_prewarm_reports_real_latency_and_removes_probe_audio(tmp_path):
    provider = Qwen3TTSProvider(FakeWorker(), cache_dir=tmp_path / "audio")
    result = await provider.prewarm("Bonjour.", _voice(tmp_path))
    assert result["ok"] is True
    assert result["provider"] == "qwen3-tts"
    assert result["latency_ms"] >= 0
    assert list((tmp_path / "audio").glob("qwen-*.wav")) == []


@pytest.mark.asyncio
async def test_worker_client_protocol_and_shutdown(tmp_path):
    script = tmp_path / "fake_worker.py"
    script.write_text(
        "import json,sys\n"
        "print(json.dumps({'event':'ready','device':'test'}),flush=True)\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line); action=r.get('action')\n"
        " if action=='shutdown': print(json.dumps({'id':r['id'],'ok':True}),flush=True); break\n"
        " print(json.dumps({'id':r['id'],'ok':True,'path':r.get('output_path','')}),flush=True)\n",
        encoding="utf-8",
    )
    model = tmp_path / "model"
    model.mkdir()
    client = QwenWorkerClient(
        python=Path(sys.executable), worker_script=script, model_dir=model,
        startup_timeout_s=5, request_timeout_s=5,
    )
    result = await client.request({"action": "health"})
    assert result["ok"] is True and client.status()["device"] == "test"
    await client.aclose()
    assert client.alive is False


@pytest.mark.asyncio
async def test_worker_client_cancellation_terminates_uncooperative_process(tmp_path):
    script = tmp_path / "slow_worker.py"
    script.write_text(
        "import json,sys,time\n"
        "print(json.dumps({'event':'ready','device':'test'}),flush=True)\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line); time.sleep(30)\n",
        encoding="utf-8",
    )
    model = tmp_path / "model"
    model.mkdir()
    client = QwenWorkerClient(
        python=Path(sys.executable), worker_script=script, model_dir=model,
        startup_timeout_s=5, request_timeout_s=35,
    )
    token = CancelToken()
    task = asyncio.create_task(client.request({"action": "health"}, cancel=token))
    await asyncio.sleep(0.1)
    token.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.alive is False
