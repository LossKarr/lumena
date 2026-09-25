"""Windows per-user pairing store, protected by DPAPI and a private directory ACL."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

from filelock import FileLock

from .ide_pairing import PairingCredentials, PairingError

ENTROPY = b"Lumena IDE pairing v1"


def default_pairing_directory() -> Path:
    override = os.environ.get("LUMENA_IDE_PAIRING_DIR", "")
    if override:
        if sys.platform != "win32" or not Path(override).is_absolute():
            raise PairingError("protected_storage_unavailable")
        return Path(override)
    local = os.environ.get("LOCALAPPDATA", "")
    if sys.platform != "win32" or not local or not Path(local).is_absolute():
        raise PairingError("protected_storage_unavailable")
    return Path(local) / "Lumena" / "ide-connection"


class PairingStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory if directory is not None else default_pairing_directory()
        self.path = self.directory / "pairing.dpapi.json"

    def _prepare(self) -> None:
        if sys.platform != "win32":
            raise PairingError("protected_storage_unavailable")
        import win32api
        import win32security

        self.directory.mkdir(parents=True, exist_ok=True)
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        try:
            user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        system = win32security.CreateWellKnownSid(win32security.WinLocalSystemSid, None)
        dacl = win32security.ACL()
        flags = win32security.OBJECT_INHERIT_ACE | win32security.CONTAINER_INHERIT_ACE
        for sid in (user, system):
            dacl.AddAccessAllowedAceEx(win32security.ACL_REVISION, flags, 0x10000000, sid)
        win32security.SetNamedSecurityInfo(
            str(self.directory), win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None, None, dacl, None,
        )

    def load(self) -> PairingCredentials | None:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(4097)
        except FileNotFoundError:
            return None
        try:
            if len(raw) > 4096:
                raise ValueError()
            value = json.loads(raw)
            if not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] != 1:
                raise ValueError()
            if value.get("state") == "revoked" and set(value) == {"schema", "state"}:
                return None
            if value.get("state") != "active" or set(value) != {"schema", "state", "key_id", "encrypted"}:
                raise ValueError()
            if sys.platform != "win32":
                raise PairingError("protected_storage_unavailable")
            import win32crypt

            blob = base64.b64decode(value["encrypted"], validate=True)
            secret = win32crypt.CryptUnprotectData(blob, ENTROPY, None, None, 1)[1]
            return PairingCredentials(value["key_id"], secret)
        except Exception:
            raise PairingError("pairing_store_invalid") from None

    def _write(self, value: dict) -> None:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=self.directory, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(value, separators=(",", ":")).encode("ascii"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _new_key(self) -> PairingCredentials:
        import win32crypt

        key = PairingCredentials(secrets.token_hex(16), secrets.token_bytes(32))
        encrypted = win32crypt.CryptProtectData(key.secret, None, ENTROPY, None, None, 1)
        self._write({
            "schema": 1, "state": "active", "key_id": key.key_id,
            "encrypted": base64.b64encode(encrypted).decode("ascii"),
        })
        return key

    def initialize(self) -> PairingCredentials | None:
        self._prepare()
        with FileLock(str(self.directory / "pairing.lock")):
            if self.path.exists():
                return self.load()
            return self._new_key()

    def rotate(self) -> PairingCredentials:
        """An explicit rotation creates a new key id, invalidating previous sessions."""
        self._prepare()
        with FileLock(str(self.directory / "pairing.lock")):
            return self._new_key()

    def revoke(self) -> None:
        self._prepare()
        with FileLock(str(self.directory / "pairing.lock")):
            self._write({"schema": 1, "state": "revoked"})
