from __future__ import annotations

import pytest

from src.training.personal.naming import (
    SemanticVersion,
    build_model_name,
    next_model_name,
    normalize_prefix,
    parse_model_name,
)


def test_default_and_custom_model_names() -> None:
    assert build_model_name() == "lumena-model-1.0.0"
    assert normalize_prefix("") == "lumena"
    assert build_model_name("Mon Modèle") == "mon-modele-model-1.0.0"
    assert next_model_name("nova-model-1.2.9") == "nova-model-1.2.10"
    assert next_model_name("nova-model-1.2.9", "minor") == "nova-model-1.3.0"
    assert next_model_name("nova-model-1.2.9", "major") == "nova-model-2.0.0"


def test_name_parse_roundtrip() -> None:
    prefix, version = parse_model_name("atelier-model-12.3.4")
    assert prefix == "atelier"
    assert version == SemanticVersion(12, 3, 4)
    assert build_model_name(prefix, version) == "atelier-model-12.3.4"


@pytest.mark.parametrize("value", ["---", "***", "a" * 49])
def test_invalid_prefixes_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="model_prefix_invalid"):
        normalize_prefix(value)


@pytest.mark.parametrize("value", ["lumena", "Lumena-model-1.0.0", "lumena-model-01.0.0", "lumena-1.0.0"])
def test_invalid_personal_model_names_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="personal_model_name_invalid"):
        parse_model_name(value)
