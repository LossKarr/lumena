"""Tests du validateur unique src/skills/validation.py (source de vérité S2/S3)."""

import pytest

from src.skills.validation import (
    is_generic_description,
    parse_frontmatter,
    validate_skill_md_text,
    validate_skill_dir,
)


# ─── is_generic_description (règle trigger garanti S3) ──────────────────────

@pytest.mark.parametrize("desc,name", [
    ("", "weather"),
    ("   ", "weather"),
    ("[TODO: décrire]", "weather"),
    ("Skill weather", "weather"),
    ("Skill pour Weather", "weather"),
    ("skill", "weather"),
    ("compétence", "weather"),
    ("Skill ghost", "ghost"),
])
def test_generic_descriptions_detected(desc, name):
    assert is_generic_description(desc, name=name) is True


@pytest.mark.parametrize("desc", [
    "Récupère la météo d'une ville via Open-Meteo et la résume",
    "Génère un rapport PDF à partir d'un CSV",
    "Convertit des fichiers audio en texte",
])
def test_real_descriptions_not_generic(desc):
    assert is_generic_description(desc, name="weather") is False


def test_generic_uses_skill_name():
    # "Skill météo" est générique pour un skill nommé "meteo"
    assert is_generic_description("Skill météo", name="meteo") is True
    # mais une vraie phrase contenant le nom ne l'est pas
    assert is_generic_description("Affiche la météo en direct", name="meteo") is False


# ─── parse_frontmatter ──────────────────────────────────────────────────────

def test_parse_frontmatter_ok():
    ok, data = parse_frontmatter('---\nname: x\ndescription: "hello"\n---\n\nbody')
    assert ok is True
    assert data["name"] == "x"
    assert data["description"] == "hello"


def test_parse_frontmatter_missing():
    ok, msg = parse_frontmatter("no frontmatter here")
    assert ok is False
    assert isinstance(msg, str)


def test_parse_frontmatter_unclosed():
    ok, msg = parse_frontmatter("---\nname: x\npas de fermeture")
    assert ok is False


# ─── validate_skill_md_text ─────────────────────────────────────────────────

def test_md_text_valid():
    md = '---\nname: meteo\ndescription: "Affiche la météo en direct"\n---\n\n# Météo\n'
    ok, msg = validate_skill_md_text(md, expected_name="meteo")
    assert ok is True


def test_md_text_generic_desc_rejected():
    md = '---\nname: meteo\ndescription: "Skill meteo"\n---\n\nbody\n'
    ok, msg = validate_skill_md_text(md, expected_name="meteo")
    assert ok is False
    assert "générique" in msg.lower() or "generique" in msg.lower()


def test_md_text_bad_name():
    md = '---\nname: Meteo_Bad\ndescription: "Une vraie description claire"\n---\n\nbody\n'
    ok, msg = validate_skill_md_text(md)
    assert ok is False


def test_md_text_name_mismatch():
    md = '---\nname: meteo\ndescription: "Une vraie description claire"\n---\n\nbody\n'
    ok, msg = validate_skill_md_text(md, expected_name="autre")
    assert ok is False


# ─── validate_skill_dir ─────────────────────────────────────────────────────

def test_dir_valid(tmp_path):
    d = tmp_path / "meteo"
    d.mkdir()
    (d / "SKILL.md").write_text(
        '---\nname: meteo\ndescription: "Affiche la météo en direct"\n---\n\n# Météo\n',
        encoding="utf-8",
    )
    ok, msg = validate_skill_dir(d)
    assert ok is True


def test_dir_missing_skill_md(tmp_path):
    d = tmp_path / "vide"
    d.mkdir()
    ok, msg = validate_skill_dir(d)
    assert ok is False
    assert "SKILL.md" in msg


def test_dir_unknown_subdir(tmp_path):
    d = tmp_path / "meteo"
    d.mkdir()
    (d / "SKILL.md").write_text(
        '---\nname: meteo\ndescription: "Affiche la météo en direct"\n---\n\nbody\n',
        encoding="utf-8",
    )
    (d / "random").mkdir()
    ok, msg = validate_skill_dir(d)
    assert ok is False
    assert "non reconnu" in msg


def test_dir_script_syntax_error(tmp_path):
    d = tmp_path / "meteo"
    (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        '---\nname: meteo\ndescription: "Affiche la météo en direct"\n---\n\nbody\n',
        encoding="utf-8",
    )
    (d / "scripts" / "broken.py").write_text("def (:\n", encoding="utf-8")
    ok, msg = validate_skill_dir(d)
    assert ok is False
    assert "syntaxe" in msg.lower()
