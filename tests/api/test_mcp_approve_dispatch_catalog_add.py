"""Phase I-7 — Verrouille le pont approve → execute_approved_catalog_add.

Bug d'origine : la route POST /api/mcp/approvals/{action_id}/approve appelait
queue.approve() mais NE déclenchait JAMAIS execute_approved_catalog_add(). Le
ticket passait APPROVED, mais l'entrée DECLARED n'était jamais ajoutée au
catalog. L'utilisateur cliquait "Approuver" et rien ne se passait.

Ce test verrouille le dispatch :
  - ticket mcp_catalog_add approuvé → execute_approved_catalog_add appelé
  - réponse JSON contient `catalog_add: {executed, success, ...}`
  - ticket non-catalog_add (ex. install) → pas de dispatch (clé absente)
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.mcp.approval_queue import ApprovalDecision, ApprovalResult


@pytest.fixture(autouse=True)
def _setup_env(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "0")
    yield


def _make_app(*, queue, catalog_add_orch=None):
    """Construit une app FastAPI mock avec les singletons injectés."""
    from web.routes import deps as _deps
    from web.routes import mcp as _mcp_routes

    _deps._MCP_APPROVAL_QUEUE_SINGLETON = queue
    _deps._MCP_CATALOG_ADD_ORCHESTRATOR_SINGLETON = catalog_add_orch

    app = FastAPI()
    app.include_router(_mcp_routes.router)
    app.dependency_overrides[_deps.verify_admin_token] = lambda: None
    return TestClient(app)


def _make_queue_with_approved_result(*, result: ApprovalResult, action_id: str = "8fc827dec44e4768b9fda6dbb11a2d69"):
    """Queue mock : action_id existe en pending, approve() retourne le ApprovalResult."""
    queue = MagicMock()
    pending_view = MagicMock()
    pending_view.id = action_id
    pending_view.tool_name = "mcp_catalog_add:slack"
    queue.list_pending = MagicMock(return_value=[pending_view])
    queue.approve = MagicMock(return_value=result)
    queue.get = MagicMock(return_value=pending_view)
    return queue


class TestApproveDispatchesCatalogAdd:

    def test_catalog_add_ticket_triggers_execute(self):
        """ticket catalog_add approuvé → orchestrator.execute_approved_catalog_add appelé."""
        result = ApprovalResult(
            decision=ApprovalDecision.APPROVED,
            args={
                "action": "catalog_add",
                "server_id": "slack",
                "display_name": "Slack",
                "package_spec": "npm:@modelcontextprotocol/server-slack",
                "owner_profile": "lumena",
                "version": "latest",
            },
        )
        queue = _make_queue_with_approved_result(result=result)

        orch = MagicMock()
        exec_res = MagicMock()
        exec_res.server_id = "slack"
        exec_res.success = True
        exec_res.reason = "declared"
        exec_res.catalog_status = "declared"
        orch.execute_approved_catalog_add = MagicMock(return_value=exec_res)

        client = _make_app(queue=queue, catalog_add_orch=orch)

        resp = client.post(
            "/api/mcp/approvals/8fc827dec44e4768b9fda6dbb11a2d69/approve",
            json={"confirmed": True, "confirmation_phrase": "I-CONFIRM-APPROVE-TICKET"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["approved"] is True

        # Le dispatcher Phase I-7 doit avoir appelé l'orchestrator
        orch.execute_approved_catalog_add.assert_called_once()
        call_args = orch.execute_approved_catalog_add.call_args
        assert call_args.args[0] == "slack"
        assert call_args.kwargs.get("dry_run") is False

        # La réponse expose le résultat
        assert "catalog_add" in body
        assert body["catalog_add"]["executed"] is True
        assert body["catalog_add"]["server_id"] == "slack"
        assert body["catalog_add"]["success"] is True
        assert body["catalog_add"]["catalog_status"] == "declared"

    def test_non_catalog_add_ticket_no_dispatch(self):
        """ticket install/activation → pas de dispatch catalog_add."""
        result = ApprovalResult(
            decision=ApprovalDecision.APPROVED,
            args={"action": "install", "server_id": "slack"},
        )
        queue = _make_queue_with_approved_result(result=result)

        orch = MagicMock()
        client = _make_app(queue=queue, catalog_add_orch=orch)

        resp = client.post(
            "/api/mcp/approvals/8fc827dec44e4768b9fda6dbb11a2d69/approve",
            json={"confirmed": True, "confirmation_phrase": "I-CONFIRM-APPROVE-TICKET"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["approved"] is True
        # Pas de dispatch
        orch.execute_approved_catalog_add.assert_not_called()
        assert "catalog_add" not in body

    def test_catalog_add_ticket_orchestrator_unavailable(self):
        """ticket catalog_add approuvé mais orchestrator=None → outcome explicite."""
        result = ApprovalResult(
            decision=ApprovalDecision.APPROVED,
            args={
                "action": "catalog_add",
                "server_id": "slack",
                "display_name": "Slack",
                "package_spec": "npm:foo",
                "owner_profile": "lumena",
            },
        )
        queue = _make_queue_with_approved_result(result=result)
        client = _make_app(queue=queue, catalog_add_orch=None)

        resp = client.post(
            "/api/mcp/approvals/8fc827dec44e4768b9fda6dbb11a2d69/approve",
            json={"confirmed": True, "confirmation_phrase": "I-CONFIRM-APPROVE-TICKET"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # Approbation OK même si dispatch impossible (ticket reste APPROVED)
        assert body["approved"] is True
        # Outcome explicite : ne pas masquer l'erreur silencieusement
        assert body["catalog_add"]["executed"] is False
        assert body["catalog_add"]["error_code"] == "orchestrator_unavailable"

    def test_catalog_add_ticket_execute_failure(self):
        """execute_approved_catalog_add lève → outcome=execute_failed."""
        result = ApprovalResult(
            decision=ApprovalDecision.APPROVED,
            args={
                "action": "catalog_add",
                "server_id": "slack",
                "display_name": "Slack",
                "package_spec": "npm:foo",
                "owner_profile": "lumena",
            },
        )
        queue = _make_queue_with_approved_result(result=result)
        orch = MagicMock()
        orch.execute_approved_catalog_add = MagicMock(side_effect=RuntimeError("disk_full"))
        client = _make_app(queue=queue, catalog_add_orch=orch)

        resp = client.post(
            "/api/mcp/approvals/8fc827dec44e4768b9fda6dbb11a2d69/approve",
            json={"confirmed": True, "confirmation_phrase": "I-CONFIRM-APPROVE-TICKET"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["approved"] is True
        assert body["catalog_add"]["executed"] is False
        assert body["catalog_add"]["error_code"] == "execute_failed"
