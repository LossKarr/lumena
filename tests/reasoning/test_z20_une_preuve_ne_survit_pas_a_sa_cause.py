"""LOT Z20 — une preuve ne survit pas à ce qui l'invalide.

Run « Créneau » (2026-08-17), auto-école, 5 workers. Déroulé exact, au log :

    05:53:58  browser_dom_state              → empreinte de référence F1
    05:54:18  clic « Enregistrer »           → SANS EFFET (le JS n'était pas
                                                initialisé), attente armée
    05:54:59  → eleves: []                     rien n'a été enregistré
              … diagnostic, correctif de lecons.js par le CodeAgent,
                publish_mission_workspace, browser_navigate …
    05:58:51  browser_dom_state              → empreinte ≠ F1
              [BROWSER INTERACTION PROOF] mutation + etat dynamique confirmes
    05:58:56  MISSION FINALIZE

La mission a conclu « vérifié » sans avoir enregistré un élève ni créé une leçon.
L'empreinte avait bien changé — **à cause de la réparation**, pas de l'action.
Et le drapeau part dans le dossier de la tâche (`browser_interaction_verified`),
donc `mission_status` répondait « navigateur = prouvé » pour toujours.

Cause structurelle, trouvée dans le code : `_advance_manual_browser_flow` vit
dans un bloc gardé par `if _is_browser_tool:`. Un `delegate_task`, un
`publish_mission_workspace`, un `edit_file` n'y entrent JAMAIS — l'attente ne
POUVAIT pas y être annulée.

La règle posée ici est celle que le ledger tient déjà pour la preuve voisine
(`has_fresh_browser_action` : « la preuve navigateur date-t-elle d'APRÈS la
dernière mutation de source ? »). Elle n'avait jamais été étendue à
l'interaction — dernier membre de la famille resté sur une déduction.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.reasoning.agent_execution_state import LoopGuards
from src.reasoning.react import (
    ReActLoop,
    _INTERACTION_PROOF_INVALIDATORS,
    _advance_manual_browser_flow,
    _browser_state_fingerprint,
)


# ── Bancs ────────────────────────────────────────────────────────────────────

_DOM_AVANT = (
    'Page: creneau\nURL: http://localhost:8081/lecons.html\n'
    'Interactive elements: 11\n[1] button "Enregistrer"\n'
    '[9] combobox\n[10] combobox\n[11] combobox'
)
# Ce que le DOM est devenu APRÈS le correctif — la différence ne doit rien
# à l'action de 05:54.
_DOM_APRES_REPARATION = (
    'Page: creneau\nURL: http://localhost:8081/lecons.html\n'
    'Interactive elements: 11\n[1] button "Enregistrer"\n'
    '[9] combobox\n'
    '[10] combobox options=["Sylvie Perrin", "Karim Ben Ali", "Thomas Roux"]\n'
    '[11] combobox options=["Clio 4 BF-204-QT"]'
)


def _agent():
    return SimpleNamespace(
        exec_state=SimpleNamespace(guards=LoopGuards()), task_id="task_creneau"
    )


def _invalide(agent, outil, succes=True):
    ReActLoop._invalidate_interaction_pending(agent, outil, succes)


def _jusqua_laction_ratee(agent):
    """Rejoue 05:53:58 → 05:54:18 : référence lue, puis clic sans effet."""
    _g = agent.exec_state.guards
    (
        _,
        _g.local_preview_last_read_fingerprint,
        _g.local_preview_mutation_since_read,
    ) = _advance_manual_browser_flow(
        "",
        mutation_pending=False,
        tool_name="browser_dom_state",
        observation=_DOM_AVANT,
    )
    (
        _,
        _g.local_preview_last_read_fingerprint,
        _g.local_preview_mutation_since_read,
    ) = _advance_manual_browser_flow(
        _g.local_preview_last_read_fingerprint,
        mutation_pending=_g.local_preview_mutation_since_read,
        tool_name="browser_click_index",
        observation='✅ Clic sur [1] button "Enregistrer" a (422, 584)',
    )
    return _g


def _relit_le_dom(_g):
    (
        proven,
        _g.local_preview_last_read_fingerprint,
        _g.local_preview_mutation_since_read,
    ) = _advance_manual_browser_flow(
        _g.local_preview_last_read_fingerprint,
        mutation_pending=_g.local_preview_mutation_since_read,
        tool_name="browser_dom_state",
        observation=_DOM_APRES_REPARATION,
    )
    return proven


# ── Le cas mesuré ────────────────────────────────────────────────────────────


def test_larmement_de_creneau_est_fidele():
    """Garde-fou du banc : sans Z20, la séquence prouvait bien (à tort)."""
    _g = _jusqua_laction_ratee(_agent())
    assert _g.local_preview_mutation_since_read is True
    assert _g.local_preview_last_read_fingerprint
    assert _relit_le_dom(_g) is True   # ← le bug, tel qu'il était


@pytest.mark.parametrize(
    "reparation",
    ["delegate_task", "publish_mission_workspace", "browser_navigate", "edit_file"],
)
def test_la_reparation_annule_lattente(reparation):
    """LE lot : entre le clic et la relecture, quelque chose a rebâti la page."""
    agent = _agent()
    _g = _jusqua_laction_ratee(agent)
    _invalide(agent, reparation)
    assert _g.local_preview_mutation_since_read is False
    assert _g.local_preview_last_read_fingerprint == ""
    assert _relit_le_dom(_g) is False


def test_la_sequence_complete_de_creneau_ne_prouve_plus_rien():
    """Les trois invalidateurs se sont enchaînés ce soir-là."""
    agent = _agent()
    _g = _jusqua_laction_ratee(agent)
    for outil in ("delegate_task", "publish_mission_workspace", "browser_navigate"):
        _invalide(agent, outil)
    assert _relit_le_dom(_g) is False


def test_apres_annulation_une_vraie_interaction_prouve_encore():
    """On n'interdit pas de prouver : on exige de REFAIRE sur la page réelle."""
    agent = _agent()
    _jusqua_laction_ratee(agent)
    _invalide(agent, "publish_mission_workspace")
    _g2 = _jusqua_laction_ratee(agent)          # relecture + action, à neuf
    assert _relit_le_dom(_g2) is True


# ── L'inertie : ce qui ne doit RIEN annuler ──────────────────────────────────


@pytest.mark.parametrize(
    "outil",
    ["browser_dom_state", "browser_screenshot", "browser_get_content",
     "browser_evaluate", "read_file", "mission_status", "browser_type_index",
     "browser_click_index", "serve_website"],
)
def test_un_outil_inoffensif_nannule_rien(outil):
    """Lire, cliquer, saisir, servir : rien de tout cela ne rebâtit la page.
    `serve_website` en particulier — il ouvre, il ne modifie pas."""
    agent = _agent()
    _g = _jusqua_laction_ratee(agent)
    _invalide(agent, outil)
    assert _g.local_preview_mutation_since_read is True
    assert _g.local_preview_last_read_fingerprint


def test_un_invalidateur_en_echec_nannule_rien():
    """Un `delegate_task` qui échoue n'a rien réparé — l'attente reste valide."""
    agent = _agent()
    _g = _jusqua_laction_ratee(agent)
    _invalide(agent, "delegate_task", succes=False)
    assert _g.local_preview_mutation_since_read is True


def test_une_preuve_acquise_nest_pas_reprise():
    """C'est l'ATTENTE qui a menti, pas la preuve. Reprendre une preuve déjà
    acquise ferait boucler les missions que Z8 oblige à republier."""
    agent = _agent()
    agent.exec_state.guards.local_preview_interaction_proven = True
    _jusqua_laction_ratee(agent)
    _invalide(agent, "publish_mission_workspace")
    assert agent.exec_state.guards.local_preview_interaction_proven is True


def test_sans_rien_en_cours_lappel_est_inerte():
    agent = _agent()
    _invalide(agent, "publish_mission_workspace")
    _g = agent.exec_state.guards
    assert _g.local_preview_mutation_since_read is False
    assert _g.local_preview_last_read_fingerprint == ""


@pytest.mark.parametrize("outil", [None, "", 42])
def test_rien_ne_leve_jamais(outil):
    """La boucle ReAct ne doit pas mourir d'un garde-fou."""
    _invalide(_agent(), outil)
    ReActLoop._invalidate_interaction_pending(object(), "edit_file", True)


# ── Le branchement — c'est LUI le lot ────────────────────────────────────────


_SRC = Path("src/reasoning/react.py").read_text(encoding="utf-8")


def test_lappel_est_hors_du_garde_browser():
    """LA cause structurelle : `_advance_manual_browser_flow` est enfermée dans
    `if _is_browser_tool:`. Un correctif, une délégation, une publication n'y
    entrent jamais. Si l'appel Z20 repassait dedans, le bug reviendrait intact."""
    i_appel = _SRC.index("self._invalidate_interaction_pending(")
    i_garde = _SRC.index('_is_browser_tool = (action.tool_name or "").startswith')
    assert i_appel < i_garde


def test_lappel_recoit_le_succes_de_lobservation():
    i = _SRC.index("self._invalidate_interaction_pending(")
    assert "observation.success" in _SRC[i : i + 200]


def test_les_familles_dinvalidateurs_sont_couvertes():
    """Quatre façons de rebâtir la page — les quatre étaient au log de Créneau."""
    for outil in ("edit_file", "str_replace", "delegate_task",
                  "publish_mission_workspace", "browser_navigate"):
        assert outil in _INTERACTION_PROOF_INVALIDATORS


def test_les_outils_de_lecture_ne_sont_pas_invalidateurs():
    for outil in ("browser_dom_state", "browser_get_content", "browser_evaluate",
                  "browser_screenshot", "read_file", "serve_website"):
        assert outil not in _INTERACTION_PROOF_INVALIDATORS


def test_lempreinte_ignore_toujours_lurl():
    """Non-régression : une soumission qui ne fait que recharger la même page en
    ajoutant ses valeurs à l'URL n'est pas une preuve (règle déjà en place)."""
    a = _browser_state_fingerprint("URL: http://x/p.html?nom=a\nPage: P\n[1] bouton")
    b = _browser_state_fingerprint("URL: http://x/p.html\nPage: P\n[1] bouton")
    assert a == b


def test_la_raison_du_lot_est_datee_dans_le_code():
    i = _SRC.index("LOT Z20 — une action en attente")
    entete = _SRC[i : i + 1800]
    assert "Créneau" in entete
    assert "has_fresh_browser_action" in entete
    assert "_is_browser_tool" in entete
