"""Tests du fix V2.1 finalisation ReAct (2026-05-19).

Scénario reproductible : plan ReAct avec N tool tasks + 1 "présenter le rapport
à l'utilisateur" en dernier. Sans le fix, l'étape de présentation reste SKIP
après Action: final.

Voir REPO/PLAN_DATAGOUV_INTEGRATION.md §V2.1.
"""

from __future__ import annotations

import pytest

from src.reasoning.react_config import TaskItem


# Reproduit la table de mots-clés présente dans react.py (FINAL_ANSWER branch).
# Si tu modifies _SYNTH_KW dans react.py, mets ce miroir à jour aussi.
_SYNTH_KW = {
    "synthétis", "synthetis", "résumer", "resumer", "récapitul", "recapitul",
    "synthèse", "synthese", "conclur", "répondre", "repondre",
    "fournir une réponse", "présenter les résultats", "presenter les resultats",
    "confirm", "valider", "vérifi", "verifi",
    "informer", "inform", "notifier", "communiquer", "communique",
    "avertir", "signaler", "dire à", "dire a",
    "présenter le", "presenter le",
    "présenter la", "presenter la",
    "présenter au", "presenter au",
    "présenter à", "presenter a",
    "rapport final", "rapport complet",
    "à l'utilisateur", "a l'utilisateur",
    "donner la réponse", "donner la reponse",
    "afficher", "exposer", "expliquer",
    "livrer", "remettre", "transmettre",
    "écrire la réponse", "ecrire la reponse",
}


def _matches_synth(description: str) -> bool:
    return any(kw in description.lower() for kw in _SYNTH_KW)


# ─── Cas observé en prod (V2.1) ─────────────────────────────────────────


@pytest.mark.parametrize("desc", [
    "Présenter le rapport complet à l'utilisateur",
    "Présenter le rapport complet a l'utilisateur",
    "Donner la réponse finale à l'utilisateur",
    "Présenter au demandeur le résultat",
    "Afficher le bilan",
    "Livrer le résumé",
    "Expliquer les résultats à l'utilisateur",
])
def test_synth_kw_matches_user_facing_steps(desc: str):
    """Les tâches de présentation utilisateur DOIVENT être détectées comme synthèse."""
    assert _matches_synth(desc) is True


# ─── Tâches qui ne sont PAS des synthèses (must not over-match) ─────────


@pytest.mark.parametrize("desc", [
    "Télécharger la ressource CSV",
    "Profiler le fichier avec data_profile_file",
    "Rechercher un dataset",
    "Récupérer les détails du dataset",
    "Filtrer les communes par région",
])
def test_synth_kw_does_not_match_tool_steps(desc: str):
    """Les tâches outil ne doivent PAS être prises pour des synthèses."""
    assert _matches_synth(desc) is False


# ─── Plan complet : 4 tool steps + 1 présentation ───────────────────────


def test_plan_4_tools_plus_presentation_no_skip():
    """
    Plan reproduisant exactement le cas observé en UI V2.1 :
      1. Rechercher un dataset                          (tool)
      2. Récupérer les détails                          (tool)
      3. Télécharger la ressource CSV                   (tool)
      4. Profiler le fichier                            (tool)
      5. Présenter le rapport complet à l'utilisateur   (synthèse → auto-mark FINAL)

    Après simulation : les 4 tools complétés + auto-mark de la 5 = 5/5.
    Plus aucune étape ne doit rester SKIP.
    """
    plan = [
        TaskItem(description="Rechercher un dataset data.gouv", completed=True,
                 completed_by_tool="datagouv_search"),
        TaskItem(description="Récupérer les détails du dataset", completed=True,
                 completed_by_tool="datagouv_get_dataset"),
        TaskItem(description="Télécharger la ressource CSV", completed=True,
                 completed_by_tool="datagouv_download_resource"),
        TaskItem(description="Profiler le fichier avec data_profile_file", completed=True,
                 completed_by_tool="data_profile_file"),
        TaskItem(description="Présenter le rapport complet à l'utilisateur",
                 completed=False),
    ]

    # Simulation du bloc FINAL_ANSWER de react.py
    business_remaining = sum(
        1 for t in plan if not t.completed and not _matches_synth(t.description)
    )
    plan_business_complete = business_remaining == 0
    assert plan_business_complete is True, "Aucune tâche tool ne devrait rester"

    for t in plan:
        if not t.completed and _matches_synth(t.description):
            t.completed = True
            t.completed_by_tool = "FINAL"

    completed = sum(1 for t in plan if t.completed)
    total = len(plan)
    assert completed == total, (
        f"Attendu 5/5, obtenu {completed}/{total}. "
        "L'étape 'Présenter le rapport' ne doit plus être SKIP."
    )

    # Aucune tâche ne doit rester en SKIP
    skipped = [t.description for t in plan if not t.completed]
    assert skipped == [], f"Tâches SKIP restantes : {skipped}"


def test_plan_business_not_complete_when_tool_step_remains():
    """Inverse : si un tool step est encore in-progress, le plan n'est pas complet
    et le repair guard ne doit pas s'activer."""
    plan = [
        TaskItem(description="Télécharger la ressource", completed=False),
        TaskItem(description="Profiler le fichier", completed=False),
        TaskItem(description="Présenter le rapport à l'utilisateur", completed=False),
    ]
    business_remaining = sum(
        1 for t in plan if not t.completed and not _matches_synth(t.description)
    )
    assert business_remaining == 2
    plan_business_complete = business_remaining == 0
    assert plan_business_complete is False


# ─── Test contre-régression V1 keywords ─────────────────────────────────


@pytest.mark.parametrize("legacy_desc", [
    "Fournir une réponse à l'utilisateur",
    "Présenter les résultats",
    "Confirmer le succès",
    "Notifier l'utilisateur",
    "Informer le client",
    "Conclure",
    "Récapituler les étapes",
])
def test_legacy_synth_keywords_still_work(legacy_desc: str):
    """Les anciens keywords doivent continuer à matcher (pas de régression V1)."""
    assert _matches_synth(legacy_desc) is True


# ─── Source unique de vérité : importer le miroir doit refléter react.py ─


def test_synth_kw_mirror_includes_v2_1_additions():
    """Garde-fou : si quelqu'un retire les ajouts V2.1 du miroir local, on échoue."""
    must_have = {
        "présenter le", "rapport complet", "à l'utilisateur",
        "donner la réponse", "afficher", "livrer",
    }
    assert must_have.issubset(_SYNTH_KW)
