"""Actual runtime discovery + negotiated catalogue, with no launch side effect."""
from dataclasses import replace
import json

import pytest

from src.reasoning.external_tool_registry import ExternalToolCatalog, ExternalToolError
from src.reasoning.tool_semantics import ModelExposure
from src.tools.ide_capabilities import IDECapabilityService
from src.tools.ide_discovery import IDEDiscoveryService
from src.tools.ide_protocol import negotiate
from src.tools.ide_semantics import audited_ide_commands, local_ide_semantics
from tests.tools.test_conn1a_ide_discovery import _packaged
from tests.tools.test_conn2b_ide_protocol import hello
from tests.tools.test_conn3b_ide_semantics import command
from tests.tools.test_conn3c_ide_snapshot_binding import unit_bridge


@pytest.fixture
def service(tmp_path):
    root = tmp_path / "lumena"
    runtime = root / "ide" / "win-unpacked"
    _packaged(runtime)
    discovery = IDEDiscoveryService(root, environ={}, os_install_roots=[], portable_roots=[runtime],
                                    which=lambda _: None, platform_id="windows-x64")
    bridge = unit_bridge()
    message = hello([command(name) for name in audited_ide_commands()])
    bridge._negotiated, _ = negotiate(message, message["session_id"])
    return IDECapabilityService(discovery, bridge)


def test_no_valid_runtime_means_zero_tools_even_if_a_peer_is_connected(service):
    installation = service.discovery.discover().installation
    assert installation is not None
    installation.executable.unlink()
    assert service.bridge.catalogue_snapshot() is not None
    snapshot = service.capture()
    assert snapshot.state == "unavailable" and snapshot.tools == ()
    assert ExternalToolCatalog((snapshot,)).schemas() == []


def test_valid_closed_runtime_exposes_launch_only_with_strict_historical_schema(service):
    service.bridge._connected = False
    snapshot = service.capture()
    assert snapshot.state == "launch_only"
    assert [tool.name for tool in snapshot.tools] == ["ide_launch"]
    assert service.prepare(snapshot, "ide_launch", {"workspace": "C:/project"}).parameters == {"workspace": "C:/project"}
    assert json.loads(snapshot.tools[0].schema_json)["additionalProperties"] is False
    with pytest.raises(ExternalToolError):
        service.prepare(snapshot, "ide_status", {})
    with pytest.raises(ExternalToolError):
        service.expected_session(snapshot)


def test_authenticated_catalogue_offers_only_supported_direct_contextual_audited_commands(service):
    snapshot = service.capture()
    assert snapshot.state == "ready"
    assert len(snapshot.tools) > 50
    expected = set()
    for item in service.bridge._negotiated.commands:
        descriptor = local_ide_semantics(item["id"], instance_id="i", revision="r",
                                         availability=snapshot.tools[0].semantics.availability)
        if descriptor.model_exposure in {ModelExposure.DIRECT, ModelExposure.CONTEXTUAL}:
            expected.add("ide__" + item["id"])
    assert {tool.name for tool in snapshot.tools} == expected
    assert "ide__operation_retry" not in expected and "ide_launch" not in expected
    assert service.expected_session(snapshot) is service.bridge._negotiated


def test_unsupported_and_peer_restricted_commands_are_hidden_not_promoted(service):
    commands = [command("get_status"), command("read_file"), command("write_file")]
    commands[1]["supported"] = False
    commands[2]["semantics"]["model_exposure"] = "never"
    message = hello(commands)
    service.bridge._negotiated, _ = negotiate(message, message["session_id"])
    assert [tool.name for tool in service.capture().tools] == ["ide__get_status"]


def test_transition_does_not_rewrite_old_catalogue_or_silently_rebind_calls(service):
    connected = service.capture()
    old_schema = ExternalToolCatalog((connected,)).schemas()
    old_session = service.bridge._negotiated
    service.bridge._connected = False
    closed = service.capture()
    assert len(closed.tools) == 1 and closed.tools[0].name == "ide_launch"
    assert not service.is_current(connected)
    with pytest.raises(ExternalToolError, match="ide_snapshot_stale"):
        service.prepare(connected, "ide__get_status", {})
    assert ExternalToolCatalog((connected,)).schemas() == old_schema
    service.bridge._connected = True
    service.bridge._negotiated = replace(old_session, workspace_id="ff" * 32)
    assert not service.is_current(connected) and not service.is_current(closed)
    fresh = service.capture()
    assert service.prepare(fresh, "ide__get_status", {}).spec.name == "ide__get_status"


def test_runtime_removal_or_replacement_invalidates_snapshot(service):
    snapshot = service.capture()
    executable = service.discovery.discover().installation.executable
    executable.write_bytes(b"different-build")
    assert not service.is_current(snapshot)
    with pytest.raises(ExternalToolError, match="ide_snapshot_stale"):
        service.expected_session(snapshot)
    executable.unlink()
    assert service.capture().tools == ()


def test_capture_aborts_if_session_changes_while_catalogue_is_being_read(service, monkeypatch):
    original = service.bridge.catalogue_snapshot
    count = 0

    def changing():
        nonlocal count
        count += 1
        if count == 2:
            service.bridge._negotiated = replace(service.bridge._negotiated, workspace_id="fe" * 32)
        return original()

    monkeypatch.setattr(service.bridge, "catalogue_snapshot", changing)
    snapshot = service.capture()
    assert snapshot.state == "degraded" and not snapshot.tools


def test_strict_unknown_and_legacy_names_cannot_fall_back_to_another_tool(service):
    snapshot = service.capture()
    for name in ("ide_status", "ide__get_statu", "mcp__ide__get_status", "GET_STATUS", "read_file"):
        with pytest.raises(ExternalToolError, match="external_tool_not_in_snapshot"):
            service.prepare(snapshot, name, {})
    with pytest.raises(ExternalToolError, match="external_parameters_invalid"):
        service.prepare(snapshot, "ide__get_status", {"unknown_secret": "private-value"})
