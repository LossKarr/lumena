"""
secrets_service.py — Coffre-fort local de secrets pour Lumena (Phase 4).

Chiffrement at-rest via Fernet (clé maître locale). Organisation par
**scopes** isolés. Migration progressive opt-in depuis `.env`.

Doctrine Phase 4 (cf REPO/PLAN_MCP_LUMENA.md v4.1 + THREAT_MODEL_MCP.md §12) :
  - 0 secret MCP en clair dans YAML/JSON/profils
  - `.env` legacy toléré, JAMAIS modifié par Phase 4
  - Migration `.env` → SecretsService = opt-in CLI uniquement
  - Allowlist explicite pour injection runtime subprocess (jamais "tout le scope")
  - Chemins via src.utils.paths.DATA_DIR (pas Path("data/...") brut)
  - chmod best-effort POSIX, Windows ACL NON durcies en Phase 4
  - FileLock par scope autour des read-modify-write
  - Scrubbing logs = helper utilisable, PAS de filtre loguru global

Modules existants utilisant os.getenv() ne sont PAS modifiés en Phase 4.
Le bridge `get_env_or_secret()` permettra migration progressive PR par PR.
"""
from __future__ import annotations

import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken
from filelock import FileLock, Timeout
from loguru import logger

from src.utils.paths import DATA_DIR
from src.utils.persistence import atomic_write_json, safe_read_json


# ──────────────────────────────────────────────────────────────────────────────
# Constantes et helpers
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_SECRETS_DIR_NAME = "secrets"
_MASTER_KEY_FILENAME = ".lumena_secrets.key"
_LOCK_TIMEOUT_SECONDS = 10.0
_SECRET_REF_RE = re.compile(r"^\$secret:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$")


class SecretsServiceError(Exception):
    """Erreur générique du SecretsService."""


class CorruptedKeyError(SecretsServiceError):
    """Clé maître Fernet corrompue ou illisible."""


def is_secret_ref(value: str) -> bool:
    """True si `value` est une référence `$secret:<scope>/<key>` valide.

    Format strict. Utilisé pour valider profils MCP (Phase 5+) :
    une valeur doit être soit littérale (non secret), soit une ref `$secret:`.
    """
    if not isinstance(value, str):
        return False
    return bool(_SECRET_REF_RE.match(value))


def parse_secret_ref(value: str) -> Optional[tuple]:
    """Parse `$secret:<scope>/<key>` → (scope, key) ou None."""
    if not isinstance(value, str):
        return None
    m = _SECRET_REF_RE.match(value)
    if m is None:
        return None
    return (m.group(1), m.group(2))


# ──────────────────────────────────────────────────────────────────────────────
# Permissions best-effort
# ──────────────────────────────────────────────────────────────────────────────


def _set_secure_perms(path: Path, mode: int = 0o600) -> None:
    """Tente d'appliquer des permissions restrictives.

    POSIX : chmod best-effort. Échec silencieux si non supporté.
    Windows : ACL **non durcies en Phase 4** (limite documentée).
    """
    if sys.platform == "win32":
        # Windows ACL durcissement = futur (out of scope Phase 4)
        return
    try:
        os.chmod(path, mode)
    except OSError:
        # Filesystem ne supporte pas chmod (ex: certains montages)
        pass


# ──────────────────────────────────────────────────────────────────────────────
# Scrubbing de secrets dans les logs (helper, pas de filtre global)
# ──────────────────────────────────────────────────────────────────────────────


def scrub_secrets_in_text(
    text: str,
    known_values: List[str],
    placeholder: str = "****",
) -> str:
    """Remplace les valeurs sensibles connues par un placeholder.

    Helper PUR — n'installe aucun filtre loguru global.
    À appeler explicitement avant d'écrire dans un log.

    Args:
        text: texte à scrubber
        known_values: liste de valeurs sensibles à remplacer
        placeholder: chaîne de remplacement

    Returns:
        Texte avec les valeurs masquées.
    """
    if not isinstance(text, str) or not text:
        return text or ""
    result = text
    # On itère du plus long au plus court pour éviter qu'un préfixe
    # n'écrase une substring plus longue
    for value in sorted(known_values, key=len, reverse=True):
        if value and isinstance(value, str) and len(value) >= 4:
            result = result.replace(value, placeholder)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# SecretsService principal
# ──────────────────────────────────────────────────────────────────────────────


class SecretsService:
    """Coffre local chiffré Fernet, organisé par scopes isolés.

    Storage layout (sous DATA_DIR par défaut) :
        data/.lumena_secrets.key            → clé maître Fernet (640 POSIX)
        data/secrets/<scope>.fernet.json    → blob chiffré par scope

    Format blob :
        Le fichier .fernet.json contient un dict JSON :
            {"ciphertext": "<base64 fernet token>"}
        Le ciphertext, déchiffré, est un JSON `{"<key>": "<value>", ...}`.

    Scopes :
        - "lumena_global"           : secrets historiques Lumena
        - "mcp.<server_name>"       : secrets d'un MCP donné
        - "profile.<profile_name>"  : secrets d'un profil utilisateur

    Garanties :
        - Aucune valeur loggée par cette classe
        - list_keys retourne NOMS uniquement
        - Atomic write via persistence.atomic_write_json
        - FileLock par scope autour des read-modify-write
    """

    def __init__(
        self,
        secrets_dir: Optional[Path] = None,
        master_key_path: Optional[Path] = None,
    ):
        """Initialise le service.

        Args:
            secrets_dir: dossier des blobs chiffrés. Défaut : DATA_DIR/secrets/
            master_key_path: chemin de la clé maître. Défaut : DATA_DIR/.lumena_secrets.key

        DATA_DIR est résolu depuis src.utils.paths (respecte LUMENA_DATA_DIR env).
        """
        self._secrets_dir = secrets_dir or (DATA_DIR / _DEFAULT_SECRETS_DIR_NAME)
        self._master_key_path = master_key_path or (DATA_DIR / _MASTER_KEY_FILENAME)
        self._secrets_dir.mkdir(parents=True, exist_ok=True)
        self._master_key_path.parent.mkdir(parents=True, exist_ok=True)
        self._fernet: Optional[Fernet] = None

    # ── Master key management ─────────────────────────────────────────────

    def _get_fernet(self) -> Fernet:
        """Retourne le cipher Fernet, le crée si nécessaire."""
        if self._fernet is not None:
            return self._fernet

        if self._master_key_path.exists():
            try:
                raw = self._master_key_path.read_bytes().strip()
                if len(raw) < 10:
                    raise CorruptedKeyError(
                        f"Master key file {self._master_key_path} corrupted or empty. "
                        "Delete it to regenerate (warning: all secrets will become unreadable)."
                    )
                self._fernet = Fernet(raw)
            except CorruptedKeyError:
                raise
            except (InvalidToken, ValueError) as e:
                raise CorruptedKeyError(
                    f"Master key file {self._master_key_path} is invalid: {e}. "
                    "Delete it to regenerate (warning: all secrets will become unreadable)."
                ) from e
        else:
            key = Fernet.generate_key()
            self._master_key_path.write_bytes(key)
            _set_secure_perms(self._master_key_path, mode=0o600)
            self._fernet = Fernet(key)
            logger.info(
                "[secrets] Clé maître générée → {} (POSIX: chmod 0o600 best-effort, "
                "Windows: ACL non durcies en Phase 4)",
                self._master_key_path,
            )
        return self._fernet

    # ── Internal storage ──────────────────────────────────────────────────

    def _scope_path(self, scope: str) -> Path:
        if not scope or not isinstance(scope, str):
            raise SecretsServiceError(f"Invalid scope: {scope!r}")
        # Sanitize basique pour éviter path traversal via scope
        if "/" in scope or "\\" in scope or scope.startswith("."):
            raise SecretsServiceError(f"Invalid scope name: {scope!r}")
        return self._secrets_dir / f"{scope}.fernet.json"

    def _lock_path(self, scope: str) -> Path:
        return self._secrets_dir / f"{scope}.fernet.json.lock"

    @contextmanager
    def _scope_lock(self, scope: str):
        """File lock par scope, timeout borné."""
        lock_path = self._lock_path(scope)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(lock_path), timeout=_LOCK_TIMEOUT_SECONDS)
        try:
            with lock:
                yield
        except Timeout as e:
            raise SecretsServiceError(
                f"Could not acquire lock on scope {scope!r} within "
                f"{_LOCK_TIMEOUT_SECONDS}s"
            ) from e

    def _read_scope(self, scope: str) -> Dict[str, str]:
        """Lit et déchiffre le scope. Retourne dict vide si fichier absent."""
        path = self._scope_path(scope)
        if not path.exists():
            return {}
        blob = safe_read_json(path, default=None)
        if blob is None or not isinstance(blob, dict):
            return {}
        ciphertext = blob.get("ciphertext")
        if not ciphertext or not isinstance(ciphertext, str):
            return {}
        try:
            fernet = self._get_fernet()
            decrypted = fernet.decrypt(ciphertext.encode("utf-8"))
            data = json.loads(decrypted.decode("utf-8"))
            if not isinstance(data, dict):
                return {}
            return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
        except (InvalidToken, json.JSONDecodeError, UnicodeDecodeError):
            # Master key changée OU blob corrompu — fail safe : dict vide
            # Pas de log de la valeur, juste un warning
            logger.warning(
                "[secrets] Failed to decrypt scope {!r} — file may be corrupted "
                "or master key was rotated",
                scope,
            )
            return {}

    def _write_scope(self, scope: str, data: Dict[str, str]) -> None:
        """Chiffre et écrit atomiquement le scope."""
        fernet = self._get_fernet()
        plaintext = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ciphertext = fernet.encrypt(plaintext).decode("utf-8")
        path = self._scope_path(scope)
        atomic_write_json(path, {"ciphertext": ciphertext})
        _set_secure_perms(path, mode=0o600)

    # ── Public API ─────────────────────────────────────────────────────────

    def get(self, scope: str, key: str) -> Optional[str]:
        """Récupère un secret. Retourne None si absent."""
        if not key or not isinstance(key, str):
            return None
        with self._scope_lock(scope):
            data = self._read_scope(scope)
        return data.get(key)

    def set(self, scope: str, key: str, value: str) -> None:
        """Stocke un secret. Une chaîne vide équivaut à un delete.

        Politique stricte : `set(scope, key, "")` supprime la clé.
        Cela évite tout stockage de "secret vide" qui pollue la mémoire.
        """
        if not key or not isinstance(key, str):
            raise SecretsServiceError(f"Invalid key: {key!r}")
        if not isinstance(value, str):
            raise SecretsServiceError("Value must be a string")
        with self._scope_lock(scope):
            data = self._read_scope(scope)
            if value == "":
                data.pop(key, None)
            else:
                data[key] = value
            self._write_scope(scope, data)

    def delete(self, scope: str, key: str) -> bool:
        """Supprime un secret. Retourne True si la clé existait, False sinon."""
        if not key or not isinstance(key, str):
            return False
        with self._scope_lock(scope):
            data = self._read_scope(scope)
            if key not in data:
                return False
            data.pop(key, None)
            self._write_scope(scope, data)
            return True

    def has(self, scope: str, key: str) -> bool:
        """True si la clé existe dans le scope. N'lève pas si scope absent."""
        if not key or not isinstance(key, str):
            return False
        try:
            with self._scope_lock(scope):
                data = self._read_scope(scope)
            return key in data
        except SecretsServiceError:
            return False

    def list_keys(self, scope: str) -> List[str]:
        """Liste les NOMS de clés d'un scope. JAMAIS les valeurs."""
        with self._scope_lock(scope):
            data = self._read_scope(scope)
        return sorted(data.keys())

    def list_scopes(self) -> List[str]:
        """Liste les scopes existants (basé sur les fichiers présents)."""
        if not self._secrets_dir.exists():
            return []
        scopes: List[str] = []
        for path in self._secrets_dir.glob("*.fernet.json"):
            name = path.name[: -len(".fernet.json")]
            if name:
                scopes.append(name)
        return sorted(scopes)

    def export_for_subprocess(
        self, scope: str, keys: List[str]
    ) -> Dict[str, str]:
        """Exporte UN SOUS-ENSEMBLE de secrets d'un scope pour injection
        au subprocess (ex: env var d'un serveur MCP).

        Garanties :
          - Allowlist EXPLICITE de clés (jamais "tout le scope")
          - Clés non demandées ne sont JAMAIS exposées dans le retour
          - Aucune valeur loggée

        Args:
            scope: scope source
            keys: liste explicite des clés à exporter

        Returns:
            Dict {key: value} pour les clés demandées qui existent.
            Les clés demandées mais absentes sont OMISES du dict (pas mises à None).
        """
        if not isinstance(keys, list):
            raise SecretsServiceError("keys must be a list")
        if not all(isinstance(k, str) and k for k in keys):
            raise SecretsServiceError("All keys must be non-empty strings")
        requested = set(keys)
        with self._scope_lock(scope):
            data = self._read_scope(scope)
        # Filtre strict : ne retient QUE les clés demandées ET présentes
        return {k: data[k] for k in requested if k in data}

    # ── Bridge legacy .env ─────────────────────────────────────────────────

    def migrate_from_env(
        self,
        keys: List[str],
        scope: str = "lumena_global",
        remove_from_env: bool = False,
    ) -> Dict[str, str]:
        """Helper de migration .env → SecretsService.

        Phase 4 : `remove_from_env=False` STRICTEMENT (ne modifie jamais `.env`).
        Toute valeur True nécessitera (Phase ultérieure) une confirmation
        utilisateur explicite — c'est une politique de Phase 4.

        Args:
            keys: liste des noms de variables d'env à migrer
            scope: scope cible
            remove_from_env: IGNORÉ en Phase 4 — refusé si True

        Returns:
            Dict {key: "migrated" | "absent" | "exists_already"} pour chaque clé demandée.
        """
        if remove_from_env:
            raise SecretsServiceError(
                "remove_from_env=True is NOT supported in Phase 4 — .env must "
                "never be modified automatically. Manual removal required."
            )
        report: Dict[str, str] = {}
        for key in keys:
            if not isinstance(key, str) or not key:
                continue
            env_value = os.environ.get(key, "")
            if not env_value:
                report[key] = "absent"
                continue
            if self.has(scope, key):
                report[key] = "exists_already"
                continue
            self.set(scope, key, env_value)
            report[key] = "migrated"
        return report


# ──────────────────────────────────────────────────────────────────────────────
# Bridge utilitaire pour usage progressif dans le code existant
# ──────────────────────────────────────────────────────────────────────────────

_default_service: Optional[SecretsService] = None


def get_secrets_service() -> SecretsService:
    """Retourne le service par défaut (singleton léger)."""
    global _default_service
    if _default_service is None:
        _default_service = SecretsService()
    return _default_service


def reset_secrets_service_for_tests() -> None:
    """Reset le singleton pour isolation des tests."""
    global _default_service
    _default_service = None


def get_env_or_secret(
    key: str,
    scope: str = "lumena_global",
    service: Optional[SecretsService] = None,
) -> Optional[str]:
    """Bridge : SecretsService prioritaire, fallback os.getenv.

    Permet une migration progressive sans casser le code existant qui
    utilise os.getenv() partout.

    Args:
        key: nom de la variable / clé
        scope: scope SecretsService à consulter
        service: instance à utiliser (utile pour tests). Défaut : singleton.

    Returns:
        Valeur si trouvée dans SecretsService OU os.environ, sinon None.
    """
    svc = service if service is not None else get_secrets_service()
    try:
        value = svc.get(scope, key)
    except SecretsServiceError:
        value = None
    if value:
        return value
    env_value = os.environ.get(key, "")
    return env_value if env_value else None
