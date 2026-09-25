import json
from types import SimpleNamespace

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers import local_models as handlers


class Manager:
    async def search(self, *args, **kwargs):
        return {"models": [], "sources": {}}

    async def recommend(self, *args, **kwargs):
        return {"recommendations": []}

    def install(self, reference, **kwargs):
        return SimpleNamespace(as_dict=lambda: {"job_id": "a" * 32, "state": "queued"})

    async def delete(self, reference, ticket, source, **kwargs):
        return {"deleted": True, "state": "absent", "verified": True}


@pytest.mark.asyncio
async def test_search_returns_structured_facts_without_canned_reply(monkeypatch):
    monkeypatch.setattr(handlers, "_manager", lambda: Manager())
    result = await handlers.search_local_models_handler(HandlerContext(), query="code")
    assert result.success is True
    assert json.loads(result.output) == {"ok": True, "models": [], "sources": {}}
    assert "je " not in result.output.casefold()


@pytest.mark.asyncio
async def test_install_requires_current_explicit_user_request(monkeypatch):
    monkeypatch.setattr(handlers, "_manager", lambda: Manager())
    denied = await handlers.install_local_model_handler(
        HandlerContext(original_user_query="parle-moi de qwen3"), "qwen3:8b"
    )
    allowed = await handlers.install_local_model_handler(
        HandlerContext(original_user_query="Installe le modèle qwen3 8b"), "qwen3:8b"
    )
    assert denied.success is False
    assert json.loads(denied.output)["error_code"] == "explicit_user_install_request_required"
    assert allowed.success is True
    assert json.loads(allowed.output)["accepted"] is True


@pytest.mark.asyncio
async def test_mission_worker_cannot_install_global_model(monkeypatch):
    monkeypatch.setattr(handlers, "_manager", lambda: Manager())
    ctx = HandlerContext(original_user_query="Installe qwen3 8b", is_mission_run=True)
    result = await handlers.install_local_model_handler(ctx, "qwen3:8b")
    assert result.success is False


@pytest.mark.asyncio
async def test_pronoun_resolution_is_bound_to_last_search_result(monkeypatch):
    class SearchManager(Manager):
        async def search(self, *args, **kwargs):
            return {
                "models": [
                    {"reference": {"canonical": "first:1b"}},
                    {"reference": {"canonical": "second:2b"}},
                ],
                "sources": {},
            }

    monkeypatch.setattr(handlers, "_manager", lambda: SearchManager())
    ctx = HandlerContext(original_user_query="trouve deux modèles")
    await handlers.search_local_models_handler(ctx)
    ctx.original_user_query = "Installe le premier modèle"
    allowed = await handlers.install_local_model_handler(ctx, "first:1b")
    denied = await handlers.install_local_model_handler(ctx, "second:2b")
    assert allowed.success is True
    assert denied.success is False


def test_handler_registry_has_read_and_ticketed_mutation_tools():
    names = {item.name for item in handlers.get_local_model_handler_defs()}
    assert {"search_local_models", "recommend_local_model", "install_local_model", "disable_local_model"} <= names
    assert "prepare_delete_local_model" in names
    assert "confirm_delete_local_model" in names
    assert "delete_local_model" not in names


@pytest.mark.asyncio
async def test_delete_confirmation_uses_original_user_message_not_model_boolean(monkeypatch):
    monkeypatch.setattr(handlers, "_manager", lambda: Manager())
    denied = await handlers.confirm_delete_local_model_handler(
        HandlerContext(original_user_query="Supprime qwen3 8b"), "qwen3:8b", "x" * 43
    )
    allowed = await handlers.confirm_delete_local_model_handler(
        HandlerContext(original_user_query="Je confirme la suppression du modèle qwen3 8b"), "qwen3:8b", "x" * 43
    )
    assert denied.success is False
    assert allowed.success is True
