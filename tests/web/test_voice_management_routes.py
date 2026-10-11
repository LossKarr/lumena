"""Product wiring for bounded Voice self-management."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.voice.v2.management import get_voice_recommendation_broker
from web.routes import advanced


ROOT = Path(__file__).resolve().parents[2]


def _snapshot() -> dict:
    return {
        "schema_version": 1,
        "lifecycle": {"running": True, "state": "running", "backend": "v2"},
        "configuration": {
            "active_profile": "custom",
            "hardware_recommended_profile": "balanced",
            "settings_pending": False,
            "saved": {"runtime": {}, "voice": {}},
            "effective": {"runtime": {}, "voice": {"acoustic_profile": "push_to_talk"}},
        },
        "engine": {"requested": "auto", "effective": "piper", "fallback_count": 0},
        "hardware": {
            "profile": "cpu_fast",
            "cpu_count": 12,
            "ram_gb": 32,
            "gpu": {"available": False, "vram_gb": 0, "vram_free_gb": 0},
            "cuda_runtime": {"cublas": False, "cudnn": False},
            "cuda_ready": False,
        },
        "components": {
            "qwen3_tts": {"installed": True, "ready": True, "qualified": False, "performance": {}},
            "audio_frontend": {"aec_available": False},
        },
        "timings": {"event_count": 4},
        "privacy": {"cloud_tts_allowed": False},
    }


def test_management_route_returns_same_facts_diagnostic_and_one_shot_ticket(monkeypatch) -> None:
    monkeypatch.setattr(advanced, "_voice_management_snapshot", _snapshot)
    payload = asyncio.run(advanced.get_voice_management(goal="fast"))
    assert payload["snapshot"]["engine"]["effective"] == "piper"
    assert payload["diagnostic"]["schema_version"] == 1
    recommendation = payload["recommendation"]
    assert recommendation["goal"] == "fast"
    assert recommendation["updates"]["LUMENA_VOICE_TTS_ENGINE"] == "piper"
    assert recommendation["recommendation_id"]
    assert recommendation["expires_in_s"] > 0


def test_apply_route_consumes_ticket_and_requires_verified_transaction(monkeypatch) -> None:
    recommendation = {
        "goal": "fast",
        "profile_id": "balanced",
        "updates": {"LUMENA_VOICE_TTS_ENGINE": "piper"},
    }
    ticket = get_voice_recommendation_broker().issue(recommendation)["recommendation_id"]
    observed: dict = {}

    async def fake_apply(payload, **kwargs):
        observed["payload"] = payload
        observed["authorized"] = kwargs["explicitly_authorized"]
        return {
            "ok": True,
            "state": "verified",
            "verified": True,
            "transaction_id": "tx",
            "updated": ["LUMENA_VOICE_TTS_ENGINE"],
        }

    import src.voice.v2.management_control as control

    monkeypatch.setattr(control, "apply_voice_recommendation_transaction", fake_apply)
    monkeypatch.setattr(advanced, "_voice_management_snapshot", _snapshot)
    result = asyncio.run(advanced.apply_voice_management(ticket))
    assert observed == {"payload": recommendation, "authorized": True}
    assert result["verified"] is True
    assert result["proof"]["configuration"]["settings_pending"] is False

    with pytest.raises(HTTPException) as reused:
        asyncio.run(advanced.apply_voice_management(ticket))
    assert reused.value.status_code == 409


def test_management_routes_and_panel_are_authenticated_and_wired() -> None:
    routes = (ROOT / "web" / "routes" / "advanced.py").read_text(encoding="utf-8")
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "web" / "static" / "js" / "api.js").read_text(encoding="utf-8")
    assert 'router.get("/api/voice/management"' in routes
    assert '"/api/voice/management/apply/{recommendation_id}"' in routes
    assert 'router.post("/api/voice/management/rollback"' in routes
    assert routes.count("Depends(deps.verify_admin_token)") >= 3
    for element_id in (
        "voice-advisor-goal",
        "voice-advisor-analyze",
        "voice-advisor-apply",
        "voice-advisor-rollback",
        "voice-advisor-evidence",
    ):
        assert f'id="{element_id}"' in html
    assert "/api/voice/management?goal=" in js
    assert "/api/voice/management/apply/" in js
    assert "/api/voice/management/rollback" in js
    assert "_voiceRecommendationId=''" in js
