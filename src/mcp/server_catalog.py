"""
server_catalog.py — MCP Server Catalog (Phase 14 v3).

Référentiel persistant des servers MCP connus à Lumena.

DOCTRINE Phase 14 :
  - Stockage uniquement : aucun suivi runtime (job du RuntimeWatcher Phase 12).
  - Pas d'install effectif : ne télécharge rien, ne lance pas npm/pip install.
  - Aucun câblage runtime (tool_registry, react, sub_agent, MCPSandboxRunner,
    MCPClient, approval_queue, policy, auto_approve, runtime_watcher,
    orchestrator).
  - Validations strictes par transport pour package_spec.
  - Transitions de statut bornées (machine à états + table _ALLOWED_TRANSITIONS).
  - Plain JSON sur disque (pas de Fernet) + HMAC-SHA256 local pour intégrité.
  - Binding fichier ↔ contenu : entry.server_id == file_path.stem (anti-copie).
  - Audit forensique sans PII : whitelist stricte des champs auditables.

Sources de vérité séparées :
  - package_spec : IDENTITÉ du package (npm:<pkg> / pypi:<pkg> / local:<slug>)
  - version     : VERSION dans un champ séparé (ex: "1.2.3", "latest")
  Cette séparation évite la double source de vérité.

is_callable :
  - True ssi status == ACTIVE
  - INSTALLED, DECLARED, QUARANTINED, REMOVED → False

Layout disque :
  DATA_DIR/mcp_server_catalog/servers/<server_id>.json
  DATA_DIR/mcp_server_catalog/audit.jsonl
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from loguru import logger

from src.services.secrets_service import SecretsService, get_secrets_service
from src.utils.paths import DATA_DIR
from src.utils.persistence import atomic_write_json, safe_read_json


# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_DIRNAME = "mcp_server_catalog"
_SERVERS_SUBDIR = "servers"
_AUDIT_FILENAME = "audit.jsonl"

_HMAC_KEY_SCOPE = "lumena_global"
_HMAC_KEY_NAME = "MCP_SERVER_CATALOG_HMAC_KEY"

# Validations server_id (cohérent avec Phase 12)
_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
_WINDOWS_RESERVED_NAMES: FrozenSet[str] = frozenset({
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5",
    "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
    "lpt6", "lpt7", "lpt8", "lpt9",
})

_OWNER_PROFILE_RE = re.compile(r"^[a-z0-9_-]+$")
_DISPLAY_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,200}$")
_VERSION_RE = re.compile(r"^[a-zA-Z0-9._\-+]{1,64}$")
_NOTES_RE = re.compile(r"^[a-zA-Z0-9 _:.\-]{0,256}$")

# package_spec validations par transport
# npm : @scope/name OU name simple (slash uniquement après @scope)
_PKG_NPM_RE = re.compile(
    r"^npm:(?:@[a-z0-9][a-z0-9\-_.]{0,63}/)?[a-z0-9][a-z0-9\-_.]{0,63}$"
)
# pypi : nom seul, jamais de slash
_PKG_PYPI_RE = re.compile(r"^pypi:[a-zA-Z][a-zA-Z0-9_\-.]{0,63}$")
# local : slug seul, jamais de slash
_PKG_LOCAL_RE = re.compile(r"^local:[a-z0-9][a-z0-9_\-.]{0,63}$")

# Caractères globalement interdits (slash NON inclus — validé par transport)
_PKG_FORBIDDEN_GLOBAL = (
    " ", "\t", "\n", "\r", "\\", ";", "&", "|", "\x00",
    '"', "'", "`", "$",
)


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions / Enums
# ──────────────────────────────────────────────────────────────────────────────


class CatalogError(Exception):
    """Erreur générique du catalog."""


class ServerStatus(Enum):
    DECLARED    = "declared"
    INSTALLED   = "installed"
    ACTIVE      = "active"
    QUARANTINED = "quarantined"
    REMOVED     = "removed"


# Machine à états : aucune self-loop, REMOVED terminal.
_ALLOWED_TRANSITIONS: Dict[ServerStatus, FrozenSet[ServerStatus]] = {
    ServerStatus.DECLARED:    frozenset({
        ServerStatus.INSTALLED,
        ServerStatus.QUARANTINED,
        ServerStatus.REMOVED,
    }),
    ServerStatus.INSTALLED:   frozenset({
        ServerStatus.ACTIVE,
        ServerStatus.QUARANTINED,
        ServerStatus.REMOVED,
    }),
    ServerStatus.ACTIVE:      frozenset({
        ServerStatus.INSTALLED,
        ServerStatus.QUARANTINED,
        ServerStatus.REMOVED,
    }),
    ServerStatus.QUARANTINED: frozenset({
        ServerStatus.INSTALLED,
        ServerStatus.ACTIVE,
        ServerStatus.REMOVED,
    }),
    ServerStatus.REMOVED:     frozenset(),
}


# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ServerEntry:
    server_id: str
    display_name: str
    package_spec: str
    version: Optional[str]
    owner_profile: str
    trust_score: Optional[int]
    status: ServerStatus
    added_at: str
    updated_at: str
    last_active_at: Optional[str]
    notes: Optional[str]
    # Phase B (unification catégories MCP) — defaults pour backward-compat JSON.
    semantic_category: Optional[str] = None
    category_decision_source: str = ""
    prefer_over_native: bool = False
    # Phase I-1 : schéma de config persisté (dict serialisable JSON).
    # None tant qu'aucune détection (curated/package/probe/user) n'a eu lieu.
    config_schema: Optional[Dict[str, Any]] = None
    # Phase I-8 (Fix AC) : tokens de capability capturés depuis l'intent
    # utilisateur au moment de la proposition catalog_add. Permet au
    # capability_resolver de re-matcher un intent futur sur une entrée
    # DECLARED/INSTALLED dont le display_name seul ne suffirait pas
    # (ex: intent FR « météo » vs package npm anglais). None = pré-I-8.
    capability_tags: Optional[Tuple[str, ...]] = None
    # Phase I-8 (Fix AY) : sous-commande serveur découverte réactivement par
    # l'activation quand l'entry point console est un CLI à sous-commandes
    # (ex. windows-mcp → ("serve",)). Appliquée APRÈS le binaire résolu au
    # start. None = entry point direct (cas nominal, pré-AY).
    start_entry_args: Optional[Tuple[str, ...]] = None


# Whitelist stricte des sources de décision de catégorisation.
_VALID_DECISION_SOURCES: FrozenSet[str] = frozenset({
    "",            # non décidé (entry pré-Phase B ou jamais classifiée)
    "static",      # _MCP_SERVER_NAME_TO_SEMANTIC
    "heuristic",   # _TOOL_DESC_KEYWORDS_TO_CATEGORY
    "llm",         # infer_with_llm
    "fallback",    # category par défaut sans signal exploitable
    "user_override",  # override explicite utilisateur (chat ou UI)
})


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────────────────────
# Validators
# ──────────────────────────────────────────────────────────────────────────────


def _validate_server_id(server_id: Any) -> None:
    if not isinstance(server_id, str) or not _SERVER_ID_RE.match(server_id):
        raise CatalogError("context_invalid:server_id")
    if ".." in server_id or "/" in server_id or "\\" in server_id:
        raise CatalogError("context_invalid:server_id")
    stem = server_id.split(".", 1)[0]
    if stem in _WINDOWS_RESERVED_NAMES:
        raise CatalogError("context_invalid:server_id_windows_reserved")


def _validate_display_name(display_name: Any) -> None:
    if not isinstance(display_name, str) or not _DISPLAY_NAME_RE.match(display_name):
        raise CatalogError("context_invalid:display_name")


def _validate_package_spec(package_spec: Any) -> None:
    if not isinstance(package_spec, str) or not package_spec:
        raise CatalogError("context_invalid:package_spec")
    for ch in _PKG_FORBIDDEN_GLOBAL:
        if ch in package_spec:
            raise CatalogError("context_invalid:package_spec_forbidden_char")
    # Drive Windows (lettre + ':' sans transport reconnu)
    if (
        len(package_spec) >= 2
        and package_spec[1] == ":"
        and package_spec[0].isalpha()
        and not package_spec.startswith(("npm:", "pypi:", "local:"))
    ):
        raise CatalogError("context_invalid:package_spec_drive_letter")
    if ".." in package_spec:
        raise CatalogError("context_invalid:package_spec_path_traversal")
    if package_spec.startswith("npm:"):
        if not _PKG_NPM_RE.match(package_spec):
            raise CatalogError("context_invalid:package_spec_npm")
        return
    if package_spec.startswith("pypi:"):
        if not _PKG_PYPI_RE.match(package_spec):
            raise CatalogError("context_invalid:package_spec_pypi")
        return
    if package_spec.startswith("local:"):
        if not _PKG_LOCAL_RE.match(package_spec):
            raise CatalogError("context_invalid:package_spec_local")
        return
    raise CatalogError("context_invalid:package_spec_unknown_transport")


def _validate_version(version: Any) -> None:
    if version is None:
        return
    if not isinstance(version, str) or not _VERSION_RE.match(version):
        raise CatalogError("context_invalid:version")


def _validate_owner_profile(owner_profile: Any) -> None:
    if not isinstance(owner_profile, str) or not _OWNER_PROFILE_RE.match(owner_profile):
        raise CatalogError("context_invalid:owner_profile")


def _validate_trust_score(trust_score: Any) -> None:
    if trust_score is None:
        return
    if isinstance(trust_score, bool):
        raise CatalogError("context_invalid:trust_score_bool")
    if not isinstance(trust_score, int):
        raise CatalogError("context_invalid:trust_score_type")
    if trust_score < 0 or trust_score > 100:
        raise CatalogError("context_invalid:trust_score_range")


def _validate_notes(notes: Any) -> None:
    if notes is None:
        return
    if not isinstance(notes, str) or not _NOTES_RE.match(notes):
        raise CatalogError("context_invalid:notes")


# Phase I-8 (Fix AC) : tags de capability — tokens courts, alphanumériques
# unicode (accents FR autorisés), bornés en nombre et en longueur.
_CAPABILITY_TAG_RE = re.compile(r"^[\w\-]{2,32}$", re.UNICODE)
_CAPABILITY_TAGS_MAX = 16


def _validate_capability_tags(tags: Any) -> None:
    if tags is None:
        return
    if not isinstance(tags, (list, tuple)):
        raise CatalogError("context_invalid:capability_tags")
    if len(tags) > _CAPABILITY_TAGS_MAX:
        raise CatalogError("context_invalid:capability_tags_too_many")
    for t in tags:
        if not isinstance(t, str) or not _CAPABILITY_TAG_RE.match(t):
            raise CatalogError("context_invalid:capability_tag")


def _validate_semantic_category(value: Any) -> None:
    """Phase B : None autorisé (catégorie pas encore résolue) ; sinon
    str ∈ VALID_CATEGORIES. Import retardé pour éviter cycle d'import."""
    if value is None:
        return
    if not isinstance(value, str):
        raise CatalogError("context_invalid:semantic_category_type")
    from src.mcp.category_inference import VALID_CATEGORIES  # lazy
    if value not in VALID_CATEGORIES:
        raise CatalogError("context_invalid:semantic_category_unknown")


def _validate_decision_source(value: Any) -> None:
    if not isinstance(value, str):
        raise CatalogError("context_invalid:decision_source_type")
    if value not in _VALID_DECISION_SOURCES:
        raise CatalogError("context_invalid:decision_source_unknown")


def _validate_prefer_over_native(value: Any) -> None:
    # bool strict — int est interdit (bool est une sous-classe d'int en Python).
    if not isinstance(value, bool):
        raise CatalogError("context_invalid:prefer_over_native_type")


# ──────────────────────────────────────────────────────────────────────────────
# Catalog
# ──────────────────────────────────────────────────────────────────────────────


class MCPServerCatalog:
    """Référentiel persistant des servers MCP."""

    def __init__(
        self,
        catalog_dir: Optional[Path] = None,
        audit_log_path: Optional[Path] = None,
        secrets_service: Optional[SecretsService] = None,
    ):
        self._servers_dir = (catalog_dir or (DATA_DIR / _DEFAULT_DIRNAME)) / _SERVERS_SUBDIR
        self._audit_log_path = audit_log_path or (
            DATA_DIR / _DEFAULT_DIRNAME / _AUDIT_FILENAME
        )
        self._secrets = secrets_service
        self._hmac_key: Optional[bytes] = None

        self._servers_dir.mkdir(parents=True, exist_ok=True)
        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def servers_dir(self) -> Path:
        return self._servers_dir

    @property
    def audit_log_path(self) -> Path:
        return self._audit_log_path

    # ── HMAC key ──────────────────────────────────────────────────────────

    def _get_secrets_service(self) -> SecretsService:
        if self._secrets is None:
            self._secrets = get_secrets_service()
        return self._secrets

    def _get_hmac_key(self) -> bytes:
        if self._hmac_key is not None:
            return self._hmac_key
        svc = self._get_secrets_service()
        key_str = svc.get(_HMAC_KEY_SCOPE, _HMAC_KEY_NAME)
        if not key_str:
            import secrets as _secrets
            import base64
            key_bytes = _secrets.token_bytes(32)
            key_str = base64.b64encode(key_bytes).decode("ascii")
            svc.set(_HMAC_KEY_SCOPE, _HMAC_KEY_NAME, key_str)
            logger.info(
                "[mcp.server_catalog] HMAC key generated in SecretsService"
            )
        import base64
        try:
            self._hmac_key = base64.b64decode(key_str)
        except (ValueError, TypeError) as e:
            raise CatalogError(f"hmac_key_corrupted") from e
        return self._hmac_key

    # ── Sérialisation / HMAC / binding ────────────────────────────────────

    @staticmethod
    def _entry_to_dict(entry: ServerEntry) -> Dict[str, Any]:
        # Phase B : on n'émet les 3 champs catégorie QUE s'ils sont non-default.
        # Cela garantit la backward-compat du HMAC : les entries jamais
        # classifiées produisent exactement le même JSON canonique qu'avant.
        out: Dict[str, Any] = {
            "server_id": entry.server_id,
            "display_name": entry.display_name,
            "package_spec": entry.package_spec,
            "version": entry.version,
            "owner_profile": entry.owner_profile,
            "trust_score": entry.trust_score,
            "status": entry.status.value,
            "added_at": entry.added_at,
            "updated_at": entry.updated_at,
            "last_active_at": entry.last_active_at,
            "notes": entry.notes,
        }
        if entry.semantic_category is not None:
            out["semantic_category"] = entry.semantic_category
        if entry.category_decision_source != "":
            out["category_decision_source"] = entry.category_decision_source
        if entry.prefer_over_native:
            out["prefer_over_native"] = entry.prefer_over_native
        # Phase I-1 : config_schema emis uniquement si present (back-compat).
        if entry.config_schema is not None:
            out["config_schema"] = entry.config_schema
        # Phase I-8 (Fix AC) : capability_tags emis uniquement si present
        # (back-compat HMAC : les entries pré-I-8 gardent le même JSON canonique).
        if entry.capability_tags is not None:
            out["capability_tags"] = list(entry.capability_tags)
        # Phase I-8 (Fix AY) : start_entry_args emis uniquement si present
        # (back-compat HMAC, même pattern que capability_tags).
        if entry.start_entry_args is not None:
            out["start_entry_args"] = list(entry.start_entry_args)
        return out

    @staticmethod
    def _dict_to_entry(d: Dict[str, Any]) -> Optional[ServerEntry]:
        try:
            # Phase B : lecture tolérante des nouveaux champs (defaults si
            # absents → backward-compat avec entries pré-Phase B).
            semantic_category = d.get("semantic_category")
            category_decision_source = d.get("category_decision_source", "")
            prefer_over_native = d.get("prefer_over_native", False)
            # Typage strict à la lecture : un JSON corrompu doit échouer net.
            if semantic_category is not None and not isinstance(
                semantic_category, str
            ):
                return None
            if not isinstance(category_decision_source, str):
                return None
            if not isinstance(prefer_over_native, bool):
                return None
            # Phase I-1 : config_schema (back-compat : None si absent).
            config_schema = d.get("config_schema")
            if config_schema is not None and not isinstance(config_schema, dict):
                return None
            # Phase I-8 (Fix AC) : capability_tags (back-compat : None si absent).
            raw_tags = d.get("capability_tags")
            capability_tags: Optional[Tuple[str, ...]] = None
            if raw_tags is not None:
                if not isinstance(raw_tags, list) or not all(
                    isinstance(t, str) for t in raw_tags
                ):
                    return None
                capability_tags = tuple(raw_tags)
            # Phase I-8 (Fix AY) : start_entry_args (back-compat : None si absent).
            raw_entry_args = d.get("start_entry_args")
            start_entry_args: Optional[Tuple[str, ...]] = None
            if raw_entry_args is not None:
                if not isinstance(raw_entry_args, list) or not all(
                    isinstance(a, str) for a in raw_entry_args
                ):
                    return None
                start_entry_args = tuple(raw_entry_args)
            return ServerEntry(
                server_id=str(d["server_id"]),
                display_name=str(d["display_name"]),
                package_spec=str(d["package_spec"]),
                version=d.get("version"),
                owner_profile=str(d["owner_profile"]),
                trust_score=d.get("trust_score"),
                status=ServerStatus(d["status"]),
                added_at=str(d["added_at"]),
                updated_at=str(d["updated_at"]),
                last_active_at=d.get("last_active_at"),
                notes=d.get("notes"),
                semantic_category=semantic_category,
                category_decision_source=category_decision_source,
                prefer_over_native=prefer_over_native,
                config_schema=config_schema,
                capability_tags=capability_tags,
                start_entry_args=start_entry_args,
            )
        except (KeyError, ValueError, TypeError):
            return None

    @staticmethod
    def _canonical_entry_bytes(entry_dict: Dict[str, Any]) -> bytes:
        """Sérialisation canonique stable (tri clés) pour HMAC."""
        return json.dumps(entry_dict, sort_keys=True, ensure_ascii=False).encode(
            "utf-8"
        )

    def _compute_hmac(self, entry_dict: Dict[str, Any]) -> str:
        canonical = self._canonical_entry_bytes(entry_dict)
        return hmac.new(self._get_hmac_key(), canonical, hashlib.sha256).hexdigest()

    @staticmethod
    def _check_binding(entry_dict: Dict[str, Any], file_path: Path) -> bool:
        """Binding fichier ↔ contenu : entry.server_id == file_path.stem."""
        sid = entry_dict.get("server_id")
        return isinstance(sid, str) and sid == file_path.stem

    def _read_record(self, file_path: Path) -> Optional[Dict[str, Any]]:
        data = safe_read_json(file_path, default=None)
        if not isinstance(data, dict):
            return None
        if "entry" not in data or "integrity_hmac" not in data:
            return None
        return data

    def _verify_hmac(self, record: Dict[str, Any]) -> bool:
        entry_dict = record.get("entry")
        provided = record.get("integrity_hmac")
        if not isinstance(entry_dict, dict) or not isinstance(provided, str):
            return False
        try:
            expected = self._compute_hmac(entry_dict)
        except CatalogError:
            return False
        return hmac.compare_digest(provided, expected)

    def _persist(self, entry: ServerEntry) -> None:
        entry_dict = self._entry_to_dict(entry)
        record = {
            "entry": entry_dict,
            "integrity_hmac": self._compute_hmac(entry_dict),
        }
        path = self._server_path(entry.server_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, record)

    def _server_path(self, server_id: str) -> Path:
        return self._servers_dir / f"{server_id}.json"

    # ── Audit (whitelist stricte) ─────────────────────────────────────────

    def _append_audit(self, event: str, **fields: Any) -> None:
        """Whitelist stricte des champs : server_id, owner_profile,
        from_status, to_status, status, trust_score, reason, ts,
        semantic_category, decision_source, prefer_over_native.
        INTERDIT : display_name, package_spec, version, notes,
        stringification d'entry."""
        record = {"ts": _now_iso(), "event": event, **fields}
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"[mcp.server_catalog] audit write failed: {e}")

    # ── Load helpers ──────────────────────────────────────────────────────

    def _load_entry_from_path(
        self, file_path: Path, *, expected_stem: Optional[str] = None
    ) -> Optional[ServerEntry]:
        """Charge une entry depuis un fichier en vérifiant HMAC + binding."""
        stem = expected_stem or file_path.stem
        record = self._read_record(file_path)
        if record is None:
            self._append_audit(
                "integrity_invalid", server_id=stem, reason="read_failed"
            )
            return None
        if not self._verify_hmac(record):
            self._append_audit(
                "integrity_invalid", server_id=stem, reason="hmac_mismatch"
            )
            return None
        entry_dict = record.get("entry") or {}
        if not self._check_binding(entry_dict, file_path):
            self._append_audit(
                "binding_mismatch", server_id=stem, reason="binding_mismatch"
            )
            return None
        entry = self._dict_to_entry(entry_dict)
        if entry is None:
            self._append_audit(
                "integrity_invalid", server_id=stem, reason="entry_malformed"
            )
            return None
        return entry

    # ══════════════════════════════════════════════════════════════════════
    # API publique
    # ══════════════════════════════════════════════════════════════════════

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
        capability_tags: Optional[Any] = None,
    ) -> ServerEntry:
        """Ajoute un server au catalog avec status initial DECLARED.

        Raises CatalogError si validation échoue ou si server_id existe déjà.
        """
        _validate_server_id(server_id)
        _validate_display_name(display_name)
        _validate_package_spec(package_spec)
        _validate_version(version)
        _validate_owner_profile(owner_profile)
        _validate_trust_score(trust_score)
        _validate_notes(notes)
        _validate_capability_tags(capability_tags)

        if self._server_path(server_id).exists():
            raise CatalogError("server_already_exists")

        now = _now_iso()
        entry = ServerEntry(
            server_id=server_id,
            display_name=display_name,
            package_spec=package_spec,
            version=version,
            owner_profile=owner_profile,
            trust_score=trust_score,
            status=ServerStatus.DECLARED,
            added_at=now,
            updated_at=now,
            last_active_at=None,
            notes=notes,
            capability_tags=(
                tuple(capability_tags) if capability_tags else None
            ),
        )
        self._persist(entry)
        self._append_audit(
            "server_added",
            server_id=server_id,
            owner_profile=owner_profile,
            status=ServerStatus.DECLARED.value,
            trust_score=trust_score,
        )
        return entry

    def redeclare_server(
        self,
        *,
        server_id: str,
        display_name: str,
        package_spec: str,
        owner_profile: str,
        version: Optional[str] = None,
        trust_score: Optional[int] = None,
        notes: Optional[str] = None,
        capability_tags: Optional[Any] = None,
    ) -> ServerEntry:
        """Phase I-8 (Fix AJ) : re-déclare un server REMOVED.

        REMOVED reste terminal pour update_status (aucune transition) —
        mais un NOUVEAU catalog_add approuvé par l'humain est un nouveau
        consentement : il recrée l'entrée en DECLARED. Sans ça, un package
        supprimé une fois (server_id = hash déterministe du package_spec)
        était définitivement inréinstallable (observé runtime 2026-06-11 :
        bitcoin-mcp supprimé via le panel → tout re-add silencieusement
        no-op).

        Raises CatalogError si l'entrée n'existe pas ou n'est pas REMOVED
        (pour un id libre, utiliser add_server).
        """
        _validate_server_id(server_id)
        _validate_display_name(display_name)
        _validate_package_spec(package_spec)
        _validate_version(version)
        _validate_owner_profile(owner_profile)
        _validate_trust_score(trust_score)
        _validate_notes(notes)
        _validate_capability_tags(capability_tags)

        existing = self.get_server(server_id)
        if existing is None:
            raise CatalogError("server_unknown")
        if existing.status != ServerStatus.REMOVED:
            raise CatalogError("server_not_removed")

        now = _now_iso()
        entry = ServerEntry(
            server_id=server_id,
            display_name=display_name,
            package_spec=package_spec,
            version=version,
            owner_profile=owner_profile,
            trust_score=trust_score,
            status=ServerStatus.DECLARED,
            added_at=now,
            updated_at=now,
            last_active_at=None,
            notes=notes,
            capability_tags=(
                tuple(capability_tags) if capability_tags else None
            ),
        )
        self._persist(entry)
        self._append_audit(
            "server_redeclared",
            server_id=server_id,
            owner_profile=owner_profile,
            from_status=ServerStatus.REMOVED.value,
            to_status=ServerStatus.DECLARED.value,
            trust_score=trust_score,
        )
        return entry

    def get_server(self, server_id: str) -> Optional[ServerEntry]:
        _validate_server_id(server_id)
        path = self._server_path(server_id)
        if not path.exists():
            return None
        return self._load_entry_from_path(path, expected_stem=server_id)

    def list_servers(
        self,
        *,
        status_filter: Optional[ServerStatus] = None,
        owner_profile_filter: Optional[str] = None,
        include_removed: bool = False,
    ) -> List[ServerEntry]:
        """Liste les servers. Par défaut exclut REMOVED.

        Pour récupérer les removed : include_removed=True.
        """
        if status_filter is not None and not isinstance(status_filter, ServerStatus):
            raise CatalogError("context_invalid:status_filter_type")
        if owner_profile_filter is not None:
            _validate_owner_profile(owner_profile_filter)
        out: List[ServerEntry] = []
        if not self._servers_dir.exists():
            return out
        for file_path in sorted(self._servers_dir.glob("*.json")):
            stem = file_path.stem
            if not _SERVER_ID_RE.match(stem):
                continue
            entry = self._load_entry_from_path(file_path, expected_stem=stem)
            if entry is None:
                continue
            if not include_removed and entry.status == ServerStatus.REMOVED:
                continue
            if status_filter is not None and entry.status != status_filter:
                continue
            if (
                owner_profile_filter is not None
                and entry.owner_profile != owner_profile_filter
            ):
                continue
            out.append(entry)
        return out

    def update_status(
        self, server_id: str, new_status: ServerStatus
    ) -> ServerEntry:
        """Transition de statut bornée par _ALLOWED_TRANSITIONS.

        same → same refusé (table sans self-loop).
        Transition vers ACTIVE met last_active_at automatiquement.
        """
        _validate_server_id(server_id)
        if not isinstance(new_status, ServerStatus):
            raise CatalogError("context_invalid:new_status_type")
        entry = self.get_server(server_id)
        if entry is None:
            raise CatalogError("server_not_found")
        allowed = _ALLOWED_TRANSITIONS.get(entry.status, frozenset())
        if new_status not in allowed:
            raise CatalogError(
                f"status_transition_invalid:{entry.status.value}:{new_status.value}"
            )
        now = _now_iso()
        new_last_active = (
            now if new_status == ServerStatus.ACTIVE else entry.last_active_at
        )
        new_entry = replace(
            entry,
            status=new_status,
            updated_at=now,
            last_active_at=new_last_active,
        )
        self._persist(new_entry)
        self._append_audit(
            "server_status_changed",
            server_id=server_id,
            owner_profile=entry.owner_profile,
            from_status=entry.status.value,
            to_status=new_status.value,
        )
        return new_entry

    def update_trust_score(
        self, server_id: str, trust_score: int
    ) -> ServerEntry:
        _validate_server_id(server_id)
        _validate_trust_score(trust_score)
        if trust_score is None:
            # Trust score mutable mais on n'accepte pas None ici (set explicite uniquement)
            raise CatalogError("context_invalid:trust_score_none")
        entry = self.get_server(server_id)
        if entry is None:
            raise CatalogError("server_not_found")
        now = _now_iso()
        new_entry = replace(entry, trust_score=trust_score, updated_at=now)
        self._persist(new_entry)
        self._append_audit(
            "server_trust_score_updated",
            server_id=server_id,
            owner_profile=entry.owner_profile,
            trust_score=trust_score,
        )
        return new_entry

    def update_last_active(self, server_id: str) -> ServerEntry:
        _validate_server_id(server_id)
        entry = self.get_server(server_id)
        if entry is None:
            raise CatalogError("server_not_found")
        now = _now_iso()
        new_entry = replace(entry, last_active_at=now, updated_at=now)
        self._persist(new_entry)
        self._append_audit(
            "server_last_active_updated",
            server_id=server_id,
        )
        return new_entry

    # ── Phase B : catégorie sémantique + préférence native ───────────────

    def update_semantic_category(
        self,
        server_id: str,
        new_category: Optional[str],
        source: str,
    ) -> ServerEntry:
        """Met à jour la catégorie sémantique d'un server MCP.

        new_category=None autorisé pour réinitialiser la catégorie.
        source doit ∈ _VALID_DECISION_SOURCES (static/heuristic/llm/fallback/
        user_override).
        """
        _validate_server_id(server_id)
        _validate_semantic_category(new_category)
        _validate_decision_source(source)
        entry = self.get_server(server_id)
        if entry is None:
            raise CatalogError("server_not_found")
        now = _now_iso()
        new_entry = replace(
            entry,
            semantic_category=new_category,
            category_decision_source=source,
            updated_at=now,
        )
        self._persist(new_entry)
        self._append_audit(
            "server_semantic_category_updated",
            server_id=server_id,
            owner_profile=entry.owner_profile,
            semantic_category=new_category if new_category is not None else "",
            decision_source=source,
        )
        return new_entry

    def update_start_entry_args(
        self,
        server_id: str,
        entry_args: Optional[Any],
    ) -> ServerEntry:
        """Phase I-8 (Fix AY) : persiste la sous-commande serveur découverte
        réactivement par l'activation (entry point console = CLI à
        sous-commandes, ex. windows-mcp → ("serve",)).

        entry_args=None autorisé pour réinitialiser (entry point direct).
        Validation stricte : liste/tuple de str non vides, max 4 éléments,
        chaque élément slug court (pas d'injection shell — la commande est
        passée en argv, jamais via un shell, mais on reste conservateur).
        """
        _validate_server_id(server_id)
        normalized: Optional[Tuple[str, ...]] = None
        if entry_args is not None:
            if not isinstance(entry_args, (list, tuple)):
                raise CatalogError("context_invalid:start_entry_args")
            if len(entry_args) > 4:
                raise CatalogError("context_invalid:start_entry_args_too_many")
            for a in entry_args:
                if not isinstance(a, str) or not a or len(a) > 32:
                    raise CatalogError("context_invalid:start_entry_args")
                if not all(c.isalnum() or c in "_-" for c in a):
                    raise CatalogError("context_invalid:start_entry_args")
            normalized = tuple(entry_args)
        entry = self.get_server(server_id)
        if entry is None:
            raise CatalogError("server_not_found")
        now = _now_iso()
        new_entry = replace(
            entry,
            start_entry_args=normalized,
            updated_at=now,
        )
        self._persist(new_entry)
        self._append_audit(
            "server_start_entry_args_updated",
            server_id=server_id,
            owner_profile=entry.owner_profile,
            start_entry_args=" ".join(normalized) if normalized else "",
        )
        return new_entry

    def update_prefer_over_native(
        self, server_id: str, prefer: bool
    ) -> ServerEntry:
        """Toggle la préférence MCP sur outil natif quand catégories
        coïncident. bool strict requis."""
        _validate_server_id(server_id)
        _validate_prefer_over_native(prefer)
        entry = self.get_server(server_id)
        if entry is None:
            raise CatalogError("server_not_found")
        now = _now_iso()
        new_entry = replace(entry, prefer_over_native=prefer, updated_at=now)
        self._persist(new_entry)
        self._append_audit(
            "server_prefer_over_native_updated",
            server_id=server_id,
            owner_profile=entry.owner_profile,
            prefer_over_native=prefer,
        )
        return new_entry

    # ── Phase I-1 : config_schema (persistance schéma curated/détecté) ──────

    def update_config_schema(
        self,
        server_id: str,
        config_schema: Optional[Dict[str, Any]],
    ) -> ServerEntry:
        """Met à jour le schéma de configuration persisté pour ce serveur.

        Le schéma est un dict serialisable JSON produit par
        `config_schema.schema_to_dict()`. Passer None efface le schéma.
        """
        _validate_server_id(server_id)
        if config_schema is not None and not isinstance(config_schema, dict):
            raise CatalogError("context_invalid:config_schema_type")
        entry = self.get_server(server_id)
        if entry is None:
            raise CatalogError("server_not_found")
        now = _now_iso()
        new_entry = replace(entry, config_schema=config_schema, updated_at=now)
        self._persist(new_entry)
        # Audit minimal : juste server_id + source si présente.
        detected_from = ""
        if isinstance(config_schema, dict):
            df = config_schema.get("detected_from")
            if isinstance(df, str):
                detected_from = df
        self._append_audit(
            "server_config_schema_updated",
            server_id=server_id,
            owner_profile=entry.owner_profile,
            detected_from=detected_from,
        )
        return new_entry

    def remove_server(self, server_id: str) -> bool:
        """Soft-delete : status → REMOVED. Idempotent.

        Returns True si le server existait et a été passé à REMOVED.
        Returns False si server inconnu OU déjà REMOVED.
        """
        _validate_server_id(server_id)
        entry = self.get_server(server_id)
        if entry is None:
            return False
        if entry.status == ServerStatus.REMOVED:
            return False
        now = _now_iso()
        new_entry = replace(
            entry,
            status=ServerStatus.REMOVED,
            updated_at=now,
        )
        self._persist(new_entry)
        self._append_audit(
            "server_removed",
            server_id=server_id,
            owner_profile=entry.owner_profile,
            from_status=entry.status.value,
            to_status=ServerStatus.REMOVED.value,
        )
        return True

    def is_callable(self, server_id: str) -> bool:
        """True ssi status == ACTIVE. INSTALLED ne suffit pas."""
        try:
            entry = self.get_server(server_id)
        except CatalogError:
            return False
        return entry is not None and entry.status == ServerStatus.ACTIVE

    def is_known(self, server_id: str) -> bool:
        """True ssi entry présent et status != REMOVED."""
        try:
            entry = self.get_server(server_id)
        except CatalogError:
            return False
        return entry is not None and entry.status != ServerStatus.REMOVED
