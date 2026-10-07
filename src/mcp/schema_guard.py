"""Bounded MCP tool-schema validation and supply-chain drift detection."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from filelock import FileLock

from src.utils.persistence import atomic_write_json, safe_read_json


MAX_SCHEMA_DEPTH = 24
MAX_SCHEMA_NODES = 4096
MAX_SCHEMA_BYTES = 512 * 1024
MAX_TOOLS = 2000
_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class MCPSchemaError(ValueError):
    pass


@dataclass(frozen=True)
class SchemaAssessment:
    accepted: bool
    status: str
    fingerprint: str
    added_tools: Tuple[str, ...] = ()
    removed_tools: Tuple[str, ...] = ()
    changed_tools: Tuple[str, ...] = ()


def _walk_schema(value: Any, *, depth: int = 0, counter: Optional[list] = None) -> None:
    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > MAX_SCHEMA_NODES:
        raise MCPSchemaError("schema_node_limit")
    if depth > MAX_SCHEMA_DEPTH:
        raise MCPSchemaError("schema_depth_limit")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 512:
                raise MCPSchemaError("schema_key_invalid")
            if key == "$ref" and isinstance(child, str) and child.startswith(
                ("http://", "https://")
            ):
                raise MCPSchemaError("schema_remote_ref_forbidden")
            _walk_schema(child, depth=depth + 1, counter=counter)
    elif isinstance(value, list):
        for child in value:
            _walk_schema(child, depth=depth + 1, counter=counter)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise MCPSchemaError("schema_value_invalid")


def canonical_schema(schema: Any) -> bytes:
    if not isinstance(schema, dict):
        raise MCPSchemaError("schema_not_object")
    _walk_schema(schema)
    try:
        wire = json.dumps(
            schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MCPSchemaError("schema_not_json") from exc
    if len(wire) > MAX_SCHEMA_BYTES:
        raise MCPSchemaError("schema_byte_limit")
    return wire


def schema_fingerprint(schema: Any) -> str:
    return hashlib.sha256(canonical_schema(schema)).hexdigest()


class MCPSchemaGuard:
    def __init__(self, root: Path):
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def assess(
        self,
        server_id: str,
        tools: Iterable[Any],
        *,
        accept_current: bool = False,
    ) -> SchemaAssessment:
        if not isinstance(server_id, str) or not _SERVER_ID_RE.fullmatch(server_id):
            raise MCPSchemaError("server_id_invalid")
        tool_list = list(tools)
        if len(tool_list) > MAX_TOOLS:
            raise MCPSchemaError("tool_limit")
        current: Dict[str, str] = {}
        for tool in tool_list:
            name = getattr(tool, "name", None)
            schema = getattr(tool, "input_schema", None)
            if not isinstance(name, str) or not name or name in current:
                raise MCPSchemaError("tool_name_invalid_or_duplicate")
            current[name] = schema_fingerprint(schema if schema is not None else {})
        fingerprint = hashlib.sha256(json.dumps(
            current, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        path = self._root / f"{server_id}.json"
        lock = FileLock(str(path) + ".lock", timeout=10)
        with lock:
            saved = safe_read_json(path, default=None)
            if not isinstance(saved, dict) or saved.get("schema_version") != 1:
                self._write(path, server_id, current, fingerprint)
                return SchemaAssessment(True, "baseline_created", fingerprint)
            previous = saved.get("tools")
            if not isinstance(previous, dict) or not all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in previous.items()
            ):
                raise MCPSchemaError("schema_baseline_corrupted")
            added = tuple(sorted(set(current) - set(previous)))
            removed = tuple(sorted(set(previous) - set(current)))
            changed = tuple(sorted(
                name for name in set(current) & set(previous)
                if current[name] != previous[name]
            ))
            if not (added or removed or changed):
                return SchemaAssessment(True, "unchanged", fingerprint)
            if accept_current:
                self._write(path, server_id, current, fingerprint)
                self._pending_path(server_id).unlink(missing_ok=True)
                return SchemaAssessment(
                    True, "drift_accepted", fingerprint, added, removed, changed
                )
            atomic_write_json(self._pending_path(server_id), {
                "schema_version": 1,
                "server_id": server_id,
                "fingerprint": fingerprint,
                "tools": dict(sorted(current.items())),
                "added_tools": list(added),
                "removed_tools": list(removed),
                "changed_tools": list(changed),
            })
            return SchemaAssessment(
                False, "drift_requires_approval", fingerprint,
                added, removed, changed,
            )

    def accept_pending(self, server_id: str, fingerprint: str) -> SchemaAssessment:
        if not isinstance(server_id, str) or not _SERVER_ID_RE.fullmatch(server_id):
            raise MCPSchemaError("server_id_invalid")
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise MCPSchemaError("fingerprint_invalid")
        pending_path = self._pending_path(server_id)
        lock = FileLock(str(self._root / f"{server_id}.json") + ".lock", timeout=10)
        with lock:
            pending = safe_read_json(pending_path, default=None)
            if not isinstance(pending, dict) or pending.get("schema_version") != 1:
                raise MCPSchemaError("pending_schema_missing")
            if pending.get("fingerprint") != fingerprint:
                raise MCPSchemaError("pending_fingerprint_mismatch")
            tools = pending.get("tools")
            if not isinstance(tools, dict) or not all(
                isinstance(k, str) and isinstance(v, str)
                for k, v in tools.items()
            ):
                raise MCPSchemaError("pending_schema_corrupted")
            self._write(
                self._root / f"{server_id}.json", server_id, tools, fingerprint
            )
            pending_path.unlink(missing_ok=True)
            return SchemaAssessment(
                True,
                "drift_accepted",
                fingerprint,
                tuple(pending.get("added_tools", ())),
                tuple(pending.get("removed_tools", ())),
                tuple(pending.get("changed_tools", ())),
            )

    def _pending_path(self, server_id: str) -> Path:
        return self._root / f"{server_id}.pending.json"

    @staticmethod
    def _write(path: Path, server_id: str, tools: Mapping[str, str], fingerprint: str) -> None:
        atomic_write_json(path, {
            "schema_version": 1,
            "server_id": server_id,
            "fingerprint": fingerprint,
            "tools": dict(sorted(tools.items())),
        })


__all__ = [
    "MCPSchemaError", "MCPSchemaGuard", "SchemaAssessment",
    "canonical_schema", "schema_fingerprint",
]
