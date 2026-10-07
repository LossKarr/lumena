from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import pytest

from src.mcp.connection_spec import (
    MCPConnectionSpec,
    RemoteSpec,
    TransportKind,
    connection_spec_from_legacy,
)
from src.mcp.server_catalog import CatalogError, MCPServerCatalog


class _Secrets:
    def __init__(self):
        self.values: Dict[str, str] = {}

    def get(self, scope: str, name: str) -> Optional[str]:
        return self.values.get(f"{scope}:{name}")

    def set(self, scope: str, name: str, value: str) -> None:
        self.values[f"{scope}:{name}"] = value


@pytest.fixture
def catalog(tmp_path: Path) -> MCPServerCatalog:
    return MCPServerCatalog(
        catalog_dir=tmp_path / "catalog",
        audit_log_path=tmp_path / "catalog" / "audit.jsonl",
        secrets_service=_Secrets(),
    )


def test_historical_entry_remains_without_new_field(catalog):
    catalog.add_server(
        server_id="legacy",
        display_name="Legacy",
        package_spec="npm:mcp-legacy",
        owner_profile="owner",
    )
    entry = catalog.get_server("legacy")
    assert entry is not None
    assert entry.connection_spec is None
    raw = (catalog.servers_dir / "legacy.json").read_text(encoding="utf-8")
    assert "connection_spec" not in raw


def test_legacy_entry_can_be_migrated_without_status_loss(catalog):
    original = catalog.add_server(
        server_id="legacy",
        display_name="Legacy",
        package_spec="npm:mcp-legacy",
        owner_profile="owner",
    )
    spec = connection_spec_from_legacy("npm:mcp-legacy", version="2.0.0")
    migrated = catalog.update_connection_spec("legacy", spec.to_dict())
    assert migrated.status == original.status
    assert migrated.added_at == original.added_at
    assert migrated.connection_spec == spec.to_dict()
    assert catalog.get_server("legacy") == migrated


def test_remote_entry_requires_and_persists_remote_contract(catalog):
    spec = MCPConnectionSpec(
        transport=TransportKind.STREAMABLE_HTTP,
        remote=RemoteSpec(url="https://mcp.example.test/v1"),
    )
    entry = catalog.add_server(
        server_id="remote-docs",
        display_name="Remote docs",
        package_spec="remote:remote-docs",
        owner_profile="owner",
        connection_spec=spec.to_dict(),
    )
    assert entry.connection_spec == spec.to_dict()
    assert catalog.get_server("remote-docs") == entry


def test_remote_entry_without_contract_is_rejected(catalog):
    with pytest.raises(CatalogError, match="remote_connection_spec_required"):
        catalog.add_server(
            server_id="remote-docs",
            display_name="Remote docs",
            package_spec="remote:remote-docs",
            owner_profile="owner",
        )


def test_connection_contract_must_match_legacy_identity(catalog):
    wrong = connection_spec_from_legacy("pypi:mcp-other").to_dict()
    with pytest.raises(CatalogError, match="connection_spec_binding"):
        catalog.add_server(
            server_id="bound",
            display_name="Bound",
            package_spec="npm:mcp-bound",
            owner_profile="owner",
            connection_spec=wrong,
        )

