"""Lots L1c-3 et L1c-4 - en chat, Lumena ecrit et supprime hors du depot, avec sauvegarde.

Decisions de Charles du 15 septembre 2026 :
- en CHAT, ecrire/modifier hors du workspace est permis, sauf dans le code de Lumena ;
  borne aux endroits que l'utilisateur designe dans son message (autorisation du tour,
  `_detect_outside_access_grant`) ET au projet en cours (dossier de travail effectif :
  dossier ouvert dans l'IDE / racine d'execution du tour) ;
- sauvegarde AVANT toute modification ou suppression d'un fichier existant
  (`BACKUPS_DIR/hors_depot/`), preuve apres (relecture des handlers) ;
- MISSIONS (is_mission_run) et AUTONOMIE (autorisation du tour = None) restent
  confinees ; le CodeAgent en mission ne sort plus de son dossier par un chemin absolu ;
- suppression hors workspace en chat avec sauvegarde ; `delete_file` ne supprime
  jamais de dossier.

Tout se passe dans des dossiers JETABLES.
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
from src.runtime.context import pop_runtime_context, push_runtime_context
from src.subagents.mission_identity import mission_runtime_context
from src.tools.file_guardrails import OutsideAccessGrant, WorkspaceFileGuardrails

ORIGINAL = "ORIGINAL\n"


@pytest.fixture
def monde(tmp_path, monkeypatch):
    root = tmp_path / "lumena"
    ws = root / "workspace"
    ws.mkdir(parents=True)
    (root / "src").mkdir()
    (root / "src" / "core.py").write_text(ORIGINAL, encoding="utf-8")
    (root / ".env").write_text(ORIGINAL, encoding="utf-8")
    (ws / "a_jeter.txt").write_text(ORIGINAL, encoding="utf-8")
    perso = tmp_path / "perso"
    perso.mkdir()
    (perso / "notes.txt").write_text(ORIGINAL, encoding="utf-8")
    autre = tmp_path / "autre"
    autre.mkdir()
    (autre / "secret.txt").write_text(ORIGINAL, encoding="utf-8")
    projet = tmp_path / "projet_en_cours"
    projet.mkdir()
    (projet / "app.py").write_text(ORIGINAL, encoding="utf-8")
    backups = tmp_path / "backups"
    monkeypatch.setattr(WorkspaceFileGuardrails, "_workspace_root", lambda self: ws)
    monkeypatch.setattr("src.utils.paths.BACKUPS_DIR", backups)
    return {"root": root, "ws": ws, "perso": perso, "autre": autre, "projet": projet,
            "backups": backups, "tmp": tmp_path}


def _ctx(m, grant, *, runtime_root=None, ide=None) -> HandlerContext:
    ctx = HandlerContext.for_testing(lumena_root=m["root"], runtime_root=runtime_root or m["ws"],
                                     ide_context=ide)
    ctx.outside_access_grant = grant
    return ctx


def _chat(m, *roots) -> HandlerContext:
    return _ctx(m, OutsideAccessGrant.for_chat(*roots))


def _txt(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _sauvegardes(m, nom: str) -> list[Path]:
    base = m["backups"] / "hors_depot"
    return list(base.rglob(nom)) if base.exists() else []


# ── 1. Chat : endroit designe par l'utilisateur ──────────────────────────────

@pytest.mark.asyncio
async def test_chat_edit_file_hors_depot_avec_sauvegarde(monde):
    m = monde
    cible = m["perso"] / "notes.txt"
    r = await files_mod.edit_file_handler(_chat(m, m["perso"]), file_path=str(cible),
                                         old_content="ORIGINAL", new_content="MODIFIE")
    assert r.success, r.output
    assert _txt(cible) == "MODIFIE\n"
    copies = _sauvegardes(m, "notes.txt")
    assert len(copies) == 1 and _txt(copies[0]) == ORIGINAL


@pytest.mark.asyncio
async def test_chat_write_file_cree_hors_depot(monde):
    m = monde
    cible = m["perso"] / "nouveau.txt"
    r = await files_mod.write_file_handler(_chat(m, m["perso"]), path=str(cible), content="BONJOUR\n")
    assert r.success, r.output
    assert _txt(cible) == "BONJOUR\n"
    assert _sauvegardes(m, "nouveau.txt") == []


@pytest.mark.asyncio
async def test_chat_write_file_reecrit_hors_depot_avec_sauvegarde(monde):
    m = monde
    cible = m["perso"] / "notes.txt"
    r = await files_mod.write_file_handler(_chat(m, m["perso"]), path=str(cible),
                                          content="REECRIT\n", force_rewrite=True,
                                          rewrite_reason="test L1c-3")
    assert r.success, r.output
    assert _txt(cible) == "REECRIT\n"
    assert [_txt(c) for c in _sauvegardes(m, "notes.txt")] == [ORIGINAL]


@pytest.mark.asyncio
async def test_chat_apply_patches_hors_depot_avec_sauvegarde(monde):
    m = monde
    cible = m["perso"] / "notes.txt"
    r = await apply_patches_handler(_chat(m, m["perso"]), patches=[
        {"file": str(cible), "old": "ORIGINAL", "new": "PATCHE"}])
    assert r.success, r.output
    assert _txt(cible) == "PATCHE\n"
    assert [_txt(c) for c in _sauvegardes(m, "notes.txt")] == [ORIGINAL]


@pytest.mark.asyncio
async def test_chat_create_directory_hors_depot(monde):
    m = monde
    cible = m["perso"] / "classement"
    r = await files_mod.create_directory_handler(_chat(m, m["perso"]), path=str(cible))
    assert r.success, r.output
    assert cible.is_dir()


# ── 2. Ce qui reste refuse ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_refuse_endroit_non_designe(monde):
    m = monde
    cible = m["autre"] / "secret.txt"
    r = await files_mod.edit_file_handler(_chat(m, m["perso"]), file_path=str(cible),
                                         old_content="ORIGINAL", new_content="ECRASE")
    assert not r.success and _txt(cible) == ORIGINAL


@pytest.mark.asyncio
async def test_autorisation_de_lecture_seule_n_ecrit_pas(monde):
    """Caracterisation : `for_paths` reste une autorisation de LECTURE."""
    m = monde
    cible = m["perso"] / "notes.txt"
    r = await files_mod.edit_file_handler(_ctx(m, OutsideAccessGrant.for_paths(m["perso"])),
                                         file_path=str(cible), old_content="ORIGINAL",
                                         new_content="ECRASE")
    assert not r.success and _txt(cible) == ORIGINAL


@pytest.mark.asyncio
async def test_mission_reste_confinee_meme_avec_autorisation(monde):
    m = monde
    ctx = _chat(m, m["perso"])
    ctx.is_mission_run = True
    ctx.runtime_task_id = "task-l1c3"
    cible = m["perso"] / "notes.txt"
    r = await files_mod.edit_file_handler(ctx, file_path=str(cible),
                                         old_content="ORIGINAL", new_content="ECRASE")
    assert not r.success and _txt(cible) == ORIGINAL


@pytest.mark.asyncio
async def test_autonomie_sans_autorisation_reste_confinee(monde):
    m = monde
    ctx = _ctx(m, None, runtime_root=m["projet"], ide={"workspace_path": str(m["projet"])})
    cible = m["perso"] / "notes.txt"
    r = await files_mod.edit_file_handler(ctx, file_path=str(cible),
                                         old_content="ORIGINAL", new_content="ECRASE")
    assert not r.success and _txt(cible) == ORIGINAL
    r = await files_mod.edit_file_handler(ctx, file_path=str(m["projet"] / "app.py"),
                                         old_content="ORIGINAL", new_content="ECRASE")
    assert not r.success and _txt(m["projet"] / "app.py") == ORIGINAL


@pytest.mark.asyncio
async def test_chat_code_lumena_toujours_refuse(monde):
    m = monde
    r = await files_mod.edit_file_handler(_chat(m, m["tmp"]), file_path=str(m["root"] / "src/core.py"),
                                         old_content="ORIGINAL", new_content="ECRASE")
    assert not r.success and "edit_own_code" in str(r.output)
    assert _txt(m["root"] / "src/core.py") == ORIGINAL


@pytest.mark.asyncio
async def test_chat_liste_noire_toujours_refusee(monde):
    m = monde
    r = await files_mod.edit_file_handler(_chat(m, m["tmp"]), file_path=str(m["root"] / ".env"),
                                         old_content="ORIGINAL", new_content="ECRASE")
    assert not r.success and _txt(m["root"] / ".env") == ORIGINAL


# ── 3. Projet en cours ───────────────────────────────────────────────────────

def _chat_projet(m) -> HandlerContext:
    return _ctx(m, OutsideAccessGrant.none(), runtime_root=m["projet"],
                ide={"workspace_path": str(m["projet"])})


@pytest.mark.asyncio
async def test_chat_projet_en_cours_modifiable_sans_le_nommer(monde):
    m = monde
    cible = m["projet"] / "app.py"
    r = await files_mod.edit_file_handler(_chat_projet(m), file_path=str(cible),
                                         old_content="ORIGINAL", new_content="MODIFIE")
    assert r.success, r.output
    assert _txt(cible) == "MODIFIE\n"
    assert [_txt(c) for c in _sauvegardes(m, "app.py")] == [ORIGINAL]


@pytest.mark.asyncio
async def test_chat_projet_en_cours_depuis_le_contexte_de_la_requete(monde):
    """Chemin de production : seul le contexte d'execution est pose a chaque requete
    (le contexte des handlers garde la racine du chargement du registre)."""
    from src.runtime.context import RuntimeContext
    m = monde
    requete = RuntimeContext.build(
        channel="ide", client="test", request_id=None, conversation_id=None, message_id=None,
        workspace_policy=None, task_id=None, client_caps=None,
        workspace_path=str(m["projet"]), active_file_path=None, open_files=None,
        resolved_workspace=None, resolved_date=None, resolution_reason=None)
    ctx = _ctx(m, OutsideAccessGrant.none())
    cible = m["projet"] / "app.py"
    token = push_runtime_context(requete)
    try:
        r = await files_mod.edit_file_handler(ctx, file_path=str(cible),
                                             old_content="ORIGINAL", new_content="MODIFIE")
    finally:
        pop_runtime_context(token)
    assert r.success, r.output
    assert _txt(cible) == "MODIFIE\n"
    assert [_txt(c) for c in _sauvegardes(m, "app.py")] == [ORIGINAL]


@pytest.mark.asyncio
async def test_chat_projet_en_cours_n_ouvre_pas_le_reste_du_pc(monde):
    m = monde
    cible = m["perso"] / "notes.txt"
    r = await files_mod.edit_file_handler(_chat_projet(m), file_path=str(cible),
                                         old_content="ORIGINAL", new_content="ECRASE")
    assert not r.success and _txt(cible) == ORIGINAL


# ── 4. L1c-4 : suppression ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_supprime_hors_depot_avec_sauvegarde(monde):
    m = monde
    cible = m["perso"] / "notes.txt"
    r = await files_mod.delete_file_handler(_chat(m, m["perso"]), path=str(cible))
    assert r.success, r.output
    assert not cible.exists()
    assert [_txt(c) for c in _sauvegardes(m, "notes.txt")] == [ORIGINAL]


@pytest.mark.asyncio
async def test_chat_suppression_refusee_endroit_non_designe(monde):
    m = monde
    cible = m["autre"] / "secret.txt"
    r = await files_mod.delete_file_handler(_chat(m, m["perso"]), path=str(cible))
    assert not r.success and cible.exists()


@pytest.mark.asyncio
async def test_suppression_en_mission_hors_workspace_refusee(monde):
    m = monde
    ctx = _chat(m, m["perso"])
    ctx.is_mission_run = True
    ctx.runtime_task_id = "task-l1c4"
    cible = m["perso"] / "notes.txt"
    r = await files_mod.delete_file_handler(ctx, path=str(cible))
    assert not r.success and cible.exists()


@pytest.mark.asyncio
async def test_chat_suppression_code_lumena_refusee(monde):
    m = monde
    cible = m["root"] / "src" / "core.py"
    r = await files_mod.delete_file_handler(_chat(m, m["tmp"]), path=str(cible))
    assert not r.success and cible.exists()


@pytest.mark.asyncio
async def test_suppression_dans_le_workspace_inchangee(monde):
    m = monde
    cible = m["ws"] / "a_jeter.txt"
    r = await files_mod.delete_file_handler(_ctx(m, None), path=str(cible))
    assert r.success, r.output
    assert not cible.exists()


# ── 5. CodeAgent ─────────────────────────────────────────────────────────────

def _agent(m, task_root: Path, monkeypatch) -> CodeAgent:
    monkeypatch.setattr(CodeAgent, "_project_root", staticmethod(lambda: m["root"]))
    agent = CodeAgent.__new__(CodeAgent)
    agent._task_workspace_root = task_root
    agent.workspace_path = str(task_root)
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


REQUERANT = {"user_role": "owner", "user_id": "local:owner", "owner_user_id": "local:owner",
             "channel": "web"}


@pytest.mark.asyncio
async def test_codeagent_en_mission_ne_sort_pas_de_son_dossier(monde, monkeypatch):
    m = monde
    dossier = m["ws"] / "missions" / "m1"
    dossier.mkdir(parents=True)
    agent = _agent(m, dossier, monkeypatch)
    contexte = mission_runtime_context("m1", REQUERANT)
    assert contexte is not None
    token = push_runtime_context(contexte)
    try:
        res = await agent._execute_loop_action(
            {"action": "write_file", "path": str(m["perso"] / "fuite.txt"), "content": "X\n"},
            snapshots={})
        res2 = await agent._execute_loop_action(
            {"action": "write_file", "path": "dedans.txt", "content": "OK\n"}, snapshots={})
    finally:
        pop_runtime_context(token)
    assert "refus" in f"{res.summary} {res.detail}".lower(), res.summary
    assert not (m["perso"] / "fuite.txt").exists()
    assert res2.summary.startswith("✅"), res2.summary
    assert _txt(dossier / "dedans.txt") == "OK\n"


@pytest.mark.asyncio
async def test_codeagent_en_chat_sauvegarde_avant_ecriture_hors_depot(monde, monkeypatch):
    m = monde
    agent = _agent(m, m["projet"], monkeypatch)
    cible = m["perso"] / "notes.txt"
    res = await agent._execute_loop_action(
        {"action": "edit_file", "path": str(cible), "search": "ORIGINAL", "replace": "MODIFIE"},
        snapshots={})
    assert res.summary.startswith("✅"), res.summary
    assert _txt(cible) == "MODIFIE\n"
    assert [_txt(c) for c in _sauvegardes(m, "notes.txt")] == [ORIGINAL]
