from dataclasses import FrozenInstanceError

import pytest

from src.local_models.contracts import CatalogModel, LocalModelSource, ModelReference


def test_contract_serialization_keeps_source_and_unknown_size_distinct():
    ref = ModelReference(LocalModelSource.OLLAMA, "qwen3:8b", "qwen3:8b")
    payload = CatalogModel(reference=ref, display_name="Qwen", size_bytes=None).as_dict()
    assert payload["reference"]["source"] == "ollama"
    assert payload["size_bytes"] is None


def test_reference_is_immutable():
    ref = ModelReference(LocalModelSource.OLLAMA, "qwen3:8b", "qwen3:8b")
    with pytest.raises(FrozenInstanceError):
        ref.canonical = "other:latest"
