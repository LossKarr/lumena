from __future__ import annotations

import io
from pathlib import Path

import pytest
from starlette.datastructures import Headers, UploadFile

from src.voice.v2.observability import get_voice_telemetry
from web.routes import advanced


@pytest.mark.asyncio
async def test_chat_dictation_endpoint_uses_active_stt_and_deletes_temp_file():
    telemetry = get_voice_telemetry()
    seen = {}

    async def transcribe(path):
        path = Path(path)
        seen["path"] = path
        seen["bytes"] = path.read_bytes()
        return "bonjour Lumena"

    telemetry.register_transcribe(transcribe)
    upload = UploadFile(
        io.BytesIO(b"fake-webm-audio"),
        filename="dictation.webm",
        headers=Headers({"content-type": "audio/webm"}),
    )
    try:
        result = await advanced.transcribe_chat_dictation(upload)
    finally:
        telemetry.register_transcribe(None)
    assert result["text"] == "bonjour Lumena"
    assert result["should_send"] is False
    assert result["status"] == "ok"
    assert result["retained"] is False
    assert seen["bytes"] == b"fake-webm-audio"
    assert not seen["path"].exists()


@pytest.mark.asyncio
async def test_dictation_config_is_bounded(monkeypatch):
    monkeypatch.setenv("LUMENA_CHAT_DICTATION_MAX_S", "9999")
    monkeypatch.setenv("LUMENA_CHAT_DICTATION_SILENCE_MS", "1")
    assert await advanced.get_chat_dictation_config() == {
        "max_duration_ms": 300000,
        "silence_ms": 800,
    }


@pytest.mark.asyncio
async def test_dictation_endpoint_removes_final_send_command():
    telemetry = get_voice_telemetry()

    async def detailed(_path):
        return {
            "text": "Bonjour Lumena. Envoyer.",
            "segments": [{"text": "Bonjour Lumena."}, {"text": "Envoyer."}],
            "status": "ok",
            "device": "cpu",
            "compute_type": "int8",
        }

    telemetry.register_transcribe_detailed(detailed)
    upload = UploadFile(
        io.BytesIO(b"fake-webm-audio"),
        filename="dictation.webm",
        headers=Headers({"content-type": "audio/webm"}),
    )
    try:
        result = await advanced.transcribe_chat_dictation(upload)
    finally:
        telemetry.register_transcribe_detailed(None)
    assert result["text"] == "Bonjour Lumena."
    assert result["should_send"] is True
    assert result["command"] == "envoyer"
    assert result["command_boundary"] == "segment"


@pytest.mark.asyncio
async def test_dictation_decode_error_is_explicit_and_temp_is_deleted():
    from fastapi import HTTPException
    from src.voice.stt import STTAudioDecodeError

    telemetry = get_voice_telemetry()
    seen = {}

    async def broken(path):
        seen["path"] = Path(path)
        raise STTAudioDecodeError("Invalid data found when processing input")

    telemetry.register_transcribe_detailed(broken)
    upload = UploadFile(
        io.BytesIO(b"broken-audio"),
        filename="dictation.webm",
        headers=Headers({"content-type": "audio/webm"}),
    )
    try:
        with pytest.raises(HTTPException) as exc_info:
            await advanced.transcribe_chat_dictation(upload)
    finally:
        telemetry.register_transcribe_detailed(None)
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail["code"] == "audio_decode_failed"
    assert not seen["path"].exists()


@pytest.mark.asyncio
async def test_dictation_stt_failure_is_503_not_fake_silence():
    from fastapi import HTTPException
    from src.voice.stt import STTUnavailableError

    telemetry = get_voice_telemetry()

    async def broken(_path):
        raise STTUnavailableError("CUBLAS puis CPU indisponibles")

    telemetry.register_transcribe_detailed(broken)
    upload = UploadFile(
        io.BytesIO(b"valid-container"),
        filename="dictation.webm",
        headers=Headers({"content-type": "audio/webm"}),
    )
    try:
        with pytest.raises(HTTPException) as exc_info:
            await advanced.transcribe_chat_dictation(upload)
    finally:
        telemetry.register_transcribe_detailed(None)
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "stt_unavailable"


@pytest.mark.asyncio
async def test_dictation_state_suppresses_autonomous_voice_input():
    telemetry = get_voice_telemetry()
    assert await advanced.set_voice_dictation_state(True) == {"active": True}
    assert telemetry.is_dictation_active() is True
    assert await advanced.set_voice_dictation_state(False) == {"active": False}
    assert telemetry.is_dictation_active() is False


def test_chat_composer_contains_manual_dictation_control_and_no_auto_send():
    root = Path(__file__).resolve().parents[2]
    html = (root / "web" / "index.html").read_text(encoding="utf-8")
    chat = (root / "web" / "static" / "js" / "chat.js").read_text(encoding="utf-8")
    assert 'id="btn-chat-dictation"' in html
    assert "navigator.mediaDevices.getUserMedia" in chat
    assert "/api/voice/transcribe" in chat
    assert "_insertDictationAtCursor(input,text)" in chat
    assert "_startDictationSilenceDetector" in chat
    assert "data.should_send" in chat
    finish_body = chat.split("async function _finishChatDictation", 1)[1].split(
        "export async function toggleChatDictation", 1
    )[0]
    assert "if(data.should_send)" in finish_body
    assert "_dictationSendDoneFor!==sessionId" in finish_body
    assert "sendMessage();" in finish_body


def test_dictation_configuration_is_exposed_in_voice_group():
    from web.routes.config import _CONFIG_SCHEMA

    schema = {entry["key"]: entry for entry in _CONFIG_SCHEMA}
    assert schema["LUMENA_STT_DEVICE"]["options"] == ["cuda", "cpu"]
    assert schema["LUMENA_STT_COMPUTE"]["options"] == ["float16", "int8", "float32"]
    assert schema["LUMENA_CHAT_DICTATION_MAX_S"]["default"] == "60"
    assert schema["LUMENA_CHAT_DICTATION_SILENCE_MS"]["default"] == "1800"
