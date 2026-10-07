from src.voice.v2.performance_profiles import (
    VOICE_PERFORMANCE_PROFILES,
    active_voice_performance_profile,
    get_voice_performance_profile,
    profile_compatibility,
    recommended_voice_performance_profile,
    serialize_voice_performance_profiles,
)


def _hardware(*, cuda_ready=False, cpu_count=16, ram_gb=32.0, vram_gb=12.0):
    return {
        "cuda_ready": cuda_ready,
        "cpu_count": cpu_count,
        "ram_gb": ram_gb,
        "gpu": {"vram_gb": vram_gb},
    }


def test_profiles_are_ordered_from_lightest_to_most_capable():
    assert [profile.order for profile in VOICE_PERFORMANCE_PROFILES] == [1, 2, 3, 4, 5]
    assert [profile.id for profile in VOICE_PERFORMANCE_PROFILES] == [
        "essential", "light", "balanced", "precision", "studio_gpu",
    ]
    assert [profile.quality for profile in VOICE_PERFORMANCE_PROFILES] == [1, 2, 3, 4, 5]


def test_every_profile_disables_blocking_partial_transcriptions():
    for profile in VOICE_PERFORMANCE_PROFILES:
        assert profile.settings["LUMENA_STT_PARTIAL_EVERY_MS"] == "0"


def test_every_product_profile_keeps_speech_local_by_default():
    for profile in VOICE_PERFORMANCE_PROFILES:
        assert profile.settings["LUMENA_TTS_MODE"] == "offline"
        assert profile.settings["LUMENA_VOICE_CLOUD_ALLOWED"] == "0"
        assert "local" in profile.privacy.lower()


def test_gpu_profile_is_rejected_without_real_cuda_runtime():
    profile = get_voice_performance_profile("studio_gpu")
    compatible, reasons = profile_compatibility(profile, _hardware(cuda_ready=False))
    assert compatible is False
    assert any("CUDA" in reason for reason in reasons)


def test_recommendation_uses_actual_hardware_instead_of_gpu_presence():
    assert recommended_voice_performance_profile(_hardware(cuda_ready=False)) == "balanced"
    assert recommended_voice_performance_profile(_hardware(cuda_ready=True)) == "studio_gpu"
    assert recommended_voice_performance_profile(
        _hardware(cuda_ready=False, cpu_count=4, ram_gb=8)
    ) == "light"


def test_active_profile_requires_an_exact_complete_match():
    balanced = get_voice_performance_profile("balanced")
    values = dict(balanced.settings)
    assert active_voice_performance_profile(values) == "balanced"
    values["LUMENA_STT_MODEL"] = "large-v3-turbo"
    assert active_voice_performance_profile(values) is None


def test_serialized_catalog_marks_active_recommended_and_compatibility():
    balanced = get_voice_performance_profile("balanced")
    payload = serialize_voice_performance_profiles(
        dict(balanced.settings), _hardware(cuda_ready=False)
    )
    assert payload["active_profile"] == "balanced"
    assert payload["recommended_profile"] == "balanced"
    by_id = {item["id"]: item for item in payload["profiles"]}
    assert by_id["balanced"]["active"] is True
    assert by_id["balanced"]["recommended"] is True
    assert by_id["studio_gpu"]["compatible"] is False
