"""Tests Phase 5 — generate_website délègue à create_project_handler."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestGenerateWebsitePhase5:
    """Vérifie que generate_website_handler délègue correctement."""

    @pytest.mark.asyncio
    async def test_delegates_to_create_project_when_available(self):
        """Quand _PROJECT_HANDLER_AVAILABLE, délègue à create_project_handler."""
        from src.reasoning.handlers.website import generate_website_handler

        ctx = MagicMock()
        ctx.budget_seconds = 300.0
        ctx.llm = AsyncMock()

        mock_result = MagicMock()
        mock_result.output = "✅ Projet généré avec 5 fichiers"

        with patch("src.reasoning.handlers.website._PROJECT_HANDLER_AVAILABLE", True), \
             patch("src.reasoning.handlers.website.WEBSITE_BUILDER_AVAILABLE", True), \
             patch("src.reasoning.handlers.website._create_project_handler", AsyncMock(return_value=mock_result)) as mock_cp, \
             patch("src.reasoning.handlers.website._build_design_directives", return_value="palette: bleu, blanc"):

            result = await generate_website_handler(
                ctx=ctx,
                description="site vitrine restaurant",
                project_name="resto",
            )

        assert result.success
        assert mock_cp.called
        call_kwargs = mock_cp.call_args
        # La description doit être enrichie
        enriched_desc = call_kwargs[1]["description"] if call_kwargs[1] else call_kwargs[0][1]
        assert "restaurant" in enriched_desc.lower()
        assert "palette" in enriched_desc.lower() or "site web" in enriched_desc.lower()

    @pytest.mark.asyncio
    async def test_fallback_legacy_when_project_unavailable(self):
        """Quand _PROJECT_HANDLER_AVAILABLE=False, utilise le fallback legacy."""
        from src.reasoning.handlers.website import generate_website_handler

        ctx = MagicMock()

        with patch("src.reasoning.handlers.website._PROJECT_HANDLER_AVAILABLE", False), \
             patch("src.reasoning.handlers.website.WEBSITE_BUILDER_AVAILABLE", True), \
             patch("src.reasoning.handlers.website._generate_website", AsyncMock(return_value="legacy result")) as mock_legacy:

            result = await generate_website_handler(
                ctx=ctx,
                description="landing page SaaS",
            )

        assert result.success
        mock_legacy.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_description_returns_error(self):
        """Description vide → erreur."""
        from src.reasoning.handlers.website import generate_website_handler

        ctx = MagicMock()
        result = await generate_website_handler(ctx=ctx, description="")
        assert not result.success

    @pytest.mark.asyncio
    async def test_description_enriched_with_project_type(self):
        """La description enrichie inclut le project_type."""
        from src.reasoning.handlers.website import generate_website_handler

        ctx = MagicMock()
        ctx.budget_seconds = 300.0
        ctx.llm = AsyncMock()

        mock_result = MagicMock()
        mock_result.output = "✅ OK"

        with patch("src.reasoning.handlers.website._PROJECT_HANDLER_AVAILABLE", True), \
             patch("src.reasoning.handlers.website.WEBSITE_BUILDER_AVAILABLE", True), \
             patch("src.reasoning.handlers.website._create_project_handler", AsyncMock(return_value=mock_result)) as mock_cp, \
             patch("src.reasoning.handlers.website._build_design_directives", return_value="palette: vert"):

            await generate_website_handler(
                ctx=ctx,
                description="app e-commerce",
                project_type="fullstack",
            )

        call_kwargs = mock_cp.call_args
        enriched_desc = call_kwargs[1]["description"] if call_kwargs[1] else call_kwargs[0][1]
        assert "fullstack" in enriched_desc.lower()

    @pytest.mark.asyncio
    async def test_fallback_on_create_project_exception(self):
        """Si create_project_handler lève une exception, fallback legacy."""
        from src.reasoning.handlers.website import generate_website_handler

        ctx = MagicMock()
        ctx.budget_seconds = 300.0
        ctx.llm = AsyncMock()

        with patch("src.reasoning.handlers.website._PROJECT_HANDLER_AVAILABLE", True), \
             patch("src.reasoning.handlers.website.WEBSITE_BUILDER_AVAILABLE", True), \
             patch("src.reasoning.handlers.website._create_project_handler", AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("src.reasoning.handlers.website._generate_website", AsyncMock(return_value="legacy ok")) as mock_legacy, \
             patch("src.reasoning.handlers.website._build_design_directives", return_value=""):

            result = await generate_website_handler(ctx=ctx, description="test site")

        assert result.success
        mock_legacy.assert_called_once()
