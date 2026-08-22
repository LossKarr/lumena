"""Official xAI Grok model contracts added in August 2026."""

from unittest.mock import AsyncMock

import pytest


XAI_TEXT_MODELS = {"grok-4.6", "grok-4.5", "grok-build-0.1"}


def test_xai_catalog_contracts_and_default_are_stable():
    from src.llm.providers import ProviderType, get_default_model_for_provider, get_model_config

    expected = {
        "grok-4.6": (500_000, 131_072, 2.0),
        "grok-4.5": (500_000, 131_072, 2.0),
        "grok-build-0.1": (256_000, 32_768, 1.0),
    }
    for name, (context, output, cost) in expected.items():
        model = get_model_config(name)
        assert model is not None
        assert model.provider == ProviderType.XAI
        assert model.model_id == name
        assert model.context_window == context
        assert model.max_output_tokens == output
        assert model.cost_per_million_tokens == cost
        assert model.supports_vision is True
        assert model.supports_tools is True
        assert model.supports_image_generation is False
        assert "reasoning" in model.capabilities

    assert get_default_model_for_provider("xai").name == "grok-4.3"


def test_xai_fallbacks_retirements_and_scores_are_coherent():
    from src.llm.providers import AVAILABLE_MODELS, MODEL_SKILLS, get_model_fallbacks

    assert get_model_fallbacks("grok-4.6")[:2] == ["grok-4.5", "grok-4.3"]
    assert get_model_fallbacks("grok-4.5")[0] == "grok-4.3"
    assert get_model_fallbacks("grok-build-0.1")[:3] == [
        "grok-4.6", "grok-4.5", "grok-4.3"
    ]
    for name in XAI_TEXT_MODELS:
        assert name in MODEL_SKILLS
        assert all(fallback in AVAILABLE_MODELS for fallback in get_model_fallbacks(name))

    assert AVAILABLE_MODELS["grok-4-1-fast-reasoning"].badge == "Déprécié"
    assert AVAILABLE_MODELS["grok-4-1-fast-non-reasoning"].badge == "Déprécié"
    assert AVAILABLE_MODELS["grok-4.20-0309-reasoning"].context_window == 1_000_000


def test_xai_models_are_visible_on_text_surfaces_not_image_generation():
    from web.routes.config import _CONFIG_SCHEMA

    schema = {entry["key"]: entry for entry in _CONFIG_SCHEMA}
    for name in XAI_TEXT_MODELS:
        assert name in schema["LUMENA_DEFAULT_MODEL"]["options"]
        assert name in schema["LUMENA_AGENT_CODE_MODEL"]["options"]
        assert name not in schema["LUMENA_BRAIN_IMAGE_GEN"]["options"]

    image_name = "grok-imagine-image-2.0"
    assert image_name in schema["LUMENA_BRAIN_IMAGE_GEN"]["options"]
    assert image_name not in schema["LUMENA_DEFAULT_MODEL"]["options"]
    # Retired slug remains selectable only for backwards compatibility; the
    # service mapping lets xAI apply its documented redirect.
    assert "grok-imagine-image-pro" in schema["LUMENA_BRAIN_IMAGE_GEN"]["options"]


def test_xai_natural_aliases_and_retired_aliases_are_safe():
    from src.core_services.agent_service import AgentService

    service = AgentService.__new__(AgentService)
    assert service._match_model_alias("utilise Grok 4.6") == "grok-4.6"
    assert service._match_model_alias("passe sur Grok 4.5") == "grok-4.5"
    assert service._match_model_alias("mets Grok Build") == "grok-build-0.1"
    assert service._match_model_alias("utilise grok") == "grok-4.3"
    assert service._match_model_alias("utilise grok fast") == "grok-4.3"


def test_xai_stop_contract_preserves_grok4_and_covers_build():
    from src.llm.multi_provider import _xai_supports_stop

    assert _xai_supports_stop("grok-4.3") is False
    assert _xai_supports_stop("grok-4.6") is False
    assert _xai_supports_stop("grok-4.20-0309-reasoning") is False
    assert _xai_supports_stop("grok-build-0.1") is False
    assert _xai_supports_stop("legacy-non-reasoning-model") is False
    assert _xai_supports_stop("legacy-chat-model") is True


@pytest.mark.asyncio
async def test_xai_normal_payload_uses_exact_model(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM

    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "ok"},
                }],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            }

    class HTTP:
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["payload"] = dict(json)
            return Response()

    monkeypatch.setenv("XAI_API_KEY", "test-key")
    llm = MultiProviderLLM(model_name="grok-4.6")
    llm._http = HTTP()
    result = await llm._chat_xai_result(
        [{"role": "user", "content": "hello"}],
        model="grok-4.6",
        temperature=0.7,
        max_tokens=123,
    )

    assert result["text"] == "ok"
    assert captured["url"] == "https://api.x.ai/v1/chat/completions"
    assert captured["payload"]["model"] == "grok-4.6"
    assert captured["payload"]["max_tokens"] == 123


@pytest.mark.asyncio
async def test_xai_dispatch_removes_stop_for_new_reasoning_models(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM
    from src.llm.providers import ProviderType

    monkeypatch.setenv("XAI_API_KEY", "test-key")
    llm = MultiProviderLLM(model_name="grok-build-0.1")
    call = AsyncMock(return_value={"text": "ok"})
    monkeypatch.setattr(llm, "_chat_xai_result", call)
    await llm._chat_provider_result_inner(
        ProviderType.XAI,
        [{"role": "user", "content": "hello"}],
        0.7,
        100,
        model="grok-build-0.1",
        stop=["STOP"],
    )
    assert call.await_args.kwargs["stop"] is None


@pytest.mark.asyncio
async def test_xai_image_2_generation_and_edit_contract(monkeypatch, tmp_path):
    from src.services.image_gen import ImageGenService, _MODEL_CATALOG, _MODEL_PROVIDER

    model = "grok-imagine-image-2.0"
    assert _MODEL_PROVIDER[model] == "xai"
    assert _MODEL_CATALOG[model].cost_per_image == 0.02
    assert "image-edit" in _MODEL_CATALOG[model].capabilities
    assert "grok-imagine-image-pro" not in _MODEL_CATALOG

    service = ImageGenService.get_instance()
    calls = []

    class Response:
        def __init__(self, payload=None, content=b""):
            self._payload = payload or {}
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class HTTP:
        async def post(self, url, headers=None, json=None):
            calls.append((url, dict(json)))
            return Response({"data": [{"url": "https://example.test/image.png"}]})

        async def get(self, url):
            return Response(content=b"image")

    async def get_client():
        return HTTP()

    monkeypatch.setenv("XAI_API_KEY", "test-key")
    monkeypatch.setattr(service, "_get_client", get_client)
    generated = await service._generate_xai(
        "hello", model=model, size="1024x1024", quality="hd", style=""
    )
    assert generated[0] == b"image"
    assert generated[4] == 0.02
    assert calls[-1][0] == "https://api.x.ai/v1/images/generations"
    assert calls[-1][1]["model"] == model

    edited = await service._edit_xai(b"source", "change", model=model)
    assert edited[0] == b"image"
    assert calls[-1][0] == "https://api.x.ai/v1/images/edits"
    assert calls[-1][1]["model"] == model
    assert calls[-1][1]["image"]["url"].startswith("data:image/png;base64,")

    source = tmp_path / "source.png"
    source.write_bytes(b"source")
    edit = AsyncMock(return_value=(b"edited", "png", 1024, 1024, 0.02, None))
    monkeypatch.setattr(service, "_edit_xai", edit)
    result = await service.edit(str(source), "change", model=model)
    assert result.model == model
    assert edit.await_args.kwargs["model"] == model
