"""Correlated command envelopes. Transport provenance is not business proof."""

from __future__ import annotations

import json
import math
import re
from typing import Any

from .ide_protocol import (
    MAX_FRAME_BYTES, MISSION_SCOPE_PROTOCOL_VERSION, TRANSPORT_VERSION, NegotiatedSession, ProtocolError,
)

BINDING_FIELDS = (
    "transport_version", "session_id", "instance_id", "catalogue_revision",
    "request_id", "operation_id", "sequence", "workspace_id",
)
MAX_SAFE_INTEGER = 9_007_199_254_740_991
MAX_MISSION_FILES = 256
_MISSION_SCOPE_FIELDS = frozenset({"task_id", "role", "allowed_files", "target"})
_MISSION_EXECUTION_FIELDS = frozenset({"task_id", "role", "allowed_files", "execution"})


def _require(value: bool, code: str = "command_invalid") -> None:
    if not value:
        raise ProtocolError(code)


def validate_json(value: Any, depth: int = 0) -> None:
    _require(depth <= 64)
    if value is None or type(value) is bool:
        return
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ProtocolError("command_invalid") from None
    elif type(value) is int:
        _require(abs(value) <= MAX_SAFE_INTEGER)
    elif type(value) is float:
        _require(math.isfinite(value) and abs(value) <= MAX_SAFE_INTEGER)
    elif type(value) is list:
        for item in value:
            validate_json(item, depth + 1)
    elif type(value) is dict:
        for key, item in value.items():
            _require(isinstance(key, str))
            validate_json(key, depth + 1)
            validate_json(item, depth + 1)
    else:
        raise ProtocolError("command_invalid")


def _relative_posix(value: Any) -> bool:
    """Chemin relatif au dossier de mission, en separateurs posix, sans evasion."""
    if not isinstance(value, str) or not value or len(value) > 4096 or "\x00" in value or "\\" in value:
        return False
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return False
    return all(part not in ("", ".", "..") for part in value.split("/"))


def mission_scope_copy(scope: Any) -> dict:
    """CONN-5B-2 : forme fermee du perimetre que l'IDE revalide avant effet.

    CONN-5C-1 : exactement une des deux formes, `target` (ecriture) ou `execution`
    (tache ou test, avec l'empreinte de la commande validee pour une tache).
    """
    _require(type(scope) is dict and set(scope) in (_MISSION_SCOPE_FIELDS, _MISSION_EXECUTION_FIELDS))
    _require(isinstance(scope["task_id"], str)
             and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", scope["task_id"]) is not None)
    _require(scope["role"] in ("lead", "worker"))
    files = scope["allowed_files"]
    _require(type(files) is list and len(files) <= MAX_MISSION_FILES and all(_relative_posix(f) for f in files))
    copy = {"task_id": scope["task_id"], "role": scope["role"], "allowed_files": list(files)}
    if "target" in scope:
        _require(_relative_posix(scope["target"]))
        return {**copy, "target": scope["target"]}
    execution = scope["execution"]
    _require(type(execution) is dict and set(execution) == {"kind", "id", "command_sha256"})
    _require(execution["kind"] in ("task", "test", "command"))
    _require(type(execution["id"]) is str and 1 <= len(execution["id"]) <= 512 and "\x00" not in execution["id"])
    # CONN-5C-2 : une commande validee n'a qu'une identite, la tache synthetique `command`.
    _require(execution["kind"] != "command" or execution["id"] == "command")
    digest = execution["command_sha256"]
    if execution["kind"] in ("task", "command"):
        _require(type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest) is not None)
    else:
        _require(digest is None)
    return {**copy, "execution": {"kind": execution["kind"], "id": execution["id"], "command_sha256": digest}}


def encode_frame(message: dict) -> str:
    validate_json(message)
    encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    _require(len(encoded.encode("utf-8")) <= MAX_FRAME_BYTES, "frame_too_large")
    return encoded


def command_frame(
    session: NegotiatedSession, action: str, params: dict, *, request_id: str,
    operation_id: str, sequence: int, expires_at: int,
    mode: str = "execute", mission_scope: dict | None = None,
) -> dict:
    _require(mode in ("execute", "resume"))
    _require(isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id) is not None)
    _require(isinstance(operation_id, str) and re.fullmatch(r"[0-9a-f]{32}", operation_id) is not None)
    _require(type(sequence) is int and 1 <= sequence <= MAX_SAFE_INTEGER)
    _require(type(expires_at) is int and 1 <= expires_at <= MAX_SAFE_INTEGER)
    _require(isinstance(action, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,95}", action) is not None)
    _require(type(params) is dict)
    message = {
        "type": "command", "transport_version": TRANSPORT_VERSION,
        "session_id": session.session_id, "instance_id": session.instance_id,
        "catalogue_revision": session.catalogue_hash, "workspace_id": session.workspace_id,
        "request_id": request_id, "operation_id": operation_id, "sequence": sequence,
        "expires_at": expires_at, "action": action, "params": params, "mode": mode,
    }
    if mission_scope is not None:
        _require(session.protocol >= MISSION_SCOPE_PROTOCOL_VERSION, "mission_scope_unsupported")
        message["mission_scope"] = mission_scope_copy(mission_scope)
    encode_frame(message)
    return message


def accept_result(message: Any, command: dict) -> dict:
    _require(type(message) is dict and set(message) == {"type", "result", *BINDING_FIELDS}, "result_invalid")
    _require(message["type"] == "result", "result_invalid")
    for field in BINDING_FIELDS:
        _require(type(message[field]) is type(command[field]) and message[field] == command[field],
                 "result_uncorrelated")
    result = message["result"]
    _require(type(result) is dict and type(result.get("success")) is bool, "result_invalid")
    _require("request_id" not in result or result["request_id"] == command["request_id"], "result_uncorrelated")
    _require("_transport" not in result, "result_invalid")
    encode_frame(message)
    return {**result, "_transport": {key: command[key] for key in BINDING_FIELDS}}
