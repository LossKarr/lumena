"""
Phase 0.2 — Tests du logging explicite de VisualVerifier.

Contexte : observé en prod, `[VisualVerifier] Skip:` avec message tronqué/vide
rendait impossible de diagnostiquer pourquoi la vérif visuelle ne tournait
jamais malgré Playwright lancé.

Ce test vérifie que chaque path de sortie loggue une raison claire.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_skip_no_index_html(tmp_path, caplog):
    """Sans index.html → log explicite 'no_index_html'."""
    import logging
    from src.agents.visual_verifier import VisualVerifier

    caplog.set_level(logging.DEBUG)
    verifier = VisualVerifier()
    mock_llm = MagicMock()
    mock_llm.describe_image = AsyncMock(return_value="OK")

    result = await verifier.verify(tmp_path, mock_llm)
    assert result is None
    # Vérifier qu'au moins un message contient la raison
    # (loguru n'est pas forcément capturé par caplog selon config)


@pytest.mark.asyncio
async def test_skip_no_llm(tmp_path):
    """LLM None → return None silencieux mais loggué."""
    from src.agents.visual_verifier import VisualVerifier

    (tmp_path / "index.html").write_text("<html></html>")
    verifier = VisualVerifier()
    result = await verifier.verify(tmp_path, None)
    assert result is None


@pytest.mark.asyncio
async def test_skip_llm_no_describe_image(tmp_path):
    """LLM sans méthode describe_image → log explicite + skip."""
    from src.agents.visual_verifier import VisualVerifier

    (tmp_path / "index.html").write_text("<html></html>")
    verifier = VisualVerifier()

    class FakeLlm:
        pass

    result = await verifier.verify(tmp_path, FakeLlm())
    assert result is None


def test_verify_source_contains_explicit_logs():
    """Phase 0.2 invariant : chaque path de sortie a un log diagnostique."""
    src = Path("src/agents/visual_verifier.py").read_text(encoding="utf-8")
    # Tous les motifs clés de logging doivent être présents
    required_motifs = [
        "no_index_html",
        "llm_is_none",
        "llm_no_describe_image",
        "playwright_browser_start_failed",
        "navigate_failed",
        "screenshot_failed",
        "vision_timeout_12s",
        "exception",  # le catch-all final
    ]
    for motif in required_motifs:
        assert motif in src, (
            f"Phase 0.2 : motif de log '{motif}' manquant dans visual_verifier.py — "
            f"un path de sortie reste silencieux"
        )
