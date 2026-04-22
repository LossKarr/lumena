"""
Suite de vérité terrain pour l'intent_router.

Couvre 30+ prompts représentatifs et impose des intents attendus.
Cette suite sert de garde-fou : un refacto du router ne doit PAS
régresser ces classifications.

Exécution : pytest tests/integration/test_intent_router_ground_truth.py -q
"""
from __future__ import annotations

import pytest

from src.reasoning.intent_router import _regex_fallback, clear_cache


# ─── Cas de vérité terrain ────────────────────────────────────────────
# (query, intent_attendu) — pas de LLM, on valide uniquement le fallback regex
# car en production le LLM peut timeout et le fallback doit être sain.

GROUND_TRUTH: list[tuple[str, str]] = [
    # ── CODE_WRITE : impératifs clairs ──
    ("fais un jeu snake", "CODE_WRITE"),
    ("crée-moi un site vitrine pour ma boulangerie", "CODE_WRITE"),
    ("ajoute un bouton rouge dans le header", "CODE_WRITE"),
    ("corrige le bug du footer dans blog.html", "CODE_WRITE"),
    ("supprime le fichier old.js", "CODE_WRITE"),
    ("modifie le style du header", "CODE_WRITE"),
    ("refactor la fonction login", "CODE_WRITE"),
    ("remplace les var par des const", "CODE_WRITE"),
    ("lumena tu pourrais finir le jeu snake stp", "CODE_WRITE"),
    ("peux-tu corriger l'erreur de syntaxe", "CODE_WRITE"),
    ("stp ajoute un menu déroulant", "CODE_WRITE"),
    # ── CODE_WRITE : impératif après feedback (« bug, corrige X ») ──
    ("quand je joue le jeu bug, corrige le script.js", "CODE_WRITE"),
    ("le score s'affiche pas, répare ça", "CODE_WRITE"),

    # ── CODE_READ : analyse explicite ──
    ("analyse juste mon site, ne modifie rien", "CODE_READ"),
    ("regarde mon code et dis-moi ce que tu en penses", "CODE_READ"),
    ("qu'est-ce que tu penses de mon site ?", "CODE_READ"),
    ("donne-moi ton avis sur le header", "CODE_READ"),
    ("audite le fichier main.py", "CODE_READ"),
    ("explique-moi comment fonctionne ce code", "CODE_READ"),

    # ── CHAT : feedback / observation pure ──
    ("quand je fait jouer le jeu marche mais le message avec bouton reste afficher", "CHAT"),
    ("ça marche pas quand je clique sur le bouton du jeu", "CHAT"),
    ("j'ai un bug sur le jeu le score s'affiche pas", "CHAT"),
    ("y a un problème avec mon site", "CHAT"),
    ("le menu ne s'ouvre plus", "CHAT"),
    ("ça plante dès que je recharge la page", "CHAT"),

    # ── BROWSE : URL / visite web ──
    ("va sur lemonde.fr et résume la une", "BROWSE"),
    ("visite https://example.com", "BROWSE"),
    ("ouvre le site github.com/lumena", "BROWSE"),

    # ── TOOL : actions outils ──
    ("envoie un mail à papa", "TOOL"),
    ("planifie un rendez-vous demain à 10h", "TOOL"),
    ("configure stripe", "TOOL"),
    ("deploy le site sur ionos", "TOOL"),

    # ── RESEARCH : apprentissage / veille ──
    ("apprends-moi le trading", "RESEARCH"),
    ("étudie les actualités crypto", "RESEARCH"),
    ("recherche les tendances bourse", "RESEARCH"),
    # Nouvelles formulations couvertes par _RE_RESEARCH
    ("fais une recherche sur les hippopotames", "RESEARCH"),
    ("tu peux faire une recherche sur l'IA ?", "RESEARCH"),
    ("effectue une recherche sur le bitcoin", "RESEARCH"),
    ("cherche sur les nouvelles tendances tech", "RESEARCH"),
    ("trouve des infos sur les trous noirs", "RESEARCH"),

    # ── CHAT : conversation pure ──
    ("tu vas bien ?", "CHAT"),
    ("bonjour lumena", "CHAT"),
    ("c'est quoi ton modèle favori ?", "CHAT"),
]


@pytest.fixture(autouse=True)
def _clear_router_cache():
    """Vide le cache du router entre chaque test."""
    clear_cache()
    yield
    clear_cache()


@pytest.mark.parametrize("query,expected", GROUND_TRUTH)
def test_regex_ground_truth(query: str, expected: str):
    """Chaque prompt doit tomber sur l'intent attendu via regex fallback."""
    decision = _regex_fallback(query)
    assert decision.intent == expected, (
        f"Query: {query!r}\n"
        f"  Expected: {expected}\n"
        f"  Got:      {decision.intent} (conf={decision.confidence:.2f}, "
        f"reason={decision.reason!r})"
    )


def test_coverage_threshold():
    """La suite couvre au moins 35 prompts et les 6 catégories d'intents."""
    assert len(GROUND_TRUTH) >= 35, "Suite insuffisante (<35 prompts)"
    intents = {intent for _, intent in GROUND_TRUTH}
    missing = set(("CODE_WRITE", "CODE_READ", "BROWSE", "TOOL", "RESEARCH", "CHAT")) - intents
    assert not missing, f"Intents non testés : {missing} — tous les 6 doivent être couverts"
