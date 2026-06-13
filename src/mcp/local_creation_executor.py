"""Execute approved local MCP creation tickets.

This is the bridge for `mcp_local_create:*` approval tickets. It materializes a
safe creation request on disk, creates a minimal local MCP package, and declares
a `local:<server_id>` entry in the MCP catalog so the request becomes
installable and trackable.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Protocol

from src.mcp.approval_queue import ApprovalDecision
from src.mcp.local_package import LocalMCPPackageError, build_local_mcp_package
from src.mcp.server_catalog import ServerStatus
from src.utils.paths import DATA_DIR
from src.utils.persistence import atomic_write_json


_DEFAULT_DIRNAME = "mcp_local_creation"
_REQUESTS_SUBDIR = "requests"
_AUDIT_FILENAME = "audit.jsonl"
_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
_WINDOWS_RESERVED_NAMES = frozenset({
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5",
    "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
    "lpt6", "lpt7", "lpt8", "lpt9",
})
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_INTENT_MIN = 10
_INTENT_MAX = 512


class LocalCreationExecutorError(Exception):
    """Short-code error for approved local creation execution failures."""


class LocalCreationCatalogLike(Protocol):
    def get_server(self, server_id: str) -> Any: ...
    def add_server(
        self,
        *,
        server_id: str,
        display_name: str,
        package_spec: str,
        owner_profile: str,
        version: Optional[str] = None,
        trust_score: Optional[int] = None,
        notes: Optional[str] = None,
    ) -> Any: ...


@dataclass(frozen=True)
class LocalCreationExecutionResult:
    server_id: str
    success: bool
    created_request_path_relative: Optional[str]
    created_package_path_relative: Optional[str]
    catalog_status: Optional[str]
    reason: str
    dry_run: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_valid_server_id(server_id: Any) -> bool:
    if not isinstance(server_id, str) or not _SERVER_ID_RE.match(server_id):
        return False
    if ".." in server_id or "/" in server_id or "\\" in server_id:
        return False
    stem = server_id.split(".", 1)[0]
    return stem not in _WINDOWS_RESERVED_NAMES


def _clean_intent(raw: Any) -> str:
    if not isinstance(raw, str):
        raise LocalCreationExecutorError("intent_invalid")
    text = _CONTROL_RE.sub("", raw).strip()
    text = " ".join(text.split())
    if len(text) < _INTENT_MIN or len(text) > _INTENT_MAX:
        raise LocalCreationExecutorError("intent_invalid")
    return text


def _intent_hash(intent: str) -> str:
    return hashlib.sha256(intent.encode("utf-8")).hexdigest()


class MCPLocalCreationExecutor:
    """Materialize approved local-create tickets into a tracked catalog entry."""

    def __init__(
        self,
        *,
        catalog: LocalCreationCatalogLike,
        root_dir: Optional[Path] = None,
    ) -> None:
        if catalog is None or not hasattr(catalog, "get_server") or not hasattr(catalog, "add_server"):
            raise ValueError("catalog must expose get_server and add_server")
        self._catalog = catalog
        self._root_dir = root_dir or (DATA_DIR / _DEFAULT_DIRNAME)
        self._requests_dir = self._root_dir / _REQUESTS_SUBDIR
        self._audit_log_path = self._root_dir / _AUDIT_FILENAME

    @property
    def root_dir(self) -> Path:
        return self._root_dir

    def execute_approved_local_creation(
        self,
        approval_result: Any,
        *,
        server_id: str,
        dry_run: bool = True,
    ) -> LocalCreationExecutionResult:
        if not _is_valid_server_id(server_id):
            return self._result(server_id="", success=False, reason="server_id_invalid")

        args = self._validate_approval_result(approval_result, server_id)
        if isinstance(args, LocalCreationExecutionResult):
            return args

        intent = _clean_intent(args.get("intent"))
        expected_hash = args.get("intent_hash")
        if not isinstance(expected_hash, str) or expected_hash != _intent_hash(intent):
            return self._result(server_id=server_id, success=False, reason="intent_hash_mismatch")

        existing = self._safe_get(server_id)
        if existing is not None:
            status = getattr(getattr(existing, "status", None), "value", None)
            if status is None:
                status = str(getattr(existing, "status", ""))
            return self._result(
                server_id=server_id,
                success=True,
                reason="already_declared",
                catalog_status=status or None,
                dry_run=dry_run,
            )

        rel = f"{_REQUESTS_SUBDIR}/{server_id}.json"
        if dry_run:
            return self._result(
                server_id=server_id,
                success=False,
                reason="dry_run",
                created_request_path_relative=rel,
                created_package_path_relative=f"packages/{server_id}",
                catalog_status=ServerStatus.DECLARED.value,
                dry_run=True,
            )

        request = {
            "server_id": server_id,
            "intent": intent,
            "intent_hash": expected_hash,
            "status": "pending_local_implementation",
            "created_at": _now_iso(),
            "source": "approval_queue:mcp_local_create",
            "next_steps": [
                "implement_mcp_server",
                "package_as_npm_or_uv_server",
                "declare_installable_package_spec",
                "install_and_activate",
            ],
        }
        self._requests_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._requests_dir / f"{server_id}.json", request)
        try:
            package_info = build_local_mcp_package(
                server_id=server_id,
                intent=intent,
                intent_hash=expected_hash,
                root_dir=self._root_dir,
            )
        except LocalMCPPackageError:
            self._append_audit("local_create_failed", server_id=server_id, reason="package_build_failed")
            return self._result(server_id=server_id, success=False, reason="package_build_failed")

        try:
            self._catalog.add_server(
                server_id=server_id,
                display_name=f"Local MCP {server_id}",
                package_spec=f"local:{server_id}",
                owner_profile="lumena",
                version=None,
                trust_score=70,
                notes="local_creation_built",
            )
        except Exception:
            self._append_audit("local_create_failed", server_id=server_id, reason="catalog_add_failed")
            return self._result(server_id=server_id, success=False, reason="catalog_add_failed")

        self._append_audit("local_create_materialized", server_id=server_id, reason="declared_built")
        return self._result(
            server_id=server_id,
            success=True,
            reason="declared_built",
            created_request_path_relative=rel,
            created_package_path_relative=package_info.relative_package_path,
            catalog_status=ServerStatus.DECLARED.value,
        )

    def _validate_approval_result(
        self, approval_result: Any, server_id: str
    ) -> Dict[str, Any] | LocalCreationExecutionResult:
        decision = getattr(approval_result, "decision", None)
        decision_value = getattr(decision, "value", decision)
        if str(decision_value).lower() != ApprovalDecision.APPROVED.value:
            return self._result(server_id=server_id, success=False, reason="approval_not_granted")
        args = getattr(approval_result, "args", None)
        if not isinstance(args, dict):
            return self._result(server_id=server_id, success=False, reason="approval_args_invalid")
        if args.get("action") != "local_create":
            return self._result(server_id=server_id, success=False, reason="approval_action_mismatch")
        if args.get("server_id") != server_id:
            return self._result(server_id=server_id, success=False, reason="approval_server_id_mismatch")
        return args

    def _safe_get(self, server_id: str) -> Any:
        try:
            return self._catalog.get_server(server_id)
        except Exception:
            return None

    def _result(
        self,
        *,
        server_id: str,
        success: bool,
        reason: str,
        created_request_path_relative: Optional[str] = None,
        created_package_path_relative: Optional[str] = None,
        catalog_status: Optional[str] = None,
        dry_run: bool = False,
    ) -> LocalCreationExecutionResult:
        return LocalCreationExecutionResult(
            server_id=server_id,
            success=success,
            created_request_path_relative=created_request_path_relative,
            created_package_path_relative=created_package_path_relative,
            catalog_status=catalog_status,
            reason=reason,
            dry_run=dry_run,
        )

    def _append_audit(self, event: str, *, server_id: str, reason: str) -> None:
        record = {
            "ts": _now_iso(),
            "event": event,
            "server_id": server_id,
            "reason": reason,
        }
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            return


__all__ = [
    "LocalCreationCatalogLike",
    "LocalCreationExecutionResult",
    "LocalCreationExecutorError",
    "MCPLocalCreationExecutor",
]
