"""
Tests pour les fonctionnalités Minia-style : compose(), generate_thumbnail_pro(),
generate_headlines(), et les handlers associés.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.image_gen import (
    ComposeAsset,
    ImageGenError,
    ImageGenService,
    ImageResult,
    ThumbnailPlan,
    _PLATFORM_PROMPTS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_singleton():
    ImageGenService.reset_instance()
    import src.reasoning.handlers.image_gen as _mod
    _mod._svc = None
    yield
    ImageGenService.reset_instance()
    _mod._svc = None


@pytest.fixture
def svc():
    return ImageGenService.get_instance()


@pytest.fixture
def fake_png():
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


@pytest.fixture
def tmp_images(fake_png, tmp_path):
    """Crée 3 images temporaires pour la composition."""
    paths = {}
    for name in ("subject.png", "background.jpg", "object.png"):
        p = tmp_path / name
        p.write_bytes(fake_png)
        paths[name.split(".")[0]] = str(p)
    return paths


@pytest.fixture
def fake_result():
    return ImageResult(
        data=b"\x89PNG" + b"\x00" * 100,
        format="png",
        width=1280,
        height=720,
        provider="gemini",
        model="gemini-3.1-flash-image",
        cost_estimate=0.0,
        generation_time_ms=200,
        prompt_used="test composition",
    )


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.get_trace_context.return_value = {}
    return ctx


# ══════════════════════════════════════════════════════════════════════════
# Dataclasses
# ══════════════════════════════════════════════════════════════════════════

class TestComposeAsset:
    def test_creation(self):
        a = ComposeAsset(path="/tmp/img.png", role="subject", description="personne")
        assert a.path == "/tmp/img.png"
        assert a.role == "subject"
        assert a.description == "personne"

    def test_frozen(self):
        a = ComposeAsset(path="/tmp/img.png", role="subject")
        with pytest.raises(AttributeError):
            a.role = "background"

    def test_default_description(self):
        a = ComposeAsset(path="/tmp/img.png", role="sky")
        assert a.description == ""

    def test_valid_roles_documented(self):
        for role in ("subject", "object", "background", "foreground", "sky"):
            a = ComposeAsset(path="/x.png", role=role)
            assert a.role == role


class TestThumbnailPlan:
    def test_creation(self):
        plan = ThumbnailPlan(
            headlines=["Titre 1", "Titre 2"],
            visual_prompt="A man looking surprised",
            colors=["#FF0000", "#FFFFFF"],
            composition_notes="sujet à gauche",
            emotion="surprised",
        )
        assert len(plan.headlines) == 2
        assert plan.emotion == "surprised"

    def test_frozen(self):
        plan = ThumbnailPlan(
            headlines=["T1"], visual_prompt="...", colors=[], 
            composition_notes="", emotion="",
        )
        with pytest.raises(AttributeError):
            plan.emotion = "happy"


# ══════════════════════════════════════════════════════════════════════════
# _PLATFORM_PROMPTS
# ══════════════════════════════════════════════════════════════════════════

class TestPlatformPrompts:
    def test_all_platforms_present(self):
        expected = {
            "youtube_thumbnail", "youtube_banner", "tiktok_cover",
            "instagram_post", "instagram_story", "linkedin_post",
            "twitter_post", "podcast_cover",
        }
        assert set(_PLATFORM_PROMPTS.keys()) == expected

    def test_all_have_required_fields(self):
        for platform, info in _PLATFORM_PROMPTS.items():
            assert "dimensions" in info, f"{platform} manque 'dimensions'"
            assert "aspect" in info, f"{platform} manque 'aspect'"
            assert "rules" in info, f"{platform} manque 'rules'"
            assert isinstance(info["rules"], str)
            assert len(info["rules"]) > 30

    def test_youtube_thumbnail_no_text_rule(self):
        rules = _PLATFORM_PROMPTS["youtube_thumbnail"]["rules"]
        assert "DO NOT render" in rules or "NO TEXT" in rules

    def test_dimensions_parseable(self):
        for platform, info in _PLATFORM_PROMPTS.items():
            parts = info["dimensions"].split("x")
            assert len(parts) == 2, f"{platform} dimensions invalides"
            assert int(parts[0]) > 0 and int(parts[1]) > 0


# ══════════════════════════════════════════════════════════════════════════
# compose()
# ══════════════════════════════════════════════════════════════════════════

class TestCompose:
    @pytest.mark.asyncio
    async def test_compose_empty_assets_raises(self, svc):
        with pytest.raises(ImageGenError, match="Au moins un asset"):
            await svc.compose([], "test prompt")

    @pytest.mark.asyncio
    async def test_compose_empty_prompt_raises(self, svc, tmp_images):
        assets = [ComposeAsset(path=tmp_images["subject"], role="subject")]
        with pytest.raises(ImageGenError, match="prompt.*vide"):
            await svc.compose(assets, "")

    @pytest.mark.asyncio
    async def test_compose_missing_file_raises(self, svc):
        assets = [ComposeAsset(path="/nonexistent.png", role="subject")]
        with pytest.raises(ImageGenError, match="introuvable"):
            await svc.compose(assets, "test prompt")

    @pytest.mark.asyncio
    async def test_compose_success_gemini(self, svc, tmp_images, fake_png):
        """Test de composition réussie avec mock Gemini."""
        assets = [
            ComposeAsset(path=tmp_images["subject"], role="subject", description="homme"),
            ComposeAsset(path=tmp_images["background"], role="background", description="plage"),
        ]
        gemini_response = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": base64.b64encode(fake_png).decode(),
                        }
                    }]
                }
            }]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = gemini_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"}):
            with patch.object(svc, "_get_client", new_callable=AsyncMock, return_value=mock_client):
                result = await svc.compose(assets, "Combine subject on beach background")

        assert isinstance(result, ImageResult)
        assert result.provider == "gemini"
        assert result.data == fake_png

    @pytest.mark.asyncio
    async def test_compose_with_platform_sets_size(self, svc, tmp_images, fake_png):
        """La plateforme youtube_thumbnail force 1280x720."""
        assets = [ComposeAsset(path=tmp_images["subject"], role="subject")]
        gemini_response = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": base64.b64encode(fake_png).decode(),
                        }
                    }]
                }
            }]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = gemini_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"}):
            with patch.object(svc, "_get_client", new_callable=AsyncMock, return_value=mock_client):
                result = await svc.compose(
                    assets, "test", platform="youtube_thumbnail",
                )

        assert result.width == 1280
        assert result.height == 720

    @pytest.mark.asyncio
    async def test_compose_gemini_no_image_raises(self, svc, tmp_images):
        """Si Gemini ne retourne pas d'image, on lève ImageGenError."""
        assets = [ComposeAsset(path=tmp_images["subject"], role="subject")]
        gemini_response = {"candidates": [{"content": {"parts": [{"text": "sorry"}]}}]}

        mock_resp = MagicMock()
        mock_resp.json.return_value = gemini_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"}):
            with patch.object(svc, "_get_client", new_callable=AsyncMock, return_value=mock_client):
                with pytest.raises(ImageGenError, match="pas retourné d'image composée"):
                    await svc.compose(assets, "test")

    @pytest.mark.asyncio
    async def test_compose_sends_role_context(self, svc, tmp_images, fake_png):
        """Vérifie que chaque asset a son texte de rôle avant l'inlineData."""
        assets = [
            ComposeAsset(path=tmp_images["subject"], role="subject", description="homme surpris"),
            ComposeAsset(path=tmp_images["background"], role="background", description="bureau"),
        ]
        gemini_response = {
            "candidates": [{
                "content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": base64.b64encode(fake_png).decode()}}]}
            }]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = gemini_response
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch.dict(os.environ, {"GOOGLE_API_KEY": "fake-key"}):
            with patch.object(svc, "_get_client", new_callable=AsyncMock, return_value=mock_client):
                await svc.compose(assets, "Compose it")

        # Vérifier le body envoyé
        call_args = mock_client.post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        parts = body["contents"][0]["parts"]

        # Pattern: text role, inlineData, text role, inlineData, text prompt
        text_parts = [p for p in parts if "text" in p]
        assert any("[SUBJECT]" in p["text"] for p in text_parts)
        assert any("[BACKGROUND]" in p["text"] for p in text_parts)


# ══════════════════════════════════════════════════════════════════════════
# _parse_thumbnail_plan()
# ══════════════════════════════════════════════════════════════════════════

class TestParseThumbnailPlan:
    def test_valid_json(self, svc):
        raw = json.dumps({
            "headlines": ["Titre 1", "Titre 2"],
            "visual_prompt": "A surprised man",
            "colors": ["#FF0000"],
            "composition_notes": "center",
            "emotion": "surprised",
        })
        plan = svc._parse_thumbnail_plan(raw)
        assert isinstance(plan, ThumbnailPlan)
        assert plan.headlines == ["Titre 1", "Titre 2"]
        assert plan.emotion == "surprised"

    def test_json_in_markdown_block(self, svc):
        raw = '```json\n{"headlines": ["T1"], "visual_prompt": "x", "colors": [], "composition_notes": "", "emotion": "happy"}\n```'
        plan = svc._parse_thumbnail_plan(raw)
        assert plan.headlines == ["T1"]
        assert plan.emotion == "happy"

    def test_garbage_fallback(self, svc):
        plan = svc._parse_thumbnail_plan("not json at all")
        assert isinstance(plan, ThumbnailPlan)
        assert plan.headlines == ["Thumbnail"]
        assert plan.visual_prompt == "Professional thumbnail, high quality"

    def test_partial_json(self, svc):
        raw = json.dumps({"headlines": ["A"], "emotion": "excited"})
        plan = svc._parse_thumbnail_plan(raw)
        assert plan.headlines == ["A"]
        assert plan.emotion == "excited"
        assert plan.visual_prompt == "Professional thumbnail, high quality"


# ══════════════════════════════════════════════════════════════════════════
# generate_thumbnail_pro()
# ══════════════════════════════════════════════════════════════════════════

class TestGenerateThumbnailPro:
    @pytest.mark.asyncio
    async def test_pipeline_calls_llm_then_generate(self, svc, fake_result):
        """Vérifie le pipeline 2 étapes: LLM plan → generate."""
        plan_json = json.dumps({
            "headlines": ["Breaking: Python 4.0!!", "Python 4 est là 🐍"],
            "visual_prompt": "A python snake wearing a top hat, golden background",
            "colors": ["#FFD700", "#000000"],
            "composition_notes": "Snake centered, hat tilted",
            "emotion": "excited",
        })
        with (
            patch("src.llm.multi_provider.MultiProviderLLM") as MockLLM,
            patch.object(svc, "generate", new_callable=AsyncMock, return_value=fake_result),
        ):
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(return_value=plan_json)
            MockLLM.get_instance.return_value = mock_llm

            result, plan = await svc.generate_thumbnail_pro("Python 4.0 release")

            assert isinstance(result, ImageResult)
            assert isinstance(plan, ThumbnailPlan)
            assert len(plan.headlines) == 2
            assert plan.emotion == "excited"
            svc.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pipeline_with_assets_calls_compose(self, svc, fake_result, tmp_images):
        """Si des assets sont fournis, compose() est appelé au lieu de generate()."""
        assets = [ComposeAsset(path=tmp_images["subject"], role="subject")]
        plan_json = json.dumps({
            "headlines": ["T1"], "visual_prompt": "prompt",
            "colors": [], "composition_notes": "", "emotion": "happy",
        })
        with (
            patch("src.llm.multi_provider.MultiProviderLLM") as MockLLM,
            patch.object(svc, "compose", new_callable=AsyncMock, return_value=fake_result),
        ):
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(return_value=plan_json)
            MockLLM.get_instance.return_value = mock_llm

            result, plan = await svc.generate_thumbnail_pro(
                "Test topic", assets=assets,
            )

            svc.compose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_pipeline_default_platform_youtube(self, svc, fake_result):
        """Le platform par défaut est youtube_thumbnail."""
        plan_json = json.dumps({
            "headlines": ["T"], "visual_prompt": "p",
            "colors": [], "composition_notes": "", "emotion": "e",
        })
        with (
            patch("src.llm.multi_provider.MultiProviderLLM") as MockLLM,
            patch.object(svc, "generate", new_callable=AsyncMock, return_value=fake_result),
        ):
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(return_value=plan_json)
            MockLLM.get_instance.return_value = mock_llm

            _, plan = await svc.generate_thumbnail_pro("test")

        # Le system prompt envoyé au LLM doit mentionner youtube_thumbnail
        call_args = mock_llm.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        system_content = messages[0]["content"]
        assert "youtube_thumbnail" in system_content


# ══════════════════════════════════════════════════════════════════════════
# generate_headlines()
# ══════════════════════════════════════════════════════════════════════════

class TestGenerateHeadlines:
    @pytest.mark.asyncio
    async def test_returns_list(self, svc):
        llm_response = json.dumps(["Titre 1", "Titre 2", "Titre 3"])
        with patch("src.llm.multi_provider.MultiProviderLLM") as MockLLM:
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(return_value=llm_response)
            MockLLM.get_instance.return_value = mock_llm

            headlines = await svc.generate_headlines("test topic")

        assert isinstance(headlines, list)
        assert len(headlines) == 3

    @pytest.mark.asyncio
    async def test_respects_count(self, svc):
        llm_response = json.dumps(["A", "B", "C", "D", "E"])
        with patch("src.llm.multi_provider.MultiProviderLLM") as MockLLM:
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(return_value=llm_response)
            MockLLM.get_instance.return_value = mock_llm

            headlines = await svc.generate_headlines("test", count=3)

        assert len(headlines) == 3

    @pytest.mark.asyncio
    async def test_count_clamped_max_10(self, svc):
        llm_response = json.dumps(["A"] * 15)
        with patch("src.llm.multi_provider.MultiProviderLLM") as MockLLM:
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(return_value=llm_response)
            MockLLM.get_instance.return_value = mock_llm

            headlines = await svc.generate_headlines("test", count=20)

        assert len(headlines) <= 10

    @pytest.mark.asyncio
    async def test_fallback_on_bad_json(self, svc):
        with patch("src.llm.multi_provider.MultiProviderLLM") as MockLLM:
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(return_value="not json\nLine 1\nLine 2")
            MockLLM.get_instance.return_value = mock_llm

            headlines = await svc.generate_headlines("test")

        assert isinstance(headlines, list)
        assert len(headlines) > 0

    @pytest.mark.asyncio
    async def test_json_in_markdown_block(self, svc):
        with patch("src.llm.multi_provider.MultiProviderLLM") as MockLLM:
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(return_value='```json\n["One", "Two"]\n```')
            MockLLM.get_instance.return_value = mock_llm

            headlines = await svc.generate_headlines("test")

        assert headlines == ["One", "Two"]

    @pytest.mark.asyncio
    async def test_platform_tip_in_system_prompt(self, svc):
        with patch("src.llm.multi_provider.MultiProviderLLM") as MockLLM:
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(return_value='["T"]')
            MockLLM.get_instance.return_value = mock_llm

            await svc.generate_headlines("test", platform="tiktok")

        call_args = mock_llm.chat.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        assert "TikTok" in messages[0]["content"]


# ══════════════════════════════════════════════════════════════════════════
# Handlers
# ══════════════════════════════════════════════════════════════════════════

class TestComposeImageHandler:
    @pytest.mark.asyncio
    async def test_success(self, mock_ctx, fake_result, tmp_images, tmp_path):
        from src.reasoning.handlers.image_gen import compose_image_handler

        assets_json = json.dumps([
            {"path": tmp_images["subject"], "role": "subject", "description": "homme"},
            {"path": tmp_images["background"], "role": "background", "description": "plage"},
        ])

        with (
            patch("src.reasoning.handlers.image_gen._get_service") as mock_get_svc,
        ):
            mock_svc = MagicMock()
            mock_svc.compose = AsyncMock(return_value=fake_result)
            mock_svc.save_to_workspace.return_value = tmp_path / "composed.png"
            mock_get_svc.return_value = mock_svc

            result = await compose_image_handler(
                mock_ctx, prompt="Compose the assets", assets=assets_json,
            )

        assert result.success
        assert "composée" in result.output

    @pytest.mark.asyncio
    async def test_invalid_assets_json(self, mock_ctx):
        from src.reasoning.handlers.image_gen import compose_image_handler
        result = await compose_image_handler(
            mock_ctx, prompt="test", assets="NOT JSON",
        )
        assert not result.success
        assert "Format assets invalide" in result.output

    @pytest.mark.asyncio
    async def test_empty_assets(self, mock_ctx):
        from src.reasoning.handlers.image_gen import compose_image_handler
        result = await compose_image_handler(
            mock_ctx, prompt="test", assets="[]",
        )
        assert not result.success

    @pytest.mark.asyncio
    async def test_invalid_role(self, mock_ctx, tmp_images):
        from src.reasoning.handlers.image_gen import compose_image_handler
        assets_json = json.dumps([{"path": tmp_images["subject"], "role": "INVALID"}])
        result = await compose_image_handler(
            mock_ctx, prompt="test", assets=assets_json,
        )
        assert not result.success
        assert "Rôle invalide" in result.output


class TestGenerateThumbnailProHandler:
    @pytest.mark.asyncio
    async def test_success(self, mock_ctx, fake_result, tmp_path):
        from src.reasoning.handlers.image_gen import generate_thumbnail_pro_handler

        plan = ThumbnailPlan(
            headlines=["Titre Viral!", "Alternative"],
            visual_prompt="test",
            colors=["#FF0000"],
            composition_notes="center",
            emotion="excited",
        )

        with patch("src.reasoning.handlers.image_gen._get_service") as mock_get_svc:
            mock_svc = MagicMock()
            mock_svc.generate_thumbnail_pro = AsyncMock(return_value=(fake_result, plan))
            mock_svc.save_to_workspace.return_value = tmp_path / "thumb.png"
            mock_get_svc.return_value = mock_svc

            result = await generate_thumbnail_pro_handler(
                mock_ctx, topic="Python 4.0",
            )

        assert result.success
        assert "pipeline 2 étapes" in result.output
        assert "Titre Viral!" in result.output

    @pytest.mark.asyncio
    async def test_with_assets(self, mock_ctx, fake_result, tmp_path, tmp_images):
        from src.reasoning.handlers.image_gen import generate_thumbnail_pro_handler

        plan = ThumbnailPlan(
            headlines=["T"], visual_prompt="p", colors=[],
            composition_notes="", emotion="happy",
        )
        assets_json = json.dumps([{"path": tmp_images["subject"], "role": "subject"}])

        with patch("src.reasoning.handlers.image_gen._get_service") as mock_get_svc:
            mock_svc = MagicMock()
            mock_svc.generate_thumbnail_pro = AsyncMock(return_value=(fake_result, plan))
            mock_svc.save_to_workspace.return_value = tmp_path / "thumb.png"
            mock_get_svc.return_value = mock_svc

            result = await generate_thumbnail_pro_handler(
                mock_ctx, topic="Test", assets=assets_json,
            )

        assert result.success


class TestGenerateHeadlinesHandler:
    @pytest.mark.asyncio
    async def test_success(self, mock_ctx):
        from src.reasoning.handlers.image_gen import generate_headlines_handler

        with patch("src.reasoning.handlers.image_gen._get_service") as mock_get_svc:
            mock_svc = MagicMock()
            mock_svc.generate_headlines = AsyncMock(
                return_value=["Titre 1", "Titre 2", "Titre 3"],
            )
            mock_get_svc.return_value = mock_svc

            result = await generate_headlines_handler(
                mock_ctx, topic="Python tips",
            )

        assert result.success
        assert "3 titres viraux" in result.output
        assert "Titre 1" in result.output

    @pytest.mark.asyncio
    async def test_custom_platform(self, mock_ctx):
        from src.reasoning.handlers.image_gen import generate_headlines_handler

        with patch("src.reasoning.handlers.image_gen._get_service") as mock_get_svc:
            mock_svc = MagicMock()
            mock_svc.generate_headlines = AsyncMock(return_value=["T1"])
            mock_get_svc.return_value = mock_svc

            result = await generate_headlines_handler(
                mock_ctx, topic="Test", platform="tiktok", count="3",
            )

        assert result.success
        mock_svc.generate_headlines.assert_awaited_once_with(
            "Test", platform="tiktok", count=3, style="",
        )

    @pytest.mark.asyncio
    async def test_error_handling(self, mock_ctx):
        from src.reasoning.handlers.image_gen import generate_headlines_handler

        with patch("src.reasoning.handlers.image_gen._get_service") as mock_get_svc:
            mock_svc = MagicMock()
            mock_svc.generate_headlines = AsyncMock(side_effect=Exception("LLM down"))
            mock_get_svc.return_value = mock_svc

            result = await generate_headlines_handler(mock_ctx, topic="Test")

        assert not result.success
        assert "LLM down" in result.output
