"""LOT Z23 — une impasse locale ferme une inspection, pas une mission.

Run « jeu 3D monde ouvert » (2026-08-19). Les deux dernières lignes du log :

    02:30:18  [LOCAL PREVIEW] escalade browser_evaluate (streak=3)
    02:30:23  ⛔ inspection sans preuve interactive (streak=4) — conclusion honnête

**5,6 secondes.** Le garde réclame UNE assertion `browser_evaluate`, la mission
la fournit, l'appel aboutit mais ne démontre rien → `return`. Le run entier
meurt à 18 minutes, `deadline_at: None` — rien ne la pressait. Reste sur le
carreau le README que l'objectif demandait (« index.html, styles, scripts,
**instructions** ») : il n'a jamais rejoint le livrable.

Le même dégât est déjà écrit en en-tête de `_local_preview_loop_decision`, pour
le run Cadran : « La mission a conclu à 7 min 19 sur 60, sans avoir vérifié le
thème persistant, le responsive ni le clavier. » Le lot R′ n'avait réparé qu'un
cas particulier (un appel mal formé ne consomme plus la tentative). Le fond —
un garde de sous-boucle navigateur qui tue le run entier — restait entier.

Or ce `return` n'apportait AUCUNE honnêteté. Le truth-lock bannérise
« interaction NON prouvée » à partir de l'OBJECTIF et du ledger, quel que soit
le texte du final (doctrine 2.13.A, `final_guards.py`). Il doublait un
mécanisme qui marche, en payant la complétude. C'est le motif des 40 lots
retourné une fois de plus : le fait est juste, mais il sert à FINIR le run au
lieu de MARQUER un constat.

Z23 : le constat se grave, ferme la relecture de cette preview (sans quoi on
retombe sur le rebouclage infini du run memo, raison d'être du garde), et la
mission continue son travail.

⚠️ Limite assumée et testée plus bas : la fermeture couvre aussi
`browser_evaluate`, donc la preuve manuelle « action puis relecture » n'est plus
atteignable sur CETTE page. Ce n'est pas une perte : avant Z23, le run était
mort à cet instant précis.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.reasoning.react import (
    BROWSER_ACTION_TOOLS,
    BROWSER_VISUAL_TOOLS,
    _LP_UNPROVABLE_CLOSED_TOOLS,
    ReActLoop,
    _local_preview_loop_decision,
)
from src.reasoning.agent_execution_state import LoopGuards

_PREVIEW = "http://127.0.0.1:8081/index.html"


# ── Bancs ────────────────────────────────────────────────────────────────────


def _agent(*, unprovable=True, url=_PREVIEW, page=_PREVIEW):
    """Un agent à l'état exact d'après l'arrêt du run 3D."""
    guards = LoopGuards()
    guards.local_preview_interaction_unprovable = unprovable
    return SimpleNamespace(
        exec_state=SimpleNamespace(guards=guards),
        _lp_unprovable_url=url,
        _last_browser_page_url=page,
    )


def _gate(agent, tool):
    return ReActLoop._local_preview_unprovable_gate(agent, tool)


# ── Le verrou ferme l'inspection ─────────────────────────────────────────────


@pytest.mark.parametrize("tool", sorted(_LP_UNPROVABLE_CLOSED_TOOLS))
def test_toute_relecture_de_cette_preview_est_close(tool):
    obs = _gate(_agent(), tool)
    assert obs is not None
    assert obs.success is False
    assert obs.origin == "local_preview_unprovable"


def test_le_refus_dit_que_la_mission_continue():
    """Sans cette phrase, le modèle lit un refus comme une fin de mission —
    c'est exactement ce que Z23 corrige."""
    contenu = _gate(_agent(), "browser_screenshot").content
    assert "PAS un echec de mission" in contenu
    assert "Termine-le" in contenu


def test_le_refus_interdit_de_reaffirmer_l_interactif():
    contenu = _gate(_agent(), "browser_evaluate").content
    assert "sans jamais affirmer l'interactif" in contenu


# ── Non-régression : le rebouclage memo reste impossible ─────────────────────


def test_tout_ce_qui_nourrit_le_compteur_est_ferme():
    """LE garde-fou anti-régression. `_local_preview_loop_decision` n'incrémente
    son streak que sur les outils visuels et `browser_evaluate` : si l'un d'eux
    restait ouvert, on aurait remplacé un arrêt brutal par la boucle infinie du
    run memo que ce garde existe pour casser."""
    nourrissent = set(BROWSER_VISUAL_TOOLS) | {"browser_evaluate"}
    assert nourrissent <= set(_LP_UNPROVABLE_CLOSED_TOOLS)


def test_aucun_outil_ferme_ne_peut_relancer_la_decision():
    """Vérification dynamique du même fait, via la fonction de décision elle-même."""
    for tool in _LP_UNPROVABLE_CLOSED_TOOLS:
        action, _, _ = _local_preview_loop_decision(
            True, tool, False, 4, True, tool_succeeded=True,
        )
        # Ces outils SAVENT déclencher un stop — d'où la nécessité de les fermer.
        assert action == "stop", tool


# ── Ce qui reste ouvert : le travail ─────────────────────────────────────────


@pytest.mark.parametrize("tool", sorted(BROWSER_ACTION_TOOLS - _LP_UNPROVABLE_CLOSED_TOOLS))
def test_les_outils_d_action_restent_ouverts(tool):
    """On ferme la relecture, pas le travail."""
    assert _gate(_agent(), tool) is None


@pytest.mark.parametrize("tool", ["write_file", "publish_mission_workspace",
                                  "run_command", "delegate_task", "read_file"])
def test_le_travail_hors_navigateur_reste_ouvert(tool):
    """Le README manquant du run 3D s'écrit avec `write_file` : si Z23 le
    bloquait, le lot n'aurait servi à rien."""
    assert _gate(_agent(), tool) is None


# ── Portée bornée ────────────────────────────────────────────────────────────


def test_le_constat_ne_deborde_pas_sur_une_autre_page():
    """Un constat porte sur LA preview jugée, pas sur tout ce que la mission
    ouvrira ensuite."""
    agent = _agent(page="https://exemple.fr/article")
    assert _gate(agent, "browser_screenshot") is None


def test_sans_constat_le_gate_est_inerte():
    assert _gate(_agent(unprovable=False), "browser_screenshot") is None


def test_le_gate_ne_leve_jamais():
    """Un garde-fou ne doit jamais tuer la boucle ReAct."""
    assert _gate(SimpleNamespace(), "browser_screenshot") is None
    assert _gate(object(), "browser_screenshot") is None
    casse = SimpleNamespace(exec_state=SimpleNamespace(guards=None))
    assert _gate(casse, "browser_screenshot") is None


def test_le_champ_existe_et_vaut_faux_par_defaut():
    assert LoopGuards().local_preview_interaction_unprovable is False


# ── L'honnêteté ne dépendait pas de ce `return` ──────────────────────────────


def test_le_truth_lock_bannerise_sans_ce_return():
    """L'argument central du lot, vérifié plutôt qu'affirmé : même avec un final
    qui ne dit rien de l'interactif, la bannière tombe. Le `return` supprimé ne
    protégeait donc rien."""
    from src.reasoning.final_guards import apply_mission_truth_lock

    texte, _info = apply_mission_truth_lock(
        "Le livrable est en place dans workspace/jeu-3d-monde-ouvert/.",
        has_green_test=True,
        web_deliverable=True,
        interaction_proven=False,
        interaction_required=True,
        objective_is_game=True,
    )
    assert "NON prouvée" in texte


# ── Le branchement dans le code ──────────────────────────────────────────────


_SRC = Path("src/reasoning/react.py").read_text(encoding="utf-8")
_BRANCHE = _SRC[_SRC.index('elif _cd_action == "stop":'):][:4200]


def test_la_branche_stop_ne_termine_plus_le_run_par_defaut():
    """LE lot : le `return` n'est plus atteignable au premier constat."""
    i_else = _BRANCHE.index("\n                        else:")
    i_return = _BRANCHE.index("return (")
    assert i_else < i_return, "le return doit être sous le filet, pas en chemin direct"


def test_la_branche_grave_le_constat_et_redirige():
    assert "local_preview_interaction_unprovable = True" in _BRANCHE
    assert "self._lp_unprovable_url = _lp_url_now" in _BRANCHE
    assert "_pending_loop_guidance" in _BRANCHE


def test_le_filet_de_terminaison_est_borne_a_la_meme_page():
    """Sinon une seconde preview locale tuerait la mission — le bug d'origine
    déplacé, pas corrigé."""
    assert '_lp_unprovable_url", "") or "") == _lp_url_now' in _BRANCHE


def test_le_gate_est_branche_avant_l_execution():
    assert "self._local_preview_unprovable_gate(action.tool_name)" in _SRC
    assert "elif _lpu_obs is not None:" in _SRC


def test_la_raison_du_lot_est_datee_dans_le_code():
    entete = _SRC[_SRC.index("LOT Z23 — l'inspection est close"):][:2200]
    assert "jeu 3D monde ouvert" in entete
    assert "5,6 secondes" in entete
    assert "2.13.A" in entete
