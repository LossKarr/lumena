from __future__ import annotations

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def test_optional_pyautogui_import_cannot_leak_cv2_into_sys_path() -> None:
    """A failed optional OpenCV bootstrap must not poison spawned workers."""
    script = (
        "import sys; before=list(sys.path); "
        "import src.computer_use.controller; "
        "leaked=[p for p in sys.path if p not in before and p.lower().rstrip('\\\\/').endswith('cv2')]; "
        "raise SystemExit(1 if leaked else 0)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
