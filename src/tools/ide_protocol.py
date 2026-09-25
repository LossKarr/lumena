"""Strict IDE negotiation codec; no sockets, ReAct ownership or tool authorization."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import re
from typing import Any

PROTOCOL_VERSION = 4
MIN_PROTOCOL_VERSION = 3
# CONN-5B-2 : mission_scope n'existe qu'a partir de la version 4.
MISSION_SCOPE_PROTOCOL_VERSION = 4
SUPPORTED_PROTOCOL_VERSIONS = (3, 4)
TRANSPORT_VERSION = 1
CATALOGUE_SCHEMA_VERSION = 2
MAX_FRAME_BYTES = 1_048_576
MAX_CATALOGUE_BYTES = 262_144
MAX_COMMANDS = 512
HEARTBEAT_INTERVAL_MS = 20_000
HEARTBEAT_TIMEOUT_MS = 20_000

CATEGORIES = frozenset(
    "system workspace editor terminal task debug search git review assistant settings window".split()
)
RISKS = frozenset(("read", "write", "destructive", "system"))
TARGETS = frozenset(("main", "renderer", "hybrid"))
PROOFS = frozenset(("catalogue", "filesystem", "renderer-state", "process", "git", "settings-store",
                    "history-store", "window-state"))
PARAMETER_TYPES = frozenset(("string", "number", "boolean", "array", "object"))


class ProtocolError(Exception):
    """Fixed error codes only: untrusted catalogue content must never reach logs."""


def _require(condition: bool, reason: str = "protocol_invalid") -> None:
    if not condition:
        raise ProtocolError(reason)


def _shape(value: Any, required: set[str], optional: set[str] | frozenset[str] = frozenset()) -> bool:
    return type(value) is dict and required <= value.keys() <= required | optional


def _text(value: Any, maximum: int, *, empty: bool = False) -> bool:
    return isinstance(value, str) and (empty or bool(value)) and len(value) <= maximum


def _hex(value: Any, size: int) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(size) + r"}", value) is not None


def _json(value: Any) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        encoded.encode("utf-8")
        return encoded
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ProtocolError("protocol_invalid") from None


def canonical_catalogue(commands: Any) -> str:
    """Validate first, then canonicalize; never mutate or trust peer descriptors."""
    _require(type(commands) is list and len(commands) <= MAX_COMMANDS, "catalogue_invalid")
    names = set()
    for command in commands:
        _require(_shape(command, {"id", "title", "description", "category", "risk", "target", "proof",
                                  "supported", "parameters", "semantics", "input_schema"}), "catalogue_invalid")
        name = command["id"]
        _require(isinstance(name, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,95}", name) is not None,
                 "catalogue_invalid")
        _require(name not in names, "catalogue_invalid")
        names.add(name)
        _require(_text(command["title"], 256) and _text(command["description"], 8192, empty=True), "catalogue_invalid")
        for field, allowed in (("category", CATEGORIES), ("risk", RISKS), ("target", TARGETS), ("proof", PROOFS)):
            _require(isinstance(command[field], str) and command[field] in allowed, "catalogue_invalid")
        _require(type(command["supported"]) is bool, "catalogue_invalid")
        params = command["parameters"]
        _require(type(params) is dict and len(params) <= 64, "catalogue_invalid")
        for key, spec in params.items():
            _require(isinstance(key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", key) is not None,
                     "catalogue_invalid")
            _require(_shape(spec, {"type", "description"}, {"required"}), "catalogue_invalid")
            _require(isinstance(spec["type"], str) and spec["type"] in PARAMETER_TYPES, "catalogue_invalid")
            _require(_text(spec["description"], 2048, empty=True), "catalogue_invalid")
            _require("required" not in spec or type(spec["required"]) is bool, "catalogue_invalid")
        from .ide_semantics import validate_ide_contract
        try:
            validate_ide_contract(command)
        except (ValueError, TypeError, KeyError, RecursionError):
            raise ProtocolError("catalogue_policy_invalid") from None
    encoded = _json(sorted(commands, key=lambda item: item["id"]))
    _require(len(encoded.encode("utf-8")) <= MAX_CATALOGUE_BYTES, "catalogue_too_large")
    return encoded


@dataclass(frozen=True)
class NegotiatedSession:
    session_id: str
    instance_id: str
    ide_version: str
    build_hash: str
    catalogue_hash: str
    catalogue_json: str
    workspace_id: str | None
    workspace_path: str | None
    protocol: int
    state: str
    operation_cursor: int = 0

    @property
    def commands(self) -> list[dict]:
        # Callers receive a copy, never a mutable reference to the accepted snapshot.
        return json.loads(self.catalogue_json)


def update_workspace(message: Any, session: NegotiatedSession) -> NegotiatedSession:
    _require(_shape(message, {"type", "session_id", "workspace"}))
    _require(message["type"] == "ide_workspace" and message["session_id"] == session.session_id,
             "session_invalid")
    workspace = message["workspace"]
    if workspace is not None:
        _require(_shape(workspace, {"id", "path"}))
        _require(_hex(workspace["id"], 64) and _text(workspace["path"], 4096) and "\x00" not in workspace["path"])
    _require(len(_json(message).encode("utf-8")) <= MAX_FRAME_BYTES, "frame_too_large")
    return replace(session, workspace_id=workspace["id"] if workspace else None,
                   workspace_path=workspace["path"] if workspace else None)


def negotiate(message: Any, authenticated_session_id: str) -> tuple[NegotiatedSession, dict]:
    _require(_hex(authenticated_session_id, 32), "session_invalid")
    _require(_shape(message, {"type", "session_id", "protocol", "ide", "workspace", "nonce", "catalogue",
                              "transport_version", "operation_cursor"}))
    _require(type(message["operation_cursor"]) is int and 0 <= message["operation_cursor"] <= 9_007_199_254_740_991)
    _require(type(message["transport_version"]) is int and message["transport_version"] == TRANSPORT_VERSION,
             "transport_incompatible")
    _require(message["type"] == "ide_hello" and message["session_id"] == authenticated_session_id, "session_invalid")
    _require(_hex(message["nonce"], 64))
    version = message["protocol"]
    _require(_shape(version, {"min", "max"}))
    _require(type(version["min"]) is int and type(version["max"]) is int)
    _require(1 <= version["min"] <= version["max"] <= 65535)
    # CONN-5B-2 : la plus haute version commune ; une IDE en 3 reste servie a l'identique.
    common = [candidate for candidate in SUPPORTED_PROTOCOL_VERSIONS if version["min"] <= candidate <= version["max"]]
    _require(bool(common), "protocol_incompatible")
    chosen = max(common)
    identity = message["ide"]
    _require(_shape(identity, {"version", "instance_id", "build_hash"}))
    _require(_text(identity["version"], 64) and re.fullmatch(
        r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?", identity["version"]
    ) is not None)
    _require(_hex(identity["instance_id"], 32) and _hex(identity["build_hash"], 64))
    workspace = message["workspace"]
    if workspace is not None:
        _require(_shape(workspace, {"id", "path"}))
        _require(_hex(workspace["id"], 64) and _text(workspace["path"], 4096) and "\x00" not in workspace["path"])
    catalogue = message["catalogue"]
    _require(_shape(catalogue, {"schema", "hash", "commands"}), "catalogue_invalid")
    _require(type(catalogue["schema"]) is int and catalogue["schema"] == CATALOGUE_SCHEMA_VERSION,
             "catalogue_incompatible")
    _require(_hex(catalogue["hash"], 64), "catalogue_invalid")
    encoded = canonical_catalogue(catalogue["commands"])
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    _require(digest == catalogue["hash"], "catalogue_hash_mismatch")
    _require(len(_json(message).encode("utf-8")) <= MAX_FRAME_BYTES, "frame_too_large")
    ready = any(command["id"] == "get_status" and command["supported"] for command in catalogue["commands"])
    state = "ready" if ready else "degraded"
    session = NegotiatedSession(
        authenticated_session_id, identity["instance_id"], identity["version"], identity["build_hash"],
        digest, encoded, workspace["id"] if workspace else None, workspace["path"] if workspace else None,
        chosen, state, message["operation_cursor"],
    )
    return session, {
        "type": "ide_hello_ack", "transport_version": TRANSPORT_VERSION,
        "session_id": session.session_id, "instance_id": session.instance_id,
        "nonce": message["nonce"], "protocol": chosen, "catalogue_hash": digest,
        "catalogue_revision": digest, "state": state, "reason": "" if ready else "status_unavailable",
        "limits": {"max_frame_bytes": MAX_FRAME_BYTES, "heartbeat_interval_ms": HEARTBEAT_INTERVAL_MS,
                   "heartbeat_timeout_ms": HEARTBEAT_TIMEOUT_MS},
    }
