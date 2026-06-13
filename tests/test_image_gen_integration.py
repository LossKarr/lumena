"""
Tests pour les routes API de génération d'images et file serving.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.image_gen import ImageGenService, ImageResult


@pytest.fixture(autouse=True)
def _reset_svc():
    ImageGenService.reset_instance()
    yield
    ImageGenService.reset_instance()


# ── Tests workspace file serving ──────────────────────────────────────────

class TestServeWorkspaceFile:
    """Tests pour GET /api/files/workspace/{path}."""

    def test_path_traversal_blocked(self):
        from web.routes.image_gen import serve_workspace_file
        import asyncio
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            asyncio.run(serve_workspace_file("../../etc/passwd"))
        assert exc.value.status_code == 403

    def test_nonexistent_file_404(self):
        from web.routes.image_gen import serve_workspace_file
        import asyncio
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            asyncio.run(serve_workspace_file("nonexistent_file_xyz.png"))
        assert exc.value.status_code == 404

    def test_disallowed_extension_blocked(self, tmp_path):
        from web.routes.image_gen import serve_workspace_file
        import asyncio
        from fastapi import HTTPException

        with patch("web.routes.image_gen.WORKSPACE_DIR", tmp_path):
            bad_file = tmp_path / "malware.exe"
            bad_file.write_bytes(b"MZ")
            with pytest.raises(HTTPException) as exc:
                asyncio.run(serve_workspace_file("malware.exe"))
            assert exc.value.status_code == 403

    def test_valid_image_served(self, tmp_path):
        from web.routes.image_gen import serve_workspace_file
        import asyncio

        with patch("web.routes.image_gen.WORKSPACE_DIR", tmp_path):
            img = tmp_path / "test.png"
            img.write_bytes(b"\x89PNG" + b"\x00" * 20)
            response = asyncio.run(serve_workspace_file("test.png"))
            assert response.media_type == "image/png"

    def test_nested_path(self, tmp_path):
        from web.routes.image_gen import serve_workspace_file
        import asyncio

        with patch("web.routes.image_gen.WORKSPACE_DIR", tmp_path):
            subdir = tmp_path / "images" / "2026-04-15"
            subdir.mkdir(parents=True)
            img = subdir / "cat.jpg"
            img.write_bytes(b"\xff\xd8\xff\xe0")  # JPEG magic
            response = asyncio.run(serve_workspace_file("images/2026-04-15/cat.jpg"))
            assert response.media_type == "image/jpeg"


# ── Tests API /api/images/models ──────────────────────────────────────────

class TestApiListModels:
    @pytest.mark.asyncio
    async def test_returns_models(self):
        from web.routes.image_gen import api_list_image_models
        resp = await api_list_image_models()
        data = json.loads(resp.body)
        assert "total" in data
        assert data["total"] > 30
        assert "models" in data
        assert isinstance(data["models"], list)

    @pytest.mark.asyncio
    async def test_available_count(self):
        from web.routes.image_gen import api_list_image_models
        with patch.dict(os.environ, {"GOOGLE_API_KEY": "test"}, clear=True):
            resp = await api_list_image_models()
            data = json.loads(resp.body)
            assert data["available"] >= 1  # Au moins les modèles Gemini


# ── Tests config panel update ─────────────────────────────────────────────

class TestConfigPanel:
    def test_brain_image_gen_has_new_models(self):
        """Vérifie que LUMENA_BRAIN_IMAGE_GEN a les nouveaux modèles."""
        from web.routes.config import _CONFIG_SCHEMA
        brain_img = [s for s in _CONFIG_SCHEMA if s["key"] == "LUMENA_BRAIN_IMAGE_GEN"]
        assert brain_img, "LUMENA_BRAIN_IMAGE_GEN not found in _CONFIG_SCHEMA"
        options = brain_img[0]["options"]
        assert "auto" in options
        assert "gemini-3.1-flash-image" in options
        assert "huggingface-sdxl" in options
        assert "gpt-image-2" in options
        assert "gpt-image-1.5" in options
        assert "flux-2-pro" in options
        assert "flux-2-klein-9b" in options
        assert "flux-1.1-pro-ultra" in options
        assert "stable-image-ultra" in options
        assert "sd3.5-flash" in options
        assert "ideogram-v4-quality" in options
        assert "ideogram-v4" in options
        assert "ideogram-v4-turbo" in options
        assert "ideogram-v3-quality" in options
        assert "seedream-5-lite" in options
        assert "seedream-4.5" in options
        assert "wan-2.7-image-pro" in options
        assert "qwen-image" in options
        assert "hunyuan-image-3" in options
        assert "recraft-v4" in options
        assert "dall-e-3" not in options  # Removed obsolete model
        assert "cogview-4" not in options
        assert "cogview-4-flash" not in options


# ── Tests paths.py integration ────────────────────────────────────────────

class TestPathsIntegration:
    def test_generated_images_dir_exists(self):
        from src.utils.paths import GENERATED_IMAGES_DIR
        assert isinstance(GENERATED_IMAGES_DIR, Path)

    def test_generated_images_in_critical_dirs(self):
        from src.utils.paths import _CRITICAL_DIRS, GENERATED_IMAGES_DIR
        assert GENERATED_IMAGES_DIR in _CRITICAL_DIRS


# ── Tests tool_registry integration ───────────────────────────────────────

class TestToolRegistryIntegration:
    def test_image_gen_in_handler_modules(self):
        """Vérifie que image_gen est dans _HANDLER_MODULES."""
        from src.reasoning.tool_registry import ToolRegistry
        # Instantiate to trigger module loading
        # Just check the source to confirm the entry exists
        import src.reasoning.tool_registry as mod
        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert ".handlers.image_gen" in source
        assert "get_image_gen_handler_defs" in source


# ── Tests react_config hints ─────────────────────────────────────────────

class TestReactConfigHints:
    def test_image_hints_registered(self):
        from src.reasoning.react_config import _TOOL_COMPLETION_HINTS
        image_tools = [
            "generate_image", "edit_image", "generate_thumbnail",
            "generate_logo", "upscale_image", "remove_background",
            "replace_background", "sketch_to_image", "generate_svg",
            "list_image_models",
        ]
        for tool in image_tools:
            assert tool in _TOOL_COMPLETION_HINTS, f"{tool} missing from hints"

    def test_generate_image_hints(self):
        from src.reasoning.react_config import _TOOL_COMPLETION_HINTS
        hints = _TOOL_COMPLETION_HINTS["generate_image"]
        assert "image" in hints
        assert any("photo" in h for h in hints)


# ── Tests providers.py integration ────────────────────────────────────────

class TestProvidersIntegration:
    def test_new_provider_types(self):
        from src.llm.providers import ProviderType
        assert ProviderType.STABILITY.value == "stability"
        assert ProviderType.FLUX.value == "flux"
        assert ProviderType.IDEOGRAM.value == "ideogram"
        assert ProviderType.RECRAFT.value == "recraft"
        assert ProviderType.REPLICATE.value == "replicate"
        assert ProviderType.HUGGINGFACE.value == "huggingface"

    def test_check_api_key_stability(self):
        from src.llm.providers import ProviderType, check_api_key
        with patch.dict(os.environ, {"STABILITY_API_KEY": "test-key"}):
            assert check_api_key(ProviderType.STABILITY) is True

    def test_check_api_key_flux(self):
        from src.llm.providers import ProviderType, check_api_key
        with patch.dict(os.environ, {}, clear=True):
            assert check_api_key(ProviderType.FLUX) is False

    def test_get_api_key_replicate(self):
        from src.llm.providers import ProviderType, get_api_key
        with patch.dict(os.environ, {"REPLICATE_API_TOKEN": "r8_test"}):
            assert get_api_key(ProviderType.REPLICATE) == "r8_test"


# ── Tests chat.py BLOCKER B integration ───────────────────────────────────

class TestChatBlockerB:
    def test_created_docs_url_in_source(self):
        """Vérifie que le code chat.py contient la logique url pour images."""
        chat_source = Path("web/routes/chat.py").read_text(encoding="utf-8")
        assert '/api/files/workspace/' in chat_source
        assert '["type"] = "image"' in chat_source


# ── Tests server.py registration ─────────────────────────────────────────

class TestServerRegistration:
    def test_image_gen_imported(self):
        server_source = Path("web/server.py").read_text(encoding="utf-8")
        assert "image_gen" in server_source
        assert "app.include_router(image_gen.router)" in server_source
