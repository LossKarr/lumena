import asyncio
import json
import sys

import pytest
import websockets

from src.tools import ide_bridge as module
from src.tools.ide_pairing_store import PairingStore
from tests.tools.test_conn2a_ide_pairing import response
from tests.tools.test_conn2b_ide_protocol import hello

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(sys.platform != "win32", reason="Windows pairing integration")]


def result_for(command, **result):
    fields = ("transport_version", "session_id", "instance_id", "catalogue_revision", "request_id",
              "operation_id", "sequence", "workspace_id")
    return {"type": "result", **{field: command[field] for field in fields}, "result": result}


async def paired_client(bridge, store, *, negotiate_protocol=True, transform=None):
    port = bridge._server.sockets[0].getsockname()[1]
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    challenge = json.loads(await ws.recv())
    await ws.send(json.dumps(response(challenge, store.load().secret)))
    assert json.loads(await ws.recv())["type"] == "auth_ok"
    if negotiate_protocol:
        message = hello()
        message["session_id"] = challenge["session_id"]
        if transform:
            transform(message)
        await ws.send(json.dumps(message))
        assert json.loads(await ws.recv())["type"] == "ide_hello_ack"
    return ws


async def test_real_bridge_rejects_catalogue_before_authentication(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "IDE_WS_PORT", 0)
    bridge = module.IDEBridge(pairing_store=PairingStore(tmp_path / "store"))
    await bridge.start_server()
    try:
        port = bridge._server.sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            assert json.loads(await ws.recv())["type"] == "auth_challenge"
            await ws.send(json.dumps({"type": "ide_connected", "workspace": "forged"}))
            with pytest.raises(websockets.ConnectionClosed):
                await ws.recv()
        assert not bridge.connected and not bridge.authenticated
        assert bridge.workspace is None
        assert (await bridge.get_status())["success"] is False
    finally:
        await bridge.stop_server()


async def test_invalid_peer_cannot_replace_live_session_and_revocation_stops_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "IDE_WS_PORT", 0)
    store = PairingStore(tmp_path / "store")
    bridge = module.IDEBridge(pairing_store=store)
    await bridge.start_server()
    good = await paired_client(bridge, store)
    try:
        assert bridge.authenticated
        original = bridge._ws
        port = bridge._server.sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://127.0.0.1:{port}") as bad:
            challenge = json.loads(await bad.recv())
            await bad.send(json.dumps(response(challenge, b"wrong".ljust(32, b"x"))))
            with pytest.raises(websockets.ConnectionClosed):
                await bad.recv()
        assert bridge.authenticated and bridge._ws is original
        task = asyncio.create_task(bridge.get_status())
        command = json.loads(await good.recv())
        await good.send(json.dumps(result_for(command, success=True)))
        assert (await task)["success"] is True
        await bridge.revoke_pairing()
        assert not bridge.authenticated
        assert (await bridge.send_command("write_file"))["success"] is False
        assert store.initialize() is None
    finally:
        await good.close()
        await bridge.stop_server()


async def test_rotation_disconnects_old_session_but_new_key_can_authenticate(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "IDE_WS_PORT", 0)
    store = PairingStore(tmp_path / "store")
    bridge = module.IDEBridge(pairing_store=store)
    await bridge.start_server()
    good = await paired_client(bridge, store)
    try:
        previous = store.load().key_id
        await bridge.rotate_pairing()
        assert store.load().key_id != previous
        assert not bridge.authenticated
        fresh = await paired_client(bridge, store)
        try:
            assert bridge.authenticated
        finally:
            await fresh.close()
    finally:
        await good.close()
        await bridge.stop_server()
