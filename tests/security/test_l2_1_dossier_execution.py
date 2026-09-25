"""Lot L2-1 - une commande shell est jugee sur SON DOSSIER DE TRAVAIL.

Mesure du 16 septembre 2026 sur 1399 commandes reelles (journal d'audit) :
- le dossier de travail est explicite dans ~95 % des cas (`cd /d "..." && ...` ou
  parametre `cwd`), et deja resolu par `_resolve_cwd` ;
- la cible d'ecriture n'est LISIBLE dans la ligne que pour **11,2 %** des commandes ;
- **20,6 %** ecrivent de facon OPAQUE (`node -e`, `python -c`) : rien n'est lisible.

Donc juger le TEXTE de la commande serait une illusion de securite. On juge le
DOSSIER, avec les memes verdicts que la garde d'ecriture (L1c) :
- `workspace/` : autorise ;
- tout le reste du depot (`src/`, `web/`, `ide/`, `tests/`, et meme un dossier de
  projet pose a la racine) : refuse - decision de Charles du 16/09 ;
- hors depot : en CHAT, l'endroit designe ou le projet en cours (L1c-3) ; en MISSION,
  le dossier de mission ; en AUTONOMIE, rien.

Risque de casse mesure : 7 commandes sur 1399 seraient refusees, aucune ne visait
`src/`. Dossiers JETABLES.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.files import assert_execution_cwd_allowed
from src.reasoning.handlers.system import run_command_handler
from src.tools.file_guardrails import (
    OutsideAccessGrant,
    PathSecurityError,
    WorkspaceFileGuardrails,
)


@pytest.fixture
def monde(tmp_path, monkeypatch):
    root = tmp_path / "lumena"
    ws = root / "workspace"
    (ws / "projet-client").mkdir(parents=True)
    (root / "src").mkdir()
    (root / "jeu3d").mkdir()  # dossier de projet pose A LA RACINE du depot
    mission = ws / "missions" / "m1"
    mission.mkdir(parents=True)
    dehors = tmp_path / "perso" / "site vitrine"
    dehors.mkdir(parents=True)
    autre = tmp_path / "autre projet"
    autre.mkdir()
    monkeypatch.setattr(WorkspaceFileGuardrails, "_workspace_root", lambda self: ws)
    return {"root": root, "ws": ws, "src": root / "src", "jeu3d": root / "jeu3d",
            "mission": mission, "dehors": dehors, "autre": autre, "tmp": tmp_path}


def _chat(m, *designes) -> HandlerContext:
    ctx = HandlerContext.for_testing(lumena_root=m["root"], runtime_root=m["ws"])
    ctx.outside_access_grant = (OutsideAccessGrant.for_chat(*designes) if designes
                                else OutsideAccessGrant.none())
    return ctx


def _mission(m) -> HandlerContext:
    ctx = _chat(m)
    ctx.is_mission_run = True
    ctx.runtime_task_id = "task-l2"
    ctx.mission_workspace = "missions/m1"
    return ctx


def _autonomie(m) -> HandlerContext:
    ctx = HandlerContext.for_testing(lumena_root=m["root"], runtime_root=m["ws"])
    ctx.outside_access_grant = None
    return ctx


# ── 1. Dans le depot ────────────────────────────────────────────────────────

def test_workspace_autorise(monde):
    """Caracterisation : le cas de loin le plus frequent (1130 / 1399)."""
    m = monde
    assert_execution_cwd_allowed(m["ws"] / "projet-client", _chat(m))


def test_code_de_lumena_refuse(monde):
    m = monde
    with pytest.raises(PathSecurityError) as err:
        assert_execution_cwd_allowed(m["src"], _chat(m))
    assert "workspace" in str(err.value).lower()


def test_projet_pose_a_la_racine_du_depot_refuse(monde):
    """Decision de Charles du 16/09 : meme regle que l'ecriture, aucune exception."""
    m = monde
    with pytest.raises(PathSecurityError):
        assert_execution_cwd_allowed(m["jeu3d"], _chat(m))


def test_racine_du_depot_refusee(monde):
    m = monde
    with pytest.raises(PathSecurityError):
        assert_execution_cwd_allowed(m["root"], _chat(m))


# ── 2. Hors depot : selon qui demande ───────────────────────────────────────

def test_chat_endroit_designe_autorise(monde):
    m = monde
    assert_execution_cwd_allowed(m["dehors"], _chat(m, m["dehors"]))


def test_chat_projet_en_cours_autorise(monde):
    m = monde
    ctx = HandlerContext.for_testing(lumena_root=m["root"], runtime_root=m["dehors"],
                                     ide_context={"workspace_path": str(m["dehors"])})
    ctx.outside_access_grant = OutsideAccessGrant.none()
    assert_execution_cwd_allowed(m["dehors"], ctx)


def test_chat_endroit_non_designe_refuse(monde):
    m = monde
    with pytest.raises(PathSecurityError):
        assert_execution_cwd_allowed(m["autre"], _chat(m, m["dehors"]))


def test_mission_dans_son_dossier_autorise(monde):
    m = monde
    assert_execution_cwd_allowed(m["mission"], _mission(m))


def test_mission_hors_workspace_refuse(monde):
    m = monde
    with pytest.raises(PathSecurityError):
        assert_execution_cwd_allowed(m["dehors"], _mission(m))


def test_autonomie_hors_depot_refusee(monde):
    m = monde
    with pytest.raises(PathSecurityError):
        assert_execution_cwd_allowed(m["dehors"], _autonomie(m))


def test_contexte_leger_sans_racine_ne_plante_pas(monde):
    """Lecon L1c-1 : une garde commune tolere les contextes degrades."""
    from types import SimpleNamespace
    assert_execution_cwd_allowed(monde["src"], SimpleNamespace())


# ── 3. Branchement dans run_command ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_command_refuse_le_code_de_lumena_sans_executer(monde):
    m = monde
    temoin = m["src"] / "temoin.txt"
    r = await run_command_handler(_chat(m), command=f'echo bonjour > "{temoin}"',
                                  cwd=str(m["src"]))
    assert "refus" in str(r.output).lower()
    assert not temoin.exists(), "la commande a ete executee malgre le refus"


@pytest.mark.asyncio
async def test_run_command_marche_toujours_dans_le_workspace(monde):
    """Caracterisation : le chemin normal ne change pas."""
    m = monde
    r = await run_command_handler(_chat(m), command="echo bonjour",
                                  cwd=str(m["ws"] / "projet-client"))
    assert "bonjour" in str(r.output).lower()
    assert "refus" not in str(r.output).lower()
