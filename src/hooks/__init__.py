"""
🌟 LUMENA - Module Hooks

Système de hooks event-driven pour automatisation.
"""

from .hook_system import (
    HookSystem,
    HookEvent,
    HookContext,
    Hook,
    get_hook_system,
    register_default_hooks,
)

__all__ = [
    "HookSystem",
    "HookEvent",
    "HookContext",
    "Hook",
    "get_hook_system",
    "register_default_hooks",
]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
