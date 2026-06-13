"""
Phase 23 — MCP Search + Create Proposal.

Composant pur testable, lecture seule stricte.

Doctrine Phase 23 :
  - Phase de proposition, JAMAIS d'exécution.
  - Aucune install, aucune activation, aucun call_tool,
    aucun enregistrement dynamique de handler, aucun lancement de
    processus externe, aucune mutation runtime, aucun branchement
    ReAct, aucune mise en file ApprovalQueue, aucune mutation catalog.
  - Aucun import dur vers install_orchestrator, activation_service,
    client_factory, sandbox_runner, discovery (live).
  - Aucun import au module-level de clients HTTP tiers.
  - Sources réseau gated par network_enabled flag explicite.
  - Phase 23 v1 ne fournit que des sources offline + StubNetworkSource ;
    aucune vraie source réseau (Npm/Pypi/GitHub) en v1.
  - package_spec compatible Phase 14 : npm:NAME | npm:@scope/NAME |
    pypi:NAME | local:slug — JAMAIS @VERSION dans le spec.
    version séparée. github:/file: refusés en v1.
  - package_transport ∈ {npm, pypi, local} (Phase 18).
  - mcp_transport_hint ∈ {stdio, sse, http, unknown} (info MCP only).
  - PROPOSE_LOCAL_CREATE ⇒ catalog_proposal = None (création locale
    pas encore implémentée hors Phase 23).
  - NEEDS_APPROVAL garde catalog_proposal peuplé + requires_approval=True.
  - Description externe (search) = hash only. Description template
    interne (whitelist module) = autorisée. input_schema raw = jamais
    exposé, hash only.
  - Sortie sanitizée par whitelist stricte.
  - Audit local optionnel (DATA_DIR/mcp_proposal_planner/audit.jsonl).
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
)


# ══════════════════════════════════════════════════════════════════════════════
# Constantes module
# ══════════════════════════════════════════════════════════════════════════════

_INTENT_MAX_CHARS = 256
_TOKENS_MAX = 200
_TOKEN_MIN_LEN = 3
_SEARCH_RESULTS_MAX = 20
_TOOLS_HINT_MAX = 50
_PACKAGE_NAME_MAX = 128
_PACKAGE_SPEC_MAX = 256
_VERSION_MAX = 64
_TEMPLATE_DESCRIPTION_MAX = 200
_DESCRIPTION_HASH_LEN = 12
_INPUT_SCHEMA_HASH_LEN = 12

_MIN_PRE_SCORE_FOR_PROPOSAL = 40
_DOWNLOADS_SATURATION = 50_000
_RECENT_PUBLISH_DAYS = 365

_PACKAGE_TRANSPORTS: frozenset[str] = frozenset({"npm", "pypi", "local"})
_MCP_TRANSPORT_HINTS: frozenset[str] = frozenset(
    {"stdio", "sse", "http", "unknown"}
)

_STOP_WORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for",
    "with", "by", "is", "are", "be", "this", "that", "it", "as",
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou",
    "a", "au", "aux", "en", "dans", "sur", "pour", "par", "avec",
    "est", "sont", "ce", "ces", "qui", "que", "quoi", "comment",
})

# Helper local Phase 23 — équivalent fonctionnel à Phase 22 mais
# autonome (test de cohérence dans tests/mcp/test_proposal_planner.py).
_PHASE23_ACTIONABLE_TOKENS: frozenset[str] = frozenset({
    "read", "write", "fetch", "get", "send", "search", "query",
    "list", "scrape", "parse", "download", "upload", "calculate",
    "compute", "analyze", "convert", "generate", "create", "delete",
    "update", "transform", "extract", "execute", "run", "open",
    "close", "save", "sync", "translate", "summarize", "find",
    "connect", "monitor", "watch",
    "lire", "ecrire", "envoyer", "chercher", "lister", "telecharger",
    "calculer", "analyser", "convertir", "generer", "creer",
    "supprimer", "modifier", "extraire", "executer", "lancer",
    "ouvrir", "sauvegarder", "synchroniser", "traduire", "resumer",
    "trouve", "trouver", "connecter", "surveiller", "monitorer",
    "file", "fichier", "folder", "dossier", "directory", "browser",
    "navigateur", "api", "endpoint", "url", "database", "sql",
    "table", "image", "video", "audio", "pdf", "doc", "docx", "xlsx",
    "json", "csv", "yaml", "xml", "email", "mail", "calendar",
    "calendrier", "github", "git", "repo", "issue", "ticket", "slack",
    "discord", "telegram", "whatsapp", "spotify", "notion", "shell",
    "command", "script", "log", "metric", "screenshot", "page", "site",
    "webhook",
})

# Whitelist sanitization Phase 23 — champs autorisés dans evidence.
_EVIDENCE_WHITELIST: frozenset[str] = frozenset({
    "proposal_id",
    "created_at",
    "sources_consulted",
    "network_sources_enabled",
    "search_results_count",
    "search_results_filtered_count",
    "top_pre_score",
    "min_pre_score_required",
    "catalog_race_detected",
    "creation_complexity_estimate",
    "creation_rationale_code",
    "actionable_intent",
    "decision_reason_code",
    "sources_degraded",
    "package_transport_top",
})

# Codes de blocker autorisés Phase 23.
_BLOCKER_CODES: frozenset[str] = frozenset({
    "network_source_requires_approval",
    "creation_security_sensitive",
    "creation_intent_too_vague",
    "all_candidates_below_threshold",
    "catalog_lookup_failed",
})

_RATIONALE_CODES_CATALOG: frozenset[str] = frozenset({
    "existing_search_match",
    "local_creation_existing_package",
})

_RATIONALE_CODES_CREATION: frozenset[str] = frozenset({
    "matched_templates",
    "intent_too_vague",
    "security_sensitive",
})

_COMPLEXITY_LEVELS: frozenset[str] = frozenset({
    "low", "medium", "high", "refuse",
})

_RISK_LEVELS: frozenset[str] = frozenset({
    "low", "medium", "high",
})


# ── Regex Phase 14 compatibility ─────────────────────────────────────────────

_RE_NPM_SCOPED = re.compile(r"^npm:@[a-zA-Z0-9_\-]+/[a-zA-Z0-9_\-\.]+$")
_RE_NPM_UNSCOPED = re.compile(r"^npm:[a-zA-Z0-9_\-\.]+$")
_RE_PYPI = re.compile(r"^pypi:[a-zA-Z0-9_\-\.]+$")
_RE_LOCAL = re.compile(r"^local:[a-zA-Z0-9_\-]+$")

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


# ══════════════════════════════════════════════════════════════════════════════
# Helpers déterministes
# ══════════════════════════════════════════════════════════════════════════════


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_proposal_id() -> str:
    return uuid.uuid4().hex


def _sanitize_intent(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    normalized = unicodedata.normalize("NFC", raw)
    cleaned = _CONTROL_RE.sub("", normalized).strip()
    if len(cleaned) > _INTENT_MAX_CHARS:
        cleaned = cleaned[:_INTENT_MAX_CHARS]
    return cleaned


def _tokenize(text: str) -> set[str]:
    if not isinstance(text, str) or not text:
        return set()
    norm = unicodedata.normalize("NFC", text).lower()
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


def _phase23_is_actionable_intent(tokens: set[str]) -> bool:
    """Helper LOCAL Phase 23. Volontairement séparé de Phase 22.

    Le test de cohérence Phase 22 ↔ Phase 23 vit dans
    tests/mcp/test_proposal_planner.py et vérifie l'équivalence
    sur un jeu d'intents de référence.
    """
    if not tokens:
        return False
    return bool(tokens & _PHASE23_ACTIONABLE_TOKENS)


def _description_hash(text: Any) -> str:
    if not isinstance(text, str):
        text = ""
    h = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return h[:_DESCRIPTION_HASH_LEN]


def _input_schema_hash(schema: Any) -> str:
    try:
        serialized = json.dumps(
            schema, sort_keys=True, ensure_ascii=False
        )
    except Exception:
        serialized = ""
    h = hashlib.sha256(serialized.encode("utf-8", errors="replace")).hexdigest()
    return h[:_INPUT_SCHEMA_HASH_LEN]


def _is_valid_package_spec_phase14(spec: Any) -> bool:
    """Phase 14 compat : npm:NAME | npm:@scope/NAME | pypi:NAME | local:slug.

    JAMAIS @VERSION. JAMAIS slash dans pypi:/local:.
    github:/file: refusés.
    """
    if not isinstance(spec, str) or not spec:
        return False
    if len(spec) > _PACKAGE_SPEC_MAX:
        return False
    if "@" in spec.split(":", 1)[-1] and not spec.startswith("npm:@"):
        # @ autorisé seulement comme préfixe de scope npm
        return False
    if _RE_NPM_SCOPED.match(spec):
        return True
    if _RE_NPM_UNSCOPED.match(spec):
        return True
    if _RE_PYPI.match(spec):
        return True
    if _RE_LOCAL.match(spec):
        return True
    return False


def _is_valid_package_transport(t: Any) -> bool:
    return isinstance(t, str) and t in _PACKAGE_TRANSPORTS


def _is_valid_mcp_transport_hint(t: Any) -> bool:
    return isinstance(t, str) and t in _MCP_TRANSPORT_HINTS


def _safe_call(fn, *args, **kwargs) -> Tuple[bool, Any]:
    try:
        return True, fn(*args, **kwargs)
    except Exception:
        return False, None


def _compute_pre_score(meta: Dict[str, Any]) -> int:
    """0..100, déterministe, zero call live.

    Pondération Phase 23 v2 :
      - downloads_count    : 15 pts (saturée à 50k)
      - has_repo           : 15
      - has_license        : 15
      - last_publish_recent: 15
      - package_transport_known : 10
      - mcp_transport_hint != "unknown" : 10
      - source == "curated": 20
    """
    score = 0

    dc = meta.get("downloads_count")
    if isinstance(dc, int) and dc > 0:
        ratio = min(dc, _DOWNLOADS_SATURATION) / _DOWNLOADS_SATURATION
        score += int(round(15 * ratio))

    if meta.get("has_repo") is True:
        score += 15
    if meta.get("has_license") is True:
        score += 15

    last_pub = meta.get("last_publish_date")
    if isinstance(last_pub, str) and last_pub:
        try:
            s = last_pub
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            delta_days = (datetime.now(timezone.utc) - dt).days
            if 0 <= delta_days <= _RECENT_PUBLISH_DAYS:
                score += 15
        except Exception:
            pass

    pt = meta.get("package_transport")
    if _is_valid_package_transport(pt):
        score += 10

    mth = meta.get("mcp_transport_hint")
    if (
        isinstance(mth, str)
        and mth in _MCP_TRANSPORT_HINTS
        and mth != "unknown"
    ):
        score += 10

    if meta.get("source") == "curated":
        score += 20

    return max(0, min(score, 100))


# ══════════════════════════════════════════════════════════════════════════════
# Protocols read-only
# ══════════════════════════════════════════════════════════════════════════════


class MCPSearchSourceLike(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def is_network(self) -> bool: ...
    def search(
        self, query_tokens: set[str], *, limit: int
    ) -> List[Dict[str, Any]]: ...


class CatalogLookupLike(Protocol):
    def find_by_package_spec(self, package_spec: str) -> Optional[str]: ...
    def find_by_server_id(self, server_id: str) -> Optional[Any]: ...


# ══════════════════════════════════════════════════════════════════════════════
# Modèles immutables
# ══════════════════════════════════════════════════════════════════════════════


class MCPProposalDecision(str, Enum):
    USE_EXISTING_CANDIDATE = "use_existing_candidate"
    PROPOSE_CATALOG_DECLARED = "propose_catalog_declared"
    PROPOSE_LOCAL_CREATE = "propose_local_create"
    NO_SAFE_CANDIDATE = "no_safe_candidate"
    NEEDS_APPROVAL = "needs_approval"


@dataclass(frozen=True)
class MCPSearchResult:
    source: str
    package_name: str
    package_spec: str
    version: str
    package_transport: str
    mcp_transport_hint: str
    description_hash: str
    tools_hint: Tuple[str, ...]
    trust_pre_score: int
    license_id: Optional[str]


@dataclass(frozen=True)
class ToolTemplateProposal:
    tool_name: str
    description: str
    input_schema_hash: str
    risk_level: str


@dataclass(frozen=True)
class MCPCreationProposal:
    suggested_server_id: str
    suggested_display_name: str
    suggested_tools: Tuple[ToolTemplateProposal, ...]
    rationale_code: str
    complexity_estimate: str


@dataclass(frozen=True)
class CatalogProposal:
    proposed_server_id: str
    proposed_display_name: str
    proposed_package_spec: str
    proposed_version: str
    proposed_package_transport: str
    proposed_mcp_transport_hint: str
    proposed_trust_score_set: Optional[int]
    rationale_code: str
    requires_approval: bool
    target_status_on_add: str


@dataclass(frozen=True)
class MCPProposalPlanBlocker:
    blocker_code: str
    details_count: int


@dataclass(frozen=True)
class MCPProposalPlannerDeps:
    sources: Tuple[MCPSearchSourceLike, ...] = ()
    catalog_lookup: Optional[CatalogLookupLike] = None


@dataclass(frozen=True)
class MCPProposalPlan:
    proposal_id: str
    intent_query_sanitized: str
    decision: MCPProposalDecision
    search_results: Tuple[MCPSearchResult, ...]
    creation_proposal: Optional[MCPCreationProposal]
    catalog_proposal: Optional[CatalogProposal]
    blockers: Tuple[MCPProposalPlanBlocker, ...]
    evidence: Dict[str, Any]
    created_at: str


# ══════════════════════════════════════════════════════════════════════════════
# Templates Phase 23 v1 — whitelist interne, descriptions safe
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _ToolTemplate:
    match_tokens: frozenset[str]
    tool_name: str
    description: str
    input_schema: Dict[str, Any]
    risk_level: str


def _mk_template(
    match_tokens: set[str],
    tool_name: str,
    description: str,
    input_schema: Dict[str, Any],
    risk_level: str,
) -> _ToolTemplate:
    assert risk_level in _RISK_LEVELS
    assert len(description) <= _TEMPLATE_DESCRIPTION_MAX
    return _ToolTemplate(
        match_tokens=frozenset(match_tokens),
        tool_name=tool_name,
        description=description,
        input_schema=input_schema,
        risk_level=risk_level,
    )


_TOOL_TEMPLATES: Tuple[_ToolTemplate, ...] = (
    _mk_template(
        {"email", "mail", "send", "envoyer"},
        "send_email",
        "Send an email with subject and body.",
        {
            "type": "object",
            "required": ["to", "subject", "body"],
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
        },
        "low",
    ),
    _mk_template(
        {"weather", "forecast", "meteo"},
        "get_weather",
        "Fetch current weather for a location.",
        {
            "type": "object",
            "required": ["location"],
            "properties": {
                "location": {"type": "string"},
                "units": {"type": "string"},
            },
        },
        "low",
    ),
    _mk_template(
        {"github", "issue", "create"},
        "github_create_issue",
        "Create a GitHub issue in a repository.",
        {
            "type": "object",
            "required": ["repo", "title"],
            "properties": {
                "repo": {"type": "string"},
                "title": {"type": "string"},
                "body": {"type": "string"},
            },
        },
        "medium",
    ),
    _mk_template(
        {"slack", "message", "send"},
        "slack_send_message",
        "Send a message to a Slack channel.",
        {
            "type": "object",
            "required": ["channel", "text"],
            "properties": {
                "channel": {"type": "string"},
                "text": {"type": "string"},
            },
        },
        "medium",
    ),
    _mk_template(
        {"notion", "page", "create"},
        "notion_create_page",
        "Create a new Notion page.",
        {
            "type": "object",
            "required": ["parent", "title"],
            "properties": {
                "parent": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
            },
        },
        "medium",
    ),
    _mk_template(
        {"calendar", "event", "create", "calendrier"},
        "calendar_create_event",
        "Create a calendar event.",
        {
            "type": "object",
            "required": ["start", "title"],
            "properties": {
                "start": {"type": "string"},
                "end": {"type": "string"},
                "title": {"type": "string"},
            },
        },
        "low",
    ),
    _mk_template(
        {"database", "sql", "query"},
        "db_query",
        "Run a parameterized SQL query.",
        {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "params": {"type": "array"},
            },
        },
        "high",
    ),
    _mk_template(
        {"api", "fetch", "url"},
        "api_fetch",
        "Fetch a JSON response from a HTTP endpoint.",
        {
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string"},
                "method": {"type": "string"},
            },
        },
        "medium",
    ),
    _mk_template(
        {"translate", "traduire"},
        "translate_text",
        "Translate text between languages.",
        {
            "type": "object",
            "required": ["text", "target_lang"],
            "properties": {
                "text": {"type": "string"},
                "target_lang": {"type": "string"},
            },
        },
        "low",
    ),
    _mk_template(
        {"summarize", "resumer"},
        "summarize_text",
        "Summarize a piece of text.",
        {
            "type": "object",
            "required": ["text"],
            "properties": {
                "text": {"type": "string"},
                "max_words": {"type": "integer"},
            },
        },
        "low",
    ),
    _mk_template(
        {"scrape", "page", "site", "browser"},
        "scrape_page",
        "Extract text content from a web page.",
        {
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string"},
            },
        },
        "medium",
    ),
    _mk_template(
        {"webhook", "send"},
        "webhook_post",
        "POST a JSON payload to a webhook URL.",
        {
            "type": "object",
            "required": ["url", "payload"],
            "properties": {
                "url": {"type": "string"},
                "payload": {"type": "object"},
            },
        },
        "medium",
    ),
    _mk_template(
        {"spotify", "play"},
        "spotify_play",
        "Start playback on Spotify.",
        {
            "type": "object",
            "properties": {
                "uri": {"type": "string"},
            },
        },
        "low",
    ),
    _mk_template(
        {"file", "fichier", "read", "lire"},
        "remote_read_file",
        "Read a file from a remote storage backend.",
        {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {"type": "string"},
            },
        },
        "medium",
    ),
    _mk_template(
        {"file", "fichier", "write", "ecrire", "save", "sauvegarder"},
        "remote_write_file",
        "Write content to a file on a remote storage backend.",
        {
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
        },
        "medium",
    ),
)


# Tokens marquant des intents trop sensibles pour qu'un MCP local
# soit proposé sans validation humaine spécifique.
_SECURITY_SENSITIVE_TOKENS: frozenset[str] = frozenset({
    "credit", "card", "payment", "password", "secret",
    "bank", "wire", "transfer", "ssn", "rib", "iban",
    "kyc", "biometric", "passport", "social",
})


# ══════════════════════════════════════════════════════════════════════════════
# Source impls Phase 23 v1
# ══════════════════════════════════════════════════════════════════════════════


def _normalize_search_entry(entry: Dict[str, Any], source_name: str) -> Optional[Dict[str, Any]]:
    """Valide + normalise un dict brut renvoyé par une source.

    Filtre les entrées non conformes Phase 14/18. Aucun raise.
    """
    if not isinstance(entry, dict):
        return None
    pkg_name = entry.get("package_name")
    pkg_spec = entry.get("package_spec")
    pkg_transport = entry.get("package_transport")
    mth = entry.get("mcp_transport_hint", "unknown")
    if not isinstance(pkg_name, str) or not pkg_name:
        return None
    if len(pkg_name) > _PACKAGE_NAME_MAX:
        return None
    if not _is_valid_package_spec_phase14(pkg_spec):
        return None
    if not _is_valid_package_transport(pkg_transport):
        return None
    if not _is_valid_mcp_transport_hint(mth):
        mth = "unknown"
    version = entry.get("version", "")
    if not isinstance(version, str):
        version = ""
    if len(version) > _VERSION_MAX:
        version = version[:_VERSION_MAX]
    description = entry.get("description", "")
    if not isinstance(description, str):
        description = ""
    tools_hint = entry.get("tools_hint", [])
    if not isinstance(tools_hint, list):
        tools_hint = []
    tools_hint_clean: List[str] = []
    for t in tools_hint:
        if isinstance(t, str) and t:
            tools_hint_clean.append(t)
            if len(tools_hint_clean) >= _TOOLS_HINT_MAX:
                break
    downloads = entry.get("downloads_count")
    if not isinstance(downloads, int) or downloads < 0:
        downloads = 0
    license_id = entry.get("license_id")
    if not isinstance(license_id, str):
        license_id = None
    return {
        "source": source_name,
        "package_name": pkg_name,
        "package_spec": pkg_spec,
        "version": version,
        "package_transport": pkg_transport,
        "mcp_transport_hint": mth,
        "description": description,
        "tools_hint": tools_hint_clean,
        "downloads_count": downloads,
        "last_publish_date": entry.get("last_publish_date", ""),
        "has_repo": entry.get("has_repo") is True,
        "has_license": entry.get("has_license") is True,
        "license_id": license_id,
    }


class CuratedOfflineCatalogSource:
    """Source offline lisant un fichier JSON statique d'entrées MCP curées.

    Format attendu (tableau JSON) — chaque entrée :
      {
        "package_name": str,
        "package_spec": str,        # "npm:NAME" | "npm:@scope/NAME" | "pypi:NAME"
        "version": str,             # "1.2.3" ou "" si latest
        "package_transport": str,   # "npm" | "pypi" | "local"
        "mcp_transport_hint": str,  # "stdio" | "sse" | "http" | "unknown"
        "description": str,         # hashée par l'orchestrator
        "tools_hint": [str],        # max 50 noms
        "license_id": str,
        "has_repo": bool,
        "has_license": bool,
        "last_publish_date": str,
        "downloads_count": int
      }

    Phase 23 v1 ship un fichier `[]` (vide). Les entrées réelles
    seront ajoutées dans une PR séparée avec validation manuelle.
    """

    def __init__(self, path: Path) -> None:
        if not isinstance(path, Path):
            raise TypeError("path must be a pathlib.Path")
        self._path = path

    @property
    def name(self) -> str:
        return "curated"

    @property
    def is_network(self) -> bool:
        return False

    def search(
        self, query_tokens: set[str], *, limit: int
    ) -> List[Dict[str, Any]]:
        if not self._path.exists():
            return []
        try:
            raw = self._path.read_text(encoding="utf-8", errors="replace")
            data = json.loads(raw)
        except Exception:
            return []
        if not isinstance(data, list):
            return []
        out: List[Dict[str, Any]] = []
        for entry in data:
            normed = _normalize_search_entry(entry, self.name)
            if normed is None:
                continue
            # Match par tokens : name + tools_hint
            entry_tokens = _tokenize(
                normed["package_name"] + " "
                + " ".join(normed["tools_hint"])
            )
            if query_tokens and not (query_tokens & entry_tokens):
                continue
            out.append(normed)
            if len(out) >= limit:
                break
        return out


class LocalFilesystemSource:
    """Scanne un dossier racine pour `mcp.json` / `package.json` locaux.

    Lecture stricte, aucune introspection runtime, max 2 niveaux,
    ignore node_modules/__pycache__/.git.
    """

    _IGNORED = frozenset({"node_modules", "__pycache__", ".git", ".venv"})

    def __init__(self, scan_root: Path) -> None:
        if not isinstance(scan_root, Path):
            raise TypeError("scan_root must be a pathlib.Path")
        self._root = scan_root

    @property
    def name(self) -> str:
        return "local_fs"

    @property
    def is_network(self) -> bool:
        return False

    def search(
        self, query_tokens: set[str], *, limit: int
    ) -> List[Dict[str, Any]]:
        if not self._root.exists():
            return []
        results: List[Dict[str, Any]] = []
        self._scan(self._root, query_tokens, limit, results, depth=0)
        return results

    def _scan(
        self,
        path: Path,
        query_tokens: set[str],
        limit: int,
        results: List[Dict[str, Any]],
        depth: int,
    ) -> None:
        if depth > 2 or len(results) >= limit:
            return
        try:
            children = list(path.iterdir())
        except OSError:
            return
        for child in children:
            if len(results) >= limit:
                return
            if child.name in self._IGNORED:
                continue
            if child.is_dir():
                self._scan(child, query_tokens, limit, results, depth + 1)
                continue
            if child.name not in ("mcp.json", "package.json"):
                continue
            try:
                raw = child.read_text(encoding="utf-8", errors="replace")
                data = json.loads(raw)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            # On fabrique un package_spec local:slug à partir du nom
            slug_raw = data.get("name", "")
            slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(slug_raw)) \
                if isinstance(slug_raw, str) else ""
            if not slug:
                continue
            entry = {
                "package_name": slug_raw,
                "package_spec": "local:" + slug,
                "version": data.get("version", "")
                if isinstance(data.get("version", ""), str) else "",
                "package_transport": "local",
                "mcp_transport_hint": data.get(
                    "mcp_transport_hint", "unknown"
                ),
                "description": data.get("description", "")
                if isinstance(data.get("description", ""), str) else "",
                "tools_hint": data.get("tools_hint", [])
                if isinstance(data.get("tools_hint", []), list) else [],
                "has_repo": False,
                "has_license": False,
                "license_id": None,
            }
            normed = _normalize_search_entry(entry, self.name)
            if normed is None:
                continue
            entry_tokens = _tokenize(
                normed["package_name"] + " "
                + " ".join(normed["tools_hint"])
            )
            if query_tokens and not (query_tokens & entry_tokens):
                continue
            results.append(normed)


class StubNetworkSource:
    """Source réseau stub Phase 23 v1.

    Aucune vraie source réseau (Npm/Pypi/GitHub) n'est implémentée en v1.
    Cette classe :
      - respecte le Protocol MCPSearchSourceLike,
      - expose `is_network=True`,
      - exige `network_enabled: bool` au constructeur,
      - retourne TOUJOURS `[]` (même avec network_enabled=True),
      - ne fait AUCUN appel HTTP, aucun import de clients HTTP tiers.

    Permet de valider l'architecture network-gated en tests sans
    tester de fausse feature réseau.
    """

    def __init__(self, name: str, *, network_enabled: bool) -> None:
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(network_enabled, bool):
            raise TypeError("network_enabled must be bool")
        self._name = name
        self._network_enabled = network_enabled

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_network(self) -> bool:
        return True

    @property
    def network_enabled(self) -> bool:
        return self._network_enabled

    def search(
        self, query_tokens: set[str], *, limit: int
    ) -> List[Dict[str, Any]]:
        # Aucun call réseau, jamais. v1 stub.
        return []


# ══════════════════════════════════════════════════════════════════════════════
# Creation planner — déterministe, descriptions whitelist
# ══════════════════════════════════════════════════════════════════════════════


def _suggest_server_id(intent_tokens: set[str]) -> str:
    """Génère un server_id local déterministe :
       local_<hash8(tokens triés)>.
    """
    sorted_tokens = " ".join(sorted(intent_tokens)) if intent_tokens else ""
    h = hashlib.sha256(sorted_tokens.encode("utf-8")).hexdigest()
    return "local_" + h[:8]


def _detect_security_sensitive(intent_tokens: set[str]) -> bool:
    return bool(intent_tokens & _SECURITY_SENSITIVE_TOKENS)


def _build_creation_proposal(
    intent_tokens: set[str],
) -> MCPCreationProposal:
    """Retourne toujours un MCPCreationProposal (complexity_estimate
    indique l'issue).
    """
    if not intent_tokens:
        return MCPCreationProposal(
            suggested_server_id=_suggest_server_id(intent_tokens),
            suggested_display_name="(empty intent)",
            suggested_tools=(),
            rationale_code="intent_too_vague",
            complexity_estimate="refuse",
        )
    if _detect_security_sensitive(intent_tokens):
        return MCPCreationProposal(
            suggested_server_id=_suggest_server_id(intent_tokens),
            suggested_display_name="(security-sensitive intent)",
            suggested_tools=(),
            rationale_code="security_sensitive",
            complexity_estimate="refuse",
        )
    # Sélection des templates par overlap décroissant
    scored: List[Tuple[int, _ToolTemplate]] = []
    for tpl in _TOOL_TEMPLATES:
        overlap = len(intent_tokens & tpl.match_tokens)
        if overlap > 0:
            scored.append((overlap, tpl))
    if not scored:
        return MCPCreationProposal(
            suggested_server_id=_suggest_server_id(intent_tokens),
            suggested_display_name="(no matching template)",
            suggested_tools=(),
            rationale_code="intent_too_vague",
            complexity_estimate="refuse",
        )
    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [tpl for _, tpl in scored[:5]]
    tools_out: List[ToolTemplateProposal] = []
    for tpl in selected:
        tools_out.append(ToolTemplateProposal(
            tool_name=tpl.tool_name,
            description=tpl.description,
            input_schema_hash=_input_schema_hash(tpl.input_schema),
            risk_level=tpl.risk_level,
        ))
    n = len(tools_out)
    if n == 1:
        complexity = "low"
    elif n <= 3:
        complexity = "medium"
    else:
        complexity = "high"
    display = "Local MCP — " + ", ".join(
        sorted({t.tool_name for t in tools_out})
    )
    if len(display) > 100:
        display = display[:100]
    return MCPCreationProposal(
        suggested_server_id=_suggest_server_id(intent_tokens),
        suggested_display_name=display,
        suggested_tools=tuple(tools_out),
        rationale_code="matched_templates",
        complexity_estimate=complexity,
    )


# ══════════════════════════════════════════════════════════════════════════════
# MCPProposalPlanner — stateless, lecture seule
# ══════════════════════════════════════════════════════════════════════════════


class MCPProposalPlanner:
    """Phase 23 — produit un MCPProposalPlan en lecture seule.

    Aucun cache. Aucune mutation. Sources lues à chaque appel.
    """

    def __init__(
        self,
        deps: MCPProposalPlannerDeps,
        *,
        audit_log_path: Optional[Path] = None,
    ) -> None:
        if not isinstance(deps, MCPProposalPlannerDeps):
            raise TypeError(
                "deps must be a MCPProposalPlannerDeps instance"
            )
        if audit_log_path is not None and not isinstance(
            audit_log_path, Path
        ):
            raise TypeError("audit_log_path must be a pathlib.Path or None")
        self._deps = deps
        self._audit_log_path = audit_log_path

    # ── Public ─────────────────────────────────────────────────────────────

    def plan_proposal(
        self,
        intent: str,
        *,
        caller_kind: str,
        profile: Optional[str] = None,
        phase22_plan: Optional[Any] = None,  # CapabilityResolutionPlan optionnel
    ) -> MCPProposalPlan:
        intent_sanitized = _sanitize_intent(intent)
        intent_tokens = _tokenize(intent_sanitized)
        proposal_id = _new_proposal_id()
        created_at = _now_utc_iso()
        sources_degraded: List[str] = []

        # ── 0. Phase I-7 : priorité absolue KNOWN_MCPS curated ────────────
        # Avant d'appeler les sources réseau (qui peuvent retourner des
        # packages random), on regarde si l'intent matche un MCP officiel
        # curated (KNOWN_MCPS). Si oui, on court-circuite et on retourne
        # le candidat curated direct, avec score max et source non-network
        # (pas de requires_approval, ticket simple).
        raw_results: List[Dict[str, Any]] = []
        sources_consulted: List[str] = []
        network_enabled_any = False
        try:
            from src.mcp.known_mcps import lookup_known_mcp  # noqa: WPS433
            curated_match = lookup_known_mcp(intent_sanitized)
        except Exception:  # noqa: BLE001
            curated_match = None

        if curated_match is not None:
            sources_consulted.append("known_mcps_curated")
            spec = curated_match.package_spec
            transport = (
                "npm" if spec.startswith("npm:")
                else ("pypi" if spec.startswith("pypi:") else "unknown")
            )
            raw_results.append({
                "source": "known_mcps_curated",
                "package_name": curated_match.slug,
                "package_spec": spec,
                "version": "latest",
                "package_transport": transport,
                "mcp_transport_hint": "stdio",
                "description": curated_match.description or curated_match.slug,
                "tools_hint": [],
                "license_id": "MIT",
                "has_repo": True,
                "has_license": True,
                # Force la priorité absolue (curated officiel ≫ npm search)
                "_curated_priority": True,
            })

        # ── 1. Search via toutes les sources réseau (si pas curated match) ─
        # Si on a déjà un curated match prioritaire, on saute la search
        # réseau pour éviter qu'un package random soit injecté.
        if curated_match is None:
            for source in self._deps.sources:
                sources_consulted.append(source.name)
                if source.is_network:
                    ne = getattr(source, "network_enabled", False)
                    if ne is True:
                        network_enabled_any = True
                ok, found = _safe_call(
                    source.search, intent_tokens, limit=_SEARCH_RESULTS_MAX
                )
                if not ok:
                    sources_degraded.append(source.name)
                    continue
                if not isinstance(found, list):
                    continue
                for raw in found:
                    normed = _normalize_search_entry(raw, source.name)
                    if normed is None:
                        continue
                    raw_results.append(normed)

        # ── 2. Scoring + filter ───────────────────────────────────────────
        # Phase I-8 (Fix AI) : PERTINENCE. Le pre-score était 100% qualité
        # (downloads/licence/recence) et 0% pertinence : sur un intent
        # « crypto », les gros packages génériques contenant « mcp »
        # gagnaient (observé runtime 2026-06-11 : mcp-framework puis
        # mcp-use élus — des outils de dev, pas des serveurs du domaine).
        # Règle : un candidat non-curated sans AUCUN token discriminant
        # partagé avec l'intent (nom + description) est écarté ; le
        # recouvrement donne un bonus. derive_capability_tags retire les
        # tokens du cycle de vie (« mcp », « use », « installer »...) :
        # « mcp-use » ne matche rien, « bitcoin-mcp » matche « bitcoin ».
        relevance_tokens: set = set()
        try:
            from src.mcp.capability_tags import (  # noqa: WPS433
                derive_capability_tags,
            )
            relevance_tokens = set(
                derive_capability_tags(" ".join(sorted(intent_tokens)))
            )
        except Exception:  # noqa: BLE001
            relevance_tokens = set()
        scored: List[Tuple[int, Dict[str, Any]]] = []
        for raw in raw_results:
            # Phase I-7 : curated officiel = score max absolu (100)
            if raw.get("_curated_priority") is True:
                score = 100
            else:
                score = _compute_pre_score(raw)
                if relevance_tokens:
                    haystack = "{} {}".format(
                        raw.get("package_name") or "",
                        raw.get("description") or "",
                    )
                    # Le tokenizer ne splitte pas les underscores/points :
                    # « search_xyz_tool » doit exposer « xyz ».
                    haystack = haystack.replace("_", " ").replace(".", " ")
                    try:
                        hay_tokens = set(derive_capability_tags(haystack))
                    except Exception:  # noqa: BLE001
                        hay_tokens = set()
                    overlap = len(relevance_tokens & hay_tokens)
                    if overlap == 0:
                        continue  # hors sujet → jamais proposé
                    score = min(score + min(overlap, 3) * 10, 100)
            scored.append((score, raw))
        scored.sort(key=lambda x: x[0], reverse=True)
        filtered = [(s, r) for s, r in scored if s >= _MIN_PRE_SCORE_FOR_PROPOSAL]
        filtered = filtered[:_SEARCH_RESULTS_MAX]

        # Construction des MCPSearchResult sanitizés
        search_results: List[MCPSearchResult] = []
        for score, raw in filtered:
            search_results.append(MCPSearchResult(
                source=raw["source"],
                package_name=raw["package_name"][:_PACKAGE_NAME_MAX],
                package_spec=raw["package_spec"],
                version=raw["version"],
                package_transport=raw["package_transport"],
                mcp_transport_hint=raw["mcp_transport_hint"],
                description_hash=_description_hash(raw["description"]),
                tools_hint=tuple(raw["tools_hint"][:_TOOLS_HINT_MAX]),
                trust_pre_score=score,
                license_id=raw.get("license_id"),
            ))

        # ── 3. Cascade décisionnelle ──────────────────────────────────────
        actionable = _phase23_is_actionable_intent(intent_tokens)
        catalog_race = False
        catalog_lookup_failed = False

        creation_proposal: Optional[MCPCreationProposal] = None
        catalog_proposal: Optional[CatalogProposal] = None
        blockers: List[MCPProposalPlanBlocker] = []

        decision = MCPProposalDecision.NO_SAFE_CANDIDATE
        selected_idx: Optional[int] = None

        # Parcours dans l'ordre du score : on cherche le premier candidat
        # exploitable (pas QUARANTINED).
        for idx, sr in enumerate(search_results):
            race_sid = self._lookup_catalog(
                sr.package_spec, sources_degraded
            )
            if race_sid is None and self._deps.catalog_lookup is None:
                # Pas de catalog_lookup → on ne peut pas détecter race.
                # On considère que c'est PROPOSE_CATALOG_DECLARED valide.
                pass
            if race_sid is not None:
                # Existe déjà au catalog : USE_EXISTING_CANDIDATE
                # (sauf QUARANTINED — on drop et retry suivant).
                if self._is_server_quarantined(race_sid):
                    continue
                catalog_race = True
                decision = MCPProposalDecision.USE_EXISTING_CANDIDATE
                selected_idx = idx
                break
            # Pas en catalog → PROPOSE_CATALOG_DECLARED candidate
            decision = MCPProposalDecision.PROPOSE_CATALOG_DECLARED
            selected_idx = idx
            break

        # ── 4. Si pas de search candidate exploitable : creation ou no_safe
        if decision == MCPProposalDecision.NO_SAFE_CANDIDATE:
            if not search_results:
                if raw_results:
                    blockers.append(MCPProposalPlanBlocker(
                        blocker_code="all_candidates_below_threshold",
                        details_count=len(raw_results),
                    ))
            if actionable:
                cp = _build_creation_proposal(intent_tokens)
                if cp.complexity_estimate in ("low", "medium"):
                    decision = MCPProposalDecision.PROPOSE_LOCAL_CREATE
                    creation_proposal = cp
                elif cp.rationale_code == "security_sensitive":
                    blockers.append(MCPProposalPlanBlocker(
                        blocker_code="creation_security_sensitive",
                        details_count=1,
                    ))
                    creation_proposal = cp  # exposé pour traçabilité
                elif cp.rationale_code == "intent_too_vague":
                    blockers.append(MCPProposalPlanBlocker(
                        blocker_code="creation_intent_too_vague",
                        details_count=1,
                    ))
                    creation_proposal = cp  # exposé pour traçabilité

        # ── 5. Catalog proposal pour PROPOSE_CATALOG_DECLARED ─────────────
        selected_sr: Optional[MCPSearchResult] = None
        if (
            decision == MCPProposalDecision.PROPOSE_CATALOG_DECLARED
            and selected_idx is not None
        ):
            selected_sr = search_results[selected_idx]
            requires_approval = self._source_is_network_enabled(
                selected_sr.source
            )
            catalog_proposal = CatalogProposal(
                proposed_server_id=self._derive_server_id(selected_sr),
                proposed_display_name=selected_sr.package_name[:120],
                proposed_package_spec=selected_sr.package_spec,
                proposed_version=selected_sr.version,
                proposed_package_transport=selected_sr.package_transport,
                proposed_mcp_transport_hint=selected_sr.mcp_transport_hint,
                proposed_trust_score_set=selected_sr.trust_pre_score,
                rationale_code="existing_search_match",
                requires_approval=requires_approval,
                target_status_on_add="declared",
            )
            if requires_approval:
                decision = MCPProposalDecision.NEEDS_APPROVAL
                blockers.append(MCPProposalPlanBlocker(
                    blocker_code="network_source_requires_approval",
                    details_count=1,
                ))

        # PROPOSE_LOCAL_CREATE ⇒ catalog_proposal = None (doctrine v2 §F2.2)
        if decision == MCPProposalDecision.PROPOSE_LOCAL_CREATE:
            catalog_proposal = None

        # USE_EXISTING_CANDIDATE ⇒ catalog_proposal = None
        if decision == MCPProposalDecision.USE_EXISTING_CANDIDATE:
            catalog_proposal = None

        # ── 6. Evidence sanitisée ─────────────────────────────────────────
        top_pre = search_results[0].trust_pre_score if search_results else 0
        top_pkg_transport = (
            search_results[0].package_transport if search_results else ""
        )
        evidence: Dict[str, Any] = {
            "proposal_id": proposal_id,
            "created_at": created_at,
            "sources_consulted": sorted(set(sources_consulted)),
            "network_sources_enabled": network_enabled_any,
            "search_results_count": len(raw_results),
            "search_results_filtered_count": len(search_results),
            "top_pre_score": top_pre,
            "min_pre_score_required": _MIN_PRE_SCORE_FOR_PROPOSAL,
            "catalog_race_detected": catalog_race,
            "creation_complexity_estimate": (
                creation_proposal.complexity_estimate
                if creation_proposal is not None else ""
            ),
            "creation_rationale_code": (
                creation_proposal.rationale_code
                if creation_proposal is not None else ""
            ),
            "actionable_intent": actionable,
            "decision_reason_code": decision.value,
            "sources_degraded": sorted(set(sources_degraded)),
            "package_transport_top": top_pkg_transport,
        }
        if catalog_lookup_failed:
            blockers.append(MCPProposalPlanBlocker(
                blocker_code="catalog_lookup_failed",
                details_count=1,
            ))
        evidence = {
            k: v for k, v in evidence.items() if k in _EVIDENCE_WHITELIST
        }

        plan = MCPProposalPlan(
            proposal_id=proposal_id,
            intent_query_sanitized=intent_sanitized,
            decision=decision,
            search_results=tuple(search_results),
            creation_proposal=creation_proposal,
            catalog_proposal=catalog_proposal,
            blockers=tuple(blockers),
            evidence=evidence,
            created_at=created_at,
        )

        self._append_audit_if_configured(plan, caller_kind, profile)
        return plan

    # ── Catalog lookup helpers ─────────────────────────────────────────────

    def _lookup_catalog(
        self, package_spec: str, sources_degraded: List[str]
    ) -> Optional[str]:
        cl = self._deps.catalog_lookup
        if cl is None:
            return None
        ok, res = _safe_call(cl.find_by_package_spec, package_spec)
        if not ok:
            sources_degraded.append("catalog_lookup")
            return None
        if isinstance(res, str) and res:
            return res
        return None

    def _is_server_quarantined(self, server_id: str) -> bool:
        cl = self._deps.catalog_lookup
        if cl is None:
            return False
        ok, entry = _safe_call(cl.find_by_server_id, server_id)
        if not ok or entry is None:
            return False
        status = getattr(entry, "status", None)
        val = getattr(status, "value", None)
        if isinstance(val, str):
            return val.lower() == "quarantined"
        if isinstance(status, str):
            return status.lower() == "quarantined"
        return False

    def _derive_server_id(self, sr: MCPSearchResult) -> str:
        """Génère un server_id pour la proposition.

        Phase I-7 : si le package_spec correspond à un MCP curated (KNOWN_MCPS),
        on retourne le slug canonique (ex: "slack") au lieu d'un hash synthétique
        (ex: "proposed_e28f7ba17d"). Indispensable pour que le bypass auto-approve
        curated reconnaisse le ticket et déclenche l'autonomie complète.
        """
        try:
            from src.mcp.known_mcps import find_known_mcp_by_package_spec  # noqa: WPS433
            curated = find_known_mcp_by_package_spec(sr.package_spec)
            if curated is not None and isinstance(curated.slug, str) and curated.slug:
                return curated.slug
        except Exception:  # noqa: BLE001
            pass
        # Fallback historique : hash déterministe du package_spec.
        h = hashlib.sha256(sr.package_spec.encode("utf-8")).hexdigest()
        return "proposed_" + h[:10]

    def _source_is_network_enabled(self, source_name: str) -> bool:
        for s in self._deps.sources:
            if s.name == source_name:
                if s.is_network:
                    return bool(getattr(s, "network_enabled", False))
                return False
        return False

    # ── Audit local optionnel ──────────────────────────────────────────────

    def _append_audit_if_configured(
        self,
        plan: MCPProposalPlan,
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
            "event": "proposal_planned",
            "phase": "23",
            "proposal_id": plan.proposal_id,
            "decision": plan.decision.value,
            "caller_kind": str(caller_kind)[:32] if isinstance(
                caller_kind, str
            ) else "unknown",
            "profile": str(profile)[:64] if isinstance(profile, str) else None,
            "search_results_count": plan.evidence.get(
                "search_results_filtered_count", 0
            ),
            "blockers_count": len(plan.blockers),
            "actionable_intent": plan.evidence.get("actionable_intent", False),
            "network_sources_enabled": plan.evidence.get(
                "network_sources_enabled", False
            ),
            "creation_complexity_estimate": plan.evidence.get(
                "creation_complexity_estimate", ""
            ),
            "sources_degraded_count": len(
                plan.evidence.get("sources_degraded", [])
            ),
        }
        try:
            line = json.dumps(event, ensure_ascii=False)
            with self._audit_log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            return
