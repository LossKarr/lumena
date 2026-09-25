"""Actual foreign agent loops, bounded admission and owner-loop cleanup."""

import asyncio
import contextvars
import json
import sys
import threading

import pytest
import pytest_asyncio

from src.tools import ide_bridge as module
from src.tools.ide_pairing_store import PairingStore
from src.tools.ide_transport import IDELoopDispatcher, TransportBusy, TransportUnavailable
from tests.tools.test_conn2a_ide_bridge_auth import paired_client, result_for


async def drained(dispatcher):
    async with asyncio.timeout(2):
        while dispatcher.pending_count:
            await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_dispatch_preserves_context_and_runs_only_on_owner():
    dispatcher = IDELoopDispatcher()
    dispatcher.bind()
    owner = asyncio.get_running_loop()
    context = contextvars.ContextVar("mission", default="owner")

    async def work():
        assert asyncio.get_running_loop() is owner
        return context.get(), threading.get_ident()

    async def agent():
        context.set("mission-17/conversation-3")
        assert asyncio.get_running_loop() is not owner
        return await dispatcher.dispatch(work, 2)

    try:
        value, thread = await asyncio.to_thread(asyncio.run, agent())
        assert value == "mission-17/conversation-3"
        assert thread == threading.get_ident()
        assert context.get() == "owner"
        await drained(dispatcher)
    finally:
        await dispatcher.close()


@pytest.mark.asyncio
async def test_admission_is_bounded_and_cancellation_releases_after_owner_cleanup():
    dispatcher = IDELoopDispatcher(limit=1)
    dispatcher.bind()
    entered = asyncio.Event()
    stopped = asyncio.Event()

    async def work():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    first = asyncio.create_task(dispatcher.dispatch(work, 2))
    await entered.wait()
    with pytest.raises(TransportBusy):
        await dispatcher.dispatch(work, 2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await drained(dispatcher)
    assert stopped.is_set()
    assert await dispatcher.dispatch(lambda: asyncio.sleep(0, result="next"), 2) == "next"
    await dispatcher.close()


@pytest.mark.asyncio
async def test_owner_timeout_is_published_only_after_pending_work_is_cleaned():
    dispatcher = IDELoopDispatcher(limit=1)
    dispatcher.bind()
    cleaning = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleaned = asyncio.Event()

    async def work():
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release_cleanup.wait()
            cleaned.set()

    caller = asyncio.create_task(dispatcher.dispatch(work, 0.02))
    try:
        await asyncio.wait_for(cleaning.wait(), 1)
        assert not caller.done()
        assert dispatcher.pending_count == 1
        release_cleanup.set()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(caller, 1)
        assert cleaned.is_set()
        assert dispatcher.pending_count == 0
        assert await dispatcher.dispatch(lambda: asyncio.sleep(0, result="next"), 1) == "next"
    finally:
        release_cleanup.set()
        await dispatcher.close()
        await asyncio.gather(caller, return_exceptions=True)


@pytest.mark.asyncio
async def test_stop_drains_waiters_and_restart_binds_new_work():
    dispatcher = IDELoopDispatcher()
    with pytest.raises(TransportUnavailable):
        await dispatcher.dispatch(lambda: asyncio.sleep(0), 1)
    dispatcher.bind()
    entered = asyncio.Event()

    async def work():
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(dispatcher.dispatch(work, 5))
    await entered.wait()
    await dispatcher.close()
    with pytest.raises(TransportUnavailable):
        await task
    await drained(dispatcher)
    dispatcher.bind()
    assert await dispatcher.dispatch(lambda: asyncio.sleep(0, result=4), 1) == 4
    await dispatcher.close()


@pytest.mark.asyncio
async def test_cancel_before_dispatch_never_calls_factory():
    dispatcher = IDELoopDispatcher()
    dispatcher.bind()
    calls = []

    async def work():
        calls.append("effect")

    # Zero remaining deadline is rejected before creating the owner task.
    with pytest.raises(TimeoutError):
        await dispatcher.dispatch(work, 0)
    await drained(dispatcher)
    assert calls == []
    await dispatcher.close()


@pytest.mark.asyncio
async def test_work_exception_is_propagated_not_replaced_with_success():
    dispatcher = IDELoopDispatcher()
    dispatcher.bind()

    async def work():
        raise ValueError("test failure")

    try:
        with pytest.raises(ValueError, match="test failure"):
            await asyncio.to_thread(asyncio.run, dispatcher.dispatch(work, 2))
        await drained(dispatcher)
    finally:
        await dispatcher.close()


@pytest_asyncio.fixture
async def connection(tmp_path, monkeypatch):
    if sys.platform != "win32":
        pytest.skip("real Windows pairing integration")
    monkeypatch.setattr(module, "IDE_WS_PORT", 0)
    store = PairingStore(tmp_path / "pairing")
    bridge = module.IDEBridge(pairing_store=store)
    loop = asyncio.get_running_loop()
    was_debug = loop.get_debug()
    loop.set_debug(True)
    await bridge.start_server()
    ws = await paired_client(bridge, store)
    try:
        yield bridge, ws
    finally:
        await ws.close()
        await bridge.stop_server()
        loop.set_debug(was_debug)


@pytest.mark.asyncio
@pytest.mark.parametrize("pin_snapshot", [False, True])
async def test_real_socket_foreign_loop_out_of_order_results_keep_correlation(connection, monkeypatch, pin_snapshot):
    bridge, ws = connection
    owner = asyncio.get_running_loop()
    context = contextvars.ContextVar("run", default="server")
    observed = []
    original = bridge._send_command_on_owner
    expected = bridge.catalogue_snapshot() if pin_snapshot else None

    async def checked(action, params, session_id, expires_at, resume, attempt, expected_snapshot=None):
        assert asyncio.get_running_loop() is owner
        assert expected_snapshot is expected
        observed.append(context.get())
        return await original(action, params, session_id, expires_at, resume, attempt, expected_snapshot)

    monkeypatch.setattr(bridge, "_send_command_on_owner", checked)

    async def agent():
        context.set("worker/conversation")
        return await asyncio.gather(*[
            bridge.send_command("get_status", {"index": index}, expected_snapshot=expected) for index in range(4)
        ])

    caller = asyncio.create_task(asyncio.to_thread(asyncio.run, agent()))
    commands = [json.loads(await asyncio.wait_for(ws.recv(), 3)) for _ in range(4)]
    assert len({item["request_id"] for item in commands}) == 4
    assert all(future.get_loop() is owner for future in bridge._pending.values())
    for command in reversed(commands):
        await ws.send(json.dumps(result_for(command, success=True, index=command["params"]["index"])))
    results = await asyncio.wait_for(caller, 3)
    assert [result["index"] for result in results] == list(range(4))
    assert observed == ["worker/conversation"] * 4
    await drained(bridge._dispatcher)
    assert bridge._pending == {}


@pytest.mark.asyncio
async def test_foreign_loop_timeout_drops_pending_and_late_result(connection):
    bridge, ws = connection
    caller = asyncio.create_task(asyncio.to_thread(
        asyncio.run, bridge.send_command("get_status", timeout=0.1),
    ))
    command = json.loads(await ws.recv())
    result = await caller
    assert result == {"success": False, "error": "IDE command timed out", "outcome": "unknown",
                      "_transport": {key: value for key, value in result_for(command).items()
                                     if key not in ("type", "result")}}
    await drained(bridge._dispatcher)
    assert bridge._pending == {}
    await ws.send(json.dumps(result_for(command, success=True)))
    await asyncio.sleep(0.01)
    assert bridge.ready and bridge._pending == {}


@pytest.mark.asyncio
async def test_foreign_caller_loop_shutdown_cancels_wait_without_claiming_remote_cancel(connection):
    bridge, ws = connection
    received = threading.Event()

    async def agent():
        asyncio.create_task(bridge.send_command("get_status"))
        await asyncio.to_thread(received.wait, 2)
        # asyncio.run closes this agent loop, cancelling its remaining task.

    caller = asyncio.create_task(asyncio.to_thread(asyncio.run, agent()))
    await ws.recv()
    received.set()
    await caller
    await drained(bridge._dispatcher)
    assert bridge._pending == {}
    assert bridge.ready


@pytest.mark.asyncio
async def test_disconnect_wakes_foreign_caller_without_retry(connection):
    bridge, ws = connection
    caller = asyncio.create_task(asyncio.to_thread(asyncio.run, bridge.send_command("get_status")))
    command = json.loads(await ws.recv())
    await ws.close()
    result = await asyncio.wait_for(caller, 2)
    assert result == {"success": False, "error": "IDE disconnected", "outcome": "unknown",
                      "_transport": {key: value for key, value in result_for(command).items()
                                     if key not in ("type", "result")}}
    await drained(bridge._dispatcher)
    assert bridge._pending == {} and not bridge.ready


@pytest.mark.asyncio
async def test_real_socket_backpressure_refuses_extra_command_before_send(connection):
    bridge, ws = connection
    await bridge._dispatcher.close()
    bridge._dispatcher = IDELoopDispatcher(limit=1)
    bridge._dispatcher.bind()
    caller = asyncio.create_task(bridge.send_command("get_status"))
    command = json.loads(await ws.recv())
    result = await asyncio.to_thread(asyncio.run, bridge.send_command("get_status"))
    assert result == {"success": False, "error": "IDE transport busy", "outcome": "not_sent"}
    assert len(bridge._pending) == 1
    await ws.send(json.dumps(result_for(command, success=True)))
    assert (await caller)["success"]


@pytest.mark.asyncio
async def test_server_shutdown_does_not_claim_already_sent_action_was_not_sent(connection):
    bridge, ws = connection
    caller = asyncio.create_task(asyncio.to_thread(asyncio.run, bridge.send_command("get_status")))
    command = json.loads(await ws.recv())
    await bridge.stop_server()
    result = await caller
    assert result == {"success": False, "error": "IDE transport stopped", "outcome": "unknown",
                      "_transport": {key: value for key, value in result_for(command).items()
                                     if key not in ("type", "result")}}
    await drained(bridge._dispatcher)
    assert bridge._pending == {}


@pytest.mark.asyncio
async def test_queued_command_cannot_switch_to_a_replacement_session(connection, monkeypatch):
    bridge, ws = connection
    original = bridge._dispatcher.dispatch

    async def replace_before_dispatch(factory, timeout):
        from dataclasses import replace
        bridge._negotiated = replace(bridge._negotiated, session_id="aa" * 16)
        return await original(factory, timeout)

    monkeypatch.setattr(bridge._dispatcher, "dispatch", replace_before_dispatch)
    result = await bridge.send_command("get_status")
    assert result == {"success": False, "error": "IDE session changed", "outcome": "not_sent"}
    assert bridge._pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [0, -1, 301, float("inf"), float("nan"), True, "30"])
async def test_invalid_timeout_cannot_enqueue(timeout):
    bridge = module.IDEBridge()
    result = await bridge.send_command("get_status", timeout=timeout)
    assert result == {"success": False, "error": "Invalid IDE command timeout"}
    assert bridge._dispatcher.pending_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [[], "text", {"bad": float("nan")}, {"bad": object()}])
async def test_non_json_parameters_cannot_enqueue(params):
    bridge = module.IDEBridge()
    result = await bridge.send_command("get_status", params)
    assert result == {"success": False, "error": "Invalid IDE command parameters"}
    assert bridge._dispatcher.pending_count == 0
