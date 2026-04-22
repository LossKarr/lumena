"""Simple process-scoped file lock used to avoid duplicate local instances."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional


def default_lock_path(filename: str) -> Path:
    """Return a lock path under the system temp directory."""
    return Path(tempfile.gettempdir()) / filename


class ProcessFileLock:
    """Atomic lock file based on O_EXCL create semantics."""

    def __init__(self, path: Path | str, lock_name: str, owner_id: Optional[str] = None):
        self.path = Path(path)
        self.lock_name = lock_name
        self.owner_id = owner_id or f"{socket.gethostname()}:{os.getpid()}"
        self._fd: Optional[int] = None

    @property
    def is_acquired(self) -> bool:
        return self._fd is not None

    def read_lock_info(self) -> Dict[str, Any]:
        """Read lock owner metadata if available."""
        if not self.path.exists():
            return {}
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
            if not raw:
                return {}
            info = json.loads(raw)
            if isinstance(info, dict):
                return info
        except Exception:
            pass  # lock file illisible
        return {}

    def acquire(self) -> bool:
        """Try to acquire the lock. Returns False if another live process owns it."""
        if self._fd is not None:
            return True

        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "lock_name": self.lock_name,
            "pid": os.getpid(),
            "owner_id": self.owner_id,
            "created_at": time.time(),
        }
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")

        for attempt in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, data)
                try:
                    os.fsync(fd)
                except OSError:
                    # fsync may not be available on some file systems.
                    pass  # fsync non supporté
                self._fd = fd
                return True
            except FileExistsError:
                if attempt == 0 and self._is_stale_lock():
                    self._try_remove_stale_lock()
                    continue
                return False
            except OSError:
                return False

        return False

    def release(self) -> None:
        """Release lock if held by current process."""
        if self._fd is None:
            return

        try:
            os.close(self._fd)
        except OSError:
            pass  # fd déjà fermé
        finally:
            self._fd = None

        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass  # lock file cleanup best-effort

    def _is_stale_lock(self) -> bool:
        info = self.read_lock_info()
        pid = info.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            return False
        if pid == os.getpid():
            return False
        return not _is_process_alive(pid)

    def _try_remove_stale_lock(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass  # stale lock removal best-effort


def _is_process_alive(pid: int) -> bool:
    """Cross-platform alive check for another process ID."""
    if pid <= 0:
        return False

    # On Windows, os.kill(pid, 0) may return OSError for missing PIDs,
    # so we rely on tasklist which is more reliable for existence checks.
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=2,
                check=False,
            )
            stdout = (result.stdout or "").strip()
            if not stdout:
                return False
            if stdout.startswith("INFO:"):
                return False
            return f'"{pid}"' in stdout or f",{pid}," in stdout
        except Exception:
            # Fail-safe: if detection fails, do not break lock semantics.
            return True

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
