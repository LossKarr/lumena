from pathlib import Path

from src.skills.loader import SkillLoader
from src.skills.validation import validate_skill_dir


ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = ROOT / "skills" / "lumena-ide-operator"


def _loader() -> SkillLoader:
    loader = SkillLoader(base_dirs=[ROOT / "skills"])
    loader.load_all()
    return loader


def test_lumena_ide_operator_is_a_valid_loadable_skill():
    valid, message = validate_skill_dir(SKILL_DIR)
    assert valid, message

    skill = _loader().get_skill("lumena-ide-operator")
    assert skill is not None
    assert {"ide", "éditeur", "editor", "lumena_ide", "ide_launch"} == set(
        skill.apply_to
    )


def test_lumena_ide_operator_matches_real_user_phrasings_first():
    loader = _loader()
    queries = (
        "ouvre ton IDE avec ce workspace",
        "lis le buffer non sauvegardé dans ton éditeur",
        "ouvre une nouvelle instance de Lumena IDE",
        "reconnecte le pont IDE puis retourne sur cet onglet",
    )

    for query in queries:
        matches = loader.match_skills(query, max_results=3)
        assert matches and matches[0].name == "lumena-ide-operator", query


def test_apply_to_releve_un_match_ide_court_au_dessus_du_seuil():
    match = _loader().match_skills("ide", max_results=1)[0]

    assert match.name == "lumena-ide-operator"
    assert match.score >= 9.0
    assert "applyTo:ide" in match.reasons


def test_lumena_ide_operator_does_not_pollute_unrelated_chat():
    context = _loader().build_active_skills_context(
        "explique pourquoi cette fonction Python retourne None",
        max_results=3,
        max_chars=20_000,
    )

    assert "`lumena-ide-operator`" not in context


def test_context_preserves_instance_policy_recovery_and_proof():
    context = _loader().build_active_skills_context(
        "ouvre ton IDE et lis le buffer non sauvegardé",
        max_results=1,
        max_chars=20_000,
    )

    assert "`lumena-ide-operator`" in context
    assert "catalogue vivant" in context
    assert "instance dédiée" in context
    assert "Ne contourne jamais un refus IDE" in context
    assert "effet observé" in context
    assert "préenregistré" in context
