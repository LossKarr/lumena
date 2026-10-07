"""Contrats du catalogue image actuel (MOD-6)."""

from __future__ import annotations


def test_current_openai_image_models_are_catalogued():
    from src.services.image_gen import _MODEL_CATALOG, _MODEL_PROVIDER

    for name in ("gpt-image-2.5-sunburst", "gpt-image-2.5-flare"):
        assert _MODEL_PROVIDER[name] == "openai"
        assert name in _MODEL_CATALOG
        assert _MODEL_CATALOG[name].selectable is True
        assert "image-edit" in _MODEL_CATALOG[name].capabilities


def test_deprecated_image_models_are_not_auto_routed():
    from src.services.image_gen import _MODEL_CATALOG, _PROVIDER_FALLBACK_ORDER

    retired = {
        "gpt-image-1.5", "gpt-image-1-mini",
        "imagen-4-ultra", "imagen-4", "imagen-4-fast",
        "grok-imagine-image-pro", "grok-imagine-image-quality",
    }
    assert retired.isdisjoint(_PROVIDER_FALLBACK_ORDER)
    for name in retired & set(_MODEL_CATALOG):
        assert _MODEL_CATALOG[name].selectable is False


def test_recraft_v41_exact_ids_have_raster_or_svg_capability():
    from src.services.image_gen import _MODEL_CATALOG, _MODEL_PROVIDER

    names = {
        "recraftv4_1", "recraftv4_1_vector", "recraftv4_1_pro", "recraftv4_1_pro_vector",
        "recraftv4_1_utility", "recraftv4_1_utility_pro",
    }
    for name in names:
        assert _MODEL_PROVIDER[name] == "recraft"
        info = _MODEL_CATALOG[name]
        expected = "svg" if name.endswith("_vector") else "text-to-image"
        assert expected in info.capabilities
    assert {name: _MODEL_CATALOG[name].cost_per_image for name in names} == {
        "recraftv4_1": 0.035,
        "recraftv4_1_vector": 0.08,
        "recraftv4_1_pro": 0.21,
        "recraftv4_1_pro_vector": 0.30,
        "recraftv4_1_utility": 0.035,
        "recraftv4_1_utility_pro": 0.21,
    }


def test_seedream_5_pro_preserves_resolution_prices():
    from src.services.image_gen import _MODEL_CATALOG, _MODEL_PROVIDER

    assert _MODEL_PROVIDER["seedream-5-pro"] == "replicate"
    info = _MODEL_CATALOG["seedream-5-pro"]
    assert info.variant_costs == {"1k": 0.045, "2k": 0.09}
    assert "multi-reference" in info.capabilities


def test_stability_flash_uses_the_official_credit_price():
    from src.services.image_gen import _MODEL_CATALOG

    assert _MODEL_CATALOG["sd3.5-flash"].cost_per_image == 0.025


def test_image_mapping_catalog_and_selectable_fallback_are_symmetric():
    from src.services.image_gen import _MODEL_CATALOG, _MODEL_PROVIDER, _PROVIDER_FALLBACK_ORDER

    assert set(_MODEL_PROVIDER) == set(_MODEL_CATALOG)
    assert set(_PROVIDER_FALLBACK_ORDER) == {
        name for name, info in _MODEL_CATALOG.items() if info.selectable and info.auto_eligible
    }
