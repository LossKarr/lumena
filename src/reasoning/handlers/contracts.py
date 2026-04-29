"""
contracts.py - Format de retour unifie pour tous les handlers fragmentes.

Chaque handler fragmenté retourne un HandlerResult au lieu d'un str brut.
Cela permet le suivi, le logging, et la conversion vers le format legacy (str).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class SubToolResult:
    """Résultat structuré d'un sous-appel exécuté via parallel_tools."""

    tool_name: str
    success: bool
    content: str
    status_code: str = ""
    proof: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HandlerResult:
    """
    Resultat unifie d'un handler fragmenté.

    Attributes:
        success: True si l'operation a reussi, False sinon.
        output: Le contenu texte retourne (equivalent du return str legacy).
        error: Message d'erreur si success=False, None sinon.
        duration_ms: Duree d'execution en millisecondes (rempli automatiquement par le wrapper).
        handler_name: Nom du handler qui a produit ce resultat (pour le debug).
        sub_results: Résultats structurés des sous-outils (parallel_tools uniquement).
    """

    success: bool
    output: str
    error: Optional[str] = None
    duration_ms: float = 0.0
    handler_name: str = ""
    status_code: str = ""  # AgentResult.status_code propagé (success/partial/error…)
    sub_results: tuple = ()  # tuple[SubToolResult] — peuplé par parallel_tools_handler

    def to_legacy_str(self) -> str:
        """
        Convertit en string pour compatibilite avec le format legacy de react.py.

        Le format legacy retourne simplement un str. Cette methode garantit
        que le comportement est identique, que le handler soit legacy ou fragmente.
        """
        return self.output

    @staticmethod
    def ok(output: str, *, handler_name: str = "", status_code: str = "") -> HandlerResult:
        """Cree un resultat succes."""
        return HandlerResult(success=True, output=output, handler_name=handler_name, status_code=status_code)

    @staticmethod
    def fail(error: str, *, output: str = "", handler_name: str = "", status_code: str = "") -> HandlerResult:
        """Cree un resultat erreur. output contient le message d'erreur formate pour legacy."""
        return HandlerResult(
            success=False,
            output=output or error,
            error=error,
            handler_name=handler_name,
            status_code=status_code,
        )


class HandlerTimer:
    """Context manager pour mesurer la duree d'un handler."""

    def __init__(self) -> None:
        self._start: float = 0.0
        self.elapsed_ms: float = 0.0

    def __enter__(self) -> "HandlerTimer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args) -> None:
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
