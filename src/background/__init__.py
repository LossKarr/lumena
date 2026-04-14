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
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
