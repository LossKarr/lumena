import json
from types import SimpleNamespace

import pytest

from src.core_services.agent_service import AgentService
from src.runtime.channel_steering import route_channel_steering
from src.runtime.task_orchestrator import TaskOrchestrator
from src.runtime.task_steering_store import TaskSteeringStore


def _work(orch, objective="panneau client", *, conversation="conv-a"):
    record = orch.start_task(
        conversation_id=conversation, channel="web", message_preview=objective,
        metadata={
            "kind": "agent_turn", "initial_objective": objective,
            "owner_user_id": "local:owner", "source_conversation_id": conversation,
        },
    )
    orch.mark_running(record.task_id)
    return record.task_id


def test_clear_web_orientation_routes_to_unique_normal_agent_turn():
    orch = TaskOrchestrator(persistence_path=None)
    task_id = _work(orch)
    result = route_channel_steering(
        orch, "ajoute aussi une recherche", source_channel="web",
        conversation_id="conv-a", owner_user_id="local:owner", sender_is_owner=True,
    )
    assert result.handled and result.facts["accepted"] is True
    assert TaskSteeringStore(orch).list(task_id)[0]["text"] == "ajoute aussi une recherche"


def test_external_guest_is_refused_before_target_resolution():
    orch = TaskOrchestrator(persistence_path=None)
    _work(orch)
    result = route_channel_steering(
        orch, "change plutôt la couleur", source_channel="telegram",
        conversation_id="tg-other", owner_user_id="local:owner", sender_is_owner=False,
    )
    assert result.handled and result.facts == {
        "code": "owner_mismatch", "accepted": False, "source_channel": "telegram",
    }


def test_owner_can_cross_channels_only_when_root_target_is_unique():
    orch = TaskOrchestrator(persistence_path=None)
    task_id = _work(orch)
    result = route_channel_steering(
        orch, "ne touche plus à l'authentification", source_channel="telegram",
        conversation_id="tg-owner", owner_user_id="local:owner", sender_is_owner=True,
    )
    assert result.facts["task"]["task_id"] == task_id
    _work(orch, "autre mission", conversation="conv-b")
    ambiguous = route_channel_steering(
        orch, "ajoute aussi des tests", source_channel="telegram",
        conversation_id="tg-owner", owner_user_id="local:owner", sender_is_owner=True,
    )
    assert ambiguous.facts["code"] == "ambiguous_target"
    assert len(ambiguous.facts["candidates"]) == 2


def test_ordinary_conversation_is_not_intercepted():
    orch = TaskOrchestrator(persistence_path=None)
    _work(orch)
    result = route_channel_steering(
        orch, "comment vas-tu ?", source_channel="web",
        conversation_id="conv-a", owner_user_id="local:owner", sender_is_owner=True,
    )
    assert result.handled is False and result.facts == {}


@pytest.mark.asyncio
async def test_handled_orientation_never_starts_a_second_turn_when_composition_fails():
    orch = TaskOrchestrator(persistence_path=None)
    task_id = _work(orch)

    class FailingLLM:
        async def chat(self, _messages):
            raise RuntimeError("provider unavailable")

    core = SimpleNamespace(
        task_orchestrator=orch,
        llm=FailingLLM(),
        personality=SimpleNamespace(get_system_prompt=lambda: "Lumena"),
    )
    response = await AgentService(core)._route_and_compose_steering(
        "ajoute aussi une verification", "web", None,
    )

    facts = json.loads(response)
    assert facts["accepted"] is True
    assert facts["task"]["task_id"] == task_id
    assert TaskSteeringStore(orch).list(task_id)[0]["text"] == "ajoute aussi une verification"
