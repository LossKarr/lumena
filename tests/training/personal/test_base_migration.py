from src.training.personal.base_migration import BaseCandidate, BaseMigrationAdvisor, from_scratch_preflight


def test_advisor_returns_at_most_three_compatible_choices_and_preserves_old() -> None:
    candidates = [BaseCandidate(f"model-{size}", size, size * 2, size, size * 2, f"{size}B") for size in (3, 7, 14, 32)]
    result = BaseMigrationAdvisor().recommend({"ram_gb": 32, "vram_gb": 12, "free_disk_gb": 100}, candidates)
    assert result["recommended"]["model_id"] == "model-7"
    assert len(result["alternatives"]) <= 2
    assert result["old_version_preserved"] is True
    assert result["activation_automatic"] is False


def test_from_scratch_refuses_unproven_resources_and_licenses() -> None:
    result = from_scratch_preflight(hardware={"ram_gb": 32, "vram_gb": 12, "free_disk_gb": 200}, licensed_tokens=1000, tokenizer_versioned=False)
    assert result["allowed"] is False
    assert {"licensed_corpus_too_small", "vram_insufficient", "tokenizer_not_versioned"} <= set(result["blockers"])
