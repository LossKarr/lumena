"""Official Google Gemini model contracts added in August 2026."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


GOOGLE_TEXT_MODELS = {
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
}


def test_google_catalog_contracts_and_default_are_stable():
    from src.llm.providers import ProviderType, get_default_model_for_provider, get_model_config

    expected_cost = {
        "gemini-3.6-flash": 1.50,
        "gemini-3.5-flash-lite": 0.30,
        "gemini-3.1-flash-lite": 0.25,
        "gemini-2.5-flash-lite": 0.10,
    }
    for name in GOOGLE_TEXT_MODELS:
        model = get_model_config(name)
        assert model is not None
        assert model.provider == ProviderType.GOOGLE
        assert model.model_id == name
        assert model.context_window == 1_048_576
        assert model.max_output_tokens == 65_536
        assert model.supports_vision is True
        assert model.supports_tools is True
        assert model.supports_image_generation is False
        assert model.cost_per_million_tokens == expected_cost[name]

    assert get_default_model_for_provider("google").name == "gemini-3.5-flash"


def test_google_fallbacks_are_valid_and_ordered():
    from src.llm.providers import AVAILABLE_MODELS, MODEL_SKILLS, get_model_fallbacks

    assert get_model_fallbacks("gemini-3.6-flash")[:2] == [
        "gemini-3.5-flash",
        "gemini-2.5-flash",
    ]
    assert get_model_fallbacks("gemini-3.5-flash-lite")[:2] == [
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash-lite",
    ]
    assert get_model_fallbacks("gemini-3.1-flash-lite")[0] == "gemini-2.5-flash-lite"
    for name in GOOGLE_TEXT_MODELS:
        assert name in MODEL_SKILLS
        assert all(fallback in AVAILABLE_MODELS for fallback in get_model_fallbacks(name))


def test_google_models_are_visible_on_the_right_surfaces_only():
    from web.routes.config import _CONFIG_SCHEMA

    schema = {entry["key"]: entry for entry in _CONFIG_SCHEMA}
    for name in GOOGLE_TEXT_MODELS:
        assert name in schema["LUMENA_DEFAULT_MODEL"]["options"]
        assert name in schema["LUMENA_AGENT_GENERAL_MODEL"]["options"]
        assert name not in schema["LUMENA_BRAIN_IMAGE_GEN"]["options"]

    image_name = "gemini-3.1-flash-lite-image"
    assert image_name in schema["LUMENA_BRAIN_IMAGE_GEN"]["options"]
    assert image_name not in schema["LUMENA_DEFAULT_MODEL"]["options"]


def test_google_natural_aliases_preserve_generic_gemini():
    from src.core_services.agent_service import AgentService

    service = AgentService.__new__(AgentService)
    assert service._match_model_alias("utilise Gemini 3.6 Flash") == "gemini-3.6-flash"
    assert service._match_model_alias("passe sur Gemini 3.5 Lite") == "gemini-3.5-flash-lite"
    assert service._match_model_alias("mets Gemini 3.1 Lite") == "gemini-3.1-flash-lite"
    assert service._match_model_alias("utilise gemini") == "gemini-2.5-flash"


def test_google_sampling_contract_is_model_specific():
    from src.llm.multi_provider import _build_google_generation_config

    for name in ("gemini-3.6-flash", "gemini-3.5-flash-lite"):
        payload = _build_google_generation_config(
            name, temperature=0.7, max_tokens=123, stop=["STOP"]
        )
        assert payload == {"maxOutputTokens": 123, "stopSequences": ["STOP"]}

    legacy = _build_google_generation_config(
        "gemini-3.1-flash-lite", temperature=0.7, max_tokens=123
    )
    assert legacy == {"maxOutputTokens": 123, "temperature": 0.7}


@pytest.mark.asyncio
async def test_google_normal_payload_uses_exact_model_and_omits_sampling(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM

    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [{
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": "ok"}]},
                }],
                "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 1},
            }

    class HTTP:
        async def post(self, url, json=None):
            captured["url"] = url
            captured["payload"] = dict(json)
            return Response()

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    llm = MultiProviderLLM(model_name="gemini-3.6-flash")
    llm._http = HTTP()
    result = await llm._chat_google_result(
        [{"role": "user", "content": "hello"}],
        model="gemini-3.6-flash",
        temperature=0.7,
        max_tokens=123,
    )

    assert result["text"] == "ok"
    assert "/models/gemini-3.6-flash:generateContent" in captured["url"]
    assert captured["payload"]["generationConfig"] == {"maxOutputTokens": 123}


@pytest.mark.asyncio
async def test_google_stream_payload_uses_same_sampling_contract(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM

    captured = {}

    class StreamResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield 'data: {"candidates":[{"content":{"parts":[{"text":"ok"}]}}]}'

    class HTTP:
        def stream(self, method, url, json=None):
            captured["url"] = url
            captured["payload"] = dict(json)
            return StreamResponse()

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    llm = MultiProviderLLM(model_name="gemini-3.5-flash-lite")
    llm._http = HTTP()
    chunks = [
        chunk
        async for chunk in llm._stream_google(
            [{"role": "user", "content": "hello"}], 0.7, 123
        )
    ]

    assert chunks == ["ok"]
    assert "/models/gemini-3.5-flash-lite:streamGenerateContent" in captured["url"]
    assert captured["payload"]["generationConfig"] == {"maxOutputTokens": 123}


@pytest.mark.asyncio
async def test_google_lite_image_generation_and_explicit_edit_use_selected_model(
    monkeypatch, tmp_path
):
    from src.services.image_gen import ImageGenService, _MODEL_CATALOG, _MODEL_PROVIDER

    model = "gemini-3.1-flash-lite-image"
    assert _MODEL_PROVIDER[model] == "gemini"
    assert "image-edit" in _MODEL_CATALOG[model].capabilities

    service = ImageGenService.get_instance()
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [{
                    "content": {"parts": [{
                        "inlineData": {"mimeType": "image/png", "data": "aW1n"}
                    }]}
                }]
            }

    class HTTP:
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["payload"] = dict(json)
            return Response()

    async def get_client():
        return HTTP()

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    monkeypatch.setattr(service, "_get_client", get_client)
    data, *_ = await service._generate_gemini(
        "hello", model=model, size="1024x1024", quality="hd", style=""
    )
    assert data == b"img"
    assert f"/models/{model}:generateContent" in captured["url"]

    image_path = tmp_path / "source.png"
    image_path.write_bytes(b"source")
    generate = AsyncMock(return_value=(b"edited", "png", 1024, 1024, 0.0, None))
    monkeypatch.setattr(service, "_generate_gemini", generate)
    result = await service.edit(str(image_path), "change it", model=model)
    assert result.model == model
    assert generate.await_args.kwargs["model"] == model

