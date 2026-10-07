import asyncio

import pytest
from fastapi import HTTPException

from web.routes import config as config_routes


def _hardware(cuda_ready=False):
    return {
        "cuda_ready": cuda_ready,
        "cpu_count": 16,
        "ram_gb": 32.0,
        "gpu": {"vram_gb": 12.0},
    }


def test_profile_catalog_reports_custom_and_recommended(monkeypatch):
    monkeypatch.setattr(config_routes, "_read_env_file", lambda: {})
    monkeypatch.setattr(
        config_routes, "_voice_performance_hardware", lambda: _hardware(False)
    )
    payload = asyncio.run(config_routes.get_voice_performance_profiles())
    assert payload["active_profile"] == "custom"
    assert payload["recommended_profile"] == "balanced"
    assert len(payload["profiles"]) == 5


def test_apply_profile_writes_only_its_bounded_settings(monkeypatch):
    written = {}
    monkeypatch.setattr(
        config_routes, "_voice_performance_hardware", lambda: _hardware(False)
    )
    monkeypatch.setattr(
        config_routes, "_write_env_values", lambda values: written.update(values)
    )
    payload = asyncio.run(
        config_routes.apply_voice_performance_profile("balanced")
    )
    assert payload["success"] is True
    assert payload["needs_restart"] is True
    assert written["LUMENA_STT_MODEL"] == "small"
    assert written["LUMENA_STT_DEVICE"] == "cpu"
    assert written["LUMENA_STT_PARTIAL_EVERY_MS"] == "0"
    assert "LUMENA_STT_LANGUAGE" not in written
    assert "LUMENA_VOICE_INPUT_DEVICE" not in written
    assert "LUMENA_VOICE_SESSION_ROLE" not in written


def test_apply_gpu_profile_is_blocked_without_cuda_runtime(monkeypatch):
    monkeypatch.setattr(
        config_routes, "_voice_performance_hardware", lambda: _hardware(False)
    )
    with pytest.raises(HTTPException) as error:
        asyncio.run(config_routes.apply_voice_performance_profile("studio_gpu"))
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "voice_profile_incompatible"
