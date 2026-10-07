from pathlib import Path

from src.skills.validation import validate_skill_md_text


ROOT = Path(__file__).resolve().parents[2]


def test_personal_model_skill_is_valid_and_uses_dynamic_tools() -> None:
    path = ROOT / "skills" / "personal-model-manager" / "SKILL.md"
    content = path.read_text(encoding="utf-8")
    valid, message = validate_skill_md_text(content)
    assert valid, message
    assert "personal_model_status" in content
    assert "personal_learning_health" in content
    assert "Ne modifie jamais" in content
    assert "enabled=true" in content
    assert "23 768" not in content
