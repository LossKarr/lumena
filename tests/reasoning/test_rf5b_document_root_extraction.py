"""RF-5b — matrice d'ETAT de la racine documentaire (route + catalogue).

Lot RF-5b du plan `plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md`.
Ecrit AVANT l'extraction ; la reference est capturee sur le code d'origine.

Six methodes : `_document_route_for_run` (la racine du graphe) et les cinq qui
n'ont besoin que d'elle.

Contrairement a RF-5a, ces methodes ne sont PAS pures : deux mutent `self`
(`_document_route`, `_document_catalog_evidence`) et une mute les taches du
plan puis emet son etat. La preuve compare donc, pour chaque scenario :

- la valeur de retour ;
- la route mise en cache ;
- le cache de preuves catalogue ;
- l'etat complet des taches du plan ;
- le nombre d'emissions de l'etat du plan.

Les boucles sont construites par `object.__new__(ReActLoop)`, comme le fait le
depot : `__init__` n'est pas appele et les attributs absents le restent. C'est
le defaut qui avait casse 54 tests en RF-4.
"""
from __future__ import annotations

import ast
import copy
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


CATALOGUE_A = '{"models": [{"id": "facture_std"}, {"id": "facture_pro"}, {"id": "devis_std"}]}'
CATALOGUE_B = '{"models": [{"id": "contrat_a"}, {"id": "contrat_b"}]}'


class _Orchestrateur:
    def __init__(self, kind="mission"):
        self.kind = kind

    def get_task(self, _tid):
        return {"metadata": {"kind": self.kind}}


# Chaque cas : (methode, args, etat initial)
CAS: Dict[str, Tuple[str, tuple, Dict[str, Any]]] = {}


def _c(nom, methode, args=(), **etat):
    CAS[nom] = (methode, args, etat)


# ── _document_route_for_run ──────────────────────────────────────────────────
_c("route_01_agent_facture", "_document_route_for_run", (),
   runtime_ctx=SimpleNamespace(mode="agent"), _original_query="Genere la facture Dupont")
_c("route_02_chat", "_document_route_for_run", (),
   runtime_ctx=SimpleNamespace(mode="chat"), _original_query="Genere la facture Dupont")
_c("route_03_sans_runtime", "_document_route_for_run", (),
   _original_query="Genere le devis")
_c("route_04_query_explicite", "_document_route_for_run", ("Genere un contrat NDA",),
   runtime_ctx=SimpleNamespace(mode="agent"), _original_query="autre chose")
_c("route_05_sans_query", "_document_route_for_run", (),
   runtime_ctx=SimpleNamespace(mode="agent"))
_c("route_06_mission", "_document_route_for_run", (),
   runtime_ctx=SimpleNamespace(mode="agent"), _original_query="Genere la facture Dupont",
   task_id="t-1", task_orchestrator=_Orchestrateur("mission"))
_c("route_07_tache_non_mission", "_document_route_for_run", (),
   runtime_ctx=SimpleNamespace(mode="agent"), _original_query="Genere la facture Dupont",
   task_id="t-1", task_orchestrator=_Orchestrateur("autre"))
_c("route_08_deux_documents", "_document_route_for_run", (),
   runtime_ctx=SimpleNamespace(mode="agent"),
   _original_query="Genere la facture ET le devis pour Dupont")

# ── _record_document_catalog_evidence ────────────────────────────────────────
_c("rec_01_liste_simple", "_record_document_catalog_evidence",
   (_act("list_document_models", {"origin": "studio", "limit": 3, "sort": "nom"}),
    _obs(CATALOGUE_A, True)))
_c("rec_02_sans_observation", "_record_document_catalog_evidence",
   (_act("list_document_models", {}), None))
_c("rec_03_echec", "_record_document_catalog_evidence",
   (_act("list_document_models", {"origin": "studio"}), _obs(CATALOGUE_A, False)))
_c("rec_04_avec_kind_ignore", "_record_document_catalog_evidence",
   (_act("list_document_models", {"origin": "studio", "kind": "facture"}),
    _obs(CATALOGUE_A, True)))
_c("rec_05_parallele", "_record_document_catalog_evidence",
   (_act("parallel_tools", {}),
    _obs("", True, sub=[_sub("list_document_models", {"origin": "a", "limit": 2}, True, CATALOGUE_B),
                        _sub("read_file", {}, True, "x")])))
_c("rec_06_autre_outil", "_record_document_catalog_evidence",
   (_act("create_pdf", {}), _obs("ok", True)))
_c("rec_07_cache_existant", "_record_document_catalog_evidence",
   (_act("list_document_models", {"origin": "studio", "limit": 3, "sort": "nom"}),
    _obs(CATALOGUE_A, True)),
   _document_catalog_evidence={("autre", 0, ""): ({"id": "x"},)})

# ── _document_catalog_selection_groups ───────────────────────────────────────
_c("grp_01_sans_selection", "_document_catalog_selection_groups", (),
   runtime_ctx=SimpleNamespace(mode="agent"), _original_query="Genere la facture",
   history=[])
_c("grp_02_depuis_historique", "_document_catalog_selection_groups", (),
   runtime_ctx=SimpleNamespace(mode="agent"),
   _original_query="Montre-moi les 3 premiers modeles de documents",
   history=[_etape("list_document_models", {"origin": "studio", "limit": 3},
                   _obs(CATALOGUE_A, True))])
_c("grp_03_cache_prioritaire", "_document_catalog_selection_groups", (),
   runtime_ctx=SimpleNamespace(mode="agent"),
   _original_query="Montre-moi les 3 premiers modeles de documents",
   history=[_etape("list_document_models", {"origin": "studio", "limit": 3},
                   _obs(CATALOGUE_B, True))],
   _document_catalog_evidence={("studio", 3, ""): ({"id": "cache_1"},)})
_c("grp_04_historique_vide", "_document_catalog_selection_groups", (),
   runtime_ctx=SimpleNamespace(mode="agent"),
   _original_query="Montre-moi les 3 premiers modeles de documents")

# ── _document_catalog_selection_models ───────────────────────────────────────
_c("mod_01_vide", "_document_catalog_selection_models", (),
   runtime_ctx=SimpleNamespace(mode="agent"), _original_query="Genere la facture",
   history=[])
_c("mod_02_avec_lignes", "_document_catalog_selection_models", (),
   runtime_ctx=SimpleNamespace(mode="agent"),
   _original_query="Montre-moi les 3 premiers modeles de documents",
   history=[_etape("list_document_models", {"origin": "studio", "limit": 3},
                   _obs(CATALOGUE_A, True))])

# ── _document_expected_template_ids ──────────────────────────────────────────
_c("tpl_01_vide", "_document_expected_template_ids", (),
   runtime_ctx=SimpleNamespace(mode="agent"), _original_query="Genere la facture",
   history=[])
_c("tpl_02_avec_lignes", "_document_expected_template_ids", (),
   runtime_ctx=SimpleNamespace(mode="agent"),
   _original_query="Montre-moi les 3 premiers modeles de documents",
   history=[_etape("list_document_models", {"origin": "studio", "limit": 3},
                   _obs(CATALOGUE_A, True))])

# ── _reconcile_document_catalog_plan ─────────────────────────────────────────
_c("rcn_01_plan_vide", "_reconcile_document_catalog_plan", (3,),
   runtime_ctx=SimpleNamespace(mode="agent"), _original_query="Genere la facture")
_c("rcn_02_sans_selection_multiple", "_reconcile_document_catalog_plan", (3,),
   runtime_ctx=SimpleNamespace(mode="agent"), _original_query="Genere la facture",
   _plan=["Lister les modeles"])
_c("rcn_03_sans_preuve", "_reconcile_document_catalog_plan", (4,),
   runtime_ctx=SimpleNamespace(mode="agent"),
   _original_query="Montre 3 modeles studio et 2 modeles perso",
   _plan=["Lister les modeles studio", "Lister les modeles perso"])
_c("rcn_04_evidence_non_dict", "_reconcile_document_catalog_plan", (4,),
   runtime_ctx=SimpleNamespace(mode="agent"),
   _original_query="Montre 3 modeles studio et 2 modeles perso",
   _plan=["Lister les modeles studio"], _document_catalog_evidence="pas un dict")
_c("rcn_05_taches_deja_completees", "_reconcile_document_catalog_plan", (5,),
   runtime_ctx=SimpleNamespace(mode="agent"),
   _original_query="Montre 3 modeles studio et 2 modeles perso",
   _plan=["Lister les modeles studio"], _pre_completees=[0])

# ── _reconcile_document_catalog_plan : le CHEMIN DE MUTATION ─────────────────
#
# Les cinq cas ci-dessus n'atteignaient jamais le credit ni l'emission : sans
# selections multiples, la methode sort en 3 lignes. Une matrice qui n'exerce
# pas le chemin de mutation de la SEULE methode mutante ne prouve rien.
#
# La formulation ci-dessous est celle du depot
# (`tests/documents/test_document_workflow_atomicity.py::_state`) : c'est elle
# qui produit deux selections. Ne pas l'inventer — la reprendre.

REQUETE_DEUX_SELECTIONS = (
    "Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, "
    "dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, "
    "identifie un champ réellement modifiable, remplace sa valeur par "
    "TEST-REVISION-2026, puis vérifie la nouvelle version."
)
LIGNES_CUSTOM = tuple({"id": f"custom-{i}"} for i in range(1, 5))
LIGNES_BUILTIN = tuple({"id": f"builtin-{i}"} for i in range(1, 31))
PREUVES_COMPLETES = {
    ("custom", 4, "recent"): LIGNES_CUSTOM,
    ("builtin", 30, "name"): LIGNES_BUILTIN,
}

_c("rcn_06_credite_et_emet", "_reconcile_document_catalog_plan", (2,),
   runtime_ctx=SimpleNamespace(mode="agent"), _original_query=REQUETE_DEUX_SELECTIONS,
   _plan=["Lister les 4 modèles personnalisés", "Lister les 30 modèles intégrés"],
   _document_catalog_evidence=dict(PREUVES_COMPLETES))

_c("rcn_07_une_seule_origine_prouvee", "_reconcile_document_catalog_plan", (3,),
   runtime_ctx=SimpleNamespace(mode="agent"), _original_query=REQUETE_DEUX_SELECTIONS,
   _plan=["Lister les 4 modèles personnalisés", "Lister les 30 modèles intégrés"],
   _document_catalog_evidence={("custom", 4, "recent"): LIGNES_CUSTOM})

_c("rcn_08_lignes_insuffisantes", "_reconcile_document_catalog_plan", (4,),
   runtime_ctx=SimpleNamespace(mode="agent"), _original_query=REQUETE_DEUX_SELECTIONS,
   _plan=["Lister les 4 modèles personnalisés"],
   _document_catalog_evidence={("custom", 4, "recent"): LIGNES_CUSTOM[:2]})

_c("rcn_09_tache_de_generation_jamais_creditee", "_reconcile_document_catalog_plan", (5,),
   runtime_ctx=SimpleNamespace(mode="agent"), _original_query=REQUETE_DEUX_SELECTIONS,
   _plan=["Générer les 30 modèles intégrés"],
   _document_catalog_evidence=dict(PREUVES_COMPLETES))

_c("rcn_10_tache_deja_completee", "_reconcile_document_catalog_plan", (6,),
   runtime_ctx=SimpleNamespace(mode="agent"), _original_query=REQUETE_DEUX_SELECTIONS,
   _plan=["Lister les 4 modèles personnalisés", "Lister les 30 modèles intégrés"],
   _document_catalog_evidence=dict(PREUVES_COMPLETES), _pre_completees=[0])


def instantane(nom: str) -> Dict[str, Any]:
    """Applique le scenario sur une boucle neuve et retourne l'ETAT MUTE.

    FAIL-CLOSED : aucune exception n'est rattrapee.
    """
    from src.reasoning.react import ReActLoop
    from src.reasoning.react_config import TaskItem

    methode, args, etat_source = CAS[nom]
    # COPIE : `instantane` doit etre rejouable a l'identique. Muter le
    # dictionnaire stocke rendrait le 2e appel different du 1er, et la
    # comparaison avant/apres n'aurait plus aucun sens.
    etat = dict(etat_source)
    plan = etat.pop("_plan", None)
    pre = etat.pop("_pre_completees", ())

    boucle = object.__new__(ReActLoop)
    for cle, valeur in etat.items():
        setattr(boucle, cle, copy.deepcopy(valeur) if cle == "_document_catalog_evidence"
                else valeur)

    if plan is not None:
        boucle._task_plan = [TaskItem(description=d) for d in plan]
        for i in pre:
            boucle._task_plan[i].completed = True
            boucle._task_plan[i].completed_by_tool = "list_document_models"
    boucle._plan_emitted = True
    boucle._plan_last_emit_state = ""

    emissions = {"n": 0}

    def _emit(context_tool="", _c=emissions):
        _c["n"] += 1

    boucle._emit_plan_state = _emit

    resultat = getattr(ReActLoop, methode)(boucle, *args)
    if hasattr(resultat, "__next__"):
        resultat = tuple(resultat)

    route = getattr(boucle, "_document_route", None)
    taches = [
        {
            "description": t.description, "completed": t.completed,
            "completed_at_iteration": t.completed_at_iteration,
            "completed_by_tool": t.completed_by_tool,
            "completion_status": str(t.completion_status),
            "completion_evidence": str(t.completion_evidence),
            "completion_confidence": t.completion_confidence,
        }
        for t in (getattr(boucle, "_task_plan", None) or [])
    ]
    return {
        "retour": repr(resultat),
        "route": repr(route),
        "catalogue": repr(getattr(boucle, "_document_catalog_evidence", None)),
        "taches": taches,
        "emissions": emissions["n"],
    }


# ════════════════════════════════════════════════════════════════════════
#  La reference, capturee AVANT extraction
# ════════════════════════════════════════════════════════════════════════

BASELINE = {
    "grp_01_sans_selection": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "()",
        "route": "DocumentRoute(kind='facture', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=True, requires_document_tools=True, confidence=1.0, matched_alias='facture', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='facture', operation='create', source_text='Genere la facture', confidence=1.0, matched_alias='facture'),), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere la facture'),), minimum_pages=0)",
        "taches": []
    },
    "grp_02_depuis_historique": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "()",
        "route": "DocumentRoute(kind=None, operation='none', ui_mode='agent', requires_studio=False, legacy_fallback_allowed=False, reason='no_document_signal', owns_run=False, requires_document_tools=False, confidence=0.0, matched_alias='', ambiguous_kinds=(), items=(), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(), minimum_pages=0)",
        "taches": []
    },
    "grp_03_cache_prioritaire": {
        "catalogue": "{('studio', 3, ''): ({'id': 'cache_1'},)}",
        "emissions": 0,
        "retour": "()",
        "route": "DocumentRoute(kind=None, operation='none', ui_mode='agent', requires_studio=False, legacy_fallback_allowed=False, reason='no_document_signal', owns_run=False, requires_document_tools=False, confidence=0.0, matched_alias='', ambiguous_kinds=(), items=(), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(), minimum_pages=0)",
        "taches": []
    },
    "grp_04_historique_vide": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "()",
        "route": "DocumentRoute(kind=None, operation='none', ui_mode='agent', requires_studio=False, legacy_fallback_allowed=False, reason='no_document_signal', owns_run=False, requires_document_tools=False, confidence=0.0, matched_alias='', ambiguous_kinds=(), items=(), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(), minimum_pages=0)",
        "taches": []
    },
    "mod_01_vide": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "()",
        "route": "DocumentRoute(kind='facture', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=True, requires_document_tools=True, confidence=1.0, matched_alias='facture', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='facture', operation='create', source_text='Genere la facture', confidence=1.0, matched_alias='facture'),), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere la facture'),), minimum_pages=0)",
        "taches": []
    },
    "mod_02_avec_lignes": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "()",
        "route": "DocumentRoute(kind=None, operation='none', ui_mode='agent', requires_studio=False, legacy_fallback_allowed=False, reason='no_document_signal', owns_run=False, requires_document_tools=False, confidence=0.0, matched_alias='', ambiguous_kinds=(), items=(), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(), minimum_pages=0)",
        "taches": []
    },
    "rcn_01_plan_vide": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "0",
        "route": "None",
        "taches": []
    },
    "rcn_02_sans_selection_multiple": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "0",
        "route": "DocumentRoute(kind='facture', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=True, requires_document_tools=True, confidence=1.0, matched_alias='facture', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='facture', operation='create', source_text='Genere la facture', confidence=1.0, matched_alias='facture'),), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere la facture'),), minimum_pages=0)",
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Lister les modeles"
            }
        ]
    },
    "rcn_03_sans_preuve": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "0",
        "route": "DocumentRoute(kind=None, operation='none', ui_mode='agent', requires_studio=False, legacy_fallback_allowed=False, reason='no_document_signal', owns_run=False, requires_document_tools=False, confidence=0.0, matched_alias='', ambiguous_kinds=(), items=(), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(), minimum_pages=0)",
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Lister les modeles studio"
            },
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Lister les modeles perso"
            }
        ]
    },
    "rcn_04_evidence_non_dict": {
        "catalogue": "'pas un dict'",
        "emissions": 0,
        "retour": "0",
        "route": "DocumentRoute(kind=None, operation='none', ui_mode='agent', requires_studio=False, legacy_fallback_allowed=False, reason='no_document_signal', owns_run=False, requires_document_tools=False, confidence=0.0, matched_alias='', ambiguous_kinds=(), items=(), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(), minimum_pages=0)",
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Lister les modeles studio"
            }
        ]
    },
    "rcn_05_taches_deja_completees": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "0",
        "route": "DocumentRoute(kind=None, operation='none', ui_mode='agent', requires_studio=False, legacy_fallback_allowed=False, reason='no_document_signal', owns_run=False, requires_document_tools=False, confidence=0.0, matched_alias='', ambiguous_kinds=(), items=(), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(), minimum_pages=0)",
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": None,
                "completed_by_tool": "list_document_models",
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Lister les modeles studio"
            }
        ]
    },
    "rcn_06_credite_et_emet": {
        "catalogue": "{('custom', 4, 'recent'): ({'id': 'custom-1'}, {'id': 'custom-2'}, {'id': 'custom-3'}, {'id': 'custom-4'}), ('builtin', 30, 'name'): ({'id': 'builtin-1'}, {'id': 'builtin-2'}, {'id': 'builtin-3'}, {'id': 'builtin-4'}, {'id': 'builtin-5'}, {'id': 'builtin-6'}, {'id': 'builtin-7'}, {'id': 'builtin-8'}, {'id': 'builtin-9'}, {'id': 'builtin-10'}, {'id': 'builtin-11'}, {'id': 'builtin-12'}, {'id': 'builtin-13'}, {'id': 'builtin-14'}, {'id': 'builtin-15'}, {'id': 'builtin-16'}, {'id': 'builtin-17'}, {'id': 'builtin-18'}, {'id': 'builtin-19'}, {'id': 'builtin-20'}, {'id': 'builtin-21'}, {'id': 'builtin-22'}, {'id': 'builtin-23'}, {'id': 'builtin-24'}, {'id': 'builtin-25'}, {'id': 'builtin-26'}, {'id': 'builtin-27'}, {'id': 'builtin-28'}, {'id': 'builtin-29'}, {'id': 'builtin-30'})}",
        "emissions": 1,
        "retour": "2",
        "route": "DocumentRoute(kind=None, operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='custom_model_selection', owns_run=True, requires_document_tools=True, confidence=0.0, matched_alias='', ambiguous_kinds=(), items=(), selection_origin='custom', selection_limit=4, selection_sort='recent', selections=(DocumentModelSelection(origin='custom', limit=4, sort='recent', reason='custom_model_selection'), DocumentModelSelection(origin='builtin', limit=30, sort='name', reason='catalog_count_selection')), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.'), DocumentWorkflowAction(operation='open', target_ordinal=0, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.'), DocumentWorkflowAction(operation='revise', target_ordinal=3, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.'), DocumentWorkflowAction(operation='verify', target_ordinal=0, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.')), minimum_pages=0)",
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 2,
                "completed_by_tool": "list_document_models",
                "completion_confidence": "strong",
                "completion_evidence": "catalogue exact origin=custom, limit=4, sort=recent",
                "completion_status": "verified",
                "description": "Lister les 4 modèles personnalisés"
            },
            {
                "completed": True,
                "completed_at_iteration": 2,
                "completed_by_tool": "list_document_models",
                "completion_confidence": "strong",
                "completion_evidence": "catalogue exact origin=builtin, limit=30, sort=name",
                "completion_status": "verified",
                "description": "Lister les 30 modèles intégrés"
            }
        ]
    },
    "rcn_07_une_seule_origine_prouvee": {
        "catalogue": "{('custom', 4, 'recent'): ({'id': 'custom-1'}, {'id': 'custom-2'}, {'id': 'custom-3'}, {'id': 'custom-4'})}",
        "emissions": 1,
        "retour": "1",
        "route": "DocumentRoute(kind=None, operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='custom_model_selection', owns_run=True, requires_document_tools=True, confidence=0.0, matched_alias='', ambiguous_kinds=(), items=(), selection_origin='custom', selection_limit=4, selection_sort='recent', selections=(DocumentModelSelection(origin='custom', limit=4, sort='recent', reason='custom_model_selection'), DocumentModelSelection(origin='builtin', limit=30, sort='name', reason='catalog_count_selection')), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.'), DocumentWorkflowAction(operation='open', target_ordinal=0, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.'), DocumentWorkflowAction(operation='revise', target_ordinal=3, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.'), DocumentWorkflowAction(operation='verify', target_ordinal=0, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.')), minimum_pages=0)",
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": 3,
                "completed_by_tool": "list_document_models",
                "completion_confidence": "strong",
                "completion_evidence": "catalogue exact origin=custom, limit=4, sort=recent",
                "completion_status": "verified",
                "description": "Lister les 4 modèles personnalisés"
            },
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Lister les 30 modèles intégrés"
            }
        ]
    },
    "rcn_08_lignes_insuffisantes": {
        "catalogue": "{('custom', 4, 'recent'): ({'id': 'custom-1'}, {'id': 'custom-2'})}",
        "emissions": 0,
        "retour": "0",
        "route": "DocumentRoute(kind=None, operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='custom_model_selection', owns_run=True, requires_document_tools=True, confidence=0.0, matched_alias='', ambiguous_kinds=(), items=(), selection_origin='custom', selection_limit=4, selection_sort='recent', selections=(DocumentModelSelection(origin='custom', limit=4, sort='recent', reason='custom_model_selection'), DocumentModelSelection(origin='builtin', limit=30, sort='name', reason='catalog_count_selection')), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.'), DocumentWorkflowAction(operation='open', target_ordinal=0, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.'), DocumentWorkflowAction(operation='revise', target_ordinal=3, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.'), DocumentWorkflowAction(operation='verify', target_ordinal=0, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.')), minimum_pages=0)",
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Lister les 4 modèles personnalisés"
            }
        ]
    },
    "rcn_09_tache_de_generation_jamais_creditee": {
        "catalogue": "{('custom', 4, 'recent'): ({'id': 'custom-1'}, {'id': 'custom-2'}, {'id': 'custom-3'}, {'id': 'custom-4'}), ('builtin', 30, 'name'): ({'id': 'builtin-1'}, {'id': 'builtin-2'}, {'id': 'builtin-3'}, {'id': 'builtin-4'}, {'id': 'builtin-5'}, {'id': 'builtin-6'}, {'id': 'builtin-7'}, {'id': 'builtin-8'}, {'id': 'builtin-9'}, {'id': 'builtin-10'}, {'id': 'builtin-11'}, {'id': 'builtin-12'}, {'id': 'builtin-13'}, {'id': 'builtin-14'}, {'id': 'builtin-15'}, {'id': 'builtin-16'}, {'id': 'builtin-17'}, {'id': 'builtin-18'}, {'id': 'builtin-19'}, {'id': 'builtin-20'}, {'id': 'builtin-21'}, {'id': 'builtin-22'}, {'id': 'builtin-23'}, {'id': 'builtin-24'}, {'id': 'builtin-25'}, {'id': 'builtin-26'}, {'id': 'builtin-27'}, {'id': 'builtin-28'}, {'id': 'builtin-29'}, {'id': 'builtin-30'})}",
        "emissions": 0,
        "retour": "0",
        "route": "DocumentRoute(kind=None, operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='custom_model_selection', owns_run=True, requires_document_tools=True, confidence=0.0, matched_alias='', ambiguous_kinds=(), items=(), selection_origin='custom', selection_limit=4, selection_sort='recent', selections=(DocumentModelSelection(origin='custom', limit=4, sort='recent', reason='custom_model_selection'), DocumentModelSelection(origin='builtin', limit=30, sort='name', reason='catalog_count_selection')), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.'), DocumentWorkflowAction(operation='open', target_ordinal=0, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.'), DocumentWorkflowAction(operation='revise', target_ordinal=3, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.'), DocumentWorkflowAction(operation='verify', target_ordinal=0, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.')), minimum_pages=0)",
        "taches": [
            {
                "completed": False,
                "completed_at_iteration": None,
                "completed_by_tool": None,
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Générer les 30 modèles intégrés"
            }
        ]
    },
    "rcn_10_tache_deja_completee": {
        "catalogue": "{('custom', 4, 'recent'): ({'id': 'custom-1'}, {'id': 'custom-2'}, {'id': 'custom-3'}, {'id': 'custom-4'}), ('builtin', 30, 'name'): ({'id': 'builtin-1'}, {'id': 'builtin-2'}, {'id': 'builtin-3'}, {'id': 'builtin-4'}, {'id': 'builtin-5'}, {'id': 'builtin-6'}, {'id': 'builtin-7'}, {'id': 'builtin-8'}, {'id': 'builtin-9'}, {'id': 'builtin-10'}, {'id': 'builtin-11'}, {'id': 'builtin-12'}, {'id': 'builtin-13'}, {'id': 'builtin-14'}, {'id': 'builtin-15'}, {'id': 'builtin-16'}, {'id': 'builtin-17'}, {'id': 'builtin-18'}, {'id': 'builtin-19'}, {'id': 'builtin-20'}, {'id': 'builtin-21'}, {'id': 'builtin-22'}, {'id': 'builtin-23'}, {'id': 'builtin-24'}, {'id': 'builtin-25'}, {'id': 'builtin-26'}, {'id': 'builtin-27'}, {'id': 'builtin-28'}, {'id': 'builtin-29'}, {'id': 'builtin-30'})}",
        "emissions": 1,
        "retour": "1",
        "route": "DocumentRoute(kind=None, operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='custom_model_selection', owns_run=True, requires_document_tools=True, confidence=0.0, matched_alias='', ambiguous_kinds=(), items=(), selection_origin='custom', selection_limit=4, selection_sort='recent', selections=(DocumentModelSelection(origin='custom', limit=4, sort='recent', reason='custom_model_selection'), DocumentModelSelection(origin='builtin', limit=30, sort='name', reason='catalog_count_selection')), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.'), DocumentWorkflowAction(operation='open', target_ordinal=0, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.'), DocumentWorkflowAction(operation='revise', target_ordinal=3, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.'), DocumentWorkflowAction(operation='verify', target_ordinal=0, output_format='', source_text='Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, identifie un champ réellement modifiable, remplace sa valeur par TEST-REVISION-2026, puis vérifie la nouvelle version.')), minimum_pages=0)",
        "taches": [
            {
                "completed": True,
                "completed_at_iteration": None,
                "completed_by_tool": "list_document_models",
                "completion_confidence": "",
                "completion_evidence": "",
                "completion_status": "",
                "description": "Lister les 4 modèles personnalisés"
            },
            {
                "completed": True,
                "completed_at_iteration": 6,
                "completed_by_tool": "list_document_models",
                "completion_confidence": "strong",
                "completion_evidence": "catalogue exact origin=builtin, limit=30, sort=name",
                "completion_status": "verified",
                "description": "Lister les 30 modèles intégrés"
            }
        ]
    },
    "rec_01_liste_simple": {
        "catalogue": "{('studio', 3, 'nom'): ({'id': 'facture_std'}, {'id': 'facture_pro'}, {'id': 'devis_std'})}",
        "emissions": 0,
        "retour": "None",
        "route": "None",
        "taches": []
    },
    "rec_02_sans_observation": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "None",
        "route": "None",
        "taches": []
    },
    "rec_03_echec": {
        "catalogue": "{}",
        "emissions": 0,
        "retour": "None",
        "route": "None",
        "taches": []
    },
    "rec_04_avec_kind_ignore": {
        "catalogue": "{}",
        "emissions": 0,
        "retour": "None",
        "route": "None",
        "taches": []
    },
    "rec_05_parallele": {
        "catalogue": "{('a', 2, ''): ({'id': 'contrat_a'}, {'id': 'contrat_b'})}",
        "emissions": 0,
        "retour": "None",
        "route": "None",
        "taches": []
    },
    "rec_06_autre_outil": {
        "catalogue": "{}",
        "emissions": 0,
        "retour": "None",
        "route": "None",
        "taches": []
    },
    "rec_07_cache_existant": {
        "catalogue": "{('autre', 0, ''): ({'id': 'x'},), ('studio', 3, 'nom'): ({'id': 'facture_std'}, {'id': 'facture_pro'}, {'id': 'devis_std'})}",
        "emissions": 0,
        "retour": "None",
        "route": "None",
        "taches": []
    },
    "route_01_agent_facture": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "DocumentRoute(kind='facture', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=True, requires_document_tools=True, confidence=1.0, matched_alias='facture', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='facture', operation='create', source_text='Genere la facture Dupont', confidence=1.0, matched_alias='facture'),), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere la facture Dupont'),), minimum_pages=0)",
        "route": "DocumentRoute(kind='facture', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=True, requires_document_tools=True, confidence=1.0, matched_alias='facture', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='facture', operation='create', source_text='Genere la facture Dupont', confidence=1.0, matched_alias='facture'),), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere la facture Dupont'),), minimum_pages=0)",
        "taches": []
    },
    "route_02_chat": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "DocumentRoute(kind='facture', operation='create', ui_mode='chat', requires_studio=False, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=False, requires_document_tools=False, confidence=1.0, matched_alias='facture', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='facture', operation='create', source_text='Genere la facture Dupont', confidence=1.0, matched_alias='facture'),), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere la facture Dupont'),), minimum_pages=0)",
        "route": "DocumentRoute(kind='facture', operation='create', ui_mode='chat', requires_studio=False, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=False, requires_document_tools=False, confidence=1.0, matched_alias='facture', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='facture', operation='create', source_text='Genere la facture Dupont', confidence=1.0, matched_alias='facture'),), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere la facture Dupont'),), minimum_pages=0)",
        "taches": []
    },
    "route_03_sans_runtime": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "DocumentRoute(kind='devis', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=True, requires_document_tools=True, confidence=1.0, matched_alias='devis', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='devis', operation='create', source_text='Genere le devis', confidence=1.0, matched_alias='devis'),), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere le devis'),), minimum_pages=0)",
        "route": "DocumentRoute(kind='devis', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=True, requires_document_tools=True, confidence=1.0, matched_alias='devis', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='devis', operation='create', source_text='Genere le devis', confidence=1.0, matched_alias='devis'),), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere le devis'),), minimum_pages=0)",
        "taches": []
    },
    "route_04_query_explicite": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "DocumentRoute(kind='contrat_prestation', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=True, requires_document_tools=True, confidence=1.0, matched_alias='', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='contrat_prestation', operation='create', source_text='Genere un contrat NDA', confidence=1.0, matched_alias=''), DocumentRequestItem(index=2, kind='nda', operation='create', source_text='Genere un contrat NDA', confidence=1.0, matched_alias='')), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere un contrat NDA'),), minimum_pages=0)",
        "route": "DocumentRoute(kind='contrat_prestation', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=True, requires_document_tools=True, confidence=1.0, matched_alias='', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='contrat_prestation', operation='create', source_text='Genere un contrat NDA', confidence=1.0, matched_alias=''), DocumentRequestItem(index=2, kind='nda', operation='create', source_text='Genere un contrat NDA', confidence=1.0, matched_alias='')), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere un contrat NDA'),), minimum_pages=0)",
        "taches": []
    },
    "route_05_sans_query": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "DocumentRoute(kind=None, operation='none', ui_mode='agent', requires_studio=False, legacy_fallback_allowed=False, reason='no_document_signal', owns_run=False, requires_document_tools=False, confidence=0.0, matched_alias='', ambiguous_kinds=(), items=(), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(), minimum_pages=0)",
        "route": "DocumentRoute(kind=None, operation='none', ui_mode='agent', requires_studio=False, legacy_fallback_allowed=False, reason='no_document_signal', owns_run=False, requires_document_tools=False, confidence=0.0, matched_alias='', ambiguous_kinds=(), items=(), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(), minimum_pages=0)",
        "taches": []
    },
    "route_06_mission": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "DocumentRoute(kind='facture', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=False, requires_document_tools=True, confidence=1.0, matched_alias='facture', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='facture', operation='create', source_text='Genere la facture Dupont', confidence=1.0, matched_alias='facture'),), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere la facture Dupont'),), minimum_pages=0)",
        "route": "DocumentRoute(kind='facture', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=False, requires_document_tools=True, confidence=1.0, matched_alias='facture', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='facture', operation='create', source_text='Genere la facture Dupont', confidence=1.0, matched_alias='facture'),), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere la facture Dupont'),), minimum_pages=0)",
        "taches": []
    },
    "route_07_tache_non_mission": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "DocumentRoute(kind='facture', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=False, requires_document_tools=True, confidence=1.0, matched_alias='facture', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='facture', operation='create', source_text='Genere la facture Dupont', confidence=1.0, matched_alias='facture'),), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere la facture Dupont'),), minimum_pages=0)",
        "route": "DocumentRoute(kind='facture', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=False, requires_document_tools=True, confidence=1.0, matched_alias='facture', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='facture', operation='create', source_text='Genere la facture Dupont', confidence=1.0, matched_alias='facture'),), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere la facture Dupont'),), minimum_pages=0)",
        "taches": []
    },
    "route_08_deux_documents": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "DocumentRoute(kind='facture', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=True, requires_document_tools=True, confidence=1.0, matched_alias='', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='facture', operation='create', source_text='Genere la facture ET le devis pour Dupont', confidence=1.0, matched_alias=''), DocumentRequestItem(index=2, kind='devis', operation='create', source_text='Genere la facture ET le devis pour Dupont', confidence=1.0, matched_alias='')), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere la facture ET le devis pour Dupont'),), minimum_pages=0)",
        "route": "DocumentRoute(kind='facture', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=True, requires_document_tools=True, confidence=1.0, matched_alias='', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='facture', operation='create', source_text='Genere la facture ET le devis pour Dupont', confidence=1.0, matched_alias=''), DocumentRequestItem(index=2, kind='devis', operation='create', source_text='Genere la facture ET le devis pour Dupont', confidence=1.0, matched_alias='')), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere la facture ET le devis pour Dupont'),), minimum_pages=0)",
        "taches": []
    },
    "tpl_01_vide": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "()",
        "route": "DocumentRoute(kind='facture', operation='create', ui_mode='agent', requires_studio=True, legacy_fallback_allowed=False, reason='explicit_creation', owns_run=True, requires_document_tools=True, confidence=1.0, matched_alias='facture', ambiguous_kinds=(), items=(DocumentRequestItem(index=1, kind='facture', operation='create', source_text='Genere la facture', confidence=1.0, matched_alias='facture'),), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(DocumentWorkflowAction(operation='generate', target_ordinal=0, output_format='', source_text='Genere la facture'),), minimum_pages=0)",
        "taches": []
    },
    "tpl_02_avec_lignes": {
        "catalogue": "None",
        "emissions": 0,
        "retour": "()",
        "route": "DocumentRoute(kind=None, operation='none', ui_mode='agent', requires_studio=False, legacy_fallback_allowed=False, reason='no_document_signal', owns_run=False, requires_document_tools=False, confidence=0.0, matched_alias='', ambiguous_kinds=(), items=(), selection_origin='', selection_limit=0, selection_sort='', selections=(), workflow_actions=(), minimum_pages=0)",
        "taches": []
    }
}


# ══════════════════════════════════════════════════════════════════════════
#  1. Les 33 comparaisons d'etat
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", sorted(CAS))
def test_l_etat_mute_est_identique_a_la_reference(nom):
    obtenu = instantane(nom)
    attendu = BASELINE[nom]
    for cle in ("retour", "route", "catalogue", "taches", "emissions"):
        assert obtenu[cle] == attendu[cle], (
            f"{nom} : {cle} a change\n  attendu : {attendu[cle]}\n  obtenu  : {obtenu[cle]}"
        )


def test_le_harnais_est_rejouable():
    """Un harnais qui mute son propre jeu de cas compare deux choses
    differentes. Une premiere version de `instantane` retirait des cles du
    dictionnaire stocke : le 2e appel ne valait plus le 1er."""
    for nom in sorted(CAS):
        assert instantane(nom) == instantane(nom), f"{nom} : harnais non rejouable"


def test_le_chemin_de_MUTATION_est_reellement_exerce():
    """Une matrice qui n'atteint pas le chemin de mutation de la SEULE methode
    mutante ne prouve rien.

    Les cinq premiers cas de reconciliation sortaient tous en trois lignes,
    faute de selections multiples : 0 emission, 1 cochage. Les scenarios
    `rcn_06` a `rcn_10` reprennent la formulation du depot
    (`tests/documents/test_document_workflow_atomicity.py::_state`), la seule
    qui produise deux selections.
    """
    emettants = [n for n, v in BASELINE.items() if v["emissions"]]
    cochants = [n for n, v in BASELINE.items()
                if any(t["completed"] for t in v["taches"])]
    retours = {BASELINE[n]["retour"] for n in BASELINE if n.startswith("rcn_")}

    assert len(emettants) >= 3, f"chemin d'emission sous-exerce : {emettants}"
    assert len(cochants) >= 4, f"chemin de cochage sous-exerce : {cochants}"
    assert retours >= {"0", "1", "2"}, (
        f"la reconciliation ne rend pas assez de valeurs distinctes : {retours}"
    )


def test_la_matrice_discrimine():
    import json as _json

    distincts = {_json.dumps(v, sort_keys=True) for v in BASELINE.values()}
    assert len(distincts) >= 20, f"matrice trop pauvre : {len(distincts)} etats"


# ══════════════════════════════════════════════════════════════════════════
#  2. Les DEUX defauts de conception du lot, fermes par un test chacun
# ══════════════════════════════════════════════════════════════════════════


def test_la_fabrique_d_entree_est_une_FONCTION_DE_MODULE():
    """DEFAUT 1 — 80 tests tombes, dont 28 sur la meme AttributeError.

    Les tests du depot appellent ces six methodes SUR LA CLASSE, avec un sac
    d'etat quelconque :

        route = ReActLoop._document_route_for_run(state)   # SimpleNamespace

    Une premiere version faisait de la fabrique une METHODE, appelee par
    `self._entree_document_catalogue()`. Resultat :
    `'SimpleNamespace' object has no attribute '_entree_document_catalogue'`.

    Ce test verifie les deux choses qui le rendent impossible : la fabrique est
    au niveau MODULE, et elle fonctionne sur un sac d'etat qui n'est pas un
    `ReActLoop`.
    """
    import inspect as _inspect
    from types import SimpleNamespace as _SN

    import src.reasoning.react as react_mod

    assert hasattr(react_mod, "_entree_document_catalogue"), (
        "la fabrique n'est pas une fonction de module"
    )
    assert not hasattr(react_mod.ReActLoop, "_entree_document_catalogue"), (
        "la fabrique est redevenue une methode : le duck-typing des 196 sites "
        "d'appel est casse"
    )
    sig = _inspect.signature(react_mod._entree_document_catalogue)
    assert list(sig.parameters) == ["etat"]

    # elle doit fonctionner sur un sac d'etat nu
    entree = react_mod._entree_document_catalogue(_SN())
    assert entree.obtenir_runtime_ctx() is None
    assert entree.obtenir_plan() is None
    assert entree.obtenir_route_cache() is None


def test_les_six_methodes_acceptent_un_sac_d_etat_qui_n_est_pas_un_ReActLoop():
    """Corollaire direct du precedent, verifie de bout en bout."""
    from types import SimpleNamespace as _SN

    from src.reasoning.react import ReActLoop

    sac = _SN(runtime_ctx=_SN(mode="agent"), _original_query="Genere la facture Dupont")
    route = ReActLoop._document_route_for_run(sac)
    assert route is not None
    assert getattr(sac, "_document_route", None) is route, (
        "la mutation n'a pas atteint le sac d'etat"
    )
    assert ReActLoop._document_catalog_selection_groups(sac) == ()
    assert ReActLoop._document_expected_template_ids(sac) == ()
    assert ReActLoop._reconcile_document_catalog_plan(sac, 1) == 0


def test_la_fabrique_n_est_construite_qu_une_fois_par_appel():
    """DEFAUT 2 — `react.py` GAGNAIT 85 lignes.

    Une version reconstruisait l'entree dans chacune des six coquilles. Le
    raccord est factorise ; ce test verifie qu'aucune coquille ne reconstruit
    l'entree en ligne.
    """
    import inspect as _inspect

    from src.reasoning.react import ReActLoop

    for nom in NOMS_RF5B:
        source = _inspect.getsource(getattr(ReActLoop, nom))
        assert "_EntreeDocumentCatalogue(" not in source, (
            f"{nom} reconstruit l'entree au lieu d'appeler la fabrique"
        )
        assert "_entree_document_catalogue(self)" in source, (
            f"{nom} n'appelle pas la fabrique"
        )


# ══════════════════════════════════════════════════════════════════════════
#  3. Fermeture, contrat d'etat et reexports
# ══════════════════════════════════════════════════════════════════════════


NOMS_RF5B = [
    "_document_route_for_run", "_record_document_catalog_evidence",
    "_document_catalog_selection_groups", "_document_catalog_selection_models",
    "_document_expected_template_ids", "_reconcile_document_catalog_plan",
]


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


def test_l_entree_est_entierement_appelable_et_figee():
    """Aucune valeur pre-calculee : c'est ce qui garde la paresse, et c'est ce
    qui permet aux boucles sans `__init__` de continuer a fonctionner."""
    import dataclasses

    from src.reasoning.document_runtime import EntreeDocumentCatalogue

    champs = dataclasses.fields(EntreeDocumentCatalogue)
    assert len(champs) == 14, f"{len(champs)} champs au lieu de 14"
    assert EntreeDocumentCatalogue.__dataclass_params__.frozen
    non_appelables = [c.name for c in champs if "Callable" not in str(c.type)]
    assert non_appelables == [], (
        f"champs pre-calcules : {non_appelables} — la paresse est perdue"
    )


def test_les_deux_formes_de_lecture_des_preuves_catalogue_sont_preservees():
    """`_document_catalog_evidence` etait lu sous DEUX formes, avec deux
    defauts differents (`None` et `{}`). Une seule valeur ne peut pas rendre
    les deux — meme motif que le `execution_ledger` de RF-4."""
    import dataclasses
    from types import SimpleNamespace as _SN

    import src.reasoning.react as react_mod
    from src.reasoning.document_runtime import EntreeDocumentCatalogue

    noms = {c.name for c in dataclasses.fields(EntreeDocumentCatalogue)}
    assert "obtenir_preuves_catalogue" in noms
    assert "obtenir_preuves_catalogue_ou_vide" in noms

    entree = react_mod._entree_document_catalogue(_SN())
    assert entree.obtenir_preuves_catalogue() is None
    assert entree.obtenir_preuves_catalogue_ou_vide() == {}

    module = NOUVEAU.read_text(encoding="utf-8")
    assert "e.obtenir_preuves_catalogue()" in module
    assert "e.obtenir_preuves_catalogue_ou_vide()" in module


def test_les_deux_mutations_restent_portees_par_react():
    """Invariant 5. Le module extrait n'ecrit jamais l'etat lui-meme : il passe
    par les deux fermetures definies dans `react.py`."""
    import inspect as _inspect

    import src.reasoning.react as react_mod

    source = _inspect.getsource(react_mod._entree_document_catalogue)
    assert "etat._document_route = valeur" in source
    assert "etat._document_catalog_evidence = valeur" in source

    module = NOUVEAU.read_text(encoding="utf-8")
    assert "e.definir_route_cache(route)" in module
    assert "e.definir_preuves_catalogue(evidence)" in module


def test_la_sortie_vers_la_famille_mission_reste_un_appelable():
    """`_mission_routing_objective` appartient a RF-6, bloque par le §18 du
    plan. Le module ne doit pas tenter de l'absorber."""
    module = NOUVEAU.read_text(encoding="utf-8")
    assert "e.objectif_routage_mission()" in module

    # On verifie l'USAGE, pas la mention : l'en-tete du module explique
    # justement pourquoi cette methode reste dehors, et une recherche de
    # sous-chaine naive echouerait sur sa propre documentation. C'est
    # exactement le piege rencontre en RF-3 avec `DEFAULT_IDENTITY`.
    arbre = ast.parse(module)
    fautes = sorted({
        n.attr for n in ast.walk(arbre)
        if isinstance(n, ast.Attribute) and n.attr == "_mission_routing_objective"
    } | {
        n.id for n in ast.walk(arbre)
        if isinstance(n, ast.Name) and n.id == "_mission_routing_objective"
    })
    assert fautes == [], f"le module appelle la famille mission : {fautes}"


@pytest.mark.parametrize("nom", NOMS_RF5B)
def test_le_reexport_et_la_signature_sont_inchanges(nom):
    import inspect as _inspect

    from src.reasoning.react import ReActLoop

    REFERENCE = {
        "_document_route_for_run": ["self", "query"],
        "_record_document_catalog_evidence": ["self", "action", "observation"],
        "_document_catalog_selection_groups": ["self"],
        "_document_catalog_selection_models": ["self"],
        "_document_expected_template_ids": ["self"],
        "_reconcile_document_catalog_plan": ["self", "iteration"],
    }
    assert hasattr(ReActLoop, nom), f"reexport disparu : {nom}"
    sig = _inspect.signature(getattr(ReActLoop, nom))
    assert list(sig.parameters) == REFERENCE[nom], (
        f"{nom} : signature publique modifiee -> {list(sig.parameters)}"
    )
    assert sig.parameters.get("query") is None or \
        sig.parameters["query"].default is None
