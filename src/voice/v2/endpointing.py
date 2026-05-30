"""Endpointing heuristique V1 (FR) — distinguer silence et vraie fin de pensée.

V1 = heuristiques lexicales + signal temporel. V2 (plus tard) = petit modèle local
ou turn-detector LiveKit. La règle V2.3 : les partiels pilotent le TIMING (cette
décision), jamais le contenu/action.
"""
from __future__ import annotations

import re

from .state import EndpointDecision

# Locutions de fin (multi-mots) → l'utilisateur n'a PAS fini.
_FILLERS_PHRASES = (
    "parce que", "parce qu", "je veux", "je voudrais", "tu peux", "tu pourrais",
    "est-ce que", "c'est a dire", "c'est-a-dire",
)
# Dernier mot connecteur/pronom → continuation probable.
_TRAILING_WORDS = frozenset({
    "euh", "hum", "heu", "et", "donc", "car", "si", "mais", "alors", "puis",
    "ensuite", "pour", "que", "qui", "tu", "je", "de", "a", "à", "la", "le",
    "les", "un", "une", "des", "comment", "attends",
})

# Signaux de fin de tour claire.
_ACTION_VERBS = (
    "ouvre", "ferme", "lance", "déploie", "deploie", "supprime", "crée", "cree",
    "liste", "montre", "explique", "résume", "resume", "arrête", "arrete", "stop",
    "envoie", "ajoute", "vide", "mets", "fais",
)


def _normalize(text: str) -> str:
    return (text or "").strip().lower().rstrip(" .!?,;")


def decide_endpoint(text: str, *, is_final: bool = False,
                    pause_ms: int = 0, user_avg_pause_ms: int = 600) -> EndpointDecision:
    """Décide si le tour est terminé.

    - `is_final`  : transcript final STT (plus fiable).
    - `pause_ms`  : durée de silence observée.
    - `user_avg_pause_ms` : rythme de pause appris sur la session (signal robuste).
    """
    t = _normalize(text)
    if not t:
        return EndpointDecision("uncertain", 0.1, 300, 2500, "empty")

    # 1) Fin par locution/mot connecteur → l'utilisateur cherche ses mots : attendre.
    last_word = t.rsplit(" ", 1)[-1] if " " in t else t
    if any(t.endswith(p) for p in _FILLERS_PHRASES) or last_word in _TRAILING_WORDS:
        return EndpointDecision("continue_expected", 0.85, 600, 3000, "ends_with_filler")

    # 2) Question complète OU ordre clair → répondre vite.
    looks_question = t.endswith(("?",)) or t.startswith(("est-ce", "pourquoi", "comment", "quand", "où", "ou ", "qui", "quel", "quelle", "combien"))
    first_word = t.split(" ", 1)[0] if t else ""
    looks_command = first_word in _ACTION_VERBS
    if looks_question or looks_command:
        return EndpointDecision("turn_complete", 0.8, 200, 1200, "complete_request")

    # 3) Final + silence ≥ rythme habituel → fin probable.
    if is_final and pause_ms >= max(300, user_avg_pause_ms):
        return EndpointDecision("turn_complete", 0.7, 200, 1500, "final_with_pause")

    # 4) Final court répondant à une question de Lumena (oui/non/ok…).
    if is_final and len(t.split()) <= 3:
        return EndpointDecision("turn_complete", 0.65, 200, 1200, "short_final")

    # 5) Sinon : incertain → ne pas répondre, attendre confirmation.
    return EndpointDecision("uncertain", 0.4, 400, 2500, "uncertain")
