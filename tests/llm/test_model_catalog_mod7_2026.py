"""MOD-7 — le catalogue alimente seul les sélecteurs, le setup et l'API."""

from __future__ import annotations

import asyncio
from dataclasses import replace


def test_text_selectors_only_expose_selectable_text_models():
    from src.llm.providers import AVAILABLE_MODELS
    from web.routes.config import _CONFIG_SCHEMA, _refresh_model_selector_options

    _refresh_model_selector_options()
    expected = {
        name for name, model in AVAILABLE_MODELS.items()
        if model.is_selectable() and not model.supports_image_generation
    }
    schema = {entry["key"]: entry for entry in _CONFIG_SCHEMA}
    assert set(schema["LUMENA_DEFAULT_MODEL"]["options"]) == expected
    assert set(schema["LUMENA_JUDGE_MODEL"]["options"]) == expected
    assert schema["LUMENA_DEFAULT_MODEL"]["default"] == "deepseek-flash"
    assert {
        "gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna",
        "claude-opus-5.5", "claude-sonnet-5.5", "grok-4.7",
    } <= expected
    assert "claude-mythos-5.1" not in expected


def test_specialized_selectors_are_derived_from_capabilities():
    from src.llm.providers import AVAILABLE_MODELS
    from web.routes.config import _CONFIG_SCHEMA, _refresh_model_selector_options

    _refresh_model_selector_options()
    schema = {entry["key"]: entry for entry in _CONFIG_SCHEMA}
    expected_vision = {"auto", *(
        name for name, model in AVAILABLE_MODELS.items()
        if model.is_selectable() and model.supports_vision
    )}
    expected_web = {"auto", *(
        name for name, model in AVAILABLE_MODELS.items()
        if model.is_selectable() and model.supports_tools
    )}
    assert set(schema["LUMENA_BRAIN_VISION"]["options"]) == expected_vision
    assert set(schema["LUMENA_BRAIN_WEB"]["options"]) == expected_web


def test_image_selector_is_the_selectable_image_catalog():
    from src.services.image_gen import _MODEL_CATALOG
    from web.routes.config import _CONFIG_SCHEMA, _refresh_model_selector_options

    _refresh_model_selector_options()
    field = next(entry for entry in _CONFIG_SCHEMA if entry["key"] == "LUMENA_BRAIN_IMAGE_GEN")
    expected = {"auto", *(name for name, info in _MODEL_CATALOG.items() if info.selectable)}
    assert set(field["options"]) == expected


def test_text_selectors_refresh_after_dynamic_local_discovery():
    from src.llm.providers import AVAILABLE_MODELS
    from web.routes.config import _CONFIG_SCHEMA, _refresh_model_selector_options

    local = replace(
        AVAILABLE_MODELS["qwen3-8b"],
        name="catalog-dynamic-local",
        display_name="Catalogue Dynamic Local",
        model_id="catalog-dynamic-local:latest",
    )
    AVAILABLE_MODELS[local.name] = local
    try:
        _refresh_model_selector_options()
        schema = {entry["key"]: entry for entry in _CONFIG_SCHEMA}
        assert local.name in schema["LUMENA_DEFAULT_MODEL"]["options"]
    finally:
        AVAILABLE_MODELS.pop(local.name, None)
        _refresh_model_selector_options()


def test_setup_recommendations_are_valid_option_subsets():
    from web.routes.setup import setup_schema

    payload = asyncio.run(setup_schema())
    step = next(item for item in payload["steps"] if item["id"] == "brains")
    fields = {field["key"]: field for field in step["fields"]}
    for key, info in step["brains_info"].items():
        allowed = set(fields[key]["options"]) - {"auto"}
        assert set(info["top"]) <= allowed
        assert set(info["top_free"]) <= allowed


def test_models_api_exposes_catalog_governance_and_pricing(monkeypatch):
    from web.routes import deps
    from web.routes.models import get_models

    monkeypatch.setattr(deps, "lumena", None)
    payload = asyncio.run(get_models())
    entries = {entry["name"]: entry for entry in payload["models"]}

    assert "deepseek-v3" not in entries
    astra = entries["gpt-6-astra"]
    assert astra["lifecycle"] == "stable"
    assert astra["fallback_eligible"] is False
    assert astra["max_output_tokens"] == 128_000
    assert astra["pricing"]["input_per_million"] == 10.0
    assert astra["pricing"]["verified_on"] == "2026-09-20"

    for name in (
        "gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna",
        "claude-opus-5.5", "claude-sonnet-5.5", "grok-4.7",
    ):
        assert entries[name]["lifecycle"] == "stable"
        assert entries[name]["pricing"] is not None
    assert "claude-mythos-5.1" not in entries


def test_plain_cli_is_catalog_driven_and_hides_limited_models(monkeypatch, capsys):
    import src.cli as cli
    from src.llm.providers import AVAILABLE_MODELS

    monkeypatch.setattr(cli, "RICH_AVAILABLE", False)
    monkeypatch.setattr(cli, "MULTI_PROVIDER_AVAILABLE", True)
    monkeypatch.setattr(cli, "AVAILABLE_MODELS", {
        "qwen3-8b": AVAILABLE_MODELS["qwen3-8b"],
        "gpt-6-sol": AVAILABLE_MODELS["gpt-6-sol"],
        "claude-mythos-5.1": AVAILABLE_MODELS["claude-mythos-5.1"],
    })
    monkeypatch.setattr(cli, "check_api_key", lambda _provider: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")

    assert cli.select_model_menu() == "gpt-6-sol"
    output = capsys.readouterr().out
    assert "GPT-6 Sol" in output
    assert "Mythos" not in output
