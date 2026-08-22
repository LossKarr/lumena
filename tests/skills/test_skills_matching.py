from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.skills.loader import SkillLoader


def _write_skill(base: Path, name: str, description: str, keywords: str = "") -> None:
    skill_dir = base / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    keywords_line = f"keywords: [{keywords}]\n" if keywords else ""
    (skill_dir / "SKILL.md").write_text(
        (
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            f"{keywords_line}"
            "---\n\n"
            f"# {name}\n\n"
            f"Instructions pour {name}\n"
        ),
        encoding="utf-8",
    )


def test_extension_queries_match_expected_skills(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "pdf", "Convertit et gere des PDF", "pdf,document")
    _write_skill(skills_dir, "docx", "Edition de documents Word", "docx,word")
    _write_skill(skills_dir, "pptx", "Creation de presentations PowerPoint", "pptx,presentation,deck")
    _write_skill(skills_dir, "xlsx", "Traitement de tableurs Excel", "xlsx,excel,tableur")

    loader = SkillLoader(base_dirs=[skills_dir])
    loader.load_all()

    assert loader.match_skills("fais un PDF", max_results=3)[0].name == "pdf"
    assert loader.match_skills("modifie ce docx", max_results=3)[0].name == "docx"
    assert loader.match_skills("deck presentation client", max_results=3)[0].name == "pptx"
    assert loader.match_skills("tableur xlsx", max_results=3)[0].name == "xlsx"


def test_generic_web_calculation_does_not_activate_xlsx_extension_intent(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "xlsx", "Traitement de tableurs Excel", "xlsx,excel,tableur")
    _write_skill(skills_dir, "frontend-design", "Design d'interfaces web", "frontend,html,css")

    loader = SkillLoader(base_dirs=[skills_dir])
    loader.load_all()

    web_matches = loader.match_skills(
        "Cree une interface web HTML CSS qui calcule un score impact effort",
        max_results=3,
    )
    assert web_matches
    assert web_matches[0].name == "frontend-design"
    assert all("extension intent:xlsx" not in match.reasons for match in web_matches)

    spreadsheet_matches = loader.match_skills(
        "Cree un tableur Excel avec les calculs et exporte-le en xlsx",
        max_results=3,
    )
    assert spreadsheet_matches[0].name == "xlsx"
    assert "extension intent:xlsx" in spreadsheet_matches[0].reasons


def test_build_active_skills_context_is_bounded(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "pdf", "PDF handler", "pdf")
    _write_skill(skills_dir, "docx", "DOCX handler", "docx")

    loader = SkillLoader(base_dirs=[skills_dir])
    loader.load_all()

    context = loader.build_active_skills_context(
        query="cree un pdf puis un docx",
        max_results=2,
        max_chars=600,
    )
    assert context.startswith("## Bonnes pratiques")
    assert len(context) <= 600
    assert ("`pdf`" in context) or ("`docx`" in context)


def test_directory_skill_wins_over_legacy_file_collision(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "skill-creator", "directory version", "skill")
    (skills_dir / "skill-creator.md").write_text(
        (
            "---\n"
            "name: skill-creator\n"
            "description: legacy version\n"
            "---\n\n"
            "legacy instructions\n"
        ),
        encoding="utf-8",
    )

    loader = SkillLoader(base_dirs=[skills_dir])
    loader.load_all()
    loaded = loader.get_skill("skill-creator")
    assert loaded is not None
    assert loaded.path.is_dir()
    assert "directory version" in loaded.description


def test_real_mcp_skill_loads_as_modern_directory_skill():
    root = Path(__file__).parents[2]
    loader = SkillLoader(base_dirs=[root / "skills"])
    loader.load_all()

    skill = loader.get_skill("mcp-builder")
    assert skill is not None
    assert skill.path.is_dir()
    assert skill.path.name == "mcp-builder"
    # Phase I-7 : doctrine 3-cas — run_mcp_autonomy pour install/active,
    # Phase F pour CRUD, add_mcp(live=false) pour dry-run.
    assert "run_mcp_autonomy" in skill.instructions
    assert "add_mcp" in skill.instructions


def test_real_mcp_skill_matches_missing_external_capability():
    root = Path(__file__).parents[2]
    loader = SkillLoader(base_dirs=[root / "skills"])
    loader.load_all()

    matches = loader.match_skills(
        "trouve et installe un MCP pour connecter une API externe",
        max_results=3,
    )

    assert matches
    assert matches[0].name == "mcp-builder"


def test_real_mcp_skill_context_contains_operating_doctrine():
    root = Path(__file__).parents[2]
    loader = SkillLoader(base_dirs=[root / "skills"])
    loader.load_all()

    context = loader.build_active_skills_context(
        query="il me manque un outil externe MCP pour analyser une API",
        max_results=1,
        max_chars=4000,
    )

    assert "`mcp-builder`" in context
    # Phase I-7 : skill réécrit avec doctrine 3 cas.
    # Cas 1 : run_mcp_autonomy pour install/active/utilise.
    # Cas 2 : Phase F (add_mcp/disable_mcp/...) pour CRUD.
    # Confirmation_phrases I-CONFIRM-* restent obligatoires côté live.
    assert "run_mcp_autonomy" in context
    assert "add_mcp" in context
    assert "I-CONFIRM-MCP-AUTONOMY" in context
    assert "confirmation_phrase" in context
    # Garde-fou doctrinal : Lumena ne demande JAMAIS la phrase technique
    # à l'utilisateur.
    assert "JAMAIS" in context or "Jamais" in context
