"""
config_manager.py - Handlers V2 pour lire/modifier la configuration Lumena.

Permet à Lumena de consulter et modifier ses propres paramètres
(modèle par défaut, max itérations, timeouts, etc.) via le chat.
Réutilise le même schéma et la même logique d'écriture .env que
web/routes/config.py pour garantir la cohérence.

Sécurité: les clés API (type "secret") sont exclues des modifications
via chat — elles restent éditables uniquement via la page web.
"""
from __future__ import annotations

import os
from typing import Any, List

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Lazy import de la logique config (même source que la web UI) ──────────

def _get_config_internals():
    """Import paresseux pour éviter les dépendances circulaires au chargement."""
    from web.routes.config import (
        _CONFIG_SCHEMA,
        _read_env_file,
        _write_env_values,
    )
    return _CONFIG_SCHEMA, _read_env_file, _write_env_values


# Clés que Lumena ne doit PAS pouvoir modifier via le chat
_BLOCKED_TYPES = {"secret"}

# P6 — niveaux de visibilité
_LEVEL_INCLUDE = {
    "simple": {"simple"},
    "avancé": {"simple", "avancé"},
    "avance": {"simple", "avancé"},
    "expert": {"simple", "avancé", "expert"},
    "tout": {"simple", "avancé", "expert"},
    "all": {"simple", "avancé", "expert"},
}


def _fuzzy_match(query: str, schema: list[dict]) -> list[dict]:
    """Fuzzy match sur label, key et hint."""
    q = query.lower()
    results = []
    for entry in schema:
        haystack = f"{entry.get('label', '')} {entry['key']} {entry.get('hint', '')}".lower()
        if q in haystack:
            results.append(entry)
    return results


# ─── Handlers ──────────────────────────────────────────────────────────────

async def get_config_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Retourne la configuration actuelle de Lumena (secrets masqués).

    P6.1: sans filtre → simple uniquement.
    group=avancé → simple+avancé. group=tout → tout.
    """
    try:
        schema, read_env, _ = _get_config_internals()
        env_vals = read_env()

        group_filter = kwargs.get("group", "").strip().lower()

        # P6.1 — déterminer les niveaux visibles
        allowed_levels = _LEVEL_INCLUDE.get(group_filter, None)
        is_level_filter = allowed_levels is not None
        if not group_filter:
            allowed_levels = {"simple"}  # défaut = simple uniquement
            is_level_filter = True

        groups: dict[str, list[dict]] = {}
        for entry in schema:
            g = entry["group"]
            level = entry.get("level", "avancé")
            # Si c'est un filtre par niveau, appliquer
            if is_level_filter and level not in allowed_levels:
                continue
            # Si c'est un filtre par nom de groupe
            if not is_level_filter and group_filter not in g.lower():
                continue
            raw = env_vals.get(entry["key"], entry["default"])
            display = "***" if entry["type"] == "secret" and raw else raw
            groups.setdefault(g, []).append(
                f"  {entry['label']} ({entry['key']}) = {display}"
            )

        if not groups:
            return HandlerResult.ok(
                "Aucun paramètre trouvé pour ce filtre.",
                handler_name="get_lumena_config",
            )

        lines = []
        for g, items in groups.items():
            lines.append(f"\n[{g}]")
            lines.extend(items)

        suffix = ""
        if is_level_filter and group_filter in ("", "simple"):
            suffix = "\n\n(Affichage simple. Pour voir plus: group=avancé ou group=tout)"

        return HandlerResult.ok(
            "Configuration actuelle:\n" + "\n".join(lines) + suffix,
            handler_name="get_lumena_config",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"Erreur lecture config: {e}", handler_name="get_lumena_config"
        )


async def update_config_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Modifie un paramètre de configuration Lumena."""
    try:
        schema, read_env, write_env = _get_config_internals()

        key = kwargs.get("key", "").strip()
        value = str(kwargs.get("value", "")).strip()

        if not key or value == "":
            return HandlerResult.fail(
                "Paramètres 'key' et 'value' requis.",
                handler_name="update_lumena_config",
            )

        # Lookup dans le schéma
        entry = next((s for s in schema if s["key"] == key), None)
        if entry is None:
            # Essayer par label (l'utilisateur dit "modèle par défaut" pas "LUMENA_DEFAULT_MODEL")
            key_lower = key.lower()
            entry = next(
                (s for s in schema if key_lower in s["label"].lower() or key_lower in s["key"].lower()),
                None,
            )
            if entry is None:
                valid = ", ".join(s["key"] for s in schema if s["type"] not in _BLOCKED_TYPES)
                return HandlerResult.fail(
                    f"Clé '{key}' inconnue. Clés valides: {valid}",
                    handler_name="update_lumena_config",
                )
            key = entry["key"]

        # Bloquer les secrets
        if entry["type"] in _BLOCKED_TYPES:
            return HandlerResult.fail(
                f"Modification de '{key}' interdite via le chat (clé API/secret). "
                f"Utilise la page Configuration web pour modifier les secrets.",
                handler_name="update_lumena_config",
            )

        # Validation selon le type
        if entry["type"] == "number":
            try:
                _ = int(value) if "." not in value else float(value)
            except ValueError:
                return HandlerResult.fail(
                    f"'{value}' n'est pas un nombre valide pour {entry['label']}.",
                    handler_name="update_lumena_config",
                )

        if entry["type"] == "bool":
            low = value.lower()
            if low in ("true", "1", "oui", "yes", "on", "activé"):
                value = "1"
            elif low in ("false", "0", "non", "no", "off", "désactivé"):
                value = "0"
            else:
                return HandlerResult.fail(
                    f"Valeur booléenne invalide: '{value}'. Utilise oui/non, 1/0, true/false.",
                    handler_name="update_lumena_config",
                )

        if entry["type"] == "select" and "options" in entry:
            if value not in entry["options"]:
                return HandlerResult.fail(
                    f"'{value}' n'est pas une option valide pour {entry['label']}. "
                    f"Options: {', '.join(entry['options'])}",
                    handler_name="update_lumena_config",
                )

        # P6.3 — Guards selon le niveau
        level = entry.get("level", "avancé")
        needs_restart = entry.get("restart", False)

        # Lire ancienne valeur pour le log
        env_vals = read_env()
        old_value = env_vals.get(key, entry["default"])

        # Écrire dans .env + os.environ
        write_env({key: value})
        os.environ[key] = value

        logger.info(f"[config_manager] {key}: '{old_value}' → '{value}'")

        # Construire le message avec guards
        parts = [f"Configuration mise à jour: {entry['label']} ({key}) = {value} (ancienne valeur: {old_value})"]
        if needs_restart:
            parts.append("Un redémarrage est nécessaire pour appliquer ce changement.")
        if level == "avancé":
            parts.append("(paramètre avancé)")
        elif level == "expert":
            parts.append("(paramètre expert — attention aux effets de bord)")

        return HandlerResult.ok(
            " ".join(parts),
            handler_name="update_lumena_config",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"Erreur modification config: {e}",
            handler_name="update_lumena_config",
        )


async def explain_config_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """P6.2 — Explique un paramètre de configuration (fuzzy match label+hint)."""
    try:
        schema, read_env, _ = _get_config_internals()
        query = kwargs.get("query", "").strip()
        if not query:
            return HandlerResult.fail(
                "Paramètre 'query' requis (ex: 'sandbox', 'modèle', 'timeout').",
                handler_name="explain_lumena_config",
            )
        matches = _fuzzy_match(query, schema)
        if not matches:
            return HandlerResult.ok(
                f"Aucun paramètre trouvé pour '{query}'.",
                handler_name="explain_lumena_config",
            )
        env_vals = read_env()
        lines = []
        for entry in matches[:10]:
            val = env_vals.get(entry["key"], entry["default"])
            if entry["type"] == "secret" and val:
                val = "***"
            hint = entry.get("hint", "")
            level = entry.get("level", "avancé")
            lines.append(
                f"\n• {entry['label']} ({entry['key']})\n"
                f"  Groupe: {entry['group']} | Niveau: {level} | Type: {entry['type']}\n"
                f"  Valeur actuelle: {val} (défaut: {entry['default']})\n"
                f"  {hint}"
            )
        header = f"{len(matches)} paramètre(s) trouvé(s) pour '{query}':"
        suggestion = ""
        modifiable = [e for e in matches if e["type"] not in _BLOCKED_TYPES]
        if modifiable:
            ex = modifiable[0]
            suggestion = f'\n\nPour modifier: update_lumena_config(key="{ex["key"]}", value="...")'
        elif matches:
            suggestion = "\n\nCes paramètres sont des secrets — modifiables uniquement via la page Configuration web."
        return HandlerResult.ok(
            header + "".join(lines) + suggestion,
            handler_name="explain_lumena_config",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"Erreur: {e}", handler_name="explain_lumena_config"
        )


# ─── Registre ──────────────────────────────────────────────────────────────

def get_config_manager_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions de handlers config_manager pour le registre V2."""
    return [
        HandlerDef(
            name="get_lumena_config",
            description=(
                "Affiche la configuration actuelle de Lumena. "
                "Mots-clés: 'quelle est ta config', 'montre tes paramètres', "
                "'quel modèle tu utilises', 'affiche la configuration'. "
                "Paramètre optionnel 'group' pour filtrer (ex: 'LLM', 'Voix', 'Autonomie')."
            ),
            parameters={
                "properties": {
                    "group": {
                        "type": "string",
                        "description": "Filtrer par niveau (simple, avancé, expert, tout) ou par nom de groupe (LLM, Voix, Autonomie…). Vide = simple uniquement.",
                        "default": "",
                    },
                },
                "required": [],
            },
            handler=get_config_handler,
            category="system",
            source_module="handlers.config_manager",
        ),
        HandlerDef(
            name="explain_lumena_config",
            description=(
                "Explique un paramètre de configuration de Lumena. "
                "Mots-clés: 'comment configurer X', 'c'est quoi le sandbox', "
                "'explique le timeout', 'aide config'. "
                "Cherche par mot-clé dans les noms, labels et descriptions."
            ),
            parameters={
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Mot-clé ou sujet à chercher (ex: 'sandbox', 'modèle', 'timeout', 'alertes')",
                    },
                },
                "required": ["query"],
            },
            handler=explain_config_handler,
            category="system",
            source_module="handlers.config_manager",
        ),
        HandlerDef(
            name="update_lumena_config",
            description=(
                "Modifie un paramètre de configuration de Lumena. "
                "Mots-clés: 'change le modèle', 'mets les itérations à 50', "
                "'modifie le timeout', 'augmente le budget actions', "
                "'active le TTS', 'désactive les alertes SMS'. "
                "Interdit pour les clés API (secrets) — redirige vers la page web. "
                "La clé peut être le nom technique (LUMENA_MAX_REACT_ITERATIONS) "
                "ou un extrait du label (max itérations)."
            ),
            parameters={
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Nom du paramètre (ex: LUMENA_MAX_REACT_ITERATIONS) ou extrait du label (ex: 'max itérations')",
                    },
                    "value": {
                        "type": "string",
                        "description": "Nouvelle valeur (ex: '50', 'deepseek-v3', 'true')",
                    },
                },
                "required": ["key", "value"],
            },
            handler=update_config_handler,
            category="system",
            source_module="handlers.config_manager",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
