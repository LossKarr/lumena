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


def test_build_active_skills_context_is_bounded(tmp_path: Path):
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "pdf", "PDF handler", "pdf")
    _write_skill(skills_dir, "docx", "DOCX handler", "docx")

    loader = SkillLoader(base_dirs=[skills_dir])
    loader.load_all()

    context = loader.build_active_skills_context(
        query="cree un pdf puis un docx",
        max_results=2,
        max_chars=220,
    )
    assert context.startswith("## Skills actifs")
    assert len(context) <= 220
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
