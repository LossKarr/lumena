"""
Tests unitaires pour handlers/skills.py — 8 handlers.

Convention: chaque handler reçoit un HandlerContext et retourne HandlerResult.
Les dépendances externes sont mockées via patch.dict(sys.modules).
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.skills import (
    read_own_code_handler,
    create_skill_handler,
    list_skills_handler,
    pip_check_handler,
    search_in_code_handler,
    get_my_capabilities_handler,
    rollback_handler,
    list_backups_handler,
    get_skills_handler_defs,
)


@pytest.fixture
def ctx(tmp_path):
    """HandlerContext minimal pour tests."""
    return HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path / "workspace")


# ─── read_own_code ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_own_code_file_found(ctx, tmp_path):
    """Lecture d'un fichier existant retourne son contenu."""
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "core.py").write_text("print('hello')", encoding="utf-8")
    r = await read_own_code_handler(ctx, file_path="core.py")
    assert r.success
    assert "print('hello')" in r.output


@pytest.mark.asyncio
async def test_read_own_code_file_not_found(ctx):
    """Fichier inexistant retourne erreur."""
    r = await read_own_code_handler(ctx, file_path="nope.py")
    assert not r.success
    assert "non trouvé" in r.output or "non trouvé" in (r.error or "")


@pytest.mark.asyncio
async def test_read_own_code_directory(ctx, tmp_path):
    """Lecture d'un dossier retourne la liste des entrées."""
    src_dir = tmp_path / "src" / "tools"
    src_dir.mkdir(parents=True)
    (src_dir / "a.py").write_text("a", encoding="utf-8")
    (src_dir / "b.py").write_text("b", encoding="utf-8")
    r = await read_own_code_handler(ctx, file_path="tools")
    assert r.success
    assert "a.py" in r.output


@pytest.mark.asyncio
async def test_read_own_code_line_range(ctx, tmp_path):
    """Lecture avec range de lignes."""
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "big.py").write_text("\n".join(f"line{i}" for i in range(100)), encoding="utf-8")
    r = await read_own_code_handler(ctx, file_path="big.py", start_line=5, end_line=10)
    assert r.success
    assert "lignes 5-10" in r.output


# ─── create_skill ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_skill_success(ctx, tmp_path):
    """Création de skill quand le module skills existe."""
    (tmp_path / "skills").mkdir()
    mock_skills = MagicMock()
    mock_skills.reload_skills = MagicMock()
    with patch.dict(sys.modules, {"src.skills": mock_skills}):
        r = await create_skill_handler(ctx, name="test_skill", content="Hello skill")
    assert r.success
    assert "cree avec succes" in r.output


@pytest.mark.asyncio
async def test_create_skill_already_exists(ctx, tmp_path):
    """Création échoue si le skill existe déjà."""
    skill_dir = tmp_path / "skills" / "my-skill"
    skill_dir.mkdir(parents=True)
    r = await create_skill_handler(ctx, name="my_skill", content="content")
    assert not r.success
    assert "existe deja" in r.output or "existe deja" in (r.error or "")


@pytest.mark.asyncio
async def test_create_skill_invalid_name(ctx, tmp_path):
    """Nom invalide retourne erreur."""
    (tmp_path / "skills").mkdir()
    r = await create_skill_handler(ctx, name="!!!", content="content")
    assert not r.success


# ─── list_skills ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_skills_success(ctx):
    """list_skills retourne le résultat du module skills."""
    mock_skills = MagicMock()
    mock_skills.list_skills = MagicMock(return_value="skill1, skill2")
    with patch.dict(sys.modules, {"src.skills": mock_skills}):
        r = await list_skills_handler(ctx)
    assert r.success
    assert "skill1" in r.output


@pytest.mark.asyncio
async def test_list_skills_import_error(ctx):
    """ImportError retourne erreur gracieuse."""
    with patch.dict(sys.modules, {"src.skills": None}):
        r = await list_skills_handler(ctx)
    assert not r.success


# ─── pip_check ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pip_check_installed(ctx):
    """Package installé (os est toujours dispo)."""
    r = await pip_check_handler(ctx, package="os")
    assert r.success
    assert "installé" in r.output


@pytest.mark.asyncio
async def test_pip_check_not_installed(ctx):
    """Package non installé."""
    r = await pip_check_handler(ctx, package="nonexistent_package_xyz_12345")
    assert r.success  # pas un fail, juste info
    assert "PAS installé" in r.output


# ─── search_in_code ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_in_code_success(ctx):
    """search_in_code retourne les résultats de l'improver."""
    mock_improver = MagicMock()
    mock_improver.search_in_code.return_value = "Found 3 matches"
    mock_module = MagicMock()
    mock_module.get_self_improver = MagicMock(return_value=mock_improver)
    with patch.dict(sys.modules, {"src.autonomy.self_improve": mock_module}):
        r = await search_in_code_handler(ctx, query="test")
    assert r.success
    assert "Found 3 matches" in r.output


@pytest.mark.asyncio
async def test_search_in_code_import_error(ctx):
    """Module manquant retourne erreur."""
    with patch.dict(sys.modules, {"src.autonomy.self_improve": None}):
        r = await search_in_code_handler(ctx, query="test")
    assert not r.success
    assert "non disponible" in (r.error or r.output)


# ─── get_my_capabilities ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_my_capabilities_success(ctx):
    """get_my_capabilities retourne les capacités."""
    mock_improver = MagicMock()
    mock_improver.get_my_capabilities.return_value = "I can do everything"
    mock_module = MagicMock()
    mock_module.get_self_improver = MagicMock(return_value=mock_improver)
    with patch.dict(sys.modules, {"src.autonomy.self_improve": mock_module}):
        r = await get_my_capabilities_handler(ctx)
    assert r.success
    assert "everything" in r.output


# ─── rollback ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rollback_success(ctx):
    """rollback retourne le message de l'improver."""
    mock_improver = MagicMock()
    mock_improver.rollback.return_value = (True, "Restored OK")
    mock_module = MagicMock()
    mock_module.get_self_improver = MagicMock(return_value=mock_improver)
    with patch.dict(sys.modules, {"src.autonomy.self_improve": mock_module}):
        r = await rollback_handler(ctx)
    assert r.success
    assert "Restored OK" in r.output


@pytest.mark.asyncio
async def test_rollback_import_error(ctx):
    """Module manquant retourne erreur."""
    with patch.dict(sys.modules, {"src.autonomy.self_improve": None}):
        r = await rollback_handler(ctx)
    assert not r.success


# ─── list_backups ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_backups_empty(ctx):
    """Aucun backup retourne message approprié."""
    mock_improver = MagicMock()
    mock_improver.list_backups.return_value = []
    mock_module = MagicMock()
    mock_module.get_self_improver = MagicMock(return_value=mock_improver)
    with patch.dict(sys.modules, {"src.autonomy.self_improve": mock_module}):
        r = await list_backups_handler(ctx)
    assert r.success
    assert "Aucun backup" in r.output


@pytest.mark.asyncio
async def test_list_backups_with_data(ctx):
    """Backups présents retourne la liste."""
    mock_improver = MagicMock()
    mock_improver.list_backups.return_value = [
        {"session": "2026-03-01", "file_count": 5},
        {"session": "2026-03-02", "file_count": 10},
    ]
    mock_module = MagicMock()
    mock_module.get_self_improver = MagicMock(return_value=mock_improver)
    with patch.dict(sys.modules, {"src.autonomy.self_improve": mock_module}):
        r = await list_backups_handler(ctx)
    assert r.success
    assert "2026-03-01" in r.output
    assert "5 fichiers" in r.output


# ─── HandlerDefs ───────────────────────────────────────────────────────────

def test_handler_defs_count():
    """get_skills_handler_defs retourne exactement 14 defs."""
    defs = get_skills_handler_defs()
    assert len(defs) == 14


def test_handler_defs_names():
    """Chaque def a un nom unique et non-vide."""
    defs = get_skills_handler_defs()
    names = [d.name for d in defs]
    assert len(names) == len(set(names))
    assert all(names)


def test_handler_defs_expected_names():
    """Les noms correspondent à l'inventaire."""
    expected = {
        "read_own_code", "create_skill", "list_skills", "pip_check",
        "search_in_code", "get_my_capabilities", "rollback", "list_backups",
        "execute_skill",
        "reload_skills", "sync_skills_main", "read_skill_reference",
        "edit_own_code", "run_tests",
    }
    defs = get_skills_handler_defs()
    actual = {d.name for d in defs}
    assert actual == expected


def test_handler_defs_have_handlers():
    """Chaque def a un handler callable."""
    for d in get_skills_handler_defs():
        assert callable(d.handler), f"{d.name} handler not callable"
