"""Lot L1c-1 - une seule garde d'ecriture pour TOUTES les portes (ferme N11).

Mesure du 15 septembre 2026 (sonde sur racine jetable) : `apply_patches` et
`edit_file` ecrasaient `.env`, `data/...` et `models/...`. La liste noire P0.2 n'etait
appelee que par `write_file`, `insert_at_anchor`, `apply_patch_new` et le rail IDE.
Le CodeAgent ecrit directement sur le disque apres `SubAgent._resolve_path` (chemin
absolu rendu tel quel) et `write_website_files` accepte n'importe quel `output_dir`.

Regle : toute ecriture passe par la meme garde (frontiere + perimetre de mission +
liste noire). Durcissement pur : aucun droit nouveau, les ecritures legitimes du
workspace restent identiques (caracterisations ci-dessous).

Tout se passe dans une racine Lumena JETABLE ; le vrai depot n'est jamais touche.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.agents.codeagent_todo import CodeAgentTodoState
from src.agents.sub_agent import CodeAgent
from src.llm.output_normalizer import normalize_file_path
from src.reasoning.handlers.batch import apply_patches_handler
from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers import files as files_mod
from src.reasoning.handlers.website import write_website_files_handler
from src.tools.file_guardrails import WorkspaceFileGuardrails

ORIGINAL = "ORIGINAL\n"
PROTEGES = (".env", "data/memoire.txt", "models/poids.txt")


@pytest.fixture
def racine(tmp_path, monkeypatch):
    root = tmp_path / "lumena"
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    for rel in PROTEGES:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(ORIGINAL, encoding="utf-8")
    (workspace / "app.txt").write_text(ORIGINAL, encoding="utf-8")
    # `data/` est un marqueur de projet : sans ceci la racine d'espace de travail
    # serait le VRAI workspace de Lumena.
    monkeypatch.setattr(WorkspaceFileGuardrails, "_workspace_root", lambda self: workspace)
    return root, workspace


def _ctx(root: Path, workspace: Path) -> HandlerContext:
    ctx = HandlerContext.for_testing(lumena_root=root, runtime_root=workspace)
    ctx.outside_access_grant = None
    return ctx


def _intact(root: Path, rel: str) -> bool:
    return (root / rel).read_text(encoding="utf-8") == ORIGINAL


def _refus(result) -> bool:
    return (not result.success) and "refus" in str(result.output).lower()


# ── 1. Handlers : la liste noire vaut pour toutes les portes ─────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("rel", PROTEGES)
async def test_edit_file_refuse_zone_protegee(racine, rel):
    root, ws = racine
    r = await files_mod.edit_file_handler(_ctx(root, ws), file_path=str(root / rel),
                                         old_content="ORIGINAL", new_content="ECRASE")
    assert _refus(r) and _intact(root, rel)


@pytest.mark.asyncio
@pytest.mark.parametrize("rel", PROTEGES)
async def test_multi_edit_file_refuse_zone_protegee(racine, rel):
    root, ws = racine
    r = await files_mod.multi_edit_file_handler(_ctx(root, ws), edits=[
        {"file_path": str(root / rel), "old_content": "ORIGINAL", "new_content": "ECRASE"}])
    assert _refus(r) and _intact(root, rel)


@pytest.mark.asyncio
@pytest.mark.parametrize("rel", PROTEGES)
async def test_apply_patch_refuse_zone_protegee(racine, rel):
    root, ws = racine
    r = await files_mod.apply_patch_handler(_ctx(root, ws), file_path=str(root / rel),
                                           old_content="ORIGINAL", new_content="ECRASE")
    assert _refus(r) and _intact(root, rel)


@pytest.mark.asyncio
@pytest.mark.parametrize("rel", PROTEGES)
async def test_apply_patches_refuse_zone_protegee(racine, rel):
    root, ws = racine
    r = await apply_patches_handler(_ctx(root, ws), patches=[
        {"file": str(root / rel), "old": "ORIGINAL", "new": "ECRASE"}])
    assert _refus(r) and _intact(root, rel)


@pytest.mark.asyncio
async def test_apply_patches_refuse_tout_le_lot_si_un_seul_fichier_protege(racine):
    root, ws = racine
    r = await apply_patches_handler(_ctx(root, ws), patches=[
        {"file": str(ws / "app.txt"), "old": "ORIGINAL", "new": "MODIFIE"},
        {"file": str(root / ".env"), "old": "ORIGINAL", "new": "ECRASE"}])
    assert _refus(r)
    assert _intact(root, ".env")
    assert (ws / "app.txt").read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.asyncio
async def test_undo_edit_refuse_restauration_sur_zone_protegee(racine, tmp_path, monkeypatch):
    root, ws = racine
    backups = tmp_path / "backups"
    (backups / "session_1").mkdir(parents=True)
    (backups / "session_1" / ".env").write_text("RESTAURE\n", encoding="utf-8")
    monkeypatch.setattr("src.utils.paths.BACKUPS_DIR", backups)
    r = await files_mod.undo_edit_handler(_ctx(root, ws), file_path=str(root / ".env"))
    assert _refus(r) and _intact(root, ".env")


@pytest.mark.asyncio
async def test_create_zip_refuse_sortie_en_zone_protegee(racine):
    root, ws = racine
    sortie = root / "data" / "archive.zip"
    r = await files_mod.create_zip_handler(_ctx(root, ws), source_paths=str(ws / "app.txt"),
                                          zip_path=str(sortie))
    assert _refus(r) and not sortie.exists()


@pytest.mark.asyncio
async def test_create_directory_refuse_zone_protegee(racine):
    root, ws = racine
    cible = root / "models" / "nouveau"
    r = await files_mod.create_directory_handler(_ctx(root, ws), path=str(cible))
    assert _refus(r) and not cible.exists()


# ── 2. apply_patches respecte aussi le perimetre de mission ──────────────────

def _ctx_mission(root: Path, ws: Path, allowed: list[str]) -> HandlerContext:
    ctx = _ctx(root, ws)
    ctx.is_mission_run = True
    ctx.runtime_task_id = "task-l1c1"
    ctx.mission_workspace = "missions/m1"
    ctx.mission_allowed_files = allowed
    return ctx


@pytest.mark.asyncio
async def test_apply_patches_refuse_fichier_hors_perimetre_de_mission(racine):
    root, ws = racine
    mission = ws / "missions" / "m1"
    mission.mkdir(parents=True)
    (mission / "a.py").write_text(ORIGINAL, encoding="utf-8")
    (mission / "b.py").write_text(ORIGINAL, encoding="utf-8")
    r = await apply_patches_handler(_ctx_mission(root, ws, ["a.py"]), patches=[
        {"file": str(mission / "b.py"), "old": "ORIGINAL", "new": "ECRASE"}])
    assert _refus(r) or "périmètre" in str(r.output)
    assert not r.success
    assert (mission / "b.py").read_text(encoding="utf-8") == ORIGINAL


@pytest.mark.asyncio
async def test_apply_patches_ecrit_son_fichier_de_mission(racine):
    """Caracterisation : le fichier possede reste modifiable."""
    root, ws = racine
    mission = ws / "missions" / "m1"
    mission.mkdir(parents=True)
    (mission / "a.py").write_text(ORIGINAL, encoding="utf-8")
    r = await apply_patches_handler(_ctx_mission(root, ws, ["a.py"]), patches=[
        {"file": str(mission / "a.py"), "old": "ORIGINAL", "new": "MODIFIE"}])
    assert r.success
    assert (mission / "a.py").read_text(encoding="utf-8") == "MODIFIE\n"


# ── 3. Caracterisations : le workspace reste inscriptible ────────────────────

@pytest.mark.asyncio
async def test_apply_patches_ecrit_dans_le_workspace(racine):
    root, ws = racine
    r = await apply_patches_handler(_ctx(root, ws), patches=[
        {"file": str(ws / "app.txt"), "old": "ORIGINAL", "new": "MODIFIE"}])
    assert r.success
    assert (ws / "app.txt").read_text(encoding="utf-8") == "MODIFIE\n"


@pytest.mark.asyncio
async def test_edit_file_ecrit_dans_le_workspace(racine):
    root, ws = racine
    r = await files_mod.edit_file_handler(_ctx(root, ws), file_path=str(ws / "app.txt"),
                                         old_content="ORIGINAL", new_content="MODIFIE")
    assert r.success and "refus" not in str(r.output).lower()
    assert (ws / "app.txt").read_text(encoding="utf-8") == "MODIFIE\n"


@pytest.mark.asyncio
async def test_create_directory_dans_le_workspace(racine):
    root, ws = racine
    r = await files_mod.create_directory_handler(_ctx(root, ws), path=str(ws / "nouveau"))
    assert r.success and (ws / "nouveau").is_dir()


# ── 4. write_website_files ────────────────────────────────────────────────────

SITE = {"project_name": "site", "files": {"index.html": "<html><body>ok</body></html>"}}


@pytest.mark.asyncio
async def test_write_website_files_refuse_sortie_en_zone_protegee(racine):
    root, ws = racine
    sortie = root / "data" / "site"
    r = await write_website_files_handler(_ctx(root, ws), json_data=SITE, output_dir=str(sortie))
    assert _refus(r) and not sortie.exists()


@pytest.mark.asyncio
async def test_write_website_files_ecrit_dans_le_workspace(racine):
    root, ws = racine
    sortie = ws / "site"
    r = await write_website_files_handler(_ctx(root, ws), json_data=SITE, output_dir=str(sortie))
    assert r.success and "refus" not in str(r.output).lower()
    assert (sortie / "index.html").exists()


# ── 5. CodeAgent : ecritures directes ────────────────────────────────────────

def _agent(root: Path, ws: Path, monkeypatch) -> CodeAgent:
    monkeypatch.setattr(CodeAgent, "_project_root", staticmethod(lambda: root))
    agent = CodeAgent.__new__(CodeAgent)
    agent._task_workspace_root = ws
    agent.workspace_path = str(ws)
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


def _actions(chemin: str) -> list[dict]:
    return [
        {"action": "write_file", "path": chemin, "content": "ECRASE\n"},
        {"action": "edit_file", "path": chemin, "search": "ORIGINAL", "replace": "ECRASE"},
        {"action": "str_replace", "path": chemin, "old_str": "ORIGINAL", "new_str": "ECRASE"},
        {"action": "edit_lines", "path": chemin, "start_line": 1, "end_line": 1,
         "content": "ECRASE"},
        {"action": "insert_at_anchor", "path": chemin, "anchor": "ORIGINAL",
         "content": "ECRASE", "position": "after"},
        {"action": "undo_edit", "path": chemin},
        # Chemin ABSOLU jetable : `apply_patch` du CodeAgent resout depuis le dossier
        # courant (le vrai depot pendant pytest) - jamais de chemin relatif ici.
        {"action": "apply_patch",
         "patch": f"*** Begin Patch\n*** Update File: {chemin}\n@@\n-ORIGINAL\n+ECRASE\n*** End Patch"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("rel", PROTEGES)
@pytest.mark.parametrize("index", range(7))
async def test_codeagent_refuse_ecriture_directe_en_zone_protegee(racine, monkeypatch, rel, index):
    root, ws = racine
    agent = _agent(root, ws, monkeypatch)
    chemin = str(root / rel)
    action = _actions(chemin)[index]
    # Cle normalisee comme le fait `_execute_loop_action` : sinon `undo_edit` ne
    # trouverait pas son instantane et le test passerait sans rien prouver.
    snapshots = {normalize_file_path(chemin): "ECRASE\n"}
    res = await agent._execute_loop_action(dict(action), snapshots=snapshots)
    assert "refus" in f"{res.summary} {res.detail}".lower(), (action["action"], res.summary)
    assert _intact(root, rel)


@pytest.mark.asyncio
async def test_codeagent_ecrit_dans_son_workspace(racine, monkeypatch):
    """Caracterisation : ecriture et edition normales du CodeAgent inchangees."""
    root, ws = racine
    agent = _agent(root, ws, monkeypatch)
    res = await agent._execute_loop_action(
        {"action": "write_file", "path": "hello.py", "content": "print('ok')\n"}, snapshots={})
    assert res.summary.startswith("✅"), res.summary
    assert (ws / "hello.py").read_text(encoding="utf-8") == "print('ok')\n"
    res = await agent._execute_loop_action(
        {"action": "edit_file", "path": "app.txt", "search": "ORIGINAL", "replace": "MODIFIE"},
        snapshots={})
    assert res.summary.startswith("✅"), res.summary
    assert (ws / "app.txt").read_text(encoding="utf-8") == "MODIFIE\n"
