from pathlib import Path
import json
import os
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.utils import file_lock as file_lock_module
from src.utils.file_lock import ProcessFileLock


def test_process_file_lock_reclaims_stale_lock(monkeypatch, tmp_path: Path):
    lock_path = tmp_path / "lumena_web.lock"
    lock_path.write_text(
        json.dumps(
            {
                "lock_name": "lumena-web",
                "pid": 999999,
                "owner_id": "old",
                "created_at": 0.0,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(file_lock_module, "_is_process_alive", lambda _pid: False)

    lock = ProcessFileLock(lock_path, lock_name="lumena-web", owner_id="new-owner")
    assert lock.acquire() is True
    info = lock.read_lock_info()
    assert info.get("owner_id") == "new-owner"
    assert info.get("pid") == os.getpid()
    lock.release()


@pytest.mark.skipif(file_lock_module.os.name != "nt", reason="Windows-specific tasklist parsing")
def test_is_process_alive_windows_returns_false_for_info(monkeypatch):
    class _Result:
        stdout = "INFO: No tasks are running which match the specified criteria.\r\n"

    monkeypatch.setattr(file_lock_module.subprocess, "run", lambda *a, **k: _Result())
    assert file_lock_module._is_process_alive(12345) is False


@pytest.mark.skipif(file_lock_module.os.name != "nt", reason="Windows-specific tasklist parsing")
def test_is_process_alive_windows_returns_true_when_pid_present(monkeypatch):
    class _Result:
        stdout = '"python.exe","12345","Console","1","10,000 K"\r\n'

    monkeypatch.setattr(file_lock_module.subprocess, "run", lambda *a, **k: _Result())
    assert file_lock_module._is_process_alive(12345) is True
