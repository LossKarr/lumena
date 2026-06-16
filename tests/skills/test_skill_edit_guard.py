"""Tests F2c — garde anti-édition brute d'un SKILL.md + F3 matching remotion.

F2c : éditer un skills/<x>/SKILL.md via edit_file/insert_at_anchor… doit être
refusé et rediriger vers update_skill (sinon la validation est contournée).
"""

import pytest

from src.reasoning.caller_context import REACT, CODEAGENT
from src.reasoning.tool_registry import ToolRegistry


# ─── _is_skill_md_path (pur) ────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "skills/compte-rendu-reunion/SKILL.md",
    "skills/pdf/SKILL.md",
    r"C:\Users\x\lumena\skills\meteo\SKILL.md",
    "C:/Users/x/lumena/skills/meteo/skill.md",
])
def test_is_skill_md_path_true(path):
    assert ToolRegistry._is_skill_md_path(path) is True


@pytest.mark.parametrize("path", [
    "skills/pdf/references/guide.md",      # pas un SKILL.md
    "src/core.py",
    "workspace/notes/SKILL.md",            # pas sous skills/<x>/
    "skills/SKILL.md",                     # pas assez profond
    "",
])
def test_is_skill_md_path_false(path):
    assert ToolRegistry._is_skill_md_path(path) is False


# ─── _skill_edit_guard ──────────────────────────────────────────────────────

@pytest.fixture
def reg(tmp_path):
    return ToolRegistry(lumena=None, lumena_root=tmp_path)


@pytest.mark.parametrize("tool", [
    "edit_file", "insert_at_anchor", "write_file", "str_replace",
    "edit_by_lines", "multi_edit_file", "apply_patch",
])
def test_guard_bloque_edition_skill_md(reg, tool):
    obs = reg._skill_edit_guard(
        tool, {"path": "skills/meteo/SKILL.md"}, REACT
    )
    assert obs is not None
    assert obs.success is False
    assert "update_skill" in obs.content


def test_guard_laisse_passer_autre_fichier(reg):
    assert reg._skill_edit_guard("edit_file", {"path": "src/core.py"}, REACT) is None


def test_guard_laisse_passer_outil_non_edit(reg):
    assert reg._skill_edit_guard("read_file", {"path": "skills/meteo/SKILL.md"}, REACT) is None


def test_guard_seulement_react(reg):
    # Un caller non-react (CodeAgent) n'est pas bloqué par ce garde.
    assert reg._skill_edit_guard("edit_file", {"path": "skills/meteo/SKILL.md"}, CODEAGENT) is None


# ─── F3 — le faux positif remotion sur « compte-rendu » est corrigé ─────────

def test_remotion_ne_matche_plus_compte_rendu():
    from src.skills.loader import EXTENSION_TRIGGER_MAP
    assert "rendu" not in EXTENSION_TRIGGER_MAP["remotion"]
