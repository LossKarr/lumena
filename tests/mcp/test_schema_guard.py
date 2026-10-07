from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.mcp.schema_guard import MCPSchemaError, MCPSchemaGuard, schema_fingerprint


def tool(name, schema):
    return SimpleNamespace(name=name, input_schema=schema)


def test_baseline_unchanged_drift_and_explicit_acceptance(tmp_path):
    guard = MCPSchemaGuard(tmp_path)
    first = guard.assess("server", [tool("read", {"type": "object"})])
    assert first.accepted and first.status == "baseline_created"
    same = guard.assess("server", [tool("read", {"type": "object"})])
    assert same.accepted and same.status == "unchanged"
    drift = guard.assess("server", [
        tool("read", {"type": "object", "properties": {"path": {"type": "string"}}}),
        tool("delete", {"type": "object"}),
    ])
    assert not drift.accepted
    assert drift.added_tools == ("delete",)
    assert drift.changed_tools == ("read",)
    accepted = guard.assess("server", [
        tool("read", {"type": "object", "properties": {"path": {"type": "string"}}}),
        tool("delete", {"type": "object"}),
    ], accept_current=True)
    assert accepted.accepted and accepted.status == "drift_accepted"


def test_external_refs_and_resource_bombs_are_rejected(tmp_path):
    with pytest.raises(MCPSchemaError, match="remote_ref"):
        schema_fingerprint({"$ref": "https://evil.example/schema.json"})
    nested = {}
    cursor = nested
    for _ in range(30):
        cursor["properties"] = {}
        cursor = cursor["properties"]
    with pytest.raises(MCPSchemaError, match="depth_limit"):
        schema_fingerprint(nested)


def test_corrupted_baseline_fails_closed(tmp_path):
    guard = MCPSchemaGuard(tmp_path)
    (tmp_path / "server.json").write_text('{"schema_version":1,"tools":[]}', encoding="utf-8")
    with pytest.raises(MCPSchemaError, match="baseline_corrupted"):
        guard.assess("server", [tool("read", {})])


def test_pending_acceptance_is_bound_to_exact_fingerprint(tmp_path):
    guard = MCPSchemaGuard(tmp_path)
    guard.assess("server", [tool("read", {})])
    drift = guard.assess("server", [tool("write", {})])
    with pytest.raises(MCPSchemaError, match="fingerprint_mismatch"):
        guard.accept_pending("server", "0" * 64)
    accepted = guard.accept_pending("server", drift.fingerprint)
    assert accepted.accepted
    assert accepted.added_tools == ("write",)
    assert guard.assess("server", [tool("write", {})]).status == "unchanged"
