import json
import time

import pytest
import websockets

from src.tools.ide_command_protocol import accept_result, command_frame, encode_frame
from src.tools.ide_protocol import ProtocolError, negotiate
from tests.tools.test_conn2a_ide_bridge_auth import result_for
from tests.tools.test_conn2b_ide_protocol import SESSION, hello
from tests.tools.test_conn2c_ide_transport import connection as _connection_fixture

connection = _connection_fixture


def command():
    session, _ = negotiate(hello(), SESSION)
    return command_frame(session, "get_status", {"text": "Cafe\u0301", "n": 1.5},
                         request_id="request-1", operation_id="ab" * 16, sequence=1,
                         expires_at=int(time.time() * 1000) + 10_000)


def test_result_preserves_body_and_adds_bound_provenance_not_a_success_claim():
    request = command()
    result = accept_result(result_for(request, success=False, error="native failure"), request)
    assert result["success"] is False and result["error"] == "native failure"
    assert result["_transport"]["operation_id"] == "ab" * 16
    assert result["_transport"]["request_id"] == "request-1"
    assert result["_transport"]["workspace_id"] == hello()["workspace"]["id"]
    assert json.loads(encode_frame(request)) == request


@pytest.mark.parametrize("field,value", [
    ("transport_version", True), ("session_id", "ff" * 16), ("instance_id", "ff" * 16),
    ("catalogue_revision", "ff" * 32), ("request_id", "request-2"), ("operation_id", "ff" * 16),
    ("sequence", 2), ("sequence", True), ("workspace_id", None),
])
def test_result_requires_every_correlation_field(field, value):
    request = command()
    response = result_for(request, success=True)
    response[field] = value
    with pytest.raises(ProtocolError, match="result_uncorrelated"):
        accept_result(response, request)


@pytest.mark.parametrize("body", [{}, {"success": "true"}, {"success": 1},
                                   {"success": True, "request_id": "forged"},
                                   {"success": True, "_transport": {"proof": True}}])
def test_malformed_business_result_never_resolves_as_success(body):
    request = command()
    with pytest.raises(ProtocolError):
        accept_result(result_for(request, **body), request)


def test_legacy_result_without_envelope_is_refused():
    with pytest.raises(ProtocolError, match="result_invalid"):
        accept_result({"type": "result", "request_id": "request-1", "success": True}, command())


@pytest.mark.parametrize("version", [None, 2, True, "1"])
def test_transport_version_is_negotiated_before_commands(version):
    message = hello()
    if version is None:
        message.pop("transport_version")
    else:
        message["transport_version"] = version
    with pytest.raises(ProtocolError):
        negotiate(message, SESSION)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 2**53, "\ud800"])
def test_lossy_or_non_json_values_are_refused(value):
    request = command()
    request["params"] = {"value": value}
    with pytest.raises(ProtocolError):
        encode_frame(request)


def test_depth_and_byte_limits_are_real_bounds():
    request = command()
    nested = []
    for _ in range(65):
        nested = [nested]
    request["params"] = {"nested": nested}
    with pytest.raises(ProtocolError):
        encode_frame(request)
    request["params"] = {"text": "x" * 1_048_576}
    with pytest.raises(ProtocolError, match="frame_too_large"):
        encode_frame(request)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["session_id", "instance_id", "catalogue_revision", "operation_id", "sequence"])
async def test_real_socket_tampered_result_disconnects_instead_of_completing(connection, field):
    import asyncio
    bridge, socket = connection
    caller = asyncio.create_task(bridge.send_command("get_status"))
    request = json.loads(await socket.recv())
    response = result_for(request, success=True)
    response[field] = 2 if field == "sequence" else "f" * len(response[field])
    await socket.send(json.dumps(response))
    with pytest.raises(websockets.ConnectionClosed):
        await socket.recv()
    result = await caller
    assert result["success"] is False and result["outcome"] == "unknown"
    assert bridge.protocol_status == {"state": "degraded", "reason": "result_uncorrelated"}
    assert bridge._pending == {} and bridge._commands == {}


@pytest.mark.asyncio
async def test_a_result_for_one_pending_request_cannot_complete_another(connection):
    import asyncio
    bridge, socket = connection
    first = asyncio.create_task(bridge.send_command("get_status"))
    second = asyncio.create_task(bridge.send_command("get_status"))
    requests = [json.loads(await socket.recv()), json.loads(await socket.recv())]
    assert [request["sequence"] for request in requests] == [1, 2]
    response = result_for(requests[0], success=True)
    response["request_id"] = requests[1]["request_id"]
    await socket.send(json.dumps(response))
    results = await asyncio.gather(first, second)
    assert all(result["success"] is False for result in results)
    assert bridge._commands == {}
