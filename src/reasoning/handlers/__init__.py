# src/reasoning/handlers/__init__.py
"""
Handlers fragmentes depuis react.py.

Ce package contient les handlers extraits du God File react.py,
organises par domaine fonctionnel. Chaque module expose des fonctions
async standalone qui prennent un HandlerContext en premier argument.

Architecture:
    contracts.py   - HandlerResult dataclass (format retour unifie)
    context.py     - HandlerContext dataclass (tout ce dont un handler a besoin)
    registry_v2.py - HandlerRegistryV2 (registre des handlers fragmentes)
    parity_tools.py - Utilitaires de test de parite legacy vs fragment
"""

from .contracts import HandlerResult
from .context import HandlerContext
from .registry_v2 import HandlerRegistryV2, HandlerDef

__all__ = [
    "HandlerResult",
    "HandlerContext",
    "HandlerRegistryV2",
    "HandlerDef",
]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
