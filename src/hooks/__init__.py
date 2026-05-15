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
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
