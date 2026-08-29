"""RF-7a — matrice de VALEURS DE RETOUR du runtime navigateur (lectrices).

Lot RF-7a du plan `plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md`.
Ecrit AVANT l'extraction ; la reference est capturee sur le code d'origine.

Douze methodes, 330 lignes, **zero mutation**.

--- Pourquoi RF-7 s'arrete la, et pourquoi c'est mesure ---

Le plan decrit RF-7 comme « proprietes d'etat ET gates » vers
`browser_runtime.py`. La mesure dit autre chose :

    16 attributs d'etat navigateur, ecrits par : `_run_internal` — les 16.
    Zero exception.

L'etat navigateur n'est donc pas extractible sans ouvrir `_run_internal`
(perimetre RF-9, bloque par le §18). Ce qui RESTE extractible, ce sont les
LECTRICES : douze methodes qui consomment cet etat sans jamais l'ecrire.

C'est exactement la strategie que le plan prescrit pour RF-7 :
« extraire d'abord les decisions a partir d'un snapshot immutable ; appliquer
les mutations dans react.py ». Ici il n'y a meme aucune mutation a laisser.

--- Quatre de ces douze sont des GARDES ---

L'invariant 7 exige qu'ils restent fail-closed. La matrice enregistre donc,
pour chaque garde, s'il REFUSE (motif non vide) ou LAISSE PASSER (chaine
vide), et un test exige que les deux familles soient peuplees.

--- Le motif des deux formes, SIXIEME occurrence ---

`_is_mission_run` est lu par `getattr(self, "_is_mission_run", False)` dans
`_post_delegate_web_verify_allowed` et par `self._is_mission_run` (qui LEVE)
dans `_current_browser_proof`. Une seule forme ne peut pas rendre les deux.
"""
from __future__ import annotations

import ast
import copy
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

import pytest

RACINE = Path(__file__).resolve().parents[2]
REACT = RACINE / "src" / "reasoning" / "react.py"
NOUVEAU = RACINE / "src" / "reasoning" / "browser_runtime.py"


# Surface EXACTE du ledger appelee par le sous-lot, mesuree par AST sur les
# 12 methodes ET leurs 5 sortants. Un test verifie que cette liste ne derive pas.
#
# POURQUOI C'EST CRITIQUE : 15 des 18 methodes concernees sont enveloppees dans
# un `except`. Un stub incomplet ne plante pas — il fait rendre "" au garde, ce
# qui ressemble a un « pas de refus » legitime. La premiere version de cette
# matrice n'avait pas `written_basenames` : ZERO refus sur 47 cas, et rien ne
# le signalait. C'est le motif du chantier, dans l'outillage de preuve lui-meme.
SURFACE_LEDGER = (
    "has_browser_action", "has_fresh_browser_action", "has_source_mutation",
    "has_successful_action", "written_basenames",
)


class _Ledger:
    """Ledger couvrant la surface COMPLETE mesuree ci-dessus."""

    def __init__(self, browser=False, fresh=False, ecrits=(),
                 mutation_source=False, actions_reussies=()):
        self._browser = browser
        self._fresh = fresh
        self._ecrits = tuple(x.lower() for x in ecrits)
        self._mutation_source = mutation_source
        # DEFAUT : aucune action reussie. Une premiere version rendait True
        # inconditionnellement — le code croyait alors que
        # `browser_verify_local_project` avait reussi, et
        # `_truth_lock_interaction_proven` rendait True partout. Un stub qui
        # repond « oui » a une question qu'on ne lui a pas posee est aussi
        # dangereux qu'un stub incomplet.
        self._actions_reussies = frozenset(actions_reussies)

    def has_browser_action(self):
        return self._browser

    def has_fresh_browser_action(self):
        return self._fresh

    def has_source_mutation(self):
        return self._mutation_source

    def has_successful_action(self, nom=None, *a, **k):
        return nom in self._actions_reussies

    def written_basenames(self):
        return self._ecrits


def _obs(content="", success=True):
    return SimpleNamespace(content=content, success=success)


def _etape(tool_name, tool_args=None, observation=None):
    return SimpleNamespace(
        action=SimpleNamespace(tool_name=tool_name, tool_args=tool_args or {}),
        observation=observation,
    )


DOM_RICHE = (
    "[1] heading 'Accueil'\n[2] link 'Contact'\n[3] textbox 'Email'\n"
    "[4] button 'Envoyer'\n[5] link 'Tarifs'"
)

HIST_VIDE: List[Any] = []
HIST_UNE_PAGE = [
    _etape("browser_navigate", {"url": "http://127.0.0.1:8245/index.html"},
           _obs(DOM_RICHE, True)),
    _etape("browser_snapshot", {}, _obs(DOM_RICHE, True)),
]
HIST_DEUX_PAGES = HIST_UNE_PAGE + [
    _etape("browser_navigate", {"url": "http://127.0.0.1:8245/contact.html"},
           _obs("[1] heading 'Contact'\n[2] textbox 'Email'", True)),
]
HIST_ECHEC = [
    _etape("browser_navigate", {"url": "http://127.0.0.1:8245/x.html"},
           _obs("❌ ERR_CONNECTION_REFUSED", False)),
]
HIST_INTERACTION = HIST_UNE_PAGE + [
    _etape("browser_type_index", {"index": "3", "text": "a@b.fr"}, _obs("✅ saisi", True)),
    _etape("browser_click_index", {"index": "4"}, _obs("✅ Clic — formulaire soumis", True)),
]
HIST_CODE = [
    _etape("write_file", {"path": "app.py"}, _obs("✅ Fichier cree : app.py", True)),
]


CAS: Dict[str, Tuple[str, tuple, Dict[str, Any]]] = {}


def _c(nom, methode, args=(), **etat):
    CAS[nom] = (methode, args, etat)


# ── _browser_verify_intent (@staticmethod, pur) ──────────────────────────────
_c("int_01_navigateur_verif", "_browser_verify_intent", ("verifie au navigateur",))
_c("int_02_browser_test", "_browser_verify_intent", ("test the browser flow",))
_c("int_03_sans_verbe", "_browser_verify_intent", ("ouvre le navigateur",))
_c("int_04_sans_navigateur", "_browser_verify_intent", ("verifie les tests",))
_c("int_05_vide", "_browser_verify_intent", ("",))
_c("int_06_none", "_browser_verify_intent", (None,))
_c("int_07_naviguer_controle", "_browser_verify_intent", ("naviguer et controler la page",))

# ── _post_delegate_web_verify_allowed ────────────────────────────────────────
_c("pdv_01_hors_mission", "_post_delegate_web_verify_allowed", ())
_c("pdv_02_en_mission", "_post_delegate_web_verify_allowed", (),
   task_id="t-1", task_orchestrator="MISSION")

# ── _current_browser_proof ───────────────────────────────────────────────────
_c("prf_01_aucune_action", "_current_browser_proof", (),
   execution_ledger=_Ledger(browser=False, fresh=False))
_c("prf_02_action_historique", "_current_browser_proof", (),
   execution_ledger=_Ledger(browser=True, fresh=False))
_c("prf_03_mission_stale", "_current_browser_proof", (),
   execution_ledger=_Ledger(browser=True, fresh=False),
   task_id="t-1", task_orchestrator="MISSION")
_c("prf_04_mission_fraiche", "_current_browser_proof", (),
   execution_ledger=_Ledger(browser=True, fresh=True),
   task_id="t-1", task_orchestrator="MISSION")
_c("prf_05_ledger_absent", "_current_browser_proof", ())

# ── _browser_content_seen ────────────────────────────────────────────────────
_c("vu_01_vide", "_browser_content_seen", (), history=HIST_VIDE)
_c("vu_02_une_page", "_browser_content_seen", (), history=HIST_UNE_PAGE)
_c("vu_03_echec", "_browser_content_seen", (), history=HIST_ECHEC)
_c("vu_04_sans_navigateur", "_browser_content_seen", (), history=HIST_CODE)

# ── _browser_runtime_failed / verified pour le truth-lock ────────────────────
_c("rtf_01_vierge", "_browser_runtime_failed_for_truth_lock", ())
_c("rtf_02_echec_marque", "_browser_runtime_failed_for_truth_lock", (),
   _web_runtime_failed=True)
_c("rtv_01_vierge", "_browser_runtime_verified_for_truth_lock", ())
_c("rtv_02_verifie_marque", "_browser_runtime_verified_for_truth_lock", (),
   _web_runtime_verified=True)

# ── _truth_lock_interaction_proven ───────────────────────────────────────────
_c("tli_01_sans_interaction", "_truth_lock_interaction_proven", (),
   history=HIST_UNE_PAGE, execution_ledger=_Ledger())
_c("tli_02_avec_interaction", "_truth_lock_interaction_proven", (),
   history=HIST_INTERACTION, execution_ledger=_Ledger(browser=True))
_c("tli_03_historique_vide", "_truth_lock_interaction_proven", (),
   history=HIST_VIDE, execution_ledger=_Ledger())

# ── _pages_never_opened_reason — GARDE Z11 ───────────────────────────────────
_c("z11_01_vide", "_pages_never_opened_reason", (), history=HIST_VIDE)
_c("z11_02_une_page", "_pages_never_opened_reason", (), history=HIST_UNE_PAGE)
_c("z11_03_deux_pages", "_pages_never_opened_reason", (), history=HIST_DEUX_PAGES)
_c("z11_04_sans_navigateur", "_pages_never_opened_reason", (), history=HIST_CODE)

# ── _finalize_browser_gate_pending — GARDE ───────────────────────────────────
_c("fbg_01_sans_preuve", "_finalize_browser_gate_pending",
   ("frontend fonctionnel", "construis le site et verifie au navigateur"),
   history=HIST_VIDE, execution_ledger=_Ledger())
_c("fbg_02_avec_preuve", "_finalize_browser_gate_pending",
   ("frontend fonctionnel", "construis le site et verifie au navigateur"),
   history=HIST_UNE_PAGE, execution_ledger=_Ledger(browser=True))
_c("fbg_03_sans_intention", "_finalize_browser_gate_pending",
   ("module livre", "ecris un module python"),
   history=HIST_VIDE, execution_ledger=_Ledger())
_c("fbg_04_deja_tire", "_finalize_browser_gate_pending",
   ("frontend fonctionnel", "verifie au navigateur"),
   history=HIST_VIDE, execution_ledger=_Ledger(), _browser_gate_shots=1)

# ── _finalize_interaction_gate_pending — GARDE ───────────────────────────────
_c("fig_01_sans_interaction", "_finalize_interaction_gate_pending",
   ("formulaire teste", "remplis le formulaire et soumets-le au navigateur"),
   history=HIST_UNE_PAGE, execution_ledger=_Ledger(browser=True))
_c("fig_02_avec_interaction", "_finalize_interaction_gate_pending",
   ("formulaire teste", "remplis le formulaire et soumets-le au navigateur"),
   history=HIST_INTERACTION, execution_ledger=_Ledger(browser=True))
_c("fig_03_sans_demande", "_finalize_interaction_gate_pending",
   ("module livre", "ecris un module python"),
   history=HIST_VIDE, execution_ledger=_Ledger())
_c("fig_04_deja_tire", "_finalize_interaction_gate_pending",
   ("formulaire teste", "remplis le formulaire et soumets-le"),
   history=HIST_UNE_PAGE, execution_ledger=_Ledger(browser=True),
   _interaction_gate_shots=1)

# ── _local_preview_unprovable_gate — GARDE ───────────────────────────────────
_c("lpu_01_outil_ferme", "_local_preview_unprovable_gate", ("browser_snapshot",),
   history=HIST_UNE_PAGE)
_c("lpu_02_outil_ouvert", "_local_preview_unprovable_gate", ("write_file",),
   history=HIST_UNE_PAGE)
_c("lpu_03_historique_vide", "_local_preview_unprovable_gate", ("browser_snapshot",),
   history=HIST_VIDE)
_c("lpu_04_url_marquee", "_local_preview_unprovable_gate", ("browser_snapshot",),
   history=HIST_UNE_PAGE, _lp_unprovable_url="http://127.0.0.1:8245/index.html",
   _last_browser_page_url="http://127.0.0.1:8245/index.html")
_c("lpu_05_outil_vide", "_local_preview_unprovable_gate", ("",), history=HIST_UNE_PAGE)

# ── _mission_browser_verify_pending — GARDE, le plus compose ─────────────────
_c("mbv_01_hors_mission", "_mission_browser_verify_pending",
   ("frontend fonctionnel", "construis le site et verifie au navigateur"),
   history=HIST_VIDE, execution_ledger=_Ledger())
_c("mbv_02_avec_preuve", "_mission_browser_verify_pending",
   ("frontend fonctionnel", "construis le site et verifie au navigateur"),
   history=HIST_UNE_PAGE, execution_ledger=_Ledger(browser=True))
_c("mbv_03_sans_intention", "_mission_browser_verify_pending",
   ("module livre", "ecris un module python"),
   history=HIST_CODE, execution_ledger=_Ledger())
_c("mbv_04_interaction_demandee", "_mission_browser_verify_pending",
   ("formulaire teste", "remplis le formulaire au navigateur et soumets-le"),
   history=HIST_UNE_PAGE, execution_ledger=_Ledger(browser=True))
_c("mbv_05_interaction_prouvee", "_mission_browser_verify_pending",
   ("formulaire teste", "remplis le formulaire au navigateur et soumets-le"),
   history=HIST_INTERACTION, execution_ledger=_Ledger(browser=True))


# ── Les gardes ne peuvent tirer que si un LIVRABLE WEB existe ────────────────
#
# `_mission_web_present_for_gate` interroge `execution_ledger.written_basenames()`
# et cherche .html/.htm/.js. Sans cela, tous les gardes sortent en "" — ce qui
# n'est pas un refus, c'est une absence de sujet.

WEB_ECRIT = _Ledger(ecrits=("index.html", "contact.html", "app.js"))
WEB_ECRIT_VU = _Ledger(browser=True, ecrits=("index.html", "contact.html"))
# Une preview REELLEMENT verifiee : `browser_verify_local_project` a reussi.
WEB_VERIFIE = _Ledger(browser=True, ecrits=("index.html",),
                      actions_reussies=("browser_verify_local_project",))

_c("gde_01_web_ecrit_jamais_ouvert", "_mission_browser_verify_pending",
   ("frontend fonctionnel ✅", "construis le site et verifie au navigateur"),
   history=HIST_VIDE, execution_ledger=WEB_ECRIT)
_c("gde_02_web_ecrit_une_page_vue", "_mission_browser_verify_pending",
   ("frontend fonctionnel ✅", "construis le site et verifie au navigateur"),
   history=HIST_UNE_PAGE, execution_ledger=WEB_ECRIT_VU)
_c("gde_03_web_ecrit_deux_pages_vues", "_mission_browser_verify_pending",
   ("frontend fonctionnel ✅", "construis le site et verifie au navigateur"),
   history=HIST_DEUX_PAGES, execution_ledger=WEB_ECRIT_VU)
_c("gde_04_finalize_web_sans_preuve", "_finalize_browser_gate_pending",
   ("site livre", "construis le site et verifie au navigateur"),
   history=HIST_VIDE, execution_ledger=WEB_ECRIT)
_c("gde_05_finalize_web_avec_preuve", "_finalize_browser_gate_pending",
   ("site livre", "construis le site et verifie au navigateur"),
   history=HIST_UNE_PAGE, execution_ledger=WEB_ECRIT_VU)
_c("gde_06_z11_web_ecrit_rien_ouvert", "_pages_never_opened_reason", (),
   history=HIST_VIDE, execution_ledger=WEB_ECRIT)
_c("gde_07_z11_web_une_page_sur_deux", "_pages_never_opened_reason", (),
   history=HIST_UNE_PAGE, execution_ledger=WEB_ECRIT_VU)
_c("gde_08_interaction_web_sans_clic", "_finalize_interaction_gate_pending",
   ("formulaire teste", "remplis le formulaire au navigateur et soumets-le"),
   history=HIST_UNE_PAGE, execution_ledger=WEB_ECRIT_VU)
_c("gde_09_interaction_web_avec_clic", "_finalize_interaction_gate_pending",
   ("formulaire teste", "remplis le formulaire au navigateur et soumets-le"),
   history=HIST_INTERACTION, execution_ledger=WEB_ECRIT_VU)


# ══════════════════════════════════════════════════════════════════════════
#  Les TROIS methodes muettes — et pourquoi elles l'etaient
# ══════════════════════════════════════════════════════════════════════════
#
# La premiere version de cette matrice laissait trois methodes sur douze avec
# UNE SEULE issue. Trois causes distinctes, toutes dans les scenarios :
#
#   `_truth_lock_interaction_proven`     -> `exec_state.guards` jamais fourni
#   `_local_preview_unprovable_gate`     -> `browser_snapshot` n'est PAS dans
#                                           `_LP_UNPROVABLE_CLOSED_TOOLS`
#                                           (il faut `browser_evaluate`)
#   `_finalize_interaction_gate_pending` -> la formulation ne declenchait pas
#                                           `objective_requires_web_interaction_proof`
#
# Formulation mesuree qui declenche l'interaction :
#   "fais un site avec un formulaire, remplis-le et clique sur envoyer pour verifier"

Q_INTERACTION = ("fais un site avec un formulaire, remplis-le et clique sur "
                 "envoyer pour verifier")
URL_PREVIEW = "http://127.0.0.1:8245/index.html"


def _exec_state(**drapeaux):
    """`exec_state.guards` — les trois methodes muettes le lisent toutes."""
    return SimpleNamespace(guards=SimpleNamespace(
        local_preview_interaction_proven=drapeaux.get("interaction_prouvee", False),
        local_preview_interaction_unprovable=drapeaux.get("preview_indemontrable", False),
    ))


# ── _truth_lock_interaction_proven : l'issue VRAIE ───────────────────────────
_c("tli_04_assertion_locale", "_truth_lock_interaction_proven", (),
   history=HIST_INTERACTION, execution_ledger=WEB_ECRIT_VU,
   exec_state=_exec_state(interaction_prouvee=True))
_c("tli_05_assertion_locale_hors_web", "_truth_lock_interaction_proven", (),
   history=HIST_VIDE, execution_ledger=_Ledger(),
   exec_state=_exec_state(interaction_prouvee=True))

# ── _local_preview_unprovable_gate : l'issue REFUS ───────────────────────────
_c("lpu_06_ferme_outil_ferme", "_local_preview_unprovable_gate", ("browser_evaluate",),
   history=HIST_UNE_PAGE, execution_ledger=WEB_ECRIT_VU,
   exec_state=_exec_state(preview_indemontrable=True),
   _last_browser_page_url=URL_PREVIEW, _lp_unprovable_url=URL_PREVIEW)
_c("lpu_07_ferme_mais_autre_page", "_local_preview_unprovable_gate", ("browser_evaluate",),
   history=HIST_UNE_PAGE, execution_ledger=WEB_ECRIT_VU,
   exec_state=_exec_state(preview_indemontrable=True),
   _last_browser_page_url="http://127.0.0.1:8245/autre.html",
   _lp_unprovable_url=URL_PREVIEW)
_c("lpu_08_ferme_mais_outil_ouvert", "_local_preview_unprovable_gate", ("write_file",),
   history=HIST_UNE_PAGE, execution_ledger=WEB_ECRIT_VU,
   exec_state=_exec_state(preview_indemontrable=True),
   _last_browser_page_url=URL_PREVIEW, _lp_unprovable_url=URL_PREVIEW)
_c("lpu_09_drapeau_absent", "_local_preview_unprovable_gate", ("browser_evaluate",),
   history=HIST_UNE_PAGE, execution_ledger=WEB_ECRIT_VU,
   exec_state=_exec_state(),
   _last_browser_page_url=URL_PREVIEW, _lp_unprovable_url=URL_PREVIEW)

# ── _finalize_interaction_gate_pending : l'issue REFUS ───────────────────────
_c("fig_05_interaction_demandee_non_prouvee", "_finalize_interaction_gate_pending",
   ("formulaire teste ✅", Q_INTERACTION),
   history=HIST_UNE_PAGE, execution_ledger=WEB_ECRIT_VU,
   _original_query=Q_INTERACTION, exec_state=_exec_state())
_c("fig_06_interaction_prouvee", "_finalize_interaction_gate_pending",
   ("formulaire teste ✅", Q_INTERACTION),
   history=HIST_INTERACTION, execution_ledger=WEB_ECRIT_VU,
   _original_query=Q_INTERACTION, exec_state=_exec_state(interaction_prouvee=True))
_c("fig_07_interaction_sans_page_ouverte", "_finalize_interaction_gate_pending",
   ("formulaire teste", Q_INTERACTION),
   history=HIST_VIDE, execution_ledger=WEB_ECRIT,
   _original_query=Q_INTERACTION, exec_state=_exec_state())

# ── _truth_lock_interaction_flag / game_flag exerces au passage ──────────────
_c("mbv_06_interaction_demandee", "_mission_browser_verify_pending",
   ("formulaire teste ✅", Q_INTERACTION),
   history=HIST_UNE_PAGE, execution_ledger=WEB_ECRIT_VU,
   _original_query=Q_INTERACTION, exec_state=_exec_state())
_c("mbv_07_interaction_prouvee", "_mission_browser_verify_pending",
   ("formulaire teste ✅", Q_INTERACTION),
   history=HIST_INTERACTION, execution_ledger=WEB_ECRIT_VU,
   _original_query=Q_INTERACTION, exec_state=_exec_state(interaction_prouvee=True))


def instantane(nom: str) -> Dict[str, Any]:
    """Applique le scenario et retourne la VALEUR DE RETOUR normalisee.

    FAIL-CLOSED : aucune exception n'est rattrapee.

    Les boucles sont construites par `object.__new__(ReActLoop)`, comme le fait
    le depot : les attributs non fournis restent ABSENTS, et c'est ce qui
    distingue les deux formes de lecture de `_is_mission_run`.
    """
    import inspect as _inspect

    from src.reasoning.react import ReActLoop

    methode, args, etat_source = CAS[nom]
    etat = dict(etat_source)

    est_statique = isinstance(
        _inspect.getattr_static(ReActLoop, methode), staticmethod)

    boucle = object.__new__(ReActLoop)
    # Socle minimal. `_is_worker_run` descend jusqu'a `_mission_allowed_files_meta`
    # qui lit `self.task_id` EN DIRECT : absent, il leve. Les poser a None donne
    # « hors mission » proprement, et les scenarios mission les surchargent.
    boucle.task_id = None
    boucle.task_orchestrator = None
    boucle.history = []
    for cle, valeur in etat.items():
        if cle == "task_orchestrator" and valeur == "MISSION":
            valeur = SimpleNamespace(
                get_task=lambda _t: {"metadata": {"kind": "mission"}})
        setattr(boucle, cle, copy.deepcopy(valeur) if cle == "history" else valeur)

    fonction = getattr(ReActLoop, methode)
    resultat = fonction(*args) if est_statique else fonction(boucle, *args)
    if hasattr(resultat, "__next__"):
        resultat = tuple(resultat)

    # Pour les quatre gardes, on enregistre explicitement la DECISION.
    GARDES = {
        "_pages_never_opened_reason", "_finalize_browser_gate_pending",
        "_finalize_interaction_gate_pending", "_mission_browser_verify_pending",
        "_local_preview_unprovable_gate",
    }
    decision = ""
    if methode in GARDES:
        decision = "REFUSE" if (resultat not in (None, "", False)) else "PASSE"

    return {"decision": decision, "retour": _normaliser(repr(resultat))[:1200]}


# L'`Observation` rendue par `_local_preview_unprovable_gate` porte un
# `timestamp=datetime.datetime(...)` qui change a CHAQUE execution. Hacher le
# repr brut donnerait une matrice qui derive toute seule — exactement le
# defaut de la matrice de prompt en RF-3, corrige la par normalisation.
#
# On neutralise l'horodatage, et RIEN d'autre : tout le reste de l'Observation
# (contenu, success, origin, sub_results) reste compare a l'octet pres.
_HORODATAGE = re.compile(r"datetime\.datetime\([^)]*\)")


def _normaliser(texte: str) -> str:
    return _HORODATAGE.sub("<HORODATAGE>", texte)


# ════════════════════════════════════════════════════════════════════════
#  La reference, capturee AVANT extraction
# ════════════════════════════════════════════════════════════════════════

BASELINE = {
    "fbg_01_sans_preuve": {
        "decision": "PASSE",
        "retour": "''"
    },
    "fbg_02_avec_preuve": {
        "decision": "PASSE",
        "retour": "''"
    },
    "fbg_03_sans_intention": {
        "decision": "PASSE",
        "retour": "''"
    },
    "fbg_04_deja_tire": {
        "decision": "PASSE",
        "retour": "''"
    },
    "fig_01_sans_interaction": {
        "decision": "PASSE",
        "retour": "''"
    },
    "fig_02_avec_interaction": {
        "decision": "PASSE",
        "retour": "''"
    },
    "fig_03_sans_demande": {
        "decision": "PASSE",
        "retour": "''"
    },
    "fig_04_deja_tire": {
        "decision": "PASSE",
        "retour": "''"
    },
    "fig_05_interaction_demandee_non_prouvee": {
        "decision": "REFUSE",
        "retour": "'page(s) jamais ouverte(s) au navigateur : `contact.html` — une page que personne ne regarde est une page que personne ne corrige'"
    },
    "fig_06_interaction_prouvee": {
        "decision": "PASSE",
        "retour": "''"
    },
    "fig_07_interaction_sans_page_ouverte": {
        "decision": "PASSE",
        "retour": "''"
    },
    "gde_01_web_ecrit_jamais_ouvert": {
        "decision": "REFUSE",
        "retour": "'page(s) jamais ouverte(s) au navigateur : `contact.html`, `index.html` — une page que personne ne regarde est une page que personne ne corrige'"
    },
    "gde_02_web_ecrit_une_page_vue": {
        "decision": "REFUSE",
        "retour": "'page(s) jamais ouverte(s) au navigateur : `contact.html` — une page que personne ne regarde est une page que personne ne corrige'"
    },
    "gde_03_web_ecrit_deux_pages_vues": {
        "decision": "PASSE",
        "retour": "''"
    },
    "gde_04_finalize_web_sans_preuve": {
        "decision": "REFUSE",
        "retour": "'page(s) jamais ouverte(s) au navigateur : `contact.html`, `index.html` — une page que personne ne regarde est une page que personne ne corrige'"
    },
    "gde_05_finalize_web_avec_preuve": {
        "decision": "REFUSE",
        "retour": "'page(s) jamais ouverte(s) au navigateur : `contact.html` — une page que personne ne regarde est une page que personne ne corrige'"
    },
    "gde_06_z11_web_ecrit_rien_ouvert": {
        "decision": "REFUSE",
        "retour": "'page(s) jamais ouverte(s) au navigateur : `contact.html`, `index.html` — une page que personne ne regarde est une page que personne ne corrige'"
    },
    "gde_07_z11_web_une_page_sur_deux": {
        "decision": "REFUSE",
        "retour": "'page(s) jamais ouverte(s) au navigateur : `contact.html` — une page que personne ne regarde est une page que personne ne corrige'"
    },
    "gde_08_interaction_web_sans_clic": {
        "decision": "PASSE",
        "retour": "''"
    },
    "gde_09_interaction_web_avec_clic": {
        "decision": "PASSE",
        "retour": "''"
    },
    "int_01_navigateur_verif": {
        "decision": "",
        "retour": "True"
    },
    "int_02_browser_test": {
        "decision": "",
        "retour": "True"
    },
    "int_03_sans_verbe": {
        "decision": "",
        "retour": "False"
    },
    "int_04_sans_navigateur": {
        "decision": "",
        "retour": "False"
    },
    "int_05_vide": {
        "decision": "",
        "retour": "False"
    },
    "int_06_none": {
        "decision": "",
        "retour": "False"
    },
    "int_07_naviguer_controle": {
        "decision": "",
        "retour": "True"
    },
    "lpu_01_outil_ferme": {
        "decision": "PASSE",
        "retour": "None"
    },
    "lpu_02_outil_ouvert": {
        "decision": "PASSE",
        "retour": "None"
    },
    "lpu_03_historique_vide": {
        "decision": "PASSE",
        "retour": "None"
    },
    "lpu_04_url_marquee": {
        "decision": "PASSE",
        "retour": "None"
    },
    "lpu_05_outil_vide": {
        "decision": "PASSE",
        "retour": "None"
    },
    "lpu_06_ferme_outil_ferme": {
        "decision": "REFUSE",
        "retour": "Observation(content=\"⛔ Constat ACQUIS sur cette preview locale : la validation interactive n'y est pas prouvable (assertion deja tentee, sans resultat probant). Ce constat est definitif pour ce run — l'inspecter a nouveau ne le changera pas.\\n\\nCe n'est PAS un echec de mission : le reste de ce qui t'a ete demande t'attend. Termine-le (fichiers annonces, livrables, verifications hors navigateur), puis conclus en enoncant ce constat tel quel — sans jamais affirmer l'interactif.\", success=False, timestamp=<HORODATAGE>, sub_results=(), origin='local_preview_unprovable')"
    },
    "lpu_07_ferme_mais_autre_page": {
        "decision": "PASSE",
        "retour": "None"
    },
    "lpu_08_ferme_mais_outil_ouvert": {
        "decision": "PASSE",
        "retour": "None"
    },
    "lpu_09_drapeau_absent": {
        "decision": "PASSE",
        "retour": "None"
    },
    "mbv_01_hors_mission": {
        "decision": "PASSE",
        "retour": "''"
    },
    "mbv_02_avec_preuve": {
        "decision": "PASSE",
        "retour": "''"
    },
    "mbv_03_sans_intention": {
        "decision": "PASSE",
        "retour": "''"
    },
    "mbv_04_interaction_demandee": {
        "decision": "PASSE",
        "retour": "''"
    },
    "mbv_05_interaction_prouvee": {
        "decision": "PASSE",
        "retour": "''"
    },
    "mbv_06_interaction_demandee": {
        "decision": "REFUSE",
        "retour": "'page(s) jamais ouverte(s) au navigateur : `contact.html` — une page que personne ne regarde est une page que personne ne corrige'"
    },
    "mbv_07_interaction_prouvee": {
        "decision": "REFUSE",
        "retour": "'page(s) jamais ouverte(s) au navigateur : `contact.html` — une page que personne ne regarde est une page que personne ne corrige'"
    },
    "pdv_01_hors_mission": {
        "decision": "",
        "retour": "True"
    },
    "pdv_02_en_mission": {
        "decision": "",
        "retour": "False"
    },
    "prf_01_aucune_action": {
        "decision": "",
        "retour": "False"
    },
    "prf_02_action_historique": {
        "decision": "",
        "retour": "True"
    },
    "prf_03_mission_stale": {
        "decision": "",
        "retour": "False"
    },
    "prf_04_mission_fraiche": {
        "decision": "",
        "retour": "True"
    },
    "prf_05_ledger_absent": {
        "decision": "",
        "retour": "False"
    },
    "rtf_01_vierge": {
        "decision": "",
        "retour": "False"
    },
    "rtf_02_echec_marque": {
        "decision": "",
        "retour": "True"
    },
    "rtv_01_vierge": {
        "decision": "",
        "retour": "False"
    },
    "rtv_02_verifie_marque": {
        "decision": "",
        "retour": "True"
    },
    "tli_01_sans_interaction": {
        "decision": "",
        "retour": "False"
    },
    "tli_02_avec_interaction": {
        "decision": "",
        "retour": "False"
    },
    "tli_03_historique_vide": {
        "decision": "",
        "retour": "False"
    },
    "tli_04_assertion_locale": {
        "decision": "",
        "retour": "True"
    },
    "tli_05_assertion_locale_hors_web": {
        "decision": "",
        "retour": "True"
    },
    "vu_01_vide": {
        "decision": "",
        "retour": "None"
    },
    "vu_02_une_page": {
        "decision": "",
        "retour": "\"[1] heading 'Accueil'\\n[2] link 'Contact'\\n[3] textbox 'Email'\\n[4] button 'Envoyer'\\n[5] link 'Tarifs'\""
    },
    "vu_03_echec": {
        "decision": "",
        "retour": "'❌ ERR_CONNECTION_REFUSED'"
    },
    "vu_04_sans_navigateur": {
        "decision": "",
        "retour": "None"
    },
    "z11_01_vide": {
        "decision": "PASSE",
        "retour": "''"
    },
    "z11_02_une_page": {
        "decision": "PASSE",
        "retour": "''"
    },
    "z11_03_deux_pages": {
        "decision": "PASSE",
        "retour": "''"
    },
    "z11_04_sans_navigateur": {
        "decision": "PASSE",
        "retour": "''"
    }
}


NOMS_RF7A = [
    "_post_delegate_web_verify_allowed", "_current_browser_proof",
    "_browser_verify_intent", "_mission_browser_verify_pending",
    "_pages_never_opened_reason", "_finalize_browser_gate_pending",
    "_finalize_interaction_gate_pending", "_browser_content_seen",
    "_browser_runtime_failed_for_truth_lock",
    "_browser_runtime_verified_for_truth_lock",
    "_local_preview_unprovable_gate", "_truth_lock_interaction_proven",
]
GARDES = {
    "_pages_never_opened_reason", "_finalize_browser_gate_pending",
    "_finalize_interaction_gate_pending", "_mission_browser_verify_pending",
    "_local_preview_unprovable_gate",
}


# ══════════════════════════════════════════════════════════════════════════
#  1. Les 67 comparaisons
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", sorted(CAS))
def test_la_valeur_de_retour_est_identique_a_la_reference(nom):
    obtenu = instantane(nom)
    attendu = BASELINE[nom]
    assert obtenu["decision"] == attendu["decision"], f"{nom} : la DECISION a change"
    assert obtenu["retour"] == attendu["retour"], f"{nom} : la valeur de retour a change"


def test_le_harnais_est_rejouable():
    for nom in sorted(CAS):
        assert instantane(nom) == instantane(nom), f"{nom} : harnais non rejouable"


def test_les_douze_methodes_produisent_CHACUNE_leurs_deux_issues():
    """LE test qui a bloque ce lot pendant une heure.

    « Combien de valeurs distinctes » est une mauvaise metrique ici : la
    plupart de ces methodes rendent un booleen. La bonne question est : chaque
    methode produit-elle ses DEUX issues ?

    La premiere version de la matrice laissait TROIS methodes sur douze avec
    une seule issue — c'est comme tester un interrupteur en n'appuyant que sur
    « eteindre ». Les extraire dans cet etat, c'etait signer une preuve qui ne
    prouve rien.
    """
    import collections

    par = collections.defaultdict(set)
    for nom, v in BASELINE.items():
        par[CAS[nom][0]].add(v["retour"])

    manquantes = sorted(m for m in NOMS_RF7A if m not in par)
    assert manquantes == [], f"methodes non exercees : {manquantes}"

    muettes = sorted(m for m, v in par.items() if len(v) < 2)
    assert muettes == [], (
        f"methodes a UNE SEULE issue — la matrice ne les prouve pas : {muettes}"
    )


def test_les_gardes_refusent_autant_qu_ils_laissent_passer():
    """Invariant 7 : les gardes navigateur restent fail-closed.

    Mesure de reference : 30 passages contre 10 refus. Un garde qui
    laisserait tout passer rendrait la matrice verte sans que personne ne le
    voie — c'est ce qui est arrive a la premiere version, avec ZERO refus sur
    47 cas.
    """
    passe = [n for n, v in BASELINE.items() if v["decision"] == "PASSE"]
    refus = [n for n, v in BASELINE.items() if v["decision"] == "REFUSE"]
    assert len(refus) >= 8, f"les gardes ne refusent plus que {len(refus)} fois"
    assert len(passe) >= 25, f"trop peu de passages exerces : {len(passe)}"

    for nom in refus:
        assert len(BASELINE[nom]["retour"]) > 30, (
            f"{nom} : refus sans motif exploitable"
        )


def test_le_stub_de_ledger_couvre_la_surface_REELLEMENT_appelee():
    """DEFAUT n°1 du lot, ferme par ce test.

    15 des 18 methodes concernees sont enveloppees dans un `except`. Un stub
    incomplet ne plante donc pas : il fait rendre "" au garde, ce qui ressemble
    a un « pas de refus » legitime. La premiere matrice n'avait pas
    `written_basenames` — resultat : ZERO refus sur 47 cas, et rien ne le
    signalait.

    Ce test relit le code REEL et exige que le stub couvre exactement ce qui
    est appele.
    """
    import ast as _ast

    # Les appels vivent desormais dans le module extrait, sous la forme
    # `e.obtenir_ledger().X()`. On les relit LA, pas dans `react.py`.
    arbre = _ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    appels = {
        n.attr for n in _ast.walk(arbre)
        if isinstance(n, _ast.Attribute) and "obtenir_ledger()" in _ast.unparse(n.value)
    }
    assert appels, "aucun appel au ledger trouve dans le module extrait"

    for m in SURFACE_LEDGER:
        assert hasattr(_Ledger, m), f"le stub ne couvre pas `{m}`"

    non_couverts = sorted(appels - set(SURFACE_LEDGER))
    assert non_couverts == [], (
        f"le code appelle des methodes de ledger que le stub ne simule pas : "
        f"{non_couverts} — un `except` les avalerait en silence"
    )


def test_l_horodatage_est_neutralise_et_rien_d_autre():
    """DEFAUT n°4 du lot : l'`Observation` du garde preview porte un
    `timestamp` qui change a chaque execution. La matrice derivait toute seule
    — meme defaut que la matrice de prompt en RF-3.

    On neutralise l'horodatage, et RIEN d'autre.
    """
    brut = ("Observation(content='x', success=False, "
            "timestamp=datetime.datetime(2026, 8, 28, 2, 28, 37, 63656), origin='y')")
    assert "<HORODATAGE>" in _normaliser(brut)
    assert "content='x'" in _normaliser(brut)
    assert "success=False" in _normaliser(brut)
    assert "origin='y'" in _normaliser(brut)


# ══════════════════════════════════════════════════════════════════════════
#  2. La PREUVE COMPORTEMENTALE adossee au repointage R2
# ══════════════════════════════════════════════════════════════════════════


def test_comportement_le_constat_de_preview_ferme_la_relecture_de_CETTE_page():
    """Preuve COMPORTEMENTALE adossee au repointage de
    `tests/reasoning/test_z23_une_impasse_locale_ne_tue_pas_la_mission.py::
    test_la_raison_du_lot_est_datee_dans_le_code`.

    Ce test-la cherche la docstring du lot Z23 dans le TEXTE de `react.py` ;
    elle est partie avec le corps. Celui-ci ne cherche aucun texte : il
    verifie que le garde REFUSE vraiment sur la page jugee, et qu'il reste
    INERTE ailleurs — ce qui est l'affirmation de fond de Z23 :

        « le constat ferme la relecture de CETTE preview, et rien d'autre. »
    """
    ferme = instantane("lpu_06_ferme_outil_ferme")
    assert ferme["decision"] == "REFUSE", "le constat ne ferme plus la relecture"
    assert "Constat ACQUIS" in ferme["retour"]

    # Borne de portee : une AUTRE page reste ouverte a l'inspection.
    autre = instantane("lpu_07_ferme_mais_autre_page")
    assert autre["decision"] == "PASSE", (
        "le constat deborde sur une autre page — c'est le bug d'origine de Z23, "
        "deplace et non corrige"
    )

    # Borne d'outil : un outil hors de la liste fermee passe.
    hors = instantane("lpu_08_ferme_mais_outil_ouvert")
    assert hors["decision"] == "PASSE"

    # Sans le drapeau, le garde est inerte.
    sans = instantane("lpu_09_drapeau_absent")
    assert sans["decision"] == "PASSE"


# ══════════════════════════════════════════════════════════════════════════
#  3. Fermeture, contrat d'etat, reexports
# ══════════════════════════════════════════════════════════════════════════


def test_le_module_extrait_ne_reference_ni_self_ni_la_classe():
    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    fautes = sorted({
        n.id for n in ast.walk(arbre)
        if isinstance(n, ast.Name) and n.id in ("self", "ReActLoop")
    })
    assert fautes == [], f"attache a ReActLoop restee dans le module : {fautes}"


def test_aucune_fonction_extraite_ne_garde_un_parametre_self():
    """Garde-fou herite de RF-5d2 — il a paye immediatement ici.

    `_finalize_interaction_gate_pending` a une signature MULTILIGNE dont
    l'indentation change apres desindentation. Le rebindage l'a ratee DEUX
    fois : la premiere version cherchait 8 espaces, la seconde un motif
    contenant `\\n` alors que le remplacement se fait ligne par ligne.
    L'extracteur n'a rien signale ; c'est ce test qui a attrape les deux.
    """
    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    fautes = [
        n.name for n in arbre.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.args.args and n.args.args[0].arg == "self"
    ]
    assert fautes == [], f"signatures non rebindees : {fautes}"


def test_le_module_extrait_n_importe_pas_react():
    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    fautes = []
    for n in ast.walk(arbre):
        if isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            if mod == "react" or mod.endswith(".react") or mod == "src.reasoning.react":
                fautes.append(ast.unparse(n))
        elif isinstance(n, ast.Import):
            for al in n.names:
                if al.name.endswith(".react") or al.name == "react":
                    fautes.append(ast.unparse(n))
    assert fautes == [], f"import vers react.py : {fautes}"


def test_l_entree_est_entierement_appelable_et_sans_mutation():
    import dataclasses

    from src.reasoning.browser_runtime import EntreeNavigateur

    champs = dataclasses.fields(EntreeNavigateur)
    # 25 champs : 11 lectures d'etat + 2 formes de `_is_mission_run`
    # + 6 appels internes redescendus sur l'instance + 4 sortants
    # + 2 constantes restees dans `react.py`.
    assert len(champs) == 25, f"{len(champs)} champs au lieu de 25"
    assert EntreeNavigateur.__dataclass_params__.frozen
    non_appelables = [c.name for c in champs if "Callable" not in str(c.type)]
    assert non_appelables == [], f"champs pre-calcules : {non_appelables}"

    # ZERO setter : ce sous-lot n'a aucune mutation.
    setters = [c.name for c in champs if c.name.startswith("definir")]
    assert setters == [], f"un setter est apparu : {setters}"


def test_les_DEUX_formes_de_is_mission_run_sont_preservees():
    """Sixieme occurrence du motif dans ce chantier.

    `_post_delegate_web_verify_allowed` lit `getattr(self, "_is_mission_run",
    False)` — qui tolere l'absence. `_current_browser_proof` lit
    `self._is_mission_run` — qui LEVE. Une seule forme ne peut pas rendre les
    deux comportements.
    """
    from types import SimpleNamespace as _SN

    import src.reasoning.react as react_mod

    entree = react_mod._entree_navigateur(_SN())
    assert entree.est_run_mission() is False        # forme gardee : tolere
    with pytest.raises(AttributeError):             # forme stricte : leve
        entree.est_run_mission_strict()

    module = NOUVEAU.read_text(encoding="utf-8")
    assert "e.est_run_mission()" in module
    assert "e.est_run_mission_strict()" in module


def test_les_deux_constantes_restent_dans_react():
    """`_LP_UNPROVABLE_CLOSED_TOOLS` est inscrite dans les CONSTANTES_RESTEES du
    test de RF-1 ; `_MAX_INTERACTION_GATE_SHOTS` a un consommateur dans
    `_run_internal`. Les deux passent en VALEUR, et l'identite est preservee
    (invariant 12)."""
    import src.reasoning.react as react_mod
    from types import SimpleNamespace as _SN

    assert hasattr(react_mod, "_LP_UNPROVABLE_CLOSED_TOOLS")
    assert hasattr(react_mod, "_MAX_INTERACTION_GATE_SHOTS")

    entree = react_mod._entree_navigateur(_SN())
    assert entree.outils_fermes_preview() is react_mod._LP_UNPROVABLE_CLOSED_TOOLS
    assert entree.max_tirs_gate_interaction() == react_mod._MAX_INTERACTION_GATE_SHOTS

    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    noms = {n.id for n in ast.walk(arbre) if isinstance(n, ast.Name)}
    assert "_LP_UNPROVABLE_CLOSED_TOOLS" not in noms
    assert "_MAX_INTERACTION_GATE_SHOTS" not in noms


def test_la_fabrique_est_une_FONCTION_DE_MODULE():
    import inspect as _inspect

    import src.reasoning.react as react_mod

    assert hasattr(react_mod, "_entree_navigateur")
    assert not hasattr(react_mod.ReActLoop, "_entree_navigateur")
    assert list(_inspect.signature(react_mod._entree_navigateur).parameters) == ["etat"]


@pytest.mark.parametrize("nom", NOMS_RF7A)
def test_le_reexport_et_la_signature_sont_inchanges(nom):
    import inspect as _inspect

    from src.reasoning.react import ReActLoop

    REFERENCE = {
        "_post_delegate_web_verify_allowed": ["self"],
        "_current_browser_proof": ["self"],
        "_browser_verify_intent": ["text"],
        "_mission_browser_verify_pending": ["self", "answer", "original_query"],
        "_pages_never_opened_reason": ["self"],
        "_finalize_browser_gate_pending": ["self", "note", "original_query"],
        "_finalize_interaction_gate_pending": ["self", "note", "original_query"],
        "_browser_content_seen": ["self"],
        "_browser_runtime_failed_for_truth_lock": ["self"],
        "_browser_runtime_verified_for_truth_lock": ["self"],
        "_local_preview_unprovable_gate": ["self", "tool_name"],
        "_truth_lock_interaction_proven": ["self"],
    }
    assert hasattr(ReActLoop, nom), f"reexport disparu : {nom}"
    sig = _inspect.signature(getattr(ReActLoop, nom))
    assert list(sig.parameters) == REFERENCE[nom], (
        f"{nom} : signature publique modifiee -> {list(sig.parameters)}"
    )


def test_browser_verify_intent_reste_un_staticmethod():
    """Invariant 13 : la forme du descripteur fait partie du contrat."""
    import inspect as _inspect

    from src.reasoning.react import ReActLoop

    brut = _inspect.getattr_static(ReActLoop, "_browser_verify_intent")
    assert isinstance(brut, staticmethod), f"forme changee : {type(brut)}"


def test_les_seize_attributs_d_etat_restent_ecrits_par_run_internal():
    """LA mesure qui borne ce lot, verrouillee.

    Si un jour un de ces attributs devient ecrit ailleurs, RF-7b redevient
    possible — et ce test le dira. Tant qu'il passe, l'etat navigateur
    appartient a `_run_internal`, et ce lot ne peut pas aller plus loin.
    """
    import ast as _ast
    import collections

    arbre = _ast.parse(REACT.read_text(encoding="utf-8"))
    cls = next(n for n in arbre.body
               if isinstance(n, _ast.ClassDef) and n.name == "ReActLoop")
    membres = {n.name: n for n in cls.body
               if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))}
    MOTS = ("browser", "dom", "visual", "preview", "navig", "screenshot",
            "page", "web_verify", "interaction")
    ecrits = collections.defaultdict(set)
    for nom, n in membres.items():
        for x in _ast.walk(n):
            if (isinstance(x, _ast.Attribute) and isinstance(x.value, _ast.Name)
                    and x.value.id == "self" and isinstance(x.ctx, _ast.Store)
                    and any(m in x.attr.lower() for m in MOTS)):
                ecrits[x.attr].add(nom)

    ailleurs = {k: sorted(v) for k, v in ecrits.items() if v != {"_run_internal"}}
    assert ailleurs == {}, (
        "des attributs d'etat navigateur sont desormais ecrits hors de "
        f"`_run_internal` : {ailleurs} — RF-7b redevient possible"
    )
