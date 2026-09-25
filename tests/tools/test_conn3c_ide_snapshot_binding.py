"""An advertised capability must not migrate to a different IDE/workspace."""
import asyncio
from dataclasses import replace
import json
import sys
from types import SimpleNamespace

import pytest

from src.tools.ide_bridge import IDEBridge
from src.tools.ide_pairing import PairingError
from src.tools.ide_pairing_store import PairingStore
from src.tools.ide_protocol import negotiate
from tests.tools.test_conn2a_ide_bridge_auth import paired_client, result_for
from tests.tools.test_conn2b_ide_protocol import hello


def unit_bridge():
    bridge = IDEBridge()
    session, _ = negotiate(hello(), hello()["session_id"])
    bridge._negotiated = session
    bridge._session = SimpleNamespace(session_id=session.session_id)
    bridge._ws = object()
    bridge._connected = True
    bridge._pairing = SimpleNamespace(is_current=lambda _: True)
    return bridge


def test_capture_returns_the_immutable_negotiated_object_with_independent_commands():
    bridge = unit_bridge()
    snapshot = bridge.catalogue_snapshot()
    assert snapshot is bridge._negotiated
    snapshot.commands.clear()
    assert snapshot.commands


@pytest.mark.parametrize("field,value", [
    ("_connected", False), ("_ws", None), ("_session", None), ("_negotiated", None), ("_pairing", None),
    ("_session", SimpleNamespace(session_id="foreign")),
])
def test_capture_requires_one_complete_matching_authenticated_session(field, value):
    bridge = unit_bridge()
    setattr(bridge, field, value)
    assert bridge.catalogue_snapshot() is None


@pytest.mark.parametrize("change", ["socket", "pairing", "session", "catalogue", "disconnect", "revoke"])
def test_capture_does_not_mix_generations_during_pairing_validation(change):
    bridge = unit_bridge()

    def current(_):
        if change == "socket":
            bridge._ws = object()
        elif change == "pairing":
            bridge._pairing = SimpleNamespace(is_current=lambda _: True)
        elif change == "session":
            bridge._session = SimpleNamespace(session_id=bridge._session.session_id)
        elif change == "catalogue":
            bridge._negotiated = replace(bridge._negotiated, workspace_id="aa" * 32)
        elif change == "disconnect":
            bridge._connected = False
        return change != "revoke"

    bridge._pairing.is_current = current
    assert bridge.catalogue_snapshot() is None


def test_pairing_error_and_degraded_catalogue_fail_closed():
    bridge = unit_bridge()
    bridge._negotiated = replace(bridge._negotiated, state="degraded")
    assert bridge.catalogue_snapshot() is None
    bridge = unit_bridge()

    def unavailable(_):
        raise PairingError("pairing_unavailable")

    bridge._pairing.is_current = unavailable
    assert bridge.catalogue_snapshot() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["workspace", "revision", "session", "disconnect", "forged"])
async def test_owner_rejects_stale_snapshot_without_sequence_or_operation_allocation(change):
    bridge = unit_bridge()
    snapshot = bridge.catalogue_snapshot()
    if change == "workspace":
        bridge._negotiated = replace(snapshot, workspace_id="aa" * 32, workspace_path="C:/second")
    elif change == "revision":
        bridge._negotiated = replace(snapshot, catalogue_hash="bb" * 32)
    elif change == "session":
        bridge._session = SimpleNamespace(session_id="foreign")
    elif change == "disconnect":
        bridge._connected = False
    else:
        snapshot = replace(snapshot)
    result = await bridge._send_command_on_owner(
        "get_status", {}, bridge._session.session_id, 9999999999999, expected_snapshot=snapshot)
    assert result == {"success": False, "error": "IDE catalogue snapshot stale", "outcome": "not_sent"}
    assert bridge._pending == {} and bridge._commands == {}
    assert bridge._sequence == 0 and bridge._operation_counter == 0


@pytest.mark.asyncio
@pytest.mark.skipif(sys.platform != "win32", reason="Windows pairing integration")
async def test_real_queued_command_is_not_sent_after_workspace_change(tmp_path, monkeypatch):
    from src.tools import ide_bridge as module
    monkeypatch.setattr(module, "IDE_WS_PORT", 0)
    store = PairingStore(tmp_path / "store")
    bridge = IDEBridge(pairing_store=store)
    await bridge.start_server()
    ws = await paired_client(bridge, store)
    entered, release = asyncio.Event(), asyncio.Event()
    dispatch = bridge._dispatcher.dispatch

    async def held(operation, timeout):
        entered.set()
        await release.wait()
        return await dispatch(operation, timeout)

    monkeypatch.setattr(bridge._dispatcher, "dispatch", held)
    task = None
    try:
        snapshot = bridge.catalogue_snapshot()
        assert snapshot is not None
        task = asyncio.create_task(bridge.send_command("get_status", expected_snapshot=snapshot))
        await asyncio.wait_for(entered.wait(), 2)
        await bridge.handle_message(json.dumps({"type": "ide_workspace", "session_id": snapshot.session_id,
                                               "workspace": {"id": "aa" * 32, "path": "C:/second"}}))
        release.set()
        result = await task
        assert result["outcome"] == "not_sent" and not result["success"]
        assert bridge._sequence == 0 and bridge._pending == {}
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ws.recv(), .05)
        fresh = bridge.catalogue_snapshot()
        task = asyncio.create_task(bridge.send_command("get_status", expected_snapshot=fresh))
        command = json.loads(await asyncio.wait_for(ws.recv(), 2))
        assert command["workspace_id"] == "aa" * 32
        await ws.send(json.dumps(result_for(command, success=True)))
        assert (await task)["success"]
    finally:
        release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await ws.close()
        await bridge.stop_server()
