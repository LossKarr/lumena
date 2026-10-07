from __future__ import annotations

import json

from src.llm.multi_provider import MultiProviderLLM


def test_explicit_model_keeps_priority_over_personal_default(monkeypatch):
    assert MultiProviderLLM._resolve_initial_model_name("deepseek-flash") == "deepseek-flash"


def test_verified_personal_global_default_is_loaded_on_restart(tmp_path, monkeypatch):
    import src.utils.paths as paths
    registry = tmp_path / "personal_model" / "lineages" / "registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({
        "schema_version": 1,
        "global_default": {"lineage_id": "lineage", "version": "1.0.0"},
        "lineages": {"lineage": {"versions": {"1.0.0": {
            "model_name": "lumena-model-1.0.0", "global_default": True,
            "personal_active": True, "artifact_hashes": {"ollama_canary": "proof"},
        }}}},
    }), encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.delenv("LUMENA_DEFAULT_MODEL", raising=False)
    assert MultiProviderLLM._resolve_initial_model_name(None) == "lumena-model-1.0.0"
