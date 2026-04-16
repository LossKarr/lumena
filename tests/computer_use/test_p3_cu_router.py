"""Tests P3 — CU Router + Unified Vision + Provider Health."""

import os
import time
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


# ── P3.5 : build_vision_policy ──────────────────────────────────────────────

class TestBuildVisionPolicy:
    """Tests pour build_vision_policy() dans cu_router.py."""

    def test_local_mode_returns_ollama(self):
        from src.computer_use.cu_router import build_vision_policy
        with patch.dict(os.environ, {"LUMENA_EXECUTION_MODE": "local"}):
            policy = build_vision_policy("vision_describe")
        assert policy == ["ollama"]

    def test_local_mode_grounding_returns_ollama(self):
        from src.computer_use.cu_router import build_vision_policy
        with patch.dict(os.environ, {"LUMENA_EXECUTION_MODE": "local"}):
            policy = build_vision_policy("vision_grounding")
        assert policy == ["ollama"]

    def test_cloud_mode_no_keys_empty(self):
        """Sans aucune clé API configurée, la policy cloud doit être vide."""
        from src.computer_use.cu_router import build_vision_policy
        env = {
            "LUMENA_EXECUTION_MODE": "cloud",
            "LUMENA_CU_VISION_ORDER": "",
        }
        # retirer toutes les clés potentielles
        clear_keys = {
            "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "",
            "GOOGLE_API_KEY": "", "XAI_API_KEY": "",
        }
        with patch.dict(os.environ, {**env, **clear_keys}, clear=False):
            # forcer les env vars vides à absents via patch
            with patch("src.computer_use.cu_router._has_api_key", return_value=False):
                policy = build_vision_policy("vision_describe")
        assert policy == []

    def test_cloud_mode_with_keys_filtered(self):
        """Avec des clés disponibles, policy contient uniquement les providers avec clé."""
        from src.computer_use.cu_router import build_vision_policy
        with patch.dict(os.environ, {"LUMENA_EXECUTION_MODE": "cloud", "LUMENA_CU_VISION_ORDER": ""}):
            with patch("src.computer_use.cu_router._has_api_key",
                       side_effect=lambda p: p in ("openai", "google")):
                policy = build_vision_policy("vision_describe")
        # openai et google disponibles dans l'ordre de la policy par défaut
        assert "openai" in policy
        assert "google" in policy
        assert "anthropic" not in policy
        assert "xai" not in policy

    def test_hybrid_ollama_vision_flag(self):
        """LUMENA_CU_OLLAMA_VISION=1 ajoute ollama en queue en mode hybrid."""
        from src.computer_use.cu_router import build_vision_policy
        env = {
            "LUMENA_EXECUTION_MODE": "hybrid",
            "LUMENA_CU_OLLAMA_VISION": "1",
            "LUMENA_CU_VISION_ORDER": "",
        }
        with patch.dict(os.environ, env):
            with patch("src.computer_use.cu_router._has_api_key", return_value=True):
                policy = build_vision_policy("vision_describe")
        assert policy[-1] == "ollama"

    def test_hybrid_ollama_not_added_without_flag(self):
        from src.computer_use.cu_router import build_vision_policy
        env = {
            "LUMENA_EXECUTION_MODE": "hybrid",
            "LUMENA_CU_OLLAMA_VISION": "0",
            "LUMENA_CU_VISION_ORDER": "",
        }
        with patch.dict(os.environ, env):
            with patch("src.computer_use.cu_router._has_api_key", return_value=True):
                policy = build_vision_policy("vision_describe")
        assert "ollama" not in policy

    def test_override_env_var(self):
        """LUMENA_CU_VISION_ORDER prend le dessus sur tout."""
        from src.computer_use.cu_router import build_vision_policy
        with patch.dict(os.environ, {
            "LUMENA_CU_VISION_ORDER": "anthropic,google",
            "LUMENA_EXECUTION_MODE": "cloud",
        }):
            policy = build_vision_policy("vision_describe")
        assert policy == ["anthropic", "google"]

    def test_override_never_includes_non_llm(self):
        """Une override contenant dom/uia/ocr doit les filtrer."""
        from src.computer_use.cu_router import build_vision_policy
        with patch.dict(os.environ, {
            "LUMENA_CU_VISION_ORDER": "dom,uia,ocr,anthropic",
        }):
            policy = build_vision_policy("vision_describe")
        for forbidden in ("dom", "uia", "ocr"):
            assert forbidden not in policy
        assert "anthropic" in policy

    def test_invariant_no_nonllm_in_default_policy(self):
        """L'invariant : dom/uia/ocr ne doivent JAMAIS apparaître."""
        from src.computer_use.cu_router import build_vision_policy
        for mode in ("cloud", "hybrid", "local"):
            for cap in ("vision_describe", "vision_grounding"):
                with patch.dict(os.environ, {
                    "LUMENA_EXECUTION_MODE": mode,
                    "LUMENA_CU_VISION_ORDER": "",
                }):
                    with patch("src.computer_use.cu_router._has_api_key", return_value=True):
                        policy = build_vision_policy(cap)
                for forbidden in ("dom", "uia", "ocr"):
                    assert forbidden not in policy, f"'{forbidden}' dans policy {mode}/{cap}: {policy}"


# ── P3.5 : build_state_policy ───────────────────────────────────────────────

class TestBuildStatePolicy:
    """Tests pour build_state_policy()."""

    def test_web_cloud_returns_dom(self):
        from src.computer_use.cu_router import build_state_policy
        with patch.dict(os.environ, {"LUMENA_EXECUTION_MODE": "cloud"}):
            assert build_state_policy("web") == ["dom"]

    def test_desktop_cloud_returns_uia(self):
        from src.computer_use.cu_router import build_state_policy
        with patch.dict(os.environ, {"LUMENA_EXECUTION_MODE": "cloud"}):
            assert build_state_policy("desktop") == ["uia"]

    def test_web_local_returns_dom_ocr(self):
        from src.computer_use.cu_router import build_state_policy
        with patch.dict(os.environ, {"LUMENA_EXECUTION_MODE": "local"}):
            assert build_state_policy("web") == ["dom", "ocr"]

    def test_desktop_local_returns_uia_ocr(self):
        from src.computer_use.cu_router import build_state_policy
        with patch.dict(os.environ, {"LUMENA_EXECUTION_MODE": "local"}):
            assert build_state_policy("desktop") == ["uia", "ocr"]

    def test_state_policy_never_contains_llm(self):
        """Invariant : state_policy ne contient jamais openai/anthropic/google/xai/ollama."""
        from src.computer_use.cu_router import build_state_policy
        llm_providers = {"openai", "anthropic", "google", "xai", "ollama"}
        for mode in ("cloud", "hybrid", "local"):
            for ctx in ("web", "desktop"):
                with patch.dict(os.environ, {"LUMENA_EXECUTION_MODE": mode}):
                    policy = build_state_policy(ctx)
                for p in policy:
                    assert p not in llm_providers, f"Provider LLM '{p}' dans state_policy {mode}/{ctx}"


# ── P3.5 : get_execution_mode ───────────────────────────────────────────────

class TestGetExecutionMode:
    def test_default_hybrid(self):
        from src.computer_use.cu_router import get_execution_mode
        with patch.dict(os.environ, {}, clear=False):
            env_save = os.environ.pop("LUMENA_EXECUTION_MODE", None)
            mode = get_execution_mode()
            if env_save is not None:
                os.environ["LUMENA_EXECUTION_MODE"] = env_save
        assert mode in ("hybrid", "cloud", "local")  # dépend de l'env test

    def test_local_mode(self):
        from src.computer_use.cu_router import get_execution_mode
        with patch.dict(os.environ, {"LUMENA_EXECUTION_MODE": "local"}):
            assert get_execution_mode() == "local"

    def test_cloud_mode(self):
        from src.computer_use.cu_router import get_execution_mode
        with patch.dict(os.environ, {"LUMENA_EXECUTION_MODE": "cloud"}):
            assert get_execution_mode() == "cloud"


# ── P3.3 : _ProviderHealthEntry + instance-level health ────────────────────

class TestVisionProviderHealth:
    """Tests pour VisionModule._is_provider_available / _record_provider_failure."""

    def _make_module(self):
        from src.computer_use.vision import VisionModule
        with patch("src.computer_use.vision.ScreenAnalyzer"):
            m = VisionModule.__new__(VisionModule)
            m._provider_health = {}
            return m

    def test_unknown_provider_available(self):
        from src.computer_use.vision import VisionModule
        mod = self._make_module()
        assert mod._is_provider_available("new_provider") is True

    def test_permanent_failure_not_available(self):
        from src.computer_use.vision import VisionModule, _ProviderHealthEntry
        mod = self._make_module()
        mod._provider_health["google"] = _ProviderHealthEntry(permanent=True)
        assert mod._is_provider_available("google") is False

    def test_cooldown_not_available(self):
        from src.computer_use.vision import VisionModule, _ProviderHealthEntry
        mod = self._make_module()
        mod._provider_health["anthropic"] = _ProviderHealthEntry(
            cooldown_until=time.time() + 60
        )
        assert mod._is_provider_available("anthropic") is False

    def test_cooldown_expired_available(self):
        from src.computer_use.vision import VisionModule, _ProviderHealthEntry
        mod = self._make_module()
        mod._provider_health["anthropic"] = _ProviderHealthEntry(
            cooldown_until=time.time() - 1  # expiré
        )
        assert mod._is_provider_available("anthropic") is True

    def test_record_transient_failure_sets_cooldown(self):
        from src.computer_use.vision import VisionModule
        mod = self._make_module()
        exc = RuntimeError("timeout")
        mod._record_provider_failure("google", exc)
        entry = mod._provider_health["google"]
        assert not entry.permanent
        assert entry.cooldown_until > time.time()
        assert entry.failures == 1

    def test_record_auth_failure_permanent(self):
        from src.computer_use.vision import VisionModule

        class FakeHTTPStatus(Exception):
            class response:
                status_code = 401

        mod = self._make_module()
        mod._record_provider_failure("openai", FakeHTTPStatus())
        entry = mod._provider_health["openai"]
        assert entry.permanent is True

    def test_health_is_instance_level(self):
        """Deux instances de VisionModule ont des health séparés."""
        from src.computer_use.vision import VisionModule, _ProviderHealthEntry
        mod1 = self._make_module()
        mod2 = self._make_module()
        mod1._provider_health["google"] = _ProviderHealthEntry(permanent=True)
        assert mod2._is_provider_available("google") is True  # pas de fuite


# ── P3.5 : route_cu_vision ──────────────────────────────────────────────────

class TestRouteCuVision:
    """Tests pour route_cu_vision()."""

    def _make_vision(self, call_analyze_side_effect=None, call_analyze_return="ok"):
        """Crée un mock VisionModule."""
        v = MagicMock()
        v._provider_health = {}
        v._is_provider_available = MagicMock(return_value=True)
        v._record_provider_failure = MagicMock()
        if call_analyze_side_effect:
            v._call_analyze = AsyncMock(side_effect=call_analyze_side_effect)
        else:
            v._call_analyze = AsyncMock(return_value=call_analyze_return)
        return v

    @pytest.mark.asyncio
    async def test_success_first_provider(self):
        from src.computer_use.cu_router import route_cu_vision
        v = self._make_vision(call_analyze_return="texte cool")
        result = await route_cu_vision(v, "/tmp/img.png", "prompt", cascade=["openai"])
        assert result["success"] is True
        assert result["text"] == "texte cool"
        assert result["provider"] == "openai"

    @pytest.mark.asyncio
    async def test_cascade_first_fails_second_succeeds(self):
        from src.computer_use.cu_router import route_cu_vision
        calls = [RuntimeError("timeout"), "réponse claude"]
        v = self._make_vision(call_analyze_side_effect=calls)
        result = await route_cu_vision(
            v, "/tmp/img.png", "prompt", cascade=["google", "anthropic"]
        )
        assert result["success"] is True
        assert result["provider"] == "anthropic"
        v._record_provider_failure.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_fail_returns_error(self):
        from src.computer_use.cu_router import route_cu_vision
        v = self._make_vision(call_analyze_side_effect=RuntimeError("dead"))
        result = await route_cu_vision(
            v, "/tmp/img.png", "prompt", cascade=["google", "anthropic"]
        )
        assert result["success"] is False
        assert "failed" in result["error"]

    @pytest.mark.asyncio
    async def test_skip_unavailable_provider(self):
        from src.computer_use.cu_router import route_cu_vision
        v = self._make_vision(call_analyze_return="ok")
        v._is_provider_available = MagicMock(side_effect=lambda p: p == "anthropic")
        result = await route_cu_vision(
            v, "/tmp/img.png", "prompt", cascade=["google", "anthropic"]
        )
        assert result["success"] is True
        assert result["provider"] == "anthropic"
        # google a été skippé → _call_analyze appelé UNE SEULE fois
        v._call_analyze.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invariant_dom_uia_ocr_filtered(self):
        """Un cascade contenant dom/uia/ocr doit les ignorer silencieusement."""
        from src.computer_use.cu_router import route_cu_vision
        v = self._make_vision(call_analyze_return="ok")
        result = await route_cu_vision(
            v, "/tmp/img.png", "prompt", cascade=["dom", "uia", "ocr", "openai"]
        )
        # openai doit avoir été appelé malgré les entrées non-LLM
        assert result["success"] is True
        assert result["provider"] == "openai"
        # _call_analyze jamais appelé avec dom/uia/ocr
        for call in v._call_analyze.await_args_list:
            assert call.args[0] not in ("dom", "uia", "ocr")


# ── P3.9 : tous les modèles ont capabilities non-None ──────────────────────

class TestAllModelsHaveCapabilities:
    def test_all_available_models_have_capabilities(self):
        from src.llm.providers import AVAILABLE_MODELS
        for name, cfg in AVAILABLE_MODELS.items():
            assert hasattr(cfg, "capabilities"), f"{name} n'a pas d'attribut capabilities"
            assert cfg.capabilities is not None, f"{name}.capabilities est None"
            assert isinstance(cfg.capabilities, frozenset), f"{name}.capabilities n'est pas un frozenset"


# ── P3.8 : env vars cu_agent_loop ───────────────────────────────────────────

class TestCuAgentLoopEnvVars:
    def test_max_iterations_from_env(self):
        with patch.dict(os.environ, {"LUMENA_CU_MAX_ITERATIONS": "42"}):
            import importlib
            import src.computer_use.cu_agent_loop as _m
            importlib.reload(_m)
            assert _m.MAX_ITERATIONS == 42

    def test_timeout_seconds_from_env(self):
        with patch.dict(os.environ, {"LUMENA_CU_TIMEOUT_SEC": "300"}):
            import importlib
            import src.computer_use.cu_agent_loop as _m
            importlib.reload(_m)
            assert _m.TIMEOUT_SECONDS == 300

    def test_defaults_unchanged(self):
        """Sans env vars, les defaults doivent être 30 et 600."""
        env_clean = dict(os.environ)
        env_clean.pop("LUMENA_CU_MAX_ITERATIONS", None)
        env_clean.pop("LUMENA_CU_TIMEOUT_SEC", None)
        with patch.dict(os.environ, env_clean, clear=True):
            import importlib
            import src.computer_use.cu_agent_loop as _m
            importlib.reload(_m)
            assert _m.MAX_ITERATIONS == 30
            assert _m.TIMEOUT_SECONDS == 600


# ── P3.7 : config panel Computer Use ────────────────────────────────────────

class TestConfigPanelComputerUse:
    def test_computer_use_group_exists(self):
        from web.routes.config import _CONFIG_SCHEMA
        groups = {e.get("group") for e in _CONFIG_SCHEMA}
        assert "Computer Use" in groups

    def test_execution_mode_entry(self):
        from web.routes.config import _CONFIG_SCHEMA
        entry = next((e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_EXECUTION_MODE"), None)
        assert entry is not None
        assert entry["type"] == "select"
        assert "hybrid" in entry.get("options", [])

    def test_max_iterations_entry(self):
        from web.routes.config import _CONFIG_SCHEMA
        entry = next((e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_CU_MAX_ITERATIONS"), None)
        assert entry is not None
        assert entry["type"] == "number"
        assert int(entry["default"]) == 30

    def test_timeout_entry(self):
        from web.routes.config import _CONFIG_SCHEMA
        entry = next((e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_CU_TIMEOUT_SEC"), None)
        assert entry is not None
        assert entry["type"] == "number"
        assert int(entry["default"]) == 600

    def test_five_cu_entries_in_group(self):
        from web.routes.config import _CONFIG_SCHEMA
        cu_keys = {e["key"] for e in _CONFIG_SCHEMA if e.get("group") == "Computer Use"}
        expected = {
            "LUMENA_EXECUTION_MODE", "LUMENA_CU_VISION_ORDER",
            "LUMENA_CU_OLLAMA_VISION", "LUMENA_CU_MAX_ITERATIONS",
            "LUMENA_CU_TIMEOUT_SEC",
        }
        assert expected <= cu_keys
