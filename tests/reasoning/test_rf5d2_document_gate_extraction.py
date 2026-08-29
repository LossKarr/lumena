"""RF-5d2 — matrice d'ETAT de la PORTE documentaire et de la reconciliation.

Lot RF-5d2 du plan `plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md`.
Ecrit AVANT l'extraction ; la reference est capturee sur le code d'origine.

Sept methodes, 496 lignes, **zero mutation** : elles sont toutes parties avec
RF-5d1. L'enjeu de ce lot est ailleurs.

--- Ce lot porte une PORTE, et l'invariant 7 l'oblige a rester fail-closed ---

`_structured_document_tool_gate` (260 l.) rend `None` pour laisser passer, ou
une `Observation` de refus portant sa consigne. Elle compte **huit sites de
refus**.

Sur les huit lots precedents, mes matrices mesuraient surtout ce qui SE
PRODUIT. Celle-ci doit mesurer ce qui EST BLOQUE : chaque scenario enregistre
explicitement `PASSE` ou `REFUSE`, et un test exige que les deux familles
soient reellement peuplees. Une porte qui laisserait tout passer rendrait la
matrice verte sans que personne ne le voie.

L'invariant 6 est verifie separement : une exception ne devient jamais une
autorisation.

--- La frontiere avec RF-4 ---

`_reconcile_document_plan_from_manifest` et `_reconcile_document_workflow_plan`
ECRIVENT dans le plan que `react_plan_runtime.py` fait progresser, et emettent
son etat. La matrice capture donc l'etat complet des taches et le compteur
d'emissions : il ne doit pas sortir de ce lot deux proprietaires du meme etat.

--- Le motif des deux defauts, QUATRIEME occurrence ---

`_task_plan` est lu sous les deux formes : `self._task_plan` (qui leve) et
`getattr(self, "_task_plan", None)` (qui tolere). Apres `execution_ledger`
(RF-4), `_document_catalog_evidence` (RF-5b) et `_document_workflow_evidence`
(RF-5d1), le motif est cherche systematiquement — et il rapporte encore.
"""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

import pytest

RACINE = Path(__file__).resolve().parents[2]
REACT = RACINE / "src" / "reasoning" / "react.py"
NOUVEAU = RACINE / "src" / "reasoning" / "document_runtime.py"


def _obs(content="", success=True, sub=None):
    o = SimpleNamespace(content=content, success=success)
    if sub is not None:
        o.sub_results = sub
    return o


def _act(tool_name, tool_args=None):
    return SimpleNamespace(tool_name=tool_name, tool_args=tool_args or {})


def _etape(tool_name, tool_args=None, observation=None):
    return SimpleNamespace(action=_act(tool_name, tool_args), observation=observation)


def _sub(tool_name, args=None, success=True, content=""):
    return SimpleNamespace(tool_name=tool_name, args=args or {},
                           success=success, content=content)


def _preuve(kind, *, template_id="", doc="d", verifie=True, pages=2):
    return json.dumps({
        "kind": kind, "document_id": doc, "path": f"out/{kind}.pdf",
        "filename": f"{kind}.pdf", "template_id": template_id,
        "render_verified": verifie, "page_count": pages,
    })


Q_FACTURE = "Genere la facture Dupont"
Q_DEUX = "Genere la facture ET le devis pour Dupont"
Q_CHAT = "Bonjour, comment vas-tu ?"
Q_REVISION = (
    "Genere la facture et le devis. Sur le deuxieme document, remplace la "
    "valeur du champ total par TEST-REVISION-2026, puis verifie la nouvelle "
    "version."
)
Q_CATALOGUE = (
    "Genere mes 4 derniers modeles personnalises puis les 30 modeles integres, "
    "dans cet ordre. Ouvre ensuite les 34 documents."
)

HIST_VIDE: List[Any] = []
HIST_FACTURE = [
    _etape("generate_studio_document", {"kind": "facture"},
           _obs(_preuve("facture", doc="d1"), True)),
]
HIST_DEUX = HIST_FACTURE + [
    _etape("generate_studio_document", {"kind": "devis"},
           _obs(_preuve("devis", doc="d2"), True)),
]


class _Outils:
    """Sac d'outils minimal : la porte lit `self.tools`."""

    def __init__(self, disponibles=()):
        self._disponibles = set(disponibles)

    def get_tools_description(self) -> str:
        return "\n".join(f"- {t}" for t in sorted(self._disponibles))

    def has_tool(self, nom: str) -> bool:
        return nom in self._disponibles


class _Orchestrateur:
    """Orchestrateur minimal : suffit a rendre `_is_mission_run` vrai."""

    def get_task(self, _tid):
        return {"metadata": {"kind": "mission"}}


OUTILS = _Outils({
    "generate_studio_document", "generate_studio_documents",
    "revise_studio_document", "open_document_delivery", "list_document_models",
    "create_pdf", "read_file", "parallel_tools", "final_answer",
})


CAS: Dict[str, Tuple[str, tuple, Dict[str, Any]]] = {}


def _c(nom, methode, args=(), **etat):
    CAS[nom] = (methode, args, etat)


# ── _structured_document_tool_gate : PASSAGES et REFUS ───────────────────────
_c("gate_01_hors_perimetre_chat", "_structured_document_tool_gate",
   ("create_pdf", {}), _original_query=Q_CHAT, history=HIST_VIDE)
_c("gate_02_studio_autorise", "_structured_document_tool_gate",
   ("generate_studio_document", {"kind": "facture"}),
   _original_query=Q_FACTURE, history=HIST_VIDE)
_c("gate_03_legacy_avant_studio", "_structured_document_tool_gate",
   ("create_pdf", {"title": "facture"}), _original_query=Q_FACTURE, history=HIST_VIDE)
_c("gate_04_lecture_autorisee", "_structured_document_tool_gate",
   ("read_file", {"path": "a.txt"}), _original_query=Q_FACTURE, history=HIST_VIDE)
_c("gate_05_final_avant_studio", "_structured_document_tool_gate",
   ("final_answer", {"answer": "voila"}), _original_query=Q_FACTURE, history=HIST_VIDE)
_c("gate_06_final_apres_studio", "_structured_document_tool_gate",
   ("final_answer", {"answer": "voila"}), _original_query=Q_FACTURE, history=HIST_FACTURE)
_c("gate_07_lot_studio", "_structured_document_tool_gate",
   ("generate_studio_documents", {"requests": [{"kind": "facture"}, {"kind": "devis"}]}),
   _original_query=Q_DEUX, history=HIST_VIDE)
_c("gate_08_revision_avant_generation", "_structured_document_tool_gate",
   ("revise_studio_document", {"document_id": "d1"}),
   _original_query=Q_REVISION, history=HIST_VIDE)
_c("gate_09_revision_apres_generation", "_structured_document_tool_gate",
   ("revise_studio_document", {"document_id": "d2"}),
   _original_query=Q_REVISION, history=HIST_DEUX)
_c("gate_10_generation_apres_revision_demandee", "_structured_document_tool_gate",
   ("generate_studio_document", {"kind": "facture"}),
   _original_query=Q_REVISION, history=HIST_DEUX)
_c("gate_11_catalogue_liste", "_structured_document_tool_gate",
   ("list_document_models", {"origin": "custom", "limit": 4}),
   _original_query=Q_CATALOGUE, history=HIST_VIDE)
_c("gate_12_catalogue_generation_sans_liste", "_structured_document_tool_gate",
   ("generate_studio_documents", {"requests": [{"template_id": "t1"}]}),
   _original_query=Q_CATALOGUE, history=HIST_VIDE)
_c("gate_13_catalogue_outil_bloque", "_structured_document_tool_gate",
   ("create_pdf", {}), _original_query=Q_CATALOGUE, history=HIST_VIDE)
_c("gate_14_parallele", "_structured_document_tool_gate",
   ("parallel_tools", {"tool_calls": [{"name": "create_pdf"}]}),
   _original_query=Q_CATALOGUE, history=HIST_VIDE)
_c("gate_15_args_absents", "_structured_document_tool_gate",
   ("generate_studio_document", None), _original_query=Q_FACTURE, history=HIST_VIDE)

# ── _document_workflow_pending_action ────────────────────────────────────────
_c("pnd_01_vierge", "_document_workflow_pending_action", (),
   _original_query=Q_REVISION, history=HIST_VIDE)
_c("pnd_02_apres_generation", "_document_workflow_pending_action", (),
   _original_query=Q_REVISION, history=HIST_DEUX)
_c("pnd_03_sans_workflow", "_document_workflow_pending_action", (),
   _original_query=Q_FACTURE, history=HIST_FACTURE)

# ── _document_workflow_progress_signature ────────────────────────────────────
_c("sig_01_vierge", "_document_workflow_progress_signature", (),
   _original_query=Q_REVISION, history=HIST_VIDE)
_c("sig_02_apres_generation", "_document_workflow_progress_signature", (),
   _original_query=Q_REVISION, history=HIST_DEUX)
_c("sig_03_sans_demande", "_document_workflow_progress_signature", (),
   _original_query=Q_CHAT, history=HIST_VIDE)

# ── _document_final_fulfills_plan_task ───────────────────────────────────────
_c("fin_01_tache_documentaire", "_document_final_fulfills_plan_task",
   ("Livrer la facture au client",), _original_query=Q_FACTURE, history=HIST_FACTURE)
_c("fin_02_tache_quelconque", "_document_final_fulfills_plan_task",
   ("Analyser le besoin",), _original_query=Q_FACTURE, history=HIST_FACTURE)
_c("fin_03_bilan", "_document_final_fulfills_plan_task",
   ("Faire le bilan des documents",), _original_query=Q_REVISION, history=HIST_DEUX)

# ── _force_mission_proactive_document_tools ──────────────────────────────────
# `_is_mission_run` est une PROPERTY sans setter : on ne peut pas la poser sur
# l'instance. On fournit donc un orchestrateur, comme le ferait un vrai run.
_c("frc_01_hors_mission", "_force_mission_proactive_document_tools", (),
   _original_query=Q_FACTURE)
_c("frc_02_en_mission", "_force_mission_proactive_document_tools", (),
   _original_query=Q_FACTURE, task_id="t-1", task_orchestrator=_Orchestrateur())

# ── _reconcile_* : la FRONTIERE avec RF-4 ────────────────────────────────────
_c("rcm_01_plan_vide", "_reconcile_document_plan_from_manifest", (2,),
   _original_query=Q_FACTURE, history=HIST_FACTURE)
_c("rcm_02_sans_preuve", "_reconcile_document_plan_from_manifest", (2,),
   _original_query=Q_FACTURE, history=HIST_VIDE,
   _plan=["Generer la facture", "Verifier la facture"])
_c("rcm_03_avec_preuve", "_reconcile_document_plan_from_manifest", (3,),
   _original_query=Q_FACTURE, history=HIST_FACTURE,
   _plan=["Generer la facture", "Verifier la facture"])
_c("rcm_04_deux_documents", "_reconcile_document_plan_from_manifest", (4,),
   _original_query=Q_DEUX, history=HIST_DEUX,
   _plan=["Generer la facture", "Generer le devis"])

_c("rcw_01_plan_vide", "_reconcile_document_workflow_plan", (2,),
   _original_query=Q_REVISION, history=HIST_DEUX)
_c("rcw_02_sans_workflow", "_reconcile_document_workflow_plan", (2,),
   _original_query=Q_FACTURE, history=HIST_FACTURE,
   _plan=["Generer la facture"])
_c("rcw_03_workflow_en_cours", "_reconcile_document_workflow_plan", (3,),
   _original_query=Q_REVISION, history=HIST_DEUX,
   _plan=["Generer les documents", "Reviser le deuxieme", "Verifier la revision"])

# --- le CHEMIN DE CREDIT, sans lequel la reconciliation ne prouve rien -------
#
# `document_workflow_task_operation` ne reconnait PAS « generate » : seules
# open/revise/verify/export/history sont mappees. Pour qu'une tache soit
# creditee, il faut donc que l'action EN ATTENTE soit posterieure a celle que
# la tache nomme. On enregistre donc une revision reelle avant d'appeler :
# l'attente passe a « verify », et la tache « Reviser... » devient creditable.

REVISION_ENREGISTREE = json.dumps({
    "document_id": "d2", "path": "out/devis.pdf",
    "changed_fields": {"total": "TEST-REVISION-2026"},
})

_c("rcw_04_revision_enregistree_credite", "_reconcile_document_workflow_plan", (4,),
   _original_query=Q_REVISION, history=HIST_DEUX,
   _plan=["Reviser le deuxieme document", "Verifier la nouvelle version"],
   _ENCHAINER=[("revise_studio_document",
                {"document_id": "d2", "data": {"total": "TEST-REVISION-2026"}},
                REVISION_ENREGISTREE)])

_c("rcw_05_taches_deja_completees", "_reconcile_document_workflow_plan", (5,),
   _original_query=Q_REVISION, history=HIST_DEUX,
   _plan=["Reviser le deuxieme document"],
   _PRE_COMPLETEES=[0],
   _ENCHAINER=[("revise_studio_document",
                {"document_id": "d2", "data": {"total": "X"}},
                REVISION_ENREGISTREE)])

_c("rcw_06_tache_non_workflow", "_reconcile_document_workflow_plan", (6,),
   _original_query=Q_REVISION, history=HIST_DEUX,
   _plan=["Analyser le besoin du client"],
   _ENCHAINER=[("revise_studio_document",
                {"document_id": "d2", "data": {"total": "X"}},
                REVISION_ENREGISTREE)])


def instantane(nom: str) -> Dict[str, Any]:
    """Applique le scenario et retourne l'ETAT MUTE et la DECISION.

    FAIL-CLOSED : aucune exception n'est rattrapee.

    `decision` vaut `PASSE` ou `REFUSE` pour la porte : c'est ce qui permet
    d'exiger que les deux familles soient peuplees.
    """
    from src.reasoning.react import ReActLoop
    from src.reasoning.react_config import TaskItem

    methode, args, etat_source = CAS[nom]
    etat = dict(etat_source)
    plan = etat.pop("_plan", None)
    enchainer = etat.pop("_ENCHAINER", ())
    pre = etat.pop("_PRE_COMPLETEES", ())

    boucle = object.__new__(ReActLoop)
    boucle.runtime_ctx = SimpleNamespace(mode="agent")
    boucle.history = []
    boucle.tools = OUTILS
    # `_force_mission_proactive_document_tools` lit `self._is_mission_run` EN
    # DIRECT, la ou tout le reste de la famille passe par `getattr`. La
    # property exige `task_id` et `task_orchestrator` : absents, la lecture
    # directe LEVE alors que la forme gardee rendrait False. C'est le motif des
    # deux formes, cinquieme occurrence du chantier — ici sur un descripteur.
    boucle.task_id = None
    boucle.task_orchestrator = None
    for cle, valeur in etat.items():
        setattr(boucle, cle, copy.deepcopy(valeur) if cle.startswith("_document") else valeur)
    if plan is not None:
        boucle._task_plan = [TaskItem(description=d) for d in plan]
        for i in pre:
            boucle._task_plan[i].completed = True
            boucle._task_plan[i].completed_by_tool = "revise_studio_document"
    boucle._plan_emitted = True
    boucle._plan_last_emit_state = ""

    emissions = {"n": 0}
    boucle._emit_plan_state = lambda **kw: emissions.__setitem__("n", emissions["n"] + 1)

    # `_ENCHAINER` rejoue de VRAIS enregistrements avant l'appel mesure : la
    # reconciliation est ainsi prouvee sur un magasin PRODUIT par le runtime,
    # et non fabrique a la main.
    for outil, arguments, contenu in enchainer:
        ReActLoop._record_document_workflow_evidence(
            boucle, _act(outil, arguments), _obs(contenu, True))

    resultat = getattr(ReActLoop, methode)(boucle, *args)
    if hasattr(resultat, "__next__"):
        resultat = tuple(resultat)

    decision = ""
    if methode == "_structured_document_tool_gate":
        decision = "PASSE" if resultat is None else "REFUSE"

    return {
        "decision": decision,
        "retour": repr(getattr(resultat, "content", resultat))[:1500],
        "emissions": emissions["n"],
        "taches": [
            {
                "description": t.description, "completed": t.completed,
                "completed_at_iteration": t.completed_at_iteration,
                "completed_by_tool": t.completed_by_tool,
                "completion_status": str(t.completion_status),
                "completion_evidence": str(t.completion_evidence),
                "completion_confidence": t.completion_confidence,
            }
            for t in (getattr(boucle, "_task_plan", None) or [])
        ],
    }


# ════════════════════════════════════════════════════════════════════════
#  La reference, capturee AVANT extraction
# ════════════════════════════════════════════════════════════════════════

BASELINE = {
    "fin_01_tache_documentaire": {
        "decision": "",
        "emissions": 0,
        "retour": "True",
        "taches": []
    },
    "fin_02_tache_quelconque": {
        "decision": "",
        "emissions": 0,
        "retour": "False",
        "taches": []
    },
    "fin_03_bilan": {
        "decision": "",
        "emissions": 0,
        "retour": "False",
        "taches": []
    },
    "frc_01_hors_mission": {
        "decision": "",
        "emissions": 0,
        "retour": "[]",
        "taches": []
    },
    "frc_02_en_mission": {
        "decision": "",
        "emissions": 0,
        "retour": "[]",
        "taches": []
    },
    "gate_01_hors_perimetre_chat": {
        "decision": "PASSE",
        "emissions": 0,
        "retour": "None",
        "taches": []
    },
    "gate_02_studio_autorise": {
        "decision": "PASSE",
        "emissions": 0,
        "retour": "None",
        "taches": []
    },
    "gate_03_legacy_avant_studio": {
        "decision": "REFUSE",
        "emissions": 0,
        "retour": "\"Fallback `create_pdf` refuse: 1 type(s) restent sans tentative Studio: facture. Appelle `list_document_models(kind='<type>')`, puis `generate_studio_documents(requests=[...])` une fois pour le lot, ou `generate_studio_document(kind='<type>', data={...})` pour chaque type. Le modele par defaut, sa mise en page et le logo actif seront appliques. Le fallback historique ne sera disponible qu'apres une tentative Studio pour chaque type demande.\"",
        "taches": []
    },
    "gate_04_lecture_autorisee": {
        "decision": "PASSE",
        "emissions": 0,
        "retour": "None",
        "taches": []
    },
    "gate_05_final_avant_studio": {
        "decision": "PASSE",
        "emissions": 0,
        "retour": "None",
        "taches": []
    },
    "gate_06_final_apres_studio": {
        "decision": "PASSE",
        "emissions": 0,
        "retour": "None",
        "taches": []
    },
    "gate_07_lot_studio": {
        "decision": "PASSE",
        "emissions": 0,
        "retour": "None",
        "taches": []
    },
    "gate_08_revision_avant_generation": {
        "decision": "REFUSE",
        "emissions": 0,
        "retour": "\"Ordre du workflow documentaire refuse: la revision ne peut pas preceder l'etape `generate`. Termine d'abord la generation certifiee du lot exact.\"",
        "taches": []
    },
    "gate_09_revision_apres_generation": {
        "decision": "PASSE",
        "emissions": 0,
        "retour": "None",
        "taches": []
    },
    "gate_10_generation_apres_revision_demandee": {
        "decision": "PASSE",
        "emissions": 0,
        "retour": "None",
        "taches": []
    },
    "gate_11_catalogue_liste": {
        "decision": "REFUSE",
        "emissions": 0,
        "retour": "\"Parametres catalogue incorrects pour cette requete. Appelle `list_document_models(origin='custom', limit=4, sort='recent')`, puis `list_document_models(origin='builtin', limit=30, sort='name')` exactement; ne change ni origin, ni limit, ni sort.\"",
        "taches": []
    },
    "gate_12_catalogue_generation_sans_liste": {
        "decision": "REFUSE",
        "emissions": 0,
        "retour": "\"Selection documentaire non prouvee. Appelle d'abord `list_document_models(origin='custom', limit=4, sort='recent')`, puis `list_document_models(origin='builtin', limit=30, sort='name')`, puis reutilise exactement les template_id retournes.\"",
        "taches": []
    },
    "gate_13_catalogue_outil_bloque": {
        "decision": "REFUSE",
        "emissions": 0,
        "retour": "'`create_pdf` refuse pour cette selection de catalogue. Utilise `list_document_models`, puis un seul `generate_studio_documents` avec exactement les template_id retournes.'",
        "taches": []
    },
    "gate_14_parallele": {
        "decision": "REFUSE",
        "emissions": 0,
        "retour": "\"Workflow catalogue en deux phases: `parallel_tools` peut seulement lister les catalogues exacts. Aucune generation, ouverture ou revision documentaire ne peut y etre imbriquee. Appelle `list_document_models(origin='custom', limit=4, sort='recent')`, puis `list_document_models(origin='builtin', limit=30, sort='name')`, attends leurs resultats, puis appelle `generate_studio_documents` directement et sequentiellement. Sous-appel refuse: create_pdf\"",
        "taches": []
    },
    "gate_15_args_absents": {
        "decision": "PASSE",
        "emissions": 0,
        "retour": "None",
        "taches": []
    },
    "pnd_01_vierge": {
        "decision": "",
        "emissions": 0,
        "retour": "DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere la facture et le devis. Sur le deuxieme document, remplace la valeur du champ total par TEST-REVISION-2026, puis verifie la nouvelle version.')",
        "taches": []
    },
    "pnd_02_apres_generation": {
        "decision": "",
        "emissions": 0,
        "retour": "DocumentWorkflowAction(operation='revise', target_ordinal=2, output_format='', source_text='Genere la facture et le devis. Sur le deuxieme document, remplace la valeur du champ total par TEST-REVISION-2026, puis verifie la nouvelle version.')",
        "taches": []
    },
    "pnd_03_sans_workflow": {
        "decision": "",
        "emissions": 0,
        "retour": "None",
        "taches": []
    },
    "rcm_01_plan_vide": {
        "decision": "",
        "emissions": 0,
        "retour": "0",
        "taches": []
    },
    "rcm_02_sans_preuve": {
        "decision": "",
        "emissions": 0,
        "retour": "0",
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Generer la facture"
            },
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Verifier la facture"
            }
        ]
    },
    "rcm_03_avec_preuve": {
        "decision": "",
        "emissions": 0,
        "retour": "0",
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Generer la facture"
            },
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Verifier la facture"
            }
        ]
    },
    "rcm_04_deux_documents": {
        "decision": "",
        "emissions": 1,
        "retour": "2",
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 4,
                "completed_by_tool": "document_manifest",
                "completion_confidence": "strong",
                "completion_evidence": "manifest exact: facture",
                "completion_status": "created",
                "description": "Generer la facture"
            },
            {
                "completed": True,
                "completed_at_iteration": 4,
                "completed_by_tool": "document_manifest",
                "completion_confidence": "strong",
                "completion_evidence": "manifest exact: devis",
                "completion_status": "created",
                "description": "Generer le devis"
            }
        ]
    },
    "rcw_01_plan_vide": {
        "decision": "",
        "emissions": 0,
        "retour": "0",
        "taches": []
    },
    "rcw_02_sans_workflow": {
        "decision": "",
        "emissions": 0,
        "retour": "0",
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Generer la facture"
            }
        ]
    },
    "rcw_03_workflow_en_cours": {
        "decision": "",
        "emissions": 0,
        "retour": "0",
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Generer les documents"
            },
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Reviser le deuxieme"
            },
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Verifier la revision"
            }
        ]
    },
    "rcw_04_revision_enregistree_credite": {
        "decision": "",
        "emissions": 1,
        "retour": "1",
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 4,
                "completed_by_tool": "document_workflow_proof",
                "completion_confidence": "strong",
                "completion_evidence": "revision de la cible exacte d2 vers d2",
                "completion_status": "updated",
                "description": "Reviser le deuxieme document"
            },
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Verifier la nouvelle version"
            }
        ]
    },
    "rcw_05_taches_deja_completees": {
        "decision": "",
        "emissions": 0,
        "retour": "0",
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": None,
                "completed_by_tool": "revise_studio_document",
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Reviser le deuxieme document"
            }
        ]
    },
    "rcw_06_tache_non_workflow": {
        "decision": "",
        "emissions": 0,
        "retour": "0",
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Analyser le besoin du client"
            }
        ]
    },
    "sig_01_vierge": {
        "decision": "",
        "emissions": 0,
        "retour": "(0, '', '', 'None', '', ())",
        "taches": []
    },
    "sig_02_apres_generation": {
        "decision": "",
        "emissions": 0,
        "retour": "(0, '', '', 'None', '', ())",
        "taches": []
    },
    "sig_03_sans_demande": {
        "decision": "",
        "emissions": 0,
        "retour": "(0, '', '', 'None', '', ())",
        "taches": []
    }
}


NOMS_RF5D2 = [
    "_force_mission_proactive_document_tools", "_document_workflow_progress_signature",
    "_document_workflow_pending_action", "_document_final_fulfills_plan_task",
    "_reconcile_document_plan_from_manifest", "_reconcile_document_workflow_plan",
    "_structured_document_tool_gate",
]


# ══════════════════════════════════════════════════════════════════════════
#  1. Les 36 comparaisons d'etat
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", sorted(CAS))
def test_l_etat_mute_est_identique_a_la_reference(nom):
    obtenu = instantane(nom)
    attendu = BASELINE[nom]
    for cle in ("decision", "retour", "emissions", "taches"):
        assert obtenu[cle] == attendu[cle], (
            f"{nom} : {cle} a change\n"
            f"  attendu : {str(attendu[cle])[:400]}\n"
            f"  obtenu  : {str(obtenu[cle])[:400]}"
        )


def test_le_harnais_est_rejouable():
    for nom in sorted(CAS):
        assert instantane(nom) == instantane(nom), f"{nom} : harnais non rejouable"


# ══════════════════════════════════════════════════════════════════════════
#  2. La PORTE — invariant 7 : elle doit rester FAIL-CLOSED
# ══════════════════════════════════════════════════════════════════════════


def test_la_porte_refuse_autant_qu_elle_laisse_passer():
    """L'enjeu de ce lot n'est pas l'extraction, c'est le REFUS.

    Sur les huit lots precedents, mes matrices mesuraient surtout ce qui SE
    PRODUIT. Une porte qui laisserait tout passer rendrait une matrice « verte »
    sans que personne ne le voie.

    Mesure de reference : 9 passages contre 6 refus.
    """
    passe = [n for n, v in BASELINE.items() if v["decision"] == "PASSE"]
    refus = [n for n, v in BASELINE.items() if v["decision"] == "REFUSE"]

    assert len(passe) >= 8, f"trop peu de passages exerces : {len(passe)}"
    assert len(refus) >= 6, (
        f"la porte ne refuse plus que {len(refus)} fois sur {len(passe)+len(refus)} : "
        "elle s'est ouverte"
    )

    # chaque refus doit porter une consigne, pas un refus muet
    for nom in refus:
        contenu = BASELINE[nom]["retour"]
        assert len(contenu) > 40, f"{nom} : refus sans consigne exploitable"
        assert contenu != repr(None)


def test_les_familles_de_refus_sont_toutes_representees():
    """Les six refus mesures couvrent quatre familles distinctes : ordre du
    workflow, fallback legacy, selection catalogue non prouvee, outil bloque."""
    refus = {n: BASELINE[n]["retour"] for n, v in BASELINE.items()
             if v["decision"] == "REFUSE"}
    motifs = " ".join(refus.values())
    for marqueur in ("Ordre du workflow", "Fallback", "Selection documentaire",
                     "catalogue"):
        assert marqueur in motifs, f"famille de refus absente : {marqueur}"


def test_une_exception_ne_devient_jamais_une_autorisation():
    """Invariant 6. On fait echouer la resolution de route et on verifie que
    l'exception REMONTE — elle ne doit pas se transformer en `None`, qui
    signifierait « passe »."""
    from types import SimpleNamespace as _SN

    import src.reasoning.document_runtime as dr
    from src.reasoning.react import ReActLoop

    sac = _SN(runtime_ctx=_SN(mode="agent"), _original_query=Q_FACTURE,
              history=[], tools=OUTILS, task_id=None, task_orchestrator=None)

    vrai = dr.resolve_document_route

    def _explose(*a, **k):
        raise RuntimeError("resolution impossible (simule RF-5d2)")

    dr.resolve_document_route = _explose
    try:
        with pytest.raises(RuntimeError):
            ReActLoop._structured_document_tool_gate(sac, "create_pdf", {})
    finally:
        dr.resolve_document_route = vrai


# ══════════════════════════════════════════════════════════════════════════
#  3. La frontiere avec RF-4 : ecrire dans le plan sans en devenir proprietaire
# ══════════════════════════════════════════════════════════════════════════


def test_la_reconciliation_credite_et_emet_reellement():
    """Une matrice qui n'atteint pas le chemin de credit ne prouve rien.

    La premiere version de `rcw_*` n'emettait JAMAIS : sans revision
    enregistree, l'action en attente reste « revise » et aucune tache n'est
    creditable. Trois scenarios ont ete ajoutes.
    """
    emis = [n for n, v in BASELINE.items() if v["emissions"]]
    coch = [n for n, v in BASELINE.items()
            if any(t["completed"] for t in v["taches"])]
    assert len(emis) >= 2, f"chemin d'emission sous-exerce : {emis}"
    assert len(coch) >= 3, f"chemin de credit sous-exerce : {coch}"
    assert any(n.startswith("rcm_") for n in emis), "manifeste : credit non exerce"
    assert any(n.startswith("rcw_") for n in emis), "workflow : credit non exerce"


def test_l_emission_passe_par_l_appelable_de_react_et_non_par_le_module():
    """Les deux `_reconcile_*` ecrivent dans le plan que
    `react_plan_runtime.py` fait progresser. Elles n'en deviennent pas
    proprietaires : `react.py` reste seul a definir ce qu'emettre veut dire."""
    module = NOUVEAU.read_text(encoding="utf-8")
    assert "e.workflow.livraison.catalogue.emettre_etat_plan(" in module

    # On verifie l'USAGE, pas la mention. C'est la TROISIEME fois du chantier
    # qu'un test de ce type echoue sur sa propre documentation : RF-3 avec
    # `DEFAULT_IDENTITY`, RF-5b avec `_mission_routing_objective`, et ici le
    # commentaire d'en-tete qui explique justement pourquoi l'emission ne passe
    # PAS par `_emit_plan_state`. Le reflexe de la sous-chaine est abandonne.
    arbre = ast.parse(module)
    fautes = sorted({
        n.attr for n in ast.walk(arbre)
        if isinstance(n, ast.Attribute) and n.attr == "_emit_plan_state"
    } | {
        n.id for n in ast.walk(arbre)
        if isinstance(n, ast.Name) and n.id == "_emit_plan_state"
    })
    assert fautes == [], (
        f"le module extrait appelle directement l'emission de `react.py` : {fautes}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  4. Le garde-fou qui manquait a mon extracteur
# ══════════════════════════════════════════════════════════════════════════


def test_aucune_fonction_extraite_ne_garde_un_parametre_self():
    """DEUX signatures ont echappe au rebindage de ce lot, silencieusement.

    - `_force_mission_proactive_document_tools` : l'annotation de retour etait
      `list[str]` et non `None`, la chaine cherchee ne correspondait pas ;
    - `_structured_document_tool_gate` : signature MULTILIGNE, dont
      l'indentation change apres desindentation.

    Dans les deux cas l'extracteur n'a rien signale et c'est la matrice qui a
    leve un `NameError: name 'e' is not defined`. Ce test transforme cette
    detection tardive en garde-fou : aucune fonction du module ne doit garder
    un parametre `self`.
    """
    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    fautes = [
        n.name for n in arbre.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.args.args and n.args.args[0].arg == "self"
    ]
    assert fautes == [], f"signatures non rebindees : {fautes}"


def test_le_module_extrait_ne_reference_ni_self_ni_la_classe():
    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    fautes = sorted({
        n.id for n in ast.walk(arbre)
        if isinstance(n, ast.Name) and n.id in ("self", "ReActLoop")
    })
    assert fautes == [], f"attache a ReActLoop restee dans le module : {fautes}"


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


# ══════════════════════════════════════════════════════════════════════════
#  5. Contrats emboites, formes strictes, reexports
# ══════════════════════════════════════════════════════════════════════════


def test_les_quatre_contrats_restent_emboites_et_intacts():
    import dataclasses

    from src.reasoning.document_runtime import (
        EntreeDocumentCatalogue,
        EntreeLivraisonDocument,
        EntreePorteDocument,
        EntreeWorkflowDocument,
    )

    assert len(dataclasses.fields(EntreeDocumentCatalogue)) == 14, "RF-5b elargi"
    assert len(dataclasses.fields(EntreeLivraisonDocument)) == 9, "RF-5c elargi"
    assert len(dataclasses.fields(EntreeWorkflowDocument)) == 3, "RF-5d1 elargi"
    champs = {c.name for c in dataclasses.fields(EntreePorteDocument)}
    assert champs == {"workflow", "obtenir_plan_strict",
                      "est_run_mission_strict", "obtenir_outils"}, champs
    assert EntreePorteDocument.__dataclass_params__.frozen


def test_les_DEUX_formes_strictes_sont_preservees():
    """Motif des deux formes, quatrieme et cinquieme occurrences.

    - `_task_plan` : `getattr(self, "_task_plan", None)` tolere l'absence,
      `self._task_plan` leve ;
    - `_is_mission_run` : `_force_mission_proactive_document_tools` le lit EN
      DIRECT alors que toute la famille passe par `getattr`.

    Une seule forme ne peut pas rendre les deux comportements. Ce test verifie
    que la forme stricte LEVE bien la ou l'originale levait.
    """
    from types import SimpleNamespace as _SN

    import src.reasoning.react as react_mod

    entree = react_mod._entree_porte_document(_SN())
    with pytest.raises(AttributeError):
        entree.obtenir_plan_strict()
    with pytest.raises(AttributeError):
        entree.obtenir_outils()

    # la forme GARDEE, elle, tolere — et elle vit dans l'entree catalogue
    assert entree.workflow.livraison.catalogue.obtenir_plan() is None

    module = NOUVEAU.read_text(encoding="utf-8")
    assert "e.obtenir_plan_strict()" in module
    assert "e.workflow.livraison.catalogue.obtenir_plan()" in module


def test_la_constante_deplacee_reste_reexportee_par_react():
    """`_MISSION_PROACTIVE_DOCUMENT_TOOLS` avait UN SEUL consommateur — la
    methode qui part avec ce lot. Elle l'a suivi ; `react.py` la reexporte
    (invariant 4)."""
    import src.reasoning.react as react_mod
    from src.reasoning.document_runtime import _MISSION_PROACTIVE_DOCUMENT_TOOLS

    assert hasattr(react_mod, "_MISSION_PROACTIVE_DOCUMENT_TOOLS")
    assert (react_mod._MISSION_PROACTIVE_DOCUMENT_TOOLS
            is _MISSION_PROACTIVE_DOCUMENT_TOOLS), (
        "le reexport a perdu l'identite de l'objet (invariant 12)"
    )
    assert "list_document_models" in _MISSION_PROACTIVE_DOCUMENT_TOOLS


def test_la_fabrique_de_porte_est_une_FONCTION_DE_MODULE():
    import inspect as _inspect

    import src.reasoning.react as react_mod

    assert hasattr(react_mod, "_entree_porte_document")
    assert not hasattr(react_mod.ReActLoop, "_entree_porte_document")
    assert list(_inspect.signature(
        react_mod._entree_porte_document).parameters) == ["etat"]


@pytest.mark.parametrize("nom", NOMS_RF5D2)
def test_le_reexport_et_la_signature_sont_inchanges(nom):
    import inspect as _inspect

    from src.reasoning.react import ReActLoop

    REFERENCE = {
        "_force_mission_proactive_document_tools": ["self"],
        "_document_workflow_progress_signature": ["self"],
        "_document_workflow_pending_action": ["self"],
        "_document_final_fulfills_plan_task": ["self", "task_desc"],
        "_reconcile_document_plan_from_manifest": ["self", "iteration"],
        "_reconcile_document_workflow_plan": ["self", "iteration"],
        "_structured_document_tool_gate": ["self", "tool_name", "tool_args"],
    }
    assert hasattr(ReActLoop, nom), f"reexport disparu : {nom}"
    sig = _inspect.signature(getattr(ReActLoop, nom))
    assert list(sig.parameters) == REFERENCE[nom], (
        f"{nom} : signature publique modifiee -> {list(sig.parameters)}"
    )


def test_la_famille_documentaire_a_entierement_quitte_react():
    """Fin de RF-5 : plus aucun corps documentaire dans `ReActLoop`."""
    import inspect as _inspect

    from src.reasoning.react import ReActLoop

    for nom in NOMS_RF5D2:
        source = _inspect.getsource(getattr(ReActLoop, nom))
        assert "_entree_porte_document(self)" in source, f"{nom} : coquille absente"
        assert len(source.splitlines()) <= 10, (
            f"{nom} : la coquille fait {len(source.splitlines())} lignes, "
            "un corps est reste"
        )
