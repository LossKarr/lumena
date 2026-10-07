from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.voice.v2.observability import VoiceTelemetryRegistry
from src.voice.v2.privacy import VoicePrivacyError, purge_voice_data
from web.routes import advanced, deps


ROOT = Path(__file__).resolve().parents[2]


def test_privacy_purge_is_bounded_and_does_not_follow_symlinks(tmp_path):
    data = tmp_path / "data"
    voice = data / "voice"
    external = tmp_path / "external"
    voice.mkdir(parents=True)
    external.mkdir()
    (voice / "session.wav").write_bytes(b"audio")
    (external / "keep.wav").write_bytes(b"private")
    try:
        (voice / "external-link").symlink_to(external, target_is_directory=True)
    except OSError:
        pass

    result = purge_voice_data(voice, data_dir=data)

    assert result["deleted"] is True
    assert not voice.exists()
    assert (external / "keep.wav").read_bytes() == b"private"


def test_privacy_purge_refuses_any_path_outside_data_voice(tmp_path):
    data = tmp_path / "data"
    other = data / "not-voice"
    other.mkdir(parents=True)
    with pytest.raises(VoicePrivacyError, match="data/voice"):
        purge_voice_data(other, data_dir=data)
    assert other.exists()


def test_telemetry_reset_clears_callbacks_status_and_timing():
    telemetry = VoiceTelemetryRegistry()
    telemetry.update(state="listening", transcript="must-not-survive")
    telemetry.register_stop_audio(lambda: None)
    telemetry.record_timing("stt.started", turn_id="one", at=1.0)
    telemetry.reset()
    snapshot = telemetry.snapshot()
    assert snapshot == {
        "timings": {
            "event_count": 0,
                "interruption_count": 0,
            "endpointing_ms": {"count": 0, "p50": None, "p95": None},
            "stt_ms": {"count": 0, "p50": None, "p95": None},
            "llm_ms": {"count": 0, "p50": None, "p95": None},
            "tts_ms": {"count": 0, "p50": None, "p95": None},
            "playback_gap_ms": {"count": 0, "p50": None, "p95": None},
            "synthesis_wait_gap_ms": {"count": 0, "p50": None, "p95": None},
            "artificial_gap_ms": {"count": 0, "p50": None, "p95": None},
        }
    }
    assert telemetry.stop_audio() is False


def test_voice_logs_do_not_embed_transcripts_or_spoken_responses():
    sources = [
        ROOT / "src" / "voice" / "v2" / "live.py",
        ROOT / "src" / "voice" / "stt.py",
        ROOT / "src" / "voice" / "tts.py",
        ROOT / "src" / "voice" / "providers" / "xtts_provider.py",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in sources)
    for forbidden in (
        "transcript: {txt!r}", "réponse  : {answer!r}",
        "résultat : {plan.spoken!r}", "Commande entendue: {final_text}",
        "[ALPHA] '{text}'", "texte: {text[:50]}", "'{text[:40]}...'",
    ):
        assert forbidden not in combined


def _voice_app() -> FastAPI:
    app = FastAPI()
    app.include_router(advanced.router)
    return app


def test_delete_voice_data_route_requires_admin_token(monkeypatch):
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "voice-secret")
    with TestClient(_voice_app()) as client:
        assert client.delete("/api/voice/data?confirm=DELETE").status_code in {401, 403}
        assert client.delete(
            "/api/voice/data?confirm=DELETE",
            headers={"Authorization": "Bearer wrong"},
        ).status_code in {401, 403}


def test_delete_voice_data_route_is_scoped_and_confirmed(monkeypatch, tmp_path):
    data = tmp_path / "data"
    voice = data / "voice"
    voice.mkdir(parents=True)
    (voice / "profile.json").write_text("{}", encoding="utf-8")
    (data / "memory.json").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(advanced, "DATA_DIR", data)
    monkeypatch.setattr(deps, "VoiceManager", None)
    app = _voice_app()
    app.dependency_overrides[deps.verify_admin_token] = lambda: None
    with TestClient(app) as client:
        assert client.delete("/api/voice/data").status_code == 422
        response = client.delete("/api/voice/data?confirm=DELETE")
    assert response.status_code == 200
    assert response.json()["scope"] == "data/voice"
    assert not voice.exists()
    assert (data / "memory.json").read_text(encoding="utf-8") == "keep"
