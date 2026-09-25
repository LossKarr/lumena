from pathlib import Path

from src.runtime.task_orchestrator import TaskOrchestrator
from src.runtime.work_registry import ActiveWorkRegistry
from src.runtime.work_target_resolver import WorkTargetResolver


def _task(orch, *, owner="owner-a", conversation="conv-a", kind="agent_turn"):
    record = orch.start_task(
        conversation_id=conversation,
        channel="web",
        message_preview="objectif",
        metadata={"kind": kind, "owner_user_id": owner, "objective": "objectif"},
    )
    orch.mark_running(record.task_id)
    return record.task_id


def test_runtime_registry_includes_normal_agent_turns_and_filters_owner():
    orch = TaskOrchestrator(persistence_path=None)
    owned = _task(orch)
    _task(orch, owner="owner-b")
    assert ActiveWorkRegistry(orch, owner_user_id="owner-a").active_ids() == [owned]


def test_owner_scoped_registry_excludes_legacy_work_without_owner():
    orch = TaskOrchestrator(persistence_path=None)
    ownerless = orch.start_task(
        conversation_id="conv-a", channel="web", message_preview="ancien travail",
        metadata={"kind": "agent_turn", "objective": "ancien travail"},
    )
    orch.mark_running(ownerless.task_id)
    assert ActiveWorkRegistry(orch, owner_user_id="owner-a").active_ids() == []


def test_resolver_rejects_cross_owner_and_cross_conversation_targets():
    orch = TaskOrchestrator(persistence_path=None)
    task_id = _task(orch)
    resolver = WorkTargetResolver(orch)
    assert resolver.resolve(owner_user_id="owner-b", preferred_task_id=task_id).code == "owner_mismatch"
    assert resolver.resolve(
        owner_user_id="owner-a", conversation_id="conv-b", preferred_task_id=task_id,
    ).code == "conversation_mismatch"


def test_resolver_requires_explicit_target_when_multiple_are_visible():
    orch = TaskOrchestrator(persistence_path=None)
    first = _task(orch)
    second = _task(orch)
    result = WorkTargetResolver(orch).resolve(owner_user_id="owner-a", conversation_id="conv-a")
    assert result.code == "ambiguous_target"
    assert set(result.candidates) == {first, second}


def test_general_runtime_layers_do_not_import_voice():
    runtime_root = Path(__file__).parents[2] / "src" / "runtime"
    offenders = []
    for path in runtime_root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "src.voice" in text:
            offenders.append(path.name)
    assert offenders == []


def test_general_steering_entrypoints_do_not_import_voice():
    root = Path(__file__).parents[2]
    entrypoints = [
        root / "src" / "core_services" / "agent_service.py",
        root / "src" / "reasoning" / "steering_runtime.py",
        root / "src" / "subagents" / "mission_amendments.py",
        root / "src" / "runtime" / "mission_steering.py",
        root / "web" / "routes" / "steering.py",
    ]
    offenders = [
        str(path.relative_to(root))
        for path in entrypoints
        if "src.voice" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_le_registre_ecarte_un_travail_d_une_session_precedente():
    """Un tour laisse actif par un arret du serveur n'est plus du travail en cours.

    Rien ne reconcilie les taches au demarrage : un tour interrompu reste
    `checkpointed` indefiniment. Le registre projette « ce qui travaille
    MAINTENANT » ; une tache dont la derniere mise a jour precede le demarrage du
    processus courant ne peut pas travailler maintenant. Les missions gardent leur
    propre reprise au boot : ce filtre ne touche pas la persistance.
    """
    orch = TaskOrchestrator(persistence_path=None)
    vivant = _task(orch)
    zombie = _task(orch)
    record = orch._tasks[zombie]
    assert hasattr(record, "updated_at"), "le record ne porte pas d'horodatage de mise a jour"
    record.updated_at = "2026-01-01T00:00:00+00:00"
    assert ActiveWorkRegistry(orch, owner_user_id="owner-a").active_ids() == [vivant], (
        "un tour d'une session precedente est encore presente comme travail actif"
    )
