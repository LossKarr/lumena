from __future__ import annotations

from datetime import datetime
from email import policy
from email.header import decode_header
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any, Dict, List, Optional
import email.utils
import imaplib
import json
import mimetypes
from ..utils.persistence import atomic_write_json
import os
import re
import smtplib
import ssl
import threading
import time

from loguru import logger


class MailHub:
    """Hub mail multi-comptes via IMAP/SMTP.

    Secrets: le mot de passe n'est pas stocké, il est lu depuis une variable
    d'environnement référencée par chaque compte.
    """

    def __init__(self, data_root: Path):
        self.data_root = Path(data_root)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.accounts_file = self.data_root / "accounts.json"
        self.audit_file = self.data_root / "audit.log"
        self.rate_limit_file = self.data_root / "rate_limits.json"
        self._lock = threading.Lock()

    def _emit(self, state: str, message: str, **kwargs: Any) -> None:
        pass  # Brain 3D removed — kept as no-op to avoid touching 17 call sites

    @staticmethod
    def _decode_mime(value: Optional[str]) -> str:
        if not value:
            return ""
        try:
            parts = decode_header(value)
            chunks: List[str] = []
            for item, encoding in parts:
                if isinstance(item, bytes):
                    chunks.append(item.decode(encoding or "utf-8", errors="replace"))
                else:
                    chunks.append(item)
            return "".join(chunks).strip()
        except Exception:
            return str(value)

    @staticmethod
    def _to_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _to_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _normalize_bool(value: str, default: bool = False) -> bool:
        raw = (value or "").strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    @staticmethod
    def _is_valid_alias(alias: str) -> bool:
        return bool(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,63}", alias or ""))

    @staticmethod
    def _is_valid_host(host: str) -> bool:
        normalized = (host or "").strip().lower()
        if len(normalized) < 3 or len(normalized) > 253:
            return False
        if normalized.endswith("."):
            normalized = normalized[:-1]
        labels = normalized.split(".")
        if len(labels) < 2:
            return False
        label_re = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
        return all(label_re.fullmatch(part) for part in labels)

    @staticmethod
    def _is_valid_email(address: str) -> bool:
        normalized = (address or "").strip()
        if len(normalized) < 5 or len(normalized) > 254:
            return False
        return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized))

    @staticmethod
    def _is_valid_env_key(value: str) -> bool:
        return bool(re.fullmatch(r"[A-Z_][A-Z0-9_]{1,127}", (value or "").strip()))

    @staticmethod
    def _normalize_folder(folder: str) -> str:
        value = (folder or "INBOX").strip()
        # Décoder IMAP UTF-7 modifié (ex: "envoy&AOk-s" → "envoyés")
        try:
            if "&" in value and "-" in value:
                decoded = value.encode("ascii").decode("utf-7")
                if decoded != value:
                    value = decoded
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass  # caractères non-décodables, on garde la valeur telle quelle
        value = re.sub(r"\s+", " ", value)
        return value[:128] if value else "INBOX"

    # Alias communs → noms réels Gmail (imap.gmail.com)
    _GMAIL_FOLDER_ALIASES: Dict[str, List[str]] = {
        "sent":    ["[Gmail]/Sent Mail", "[Gmail]/Sent"],
        "envoyés": ["[Gmail]/Sent Mail", "[Gmail]/Sent"],
        "envoyes": ["[Gmail]/Sent Mail", "[Gmail]/Sent"],
        # Chemin complet accentué → fallback ASCII (imaplib ne supporte pas les accents)
        "[gmail]/messages envoyés": ["[Gmail]/Sent Mail", "[Gmail]/Sent"],
        "messages envoyés": ["[Gmail]/Sent Mail", "[Gmail]/Sent"],
        "trash":   ["[Gmail]/Trash", "[Gmail]/Corbeille"],
        "corbeille": ["[Gmail]/Trash", "[Gmail]/Corbeille"],
        "[gmail]/corbeille": ["[Gmail]/Trash"],
        "spam":    ["[Gmail]/Spam"],
        "junk":    ["[Gmail]/Spam"],
        "drafts":  ["[Gmail]/Drafts", "[Gmail]/Brouillons"],
        "brouillons": ["[Gmail]/Drafts", "[Gmail]/Brouillons"],
        "[gmail]/brouillons": ["[Gmail]/Drafts"],
        "all":     ["[Gmail]/All Mail", "[Gmail]/Tous les messages"],
        "starred": ["[Gmail]/Starred", "[Gmail]/Suivis"],
    }

    def _try_select_folder(self, client: "imaplib.IMAP4", folder: str):
        """Sélectionne un dossier IMAP avec fallback automatique pour Gmail."""
        try:
            status, data = client.select(folder, readonly=True)
            if status == "OK":
                return status, folder
        except (UnicodeEncodeError, UnicodeDecodeError):
            # Le nom de dossier contient des caractères non-ASCII (ex: "Messages envoyés")
            # → on passe directement au fallback alias
            pass
        except Exception as e:
            logger.debug("IMAP select folder fallback: {}", e)
        for alias in self._GMAIL_FOLDER_ALIASES.get(folder.lower().strip(), []):
            try:
                status, data = client.select(alias, readonly=True)
                if status == "OK":
                    return status, alias
            except Exception:
                continue
        return "NO", folder

    def list_folders(self, alias: str) -> Dict[str, Any]:
        """Liste tous les dossiers IMAP disponibles sur le compte."""
        account = self._get_account(alias)
        client = None
        try:
            client = self._connect_imap_with_retry(account)
            status, folder_list = client.list()
            if status != "OK":
                return {"success": False, "error": "impossible de lister les dossiers"}
            folders: List[str] = []
            for item in (folder_list or []):
                if isinstance(item, bytes):
                    decoded = item.decode("utf-8", errors="replace")
                    # Format IMAP : (\Flag) "/" "Folder Name"
                    # On extrait la dernière partie après le séparateur
                    match = re.search(r'"[^"]*"\s+"?([^"]+)"?$', decoded)
                    if match:
                        name = match.group(1).strip().strip('"')
                    else:
                        parts = decoded.split(" ")
                        name = parts[-1].strip().strip('"') if parts else ""
                    if name:
                        folders.append(name)
            return {"success": True, "count": len(folders), "folders": folders}
        except Exception as e:
            return {"success": False, "error": str(e)}
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass  # IMAP logout cleanup best-effort

    @staticmethod
    def _normalize_uid(uid) -> str:
        value = str(uid).strip() if uid is not None else ""
        if not re.fullmatch(r"\d{1,20}", value):
            raise ValueError("uid invalide")
        return value

    @staticmethod
    def _today_key() -> str:
        return datetime.utcnow().strftime("%Y-%m-%d")

    def _workspace_root(self) -> Path:
        from ..utils.paths import WORKSPACE_DIR
        workspace_root = WORKSPACE_DIR.resolve()
        workspace_root.mkdir(parents=True, exist_ok=True)
        return workspace_root

    def _resolve_workspace_output_dir(self, output_dir: str, default_dir: Path) -> Path:
        workspace_root = self._workspace_root()
        if (output_dir or "").strip():
            chosen = Path(output_dir).expanduser()
            if not chosen.is_absolute():
                chosen = workspace_root / chosen
            target_dir = chosen.resolve()
        else:
            target_dir = default_dir.resolve()

        try:
            target_dir.relative_to(workspace_root)
        except Exception:
            raise ValueError(f"output_dir hors workspace interdit: {target_dir}")

        return target_dir

    @staticmethod
    def _ensure_str(value) -> str:
        """Coerce list/tuple to comma-separated string (LLM may pass either)."""
        if isinstance(value, (list, tuple)):
            return ",".join(str(v) for v in value)
        return str(value) if value else ""

    @staticmethod
    def _parse_csv_values(value) -> List[str]:
        if isinstance(value, (list, tuple)):
            return [str(v).strip().strip('"').strip("'") for v in value if str(v).strip()]
        items: List[str] = []
        for raw in (value or "").split(","):
            normalized = raw.strip().strip('"').strip("'")
            if normalized:
                items.append(normalized)
        return items

    @staticmethod
    def _guess_mime_parts(file_path: Path) -> tuple[str, str]:
        ctype, _ = mimetypes.guess_type(str(file_path))
        if ctype and "/" in ctype:
            maintype, subtype = ctype.split("/", 1)
            return maintype, subtype
        return "application", "octet-stream"

    @staticmethod
    def _safe_attachment_name(filename: str, fallback: str) -> str:
        candidate = Path((filename or "").strip()).name
        if not candidate:
            candidate = fallback
        candidate = re.sub(r"[\r\n]+", " ", candidate).strip()
        return candidate[:180] if candidate else fallback

    def _resolve_attachment_paths(self, attachments: str) -> List[Path]:
        requested = self._parse_csv_values(attachments)
        if not requested:
            return []

        max_count = self._to_int(os.getenv("LUMENA_MAIL_MAX_ATTACHMENTS", "12"), 12)
        max_count = max(1, min(max_count, 50))
        if len(requested) > max_count:
            raise ValueError(f"trop de pièces jointes (max={max_count})")

        max_file_bytes = int(max(1.0, self._to_float(os.getenv("LUMENA_MAIL_MAX_ATTACHMENT_MB", "20"), 20.0)) * 1024 * 1024)
        max_total_bytes = int(max(1.0, self._to_float(os.getenv("LUMENA_MAIL_MAX_TOTAL_ATTACHMENTS_MB", "45"), 45.0)) * 1024 * 1024)

        allowed_roots_raw = self._parse_csv_values(os.getenv("LUMENA_MAIL_ALLOWED_ATTACHMENT_ROOTS", ""))
        allowed_roots: List[Path] = []
        for raw_root in allowed_roots_raw:
            root_path = Path(raw_root).expanduser()
            if not root_path.is_absolute():
                root_path = (Path.cwd() / root_path)
            allowed_roots.append(root_path.resolve())

        resolved: List[Path] = []
        total_size = 0
        for raw_path in requested:
            path_obj = Path(raw_path).expanduser()
            if not path_obj.is_absolute():
                path_obj = (Path.cwd() / path_obj)
            resolved_path = path_obj.resolve()

            if not resolved_path.exists() or not resolved_path.is_file():
                raise ValueError(f"fichier introuvable: {raw_path}")

            if allowed_roots:
                authorized = False
                for root in allowed_roots:
                    try:
                        resolved_path.relative_to(root)
                        authorized = True
                        break
                    except Exception:
                        continue
                if not authorized:
                    raise ValueError(f"fichier hors racines autorisées: {resolved_path}")

            file_size = resolved_path.stat().st_size
            if file_size > max_file_bytes:
                raise ValueError(f"fichier trop volumineux ({resolved_path.name})")

            total_size += file_size
            if total_size > max_total_bytes:
                raise ValueError("taille totale des pièces jointes dépassée")

            resolved.append(resolved_path)

        return resolved

    @staticmethod
    def _extract_uid(fetch_response: Any, fallback: str) -> str:
        fallback_value = (fallback or "").strip()
        for part in fetch_response or []:
            if not isinstance(part, tuple) or not part:
                continue
            header = part[0]
            if isinstance(header, bytes):
                text = header.decode("utf-8", errors="ignore")
            else:
                text = str(header)
            match = re.search(r"UID\s+(\d+)", text)
            if match:
                return match.group(1)
        return fallback_value

    def _append_audit(self, action: str, alias: str, success: bool, detail: str = "") -> None:
        payload = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "action": action,
            "alias": (alias or "")[:64],
            "success": bool(success),
            "detail": (detail or "")[:300],
        }
        try:
            with self._lock:
                self.audit_file.parent.mkdir(parents=True, exist_ok=True)
                with self.audit_file.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug("MailHub audit write: {}", e)

    def _check_rate_limit(self, action: str, max_count: int, window_sec: int) -> bool:
        now = int(time.time())
        key = f"{action}:{window_sec}:{max_count}"
        try:
            with self._lock:
                if self.rate_limit_file.exists():
                    data = json.loads(self.rate_limit_file.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        data = {}
                else:
                    data = {}

                entries = data.get(key)
                if not isinstance(entries, list):
                    entries = []
                entries = [int(x) for x in entries if isinstance(x, (int, float)) and int(x) > now - window_sec]
                if len(entries) >= max_count:
                    data[key] = entries
                    atomic_write_json(self.rate_limit_file, data)
                    return False

                entries.append(now)
                data[key] = entries
                atomic_write_json(self.rate_limit_file, data)
                return True
        except Exception:
            return True

    def _mail_action_enabled(self, action: str, default: bool = True) -> bool:
        key = f"LUMENA_MAIL_ENABLE_{action.upper()}"
        return self._normalize_bool(os.getenv(key, ""), default)

    def _connect_imap_with_retry(self, account: Dict[str, Any], retries: int = 2) -> imaplib.IMAP4:
        delay_sec = self._to_float(os.getenv("LUMENA_MAIL_RETRY_DELAY_SEC", "0.8"), 0.8)
        last_error: Optional[Exception] = None
        for attempt in range(max(1, retries + 1)):
            try:
                return self._connect_imap(account)
            except Exception as exc:
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(max(0.0, delay_sec))
        if last_error is not None:
            raise last_error
        raise RuntimeError("imap connection failed")

    def _connect_smtp_with_retry(self, account: Dict[str, Any], retries: int = 2) -> smtplib.SMTP:
        delay_sec = self._to_float(os.getenv("LUMENA_MAIL_RETRY_DELAY_SEC", "0.8"), 0.8)
        last_error: Optional[Exception] = None
        for attempt in range(max(1, retries + 1)):
            try:
                return self._connect_smtp(account)
            except Exception as exc:
                last_error = exc
                if attempt >= retries:
                    break
                time.sleep(max(0.0, delay_sec))
        if last_error is not None:
            raise last_error
        raise RuntimeError("smtp connection failed")

    def _apply_recipient_policy(self, recipients: List[str]) -> Optional[str]:
        allowed_recipients = {
            x.strip().lower()
            for x in (os.getenv("LUMENA_MAIL_ALLOWED_RECIPIENTS", "") or "").split(",")
            if x.strip()
        }
        allowed_domains = {
            x.strip().lower().lstrip("@")
            for x in (os.getenv("LUMENA_MAIL_ALLOWED_DOMAINS", "") or "").split(",")
            if x.strip()
        }
        if not allowed_recipients and not allowed_domains:
            return None

        for recipient in recipients:
            lower = recipient.lower()
            domain = lower.split("@")[-1] if "@" in lower else ""
            if lower in allowed_recipients:
                continue
            if domain and domain in allowed_domains:
                continue
            return f"destinataire non autorisé par policy: {recipient}"
        return None

    def _load_accounts(self) -> Dict[str, Any]:
        if not self.accounts_file.exists():
            return {"accounts": {}}
        try:
            data = json.loads(self.accounts_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"accounts": {}}
            if "accounts" not in data or not isinstance(data["accounts"], dict):
                return {"accounts": {}}
            return data
        except Exception:
            return {"accounts": {}}

    def _save_accounts(self, data: Dict[str, Any]) -> None:
        self.accounts_file.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.accounts_file, data)

    def _get_account(self, alias: str) -> Dict[str, Any]:
        alias = (alias or "").strip()
        if not alias:
            raise ValueError("alias vide")
        data = self._load_accounts()
        accounts = data.get("accounts") or {}
        account = accounts.get(alias)
        if not account:
            # Auto-correction: si un seul compte commence par l'alias fourni, l'utiliser
            candidates = [k for k in accounts if k.startswith(alias)]
            if len(candidates) == 1:
                logger.info(f"📧 Alias '{alias}' auto-corrigé → '{candidates[0]}'")
                return accounts[candidates[0]]
            available = ", ".join(sorted(accounts.keys())) or "aucun"
            raise ValueError(f"compte introuvable: {alias} — comptes disponibles: {available}")
        return account

    def _resolve_password(self, account: Dict[str, Any]) -> str:
        env_key = (account.get("password_env") or "").strip()
        if not env_key:
            raise ValueError("password_env manquant")
        if not self._is_valid_env_key(env_key):
            raise ValueError("password_env invalide")
        password = os.getenv(env_key, "")
        if not password:
            raise ValueError(f"variable d'environnement absente: {env_key}")
        # Gmail app passwords are displayed with spaces but SMTP requires them without
        return password.replace(" ", "")

    def _connect_imap(self, account: Dict[str, Any]) -> imaplib.IMAP4:
        host = str(account.get("imap_host") or "").strip()
        if not host:
            raise ValueError("imap_host manquant")
        if not self._is_valid_host(host):
            raise ValueError("imap_host invalide")
        port = self._to_int(account.get("imap_port"), 993)
        if port < 1 or port > 65535:
            raise ValueError("imap_port invalide")
        use_ssl = bool(account.get("imap_ssl", True))
        username = str(account.get("username") or account.get("email") or "").strip()
        password = self._resolve_password(account)
        timeout = self._to_float(os.getenv("LUMENA_MAIL_CONNECT_TIMEOUT_SEC", "20"), 20.0)
        if not username:
            raise ValueError("username/email manquant")

        if use_ssl:
            client: imaplib.IMAP4 = imaplib.IMAP4_SSL(host=host, port=port, timeout=timeout)
        else:
            client = imaplib.IMAP4(host=host, port=port, timeout=timeout)
            client.starttls()
        client.login(username, password)
        return client

    def _connect_smtp(self, account: Dict[str, Any]) -> smtplib.SMTP:
        host = str(account.get("smtp_host") or "").strip()
        if not host:
            raise ValueError("smtp_host manquant")
        if not self._is_valid_host(host):
            raise ValueError("smtp_host invalide")
        port = self._to_int(account.get("smtp_port"), 465)
        if port < 1 or port > 65535:
            raise ValueError("smtp_port invalide")
        use_ssl = bool(account.get("smtp_ssl", True))
        username = str(account.get("username") or account.get("email") or "").strip()
        password = self._resolve_password(account)
        timeout = self._to_float(os.getenv("LUMENA_MAIL_CONNECT_TIMEOUT_SEC", "20"), 20.0)
        if not username:
            raise ValueError("username/email manquant")

        if use_ssl:
            context = ssl.create_default_context()
            client: smtplib.SMTP = smtplib.SMTP_SSL(host=host, port=port, context=context, timeout=timeout)
        else:
            client = smtplib.SMTP(host=host, port=port, timeout=timeout)
            client.ehlo()
            client.starttls(context=ssl.create_default_context())
            client.ehlo()

        client.login(username, password)
        return client

    def upsert_account(
        self,
        alias: str,
        email_address: str,
        imap_host: str,
        imap_port: int = 993,
        smtp_host: str = "",
        smtp_port: int = 465,
        username: str = "",
        password_env: str = "",
        imap_ssl: bool = True,
        smtp_ssl: bool = True,
    ) -> Dict[str, Any]:
        alias = (alias or "").strip()
        if not alias:
            return {"success": False, "error": "alias obligatoire"}
        if not self._is_valid_alias(alias):
            return {"success": False, "error": "alias invalide (2-64, lettres/chiffres/_.-)"}
        email_address = (email_address or "").strip()
        imap_host = (imap_host or "").strip()
        smtp_host = (smtp_host or "").strip() or imap_host
        username = (username or "").strip() or email_address
        password_env = (password_env or "").strip()
        if not email_address or not imap_host or not password_env:
            return {"success": False, "error": "email_address, imap_host et password_env requis"}
        if not self._is_valid_email(email_address):
            return {"success": False, "error": "email_address invalide"}
        if not self._is_valid_host(imap_host):
            return {"success": False, "error": "imap_host invalide"}
        if not self._is_valid_host(smtp_host):
            return {"success": False, "error": "smtp_host invalide"}
        if not self._is_valid_env_key(password_env):
            return {"success": False, "error": "password_env invalide"}

        # --- Guard: refuse to save if the env var doesn't actually exist ---
        import os as _os
        if not _os.environ.get(password_env):
            # Hint: list already configured accounts so the LLM doesn't re-create
            _existing = list((self._load_accounts().get("accounts") or {}).keys())
            _hint = (
                f" Conseil: appelle mail_list_accounts — comptes déjà disponibles: {', '.join(_existing)}"
                if _existing else
                " Conseil: appelle mail_list_accounts pour vérifier les comptes existants."
            )
            return {
                "success": False,
                "error": f"variable d'environnement '{password_env}' absente ou vide. "
                         f"Impossible de configurer ce compte sans credentials.{_hint}",
            }

        imap_port_i = self._to_int(imap_port, 993)
        smtp_port_i = self._to_int(smtp_port, 465)
        smtp_ssl_b = bool(smtp_ssl)
        if imap_port_i < 1 or imap_port_i > 65535:
            return {"success": False, "error": "imap_port invalide"}
        if smtp_port_i < 1 or smtp_port_i > 65535:
            return {"success": False, "error": "smtp_port invalide"}
        # Auto-correct common SSL/port mismatch (e.g. LLM chooses 587+ssl=True)
        # Port 465 = SSL/TLS direct (smtp_ssl=True), port 587 = STARTTLS (smtp_ssl=False)
        if smtp_port_i == 587 and smtp_ssl_b:
            smtp_port_i = 465  # STARTTLS+ssl=True → use 465 for SSL/TLS
        elif smtp_port_i == 465 and not smtp_ssl_b:
            smtp_ssl_b = True  # port 465 requires ssl=True

        with self._lock:
            data = self._load_accounts()
            accounts = data.setdefault("accounts", {})
            accounts[alias] = {
                "alias": alias,
                "email": email_address,
                "username": username,
                "password_env": password_env,
                "imap_host": imap_host,
                "imap_port": imap_port_i,
                "imap_ssl": bool(imap_ssl),
                "smtp_host": smtp_host,
                "smtp_port": smtp_port_i,
                "smtp_ssl": smtp_ssl_b,
                "updated_at": datetime.utcnow().isoformat() + "Z",
            }
            self._save_accounts(data)
        self._emit("account_saved", "Compte mail enregistré", alias=alias)
        self._append_audit("account_upsert", alias, True, f"email={email_address}")
        return {"success": True, "alias": alias, "accounts_file": str(self.accounts_file)}

    def list_accounts(self) -> Dict[str, Any]:
        data = self._load_accounts()
        accounts = data.get("accounts", {})
        items: List[Dict[str, Any]] = []
        for alias, cfg in sorted(accounts.items(), key=lambda t: t[0].lower()):
            items.append(
                {
                    "alias": alias,
                    "email": cfg.get("email", ""),
                    "imap_host": cfg.get("imap_host", ""),
                    "smtp_host": cfg.get("smtp_host", ""),
                    "password_env": cfg.get("password_env", ""),
                }
            )
        return {"success": True, "count": len(items), "accounts": items}

    def remove_account(self, alias: str) -> Dict[str, Any]:
        alias = (alias or "").strip()
        if not alias:
            return {"success": False, "error": "alias obligatoire"}
        with self._lock:
            data = self._load_accounts()
            accounts = data.setdefault("accounts", {})
            if alias not in accounts:
                return {"success": False, "error": f"compte introuvable: {alias}"}
            del accounts[alias]
            self._save_accounts(data)
        self._emit("account_removed", "Compte mail supprimé", alias=alias)
        self._append_audit("account_remove", alias, True)
        return {"success": True, "alias": alias}

    def list_messages(
        self,
        alias: str,
        folder: str = "INBOX",
        limit: int = 25,
        unseen_only: bool = False,
        sender_filter: str = "",
        subject_filter: str = "",
        sort_by: str = "date",
        order: str = "desc",
    ) -> Dict[str, Any]:
        account = self._get_account(alias)
        limit = max(1, min(self._to_int(limit, 25), 200))
        sender_filter = (sender_filter or "").strip().lower()
        subject_filter = (subject_filter or "").strip().lower()
        sort_by = (sort_by or "date").strip().lower()
        order = (order or "desc").strip().lower()
        folder = self._normalize_folder(folder)
        if sort_by not in {"date", "from", "subject"}:
            sort_by = "date"
        if order not in {"asc", "desc"}:
            order = "desc"

        self._emit("mail_list_start", "Lecture des emails", alias=alias, folder=folder)

        client: Optional[imaplib.IMAP4] = None
        try:
            client = self._connect_imap_with_retry(account)
            status, folder = self._try_select_folder(client, folder)
            if status != "OK":
                return {"success": False, "error": f"dossier inaccessible: {folder}"}

            query = "UNSEEN" if unseen_only else "ALL"
            status, data = client.search(None, query)
            if status != "OK" or not data:
                return {"success": True, "count": 0, "messages": []}

            ids = [item for item in data[0].split() if item]
            ids = ids[-limit:]
            parsed: List[Dict[str, Any]] = []

            for msg_id in ids:
                fetch_status, msg_data = client.fetch(msg_id, "(UID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                if fetch_status != "OK" or not msg_data:
                    continue
                raw = b""
                for part in msg_data:
                    if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                        raw = bytes(part[1])
                        break
                if not raw:
                    continue

                message = BytesParser(policy=policy.default).parsebytes(raw)
                from_value = self._decode_mime(message.get("From"))
                subject_value = self._decode_mime(message.get("Subject"))
                date_value = message.get("Date") or ""

                if sender_filter and sender_filter not in from_value.lower():
                    continue
                if subject_filter and subject_filter not in subject_value.lower():
                    continue

                parsed.append(
                    {
                        "uid": self._extract_uid(msg_data, msg_id.decode("utf-8", errors="ignore")),
                        "from": from_value,
                        "subject": subject_value,
                        "date": date_value,
                    }
                )

            reverse = order != "asc"
            if sort_by in {"from", "subject"}:
                parsed.sort(key=lambda item: (item.get(sort_by) or "").lower(), reverse=reverse)
            else:
                def _date_key(item: Dict[str, Any]) -> float:
                    value = item.get("date") or ""
                    try:
                        dt = email.utils.parsedate_to_datetime(value)
                        if dt is None:
                            return 0.0
                        return dt.timestamp()
                    except Exception:
                        return 0.0

                parsed.sort(key=_date_key, reverse=reverse)

            self._emit("mail_list_done", "Emails listés", alias=alias, count=len(parsed))
            self._append_audit("mail_list", alias, True, f"folder={folder},count={len(parsed)}")
            return {"success": True, "count": len(parsed), "messages": parsed}
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass  # IMAP logout cleanup best-effort

    def read_message(
        self,
        alias: str,
        uid: str,
        folder: str = "INBOX",
        max_chars: int = 12000,
    ) -> Dict[str, Any]:
        account = self._get_account(alias)
        uid = self._normalize_uid(uid)
        folder = self._normalize_folder(folder)
        max_chars = max(500, min(self._to_int(max_chars, 12000), 100000))

        self._emit("mail_read_start", "Lecture email", alias=alias, uid=uid)

        client: Optional[imaplib.IMAP4] = None
        try:
            client = self._connect_imap_with_retry(account)
            status, _ = client.select(folder)
            if status != "OK":
                return {"success": False, "error": f"dossier inaccessible: {folder}"}

            fetch_status, msg_data = client.uid("FETCH", uid, "(RFC822)")
            if fetch_status != "OK" or not msg_data:
                return {"success": False, "error": f"email introuvable uid={uid}"}

            raw = b""
            for part in msg_data:
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                    raw = bytes(part[1])
                    break

            if not raw:
                return {"success": False, "error": "contenu email vide"}

            message = BytesParser(policy=policy.default).parsebytes(raw)
            subject_value = self._decode_mime(message.get("Subject"))
            from_value = self._decode_mime(message.get("From"))
            to_value = self._decode_mime(message.get("To"))
            date_value = message.get("Date") or ""
            attachments: List[Dict[str, Any]] = []

            body_text = ""
            if message.is_multipart():
                for part in message.walk():
                    ctype = (part.get_content_type() or "").lower()
                    disp = (part.get("Content-Disposition") or "").lower()
                    filename = self._decode_mime(part.get_filename())
                    if "attachment" in disp or filename:
                        payload = part.get_payload(decode=True) or b""
                        attachments.append(
                            {
                                "filename": filename or "attachment.bin",
                                "content_type": ctype or "application/octet-stream",
                                "size": len(payload),
                            }
                        )
                    if "attachment" in disp:
                        continue
                    if ctype == "text/plain":
                        payload = part.get_payload(decode=True) or b""
                        charset = part.get_content_charset() or "utf-8"
                        body_text = payload.decode(charset, errors="replace")
                        break
                if not body_text:
                    for part in message.walk():
                        ctype = (part.get_content_type() or "").lower()
                        if ctype == "text/html":
                            payload = part.get_payload(decode=True) or b""
                            charset = part.get_content_charset() or "utf-8"
                            html = payload.decode(charset, errors="replace")
                            body_text = re.sub(r"<[^>]+>", " ", html)
                            break
            else:
                payload = message.get_payload(decode=True) or b""
                charset = message.get_content_charset() or "utf-8"
                body_text = payload.decode(charset, errors="replace")

            body_text = re.sub(r"\s+", " ", body_text or "").strip()
            if len(body_text) > max_chars:
                body_text = body_text[:max_chars] + " ...[tronqué]"

            self._emit("mail_read_done", "Email lu", alias=alias, uid=uid)
            self._append_audit("mail_read", alias, True, f"folder={folder},uid={uid}")
            return {
                "success": True,
                "uid": uid,
                "subject": subject_value,
                "from": from_value,
                "to": to_value,
                "date": date_value,
                "body": body_text,
                "attachments": attachments,
            }
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass  # IMAP logout cleanup best-effort

    def send_message(
        self,
        alias: str,
        to: str,
        subject: str,
        body: str,
        cc: str = "",
        bcc: str = "",
        attachments: str = "",
    ) -> Dict[str, Any]:
        if not self._mail_action_enabled("send", default=True):
            return {"success": False, "error": "envoi désactivé (LUMENA_MAIL_ENABLE_SEND=0)"}

        account = self._get_account(alias)
        to = self._ensure_str(to).strip()
        cc = self._ensure_str(cc).strip()
        bcc = self._ensure_str(bcc).strip()
        attachments = self._ensure_str(attachments)
        subject = (subject or "").strip()
        if not to:
            return {"success": False, "error": "destinataire vide"}

        if not self._check_rate_limit(
            "mail_send",
            self._to_int(os.getenv("LUMENA_MAIL_SEND_MAX_PER_WINDOW", "30"), 30),
            self._to_int(os.getenv("LUMENA_MAIL_SEND_WINDOW_SEC", "600"), 600),
        ):
            return {"success": False, "error": "limite d'envoi atteinte, réessayez plus tard"}

        self._emit("mail_send_start", "Envoi email", alias=alias, to=to)

        recipients: List[str] = []
        for chunk in [to, cc, bcc]:
            if not chunk:
                continue
            for item in chunk.split(","):
                normalized = item.strip()
                if normalized:
                    recipients.append(normalized)
        recipients = list(dict.fromkeys(recipients))
        if not recipients:
            return {"success": False, "error": "aucun destinataire valide"}

        if len(recipients) > self._to_int(os.getenv("LUMENA_MAIL_MAX_RECIPIENTS", "20"), 20):
            return {"success": False, "error": "trop de destinataires"}

        for recipient in recipients:
            if not self._is_valid_email(recipient):
                return {"success": False, "error": f"destinataire invalide: {recipient}"}

        policy_error = self._apply_recipient_policy(recipients)
        if policy_error:
            self._append_audit("mail_send", alias, False, policy_error)
            return {"success": False, "error": policy_error}

        subject = subject[:300]
        max_body = self._to_int(os.getenv("LUMENA_MAIL_MAX_BODY_CHARS", "200000"), 200000)
        body = (body or "")[: max(1, max_body)]

        try:
            attachment_paths = self._resolve_attachment_paths(attachments)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        msg = EmailMessage()
        msg["From"] = account.get("email") or account.get("username") or ""
        msg["To"] = to
        if cc.strip():
            msg["Cc"] = cc
        msg["Subject"] = subject
        msg["Date"] = email.utils.formatdate(localtime=True)
        _from_domain = (account.get("email") or "").split("@")[-1] or "lumena.local"
        msg["Message-ID"] = email.utils.make_msgid(domain=_from_domain)
        msg.set_content(body or "")

        attached_names: List[str] = []
        for file_path in attachment_paths:
            try:
                payload = file_path.read_bytes()
            except Exception as exc:
                return {"success": False, "error": f"lecture pièce jointe impossible ({file_path.name}): {exc}"}

            maintype, subtype = self._guess_mime_parts(file_path)
            msg.add_attachment(payload, maintype=maintype, subtype=subtype, filename=file_path.name)
            attached_names.append(file_path.name)

        client: Optional[smtplib.SMTP] = None
        try:
            client = self._connect_smtp_with_retry(account)
            client.send_message(msg, to_addrs=recipients)
            self._emit("mail_send_done", "Email envoyé", alias=alias, to=to)
            self._append_audit("mail_send", alias, True, f"to={len(recipients)},attachments={len(attached_names)}")
            return {"success": True, "to": recipients, "subject": subject, "attachments": attached_names}
        except Exception as exc:
            self._append_audit("mail_send", alias, False, str(exc))
            return {"success": False, "error": f"échec envoi: {exc}"}
        finally:
            if client is not None:
                try:
                    client.quit()
                except Exception:
                    pass  # SMTP quit cleanup best-effort

    def reply_message(
        self,
        alias: str,
        uid: str,
        body: str,
        folder: str = "INBOX",
        cc: str = "",
        bcc: str = "",
        reply_all: bool = False,
        attachments: str = "",
    ) -> Dict[str, Any]:
        if not self._mail_action_enabled("reply", default=True):
            return {"success": False, "error": "réponse désactivée (LUMENA_MAIL_ENABLE_REPLY=0)"}

        cc = self._ensure_str(cc)
        bcc = self._ensure_str(bcc)
        attachments = self._ensure_str(attachments)

        account = self._get_account(alias)
        uid = self._normalize_uid(uid)
        folder = self._normalize_folder(folder)

        if not self._check_rate_limit(
            "mail_reply",
            self._to_int(os.getenv("LUMENA_MAIL_REPLY_MAX_PER_WINDOW", "30"), 30),
            self._to_int(os.getenv("LUMENA_MAIL_REPLY_WINDOW_SEC", "600"), 600),
        ):
            return {"success": False, "error": "limite de réponses atteinte, réessayez plus tard"}

        self._emit("mail_reply_start", "Réponse email", alias=alias, uid=uid)

        imap_client: Optional[imaplib.IMAP4] = None
        smtp_client: Optional[smtplib.SMTP] = None
        try:
            imap_client = self._connect_imap_with_retry(account)
            status, _ = imap_client.select(folder, readonly=True)
            if status != "OK":
                return {"success": False, "error": f"dossier inaccessible: {folder}"}

            fetch_status, msg_data = imap_client.uid("FETCH", uid, "(RFC822)")
            if fetch_status != "OK" or not msg_data:
                return {"success": False, "error": f"email introuvable uid={uid}"}

            raw = b""
            for part in msg_data:
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                    raw = bytes(part[1])
                    break

            if not raw:
                return {"success": False, "error": "contenu email source vide"}

            source_message = BytesParser(policy=policy.default).parsebytes(raw)
            reply_to_header = source_message.get("Reply-To") or ""
            from_header = source_message.get("From") or ""
            to_headers = source_message.get_all("To", []) or []
            cc_headers = source_message.get_all("Cc", []) or []
            source_subject = self._decode_mime(source_message.get("Subject"))
            source_message_id = (source_message.get("Message-ID") or "").strip()
            source_references = (source_message.get("References") or "").strip()

            main_recipient = (email.utils.parseaddr(reply_to_header)[1] or email.utils.parseaddr(from_header)[1] or "").strip()
            if not main_recipient or not self._is_valid_email(main_recipient):
                return {"success": False, "error": "destinataire de réponse introuvable ou invalide"}

            own_email = ((account.get("email") or account.get("username") or "") or "").strip().lower()

            cc_recipients: List[str] = []
            if bool(reply_all):
                for _, address in email.utils.getaddresses(list(to_headers) + list(cc_headers)):
                    normalized = (address or "").strip()
                    if not normalized:
                        continue
                    if not self._is_valid_email(normalized):
                        continue
                    lower = normalized.lower()
                    if lower == main_recipient.lower() or (own_email and lower == own_email):
                        continue
                    cc_recipients.append(normalized)

            for chunk in [cc, bcc]:
                if not chunk:
                    continue
                for item in chunk.split(","):
                    normalized = item.strip()
                    if normalized:
                        if not self._is_valid_email(normalized):
                            return {"success": False, "error": f"destinataire invalide: {normalized}"}

            explicit_cc = [item.strip() for item in (cc or "").split(",") if item.strip()]
            explicit_bcc = [item.strip() for item in (bcc or "").split(",") if item.strip()]
            cc_recipients.extend(explicit_cc)

            cc_unique = list(dict.fromkeys(cc_recipients))
            bcc_unique = list(dict.fromkeys(explicit_bcc))
            all_recipients = [main_recipient] + cc_unique + bcc_unique
            all_recipients = list(dict.fromkeys(all_recipients))

            if not all_recipients:
                return {"success": False, "error": "aucun destinataire valide"}

            if len(all_recipients) > self._to_int(os.getenv("LUMENA_MAIL_MAX_RECIPIENTS", "20"), 20):
                return {"success": False, "error": "trop de destinataires"}

            for recipient in all_recipients:
                if not self._is_valid_email(recipient):
                    return {"success": False, "error": f"destinataire invalide: {recipient}"}

            policy_error = self._apply_recipient_policy(all_recipients)
            if policy_error:
                self._append_audit("mail_reply", alias, False, policy_error)
                return {"success": False, "error": policy_error}

            reply_subject = source_subject or ""
            if not re.match(r"^\s*re\s*:", reply_subject, flags=re.IGNORECASE):
                reply_subject = f"Re: {reply_subject}" if reply_subject else "Re:"
            reply_subject = reply_subject[:300]

            max_body = self._to_int(os.getenv("LUMENA_MAIL_MAX_BODY_CHARS", "200000"), 200000)
            body = (body or "")[: max(1, max_body)]

            try:
                attachment_paths = self._resolve_attachment_paths(attachments)
            except ValueError as exc:
                return {"success": False, "error": str(exc)}

            msg = EmailMessage()
            msg["From"] = account.get("email") or account.get("username") or ""
            msg["To"] = main_recipient
            if cc_unique:
                msg["Cc"] = ", ".join(cc_unique)
            msg["Subject"] = reply_subject
            msg["Date"] = email.utils.formatdate(localtime=True)
            _from_domain = (account.get("email") or "").split("@")[-1] or "lumena.local"
            msg["Message-ID"] = email.utils.make_msgid(domain=_from_domain)
            if source_message_id:
                msg["In-Reply-To"] = source_message_id
                references = f"{source_references} {source_message_id}".strip()
                if references:
                    msg["References"] = references
            elif source_references:
                msg["References"] = source_references
            msg.set_content(body or "")

            attached_names: List[str] = []
            for file_path in attachment_paths:
                try:
                    payload = file_path.read_bytes()
                except Exception as exc:
                    return {"success": False, "error": f"lecture pièce jointe impossible ({file_path.name}): {exc}"}

                maintype, subtype = self._guess_mime_parts(file_path)
                msg.add_attachment(payload, maintype=maintype, subtype=subtype, filename=file_path.name)
                attached_names.append(file_path.name)

            smtp_client = self._connect_smtp_with_retry(account)
            smtp_client.send_message(msg, to_addrs=all_recipients)

            self._emit("mail_reply_done", "Réponse envoyée", alias=alias, uid=uid, to=main_recipient)
            self._append_audit("mail_reply", alias, True, f"uid={uid},to={len(all_recipients)},attachments={len(attached_names)}")
            return {
                "success": True,
                "uid": uid,
                "to": [main_recipient],
                "cc": cc_unique,
                "subject": reply_subject,
                "reply_all": bool(reply_all),
                "attachments": attached_names,
            }
        except Exception as exc:
            self._append_audit("mail_reply", alias, False, str(exc))
            return {"success": False, "error": f"échec réponse: {exc}"}
        finally:
            if smtp_client is not None:
                try:
                    smtp_client.quit()
                except Exception:
                    pass  # SMTP quit cleanup best-effort
            if imap_client is not None:
                try:
                    imap_client.logout()
                except Exception:
                    pass  # IMAP logout cleanup best-effort

    def download_attachments(
        self,
        alias: str,
        uid: str,
        folder: str = "INBOX",
        output_dir: str = "",
        overwrite: bool = False,
        max_files: int = 25,
    ) -> Dict[str, Any]:
        if not self._mail_action_enabled("download_attachments", default=True):
            return {"success": False, "error": "download attachments désactivé (LUMENA_MAIL_ENABLE_DOWNLOAD_ATTACHMENTS=0)"}

        account = self._get_account(alias)
        uid = self._normalize_uid(uid)
        folder = self._normalize_folder(folder)
        max_files = max(1, min(self._to_int(max_files, 25), 100))

        if not self._check_rate_limit(
            "mail_download_attachments",
            self._to_int(os.getenv("LUMENA_MAIL_DOWNLOAD_ATTACHMENTS_MAX_PER_WINDOW", "60"), 60),
            self._to_int(os.getenv("LUMENA_MAIL_DOWNLOAD_ATTACHMENTS_WINDOW_SEC", "600"), 600),
        ):
            return {"success": False, "error": "limite de téléchargement atteinte, réessayez plus tard"}

        date_key = self._today_key()
        base_dir = self._workspace_root() / date_key / "mail" / "attachments" / alias / uid
        target_dir = self._resolve_workspace_output_dir(output_dir, base_dir)

        self._emit("mail_download_attachments_start", "Téléchargement pièces jointes", alias=alias, uid=uid)

        client: Optional[imaplib.IMAP4] = None
        try:
            client = self._connect_imap_with_retry(account)
            status, _ = client.select(folder)
            if status != "OK":
                return {"success": False, "error": f"dossier inaccessible: {folder}"}

            fetch_status, msg_data = client.uid("FETCH", uid, "(RFC822)")
            if fetch_status != "OK" or not msg_data:
                return {"success": False, "error": f"email introuvable uid={uid}"}

            raw = b""
            for part in msg_data:
                if isinstance(part, tuple) and len(part) >= 2 and isinstance(part[1], (bytes, bytearray)):
                    raw = bytes(part[1])
                    break

            if not raw:
                return {"success": False, "error": "contenu email vide"}

            message = BytesParser(policy=policy.default).parsebytes(raw)
            target_dir.mkdir(parents=True, exist_ok=True)

            saved: List[Dict[str, Any]] = []
            index = 0
            for part in message.walk():
                disp = (part.get("Content-Disposition") or "").lower()
                filename = self._decode_mime(part.get_filename())
                if "attachment" not in disp and not filename:
                    continue

                payload = part.get_payload(decode=True) or b""
                if not payload:
                    continue

                index += 1
                if index > max_files:
                    break

                content_type = (part.get_content_type() or "application/octet-stream").lower()
                safe_name = self._safe_attachment_name(filename, f"attachment_{index}.bin")
                output_path = target_dir / safe_name

                if output_path.exists() and not overwrite:
                    stem = output_path.stem
                    suffix = output_path.suffix
                    n = 2
                    while True:
                        candidate = target_dir / f"{stem}_{n}{suffix}"
                        if not candidate.exists():
                            output_path = candidate
                            break
                        n += 1

                output_path.write_bytes(payload)
                saved.append(
                    {
                        "filename": output_path.name,
                        "path": str(output_path),
                        "size": len(payload),
                        "content_type": content_type,
                    }
                )

            self._emit("mail_download_attachments_done", "Pièces jointes téléchargées", alias=alias, uid=uid, count=len(saved))
            self._append_audit("mail_download_attachments", alias, True, f"uid={uid},count={len(saved)}")
            return {
                "success": True,
                "uid": uid,
                "folder": folder,
                "output_dir": str(target_dir),
                "count": len(saved),
                "attachments": saved,
            }
        except Exception as exc:
            self._append_audit("mail_download_attachments", alias, False, str(exc))
            return {"success": False, "error": f"échec téléchargement pièces jointes: {exc}"}
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass  # IMAP logout cleanup best-effort

    def delete_message(self, alias: str, uid: str, folder: str = "INBOX", expunge: bool = True) -> Dict[str, Any]:
        if not self._mail_action_enabled("delete", default=True):
            return {"success": False, "error": "suppression désactivée (LUMENA_MAIL_ENABLE_DELETE=0)"}
        account = self._get_account(alias)
        uid = self._normalize_uid(uid)
        folder = self._normalize_folder(folder)

        if not self._check_rate_limit(
            "mail_delete",
            self._to_int(os.getenv("LUMENA_MAIL_DELETE_MAX_PER_WINDOW", "100"), 100),
            self._to_int(os.getenv("LUMENA_MAIL_DELETE_WINDOW_SEC", "600"), 600),
        ):
            return {"success": False, "error": "limite de suppression atteinte, réessayez plus tard"}

        self._emit("mail_delete_start", "Suppression email", alias=alias, uid=uid)

        client: Optional[imaplib.IMAP4] = None
        try:
            client = self._connect_imap_with_retry(account)
            status, _ = client.select(folder)
            if status != "OK":
                return {"success": False, "error": f"dossier inaccessible: {folder}"}

            mark_status, _ = client.uid("STORE", uid, "+FLAGS", "\\Deleted")
            if mark_status != "OK":
                return {"success": False, "error": f"échec suppression uid={uid}"}

            if expunge:
                client.expunge()

            self._emit("mail_delete_done", "Email supprimé", alias=alias, uid=uid)
            self._append_audit("mail_delete", alias, True, f"folder={folder},uid={uid},expunge={bool(expunge)}")
            return {"success": True, "uid": uid, "expunged": bool(expunge)}
        except Exception as exc:
            self._append_audit("mail_delete", alias, False, str(exc))
            return {"success": False, "error": f"échec suppression: {exc}"}
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass  # IMAP logout cleanup best-effort

    def move_message(
        self,
        alias: str,
        uid: str,
        target_folder: str,
        source_folder: str = "INBOX",
    ) -> Dict[str, Any]:
        if not self._mail_action_enabled("move", default=True):
            return {"success": False, "error": "déplacement désactivé (LUMENA_MAIL_ENABLE_MOVE=0)"}
        account = self._get_account(alias)
        uid = self._normalize_uid(uid)
        source_folder = self._normalize_folder(source_folder)
        target_folder = self._normalize_folder(target_folder)
        if not target_folder.strip():
            return {"success": False, "error": "target_folder vide"}

        if not self._check_rate_limit(
            "mail_move",
            self._to_int(os.getenv("LUMENA_MAIL_MOVE_MAX_PER_WINDOW", "150"), 150),
            self._to_int(os.getenv("LUMENA_MAIL_MOVE_WINDOW_SEC", "600"), 600),
        ):
            return {"success": False, "error": "limite de déplacement atteinte, réessayez plus tard"}

        self._emit("mail_move_start", "Déplacement email", alias=alias, uid=uid, target=target_folder)

        client: Optional[imaplib.IMAP4] = None
        try:
            client = self._connect_imap_with_retry(account)
            status, _ = client.select(source_folder)
            if status != "OK":
                return {"success": False, "error": f"dossier source inaccessible: {source_folder}"}

            copy_status, _ = client.uid("COPY", uid, target_folder)
            if copy_status != "OK":
                return {"success": False, "error": "copie vers dossier cible échouée"}

            mark_status, _ = client.uid("STORE", uid, "+FLAGS", "\\Deleted")
            if mark_status != "OK":
                return {"success": False, "error": "copie ok mais suppression source échouée"}
            client.expunge()

            self._emit("mail_move_done", "Email déplacé", alias=alias, uid=uid, target=target_folder)
            self._append_audit("mail_move", alias, True, f"uid={uid},from={source_folder},to={target_folder}")
            return {"success": True, "uid": uid, "source": source_folder, "target": target_folder}
        except Exception as exc:
            self._append_audit("mail_move", alias, False, str(exc))
            return {"success": False, "error": f"échec déplacement: {exc}"}
        finally:
            if client is not None:
                try:
                    client.logout()
                except Exception:
                    pass  # IMAP logout cleanup best-effort

    def quick_test(self, alias: str) -> Dict[str, Any]:
        """Teste l'auth IMAP/SMTP d'un compte."""
        account = self._get_account(alias)
        imap_ok = False
        smtp_ok = False
        imap_error = ""
        smtp_error = ""

        imap_client: Optional[imaplib.IMAP4] = None
        smtp_client: Optional[smtplib.SMTP] = None
        try:
            imap_client = self._connect_imap_with_retry(account)
            imap_ok = True
        except Exception as exc:
            imap_error = str(exc)
            logger.warning("MailHub quick_test IMAP error alias={}: {}", alias, exc)
        finally:
            if imap_client is not None:
                try:
                    imap_client.logout()
                except Exception:
                    pass  # IMAP logout cleanup best-effort

        try:
            smtp_client = self._connect_smtp_with_retry(account)
            smtp_ok = True
        except Exception as exc:
            smtp_error = str(exc)
            logger.warning("MailHub quick_test SMTP error alias={}: {}", alias, exc)
        finally:
            if smtp_client is not None:
                try:
                    smtp_client.quit()
                except Exception:
                    pass  # SMTP quit cleanup best-effort

        ok = imap_ok and smtp_ok
        self._emit("mail_quick_test", "Test connectivité mail", alias=alias, imap_ok=imap_ok, smtp_ok=smtp_ok)
        self._append_audit("mail_quick_test", alias, ok, f"imap_ok={imap_ok},smtp_ok={smtp_ok}")
        return {
            "success": ok,
            "alias": alias,
            "imap_ok": imap_ok,
            "smtp_ok": smtp_ok,
            "imap_error": imap_error,
            "smtp_error": smtp_error,
        }
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
