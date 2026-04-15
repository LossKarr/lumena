"""
ionos_deployer.py — Service SFTP multi-sites pour IONOS.

Gère la connexion SFTP, l'upload, la suppression et le listing
de fichiers sur des hébergements IONOS. Stocke les credentials
de manière sécurisée dans data/ionos_sites.json (chiffré Fernet).
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import os
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

# ── Lazy imports (graceful degradation) ───────────────────────────────────

_paramiko = None


def _get_paramiko():
    global _paramiko
    if _paramiko is None:
        import paramiko
        _paramiko = paramiko
    return _paramiko


# ── Fernet helpers ────────────────────────────────────────────────────────

_fernet_cipher = None


def _get_fernet():
    """Return a Fernet instance, creating/loading the key from data/.ionos_key."""
    global _fernet_cipher
    if _fernet_cipher is not None:
        return _fernet_cipher

    from cryptography.fernet import Fernet

    key_path = Path("data/.ionos_key")
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        raw = key_path.read_bytes().strip()
        if len(raw) < 10:
            raise RuntimeError(
                "data/.ionos_key is corrupted. Delete it and re-add your IONOS sites."
            )
        _fernet_cipher = Fernet(raw)
    else:
        key = Fernet.generate_key()
        key_path.write_bytes(key)
        logger.info("[IONOS] Clé de chiffrement générée → data/.ionos_key")
        _fernet_cipher = Fernet(key)

    return _fernet_cipher


def _encrypt(text: str) -> str:
    return _get_fernet().encrypt(text.encode()).decode()


def _decrypt(token: str) -> str:
    return _get_fernet().decrypt(token.encode()).decode()


# ── Data models ───────────────────────────────────────────────────────────

# Files that must NEVER be uploaded (security)
_FORBIDDEN_FILES = frozenset({
    ".env", ".htpasswd", ".htaccess",
    "wp-config.php", "web.config",
})

_FORBIDDEN_EXTENSIONS = frozenset({
    ".sql", ".key", ".pem", ".crt", ".pfx",
    ".bak", ".dump", ".sqlite", ".db",
})

_SITES_PATH = Path("data/ionos_sites.json")
_BACKUPS_DIR = Path("data/ionos_backups")


@dataclass
class DeployResult:
    success: bool
    uploaded: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)
    total_bytes: int = 0
    duration_sec: float = 0.0
    dry_run: bool = False


@dataclass
class RemoteFile:
    path: str
    size: int
    is_dir: bool
    modified: Optional[str] = None


# ── Main service ──────────────────────────────────────────────────────────

class IonosDeployer:
    """Service SFTP multi-sites pour IONOS."""

    def __init__(self):
        self._sites: Dict[str, dict] = {}
        self._load_sites()

    # ── Persistence ───────────────────────────────────────────────────

    def _load_sites(self):
        if _SITES_PATH.exists():
            try:
                raw = json.loads(_SITES_PATH.read_text(encoding="utf-8"))
                self._sites = raw.get("sites", {})
            except Exception as e:
                logger.error(f"[IONOS] Erreur lecture {_SITES_PATH}: {e}")
                self._sites = {}

    def _save_sites(self):
        _SITES_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "sites": self._sites,
            "encryption_check": "lumena_ionos_v1",
        }
        tmp = _SITES_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_SITES_PATH)

    # ── Site management ───────────────────────────────────────────────

    def add_site(
        self,
        domain: str,
        host: str,
        user: str,
        password: str,
        port: int = 22,
        root: str = "/",
        label: str = "",
    ) -> dict:
        """Add or update a site configuration. Tests connection first."""
        domain = domain.strip().lower()
        if not domain:
            raise ValueError("Le domaine ne peut pas être vide.")
        if not host or not user or not password:
            raise ValueError("host, user et password sont obligatoires.")

        # Test connection before saving
        self._test_connection_sync(host, user, password, port)

        self._sites[domain] = {
            "label": label or domain,
            "host": host,
            "user": user,
            "password_encrypted": _encrypt(password),
            "port": port,
            "root": root.rstrip("/") or "/",
            "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "last_deploy": None,
            "deploy_count": 0,
        }
        self._save_sites()
        logger.info(f"[IONOS] Site ajouté: {domain} → {host}")
        return {"status": "ok", "domain": domain, "host": host}

    def remove_site(self, domain: str):
        domain = domain.strip().lower()
        if domain not in self._sites:
            raise KeyError(f"Site '{domain}' non trouvé.")
        del self._sites[domain]
        self._save_sites()
        logger.info(f"[IONOS] Site supprimé: {domain}")

    def list_sites(self) -> List[dict]:
        """Return all sites WITHOUT passwords."""
        result = []
        for domain, info in self._sites.items():
            result.append({
                "domain": domain,
                "label": info.get("label", domain),
                "host": info.get("host", ""),
                "user": info.get("user", ""),
                "port": info.get("port", 22),
                "root": info.get("root", "/"),
                "last_deploy": info.get("last_deploy"),
                "deploy_count": info.get("deploy_count", 0),
            })
        return result

    def get_site(self, domain: str) -> Optional[dict]:
        domain = domain.strip().lower()
        info = self._sites.get(domain)
        if not info:
            return None
        return {
            "domain": domain,
            "label": info.get("label", domain),
            "host": info.get("host", ""),
            "user": info.get("user", ""),
            "port": info.get("port", 22),
            "root": info.get("root", "/"),
            "last_deploy": info.get("last_deploy"),
            "deploy_count": info.get("deploy_count", 0),
        }

    def _get_credentials(self, domain: str) -> dict:
        """Get site config with decrypted password."""
        domain = domain.strip().lower()
        info = self._sites.get(domain)
        if not info:
            raise KeyError(f"Site '{domain}' non trouvé. Utilisez ionos_add_site d'abord.")
        return {
            "host": info["host"],
            "user": info["user"],
            "password": _decrypt(info["password_encrypted"]),
            "port": info.get("port", 22),
            "root": info.get("root", "/"),
        }

    # ── SFTP connection ───────────────────────────────────────────────

    def _connect_sftp(self, host: str, user: str, password: str, port: int = 22):
        """Create an SFTP connection via paramiko.

        Uses SSHClient instead of raw Transport to support keyboard-interactive
        auth (required by IONOS and many shared hosting providers).
        """
        paramiko = _get_paramiko()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=host,
                port=port,
                username=user,
                password=password,
                look_for_keys=False,
                allow_agent=False,
                timeout=15,
            )
        except Exception as exc:
            if "Bad authentication type" in str(exc):
                allowed = getattr(exc, "allowed_types", [])
                if allowed == [""] or allowed == []:
                    raise ConnectionError(
                        f"Authentification SFTP refusée par {host}. "
                        "Le serveur rejette le mot de passe. "
                        "Vérifie dans le panneau IONOS que : "
                        "1) l'accès SFTP/SSH est activé, "
                        "2) le mot de passe SFTP est bien défini (il peut différer du mot de passe principal), "
                        "3) le nom d'utilisateur est correct."
                    ) from exc
            raise
        sftp = client.open_sftp()
        # Attach client to transport so we can close it later
        transport = client.get_transport()
        transport._ssh_client = client  # prevent GC + allow cleanup
        return sftp, transport

    def _test_connection_sync(self, host: str, user: str, password: str, port: int = 22):
        """Test SFTP connectivity (raises on failure)."""
        sftp = None
        transport = None
        try:
            sftp, transport = self._connect_sftp(host, user, password, port)
            sftp.listdir(".")
            logger.info(f"[IONOS] Connexion SFTP OK → {host}:{port}")
        finally:
            if sftp:
                sftp.close()
            self._close_transport(transport)

    @staticmethod
    def _close_transport(transport):
        """Close transport and its underlying SSHClient if any."""
        if not transport:
            return
        client = getattr(transport, "_ssh_client", None)
        try:
            transport.close()
        except Exception:
            pass
        if client:
            try:
                client.close()
            except Exception:
                pass

    # ── Path security ─────────────────────────────────────────────────

    @staticmethod
    def _is_forbidden(filepath: Path) -> bool:
        """Check if a file should never be uploaded."""
        name = filepath.name.lower()
        if name in _FORBIDDEN_FILES:
            return True
        if filepath.suffix.lower() in _FORBIDDEN_EXTENSIONS:
            return True
        return False

    @staticmethod
    def _validate_remote_path(path: str, root: str) -> str:
        """Validate and resolve a remote path (prevent traversal)."""
        # Normalize
        clean = path.replace("\\", "/")
        # Block traversal
        if ".." in clean.split("/"):
            raise ValueError(f"Path traversal interdit: {path}")
        # Resolve relative to root
        if not clean.startswith("/"):
            clean = root.rstrip("/") + "/" + clean
        return clean

    # ── Deploy ────────────────────────────────────────────────────────

    async def deploy(
        self,
        domain: str,
        local_dir: Path,
        *,
        dry_run: bool = False,
    ) -> DeployResult:
        """Deploy a local directory to an IONOS site via SFTP."""
        return await asyncio.to_thread(
            self._deploy_sync, domain, local_dir, dry_run=dry_run
        )

    def _deploy_sync(
        self,
        domain: str,
        local_dir: Path,
        *,
        dry_run: bool = False,
    ) -> DeployResult:
        creds = self._get_credentials(domain)
        local_dir = Path(local_dir)

        if not local_dir.is_dir():
            return DeployResult(
                success=False,
                errors=[f"Dossier local introuvable: {local_dir}"],
            )

        # Collect files
        files_to_upload: List[Tuple[Path, str]] = []
        total_size = 0
        skipped = 0

        for f in sorted(local_dir.rglob("*")):
            if not f.is_file():
                continue
            if self._is_forbidden(f):
                skipped += 1
                logger.debug(f"[IONOS] Skip interdit: {f.name}")
                continue
            rel = f.relative_to(local_dir).as_posix()
            remote = self._validate_remote_path(rel, creds["root"])
            files_to_upload.append((f, remote))
            total_size += f.stat().st_size

        # Size guard
        max_mb = int(os.getenv("LUMENA_IONOS_MAX_UPLOAD_MB", "100"))
        if total_size > max_mb * 1024 * 1024:
            return DeployResult(
                success=False,
                errors=[
                    f"Taille totale {total_size / 1048576:.1f} Mo dépasse la limite de {max_mb} Mo."
                ],
            )

        if dry_run:
            return DeployResult(
                success=True,
                uploaded=len(files_to_upload),
                skipped=skipped,
                total_bytes=total_size,
                dry_run=True,
            )

        # Backup before deploy (if enabled)
        do_backup = os.getenv("LUMENA_IONOS_BACKUP_BEFORE_DEPLOY", "1") == "1"
        if do_backup:
            try:
                self._backup_remote_sync(domain, creds)
            except Exception as e:
                logger.warning(f"[IONOS] Backup échoué (deploy continue): {e}")

        # Upload
        start = time.monotonic()
        sftp = None
        transport = None
        errors: List[str] = []
        uploaded = 0

        try:
            sftp, transport = self._connect_sftp(
                creds["host"], creds["user"], creds["password"], creds["port"]
            )

            for local_path, remote_path in files_to_upload:
                try:
                    # Ensure remote directory exists
                    remote_dir = "/".join(remote_path.split("/")[:-1])
                    self._mkdir_p(sftp, remote_dir)
                    sftp.put(str(local_path), remote_path)
                    uploaded += 1
                except Exception as e:
                    errors.append(f"{remote_path}: {e}")
                    logger.error(f"[IONOS] Upload échoué {remote_path}: {e}")

        except Exception as e:
            errors.append(f"Connexion SFTP: {e}")
        finally:
            if sftp:
                sftp.close()
            self._close_transport(transport)

        duration = time.monotonic() - start

        # Update site stats
        site = self._sites.get(domain.strip().lower())
        if site:
            site["last_deploy"] = dt.datetime.now().isoformat(timespec="seconds")
            site["deploy_count"] = site.get("deploy_count", 0) + 1
            self._save_sites()

        return DeployResult(
            success=len(errors) == 0,
            uploaded=uploaded,
            skipped=skipped,
            errors=errors,
            total_bytes=total_size,
            duration_sec=round(duration, 2),
        )

    # ── Upload specific files ─────────────────────────────────────────

    async def upload_files(
        self,
        domain: str,
        files: List[Tuple[str, Path]],
    ) -> DeployResult:
        """Upload specific files (remote_path, local_path) pairs."""
        return await asyncio.to_thread(self._upload_files_sync, domain, files)

    def _upload_files_sync(
        self,
        domain: str,
        files: List[Tuple[str, Path]],
    ) -> DeployResult:
        creds = self._get_credentials(domain)
        start = time.monotonic()
        sftp = None
        transport = None
        errors: List[str] = []
        uploaded = 0
        total_bytes = 0

        try:
            sftp, transport = self._connect_sftp(
                creds["host"], creds["user"], creds["password"], creds["port"]
            )

            for remote_rel, local_path in files:
                local_path = Path(local_path)
                if not local_path.is_file():
                    errors.append(f"Fichier local introuvable: {local_path}")
                    continue
                if self._is_forbidden(local_path):
                    errors.append(f"Fichier interdit: {local_path.name}")
                    continue

                remote = self._validate_remote_path(remote_rel, creds["root"])
                try:
                    remote_dir = "/".join(remote.split("/")[:-1])
                    self._mkdir_p(sftp, remote_dir)
                    sftp.put(str(local_path), remote)
                    uploaded += 1
                    total_bytes += local_path.stat().st_size
                except Exception as e:
                    errors.append(f"{remote}: {e}")

        except Exception as e:
            errors.append(f"Connexion SFTP: {e}")
        finally:
            if sftp:
                sftp.close()
            self._close_transport(transport)

        return DeployResult(
            success=len(errors) == 0,
            uploaded=uploaded,
            errors=errors,
            total_bytes=total_bytes,
            duration_sec=round(time.monotonic() - start, 2),
        )

    # ── List remote ───────────────────────────────────────────────────

    async def list_remote(
        self, domain: str, path: str = "/"
    ) -> List[RemoteFile]:
        return await asyncio.to_thread(self._list_remote_sync, domain, path)

    def _list_remote_sync(self, domain: str, path: str = "/") -> List[RemoteFile]:
        creds = self._get_credentials(domain)
        remote_path = self._validate_remote_path(path, creds["root"])

        sftp = None
        transport = None
        results: List[RemoteFile] = []

        try:
            sftp, transport = self._connect_sftp(
                creds["host"], creds["user"], creds["password"], creds["port"]
            )

            for entry in sftp.listdir_attr(remote_path):
                is_dir = stat.S_ISDIR(entry.st_mode) if entry.st_mode else False
                mtime = None
                if entry.st_mtime:
                    mtime = dt.datetime.fromtimestamp(entry.st_mtime).isoformat(
                        timespec="seconds"
                    )
                results.append(RemoteFile(
                    path=f"{remote_path.rstrip('/')}/{entry.filename}",
                    size=entry.st_size or 0,
                    is_dir=is_dir,
                    modified=mtime,
                ))

        except Exception as e:
            logger.error(f"[IONOS] List remote échoué: {e}")
            raise
        finally:
            if sftp:
                sftp.close()
            self._close_transport(transport)

        return results

    # ── Delete remote ─────────────────────────────────────────────────

    async def delete_remote(
        self, domain: str, paths: List[str]
    ) -> Dict[str, Any]:
        return await asyncio.to_thread(self._delete_remote_sync, domain, paths)

    def _delete_remote_sync(
        self, domain: str, paths: List[str]
    ) -> Dict[str, Any]:
        creds = self._get_credentials(domain)
        sftp = None
        transport = None
        deleted = 0
        errors: List[str] = []

        try:
            sftp, transport = self._connect_sftp(
                creds["host"], creds["user"], creds["password"], creds["port"]
            )

            for p in paths:
                remote = self._validate_remote_path(p, creds["root"])
                try:
                    sftp.remove(remote)
                    deleted += 1
                except Exception as e:
                    errors.append(f"{remote}: {e}")

        except Exception as e:
            errors.append(f"Connexion SFTP: {e}")
        finally:
            if sftp:
                sftp.close()
            self._close_transport(transport)

        return {
            "success": len(errors) == 0,
            "deleted": deleted,
            "errors": errors,
        }

    # ── Test connection ───────────────────────────────────────────────

    async def test_connection(self, domain: str) -> bool:
        creds = self._get_credentials(domain)
        try:
            await asyncio.to_thread(
                self._test_connection_sync,
                creds["host"], creds["user"], creds["password"], creds["port"],
            )
            return True
        except Exception:
            return False

    # ── Backup ────────────────────────────────────────────────────────

    def _backup_remote_sync(self, domain: str, creds: dict):
        """Download existing remote files to local backup dir."""
        sftp = None
        transport = None
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = _BACKUPS_DIR / domain / ts
        backup_dir.mkdir(parents=True, exist_ok=True)

        try:
            sftp, transport = self._connect_sftp(
                creds["host"], creds["user"], creds["password"], creds["port"]
            )
            self._download_dir(sftp, creds["root"] or "/", backup_dir)
            logger.info(f"[IONOS] Backup → {backup_dir}")
        finally:
            if sftp:
                sftp.close()
            self._close_transport(transport)

    def _download_dir(self, sftp, remote_dir: str, local_dir: Path):
        """Recursively download a remote directory."""
        try:
            entries = sftp.listdir_attr(remote_dir)
        except Exception:
            return

        for entry in entries:
            remote_path = f"{remote_dir.rstrip('/')}/{entry.filename}"
            local_path = local_dir / entry.filename

            if stat.S_ISDIR(entry.st_mode) if entry.st_mode else False:
                local_path.mkdir(exist_ok=True)
                self._download_dir(sftp, remote_path, local_path)
            else:
                try:
                    sftp.get(remote_path, str(local_path))
                except Exception as e:
                    logger.debug(f"[IONOS] Backup skip {remote_path}: {e}")

    # ── SFTP helpers ──────────────────────────────────────────────────

    @staticmethod
    def _mkdir_p(sftp, remote_dir: str):
        """Recursively create remote directories (like mkdir -p)."""
        if not remote_dir or remote_dir == "/":
            return
        dirs = remote_dir.split("/")
        current = ""
        for d in dirs:
            if not d:
                current = "/"
                continue
            current = f"{current}/{d}" if current != "/" else f"/{d}"
            try:
                sftp.stat(current)
            except FileNotFoundError:
                sftp.mkdir(current)
