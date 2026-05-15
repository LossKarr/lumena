"""
🚀 LUMENA - Background Module

Gestion des tâches en arrière-plan.
"""

from .manager import (
    BackgroundTaskManager,
    BackgroundTask,
    TaskStatus,
    get_task_manager
)

__all__ = [
    'BackgroundTaskManager',
    'BackgroundTask',
    'TaskStatus',
    'get_task_manager'
]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
