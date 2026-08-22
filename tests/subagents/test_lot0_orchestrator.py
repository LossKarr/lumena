"""Lot 0.a — TaskOrchestrator unique (web ← cœur).

Vérifie que `unify_task_orchestrator` fait pointer l'instance du web sur celle,
persistante, du cœur — et qu'après ça, panneau (routes) et boucle voient la MÊME
instance. Cas dégradés couverts (lumena absente / sans orchestrateur).
"""
from __future__ import annotations

import types

from src.subagents.wiring import unify_task_orchestrator


class _FakeOrchestrator:
    def __init__(self, persistent: bool = True):
        self.persistent = persistent


class _FakeLumena:
    def __init__(self, orch):
        self.task_orchestrator = orch


def _fake_deps(initial=None):
    """Module factice imitant `web.routes.deps` (porte `_TASK_ORCHESTRATOR`)."""
    m = types.SimpleNamespace()
    m._TASK_ORCHESTRATOR = initial
    return m


def test_unify_points_web_to_core_instance():
    core_orch = _FakeOrchestrator()
    deps = _fake_deps(initial=_FakeOrchestrator())  # ancienne instance volatile
    lumena = _FakeLumena(core_orch)

    assert unify_task_orchestrator(deps, lumena) is True
    # le web pointe DÉSORMAIS sur l'instance du cœur (même objet)
    assert deps._TASK_ORCHESTRATOR is core_orch


def test_unify_noop_when_lumena_none():
    deps = _fake_deps(initial="sentinel")
    assert unify_task_orchestrator(deps, None) is False
    assert deps._TASK_ORCHESTRATOR == "sentinel"  # inchangé


def test_unify_noop_when_no_orchestrator():
    deps = _fake_deps(initial="sentinel")
    lumena = types.SimpleNamespace()  # pas d'attribut task_orchestrator
    assert unify_task_orchestrator(deps, lumena) is False
    assert deps._TASK_ORCHESTRATOR == "sentinel"


def test_unify_noop_when_deps_none():
    # ultra-défensif : ne casse pas si le module deps est absent
    assert unify_task_orchestrator(None, _FakeLumena(_FakeOrchestrator())) is False


def test_real_core_orchestrator_is_persistent_and_shared(tmp_path):
    """Sanity sur les vraies classes : l'orchestrateur du cœur a une persistance,
    et l'unification partage bien CETTE instance (panel = worker)."""
    from src.runtime.task_orchestrator import TaskOrchestrator

    core_orch = TaskOrchestrator(persistence_path=str(tmp_path / "lot0_state.json"))
    deps = _fake_deps(initial=TaskOrchestrator())  # volatile (sans persistance)
    lumena = _FakeLumena(core_orch)

    assert unify_task_orchestrator(deps, lumena) is True
    assert deps._TASK_ORCHESTRATOR is core_orch
    # une tâche créée « côté route » est visible « côté boucle » (même instance)
    rec = deps._TASK_ORCHESTRATOR.start_task(
        conversation_id="__missions__", channel="mission", message_preview="x",
        metadata={"kind": "mission"},
    )
    assert core_orch.get_task(rec.task_id) is not None
