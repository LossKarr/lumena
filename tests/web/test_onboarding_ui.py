from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
ONBOARDING = (ROOT / "web" / "static" / "js" / "onboarding.js").read_text(encoding="utf-8")
SETUP = (ROOT / "web" / "static" / "js" / "setup.js").read_text(encoding="utf-8")
MAIN = (ROOT / "web" / "static" / "js" / "main.js").read_text(encoding="utf-8")


def test_real_shell_controls_have_stable_onboarding_targets() -> None:
    for target in (
        "navigation", "agent-navigation", "model-selector", "agent-mode",
        "chat-area", "composer", "file-button",
    ):
        assert f'data-onboarding-target="{target}"' in INDEX


def test_tour_is_replayable_and_loaded_as_a_small_module() -> None:
    assert 'id="replay-onboarding-btn"' in INDEX
    assert "import { initOnboarding, replayOnboarding }" in MAIN
    assert "q('replay-onboarding-btn'" in MAIN
    assert '/static/css/onboarding.css?v=3' in INDEX


def test_tour_uses_the_existing_global_admin_token_contract() -> None:
    assert "typeof ADMIN_TOKEN!=='undefined'&&ADMIN_TOKEN" in ONBOARDING
    assert "const value = currentAdminToken()" in ONBOARDING
    assert "./onboarding.js?v=8" in MAIN


def test_tour_never_sends_a_chat_message_automatically() -> None:
    assert "sendMessage(" not in ONBOARDING
    assert "quickSend(" not in ONBOARDING
    assert "Cette étape consommera des tokens uniquement" in ONBOARDING
    assert "chat_response_received" in ONBOARDING


def test_mode_choice_and_failures_use_real_events_and_visible_recovery() -> None:
    assert "data-mode=\"chat\"" in ONBOARDING
    assert "data-mode=\"agent\"" in ONBOARDING
    assert "lumena:mode-changed" in ONBOARDING
    assert "mode_selected" in ONBOARDING
    assert "Impossible d’enregistrer cette étape" in ONBOARDING
    assert "ArrowRight" in ONBOARDING


def test_legacy_installation_is_migrated_without_reopening_tour() -> None:
    assert "wizardJustDone" in ONBOARDING
    assert "legacyDone&&state.tour_status==='not_started'" in ONBOARDING
    assert "state=await api('/complete',{})" in ONBOARDING


def test_quick_setup_uses_smart_access_and_preserves_complete_schema() -> None:
    assert "_allSteps = data.steps || []" in SETUP
    assert "_steps = [..._allSteps]" in SETUP
    assert "id: 'access'" in SETUP
    assert "_accessChoice === 'api'" in SETUP
    assert "_accessChoice === 'local'" in SETUP
    assert "Configuration complète" in SETUP
    assert "if (_setupMode !== 'quick')" in SETUP


def test_smart_access_uses_real_existing_api_codex_and_ollama_contracts() -> None:
    for endpoint in (
        "/api/models",
        "/api/codex-subscription/account/status",
        "/api/codex-subscription/adopt",
        "/api/codex-subscription/login/start",
        "/api/codex-subscription/login/wait",
        "/api/codex-subscription/models",
        "/api/codex-subscription/model/select",
        "/api/setup/ollama-models",
    ):
        assert endpoint in SETUP
    assert "Aucune configuration prête détectée" in SETUP
    assert "Configuration détectée" in SETUP


def test_setup_success_waits_for_real_shell_before_starting_tour() -> None:
    startup = (ROOT / "web" / "static" / "js" / "startup.js").read_text(encoding="utf-8")
    assert "await window.startLumena()" in SETUP
    assert "window.selectStartupModel(selectedModel)" in SETUP
    assert "if (started === false) return" in SETUP
    assert "return true" in startup
    assert "return false" in startup
    assert "new CustomEvent('lumena:setup-complete')" not in SETUP
    assert "lumena:app-ready" in startup
    assert "_injectWelcomeMessage();" not in SETUP


def test_accessibility_and_responsive_contract_is_present() -> None:
    css = (ROOT / "web" / "static" / "css" / "onboarding.css").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css
    assert "forced-colors" in css
    assert "@media(max-width:760px)" in css
    assert "aria-labelledby=\"onboarding-title\"" in ONBOARDING
    assert "_bindWizardFocusTrap" in SETUP
    assert "event.key !== 'Tab'" in SETUP


def test_optional_skip_resumes_and_native_alerts_are_not_used() -> None:
    assert "steps:['work_progress'],dismiss:false" in ONBOARDING
    assert "dismiss:true" in ONBOARDING
    assert "alert(" not in ONBOARDING
    assert "alert(" not in SETUP


def test_setup_protected_requests_share_the_historical_token_contract() -> None:
    assert "function _currentAdminToken()" in SETUP
    assert "typeof ADMIN_TOKEN !== 'undefined' && ADMIN_TOKEN" in SETUP
    assert "Authorization: `Bearer ${_currentAdminToken()}`" in SETUP
    assert 'id="setup-save-error"' in SETUP


def test_tour_has_persisted_adaptive_goals_without_automatic_execution() -> None:
    assert "data-goal=\"chat\"" in ONBOARDING
    assert "data-goal=\"agent\"" in ONBOARDING
    assert "data-goal=\"file\"" in ONBOARDING
    assert "state=await api('/goal',{goal})" in ONBOARDING
    assert "FLOWS" in ONBOARDING
    assert "fillSuggestedPrompt" in ONBOARDING
    assert "input.value=suggestedPrompt()" in ONBOARDING
    assert "sendMessage(" not in ONBOARDING


def test_tour_exit_is_confirmed_without_native_dialog() -> None:
    assert "showDismissConfirm" in ONBOARDING
    assert "Reprendre plus tard" in ONBOARDING
    assert "confirm(" not in ONBOARDING
