"""
approval_queue.py — Approval Queue MCP (Phase 10).

Queue persistante pour proposer une action MCP bloquée Phase 9 et attendre
une décision humaine. Actions chiffrées Fernet sur disque, expiration auto.

DOCTRINE Phase 10 :
  - Module ISOLÉ : aucun câblage runtime (tool_registry, policy)
  - Args JAMAIS écrits en clair sur disque (Fernet ciphertext uniquement)
  - Args retournés en clair UNIQUEMENT via approve() au caller
  - Clé Fernet stockée via SecretsService Phase 4 (auto-générée lazy)
  - FileLock par action_id : pas de double approve/reject concurrent
  - Validation stricte action_id (UUID4 hex 32 chars [0-9a-f])
  - Dossier decisions/ : audit append-only des décisions (PAS d'args)

Hors scope Phase 10 :
  - Câblage runtime _mcp_policy_check (phase ultérieure)
  - Notifications Charles (Telegram/Discord)
  - Auto-approve patterns signés (Phase 11)
  - UI panel (Phase 13)
  - Purge automatique de decisions/ (audit append-only Phase 10)

Layout disque :
    DATA_DIR/mcp_approvals/
        pending/<action_id>.json    # ciphertext args + métadonnées
        pending/<action_id>.lock    # FileLock par action
        decisions/<action_id>.json  # audit : decision + reason (pas d'args)
"""
from __future__ import annotations

import json
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken
from filelock import FileLock, Timeout
from loguru import logger

from src.mcp.policy import MCPPolicy
from src.services.secrets_service import SecretsService, get_secrets_service
from src.utils.paths import DATA_DIR
from src.utils.persistence import atomic_write_json, safe_read_json


# ──────────────────────────────────────────────────────────────────────────────
# Constantes et helpers
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_QUEUE_DIRNAME = "mcp_approvals"
_PENDING_SUBDIR = "pending"
_DECISIONS_SUBDIR = "decisions"
_DEFAULT_TTL_S = 1800.0  # 30 minutes
_LOCK_TIMEOUT_S = 5.0
_FERNET_KEY_SCOPE = "lumena_global"
_FERNET_KEY_NAME = "MCP_APPROVAL_FERNET_KEY"

_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions
# ──────────────────────────────────────────────────────────────────────────────


class ApprovalQueueError(Exception):
    """Erreur générique de la queue d'approbation MCP."""


def _is_valid_action_id(action_id: str) -> bool:
    """True si action_id est un UUID4 hex valide (format uuid.uuid4().hex).

    Validation stricte :
      - chaîne 32 chars [0-9a-f]
      - parse UUID OK
      - version == 4
      - round-trip hex identique (anti uppercase / tirets)
    """
    if not isinstance(action_id, str):
        return False
    if not _HEX32_RE.match(action_id):
        return False
    try:
        parsed = uuid.UUID(action_id)
    except (ValueError, TypeError, AttributeError):
        return False
    return parsed.version == 4 and parsed.hex == action_id


# ──────────────────────────────────────────────────────────────────────────────
# Enums et dataclasses
# ──────────────────────────────────────────────────────────────────────────────


class ApprovalDecision(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass(frozen=True)
class PendingAction:
    """Vue lecture d'une action en queue (PAS d'args bruts)."""

    id: str
    tool_name: str
    policy: MCPPolicy
    caller_kind: str
    risk_summary: str
    proposed_at: str  # ISO 8601
    expires_at: str   # ISO 8601


@dataclass(frozen=True)
class ApprovalResult:
    """Résultat d'un appel approve()."""

    decision: ApprovalDecision
    args: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None


@dataclass(frozen=True)
class ApprovalRequest:
    """Vue atomique complète pour un évaluateur local de confiance.

    Cette vue contient les args déchiffrés. Elle est réservée au code serveur
    local (ex: AutoApproveEngine), jamais aux routes ni aux réponses UI.
    """

    id: str
    tool_name: str
    policy: MCPPolicy
    caller_kind: str
    risk_summary: str
    proposed_at: str
    expires_at: str
    args: Dict[str, Any]


# ──────────────────────────────────────────────────────────────────────────────
# Validation action_id (UUID4 hex strict)
# ──────────────────────────────────────────────────────────────────────────────


def _validate_action_id(action_id: Any) -> None:
    """Refuse tout action_id qui n'est pas un UUID4 réel (uuid4().hex).

    Empêche path traversal, slashes, casse, et autres versions UUID
    (ex: "0"*32 = UUID version 0, refusé).
    """
    if not isinstance(action_id, str):
        raise ApprovalQueueError(
            f"Invalid action_id type: expected str, got {type(action_id).__name__}"
        )
    if not _is_valid_action_id(action_id):
        raise ApprovalQueueError(
            f"Invalid action_id: must be a uuid4().hex (32 lowercase [0-9a-f], "
            f"version=4). Got: {action_id!r}"
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


# ──────────────────────────────────────────────────────────────────────────────
# ApprovalQueue
# ──────────────────────────────────────────────────────────────────────────────


class ApprovalQueue:
    """Gère le cycle de vie des actions en attente d'approbation humaine.

    Pattern d'usage (Phase 10 isolée — câblage runtime futur) :
        queue = ApprovalQueue()
        action_id = queue.propose(
            tool_name="mcp__github__delete_repo",
            args={"name": "lossy/test"},
            policy=MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE,
            caller_kind="react",
            risk_summary="Delete repo lossy/test",
        )
        # ... Charles examine puis ...
        result = queue.approve(action_id)
        if result.decision == ApprovalDecision.APPROVED:
            # le caller exécute avec result.args
            ...

    Garanties :
      - Args chiffrés Fernet sur disque, jamais en clair
      - FileLock par action_id : pas de double approve concurrent
      - Validation action_id stricte (UUID4 hex)
      - Decisions historisées dans decisions/ (audit)
    """

    def __init__(
        self,
        queue_dir: Optional[Path] = None,
        secrets_service: Optional[SecretsService] = None,
        default_ttl_s: float = _DEFAULT_TTL_S,
    ):
        """Init queue.

        Args:
            queue_dir: dossier racine queue (défaut DATA_DIR/mcp_approvals/).
            secrets_service: instance pour clé Fernet (défaut singleton).
            default_ttl_s: TTL par défaut des propositions (1800s = 30 min).
        """
        self._queue_dir = queue_dir or (DATA_DIR / _DEFAULT_QUEUE_DIRNAME)
        self._pending_dir = self._queue_dir / _PENDING_SUBDIR
        self._decisions_dir = self._queue_dir / _DECISIONS_SUBDIR
        self._pending_dir.mkdir(parents=True, exist_ok=True)
        self._decisions_dir.mkdir(parents=True, exist_ok=True)
        self._secrets = secrets_service  # lazy si None
        self._default_ttl_s = default_ttl_s
        self._cipher: Optional[Fernet] = None

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def queue_dir(self) -> Path:
        return self._queue_dir

    @property
    def pending_dir(self) -> Path:
        return self._pending_dir

    @property
    def decisions_dir(self) -> Path:
        return self._decisions_dir

    @property
    def default_ttl_s(self) -> float:
        return self._default_ttl_s

    # ── Fernet management ─────────────────────────────────────────────────

    def _get_secrets_service(self) -> SecretsService:
        if self._secrets is None:
            self._secrets = get_secrets_service()
        return self._secrets

    def _get_cipher(self) -> Fernet:
        """Retourne le cipher Fernet, génère la clé si absente."""
        if self._cipher is not None:
            return self._cipher
        svc = self._get_secrets_service()
        key_str = svc.get(_FERNET_KEY_SCOPE, _FERNET_KEY_NAME)
        if not key_str:
            # Génération lazy + stockage
            key_bytes = Fernet.generate_key()
            svc.set(_FERNET_KEY_SCOPE, _FERNET_KEY_NAME, key_bytes.decode("utf-8"))
            key_str = key_bytes.decode("utf-8")
            logger.info(
                "[mcp.approval] Fernet key generated and stored in SecretsService"
            )
        try:
            self._cipher = Fernet(key_str.encode("utf-8"))
        except (InvalidToken, ValueError) as e:
            raise ApprovalQueueError(
                f"MCP_APPROVAL_FERNET_KEY corrupted in SecretsService: {e}"
            ) from e
        return self._cipher

    # ── Lock par action_id ────────────────────────────────────────────────

    @contextmanager
    def _action_lock(self, action_id: str):
        """FileLock par action_id, timeout 5s."""
        _validate_action_id(action_id)
        lock_path = self._pending_dir / f"{action_id}.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(lock_path), timeout=_LOCK_TIMEOUT_S)
        try:
            with lock:
                yield
        except Timeout as e:
            raise ApprovalQueueError(
                f"Could not acquire action lock for {action_id!r} within {_LOCK_TIMEOUT_S}s"
            ) from e

    # ── Paths helpers ─────────────────────────────────────────────────────

    def _pending_path(self, action_id: str) -> Path:
        return self._pending_dir / f"{action_id}.json"

    def _decision_path(self, action_id: str) -> Path:
        return self._decisions_dir / f"{action_id}.json"

    # ── Sérialisation / parsing ───────────────────────────────────────────

    def _read_pending_raw(self, action_id: str) -> Optional[Dict[str, Any]]:
        path = self._pending_path(action_id)
        if not path.exists():
            return None
        data = safe_read_json(path, default=None)
        if not isinstance(data, dict):
            return None
        return data

    def _build_pending_action(self, raw: Dict[str, Any]) -> Optional[PendingAction]:
        try:
            return PendingAction(
                id=str(raw["id"]),
                tool_name=str(raw["tool_name"]),
                policy=MCPPolicy(raw["policy"]),
                caller_kind=str(raw["caller_kind"]),
                risk_summary=str(raw.get("risk_summary", "")),
                proposed_at=str(raw["proposed_at"]),
                expires_at=str(raw["expires_at"]),
            )
        except (KeyError, ValueError, TypeError):
            return None

    def _is_raw_expired(self, raw: Dict[str, Any], *, now: Optional[datetime] = None) -> bool:
        exp = _parse_iso(raw.get("expires_at", ""))
        if exp is None:
            return False
        cur = now or datetime.now(timezone.utc)
        return cur >= exp

    def _record_decision(
        self,
        *,
        action_id: str,
        tool_name: str,
        policy_value: str,
        decision: ApprovalDecision,
        reason: Optional[str] = None,
    ) -> None:
        """Écrit le verdict dans decisions/ (audit append-only, jamais d'args)."""
        record = {
            "id": action_id,
            "tool_name": tool_name,
            "policy": policy_value,
            "decision": decision.value,
            "reason": reason,
            "decided_at": _now_iso(),
        }
        atomic_write_json(self._decision_path(action_id), record)

    # ── Validation propose ────────────────────────────────────────────────

    @staticmethod
    def _validate_tool_name(tool_name: Any) -> None:
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ApprovalQueueError(
                f"Invalid tool_name: {tool_name!r}"
            )

    @staticmethod
    def _validate_args(args: Any) -> None:
        if not isinstance(args, dict):
            raise ApprovalQueueError(
                f"args must be a dict, got {type(args).__name__}"
            )

    # ── API publique ──────────────────────────────────────────────────────

    def propose(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        policy: MCPPolicy,
        caller_kind: str,
        risk_summary: str,
        ttl_s: Optional[float] = None,
    ) -> str:
        """Soumet une action en queue. Args chiffrés Fernet à l'écriture.

        Returns:
            action_id (UUID4 hex 32 chars).
        """
        self._validate_tool_name(tool_name)
        self._validate_args(args)
        if not isinstance(policy, MCPPolicy):
            raise ApprovalQueueError(
                f"policy must be MCPPolicy, got {type(policy).__name__}"
            )
        if not isinstance(caller_kind, str) or not caller_kind.strip():
            raise ApprovalQueueError(f"Invalid caller_kind: {caller_kind!r}")
        if not isinstance(risk_summary, str):
            raise ApprovalQueueError("risk_summary must be a string")

        ttl = float(ttl_s) if ttl_s is not None else self._default_ttl_s
        if ttl <= 0:
            raise ApprovalQueueError(f"ttl_s must be > 0, got {ttl}")

        action_id = uuid.uuid4().hex  # 32 chars lowercase
        cipher = self._get_cipher()
        args_json = json.dumps(args, ensure_ascii=False).encode("utf-8")
        ciphertext = cipher.encrypt(args_json).decode("utf-8")

        now = datetime.now(timezone.utc)
        expires = now.replace(microsecond=0)
        # Calcul expires_at en ajoutant ttl secondes
        from datetime import timedelta
        expires_dt = now + timedelta(seconds=ttl)

        record = {
            "id": action_id,
            "tool_name": tool_name,
            "policy": policy.value,
            "caller_kind": caller_kind,
            "risk_summary": risk_summary,
            "proposed_at": now.isoformat(),
            "expires_at": expires_dt.isoformat(),
            "args_ciphertext": ciphertext,
        }
        atomic_write_json(self._pending_path(action_id), record)
        return action_id

    def list_pending(self) -> List[PendingAction]:
        """Retourne les actions encore valides (non expirées)."""
        if not self._pending_dir.exists():
            return []
        result: List[PendingAction] = []
        now = datetime.now(timezone.utc)
        for path in sorted(self._pending_dir.glob("*.json")):
            # Ignore les .lock (FileLock crée *.lock, pas *.json donc safe)
            data = safe_read_json(path, default=None)
            if not isinstance(data, dict):
                continue
            if self._is_raw_expired(data, now=now):
                continue
            entry = self._build_pending_action(data)
            if entry is not None:
                result.append(entry)
        return result

    def get(self, action_id: str) -> Optional[PendingAction]:
        """Action par id (None si absente, expirée, ou déjà décidée)."""
        _validate_action_id(action_id)
        raw = self._read_pending_raw(action_id)
        if raw is None:
            return None
        if self._is_raw_expired(raw):
            return None
        return self._build_pending_action(raw)

    def is_expired(self, action_id: str) -> bool:
        """True si l'action existe ET est expirée. False sinon (y compris inconnue)."""
        _validate_action_id(action_id)
        raw = self._read_pending_raw(action_id)
        if raw is None:
            return False
        return self._is_raw_expired(raw)

    def _decrypt_args_from_raw(self, action_id: str, raw: Dict[str, Any]) -> Dict[str, Any]:
        ciphertext = raw.get("args_ciphertext")
        if not isinstance(ciphertext, str) or not ciphertext:
            raise ApprovalQueueError(
                f"Missing args_ciphertext in pending action {action_id!r}"
            )
        cipher = self._get_cipher()
        try:
            plaintext = cipher.decrypt(ciphertext.encode("utf-8"))
            args = json.loads(plaintext.decode("utf-8"))
        except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ApprovalQueueError(
                f"Could not decrypt/parse args for {action_id!r}: {e}"
            ) from e
        if not isinstance(args, dict):
            raise ApprovalQueueError(
                f"Decrypted args is not a dict for {action_id!r}"
            )
        return args

    def approve_if(
        self,
        action_id: str,
        evaluator: Callable[[ApprovalRequest], bool],
    ) -> ApprovalResult:
        """Approve atomically only if a local evaluator accepts the request."""
        _validate_action_id(action_id)
        if not callable(evaluator):
            raise ApprovalQueueError("evaluator must be callable")
        with self._action_lock(action_id):
            raw = self._read_pending_raw(action_id)
            if raw is None:
                raise ApprovalQueueError(
                    f"Unknown action_id: {action_id!r}"
                )
            tool_name = str(raw.get("tool_name", ""))
            policy_value = str(raw.get("policy", ""))

            if self._is_raw_expired(raw):
                self._pending_path(action_id).unlink(missing_ok=True)
                self._record_decision(
                    action_id=action_id,
                    tool_name=tool_name,
                    policy_value=policy_value,
                    decision=ApprovalDecision.EXPIRED,
                    reason="ttl reached before approval",
                )
                return ApprovalResult(
                    decision=ApprovalDecision.EXPIRED,
                    reason="ttl reached before approval",
                )

            try:
                policy = MCPPolicy(policy_value)
            except ValueError as e:
                raise ApprovalQueueError(
                    f"Invalid policy in pending action {action_id!r}: {policy_value!r}"
                ) from e
            args = self._decrypt_args_from_raw(action_id, raw)
            request = ApprovalRequest(
                id=action_id,
                tool_name=tool_name,
                policy=policy,
                caller_kind=str(raw.get("caller_kind", "")),
                risk_summary=str(raw.get("risk_summary", "")),
                proposed_at=str(raw.get("proposed_at", "")),
                expires_at=str(raw.get("expires_at", "")),
                args=args,
            )
            try:
                allowed = bool(evaluator(request))
            except Exception as e:
                raise ApprovalQueueError(
                    f"Auto-approval evaluator failed for {action_id!r}"
                ) from e
            if not allowed:
                return ApprovalResult(
                    decision=ApprovalDecision.PENDING,
                    reason="auto_approve_not_matched",
                )

            self._pending_path(action_id).unlink(missing_ok=True)
            self._record_decision(
                action_id=action_id,
                tool_name=tool_name,
                policy_value=policy_value,
                decision=ApprovalDecision.APPROVED,
                reason="auto_approved",
            )
            return ApprovalResult(
                decision=ApprovalDecision.APPROVED,
                args=args,
                reason="auto_approved",
            )

    def approve(self, action_id: str) -> ApprovalResult:
        """Approuve : décrypte args, supprime de pending, enregistre decision.

        Raises:
            ApprovalQueueError si action_id inconnue, ou format/cipher invalides.
        """
        _validate_action_id(action_id)
        with self._action_lock(action_id):
            raw = self._read_pending_raw(action_id)
            if raw is None:
                raise ApprovalQueueError(
                    f"Unknown action_id: {action_id!r}"
                )
            tool_name = str(raw.get("tool_name", ""))
            policy_value = str(raw.get("policy", ""))

            if self._is_raw_expired(raw):
                self._pending_path(action_id).unlink(missing_ok=True)
                self._record_decision(
                    action_id=action_id,
                    tool_name=tool_name,
                    policy_value=policy_value,
                    decision=ApprovalDecision.EXPIRED,
                    reason="ttl reached before approval",
                )
                return ApprovalResult(
                    decision=ApprovalDecision.EXPIRED,
                    reason="ttl reached before approval",
                )

            args = self._decrypt_args_from_raw(action_id, raw)

            # Supprime de pending, écrit décision audit
            self._pending_path(action_id).unlink(missing_ok=True)
            self._record_decision(
                action_id=action_id,
                tool_name=tool_name,
                policy_value=policy_value,
                decision=ApprovalDecision.APPROVED,
                reason=None,
            )
            return ApprovalResult(
                decision=ApprovalDecision.APPROVED,
                args=args,
            )

    def reject(self, action_id: str, reason: str) -> bool:
        """Rejette : supprime de pending, enregistre decision.

        Returns:
            True si présente et retirée, False sinon (idempotent).
        """
        _validate_action_id(action_id)
        if not isinstance(reason, str):
            raise ApprovalQueueError("reason must be a string")
        with self._action_lock(action_id):
            raw = self._read_pending_raw(action_id)
            if raw is None:
                return False
            tool_name = str(raw.get("tool_name", ""))
            policy_value = str(raw.get("policy", ""))
            self._pending_path(action_id).unlink(missing_ok=True)
            self._record_decision(
                action_id=action_id,
                tool_name=tool_name,
                policy_value=policy_value,
                decision=ApprovalDecision.REJECTED,
                reason=reason,
            )
            return True

    def cleanup_expired(self) -> int:
        """Purge les actions expirées, sous FileLock par action_id.

        Returns: count purgées.

        Pattern :
          1. Première passe : scan + identification des candidats expirés
          2. Pour chaque candidat → prend FileLock(action_id) → re-vérifie
             (un autre worker pourrait avoir déjà géré) → supprime +
             audit decision
        """
        if not self._pending_dir.exists():
            return 0

        # Phase 1 : identification candidats (sans lock)
        candidates: List[str] = []
        now = datetime.now(timezone.utc)
        for path in list(self._pending_dir.glob("*.json")):
            data = safe_read_json(path, default=None)
            if not isinstance(data, dict):
                continue
            action_id = str(data.get("id", ""))
            # Skip si id absent/corrompu (pas de validation stricte ici car
            # on parcourt des fichiers, pas une entrée utilisateur)
            if not action_id or not _is_valid_action_id(action_id):
                continue
            if not self._is_raw_expired(data, now=now):
                continue
            candidates.append(action_id)

        # Phase 2 : pour chaque candidat, lock + re-check + suppression atomique
        count = 0
        for action_id in candidates:
            try:
                with self._action_lock(action_id):
                    raw = self._read_pending_raw(action_id)
                    if raw is None:
                        # Déjà supprimé par un autre worker
                        continue
                    if not self._is_raw_expired(raw, now=now):
                        # Re-vérification : plus expiré (improbable mais safe)
                        continue
                    tool_name = str(raw.get("tool_name", ""))
                    policy_value = str(raw.get("policy", ""))
                    self._pending_path(action_id).unlink(missing_ok=True)
                    self._record_decision(
                        action_id=action_id,
                        tool_name=tool_name,
                        policy_value=policy_value,
                        decision=ApprovalDecision.EXPIRED,
                        reason="cleanup_expired sweep",
                    )
                    count += 1
            except ApprovalQueueError:
                # Lock timeout : autre worker en cours, on passe
                continue
        return count
