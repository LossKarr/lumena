"""V2.3 fix prod (2026-05-19) : fallback FINAL depuis observation outil tabulaire.

Scénario observé en UI :
- `data_unique_values` retourne un tableau Markdown complet
- LLM produit un FINAL "intention" ("je vais livrer la liste")
- Après thought_leak_repairs épuisés, l'utilisateur recevait 41 chars
- Attendu : utiliser l'observation comme FINAL de secours
"""

from __future__ import annotations

import pytest

from src.reasoning.react import (
    _obs_looks_tabular,
    _synthesize_response_from_observation,
)


# ─── _obs_looks_tabular ─────────────────────────────────────────────────


@pytest.mark.parametrize("obs", [
    # data_unique_values
    "📊 Valeurs uniques de `nature` dans `marches.csv`\n"
    "Lignes scannées : 2975 | Valeurs distinctes : 4 | Retournées : 4\n"
    "\n| Valeur | Fréquence |\n|---|---|\n| Travaux | 1495 |\n| Services | 800 |",
    # data_aggregate
    "📈 Agrégation `sum(population)` sur `communes.csv`\n"
    "Group by : ['region']\n"
    "Lignes scannées : 35000 | Groupes : 13 | Retournés : 13\n"
    "\n| region | result | _count |\n|---|---|---|\n| IDF | 12000000 | 1300 |",
    # data_filter_rows
    "🔍 Filtre sur `communes.csv`\n"
    "Lignes scannées : 35000 | Matched : 1495 | Retournées : 10\n"
    "\n| code | commune | population |\n|---|---|---|\n| 75056 | Paris | 2102650 |",
    # data_profile_file
    "📊 Profil de `communes.csv`\n"
    "Format détecté : csv | Lignes : 35000 | Colonnes : 7\n"
    "Encoding : utf-8 | Séparateur : `;`\n"
    "**Colonnes** :\n- `code_insee` (text) [territoire] — 0% null",
    # datagouv_download_resource
    "✅ Téléchargé : `downloads/datagouv/x.csv` (10.5 KB)\n"
    "   chemin absolu : `C:\\...\\x.csv`\n"
    "   format détecté : `csv`\n"
    "   Hash MD5 : `abc123`",
])
def test_tabular_observations_detected(obs: str):
    """Ces observations sont des livrables exploitables → True."""
    assert _obs_looks_tabular(obs) is True


@pytest.mark.parametrize("non_tabular", [
    "Je vais essayer.",
    "OK.",
    "Erreur : fichier introuvable",
    "x" * 100,  # long mais pas de marqueurs
    "",
    None,
])
def test_non_tabular_observations_rejected(non_tabular):
    assert _obs_looks_tabular(non_tabular) is False


# ─── _synthesize_response_from_observation ──────────────────────────────


def test_synthesize_returns_obs_with_label():
    obs = (
        "📊 Valeurs uniques de `nature` dans `marches.csv`\n"
        "Lignes scannées : 2975 | Valeurs distinctes : 4 | Retournées : 4\n"
        "\n| Valeur | Fréquence |\n|---|---|\n"
        "| ERI | 1495 |\n| PPS | 800 |\n| RS2I | 350 |\n| Autres | 330 |"
    )
    out = _synthesize_response_from_observation(obs, "data_unique_values", "liste les natures")
    assert out is not None
    assert "data_unique_values" in out
    # Le tableau doit être présent dans la réponse
    assert "ERI" in out and "1495" in out
    assert "PPS" in out
    # Marquage explicite "fallback" / synthèse
    assert (
        "fallback" in out.lower()
        or "synthèse" in out.lower()
        or "réponse générée" in out.lower()
        or "dernière observation" in out.lower()
    )


def test_synthesize_truncates_long_observation():
    huge = "📊 Valeurs uniques de x\nLignes scannées : 1\n| A | B |\n|---|---|\n" + ("X" * 10000)
    out = _synthesize_response_from_observation(huge, "data_unique_values", "x")
    assert out is not None
    assert len(out) < 7000  # 6 KB body + label
    assert "tronqué" in out.lower()


def test_synthesize_returns_none_for_non_tabular():
    out = _synthesize_response_from_observation(
        "Je vais livrer la réponse.", "data_unique_values", "x"
    )
    assert out is None


def test_synthesize_returns_none_for_short_obs():
    out = _synthesize_response_from_observation("OK", "x", "y")
    assert out is None


# ─── Scénario exact des logs prod ───────────────────────────────────────


def test_exact_prod_scenario_unique_values():
    """Reproduit le cas UI : data_unique_values + FINAL intention.

    Sans le fix : utilisateur reçoit "Je vais livrer..." (chars=41).
    Avec le fix : utilisateur reçoit le tableau complet via fallback.
    """
    observation = (
        "📊 Valeurs uniques de `NATURE` dans `marches_publics.csv`\n"
        "Lignes scannées : 2975 | Valeurs distinctes : 4 | Retournées : 4\n"
        "\n| Valeur | Fréquence |\n|---|---|\n"
        "| ERI | 1495 |\n| PPS | 950 |\n| RS2I | 480 |\n| Maîtrise d'œuvre | 50 |"
    )
    intention = "Les données sont déjà récupérées, je livre la liste maintenant."

    # 1. L'observation est bien tabulaire
    assert _obs_looks_tabular(observation) is True
    # 2. La synthèse réussit
    out = _synthesize_response_from_observation(observation, "data_unique_values", "liste")
    assert out is not None
    assert "ERI" in out and "1495" in out
    # 3. Le LLM intention serait sinon laissé tel quel → guard requis
    from src.reasoning.react import _looks_like_intention
    assert _looks_like_intention(intention) is True
