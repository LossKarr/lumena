"""Protocol proof is distinct from authentication and from tool authorization."""

from copy import deepcopy
import hashlib
import json

import pytest

from src.tools.ide_protocol import ProtocolError, canonical_catalogue, negotiate
from src.tools.ide_semantics import ide_contract


SESSION = "01" * 16
COMMAND = {
    "id": "get_status", "title": "Etat IDE", "description": "Etat reel de l'editeur",
    "category": "system", "risk": "read", "target": "hybrid", "proof": "renderer-state",
    "supported": True, "parameters": {}, **ide_contract("get_status"),
}


def hello(commands=None):
    commands = deepcopy([COMMAND] if commands is None else commands)
    catalogue_json = json.dumps(sorted(commands, key=lambda item: item["id"]),
                                sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return {
        "type": "ide_hello", "transport_version": 1, "operation_cursor": 0, "session_id": SESSION, "protocol": {"min": 3, "max": 3},
        "ide": {"version": "1.0.0", "instance_id": "02" * 16, "build_hash": "03" * 32},
        "workspace": {"id": "04" * 32, "path": "C:/workspace/example"},
        "nonce": "05" * 32,
        "catalogue": {"schema": 2, "hash": hashlib.sha256(catalogue_json.encode()).hexdigest(), "commands": commands},
    }


def test_accepts_complete_hello_and_returns_bound_acknowledgement():
    message = hello()
    session, ack = negotiate(message, SESSION)
    assert session.state == "ready" and session.protocol == 3
    assert session.catalogue_hash == message["catalogue"]["hash"]
    assert session.commands == [COMMAND]
    assert ack == {
        "type": "ide_hello_ack", "transport_version": 1, "session_id": SESSION,
        "instance_id": message["ide"]["instance_id"], "nonce": message["nonce"],
        "protocol": 3, "catalogue_hash": session.catalogue_hash,
        "catalogue_revision": session.catalogue_hash, "state": "ready", "reason": "",
        "limits": {"max_frame_bytes": 1048576, "heartbeat_interval_ms": 20000, "heartbeat_timeout_ms": 20000},
    }


# CONN-5B-2 : la version 4 est supportee, {4, 5} n'est plus incompatible.
@pytest.mark.parametrize("protocol", [{"min": 2, "max": 2}, {"min": 5, "max": 6}])
def test_rejects_unsupported_versions_without_fallback(protocol):
    message = hello()
    message["protocol"] = protocol
    with pytest.raises(ProtocolError, match="^protocol_incompatible$"):
        negotiate(message, SESSION)


def test_selects_supported_version_inside_wider_valid_range():
    message = hello()
    message["protocol"] = {"min": 2, "max": 4}
    # CONN-5B-2 : la plus haute version commune est desormais 4.
    assert negotiate(message, SESSION)[0].protocol == 4


@pytest.mark.parametrize("field,value", [
    ("session_id", "ff" * 16), ("nonce", "wrong"), ("type", "ide_connected"),
    ("protocol", {"min": True, "max": 3}), ("protocol", {"min": 4, "max": 3}),
    ("workspace", {"path": "x"}), ("catalogue", {"schema": 2, "hash": "00" * 32, "commands": []}),
    ("ide", {"version": "1.0.0", "instance_id": "02" * 16, "build_hash": "unknown"}),
])
def test_rejects_malformed_unbound_or_tampered_hellos(field, value):
    message = hello()
    message[field] = value
    with pytest.raises(ProtocolError):
        negotiate(message, SESSION)


@pytest.mark.parametrize("commands", [[], [{**COMMAND, "supported": False}], [{**COMMAND, "id": "get_state"}]])
def test_missing_status_is_degraded_not_ready(commands):
    session, ack = negotiate(hello(commands), SESSION)
    assert session.state == ack["state"] == "degraded"
    assert ack["reason"] == "status_unavailable"


def test_catalogue_is_canonical_and_snapshot_cannot_be_mutated_through_returned_values():
    data = ide_contract("editor_cursor_goto")
    schema = data["input_schema"]
    other = {**COMMAND, "id": "editor_cursor_goto", "title": "Vue caf\u00e9", **data,
             "parameters": {key: {"type": spec["type"], "description": "Position", "required": key in schema["required"]}
                            for key, spec in schema["properties"].items()}}
    message = hello([COMMAND, other])
    session, _ = negotiate(message, SESSION)
    assert canonical_catalogue([COMMAND, other]) == canonical_catalogue([other, COMMAND])
    message["catalogue"]["commands"].clear()
    session.commands[0]["title"] = "tampered"
    assert session.commands[0]["title"] == "Vue caf\u00e9"
    assert len(session.commands) == 2


@pytest.mark.parametrize("commands", [
    [COMMAND, COMMAND], [{**COMMAND, "id": "../escape"}], [{**COMMAND, "supported": 1}],
    [{**COMMAND, "risk": "unknown"}], [{**COMMAND, "extra": True}],
    [{**COMMAND, "parameters": {"x": {"type": "unknown", "description": "x"}}}],
    [{**COMMAND, "parameters": {"x": {"type": "object", "description": "x", "required": "yes"}}}],
])
def test_rejects_invalid_catalogue_descriptors(commands):
    with pytest.raises(ProtocolError):
        canonical_catalogue(commands)


def test_unknown_fields_and_oversized_inputs_are_not_silently_accepted():
    message = hello()
    message["secret"] = "not allowed"
    with pytest.raises(ProtocolError):
        negotiate(message, SESSION)
    with pytest.raises(ProtocolError):
        canonical_catalogue([{**COMMAND, "id": f"cmd_{index}"} for index in range(513)])
    with pytest.raises(ProtocolError):
        canonical_catalogue([{**COMMAND, "description": "x" * 8193}])
