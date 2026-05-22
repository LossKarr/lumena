"""V2.1 fix prod rev 2 (2026-05-19) : un thought "INTENTION" ne doit JAMAIS
être accepté comme réponse finale.

Test prod observé :
- Plan business complete, Action: final, answer courte.
- ReAct prenait thought.content comme réponse alors qu'il contenait
  "Toutes les étapes sont terminées... je vais livrer une réponse complète."
- L'utilisateur recevait cette promesse, pas le livrable.

Le helper `_looks_like_intention` doit retourner True pour ces cas et False
pour les vrais livrables.
"""

from __future__ import annotations

import pytest

from src.reasoning.react import _looks_like_intention


# ─── Cas observé en prod (rejet attendu) ────────────────────────────────


@pytest.mark.parametrize("intention_text", [
    "Toutes les étapes sont terminées ✅. Le plan est à 4/5, il me reste à "
    "synthétiser les résultats pour l'utilisateur. J'ai toutes les données "
    "nécessaires... Je vais livrer une réponse complète et claire.",
    "Je vais maintenant fournir la réponse à l'utilisateur.",
    "Je dois synthétiser les résultats avant de répondre.",
    "Je peux maintenant livrer le rapport demandé.",
    "Toutes les étapes sont complètes, je vais répondre.",
    "Il me reste à présenter le résultat à l'utilisateur.",
    "I will now provide the complete answer to the user.",
    "Let me now summarize the findings.",
])
def test_intention_text_detected(intention_text: str):
    """Ces textes sont des promesses, pas des livrables → True."""
    assert _looks_like_intention(intention_text) is True, (
        f"L'intention suivante n'a pas été détectée :\n{intention_text}"
    )


# ─── Cas livrable réel (acceptation attendue) ───────────────────────────


@pytest.mark.parametrize("deliverable", [
    # Output type data_profile_file
    "Voici le profil du fichier marches_publics.csv :\n"
    "- Lignes : 42\n- Colonnes : 17\n- Encoding : utf-8\n- Séparateur : `;`\n"
    "- MD5 : 0bc3007e95616934a9a39e1508d49db0",
    # Réponse business avec citation data.gouv
    "J'ai téléchargé le dataset avec resource_id 98e4ae8b-cff3-4b46-80e0-ce75a3143d4f. "
    "Le fichier contient 35000 communes avec leurs codes INSEE.",
    # Tableau markdown
    "| Commune | Population |\n|---|---|\n| Paris | 2102650 |\n| Marseille | 873076 |",
    # Réponse courte mais factuelle
    "Le dataset contient 42 lignes et 17 colonnes au format CSV séparateur `;`.",
    # Réponse mixte intention + livrable (livrable doit prédominer)
    "Je vais te résumer : 42 lignes, 17 colonnes, MD5 0bc3007e95616934, "
    "chemin downloads/datagouv/marches.csv, encoding utf-8.",
])
def test_real_deliverable_accepted(deliverable: str):
    """Ces textes contiennent des données concrètes → False (livrable OK)."""
    assert _looks_like_intention(deliverable) is False, (
        f"Le livrable suivant a été marqué comme intention :\n{deliverable}"
    )


# ─── Edge cases ──────────────────────────────────────────────────────────


def test_empty_text_is_intention():
    """Texte vide = pas de livrable."""
    assert _looks_like_intention("") is True
    assert _looks_like_intention(None) is True  # type: ignore


def test_pure_text_no_intention_keywords_accepted():
    """Texte neutre sans marqueur d'intention → False (livrable potentiel)."""
    text = "Le marché public a été attribué à l'entreprise SAS Dupont pour 50000 euros."
    assert _looks_like_intention(text) is False


def test_intention_with_numbers_still_intention_if_no_deliverable_markers():
    """Si l'intention domine et qu'il n'y a que peu de chiffres → reste intention."""
    text = "Je vais maintenant te livrer la réponse en 2 étapes."
    # 1 nombre seulement → ne fait pas basculer en livrable
    assert _looks_like_intention(text) is True


def test_intention_with_three_numbers_becomes_deliverable():
    """Heuristique chiffres : 3+ nombres distincts → considéré comme livrable."""
    text = "Je vais te livrer : 42 lignes, 17 colonnes, MD5 0bc30."
    # 42, 17, 0bc30 → trois nombres ≥ 2 chiffres
    assert _looks_like_intention(text) is False
