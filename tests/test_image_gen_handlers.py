"""
Tests pour les handlers de génération d'images (handlers/image_gen.py).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.image_gen import ImageResult, ImageGenService


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_svc():
    ImageGenService.reset_instance()
    # Reset lazy _svc cache in handlers module
    import src.reasoning.handlers.image_gen as _mod
    _mod._svc = None
    yield
    ImageGenService.reset_instance()
    _mod._svc = None


@pytest.fixture
def mock_ctx():
    ctx = MagicMock()
    ctx.get_trace_context.return_value = {}
    return ctx


@pytest.fixture
def fake_result(tmp_path):
    """Un ImageResult factice + service mocké pour save."""
    result = ImageResult(
        data=b"\x89PNG" + b"\x00" * 100,
        format="png",
        width=1024,
        height=1024,
        provider="gemini",
        model="gemini-3.1-flash-image",
        cost_estimate=0.0,
        generation_time_ms=150,
        prompt_used="test cat",
    )
    return result


# ── Tests handler defs ────────────────────────────────────────────────────

class TestHandlerDefs:
    def test_returns_list(self):
        from src.reasoning.handlers.image_gen import get_image_gen_handler_defs
        defs = get_image_gen_handler_defs()
        assert isinstance(defs, list)
        assert len(defs) == 13

    def test_all_have_required_fields(self):
        from src.reasoning.handlers.image_gen import get_image_gen_handler_defs
        for hdef in get_image_gen_handler_defs():
            assert hdef.name
            assert hdef.description
            assert hdef.handler is not None
            assert hdef.category == "image"
            assert hdef.source_module == "handlers.image_gen"

    def test_handler_names(self):
        from src.reasoning.handlers.image_gen import get_image_gen_handler_defs
        names = {h.name for h in get_image_gen_handler_defs()}
        expected = {
            "generate_image", "edit_image", "generate_thumbnail",
            "generate_logo", "upscale_image", "remove_background",
            "replace_background", "sketch_to_image", "generate_svg",
            "list_image_models", "compose_image",
            "generate_thumbnail_pro", "generate_headlines",
        }
        assert names == expected

    def test_generate_image_has_prompt_required(self):
        from src.reasoning.handlers.image_gen import get_image_gen_handler_defs
        gen = [h for h in get_image_gen_handler_defs() if h.name == "generate_image"][0]
        assert "prompt" in gen.parameters["required"]

    def test_edit_image_has_image_path_required(self):
        from src.reasoning.handlers.image_gen import get_image_gen_handler_defs
        edit = [h for h in get_image_gen_handler_defs() if h.name == "edit_image"][0]
        assert "image_path" in edit.parameters["required"]
        assert "prompt" in edit.parameters["required"]


# ── Tests generate_image_handler ──────────────────────────────────────────

class TestGenerateImageHandler:
    @pytest.mark.asyncio
    async def test_success(self, mock_ctx, fake_result, tmp_path):
        from src.reasoning.handlers.image_gen import generate_image_handler

        mock_svc = MagicMock(spec=ImageGenService)
        mock_svc.generate = AsyncMock(return_value=fake_result)
        mock_svc.save_to_workspace.return_value = tmp_path / "cat_abc.png"
        (tmp_path / "cat_abc.png").write_bytes(b"\x89PNG")

        with patch("src.reasoning.handlers.image_gen._get_service", return_value=mock_svc):
            result = await generate_image_handler(mock_ctx, prompt="a cute cat")

        assert result.success
        assert "Image générée" in result.output
        assert "cat_abc.png" in result.output

    @pytest.mark.asyncio
    async def test_failure(self, mock_ctx):
        from src.reasoning.handlers.image_gen import generate_image_handler
        from src.services.image_gen import ImageGenError

        mock_svc = MagicMock(spec=ImageGenService)
        mock_svc.generate = AsyncMock(side_effect=ImageGenError("No provider"))

        with patch("src.reasoning.handlers.image_gen._get_service", return_value=mock_svc):
            result = await generate_image_handler(mock_ctx, prompt="cat")

        assert not result.success
        assert "No provider" in result.output


# ── Tests edit_image_handler ──────────────────────────────────────────────

class TestEditImageHandler:
    @pytest.mark.asyncio
    async def test_success(self, mock_ctx, fake_result, tmp_path):
        from src.reasoning.handlers.image_gen import edit_image_handler

        mock_svc = MagicMock(spec=ImageGenService)
        mock_svc.edit = AsyncMock(return_value=fake_result)
        mock_svc.save_to_workspace.return_value = tmp_path / "edited.png"

        with patch("src.reasoning.handlers.image_gen._get_service", return_value=mock_svc):
            result = await edit_image_handler(mock_ctx, image_path="/tmp/img.png", prompt="make red")

        assert result.success
        assert "éditée" in result.output


# ── Tests generate_thumbnail_handler ──────────────────────────────────────

class TestThumbnailHandler:
    @pytest.mark.asyncio
    async def test_success(self, mock_ctx, fake_result, tmp_path):
        from src.reasoning.handlers.image_gen import generate_thumbnail_handler

        mock_svc = MagicMock(spec=ImageGenService)
        mock_svc.generate = AsyncMock(return_value=fake_result)
        mock_svc.save_to_workspace.return_value = tmp_path / "thumb_cat.png"

        with patch("src.reasoning.handlers.image_gen._get_service", return_value=mock_svc):
            result = await generate_thumbnail_handler(mock_ctx, prompt="reaction face")

        assert result.success
        assert "Miniature" in result.output

    @pytest.mark.asyncio
    async def test_text_overlay(self, mock_ctx, fake_result, tmp_path):
        from src.reasoning.handlers.image_gen import generate_thumbnail_handler

        mock_svc = MagicMock(spec=ImageGenService)
        mock_svc.generate = AsyncMock(return_value=fake_result)
        mock_svc.save_to_workspace.return_value = tmp_path / "thumb.png"

        with patch("src.reasoning.handlers.image_gen._get_service", return_value=mock_svc):
            await generate_thumbnail_handler(mock_ctx, prompt="cat", text_overlay="10 TIPS")

        call_args = mock_svc.generate.call_args
        assert "10 TIPS" in call_args[0][0]


# ── Tests generate_logo_handler ───────────────────────────────────────────

class TestLogoHandler:
    @pytest.mark.asyncio
    async def test_svg_model(self, mock_ctx, fake_result, tmp_path):
        from src.reasoning.handlers.image_gen import generate_logo_handler

        mock_svc = MagicMock(spec=ImageGenService)
        mock_svc._has_api_key.return_value = True
        mock_svc.generate = AsyncMock(return_value=fake_result)
        mock_svc.save_to_workspace.return_value = tmp_path / "logo.svg"

        with patch("src.reasoning.handlers.image_gen._get_service", return_value=mock_svc):
            result = await generate_logo_handler(mock_ctx, prompt="fitness brand", svg="true")

        assert result.success
        # Should have selected recraft-v4-svg
        call_args = mock_svc.generate.call_args
        assert call_args[1]["model"] == "recraft-v4-svg"


# ── Tests upscale/remove_bg/replace_bg handlers ──────────────────────────

class TestUpscaleHandler:
    @pytest.mark.asyncio
    async def test_success(self, mock_ctx, fake_result, tmp_path):
        from src.reasoning.handlers.image_gen import upscale_image_handler

        mock_svc = MagicMock(spec=ImageGenService)
        mock_svc.upscale = AsyncMock(return_value=fake_result)
        mock_svc.save_to_workspace.return_value = tmp_path / "upscaled.png"

        with patch("src.reasoning.handlers.image_gen._get_service", return_value=mock_svc):
            result = await upscale_image_handler(mock_ctx, image_path="/tmp/img.png")

        assert result.success
        assert "upscalée" in result.output


class TestRemoveBgHandler:
    @pytest.mark.asyncio
    async def test_success(self, mock_ctx, fake_result, tmp_path):
        from src.reasoning.handlers.image_gen import remove_background_handler

        mock_svc = MagicMock(spec=ImageGenService)
        mock_svc.remove_background = AsyncMock(return_value=fake_result)
        mock_svc.save_to_workspace.return_value = tmp_path / "nobg.png"

        with patch("src.reasoning.handlers.image_gen._get_service", return_value=mock_svc):
            result = await remove_background_handler(mock_ctx, image_path="/tmp/img.png")

        assert result.success
        assert "Fond supprimé" in result.output


class TestReplaceBgHandler:
    @pytest.mark.asyncio
    async def test_success(self, mock_ctx, fake_result, tmp_path):
        from src.reasoning.handlers.image_gen import replace_background_handler

        mock_svc = MagicMock(spec=ImageGenService)
        mock_svc.replace_background = AsyncMock(return_value=fake_result)
        mock_svc.save_to_workspace.return_value = tmp_path / "rebg.png"

        with patch("src.reasoning.handlers.image_gen._get_service", return_value=mock_svc):
            result = await replace_background_handler(
                mock_ctx, image_path="/tmp/img.png", background_prompt="tropical beach",
            )

        assert result.success
        assert "Fond remplacé" in result.output


# ── Tests list_image_models handler ───────────────────────────────────────

class TestListModelsHandler:
    @pytest.mark.asyncio
    async def test_returns_list(self, mock_ctx):
        from src.reasoning.handlers.image_gen import list_image_models_handler

        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test"}, clear=True):
            result = await list_image_models_handler(mock_ctx)

        assert result.success
        assert "Disponibles" in result.output


# ── Tests sketch_to_image handler ─────────────────────────────────────────

class TestSketchHandler:
    @pytest.mark.asyncio
    async def test_success(self, mock_ctx, fake_result, tmp_path):
        from src.reasoning.handlers.image_gen import sketch_to_image_handler

        mock_svc = MagicMock(spec=ImageGenService)
        mock_svc.sketch_to_image = AsyncMock(return_value=fake_result)
        mock_svc.save_to_workspace.return_value = tmp_path / "sketch.png"

        with patch("src.reasoning.handlers.image_gen._get_service", return_value=mock_svc):
            result = await sketch_to_image_handler(
                mock_ctx, image_path="/tmp/sketch.png", prompt="realistic cat",
            )

        assert result.success
        assert "Croquis" in result.output


# ── Tests generate_svg handler ────────────────────────────────────────────

class TestSvgHandler:
    @pytest.mark.asyncio
    async def test_auto_recraft(self, mock_ctx, tmp_path):
        from src.reasoning.handlers.image_gen import generate_svg_handler

        svg_result = ImageResult(
            data=b"<svg></svg>", format="svg", width=0, height=0,
            provider="recraft", model="recraft-v4-svg",
            cost_estimate=0.04, generation_time_ms=300,
            prompt_used="icon",
        )
        mock_svc = MagicMock(spec=ImageGenService)
        mock_svc._has_api_key.return_value = True
        mock_svc.generate = AsyncMock(return_value=svg_result)
        mock_svc.save_to_workspace.return_value = tmp_path / "icon.svg"

        with patch("src.reasoning.handlers.image_gen._get_service", return_value=mock_svc):
            result = await generate_svg_handler(mock_ctx, prompt="simple icon")

        assert result.success
        assert "SVG vectoriel" in result.output
