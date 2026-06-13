"""
auto_approve.py — Auto-Approve Patterns bornés (Phase 11 v3).

Engine permettant de définir des règles précises pour auto-approuver
certaines actions MCP sans passer par ApprovalQueue Phase 10.

DOCTRINE Phase 11 :
  - Side effects CONTRÔLÉS : evaluate() écrit quota + audit sur MATCHED,
    JAMAIS d'exécution d'outil MCP. Le caller (orchestrateur futur)
    décide quoi faire avec la décision retournée.
  - Aucun câblage runtime (tool_registry, _mcp_policy_check)
  - Patterns chiffrés Fernet + signés HMAC-SHA256 (intégrité LOCALE,
    pas signature d'approbation humaine)
  - Garde-fous stricts à add_pattern : 4 axes (tool_name, policy,
    caller_kind, args), DSL stricte, glob borné par policy, expiration

Hors scope :
  - Câblage _mcp_policy_check (phase ultérieure)
  - UI panel (Phase 13)
  - Génération automatique par LLM
  - Rotation audit log

⚠️ HMAC = INTÉGRITÉ LOCALE seulement
La clé HMAC est stockée via SecretsService (keyring local). Toute personne
ayant accès au keyring peut signer un pattern. Le HMAC garantit la
détection de modifications manuelles du fichier, PAS l'approbation
humaine. La validation par Charles doit se faire au moment du
add_pattern (UI Phase 13 ou étape externe).

Convention de mapping args_constraints → args :
  - to_allowlist        → args["to"] (str ou list)
  - channel_allowlist   → args["channel"]
  - url_allowlist       → args["url"]
  - account_allowlist   → args["account_id"]
  - recipient_allowlist → args["recipient"]
  - subject_max_chars   → len(str(args["subject"]))
  - body_max_chars      → len(str(args["body"]))
  - amount_max_eur      → args["amount_eur"] OU
                          args["amount"] + args["currency"]=="EUR"
  - amount_max_usd      → idem USD
  - attachments_forbidden=True → args["attachments"] falsy ou absent

Layout disque :
    DATA_DIR/mcp_auto_approve/patterns/<profile>/<id>.json    (Fernet + HMAC)
    DATA_DIR/mcp_auto_approve_audit/audit.jsonl               (append-only)
    DATA_DIR/mcp_auto_approve_quotas/<profile>/<id>_<YYYY-MM-DD>.count
"""
from __future__ import annotations

import hmac
import hashlib
import json
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from cryptography.fernet import Fernet, InvalidToken
from filelock import FileLock, Timeout
from loguru import logger

from src.mcp.policy import MCPPolicy
from src.services.secrets_service import SecretsService, get_secrets_service
from src.utils.paths import DATA_DIR
from src.utils.persistence import atomic_write_json, safe_read_json


# ──────────────────────────────────────────────────────────────────────────────
# Constantes et configuration
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_PATTERNS_DIRNAME = "mcp_auto_approve"
_DEFAULT_AUDIT_DIRNAME = "mcp_auto_approve_audit"
_DEFAULT_QUOTAS_DIRNAME = "mcp_auto_approve_quotas"
_AUDIT_FILENAME = "audit.jsonl"

_FERNET_KEY_SCOPE = "lumena_global"
_FERNET_KEY_NAME = "MCP_AUTO_APPROVE_FERNET_KEY"
_HMAC_KEY_NAME = "MCP_AUTO_APPROVE_HMAC_KEY"

_DEFAULT_MAX_LIFETIME_DAYS = 90
_LOCK_TIMEOUT_S = 5.0
_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
_PROFILE_RE = re.compile(r"^[a-z0-9_-]+$")

# Caller kinds valides (cohérent avec src/reasoning/caller_context.py)
_VALID_CALLER_KINDS: FrozenSet[str] = frozenset(
    {"react", "codeagent", "autonomy", "scheduler", "daemon", "silent"}
)

# Tool name minimum prefix : refuse "*" ou préfixes trop courts
_TOOL_NAME_MIN_PREFIX_LEN = 8  # "mcp__a__" min
_TOOL_NAME_EXACT_RE = re.compile(r"^mcp__[A-Za-z0-9_\-.]+__[A-Za-z0-9_\-.]+$")
_TOOL_NAME_GLOB_RE = re.compile(r"^mcp__[A-Za-z0-9_\-.]+__\*$")

# Policies autorisées pour glob (READ_ONLY / EXTERNAL_READ uniquement)
_GLOB_ALLOWED_POLICIES: FrozenSet[MCPPolicy] = frozenset(
    {MCPPolicy.READ_ONLY, MCPPolicy.EXTERNAL_READ}
)

# DSL stricte : whitelist des clés autorisées dans args_constraints
_KNOWN_CONSTRAINT_KEYS: FrozenSet[str] = frozenset(
    {
        "to_allowlist",
        "channel_allowlist",
        "url_allowlist",
        "account_allowlist",
        "recipient_allowlist",
        "subject_max_chars",
        "body_max_chars",
        "amount_max_eur",
        "amount_max_usd",
        "attachments_forbidden",
    }
)

_CONSTRAINT_KEY_TYPES: Dict[str, Tuple[type, ...]] = {
    "to_allowlist": (list,),
    "channel_allowlist": (list,),
    "url_allowlist": (list,),
    "account_allowlist": (list,),
    "recipient_allowlist": (list,),
    "subject_max_chars": (int,),
    "body_max_chars": (int,),
    "amount_max_eur": (int, float),
    "amount_max_usd": (int, float),
    "attachments_forbidden": (bool,),
}

# Mapping allowlist → args field (pour evaluate)
_ALLOWLIST_TO_ARGS_FIELD: Dict[str, str] = {
    "to_allowlist": "to",
    "channel_allowlist": "channel",
    "url_allowlist": "url",
    "account_allowlist": "account_id",
    "recipient_allowlist": "recipient",
}

# Mapping max_chars → args field
_MAX_CHARS_TO_ARGS_FIELD: Dict[str, str] = {
    "subject_max_chars": "subject",
    "body_max_chars": "body",
}


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────


class AutoApproveError(Exception):
    """Erreur générique de l'engine auto-approve."""


# ──────────────────────────────────────────────────────────────────────────────
# Enums et dataclasses
# ──────────────────────────────────────────────────────────────────────────────


class AutoApproveDecision(Enum):
    MATCHED = "matched"
    NO_MATCH = "no_match"
    QUOTA_EXCEEDED = "quota_exceeded"
    EXPIRED = "expired"
    INTEGRITY_INVALID = "integrity_invalid"
    CONSTRAINTS_VIOLATED = "constraints_violated"
    POLICY_MISMATCH = "policy_mismatch"
    CALLER_NOT_ALLOWED = "caller_not_allowed"


@dataclass(frozen=True)
class AutoApprovePattern:
    id: str
    profile: str
    kind: str
    tool_name_pattern: str
    policy: MCPPolicy
    caller_kinds_allowed: List[str]
    args_constraints: Dict[str, Any]
    quota_max_per_day: int
    expires_at: str
    created_at: str


@dataclass(frozen=True)
class AutoApproveEvaluation:
    decision: AutoApproveDecision
    matched_pattern_id: Optional[str] = None
    reason: Optional[str] = None
    quota_consumed: bool = False


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(iso_str: str) -> Optional[datetime]:
    if not isinstance(iso_str, str) or not iso_str:
        return None
    s = iso_str
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _is_valid_pattern_id(pattern_id: Any) -> bool:
    """UUID4 hex strict (comme Phase 10)."""
    if not isinstance(pattern_id, str):
        return False
    if not _HEX32_RE.match(pattern_id):
        return False
    try:
        parsed = uuid.UUID(pattern_id)
    except (ValueError, TypeError, AttributeError):
        return False
    return parsed.version == 4 and parsed.hex == pattern_id


def _validate_pattern_id(pattern_id: Any) -> None:
    if not _is_valid_pattern_id(pattern_id):
        raise AutoApproveError(
            f"Invalid pattern_id: must be uuid4().hex, got {pattern_id!r}"
        )


def _validate_profile(profile: Any) -> None:
    if not isinstance(profile, str) or not profile or not _PROFILE_RE.match(profile):
        raise AutoApproveError(
            f"Invalid profile: must match [a-z0-9_-]+, got {profile!r}"
        )


def _canonical_json_for_hmac(pattern_record: Dict[str, Any]) -> bytes:
    """Sérialisation canonique stable (tri clés) pour calcul HMAC.

    Exclut le champ integrity_hmac lui-même.
    """
    payload = {k: v for k, v in pattern_record.items() if k != "integrity_hmac"}
    # MCPPolicy déjà sérialisé en str (pattern.policy.value)
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")


def _compute_integrity_hmac(pattern_record: Dict[str, Any], hmac_key: bytes) -> str:
    """HMAC-SHA256 hex sur le canonical JSON du pattern."""
    canonical = _canonical_json_for_hmac(pattern_record)
    return hmac.new(hmac_key, canonical, hashlib.sha256).hexdigest()


def _safe_audit_reason(violation_kind: str, constraint_name: str = "") -> str:
    """Construit un reason sans PII : kind ou kind:constraint_name uniquement.

    JAMAIS d'args values, JAMAIS d'URLs/emails/usernames.
    """
    if constraint_name:
        return f"{violation_kind}:{constraint_name}"
    return violation_kind


# ──────────────────────────────────────────────────────────────────────────────
# Validation add_pattern (garde-fous)
# ──────────────────────────────────────────────────────────────────────────────


def _validate_kind(kind: Any) -> None:
    if not isinstance(kind, str) or not kind.strip():
        raise AutoApproveError(f"Invalid kind: must be non-empty string, got {kind!r}")


def _validate_tool_name_pattern(tool_name_pattern: Any, policy: MCPPolicy) -> None:
    """Glob autorisé seulement pour READ_ONLY / EXTERNAL_READ.
    Pour les autres policies, exact match obligatoire.
    """
    if not isinstance(tool_name_pattern, str) or not tool_name_pattern.strip():
        raise AutoApproveError(
            f"Invalid tool_name_pattern: must be non-empty string, got {tool_name_pattern!r}"
        )
    s = tool_name_pattern.strip()
    if len(s) < _TOOL_NAME_MIN_PREFIX_LEN:
        raise AutoApproveError(
            f"tool_name_pattern too short (min {_TOOL_NAME_MIN_PREFIX_LEN} chars): {s!r}"
        )
    if s == "*" or s == "mcp__*" or s == "**":
        raise AutoApproveError(
            f"tool_name_pattern too broad (no wildcards-only): {s!r}"
        )
    # Exact ?
    if _TOOL_NAME_EXACT_RE.match(s):
        return
    # Glob bornée mcp__server__* ?
    if _TOOL_NAME_GLOB_RE.match(s):
        if policy not in _GLOB_ALLOWED_POLICIES:
            raise AutoApproveError(
                f"Glob tool_name_pattern only allowed for "
                f"READ_ONLY or EXTERNAL_READ policy. Got policy={policy.value}, "
                f"pattern={s!r}. Use exact tool name."
            )
        return
    raise AutoApproveError(
        f"tool_name_pattern must be exact 'mcp__server__tool' or glob "
        f"'mcp__server__*' (latter only for READ_ONLY/EXTERNAL_READ): {s!r}"
    )


def _validate_caller_kinds(caller_kinds_allowed: Any) -> None:
    if not isinstance(caller_kinds_allowed, list) or not caller_kinds_allowed:
        raise AutoApproveError(
            "caller_kinds_allowed must be a non-empty list"
        )
    for kind in caller_kinds_allowed:
        if not isinstance(kind, str):
            raise AutoApproveError(
                f"caller_kinds_allowed entry must be str, got {type(kind).__name__}"
            )
        if kind not in _VALID_CALLER_KINDS:
            raise AutoApproveError(
                f"Unknown caller_kind {kind!r}. "
                f"Valid: {sorted(_VALID_CALLER_KINDS)}"
            )


def _validate_args_constraints(args_constraints: Any) -> Dict[str, Any]:
    """Valide DSL stricte et retourne le dict normalisé (avec
    attachments_forbidden=True par défaut si absent)."""
    if not isinstance(args_constraints, dict):
        raise AutoApproveError(
            f"args_constraints must be a dict, got {type(args_constraints).__name__}"
        )
    if not args_constraints:
        raise AutoApproveError(
            "args_constraints must be non-empty (unconditional rules refused)"
        )
    normalized: Dict[str, Any] = {}
    for key, value in args_constraints.items():
        if key not in _KNOWN_CONSTRAINT_KEYS:
            raise AutoApproveError(
                f"Unknown constraint key {key!r}. "
                f"Known keys: {sorted(_KNOWN_CONSTRAINT_KEYS)}"
            )
        expected_types = _CONSTRAINT_KEY_TYPES[key]
        if not isinstance(value, expected_types) or isinstance(value, bool) and bool not in expected_types:
            # bool est sous-classe de int : si key attend int, refuse bool
            type_names = ", ".join(t.__name__ for t in expected_types)
            raise AutoApproveError(
                f"Constraint {key!r} must be of type {type_names}, "
                f"got {type(value).__name__}"
            )
        # Validations spécifiques
        if key.endswith("_allowlist"):
            if len(value) == 0:
                raise AutoApproveError(
                    f"Constraint {key!r} must be a non-empty list"
                )
            for entry in value:
                if not isinstance(entry, str) or not entry.strip():
                    raise AutoApproveError(
                        f"Constraint {key!r} entries must be non-empty strings"
                    )
        elif key.endswith("_max_chars"):
            if value <= 0:
                raise AutoApproveError(
                    f"Constraint {key!r} must be > 0, got {value}"
                )
        elif key.startswith("amount_max_"):
            if value <= 0:
                raise AutoApproveError(
                    f"Constraint {key!r} must be > 0, got {value}"
                )
        normalized[key] = value
    # Défaut : attachments_forbidden=True si absent
    if "attachments_forbidden" not in normalized:
        normalized["attachments_forbidden"] = True
    return normalized


def _validate_quota(quota_max_per_day: Any) -> None:
    if isinstance(quota_max_per_day, bool):
        raise AutoApproveError("quota_max_per_day must be int (not bool)")
    if not isinstance(quota_max_per_day, int):
        raise AutoApproveError(
            f"quota_max_per_day must be int, got {type(quota_max_per_day).__name__}"
        )
    if quota_max_per_day <= 0:
        raise AutoApproveError(
            f"quota_max_per_day must be > 0, got {quota_max_per_day}"
        )


def _validate_expires_at(
    expires_at: Any, *, now: datetime, max_lifetime_days: int
) -> None:
    parsed = _parse_iso(expires_at) if isinstance(expires_at, str) else None
    if parsed is None:
        raise AutoApproveError(
            f"expires_at must be ISO 8601 string, got {expires_at!r}"
        )
    if parsed <= now:
        raise AutoApproveError("expires_at must be in the future")
    max_dt = now + timedelta(days=max_lifetime_days)
    if parsed > max_dt:
        raise AutoApproveError(
            f"expires_at exceeds max lifetime ({max_lifetime_days} days)"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Engine
# ──────────────────────────────────────────────────────────────────────────────


class AutoApproveEngine:
    """Engine d'auto-approbation Phase 11."""

    def __init__(
        self,
        patterns_dir: Optional[Path] = None,
        audit_log_path: Optional[Path] = None,
        quotas_dir: Optional[Path] = None,
        secrets_service: Optional[SecretsService] = None,
        default_max_lifetime_days: int = _DEFAULT_MAX_LIFETIME_DAYS,
    ):
        self._patterns_root = patterns_dir or (
            DATA_DIR / _DEFAULT_PATTERNS_DIRNAME / "patterns"
        )
        self._audit_log_path = audit_log_path or (
            DATA_DIR / _DEFAULT_AUDIT_DIRNAME / _AUDIT_FILENAME
        )
        self._quotas_root = quotas_dir or (DATA_DIR / _DEFAULT_QUOTAS_DIRNAME)
        self._secrets = secrets_service
        self._max_lifetime_days = default_max_lifetime_days
        self._cipher: Optional[Fernet] = None
        self._hmac_key: Optional[bytes] = None

        self._patterns_root.mkdir(parents=True, exist_ok=True)
        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._quotas_root.mkdir(parents=True, exist_ok=True)

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def patterns_root(self) -> Path:
        return self._patterns_root

    @property
    def audit_log_path(self) -> Path:
        return self._audit_log_path

    @property
    def quotas_root(self) -> Path:
        return self._quotas_root

    @property
    def max_lifetime_days(self) -> int:
        return self._max_lifetime_days

    # ── Clés ──────────────────────────────────────────────────────────────

    def _get_secrets_service(self) -> SecretsService:
        if self._secrets is None:
            self._secrets = get_secrets_service()
        return self._secrets

    def _get_cipher(self) -> Fernet:
        if self._cipher is not None:
            return self._cipher
        svc = self._get_secrets_service()
        key_str = svc.get(_FERNET_KEY_SCOPE, _FERNET_KEY_NAME)
        if not key_str:
            key_bytes = Fernet.generate_key()
            svc.set(_FERNET_KEY_SCOPE, _FERNET_KEY_NAME, key_bytes.decode("utf-8"))
            key_str = key_bytes.decode("utf-8")
            logger.info(
                "[mcp.auto_approve] Fernet key generated in SecretsService"
            )
        try:
            self._cipher = Fernet(key_str.encode("utf-8"))
        except (InvalidToken, ValueError) as e:
            raise AutoApproveError(
                f"MCP_AUTO_APPROVE_FERNET_KEY corrupted: {e}"
            ) from e
        return self._cipher

    def _get_hmac_key(self) -> bytes:
        if self._hmac_key is not None:
            return self._hmac_key
        svc = self._get_secrets_service()
        key_str = svc.get(_FERNET_KEY_SCOPE, _HMAC_KEY_NAME)
        if not key_str:
            # 32 bytes random encodés base64 (lisible/stockable)
            import secrets as _secrets
            key_bytes = _secrets.token_bytes(32)
            import base64
            key_str = base64.b64encode(key_bytes).decode("ascii")
            svc.set(_FERNET_KEY_SCOPE, _HMAC_KEY_NAME, key_str)
            logger.info(
                "[mcp.auto_approve] HMAC key generated in SecretsService"
            )
        import base64
        try:
            self._hmac_key = base64.b64decode(key_str)
        except (ValueError, TypeError) as e:
            raise AutoApproveError(
                f"MCP_AUTO_APPROVE_HMAC_KEY corrupted: {e}"
            ) from e
        return self._hmac_key

    # ── Paths helpers ─────────────────────────────────────────────────────

    def _profile_dir(self, profile: str) -> Path:
        return self._patterns_root / profile

    def _pattern_path(self, profile: str, pattern_id: str) -> Path:
        return self._profile_dir(profile) / f"{pattern_id}.json"

    def _quota_path(self, profile: str, pattern_id: str, day_str: str) -> Path:
        return self._quotas_root / profile / f"{pattern_id}_{day_str}.count"

    def _quota_lock_path(self, profile: str, pattern_id: str, day_str: str) -> Path:
        return self._quotas_root / profile / f"{pattern_id}_{day_str}.lock"

    # ── Audit (sans PII) ──────────────────────────────────────────────────

    def _append_audit(self, event: str, **fields: Any) -> None:
        """Append-only au audit.jsonl. AUCUNE valeur d'args attendue dans fields."""
        record = {
            "ts": _now_iso(),
            "event": event,
            **fields,
        }
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"[mcp.auto_approve] audit write failed: {e}")

    # ── Quota tracking ────────────────────────────────────────────────────

    @staticmethod
    def _day_str(now: Optional[datetime] = None) -> str:
        cur = now or _now_utc()
        return cur.strftime("%Y-%m-%d")

    @contextmanager
    def _quota_lock(self, profile: str, pattern_id: str, day_str: str):
        lock_path = self._quota_lock_path(profile, pattern_id, day_str)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(lock_path), timeout=_LOCK_TIMEOUT_S)
        try:
            with lock:
                yield
        except Timeout as e:
            raise AutoApproveError(
                f"Could not acquire quota lock within {_LOCK_TIMEOUT_S}s"
            ) from e

    def _read_quota(self, profile: str, pattern_id: str, day_str: str) -> int:
        path = self._quota_path(profile, pattern_id, day_str)
        if not path.exists():
            return 0
        try:
            return int(path.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            return 0

    def _write_quota(
        self, profile: str, pattern_id: str, day_str: str, value: int
    ) -> None:
        """Écriture atomique via tmp + os.replace.

        Le FileLock protège la concurrence multi-process, l'os.replace
        protège contre les crashes mid-write (jamais de fichier tronqué).
        """
        path = self._quota_path(profile, pattern_id, day_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(str(value), encoding="utf-8")
        tmp_path.replace(path)

    def get_quota_consumed(
        self, pattern_id: str, day: Optional[str] = None
    ) -> int:
        """Retourne le compteur quota pour pattern_id et day (ou aujourd'hui)."""
        _validate_pattern_id(pattern_id)
        day_str = day or self._day_str()
        # Cherche dans tous les profiles
        for profile_dir in self._quotas_root.iterdir() if self._quotas_root.exists() else []:
            if not profile_dir.is_dir():
                continue
            candidate = profile_dir / f"{pattern_id}_{day_str}.count"
            if candidate.exists():
                try:
                    return int(candidate.read_text(encoding="utf-8").strip() or "0")
                except (OSError, ValueError):
                    return 0
        return 0

    def reset_quota(
        self, pattern_id: str, day: Optional[str] = None
    ) -> bool:
        """Admin/debug : remet à zéro le quota d'un pattern pour un jour."""
        _validate_pattern_id(pattern_id)
        day_str = day or self._day_str()
        removed = False
        for profile_dir in self._quotas_root.iterdir() if self._quotas_root.exists() else []:
            if not profile_dir.is_dir():
                continue
            candidate = profile_dir / f"{pattern_id}_{day_str}.count"
            if candidate.exists():
                candidate.unlink(missing_ok=True)
                removed = True
        return removed

    # ── Sérialisation pattern ─────────────────────────────────────────────

    def _serialize_and_encrypt_pattern(
        self, pattern: AutoApprovePattern
    ) -> Dict[str, Any]:
        """Sérialise puis chiffre le pattern pour stockage disque.

        Étapes :
          1. Construit le record en clair (tous les champs métier).
          2. Calcule l'HMAC-SHA256 local sur le canonical JSON du record
             (clé locale via SecretsService, tri stable des clés).
          3. Ajoute le champ `integrity_hmac` au record.
          4. Chiffre l'ensemble (record + hmac) avec Fernet et retourne
             un wrapper `{"ciphertext": "..."}`.

        Le record complet est chiffré Fernet pour confidentialité at-rest ;
        l'HMAC garantit l'intégrité du payload canonique avant chiffrement.
        Le binding fichier↔contenu (id + profile vs path) est vérifié
        séparément au load par `_check_binding`.
        """
        plain_record = {
            "id": pattern.id,
            "profile": pattern.profile,
            "kind": pattern.kind,
            "tool_name_pattern": pattern.tool_name_pattern,
            "policy": pattern.policy.value,
            "caller_kinds_allowed": list(pattern.caller_kinds_allowed),
            "args_constraints": dict(pattern.args_constraints),
            "quota_max_per_day": pattern.quota_max_per_day,
            "expires_at": pattern.expires_at,
            "created_at": pattern.created_at,
        }
        hmac_key = self._get_hmac_key()
        integrity = _compute_integrity_hmac(plain_record, hmac_key)
        plain_record["integrity_hmac"] = integrity

        # Chiffrement Fernet du record entier
        cipher = self._get_cipher()
        plaintext = json.dumps(plain_record, ensure_ascii=False).encode("utf-8")
        ciphertext = cipher.encrypt(plaintext).decode("utf-8")
        return {"ciphertext": ciphertext}

    def _read_and_decrypt_pattern_record(
        self, file_path: Path
    ) -> Optional[Dict[str, Any]]:
        """Déchiffre le record. Retourne None si invalide."""
        data = safe_read_json(file_path, default=None)
        if not isinstance(data, dict):
            return None
        ciphertext = data.get("ciphertext")
        if not isinstance(ciphertext, str) or not ciphertext:
            return None
        try:
            cipher = self._get_cipher()
            plaintext = cipher.decrypt(ciphertext.encode("utf-8"))
            record = json.loads(plaintext.decode("utf-8"))
        except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(record, dict):
            return None
        return record

    def _verify_integrity(self, record: Dict[str, Any]) -> bool:
        """True si HMAC valide, False sinon."""
        provided = record.get("integrity_hmac")
        if not isinstance(provided, str) or not provided:
            return False
        try:
            hmac_key = self._get_hmac_key()
            expected = _compute_integrity_hmac(record, hmac_key)
        except AutoApproveError:
            return False
        return hmac.compare_digest(provided, expected)

    @staticmethod
    def _check_binding(
        record: Dict[str, Any], file_path: Path, expected_profile: str
    ) -> bool:
        """Vérifie binding fichier ↔ contenu.

        Empêche : copier un pattern valide (HMAC OK) d'un profile vers un
        autre, ou renommer un fichier vers un autre UUID. Le HMAC seul ne
        détecte pas ces déplacements car il signe le contenu, pas le chemin.

        Returns True ssi record["id"] == file_path.stem ET
        record["profile"] == expected_profile.
        """
        rid = record.get("id")
        rprofile = record.get("profile")
        if not isinstance(rid, str) or rid != file_path.stem:
            return False
        if not isinstance(rprofile, str) or rprofile != expected_profile:
            return False
        return True

    def _record_to_pattern(self, record: Dict[str, Any]) -> Optional[AutoApprovePattern]:
        try:
            return AutoApprovePattern(
                id=str(record["id"]),
                profile=str(record["profile"]),
                kind=str(record["kind"]),
                tool_name_pattern=str(record["tool_name_pattern"]),
                policy=MCPPolicy(record["policy"]),
                caller_kinds_allowed=list(record["caller_kinds_allowed"]),
                args_constraints=dict(record["args_constraints"]),
                quota_max_per_day=int(record["quota_max_per_day"]),
                expires_at=str(record["expires_at"]),
                created_at=str(record["created_at"]),
            )
        except (KeyError, ValueError, TypeError):
            return None

    # ── API publique : add / remove / list / get ──────────────────────────

    def add_pattern(
        self,
        *,
        profile: str,
        kind: str,
        tool_name_pattern: str,
        policy: MCPPolicy,
        caller_kinds_allowed: List[str],
        args_constraints: Dict[str, Any],
        quota_max_per_day: int,
        expires_at: str,
    ) -> str:
        """Crée, valide, signe et persiste un pattern.

        Returns: pattern_id (uuid4().hex).
        """
        # Validations
        _validate_profile(profile)
        _validate_kind(kind)
        if not isinstance(policy, MCPPolicy):
            raise AutoApproveError(
                f"policy must be MCPPolicy, got {type(policy).__name__}"
            )
        _validate_tool_name_pattern(tool_name_pattern, policy)
        _validate_caller_kinds(caller_kinds_allowed)
        normalized_constraints = _validate_args_constraints(args_constraints)
        _validate_quota(quota_max_per_day)
        now = _now_utc()
        _validate_expires_at(
            expires_at, now=now, max_lifetime_days=self._max_lifetime_days
        )

        pattern_id = uuid.uuid4().hex
        pattern = AutoApprovePattern(
            id=pattern_id,
            profile=profile,
            kind=kind,
            tool_name_pattern=tool_name_pattern.strip(),
            policy=policy,
            caller_kinds_allowed=list(caller_kinds_allowed),
            args_constraints=normalized_constraints,
            quota_max_per_day=quota_max_per_day,
            expires_at=expires_at,
            created_at=now.isoformat(),
        )

        wrapped = self._serialize_and_encrypt_pattern(pattern)
        self._profile_dir(profile).mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._pattern_path(profile, pattern_id), wrapped)

        self._append_audit(
            "pattern_added",
            pattern_id=pattern_id,
            profile=profile,
            kind=kind,
            policy=policy.value,
        )
        return pattern_id

    def remove_pattern(self, pattern_id: str) -> bool:
        """Idempotent : supprime le pattern de tous les profiles si trouvé."""
        _validate_pattern_id(pattern_id)
        removed = False
        for profile_dir in self._patterns_root.iterdir() if self._patterns_root.exists() else []:
            if not profile_dir.is_dir():
                continue
            candidate = profile_dir / f"{pattern_id}.json"
            if candidate.exists():
                candidate.unlink(missing_ok=True)
                removed = True
                self._append_audit(
                    "pattern_removed",
                    pattern_id=pattern_id,
                    profile=profile_dir.name,
                )
        return removed

    def list_patterns(self, profile: Optional[str] = None) -> List[AutoApprovePattern]:
        """Liste les patterns valides (HMAC + binding OK).

        Patterns invalides (HMAC mismatch, déchiffrement échoué, ou binding
        fichier↔contenu mismatch) sont silencieusement skipped.
        """
        out: List[AutoApprovePattern] = []
        if profile is not None:
            _validate_profile(profile)
            profile_dirs = [self._profile_dir(profile)]
        else:
            profile_dirs = [
                d for d in (self._patterns_root.iterdir() if self._patterns_root.exists() else [])
                if d.is_dir()
            ]
        for profile_dir in profile_dirs:
            if not profile_dir.exists() or not profile_dir.is_dir():
                continue
            for file_path in sorted(profile_dir.glob("*.json")):
                record = self._read_and_decrypt_pattern_record(file_path)
                if record is None:
                    continue
                if not self._verify_integrity(record):
                    continue
                if not self._check_binding(record, file_path, profile_dir.name):
                    continue
                pat = self._record_to_pattern(record)
                if pat is not None:
                    out.append(pat)
        return out

    def get_pattern(self, pattern_id: str) -> Optional[AutoApprovePattern]:
        _validate_pattern_id(pattern_id)
        for profile_dir in self._patterns_root.iterdir() if self._patterns_root.exists() else []:
            if not profile_dir.is_dir():
                continue
            candidate = profile_dir / f"{pattern_id}.json"
            if candidate.exists():
                record = self._read_and_decrypt_pattern_record(candidate)
                if record is None:
                    return None
                if not self._verify_integrity(record):
                    return None
                if not self._check_binding(record, candidate, profile_dir.name):
                    return None
                return self._record_to_pattern(record)
        return None

    # ── Matching helpers ──────────────────────────────────────────────────

    @staticmethod
    def _tool_name_matches(pattern: str, tool_name: str) -> bool:
        if pattern == tool_name:
            return True
        if pattern.endswith("__*"):
            prefix = pattern[:-1]  # garde "mcp__server__"
            return tool_name.startswith(prefix)
        return False

    @staticmethod
    def _check_allowlist(
        value: Any, allowlist: List[str]
    ) -> bool:
        """True si value (str ou list de str) ⊆ allowlist."""
        if value is None:
            return False
        allow_set = set(allowlist)
        if isinstance(value, str):
            return value in allow_set
        if isinstance(value, list):
            return all(isinstance(v, str) and v in allow_set for v in value)
        return False

    @staticmethod
    def _check_amount_constraint(
        max_value: float,
        currency_code: str,  # "EUR" ou "USD"
        args: Dict[str, Any],
    ) -> bool:
        """True si le montant est <= max_value pour la devise donnée.

        Lit dans cet ordre :
          1. args[f"amount_{lowercase currency_code}"] direct
          2. args["amount"] + args["currency"].upper() == currency_code
        Si ni l'un ni l'autre disponible, ou currency mismatch → False.
        """
        direct_key = f"amount_{currency_code.lower()}"
        if direct_key in args:
            try:
                amount = float(args[direct_key])
            except (TypeError, ValueError):
                return False
            return amount <= max_value
        # Fallback : amount + currency
        if "amount" in args and "currency" in args:
            try:
                amount = float(args["amount"])
            except (TypeError, ValueError):
                return False
            currency_arg = args["currency"]
            if not isinstance(currency_arg, str):
                return False
            if currency_arg.upper() != currency_code.upper():
                return False
            return amount <= max_value
        return False

    def _check_constraints(
        self,
        constraints: Dict[str, Any],
        args: Dict[str, Any],
    ) -> Tuple[bool, Optional[str]]:
        """Vérifie toutes les contraintes. Retourne (ok, constraint_name_si_violation).

        Le constraint_name retourné est UNIQUEMENT le nom de la contrainte
        (jamais d'args values).
        """
        for key, value in constraints.items():
            if key in _ALLOWLIST_TO_ARGS_FIELD:
                arg_field = _ALLOWLIST_TO_ARGS_FIELD[key]
                arg_value = args.get(arg_field)
                if not self._check_allowlist(arg_value, value):
                    return False, key
            elif key in _MAX_CHARS_TO_ARGS_FIELD:
                arg_field = _MAX_CHARS_TO_ARGS_FIELD[key]
                arg_value = args.get(arg_field, "")
                if not isinstance(arg_value, str):
                    arg_value = str(arg_value)
                if len(arg_value) > value:
                    return False, key
            elif key == "amount_max_eur":
                if not self._check_amount_constraint(float(value), "EUR", args):
                    return False, key
            elif key == "amount_max_usd":
                if not self._check_amount_constraint(float(value), "USD", args):
                    return False, key
            elif key == "attachments_forbidden":
                if value is True:
                    if args.get("attachments"):
                        return False, key
        return True, None

    # ── evaluate ──────────────────────────────────────────────────────────

    def evaluate(
        self,
        *,
        profile: str,
        tool_name: str,
        args: Dict[str, Any],
        policy: MCPPolicy,
        caller_kind: str,
    ) -> AutoApproveEvaluation:
        """Évalue si l'action peut être auto-approuvée.

        Quota consumed only on MATCHED. Security audit may be appended for
        matched, integrity_invalid, policy_mismatch, caller_not_allowed,
        constraints_violated, quota_exceeded. No MCP tool execution.

        Audit reasons never include args values nor business fields from
        tampered patterns (integrity_invalid events use file.stem and the
        requested profile only).

        Returns : AutoApproveEvaluation avec décision détaillée.

        Hiérarchie de retour si plusieurs patterns matchent tool_name :
            MATCHED (premier trouvé) > INTEGRITY_INVALID > POLICY_MISMATCH
            > CALLER_NOT_ALLOWED > CONSTRAINTS_VIOLATED > QUOTA_EXCEEDED
            > EXPIRED > NO_MATCH
        """
        _validate_profile(profile)
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise AutoApproveError(f"Invalid tool_name: {tool_name!r}")
        if not isinstance(args, dict):
            raise AutoApproveError("args must be a dict")
        if not isinstance(policy, MCPPolicy):
            raise AutoApproveError(
                f"policy must be MCPPolicy, got {type(policy).__name__}"
            )
        if not isinstance(caller_kind, str) or not caller_kind.strip():
            raise AutoApproveError(f"Invalid caller_kind: {caller_kind!r}")

        profile_dir = self._profile_dir(profile)
        if not profile_dir.exists():
            return AutoApproveEvaluation(decision=AutoApproveDecision.NO_MATCH)

        # Tracking des décisions par priorité (en cas de multi-patterns)
        found_integrity_invalid = False
        found_policy_mismatch: Optional[str] = None
        found_caller_not_allowed: Optional[str] = None
        found_constraints_violated: Optional[Tuple[str, str]] = None  # (pid, constraint)
        found_quota_exceeded: Optional[str] = None
        found_expired = False

        now = _now_utc()
        day_str = self._day_str(now)

        for file_path in sorted(profile_dir.glob("*.json")):
            # Pattern_id depuis nom de fichier (fiable même si HMAC invalide)
            file_pattern_id = file_path.stem
            if not _is_valid_pattern_id(file_pattern_id):
                continue

            record = self._read_and_decrypt_pattern_record(file_path)
            if record is None:
                # Pas de déchiffrement = traitement intégrité invalide
                if not found_integrity_invalid:
                    found_integrity_invalid = True
                    self._append_audit(
                        "integrity_invalid",
                        pattern_id=file_pattern_id,
                        profile=profile,
                        reason=_safe_audit_reason("decrypt_failed"),
                    )
                continue

            # ⚠️ Vérif HMAC AVANT de faire confiance aux champs métier
            if not self._verify_integrity(record):
                if not found_integrity_invalid:
                    found_integrity_invalid = True
                    self._append_audit(
                        "integrity_invalid",
                        pattern_id=file_pattern_id,
                        profile=profile,
                        reason=_safe_audit_reason("hmac_mismatch"),
                    )
                continue

            # ⚠️ Vérif binding fichier ↔ contenu : empêche les copies/renommages
            # d'un pattern valide pour exfiltrer ses droits vers un autre
            # profile ou id. L'audit n'utilise QUE file.stem et profile (le
            # dossier), JAMAIS record["id"]/record["profile"] qui pourraient
            # être ceux du pattern original copié.
            if not self._check_binding(record, file_path, profile):
                if not found_integrity_invalid:
                    found_integrity_invalid = True
                    self._append_audit(
                        "integrity_invalid",
                        pattern_id=file_pattern_id,
                        profile=profile,
                        reason=_safe_audit_reason("binding_mismatch"),
                    )
                continue

            pattern = self._record_to_pattern(record)
            if pattern is None:
                continue

            # 1. tool_name match ?
            if not self._tool_name_matches(pattern.tool_name_pattern, tool_name):
                continue

            # 2. policy match ?
            if pattern.policy != policy:
                if found_policy_mismatch is None:
                    found_policy_mismatch = pattern.id
                continue

            # 3. caller_kind allowed ?
            if caller_kind not in pattern.caller_kinds_allowed:
                if found_caller_not_allowed is None:
                    found_caller_not_allowed = pattern.id
                continue

            # 4. expired ?
            exp_dt = _parse_iso(pattern.expires_at)
            if exp_dt is None or now >= exp_dt:
                found_expired = True
                continue

            # 5. constraints ?
            ok, constraint_violated = self._check_constraints(
                pattern.args_constraints, args
            )
            if not ok:
                if found_constraints_violated is None:
                    found_constraints_violated = (pattern.id, constraint_violated or "")
                continue

            # 6. quota ?
            with self._quota_lock(pattern.profile, pattern.id, day_str):
                current_count = self._read_quota(pattern.profile, pattern.id, day_str)
                if current_count >= pattern.quota_max_per_day:
                    if found_quota_exceeded is None:
                        found_quota_exceeded = pattern.id
                    continue
                # 7. MATCHED : increment quota + audit
                self._write_quota(
                    pattern.profile, pattern.id, day_str, current_count + 1
                )
                self._append_audit(
                    "evaluation_matched",
                    pattern_id=pattern.id,
                    profile=pattern.profile,
                    tool_name=tool_name,
                    policy=policy.value,
                    caller_kind=caller_kind,
                    reason=_safe_audit_reason("matched"),
                )
                return AutoApproveEvaluation(
                    decision=AutoApproveDecision.MATCHED,
                    matched_pattern_id=pattern.id,
                    quota_consumed=True,
                )

        # Aucun MATCHED. Retourne décision hiérarchique la plus précise trouvée.
        if found_integrity_invalid:
            return AutoApproveEvaluation(
                decision=AutoApproveDecision.INTEGRITY_INVALID,
                reason=_safe_audit_reason("integrity_invalid"),
            )
        if found_policy_mismatch is not None:
            self._append_audit(
                "evaluation_policy_mismatch",
                pattern_id=found_policy_mismatch,
                profile=profile,
                tool_name=tool_name,
                expected_policy=policy.value,
                reason=_safe_audit_reason("policy_mismatch"),
            )
            return AutoApproveEvaluation(
                decision=AutoApproveDecision.POLICY_MISMATCH,
                matched_pattern_id=found_policy_mismatch,
                reason=_safe_audit_reason("policy_mismatch"),
            )
        if found_caller_not_allowed is not None:
            self._append_audit(
                "evaluation_caller_not_allowed",
                pattern_id=found_caller_not_allowed,
                profile=profile,
                tool_name=tool_name,
                reason=_safe_audit_reason("caller_not_allowed"),
            )
            return AutoApproveEvaluation(
                decision=AutoApproveDecision.CALLER_NOT_ALLOWED,
                matched_pattern_id=found_caller_not_allowed,
                reason=_safe_audit_reason("caller_not_allowed"),
            )
        if found_constraints_violated is not None:
            pid, constraint = found_constraints_violated
            self._append_audit(
                "evaluation_constraints_violated",
                pattern_id=pid,
                profile=profile,
                tool_name=tool_name,
                reason=_safe_audit_reason("constraint_violated", constraint),
            )
            return AutoApproveEvaluation(
                decision=AutoApproveDecision.CONSTRAINTS_VIOLATED,
                matched_pattern_id=pid,
                reason=_safe_audit_reason("constraint_violated", constraint),
            )
        if found_quota_exceeded is not None:
            self._append_audit(
                "evaluation_quota_exceeded",
                pattern_id=found_quota_exceeded,
                profile=profile,
                tool_name=tool_name,
                reason=_safe_audit_reason("quota_exceeded"),
            )
            return AutoApproveEvaluation(
                decision=AutoApproveDecision.QUOTA_EXCEEDED,
                matched_pattern_id=found_quota_exceeded,
                reason=_safe_audit_reason("quota_exceeded"),
            )
        if found_expired:
            return AutoApproveEvaluation(decision=AutoApproveDecision.EXPIRED)
        return AutoApproveEvaluation(decision=AutoApproveDecision.NO_MATCH)
