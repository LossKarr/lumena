"""Contrat commun du catalogue de modèles 2026.

Ces tests protègent le socle utilisé par les lots fournisseur suivants sans
imposer immédiatement de nouvelles métadonnées à toutes les entrées legacy.
"""

from __future__ import annotations

import pytest


def test_structured_pricing_preserves_legacy_input_cost():
    from src.llm.providers import ModelConfig, ModelLifecycle, ModelPricing, ProviderType

    pricing = ModelPricing(
        input_per_million=10.0,
        output_per_million=50.0,
        cached_input_per_million=1.0,
        cache_write_per_million=12.5,
        source_url="https://developers.openai.com/api/docs/models/gpt-6-astra",
        verified_on="2026-09-20",
        long_context_threshold=272_000,
        long_context_input_multiplier=2.0,
        long_context_output_multiplier=1.5,
    )
    cfg = ModelConfig(
        name="gpt-6-astra",
        display_name="GPT-6 Astra",
        provider=ProviderType.OPENAI,
        model_id="gpt-6-astra",
        cost_per_million_tokens=10.0,
        pricing=pricing,
        lifecycle=ModelLifecycle.STABLE,
    )

    assert cfg.input_cost_per_million == 10.0
    assert cfg.cost_per_million_tokens == 10.0
    assert pricing.estimate_cost(input_tokens=100_000, output_tokens=100_000) == 6.0
    assert pricing.estimate_cost(input_tokens=300_000, output_tokens=100_000) == 13.5


def test_lifecycle_controls_selection_and_fallback_by_default():
    from src.llm.providers import ModelConfig, ModelLifecycle, ProviderType

    stable = ModelConfig("stable", "Stable", ProviderType.OPENAI, "stable")
    preview = ModelConfig(
        "preview", "Preview", ProviderType.OPENAI, "preview", lifecycle=ModelLifecycle.PREVIEW
    )
    retired = ModelConfig(
        "retired", "Retired", ProviderType.OPENAI, "retired", lifecycle=ModelLifecycle.RETIRED
    )

    assert stable.is_selectable() is True
    assert stable.is_fallback_eligible() is True
    assert preview.is_selectable() is True
    assert preview.is_fallback_eligible() is False
    assert retired.is_selectable() is False
    assert retired.is_fallback_eligible() is False


def test_explicit_policy_can_further_restrict_a_model():
    from src.llm.providers import ModelConfig, ProviderType

    cfg = ModelConfig(
        "account-limited",
        "Account limited",
        ProviderType.ANTHROPIC,
        "account-limited",
        selectable=False,
        fallback_eligible=False,
    )

    assert cfg.is_selectable() is False
    assert cfg.is_fallback_eligible() is False


def test_alias_resolution_is_visible_and_does_not_duplicate_catalog_entries():
    from src.llm.providers import (
        AVAILABLE_MODELS,
        MODEL_ALIASES,
        ModelAlias,
        get_model_config,
        resolve_model_name,
    )

    assert all(alias not in AVAILABLE_MODELS for alias in MODEL_ALIASES)
    for alias, migration in MODEL_ALIASES.items():
        assert isinstance(migration, ModelAlias)
        assert migration.target in AVAILABLE_MODELS
        assert resolve_model_name(alias) == migration.target
        assert get_model_config(alias) is AVAILABLE_MODELS[migration.target]


def test_catalog_validator_reports_identity_alias_and_fallback_failures():
    from src.llm.providers import (
        ModelAlias,
        ModelConfig,
        ModelLifecycle,
        ProviderType,
        validate_model_catalog,
    )

    models = {
        "wrong-key": ModelConfig(
            "actual-name", "Bad", ProviderType.OPENAI, "", context_window=0, max_output_tokens=0
        ),
        "retired": ModelConfig(
            "retired",
            "Retired",
            ProviderType.OPENAI,
            "retired",
            lifecycle=ModelLifecycle.RETIRED,
            selectable=True,
            fallback_eligible=True,
        ),
    }
    errors = validate_model_catalog(
        models=models,
        aliases={"loop-a": ModelAlias("loop-b"), "loop-b": ModelAlias("loop-a")},
        fallbacks={"missing-root": ["missing-target"], "retired": ["retired"]},
    )

    assert any("wrong-key" in error and "name" in error for error in errors)
    assert any("model_id" in error for error in errors)
    assert any("context_window" in error for error in errors)
    assert any("alias loop" in error for error in errors)
    assert any("missing-root" in error for error in errors)
    assert any("retired" in error and "fallback" in error for error in errors)


def test_production_catalog_contract_is_valid():
    from src.llm.providers import validate_model_catalog

    assert validate_model_catalog() == []


def test_models_info_exposes_structured_contract_without_removing_legacy_fields():
    from src.llm.providers import AVAILABLE_MODELS, build_models_info

    info = build_models_info()
    sample_name = next(iter(AVAILABLE_MODELS))
    sample = info[sample_name]

    assert {"provider", "cost", "desc"}.issubset(sample)
    assert {
        "model_id",
        "lifecycle",
        "selectable",
        "fallback_eligible",
        "aliases",
        "pricing",
    }.issubset(sample)


def test_invalid_pricing_is_rejected_early():
    from src.llm.providers import ModelPricing

    with pytest.raises(ValueError, match="non-negative"):
        ModelPricing(input_per_million=-1.0)
    with pytest.raises(ValueError, match="threshold"):
        ModelPricing(long_context_threshold=0)
