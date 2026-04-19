"""P8.ENV_CONTEXT — contexte environnement injecté dans le prompt système.

Fournit platform, python version, cwd, et git branch (best-effort) dans un
bloc compact prêt-à-injecter. Gardé par flag LUMENA_ENV_CONTEXT.

Usage:
    from src.utils.env_context import build_env_context_block
    block = build_env_context_block(workspace_path)  # "" si flag off
"""
from __future__ import annotations

import platform
import subprocess
import sys
from functools import lru_cache
from pathlib import Path


def _git_branch(workspace: Path) -> str:
    """Best-effort git branch, retourne '' si non-git ou timeout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
        if result.returncode == 0:
            return (result.stdout or "").strip()
    except Exception:
        return ""
    return ""


@lru_cache(maxsize=8)
def _build_cached(workspace_path: str, flag: bool) -> str:
    if not flag:
        return ""
    try:
        ws = Path(workspace_path) if workspace_path else Path.cwd()
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        lines = [
            "== ENVIRONNEMENT D'EXÉCUTION ==",
            f"- OS: {platform.system()} {platform.release()} ({platform.machine()})",
            f"- Python: {py_ver}",
            f"- CWD: {ws}",
        ]
        branch = _git_branch(ws)
        if branch:
            lines.append(f"- Git branch: {branch}")
        lines.append(
            "- Shell Windows: PowerShell (utilise `;` pour chaîner, PAS `&&`)"
            if platform.system() == "Windows"
            else "- Shell POSIX: bash (`&&` et `|` supportés)"
        )
        return "\n".join(lines) + "\n"
    except Exception:
        return ""


def build_env_context_block(workspace_path: str = "") -> str:
    """Construit un bloc de contexte ENV compact (max ~10 lignes).

    Retourne "" si le flag ENV_CONTEXT est désactivé.
    Best-effort : toute erreur renvoie "".
    Résultat mis en cache si PROMPT_CACHE actif.
    """
    try:
        from src.config.codeagent_flags import ENV_CONTEXT, PROMPT_CACHE
    except Exception:
        return ""

    if not ENV_CONTEXT:
        return ""

    if PROMPT_CACHE:
        return _build_cached(workspace_path, True)
    # Sans cache : appel direct (toujours frais)
    _build_cached.cache_clear()
    return _build_cached(workspace_path, True)


__all__ = ["build_env_context_block"]

