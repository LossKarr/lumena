"""
drift_checker.py — Audit lecture seule de l'état du ToolRegistry.

Détecte le drift entre :
  - outils annoncés au LLM (advertised_in_prompt)
  - outils réellement callable selon le contrat de catégorie pour
    différents (caller, état workspace)

Garanties strictes :
  - Aucune mutation : ne touche pas registry, fichiers, metrics, ledger
  - Aucun side effect : ne loggue rien, ne publie aucune trace
  - Aucun appel de handler : ne fait que de la lecture de métadonnées
    (HandlerDef, schema) et appelle uniquement la fonction pure
    `check_contract_for_audit` du module tool_categories.

Périmètre Phase 2 (limites explicites) :
  - `contract_callable_for_context` est une preuve de "passe le contrat
    de catégorie", PAS une preuve d'exécution réelle. Les checks
    path-dependent (ToolRegistry._policy_check) sont orthogonaux et
    hors scope Phase 2.
  - `dry_call_status` est un static_check seulement (pas d'exécution du
    handler). Pour les outils mutants (delete_file, mail_send, …) c'est
    la seule option safe.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.reasoning.tool_categories import (
    check_contract_for_audit,
    get_semantic_category,
)

if TYPE_CHECKING:
    from src.reasoning.tool_registry import ToolRegistry


# ──────────────────────────────────────────────────────────────────────────────
# Constantes : callers et états workspace testés
# ──────────────────────────────────────────────────────────────────────────────

AUDIT_CALLERS = ("react", "autonomy", "codeagent")
"""Caller kinds couverts par l'audit Phase 2 (3 callers principaux).

Volontairement limité à 3 : scheduler/silent/unknown sont couverts par
test_pure_function dans test_check_contract_for_audit.py mais ajouter ces
combinaisons dans l'audit ne révèle pas plus de drift (3306 → 6612 vérifs
sans valeur ajoutée).
"""


# ──────────────────────────────────────────────────────────────────────────────
# Structures de sortie
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ContractCallableMatrix:
    """Matrice contract-passe-t-il par (caller × workspace_state).

    Note : "contract_callable" signifie "passe le check de catégorie".
    Ce n'est pas une preuve d'exécution réelle (_policy_check, args, etc.
    restent à charge de l'exécution runtime).
    """
    react_with_workspace: bool
    react_without_workspace: bool
    autonomy_with_workspace: bool
    autonomy_without_workspace: bool
    codeagent_with_workspace: bool
    codeagent_without_workspace: bool


@dataclass(frozen=True)
class ToolAuditEntry:
    name: str
    category: str
    semantic_category: str
    source_module: str
    advertised_in_prompt: bool
    contract_callable_for_context: ContractCallableMatrix
    refusal_reasons: Dict[str, str]
    dry_call_status: str  # "static_check_ok" | "static_check_failed: <reason>"
    drift_detected: bool


@dataclass(frozen=True)
class AuditSummary:
    ts: str
    total_tools: int
    advertised_count: int
    contract_callable_any_context: int
    drift_count: int
    broken_count: int
    categories: int


@dataclass(frozen=True)
class AuditFullReport:
    ts: str
    total_tools: int
    advertised_count: int
    contract_callable_any_context: int
    drift_count: int
    broken_count: int
    categories: int
    tools: List[ToolAuditEntry]


# ──────────────────────────────────────────────────────────────────────────────
# Static check : vérifie HandlerDef sans exécuter le handler
# ──────────────────────────────────────────────────────────────────────────────


def _static_check_tool(tool_dict: Dict[str, Any]) -> str:
    """Vérifie qu'un tool dict est structurellement valide.

    AUCUN appel au handler — uniquement lecture des champs.

    Args:
        tool_dict: l'entrée du dict ToolRegistry.tools[name]

    Returns:
        "static_check_ok" si valide
        "static_check_failed: <reason>" sinon
    """
    if not isinstance(tool_dict, dict):
        return "static_check_failed: tool entry is not a dict"

    handler = tool_dict.get("handler")
    if handler is None:
        return "static_check_failed: handler missing"
    if not callable(handler):
        return "static_check_failed: handler not callable"

    description = tool_dict.get("description")
    if not isinstance(description, str) or not description.strip():
        return "static_check_failed: description missing or empty"

    parameters = tool_dict.get("parameters")
    if parameters is None:
        # Certains outils sans paramètres : tolérons {} mais pas None
        return "static_check_failed: parameters is None"
    if not isinstance(parameters, dict):
        return "static_check_failed: parameters is not a dict"

    return "static_check_ok"


# ──────────────────────────────────────────────────────────────────────────────
# Audit principal
# ──────────────────────────────────────────────────────────────────────────────


def _compute_advertised_names(registry: "ToolRegistry") -> set:
    """Extrait les noms d'outils visibles par le LLM via get_tools_schema().

    Parse strict du champ `function.name` — pas de substring matching dans
    une grande chaîne (fragile).
    """
    try:
        schema = registry.get_tools_schema()
    except Exception:
        return set()

    names = set()
    for entry in schema:
        if not isinstance(entry, dict):
            continue
        fn = entry.get("function") or {}
        if isinstance(fn, dict):
            name = fn.get("name")
            if isinstance(name, str) and name:
                names.add(name)
    return names


def _resolve_source_module(name: str, v2_registry: Any) -> str:
    """Récupère source_module via HandlerRegistryV2 si disponible.

    Lecture seule, aucun side effect. Tolère :
      - v2_registry None ou absent
      - v2_registry.get() levant ou retournant None
      - HandlerDef sans champ source_module
    """
    if v2_registry is None:
        return ""
    try:
        hdef = v2_registry.get(name)
    except Exception:
        return ""
    if hdef is None:
        return ""
    sm = getattr(hdef, "source_module", "")
    return sm if isinstance(sm, str) else ""


def _audit_single_tool(
    name: str,
    tool_dict: Dict[str, Any],
    module_category: str,
    advertised_names: set,
    v2_registry: Any = None,
) -> ToolAuditEntry:
    """Audit d'un seul outil — fonction pure (modulo lecture tool_dict
    et lecture HandlerDef.source_module via v2_registry).
    """
    semantic = get_semantic_category(module_category)
    refusal_reasons: Dict[str, str] = {}

    # 6 contextes : 3 callers × 2 workspace_states
    matrix_values: Dict[str, bool] = {}
    for caller_kind in AUDIT_CALLERS:
        for has_workspace in (True, False):
            ws_key = "with_workspace" if has_workspace else "without_workspace"
            key = f"{caller_kind}.{ws_key}"
            refusal = check_contract_for_audit(
                semantic_category=semantic,
                caller_kind=caller_kind,
                has_workspace=has_workspace,
            )
            callable_ = (refusal is None)
            matrix_values[f"{caller_kind}_{ws_key}"] = callable_
            if not callable_ and refusal:
                refusal_reasons[key] = refusal

    matrix = ContractCallableMatrix(
        react_with_workspace=matrix_values["react_with_workspace"],
        react_without_workspace=matrix_values["react_without_workspace"],
        autonomy_with_workspace=matrix_values["autonomy_with_workspace"],
        autonomy_without_workspace=matrix_values["autonomy_without_workspace"],
        codeagent_with_workspace=matrix_values["codeagent_with_workspace"],
        codeagent_without_workspace=matrix_values["codeagent_without_workspace"],
    )

    advertised = name in advertised_names
    callable_any = any(matrix_values.values())
    drift = advertised and not callable_any

    dry_status = _static_check_tool(tool_dict)
    source_module = _resolve_source_module(name, v2_registry)

    return ToolAuditEntry(
        name=name,
        category=module_category,
        semantic_category=semantic,
        source_module=source_module,
        advertised_in_prompt=advertised,
        contract_callable_for_context=matrix,
        refusal_reasons=refusal_reasons,
        dry_call_status=dry_status,
        drift_detected=drift,
    )


def audit_registry(registry: "ToolRegistry") -> AuditFullReport:
    """Effectue l'audit complet d'un ToolRegistry.

    Args:
        registry: instance ToolRegistry à auditer

    Returns:
        AuditFullReport avec summary + entries pour tous les outils

    Garanties :
        - Aucune mutation du registry
        - Aucun side effect (logs, metrics, traces)
        - Aucun appel des handlers (static_check seulement)
    """
    ts = datetime.now(timezone.utc).isoformat()
    advertised_names = _compute_advertised_names(registry)
    tool_modules = getattr(registry, "_tool_modules", {}) or {}
    tools_dict = getattr(registry, "tools", {}) or {}
    v2_registry = getattr(registry, "_v2_registry", None)

    entries: List[ToolAuditEntry] = []
    for name in sorted(tools_dict.keys()):
        tool_dict = tools_dict[name]
        category = tool_modules.get(name, "unknown")
        entry = _audit_single_tool(
            name=name,
            tool_dict=tool_dict if isinstance(tool_dict, dict) else {},
            module_category=category,
            advertised_names=advertised_names,
            v2_registry=v2_registry,
        )
        entries.append(entry)

    # Summary
    total = len(entries)
    advertised_count = sum(1 for e in entries if e.advertised_in_prompt)
    callable_any_count = sum(
        1 for e in entries
        if any([
            e.contract_callable_for_context.react_with_workspace,
            e.contract_callable_for_context.react_without_workspace,
            e.contract_callable_for_context.autonomy_with_workspace,
            e.contract_callable_for_context.autonomy_without_workspace,
            e.contract_callable_for_context.codeagent_with_workspace,
            e.contract_callable_for_context.codeagent_without_workspace,
        ])
    )
    drift_count = sum(1 for e in entries if e.drift_detected)
    broken_count = sum(
        1 for e in entries if not e.dry_call_status.startswith("static_check_ok")
    )
    categories_count = len(
        {e.semantic_category for e in entries if e.semantic_category}
    )

    return AuditFullReport(
        ts=ts,
        total_tools=total,
        advertised_count=advertised_count,
        contract_callable_any_context=callable_any_count,
        drift_count=drift_count,
        broken_count=broken_count,
        categories=categories_count,
        tools=entries,
    )


def to_summary(full: AuditFullReport) -> AuditSummary:
    """Réduit un AuditFullReport à son summary (format=summary)."""
    return AuditSummary(
        ts=full.ts,
        total_tools=full.total_tools,
        advertised_count=full.advertised_count,
        contract_callable_any_context=full.contract_callable_any_context,
        drift_count=full.drift_count,
        broken_count=full.broken_count,
        categories=full.categories,
    )


def filter_drift_only(full: AuditFullReport) -> AuditFullReport:
    """Filtre le report pour ne garder que les outils en drift."""
    drift_entries = [e for e in full.tools if e.drift_detected]
    return AuditFullReport(
        ts=full.ts,
        total_tools=full.total_tools,
        advertised_count=full.advertised_count,
        contract_callable_any_context=full.contract_callable_any_context,
        drift_count=full.drift_count,
        broken_count=full.broken_count,
        categories=full.categories,
        tools=drift_entries,
    )


def filter_by_tool_name(full: AuditFullReport, tool_name: str) -> AuditFullReport:
    """Filtre le report pour un seul outil (ou liste vide si inexistant)."""
    matching = [e for e in full.tools if e.name == tool_name]
    return AuditFullReport(
        ts=full.ts,
        total_tools=full.total_tools,
        advertised_count=full.advertised_count,
        contract_callable_any_context=full.contract_callable_any_context,
        drift_count=full.drift_count,
        broken_count=full.broken_count,
        categories=full.categories,
        tools=matching,
    )
