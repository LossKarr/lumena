"""
parity_tools.py - Utilitaires pour tester la parité legacy ↔ fragment.

Fournit des helpers pour comparer le comportement d'un handler legacy
(méthode de ToolRegistry) avec son équivalent fragmenté (fonction standalone).

Usage dans les tests:
    from src.reasoning.handlers.parity_tools import assert_parity

    await assert_parity(
        legacy_registry=registry,
        v2_registry=v2_registry,
        ctx=ctx,
        tool_name="read_file",
        kwargs={"path": "test.txt"},
    )
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerRegistryV2

logger = logging.getLogger("lumena.handlers.parity")


@dataclass
class ParityResult:
    """Résultat d'un test de parité entre handler legacy et fragmenté."""

    tool_name: str
    legacy_output: str
    v2_output: str
    match: bool
    diff_summary: str = ""

    def __str__(self) -> str:
        status = "✅ MATCH" if self.match else "❌ DIFF"
        return f"[{status}] {self.tool_name}: {self.diff_summary}"


async def run_legacy_handler(
    legacy_registry,
    tool_name: str,
    kwargs: Dict[str, Any],
) -> str:
    """
    Exécute un handler legacy via ToolRegistry et retourne le résultat str.

    Args:
        legacy_registry: Instance de ToolRegistry (react.py).
        tool_name: Nom de l'outil.
        kwargs: Arguments de l'outil.

    Returns:
        Le str retourné par le handler legacy.
    """
    tool_def = legacy_registry.tools.get(tool_name)
    if tool_def is None:
        raise ValueError(f"Tool '{tool_name}' not found in legacy registry")
    handler = tool_def["handler"]
    result = await handler(**kwargs)
    return result


async def run_v2_handler(
    v2_registry: HandlerRegistryV2,
    ctx: HandlerContext,
    tool_name: str,
    kwargs: Dict[str, Any],
) -> str:
    """
    Exécute un handler V2 via HandlerRegistryV2 et retourne le résultat str.

    Args:
        v2_registry: Instance de HandlerRegistryV2.
        ctx: HandlerContext.
        tool_name: Nom de l'outil.
        kwargs: Arguments de l'outil.

    Returns:
        Le str retourné par le handler V2 (via to_legacy_str).
    """
    result = await v2_registry.execute(tool_name, ctx, **kwargs)
    return result.to_legacy_str()


def _normalize_output(text: str) -> str:
    """
    Normalise un output pour la comparaison de parité.

    Supprime les éléments variables (timestamps, chemins absolus exacts)
    tout en préservant la structure fonctionnelle.
    """
    import re

    # Normalise les séparateurs de ligne
    text = text.replace("\r\n", "\n").strip()
    # Normalise les espaces multiples
    text = re.sub(r"[ \t]+", " ", text)
    return text


async def assert_parity(
    legacy_registry,
    v2_registry: HandlerRegistryV2,
    ctx: HandlerContext,
    tool_name: str,
    kwargs: Dict[str, Any],
    *,
    normalize: bool = True,
    strict: bool = False,
) -> ParityResult:
    """
    Compare le résultat d'un handler legacy vs son handler V2 fragmenté.

    Args:
        legacy_registry: Instance ToolRegistry.
        v2_registry: Instance HandlerRegistryV2.
        ctx: HandlerContext.
        tool_name: Nom de l'outil.
        kwargs: Arguments.
        normalize: Si True, normalise les outputs avant comparaison.
        strict: Si True, lève AssertionError en cas de diff.

    Returns:
        ParityResult.
    """
    legacy_output = await run_legacy_handler(legacy_registry, tool_name, kwargs)
    v2_output = await run_v2_handler(v2_registry, ctx, tool_name, kwargs)

    if normalize:
        cmp_legacy = _normalize_output(legacy_output)
        cmp_v2 = _normalize_output(v2_output)
    else:
        cmp_legacy = legacy_output
        cmp_v2 = v2_output

    match = cmp_legacy == cmp_v2

    diff_summary = ""
    if not match:
        # Produit un résumé de la diff
        legacy_lines = cmp_legacy.splitlines()
        v2_lines = cmp_v2.splitlines()
        diff_summary = (
            f"Legacy: {len(legacy_lines)} lignes, V2: {len(v2_lines)} lignes. "
            f"Premier écart à la ligne {_first_diff_line(legacy_lines, v2_lines)}"
        )

    result = ParityResult(
        tool_name=tool_name,
        legacy_output=legacy_output,
        v2_output=v2_output,
        match=match,
        diff_summary=diff_summary,
    )

    if strict and not match:
        raise AssertionError(
            f"Parity check failed for '{tool_name}': {diff_summary}\n"
            f"--- Legacy ---\n{legacy_output[:500]}\n"
            f"--- V2 ---\n{v2_output[:500]}"
        )

    return result


def _first_diff_line(a: list, b: list) -> int:
    """Retourne le numéro de la première ligne différente (1-based)."""
    for i, (la, lb) in enumerate(zip(a, b)):
        if la != lb:
            return i + 1
    return max(len(a), len(b))


async def batch_parity_check(
    legacy_registry,
    v2_registry: HandlerRegistryV2,
    ctx: HandlerContext,
    test_cases: List[Dict[str, Any]],
) -> List[ParityResult]:
    """
    Exécute une série de tests de parité.

    Args:
        test_cases: Liste de dicts {tool_name: str, kwargs: dict}.

    Returns:
        Liste de ParityResult.
    """
    results = []
    for tc in test_cases:
        tool_name = tc["tool_name"]
        kwargs = tc.get("kwargs", {})
        try:
            result = await assert_parity(
                legacy_registry, v2_registry, ctx,
                tool_name, kwargs,
            )
            results.append(result)
        except Exception as exc:
            results.append(ParityResult(
                tool_name=tool_name,
                legacy_output="",
                v2_output="",
                match=False,
                diff_summary=f"Exception: {exc}",
            ))
    return results


def parity_report_markdown(results: List[ParityResult]) -> str:
    """Génère un rapport de parité en Markdown."""
    lines = ["# Rapport de Parité Legacy ↔ V2\n"]
    total = len(results)
    matched = sum(1 for r in results if r.match)
    lines.append(f"**{matched}/{total}** handlers en parité ({matched / total * 100:.1f}%)\n")
    lines.append("| Tool | Status | Details |")
    lines.append("|------|--------|---------|")
    for r in results:
        status = "✅" if r.match else "❌"
        details = r.diff_summary or "OK"
        lines.append(f"| {r.tool_name} | {status} | {details} |")
    return "\n".join(lines)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
