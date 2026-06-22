"""Brique 2 (A3 reformulé) — niveaux de capacité par pair.

Quand une autre Lumena délègue une tâche, B ne doit pas systématiquement
exécuter avec **tous** ses outils. Le **niveau** accordé au pair décide du
jeu d'outils mis entre les mains de l'agent qui traite la demande.

Deux niveaux :

- **`chat`** (défaut, sûr) : collaboration légère — lire sa mémoire/son journal,
  raisonner, interroger le savoir d'un pair, chercher de l'info. **Liste blanche
  explicite, zéro outil d'action** (pas d'écriture fichier, pas de commande, pas
  de `computer_use`, pas de `delegate_task`).
- **`mission`** (agent complet) : B agit **comme si l'utilisateur lui parlait**,
  **pleine puissance — accès à TOUS ses outils** (y compris `delegate_task` →
  CodeAgent : si la mission demande de coder, B doit pouvoir coder). **Accordé
  explicitement** par l'humain, par pair (Lumena de sa flotte).

Principes (validés) :
  - Le niveau est ce que **B accorde** au pair (registre), jamais ce que le pair
    **demande**.
  - Défaut = `chat`. Tout niveau inconnu → `chat` (**fail-closed**).

Module **pur** (aucune I/O) → entièrement testable.
"""
from __future__ import annotations

from typing import Iterable, Optional, Set

LEVELS: frozenset[str] = frozenset({"chat", "mission"})
DEFAULT_LEVEL: str = "chat"

# Liste blanche du niveau `chat` : uniquement de la LECTURE / réflexion / savoir.
# Noms réels (cf. ToolRegistry._CACHEABLE_TOOLS + handlers). Volontairement SANS
# read_file/list_directory (accès disque → chemins sensibles) ni aucun outil
# d'action. Erreur du côté « trop restreint » = sûr.
CHAT_ALLOWED_TOOLS: frozenset[str] = frozenset({
    "memory_search", "memory_stats", "memory_get",
    "read_journal", "list_journal_dates", "search_journal",
    "get_my_capabilities", "get_agents_status",
    "query_peer_knowledge",
    "web_search",
    "get_time",
})

def normalize_level(level: Optional[str]) -> str:
    """Niveau normalisé, fail-closed sur `chat` si inconnu/absent."""
    lvl = (level or "").strip().lower()
    return lvl if lvl in LEVELS else DEFAULT_LEVEL


def is_mission_level(level: Optional[str]) -> bool:
    return normalize_level(level) == "mission"


def resolve_allowed_tools(
    level: Optional[str],
    all_tool_names: Optional[Iterable[str]] = None,
) -> Optional[Set[str]]:
    """Jeu d'outils autorisé pour un niveau donné.

    - `mission` → **None = aucune restriction** : l'agent a accès à **TOUS** ses
      outils (CodeAgent inclus). B agit comme si l'utilisateur lui parlait.
    - `chat` → **liste blanche** explicite (intersectée avec les outils réels si
      `all_tool_names` est fourni, pour ne pas référencer un outil inexistant).

    Le retour est destiné à `think_and_act_silent(allowed_tools=…)`
    (où `None` signifie « tous les outils »).
    """
    lvl = normalize_level(level)

    if lvl == "mission":
        return None  # pleine puissance : tous les outils

    # chat
    if all_tool_names is None:
        return set(CHAT_ALLOWED_TOOLS)
    return set(CHAT_ALLOWED_TOOLS) & set(all_tool_names)


def describe_level(level: Optional[str]) -> str:
    """Libellé court pour l'UI / l'audit."""
    return "Mission (agent complet)" if is_mission_level(level) else "Chat (lecture & savoir)"
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
