"""
custom.py — Outils créés dynamiquement par Lumena en autonomie.

Flux d'utilisation :
  1. Lumena bloque → aucun outil ne résout son problème
  2. custom_tool_search → elle fouille data/custom_handlers/
  3a. Un outil existant correspond → custom_tool_load → l'utilise
  3b. Rien d'utile → custom_tool_create → écrit, vérifie, charge, utilise

Les handlers custom sont chargés à la demande (pas au démarrage).
Ils sont injectés dynamiquement dans le ToolRegistry de la session courante.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef

# ─── Constantes ────────────────────────────────────────────────────────────

from src.utils.paths import CUSTOM_HANDLERS_DIR

CUSTOM_HANDLERS_DIR_PATH = CUSTOM_HANDLERS_DIR

# Patterns interdits dans le code custom (sécurité)
_FORBIDDEN_PATTERNS = [
    r"\bos\.system\b",
    r"\bsubprocess\b",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"\b__import__\s*\(",
    r"\bshutil\.rmtree\b",
    r"\bshutil\.move\b",
    r"\bos\.remove\b",
    r"\bos\.unlink\b",
    r"\bctypes\b",
    r"\bsocket\b",
    r"open\s*\([^)]+['\"][wa]['\"]",  # open(..., 'w') ou open(..., 'a')
]

# Nom d'outil valide : snake_case, 3-50 chars
_VALID_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,49}$")


# ─── Helpers ───────────────────────────────────────────────────────────────

def _custom_dir() -> Path:
    CUSTOM_HANDLERS_DIR.mkdir(parents=True, exist_ok=True)
    return CUSTOM_HANDLERS_DIR


def _is_safe_code(code: str) -> tuple[bool, str]:
    """Vérifie syntaxe + absence de patterns dangereux."""
    for pattern in _FORBIDDEN_PATTERNS:
        if re.search(pattern, code):
            return False, f"Pattern interdit détecté: `{pattern}`"
    try:
        ast.parse(code)
    except SyntaxError as e:
        return False, f"Erreur de syntaxe Python: {e}"
    return True, ""


def _read_meta(py_file: Path) -> Optional[Dict[str, Any]]:
    """Lit le header JSON d'un fichier handler custom (commentaire en tête)."""
    try:
        lines = py_file.read_text(encoding="utf-8").splitlines()
        # Le header est dans les premières lignes, entre # META: { ... }
        for line in lines[:10]:
            line = line.strip()
            if line.startswith("# META:"):
                raw = line[len("# META:"):].strip()
                return json.loads(raw)
    except Exception as e:
        logger.debug(f"Parse META custom handler: {e}")
    return None


def _write_custom_handler(
    tool_name: str,
    description: str,
    parameters_schema: Dict[str, Any],
    code: str,
) -> Path:
    """Sauvegarde un handler custom sur disque avec son header de métadonnées."""
    meta = {
        "name": tool_name,
        "description": description,
        "parameters": parameters_schema,
    }
    meta_line = f"# META: {json.dumps(meta, ensure_ascii=False)}"
    full_code = f"{meta_line}\n\n{code}\n"
    out_path = _custom_dir() / f"{tool_name}.py"
    out_path.write_text(full_code, encoding="utf-8")
    return out_path


def _load_handler_from_file(py_file: Path) -> Optional[Any]:
    """
    Charge dynamiquement une fonction `handler` depuis un fichier .py custom.
    Retourne la fonction, ou None si le chargement échoue.
    """
    module_name = f"lumena_custom_{py_file.stem}"
    try:
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)  # type: ignore[union-attr]
        fn = getattr(module, "handler", None)
        if callable(fn):
            return fn
    except Exception as e:
        logger.warning(f"[custom] Échec chargement {py_file.name}: {e}")
    return None


# ─── Handlers des 3 outils ────────────────────────────────────────────────

async def custom_tool_search_handler(
    ctx: HandlerContext,
    query: str = "",
    **kwargs,
) -> HandlerResult:
    """Liste les outils custom disponibles dans data/custom_handlers/."""
    d = _custom_dir()
    files = sorted(d.glob("*.py"))
    if not files:
        return HandlerResult.ok(
            "📂 Aucun outil custom disponible pour l'instant.\n"
            "Utilise `custom_tool_create` pour en créer un.",
            handler_name="custom_tool_search",
        )

    results = []
    query_lower = query.strip().lower()
    for f in files:
        meta = _read_meta(f)
        if meta:
            name = meta.get("name", f.stem)
            desc = meta.get("description", "(pas de description)")
        else:
            name = f.stem
            desc = "(métadonnées manquantes)"

        # Filtre optionnel par mots-clés
        if query_lower and query_lower not in name.lower() and query_lower not in desc.lower():
            continue
        results.append(f"• **{name}** — {desc}")

    if not results:
        return HandlerResult.ok(
            f"📂 Aucun outil custom ne correspond à '{query}'.\n"
            "Utilise `custom_tool_create` pour en créer un.",
            handler_name="custom_tool_search",
        )

    listing = "\n".join(results)
    return HandlerResult.ok(
        f"📂 **Outils custom disponibles** ({len(results)}) :\n{listing}\n\n"
        "Pour charger un outil : `custom_tool_load` avec le nom de l'outil.",
        handler_name="custom_tool_search",
    )


async def custom_tool_load_handler(
    ctx: HandlerContext,
    name: str,
    **kwargs,
) -> HandlerResult:
    """
    Charge un outil custom depuis data/custom_handlers/ et l'injecte dans
    le ToolRegistry de la session courante pour utilisation immédiate.
    """
    name = name.strip()
    py_file = _custom_dir() / f"{name}.py"
    if not py_file.exists():
        return HandlerResult.fail(
            f"❌ Outil custom '{name}' introuvable dans data/custom_handlers/.\n"
            "Utilise `custom_tool_search` pour voir les outils disponibles.",
            handler_name="custom_tool_load",
        )

    meta = _read_meta(py_file)
    if not meta:
        return HandlerResult.fail(
            f"❌ Métadonnées manquantes dans {name}.py — fichier corrompu ?",
            handler_name="custom_tool_load",
        )

    fn = _load_handler_from_file(py_file)
    if fn is None:
        return HandlerResult.fail(
            f"❌ Impossible de charger la fonction `handler` depuis {name}.py",
            handler_name="custom_tool_load",
        )

    # Injecter dans le ToolRegistry de la session via ctx
    tool_registry = getattr(ctx, "_tool_registry_ref", None)
    if tool_registry is not None and hasattr(tool_registry, "tools"):
        description = meta.get("description", "Outil custom")
        params = meta.get("parameters", {})

        async def _wrapper(**kw):
            result = await fn(ctx, **kw)
            if isinstance(result, HandlerResult):
                return result.to_legacy_str()
            return str(result)

        tool_registry.tools[name] = {
            "description": description,
            "parameters": params,
            "handler": _wrapper,
        }
        logger.info(f"[custom] ✅ Outil '{name}' chargé et injecté dans la session")
        return HandlerResult.ok(
            f"✅ Outil **{name}** chargé avec succès !\n"
            f"Description : {description}\n"
            f"Tu peux maintenant l'utiliser avec `ACTION: {name}`",
            handler_name="custom_tool_load",
        )
    else:
        # Fallback : pas de référence au registry → informer seulement
        return HandlerResult.ok(
            f"⚠️ Outil '{name}' chargé mais pas pu être injecté dans la session.\n"
            f"Redémarre Lumena pour que l'outil soit disponible.",
            handler_name="custom_tool_load",
        )


async def custom_tool_create_handler(
    ctx: HandlerContext,
    tool_name: str,
    description: str,
    parameters_schema: Dict[str, Any],
    code: str,
    **kwargs,
) -> HandlerResult:
    """
    Crée un nouvel outil Python custom, le vérifie, le sauvegarde et le charge
    immédiatement dans la session courante.
    """
    # Validation du nom
    tool_name = tool_name.strip().lower().replace("-", "_")
    if not _VALID_NAME_RE.match(tool_name):
        return HandlerResult.fail(
            f"❌ Nom invalide: '{tool_name}'. Doit être snake_case, 3–50 chars (a-z, 0-9, _).",
            handler_name="custom_tool_create",
        )

    # Vérifier que le nom ne collisionne pas avec un outil existant
    tool_registry = getattr(ctx, "_tool_registry_ref", None)
    if tool_registry and hasattr(tool_registry, "tools"):
        if tool_name in tool_registry.tools:
            return HandlerResult.fail(
                f"❌ Un outil nommé '{tool_name}' existe déjà dans le registre principal.\n"
                "Choisis un nom différent.",
                handler_name="custom_tool_create",
            )

    # Vérification sécurité du code
    safe, reason = _is_safe_code(code)
    if not safe:
        return HandlerResult.fail(
            f"❌ Code refusé pour raison de sécurité : {reason}\n"
            "Retire les instructions dangereuses et réessaie.",
            handler_name="custom_tool_create",
        )

    # Vérifier que la fonction `handler` est bien définie dans le code
    try:
        tree = ast.parse(code)
        func_names = [
            node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
        ]
        if "handler" not in func_names:
            return HandlerResult.fail(
                "❌ Le code doit contenir une fonction `async def handler(ctx, **kwargs):`\n"
                f"Fonctions trouvées : {func_names or '(aucune)'}",
                handler_name="custom_tool_create",
            )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur d'analyse du code: {e}",
            handler_name="custom_tool_create",
        )

    # Sauvegarde sur disque
    try:
        py_file = _write_custom_handler(tool_name, description, parameters_schema, code)
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur d'écriture sur disque: {e}",
            handler_name="custom_tool_create",
        )

    # Chargement immédiat dans la session
    fn = _load_handler_from_file(py_file)
    injected = False
    if fn and tool_registry and hasattr(tool_registry, "tools"):
        async def _wrapper(**kw):
            result = await fn(ctx, **kw)
            if isinstance(result, HandlerResult):
                return result.to_legacy_str()
            return str(result)

        tool_registry.tools[tool_name] = {
            "description": description,
            "parameters": parameters_schema,
            "handler": _wrapper,
        }
        injected = True
        logger.info(f"[custom] ✅ Outil '{tool_name}' créé, sauvegardé et injecté")

    status = (
        "✅ Outil chargé et disponible immédiatement dans cette session."
        if injected
        else "⚠️ Outil sauvegardé mais pas injecté — redémarre pour l'activer."
    )

    return HandlerResult.ok(
        f"🔧 Outil **{tool_name}** créé avec succès !\n"
        f"📄 Fichier : `data/custom_handlers/{tool_name}.py`\n"
        f"📝 Description : {description}\n"
        f"{status}\n"
        f"Tu peux maintenant l'utiliser avec `ACTION: {tool_name}`",
        handler_name="custom_tool_create",
    )


# ─── Export des HandlerDef ─────────────────────────────────────────────────

def get_custom_tool_handler_defs() -> list[HandlerDef]:
    """Retourne les 3 HandlerDef à enregistrer dans le ToolRegistry."""
    return [
        HandlerDef(
            name="custom_tool_search",
            description=(
                "Fouille data/custom_handlers/ pour trouver un outil que tu as déjà créé. "
                "À utiliser quand aucun outil standard ne résout ton problème, "
                "AVANT d'en créer un nouveau."
            ),
            parameters={
                "query": {
                    "type": "string",
                    "description": "Mots-clés pour filtrer les outils (optionnel)",
                }
            },
            handler=custom_tool_search_handler,
            category="custom",
            source_module="handlers.custom",
        ),
        HandlerDef(
            name="custom_tool_load",
            description=(
                "Charge un outil custom depuis data/custom_handlers/ et l'injecte "
                "dans la session courante pour utilisation immédiate."
            ),
            parameters={
                "name": {
                    "type": "string",
                    "description": "Nom de l'outil custom à charger (snake_case)",
                }
            },
            handler=custom_tool_load_handler,
            category="custom",
            source_module="handlers.custom",
        ),
        HandlerDef(
            name="custom_tool_create",
            description=(
                "Crée un nouvel outil Python personnalisé quand aucun outil existant "
                "ne résout ton problème. L'outil est vérifié, sauvegardé et disponible "
                "immédiatement. Le code DOIT contenir `async def handler(ctx, **kwargs): ...`"
            ),
            parameters={
                "tool_name": {
                    "type": "string",
                    "description": "Nom de l'outil en snake_case (ex: parse_pdf_table)",
                },
                "description": {
                    "type": "string",
                    "description": "Description courte de ce que fait l'outil",
                },
                "parameters_schema": {
                    "type": "object",
                    "description": "Schéma JSON des paramètres acceptés par l'outil",
                },
                "code": {
                    "type": "string",
                    "description": (
                        "Code Python complet. Doit contenir: "
                        "`async def handler(ctx, **kwargs) -> str:` "
                        "Interdits: os.system, subprocess, eval, exec, open(..., 'w')"
                    ),
                },
            },
            handler=custom_tool_create_handler,
            category="custom",
            source_module="handlers.custom",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
