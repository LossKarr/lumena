from pathlib import Path

from src.skills.loader import SkillLoader
from src.skills.validation import validate_skill_dir


ROOT = Path(__file__).parents[2]


def _loader():
    loader = SkillLoader(base_dirs=[ROOT / "skills"])
    loader.load_all()
    return loader


def test_voice_manager_skill_is_valid_and_loadable():
    path = ROOT / "skills" / "voice-manager"
    ok, message = validate_skill_dir(path)
    assert ok, message
    skill = _loader().get_skill("voice-manager")
    assert skill is not None
    assert set(skill.apply_to) == {
        "inspect_voice_runtime",
        "diagnose_voice",
        "recommend_voice_configuration",
        "apply_voice_recommendation",
        "verify_voice_configuration",
        "rollback_voice_configuration",
    }


def test_voice_requests_activate_voice_manager_skill():
    loader = _loader()
    for query in (
        "pourquoi ta voix qwen est lente",
        "règle ta voix pour parler plus vite",
        "quel moteur tts utilises-tu réellement",
        "je veux la meilleure qualité vocale entièrement locale",
        "repasse au réglage vocal précédent",
        "configure ton micro en push to talk",
    ):
        matches = loader.match_skills(query, max_results=5)
        assert "voice-manager" in [match.name for match in matches]


def test_skill_requires_proof_and_has_no_shell_install_path():
    skill = _loader().get_skill("voice-manager")
    assert skill is not None
    text = skill.instructions
    assert "verified" in text
    assert "settings_pending" in text
    assert "Ne lance pas `pip`" in text
