"""
Fuzzy replace engine — P1 Plan Suprême CodeAgent.

Consolide toutes les stratégies fuzzy pour str_replace / edit_file :
- Exact match
- CRLF normalization (Windows \r\n → \n)
- Trailing whitespace normalization
- Full strip (leading + trailing)
- Unicode punctuation normalization (smart quotes, dashes, nbsp)
- Collapsed whitespace (multiples spaces → 1)
- Indent-tolerant (leading whitespace flexible)

Chaque passe renvoie le contenu modifié ET le nom de la méthode qui a matché,
permettant au caller d'informer le LLM du type de normalisation appliqué.

API publique :
    fuzzy_replace(content, old_str, new_str) → (new_content, method) | None

Feature-flag-gated via src.config.codeagent_flags.FUZZY_REPLACE.
Fail-safe : aucune exception ne fuit, None si rien ne matche.
"""

from __future__ import annotations

import re
from typing import NamedTuple, Optional

from src.tools.apply_patch import _normalize_punctuation


class FuzzyMatch(NamedTuple):
    new_content: str
    method: str
    matched_text: str


# ── Normalisations ────────────────────────────────────────────────────────

_MULTI_WS = re.compile(r"[ \t]+")


def _normalize_crlf(text: str) -> str:
    """Convertit \r\n / \r en \n."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _collapse_ws(text: str) -> str:
    """Écrase les espaces/tabs multiples en un seul espace (préserve \n)."""
    lines = text.split("\n")
    return "\n".join(_MULTI_WS.sub(" ", line) for line in lines)


def _strip_leading_indent(text: str) -> str:
    """Retire l'indentation en tête de chaque ligne (pour matching indent-tolerant)."""
    return "\n".join(line.lstrip() for line in text.split("\n"))


def _full_normalize(text: str) -> str:
    """Passe combinée : CRLF → punct Unicode → strip lines → collapse WS."""
    t = _normalize_crlf(text)
    t = _normalize_punctuation(t)
    t = "\n".join(line.rstrip() for line in t.split("\n"))
    t = _collapse_ws(t)
    return t


# ── Moteur principal ──────────────────────────────────────────────────────


def fuzzy_replace(content: str, old_str: str, new_str: str) -> Optional[FuzzyMatch]:
    """
    Tente de remplacer `old_str` par `new_str` dans `content` avec plusieurs
    passes de normalisation croissante.

    Returns:
        FuzzyMatch(new_content, method, matched_text) si trouvé, None sinon.

    Garantie : remplace UNE SEULE occurrence (la première trouvée).
    Aucune exception levée (fail-safe).

    Méthodes (dans l'ordre) :
        1. exact        — str.replace direct
        2. crlf         — CRLF → LF sur les deux côtés
        3. rstrip       — retire trailing whitespace des lignes
        4. strip        — retire leading+trailing whitespace
        5. punct        — normalise guillemets/tirets/espaces Unicode
        6. collapse_ws  — écrase espaces multiples
        7. full         — toutes les normalisations combinées
        8. indent       — ignore l'indentation de chaque ligne

    Sécurité :
        - Si FUZZY_REPLACE=0 → retourne None sauf pour pass 1 (exact)
        - Si old_str vide → None (pas de match possible)
    """
    if not old_str or not content:
        return None

    # Pass 1 : exact (toujours tenté même si flag off)
    if old_str in content:
        return FuzzyMatch(
            new_content=content.replace(old_str, new_str, 1),
            method="exact",
            matched_text=old_str,
        )

    # Flag gate : passes 2+ nécessitent FUZZY_REPLACE
    try:
        from src.config.codeagent_flags import FUZZY_REPLACE
        if not FUZZY_REPLACE:
            return None
    except Exception:
        return None

    # Pass 2 : CRLF normalization
    content_lf = _normalize_crlf(content)
    old_lf = _normalize_crlf(old_str)
    if old_lf != old_str or content_lf != content:
        if old_lf in content_lf:
            idx = content_lf.index(old_lf)
            # On reconstruit sur le contenu normalisé pour garder la cohérence
            return FuzzyMatch(
                new_content=content_lf[:idx] + new_str + content_lf[idx + len(old_lf):],
                method="crlf",
                matched_text=old_lf,
            )

    # Pass 3 : rstrip sur chaque ligne
    def _rstrip_lines(s: str) -> str:
        return "\n".join(line.rstrip() for line in s.split("\n"))

    content_r = _rstrip_lines(content_lf)
    old_r = _rstrip_lines(old_lf)
    if old_r in content_r:
        idx = content_r.index(old_r)
        return FuzzyMatch(
            new_content=content_r[:idx] + new_str + content_r[idx + len(old_r):],
            method="rstrip",
            matched_text=old_r,
        )

    # Pass 4 : strip complet de chaque ligne
    def _strip_lines(s: str) -> str:
        return "\n".join(line.strip() for line in s.split("\n"))

    content_s = _strip_lines(content_lf)
    old_s = _strip_lines(old_lf)
    if old_s and old_s in content_s:
        idx = content_s.index(old_s)
        return FuzzyMatch(
            new_content=content_s[:idx] + new_str + content_s[idx + len(old_s):],
            method="strip",
            matched_text=old_s,
        )

    # Pass 5 : punct Unicode
    content_p = _normalize_punctuation(content_lf)
    old_p = _normalize_punctuation(old_lf)
    if old_p in content_p:
        idx = content_p.index(old_p)
        return FuzzyMatch(
            new_content=content_p[:idx] + new_str + content_p[idx + len(old_p):],
            method="punct",
            matched_text=old_p,
        )

    # Pass 6 : collapsed whitespace
    content_cw = _collapse_ws(content_lf)
    old_cw = _collapse_ws(old_lf)
    if old_cw in content_cw:
        idx = content_cw.index(old_cw)
        return FuzzyMatch(
            new_content=content_cw[:idx] + new_str + content_cw[idx + len(old_cw):],
            method="collapse_ws",
            matched_text=old_cw,
        )

    # Pass 7 : full normalization
    content_f = _full_normalize(content)
    old_f = _full_normalize(old_str)
    if old_f and old_f in content_f:
        idx = content_f.index(old_f)
        return FuzzyMatch(
            new_content=content_f[:idx] + new_str + content_f[idx + len(old_f):],
            method="full",
            matched_text=old_f,
        )

    # Pass 8 : indent-tolerant (retire indent de chaque ligne)
    content_i = _strip_leading_indent(content_lf)
    old_i = _strip_leading_indent(old_lf)
    if old_i and old_i in content_i:
        idx = content_i.index(old_i)
        return FuzzyMatch(
            new_content=content_i[:idx] + new_str + content_i[idx + len(old_i):],
            method="indent",
            matched_text=old_i,
        )

    return None


__all__ = ["fuzzy_replace", "FuzzyMatch"]
