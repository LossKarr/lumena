"""Replay wire identity and explicit recovery; no automatic mutation retry."""

import asyncio
from dataclasses import replace
import json

import pytest

from src.tools import ide_bridge as module
from src.tools.ide_pairing_store import PairingStore
from src.tools.ide_protocol import ProtocolError, negotiate
from tests.tools.test_conn2a_ide_bridge_auth import paired_client, result_for
from tests.tools.test_conn2b_ide_protocol import SESSION, hello
from tests.tools.test_conn2c_ide_transport import connection as _connection_fixture

connection = _connection_fixture


@pytest.mark.parametrize("cursor", [None, True, -1, 1.5, "0", 2**53])
def test_invalid_or_missing_operation_cursor_cannot_negotiate(cursor):
    message = hello()
    if cursor is None:
        message.pop("operation_cursor")
    else:
        message["operation_cursor"] = cursor
    with pytest.raises(ProtocolError):
        negotiate(message, SESSION)


@pytest.mark.asyncio
async def test_new_python_server_starts_after_the_authenticated_native_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "IDE_WS_PORT", 0)
    store = PairingStore(tmp_path / "pairing")
    bridge = module.IDEBridge(pairing_store=store)
    await bridge.start_server()
    socket = await paired_client(bridge, store, transform=lambda message: message.update(operation_cursor=35))
    try:
        caller = asyncio.create_task(bridge.send_command("get_status"))
        request = json.loads(await socket.recv())
        assert request["operation_id"] == request["instance_id"][:16] + f"{36:016x}"
        assert request["sequence"] == 1 and request["mode"] == "execute"
        await socket.send(json.dumps(result_for(request, success=True)))
        assert (await caller)["success"] is True
    finally:
        await socket.close()
        await bridge.stop_server()


@pytest.mark.asyncio
async def test_timeout_exposes_reference_and_resume_uses_new_attempt_not_new_operation(connection):
    bridge, socket = connection
    caller = asyncio.create_task(bridge.send_command("get_status", timeout=0.1))
    first = json.loads(await socket.recv())
    result = await caller
    assert result["success"] is False and result["outcome"] == "unknown"
    assert result["_transport"]["operation_id"] == first["operation_id"]
    assert bridge._pending == {} and bridge._commands == {}
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(socket.recv(), 0.02)
    resume = asyncio.create_task(bridge.send_command("get_status", resume_transport=result["_transport"]))
    second = json.loads(await socket.recv())
    assert second["request_id"] != first["request_id"]
    assert second["operation_id"] == first["operation_id"]
    assert second["sequence"] == 2 and second["mode"] == "resume"
    await socket.send(json.dumps(result_for(second, success=True, operation_id=first["request_id"])))
    resumed = await resume
    assert resumed["success"] is True
    assert resumed["_transport"]["operation_id"] == first["operation_id"]
    assert resumed["operation_id"] == first["request_id"]
    assert bridge._operation_counter == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["instance_id", "catalogue_hash", "workspace_id"])
async def test_resume_cannot_switch_to_another_instance_catalogue_or_workspace(connection, field):
    bridge, socket = connection
    caller = asyncio.create_task(bridge.send_command("get_status"))
    request = json.loads(await socket.recv())
    await socket.send(json.dumps(result_for(request, success=True)))
    reference = (await caller)["_transport"]
    bridge._negotiated = replace(bridge._negotiated, **{field: "f" * (32 if field == "instance_id" else 64)})
    result = await bridge.send_command("get_status", resume_transport=reference)
    assert result == {"success": False, "error": "IDE resume scope changed", "outcome": "unknown"}
    assert bridge._sequence == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("reference", [{}, [], "id", {"operation_id": "f" * 32}])
async def test_incomplete_resume_reference_is_refused_before_dispatch(connection, reference):
    bridge, _ = connection
    result = await bridge.send_command("get_status", resume_transport=reference)
    assert result["success"] is False and result["outcome"] == "not_sent"
    assert bridge._sequence == 0


@pytest.mark.asyncio
async def test_future_resume_id_cannot_advance_the_native_cursor(connection):
    bridge, socket = connection
    caller = asyncio.create_task(bridge.send_command("get_status"))
    request = json.loads(await socket.recv())
    await socket.send(json.dumps(result_for(request, success=True)))
    reference = (await caller)["_transport"]
    reference["operation_id"] = request["instance_id"][:16] + f"{2:016x}"
    result = await bridge.send_command("get_status", resume_transport=reference)
    assert result["error"] == "Unknown IDE resume reference"
    assert bridge._sequence == 1 and bridge._operation_counter == 1


@pytest.mark.asyncio
async def test_unknown_native_replay_stays_unknown_without_a_fresh_retry(connection):
    bridge, socket = connection
    caller = asyncio.create_task(bridge.send_command("get_status"))
    first = json.loads(await socket.recv())
    await socket.send(json.dumps(result_for(first, success=True)))
    reference = (await caller)["_transport"]
    retry = asyncio.create_task(bridge.send_command("get_status", resume_transport=reference))
    request = json.loads(await socket.recv())
    await socket.send(json.dumps(result_for(request, success=False, error="operation_unknown", outcome="unknown")))
    assert (await retry)["outcome"] == "unknown"
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(socket.recv(), 0.02)
    assert bridge._operation_counter == 1 and bridge._sequence == 2
