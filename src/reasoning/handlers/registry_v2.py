"""
registry_v2.py - Registre V2 des handlers fragmentés.

Remplacera à terme le dict self.tools de ToolRegistry (react.py).
Stocke les définitions de handlers avec leur schéma de paramètres
et les exécute via un HandlerContext unifié.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .context import HandlerContext
from .contracts import HandlerResult, HandlerTimer

logger = logging.getLogger("lumena.handlers.registry_v2")

# Type d'un handler fragmenté: async (ctx, **kwargs) -> HandlerResult
HandlerFunc = Callable[..., Awaitable[HandlerResult]]


@dataclass
class HandlerDef:
    """
    Définition complète d'un handler fragmenté.

    Attributes:
        name: Nom de l'outil (ex: "read_file"). Doit correspondre au nom legacy.
        description: Description pour le prompt LLM.
        parameters: Schéma JSON des paramètres (même format que ToolRegistry.register).
        handler: Fonction async (ctx: HandlerContext, **kwargs) -> HandlerResult.
        category: Catégorie fonctionnelle (files, system, web, memory, etc.).
        source_module: Module d'origine (ex: "handlers.files").
    """

    name: str
    description: str
    parameters: Dict[str, Any]
    handler: HandlerFunc
    category: str = ""
    source_module: str = ""


class HandlerRegistryV2:
    """
    Registre V2 des handlers fragmentés.

    Fonctionnalités:
    - Enregistrement par catégorie
    - Exécution avec mesure de durée et wrapping HandlerResult
    - Export au format legacy (pour compatibilité with ToolRegistry.tools)
    - Validation de parité (tous les handlers legacy sont-ils couverts?)
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, HandlerDef] = {}
        self._categories: Dict[str, List[str]] = {}  # category -> [tool_names]

    # ─── Enregistrement ────────────────────────────────────────────────────

    def register(self, handler_def: HandlerDef) -> None:
        """Enregistre un handler. Lève ValueError si le nom existe déjà."""
        if handler_def.name in self._handlers:
            raise ValueError(
                f"Handler '{handler_def.name}' déjà enregistré "
                f"(source: {self._handlers[handler_def.name].source_module})"
            )
        self._handlers[handler_def.name] = handler_def
        cat = handler_def.category or "uncategorized"
        self._categories.setdefault(cat, []).append(handler_def.name)
        logger.debug("Registered handler: %s (category=%s)", handler_def.name, cat)

    def register_many(self, defs: List[HandlerDef]) -> None:
        """Enregistre plusieurs handlers d'un coup."""
        for d in defs:
            self.register(d)

    # ─── Consultation ──────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[HandlerDef]:
        """Retourne la définition d'un handler, ou None."""
        return self._handlers.get(name)

    def has(self, name: str) -> bool:
        """True si le handler est enregistré."""
        return name in self._handlers

    @property
    def tool_names(self) -> List[str]:
        """Liste de tous les noms d'outils enregistrés."""
        return list(self._handlers.keys())

    @property
    def count(self) -> int:
        return len(self._handlers)

    def by_category(self, category: str) -> List[HandlerDef]:
        """Retourne tous les handlers d'une catégorie."""
        names = self._categories.get(category, [])
        return [self._handlers[n] for n in names if n in self._handlers]

    @property
    def categories(self) -> List[str]:
        """Liste des catégories."""
        return list(self._categories.keys())

    # ─── Exécution ─────────────────────────────────────────────────────────

    async def execute(
        self,
        name: str,
        ctx: HandlerContext,
        **kwargs,
    ) -> HandlerResult:
        """
        Exécute un handler par nom avec un HandlerContext.

        Mesure la durée, attrape les exceptions, retourne toujours un HandlerResult.
        """
        hdef = self._handlers.get(name)
        if hdef is None:
            return HandlerResult.fail(
                f"Outil inconnu: {name}",
                handler_name=name,
            )

        with HandlerTimer() as timer:
            try:
                result = await hdef.handler(ctx, **kwargs)
            except Exception as exc:
                logger.exception("Handler '%s' raised: %s", name, exc)
                return HandlerResult(
                    success=False,
                    output=f"❌ Erreur interne {name}: {exc}",
                    error=str(exc),
                    duration_ms=0.0,
                    handler_name=name,
                )

        # Enrichit le résultat avec la durée et le nom
        return HandlerResult(
            success=result.success,
            output=result.output,
            error=result.error,
            duration_ms=timer.elapsed_ms,
            handler_name=name,
        )

    # ─── Export legacy ─────────────────────────────────────────────────────

    def to_legacy_tools_dict(self, ctx: HandlerContext) -> Dict[str, Dict[str, Any]]:
        """
        Convertit le registre V2 au format legacy ToolRegistry.tools.

        Chaque entrée: {name: {name, description, parameters, handler}}
        Le handler est wrappé pour capturer le ctx et retourner un str.

        Ceci sera utilisé en Phase 7 pour brancher les handlers fragmentés
        dans ReActLoop sans changer son interface.
        """
        legacy: Dict[str, Dict[str, Any]] = {}
        for name, hdef in self._handlers.items():
            # Crée un wrapper qui convertit HandlerResult -> str
            async def _legacy_wrapper(_hdef=hdef, _ctx=ctx, **kw) -> str:
                result = await _hdef.handler(_ctx, **kw)
                return result.to_legacy_str()

            # Convertir les paramètres V2 (JSON Schema) au format legacy (dict plat)
            # V2: {"properties": {"path": {...}}, "required": [...]}
            # Legacy: {"path": {...}} + "required": [...]
            params = hdef.parameters
            required = []
            if "properties" in params:
                required = params.get("required", [])
                params = params["properties"]

            legacy[name] = {
                "name": name,
                "description": hdef.description,
                "parameters": params,
                "required": required,
                "handler": _legacy_wrapper,
            }
        return legacy

    # ─── Parité ────────────────────────────────────────────────────────────

    def get_parity_report(self, legacy_tool_names: List[str]) -> Dict[str, Any]:
        """
        Compare les handlers V2 avec la liste legacy.

        Returns:
            Dict avec:
            - covered: liste des tools couverts par V2
            - missing: liste des tools legacy non encore migrés
            - extra: liste des tools V2 qui n'existent pas en legacy
            - coverage_pct: pourcentage de couverture
        """
        legacy_set = set(legacy_tool_names)
        v2_set = set(self._handlers.keys())

        covered = sorted(legacy_set & v2_set)
        missing = sorted(legacy_set - v2_set)
        extra = sorted(v2_set - legacy_set)
        total = len(legacy_set)
        coverage_pct = (len(covered) / total * 100) if total > 0 else 0.0

        return {
            "covered": covered,
            "missing": missing,
            "extra": extra,
            "coverage_pct": round(coverage_pct, 1),
            "total_legacy": total,
            "total_v2": len(v2_set),
        }

    # ─── Description pour le prompt ────────────────────────────────────────

    def get_tools_description(self) -> str:
        """
        Retourne une description compacte des outils (1 ligne chacun).
        Même format que ToolRegistry.get_tools_description().
        """
        descriptions = []
        for name, hdef in self._handlers.items():
            if "properties" in hdef.parameters:
                props = hdef.parameters["properties"]
                required = set(hdef.parameters.get("required", []))
            else:
                props = hdef.parameters
                required = set()
            if not props:
                descriptions.append(f"- {name}(): {hdef.description}")
            else:
                param_list = ", ".join(
                    f"{p}" if p in required else f"{p}?"
                    for p in props
                )
                descriptions.append(f"- {name}({param_list}): {hdef.description}")
        return "\n".join(descriptions)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
