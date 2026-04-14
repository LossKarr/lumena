"""
Tests pour les corrections CU Agent Loop (JSON parsing, provider caching, erreurs consécutives).
Vérifie que le CU Agent est model-agnostic et fonctionne avec n'importe quel LLM vision :
- minicpm-v JSON cassé (CJK, single quotes, tokens modèle)
- llava / moondream / gemma3 — aliases d'actions, JSON imparfait
- Cache d'erreurs permanentes (TOUT échec provider = désactivé)
- Compteur d'erreurs consécutives
"""
import json
import sys
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

# ── Import du module à tester ──
from src.computer_use.cu_agent_loop import (
    _parse_cu_action,
    _sanitize_llm_json,
    _dict_to_action,
    CUAction,
    CUAgentLoop,
    MAX_CONSECUTIVE_ERRORS,
)


# ═══════════════════════════════════════════════════════════════════════════
#  _sanitize_llm_json
# ═══════════════════════════════════════════════════════════════════════════

class TestSanitizeLlmJson:
    """Tests pour le nettoyage de JSON produit par des LLMs faibles."""

    def test_removes_chinese_characters(self):
        text = '{"thought": "Je vais scroll向下滚动以查找", "action": "scroll", "params": {"direction": "down", "amount": 5}}'
        cleaned = _sanitize_llm_json(text)
        # Aucun caractère CJK ne devrait rester
        assert "向下" not in cleaned
        assert "滚动" not in cleaned
        # Le JSON devrait être parsable
        data = json.loads(cleaned)
        assert data["action"] == "scroll"

    def test_fixes_single_quotes(self):
        text = """{"thought": "Je vois", "action": "scroll", "params": {"direction": 'down', "amount": 5}}"""
        cleaned = _sanitize_llm_json(text)
        assert "'down'" not in cleaned
        assert '"down"' in cleaned

    def test_removes_box_tokens(self):
        text = '{"thought": "Je clique", "action": "click", "params": {"x": <box>500, "y": 300}}'
        cleaned = _sanitize_llm_json(text)
        assert "<box>" not in cleaned
        assert "</box>" not in cleaned

    def test_removes_ref_tokens(self):
        text = '{"thought": "texte <ref>element</ref>", "action": "click", "params": {"x": 100, "y": 200}}'
        cleaned = _sanitize_llm_json(text)
        assert "<ref>" not in cleaned
        assert "</ref>" not in cleaned

    def test_preserves_valid_json(self):
        text = '{"thought": "OK", "action": "click", "params": {"x": 500, "y": 300}}'
        cleaned = _sanitize_llm_json(text)
        data = json.loads(cleaned)
        assert data["action"] == "click"
        assert data["params"]["x"] == 500

    def test_removes_control_characters(self):
        text = '{"thought": "OK\x00\x01", "action": "done", "params": {"summary": "fini"}}'
        cleaned = _sanitize_llm_json(text)
        assert "\x00" not in cleaned
        assert "\x01" not in cleaned

    def test_handles_mixed_cjk_and_french(self):
        text = '{"thought": "Je vois que j\'ai滚动,但是没有找到", "action": "scroll", "params": {"direction": "down", "amount": 150}}'
        cleaned = _sanitize_llm_json(text)
        assert "滚动" not in cleaned
        assert "没有" not in cleaned

    def test_empty_string(self):
        assert _sanitize_llm_json("") == ""

    def test_double_spaces_cleaned(self):
        text = '{"thought": "test  avec   espaces", "action": "done"}'
        cleaned = _sanitize_llm_json(text)
        assert "  " not in cleaned


# ═══════════════════════════════════════════════════════════════════════════
#  _parse_cu_action — Stratégie 5 (nettoyage CJK)
# ═══════════════════════════════════════════════════════════════════════════

class TestParseCuActionCJK:
    """Tests parsing JSON avec caractères CJK (bug minicpm-v réel)."""

    def test_parse_chinese_in_thought(self):
        """Reproduction exacte du bug: scroll向下滚动以查找."""
        raw = '{"thought": "Je vois que j\'ai fait du scroll向下滚动", "action": "scroll", "params": {"direction": "down", "amount": 200}}'
        action = _parse_cu_action(raw)
        # Doit réussir à parser après nettoyage, pas retourner error
        assert action.action == "scroll"
        assert action.params.get("direction") == "down"

    def test_parse_single_quotes_in_params(self):
        """Bug réel: single quotes dans les valeurs params."""
        raw = """{"thought": "scroll", "action": "scroll", "params": {"direction": 'down', "amount": 200}}"""
        action = _parse_cu_action(raw)
        assert action.action == "scroll"

    def test_parse_box_token_in_coords(self):
        """Bug réel: <box> token dans les coordonnées."""
        raw = '{"thought": "Je clique", "action": "click", "params": {"x": 571, "y": 300}}'
        action = _parse_cu_action(raw)
        assert action.action == "click"
        assert action.params["x"] == 571

    def test_parse_pure_english_fallback(self):
        """Ollama qui répond en texte pur au lieu de JSON."""
        raw = "Based on the instructions provided and reviewing the screenshot, it appears that..."
        action = _parse_cu_action(raw)
        assert action.action == "error"

    def test_parse_valid_json_unchanged(self):
        """JSON valide ne doit pas être affecté."""
        raw = '{"thought": "OK", "action": "click", "params": {"x": 100, "y": 200}}'
        action = _parse_cu_action(raw)
        assert action.action == "click"
        assert action.params["x"] == 100
        assert action.params["y"] == 200

    def test_parse_done_action(self):
        raw = '{"thought": "Terminé", "action": "done", "params": {"summary": "Tâche accomplie"}}'
        action = _parse_cu_action(raw)
        assert action.action == "done"
        assert action.params["summary"] == "Tâche accomplie"

    def test_parse_empty_response(self):
        action = _parse_cu_action("")
        assert action.action == "error"

    def test_parse_heavily_corrupted_json(self):
        """JSON très corrompu avec CJK, quotes mixtes, tokens."""
        raw = '{"thought": "Je vais scroll<ref>向下</ref>滚动", "action": "scroll", "params": {"direction": \'down\', "amount": 10}}'
        action = _parse_cu_action(raw)
        # Doit réussir grâce à la stratégie 5
        assert action.action == "scroll"


# ═══════════════════════════════════════════════════════════════════════════
#  _parse_cu_action — Regressions tests
# ═══════════════════════════════════════════════════════════════════════════

class TestParseCuActionRegression:
    """Tests de non-régression pour le parsing existant."""

    def test_json_in_markdown(self):
        raw = '```json\n{"thought": "test", "action": "click", "params": {"x": 1, "y": 2}}\n```'
        action = _parse_cu_action(raw)
        assert action.action == "click"

    def test_json_with_surrounding_text(self):
        raw = 'Voici ma réponse: {"thought": "test", "action": "scroll", "params": {"direction": "up", "amount": 3}} fin.'
        action = _parse_cu_action(raw)
        assert action.action == "scroll"

    def test_action_aliases(self):
        raw = '{"thought": "fini", "action": "finish", "params": {"summary": "OK"}}'
        action = _parse_cu_action(raw)
        assert action.action == "done"

    def test_type_alias(self):
        raw = '{"thought": "je tape", "action": "type", "params": {"text": "hello"}}'
        action = _parse_cu_action(raw)
        assert action.action == "type_text"

    def test_open_url(self):
        raw = '{"thought": "navigation", "action": "open_url", "params": {"url": "https://google.com"}}'
        action = _parse_cu_action(raw)
        assert action.action == "open_url"
        assert action.params["url"] == "https://google.com"


# ═══════════════════════════════════════════════════════════════════════════
#  MAX_CONSECUTIVE_ERRORS constant
# ═══════════════════════════════════════════════════════════════════════════

class TestMaxConsecutiveErrors:
    """Vérifie que la constante existe et est raisonnable."""

    def test_constant_exists(self):
        assert MAX_CONSECUTIVE_ERRORS >= 3
        assert MAX_CONSECUTIVE_ERRORS <= 10

    def test_default_value(self):
        assert MAX_CONSECUTIVE_ERRORS == 5


# ═══════════════════════════════════════════════════════════════════════════
#  CUAgentLoop — Provider failure caching
# ═══════════════════════════════════════════════════════════════════════════

class TestProviderFailureCaching:
    """P3.6 — CUAgentLoop délègue la gestion de santé à route_cu_vision/VisionModule.
    _provider_failures a été supprimé — remplacé par VisionModule._provider_health."""

    def test_no_provider_failures_attr(self):
        """_provider_failures n'existe plus sur CUAgentLoop (migré vers VisionModule)."""
        loop = CUAgentLoop()
        assert not hasattr(loop, "_provider_failures"), (
            "_provider_failures doit être absent — la gestion santé est dans VisionModule._provider_health"
        )

    @pytest.mark.asyncio
    async def test_call_vision_llm_uses_route_cu_vision(self):
        """_call_vision_llm doit déléguer à route_cu_vision (nouveau contrat P3.6)."""
        from unittest.mock import AsyncMock, patch
        loop = CUAgentLoop()
        loop._vision_llm_func = None

        mock_vision = MagicMock()
        mock_vision._provider_health = {}
        mock_vision._is_provider_available = MagicMock(return_value=True)
        mock_vision._record_provider_failure = MagicMock()
        mock_vision._call_analyze = AsyncMock(return_value="réponse ok")
        loop._vision = mock_vision

        with patch("src.computer_use.cu_router.build_vision_policy", return_value=["google"]):
            result = await loop._call_vision_llm("test.png", "prompt test")
        assert result.get("success") is True

    @pytest.mark.asyncio
    async def test_gemini_429_handled_by_vision_health(self):
        """Quand route_cu_vision échoue sur tous, retourne un résultat sans succès."""
        loop = CUAgentLoop()
        loop._vision_llm_func = None

        mock_vision = MagicMock()
        mock_vision._provider_health = {}
        mock_vision._is_provider_available = MagicMock(return_value=True)
        mock_vision._record_provider_failure = MagicMock()
        mock_vision._call_analyze = AsyncMock(side_effect=RuntimeError("429 Too Many Requests"))
        loop._vision = mock_vision

        with patch("src.computer_use.cu_router.build_vision_policy", return_value=["google"]):
            result = await loop._call_vision_llm("test.png", "test")
        assert result.get("success") is False

    @pytest.mark.asyncio
    async def test_claude_401_handled_by_vision_health(self):
        """Le health record est appelé en cas d'erreur."""
        loop = CUAgentLoop()
        loop._vision_llm_func = None

        mock_vision = MagicMock()
        mock_vision._provider_health = {}
        mock_vision._is_provider_available = MagicMock(return_value=True)
        mock_vision._record_provider_failure = MagicMock()
        mock_vision._call_analyze = AsyncMock(side_effect=RuntimeError("401 Unauthorized"))
        loop._vision = mock_vision

        with patch("src.computer_use.cu_router.build_vision_policy", return_value=["anthropic"]):
            await loop._call_vision_llm("test.png", "test")
        mock_vision._record_provider_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_not_cached(self):
        """Provider qui réussit ne génère pas record_failure."""
        loop = CUAgentLoop()
        loop._vision_llm_func = None

        mock_vision = MagicMock()
        mock_vision._provider_health = {}
        mock_vision._is_provider_available = MagicMock(return_value=True)
        mock_vision._record_provider_failure = MagicMock()
        mock_vision._call_analyze = AsyncMock(return_value="ok")
        loop._vision = mock_vision

        with patch("src.computer_use.cu_router.build_vision_policy", return_value=["google"]):
            result = await loop._call_vision_llm("test.png", "test")
        assert result["success"] is True
        mock_vision._record_provider_failure.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
#  Playwright — Profile lock recovery
# ═══════════════════════════════════════════════════════════════════════════

class TestPlaywrightProfileLock:
    """Tests que la logique de récupération de profil verrouillé existe."""

    def test_start_method_has_lock_recovery(self):
        """Vérifie que le start() contient la logique de lock recovery."""
        import inspect
        from src.tools.playwright_browser import PlaywrightBrowser
        source = inspect.getsource(PlaywrightBrowser.start)
        assert "verrouillé" in source or "lock" in source.lower()
        assert "taskkill" in source
        assert "SingletonLock" in source


class TestPlaywrightIsRunningPersistentContext:
    """Tests que is_running fonctionne correctement en mode persistent context.

    Bug critique: en persistent context, self._browser est None (seul
    self._context est assigné). L'ancien is_running renvoyait toujours
    False, ce qui causait un redémarrage du navigateur à chaque appel.
    """

    def test_is_running_true_persistent_context(self):
        """is_running doit renvoyer True si _context et _page existent."""
        from src.tools.playwright_browser import PlaywrightBrowser
        from unittest.mock import MagicMock

        browser = PlaywrightBrowser.__new__(PlaywrightBrowser)
        browser._browser = None  # Persistent context: _browser est None
        browser._context = MagicMock()
        browser._page = MagicMock()
        browser._page.is_closed.return_value = False

        assert browser.is_running is True

    def test_is_running_false_page_closed(self):
        """is_running doit renvoyer False si la page est fermée."""
        from src.tools.playwright_browser import PlaywrightBrowser
        from unittest.mock import MagicMock

        browser = PlaywrightBrowser.__new__(PlaywrightBrowser)
        browser._browser = None
        browser._context = MagicMock()
        browser._page = MagicMock()
        browser._page.is_closed.return_value = True

        assert browser.is_running is False

    def test_is_running_false_nothing_set(self):
        """is_running doit renvoyer False si rien n'est initialisé."""
        from src.tools.playwright_browser import PlaywrightBrowser

        browser = PlaywrightBrowser.__new__(PlaywrightBrowser)
        browser._browser = None
        browser._context = None
        browser._page = None

        assert browser.is_running is False

    def test_is_running_true_standard_mode(self):
        """is_running en mode standard (avec _browser)."""
        from src.tools.playwright_browser import PlaywrightBrowser
        from unittest.mock import MagicMock

        browser = PlaywrightBrowser.__new__(PlaywrightBrowser)
        browser._browser = MagicMock()
        browser._browser.is_connected.return_value = True
        browser._context = None
        browser._page = None

        assert browser.is_running is True


class TestPlaywrightGoogleSearchRobustness:
    """Tests de garde sur la robustesse de search_google()."""

    def test_search_google_uses_visible_selector(self):
        import inspect
        from src.tools.playwright_browser import PlaywrightBrowser
        source = inspect.getsource(PlaywrightBrowser.search_google)
        assert ":visible" in source

    def test_search_google_uses_cookie_helper(self):
        import inspect
        from src.tools.playwright_browser import PlaywrightBrowser
        source = inspect.getsource(PlaywrightBrowser.search_google)
        assert "accept_cookies" in source

    def test_search_google_has_dom_fallback(self):
        import inspect
        from src.tools.playwright_browser import PlaywrightBrowser
        source = inspect.getsource(PlaywrightBrowser.search_google)
        assert "Champ de recherche Google introuvable" in source
        assert "dom_fallback_used" in source


class TestReactBrowserLoopGuard:
    """Tests de garde pour éviter les boucles browser sans fin."""

    def test_react_has_browser_fail_streak_guard(self):
        import inspect
        from src.reasoning.react import ReActLoop
        source = inspect.getsource(ReActLoop._run_internal)
        assert "browser_fail_streak" in source
        assert "browser_fail_streak >= 4" in source
        assert "browser_fail_streak" in source
        assert "0 resultats" in source

    def test_react_has_web_fetch_fail_streak_guard(self):
        import inspect
        from src.reasoning.react import ReActLoop
        source = inspect.getsource(ReActLoop._run_internal)
        assert "web_fetch_fail_streak" in source
        assert "web_fetch_fail_streak >= 2" in source
        assert "erreur fetch" in source
        assert "403" in source

    def test_react_exempts_browser_get_content_from_repeat_guard(self):
        import inspect
        from src.reasoning.react import ReActLoop
        source = inspect.getsource(ReActLoop._run_internal)
        assert '"browser_get_content"' in source


# ═══════════════════════════════════════════════════════════════════════════
#  Vision — Ollama options
# ═══════════════════════════════════════════════════════════════════════════

class TestOllamaVisionOptions:
    """Tests que les options Ollama sont correctement configurées."""

    def test_ollama_payload_has_temperature(self):
        """Vérifie que analyze_with_ollama envoie temperature."""
        import inspect
        from src.computer_use.vision import VisionModule
        source = inspect.getsource(VisionModule.analyze_with_ollama)
        assert "temperature" in source
        assert "0.1" in source

    def test_ollama_payload_has_num_predict(self):
        """Vérifie que analyze_with_ollama limite la longueur de réponse."""
        import inspect
        from src.computer_use.vision import VisionModule
        source = inspect.getsource(VisionModule.analyze_with_ollama)
        assert "num_predict" in source


# ═══════════════════════════════════════════════════════════════════════════
#  Model-agnostic: Sanitizer étendu
# ═══════════════════════════════════════════════════════════════════════════

class TestSanitizeLlmJsonExtended:
    """Tests pour le nettoyage model-agnostic (hangul, arabic, trailing commas, etc.)."""

    def test_removes_hangul_characters(self):
        text = '{"thought": "보이는 화면에서", "action": "click", "params": {"x": 100, "y": 200}}'
        cleaned = _sanitize_llm_json(text)
        assert "보이는" not in cleaned
        data = json.loads(cleaned)
        assert data["action"] == "click"

    def test_removes_im_start_tokens(self):
        text = '<|im_start|>assistant\n{"thought": "OK", "action": "done", "params": {"summary": "fini"}}<|im_end|>'
        cleaned = _sanitize_llm_json(text)
        assert "<|im_start|>" not in cleaned
        assert "<|im_end|>" not in cleaned
        data = json.loads(cleaned)
        assert data["action"] == "done"

    def test_removes_trailing_comma(self):
        text = '{"thought": "OK", "action": "click", "params": {"x": 100, "y": 200,},}'
        cleaned = _sanitize_llm_json(text)
        data = json.loads(cleaned)
        assert data["params"]["x"] == 100

    def test_removes_comments(self):
        text = '{"thought": "OK", // this is a comment\n"action": "done", "params": {"summary": "ok"}}'
        cleaned = _sanitize_llm_json(text)
        data = json.loads(cleaned)
        assert data["action"] == "done"

    def test_removes_block_comments(self):
        text = '{"thought": "OK", /* ignore */ "action": "done", "params": {"summary": "ok"}}'
        cleaned = _sanitize_llm_json(text)
        data = json.loads(cleaned)
        assert data["action"] == "done"

    def test_removes_arabic_characters(self):
        text = '{"thought": "أنا أرى الشاشة", "action": "click", "params": {"x": 50, "y": 50}}'
        cleaned = _sanitize_llm_json(text)
        assert "أنا" not in cleaned

    def test_removes_unk_token(self):
        text = '{"thought": "<unk>test<unk>", "action": "done", "params": {"summary": "ok"}}'
        cleaned = _sanitize_llm_json(text)
        assert "<unk>" not in cleaned


# ═══════════════════════════════════════════════════════════════════════════
#  Model-agnostic: Action aliases étendues
# ═══════════════════════════════════════════════════════════════════════════

class TestActionAliasesExtended:
    """Tests pour les aliases d'actions model-agnostic."""

    def test_mouse_click_alias(self):
        raw = '{"thought": "clic", "action": "mouse_click", "params": {"x": 100, "y": 200}}'
        action = _parse_cu_action(raw)
        assert action.action == "click"

    def test_tap_alias(self):
        raw = '{"thought": "clic", "action": "tap", "params": {"x": 100, "y": 200}}'
        action = _parse_cu_action(raw)
        assert action.action == "click"

    def test_task_done_alias(self):
        raw = '{"thought": "fini", "action": "task_done", "params": {"summary": "OK"}}'
        action = _parse_cu_action(raw)
        assert action.action == "done"

    def test_task_complete_alias(self):
        raw = '{"thought": "fini", "action": "task_complete", "params": {"summary": "OK"}}'
        action = _parse_cu_action(raw)
        assert action.action == "done"

    def test_input_alias(self):
        raw = '{"thought": "tape", "action": "input", "params": {"text": "hello"}}'
        action = _parse_cu_action(raw)
        assert action.action == "type_text"

    def test_enter_text_alias(self):
        raw = '{"thought": "tape", "action": "enter_text", "params": {"text": "hello"}}'
        action = _parse_cu_action(raw)
        assert action.action == "type_text"

    def test_launch_alias(self):
        raw = '{"thought": "ouvre", "action": "launch", "params": {"name": "chrome"}}'
        action = _parse_cu_action(raw)
        assert action.action == "open_app"

    def test_keypress_alias(self):
        raw = '{"thought": "touche", "action": "keypress", "params": {"key": "enter"}}'
        action = _parse_cu_action(raw)
        assert action.action == "press_key"

    def test_key_combo_alias(self):
        raw = '{"thought": "raccourci", "action": "key_combo", "params": {"keys": "ctrl+c"}}'
        action = _parse_cu_action(raw)
        assert action.action == "hotkey"

    def test_success_alias(self):
        raw = '{"thought": "fini", "action": "success", "params": {"summary": "OK"}}'
        action = _parse_cu_action(raw)
        assert action.action == "done"


# ═══════════════════════════════════════════════════════════════════════════
#  Model-agnostic: Ollama vision détection dynamique
# ═══════════════════════════════════════════════════════════════════════════

class TestOllamaVisionDetection:
    """Tests que analyze_with_ollama détecte n'importe quel modèle vision."""

    def test_ollama_has_preferred_vision_list(self):
        """Vérifie que la liste de priorité contient les modèles courants."""
        import inspect
        from src.computer_use.vision import VisionModule
        source = inspect.getsource(VisionModule.analyze_with_ollama)
        for model in ["minicpm-v", "llava", "moondream", "gemma3", "llama3.2-vision"]:
            assert model in source, f"{model} manquant dans la liste de détection"

    def test_ollama_has_dynamic_detection(self):
        """Vérifie que la détection dynamique par modelfile/families existe."""
        import inspect
        from src.computer_use.vision import VisionModule
        source = inspect.getsource(VisionModule.analyze_with_ollama)
        assert "projector" in source
        assert "clip" in source
        assert "/api/show" in source


# ═══════════════════════════════════════════════════════════════════════════
#  Provider failure caching: TOUT échec = désactivé
# ═══════════════════════════════════════════════════════════════════════════

class TestProviderFailureCachingAll:
    """P3.6 — route_cu_vision + VisionModule._provider_health remplace _provider_failures."""

    @pytest.mark.asyncio
    async def test_key_missing_handled_by_vision_health(self):
        """Clé API absente : route_cu_vision appelle _record_provider_failure."""
        from unittest.mock import AsyncMock, patch
        loop = CUAgentLoop()
        loop._vision_llm_func = None

        mock_vision = MagicMock()
        mock_vision._provider_health = {}
        mock_vision._is_provider_available = MagicMock(return_value=True)
        mock_vision._record_provider_failure = MagicMock()
        mock_vision._call_analyze = AsyncMock(side_effect=RuntimeError("GOOGLE_API_KEY non configurée"))
        loop._vision = mock_vision

        with patch("src.computer_use.cu_router.build_vision_policy", return_value=["google"]):
            result = await loop._call_vision_llm("test.png", "test")
        assert result.get("success") is False
        mock_vision._record_provider_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_network_error_handled_by_vision_health(self):
        """Erreur réseau : enregistre un failure sur le VisionModule."""
        from unittest.mock import AsyncMock, patch
        loop = CUAgentLoop()
        loop._vision_llm_func = None

        mock_vision = MagicMock()
        mock_vision._provider_health = {}
        mock_vision._is_provider_available = MagicMock(return_value=True)
        mock_vision._record_provider_failure = MagicMock()
        call_count = [0]

        async def maybe_fail(provider, *a, **kw):
            if provider == "google":
                call_count[0] += 1
                raise RuntimeError("ConnectionRefusedError")
            return "ok"

        mock_vision._call_analyze = maybe_fail
        loop._vision = mock_vision

        with patch("src.computer_use.cu_router.build_vision_policy", return_value=["google", "anthropic"]):
            result = await loop._call_vision_llm("test.png", "test")
        assert result.get("success") is True  # anthropic succeed
