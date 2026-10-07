"""Endpointing explicable — distinguer silence et vraie fin de pensée.

V1 = heuristiques lexicales + signal temporel. V2 (plus tard) = petit modèle local
ou turn-detector LiveKit. La règle V2.3 : les partiels pilotent le TIMING (cette
décision), jamais le contenu/action.
"""
from __future__ import annotations

import re
import unicodedata

from .state import EndpointDecision

# Locutions de fin (multi-mots) → l'utilisateur n'a PAS fini.
_FILLERS_PHRASES = (
    "parce que", "parce qu", "je veux", "je voudrais", "tu peux", "tu pourrais",
    "est-ce que", "c'est a dire", "c'est-a-dire", "je veux dire", "non plutot",
    "en fait", "attends je", "because", "i want", "i mean", "no rather",
    "porque", "quiero", "quiero decir", "no mejor",
)
# Dernier mot connecteur/pronom → continuation probable.
_TRAILING_WORDS = frozenset({
    "euh", "hum", "heu", "et", "donc", "car", "si", "mais", "alors", "puis",
    "ensuite", "pour", "que", "qui", "tu", "je", "de", "a", "à", "la", "le",
    "les", "un", "une", "des", "comment", "attends",
    "or", "because", "so", "then", "but", "if", "and", "well", "wait",
    "porque", "entonces", "pero", "si", "y", "bueno", "espera",
})

# Signaux de fin de tour claire.
_ACTION_VERBS = (
    "ouvre", "ferme", "lance", "déploie", "deploie", "supprime", "crée", "cree",
    "liste", "montre", "explique", "résume", "resume", "arrête", "arrete", "stop",
    "envoie", "ajoute", "vide", "mets", "fais",
)


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w]+", " ", value.lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


# LOT VOICE-5 (29/09/2026) — plancher d'attente avant de trancher un tour.
#
# Mesure comparant les deux journaux : le 28/09 avant 18 h 35 (avant que V3 ne branche
# l'endpointing dans le pipeline live), la capture s'arretait apres le HANGOVER de 700 ms
# et **84 % des phrases captees contenaient le verbe d'action** (25 caracteres de moyenne).
# Le 29/09 a 01 h 30, avec l'endpointing branche et ses 180 ms : **37 %** seulement
# (18 caracteres). Sans verbe, Lumena ne peut pas agir — elle demande de preciser. Le
# nombre d'outils par demande est tombe de 5,7 a 0,9, et Charles a repete six fois.
#
# Le pire cas mesure : le mot « ouvre » SEUL rendait `turn_complete / 180 ms`, parce que
# `looks_command` teste le PREMIER mot. La decision tombait avant « ...moi Discord ».
#
# Ce plancher rend a l'utilisateur le temps qu'il avait. L'endpointing garde tout le
# reste : il peut toujours ALLONGER l'attente (jusqu'a 2,8 s sur une hesitation), et les
# ETATS qu'il rend sont inchanges — ce lot touche des DELAIS, pas des decisions.
_PLANCHER_MS = 700          # le hangover qui regnait seul dans la backup
_PLANCHER_REPONSE_COURTE_MS = 500   # « oui »/« ok » : pas besoin d'attendre autant


def decide_endpoint(text: str, *, is_final: bool = False,
                    pause_ms: int = 0, user_avg_pause_ms: int = 600) -> EndpointDecision:
    """Décide si le tour est terminé.

    - `is_final`  : transcript final STT (plus fiable).
    - `pause_ms`  : durée de silence observée.
    - `user_avg_pause_ms` : rythme de pause appris sur la session (signal robuste).
    """
    raw = (text or "").strip()
    t = _normalize(raw)
    if not t:
        return EndpointDecision("uncertain", 0.1, _PLANCHER_MS, 2500, "empty")

    # 1) Fin par locution/mot connecteur → l'utilisateur cherche ses mots : attendre.
    last_word = t.rsplit(" ", 1)[-1] if " " in t else t
    adaptive_pause = max(350, min(1600, int(user_avg_pause_ms or 600)))
    suspended_punctuation = bool(re.search(r"(?:[,;:]|\.{2,}|…|[-—])\s*$", raw))
    if (any(t.endswith(p) for p in _FILLERS_PHRASES)
            or last_word in _TRAILING_WORDS or suspended_punctuation):
        return EndpointDecision(
            "continue_expected", 0.88,
            max(600, adaptive_pause), max(2800, adaptive_pause * 3),
            "suspended_or_continuation",
        )

    # 2) Question complète OU ordre clair → répondre vite.
    looks_question = raw.endswith("?") or t.startswith((
        "est-ce", "pourquoi", "comment", "quand", "ou", "qui", "quel", "quelle",
        "combien", "why", "how", "when", "where", "what", "which", "who",
        "por que", "como", "cuando", "donde", "que", "quien", "cual",
    ))
    first_word = t.split(" ", 1)[0] if t else ""
    looks_command = first_word in _ACTION_VERBS
    if looks_question or looks_command:
        return EndpointDecision("turn_complete", 0.84, _PLANCHER_MS, 1800,
                                "complete_request")

    # 3) Final + silence ≥ rythme habituel → fin probable.
    if is_final and pause_ms >= adaptive_pause:
        return EndpointDecision("turn_complete", 0.7, _PLANCHER_MS, 1800,
                                "final_with_pause")

    # 4) Final court répondant à une question de Lumena (oui/non/ok…).
    if is_final and len(t.split()) <= 3:
        return EndpointDecision("turn_complete", 0.65, _PLANCHER_REPONSE_COURTE_MS, 1500,
                                "short_final")

    # 5) Sinon : incertain → ne pas répondre, attendre confirmation.
    return EndpointDecision(
        # VOICE-5 : cette branche n'est PAS planchee, a dessein. `uncertain` alimente un
        # timer A DEUX ETAGES (premier tir -> `user_paused`, second -> on tranche) :
        # l'utilisateur y a donc deja deux fenetres. Y porter le plancher faisait
        # basculer la seconde evaluation en `turn_complete` (`pause_ms >= adaptive_pause`)
        # et SUPPRIMAIT le second etage — casse mesuree sur
        # `test_timer_endpoint_uncertain_neutral_waits` et
        # `test_uncertain_final_uses_two_stage_timer_without_double_send`.
        "uncertain", 0.4, min(450, adaptive_pause),
        max(1800, adaptive_pause * 2), "uncertain",
    )
