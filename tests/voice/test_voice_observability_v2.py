import pytest

from src.voice.v2.observability import VoiceTelemetryRegistry, get_voice_telemetry
from src.voice.v2.supervisor import VoiceV2Manager
from web.routes import advanced, deps


def test_telemetry_snapshot_is_copy_and_stop_is_explicit():
    registry = VoiceTelemetryRegistry()
    calls = []
    registry.update(state="speaking", first_audio_ms=123.4)
    snap = registry.snapshot()
    snap["state"] = "corrupted"
    assert registry.snapshot()["state"] == "speaking"
    assert registry.stop_audio() is False
    registry.register_stop_audio(lambda: calls.append("stop"))
    assert registry.stop_audio() is True
    assert calls == ["stop"]


@pytest.mark.asyncio
async def test_voice_test_control_is_async_and_explicit():
    registry = VoiceTelemetryRegistry()
    calls = []
    assert await registry.test_voice() is False

    async def test_callback():
        calls.append("spoken")

    registry.register_test_voice(test_callback)
    assert await registry.test_voice() is True
    assert calls == ["spoken"]


@pytest.mark.asyncio
async def test_dictation_state_and_transcriber_are_explicit(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr("src.voice.v2.observability.time.monotonic", lambda: clock["now"])
    registry = VoiceTelemetryRegistry()
    assert registry.is_dictation_active() is False
    registry.set_dictation_active(True)
    assert registry.is_dictation_active() is True
    assert registry.snapshot()["dictation_active"] is True
    clock["now"] += 91.0
    assert registry.is_dictation_active() is False
    assert registry.snapshot()["dictation_active"] is False
    registry.set_dictation_active(True)
    assert await registry.transcribe("sample.webm") is None

    async def transcribe(path):
        assert path == "sample.webm"
        return "message dicté"

    registry.register_transcribe(transcribe)
    assert await registry.transcribe("sample.webm") == "message dicté"


def test_dictation_custom_lease_covers_long_local_transcription(monkeypatch):
    clock = {"now": 10.0}
    monkeypatch.setattr("src.voice.v2.observability.time.monotonic", lambda: clock["now"])
    registry = VoiceTelemetryRegistry()
    registry.set_dictation_active(True, lease_s=240)
    clock["now"] += 100
    assert registry.is_dictation_active() is True
    clock["now"] += 141
    assert registry.is_dictation_active() is False


@pytest.mark.asyncio
async def test_structured_transcriber_preserves_status_and_segments():
    registry = VoiceTelemetryRegistry()

    async def detailed(_audio):
        return {
            "text": "Bonjour. Envoyer.",
            "segments": [{"text": "Envoyer."}],
            "status": "ok",
        }

    registry.register_transcribe_detailed(detailed)
    result = await registry.transcribe_detailed("sample.webm")
    assert result["status"] == "ok"
    assert result["segments"] == [{"text": "Envoyer."}]


@pytest.mark.asyncio
async def test_simple_registration_supersedes_stale_detailed_transcriber():
    registry = VoiceTelemetryRegistry()

    async def stale(_audio):
        raise RuntimeError("ancien runtime")

    registry.register_transcribe_detailed(stale)
    registry.register_transcribe(lambda _audio: "nouveau runtime")
    assert await registry.transcribe_detailed("sample.webm") is None
    assert await registry.transcribe("sample.webm") == "nouveau runtime"


@pytest.mark.asyncio
async def test_atomic_transcriber_pair_and_owner_safe_cleanup():
    registry = VoiceTelemetryRegistry()
    old_owner = object()
    new_owner = object()
    registry.register_transcribers(
        lambda _audio: "ancien", lambda _audio: {"text": "ancien"},
        owner=old_owner,
    )
    registry.register_transcribers(
        lambda _audio: "nouveau",
        lambda _audio: {"text": "nouveau", "status": "ok"},
        owner=new_owner,
    )
    assert registry.clear_transcribers(owner=old_owner) is False
    assert (await registry.transcribe_detailed("sample.webm"))["text"] == "nouveau"
    assert registry.clear_transcribers(owner=new_owner) is True
    assert await registry.transcribe("sample.webm") is None


def test_supervisor_status_includes_runtime_telemetry():
    telemetry = get_voice_telemetry()
    telemetry.update(provider="piper", first_audio_ms=42, task_id="task_voice")
    manager = VoiceV2Manager(runner=lambda *a, **k: None)
    status = manager.get_status()
    assert status["provider"] == "piper"
    assert status["first_audio_ms"] == 42
    assert status["task_id"] == "task_voice"


@pytest.mark.asyncio
async def test_voice_mute_endpoint_uses_core_global_mute(monkeypatch):
    class _Core:
        def __init__(self): self.values = []
        def set_global_mute(self, enabled): self.values.append(enabled)

    core = _Core()
    monkeypatch.setattr(deps, "lumena", core)
    assert await advanced.set_voice_mute(True) == {"muted": True}
    assert core.values == [True]
    assert get_voice_telemetry().snapshot()["muted"] is True


@pytest.mark.asyncio
async def test_stop_audio_endpoint_never_cancels_task():
    telemetry = get_voice_telemetry()
    calls = []
    telemetry.register_stop_audio(lambda: calls.append("audio"))
    result = await advanced.stop_voice_audio()
    assert result == {"stopped": True, "task_continues": True}
    assert calls == ["audio"]
    telemetry.register_stop_audio(None)
