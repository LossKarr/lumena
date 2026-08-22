"""Lot 5 (B′) — PLAN GUARD : discriminant `_is_mission_run` (double verrou).

Prouve que la relaxation du PLAN GUARD ne s'active QUE dans une vraie mission
(task_id + metadata.kind='mission') et JAMAIS pour le chat (présent non gaté).
La logique de "travail prouvé" est testée séparément (plan_progress.mission_progress_proven).
"""
from __future__ import annotations

import types

from src.reasoning.react import ReActLoop


def _engine(task_orchestrator=None, task_id=None):
    return ReActLoop(task_orchestrator=task_orchestrator, task_id=task_id)


def _orch_returning(meta):
    # faux orchestrateur minimal : get_task(id) -> dict {metadata: ...}
    return types.SimpleNamespace(get_task=lambda _tid: {"metadata": meta} if meta is not None else None)


def test_chat_sans_task_id_nest_pas_une_mission():
    assert _engine(_orch_returning({"kind": "mission"}), None)._is_mission_run is False


def test_task_id_sans_orchestrateur_nest_pas_une_mission():
    assert _engine(None, "task_x")._is_mission_run is False


def test_kind_non_mission_nest_pas_une_mission():
    # un task_id tracké mais PAS une mission (ex: tâche d'autonomie) → relaxation OFF
    assert _engine(_orch_returning({"kind": "autonomy"}), "task_x")._is_mission_run is False
    assert _engine(_orch_returning({}), "task_x")._is_mission_run is False
    assert _engine(_orch_returning(None), "task_x")._is_mission_run is False


def test_vraie_mission_est_detectee():
    assert _engine(_orch_returning({"kind": "mission"}), "task_x")._is_mission_run is True


def test_orchestrateur_qui_leve_ne_casse_pas():
    boom = types.SimpleNamespace(get_task=lambda _tid: (_ for _ in ()).throw(RuntimeError("x")))
    assert _engine(boom, "task_x")._is_mission_run is False
