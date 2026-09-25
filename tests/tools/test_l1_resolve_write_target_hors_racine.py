"""Lot L1 - `resolve_write_target` ne plante plus hors de la racine Lumena (N1, N8).

Mesures du 15 septembre 2026 : `target.relative_to(self.lumena_root)` leve
`ValueError` quand l'espace de travail est hors de la racine (reglage
`LUMENA_WORKSPACE_DIR` absolu ailleurs) ou relatif (`./workspace`, valeur du `.env`
reel, obtenue par `lumena_ultime.py` et `run_*.py` qui chargent `.env` avant
`paths.py`). Toute ecriture de mission cassait, native comme IDE.

Le chemin relatif renvoye ne sert qu'a l'affichage et a l'historique des editions.
Invariant central : **la configuration par defaut (production web/bureau) rend
exactement la meme cible et le meme chemin relatif qu'avant**.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.tools.file_guardrails import PathSecurityError, WorkspaceFileGuardrails
from src.utils import paths as paths_module

MISSION = "missions/m1"


@pytest.fixture(autouse=True)
def _projet_non_epingle():
    WorkspaceFileGuardrails._current_project = None
    WorkspaceFileGuardrails._pinned_project = None
    yield
    WorkspaceFileGuardrails._current_project = None
    WorkspaceFileGuardrails._pinned_project = None


@pytest.fixture
def racine(tmp_path):
    root = tmp_path / "lumena"
    (root / "src").mkdir(parents=True)  # marqueur : racine de projet -> WORKSPACE_DIR
    return root


def _gardes(racine, monkeypatch, workspace):
    monkeypatch.setattr(paths_module, "WORKSPACE_DIR", workspace)
    return WorkspaceFileGuardrails(racine)


# ── 1. Caracterisation : configuration par defaut inchangee ──────────────────────

def test_defaut_cible_et_chemin_relatif_inchanges(racine, monkeypatch):
    g = _gardes(racine, monkeypatch, racine / "workspace")
    target, redirected, rel = g.resolve_write_target("a.py", mission_workspace_subdir=MISSION)
    assert target == racine / "workspace" / "missions" / "m1" / "a.py"
    assert redirected is True
    assert rel == "workspace/missions/m1/a.py"


def test_defaut_chemin_absolu_hors_limites_toujours_refuse(racine, monkeypatch, tmp_path):
    g = _gardes(racine, monkeypatch, racine / "workspace")
    with pytest.raises(PathSecurityError):
        g.resolve_write_target(str(tmp_path / "ailleurs" / "x.py"))


# ── 2. Espace de travail absolu hors de la racine ────────────────────────────────

def test_hors_racine_plus_de_plantage_cible_exacte(racine, monkeypatch, tmp_path):
    ws = tmp_path / "espace"
    g = _gardes(racine, monkeypatch, ws)
    target, redirected, rel = g.resolve_write_target("lib/a.py", mission_workspace_subdir=MISSION)
    assert target == ws / "missions" / "m1" / "lib" / "a.py"
    assert redirected is True
    assert rel == "missions/m1/lib/a.py"


def test_hors_racine_absolu_hors_des_deux_limites_refuse(racine, monkeypatch, tmp_path):
    g = _gardes(racine, monkeypatch, tmp_path / "espace")
    with pytest.raises(PathSecurityError):
        g.resolve_write_target(str(tmp_path / "ailleurs" / "x.py"))


def test_hors_racine_ecriture_stricte(racine, monkeypatch, tmp_path):
    ws = tmp_path / "espace"
    g = _gardes(racine, monkeypatch, ws)
    result = g.write_file_strict("a.py", "VALEUR = 1\n", mission_workspace_subdir=MISSION)
    assert result.success is True, result.message
    assert (ws / "missions" / "m1" / "a.py").read_text(encoding="utf-8") == "VALEUR = 1\n"
    assert result.workspace_relative == "missions/m1/a.py"


# ── 3. Espace de travail relatif (valeur du .env reel) ───────────────────────────

def test_relatif_cible_absolue_depuis_le_dossier_de_lancement(racine, monkeypatch):
    monkeypatch.chdir(racine)
    g = _gardes(racine, monkeypatch, Path("workspace"))
    target, _, rel = g.resolve_write_target("a.py", mission_workspace_subdir=MISSION)
    assert target.is_absolute()
    assert target == (racine / "workspace" / "missions" / "m1" / "a.py").resolve()
    assert rel == "workspace/missions/m1/a.py"


# ── 4. Chaines reelles : write_file natif et ecriture IDE de mission ─────────────

def _contexte_mission(racine, ws):
    ctx = HandlerContext.for_testing(lumena_root=racine, runtime_root=ws)
    ctx.is_mission_run = True
    ctx.runtime_task_id = "m1"
    ctx.mission_workspace = MISSION
    return ctx


def test_write_file_natif_en_mission_hors_racine(racine, monkeypatch, tmp_path):
    from src.reasoning.handlers.files import write_file_handler

    ws = tmp_path / "espace"
    monkeypatch.setattr(paths_module, "WORKSPACE_DIR", ws)
    ctx = _contexte_mission(racine, ws)
    result = asyncio.run(write_file_handler(ctx, path="a.py", content="VALEUR = 42\n"))
    assert result.success is True, result.output
    assert (ws / "missions" / "m1" / "a.py").read_text(encoding="utf-8") == "VALEUR = 42\n"


def test_ecriture_ide_de_mission_hors_racine(racine, monkeypatch, tmp_path):
    from src.reasoning.ide_mission_scope import MissionScope, canonical_workspace, prepare_mission_write

    ws = tmp_path / "espace"
    monkeypatch.setattr(paths_module, "WORKSPACE_DIR", ws)
    ctx = _contexte_mission(racine, ws)
    (ws / "missions" / "m1").mkdir(parents=True)
    scope = MissionScope(task_id="m1", parent_id=None, role="lead", depth=1, mission_workspace=MISSION,
                         mission_root=canonical_workspace(str(ws / "missions" / "m1")), allowed_files=frozenset(),
                         caller_kind="react")
    write = prepare_mission_write(scope, ctx, {"path": "a.py", "content": "x"})
    assert write.relative_target == "a.py"
    assert Path(write.target) == Path(canonical_workspace(str(ws / "missions" / "m1" / "a.py")))
