"""
Classe de base pour tous les services fragmentés.
"""

from abc import ABC
from loguru import logger

from .contracts import ServiceContext


class BaseService(ABC):
    """Classe de base — chaque service reçoit un ServiceContext à l'init."""

    def __init__(self, ctx: ServiceContext) -> None:
        self.ctx = ctx

    @property
    def data_dir(self):
        return self.ctx.data_dir

    @property
    def llm(self):
        return self.ctx.llm

    @property
    def memory(self):
        return self.ctx.memory
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
