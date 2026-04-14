"""
🛠️ LUMENA - Module Tools

Système d'outils automatique inspiré de Moltbot.
Les outils sont toujours disponibles et le LLM décide quand les utiliser.
"""

from .tool_system import (
    LumenaToolSystem,
    get_tool_system,
    ToolCall,
    ToolResult
)

# Smart Patching (Phase 1 - inspiré Moltbot)
try:
    from .apply_patch import apply_patch, edit_file, parse_patch, PatchResult
except ImportError:
    apply_patch = None
    edit_file = None

# Context Compaction (Phase 2 - inspiré Moltbot)
try:
    from .compaction import (
        ContextCompactor, 
        get_token_stats, 
        format_token_stats, 
        estimate_tokens
    )
except ImportError:
    ContextCompactor = None
    estimate_tokens = None

__all__ = [
    "LumenaToolSystem",
    "get_tool_system",
    "ToolCall",
    "ToolResult",
    # New tools
    "apply_patch",
    "edit_file",
    "ContextCompactor",
    "estimate_tokens",
]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
