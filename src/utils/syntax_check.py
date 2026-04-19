"""Validation syntaxique légère pour les fichiers édités par CodeAgent et ReAct.

P7 PLAN_SUPREME_CODEAGENT — réutilisable depuis :
- src/agents/sub_agent.py (CodeAgent)
- src/reasoning/handlers/files.py (ReAct write_file/edit_file/apply_patch)

Tous les checks sont :
- Async (subprocess + to_thread)
- Best-effort : retournent "" en cas d'absence d'outil
- Bornés : timeout court (5-15s)
- Non-bloquants : ne lèvent jamais d'exception
- Sortie brève : tronqué à 600 chars max
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from loguru import logger


_PY_EXTS = (".py",)
_JS_EXTS = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")
_HTML_EXTS = (".html", ".htm")
_CSS_EXTS = (".css", ".scss", ".sass")
_JSON_EXTS = (".json",)


async def check_syntax(file_path: str | Path, *, workspace_root: Path | None = None) -> str:
    """Dispatcher unique. Retourne "" si OK, message d'erreur tronqué sinon.

    Ne lève jamais d'exception — tous les fallbacks silencieux.
    """
    try:
        from src.config.codeagent_flags import REACT_QUALITY_GATES
        if not REACT_QUALITY_GATES:
            return ""
    except Exception:
        pass  # flag inaccessible → on continue quand même

    try:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return ""
        suffix = path.suffix.lower()
        if suffix in _PY_EXTS:
            return await _check_python(path, workspace_root)
        if suffix in _JS_EXTS:
            return await _check_javascript(path)
        if suffix in _JSON_EXTS:
            return _check_json(path)
        if suffix in _HTML_EXTS:
            return _check_brackets(path, html_mode=True)
        if suffix in _CSS_EXTS:
            return _check_brackets(path, html_mode=False)
    except Exception as exc:
        logger.debug(f"[syntax_check] exception silencieuse: {exc}")
    return ""


async def _check_python(path: Path, workspace_root: Path | None) -> str:
    """Ruff > py_compile (syntaxe + lint léger)."""
    root = workspace_root or Path(__file__).resolve().parent.parent.parent

    # 1) ruff (priorité)
    ruff_exe = root / "venv" / "Scripts" / "ruff.exe"
    if not ruff_exe.exists():
        ruff_exe = root / "venv" / "bin" / "ruff"
    if not ruff_exe.exists():
        ruff_path = shutil.which("ruff")
        if ruff_path:
            ruff_exe = Path(ruff_path)

    if ruff_exe.exists():
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                [
                    str(ruff_exe), "check", "--select", "E,F",
                    "--no-fix", "--output-format", "concise", str(path),
                ],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode != 0 and (proc.stdout or "").strip():
                return proc.stdout.strip()[:600]
            return ""
        except Exception:
            pass  # fallback py_compile

    # 2) py_compile (syntaxe stricte uniquement)
    try:
        import sys
        proc = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip()[:500]
    except Exception:
        pass
    return ""


async def _check_javascript(path: Path) -> str:
    """node --check (best-effort, silencieux si node absent)."""
    node_exe = shutil.which("node")
    if not node_exe:
        return ""
    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            [node_exe, "--check", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return (proc.stderr or proc.stdout).strip()[:500]
    except Exception:
        pass
    return ""


def _check_json(path: Path) -> str:
    """json.loads — détection rapide JSON cassé."""
    import json
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return f"JSON invalide L{exc.lineno}:{exc.colno} — {exc.msg}"[:300]
    except Exception:
        pass
    return ""


def _check_brackets(path: Path, *, html_mode: bool) -> str:
    """Bracket balance check (HTML/CSS).

    Ignore strings/commentaires basiques. Détecte juste les déséquilibres
    grossiers ({} sans paire, ou tags HTML grossièrement déséquilibrés).
    """
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""

    # Strip strings + comments
    import re
    if html_mode:
        # Strip <!-- ... -->
        clean = re.sub(r"<!--[\s\S]*?-->", "", content)
        # Compte les balises ouvrantes vs fermantes (heuristique grossière)
        opens = len(re.findall(r"<(?!/)[a-zA-Z][^>]*?(?<!/)>", clean))
        closes = len(re.findall(r"</[a-zA-Z][^>]*?>", clean))
        # Tolérance large : tags void (img, br, input, hr, meta, link)
        # Si écart > 10 → probable problème
        if abs(opens - closes) > 15:
            return f"HTML déséquilibré : {opens} balises ouvrantes vs {closes} fermantes"
    else:
        # CSS : strip /* ... */ et strings
        clean = re.sub(r'/\*[\s\S]*?\*/', "", content)
        clean = re.sub(r'"(?:[^"\\]|\\.)*"', '""', clean)
        clean = re.sub(r"'(?:[^'\\]|\\.)*'", "''", clean)
        diff = clean.count("{") - clean.count("}")
        if diff != 0:
            return f"CSS accolades déséquilibrées : {diff:+d} (excès)"
    return ""
