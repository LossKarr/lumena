"""Agents Lumena.

Les exports historiques restent disponibles, mais `sub_agent.py` est importe
seulement quand un export est demande. Cela garde les tests des petits modules
CodeAgent rapides et sans initialisation globale inutile.
"""

__all__ = [
    "SubAgent",
    "SubAgentOrchestrator",
    "CodeAgent",
    "ResearchAgent",
    "FileAgent",
    "BrowserAgent",
    "DebugAgent",
    "RefactorAgent",
    "AgentType",
    "AgentStatus",
    "get_orchestrator",
    "delegate_to_agent"
]


def __getattr__(name: str):
    if name in __all__:
        from importlib import import_module
        sub_agent = import_module(f"{__name__}.sub_agent")
        return getattr(sub_agent, name)
    raise AttributeError(name)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
