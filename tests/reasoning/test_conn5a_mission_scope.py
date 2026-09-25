"""CONN-5A - module pur de perimetre de mission pour les appels IDE.

Le perimetre est DERIVE de la projection HandlerContext et REVALIDE contre le
TaskOrchestrator, avec la normalisation native (memes methodes que les gardes
`_assert_mission_file_allowed` et G1). Le role vient de `parent_id`, jamais de
`allowed_files` (H4-b).
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import os
from types import SimpleNamespace

import pytest

from src.reasoning.caller_context import REACT, UNKNOWN
from src.reasoning.ide_mission_scope import (
    MissionScope, MissionScopeError, authorize_mission_call, canonical_workspace, derive_mission_scope,
)
from src.runtime.task_orchestrator import TaskOrchestrator
from tests.reasoning.test_conn3c_live_catalogue import registry as registry
from tests.tools.test_conn3c_ide_capabilities import service as service


def _mission(registry, *, task_id="task_worker", parent="task_lead", allowed=("README.md",), kind="mission"):
    orch = TaskOrchestrator()
    sub = f"missions/{parent or task_id}"
    meta = {"kind": kind, "mission_workspace": sub, "depth": 2 if parent else 1}
    if parent:
        meta["parent_id"] = parent
    if allowed is not None:
        meta["allowed_files"] = list(allowed)
    orch.start_task(conversation_id="conversation", channel="web", message_preview="mission",
                    metadata=meta, task_id=task_id)
    ctx = registry._v2_context
    ctx.lumena = SimpleNamespace(task_orchestrator=orch)
    ctx.is_mission_run = True
    ctx.runtime_task_id = task_id
    ctx.mission_workspace = sub
    ctx.mission_allowed_files = list(allowed or [])
    root = ctx.file_guardrails._workspace_root().resolve() / sub
    return orch, ctx, root


def _semantique(service, nom):
    return next(tool.semantics for tool in service.capture().tools if tool.name == nom)


def _code(excinfo):
    return str(excinfo.value)


# ── canonical_workspace ──────────────────────────────────────────────────────


@pytest.mark.parametrize("valeur", [None, "", "   ", "relatif/dossier", "a\x00b", 42])
def test_chemin_invalide_n_a_pas_de_forme_canonique(valeur):
    assert canonical_workspace(valeur) is None


def test_separateur_final_ne_change_pas_la_forme_canonique(tmp_path):
    assert canonical_workspace(str(tmp_path) + os.sep) == canonical_workspace(str(tmp_path))


# ── derive_mission_scope : detection ─────────────────────────────────────────


def test_tour_de_chat_sans_tache_n_est_pas_une_mission(registry):
    registry._v2_context.is_mission_run = False
    assert derive_mission_scope(registry._v2_context, runtime_task_id=None, caller=REACT) is None


def test_tache_d_orchestrateur_qui_n_est_pas_une_mission_n_en_est_pas_une(registry):
    orch = TaskOrchestrator()
    orch.start_task(conversation_id="c", channel="web", message_preview="m",
                    metadata={"source": "api_chat"}, task_id="task_chat")
    ctx = registry._v2_context
    ctx.is_mission_run = False
    ctx.lumena = SimpleNamespace(task_orchestrator=orch)
    assert derive_mission_scope(ctx, runtime_task_id="task_chat", caller=REACT) is None


def test_vraie_mission_non_projetee_est_incomplete(registry):
    orch = TaskOrchestrator()
    orch.start_task(conversation_id="c", channel="web", message_preview="m",
                    metadata={"kind": "mission", "mission_workspace": "missions/task_m"}, task_id="task_m")
    ctx = registry._v2_context
    ctx.is_mission_run = False
    ctx.lumena = SimpleNamespace(task_orchestrator=orch)
    with pytest.raises(MissionScopeError) as excinfo:
        derive_mission_scope(ctx, runtime_task_id="task_m", caller=REACT)
    assert _code(excinfo) == "ide_mission_scope_incomplete"


def test_tache_inverifiable_sans_orchestrateur_est_incomplete(registry):
    ctx = registry._v2_context
    ctx.is_mission_run = False
    ctx.lumena = None
    with pytest.raises(MissionScopeError) as excinfo:
        derive_mission_scope(ctx, runtime_task_id="task_x", caller=REACT)
    assert _code(excinfo) == "ide_mission_scope_incomplete"


def test_l_erreur_de_perimetre_est_une_erreur_d_outil_externe():
    from src.reasoning.external_tool_registry import ExternalToolError

    assert issubclass(MissionScopeError, ExternalToolError)


# ── derive_mission_scope : contenu ───────────────────────────────────────────


def test_worker_complet(registry):
    _, ctx, root = _mission(registry)
    scope = derive_mission_scope(ctx, runtime_task_id=None, caller=REACT)
    assert isinstance(scope, MissionScope)
    assert scope.task_id == "task_worker" and scope.parent_id == "task_lead"
    assert scope.role == "worker" and scope.depth == 2
    assert scope.mission_workspace == "missions/task_lead"
    assert scope.mission_root == canonical_workspace(str(root))
    assert scope.allowed_files == frozenset({"README.md"})
    assert scope.caller_kind == "react"


def test_worker_d_effets_sans_allowed_files_reste_un_worker(registry):
    """H4-b : sans la cle, un worker n'est PAS le lead."""
    _, ctx, _ = _mission(registry, allowed=None)
    scope = derive_mission_scope(ctx, runtime_task_id=None, caller=REACT)
    assert scope.role == "worker" and scope.parent_id == "task_lead"
    assert scope.allowed_files is None


def test_lead(registry):
    _, ctx, _ = _mission(registry, task_id="task_lead", parent=None, allowed=None)
    scope = derive_mission_scope(ctx, runtime_task_id=None, caller=REACT)
    assert scope.role == "lead" and scope.parent_id is None and scope.depth == 1


def test_normalisation_native_des_fichiers_prefixes_n_est_pas_une_divergence(registry):
    """LOT 2.8 : `missions/<id>/README.md` et `README.md` designent le meme fichier."""
    _, ctx, _ = _mission(registry)
    ctx.mission_allowed_files = ["missions/task_lead/README.md"]
    scope = derive_mission_scope(ctx, runtime_task_id=None, caller=REACT)
    assert scope.allowed_files == frozenset({"README.md"})


def test_perimetre_immuable(registry):
    _, ctx, _ = _mission(registry)
    scope = derive_mission_scope(ctx, runtime_task_id=None, caller=REACT)
    with pytest.raises(FrozenInstanceError):
        scope.role = "lead"


@pytest.mark.parametrize("champ,valeur", [
    ("mission_workspace", "missions/autre"),
    ("mission_allowed_files", ["autre.py"]),
])
def test_projection_divergente(registry, champ, valeur):
    _, ctx, _ = _mission(registry)
    setattr(ctx, champ, valeur)
    with pytest.raises(MissionScopeError) as excinfo:
        derive_mission_scope(ctx, runtime_task_id=None, caller=REACT)
    assert _code(excinfo) == "ide_mission_scope_divergent"


@pytest.mark.parametrize("defaut", ["sans_id", "sans_orchestrateur", "tache_inconnue", "pas_une_mission",
                                    "dossier_evasion", "dossier_absolu"])
def test_perimetre_incomplet(registry, defaut):
    _, ctx, _ = _mission(registry, kind="voice_turn" if defaut == "pas_une_mission" else "mission")
    if defaut == "sans_id":
        ctx.runtime_task_id = None
    elif defaut == "sans_orchestrateur":
        ctx.lumena = None
    elif defaut == "tache_inconnue":
        ctx.runtime_task_id = "task_fantome"
    elif defaut == "dossier_evasion":
        ctx.mission_workspace = "../evasion"
    elif defaut == "dossier_absolu":
        ctx.mission_workspace = "C:/Windows"
    with pytest.raises(MissionScopeError) as excinfo:
        derive_mission_scope(ctx, runtime_task_id=None, caller=REACT)
    assert _code(excinfo) == "ide_mission_scope_incomplete"


def test_mission_annulee(registry):
    orch, ctx, _ = _mission(registry)
    orch.cancel_task("task_worker")
    with pytest.raises(MissionScopeError) as excinfo:
        derive_mission_scope(ctx, runtime_task_id=None, caller=REACT)
    assert _code(excinfo) == "ide_mission_cancelled"


@pytest.mark.parametrize("appelant", [None, UNKNOWN], ids=["absent", "inconnu"])
def test_appelant_inconnu(registry, appelant):
    _, ctx, _ = _mission(registry)
    with pytest.raises(MissionScopeError) as excinfo:
        derive_mission_scope(ctx, runtime_task_id=None, caller=appelant)
    assert _code(excinfo) == "ide_mission_caller_unknown"


# ── authorize_mission_call ───────────────────────────────────────────────────


def test_lecture_de_fichier_sur_le_dossier_de_mission_autorisee(registry, service):
    _, ctx, root = _mission(registry)
    scope = derive_mission_scope(ctx, runtime_task_id=None, caller=REACT)
    assert authorize_mission_call(scope, _semantique(service, "ide__read_file"), str(root)) is None


@pytest.mark.parametrize("nom,code", [
    ("ide__write_file", "ide_mission_mutation_not_connected"),
    ("ide__list_files", "ide_mission_read_unconfined"),
    ("ide__workspace_recent", "ide_mission_read_unconfined"),
    ("ide__command_palette_show", "ide_mission_policy_forbidden"),
])
def test_refus_intrinseques_a_la_commande(registry, service, nom, code):
    _, ctx, root = _mission(registry)
    scope = derive_mission_scope(ctx, runtime_task_id=None, caller=REACT)
    with pytest.raises(MissionScopeError) as excinfo:
        authorize_mission_call(scope, _semantique(service, nom), str(root))
    assert _code(excinfo) == code


@pytest.mark.parametrize("ouverture", ["parent", "aucune", "relative"])
def test_dossier_ide_different_refuse(registry, service, ouverture):
    _, ctx, root = _mission(registry)
    scope = derive_mission_scope(ctx, runtime_task_id=None, caller=REACT)
    chemin = {"parent": str(root.parent), "aucune": None, "relative": "missions/task_lead"}[ouverture]
    with pytest.raises(MissionScopeError) as excinfo:
        authorize_mission_call(scope, _semantique(service, "ide__read_file"), chemin)
    assert _code(excinfo) == "ide_mission_workspace_mismatch"
