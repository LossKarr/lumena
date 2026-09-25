from src.local_models.recommendations import recommend_catalog_models


def test_recommendation_is_explainable_and_prefers_fit():
    hardware = {"gpu": {"vram_free_bytes": 8_000}, "ram_available_bytes": 16_000, "disk_free_bytes": 100_000}
    models = [
        {"display_name": "large", "size_bytes": 20_000, "capabilities": ["code"], "license": None},
        {"display_name": "fit", "size_bytes": 4_000, "capabilities": ["code"], "license": "apache-2.0"},
    ]
    ranked = recommend_catalog_models(models, hardware, intent="code")
    assert ranked[0]["model"]["display_name"] == "fit"
    assert "fits_free_vram" in ranked[0]["reasons"]
    assert ranked[0]["confidence"] == "high"


def test_unknown_size_and_license_are_not_invented():
    result = recommend_catalog_models(
        [{"display_name": "unknown", "capabilities": ["text"]}],
        {"gpu": {}, "ram_available_bytes": 1, "disk_free_bytes": 1},
    )[0]
    assert result["unknowns"] == ["download_size", "license"]
    assert result["confidence"] == "low"
