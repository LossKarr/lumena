"""Regression coverage for the Z.AI text, vision and image integration."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


ZAI_TEXT_MODELS = {
    "glm-5.2", "glm-5.1", "glm-5", "glm-5-turbo", "glm-4.7",
    "glm-4.7-flashx", "glm-4.7-flash", "glm-4.6", "glm-4.5",
    "glm-4.5-x", "glm-4.5-air", "glm-4.5-airx",
    "glm-4-32b-0414-128k", "glm-4.5-flash",
}

ZAI_VISION_MODELS = {
    "glm-5v-turbo", "glm-4.6v", "glm-4.6v-flashx",
    "glm-4.6v-flash", "glm-4.5v", "glm-ocr",
}


def test_zai_text_catalog_matches_documented_models():
    from src.llm.providers import AVAILABLE_MODELS, ProviderType

    assert ZAI_TEXT_MODELS <= set(AVAILABLE_MODELS)
    for model_name in ZAI_TEXT_MODELS:
        model = AVAILABLE_MODELS[model_name]
        assert model.provider == ProviderType.ZAI
        assert model.model_id == model_name
        assert model.supports_vision is False


def test_zai_vision_catalog_matches_documented_models():
    from src.llm.providers import AVAILABLE_MODELS, ProviderType

    assert ZAI_VISION_MODELS <= set(AVAILABLE_MODELS)
    for model_name in ZAI_VISION_MODELS:
        model = AVAILABLE_MODELS[model_name]
        assert model.provider == ProviderType.ZAI
        assert model.supports_vision is True


def test_glm_52_has_documented_long_context_contract():
    from src.llm.providers import AVAILABLE_MODELS

    model = AVAILABLE_MODELS["glm-5.2"]
    assert model.context_window == 1_000_000
    assert model.max_output_tokens == 128_000
    assert model.supports_tools is True
    assert model.cost_per_million_tokens == 1.40


def test_zai_limited_time_free_models_keep_a_nonzero_cost():
    from src.llm.providers import AVAILABLE_MODELS

    assert AVAILABLE_MODELS["glm-5.2"].cost_per_million_tokens > 0
    assert AVAILABLE_MODELS["glm-5.1"].cost_per_million_tokens > 0
    assert AVAILABLE_MODELS["glm-4.7-flash"].cost_per_million_tokens == 0
    assert AVAILABLE_MODELS["glm-4.5-flash"].cost_per_million_tokens == 0


def test_glm_52_falls_back_to_native_zai_models_before_nvidia():
    from src.llm.providers import get_model_fallbacks

    assert get_model_fallbacks("glm-5.2")[:3] == [
        "glm-5.1", "glm-4.7-flashx", "glm-4.7-flash",
    ]


@pytest.mark.asyncio
async def test_describe_image_uses_zai_openai_compatible_vision_payload(tmp_path: Path):
    from src.llm.multi_provider import MultiProviderLLM

    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"png-bytes")
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": "description"}}]}
    client = MagicMock()
    client.post = AsyncMock(return_value=response)

    with patch.dict(os.environ, {"ZAI_API_KEY": "zai-test", "ZAI_BASE_URL": "https://zai.test/v4"}, clear=False):
        with patch("src.llm.providers.get_brain_model", return_value="glm-4.6v-flash"):
            llm = MultiProviderLLM(model_name="glm-5.2")
            llm._http = client
            result = await llm.describe_image(str(image_path), "describe this")

    assert result == "description"
    url = client.post.call_args.args[0]
    payload = client.post.call_args.kwargs["json"]
    assert url == "https://zai.test/v4/chat/completions"
    assert payload["model"] == "glm-4.6v-flash"
    assert payload["messages"][0]["content"][0]["type"] == "image_url"
    assert payload["messages"][0]["content"][1]["text"] == "describe this"


def test_image_models_live_only_in_image_service_not_llm_catalog():
    from src.llm.providers import AVAILABLE_MODELS
    from src.services.image_gen import _MODEL_CATALOG, _MODEL_PROVIDER

    assert "cogview-4" not in AVAILABLE_MODELS
    assert "cogview-4-flash" not in AVAILABLE_MODELS
    assert _MODEL_PROVIDER["cogview-4"] == "zai"
    assert _MODEL_PROVIDER["glm-image"] == "zai"
    assert _MODEL_CATALOG["cogview-4"].cost_per_image == 0.01
    assert _MODEL_CATALOG["glm-image"].cost_per_image == 0.015


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "api_model", "expected_cost"),
    [
        ("cogview-4", "cogview-4-250304", 0.01),
        ("glm-image", "glm-image", 0.015),
    ],
)
async def test_zai_image_generation_uses_documented_api(model: str, api_model: str, expected_cost: float):
    from src.services.image_gen import ImageGenService

    generate_response = MagicMock()
    generate_response.raise_for_status = MagicMock()
    generate_response.json.return_value = {"data": [{"url": "https://cdn.zai.test/image.png"}]}
    image_response = MagicMock()
    image_response.raise_for_status = MagicMock()
    image_response.headers = {"content-type": "image/png"}
    image_response.content = b"png-bytes"
    client = MagicMock()
    client.post = AsyncMock(return_value=generate_response)
    client.get = AsyncMock(return_value=image_response)

    service = ImageGenService()
    service._get_client = AsyncMock(return_value=client)
    with patch.dict(os.environ, {"ZAI_API_KEY": "zai-test", "ZAI_BASE_URL": "https://zai.test/paas/v4"}, clear=False):
        result = await service._generate_zai("orange cube", model=model, size="1280x1280", quality="hd", style="")

    assert result[0] == b"png-bytes"
    assert result[1] == "png"
    assert result[4] == expected_cost
    assert client.post.call_args.args[0] == "https://zai.test/paas/v4/images/generations"
    assert client.post.call_args.kwargs["json"]["model"] == api_model
    assert client.post.call_args.kwargs["headers"]["Authorization"] == "Bearer zai-test"


@pytest.mark.asyncio
async def test_zai_image_generation_rejects_non_image_download():
    from src.services.image_gen import ImageGenError, ImageGenService

    generate_response = MagicMock()
    generate_response.raise_for_status = MagicMock()
    generate_response.json.return_value = {"data": [{"url": "https://cdn.zai.test/not-image"}]}
    download_response = MagicMock()
    download_response.raise_for_status = MagicMock()
    download_response.headers = {"content-type": "text/html"}
    client = MagicMock()
    client.post = AsyncMock(return_value=generate_response)
    client.get = AsyncMock(return_value=download_response)

    service = ImageGenService()
    service._get_client = AsyncMock(return_value=client)
    with patch.dict(os.environ, {"ZAI_API_KEY": "zai-test"}, clear=False):
        with pytest.raises(ImageGenError, match="non-image"):
            await service._generate_zai("orange cube", model="cogview-4", size="1280x1280", quality="hd", style="")


def test_image_auto_fallback_is_free_then_strictly_ascending_cost():
    from src.services.image_gen import _MODEL_CATALOG, _PROVIDER_FALLBACK_ORDER

    costs = [_MODEL_CATALOG[name].cost_per_image for name in _PROVIDER_FALLBACK_ORDER]
    first_paid = next(index for index, cost in enumerate(costs) if cost > 0)
    assert all(cost == 0 for cost in costs[:first_paid])
    assert costs[first_paid:] == sorted(costs[first_paid:])


def test_image_config_exposes_only_real_zai_image_models():
    from web.routes.config import _CONFIG_SCHEMA

    image_setting = next(item for item in _CONFIG_SCHEMA if item["key"] == "LUMENA_BRAIN_IMAGE_GEN")
    assert {"cogview-4", "glm-image"} <= set(image_setting["options"])
    assert "cogview-4-flash" not in image_setting["options"]
