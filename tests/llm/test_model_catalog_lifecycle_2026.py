"""Régressions des modèles retirés et des sélecteurs actifs."""

from __future__ import annotations

import asyncio

import pytest


RETIRED_TEXT_MODELS = {
    "kimi-k2.5",
    "kimi-k2-0905-preview",
    "kimi-k2-turbo-preview",
    "kimi-k2-thinking",
    "kimi-k2-thinking-turbo",
    "grok-4-1-fast-reasoning",
    "grok-4-1-fast-non-reasoning",
    "deepseek-v3",
    "deepseek-reasoner",
    "devstral",
}


def test_retired_models_remain_readable_but_are_not_active():
    from src.llm.providers import ModelLifecycle, get_model_config, get_selectable_models

    selectable = {model.name for model in get_selectable_models()}
    for name in RETIRED_TEXT_MODELS:
        cfg = get_model_config(name)
        assert cfg is not None, f"La configuration historique {name} doit rester lisible"
        assert cfg.lifecycle == ModelLifecycle.RETIRED
        assert cfg.is_selectable() is False
        assert cfg.is_fallback_eligible() is False
        assert cfg.successor
        assert name not in selectable


def test_retired_models_never_appear_in_resolved_fallbacks():
    from src.llm.providers import MODEL_FALLBACKS, get_model_fallbacks

    for root in MODEL_FALLBACKS:
        assert RETIRED_TEXT_MODELS.isdisjoint(get_model_fallbacks(root))


def test_web_model_catalog_hides_retired_models(monkeypatch):
    from web.routes import deps
    from web.routes.models import get_models

    monkeypatch.setattr(deps, "lumena", None)
    result = asyncio.run(get_models())
    names = {entry["name"] for entry in result["models"]}

    assert RETIRED_TEXT_MODELS.isdisjoint(names)


def test_switching_to_retired_model_returns_migration_error(monkeypatch):
    from fastapi import HTTPException
    from web.routes import deps
    from web.routes.models import ModelSwitchRequest, switch_model

    monkeypatch.setattr(deps, "lumena", None)
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(switch_model(ModelSwitchRequest(model_name="deepseek-v3")))

    assert exc_info.value.status_code == 409
    assert "deepseek-flash" in str(exc_info.value.detail)


def test_dynamic_config_selectors_exclude_retired_models():
    from web.routes.config import _CONFIG_SCHEMA

    for entry in _CONFIG_SCHEMA:
        options = entry.get("options") or []
        if entry.get("type") == "select" and entry["key"] != "LUMENA_BRAIN_IMAGE_GEN":
            assert RETIRED_TEXT_MODELS.isdisjoint(options), entry["key"]
