"""Phase 11B — peer_team_request conversationnel.

Objectif : donner au chat une seule entrée naturelle pour collaborer avec
les autres Lumena, sans que le modèle doive choisir manuellement le bon tool.
"""
from __future__ import annotations

import pytest


class TestPeerTeamRouteInference:
    def test_web_request_routes_to_task_delegate_browser_then_chat(self):
        from src.reasoning.handlers.peer_orchestrator import infer_peer_team_routes

        routes = infer_peer_team_routes("Demande lui de lancer une recherche web sur Wikipédia")

        assert routes == [
            {"scope": "task.delegate", "capability": "browser"},
            {"scope": "chat", "capability": "browser"},
        ]

    def test_knowledge_request_routes_to_knowledge_query_then_chat(self):
        from src.reasoning.handlers.peer_orchestrator import infer_peer_team_routes

        routes = infer_peer_team_routes("Demande à l'autre Lumena ce qu'elle sait en mémoire")

        assert routes == [
            {"scope": "knowledge.query", "capability": ""},
            {"scope": "chat", "capability": ""},
        ]

    def test_simple_opinion_routes_to_chat_only(self):
        from src.reasoning.handlers.peer_orchestrator import infer_peer_team_routes

        routes = infer_peer_team_routes("Demande lui son avis sur ce plan")

        assert routes == [{"scope": "chat", "capability": ""}]

    def test_preferred_scope_is_first_but_keeps_chat_fallback(self):
        from src.reasoning.handlers.peer_orchestrator import infer_peer_team_routes

        routes = infer_peer_team_routes(
            "Demande lui son avis",
            preferred_scope="task.delegate",
            capability="documents",
        )

        assert routes == [
            {"scope": "task.delegate", "capability": "documents"},
            {"scope": "chat", "capability": "documents"},
        ]


class TestPeerTeamHandler:
    @pytest.mark.asyncio
    async def test_peer_team_request_falls_back_to_chat_when_task_scope_unavailable(self, monkeypatch):
        from src.reasoning.handlers import peer_orchestrator as mod
        from src.reasoning.handlers.contracts import HandlerResult

        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        calls = []

        async def fake_orchestrate(ctx, **kwargs):
            calls.append(kwargs)
            if kwargs["scope"] == "task.delegate":
                return HandlerResult.fail("scope task.delegate non autorisé")
            return HandlerResult.ok("Réponse salon OK", handler_name="orchestrate_peer_request")

        monkeypatch.setattr(mod, "orchestrate_peer_request_handler", fake_orchestrate)

        result = await mod.peer_team_request_handler(
            None,
            user_request="Demande lui de lancer une recherche web sur Wikipédia",
        )

        assert result.success is True
        assert "route=chat" in result.output
        assert "Réponse salon OK" in result.output
        assert [c["scope"] for c in calls] == ["task.delegate", "chat"]
        assert calls[0]["capability"] == "browser"

    @pytest.mark.asyncio
    async def test_peer_team_request_reports_all_failed_routes(self, monkeypatch):
        from src.reasoning.handlers import peer_orchestrator as mod
        from src.reasoning.handlers.contracts import HandlerResult

        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")

        async def fake_orchestrate(ctx, **kwargs):
            return HandlerResult.fail(f"fail {kwargs['scope']}")

        monkeypatch.setattr(mod, "orchestrate_peer_request_handler", fake_orchestrate)

        result = await mod.peer_team_request_handler(
            None,
            user_request="Demande lui de chercher sur internet",
        )

        assert result.success is False
        assert "task.delegate/browser" in result.output
        assert "chat/browser" in result.output

    @pytest.mark.asyncio
    async def test_peer_team_request_uses_multi_best_for_team_keywords(self, monkeypatch):
        from src.reasoning.handlers import peer_orchestrator as mod
        from src.reasoning.handlers.contracts import HandlerResult

        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        calls = []

        async def fake_orchestrate(ctx, **kwargs):
            calls.append(kwargs)
            return HandlerResult.ok("Synthese multi OK", handler_name="orchestrate_peer_request")

        monkeypatch.setattr(mod, "orchestrate_peer_request_handler", fake_orchestrate)

        result = await mod.peer_team_request_handler(
            None,
            user_request="Demande a plusieurs Lumena de comparer ce plan ensemble",
            max_peers=4,
        )

        assert result.success is True
        assert "strategy=multi_best" in result.output
        assert calls[0]["strategy"] == "multi_best"
        assert calls[0]["max_peers"] == 4

    @pytest.mark.asyncio
    async def test_peer_team_request_keeps_fallback_strategy_for_simple_query(self, monkeypatch):
        from src.reasoning.handlers import peer_orchestrator as mod
        from src.reasoning.handlers.contracts import HandlerResult

        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        calls = []

        async def fake_orchestrate(ctx, **kwargs):
            calls.append(kwargs)
            return HandlerResult.ok("Avis OK", handler_name="orchestrate_peer_request")

        monkeypatch.setattr(mod, "orchestrate_peer_request_handler", fake_orchestrate)

        result = await mod.peer_team_request_handler(
            None,
            user_request="Demande lui son avis",
        )

        assert result.success is True
        assert "strategy=fallback_on_failure" in result.output
        assert calls[0]["strategy"] == "fallback_on_failure"

    @pytest.mark.asyncio
    async def test_peer_team_request_rejects_unknown_preferred_scope(self, monkeypatch):
        from src.reasoning.handlers import peer_orchestrator as mod

        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")

        result = await mod.peer_team_request_handler(
            None,
            user_request="Demande lui",
            preferred_scope="admin",
        )

        assert result.success is False
        assert "preferred_scope" in result.output


class TestPeerTeamToolWiring:
    def test_handler_defs_export_peer_team_request_first(self, monkeypatch):
        from src.reasoning.handlers.peer_orchestrator import get_peer_orchestrator_handler_defs

        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        defs = get_peer_orchestrator_handler_defs()

        assert [d.name for d in defs][:2] == ["peer_team_request", "orchestrate_peer_request"]
        assert [d.category for d in defs][:2] == ["peers", "peers"]
        # P5 : la description oriente désormais vers submit_peer_task pour les missions
        # à livrables (collaboration synchrone sans fichiers à rapatrier sinon).
        assert "Lumena" in defs[0].description
        assert "submit_peer_task" in defs[0].description

    def test_all_peer_handler_defs_use_peers_category(self, monkeypatch):
        from src.reasoning.handlers.peer_delegation import get_peer_delegation_handler_defs
        from src.reasoning.handlers.peer_knowledge import get_peer_knowledge_handler_defs
        from src.reasoning.handlers.peer_orchestrator import get_peer_orchestrator_handler_defs
        from src.reasoning.handlers.peer_tasks import get_peer_tasks_handler_defs

        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        defs = (
            get_peer_delegation_handler_defs()
            + get_peer_knowledge_handler_defs()
            + get_peer_tasks_handler_defs()
            + get_peer_orchestrator_handler_defs()
        )

        assert defs
        assert {d.category for d in defs} == {"peers"}

    def test_peer_team_request_does_not_require_workspace_contract(self):
        from src.reasoning.tool_registry import ToolRegistry
        from src.reasoning.caller_context import CallerContext

        reg = object.__new__(ToolRegistry)
        reg.ide_context = {}
        reg._tool_modules = {"peer_team_request": "peers"}

        obs = ToolRegistry._category_contract_check(
            reg,
            "peer_team_request",
            {"user_request": "demande à l'autre Lumena"},
            CallerContext(kind="react"),
        )

        assert obs is None

    def test_peers_category_contract_is_dedicated_and_workspace_free(self):
        from src.reasoning.tool_categories import get_category_contract, get_semantic_category

        contract = get_category_contract("peers")

        assert contract is not None
        assert contract.name == "peers"
        assert contract.requires_workspace is False
        assert get_semantic_category("peers") == "peers"
