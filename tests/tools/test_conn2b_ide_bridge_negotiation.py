import asyncio
import json
import sys

import pytest
import pytest_asyncio
import websockets

from src.tools import ide_bridge as module
from src.tools.ide_pairing_store import PairingStore
from tests.tools.test_conn2a_ide_bridge_auth import paired_client, result_for
from tests.tools.test_conn2b_ide_protocol import hello

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(sys.platform != "win32", reason="Windows pairing integration")]


@pytest_asyncio.fixture
async def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "IDE_WS_PORT", 0)
    store = PairingStore(tmp_path / "store")
    value = module.IDEBridge(pairing_store=store)
    await value.start_server()
    try:
        yield value, store
    finally:
        await value.stop_server()


async def test_authenticated_transport_without_hello_cannot_receive_commands(bridge):
    value, store = bridge
    ws = await paired_client(value, store, negotiate_protocol=False)
    try:
        assert not value.ready and not value.connected
        assert not (await value.send_command("write_file"))["success"]
        await ws.send(json.dumps({"type": "ide_connected", "workspace": "forged"}))
        with pytest.raises(websockets.ConnectionClosed):
            await ws.recv()
        assert value.workspace is None
        assert value.protocol_status["state"] == "incompatible"
        assert (await value.get_state())["protocol_status"]["state"] == "incompatible"
        assert (await value.get_status())["protocol_status"]["state"] == "incompatible"
    finally:
        await ws.close()


@pytest.mark.parametrize("invalid", ["version", "hash", "session"])
async def test_incompatible_peer_cannot_replace_ready_peer(bridge, invalid):
    value, store = bridge
    good = await paired_client(value, store)
    original = value._ws
    def corrupt(message):
        if invalid == "version":
            message["protocol"] = {"min": 2, "max": 2}
        elif invalid == "hash":
            message["catalogue"]["hash"] = "00" * 32
        else:
            message["session_id"] = "00" * 16
    try:
        with pytest.raises(websockets.ConnectionClosed):
            await paired_client(value, store, transform=corrupt)
        assert value._ws is original and value.ready
        assert value.protocol_status["state"] == "ready"
    finally:
        await good.close()


async def test_only_supported_negotiated_commands_are_sent(bridge):
    value, store = bridge
    good = await paired_client(value, store)
    try:
        assert value.ready
        assert not (await value.send_command("write_file"))["success"]
        oversized = await value.send_command("get_status", {"data": "x" * 1_048_576})
        assert oversized == {"success": False, "error": "IDE command exceeds frame limit"}
        assert value._pending == {}
        task = asyncio.create_task(value.get_status())
        command = json.loads(await good.recv())
        assert command["action"] == "get_status"
        await good.send(json.dumps(result_for(command, success=True)))
        assert (await task)["protocol_status"]["protocol"] == 3
    finally:
        await good.close()


async def test_workspace_change_preserves_pending_response_and_catalogue(bridge):
    value, store = bridge
    good = await paired_client(value, store)
    try:
        digest = value.protocol_status["catalogue_hash"]
        task = asyncio.create_task(value.get_status())
        command = json.loads(await good.recv())
        await good.send(json.dumps({"type": "ide_workspace", "session_id": value._session.session_id,
                                   "workspace": {"id": "aa" * 32, "path": "C:/second"}}))
        await good.send(json.dumps(result_for(command, success=True)))
        assert (await task)["success"]
        assert value.workspace == "C:/second" and value.ready
        assert value.protocol_status["catalogue_hash"] == digest
    finally:
        await good.close()


async def test_empty_catalogue_is_degraded_not_control_ready(bridge):
    value, store = bridge
    good = await paired_client(value, store, transform=lambda message: message.update(catalogue=hello([])["catalogue"]))
    try:
        assert value.authenticated and not value.ready
        assert value.protocol_status["state"] == "degraded"
        assert not (await value.get_status())["success"]
        state = await value.get_state()
        assert not state["success"] and not state["connected"]
        assert state["protocol_status"]["state"] == "degraded"
    finally:
        await good.close()


async def test_foreign_workspace_session_disconnects_without_accepting_path(bridge):
    value, store = bridge
    good = await paired_client(value, store)
    try:
        await good.send(json.dumps({"type": "ide_workspace", "session_id": "00" * 16,
                                   "workspace": {"id": "aa" * 32, "path": "C:/forged"}}))
        with pytest.raises(websockets.ConnectionClosed):
            await good.recv()
        assert not value.ready and value.workspace is None
    finally:
        await good.close()
