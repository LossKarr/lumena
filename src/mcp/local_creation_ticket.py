"""Phase 26.1 - Local MCP creation approval tickets.

Small adapter for the last missing conversation path: when the MCP loop
concludes that no known package can satisfy an intent and a local MCP server
must be created, this module creates a real ApprovalQueue pending ticket.

It does not create files, install packages, activate servers, or execute an
approved ticket. It only persists the human approval request.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Protocol

from src.mcp.policy import MCPPolicy


LOCAL_CREATE_TOOL_PREFIX = "mcp_local_create:"
LOCAL_CREATE_RISK_SUMMARY = "local_creation_required"

_INTENT_MIN = 10
_INTENT_MAX = 512
_SLUG_MAX = 48
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_CALLER_WHITELIST = frozenset({
    "react", "admin_ui", "autonomous_loop", "test",
})


class LocalCreationApprovalQueueLike(Protocol):
    def propose(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        policy: MCPPolicy,
        caller_kind: str,
        risk_summary: str,
        ttl_s: Optional[float] = None,
    ) -> str: ...


class LocalCreationTicketError(Exception):
    """Short-code error for local MCP creation ticket proposal failures."""


@dataclass(frozen=True)
class LocalCreationTicketProposal:
    approval_ticket_id: Optional[str]
    suggested_server_id: str
    tool_name: str
    risk_summary: str
    dry_run: bool


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_intent(raw: Any) -> str:
    if not isinstance(raw, str):
        raise LocalCreationTicketError("intent_invalid")
    text = unicodedata.normalize("NFC", raw)
    if _CONTROL_RE.search(text):
        raise LocalCreationTicketError("intent_invalid")
    text = " ".join(text.strip().split())
    if len(text) < _INTENT_MIN or len(text) > _INTENT_MAX:
        raise LocalCreationTicketError("intent_invalid")
    return text


def _slug_from_intent(intent: str) -> str:
    normalized = unicodedata.normalize("NFKD", intent).lower()
    ascii_text = "".join(
        ch for ch in normalized if not unicodedata.combining(ch)
    )
    tokens = [tok for tok in _TOKEN_RE.findall(ascii_text) if len(tok) >= 3]
    stop = {
        "mcp", "outil", "tool", "server", "serveur", "pour", "avec",
        "dans", "connecter", "interagir", "lire", "ecrire", "write",
        "read", "create", "creer", "true", "ticket", "base", "bases",
    }
    kept = [tok for tok in tokens if tok not in stop]
    base = kept[0] if kept else "local"
    base = base[:_SLUG_MAX].strip("-_")
    if not base:
        base = "local"
    digest = hashlib.sha256(intent.encode("utf-8")).hexdigest()[:8]
    return f"{base}_{digest}"


def _intent_hash(intent: str) -> str:
    return hashlib.sha256(intent.encode("utf-8")).hexdigest()


def _safe_caller(raw: Any) -> str:
    if not isinstance(raw, str):
        return "react"
    value = raw.strip().lower().replace("-", "_")
    return value if value in _CALLER_WHITELIST else "react"


class MCPLocalCreationTicketOrchestrator:
    """Create ApprovalQueue tickets for local MCP server creation requests."""

    def __init__(self, approval_queue: LocalCreationApprovalQueueLike):
        if approval_queue is None or not hasattr(approval_queue, "propose"):
            raise ValueError("approval_queue must expose propose")
        self._approval_queue = approval_queue

    def propose_local_creation(
        self,
        intent: Any,
        *,
        caller_kind: str = "react",
        profile: Optional[str] = None,
        dry_run: bool = True,
        ttl_s: Optional[float] = None,
    ) -> LocalCreationTicketProposal:
        cleaned = _sanitize_intent(intent)
        suggested_server_id = _slug_from_intent(cleaned)
        tool_name = f"{LOCAL_CREATE_TOOL_PREFIX}{suggested_server_id}"

        if dry_run:
            return LocalCreationTicketProposal(
                approval_ticket_id=None,
                suggested_server_id=suggested_server_id,
                tool_name=tool_name,
                risk_summary=LOCAL_CREATE_RISK_SUMMARY,
                dry_run=True,
            )

        args: Dict[str, Any] = {
            "action": "local_create",
            "intent": cleaned,
            "intent_hash": _intent_hash(cleaned),
            "server_id": suggested_server_id,
            "profile": profile if isinstance(profile, str) else None,
            "requested_at": _now_utc_iso(),
        }
        # Keep encrypted payload compact and deterministic enough for tests.
        json.dumps(args, ensure_ascii=False, sort_keys=True)

        action_id = self._approval_queue.propose(
            tool_name=tool_name,
            args=args,
            policy=MCPPolicy.LOCAL_WRITE,
            caller_kind=_safe_caller(caller_kind),
            risk_summary=LOCAL_CREATE_RISK_SUMMARY,
            ttl_s=ttl_s,
        )
        if not isinstance(action_id, str) or not action_id:
            raise LocalCreationTicketError("approval_queue_failed")
        return LocalCreationTicketProposal(
            approval_ticket_id=action_id,
            suggested_server_id=suggested_server_id,
            tool_name=tool_name,
            risk_summary=LOCAL_CREATE_RISK_SUMMARY,
            dry_run=False,
        )


__all__ = [
    "LOCAL_CREATE_RISK_SUMMARY",
    "LOCAL_CREATE_TOOL_PREFIX",
    "LocalCreationApprovalQueueLike",
    "LocalCreationTicketError",
    "LocalCreationTicketProposal",
    "MCPLocalCreationTicketOrchestrator",
]
