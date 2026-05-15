"""
src/core_services — Services fragmentés depuis LumenaCore.

Chaque service encapsule un domaine fonctionnel de core.py,
injecté via ServiceContext pour accéder aux ressources partagées.
"""

from .contracts import ServiceContext
from .base_service import BaseService

__all__ = [
    "ServiceContext",
    "BaseService",
]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
