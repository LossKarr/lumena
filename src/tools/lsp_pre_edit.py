"""
P5 — LSP pre-edit : injecter les fichiers dépendants avant une modification.

Activé uniquement si LUMENA_LSP_PRE_EDIT=1 (opt-IN).
Fail-open systématique : toute erreur LSP retourne une liste vide.
Fallback AST/grep activé automatiquement si le LSP est absent.

Utilisation :
    files = await get_related_files(workspace, "src/calculator.py", line=5)
    # → ["tests/test_calculator.py", "src/main.py"]
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Optional, Sequence

from loguru import logger

# Extensions considérées par le fallback
_PY_EXT = {".py"}
_JS_EXT = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs", ".vue"}
_IGNORE_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "dist", "build", ".backups"}


async def get_related_files(
    workspace: Path,
    file_path: str,
    line: int = 0,
    col: int = 0,
    *,
    timeout: float = 8.0,
    task_id: str = "",
) -> list[str]:
    """
    Retourne les fichiers qui dépendent du symbole à (file_path, line, col).

    Stratégie :
      1. LSP find-references (si disponible)
      2. Fallback AST/grep selon le langage (Python → ast imports, JS/TS → regex imports)
    Fail-open systématique : toute erreur retourne [].
    """
    # Essai LSP
    try:
        result = await asyncio.wait_for(
            _do_lsp_lookup(workspace, file_path, line, col),
            timeout=timeout,
        )
        if result:
            return result
        # LSP disponible mais sans résultats → tenter quand même le fallback
    except asyncio.TimeoutError:
        from src.utils.gate_metrics import record_lsp_fail_open
        record_lsp_fail_open(task_id=task_id, error=f"LSP pre-edit timeout {timeout}s")
        logger.debug("[lsp_pre_edit] LSP timeout — fallback AST/grep")
    except Exception as exc:
        from src.utils.gate_metrics import record_lsp_fail_open
        record_lsp_fail_open(task_id=task_id, error=str(exc))
        logger.debug("[lsp_pre_edit] LSP fail-open: {} — fallback AST/grep", exc)

    # Fallback AST/grep
    try:
        return _fallback_related_files(workspace, file_path)
    except Exception as exc:
        logger.debug("[lsp_pre_edit] fallback échoué: {}", exc)
        return []


def _fallback_related_files(workspace: Path, file_path: str) -> list[str]:
    """
    Fallback statique quand le LSP est absent.
    Cherche dans le workspace quels fichiers importent le fichier édité.
    """
    ws = Path(workspace).resolve()
    edited = Path(file_path)
    suffix = edited.suffix.lower()

    if suffix in _PY_EXT:
        return _python_importers(ws, edited)
    if suffix in _JS_EXT:
        return _js_importers(ws, edited)
    return []


def _python_importers(workspace: Path, edited: Path) -> list[str]:
    """Trouve les fichiers Python qui importent le module édité (via AST ou regex)."""
    # Déduire le nom de module depuis le chemin
    try:
        rel = edited.resolve().relative_to(workspace.resolve())
    except ValueError:
        return []

    # Convertir path → module (src/utils/foo.py → src.utils.foo)
    parts = list(rel.with_suffix("").parts)
    module_name = ".".join(parts)
    module_leaf = parts[-1]  # juste le nom du fichier sans extension

    # Patterns d'import à chercher
    patterns = [
        re.compile(rf"\bimport\s+{re.escape(module_leaf)}\b"),
        re.compile(rf"\bfrom\s+{re.escape(module_name)}\b"),
        re.compile(rf"\bfrom\s+[\w.]*{re.escape(module_leaf)}\b"),
    ]

    results: list[str] = []
    for f in workspace.rglob("*.py"):
        if any(part in _IGNORE_DIRS for part in f.parts):
            continue
        if f.resolve() == edited.resolve():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            if any(p.search(text) for p in patterns):
                try:
                    results.append(str(f.relative_to(workspace)))
                except ValueError:
                    results.append(str(f))
        except OSError:
            continue
        if len(results) >= 20:
            break
    return results


def _js_importers(workspace: Path, edited: Path) -> list[str]:
    """Trouve les fichiers JS/TS qui importent le fichier édité (via regex)."""
    try:
        rel = edited.resolve().relative_to(workspace.resolve())
    except ValueError:
        return []

    # Noms plausibles dans un import : sans extension, avec .js, avec index
    stem = edited.stem  # "foo" from "foo.ts"
    rel_no_ext = str(rel.with_suffix("")).replace("\\", "/")

    # Patterns: import ... from './foo', require('./foo'), etc.
    patterns = [
        re.compile(rf"""['"](\.\.?/)*{re.escape(stem)}(?:\.[jt]sx?|/index(?:\.[jt]sx?)?)?['"]"""),
        re.compile(rf"""['"](\.\.?/)*{re.escape(rel_no_ext)}(?:\.[jt]sx?)?['"]"""),
    ]

    results: list[str] = []
    for f in workspace.rglob("*"):
        if f.suffix.lower() not in _JS_EXT:
            continue
        if any(part in _IGNORE_DIRS for part in f.parts):
            continue
        if f.resolve() == edited.resolve():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
            if any(p.search(text) for p in patterns):
                try:
                    results.append(str(f.relative_to(workspace)))
                except ValueError:
                    results.append(str(f))
        except OSError:
            continue
        if len(results) >= 20:
            break
    return results


async def _do_lsp_lookup(
    workspace: Path,
    file_path: str,
    line: int,
    col: int,
) -> list[str]:
    """Interroge le LSP — peut lever des exceptions."""
    from src.tools.lsp_client import get_lsp_client

    client = get_lsp_client(workspace)
    if client is None:
        return []

    refs = await client.get_references(file_path, line, col, include_declaration=False)

    seen: set[str] = set()
    results: list[str] = []
    for ref in refs:
        ref_path = getattr(ref, "file_path", None) or getattr(ref, "path", None)
        if not ref_path:
            continue
        try:
            rel = str(Path(ref_path).relative_to(workspace))
        except ValueError:
            rel = ref_path
        if rel not in seen and rel != file_path:
            seen.add(rel)
            results.append(rel)

    return results[:20]


def format_related_files_note(related: Sequence[str], edited_file: str) -> str:
    """Formate une note "fichiers dépendants" pour injection dans l'observation."""
    if not related:
        return ""
    lines = [
        f"\n💡 Fichiers qui utilisent `{edited_file}` (vérifie leur compatibilité) :"
    ]
    for f in related:
        lines.append(f"  - {f}")
    return "\n".join(lines)


__all__ = ["get_related_files", "format_related_files_note"]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# ──────────────────────────────────────────────────────────────────────────────
