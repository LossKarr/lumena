from pathlib import Path

from web.routes.config import _CONFIG_SCHEMA


ROOT = Path(__file__).resolve().parents[2]


def test_voice_panel_has_six_accessible_product_pages():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for page in ("conversation", "voice", "listen", "languages", "models", "diagnostic"):
        assert f'data-voice-tab="{page}"' in html
        assert f'data-voice-page="{page}"' in html
    assert 'role="tablist"' in html
    assert 'aria-live="polite"' in html
    assert 'id="voice-performance-profiles"' in html
    assert "Profil de performance" in html


def test_voice_panel_exposes_truthful_runtime_fields_and_redacted_export():
    module = (ROOT / "web" / "static" / "js" / "api.js").read_text(encoding="utf-8")
    assert "hardware_profile" in module
    assert "server_aec_available" not in module or "voice-aec" in module
    assert "_safeVoiceDiagnostic" in module
    assert "last_error" in module
    assert "task_id" not in module[module.index("function _safeVoiceDiagnostic"):module.index("export function exportVoiceDiagnostic")]


def test_voice_settings_are_in_canonical_validated_schema():
    schema = {item["key"]: item for item in _CONFIG_SCHEMA}
    assert schema["LUMENA_VOICE_VAD_ENGINE"]["options"] == ["auto", "silero", "energy"]
    assert schema["LUMENA_VOICE_VAD_SPEECH_THRESHOLD"]["max"] == 0.99
    assert schema["LUMENA_VOICE_WAKE_THRESHOLD"]["min"] == 0.05
    assert schema["LUMENA_STT_LANGUAGE"]["options"] == ["auto", "fr", "en", "es"]
    assert schema["LUMENA_STT_PARTIAL_EVERY_MS"]["default"] == "0"
    assert schema["LUMENA_VOICE_V2_MODE"]["restart"] is True
    assert schema["LUMENA_VOICE_ACOUSTIC_PROFILE"]["restart"] is True
    assert schema["LUMENA_VOICE_IMPROVEMENT_OPT_IN"]["default"] == "0"


def test_voice_panel_assets_are_versioned_and_actions_are_exported():
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    main = (ROOT / "web" / "static" / "js" / "main.js").read_text(encoding="utf-8")
    api = (ROOT / "web" / "static" / "js" / "api.js").read_text(encoding="utf-8")
    assert "/static/css/voice-panel.css?v=3" in html
    assert "/static/js/main.js?v=68" in html
    assert "barge_in_mode" in api
    assert "guarded_no_aec" in api
    assert 'id="voice-settings-save"' in html
    assert 'id="voice-ptt-button"' in html
    for action in ("saveVoiceSettings", "applyVoicePerformanceProfile", "armVoicePushToTalk", "testVoiceMicro", "exportVoiceDiagnostic", "deleteVoiceData"):
        assert action in main
    assert "/api/voice/data?confirm=DELETE" in api
    assert "/api/voice/performance-profiles" in api
    assert "_lastVoiceStatus.activation_mode!=='push_to_talk'" in api
    assert "Micro armé : parle maintenant." in api
    assert "/api/voice/restart" in api
    assert "method:'DELETE'" in api

