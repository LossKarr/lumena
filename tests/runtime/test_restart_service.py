from __future__ import annotations

from pathlib import Path
import base64
import json

import pytest

from src.runtime import restart_service
from src.runtime import restart_helper


def test_desktop_launch_command_uses_local_entry_and_only_safe_switch(tmp_path, monkeypatch):
    entry = tmp_path / "run_desktop.py"
    entry.write_text("", encoding="utf-8")
    monkeypatch.setattr(restart_service.sys, "executable", str(tmp_path / "python.exe"))
    command = restart_service.desktop_launch_command(
        root=tmp_path, argv=["--web", "--not-forwarded"],
    )
    assert Path(command[0]).name in {"python.exe", "pythonw.exe"}
    assert command[1:] == [str(entry.resolve()), "--web"]


def test_windows_desktop_restart_uses_canonical_hidden_batch(tmp_path, monkeypatch):
    entry = tmp_path / "run_desktop.py"
    entry.write_text("", encoding="utf-8")
    launcher = tmp_path / "START_DESKTOP.bat"
    launcher.write_text("@echo off\r\n", encoding="utf-8")
    monkeypatch.setattr(restart_service.os, "name", "nt")
    command = restart_service.desktop_launch_command(root=tmp_path, argv=[])
    assert command == [str(launcher.resolve()), "_RUNNING_HIDDEN"]


def test_desktop_launch_command_fails_closed_without_entry(tmp_path):
    with pytest.raises(restart_service.RestartError, match="desktop_entry_missing"):
        restart_service.desktop_launch_command(root=tmp_path)


def test_windows_restart_uses_hidden_start_process_without_shell(tmp_path, monkeypatch):
    (tmp_path / "run_desktop.py").write_text("", encoding="utf-8")
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    monkeypatch.setattr(restart_service.sys, "executable", str(python))
    monkeypatch.setattr(restart_service.os, "name", "nt")
    seen = {}

    class _Result:
        returncode = 0

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr(restart_service.subprocess, "run", fake_run)
    result = restart_service.schedule_desktop_restart(
        root=tmp_path, current_pid=4242, argv=[],
    )
    assert result == {"scheduled": True, "pid": 4242, "mode": "desktop"}
    assert seen["command"][:6] == [
        "powershell.exe", "-NoProfile", "-NonInteractive",
        "-WindowStyle", "Hidden", "-Command",
    ]
    assert seen["kwargs"]["timeout"] == 15
    assert "shell" not in seen["kwargs"]
    assert "Start-Process" in seen["command"][-1]
    assert "--payload" in seen["command"][-1]


def test_restart_payload_preserves_paths_with_spaces(tmp_path, monkeypatch):
    root = tmp_path / "Lumena Product"
    root.mkdir()
    entry = root / "run_desktop.py"
    entry.write_text("", encoding="utf-8")
    python = tmp_path / "Python Runtime" / "python.exe"
    python.parent.mkdir()
    python.write_bytes(b"")
    monkeypatch.setattr(restart_service.sys, "executable", str(python))
    monkeypatch.setattr(restart_service.os, "name", "nt")
    captured = {}

    class _Result:
        returncode = 0

    def fake_run(command, **_kwargs):
        captured["script"] = command[-1]
        return _Result()

    monkeypatch.setattr(restart_service.subprocess, "run", fake_run)
    restart_service.schedule_desktop_restart(root=root, current_pid=42)
    marker = "'--payload','"
    encoded = captured["script"].split(marker, 1)[1].split("'", 1)[0]
    payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    assert payload["root"] == str(root.resolve())
    assert payload["command"][1] == str(entry.resolve())


def test_restart_helper_waits_then_launches_exact_local_command(tmp_path, monkeypatch):
    entry = tmp_path / "run_desktop.py"
    entry.write_text("", encoding="utf-8")
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    payload = base64.urlsafe_b64encode(json.dumps({
        "wait_pid": 73, "root": str(tmp_path),
        "command": [str(python), str(entry)],
    }).encode("utf-8")).decode("ascii")
    events = []
    monkeypatch.setattr(
        restart_helper, "wait_for_exit",
        lambda pid: events.append(("wait", pid)) or True,
    )
    monkeypatch.setattr(
        restart_helper, "launch",
        lambda command, root: events.append(("launch", command, root)),
    )
    assert restart_helper.main(["--payload", payload]) == 0
    assert events[0] == ("wait", 73)
    assert events[1][1] == [str(python), str(entry)]


def test_restart_helper_launches_batch_hidden_without_shell(tmp_path, monkeypatch):
    launcher = tmp_path / "START_DESKTOP.bat"
    launcher.write_text("@echo off\r\n", encoding="utf-8")
    seen = {}

    class _Result:
        returncode = 0

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return _Result()

    monkeypatch.setattr(restart_helper.os, "name", "nt")
    monkeypatch.setattr(restart_helper.subprocess, "run", fake_run)
    restart_helper.launch(
        [str(launcher.resolve()), "_RUNNING_HIDDEN"], root=tmp_path.resolve(),
    )
    assert seen["command"][:6] == [
        "powershell.exe", "-NoProfile", "-NonInteractive",
        "-WindowStyle", "Hidden", "-Command",
    ]
    assert "Start-Process" in seen["command"][-1]
    assert "-WindowStyle Hidden" in seen["command"][-1]
    assert "shell" not in seen["kwargs"]
