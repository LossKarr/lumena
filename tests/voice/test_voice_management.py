from __future__ import annotations

import json

import pytest

from src.voice.v2.management import (
    VoiceRecommendationBroker,
    build_voice_operational_snapshot,
    diagnose_voice_snapshot,
    recommend_voice_configuration,
)


def _status(**overrides):
    base = {
        "running": True,
        "state": "running",
        "backend": "v2",
        "requested_backend": "v2",
        "mode": "chat",
        "saved_runtime_options": {
            "device": "cuda",
            "compute": "float16",
            "language": "fr",
            "model": "large-v3-turbo",
            "input_device_index": 0,
        },
        "effective_runtime_options": {
            "device": "cpu",
            "compute": "int8",
            "language": "fr",
            "model": "small",
            "input_device_index": 0,
        },
        "saved_voice_settings": {
            "tts_mode": "offline",
            "tts_engine": "qwen3",
            "vad_engine": "auto",
            "acoustic_profile": "hands_free",
            "cloud_tts_allowed": False,
        },
        "effective_voice_settings": {
            "tts_mode": "offline",
            "tts_engine": "qwen3",
            "vad_engine": "energy",
            "acoustic_profile": "hands_free",
            "cloud_tts_allowed": False,
        },
        "settings_pending": True,
        "tts_engine_configured": "qwen3",
        "tts_provider_effective": "qwen3-tts",
        "qwen": {
            "installed": True,
            "ready": True,
            "qualified": False,
            "tts_streaming_mode": "segmented_files",
            "tts_native_pcm_streaming": False,
            "runtime": {
                "performance": {"qualified": False, "first_audio_ms": 18837, "rtf": 2.4, "runtime_dir": "C:/private"}
            },
            "tts_worker": {"alive": True, "restarts": 0},
        },
        "audio_frontend": {
            "profile": "hands_free",
            "aec_available": False,
            "degraded_reason": "server_aec_unavailable",
        },
        "timings": {"event_count": 20, "synthesis_wait_gap_ms": {"count": 4, "p50": 3200, "p95": 10000}},
        "privacy": {"cloud_tts_allowed": False, "improvement_data_opt_in": False},
    }
    base.update(overrides)
    return base


def _hardware(cuda=True, free=8.5, ram=31.8):
    return {
        "profile": "nvidia" if cuda else "cpu_fast",
        "cpu_count": 16,
        "ram_gb": ram,
        "gpu": {"available": cuda, "name": "RTX Test", "vram_gb": 12, "vram_free_gb": free, "driver_version": "1"},
        "cuda_runtime": {"cublas": cuda, "cudnn": cuda},
        "cuda_ready": cuda,
    }


def test_snapshot_distinguishes_saved_effective_and_hides_paths_and_text():
    raw = _status(secret="do-not-leak", transcript="private words")
    snap = build_voice_operational_snapshot(raw, env_values={}, hardware=_hardware(), now=0)
    assert snap["configuration"]["saved"]["runtime"]["model"] == "large-v3-turbo"
    assert snap["configuration"]["effective"]["runtime"]["model"] == "small"
    assert snap["configuration"]["settings_pending"] is True
    encoded = json.dumps(snap)
    assert "C:/private" not in encoded
    assert "do-not-leak" not in encoded
    assert "private words" not in encoded


def test_diagnostic_reports_measured_qwen_gaps_and_missing_aec():
    snap = build_voice_operational_snapshot(_status(), hardware=_hardware())
    diag = diagnose_voice_snapshot(snap)
    codes = {item["code"] for item in diag["issues"]}
    assert {
        "voice_settings_pending",
        "qwen_not_conversation_qualified",
        "hands_free_without_aec",
        "tts_synthesis_gap_high",
    } <= codes
    assert diag["healthy"] is False


def test_fast_goal_uses_piper_when_qwen_is_slow():
    snap = build_voice_operational_snapshot(_status(), hardware=_hardware())
    rec = recommend_voice_configuration(snap, "fast")
    assert rec["profile_id"] == "balanced"
    assert rec["updates"]["LUMENA_VOICE_TTS_ENGINE"] == "piper"
    assert "piper_low_latency_local" in rec["reasons"]


def test_quality_does_not_select_unqualified_qwen():
    snap = build_voice_operational_snapshot(_status(), hardware=_hardware())
    rec = recommend_voice_configuration(snap, "quality")
    assert rec["updates"]["LUMENA_VOICE_TTS_ENGINE"] == "piper"
    assert "qwen_not_conversation_qualified" in rec["limitations"]


def test_quality_can_select_qualified_qwen_with_free_vram():
    status = _status()
    status["qwen"]["qualified"] = True
    status["qwen"]["runtime"]["performance"]["qualified"] = True
    snap = build_voice_operational_snapshot(status, hardware=_hardware(free=10))
    rec = recommend_voice_configuration(snap, "quality")
    assert rec["profile_id"] == "studio_gpu"
    assert rec["updates"]["LUMENA_VOICE_TTS_ENGINE"] == "auto"


def test_local_only_never_enables_cloud():
    snap = build_voice_operational_snapshot(_status(), hardware=_hardware())
    rec = recommend_voice_configuration(snap, "local_only")
    assert rec["updates"]["LUMENA_VOICE_CLOUD_ALLOWED"] == "0"


def test_low_resource_profile_is_conservative():
    snap = build_voice_operational_snapshot(_status(), hardware=_hardware(cuda=False, ram=4))
    rec = recommend_voice_configuration(snap, "low_resources")
    assert rec["profile_id"] == "essential"


def test_unknown_goal_is_rejected():
    snap = build_voice_operational_snapshot(_status(), hardware=_hardware())
    with pytest.raises(ValueError, match="unsupported_voice_goal"):
        recommend_voice_configuration(snap, "invented")


def test_recommendation_ticket_is_one_shot_and_expires():
    clock = [0.0]
    broker = VoiceRecommendationBroker(ttl_s=10, clock=lambda: clock[0])
    token = broker.issue({"updates": {"A": "B"}})["recommendation_id"]
    assert broker.consume(token) == {"updates": {"A": "B"}}
    assert broker.consume(token) is None
    token = broker.issue({"updates": {"A": "C"}})["recommendation_id"]
    clock[0] = 11.0
    assert broker.consume(token) is None
