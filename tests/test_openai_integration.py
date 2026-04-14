"""
Tests de garde — Plan OpenAI Models Lumena (Phase 0).

Verrouillent la registry, les payloads et le routage vision OpenAI
pour pouvoir refactorer en toute sécurité.
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# ── Registry tests ──────────────────────────────────────────────────────────

class TestOpenAIRegistry:
    """Vérifie la composition du catalogue OpenAI dans AVAILABLE_MODELS."""

    def test_catalogue_principal_present(self):
        from src.llm.providers import AVAILABLE_MODELS, ProviderType
        expected = {"gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-4.1"}
        openai_names = {n for n, c in AVAILABLE_MODELS.items() if c.provider == ProviderType.OPENAI}
        assert expected.issubset(openai_names)

    def test_gpt41_present_and_configured(self):
        from src.llm.providers import AVAILABLE_MODELS
        cfg = AVAILABLE_MODELS["gpt-4.1"]
        assert cfg.supports_vision is True
        assert cfg.supports_tools is True
        assert cfg.badge == "Fallback"
        assert "vision_describe" in cfg.capabilities

    def test_dalle3_removed(self):
        from src.llm.providers import AVAILABLE_MODELS
        assert "dall-e-3" not in AVAILABLE_MODELS

    def test_legacy_models_present(self):
        from src.llm.providers import AVAILABLE_MODELS
        assert "gpt-4o" in AVAILABLE_MODELS
        assert "gpt-4o-mini" in AVAILABLE_MODELS

    def test_reasoning_models_present(self):
        from src.llm.providers import AVAILABLE_MODELS
        assert "o3" in AVAILABLE_MODELS
        assert "o4-mini" in AVAILABLE_MODELS

    def test_reasoning_models_tagged(self):
        from src.llm.providers import AVAILABLE_MODELS
        for name in ("o3", "o4-mini"):
            assert AVAILABLE_MODELS[name].badge == "Reasoning"
            assert "reasoning" in AVAILABLE_MODELS[name].capabilities

    def test_legacy_models_tagged(self):
        from src.llm.providers import AVAILABLE_MODELS
        for name in ("gpt-4o", "gpt-4o-mini"):
            assert AVAILABLE_MODELS[name].badge == "Legacy"

    def test_gpt5_supports_vision(self):
        from src.llm.providers import AVAILABLE_MODELS
        for name in ("gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"):
            assert AVAILABLE_MODELS[name].supports_vision is True

    def test_gpt5_supports_tools(self):
        from src.llm.providers import AVAILABLE_MODELS
        for name in ("gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano"):
            assert AVAILABLE_MODELS[name].supports_tools is True

    def test_reasoning_models_have_vision(self):
        from src.llm.providers import AVAILABLE_MODELS
        for name in ("o3", "o4-mini"):
            assert AVAILABLE_MODELS[name].supports_vision is True

    def test_vision_describe_capability(self):
        from src.llm.providers import AVAILABLE_MODELS
        for name in ("gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "o3", "o4-mini"):
            assert "vision_describe" in AVAILABLE_MODELS[name].capabilities

    def test_model_skills_openai_entries(self):
        from src.llm.providers import MODEL_SKILLS
        expected = {"gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-4o", "gpt-4o-mini", "gpt-4.1", "o3", "o4-mini"}
        for name in expected:
            assert name in MODEL_SKILLS, f"{name} absent de MODEL_SKILLS"
        assert "dall-e-3" not in MODEL_SKILLS

    def test_models_with_capability_returns_openai_vision(self):
        from src.llm.providers import models_with_capability, AVAILABLE_MODELS, ProviderType
        with patch("src.llm.providers.check_api_key", return_value=True):
            result = models_with_capability("vision_describe")
        openai_vision = [n for n in result if AVAILABLE_MODELS[n].provider == ProviderType.OPENAI]
        assert len(openai_vision) >= 3  # au moins gpt-5.4*, o3, o4-mini


# ── GPT-5 detection tests ──────────────────────────────────────────────────

class TestGPT5Detection:
    """Vérifie la détection des modèles GPT-5/reasoning dans multi_provider."""

    def test_gpt5_detected(self):
        from src.llm.multi_provider import MultiProviderLLM
        assert MultiProviderLLM._is_gpt5_model("gpt-5.4") is True
        assert MultiProviderLLM._is_gpt5_model("gpt-5.4-mini") is True
        assert MultiProviderLLM._is_gpt5_model("gpt-5.4-nano") is True

    def test_reasoning_detected_as_gpt5(self):
        from src.llm.multi_provider import MultiProviderLLM
        assert MultiProviderLLM._is_gpt5_model("o3") is True
        assert MultiProviderLLM._is_gpt5_model("o4-mini") is True

    def test_legacy_not_gpt5(self):
        from src.llm.multi_provider import MultiProviderLLM
        assert MultiProviderLLM._is_gpt5_model("gpt-4o") is False
        assert MultiProviderLLM._is_gpt5_model("gpt-4o-mini") is False
        assert MultiProviderLLM._is_gpt5_model("gpt-4.1") is False

    def test_other_providers_not_gpt5(self):
        from src.llm.multi_provider import MultiProviderLLM
        assert MultiProviderLLM._is_gpt5_model("deepseek-v3") is False
        assert MultiProviderLLM._is_gpt5_model("claude-opus-4.6") is False


# ── Payload tests ───────────────────────────────────────────────────────────

class TestOpenAIPayload:
    """Vérifie que les payloads OpenAI sont corrects par famille de modèle."""

    def test_prepare_messages_gpt5_converts_system_to_developer(self):
        from src.llm.multi_provider import MultiProviderLLM
        msgs = [{"role": "system", "content": "Tu es Lumena"}, {"role": "user", "content": "Salut"}]
        result = MultiProviderLLM._prepare_openai_messages(msgs, "gpt-5.4")
        assert result[0]["role"] == "developer"
        assert result[1]["role"] == "user"

    def test_prepare_messages_legacy_keeps_system(self):
        from src.llm.multi_provider import MultiProviderLLM
        msgs = [{"role": "system", "content": "Tu es Lumena"}, {"role": "user", "content": "Salut"}]
        result = MultiProviderLLM._prepare_openai_messages(msgs, "gpt-4o")
        assert result[0]["role"] == "system"

    def test_prepare_messages_reasoning_converts_system(self):
        from src.llm.multi_provider import MultiProviderLLM
        msgs = [{"role": "system", "content": "Hi"}, {"role": "user", "content": "Hello"}]
        result = MultiProviderLLM._prepare_openai_messages(msgs, "o3")
        assert result[0]["role"] == "developer"


# ── Vision payload tests ────────────────────────────────────────────────────

class TestOpenAIVisionPayload:
    """Vérifie que la vision OpenAI construit le bon payload via _build_openai_payload."""

    @pytest.mark.asyncio
    async def test_vision_openai_no_temperature_for_gpt5(self):
        """GPT-5.x : pas de temperature, pas de max_tokens (libre)."""
        from src.computer_use.vision import VisionModule
        from src.llm.providers import ProviderType, ModelConfig
        vm = VisionModule.__new__(VisionModule)

        captured_payload = {}

        async def mock_post(url, json=None, headers=None):
            captured_payload.update(json or {})
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json = lambda: {"choices": [{"message": {"content": "test"}}]}
            return resp

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        fake_models = {
            "gpt-5.4-nano": ModelConfig(
                name="gpt-5.4-nano", display_name="GPT-5.4 Nano", provider=ProviderType.OPENAI,
                model_id="gpt-5.4-nano", supports_vision=True, supports_tools=True,
                capabilities=frozenset({"vision_describe"}),
            )
        }

        with patch("src.llm.providers.models_with_capability", return_value=["gpt-5.4-nano"]), \
             patch("src.llm.providers.AVAILABLE_MODELS", fake_models), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch("os.getenv", return_value="fake-key"), \
             patch.object(vm, "_encode_image_base64", new_callable=AsyncMock, return_value="base64data"):
            result = await vm.analyze_with_openai("/tmp/test.png", "Describe this")

        assert "temperature" not in captured_payload
        assert "max_tokens" not in captured_payload
        assert "max_completion_tokens" not in captured_payload
        assert captured_payload["model"] == "gpt-5.4-nano"
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_vision_openai_has_temperature_for_legacy(self):
        """Les modèles legacy : temperature=0.1, pas de max_tokens (libre)."""
        from src.computer_use.vision import VisionModule
        from src.llm.providers import ProviderType, ModelConfig
        vm = VisionModule.__new__(VisionModule)

        captured_payload = {}

        async def mock_post(url, json=None, headers=None):
            captured_payload.update(json or {})
            resp = MagicMock()
            resp.status_code = 200
            resp.raise_for_status = MagicMock()
            resp.json = lambda: {"choices": [{"message": {"content": "test"}}]}
            return resp

        mock_client = AsyncMock()
        mock_client.post = mock_post
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        fake_models = {
            "gpt-4o": ModelConfig(
                name="gpt-4o", display_name="GPT-4o", provider=ProviderType.OPENAI,
                model_id="gpt-4o", supports_vision=True, supports_tools=True,
                capabilities=frozenset({"vision_describe", "tool_calling"}),
            )
        }

        with patch("src.llm.providers.models_with_capability", return_value=["gpt-4o"]), \
             patch("src.llm.providers.AVAILABLE_MODELS", fake_models), \
             patch("httpx.AsyncClient", return_value=mock_client), \
             patch("os.getenv", return_value="fake-key"), \
             patch.object(vm, "_encode_image_base64", new_callable=AsyncMock, return_value="base64data"):
            result = await vm.analyze_with_openai("/tmp/test.png", "Describe this")

        assert captured_payload["model"] == "gpt-4o"
        assert captured_payload["temperature"] == 0.1
        assert "max_tokens" not in captured_payload
        assert "max_completion_tokens" not in captured_payload
        assert result["success"] is True


# ── _is_reasoning_model tests ──────────────────────────────────────────────

class TestIsReasoningModel:
    """Vérifie la détection des reasoning models (o3, o4-mini)."""

    def test_o3_is_reasoning(self):
        from src.llm.multi_provider import MultiProviderLLM
        assert MultiProviderLLM._is_reasoning_model("o3") is True

    def test_o4_mini_is_reasoning(self):
        from src.llm.multi_provider import MultiProviderLLM
        assert MultiProviderLLM._is_reasoning_model("o4-mini") is True

    def test_gpt5_is_not_reasoning(self):
        from src.llm.multi_provider import MultiProviderLLM
        assert MultiProviderLLM._is_reasoning_model("gpt-5.4") is False
        assert MultiProviderLLM._is_reasoning_model("gpt-5.4-nano") is False

    def test_legacy_is_not_reasoning(self):
        from src.llm.multi_provider import MultiProviderLLM
        assert MultiProviderLLM._is_reasoning_model("gpt-4o") is False
        assert MultiProviderLLM._is_reasoning_model("gpt-4.1") is False

    def test_other_providers_not_reasoning(self):
        from src.llm.multi_provider import MultiProviderLLM
        assert MultiProviderLLM._is_reasoning_model("deepseek-v3") is False
        assert MultiProviderLLM._is_reasoning_model("claude-opus-4.6") is False

    def test_empty_and_none(self):
        from src.llm.multi_provider import MultiProviderLLM
        assert MultiProviderLLM._is_reasoning_model("") is False
        assert MultiProviderLLM._is_reasoning_model(None) is False


# ── _build_openai_payload tests ─────────────────────────────────────────────

class TestBuildOpenAIPayload:
    """Vérifie les 3 profils de _build_openai_payload."""

    def _build(self, model, **kwargs):
        from src.llm.multi_provider import MultiProviderLLM
        msgs = [{"role": "system", "content": "Tu es Lumena"}, {"role": "user", "content": "Salut"}]
        return MultiProviderLLM._build_openai_payload(model, msgs, **kwargs)

    # ── GPT-5 profile ──

    def test_gpt5_uses_max_completion_tokens(self):
        p = self._build("gpt-5.4")
        assert "max_completion_tokens" in p
        assert "max_tokens" not in p

    def test_gpt5_no_temperature(self):
        p = self._build("gpt-5.4", temperature=0.7)
        assert "temperature" not in p

    def test_gpt5_no_stop(self):
        p = self._build("gpt-5.4", stop=["OBSERVATION:"])
        assert "stop" not in p

    def test_gpt5_converts_system_to_developer(self):
        p = self._build("gpt-5.4")
        assert p["messages"][0]["role"] == "developer"

    # ── Reasoning profile (o3/o4-mini) ──

    def test_reasoning_uses_max_completion_tokens(self):
        p = self._build("o3")
        assert "max_completion_tokens" in p
        assert "max_tokens" not in p

    def test_reasoning_no_temperature(self):
        p = self._build("o4-mini", temperature=0.7)
        assert "temperature" not in p

    def test_reasoning_converts_system_to_developer(self):
        p = self._build("o3")
        assert p["messages"][0]["role"] == "developer"

    # ── Legacy profile (gpt-4.1, gpt-4o) ──

    def test_legacy_uses_max_tokens(self):
        p = self._build("gpt-4o")
        assert "max_tokens" in p
        assert "max_completion_tokens" not in p

    def test_legacy_has_temperature(self):
        p = self._build("gpt-4o", temperature=0.5)
        assert p["temperature"] == 0.5

    def test_legacy_has_stop(self):
        p = self._build("gpt-4.1", stop=["OBSERVATION:"])
        assert p["stop"] == ["OBSERVATION:"]

    def test_legacy_keeps_system_role(self):
        p = self._build("gpt-4o")
        assert p["messages"][0]["role"] == "system"

    # ── Paramètres transversaux ──

    def test_stream_flag(self):
        p = self._build("gpt-5.4", stream=True)
        assert p["stream"] is True

    def test_no_stream_by_default(self):
        p = self._build("gpt-5.4")
        assert "stream" not in p

    def test_tools_included(self):
        tools = [{"type": "function", "function": {"name": "test"}}]
        p = self._build("gpt-5.4", tools=tools)
        assert p["tools"] == tools

    def test_no_tools_by_default(self):
        p = self._build("gpt-5.4")
        assert "tools" not in p

    def test_model_name_preserved(self):
        p = self._build("gpt-5.4-nano")
        assert p["model"] == "gpt-5.4-nano"

    # ── max_tokens=None (vision mode) ──

    def test_gpt5_no_max_tokens_when_none(self):
        p = self._build("gpt-5.4", max_tokens=None)
        assert "max_completion_tokens" not in p
        assert "max_tokens" not in p

    def test_legacy_no_max_tokens_when_none(self):
        p = self._build("gpt-4o", max_tokens=None)
        assert "max_tokens" not in p
        assert "max_completion_tokens" not in p
        assert "temperature" in p  # temperature still present for legacy
