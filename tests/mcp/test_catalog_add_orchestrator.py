from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from src.mcp.approval_queue import ApprovalDecision
from src.mcp.catalog_add_orchestrator import (
    CATALOG_ADD_RISK_SUMMARY,
    CATALOG_ADD_TOOL_PREFIX,
    CatalogAddError,
    CatalogAddProposalInput,
    MCPCatalogAddOrchestrator,
)
from src.mcp.policy import MCPPolicy


class FakeQueue:
    def __init__(self, ticket_id: str = "e9953b6ad4ae430a8e1d4bf425f55e29") -> None:
        self.ticket_id = ticket_id
        self.calls = []

    def propose(self, **kwargs):
        self.calls.append(kwargs)
        return self.ticket_id


class FakeCatalog:
    def __init__(self, existing=None) -> None:
        self.existing = dict(existing or {})
        self.add_calls = []

    def get_server(self, server_id: str):
        status = self.existing.get(server_id)
        if status is None:
            return None
        return SimpleNamespace(status=SimpleNamespace(value=status))

    def add_server(self, **kwargs):
        self.add_calls.append(kwargs)
        self.existing[kwargs["server_id"]] = "declared"
        return SimpleNamespace(status=SimpleNamespace(value="declared"))


def _proposal(**overrides):
    data = {
        "server_id": "stripe",
        "display_name": "Stripe MCP",
        "package_spec": "npm:@stripe/mcp",
        "version": "1.0.0",
        "trust_score": 82,
    }
    data.update(overrides)
    return CatalogAddProposalInput(**data)


def _approval(**overrides):
    args = {
        "action": "catalog_add",
        "server_id": "stripe",
        "display_name": "Stripe MCP",
        "package_spec": "npm:@stripe/mcp",
        "version": "1.0.0",
        "trust_score": 82,
        "owner_profile": "lumena",
    }
    args.update(overrides.pop("args", {}))
    return SimpleNamespace(
        decision=overrides.pop("decision", ApprovalDecision.APPROVED),
        args=args,
        reason=None,
    )


def test_dry_run_propose_does_not_touch_queue():
    q = FakeQueue()
    orch = MCPCatalogAddOrchestrator(catalog=FakeCatalog(), approval_queue=q)
    result = orch.propose_catalog_add(_proposal(), caller_kind="react", dry_run=True)
    assert result.approval_ticket_id is None
    assert result.tool_name == CATALOG_ADD_TOOL_PREFIX + "stripe"
    assert result.risk_summary == CATALOG_ADD_RISK_SUMMARY
    assert q.calls == []


def test_live_propose_creates_local_write_ticket():
    q = FakeQueue()
    orch = MCPCatalogAddOrchestrator(catalog=FakeCatalog(), approval_queue=q)
    result = orch.propose_catalog_add(_proposal(), caller_kind="react", dry_run=False)
    assert result.approval_ticket_id == q.ticket_id
    call = q.calls[0]
    assert call["tool_name"] == "mcp_catalog_add:stripe"
    assert call["policy"] == MCPPolicy.LOCAL_WRITE
    assert call["caller_kind"] == "react"
    assert call["risk_summary"] == CATALOG_ADD_RISK_SUMMARY
    assert call["args"]["action"] == "catalog_add"
    assert call["args"]["package_spec"] == "npm:@stripe/mcp"


def test_execute_dry_run_does_not_add_server():
    cat = FakeCatalog()
    orch = MCPCatalogAddOrchestrator(catalog=cat, approval_queue=FakeQueue())
    result = orch.execute_approved_catalog_add("stripe", _approval(), dry_run=True)
    assert result.success is False
    assert result.reason == "dry_run"
    assert result.dry_run is True
    assert cat.add_calls == []


def test_execute_live_adds_declared_server():
    cat = FakeCatalog()
    orch = MCPCatalogAddOrchestrator(catalog=cat, approval_queue=FakeQueue())
    result = orch.execute_approved_catalog_add("stripe", _approval(), dry_run=False)
    assert result.success is True
    assert result.catalog_status == "declared"
    call = cat.add_calls[0]
    assert call["server_id"] == "stripe"
    assert call["display_name"] == "Stripe MCP"
    assert call["package_spec"] == "npm:@stripe/mcp"
    assert call["owner_profile"] == "lumena"


def test_execute_existing_catalog_entry_is_idempotent():
    cat = FakeCatalog({"stripe": "declared"})
    orch = MCPCatalogAddOrchestrator(catalog=cat, approval_queue=FakeQueue())
    result = orch.execute_approved_catalog_add("stripe", _approval(), dry_run=False)
    assert result.success is True
    assert result.reason == "already_declared"
    assert cat.add_calls == []


@pytest.mark.parametrize("sid", ["BAD", "con", "stripe/one", "stripe..one", ""])
def test_invalid_server_id_rejected(sid):
    orch = MCPCatalogAddOrchestrator(catalog=FakeCatalog(), approval_queue=FakeQueue())
    with pytest.raises(CatalogAddError):
        orch.propose_catalog_add(_proposal(server_id=sid), dry_run=True)


def test_execute_rejects_wrong_action():
    orch = MCPCatalogAddOrchestrator(catalog=FakeCatalog(), approval_queue=FakeQueue())
    result = orch.execute_approved_catalog_add(
        "stripe",
        _approval(args={"action": "install"}),
        dry_run=False,
    )
    assert result.success is False
    assert result.reason == "approval_action_mismatch"


def test_execute_rejects_server_mismatch():
    orch = MCPCatalogAddOrchestrator(catalog=FakeCatalog(), approval_queue=FakeQueue())
    result = orch.execute_approved_catalog_add(
        "stripe",
        _approval(args={"server_id": "github"}),
        dry_run=False,
    )
    assert result.success is False
    assert result.reason == "approval_server_id_mismatch"


def test_module_has_no_runtime_execution_tokens():
    text = (
        Path(__file__).resolve().parent.parent.parent
        / "src" / "mcp" / "catalog_add_orchestrator.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "subprocess", "Popen(", "call_tool(",
        "execute_approved_install", "activate(",
        "register_dynamic_handler", ".approve(", ".reject(",
    )
    for token in forbidden:
        assert token not in text
