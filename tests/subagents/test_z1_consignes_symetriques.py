"""LOT Z1 — une consigne qui s'arrête à mi-chemin ne protège personne.

Deux fois la même faute, à deux adresses : on enseigne une discipline à un agent
et on oublie son voisin immédiat.

**Le worker CSS.** Le rider frontend imposait la correspondance des noms entre le
HTML et le JS, et entre le JS et le backend. Jamais entre le HTML et le CSS. Le
worker JS ouvrait donc `index.html` pour relever les `id` — le tri de Cadran
fonctionne parfaitement. Le worker CSS ne l'ouvrait jamais :

    le HTML écrit        le CSS stylait
    .stat-card           .stats-tile
    .table-section       .table-wrap
    .nav-menu            .nav-links
    .gallery             .gallery-grid
    .reveal              .fade-in

    fibrance    3/15 classes stylées =  20 %   page cassée
    cadran      2/5                  =  40 %   tuiles sans fond ni bordure
    vigie      18/19                 =  95 %   ← un SEUL agent, en chat
    sentinelle  8/8                  = 100 %   ← un SEUL fichier

Ce n'est pas la séparation qui casse : c'est qu'un worker ne voit jamais le
fichier d'un autre. Là où un seul agent écrit tout, le même travail donne 95 %.

**Le lead.** Son prompt disait « Sinon, fais-le directement », ce qui se lit
« code à la main ». Deux décisions distinctes étaient confondues : DÉCOUPER en
sous-agents, et CODER avec l'outil de code. Sur HuffPack il a jugé — à raison —
qu'il n'y avait rien à découper, puis a produit 50 `read_file` et 5 éditions
manuelles ; le livrable est ressorti à 12 tests rouges. `delegate_task` était
pourtant dans ses mains : la consigne n'est injectée qu'aux workers contractuels.

⚠️ Steering, pas forçage. Le garde CODEAGENT-FIRST sort sur `not owned` AVANT de
chercher le marqueur, et le texte du lead ne contient même pas ce marqueur : le
lead est informé, jamais contraint.
"""
from __future__ import annotations

import inspect

import pytest

from src.subagents import mission_contract as MC
from src.subagents.runner import _LEAD_PREFIX


# ── le worker CSS sait désormais où regarder ────────────────────────────────

def test_the_frontend_rider_finally_names_the_css():
    rider = MC._RIDER_FRONTEND
    assert "STYLE" in rider
    assert "class" in rider.lower()
    # l'ancien contenu reste — on ajoute, on ne remplace pas
    assert "getElementById/querySelector du JS" in rider
    assert "routes exposées par le backend" in rider


def test_the_css_worker_is_told_to_read_the_html_first():
    """Ce que le worker JS fait spontanément parce qu'on le lui demande."""
    rider = MC._RIDER_FRONTEND.lower()
    assert "lis le fichier html" in rider or "lis le html" in rider
    assert "avant d'écrire du css" in rider


def test_the_exact_synonyms_of_the_runs_are_named():
    """Un exemple concret vaut mieux qu'une règle abstraite : ce sont les vrais
    couples relevés dans fibrance et cadran."""
    rider = MC._RIDER_FRONTEND
    assert ".stats-tile" in rider and ".stat-card" in rider


@pytest.mark.parametrize("fichier", ["style.css", "index.html", "app.js"])
def test_the_rider_still_fires_on_every_frontend_file(fichier):
    """Non-régression : le déclenchement ne change pas."""
    assert "FRONTEND" in MC._role_rider([fichier])


# ── le lead sait qu'il a le droit de déléguer son code ──────────────────────

def test_the_lead_is_told_that_not_splitting_is_not_hand_coding():
    assert "Ne PAS découper" in _LEAD_PREFIX
    assert "coder à la main" in _LEAD_PREFIX
    assert "delegate_task" in _LEAD_PREFIX
    assert "agent_type='code'" in _LEAD_PREFIX


def test_the_lead_is_now_required_to_delegate_code():
    """LOT Z1b — DÉCISION UTILISATEUR (2026-08-15) : « il faudrait qu'il utilise
    le CodeAgent si c'est du dev ».

    Z1 avait choisi le steering — informer sans contraindre, conformément au
    commentaire d'origine du fichier (« Steering, pas forçage »). Deux runs ont
    réfuté ce choix :

        HuffPack  50 read_file · 5 éditions à la main → 12 tests ROUGES
        Cadence   20 read_file · 6 éditions          → 14/15, 1 cas limite rouge

    Sur Cadence la consigne ÉTAIT injectée (vérifié au log) et `delegate_task`
    n'a jamais été appelé. C'est mot pour mot la leçon du LOT I côté workers :
    un prompt se contourne, un rail tient.
    """
    # Lot RF-6a : le corps de `_worker_codeagent_first_gate` a ete deplace vers
    # `mission_runtime.py` ; `ReActLoop` n'en garde qu'une coquille. Ce test
    # lit donc le source la ou il vit desormais — son intention est inchangee,
    # mot pour mot. Preuve COMPORTEMENTALE adossee : la matrice RF-6a compare
    # cette methode sur 3 jeux d'arguments x 22 scenarios = 66 valeurs, toutes
    # identiques avant/apres.
    from src.reasoning.mission_runtime import rf6a_worker_codeagent_first_gate

    src = inspect.getsource(rf6a_worker_codeagent_first_gate)
    assert "is_lead" in src, "le garde doit distinguer le lead"
    # Le lead n'a pas de fichiers assignés : on juge le fichier qu'il VISE.
    assert "cible" in src and "endswith(code_ext)" in src
    # Et le repli après tentative reste ouvert — jamais de blocage définitif.
    assert "attempted" in src


def test_only_code_files_are_gated_for_the_lead():
    """Une mission d'effets (mémo, rapport, CSV) ne doit rien voir changer."""
    # Lot RF-6a : le corps de `_worker_codeagent_first_gate` a ete deplace vers
    # `mission_runtime.py` ; `ReActLoop` n'en garde qu'une coquille. Ce test
    # lit donc le source la ou il vit desormais — son intention est inchangee,
    # mot pour mot. Preuve COMPORTEMENTALE adossee : la matrice RF-6a compare
    # cette methode sur 3 jeux d'arguments x 22 scenarios = 66 valeurs, toutes
    # identiques avant/apres.
    from src.reasoning.mission_runtime import rf6a_worker_codeagent_first_gate

    src = inspect.getsource(rf6a_worker_codeagent_first_gate)
    bloc = src.split("if is_lead:")[1].split("else:")[0]
    assert "return None" in bloc, "un fichier non-code doit sortir du garde"
    for ext in (".py", ".html", ".css", ".js"):
        assert ext in src


def test_the_lead_message_does_not_speak_of_contract_files():
    """Le lead n'a ni CONTRAT.md ni fichiers assignés : le message des workers
    lui serait incompréhensible."""
    # Lot RF-6a : le corps de `_worker_codeagent_first_gate` a ete deplace vers
    # `mission_runtime.py` ; `ReActLoop` n'en garde qu'une coquille. Ce test
    # lit donc le source la ou il vit desormais — son intention est inchangee,
    # mot pour mot. Preuve COMPORTEMENTALE adossee : la matrice RF-6a compare
    # cette methode sur 3 jeux d'arguments x 22 scenarios = 66 valeurs, toutes
    # identiques avant/apres.
    from src.reasoning.mission_runtime import rf6a_worker_codeagent_first_gate

    src = inspect.getsource(rf6a_worker_codeagent_first_gate)
    bloc = src.split("if is_lead:")[-1].split("return Observation")[1][:700]
    assert "CONTRAT.md" not in bloc
    assert "harnais" in bloc


def test_the_lead_still_knows_how_to_delegate_to_subagents():
    """Non-régression : le steering historique vers delegate_and_wait est intact."""
    assert "delegate_and_wait" in _LEAD_PREFIX
    assert "sous-tâches INDÉPENDANTES" in _LEAD_PREFIX
    assert "write_mission_contract" in _LEAD_PREFIX


def test_the_lead_prefix_stays_a_prefix():
    """`test_lead_profile` vérifie `task.startswith(_LEAD_PREFIX)` : le texte doit
    rester un préfixe se terminant par l'amorce de mission."""
    assert _LEAD_PREFIX.rstrip().endswith("Mission :")
