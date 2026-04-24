"""
P5 — LSP pre-edit : injecter les fichiers dépendants avant une modification.

Activé uniquement si LUMENA_LSP_PRE_EDIT=1 (opt-IN).
Fail-open systématique : toute erreur LSP retourne une liste vide.

Utilisation :
    files = await get_related_files(workspace, "src/calculator.py", line=5)
    # → ["tests/test_calculator.py", "src/main.py"]
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional, Sequence

from loguru import logger


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

    Utilise le LSP find-references en fail-open.
    Retourne [] si le LSP n'est pas disponible ou si une erreur survient.
    """
    try:
        result = await asyncio.wait_for(
            _do_lsp_lookup(workspace, file_path, line, col),
            timeout=timeout,
        )
        return result
    except asyncio.TimeoutError:
        from src.utils.gate_metrics import record_lsp_fail_open
        record_lsp_fail_open(task_id=task_id, error=f"LSP pre-edit timeout {timeout}s")
        return []
    except Exception as exc:
        from src.utils.gate_metrics import record_lsp_fail_open
        record_lsp_fail_open(task_id=task_id, error=str(exc))
        logger.debug("[lsp_pre_edit] fail-open: {}", exc)
        return []


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

    # Dédupliquer et retourner les chemins relatifs
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

    return results[:20]  # cap à 20 fichiers


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
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# ──────────────────────────────────────────────────────────────────────────────
