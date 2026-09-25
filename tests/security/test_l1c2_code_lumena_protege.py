"""Lot L1c-2 - le code de Lumena est protege ; `edit_own_code` en est la seule porte.

Decisions de Charles du 15 septembre 2026 :
- « code de Lumena » = tout le depot SAUF `workspace/` (src/, web/, ide/, tests/,
  scripts/, fichiers de la racine) ;
- `edit_own_code` reste la SEULE porte vers ce code : sauvegarde avant, preuve
  (relecture) apres, jamais en mission (l'autonomie est deja refusee par le contrat
  de categorie `skills` au registre).

Mesure du 15 septembre : 0 ecriture d'outil vers src/, web/, ide/, tests/ dans tous
les logs ; 172 cibles `apply_patches` au journal d'audit, toutes au workspace.

Si la racine d'espace de travail EST la racine Lumena (configuration legere des
tests, projet sans marqueurs), rien n'est « code de Lumena » : comportement inchange.
Tout se passe dans une racine JETABLE.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.agents.codeagent_todo import CodeAgentTodoState
from src.agents.sub_agent import CodeAgent
from src.reasoning.handlers import files as files_mod
from src.reasoning.handlers.batch import apply_patches_handler
from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.skills import edit_own_code_handler
from src.reasoning.handlers.website import write_website_files_handler
from src.tools.file_guardrails import WorkspaceFileGuardrails

ORIGINAL = "ORIGINAL\n"
CODE = ("src/core.py", "web/app.js", "ide/main.ts", "tests/test_x.py", "lumena_ultime.py")


@pytest.fixture
def racine(tmp_path, monkeypatch):
    root = tmp_path / "lumena"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    for rel in CODE + (".env",):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(ORIGINAL, encoding="utf-8")
    (workspace / "app.txt").write_text(ORIGINAL, encoding="utf-8")
    monkeypatch.setattr(WorkspaceFileGuardrails, "_workspace_root", lambda self: workspace)
    backups = tmp_path / "backups"
    monkeypatch.setattr("src.utils.paths.BACKUPS_DIR", backups)
    return root, workspace, backups


def _ctx(root: Path, ws: Path) -> HandlerContext:
    ctx = HandlerContext.for_testing(lumena_root=root, runtime_root=ws)
    ctx.outside_access_grant = None
    return ctx


def _intact(root: Path, rel: str) -> bool:
    return (root / rel).read_text(encoding="utf-8") == ORIGINAL


def _refus_code(result) -> bool:
    texte = str(result.output)
    return (not result.success) and "refus" in texte.lower() and "edit_own_code" in texte


# ── 1. Portes generiques : le code de Lumena est refuse ──────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("rel", CODE)
async def test_write_file_refuse_code_lumena(racine, rel):
    root, ws, _ = racine
    r = await files_mod.write_file_handler(_ctx(root, ws), path=str(root / rel), content="ECRASE\n")
    assert _refus_code(r) and _intact(root, rel)


@pytest.mark.asyncio
@pytest.mark.parametrize("rel", CODE)
async def test_edit_file_refuse_code_lumena(racine, rel):
    root, ws, _ = racine
    r = await files_mod.edit_file_handler(_ctx(root, ws), file_path=str(root / rel),
                                         old_content="ORIGINAL", new_content="ECRASE")
    assert _refus_code(r) and _intact(root, rel)


@pytest.mark.asyncio
async def test_multi_edit_file_refuse_code_lumena(racine):
    root, ws, _ = racine
    r = await files_mod.multi_edit_file_handler(_ctx(root, ws), edits=[
        {"file_path": str(root / "src/core.py"), "old_content": "ORIGINAL", "new_content": "ECRASE"}])
    assert _refus_code(r) and _intact(root, "src/core.py")


@pytest.mark.asyncio
async def test_apply_patch_refuse_code_lumena(racine):
    root, ws, _ = racine
    r = await files_mod.apply_patch_handler(_ctx(root, ws), file_path=str(root / "src/core.py"),
                                           old_content="ORIGINAL", new_content="ECRASE")
    assert _refus_code(r) and _intact(root, "src/core.py")


@pytest.mark.asyncio
async def test_apply_patch_new_refuse_code_lumena(racine):
    root, ws, _ = racine
    patch = "*** Begin Patch\n*** Update File: src/core.py\n@@\n-ORIGINAL\n+ECRASE\n*** End Patch"
    r = await files_mod.apply_patch_new_handler(_ctx(root, ws), patch_content=patch)
    assert _refus_code(r) and _intact(root, "src/core.py")


@pytest.mark.asyncio
async def test_insert_at_anchor_refuse_code_lumena(racine):
    root, ws, _ = racine
    r = await files_mod.insert_at_anchor_handler(_ctx(root, ws), path=str(root / "src/core.py"),
                                                anchor="ORIGINAL", content="ECRASE", position="after")
    assert _refus_code(r) and _intact(root, "src/core.py")


@pytest.mark.asyncio
async def test_apply_patches_refuse_code_lumena(racine):
    root, ws, _ = racine
    r = await apply_patches_handler(_ctx(root, ws), patches=[
        {"file": str(root / "web/app.js"), "old": "ORIGINAL", "new": "ECRASE"}])
    assert _refus_code(r) and _intact(root, "web/app.js")


@pytest.mark.asyncio
async def test_create_directory_refuse_dans_le_code(racine):
    root, ws, _ = racine
    cible = root / "src" / "nouveau_paquet"
    r = await files_mod.create_directory_handler(_ctx(root, ws), path=str(cible))
    assert _refus_code(r) and not cible.exists()


@pytest.mark.asyncio
async def test_write_website_files_refuse_sortie_dans_le_code(racine):
    root, ws, _ = racine
    sortie = root / "web" / "site"
    r = await write_website_files_handler(
        _ctx(root, ws), json_data={"project_name": "s", "files": {"index.html": "<html></html>"}},
        output_dir=str(sortie))
    assert _refus_code(r) and not sortie.exists()


@pytest.mark.asyncio
async def test_create_zip_sans_sortie_va_dans_le_workspace(racine):
    """Avant : archive posee a la RACINE Lumena. Une creation va au workspace."""
    root, ws, _ = racine
    r = await files_mod.create_zip_handler(_ctx(root, ws), source_paths=str(ws / "app.txt"))
    assert r.success, r.output
    assert not list(root.glob("archive_*.zip"))
    assert len(list(ws.glob("archive_*.zip"))) == 1


# ── 2. Caracterisations : rien d'autre ne change ─────────────────────────────

@pytest.mark.asyncio
async def test_workspace_reste_inscriptible(racine):
    root, ws, _ = racine
    r = await files_mod.edit_file_handler(_ctx(root, ws), file_path=str(ws / "app.txt"),
                                         old_content="ORIGINAL", new_content="MODIFIE")
    assert r.success and (ws / "app.txt").read_text(encoding="utf-8") == "MODIFIE\n"


@pytest.mark.asyncio
async def test_racine_egale_workspace_rien_n_est_protege(tmp_path, monkeypatch):
    """Configuration legere (racine d'espace de travail = racine) : inchange."""
    root = tmp_path / "leger"
    (root / "src").mkdir(parents=True)
    (root / "src" / "core.py").write_text(ORIGINAL, encoding="utf-8")
    monkeypatch.setattr(WorkspaceFileGuardrails, "_workspace_root", lambda self: root)
    r = await files_mod.edit_file_handler(_ctx(root, root), file_path=str(root / "src/core.py"),
                                         old_content="ORIGINAL", new_content="MODIFIE")
    assert r.success, r.output
    assert (root / "src" / "core.py").read_text(encoding="utf-8") == "MODIFIE\n"


# ── 3. edit_own_code : la seule porte, sauvegarde + preuve, jamais en mission ─

@pytest.mark.asyncio
async def test_edit_own_code_modifie_avec_sauvegarde_et_preuve(racine):
    root, ws, backups = racine
    r = await edit_own_code_handler(_ctx(root, ws), file_path="src/core.py",
                                    old_content="ORIGINAL", new_content="AMELIORE", reason="test")
    assert r.success, r.output
    assert (root / "src/core.py").read_text(encoding="utf-8") == "AMELIORE\n"
    copies = list(backups.rglob("core.py"))
    assert len(copies) == 1 and copies[0].read_text(encoding="utf-8") == ORIGINAL
    assert "sauvegarde" in str(r.output).lower()


@pytest.mark.asyncio
async def test_edit_own_code_refuse_en_mission(racine):
    root, ws, backups = racine
    ctx = _ctx(root, ws)
    ctx.is_mission_run = True
    ctx.runtime_task_id = "task-l1c2"
    r = await edit_own_code_handler(ctx, file_path="src/core.py",
                                    old_content="ORIGINAL", new_content="AMELIORE")
    assert not r.success and "mission" in str(r.output).lower()
    assert _intact(root, "src/core.py") and not backups.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("chemin", [".env", "src/../../dehors.txt", "workspace/app.txt"])
async def test_edit_own_code_refuse_hors_code(racine, chemin):
    root, ws, _ = racine
    (root.parent / "dehors.txt").write_text(ORIGINAL, encoding="utf-8")
    r = await edit_own_code_handler(_ctx(root, ws), file_path=chemin,
                                    old_content="ORIGINAL", new_content="ECRASE")
    assert not r.success
    assert _intact(root, ".env")
    assert (root.parent / "dehors.txt").read_text(encoding="utf-8") == ORIGINAL
    assert (ws / "app.txt").read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.asyncio
async def test_edit_own_code_refuse_chemin_absolu_hors_racine(racine, tmp_path):
    root, ws, _ = racine
    dehors = tmp_path / "ailleurs.py"
    dehors.write_text(ORIGINAL, encoding="utf-8")
    r = await edit_own_code_handler(_ctx(root, ws), file_path=str(dehors),
                                    old_content="ORIGINAL", new_content="ECRASE")
    assert not r.success and dehors.read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.asyncio
async def test_edit_own_code_texte_absent_aucune_sauvegarde(racine):
    root, ws, backups = racine
    r = await edit_own_code_handler(_ctx(root, ws), file_path="src/core.py",
                                    old_content="INTROUVABLE", new_content="X")
    assert not r.success and _intact(root, "src/core.py") and not backups.exists()


# ── 4. CodeAgent : ecritures directes vers le code refusees ──────────────────

def _agent(root: Path, ws: Path | None, monkeypatch) -> CodeAgent:
    monkeypatch.setattr(CodeAgent, "_project_root", staticmethod(lambda: root))
    agent = CodeAgent.__new__(CodeAgent)
    agent._task_workspace_root = ws
    agent.workspace_path = str(ws) if ws else ""
    agent._session_memory = {"files_read": {}, "errors_seen": [], "edits_done": [],
                             "grep_zero_results": {}}
    agent._session_memory_last_used = 0.0
    agent._SESSION_MEMORY_TTL = 4 * 3600
    agent._read_count_per_file = {}
    agent._edited_files = set()
    agent._codeagent_todo = CodeAgentTodoState()
    agent._attempt_profile = None
    agent._write_counts = {}
    agent._edit_restricted_files = set()
    agent._self_repair_count = 0
    agent._self_repair_count_per_file = {}
    agent._syntax_clean_snapshot = {}
    agent._check_python_syntax = AsyncMock(return_value="")
    agent._check_python_types = AsyncMock(return_value="")
    agent._check_web_syntax = AsyncMock(return_value="")
    return agent


@pytest.mark.asyncio
@pytest.mark.parametrize("action", [
    {"action": "write_file", "content": "ECRASE\n"},
    {"action": "edit_file", "search": "ORIGINAL", "replace": "ECRASE"},
    {"action": "str_replace", "old_str": "ORIGINAL", "new_str": "ECRASE"},
    {"action": "edit_lines", "start_line": 1, "end_line": 1, "content": "ECRASE"},
])
async def test_codeagent_refuse_code_lumena_chemin_absolu(racine, monkeypatch, action):
    root, ws, _ = racine
    agent = _agent(root, ws, monkeypatch)
    res = await agent._execute_loop_action(dict(action, path=str(root / "src/core.py")), snapshots={})
    assert "edit_own_code" in f"{res.summary} {res.detail}", res.summary
    assert _intact(root, "src/core.py")


@pytest.mark.asyncio
async def test_codeagent_sans_workspace_refuse_code_lumena_relatif(racine, monkeypatch):
    root, _, _ = racine
    agent = _agent(root, None, monkeypatch)
    res = await agent._execute_loop_action(
        {"action": "write_file", "path": "src/core.py", "content": "ECRASE\n"}, snapshots={})
    assert "edit_own_code" in f"{res.summary} {res.detail}", res.summary
    assert _intact(root, "src/core.py")


@pytest.mark.asyncio
async def test_codeagent_ecrit_toujours_dans_son_workspace(racine, monkeypatch):
    root, ws, _ = racine
    agent = _agent(root, ws, monkeypatch)
    res = await agent._execute_loop_action(
        {"action": "write_file", "path": "src/core.py", "content": "print('ok')\n"}, snapshots={})
    assert res.summary.startswith("✅"), res.summary
    assert (ws / "src" / "core.py").read_text(encoding="utf-8") == "print('ok')\n"
    assert _intact(root, "src/core.py")
