"""
discovery.py — MCP Discovery / Metadata Collector (Phase 17 v3).

Orchestrateur qui :
  1. vérifie le statut d'un serveur dans le Catalog (Phase 14)
  2. initialise un MCPClient (Phase 7) si nécessaire
  3. récupère la liste d'outils via list_tools()
  4. valide chaque tool (defensive + anti-confused-deputy)
  5. propose une policy via PolicyAttributor (Phase 16)
  6. produit un DiscoveryReport sérialisable

DOCTRINE Phase 17 v3 :
  - Discovery = proposition. JAMAIS d'activation automatique.
  - Aucun appel à register_dynamic_handler (Phase 8 intouchée).
  - Aucun call_tool : seuls initialize() et list_tools() utilisés.
  - Aucun câblage ReAct/CodeAgent.
  - Aucune touche : tool_registry.py, react.py, sub_agent.py, MCPClient,
    MCPSandboxRunner, approval_queue.py, policy.py, auto_approve.py,
    runtime_watcher.py, orchestrator.py, server_catalog.py,
    policy_resolver.py, policy_attributor.py, handler_adapter.py.
  - Sérialisation JSON explicite : toutes les MCPPolicy → policy.value
    via _proposal_to_dict / _report_to_dict.

initialize() conditionnel :
  Si client.is_initialized est True → initialize NON rappelé.
  Sinon → initialize() invoqué une seule fois.

Anti-confused-deputy :
  tool.name contenant "mcp__" est REFUSÉ (anti-spoofing du namespacing).

Audit forensique sans PII :
  Whitelist stricte. JAMAIS de description / input_schema raw : on log
  uniquement has_description / has_input_schema (booléens) et
  description_length / input_schema_keys_count (entiers structurels).

Layout disque (optionnel, persist_reports=False par défaut) :
  DATA_DIR/mcp_discovery/audit.jsonl
  DATA_DIR/mcp_discovery/reports/<server_id>_<ts_safe>.json
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    Sequence,
    runtime_checkable,
)

from loguru import logger

from src.mcp.client import MCPTool
from src.mcp.policy import MCPPolicy
from src.mcp.policy_attributor import (
    PolicyAttributor,
    ToolMetadata,
)
from src.mcp.server_catalog import (
    MCPServerCatalog,
    ServerStatus,
)
from src.utils.paths import DATA_DIR
from src.utils.persistence import atomic_write_json


# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_DIRNAME = "mcp_discovery"
_REPORTS_SUBDIR = "reports"
_AUDIT_FILENAME = "audit.jsonl"

_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
# Phase I-8 (Fix AZ) : la spec MCP n'impose PAS la casse des noms de tools.
# windows-mcp 3.4.2 expose `App`, `Click`, `PowerShell`... (PascalCase) —
# le regex lowercase-only refusait les 19 tools (`invalid_count: 19`,
# runtime 2026-06-13 02:43). Charset inchangé (strict), seule la casse
# est élargie. Les server_id restent lowercase (slugs dérivés par nous).
_TOOL_NAME_LOCAL_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,128}$")
_MAX_DESCRIPTION_LEN = 4096

_WINDOWS_RESERVED_NAMES = frozenset({
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5",
    "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
    "lpt6", "lpt7", "lpt8", "lpt9",
})


# ──────────────────────────────────────────────────────────────────────────────
# Protocol attendu du MCPClient
# ──────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class MCPClientLike(Protocol):
    """Protocol minimal attendu du MCPClient Phase 7.

    Le vrai MCPClient expose ces 3 éléments (cf src/mcp/client.py) :
      - is_initialized : @property bool
      - initialize() : retourne Any (Dict capabilities côté Phase 7)
      - list_tools() : retourne List[MCPTool]
    """

    is_initialized: bool

    def initialize(self) -> Any: ...

    def list_tools(self) -> List[MCPTool]: ...


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions / Dataclasses
# ──────────────────────────────────────────────────────────────────────────────


class DiscoveryError(Exception):
    """Levée pour conditions GLOBALES uniquement : server_unknown,
    server_not_callable, initialize_failed, list_tools_failed,
    server_id_invalid."""


@dataclass(frozen=True)
class ToolDiscoveryProposal:
    server_id: str
    tool_name: str                                # nom local côté MCP
    namespaced_name: str                          # mcp__{server_id}__{tool_name}
    proposed_policy: Optional[MCPPolicy]
    attribution_reason: str
    matched_keywords: List[str] = field(default_factory=list)
    trust_score_used: Optional[int] = None
    classified_policy: Optional[MCPPolicy] = None
    has_description: bool = False
    has_input_schema: bool = False
    description_length: int = 0
    input_schema_keys_count: int = 0


@dataclass(frozen=True)
class DiscoveryReport:
    server_id: str
    ts: str
    discovered_count: int
    proposed_count: int
    refused_count: int
    invalid_count: int
    error_count: int
    proposals: List[ToolDiscoveryProposal] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts_safe(iso: str) -> str:
    """Convertit un ISO 8601 en token safe pour nom de fichier."""
    return iso.replace(":", "-").replace("+", "p").replace(".", "_")


def _proposal_to_dict(proposal: ToolDiscoveryProposal) -> Dict[str, Any]:
    """Sérialise une ToolDiscoveryProposal en dict JSON-safe.

    Toutes les MCPPolicy sont converties en policy.value (ou None).
    """
    return {
        "server_id": str(proposal.server_id),
        "tool_name": str(proposal.tool_name),
        "namespaced_name": str(proposal.namespaced_name),
        "proposed_policy": (
            proposal.proposed_policy.value
            if proposal.proposed_policy is not None
            else None
        ),
        "attribution_reason": str(proposal.attribution_reason),
        "matched_keywords": [str(k) for k in proposal.matched_keywords],
        "trust_score_used": (
            int(proposal.trust_score_used)
            if proposal.trust_score_used is not None
            else None
        ),
        "classified_policy": (
            proposal.classified_policy.value
            if proposal.classified_policy is not None
            else None
        ),
        "has_description": bool(proposal.has_description),
        "has_input_schema": bool(proposal.has_input_schema),
        "description_length": int(proposal.description_length),
        "input_schema_keys_count": int(proposal.input_schema_keys_count),
    }


def _report_to_dict(report: DiscoveryReport) -> Dict[str, Any]:
    """Sérialise un DiscoveryReport en dict JSON-safe."""
    return {
        "server_id": str(report.server_id),
        "ts": str(report.ts),
        "discovered_count": int(report.discovered_count),
        "proposed_count": int(report.proposed_count),
        "refused_count": int(report.refused_count),
        "invalid_count": int(report.invalid_count),
        "error_count": int(report.error_count),
        "proposals": [_proposal_to_dict(p) for p in report.proposals],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Validators
# ──────────────────────────────────────────────────────────────────────────────


def _is_valid_server_id(server_id: Any) -> bool:
    if not isinstance(server_id, str) or not _SERVER_ID_RE.match(server_id):
        return False
    if ".." in server_id or "/" in server_id or "\\" in server_id:
        return False
    stem = server_id.split(".", 1)[0]
    if stem in _WINDOWS_RESERVED_NAMES:
        return False
    return True


def _validate_tool_name(name: Any) -> Optional[str]:
    """Returns reason code court si invalide, None si valide.

    Refuse :
      - non-str / vide
      - regex `^[A-Za-z0-9_.\\-]{1,128}$` non matchée (Fix AZ : casse libre,
        la spec MCP ne l'impose pas — windows-mcp expose du PascalCase)
      - "mcp__" présent (anti-confused-deputy)
    """
    if not isinstance(name, str) or not name:
        return "name_invalid"
    if not _TOOL_NAME_LOCAL_RE.match(name):
        return "name_invalid"
    if "mcp__" in name:
        return "name_spoofing"
    return None


def _validate_description(description: Any) -> Optional[str]:
    """Returns reason code court si invalide, None si valide.

    description est string (vrai MCPTool ne met pas None mais defensive
    accepte aussi None).
    """
    if description is None:
        return None
    if not isinstance(description, str):
        return "description_invalid"
    if len(description) > _MAX_DESCRIPTION_LEN:
        return "description_invalid"
    for ch in description:
        if ord(ch) < 0x20 and ch not in ("\t", "\n", "\r"):
            return "description_invalid"
        if ord(ch) == 0x7f:
            return "description_invalid"
    return None


def _validate_input_schema(input_schema: Any) -> Optional[str]:
    """Returns reason code court si invalide, None si valide."""
    if input_schema is None:
        return None
    if not isinstance(input_schema, dict):
        return "input_schema_invalid"
    return None


def _validate_trust_score(trust_score: Any) -> None:
    if trust_score is None:
        return
    if isinstance(trust_score, bool):
        raise DiscoveryError("trust_score_invalid")
    if not isinstance(trust_score, int):
        raise DiscoveryError("trust_score_invalid")
    if trust_score < 0 or trust_score > 100:
        raise DiscoveryError("trust_score_invalid")


# ──────────────────────────────────────────────────────────────────────────────
# DiscoveryService
# ──────────────────────────────────────────────────────────────────────────────


class MCPDiscoveryService:
    """Discovery service — produit des propositions, n'active rien."""

    def __init__(
        self,
        catalog: MCPServerCatalog,
        attributor: PolicyAttributor,
        audit_log_path: Optional[Path] = None,
        reports_dir: Optional[Path] = None,
        require_server_callable: bool = True,
        persist_reports: bool = False,
    ):
        if catalog is None:
            raise ValueError("catalog must not be None")
        if attributor is None:
            raise ValueError("attributor must not be None")
        if not callable(getattr(attributor, "attribute", None)):
            raise ValueError("attributor must expose .attribute()")

        self._catalog = catalog
        self._attributor = attributor
        self._audit_log_path = audit_log_path or (
            DATA_DIR / _DEFAULT_DIRNAME / _AUDIT_FILENAME
        )
        self._reports_dir = reports_dir or (
            DATA_DIR / _DEFAULT_DIRNAME / _REPORTS_SUBDIR
        )
        self._require_server_callable = bool(require_server_callable)
        self._persist_reports = bool(persist_reports)

        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        if self._persist_reports:
            self._reports_dir.mkdir(parents=True, exist_ok=True)

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def audit_log_path(self) -> Path:
        return self._audit_log_path

    @property
    def reports_dir(self) -> Path:
        return self._reports_dir

    @property
    def require_server_callable(self) -> bool:
        return self._require_server_callable

    @property
    def persist_reports(self) -> bool:
        return self._persist_reports

    # ── Audit (whitelist stricte) ─────────────────────────────────────────

    def _audit(self, event: str, **fields: Any) -> None:
        """Append-only audit jsonl. Champs whitelist :
        server_id, tool_name, namespaced_name, policy, classified_policy,
        reason, has_description, has_input_schema, description_length,
        input_schema_keys_count, matched_keywords, trust_score_used,
        discovered_count, proposed_count, refused_count, invalid_count,
        error_count, status, ts.
        """
        record = {"ts": _now_iso(), "event": event, **fields}
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"[mcp.discovery] audit write failed: {e}")

    # ── discover() ────────────────────────────────────────────────────────

    def discover(
        self,
        server_id: Any,
        client: MCPClientLike,
        *,
        trust_score: Optional[int] = None,
    ) -> DiscoveryReport:
        """Discovery pipeline. Voir docstring module.

        Raises DiscoveryError pour conditions globales :
          - server_id_invalid (audit n'inclut PAS server_id)
          - server_unknown
          - server_not_callable
          - initialize_failed
          - list_tools_failed
          - trust_score_invalid
        """
        # ── Étape 1 : validate server_id ─────────────────────────────────
        if not _is_valid_server_id(server_id):
            # Anti-leak : on ne logue PAS server_id (potentiellement
            # attacker-controlled).
            self._audit("server_id_invalid", reason="server_id_invalid")
            raise DiscoveryError("server_id_invalid")

        # ── Étape 1b : validate trust_score (optionnel override) ─────────
        _validate_trust_score(trust_score)

        # ── Étape 2 : catalog.get_server ─────────────────────────────────
        entry = self._catalog.get_server(server_id)
        if entry is None:
            self._audit(
                "discovery_failed",
                server_id=server_id,
                reason="server_unknown",
            )
            raise DiscoveryError("server_unknown")

        # ── Étape 3 : is_callable gate ────────────────────────────────────
        if self._require_server_callable:
            if not self._catalog.is_callable(server_id):
                self._audit(
                    "discovery_failed",
                    server_id=server_id,
                    status=entry.status.value,
                    reason="server_not_callable",
                )
                raise DiscoveryError("server_not_callable")
        else:
            # Même avec require=False, REMOVED reste bloqué pour cohérence
            if entry.status == ServerStatus.REMOVED:
                self._audit(
                    "discovery_failed",
                    server_id=server_id,
                    status=entry.status.value,
                    reason="server_not_callable",
                )
                raise DiscoveryError("server_not_callable")

        self._audit(
            "discovery_started",
            server_id=server_id,
            status=entry.status.value,
        )

        # ── Étape 4 : trust score résolution ──────────────────────────────
        effective_trust: Optional[int] = (
            trust_score if trust_score is not None else entry.trust_score
        )

        # ── Étape 5 : initialize conditionnel ─────────────────────────────
        already_initialized = bool(getattr(client, "is_initialized", False))
        if not already_initialized:
            try:
                client.initialize()
            except Exception:  # noqa: BLE001
                self._audit(
                    "initialize_failed",
                    server_id=server_id,
                    reason="initialize_failed",
                )
                raise DiscoveryError("initialize_failed")

        # ── Étape 6 : list_tools ──────────────────────────────────────────
        try:
            tools = client.list_tools()
        except Exception:  # noqa: BLE001
            self._audit(
                "list_tools_failed",
                server_id=server_id,
                reason="list_tools_failed",
            )
            raise DiscoveryError("list_tools_failed")

        if not isinstance(tools, (list, tuple)):
            self._audit(
                "list_tools_failed",
                server_id=server_id,
                reason="list_tools_failed",
            )
            raise DiscoveryError("list_tools_failed")

        discovered_count = len(tools)
        proposed_count = 0
        refused_count = 0
        invalid_count = 0
        error_count = 0
        proposals: List[ToolDiscoveryProposal] = []

        # ── Étape 7 : pour chaque MCPTool ─────────────────────────────────
        for tool in tools:
            # Defensive : accès attributs
            try:
                name = getattr(tool, "name")
                description = getattr(tool, "description", None)
                input_schema = getattr(tool, "input_schema", None)
            except Exception:  # noqa: BLE001
                invalid_count += 1
                self._audit(
                    "tool_invalid",
                    server_id=server_id,
                    reason="missing_attribute",
                )
                continue

            # Validate name (incluant anti-confused-deputy)
            name_reason = _validate_tool_name(name)
            if name_reason is not None:
                invalid_count += 1
                # Anti-leak : si name invalide, on ne logue PAS name
                self._audit(
                    "tool_invalid",
                    server_id=server_id,
                    reason=name_reason,
                )
                continue

            # Validate description (defensive)
            desc_reason = _validate_description(description)
            if desc_reason is not None:
                invalid_count += 1
                self._audit(
                    "tool_invalid",
                    server_id=server_id,
                    tool_name=name,
                    reason=desc_reason,
                )
                continue

            # Validate input_schema (defensive)
            schema_reason = _validate_input_schema(input_schema)
            if schema_reason is not None:
                invalid_count += 1
                self._audit(
                    "tool_invalid",
                    server_id=server_id,
                    tool_name=name,
                    reason=schema_reason,
                )
                continue

            # Construire ToolMetadata + appel attributor
            metadata = ToolMetadata(
                server_id=server_id,
                tool_name=name,
                description=description if isinstance(description, str) else None,
                input_schema=input_schema if isinstance(input_schema, dict) else None,
            )

            try:
                decision = self._attributor.attribute(
                    metadata, trust_score=effective_trust
                )
            except Exception:  # noqa: BLE001
                error_count += 1
                self._audit(
                    "attributor_error",
                    server_id=server_id,
                    tool_name=name,
                    reason="attributor_error",
                )
                continue

            namespaced_name = f"mcp__{server_id}__{name}"

            # Métadonnées structurelles (jamais les valeurs raw)
            has_description = isinstance(description, str) and len(description) > 0
            has_input_schema = isinstance(input_schema, dict) and len(input_schema) > 0
            description_length = len(description) if isinstance(description, str) else 0
            input_schema_keys_count = (
                len(input_schema) if isinstance(input_schema, dict) else 0
            )

            proposal = ToolDiscoveryProposal(
                server_id=server_id,
                tool_name=name,
                namespaced_name=namespaced_name,
                proposed_policy=decision.policy,
                attribution_reason=decision.reason,
                matched_keywords=list(decision.matched_keywords),
                trust_score_used=decision.trust_score_used,
                classified_policy=decision.classified_policy,
                has_description=has_description,
                has_input_schema=has_input_schema,
                description_length=description_length,
                input_schema_keys_count=input_schema_keys_count,
            )
            proposals.append(proposal)

            if decision.policy is None:
                refused_count += 1
            else:
                proposed_count += 1

            self._audit(
                "proposal_added",
                server_id=server_id,
                tool_name=name,
                namespaced_name=namespaced_name,
                policy=(decision.policy.value if decision.policy is not None else None),
                classified_policy=(
                    decision.classified_policy.value
                    if decision.classified_policy is not None else None
                ),
                reason=decision.reason,
                matched_keywords=list(decision.matched_keywords),
                trust_score_used=decision.trust_score_used,
                has_description=has_description,
                has_input_schema=has_input_schema,
                description_length=description_length,
                input_schema_keys_count=input_schema_keys_count,
            )

        # ── Étape 8 : DiscoveryReport ─────────────────────────────────────
        ts = _now_iso()
        report = DiscoveryReport(
            server_id=server_id,
            ts=ts,
            discovered_count=discovered_count,
            proposed_count=proposed_count,
            refused_count=refused_count,
            invalid_count=invalid_count,
            error_count=error_count,
            proposals=proposals,
        )

        # ── Étape 9 : persistance disque optionnelle ──────────────────────
        if self._persist_reports:
            try:
                self._reports_dir.mkdir(parents=True, exist_ok=True)
                report_path = self._reports_dir / f"{server_id}_{_ts_safe(ts)}.json"
                atomic_write_json(report_path, _report_to_dict(report))
            except OSError as e:
                logger.warning(f"[mcp.discovery] report persist failed: {e}")

        # ── Étape 10 : audit summary ──────────────────────────────────────
        self._audit(
            "discovery_summary",
            server_id=server_id,
            discovered_count=discovered_count,
            proposed_count=proposed_count,
            refused_count=refused_count,
            invalid_count=invalid_count,
            error_count=error_count,
        )

        return report
