import pytest

from src.local_models.contracts import LocalModelSource
from src.local_models.identifiers import IdentifierError, parse_model_reference


@pytest.mark.parametrize("value", [
    "qwen3:8b", "library/qwen3:8b", "nomic-embed-text", "model.name-v2:Q4_K_M",
])
def test_valid_ollama_identifiers(value):
    ref = parse_model_reference(value, "ollama")
    assert ref.source is LocalModelSource.OLLAMA
    assert ref.pull_reference


def test_huggingface_reference_is_normalized_for_ollama():
    ref = parse_model_reference("hf.co/bartowski/Llama-3.2-GGUF:Q4_K_M")
    assert ref.source is LocalModelSource.HUGGINGFACE
    assert ref.repository == "bartowski/Llama-3.2-GGUF"
    assert ref.pull_reference == "hf.co/bartowski/Llama-3.2-GGUF:Q4_K_M"


@pytest.mark.parametrize("value", [
    "", " qwen3:8b", "-qwen3:8b", "../qwen", "owner/../repo", "https://evil/model",
    "qwen\\model", "qwen\n:8b", "owner/repo/extra", "hf.co/owner/repo:../../bad",
])
def test_unsafe_references_fail_closed(value):
    with pytest.raises(IdentifierError):
        parse_model_reference(value, "huggingface" if "owner" in value else None)


def test_reference_length_is_bounded():
    with pytest.raises(IdentifierError):
        parse_model_reference("a" * 385)
