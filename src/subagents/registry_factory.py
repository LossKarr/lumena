"""Lot 0.b — Factory de `ToolRegistry` PAR MISSION (isolation du chat).

Diag (vérifié) : le chat (`agent_service.py:1954`) et la mission (`:2225`) réutilisent
le MÊME `core._tool_registry` partagé et le mutent → course mission ⇄ chat. La parade :
donner à chaque mission **son propre** registre.

Garanties (sans effet de bord) :
- outils **natifs** présents d'office (`ToolRegistry.__init__` → `_load_v2_handlers`) ;
- outils MCP de **contrôle** attachés en lecture (`attach_to_tool_registry`, idempotent) ;
- serveurs MCP **ACTIFS** : **DÉFÉRÉS en Phase 0** (on ne copie pas `_dynamic_handlers` :
  ça ne remplit pas `reg.tools` et figerait un snapshot ignorant une désactivation →
  violerait « gate le futur ». Expo des MCP actifs = feature ultérieure, avec check de
  liveness à l'exécution) ;
- **jamais** `bind_tool_registry`, **jamais** écrire `core._tool_registry`/`core.tool_system`
  → le chat conserve ses outils et permissions **intacts**.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from src.reasoning.tool_registry import ToolRegistry


def create_mission_registry(core: Any) -> ToolRegistry:
    """Crée un `ToolRegistry` dédié à une mission, isolé de celui du chat.

    Args:
        core: l'instance `LumenaCore` (passée à `ToolRegistry(lumena=core)`).

    Returns:
        Un `ToolRegistry` neuf (natifs + MCP de contrôle), **distinct** du registre du chat.
    """
    reg = ToolRegistry(lumena=core)

    # Outils MCP de CONTRÔLE (capability/ticket/run_autonomy/resume) — best-effort, en lecture.
    mcp = getattr(core, "mcp_react_integration", None)
    if mcp is not None and hasattr(mcp, "attach_to_tool_registry"):
        try:
            mcp.attach_to_tool_registry(reg)
        except Exception as exc:  # ne jamais casser la création d'une mission
            logger.debug("[mission-registry] attach MCP control skip: {}", exc)

    # Lot 5.6 — read-through vers les MCP serveurs ACTIFS du registre boot : la mission
    # VOIT (5.6.1) et pourra UTILISER (5.6.2) les MCP actifs (météo, memory…), sans copier
    # les handlers (isolation préservée, liveness vérifiée à l'exécution). Best-effort.
    boot_registry = getattr(core, "_tool_registry", None)
    if boot_registry is not None and boot_registry is not reg:
        try:
            reg.attach_mcp_readthrough(
                boot_registry,
                catalog=getattr(mcp, "catalog", None),
                watcher=getattr(mcp, "runtime_watcher", None),
            )
        except Exception as exc:  # ne jamais casser la création d'une mission
            logger.debug("[mission-registry] attach MCP read-through skip: {}", exc)

    return reg
