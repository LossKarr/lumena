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
    _obs_looks_like_test_result,
    _should_repair_incomplete_final,
    _synthesize_mission_response_from_evidence,
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


@pytest.mark.parametrize("summary", [
    "============================== 3 passed in 0.08s ==============================",
    "2 failed, 7 passed in 1.42s",
    "1 error in 0.20s",
])
def test_test_execution_observation_is_usable_fallback(summary: str):
    observation = f"pytest execution\n{summary}\nexit:0"
    assert _obs_looks_like_test_result(observation, "run_command") is True
    out = _synthesize_response_from_observation(
        observation, "run_command", "lance les tests puis conclus",
    )
    assert out is not None
    assert summary in out
    assert "run_command" in out


def test_test_result_text_without_execution_tool_is_not_proof():
    observation = "Le modele affirme que 3 passed in 0.08s, sans execution reelle."
    assert _obs_looks_like_test_result(observation, "read_file") is False
    assert _synthesize_response_from_observation(
        observation, "read_file", "verifie les tests",
    ) is None


def test_mission_fallback_combines_all_authoritative_proofs():
    evidence = [
        (
            "publish_mission_workspace",
            "📦 Livrable publié : 8 fichier(s) copiés vers `workspace/solarsip/` : "
            "app.py, rapport_solarsip.pdf, tests/test_app.py.\n"
            "➡️ Prochaine étape : lance pytest.",
            True,
        ),
        (
            "run_command",
            "pytest execution\n================ 10 passed in 0.24s ================",
            True,
        ),
        (
            "generate_studio_document",
            '{"filename":"rapport_solarsip.pdf","size":104963,"render_verified":true}',
            True,
        ),
        (
            "browser_verify_local_project",
            "## Runtime web verify: OK\nProject: C:/workspace/solarsip\n"
            "URL: http://localhost:8081\n- title: SolarSip",
            True,
        ),
        ("delegate_and_wait", "Délégation : 1/1 terminée(s).", True),
    ]

    out = _synthesize_mission_response_from_evidence(evidence)

    assert out is not None
    assert "8 fichier(s)" in out
    assert "10 passed in 0.24s" in out
    assert "rapport_solarsip.pdf — rendu vérifié — 104963 octets" in out
    assert "Runtime web verify: OK" in out
    assert "Délégation : 1/1" in out


def test_mission_fallback_never_claims_failed_browser_proof():
    out = _synthesize_mission_response_from_evidence([
        (
            "publish_mission_workspace",
            "📦 Livrable publié : app.py.",
            True,
        ),
        (
            "browser_verify_local_project",
            "## Runtime web verify: FAILED\nURL: http://localhost:8081",
            False,
        ),
    ])

    assert out is not None
    assert "Livraison" in out
    assert "Navigateur" not in out
    assert "Runtime web verify" not in out


def test_mission_fallback_requires_authoritative_success():
    assert _synthesize_mission_response_from_evidence([
        ("browser_navigate", "Navigué vers http://localhost:8081", True),
        ("run_command", "Je vais lancer les tests.", True),
        ("generate_studio_document", "Erreur de rendu", False),
    ]) is None


def test_exactly_grounded_document_final_skips_generic_length_repair():
    assert _should_repair_incomplete_final(
        stagnation_streak=0,
        plan_business_complete=False,
        document_free_grounded=True,
        looks_incomplete=True,
    ) is False


def test_unproved_incomplete_final_keeps_historical_repair():
    assert _should_repair_incomplete_final(
        stagnation_streak=0,
        plan_business_complete=False,
        document_free_grounded=False,
        looks_incomplete=True,
    ) is True


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
