from src.mcp.local_creation_ticket import (
    LOCAL_CREATE_RISK_SUMMARY,
    LOCAL_CREATE_TOOL_PREFIX,
    LocalCreationTicketError,
    MCPLocalCreationTicketOrchestrator,
)
from src.mcp.policy import MCPPolicy


class FakeApprovalQueue:
    def __init__(self):
        self.calls = []

    def propose(self, **kwargs):
        self.calls.append(kwargs)
        return "e9953b6ad4ae430a8e1d4bf425f55e29"


def test_propose_local_creation_creates_real_pending_ticket_shape():
    queue = FakeApprovalQueue()
    orch = MCPLocalCreationTicketOrchestrator(queue)

    proposal = orch.propose_local_creation(
        "Connecter Airtable pour lire les bases et ecrire des lignes",
        caller_kind="react",
        dry_run=False,
    )

    assert proposal.approval_ticket_id == "e9953b6ad4ae430a8e1d4bf425f55e29"
    assert proposal.tool_name.startswith(LOCAL_CREATE_TOOL_PREFIX)
    assert proposal.risk_summary == LOCAL_CREATE_RISK_SUMMARY
    assert proposal.dry_run is False
    assert len(queue.calls) == 1
    call = queue.calls[0]
    assert call["tool_name"] == proposal.tool_name
    assert call["policy"] is MCPPolicy.LOCAL_WRITE
    assert call["caller_kind"] == "react"
    assert call["risk_summary"] == "local_creation_required"
    assert call["args"]["action"] == "local_create"
    assert call["args"]["server_id"] == proposal.suggested_server_id
    assert call["args"]["intent_hash"]
    assert "Airtable" in call["args"]["intent"]


def test_propose_local_creation_dry_run_does_not_write_queue():
    queue = FakeApprovalQueue()
    orch = MCPLocalCreationTicketOrchestrator(queue)

    proposal = orch.propose_local_creation(
        "Connecter Airtable pour lire les bases et ecrire des lignes",
        caller_kind="react",
        dry_run=True,
    )

    assert proposal.approval_ticket_id is None
    assert proposal.dry_run is True
    assert queue.calls == []


def test_propose_local_creation_rejects_invalid_intent():
    orch = MCPLocalCreationTicketOrchestrator(FakeApprovalQueue())

    try:
        orch.propose_local_creation("abc", dry_run=False)
    except LocalCreationTicketError as exc:
        assert str(exc) == "intent_invalid"
    else:
        raise AssertionError("expected LocalCreationTicketError")
