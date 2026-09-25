"""Real keepalive traffic and foreign-loop cleanup; no model or user profile."""

import asyncio
import json
import os
import sys

import pytest
from websockets.frames import OP_PONG

from src.tools import ide_bridge as module
from src.tools.ide_pairing_store import PairingStore
from tests.tools.test_conn2a_ide_bridge_auth import paired_client, result_for
from tests.tools.test_conn2c_ide_transport import drained


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(sys.platform != "win32", reason="Windows pairing integration")]


async def test_native_heartbeat_detects_muted_peer_and_releases_foreign_caller(tmp_path, monkeypatch):
    # Optional canary retains the production 20/20-second timers. The ordinary
    # regression uses the same real server/frames with shorter timers only.
    realtime = os.environ.get("LUMENA_IDE_HEARTBEAT_TEST_REAL") == "1"
    interval = module.HEARTBEAT_INTERVAL_MS / 1000 if realtime else 0.5
    timeout = module.HEARTBEAT_TIMEOUT_MS / 1000 if realtime else 0.5
    if not realtime:
        monkeypatch.setattr(module, "HEARTBEAT_INTERVAL_MS", interval * 1000)
        monkeypatch.setattr(module, "HEARTBEAT_TIMEOUT_MS", timeout * 1000)
    monkeypatch.setattr(module, "IDE_WS_PORT", 0)
    store = PairingStore(tmp_path / "pairing")
    bridge = module.IDEBridge(pairing_store=store)
    await bridge.start_server()
    ws = await paired_client(bridge, store)
    fresh = None
    caller = None
    pongs = asyncio.Queue()
    mute = False
    send_frame = ws.protocol.send_frame

    def controlled_peer(frame):
        if frame.opcode is OP_PONG:
            pongs.put_nowait(bytes(frame.data))
            if mute:
                return
        send_frame(frame)

    # Fault injection lives only in the test client. The server's native ping,
    # deadline, close handshake and unregister path execute without replacement.
    monkeypatch.setattr(ws.protocol, "send_frame", controlled_peer)
    try:
        for _ in range(1 if realtime else 3):
            await asyncio.wait_for(pongs.get(), interval + 5)
        assert bridge.ready
        old_session = bridge._negotiated.session_id
        mute = True
        caller = asyncio.create_task(asyncio.to_thread(
            asyncio.run, bridge.send_command("get_status", timeout=interval + timeout + 10),
        ))
        command = json.loads(await asyncio.wait_for(ws.recv(), 5))
        await asyncio.wait_for(pongs.get(), interval + 5)
        result = await asyncio.wait_for(caller, interval + timeout + 5)
        assert result == {"success": False, "error": "IDE disconnected", "outcome": "unknown",
                          "_transport": {key: value for key, value in result_for(command).items()
                                         if key not in ("type", "result")}}
        await asyncio.wait_for(ws.wait_closed(), 5)
        assert ws.close_code == 1011
        assert ws.close_reason == "keepalive ping timeout"
        await drained(bridge._dispatcher)
        assert not bridge.ready and not bridge.authenticated
        assert bridge._pending == {}

        fresh = await paired_client(bridge, store)
        assert bridge.ready and bridge._negotiated.session_id != old_session
        caller = asyncio.create_task(asyncio.to_thread(asyncio.run, bridge.send_command("get_status")))
        next_command = json.loads(await asyncio.wait_for(fresh.recv(), 5))
        await fresh.send(json.dumps(result_for(next_command, success=True, marker="fresh-session")))
        assert (await asyncio.wait_for(caller, 5))["marker"] == "fresh-session"
        await drained(bridge._dispatcher)
        assert bridge._pending == {}
    finally:
        await ws.close()
        if fresh:
            await fresh.close()
        await bridge.stop_server()
        if caller:
            if not caller.done():
                caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
