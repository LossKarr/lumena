import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.providers import (
    AVAILABLE_MODELS,
    MODEL_SKILLS,
    ProviderType,
    get_default_model_for_provider,
    get_model_config,
    get_model_fallbacks,
)


NVIDIA_NIM_FREE_MODELS = [
    "nvidia-deepseek-v4-flash",
    "nvidia-gpt-oss-120b",
    "nvidia-step-3.7-flash",
    "nvidia-kimi-k2.6",
    "nvidia-glm-5.1",
    "nvidia-nemotron-3-ultra-550b-a55b",
    "nvidia-minimax-m2.7",
]


@pytest.mark.parametrize("name", NVIDIA_NIM_FREE_MODELS)
def test_nvidia_nim_model_catalog(name):
    cfg = get_model_config(name)
    assert cfg is not None
    assert cfg.provider == ProviderType.NVIDIA
    assert cfg.cost_per_million_tokens == 0.0
    assert name in MODEL_SKILLS


def test_nvidia_default_model_is_fast_free_fallback():
    cfg = get_default_model_for_provider("nvidia")
    assert cfg is not None
    assert cfg.name == "nvidia-deepseek-v4-flash"


def test_model_level_fallbacks():
    assert get_model_fallbacks("deepseek-v4-flash")[0] == "nvidia-deepseek-v4-flash"
    assert get_model_fallbacks("kimi-k2.6")[0] == "nvidia-kimi-k2.6"
    assert get_model_fallbacks("glm-5.1")[0] == "nvidia-glm-5.1"


def test_kimi_vision_but_not_image_generation():
    for name in ("kimi-k2.6", "nvidia-kimi-k2.6"):
        cfg = get_model_config(name)
        assert cfg.supports_vision is True
        assert cfg.supports_image_generation is False
        assert "vision_describe" in cfg.capabilities


def test_nvidia_minimax_not_vision():
    cfg = get_model_config("nvidia-minimax-m2.7")
    assert cfg.supports_vision is False
    assert "vision_describe" not in cfg.capabilities
    assert MODEL_SKILLS["nvidia-minimax-m2.7"]["vision"] == 0


@pytest.mark.asyncio
async def test_chat_provider_result_inner_routes_nvidia():
    from src.llm.multi_provider import MultiProviderLLM

    with patch.object(MultiProviderLLM, "_resolve_ollama_host", return_value="http://localhost:11434"):
        llm = MultiProviderLLM(model_name="nvidia-gpt-oss-120b")
    try:
        llm._chat_nvidia_result = AsyncMock(
            return_value={"text": "ok", "provider_used": "nvidia", "model_used": "openai/gpt-oss-120b"}
        )
        result = await llm._chat_provider_result_inner(
            ProviderType.NVIDIA,
            [{"role": "user", "content": "hi"}],
            temperature=0.1,
            max_tokens=64,
            model="nvidia-gpt-oss-120b",
        )
        assert result["provider_used"] == "nvidia"
        llm._chat_nvidia_result.assert_called_once()
    finally:
        await llm.close()


@pytest.mark.asyncio
async def test_nvidia_chat_resolves_internal_name_to_model_id():
    from src.llm.multi_provider import MultiProviderLLM

    with patch.dict(os.environ, {"NVIDIA_API_KEY": "test"}, clear=False):
        llm = MultiProviderLLM(model_name="nvidia-gpt-oss-120b")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }
        llm._http = AsyncMock()
        llm._http.post = AsyncMock(return_value=mock_response)
        try:
            result = await llm._chat_nvidia_result(
                [{"role": "user", "content": "hi"}],
                model="nvidia-gpt-oss-120b",
                max_tokens=64,
            )
            payload = llm._http.post.call_args.kwargs["json"]
            assert payload["model"] == "openai/gpt-oss-120b"
            assert result["text"] == "ok"
        finally:
            await llm.close()


def test_image_generation_catalog_excludes_kimi():
    from src.services.image_gen import ImageGenService

    names = {m["name"] for m in ImageGenService.get_instance().get_available_models()}
    assert "kimi-k2.6" not in names
    assert "nvidia-kimi-k2.6" not in names
    assert "nvidia-step-3.7-flash" not in names

