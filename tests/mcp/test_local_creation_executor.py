from pathlib import Path

from src.mcp.approval_queue import ApprovalDecision
from src.mcp.local_creation_executor import MCPLocalCreationExecutor
from src.mcp.server_catalog import ServerStatus


class FakeApprovalResult:
    def __init__(self, server_id="airtable_1234abcd", action="local_create"):
        intent = "Connecter Airtable pour lire les bases et ecrire des lignes"
        import hashlib
        self.decision = ApprovalDecision.APPROVED
        self.args = {
            "action": action,
            "server_id": server_id,
            "intent": intent,
            "intent_hash": hashlib.sha256(intent.encode("utf-8")).hexdigest(),
        }


class FakeCatalog:
    def __init__(self):
        self.entries = {}
        self.add_calls = []

    def get_server(self, server_id):
        return self.entries.get(server_id)

    def add_server(self, **kwargs):
        self.add_calls.append(kwargs)
        entry = type("Entry", (), {
            "server_id": kwargs["server_id"],
            "status": ServerStatus.DECLARED,
        })()
        self.entries[kwargs["server_id"]] = entry
        return entry


def test_execute_local_creation_dry_run_does_not_write(tmp_path):
    catalog = FakeCatalog()
    executor = MCPLocalCreationExecutor(catalog=catalog, root_dir=tmp_path)

    result = executor.execute_approved_local_creation(
        FakeApprovalResult(), server_id="airtable_1234abcd", dry_run=True
    )

    assert result.success is False
    assert result.dry_run is True
    assert result.reason == "dry_run"
    assert catalog.add_calls == []
    assert not (tmp_path / "requests" / "airtable_1234abcd.json").exists()


def test_execute_local_creation_writes_request_and_declares_catalog(tmp_path):
    catalog = FakeCatalog()
    executor = MCPLocalCreationExecutor(catalog=catalog, root_dir=tmp_path)

    result = executor.execute_approved_local_creation(
        FakeApprovalResult(), server_id="airtable_1234abcd", dry_run=False
    )

    assert result.success is True
    assert result.reason == "declared_built"
    assert result.catalog_status == "declared"
    request_path = tmp_path / "requests" / "airtable_1234abcd.json"
    assert request_path.exists()
    assert (tmp_path / "packages" / "airtable_1234abcd" / "pyproject.toml").exists()
    assert result.created_package_path_relative == "packages/airtable_1234abcd"
    call = catalog.add_calls[0]
    assert call["server_id"] == "airtable_1234abcd"
    assert call["package_spec"] == "local:airtable_1234abcd"
    assert call["owner_profile"] == "lumena"
    assert call["trust_score"] == 70
    assert call["notes"] == "local_creation_built"


def test_execute_local_creation_rejects_action_mismatch(tmp_path):
    catalog = FakeCatalog()
    executor = MCPLocalCreationExecutor(catalog=catalog, root_dir=tmp_path)

    result = executor.execute_approved_local_creation(
        FakeApprovalResult(action="install"), server_id="airtable_1234abcd", dry_run=False
    )

    assert result.success is False
    assert result.reason == "approval_action_mismatch"
    assert catalog.add_calls == []


def test_execute_local_creation_existing_catalog_is_idempotent(tmp_path):
    catalog = FakeCatalog()
    catalog.add_server(
        server_id="airtable_1234abcd",
        display_name="Local MCP airtable_1234abcd",
        package_spec="local:airtable_1234abcd",
        owner_profile="lumena",
        trust_score=70,
        notes="local_creation_built",
    )
    executor = MCPLocalCreationExecutor(catalog=catalog, root_dir=tmp_path)

    result = executor.execute_approved_local_creation(
        FakeApprovalResult(), server_id="airtable_1234abcd", dry_run=False
    )

    assert result.success is True
    assert result.reason == "already_declared"
    assert len(catalog.add_calls) == 1
