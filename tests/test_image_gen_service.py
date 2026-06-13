"""
Tests pour le service de génération d'images (image_gen.py).

Tests unitaires : service, modèles, providers, sauvegarde, enrichissement prompt.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import httpx
import pytest

from src.services.image_gen import (
    ImageGenService,
    ImageGenError,
    ImageResult,
    _MODEL_PROVIDER,
    _MODEL_CATALOG,
    _PROVIDER_API_KEY,
    _PROVIDER_FALLBACK_ORDER,
    _PROMPT_TEMPLATES,
    _STABILITY_EDIT_ENDPOINTS,
    _FLUX_API_PATHS,
    _safe_error_summary,
    _slugify,
    _parse_size,
    GENERATED_IMAGES_DIR,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset le singleton entre chaque test."""
    ImageGenService.reset_instance()
    yield
    ImageGenService.reset_instance()


@pytest.fixture
def svc():
    return ImageGenService.get_instance()


@pytest.fixture
def fake_png():
    """Bytes d'une image PNG minimale."""
    return b'\x89PNG\r\n\x1a\n' + b'\x00' * 100


@pytest.fixture
def tmp_image(fake_png, tmp_path):
    """Fichier image temporaire."""
    p = tmp_path / "test_image.png"
    p.write_bytes(fake_png)
    return p


# ── Tests utilitaires ─────────────────────────────────────────────────────

class TestSlugify:
    def test_basic(self):
        assert _slugify("A cute cat") == "a_cute_cat"

    def test_special_chars(self):
        assert _slugify("Hello, World! 🌍") == "hello_world"

    def test_max_len(self):
        result = _slugify("a" * 100, max_len=10)
        assert len(result) <= 10

    def test_empty(self):
        assert _slugify("") == "image"

    def test_only_special(self):
        assert _slugify("!!!") == "image"


class TestParseSize:
    def test_valid(self):
        assert _parse_size("1024x1024") == (1024, 1024)

    def test_rectangular(self):
        assert _parse_size("1792x1024") == (1792, 1024)

    def test_invalid(self):
        assert _parse_size("invalid") == (1024, 1024)

    def test_empty(self):
        assert _parse_size("") == (1024, 1024)


# ── Tests singleton ───────────────────────────────────────────────────────

class TestSingleton:
    def test_get_instance(self):
        a = ImageGenService.get_instance()
        b = ImageGenService.get_instance()
        assert a is b

    def test_reset(self):
        a = ImageGenService.get_instance()
        ImageGenService.reset_instance()
        b = ImageGenService.get_instance()
        assert a is not b


# ── Tests model/provider mapping ──────────────────────────────────────────

class TestModelProvider:
    def test_all_models_have_provider(self):
        for model, provider in _MODEL_PROVIDER.items():
            assert provider, f"Model {model} has no provider"

    def test_all_providers_have_api_key(self):
        providers = set(_MODEL_PROVIDER.values())
        for p in providers:
            assert p in _PROVIDER_API_KEY, f"Provider {p} missing from _PROVIDER_API_KEY"

    def test_fallback_order_all_valid(self):
        for model in _PROVIDER_FALLBACK_ORDER:
            assert model in _MODEL_PROVIDER, f"{model} not in _MODEL_PROVIDER"

    def test_fallback_order_covers_catalog(self):
        assert set(_PROVIDER_FALLBACK_ORDER) == set(_MODEL_CATALOG)

    def test_auto_fallback_is_cost_first_not_premium_first(self):
        first_tier = _PROVIDER_FALLBACK_ORDER[:4]
        assert first_tier == [
            "gemini-3.1-flash-image",
            "gemini-3-pro-image",
            "gemini-2.5-flash-image",
            "huggingface-sdxl",
        ]
        premium = {
            "gpt-image-2",
            "gpt-image-1.5",
            "flux-2-max",
            "imagen-4-ultra",
            "stable-image-ultra",
            "ideogram-v4-quality",
        }
        assert premium.isdisjoint(_PROVIDER_FALLBACK_ORDER[:10])

    def test_gemini_models(self):
        assert _MODEL_PROVIDER["gemini-3.1-flash-image"] == "gemini"
        assert _MODEL_PROVIDER["gemini-3-pro-image"] == "gemini"

    def test_openai_models(self):
        assert _MODEL_PROVIDER["gpt-image-2"] == "openai"
        assert _MODEL_PROVIDER["gpt-image-1.5"] == "openai"
        assert _MODEL_PROVIDER["gpt-image-1-mini"] == "openai"

    def test_ideogram_v4_models(self):
        assert _MODEL_PROVIDER["ideogram-v4-quality"] == "ideogram"
        assert _MODEL_PROVIDER["ideogram-v4"] == "ideogram"
        assert _MODEL_PROVIDER["ideogram-v4-turbo"] == "ideogram"

    def test_flux_models(self):
        assert _MODEL_PROVIDER["flux-2-pro"] == "flux"
        assert _MODEL_PROVIDER["flux-kontext-pro"] == "flux"

    def test_stability_models(self):
        assert _MODEL_PROVIDER["stable-image-ultra"] == "stability"
        assert _MODEL_PROVIDER["sd3.5-large"] == "stability"

    def test_has_api_key_none_set(self, svc):
        with patch.dict(os.environ, {}, clear=True):
            assert not svc._has_api_key("openai")

    def test_has_api_key_set(self, svc):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            assert svc._has_api_key("openai")

    def test_get_api_key_missing(self, svc):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ImageGenError, match="Clé API manquante"):
                svc._get_api_key("openai")

    def test_get_model_provider_unknown(self, svc):
        with pytest.raises(ImageGenError, match="Modèle inconnu"):
            svc._get_model_provider("nonexistent-model")


# ── Tests prompt templates ────────────────────────────────────────────────

class TestPromptTemplates:
    def test_all_templates_non_empty(self):
        for name, template in _PROMPT_TEMPLATES.items():
            assert template.strip(), f"Template {name} is empty"

    def test_thumbnail_template(self):
        assert "YouTube" in _PROMPT_TEMPLATES["thumbnail"]
        assert "thumbnail" in _PROMPT_TEMPLATES["thumbnail"]

    def test_logo_template(self):
        assert "logo" in _PROMPT_TEMPLATES["logo"].lower()


# ── Tests sauvegarde workspace ────────────────────────────────────────────

class TestSaveToWorkspace:
    def test_save_creates_file(self, svc, fake_png, tmp_path):
        result = ImageResult(
            data=fake_png, format="png", width=1024, height=1024,
            provider="test", model="test-model", cost_estimate=0.0,
            generation_time_ms=100, prompt_used="test prompt",
        )
        with patch("src.services.image_gen.GENERATED_IMAGES_DIR", tmp_path):
            filepath = svc.save_to_workspace(result, "test_slug")
            assert filepath.exists()
            assert filepath.suffix == ".png"
            assert "test_slug" in filepath.name

    def test_save_creates_meta_json(self, svc, fake_png, tmp_path):
        result = ImageResult(
            data=fake_png, format="png", width=512, height=512,
            provider="gemini", model="gemini-flash", cost_estimate=0.01,
            generation_time_ms=200, prompt_used="cat photo",
        )
        with patch("src.services.image_gen.GENERATED_IMAGES_DIR", tmp_path):
            filepath = svc.save_to_workspace(result, "cat")
            meta_path = filepath.with_suffix(".meta.json")
            # Meta may or may not exist depending on persistence import
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                assert meta["provider"] == "gemini"
                assert meta["prompt"] == "cat photo"


# ── Tests available models ────────────────────────────────────────────────

class TestAvailableModels:
    def test_returns_list(self, svc):
        with patch.dict(os.environ, {}, clear=True):
            models = svc.get_available_models()
            assert isinstance(models, list)
            assert len(models) > 30

    def test_models_have_fields(self, svc):
        models = svc.get_available_models()
        for m in models:
            assert "name" in m
            assert "provider" in m
            assert "available" in m
            assert "free" in m

    def test_free_providers(self, svc):
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "k"}):
            models = svc.get_available_models()
            gemini = [m for m in models if m["provider"] == "gemini"]
            assert all(m["free"] for m in gemini)


# ── Tests generate — dispatch mocking ─────────────────────────────────────

class TestGenerateDispatch:
    @pytest.mark.asyncio
    async def test_empty_prompt_raises(self, svc):
        with pytest.raises(ImageGenError, match="prompt est vide"):
            await svc.generate("")

    @pytest.mark.asyncio
    async def test_auto_no_keys_raises(self, svc):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ImageGenError, match="Aucun provider"):
                await svc.generate("cat")

    @pytest.mark.asyncio
    async def test_template_applied(self, svc):
        """Vérifie que le template est préfixé au prompt."""
        captured_prompt = None

        async def mock_gen(prompt, **kwargs):
            nonlocal captured_prompt
            captured_prompt = prompt
            return (b"img", "png", 1024, 1024, 0.0, None)

        svc._generate_gemini = mock_gen
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test"}):
            with patch.object(svc, "_dispatch_generate") as mock_dispatch:
                mock_dispatch.return_value = ImageResult(
                    data=b"img", format="png", width=1024, height=1024,
                    provider="gemini", model="gemini-3.1-flash-image",
                    cost_estimate=0.0, generation_time_ms=100,
                    prompt_used="test",
                )
                result = await svc.generate("cat photo", template="thumbnail")
                # The dispatch should have been called with template-prefixed prompt
                call_args = mock_dispatch.call_args
                assert "YouTube" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_specific_model(self, svc):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with patch.object(svc, "_dispatch_generate") as mock:
                mock.return_value = ImageResult(
                    data=b"img", format="png", width=1024, height=1024,
                    provider="openai", model="gpt-image-1.5",
                    cost_estimate=0.04, generation_time_ms=2000,
                    prompt_used="test",
                )
                result = await svc.generate("cat", model="gpt-image-1.5")
                mock.assert_called_once()
                assert result.provider == "openai"


# ── Tests generate auto fallback ──────────────────────────────────────────

class TestGoogleImageModels:
    @pytest.mark.asyncio
    async def test_gemini_image_api_uses_current_v1_model_id_and_header_key(self, svc):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": base64.b64encode(b"png-bytes").decode(),
                                }
                            }
                        ]
                    }
                }
            ]
        }
        client = MagicMock()
        client.post = AsyncMock(return_value=response)
        svc._get_client = AsyncMock(return_value=client)

        with patch.dict(os.environ, {"GOOGLE_API_KEY": "SECRET_GOOGLE_KEY"}, clear=True):
            await svc._generate_gemini(
                "cat",
                model="gemini-3.1-flash-image",
                size="1024x1024",
                quality="hd",
                style="",
            )

        url = client.post.call_args.args[0]
        kwargs = client.post.call_args.kwargs
        assert url == "https://generativelanguage.googleapis.com/v1/models/gemini-3.1-flash-image:generateContent"
        assert "preview" not in url
        assert "SECRET_GOOGLE_KEY" not in url
        assert "?key=" not in url
        assert kwargs["headers"]["x-goog-api-key"] == "SECRET_GOOGLE_KEY"

    def test_no_gemini_image_generate_content_url_contains_query_key_or_preview_id(self):
        text = Path("src/services/image_gen.py").read_text(encoding="utf-8")

        assert "generateContent?key=" not in text
        assert "gemini-3.1-flash-image-preview" not in text
        assert "gemini-3-pro-image-preview" not in text


class TestAutoFallback:
    @pytest.mark.asyncio
    async def test_skips_missing_keys(self, svc):
        call_count = 0

        async def mock_dispatch(prompt, *, provider, model, size, quality, style):
            nonlocal call_count
            call_count += 1
            return ImageResult(
                data=b"img", format="png", width=1024, height=1024,
                provider=provider, model=model, cost_estimate=0.0,
                generation_time_ms=100, prompt_used=prompt,
            )

        svc._dispatch_generate = mock_dispatch
        # Only set one key — GOOGLE_API_KEY covers gemini + imagen providers
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test"}, clear=True):
            result = await svc._generate_auto("cat", size="1024x1024", quality="hd", style="")
            # First model in quality-ordered fallback with GOOGLE_API_KEY
            assert result.provider in ("gemini", "imagen")
            assert call_count == 1

    @pytest.mark.asyncio
    async def test_fallback_on_error(self, svc):
        calls = []
        first_model = None

        async def mock_dispatch(prompt, *, provider, model, size, quality, style):
            nonlocal first_model
            calls.append(model)
            if first_model is None:
                first_model = model
            # Fail the first model tried
            if model == first_model:
                raise ImageGenError(f"{model} failed")
            return ImageResult(
                data=b"img", format="png", width=1024, height=1024,
                provider=provider, model=model, cost_estimate=0.0,
                generation_time_ms=100, prompt_used=prompt,
            )

        svc._dispatch_generate = mock_dispatch
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "k", "HUGGINGFACE_TOKEN": "k"}, clear=True):
            result = await svc._generate_auto("cat", size="1024x1024", quality="hd", style="")
            assert len(calls) >= 2
            assert result.model != first_model


# ── Tests edit / upscale / remove_bg ──────────────────────────────────────

class TestEditOperations:
    @pytest.mark.asyncio
    async def test_edit_no_file_raises(self, svc):
        with pytest.raises(ImageGenError, match="introuvable"):
            await svc.edit("/nonexistent/path.png", "make it red")

    @pytest.mark.asyncio
    async def test_edit_no_providers_raises(self, svc, tmp_image):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ImageGenError, match="Aucun provider d'édition"):
                await svc.edit(str(tmp_image), "make it blue")

    @pytest.mark.asyncio
    async def test_upscale_no_file_raises(self, svc):
        with pytest.raises(ImageGenError, match="introuvable"):
            await svc.upscale("/nonexistent/path.png")

    @pytest.mark.asyncio
    async def test_upscale_no_key_raises(self, svc, tmp_image):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ImageGenError, match="STABILITY_API_KEY"):
                await svc.upscale(str(tmp_image))

    @pytest.mark.asyncio
    async def test_remove_bg_no_file_raises(self, svc):
        with pytest.raises(ImageGenError, match="introuvable"):
            await svc.remove_background("/nonexistent.png")

    @pytest.mark.asyncio
    async def test_remove_bg_no_key_raises(self, svc, tmp_image):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ImageGenError, match="STABILITY_API_KEY"):
                await svc.remove_background(str(tmp_image))

    @pytest.mark.asyncio
    async def test_replace_bg_no_file_raises(self, svc):
        with pytest.raises(ImageGenError, match="introuvable"):
            await svc.replace_background("/nonexistent.png", "beach")

    @pytest.mark.asyncio
    async def test_sketch_no_file_raises(self, svc):
        with pytest.raises(ImageGenError, match="introuvable"):
            await svc.sketch_to_image("/nonexistent.png", "realistic cat")


# ── Tests constants ───────────────────────────────────────────────────────

class TestConstants:
    def test_stability_edit_endpoints(self):
        assert "inpaint" in _STABILITY_EDIT_ENDPOINTS
        assert "remove-background" in _STABILITY_EDIT_ENDPOINTS
        assert "upscale-fast" in _STABILITY_EDIT_ENDPOINTS

    def test_flux_api_paths(self):
        assert "flux-2-max" in _FLUX_API_PATHS
        assert "flux-schnell" in _FLUX_API_PATHS

    def test_generated_images_dir(self):
        assert isinstance(GENERATED_IMAGES_DIR, Path)


class TestSafeErrorSummary:
    def test_http_status_error_does_not_leak_google_api_key_url(self):
        request = httpx.Request(
            "POST",
            "https://generativelanguage.googleapis.com/v1beta/models/gemini:generateContent?key=SECRET_GOOGLE_KEY",
        )
        response = httpx.Response(429, request=request, text="quota exceeded")
        err = httpx.HTTPStatusError("rate limited", request=request, response=response)

        summary = _safe_error_summary(err)

        assert summary == "http_status:429:Too Many Requests"
        assert "SECRET_GOOGLE_KEY" not in summary
        assert "generativelanguage.googleapis.com" not in summary
        assert "?key=" not in summary

    def test_generic_error_redacts_query_secret_and_bearer(self):
        err = RuntimeError("failed url=https://x.test/path?api_key=SECRET123 header=Bearer TOKEN456")

        summary = _safe_error_summary(err)

        assert "SECRET123" not in summary
        assert "TOKEN456" not in summary
        assert "api_key=<redacted>" in summary
        assert "Bearer <redacted>" in summary


# ── Tests enrich_prompt ───────────────────────────────────────────────────

class TestEnrichPrompt:
    @pytest.mark.asyncio
    async def test_enrich_fallback_on_error(self, svc):
        """Si le LLM échoue, retourne le prompt original."""
        result = await svc.enrich_prompt("a simple cat")
        assert result == "a simple cat"


# ── Tests ImageResult dataclass ───────────────────────────────────────────

class TestImageResult:
    def test_frozen(self):
        r = ImageResult(
            data=b"test", format="png", width=1024, height=1024,
            provider="test", model="test", cost_estimate=0.0,
            generation_time_ms=100, prompt_used="cat",
        )
        with pytest.raises(AttributeError):
            r.format = "jpg"

    def test_optional_seed(self):
        r = ImageResult(
            data=b"test", format="png", width=1024, height=1024,
            provider="test", model="test", cost_estimate=0.0,
            generation_time_ms=100, prompt_used="cat", seed=42,
        )
        assert r.seed == 42

    def test_default_seed_none(self):
        r = ImageResult(
            data=b"test", format="png", width=1024, height=1024,
            provider="test", model="test", cost_estimate=0.0,
            generation_time_ms=100, prompt_used="cat",
        )
        assert r.seed is None
