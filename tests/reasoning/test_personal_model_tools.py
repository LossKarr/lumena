from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers import personal_models as handlers


class _Plane:
    def __init__(self):
        self.training_args = None

    def status(self):
        return {"principal_model": "deepseek-flash", "personal_model": None, "jobs": [], "lineages": {}}

    def recommendations(self):
        return [{"reason_codes": ["personal_learning_disabled"]}]

    def update_training_settings(self, changes, actor):
        return {"settings": changes, "proof_id": "audit_1"}

    def create_training(self, **kwargs):
        self.training_args = kwargs
        return {"reservation": {"model_name": f"{kwargs['display_prefix']}-model-1.0.0"}}


def test_personal_model_handler_contract_is_unique_and_complete() -> None:
    defs = handlers.get_personal_model_handler_defs()
    names = [item.name for item in defs]
    assert len(names) == len(set(names))
    assert {"personal_model_status", "personal_learning_health", "personal_model_recommendations", "queue_personal_training", "confirm_personal_model_action"} <= set(names)


@pytest.mark.asyncio
async def test_read_tool_returns_dynamic_facts_without_private_examples(monkeypatch) -> None:
    monkeypatch.setattr(handlers, "_plane", lambda: _Plane())
    result = await handlers.personal_model_status_handler(HandlerContext())
    payload = json.loads(result.output)
    assert payload["status"]["principal_model"] == "deepseek-flash"
    assert "messages" not in result.output


@pytest.mark.asyncio
async def test_mutation_requires_explicit_current_user_request(monkeypatch) -> None:
    monkeypatch.setattr(handlers, "_plane", lambda: _Plane())
    refused = await handlers.update_personal_training_settings_handler(HandlerContext(original_user_query="où en est mon modèle ?"), {"enabled": True})
    assert refused.success is False
    allowed = await handlers.update_personal_training_settings_handler(HandlerContext(original_user_query="active et configure l'entraînement"), {"enabled": True})
    assert allowed.success is True
    assert json.loads(allowed.output)["proof_id"] == "audit_1"


@pytest.mark.asyncio
async def test_training_tool_forwards_the_user_model_prefix(monkeypatch) -> None:
    plane = _Plane()
    monkeypatch.setattr(handlers, "_plane", lambda: plane)
    result = await handlers.queue_personal_training_handler(
        HandlerContext(original_user_query="crée et entraîne ma version"),
        "Qwen/Qwen2.5-3B-Instruct",
        {"num_epochs": 1},
        display_prefix="atelier",
    )
    assert result.success is True
    assert plane.training_args["display_prefix"] == "atelier"


def test_tool_registry_loads_personal_model_module() -> None:
    source = (Path(__file__).resolve().parents[2] / "src" / "reasoning" / "tool_registry.py").read_text(encoding="utf-8")
    assert '".handlers.personal_models"' in source
