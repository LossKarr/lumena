"""Catalog add approval bridge for autonomous MCP discovery.

This module turns a safe Phase 23 catalog proposal into a real ApprovalQueue
ticket, then materializes an approved ticket as a DECLARED server in the MCP
catalog. It does not install, activate, launch an external process, or
register tools.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

from src.mcp.approval_queue import ApprovalDecision
from src.mcp.policy import MCPPolicy


CATALOG_ADD_TOOL_PREFIX = "mcp_catalog_add:"
CATALOG_ADD_RISK_SUMMARY = "catalog_add_required"

# Phase I-8 (Fix AA.4) : plancher de confiance accordé par l'approbation
# admin explicite d'un ticket catalog_add. 70 = seuil install/write ;
# le seuil SECRETS (90) n'est JAMAIS atteint par ce plancher.
_HUMAN_APPROVED_TRUST_FLOOR = 70

_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
_WINDOWS_RESERVED_NAMES = frozenset({
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5",
    "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
    "lpt6", "lpt7", "lpt8", "lpt9",
})
_CALLER_WHITELIST = frozenset({
    "react", "admin_ui", "autonomous_loop", "test",
})


class CatalogAddApprovalQueueLike(Protocol):
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


class CatalogAddCatalogLike(Protocol):
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


class CatalogAddError(Exception):
    """Short-code error for catalog-add proposal/execution failures."""


@dataclass(frozen=True)
class CatalogAddProposalInput:
    server_id: str
    display_name: str
    package_spec: str
    version: Optional[str]
    trust_score: Optional[int]
    # Phase I-8 (Fix AC) : tags discriminants de l'intent d'origine,
    # persistés sur l'entrée pour le re-matching des intents futurs.
    capability_tags: Optional[tuple] = None


@dataclass(frozen=True)
class CatalogAddTicketProposal:
    approval_ticket_id: Optional[str]
    server_id: str
    tool_name: str
    risk_summary: str
    dry_run: bool


@dataclass(frozen=True)
class CatalogAddExecutionResult:
    server_id: str
    success: bool
    catalog_status: Optional[str]
    reason: str
    dry_run: bool = False


def _safe_caller(raw: Any) -> str:
    if not isinstance(raw, str):
        return "react"
    value = raw.strip().lower().replace("-", "_")
    return value if value in _CALLER_WHITELIST else "react"


def _is_valid_server_id(server_id: Any) -> bool:
    if not isinstance(server_id, str) or not _SERVER_ID_RE.match(server_id):
        return False
    if ".." in server_id or "/" in server_id or "\\" in server_id:
        return False
    stem = server_id.split(".", 1)[0]
    return stem not in _WINDOWS_RESERVED_NAMES


def _derive_server_id(package_spec: Any) -> str:
    # Phase I-7 : extrait un server_id depuis un package_spec npm:/pypi:/local:.
    # Exemples :
    #   npm:@modelcontextprotocol/server-slack  -> server-slack
    #   npm:mcp-foo                              -> mcp-foo
    #   pypi:mcp_server_x                        -> mcp_server_x
    #   local:/path/to/dir                       -> dir (basename)
    if not isinstance(package_spec, str) or not package_spec.strip():
        return ""
    spec = package_spec.strip()
    if ":" in spec:
        _, _, rest = spec.partition(":")
    else:
        rest = spec
    rest = rest.strip()
    if not rest:
        return ""
    if rest.startswith("@") and "/" in rest:
        rest = rest.split("/", 1)[1]
    rest = rest.replace("\\", "/").rstrip("/")
    if "/" in rest:
        rest = rest.rsplit("/", 1)[1]
    rest = rest.lower()
    rest = re.sub(r"[^a-z0-9_.\-]", "-", rest)
    rest = re.sub(r"-+", "-", rest).strip("-")
    if not rest:
        return ""
    if not rest[0].isalnum():
        rest = "mcp-" + rest
    return rest[:64]


def _status_value(entry: Any) -> Optional[str]:
    status = getattr(entry, "status", None)
    value = getattr(status, "value", status)
    return value if isinstance(value, str) else None


class MCPCatalogAddOrchestrator:
    """Create and execute approval tickets for Catalog DECLARED entries."""

    def __init__(
        self,
        *,
        catalog: CatalogAddCatalogLike,
        approval_queue: CatalogAddApprovalQueueLike,
    ) -> None:
        if catalog is None or not hasattr(catalog, "get_server") or not hasattr(catalog, "add_server"):
            raise ValueError("catalog must expose get_server and add_server")
        if approval_queue is None or not hasattr(approval_queue, "propose"):
            raise ValueError("approval_queue must expose propose")
        self._catalog = catalog
        self._approval_queue = approval_queue

    def propose_catalog_add(
        self,
        proposal: CatalogAddProposalInput,
        *,
        caller_kind: str = "react",
        dry_run: bool = True,
        ttl_s: Optional[float] = None,
    ) -> CatalogAddTicketProposal:
        self._validate_proposal(proposal)
        existing = self._safe_get(proposal.server_id)
        # Phase I-8 (Fix AJ) : une entrée REMOVED ne court-circuite PAS la
        # proposition — le server_id étant un hash déterministe du
        # package_spec, l'early-return rendait tout package supprimé
        # définitivement inréinstallable. Un nouveau ticket est créé ; son
        # approbation re-déclarera l'entrée.
        if existing is not None and _status_value(existing) != "removed":
            return CatalogAddTicketProposal(
                approval_ticket_id=None,
                server_id=proposal.server_id,
                tool_name=CATALOG_ADD_TOOL_PREFIX + proposal.server_id,
                risk_summary=CATALOG_ADD_RISK_SUMMARY,
                dry_run=dry_run,
            )
        if dry_run:
            return CatalogAddTicketProposal(
                approval_ticket_id=None,
                server_id=proposal.server_id,
                tool_name=CATALOG_ADD_TOOL_PREFIX + proposal.server_id,
                risk_summary=CATALOG_ADD_RISK_SUMMARY,
                dry_run=True,
            )
        args: Dict[str, Any] = {
            "action": "catalog_add",
            "server_id": proposal.server_id,
            "display_name": proposal.display_name,
            "package_spec": proposal.package_spec,
            "version": proposal.version,
            "trust_score": proposal.trust_score,
            "owner_profile": "lumena",
        }
        # Phase I-8 (Fix AC) : tags optionnels dans le ticket (validés
        # à l'exécution par add_server).
        prop_tags = getattr(proposal, "capability_tags", None)
        if isinstance(prop_tags, (list, tuple)) and prop_tags:
            args["capability_tags"] = [
                t for t in prop_tags if isinstance(t, str) and t
            ]
        json.dumps(args, ensure_ascii=False, sort_keys=True)
        action_id = self._approval_queue.propose(
            tool_name=CATALOG_ADD_TOOL_PREFIX + proposal.server_id,
            args=args,
            policy=MCPPolicy.LOCAL_WRITE,
            caller_kind=_safe_caller(caller_kind),
            risk_summary=CATALOG_ADD_RISK_SUMMARY,
            ttl_s=ttl_s,
        )
        if not isinstance(action_id, str) or not action_id:
            raise CatalogAddError("approval_queue_failed")
        return CatalogAddTicketProposal(
            approval_ticket_id=action_id,
            server_id=proposal.server_id,
            tool_name=CATALOG_ADD_TOOL_PREFIX + proposal.server_id,
            risk_summary=CATALOG_ADD_RISK_SUMMARY,
            dry_run=False,
        )

    def propose(
        self,
        *,
        package_spec: str,
        source_kind: Optional[str] = None,
        source_url: Optional[str] = None,
        slug: Optional[str] = None,
        display_name: Optional[str] = None,
        version: Optional[str] = None,
        trust_score: Optional[int] = None,
        caller_kind: str = "react",
        ttl_s: Optional[float] = None,
    ) -> CatalogAddTicketProposal:
        # Phase I-7 : pont entre ReAct handler add_mcp et propose_catalog_add.
        # Le handler ne connait que (package_spec, source_kind, source_url) +
        # eventuels enrichissements curated (slug, display_name, ...). On derive
        # un server_id canonique et on delegue a propose_catalog_add(dry_run=False).
        sid = slug if _is_valid_server_id(slug) else _derive_server_id(package_spec)
        if not _is_valid_server_id(sid):
            raise CatalogAddError("server_id_invalid")
        name = display_name if isinstance(display_name, str) and display_name.strip() else sid
        if not isinstance(package_spec, str) or not package_spec.strip():
            raise CatalogAddError("package_spec_invalid")
        # Phase I-8 (Fix AC) : pas d'intent utilisateur sur ce chemin
        # (add_mcp direct) — tags dérivés du nom/slug du package.
        tags: Optional[tuple] = None
        try:
            from src.mcp.capability_resolver import (  # noqa: WPS433
                derive_capability_tags,
            )
            derived = derive_capability_tags(f"{name} {sid}")
            tags = derived if derived else None
        except Exception:  # noqa: BLE001
            tags = None
        proposal = CatalogAddProposalInput(
            server_id=sid,
            display_name=name,
            package_spec=package_spec,
            version=version if isinstance(version, str) and version.strip() else None,
            trust_score=trust_score if isinstance(trust_score, int) and not isinstance(trust_score, bool) else None,
            capability_tags=tags,
        )
        return self.propose_catalog_add(
            proposal,
            caller_kind=caller_kind,
            dry_run=False,
            ttl_s=ttl_s,
        )

    def execute_approved_catalog_add(
        self,
        server_id: str,
        approval_result: Any,
        *,
        dry_run: bool = True,
    ) -> CatalogAddExecutionResult:
        if not _is_valid_server_id(server_id):
            return CatalogAddExecutionResult(
                server_id="", success=False, catalog_status=None,
                reason="server_id_invalid", dry_run=dry_run,
            )
        args = self._validate_approval_result(approval_result, server_id)
        if isinstance(args, CatalogAddExecutionResult):
            return args
        existing = self._safe_get(server_id)
        redeclare_removed = (
            existing is not None and _status_value(existing) == "removed"
        )
        if existing is not None and not redeclare_removed:
            return CatalogAddExecutionResult(
                server_id=server_id,
                success=True,
                catalog_status=_status_value(existing),
                reason="already_declared",
                dry_run=dry_run,
            )
        if dry_run:
            return CatalogAddExecutionResult(
                server_id=server_id,
                success=False,
                catalog_status="declared",
                reason="dry_run",
                dry_run=True,
            )
        # Phase I-8 (Fix AA.4) : un ticket catalog_add APPROVED n'arrive ici
        # que via consentement admin explicite (panel humain, bypass curated,
        # ou pattern admin). Ce consentement est le signal de confiance le
        # plus fort du système : on garantit un plancher trust_score à 70
        # (seuil install/write de InstallOrchestrator et PolicyAttributor),
        # sinon le pre-score heuristique réseau (downloads/license/recence)
        # bloquerait l'install d'un package que l'humain a explicitement
        # approuvé. Jamais 90 : le seuil SECRETS reste hors d'atteinte.
        raw_trust = args.get("trust_score")
        effective_trust = (
            max(raw_trust, _HUMAN_APPROVED_TRUST_FLOOR)
            if isinstance(raw_trust, int) and not isinstance(raw_trust, bool)
            else _HUMAN_APPROVED_TRUST_FLOOR
        )
        # Phase I-8 (Fix AC) : tags optionnels — invalides → ignorés.
        raw_tags = args.get("capability_tags")
        capability_tags = None
        if isinstance(raw_tags, (list, tuple)) and raw_tags:
            cleaned = [t for t in raw_tags if isinstance(t, str) and t]
            capability_tags = cleaned if cleaned else None
        # Phase I-8 (Fix AJ) : entrée REMOVED → re-déclaration (nouveau
        # consentement humain) ; sinon création classique.
        add_kwargs: Dict[str, Any] = dict(
            server_id=server_id,
            display_name=args["display_name"],
            package_spec=args["package_spec"],
            owner_profile="lumena",
            version=args.get("version"),
            trust_score=min(effective_trust, 100),
            notes="catalog_add_from_mcp_autonomy",
        )
        if redeclare_removed:
            writer = getattr(self._catalog, "redeclare_server", None)
            if not callable(writer):
                # Catalog legacy sans redeclare : impossible de réactiver.
                return CatalogAddExecutionResult(
                    server_id=server_id,
                    success=False,
                    catalog_status="removed",
                    reason="redeclare_unsupported",
                )
        else:
            writer = self._catalog.add_server
        try:
            entry = writer(capability_tags=capability_tags, **add_kwargs)
        except TypeError:
            # Catalog sans support capability_tags (mock/test legacy) :
            # retry sans le kwarg pour ne jamais casser le chemin principal.
            try:
                entry = writer(**add_kwargs)
            except Exception:
                return CatalogAddExecutionResult(
                    server_id=server_id,
                    success=False,
                    catalog_status=None,
                    reason="catalog_add_failed",
                )
        except Exception:
            return CatalogAddExecutionResult(
                server_id=server_id,
                success=False,
                catalog_status=None,
                reason="catalog_add_failed",
            )
        return CatalogAddExecutionResult(
            server_id=server_id,
            success=True,
            catalog_status=_status_value(entry) or "declared",
            reason="declared",
        )

    def _validate_proposal(self, proposal: Any) -> None:
        required = ("server_id", "display_name", "package_spec", "version", "trust_score")
        if any(not hasattr(proposal, field) for field in required):
            raise CatalogAddError("proposal_invalid")
        if not _is_valid_server_id(proposal.server_id):
            raise CatalogAddError("server_id_invalid")
        if not isinstance(proposal.display_name, str) or not proposal.display_name.strip():
            raise CatalogAddError("display_name_invalid")
        if not isinstance(proposal.package_spec, str) or not proposal.package_spec.strip():
            raise CatalogAddError("package_spec_invalid")
        if proposal.version is not None and not isinstance(proposal.version, str):
            raise CatalogAddError("version_invalid")
        if proposal.trust_score is not None:
            if isinstance(proposal.trust_score, bool) or not isinstance(proposal.trust_score, int):
                raise CatalogAddError("trust_score_invalid")
            if not (0 <= proposal.trust_score <= 100):
                raise CatalogAddError("trust_score_invalid")

    def _validate_approval_result(
        self, approval_result: Any, server_id: str
    ) -> Dict[str, Any] | CatalogAddExecutionResult:
        decision = getattr(approval_result, "decision", None)
        decision_value = getattr(decision, "value", decision)
        if str(decision_value).lower() != ApprovalDecision.APPROVED.value:
            return CatalogAddExecutionResult(
                server_id=server_id,
                success=False,
                catalog_status=None,
                reason="approval_not_granted",
            )
        args = getattr(approval_result, "args", None)
        if not isinstance(args, dict):
            return CatalogAddExecutionResult(
                server_id=server_id,
                success=False,
                catalog_status=None,
                reason="approval_args_invalid",
            )
        if args.get("action") != "catalog_add":
            return CatalogAddExecutionResult(
                server_id=server_id,
                success=False,
                catalog_status=None,
                reason="approval_action_mismatch",
            )
        if args.get("server_id") != server_id:
            return CatalogAddExecutionResult(
                server_id=server_id,
                success=False,
                catalog_status=None,
                reason="approval_server_id_mismatch",
            )
        required = ("display_name", "package_spec", "owner_profile")
        if any(not isinstance(args.get(k), str) or not args.get(k).strip() for k in required):
            return CatalogAddExecutionResult(
                server_id=server_id,
                success=False,
                catalog_status=None,
                reason="approval_args_invalid",
            )
        return args

    def _safe_get(self, server_id: str) -> Any:
        try:
            return self._catalog.get_server(server_id)
        except Exception:
            return None


__all__ = [
    "CATALOG_ADD_RISK_SUMMARY",
    "CATALOG_ADD_TOOL_PREFIX",
    "CatalogAddApprovalQueueLike",
    "CatalogAddCatalogLike",
    "CatalogAddError",
    "CatalogAddExecutionResult",
    "CatalogAddProposalInput",
    "CatalogAddTicketProposal",
    "MCPCatalogAddOrchestrator",
]
