"""Helpers PURS des guards de réponse finale — extraits de react.py.

Phase 3 (Option A) : on ne sort QUE les helpers purs (détection d'intention,
nettoyage de thought-leak, ré-masquage de secrets). Les guards *control-flow*
(thought-leak, discord-count, mask, premature-final) restent dans react car ils
sont entremêlés avec le PLAN system (cf. audit) ; ils partiront avec la Phase 4.

Module auto-contenu (stdlib uniquement) → aucun import circulaire avec react.
react ré-importe ces noms (point d'import historique des tests).
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# ── Détection « intention » vs « livrable » ──────────────────────────────────
_INTENTION_MARKERS: tuple = (
    "je vais livrer", "je vais répondre", "je vais synthétiser", "je vais presenter",
    "je vais présenter", "je vais maintenant", "je vais fournir", "je vais formuler",
    "je vais résumer", "je vais resumer", "je vais produire", "je vais rédiger",
    "je dois livrer", "je dois synthétiser", "je dois répondre",
    "je dois présenter", "je dois fournir", "je dois resumer", "je dois résumer",
    "je peux maintenant", "je peux livrer", "je peux répondre",
    "je livre", "je présente", "je propose ci-dessous", "je propose ci-dessus",
    "il me reste à", "il me reste a",
    "les données sont déjà", "les donnees sont deja",
    "déjà récupéré", "deja recupere", "déjà récupérée", "deja recuperee",
    "déjà récupérées", "deja recuperees", "déjà récupérés", "deja recuperes",
    "j'ai toutes les données", "j'ai toutes les donnees",
    "j'ai toutes les infos", "j'ai tout ce qu'il faut",
    "toutes les étapes sont terminées", "toutes les etapes sont terminees",
    "toutes les étapes sont complètes", "toutes les etapes sont completes",
    "le plan est à", "le plan est a",
    "i will now", "i will provide", "i will deliver", "i'm going to",
    "i need to synthesize", "i need to provide", "let me now",
)

# Marqueurs de LIVRABLE : présence de chiffres, citations, données concrètes.
_DELIVERABLE_MARKERS: tuple = (
    # nombres formatés
    "lignes :", "lignes:", "colonnes :", "colonnes:",
    # md tableaux / listes
    "\n- ", "\n* ", "\n1.", "\n| ",
    # provenance
    "md5", "sha256", "resource id", "resource_id", "chemin absolu",
    "downloads/", "downloads\\",
    # citations data.gouv typiques
    "data.gouv", "data_profile_file", "datagouv_",
    # devises / pourcentages
    "%", "€", "EUR", "kWh",
)


def _looks_like_intention(text: str) -> bool:
    """Détecte si un texte est une INTENTION ("je vais livrer") plutôt qu'un livrable.

    Retourne True si :
      - le texte contient au moins 1 marqueur d'intention (case-insensitive)
      - ET ne contient AUCUN marqueur de livrable (chiffres, citations, tableau...)
    Sinon False (livrable potentiellement valide).
    """
    if not text:
        return True
    low = text.lower()
    has_intention = any(m in low for m in _INTENTION_MARKERS)
    if not has_intention:
        return False
    # Marqueurs de livrable concret : nombres, citations, tableau
    has_deliverable = any(m in low for m in _DELIVERABLE_MARKERS)
    if has_deliverable:
        return False
    # Heuristique chiffres : au moins 3 nombres distincts ≥ 2 chiffres → livrable probable
    if len(set(re.findall(r"\d{2,}", text))) >= 3:
        return False
    return True


# ── Préfixes de réflexion interne (réponse FINAL qui « leake » le THOUGHT) ──
_INTERNAL_PREFIXES = (
    # FR — réflexion interne
    "l'utilisateur me demande",
    "l'utilisateur demande",
    "l'utilisateur souhaite",
    "l'utilisateur veut",
    "l'utilisateur a demandé",
    "l'utilisateur a sollicité",
    "l'utilisateur me pose",
    "me demande comment",
    "me demande de",
    "me demande si",
    "je dois maintenant",
    "je vais maintenant synthétiser",
    "je vais maintenant formuler",
    "je vais maintenant fournir",
    "je vais maintenant résumer",
    "je vais maintenant répondre",
    "je vais maintenant donner",
    "je vais répondre directement",
    "je réponds directement",
    "je dois analyser",
    "je dois vérifier",
    "je dois d'abord",
    "je dois ensuite",
    "je lance ",
    "je lance`",
    "je vais lire ",
    "je vais vérifier ",
    "je vais chercher ",
    "je vais appeler ",
    "je vais utiliser ",
    "je vais grep",
    "j'ai exécuté les",
    "j'ai déjà exécuté",
    "j'ai déjà effectué une recherche",
    "j'ai maintenant toutes les",
    "maintenant que j'ai",
    "sur la base de",
    "après avoir analysé",
    "d'après les résultats",
    "rien à faire ici",
    "rien à faire,",
    # EN — internal reasoning prefixes
    "the user is asking",
    "the user wants",
    "the user asked",
    "the user requested",
    "i need to now",
    "i should now",
    "i will now",
    "i'll now",
    "let me analyze",
    "let me now",
    "let me provide",
    "let me summarize",
    "let me now provide",
    "based on the",
    "based on my",
    "now that i have",
    "i have already",
    "i've already",
    "i have now",
    "i've now",
    "having gathered",
)


def strip_thought_leak_prefix(text: str) -> Optional[str]:
    """Supprime les phrases de réflexion interne du début d'une réponse.

    Retourne le texte nettoyé si du contenu utile reste (≥ 50 chars),
    sinon None (la reformulation classique prendra le relais).
    """
    # Patterns de phrases internes à retirer du début.
    # On retire phrase par phrase jusqu'à trouver du contenu utilisateur.
    _STRIP_PATTERNS = [
        # FR
        re.compile(
            r"^(?:l['‘’]utilisateur\s+(?:demande|veut|souhaite|a\s+demandé)[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:je\s+(?:dois|vais|peux)\s+[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:il\s+faut\s+que\s+je\s+[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:(?:maintenant\s+que\s+j['‘’]ai|après\s+avoir|sur\s+la\s+base\s+de|d['‘’]après\s+les)\s+[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:(?:j['‘’]ai\s+(?:déjà|maintenant|exécuté|effectué|analysé))[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:(?:rien\s+à\s+faire)[^.!?\n]{0,80}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        # EN
        re.compile(
            r"^(?:the\s+user\s+(?:is\s+asking|wants|asked|requested)\s+[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:(?:i\s+(?:need\s+to|should|will)|i['‘’](?:ll|ve)|let\s+me)\s+[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?:(?:based\s+on|now\s+that\s+i\s+have|i\s+have\s+(?:already|now)|having\s+gathered)\s+[^.!?\n]{0,200}[.!?\n]\s*)",
            re.IGNORECASE,
        ),
    ]

    cleaned = text.strip()
    # On fait plusieurs passes (un prefix peut en cacher un autre)
    for _ in range(5):
        changed = False
        for pat in _STRIP_PATTERNS:
            m = pat.match(cleaned)
            if m:
                cleaned = cleaned[m.end():].strip()
                changed = True
                break
        if not changed:
            break

    # Sécurité : si on a trop nettoyé, retourner None
    if not cleaned or len(cleaned) < 50:
        return None
    # Sécurité : si le résultat commence toujours par un prefix interne, abandonner
    _cl = cleaned.lower()
    _STILL_INTERNAL = (
        "l'utilisateur", "je dois ", "je vais ", "il faut que je",
        "the user ", "i need to", "i should ", "i will now",
    )
    if any(_cl.startswith(p) for p in _STILL_INTERNAL):
        return None
    return cleaned


_MASK_TOKEN_RE = re.compile(r"([A-Za-z0-9_.\-]{3,}\*{2,}[A-Za-z0-9_.\-]*)")


def remask_secrets(answer: str, observation_texts: Iterable[str]) -> str:
    """Anti-fuite : ré-impose les valeurs masquées vues en observation.

    Si une observation contient un champ masqué (`db50****.io`), toute valeur
    concrète du même préfixe/suffixe dans `answer` est réécrite vers la forme
    masquée. Pur : prend la liste des textes d'observation en paramètre.
    """
    texts = [t for t in observation_texts]
    if not answer or "****" not in "".join(texts):
        return answer

    tokens: set = set()
    for content in texts:
        if not content:
            continue
        for m in _MASK_TOKEN_RE.findall(content):
            tokens.add(m)

    out = answer
    for tok in tokens:
        star_idx = tok.find("*")
        prefix = tok[:star_idx]
        suffix = tok[star_idx:].lstrip("*")
        # Garde-fou anti sur-correction : préfixe ET suffixe assez spécifiques.
        if len(prefix) < 3 or len(suffix) < 2:
            continue
        pat = re.escape(prefix) + r"[A-Za-z0-9_\-]+" + re.escape(suffix)
        out = re.sub(pat, tok, out)
    return out
