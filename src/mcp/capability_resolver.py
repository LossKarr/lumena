"""
Phase 22 — Capability Inventory + Resolver.

Composant pur testable, lecture seule stricte.

Doctrine Phase 22 :
  - Aucun singleton runtime, aucun cache, aucune route HTTP.
  - Aucune mutation, aucun call_tool, aucun install/activate/approve/reject/
    discover live.
  - Aucun import dur vers install_orchestrator, activation_service,
    client_factory, sandbox_runner.
  - Toutes les sources sont injectées par Protocols read-only optionnels.
  - Sortie sanitizée par whitelist stricte (aucune fuite intent raw,
    args, package_spec, notes, justification, tool_name_pattern,
    args_constraints, marker, token, paths absolus, raw entries,
    stack traces).
  - SEARCH_MCP retourné uniquement si l'intent est actionable.
  - CREATE_LOCAL_MCP JAMAIS produit en Phase 22 (réservé Phase 23).
  - policy_resolver.resolve(...) == None ⇒ policy_state="unresolved",
    PAS de BLOCKED_POLICY.
  - drift block uniquement ciblé sur (server_id, tool_name) du candidat.
  - runtime DEGRADED/UNKNOWN ⇒ warning evidence, PAS de block.
  - Audit local optionnel (DATA_DIR/mcp_capability_resolver/audit.jsonl)
    si audit_log_path fourni au constructeur. Sinon aucun fichier touché.
"""
from __future__ import annotations

import json
import re
import unicodedata
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Optional,
    Protocol,
    Tuple,
)


# ══════════════════════════════════════════════════════════════════════════════
# Constantes module — non exposées comme knobs Phase 22 (cf. doctrine v3)
# ══════════════════════════════════════════════════════════════════════════════

_MATCH_NATIVE_MIN = 0.3
_MATCH_MCP_MIN = 0.3
_MATCH_DECLARED_MIN = 0.5
_INTENT_MAX_CHARS = 256
_CANDIDATES_MAX = 20
_TOKENS_MAX = 200
_TOKEN_MIN_LEN = 3

# Stop-words minimaux FR/EN (gardés courts et déterministes).
_STOP_WORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for",
    "with", "by", "is", "are", "be", "this", "that", "it", "as",
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou",
    "a", "au", "aux", "en", "dans", "sur", "pour", "par", "avec",
    "est", "sont", "ce", "ces", "qui", "que", "quoi", "comment",
})

# Whitelist Phase 22 : verbes d'action outillée + surfaces concrètes.
_ACTIONABLE_VERBS_TOOLS: frozenset[str] = frozenset({
    # verbes d'action outillée (EN)
    "read", "write", "fetch", "get", "send", "search", "query",
    "list", "scrape", "parse", "download", "upload", "calculate",
    "compute", "analyze", "convert", "generate", "create", "delete",
    "update", "transform", "extract", "execute", "run", "open",
    "close", "save", "sync", "translate", "summarize", "find",
    "connect", "monitor", "watch",
    # Fix Z (Phase I-7) : verbes du cycle de vie MCP — sans eux,
    # « installe et active le MCP memory » donnait actionable=False
    # → no_capability_found → blocked, même pour un slug curated.
    "install", "installe", "installer", "installation",
    "active", "activer", "activate", "activation",
    "configure", "configurer", "setup",
    "use", "utilise", "utiliser",
    "add", "ajoute", "ajouter",
    "enable", "disable", "desactive", "desactiver", "remove",
    # verbes d'action outillée (FR)
    "lire", "ecrire", "envoyer", "chercher", "lister", "telecharger",
    "calculer", "analyser", "convertir", "generer", "creer",
    "supprimer", "modifier", "extraire", "executer", "lancer",
    "ouvrir", "sauvegarder", "synchroniser", "traduire", "resumer",
    "trouve", "trouver", "connecter", "surveiller", "monitorer",
    # surfaces outillées concrètes
    "file", "fichier", "folder", "dossier", "directory", "browser",
    "navigateur", "api", "endpoint", "url", "database", "sql",
    "table", "image", "video", "audio", "pdf", "doc", "docx", "xlsx",
    "json", "csv", "yaml", "xml", "email", "mail", "calendar",
    "calendrier", "github", "git", "repo", "issue", "ticket", "slack",
    "discord", "telegram", "whatsapp", "spotify", "notion", "shell",
    "command", "script", "log", "metric", "screenshot", "page", "site",
    "webhook",
    # Fix Z : « mcp » et « serveur » sont outillés par définition —
    # quelqu'un qui parle de MCP demande forcément une capacité.
    "mcp", "serveur", "server",
    # Fix Z : slugs des 17 KNOWN_MCPS curated. Seuls slack/github/
    # notion/discord y figuraient (par accident) — les 13 autres
    # rendaient l'intent non-actionable.
    "memory", "time", "sqlite", "postgres", "postgresql", "filesystem",
    "linear", "sentry", "tavily", "puppeteer", "gitlab", "brave",
    "everything", "drive", "gdrive",
})

# Whitelist sanitization Phase 22 — champs autorisés dans evidence.
_EVIDENCE_WHITELIST: frozenset[str] = frozenset({
    "intent_id",
    "created_at",
    "tool_count_native",
    "tool_count_mcp_active",
    "tool_count_mcp_installed",
    "tool_count_mcp_declared",
    "catalog_counts",
    "policies_resolved_count",
    "policies_blocked_count",
    "policies_unresolved_count",
    "approvals_pending_count_for_target",
    "runtime_health_for_target",
    "drift_overall",
    "match_score_top",
    "decision_reason_code",
    "sources_degraded",
    "actionable_intent",
})

# Codes de blocker autorisés Phase 22 (cf. plan v3 F.9).
_BLOCKER_CODES: frozenset[str] = frozenset({
    "policy_blocked",
    "trust_too_low_write",
    "trust_too_low_secrets",
    "runtime_unhealthy",
    "runtime_stopped_while_active",
    "runtime_quarantined",
    "drift_divergent",
    "approval_pending",
})


# ══════════════════════════════════════════════════════════════════════════════
# Protocols read-only — toutes optionnels (None autorisé dans Deps)
# ══════════════════════════════════════════════════════════════════════════════


class ToolRegistryReadLike(Protocol):
    def list_dynamic_handlers(self) -> List[str]: ...
    def is_dynamic_handler(self, name: str) -> bool: ...
    def get_dynamic_handler_provenance(
        self, name: str
    ) -> Optional[Dict[str, Any]]: ...
    def get_dynamic_handler_policy(self, name: str) -> Optional[Any]: ...
    def get_tools_schema(self) -> List[Dict[str, Any]]: ...


class CatalogReadLike(Protocol):
    def list_servers(self, include_removed: bool = False) -> List[Any]: ...
    def get_server(self, server_id: str) -> Optional[Any]: ...


class DiscoveryReadLike(Protocol):
    def iter_persisted_reports(
        self, server_id: Optional[str] = None
    ) -> Iterable[Dict[str, Any]]: ...


class PolicyResolverReadLike(Protocol):
    def resolve(self, server_id: str, tool_name: str) -> Optional[Any]: ...


class PolicyAttributorReadLike(Protocol):
    def attribute(self, tool: Any, *, trust_score: Optional[int]) -> Any: ...


class ApprovalQueueReadLike(Protocol):
    def list_pending(self) -> List[Any]: ...


class AutoApproveReadLike(Protocol):
    def list_patterns(self, profile: Optional[str] = None) -> List[Any]: ...


class RuntimeWatcherReadLike(Protocol):
    def list_persisted_snapshots(self) -> List[str]: ...
    def load_snapshot_from_disk(self, server_id: str) -> Optional[Any]: ...
    def list_watched_servers(self) -> List[str]: ...


class DriftReadLike(Protocol):
    def audit_summary(self) -> Any: ...
    def tool_entries(self) -> List[Dict[str, Any]]: ...


# ══════════════════════════════════════════════════════════════════════════════
# Modèles de données — immutables
# ══════════════════════════════════════════════════════════════════════════════


class CapabilityDecision(str, Enum):
    USE_NATIVE_TOOL = "use_native_tool"
    USE_ACTIVE_MCP_TOOL = "use_active_mcp_tool"
    ACTIVATE_INSTALLED_MCP = "activate_installed_mcp"
    INSTALL_DECLARED_MCP = "install_declared_mcp"
    SEARCH_MCP = "search_mcp"
    CREATE_LOCAL_MCP = "create_local_mcp"  # JAMAIS produit Phase 22
    BLOCKED_POLICY = "blocked_policy"
    BLOCKED_TRUST = "blocked_trust"
    BLOCKED_RUNTIME = "blocked_runtime"
    NEEDS_APPROVAL = "needs_approval"
    NO_CAPABILITY_FOUND = "no_capability_found"


@dataclass(frozen=True)
class ToolCandidate:
    kind: str
    tool_name: str
    server_id: Optional[str]
    catalog_status: Optional[str]
    trust_score: Optional[int]
    match_score: float
    policy_state: str


@dataclass(frozen=True)
class BlockerReport:
    blocker_code: str
    target_server_id: Optional[str]
    details_count: int


@dataclass(frozen=True)
class CapabilityResolverDeps:
    tool_registry: Optional[ToolRegistryReadLike] = None
    catalog: Optional[CatalogReadLike] = None
    discovery: Optional[DiscoveryReadLike] = None
    policy_resolver: Optional[PolicyResolverReadLike] = None
    policy_attributor: Optional[PolicyAttributorReadLike] = None
    approval_queue: Optional[ApprovalQueueReadLike] = None
    auto_approve: Optional[AutoApproveReadLike] = None
    runtime_watcher: Optional[RuntimeWatcherReadLike] = None
    drift: Optional[DriftReadLike] = None


@dataclass(frozen=True)
class CapabilityResolutionPlan:
    intent_id: str
    intent_query_sanitized: str
    decision: CapabilityDecision
    selected_candidate: Optional[ToolCandidate]
    candidates: Tuple[ToolCandidate, ...]
    blockers: Tuple[BlockerReport, ...]
    evidence: Dict[str, Any]
    created_at: str


# ══════════════════════════════════════════════════════════════════════════════
# Helpers déterministes
# ══════════════════════════════════════════════════════════════════════════════


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9_]+")


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_intent_id() -> str:
    return uuid.uuid4().hex


def _sanitize_intent(raw: Any) -> str:
    """NFC + strip controls + trim + truncate _INTENT_MAX_CHARS.

    Refuse les caractères de contrôle sauf \\t et \\n. Préserve les accents.
    """
    if not isinstance(raw, str):
        return ""
    normalized = unicodedata.normalize("NFC", raw)
    cleaned = _CONTROL_RE.sub("", normalized)
    cleaned = cleaned.strip()
    if len(cleaned) > _INTENT_MAX_CHARS:
        cleaned = cleaned[:_INTENT_MAX_CHARS]
    return cleaned


def _tokenize(text: str) -> set[str]:
    """NFC + lowercase + split + stop-words + min length + cap.

    Déterministe, zero LLM, zero dépendance externe.
    """
    if not isinstance(text, str) or not text:
        return set()
    norm = unicodedata.normalize("NFC", text).lower()
    # Décomposer accents pour matching FR/EN cross-token
    decomposed = unicodedata.normalize("NFKD", norm)
    ascii_form = "".join(
        ch for ch in decomposed if not unicodedata.combining(ch)
    )
    raw_tokens = _TOKEN_SPLIT_RE.split(ascii_form)
    tokens: set[str] = set()
    for tok in raw_tokens:
        if len(tok) < _TOKEN_MIN_LEN:
            continue
        if tok in _STOP_WORDS:
            continue
        tokens.add(tok)
        if len(tokens) >= _TOKENS_MAX:
            break
    return tokens


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return float(inter) / float(union) if union else 0.0


def _is_actionable_intent(intent_tokens: set[str]) -> bool:
    """True si au moins un token est dans la whitelist outillée."""
    if not intent_tokens:
        return False
    return bool(intent_tokens & _ACTIONABLE_VERBS_TOOLS)


_CAPABILITY_TAGS_MAX = 16


def derive_capability_tags(text: str) -> Tuple[str, ...]:
    """Phase I-8 (Fix AC) : extrait les tokens DISCRIMINANTS d'un intent.

    Les tokens du cycle de vie MCP (installe, active, mcp, serveur, slugs
    curated...) sont exclus : ils matcheraient n'importe quel intent MCP
    futur et provoqueraient des faux positifs entre entrées du catalogue.
    Ne restent que les tokens métier (ex: « utiliser un MCP météo » →
    ('meteo',)). Déterministe, déjà désaccentué par _tokenize (FR→ASCII).
    """
    tokens = _tokenize(text) - _ACTIONABLE_VERBS_TOOLS
    return tuple(sorted(tokens)[:_CAPABILITY_TAGS_MAX])


def _entry_capability_tags(entry: Any) -> set[str]:
    """Tags persistés d'une entry catalog (Fix AC), set vide si absents."""
    raw = getattr(entry, "capability_tags", None)
    if not isinstance(raw, (list, tuple)):
        return set()
    return {t for t in raw if isinstance(t, str) and t}


def _tags_match_score(intent_tokens: set[str], tags: set[str]) -> float:
    """Score de match intent ↔ capability_tags.

    Indépendant de la taille de l'intent (contrairement à Jaccard) : un
    seul token discriminant partagé (ex: « meteo ») suffit à reconnaître
    l'entrée. 1 token → 0.5 (= _MATCH_DECLARED_MIN), +0.05 par token
    supplémentaire, plafonné à 0.7.
    """
    if not intent_tokens or not tags:
        return 0.0
    inter = len(intent_tokens & tags)
    if inter <= 0:
        return 0.0
    return min(0.5 + 0.05 * (inter - 1), 0.7)


def _safe_call(fn, *args, **kwargs) -> Tuple[bool, Any]:
    """Wrapper try/except retournant (ok, value). Aucun raise.

    Phase 22 dégrade silencieusement les sources qui lèvent.
    """
    try:
        return True, fn(*args, **kwargs)
    except Exception:
        return False, None


def _extract_tool_schema_identity(entry: Any) -> Tuple[Optional[str], str]:
    """Extrait (name, description) d'un schema outil plat ou OpenAI function.

    Le vrai ToolRegistry expose get_tools_schema() au format OpenAI :
    {"type": "function", "function": {"name": ..., "description": ...}}.
    Certains tests Phase 22 utilisent l'ancien format plat :
    {"name": ..., "description": ...}. Le resolver accepte les deux formes,
    en lecture pure, sans mutation ni fallback dangereux.
    """
    if not isinstance(entry, dict):
        return None, ""

    name = entry.get("name")
    description = entry.get("description", "")
    if isinstance(name, str) and name:
        return name, description if isinstance(description, str) else ""

    function = entry.get("function")
    if isinstance(function, dict):
        name = function.get("name")
        description = function.get("description", "")
        if isinstance(name, str) and name:
            return name, description if isinstance(description, str) else ""

    return None, ""


def _status_str(entry: Any) -> Optional[str]:
    """Extrait status string d'un ServerEntry-like sans import dur."""
    status = getattr(entry, "status", None)
    if status is None:
        return None
    val = getattr(status, "value", None)
    if isinstance(val, str):
        return val.lower()
    if isinstance(status, str):
        return status.lower()
    return None


def _is_policy_explicitly_blocked(policy: Any) -> bool:
    """Inspecte une policy retournée par policy_resolver et détecte un
    blocage explicite. Refuse les cas ambigus (cf. doctrine F.9.a).
    """
    if policy is None:
        return False
    if getattr(policy, "blocked", False) is True:
        return True
    if getattr(policy, "deny", False) is True:
        return True
    decision_attr = getattr(policy, "decision", None)
    if isinstance(decision_attr, str):
        if decision_attr.lower() in {"block", "deny", "blocked"}:
            return True
    return False


def _attribution_is_trust_too_low(decision: Any) -> Optional[str]:
    """Inspecte une AttributionDecision et retourne le code de blocker
    trust si applicable, sinon None.
    """
    if decision is None:
        return None
    policy = getattr(decision, "policy", "_missing_")
    if policy is None:
        reason = getattr(decision, "reason", None)
        if isinstance(reason, str):
            low = reason.lower()
            if "trust" in low and "secret" in low:
                return "trust_too_low_secrets"
            if "trust" in low and "low" in low:
                return "trust_too_low_write"
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Discovery filesystem reader — implementation par défaut
# ══════════════════════════════════════════════════════════════════════════════


class FilesystemDiscoveryReader:
    """Implémentation par défaut de DiscoveryReadLike : lit les rapports
    persistés depuis un dossier `reports_dir` (Phase 17).

    Lecture seule stricte. Aucune mutation. Aucun appel `discover()` live.
    """

    def __init__(self, reports_dir: Path) -> None:
        if not isinstance(reports_dir, Path):
            raise TypeError("reports_dir must be a pathlib.Path")
        self._reports_dir = reports_dir

    def iter_persisted_reports(
        self, server_id: Optional[str] = None
    ) -> Iterable[Dict[str, Any]]:
        if not self._reports_dir.exists():
            return
        try:
            entries = list(self._reports_dir.iterdir())
        except OSError:
            return
        for path in entries:
            if not path.is_file():
                continue
            if path.suffix.lower() != ".json":
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
                data = json.loads(raw)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            if server_id is not None:
                rep_sid = data.get("server_id")
                if rep_sid != server_id:
                    continue
            yield data


# ══════════════════════════════════════════════════════════════════════════════
# CapabilityResolver — classe stateless (sauf deps), pas de cache
# ══════════════════════════════════════════════════════════════════════════════


class CapabilityResolver:
    """Phase 22 — calcule un CapabilityResolutionPlan en lecture seule.

    Aucun cache. Aucune mutation. Toutes les sources lues à chaque appel.
    """

    def __init__(
        self,
        deps: CapabilityResolverDeps,
        *,
        audit_log_path: Optional[Path] = None,
    ) -> None:
        if not isinstance(deps, CapabilityResolverDeps):
            raise TypeError(
                "deps must be a CapabilityResolverDeps instance"
            )
        if audit_log_path is not None and not isinstance(
            audit_log_path, Path
        ):
            raise TypeError("audit_log_path must be a pathlib.Path or None")
        self._deps = deps
        self._audit_log_path = audit_log_path

    # ── Public ─────────────────────────────────────────────────────────────

    def resolve(
        self,
        intent: str,
        *,
        caller_kind: str,
        profile: Optional[str] = None,
    ) -> CapabilityResolutionPlan:
        intent_sanitized = _sanitize_intent(intent)
        intent_tokens = _tokenize(intent_sanitized)
        intent_id = _new_intent_id()
        created_at = _now_utc_iso()
        sources_degraded: List[str] = []

        # ── 1. Lecture sources ────────────────────────────────────────────
        native_tools = self._read_native_tools(sources_degraded)
        dynamic_handlers = self._read_dynamic_handlers(sources_degraded)
        catalog_entries = self._read_catalog(sources_degraded)
        discovery_by_server = self._read_discovery(
            catalog_entries, sources_degraded
        )

        # ── 2. Construction candidats ─────────────────────────────────────
        candidates: List[ToolCandidate] = []
        candidates.extend(
            self._build_native_candidates(intent_tokens, native_tools)
        )
        candidates.extend(
            self._build_mcp_active_candidates(
                intent_tokens, dynamic_handlers, catalog_entries
            )
        )
        candidates.extend(
            self._build_mcp_installed_candidates(
                intent_tokens, catalog_entries, discovery_by_server
            )
        )
        candidates.extend(
            self._build_mcp_declared_candidates(
                intent_tokens, catalog_entries
            )
        )

        # Phase I-8 (Fix AT) : la mention textuelle EXACTE d'un server_id
        # dans l'intent prime sur tout matching heuristique. Observé runtime
        # 2026-06-12 10:22 : « installer duckduckgo-mcp-server pour chercher
        # actualité bitcoin » — le mot « bitcoin » (SUJET de la recherche)
        # a fait gagner le serveur ACTIF bitcoin-mcp via le fallback tags
        # (Fix AR, 0.5) contre l'entrée DECLARED duckduckgo-mcp-server
        # pourtant nommée en toutes lettres → ready_to_use mensonger au lieu
        # de l'install. Un sid écrit tel quel dans l'intent est un signal
        # d'intention explicite, pas une heuristique.
        candidates = self._apply_sid_mention_priority(
            intent_sanitized, candidates, catalog_entries, dynamic_handlers
        )

        # Tri décroissant par match_score, cap _CANDIDATES_MAX.
        candidates.sort(key=lambda c: c.match_score, reverse=True)
        candidates = candidates[:_CANDIDATES_MAX]

        # ── 3. Cascade décisionnelle ──────────────────────────────────────
        decision, selected = self._cascade_decision(
            candidates,
            intent_tokens,
            catalog_entries,
            sources_degraded,
        )

        # ── 4. Override blockers (sur le candidat sélectionné) ────────────
        blockers: List[BlockerReport] = []
        policies_resolved = 0
        policies_blocked = 0
        policies_unresolved = 0
        approvals_count = 0
        runtime_health = "unknown"
        drift_overall = "unknown"

        if selected is not None:
            (
                decision,
                blockers,
                policies_resolved,
                policies_blocked,
                policies_unresolved,
                selected,
                approvals_count,
                runtime_health,
                drift_overall,
            ) = self._apply_blockers(
                decision, selected, candidates, sources_degraded
            )
        else:
            # Toujours collecter drift overall pour evidence, même sans
            # candidat sélectionné.
            drift_overall = self._read_drift_overall(sources_degraded)

        # ── 5. Construction evidence sanitizée ────────────────────────────
        catalog_counts = self._compute_catalog_counts(catalog_entries)
        evidence: Dict[str, Any] = {
            "intent_id": intent_id,
            "created_at": created_at,
            "tool_count_native": len(native_tools),
            "tool_count_mcp_active": len(dynamic_handlers),
            "tool_count_mcp_installed": catalog_counts.get("installed", 0),
            "tool_count_mcp_declared": catalog_counts.get("declared", 0),
            "catalog_counts": catalog_counts,
            "policies_resolved_count": policies_resolved,
            "policies_blocked_count": policies_blocked,
            "policies_unresolved_count": policies_unresolved,
            "approvals_pending_count_for_target": approvals_count,
            "runtime_health_for_target": runtime_health,
            "drift_overall": drift_overall,
            "match_score_top": (
                candidates[0].match_score if candidates else 0.0
            ),
            "decision_reason_code": decision.value,
            "sources_degraded": sorted(set(sources_degraded)),
            "actionable_intent": _is_actionable_intent(intent_tokens),
        }
        # Sanitization finale par whitelist (paranoïa).
        evidence = {
            k: v for k, v in evidence.items() if k in _EVIDENCE_WHITELIST
        }

        # ── 6. Plan immuable ──────────────────────────────────────────────
        plan = CapabilityResolutionPlan(
            intent_id=intent_id,
            intent_query_sanitized=intent_sanitized,
            decision=decision,
            selected_candidate=selected,
            candidates=tuple(candidates),
            blockers=tuple(blockers),
            evidence=evidence,
            created_at=created_at,
        )

        # ── 7. Audit local optionnel ──────────────────────────────────────
        self._append_audit_if_configured(plan, caller_kind, profile)

        return plan

    # ── Lecture sources ────────────────────────────────────────────────────

    def _read_native_tools(
        self, sources_degraded: List[str]
    ) -> List[Dict[str, Any]]:
        """Retourne uniquement les tools natifs (handlers NON dynamiques)."""
        reg = self._deps.tool_registry
        if reg is None:
            sources_degraded.append("tool_registry")
            return []
        ok, schema = _safe_call(reg.get_tools_schema)
        if not ok or not isinstance(schema, list):
            sources_degraded.append("tool_registry")
            return []
        result: List[Dict[str, Any]] = []
        for entry in schema:
            name, description = _extract_tool_schema_identity(entry)
            if not name:
                continue
            # Filtrer dynamics (handlers MCP enregistrés) — natifs only ici.
            ok_dyn, is_dyn = _safe_call(reg.is_dynamic_handler, name)
            if ok_dyn and is_dyn is True:
                continue
            result.append({"name": name, "description": description})
        return result

    def _read_dynamic_handlers(
        self, sources_degraded: List[str]
    ) -> List[Dict[str, Any]]:
        """Retourne les handlers dynamiques (= MCP actifs côté ToolRegistry).

        Pour chacun, expose name, description (depuis schema si dispo),
        server_id (provenance), policy (objet brut conservé en interne
        pour cascade, jamais exposé en sortie).
        """
        reg = self._deps.tool_registry
        if reg is None:
            return []
        ok, names = _safe_call(reg.list_dynamic_handlers)
        if not ok or not isinstance(names, list):
            sources_degraded.append("tool_registry")
            return []
        # Construire une map name -> description à partir du schema (déjà lu
        # potentiellement, mais on relit ici pour rester simple — Phase 22
        # n'a pas de cache, donc relecture acceptable).
        ok_sch, schema = _safe_call(reg.get_tools_schema)
        desc_map: Dict[str, str] = {}
        if ok_sch and isinstance(schema, list):
            for entry in schema:
                nm, ds = _extract_tool_schema_identity(entry)
                if nm:
                    desc_map[nm] = ds
        result: List[Dict[str, Any]] = []
        for name in names:
            if not isinstance(name, str):
                continue
            ok_prov, prov = _safe_call(
                reg.get_dynamic_handler_provenance, name
            )
            server_id = None
            if ok_prov and isinstance(prov, dict):
                sid = prov.get("server_id")
                if isinstance(sid, str) and sid:
                    server_id = sid
            result.append({
                "name": name,
                "description": desc_map.get(name, ""),
                "server_id": server_id,
            })
        return result

    def _read_catalog(self, sources_degraded: List[str]) -> List[Any]:
        cat = self._deps.catalog
        if cat is None:
            sources_degraded.append("catalog")
            return []
        ok, entries = _safe_call(cat.list_servers, include_removed=False)
        if not ok or not isinstance(entries, list):
            sources_degraded.append("catalog")
            return []
        return entries

    def _read_discovery(
        self,
        catalog_entries: List[Any],
        sources_degraded: List[str],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Map server_id -> liste de dicts tools issus des reports."""
        disc = self._deps.discovery
        if disc is None:
            sources_degraded.append("discovery")
            return {}
        out: Dict[str, List[Dict[str, Any]]] = {}
        for entry in catalog_entries:
            sid = getattr(entry, "server_id", None)
            if not isinstance(sid, str) or not sid:
                continue
            ok, reports_iter = _safe_call(
                disc.iter_persisted_reports, sid
            )
            if not ok:
                # Pas d'append degraded ici si certains servers ont des
                # rapports et d'autres non — on dégrade silencieusement
                # par-server.
                continue
            tools_acc: List[Dict[str, Any]] = []
            try:
                for rep in reports_iter or []:
                    if not isinstance(rep, dict):
                        continue
                    tools = rep.get("tools")
                    if not isinstance(tools, list):
                        continue
                    for t in tools:
                        if not isinstance(t, dict):
                            continue
                        nm = t.get("name") or t.get("tool_name")
                        ds = t.get("description", "")
                        if isinstance(nm, str) and nm:
                            tools_acc.append({
                                "name": nm,
                                "description": ds
                                if isinstance(ds, str) else "",
                            })
            except Exception:
                continue
            if tools_acc:
                out[sid] = tools_acc
        return out

    # ── Construction candidats ─────────────────────────────────────────────

    def _build_native_candidates(
        self,
        intent_tokens: set[str],
        native_tools: List[Dict[str, Any]],
    ) -> List[ToolCandidate]:
        out: List[ToolCandidate] = []
        for tool in native_tools:
            score = _jaccard(
                intent_tokens,
                _tokenize(tool["name"] + " " + tool["description"]),
            )
            if score <= 0.0:
                continue
            out.append(ToolCandidate(
                kind="native",
                tool_name=tool["name"],
                server_id=None,
                catalog_status=None,
                trust_score=None,
                match_score=score,
                policy_state="not_applicable",
            ))
        return out

    # Phase I-8 (Fix AT) : score quasi-certain pour un sid écrit tel quel
    # dans l'intent. Sous le 1.0 d'un match parfait, au-dessus de tous
    # les scores heuristiques (jaccard, tags, fallback AR).
    _SID_MENTION_SCORE = 0.95

    def _apply_sid_mention_priority(
        self,
        intent_sanitized: str,
        candidates: List[ToolCandidate],
        catalog_entries: List[Any],
        dynamic_handlers: List[Dict[str, Any]],
    ) -> List[ToolCandidate]:
        """Booste les candidats des server_ids mentionnés TEXTUELLEMENT.

        Mention = le sid complet (ex. « duckduckgo-mcp-server ») apparaît
        comme sous-chaîne de l'intent normalisé (lowercase, désaccentué).
        « bitcoin » seul ne mentionne PAS « bitcoin-mcp ». Si une entrée
        mentionnée n'a produit aucun candidat, un candidat synthétique est
        créé selon son statut (jamais pour une ACTIVE sans handler
        enregistré — pas de promesse fantôme).
        """
        norm = unicodedata.normalize("NFKD", intent_sanitized.lower())
        norm = "".join(
            ch for ch in norm if not unicodedata.combining(ch)
        )
        mentioned: set[str] = set()
        status_by_sid: Dict[str, str] = {}
        for entry in catalog_entries:
            sid = getattr(entry, "server_id", None)
            if not isinstance(sid, str) or len(sid) < 3:
                continue
            if sid.lower() in norm:
                mentioned.add(sid)
                status_by_sid[sid] = _status_str(entry) or ""
        if not mentioned:
            return candidates

        active_sids_with_handlers = {
            h.get("server_id")
            for h in dynamic_handlers
            if isinstance(h.get("server_id"), str)
        }
        covered: set[str] = set()
        out: List[ToolCandidate] = []
        for cand in candidates:
            if cand.server_id in mentioned:
                covered.add(cand.server_id)
                if cand.match_score < self._SID_MENTION_SCORE:
                    cand = replace(
                        cand, match_score=self._SID_MENTION_SCORE
                    )
            out.append(cand)

        kind_by_status = {
            "active": "mcp_active",
            "installed": "mcp_installed",
            "declared": "mcp_declared",
        }
        for sid in mentioned - covered:
            status = status_by_sid.get(sid, "")
            kind = kind_by_status.get(status)
            if kind is None:
                continue
            if status == "active" and sid not in active_sids_with_handlers:
                continue
            entry = next(
                (e for e in catalog_entries
                 if getattr(e, "server_id", None) == sid), None
            )
            ts = getattr(entry, "trust_score", None) if entry else None
            out.append(ToolCandidate(
                kind=kind,
                tool_name=sid,
                server_id=sid,
                catalog_status=status,
                trust_score=ts if isinstance(ts, int) else None,
                match_score=self._SID_MENTION_SCORE,
                policy_state="unknown",
            ))
        return out

    def _build_mcp_active_candidates(
        self,
        intent_tokens: set[str],
        dynamic_handlers: List[Dict[str, Any]],
        catalog_entries: List[Any],
    ) -> List[ToolCandidate]:
        # Map server_id -> trust_score pour enrichir candidate.
        trust_map: Dict[str, Optional[int]] = {}
        for entry in catalog_entries:
            sid = getattr(entry, "server_id", None)
            if isinstance(sid, str):
                ts = getattr(entry, "trust_score", None)
                if isinstance(ts, int):
                    trust_map[sid] = ts
                else:
                    trust_map[sid] = None
        # Phase I-8 (Fix AU) : le fallback identité-serveur par TAGS pour
        # les ACTIFS (ex-Fix AR) est SUPPRIMÉ. Observé runtime 2026-06-12
        # 10:37 : le tag « bitcoin » du serveur actif bitcoin-mcp matchait
        # le SUJET de la demande (« recherche DuckDuckGo pour actualité
        # bitcoin ») et battait l'entrée DECLARED duckduckgo à installer —
        # un tag de domaine matche le sujet d'une requête, jamais le
        # service demandé. Le cas légitime d'AR (« utilise le mcp X ») est
        # couvert par la mention textuelle exacte du sid (Fix AT,
        # _apply_sid_mention_priority), qui booste les candidats tools
        # ci-dessous ou crée un candidat synthétique si nécessaire.
        out: List[ToolCandidate] = []
        for handler in dynamic_handlers:
            score = _jaccard(
                intent_tokens,
                _tokenize(handler["name"] + " " + handler["description"]),
            )
            if score <= 0.0:
                continue
            sid = handler.get("server_id")
            out.append(ToolCandidate(
                kind="mcp_active",
                tool_name=handler["name"],
                server_id=sid if isinstance(sid, str) else None,
                catalog_status="active",
                trust_score=trust_map.get(sid)
                if isinstance(sid, str) else None,
                match_score=score,
                policy_state="unknown",
            ))
        return out

    def _build_mcp_installed_candidates(
        self,
        intent_tokens: set[str],
        catalog_entries: List[Any],
        discovery_by_server: Dict[str, List[Dict[str, Any]]],
    ) -> List[ToolCandidate]:
        out: List[ToolCandidate] = []
        for entry in catalog_entries:
            status = _status_str(entry)
            if status != "installed":
                continue
            sid = getattr(entry, "server_id", None)
            if not isinstance(sid, str):
                continue
            ts = getattr(entry, "trust_score", None)
            ts_int = ts if isinstance(ts, int) else None
            tools = discovery_by_server.get(sid, [])
            had_match = False
            for tool in tools:
                score = _jaccard(
                    intent_tokens,
                    _tokenize(
                        tool["name"] + " " + tool.get("description", "")
                    ),
                )
                if score <= 0.0:
                    continue
                had_match = True
                out.append(ToolCandidate(
                    kind="mcp_installed",
                    tool_name=tool["name"],
                    server_id=sid,
                    catalog_status="installed",
                    trust_score=ts_int,
                    match_score=score,
                    policy_state="unknown",
                ))

            # Phase I-7 fix G : fallback curated KNOWN_MCPS.
            # Un MCP fraichement installé n'a pas encore de DiscoveryReport
            # (l'œuf et la poule : il faut le spawner pour lister ses tools).
            # Pour les MCPs curated officiels (KNOWN_MCPS), on génère un
            # candidat synthétique basé sur slug + aliases + display_name +
            # description. Sans ça, le resolver ne peut JAMAIS proposer
            # ACTIVATE_INSTALLED_MCP et l'install reste inutilisable.
            if not had_match:
                try:
                    from src.mcp.known_mcps import get_known_mcp  # noqa: WPS433
                    curated = get_known_mcp(sid)
                except Exception:  # noqa: BLE001
                    curated = None
                if curated is not None:
                    haystack = " ".join((
                        curated.slug,
                        getattr(curated, "display_name", "") or "",
                        getattr(curated, "description", "") or "",
                        *getattr(curated, "aliases", ()),
                    ))
                    score = _jaccard(intent_tokens, _tokenize(haystack))
                    if score > 0.0:
                        out.append(ToolCandidate(
                            kind="mcp_installed",
                            # Pas de tool concret encore — on passe le slug
                            # qui sera utilisé pour générer mcp_activate:<sid>.
                            tool_name=sid,
                            server_id=sid,
                            catalog_status="installed",
                            trust_score=ts_int,
                            match_score=score,
                            policy_state="unknown",
                        ))
                        had_match = True

            # Phase I-8 (Fix AC) : fallback capability_tags pour les
            # non-curated. Même œuf-et-poule que Fix G : un MCP installé
            # mais jamais activé n'a pas de DiscoveryReport. Les tags
            # capturés à la proposition catalog_add permettent au resolver
            # de proposer ACTIVATE_INSTALLED_MCP au lieu de relancer une
            # recherche réseau.
            if not had_match:
                score = _tags_match_score(
                    intent_tokens, _entry_capability_tags(entry)
                )
                if score > 0.0:
                    out.append(ToolCandidate(
                        kind="mcp_installed",
                        tool_name=sid,
                        server_id=sid,
                        catalog_status="installed",
                        trust_score=ts_int,
                        match_score=score,
                        policy_state="unknown",
                    ))
        return out

    def _build_mcp_declared_candidates(
        self,
        intent_tokens: set[str],
        catalog_entries: List[Any],
    ) -> List[ToolCandidate]:
        """Match sur display_name (Jaccard) + capability_tags (Fix AC).

        Avant I-8, seul le display_name était matché : une entrée DECLARED
        issue d'un intent FR (« météo ») avec un package npm anglais était
        invisible au resolver, qui relançait une recherche réseau → nouveau
        candidat → nouveau ticket (churn observé runtime 2026-06-11 00:13).
        """
        out: List[ToolCandidate] = []
        for entry in catalog_entries:
            status = _status_str(entry)
            if status != "declared":
                continue
            sid = getattr(entry, "server_id", None)
            if not isinstance(sid, str):
                continue
            display = getattr(entry, "display_name", None)
            if not isinstance(display, str) or not display:
                display = sid
            score = max(
                _jaccard(intent_tokens, _tokenize(display)),
                _tags_match_score(
                    intent_tokens, _entry_capability_tags(entry)
                ),
            )
            if score <= 0.0:
                continue
            ts = getattr(entry, "trust_score", None)
            ts_int = ts if isinstance(ts, int) else None
            out.append(ToolCandidate(
                kind="mcp_declared",
                tool_name=sid,  # pas de tool concret connu pour declared
                server_id=sid,
                catalog_status="declared",
                trust_score=ts_int,
                match_score=score,
                policy_state="unknown",
            ))
        return out

    # ── Cascade décisionnelle ──────────────────────────────────────────────

    def _cascade_decision(
        self,
        candidates: List[ToolCandidate],
        intent_tokens: set[str],
        catalog_entries: List[Any],
        sources_degraded: List[str],
    ) -> Tuple[CapabilityDecision, Optional[ToolCandidate]]:
        # Premier candidat qui dépasse le seuil de sa catégorie.
        for cand in candidates:
            if cand.kind == "native" and cand.match_score >= _MATCH_NATIVE_MIN:
                return CapabilityDecision.USE_NATIVE_TOOL, cand
            if (
                cand.kind == "mcp_active"
                and cand.match_score >= _MATCH_MCP_MIN
            ):
                return CapabilityDecision.USE_ACTIVE_MCP_TOOL, cand
            if (
                cand.kind == "mcp_installed"
                and cand.match_score >= _MATCH_MCP_MIN
            ):
                return CapabilityDecision.ACTIVATE_INSTALLED_MCP, cand
            if (
                cand.kind == "mcp_declared"
                and cand.match_score >= _MATCH_DECLARED_MIN
            ):
                return CapabilityDecision.INSTALL_DECLARED_MCP, cand

        # Aucun candidat ne dépasse les seuils → SEARCH_MCP ou NO_CAPABILITY.
        actionable = _is_actionable_intent(intent_tokens)
        mcp_source_available = (
            self._deps.catalog is not None
            or self._deps.discovery is not None
        )
        catalog_non_empty = bool(catalog_entries)
        if (
            actionable
            and mcp_source_available
            and catalog_non_empty
        ):
            return CapabilityDecision.SEARCH_MCP, None
        return CapabilityDecision.NO_CAPABILITY_FOUND, None

    # ── Blockers (override décision après cascade) ─────────────────────────

    def _apply_blockers(
        self,
        decision: CapabilityDecision,
        selected: ToolCandidate,
        candidates: List[ToolCandidate],
        sources_degraded: List[str],
    ) -> Tuple[
        CapabilityDecision,
        List[BlockerReport],
        int,  # policies_resolved
        int,  # policies_blocked
        int,  # policies_unresolved
        Optional[ToolCandidate],  # selected (potentiellement enrichi)
        int,  # approvals_count
        str,  # runtime_health
        str,  # drift_overall
    ]:
        blockers: List[BlockerReport] = []
        new_decision = decision
        policies_resolved = 0
        policies_blocked = 0
        policies_unresolved = 0
        approvals_count = 0
        runtime_health = "unknown"

        # ── (1) Quarantine catalog → blocker prioritaire ──────────────────
        catalog_quarantined = self._is_target_quarantined(selected)
        if catalog_quarantined:
            blockers.append(BlockerReport(
                blocker_code="runtime_quarantined",
                target_server_id=selected.server_id,
                details_count=1,
            ))
            new_decision = CapabilityDecision.BLOCKED_RUNTIME

        # ── (2) Policy resolver (cas explicite) ───────────────────────────
        policy_state, pr_added_blocker = self._inspect_policy(
            selected, sources_degraded
        )
        if policy_state == "blocked_policy_known":
            policies_blocked += 1
            blockers.append(BlockerReport(
                blocker_code="policy_blocked",
                target_server_id=selected.server_id,
                details_count=1,
            ))
            if new_decision != CapabilityDecision.BLOCKED_RUNTIME:
                new_decision = CapabilityDecision.BLOCKED_POLICY
        elif policy_state == "allowed":
            policies_resolved += 1
        elif policy_state == "unresolved":
            policies_unresolved += 1
        # not_applicable → ne pas incrémenter

        # ── (3) Trust gate via PolicyAttributor (sans seuil hardcodé) ────
        trust_blocker_code = self._inspect_trust(
            selected, sources_degraded
        )
        if trust_blocker_code is not None:
            blockers.append(BlockerReport(
                blocker_code=trust_blocker_code,
                target_server_id=selected.server_id,
                details_count=1,
            ))
            if new_decision not in (
                CapabilityDecision.BLOCKED_RUNTIME,
                CapabilityDecision.BLOCKED_POLICY,
            ):
                new_decision = CapabilityDecision.BLOCKED_TRUST

        # ── (4) Runtime health (snapshot disque) ──────────────────────────
        runtime_health, runtime_blocker = self._inspect_runtime(
            selected, sources_degraded
        )
        if runtime_blocker is not None:
            blockers.append(runtime_blocker)
            if new_decision not in (
                CapabilityDecision.BLOCKED_POLICY,
            ):
                # runtime override policy ? doctrine v3 : runtime > policy.
                # Mais on ne dégrade pas un BLOCKED_POLICY déjà acquis.
                # Garde la priorité : RUNTIME > POLICY > TRUST > APPROVAL.
                new_decision = CapabilityDecision.BLOCKED_RUNTIME

        # ── (5) Drift ciblé tool_name ─────────────────────────────────────
        drift_overall, drift_blocker = self._inspect_drift(
            selected, sources_degraded
        )
        if drift_blocker is not None:
            blockers.append(drift_blocker)
            if new_decision not in (
                CapabilityDecision.BLOCKED_POLICY,
            ):
                new_decision = CapabilityDecision.BLOCKED_RUNTIME

        # ── (6) Approvals pending ─────────────────────────────────────────
        approvals_count, approval_blocker = self._inspect_approvals(
            selected, decision, sources_degraded
        )
        if approval_blocker is not None:
            blockers.append(approval_blocker)
            # Approval n'écrase qu'une décision non-bloquante.
            if new_decision not in (
                CapabilityDecision.BLOCKED_RUNTIME,
                CapabilityDecision.BLOCKED_POLICY,
                CapabilityDecision.BLOCKED_TRUST,
            ):
                new_decision = CapabilityDecision.NEEDS_APPROVAL

        # Recréer candidate avec policy_state mis à jour (frozen=True).
        if policy_state and policy_state != selected.policy_state:
            selected = ToolCandidate(
                kind=selected.kind,
                tool_name=selected.tool_name,
                server_id=selected.server_id,
                catalog_status=selected.catalog_status,
                trust_score=selected.trust_score,
                match_score=selected.match_score,
                policy_state=policy_state,
            )

        return (
            new_decision,
            blockers,
            policies_resolved,
            policies_blocked,
            policies_unresolved,
            selected,
            approvals_count,
            runtime_health,
            drift_overall,
        )

    def _is_target_quarantined(self, selected: ToolCandidate) -> bool:
        cat = self._deps.catalog
        if cat is None or not selected.server_id:
            return False
        ok, entry = _safe_call(cat.get_server, selected.server_id)
        if not ok or entry is None:
            return False
        return _status_str(entry) == "quarantined"

    def _inspect_policy(
        self,
        selected: ToolCandidate,
        sources_degraded: List[str],
    ) -> Tuple[str, bool]:
        """Retourne (policy_state, added_blocker_flag).

        policy_state ∈ {"not_applicable", "allowed", "unresolved",
                        "blocked_policy_known"}.
        """
        if selected.kind == "native":
            return "not_applicable", False
        pr = self._deps.policy_resolver
        if pr is None:
            return "unresolved", False
        if not selected.server_id:
            return "unresolved", False
        ok, result = _safe_call(
            pr.resolve, selected.server_id, selected.tool_name
        )
        if not ok:
            sources_degraded.append("policy_resolver")
            return "unresolved", False
        if result is None:
            return "unresolved", False
        if _is_policy_explicitly_blocked(result):
            return "blocked_policy_known", True
        return "allowed", False

    def _inspect_trust(
        self,
        selected: ToolCandidate,
        sources_degraded: List[str],
    ) -> Optional[str]:
        """Délègue à PolicyAttributor.attribute(...) si disponible.

        Phase 22 ne hardcode AUCUN seuil. Sans attributor → trust check
        skipped (None).
        """
        attr = self._deps.policy_attributor
        if attr is None:
            return None
        if selected.kind == "native":
            return None
        if not selected.server_id:
            return None
        # Construire un ToolMetadata-like via une simple structure dict-like
        # qui expose les attributs lus par PolicyAttributor. Phase 22 ne
        # peut pas importer ToolMetadata sans coupler. On utilise un
        # SimpleNamespace.
        from types import SimpleNamespace
        tool_obj = SimpleNamespace(
            server_id=selected.server_id,
            tool_name=selected.tool_name,
            description="",  # Phase 22 n'a pas accès au schema MCP raw
            input_schema={},
        )
        ok, decision = _safe_call(
            attr.attribute, tool_obj, trust_score=selected.trust_score
        )
        if not ok:
            sources_degraded.append("policy_attributor")
            return None
        return _attribution_is_trust_too_low(decision)

    def _inspect_runtime(
        self,
        selected: ToolCandidate,
        sources_degraded: List[str],
    ) -> Tuple[str, Optional[BlockerReport]]:
        rw = self._deps.runtime_watcher
        if rw is None or not selected.server_id:
            return "unknown", None
        ok, snap = _safe_call(
            rw.load_snapshot_from_disk, selected.server_id
        )
        if not ok:
            sources_degraded.append("runtime_watcher")
            return "unknown", None
        if snap is None:
            return "unknown", None
        # Health
        health = getattr(snap, "health", None)
        health_str = ""
        if hasattr(health, "value"):
            val = health.value
            if isinstance(val, str):
                health_str = val.upper()
        elif isinstance(health, str):
            health_str = health.upper()
        if health_str in ("CRASH_LOOP", "UNHEALTHY"):
            return "fail", BlockerReport(
                blocker_code="runtime_unhealthy",
                target_server_id=selected.server_id,
                details_count=1,
            )
        if health_str == "DEGRADED":
            return "warn", None
        if health_str == "HEALTHY":
            health_evidence = "ok"
        elif health_str == "UNKNOWN" or not health_str:
            health_evidence = "unknown"
        else:
            health_evidence = "unknown"
        # Process state stopped + catalog ACTIVE → block
        proc_state = getattr(snap, "process_state", None)
        if (
            selected.catalog_status == "active"
            and isinstance(proc_state, str)
            and proc_state.lower() == "stopped"
        ):
            return health_evidence, BlockerReport(
                blocker_code="runtime_stopped_while_active",
                target_server_id=selected.server_id,
                details_count=1,
            )
        return health_evidence, None

    def _inspect_drift(
        self,
        selected: ToolCandidate,
        sources_degraded: List[str],
    ) -> Tuple[str, Optional[BlockerReport]]:
        drift = self._deps.drift
        if drift is None:
            return "unknown", None
        # Overall summary
        ok_sum, summary = _safe_call(drift.audit_summary)
        if not ok_sum:
            sources_degraded.append("drift")
            return "unknown", None
        drift_count = getattr(summary, "drift_count", 0)
        has_drift_attr = getattr(summary, "has_drift", False)
        overall = "divergent" if (
            (isinstance(drift_count, int) and drift_count > 0)
            or has_drift_attr is True
        ) else "ok"
        if overall == "ok":
            return overall, None
        # Ciblage strict tool_name
        ok_entries, entries = _safe_call(drift.tool_entries)
        if not ok_entries:
            # Drift global mais impossible de cibler → pas de block
            return overall, None
        if not isinstance(entries, list):
            return overall, None
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            nm = entry.get("tool_name")
            status = entry.get("drift_status")
            if (
                nm == selected.tool_name
                and isinstance(status, str)
                and status.lower() in ("drift", "divergent")
            ):
                return overall, BlockerReport(
                    blocker_code="drift_divergent",
                    target_server_id=selected.server_id,
                    details_count=1,
                )
        return overall, None

    def _read_drift_overall(self, sources_degraded: List[str]) -> str:
        drift = self._deps.drift
        if drift is None:
            return "unknown"
        ok_sum, summary = _safe_call(drift.audit_summary)
        if not ok_sum:
            sources_degraded.append("drift")
            return "unknown"
        drift_count = getattr(summary, "drift_count", 0)
        has_drift_attr = getattr(summary, "has_drift", False)
        if (isinstance(drift_count, int) and drift_count > 0) or (
            has_drift_attr is True
        ):
            return "divergent"
        return "ok"

    def _inspect_approvals(
        self,
        selected: ToolCandidate,
        original_decision: CapabilityDecision,
        sources_degraded: List[str],
    ) -> Tuple[int, Optional[BlockerReport]]:
        aq = self._deps.approval_queue
        if aq is None or not selected.server_id:
            return 0, None
        ok, pending = _safe_call(aq.list_pending)
        if not ok:
            sources_degraded.append("approval_queue")
            return 0, None
        if not isinstance(pending, list):
            return 0, None
        count = 0
        for ticket in pending:
            tsid = getattr(ticket, "server_id", None)
            if tsid == selected.server_id:
                count += 1
            else:
                # Certains tickets exposent target_server_id ou args
                # chiffrés ; Phase 22 reste passive — getattr seulement.
                alt = getattr(ticket, "target_server_id", None)
                if alt == selected.server_id:
                    count += 1
        if count == 0:
            return 0, None
        # Seules les décisions impliquant une mutation justifient un
        # NEEDS_APPROVAL Phase 22 :
        if original_decision in (
            CapabilityDecision.ACTIVATE_INSTALLED_MCP,
            CapabilityDecision.INSTALL_DECLARED_MCP,
        ):
            return count, BlockerReport(
                blocker_code="approval_pending",
                target_server_id=selected.server_id,
                details_count=count,
            )
        return count, None

    # ── Catalog counts ─────────────────────────────────────────────────────

    def _compute_catalog_counts(
        self, catalog_entries: List[Any]
    ) -> Dict[str, int]:
        counts = {
            "declared": 0,
            "installed": 0,
            "active": 0,
            "quarantined": 0,
            "removed": 0,
        }
        for entry in catalog_entries:
            st = _status_str(entry)
            if st in counts:
                counts[st] += 1
        return counts

    # ── Audit local optionnel ──────────────────────────────────────────────

    def _append_audit_if_configured(
        self,
        plan: CapabilityResolutionPlan,
        caller_kind: str,
        profile: Optional[str],
    ) -> None:
        if self._audit_log_path is None:
            return
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        event = {
            "ts": plan.created_at,
            "event": "resolve_completed",
            "phase": "22",
            "intent_id": plan.intent_id,
            "decision": plan.decision.value,
            "caller_kind": str(caller_kind)[:32] if isinstance(
                caller_kind, str
            ) else "unknown",
            "profile": str(profile)[:64] if isinstance(profile, str) else None,
            "candidates_count": len(plan.candidates),
            "blockers_count": len(plan.blockers),
            "actionable_intent": plan.evidence.get(
                "actionable_intent", False
            ),
            "match_score_top": plan.evidence.get("match_score_top", 0.0),
            "sources_degraded_count": len(
                plan.evidence.get("sources_degraded", [])
            ),
        }
        try:
            line = json.dumps(event, ensure_ascii=False)
            with self._audit_log_path.open(
                "a", encoding="utf-8"
            ) as fh:
                fh.write(line + "\n")
        except OSError:
            return
