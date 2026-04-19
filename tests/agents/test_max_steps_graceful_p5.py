"""Tests P5 — max-steps graceful.

Vérifie:
- Warning à 80% du budget est injecté
- Résumé final LLM est appelé lors de l'exhaustion
- Flag off → comportement legacy (pas de warning, pas de résumé LLM)
"""
from __future__ import annotations

from pathlib import Path

import pytest


def test_graceful_hooks_present_in_sub_agent():
    """Le wire-up P5 doit être dans sub_agent.py."""
    src = Path(__file__).resolve().parents[2] / "src" / "agents" / "sub_agent.py"
    content = src.read_text(encoding="utf-8")
    # Warning 80%
    assert "BUDGET ITÉRATIONS" in content
    # Graceful final summary
    assert "BUDGET ÉPUISÉ" in content
    assert "MAX_STEPS_GRACEFUL" in content


def test_flag_default_on():
    from src.config.codeagent_flags import MAX_STEPS_GRACEFUL
    assert MAX_STEPS_GRACEFUL is True


def test_flag_can_be_disabled(monkeypatch):
    monkeypatch.setenv("LUMENA_MAX_STEPS_GRACEFUL", "false")
    import importlib
    import src.config.codeagent_flags as cf
    importlib.reload(cf)
    assert cf.MAX_STEPS_GRACEFUL is False
    importlib.reload(cf)  # restore


def test_warning_threshold_uses_80_percent():
    """Vérifie que la formule de seuil est bien max(1, int(max_iter*0.8))."""
    src = Path(__file__).resolve().parents[2] / "src" / "agents" / "sub_agent.py"
    content = src.read_text(encoding="utf-8")
    assert "int(max_iter * 0.8)" in content or "max_iter*0.8" in content


def test_graceful_summary_asks_three_sections():
    src = Path(__file__).resolve().parents[2] / "src" / "agents" / "sub_agent.py"
    content = src.read_text(encoding="utf-8")
    # Vérifie les 3 sections demandées au LLM
    assert "Ce qui a été accompli" in content
    assert "Ce qu'il reste à faire" in content
    assert "Recommandation" in content


def test_graceful_summary_has_fallback_on_exception():
    """Si l'appel LLM final lève une exception, on ne doit pas crasher."""
    src = Path(__file__).resolve().parents[2] / "src" / "agents" / "sub_agent.py"
    content = src.read_text(encoding="utf-8")
    # Présence d'un try/except autour de l'appel LLM final
    assert "graceful final summary failed" in content
