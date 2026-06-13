"""
overlap_detector.py — Phase E : detection deterministe d'overlaps native ↔ MCP.

Doctrine :
  - Pur Python, aucun LLM, aucun appel reseau.
  - Tokenisation lowercase + filtrage stopwords FR/EN courts.
  - Score Jaccard sur tokens (description + nom + server_name).
  - Seuil parametrable (defaut 0.35) + minimum shared keywords (defaut 2).
  - Sortie deterministe : tri stable par (mcp_tool_name, native_tool_name).

Le detecteur ne PRENDS PAS de decision sur la visibilite : il remonte les
overlaps, c'est le ToolRegistry qui applique `prefer_over_native`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional


# ──────────────────────────────────────────────────────────────────────────────
# Tokenisation
# ──────────────────────────────────────────────────────────────────────────────

# On garde les mots de >= 3 lettres pour ecarter le bruit (a/an/le/de...).
_KEYWORD_RE = re.compile(r"[A-Za-z]{3,}")

# Stopwords minimaux FR + EN — limites au strict necessaire pour ne pas
# bruler des signaux utiles dans des descriptions courtes.
_STOPWORDS: FrozenSet[str] = frozenset({
    # EN
    "the", "and", "for", "with", "this", "that", "from", "into",
    "use", "uses", "used", "via", "any", "all", "are", "was", "you",
    "your", "out", "its", "but", "not", "can", "may", "get", "set",
    "new", "old", "via", "let", "has", "have", "had", "one", "two",
    "tool", "tools", "based", "given", "their", "them", "they",
    # FR
    "les", "des", "une", "que", "qui", "pour", "avec", "dans", "sur",
    "vers", "par", "cette", "ces", "son", "ses", "leur", "leurs",
    "est", "sont", "etait", "etaient", "etre", "ete", "fait", "tout",
    "tous", "plus", "moins", "ainsi", "comme", "sans", "deja",
    "outil", "outils",
})

# Prefixes a stripper avant tokenisation des noms d'outils MCP.
_NAMESPACE_PREFIX_RE = re.compile(r"^mcp__[a-zA-Z0-9_.\-]+__")


def _strip_mcp_prefix(name: str) -> str:
    """Retire le prefixe `mcp__server__` d'un nom d'outil MCP namespace."""
    if not isinstance(name, str):
        return ""
    return _NAMESPACE_PREFIX_RE.sub("", name, count=1)


def _normalize_underscore(text: str) -> str:
    if not isinstance(text, str):
        return ""
    return text.replace("_", " ").replace("-", " ").replace(".", " ")


def _tokenize(text: str) -> FrozenSet[str]:
    """Tokenise du texte libre en set de mots significatifs (lowercase)."""
    if not isinstance(text, str) or not text:
        return frozenset()
    raw = {t.lower() for t in _KEYWORD_RE.findall(_normalize_underscore(text))}
    return frozenset(
        t for t in raw if t not in _STOPWORDS and len(t) >= 3
    )


def _jaccard(a: FrozenSet[str], b: FrozenSet[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    if intersection == 0:
        return 0.0
    union = len(a | b)
    return intersection / union if union else 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Dataclass
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class OverlapMatch:
    """Un match d'overlap natif ↔ MCP.

    Attributs:
        mcp_tool_name: Nom namespace complet (mcp__server__tool).
        native_tool_name: Nom du handler natif.
        similarity_score: Jaccard ∈ [0, 1], arrondi a 4 decimales.
        shared_keywords: Tokens en intersection (frozenset).
    """
    mcp_tool_name: str
    native_tool_name: str
    similarity_score: float
    shared_keywords: FrozenSet[str]


# ──────────────────────────────────────────────────────────────────────────────
# API publique
# ──────────────────────────────────────────────────────────────────────────────


def detect_overlaps(
    *,
    server_name: str,
    mcp_tools: List,
    native_handler_names: List[str],
    native_descriptions: Dict[str, str],
    threshold: float = 0.35,
    min_shared_keywords: int = 2,
) -> List[OverlapMatch]:
    """Detecte les overlaps natifs ↔ MCP par tokens Jaccard.

    Args:
        server_name: nom du serveur MCP (enrichit les tokens MCP).
        mcp_tools: liste de MCPTool (objets duck-typed avec .name + .description).
        native_handler_names: liste des noms de handlers natifs candidats.
        native_descriptions: map nom natif → description.
        threshold: seuil Jaccard minimum (defaut 0.35).
        min_shared_keywords: nombre minimal de tokens partages (defaut 2)
            — protege contre les faux positifs sur 1 token commun.

    Returns:
        Liste d'OverlapMatch triee par (mcp_tool_name, native_tool_name).
        Plusieurs natifs peuvent matcher un meme MCP (et inversement).
    """
    if not isinstance(threshold, (int, float)) or not (0.0 <= threshold <= 1.0):
        raise ValueError(
            f"threshold must be in [0, 1], got {threshold!r}"
        )
    if not isinstance(min_shared_keywords, int) or min_shared_keywords < 1:
        raise ValueError(
            f"min_shared_keywords must be a positive int, got {min_shared_keywords!r}"
        )

    server_tokens = _tokenize(server_name or "")

    # Pre-tokenize natifs une fois.
    native_tokens_by_name: Dict[str, FrozenSet[str]] = {}
    for native_name in native_handler_names or []:
        if not isinstance(native_name, str) or not native_name:
            continue
        native_desc = native_descriptions.get(native_name, "") if native_descriptions else ""
        native_tokens_by_name[native_name] = _tokenize(
            _normalize_underscore(native_name) + " " + (native_desc or "")
        )

    matches: List[OverlapMatch] = []
    for mcp_tool in mcp_tools or []:
        mcp_name_raw = getattr(mcp_tool, "name", None)
        if not isinstance(mcp_name_raw, str) or not mcp_name_raw:
            continue
        mcp_desc = getattr(mcp_tool, "description", "") or ""
        # Tokens MCP : nom outil sans namespace + description + nom serveur
        # (le server_name aide a faire le lien "gmail" → "email").
        mcp_tokens = (
            _tokenize(_normalize_underscore(_strip_mcp_prefix(mcp_name_raw)))
            | _tokenize(mcp_desc)
            | server_tokens
        )
        if not mcp_tokens:
            continue

        # Nom namespace de sortie (cohérent avec ce qu'enregistre adapt_tool).
        mcp_namespaced = (
            mcp_name_raw
            if mcp_name_raw.startswith("mcp__")
            else f"mcp__{server_name}__{mcp_name_raw}" if server_name else mcp_name_raw
        )

        for native_name, native_tokens in native_tokens_by_name.items():
            if not native_tokens:
                continue
            shared = mcp_tokens & native_tokens
            if len(shared) < min_shared_keywords:
                continue
            score = _jaccard(mcp_tokens, native_tokens)
            if score < threshold:
                continue
            matches.append(OverlapMatch(
                mcp_tool_name=mcp_namespaced,
                native_tool_name=native_name,
                similarity_score=round(score, 4),
                shared_keywords=frozenset(shared),
            ))

    # Tri stable pour determinisme (utile pour audit + tests).
    matches.sort(key=lambda m: (m.mcp_tool_name, m.native_tool_name))
    return matches


def group_overlaps_by_mcp(
    matches: List[OverlapMatch],
) -> Dict[str, FrozenSet[str]]:
    """Regroupe les matches par MCP tool name → set de natifs en overlap."""
    out: Dict[str, set] = {}
    for m in matches:
        out.setdefault(m.mcp_tool_name, set()).add(m.native_tool_name)
    return {k: frozenset(v) for k, v in out.items()}


def group_overlaps_by_native(
    matches: List[OverlapMatch],
) -> Dict[str, FrozenSet[str]]:
    """Regroupe les matches par natif → set de MCP en overlap."""
    out: Dict[str, set] = {}
    for m in matches:
        out.setdefault(m.native_tool_name, set()).add(m.mcp_tool_name)
    return {k: frozenset(v) for k, v in out.items()}
