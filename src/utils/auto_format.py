"""
Auto-format post-edit — P6 Plan Suprême CodeAgent.

Lance le formatter adapté au langage après une édition réussie :
- Python : `ruff format` (rapide, silencieux)
- JS/TS/HTML/CSS/JSON : skip (pas de dépendance imposée, évite les surprises)

Fail-safe : toute erreur → silencieux. Opt-out via LUMENA_AUTO_FORMAT=0.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Optional


_PYTHON_EXTS = (".py",)


async def auto_format_file(file_path: str, workspace_root: Optional[Path] = None) -> str:
    """
    Tente un format non-invasif sur le fichier si le flag AUTO_FORMAT est actif.

    Returns:
        "" en cas de skip / succès silencieux,
        message court "formatted: N change(s)" si reformaté,
        "" sur erreur (fail-safe).
    """
    try:
        from src.config.codeagent_flags import AUTO_FORMAT
        if not AUTO_FORMAT:
            return ""
    except Exception:
        return ""

    if not file_path:
        return ""

    path_obj = Path(file_path)
    ext = path_obj.suffix.lower()
    if ext not in _PYTHON_EXTS:
        return ""

    abs_path = path_obj if path_obj.is_absolute() else (workspace_root or Path.cwd()) / path_obj
    if not abs_path.exists():
        return ""

    # Localiser ruff (venv Windows / Linux)
    root = Path(__file__).parent.parent.parent
    ruff_exe = root / "venv" / "Scripts" / "ruff.exe"
    if not ruff_exe.exists():
        ruff_exe = root / "venv" / "bin" / "ruff"
    if not ruff_exe.exists():
        return ""

    try:
        # Lecture pré-format pour détecter si ruff a changé quelque chose
        before = abs_path.read_text(encoding="utf-8")
        proc = await asyncio.to_thread(
            subprocess.run,
            [str(ruff_exe), "format", "--quiet", str(abs_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return ""
        after = abs_path.read_text(encoding="utf-8")
        if before == after:
            return ""
        # Comptage grossier des lignes changées
        diff_lines = sum(1 for a, b in zip(before.splitlines(), after.splitlines()) if a != b)
        diff_lines += abs(len(before.splitlines()) - len(after.splitlines()))
        return f"🎨 auto-format (ruff): {diff_lines} ligne(s) normalisée(s)"
    except Exception:
        return ""


__all__ = ["auto_format_file"]
