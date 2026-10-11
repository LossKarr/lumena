"""Safe process restart orchestration for the Lumena desktop runtime."""
from __future__ import annotations

import os
import base64
import json
from pathlib import Path
import subprocess
import sys
from typing import Sequence


class RestartError(RuntimeError):
    """The detached restart helper could not be scheduled."""


def _powershell_literal(value: object) -> str:
    """Quote a trusted local value as one PowerShell single-quoted literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _helper_python() -> Path:
    """Return a windowless interpreter for the short-lived restart helper."""
    python = Path(sys.executable).resolve()
    if os.name == "nt":
        pythonw = python.with_name("pythonw.exe")
        if pythonw.is_file():
            return pythonw
    return python


def desktop_launch_command(*, root: Path, argv: Sequence[str] | None = None) -> list[str]:
    """Return the canonical desktop command that the helper must relaunch.

    On Windows the batch launcher owns runtime repair, Ollama detection and,
    crucially, one hidden console inherited by every console-based MCP child.
    Launching ``run_desktop.py`` under ``pythonw`` directly leaves those children
    without a console and Windows may create one visible window per process.
    """
    root = Path(root).resolve()
    entry = root / "run_desktop.py"
    if not entry.is_file():
        raise RestartError("desktop_entry_missing")
    forwarded = [item for item in (argv or ()) if item == "--web"]
    if os.name == "nt" and not forwarded:
        launcher = root / "START_DESKTOP.bat"
        if launcher.is_file():
            # Suppress the .env debug-console preference for an in-product restart.
            return [str(launcher), "_RUNNING_HIDDEN"]
    return [str(_helper_python()), str(entry), *forwarded]


def schedule_desktop_restart(
    *, root: Path, current_pid: int | None = None,
    argv: Sequence[str] | None = None,
) -> dict[str, object]:
    """Spawn an orphaned helper which waits for this process then relaunches Lumena."""
    root = Path(root).resolve()
    pid = int(current_pid or os.getpid())
    if pid <= 0:
        raise RestartError("invalid_process_id")
    launch = desktop_launch_command(root=root, argv=argv)
    payload = base64.urlsafe_b64encode(json.dumps(
        {"wait_pid": pid, "root": str(root), "command": launch},
        ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")).decode("ascii")
    helper = [
        str(_helper_python()), "-m", "src.runtime.restart_helper",
        "--payload", payload,
    ]
    try:
        if os.name == "nt":
            # Start-Process is allowed to finish before the old process is killed.
            # The helper then belongs to the Windows shell host, so taskkill /T on
            # Lumena cannot remove the process responsible for relaunching it.
            arguments = ",".join(_powershell_literal(item) for item in helper[1:])
            command = (
                "$ErrorActionPreference='Stop';"
                f"$p=Start-Process -FilePath {_powershell_literal(helper[0])} "
                f"-ArgumentList @({arguments}) -WorkingDirectory {_powershell_literal(root)} "
                "-WindowStyle Hidden -PassThru;"
                "if(-not $p.Id){exit 2}"
            )
            completed = subprocess.run(
                [
                    "powershell.exe", "-NoProfile", "-NonInteractive",
                    "-WindowStyle", "Hidden", "-Command", command,
                ],
                cwd=str(root), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=15, check=False,
            )
            if completed.returncode != 0:
                raise RestartError("restart_helper_start_failed")
        else:
            subprocess.Popen(
                helper, cwd=str(root), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RestartError("restart_helper_start_failed") from exc
    return {"scheduled": True, "pid": pid, "mode": "desktop"}
