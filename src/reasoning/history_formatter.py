"""History formatter — observation budget calibrated on real provider specs.

Centralise la logique "combien de chars je peux réinjecter dans l'historique
pour une itération ReAct / CodeAgent ?" afin d'exploiter au maximum la
fenêtre de contexte réelle de chaque modèle.

Paliers calibrés sur `src/llm/providers.py` (avril 2026) :

    <   16k ctx → 2 000 chars  # Ollama tiny (qwen3:1.7b, phi4-mini, lumena-v1…)
    <   64k ctx → 8 000 chars  # Ollama medium (qwen3:14b, gemma3:12b…)
    <  150k ctx → 24 000 chars # GPT-4o, DeepSeek V3, NVIDIA NIM 128k, Grok 4.1
    <  220k ctx → 32 000 chars # Claude 200k, o3, o4-mini, MiniMax 204k
    <  500k ctx → 32 000 chars # Kimi K2.5 (262k), Kimi Thinking NVIDIA
    < 1500k ctx → 40 000 chars # GPT-5.4/4.1, Claude 4.5+, Gemini (~1M)
    ≥ 1500k ctx → 48 000 chars # Grok 4.20 (2M)

Env vars (override global) :
    - LUMENA_REACT_OBS_LIMIT   : int (chars). Force une valeur fixe.
    - LUMENA_REACT_OBS_CLAMP   : "min:max" (ex "4000:64000"). Clamp le résultat.
    - LUMENA_REACT_PROTECT_LAST_READ : "1"/"0" (default "1"). Protège la
                                      dernière observation si elle provient
                                      d'un outil de lecture (read_file, grep…).

Protection "lecteur" : certains outils (read_file, grep_search, web_fetch…)
retournent un contenu factuel que le modèle DOIT lire en entier pour
raisonner. Pour ces outils, la MICROCOMPACTION de la *dernière* étape est
désactivée : on garde l'observation brute jusqu'au budget max du modèle.
"""
from __future__ import annotations

import os
from typing import Tuple

__all__ = [
    "compute_obs_limit",
    "compute_obs_limit_from_runtime",
    "should_protect_observation",
    "split_head_tail",
    "READER_TOOLS",
]


# ─── Outils "lecteurs" dont l'observation doit rester intacte ─────────
# Ces outils ne génèrent pas de dérive modèle → on ne gagne rien à les
# tronquer et on perd potentiellement des infos critiques (signatures de
# fonction, headers HTTP, résultats de recherche…).
READER_TOOLS: frozenset[str] = frozenset({
    # Filesystem
    "read_file", "read_file_lines", "view_file", "view_file_outline",
    "list_directory", "list_dir", "list_files", "find_files",
    # Search
    "grep_search", "search_in_code", "semantic_search", "code_search",
    # Web
    "web_fetch", "fetch_url", "browser_fetch", "fetch_webpage",
    # Mail / messaging readers
    "mail_inbox_get", "mail_read", "read_email",
    # Indexes
    "cached_files", "file_index_query",
})


def _parse_int_env(name: str, default: int = 0) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _parse_clamp_env(name: str) -> Tuple[int, int] | None:
    raw = os.getenv(name, "").strip()
    if not raw or ":" not in raw:
        return None
    try:
        lo, hi = raw.split(":", 1)
        lo_i = max(0, int(lo.strip()))
        hi_i = max(lo_i, int(hi.strip()))
        return (lo_i, hi_i)
    except ValueError:
        return None


def compute_obs_limit(max_ctx: int) -> int:
    """Retourne le budget observation (en chars) pour un modèle de fenêtre ``max_ctx`` tokens.

    Args:
        max_ctx: ``context_window`` du modèle (tokens). ``0`` ou négatif → fallback.

    Returns:
        Nombre de caractères max autorisés pour UNE observation dans l'historique,
        **avant** microcompaction.

    Override:
        ``LUMENA_REACT_OBS_LIMIT`` (int positif) écrase toute la logique.
        ``LUMENA_REACT_OBS_CLAMP="lo:hi"`` applique un clamp final.
    """
    # Override global (utilisé pour tuning/debug)
    forced = _parse_int_env("LUMENA_REACT_OBS_LIMIT")
    if forced > 0:
        value = forced
    else:
        if max_ctx <= 0:
            value = 8_000                 # fallback safe
        elif max_ctx < 16_000:
            value = 2_000                 # Ollama tiny
        elif max_ctx < 64_000:
            value = 8_000                 # Ollama medium
        elif max_ctx < 150_000:
            value = 24_000                # 128k cloud (GPT-4o, Grok 4.1, NVIDIA, DeepSeek)
        elif max_ctx < 220_000:
            value = 32_000                # 200k Claude, o3, MiniMax 204k
        elif max_ctx < 500_000:
            value = 32_000                # 262k Kimi K2.5
        elif max_ctx < 1_500_000:
            value = 40_000                # ~1M GPT-5.4, Claude 4.5+, Gemini
        else:
            value = 48_000                # 2M Grok 4.20

    clamp = _parse_clamp_env("LUMENA_REACT_OBS_CLAMP")
    if clamp:
        lo, hi = clamp
        value = max(lo, min(value, hi))
    return max(500, value)  # plancher absolu de sécurité


def compute_obs_limit_from_runtime(runtime_ctx) -> int:
    """Variante pratique lisant ``runtime_ctx.max_context_window`` si dispo."""
    if runtime_ctx is None:
        return compute_obs_limit(0)
    max_ctx = getattr(runtime_ctx, "max_context_window", 0) or 0
    return compute_obs_limit(int(max_ctx))


def should_protect_observation(tool_name: str | None) -> bool:
    """True si la dernière observation de ``tool_name`` doit rester intacte.

    Respecte le kill-switch ``LUMENA_REACT_PROTECT_LAST_READ`` (default ON).
    """
    if os.getenv("LUMENA_REACT_PROTECT_LAST_READ", "1").strip() not in ("1", "true", "yes", "on"):
        return False
    if not tool_name:
        return False
    return tool_name.strip().lower() in READER_TOOLS


def split_head_tail(text: str, budget: int, *, head_ratio: float = 0.5) -> str:
    """Microcompaction 50/50 (par défaut) : garde ``budget`` chars total, coupe au milieu.

    Args:
        text: observation brute
        budget: char budget final (incluant le marker d'élision)
        head_ratio: fraction du budget allouée au début (rest = fin). 0.5 = 50/50.

    Returns:
        Texte compacté si len(text) > budget, sinon ``text`` inchangé.
    """
    if budget <= 0 or len(text) <= budget:
        return text
    marker_reserve = 60
    usable = max(200, budget - marker_reserve)
    head_size = max(100, int(usable * head_ratio))
    tail_size = max(100, usable - head_size)
    omitted = len(text) - head_size - tail_size
    return (
        text[:head_size]
        + f"\n\n[... {omitted} chars omis (microcompact {int(head_ratio * 100)}/{100 - int(head_ratio * 100)}) ...]\n\n"
        + text[-tail_size:]
    )
