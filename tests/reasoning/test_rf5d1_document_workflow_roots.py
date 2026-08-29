"""RF-5d1 — matrice d'ETAT des deux racines du workflow documentaire.

Lot RF-5d1 du plan `plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md`.
Ecrit AVANT l'extraction ; la reference est capturee sur le code d'origine.

Deux methodes, 481 lignes, ilot ferme par le graphe :

- `_record_document_workflow_evidence` (220 l.) — **les 4 dernieres mutations
  de la famille** ;
- `_document_workflow_proof_state` (261 l.) — l'etat de preuve que tout le
  reste de RF-5d consomme.

Aucune des deux ne sort du sous-lot : leurs 11 appels sur la classe pointent
tous vers des methodes deja extraites par RF-5a, RF-5b et RF-5c.

--- Ce que la matrice doit couvrir ---

`_record_document_workflow_evidence` a **dix branches** selon le nom d'outil.
Une matrice qui n'en exercerait que deux ne prouverait rien : chacune est
couverte nommement, plus les refus (observation absente, echec).

`_document_workflow_proof_state` est ensuite appele SUR L'ETAT PRODUIT par ces
enregistrements — c'est ainsi que les deux racines se prouvent ensemble, et
non chacune sur un etat fabrique a la main.

--- HERMETICITE (lecon RF-3, appliquee des la conception) ---

La branche `generate_studio_documents` persiste un recu. Comme en RF-5c,
`save_delivery_reference` est EPINGLE et un test compte les ecritures reelles :
zero exigee. Une preuve qui depend d'un magasin persistant est un instantane,
pas une preuve.
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


def _preuve(kind, *, template_id="", doc="d", verifie=True, pages=2, chemin=None):
    return json.dumps({
        "kind": kind, "document_id": doc,
        "path": chemin or f"out/{kind}.pdf", "filename": f"{kind}.pdf",
        "template_id": template_id, "render_verified": verifie, "page_count": pages,
    })


Q_FACTURE = "Genere la facture Dupont"
Q_DEUX = "Genere la facture ET le devis pour Dupont"
Q_REVISION = (
    "Genere la facture et le devis. Sur le deuxieme document, remplace la "
    "valeur du champ total par TEST-REVISION-2026, puis verifie la nouvelle "
    "version."
)

OUVERTURE = json.dumps({"document_id": "d1", "path": "out/facture.pdf",
                        "content": "Total : 1200 EUR"})
REVISION = json.dumps({"document_id": "d1", "path": "out/facture.pdf",
                       "changed_fields": {"total": "TEST-REVISION-2026"}})
HISTORIQUE_DOC = json.dumps({"document_id": "d1", "versions": [1, 2]})
BIBLIO = json.dumps({"results": [{"id": "lib-1", "path": "out/facture.pdf"}]})

HIST_DEUX_OK = [
    _etape("generate_studio_document", {"kind": "facture"},
           _obs(_preuve("facture", doc="d1"), True)),
    _etape("generate_studio_document", {"kind": "devis"},
           _obs(_preuve("devis", doc="d2"), True)),
]


CAS: Dict[str, Tuple[str, tuple, Dict[str, Any]]] = {}


def _c(nom, methode, args=(), **etat):
    CAS[nom] = (methode, args, etat)


# ── _record_document_workflow_evidence : les DIX branches ────────────────────
_c("rec_01_refus_sans_observation", "_record_document_workflow_evidence",
   (_act("generate_studio_document", {"kind": "facture"}), None),
   _original_query=Q_FACTURE)
_c("rec_02_refus_echec", "_record_document_workflow_evidence",
   (_act("generate_studio_document", {"kind": "facture"}),
    _obs(_preuve("facture"), False)), _original_query=Q_FACTURE)
_c("rec_03_generation_simple", "_record_document_workflow_evidence",
   (_act("generate_studio_document", {"kind": "facture"}),
    _obs(_preuve("facture"), True)), _original_query=Q_FACTURE)
_c("rec_04_generation_lot", "_record_document_workflow_evidence",
   (_act("generate_studio_documents", {}),
    _obs(json.dumps({"id": "recu-lot"}), True, sub=[
        _sub("generate_studio_documents", {"kind": "facture"}, True,
             _preuve("facture", template_id="t1", doc="d1")),
        _sub("generate_studio_documents", {"kind": "devis"}, True,
             _preuve("devis", template_id="t2", doc="d2")),
    ])), _original_query=Q_DEUX)
_c("rec_05_ouverture_livraison", "_record_document_workflow_evidence",
   (_act("open_document_delivery", {}), _obs(OUVERTURE, True)),
   _original_query=Q_FACTURE)
_c("rec_06_ouverture_fichier", "_record_document_workflow_evidence",
   (_act("open_file", {"path": "out/facture.pdf"}), _obs(OUVERTURE, True)),
   _original_query=Q_FACTURE)
_c("rec_07_revision", "_record_document_workflow_evidence",
   (_act("revise_studio_document", {"document_id": "d1",
                                    "data": {"total": "TEST-REVISION-2026"}}),
    _obs(REVISION, True)), _original_query=Q_REVISION)
_c("rec_08_lecture_document", "_record_document_workflow_evidence",
   (_act("read_document", {"document_id": "d1"}),
    _obs("Total : TEST-REVISION-2026", True)), _original_query=Q_REVISION)
_c("rec_09_historique_document", "_record_document_workflow_evidence",
   (_act("get_document_history", {"document_id": "d1"}),
    _obs(HISTORIQUE_DOC, True)), _original_query=Q_REVISION)
_c("rec_10_export_bibliotheque", "_record_document_workflow_evidence",
   (_act("export_library_document", {"document_id": "d1"}),
    _obs(json.dumps({"path": "out/export.pdf"}), True)), _original_query=Q_FACTURE)
_c("rec_11_conversion_bibliotheque", "_record_document_workflow_evidence",
   (_act("convert_library_document", {"document_id": "d1"}),
    _obs(json.dumps({"path": "out/converti.pdf"}), True)), _original_query=Q_FACTURE)
_c("rec_12_recherche_bibliotheque", "_record_document_workflow_evidence",
   (_act("search_document_library", {"query": "facture"}), _obs(BIBLIO, True)),
   _original_query=Q_FACTURE)
_c("rec_13_fiche_document", "_record_document_workflow_evidence",
   (_act("get_document_record", {"document_id": "d1"}),
    _obs(json.dumps({"id": "d1", "path": "out/facture.pdf"}), True)),
   _original_query=Q_FACTURE)
_c("rec_14_outil_hors_perimetre", "_record_document_workflow_evidence",
   (_act("read_file", {"path": "a.txt"}), _obs("contenu", True)),
   _original_query=Q_FACTURE)
_c("rec_15_magasin_prealable", "_record_document_workflow_evidence",
   (_act("generate_studio_document", {"kind": "facture"}),
    _obs(_preuve("facture"), True)), _original_query=Q_FACTURE,
   _document_workflow_evidence={"batch_proofs": {}, "generation_events": [],
                                "open_events": [], "revision_events": [],
                                "revision_records": [], "verification_events": [],
                                "history_events": [], "export_events": [],
                                "library_events": [], "event_counter": 7})
_c("rec_16_magasin_invalide", "_record_document_workflow_evidence",
   (_act("generate_studio_document", {"kind": "facture"}),
    _obs(_preuve("facture"), True)), _original_query=Q_FACTURE,
   _document_workflow_evidence="pas un dict")

# ── Le chemin de RECU : deux des quatre mutations n'etaient pas exercees ─────
#
# La premiere version de cette matrice ne posait JAMAIS de reference :
# `_document_delivery_reference_id` et `_document_delivery_reference_signature`
# n'etaient touches par aucun scenario. Une matrice qui n'exerce pas deux des
# quatre mutations de la seule methode mutante ne prouve pas grand-chose.
#
# La branche exige `receipt_id` dans la charge utile (et non `id`) ET un
# manifeste complet. Les deux cas ci-dessous couvrent l'acceptation et le refus.

_c("rec_17_recu_manifeste_complet", "_record_document_workflow_evidence",
   (_act("generate_studio_documents", {}),
    _obs(json.dumps({"receipt_id": "recu-officiel-42"}), True, sub=[
        _sub("generate_studio_documents", {"kind": "facture"}, True,
             _preuve("facture", template_id="t1", doc="d1")),
        _sub("generate_studio_documents", {"kind": "devis"}, True,
             _preuve("devis", template_id="t2", doc="d2")),
    ])), _original_query=Q_DEUX)

_c("rec_18_recu_manifeste_incomplet_refuse", "_record_document_workflow_evidence",
   (_act("generate_studio_documents", {}),
    _obs(json.dumps({"receipt_id": "recu-officiel-43"}), True, sub=[
        _sub("generate_studio_documents", {"kind": "facture"}, True,
             _preuve("facture", template_id="t1", doc="d1")),
    ])), _original_query=Q_DEUX)

_c("rec_19_recu_preuve_non_verifiee_refuse", "_record_document_workflow_evidence",
   (_act("generate_studio_documents", {}),
    _obs(json.dumps({"receipt_id": "recu-officiel-44"}), True, sub=[
        _sub("generate_studio_documents", {"kind": "facture"}, True,
             _preuve("facture", template_id="t1", doc="d1", verifie=False)),
        _sub("generate_studio_documents", {"kind": "devis"}, True,
             _preuve("devis", template_id="t2", doc="d2")),
    ])), _original_query=Q_DEUX)


# ── _document_workflow_proof_state ───────────────────────────────────────────
_c("etat_01_vierge", "_document_workflow_proof_state", (), _original_query=Q_FACTURE)
_c("etat_02_sans_demande", "_document_workflow_proof_state", (),
   _original_query="Bonjour, comment vas-tu ?")
_c("etat_03_apres_generations", "_document_workflow_proof_state", (),
   _original_query=Q_DEUX, history=HIST_DEUX_OK)
_c("etat_04_revision_demandee", "_document_workflow_proof_state", (),
   _original_query=Q_REVISION, history=HIST_DEUX_OK)
_c("etat_05_magasin_rempli", "_document_workflow_proof_state", (),
   _original_query=Q_REVISION, history=HIST_DEUX_OK,
   _ENCHAINER=[("revise_studio_document",
                {"document_id": "d1", "data": {"total": "X"}}, REVISION),
               ("read_document", {"document_id": "d1"}, "Total : X")])
_c("etat_06_ouverture_enregistree", "_document_workflow_proof_state", (),
   _original_query=Q_REVISION, history=HIST_DEUX_OK,
   _ENCHAINER=[("open_document_delivery", {}, OUVERTURE)])
_c("etat_07_magasin_invalide", "_document_workflow_proof_state", (),
   _original_query=Q_FACTURE, _document_workflow_evidence="pas un dict")


RECU_FIGE = {"id": "recu-epingle-rf5d1", "path": "out/recu.json"}


def instantane(nom: str) -> Dict[str, Any]:
    """Applique le scenario et retourne l'ETAT MUTE.

    FAIL-CLOSED : aucune exception n'est rattrapee.
    `_ENCHAINER` rejoue d'abord des enregistrements reels, pour que
    `_document_workflow_proof_state` soit prouve sur un magasin PRODUIT par
    l'autre racine et non fabrique a la main.
    """
    from src.documents import document_delivery_bundle
    from src.reasoning.react import ReActLoop

    methode, args, etat_source = CAS[nom]
    etat = dict(etat_source)
    enchainer = etat.pop("_ENCHAINER", ())

    boucle = object.__new__(ReActLoop)
    boucle.runtime_ctx = SimpleNamespace(mode="agent")
    boucle.history = []
    for cle, valeur in etat.items():
        setattr(boucle, cle, copy.deepcopy(valeur))

    vrai_save = document_delivery_bundle.save_delivery_reference
    document_delivery_bundle.save_delivery_reference = lambda *a, **k: dict(RECU_FIGE)
    try:
        for outil, arguments, contenu in enchainer:
            ReActLoop._record_document_workflow_evidence(
                boucle, _act(outil, arguments), _obs(contenu, True))
        resultat = getattr(ReActLoop, methode)(boucle, *args)
    finally:
        document_delivery_bundle.save_delivery_reference = vrai_save

    if hasattr(resultat, "__next__"):
        resultat = tuple(resultat)

    magasin = getattr(boucle, "_document_workflow_evidence", None)
    return {
        "retour": repr(resultat),
        "magasin": repr(magasin),
        "reference_id": repr(getattr(boucle, "_document_delivery_reference_id", None)),
        "reference_signature": repr(
            getattr(boucle, "_document_delivery_reference_signature", None)),
        "cible_workflow": repr(getattr(boucle, "_document_workflow_target_proof", None)),
    }


# ════════════════════════════════════════════════════════════════════════
#  La reference, capturee AVANT extraction
# ════════════════════════════════════════════════════════════════════════

BASELINE = {
    "etat_01_vierge": {
        "cible_workflow": "None",
        "magasin": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "{'open': None, 'open_progress': None, 'target': None, 'revision': None, 'verification': None, 'history': None, 'export': None, 'library_verify': None}"
    },
    "etat_02_sans_demande": {
        "cible_workflow": "None",
        "magasin": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "{'open': None, 'open_progress': None, 'target': None, 'revision': None, 'verification': None, 'history': None, 'export': None, 'library_verify': None}"
    },
    "etat_03_apres_generations": {
        "cible_workflow": "None",
        "magasin": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "{'open': None, 'open_progress': {'requested': 2, 'opened': 0, 'failed': 0, 'complete': False, 'receipt_id': '', 'receipt_ids': (), 'files': [], '_event_index': 0}, 'target': None, 'revision': None, 'verification': None, 'history': None, 'export': None, 'library_verify': None}"
    },
    "etat_04_revision_demandee": {
        "cible_workflow": "DocumentDeliveryProof(kind='devis', document_id='d2', filename='devis.pdf', path='out/devis.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2)",
        "magasin": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "{'open': None, 'open_progress': {'requested': 2, 'opened': 0, 'failed': 0, 'complete': False, 'receipt_id': '', 'receipt_ids': (), 'files': [], '_event_index': 0}, 'target': DocumentDeliveryProof(kind='devis', document_id='d2', filename='devis.pdf', path='out/devis.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2), 'revision': None, 'verification': None, 'history': None, 'export': None, 'library_verify': None}"
    },
    "etat_05_magasin_rempli": {
        "cible_workflow": "DocumentDeliveryProof(kind='devis', document_id='d2', filename='devis.pdf', path='out/devis.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2)",
        "magasin": "{'batch_proofs': {}, 'generation_events': [], 'open_events': [], 'revision_events': [({'document_id': 'd1', 'data': {'total': 'X'}}, DocumentDeliveryProof(kind='devis', document_id='d1', filename='facture.pdf', path='out/facture.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=False, thumbnail_path='', page_count=0))], 'revision_records': [{'args': {'document_id': 'd1', 'data': {'total': 'X'}}, 'proof': DocumentDeliveryProof(kind='devis', document_id='d1', filename='facture.pdf', path='out/facture.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=False, thumbnail_path='', page_count=0), 'changed_fields': {'total': 'TEST-REVISION-2026'}, '_event_index': 1}], 'verification_events': [{'args': {'document_id': 'd1'}, 'content': 'Total : X', '_event_index': 2}], 'history_events': [], 'export_events': [], 'library_events': [], 'event_counter': 2}",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "{'open': None, 'open_progress': {'requested': 2, 'opened': 0, 'failed': 0, 'complete': False, 'receipt_id': '', 'receipt_ids': (), 'files': [], '_event_index': 0}, 'target': DocumentDeliveryProof(kind='devis', document_id='d2', filename='devis.pdf', path='out/devis.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2), 'revision': None, 'verification': None, 'history': None, 'export': None, 'library_verify': None}"
    },
    "etat_06_ouverture_enregistree": {
        "cible_workflow": "DocumentDeliveryProof(kind='devis', document_id='d2', filename='devis.pdf', path='out/devis.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2)",
        "magasin": "{'batch_proofs': {}, 'generation_events': [], 'open_events': [{'document_id': 'd1', 'path': 'out/facture.pdf', 'content': 'Total : 1200 EUR', '_event_index': 1, '_receipt_id': ''}], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [], 'library_events': [], 'event_counter': 1}",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "{'open': None, 'open_progress': {'requested': 2, 'opened': 0, 'failed': 0, 'complete': False, 'receipt_id': '', 'receipt_ids': (), 'files': [], '_event_index': 0}, 'target': DocumentDeliveryProof(kind='devis', document_id='d2', filename='devis.pdf', path='out/devis.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2), 'revision': None, 'verification': None, 'history': None, 'export': None, 'library_verify': None}"
    },
    "etat_07_magasin_invalide": {
        "cible_workflow": "None",
        "magasin": "'pas un dict'",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "{'open': None, 'open_progress': None, 'target': None, 'revision': None, 'verification': None, 'history': None, 'export': None, 'library_verify': None}"
    },
    "rec_01_refus_sans_observation": {
        "cible_workflow": "None",
        "magasin": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "None"
    },
    "rec_02_refus_echec": {
        "cible_workflow": "None",
        "magasin": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "None"
    },
    "rec_03_generation_simple": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {}, 'generation_events': [{'proof': DocumentDeliveryProof(kind='facture', document_id='d', filename='facture.pdf', path='out/facture.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2), '_event_index': 1}], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [], 'library_events': [], 'event_counter': 1, 'last_generation_event_index': 1}",
        "reference_id": "''",
        "reference_signature": "()",
        "retour": "None"
    },
    "rec_04_generation_lot": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {'t1': DocumentDeliveryProof(kind='facture', document_id='d1', filename='facture.pdf', path='out/facture.pdf', sha256='', template_id='t1', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2), 't2': DocumentDeliveryProof(kind='devis', document_id='d2', filename='devis.pdf', path='out/devis.pdf', sha256='', template_id='t2', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2)}, 'generation_events': [], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [], 'library_events': [], 'event_counter': 1, 'last_generation_event_index': 1}",
        "reference_id": "''",
        "reference_signature": "()",
        "retour": "None"
    },
    "rec_05_ouverture_livraison": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {}, 'generation_events': [], 'open_events': [{'document_id': 'd1', 'path': 'out/facture.pdf', 'content': 'Total : 1200 EUR', '_event_index': 1, '_receipt_id': ''}], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [], 'library_events': [], 'event_counter': 1}",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "None"
    },
    "rec_06_ouverture_fichier": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {}, 'generation_events': [], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [], 'library_events': [], 'event_counter': 1}",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "None"
    },
    "rec_07_revision": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {}, 'generation_events': [], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [], 'library_events': [], 'event_counter': 1}",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "None"
    },
    "rec_08_lecture_document": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {}, 'generation_events': [], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [{'args': {'document_id': 'd1'}, 'content': 'Total : TEST-REVISION-2026', '_event_index': 1}], 'history_events': [], 'export_events': [], 'library_events': [], 'event_counter': 1}",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "None"
    },
    "rec_09_historique_document": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {}, 'generation_events': [], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [{'args': {'document_id': 'd1'}, 'payload': {'document_id': 'd1', 'versions': [1, 2]}, '_event_index': 1}], 'export_events': [], 'library_events': [], 'event_counter': 1}",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "None"
    },
    "rec_10_export_bibliotheque": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {}, 'generation_events': [], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [{'args': {'document_id': 'd1'}, 'record': {'path': 'out/export.pdf'}, '_event_index': 1}], 'library_events': [], 'event_counter': 1}",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "None"
    },
    "rec_11_conversion_bibliotheque": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {}, 'generation_events': [], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [{'args': {'document_id': 'd1'}, 'record': {'path': 'out/converti.pdf'}, '_event_index': 1}], 'library_events': [], 'event_counter': 1}",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "None"
    },
    "rec_12_recherche_bibliotheque": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {}, 'generation_events': [], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [], 'library_events': [], 'event_counter': 1}",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "None"
    },
    "rec_13_fiche_document": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {}, 'generation_events': [], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [], 'library_events': [{'args': {'document_id': 'd1'}, 'records': [{'id': 'd1', 'path': 'out/facture.pdf'}], '_event_index': 1}], 'event_counter': 1}",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "None"
    },
    "rec_14_outil_hors_perimetre": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {}, 'generation_events': [], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [], 'library_events': [], 'event_counter': 1}",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "None"
    },
    "rec_15_magasin_prealable": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {}, 'generation_events': [{'proof': DocumentDeliveryProof(kind='facture', document_id='d', filename='facture.pdf', path='out/facture.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2), '_event_index': 8}], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [], 'library_events': [], 'event_counter': 8, 'last_generation_event_index': 8}",
        "reference_id": "''",
        "reference_signature": "()",
        "retour": "None"
    },
    "rec_16_magasin_invalide": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {}, 'generation_events': [{'proof': DocumentDeliveryProof(kind='facture', document_id='d', filename='facture.pdf', path='out/facture.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2), '_event_index': 1}], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [], 'library_events': [], 'event_counter': 1, 'last_generation_event_index': 1}",
        "reference_id": "''",
        "reference_signature": "()",
        "retour": "None"
    },
    "rec_17_recu_manifeste_complet": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {'t1': DocumentDeliveryProof(kind='facture', document_id='d1', filename='facture.pdf', path='out/facture.pdf', sha256='', template_id='t1', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2), 't2': DocumentDeliveryProof(kind='devis', document_id='d2', filename='devis.pdf', path='out/devis.pdf', sha256='', template_id='t2', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2)}, 'generation_events': [], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [], 'library_events': [], 'event_counter': 1, 'last_generation_event_index': 1}",
        "reference_id": "'recu-officiel-42'",
        "reference_signature": "(('facture', 'out/facture.pdf', 'not_checked', True), ('devis', 'out/devis.pdf', 'not_checked', True))",
        "retour": "None"
    },
    "rec_18_recu_manifeste_incomplet_refuse": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {'t1': DocumentDeliveryProof(kind='facture', document_id='d1', filename='facture.pdf', path='out/facture.pdf', sha256='', template_id='t1', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2)}, 'generation_events': [], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [], 'library_events': [], 'event_counter': 1, 'last_generation_event_index': 1}",
        "reference_id": "''",
        "reference_signature": "()",
        "retour": "None"
    },
    "rec_19_recu_preuve_non_verifiee_refuse": {
        "cible_workflow": "None",
        "magasin": "{'batch_proofs': {'t1': DocumentDeliveryProof(kind='facture', document_id='d1', filename='facture.pdf', path='out/facture.pdf', sha256='', template_id='t1', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=False, thumbnail_path='', page_count=2), 't2': DocumentDeliveryProof(kind='devis', document_id='d2', filename='devis.pdf', path='out/devis.pdf', sha256='', template_id='t2', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2)}, 'generation_events': [], 'open_events': [], 'revision_events': [], 'revision_records': [], 'verification_events': [], 'history_events': [], 'export_events': [], 'library_events': [], 'event_counter': 1, 'last_generation_event_index': 1}",
        "reference_id": "''",
        "reference_signature": "()",
        "retour": "None"
    }
}


NOMS_RF5D1 = ["_record_document_workflow_evidence", "_document_workflow_proof_state"]


# ══════════════════════════════════════════════════════════════════════════
#  1. Les 26 comparaisons d'etat
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", sorted(CAS))
def test_l_etat_mute_est_identique_a_la_reference(nom):
    obtenu = instantane(nom)
    attendu = BASELINE[nom]
    for cle in ("retour", "magasin", "reference_id",
                "reference_signature", "cible_workflow"):
        assert obtenu[cle] == attendu[cle], (
            f"{nom} : {cle} a change\n"
            f"  attendu : {str(attendu[cle])[:400]}\n"
            f"  obtenu  : {str(obtenu[cle])[:400]}"
        )


def test_le_harnais_est_rejouable():
    for nom in sorted(CAS):
        assert instantane(nom) == instantane(nom), f"{nom} : harnais non rejouable"


def test_les_dix_branches_d_enregistrement_sont_exercees():
    """`_record_document_workflow_evidence` route sur DIX noms d'outil.

    Une matrice qui n'en couvrirait que deux ne prouverait rien. Ce test
    empeche qu'on retire silencieusement des cas.
    """
    import collections

    par = collections.Counter(CAS[n][0] for n in CAS)
    assert par["_record_document_workflow_evidence"] >= 15, (
        f"seulement {par['_record_document_workflow_evidence']} cas d'enregistrement"
    )
    assert par["_document_workflow_proof_state"] >= 6

    outils = {
        CAS[n][1][0].tool_name
        for n in CAS
        if CAS[n][0] == "_record_document_workflow_evidence" and CAS[n][1][0] is not None
    }
    attendus = {
        "generate_studio_document", "generate_studio_documents",
        "open_document_delivery", "open_file", "revise_studio_document",
        "read_document", "get_document_history", "export_library_document",
        "convert_library_document", "search_document_library", "get_document_record",
    }
    manquants = sorted(attendus - outils)
    assert manquants == [], f"branches non exercees : {manquants}"


def test_les_QUATRE_mutations_sont_toutes_exercees():
    """DEFAUT de ma premiere matrice, ferme par ce test.

    Elle ne posait JAMAIS de reference : `_document_delivery_reference_id` et
    `_document_delivery_reference_signature` n'etaient touches par aucun
    scenario. Une matrice qui n'exerce pas deux des quatre mutations de la
    seule methode mutante ne prouve pas grand-chose.

    La branche exige `receipt_id` dans la charge utile (et non `id`) ET un
    manifeste complet.
    """
    magasin = [n for n, v in BASELINE.items() if v["magasin"] != "None"]
    reference = [n for n, v in BASELINE.items()
                 if v["reference_id"] not in ("None", "''")]
    signature = [n for n, v in BASELINE.items()
                 if v["reference_signature"] not in ("None", "()")]
    cible = [n for n, v in BASELINE.items() if v["cible_workflow"] != "None"]

    assert len(magasin) >= 15, f"mutation 1 sous-exercee : {len(magasin)}"
    assert reference, "mutation 2 (_document_delivery_reference_id) jamais exercee"
    assert signature, "mutation 3 (_document_delivery_reference_signature) jamais exercee"
    assert len(cible) >= 3, f"mutation 4 sous-exercee : {cible}"


def test_le_recu_n_est_pose_que_sur_un_manifeste_COMPLET():
    """Le truth-lock, cote enregistrement : un `receipt_id` ne devient une
    reference que si le manifeste est complet ET verifie."""
    assert BASELINE["rec_17_recu_manifeste_complet"]["reference_id"] == repr(
        "recu-officiel-42")
    for refus in ("rec_18_recu_manifeste_incomplet_refuse",
                  "rec_19_recu_preuve_non_verifiee_refuse"):
        assert BASELINE[refus]["reference_id"] == repr(""), (
            f"{refus} : une reference a ete posee sur un manifeste non probant"
        )


def test_la_matrice_discrimine():
    import json as _json

    distincts = {_json.dumps(v, sort_keys=True) for v in BASELINE.values()}
    assert len(distincts) >= 18, f"matrice trop pauvre : {len(distincts)} etats"


# ══════════════════════════════════════════════════════════════════════════
#  2. HERMETICITE — lecon RF-3, appliquee a la conception
# ══════════════════════════════════════════════════════════════════════════


def test_la_matrice_n_ecrit_jamais_sur_le_disque():
    from src.documents import document_delivery_bundle

    appels = {"n": 0}
    vrai = document_delivery_bundle.save_delivery_reference

    def _compteur(*a, **k):
        appels["n"] += 1
        return vrai(*a, **k)

    document_delivery_bundle.save_delivery_reference = _compteur
    try:
        for nom in sorted(CAS):
            instantane(nom)
    finally:
        document_delivery_bundle.save_delivery_reference = vrai

    assert appels["n"] == 0, (
        f"{appels['n']} ecriture(s) disque reelle(s) : la reference derivera"
    )


# ══════════════════════════════════════════════════════════════════════════
#  3. Fermeture, contrat d'etat et reexports
# ══════════════════════════════════════════════════════════════════════════


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


def test_les_trois_contrats_d_etat_restent_emboites_et_intacts():
    """Chaque sous-lot garde SON contrat. RF-5d1 n'elargit ni celui de RF-5c
    ni celui de RF-5b : il les porte."""
    import dataclasses

    from src.reasoning.document_runtime import (
        EntreeDocumentCatalogue,
        EntreeLivraisonDocument,
        EntreeWorkflowDocument,
    )

    assert len(dataclasses.fields(EntreeDocumentCatalogue)) == 14, "RF-5b elargi"
    assert len(dataclasses.fields(EntreeLivraisonDocument)) == 9, "RF-5c elargi"
    champs = {c.name for c in dataclasses.fields(EntreeWorkflowDocument)}
    assert champs == {"livraison", "obtenir_preuves_workflow_ou_none",
                      "definir_preuves_workflow"}, champs
    assert EntreeWorkflowDocument.__dataclass_params__.frozen


def test_les_deux_formes_de_lecture_du_magasin_sont_preservees():
    """`_document_workflow_evidence` est lu avec le defaut `None` d'un cote et
    `{}` de l'autre. Une seule valeur ne peut pas rendre les deux.

    Troisieme occurrence du motif apres le `execution_ledger` de RF-4 et le
    `_document_catalog_evidence` de RF-5b.
    """
    from types import SimpleNamespace as _SN

    import src.reasoning.react as react_mod

    entree = react_mod._entree_workflow_document(_SN())
    assert entree.obtenir_preuves_workflow_ou_none() is None
    assert entree.livraison.obtenir_preuves_workflow() == {}

    module = NOUVEAU.read_text(encoding="utf-8")
    assert "e.obtenir_preuves_workflow_ou_none()" in module
    assert "e.livraison.obtenir_preuves_workflow()" in module


def test_la_quatrieme_mutation_reste_portee_par_react():
    """Invariant 5 : la derniere mutation de la famille documentaire."""
    import inspect as _inspect

    import src.reasoning.react as react_mod

    source = _inspect.getsource(react_mod._entree_workflow_document)
    assert "etat._document_workflow_evidence = valeur" in source
    assert "e.definir_preuves_workflow(store)" in NOUVEAU.read_text(encoding="utf-8")


def test_la_fabrique_de_workflow_est_une_FONCTION_DE_MODULE():
    import inspect as _inspect
    from types import SimpleNamespace as _SN

    import src.reasoning.react as react_mod

    assert hasattr(react_mod, "_entree_workflow_document")
    assert not hasattr(react_mod.ReActLoop, "_entree_workflow_document"), (
        "la fabrique est une methode : le duck-typing des sites d'appel est casse"
    )
    assert list(_inspect.signature(
        react_mod._entree_workflow_document).parameters) == ["etat"]
    react_mod._entree_workflow_document(_SN())      # doit fonctionner sur un sac nu


def test_les_deux_methodes_acceptent_un_sac_d_etat_qui_n_est_pas_un_ReActLoop():
    from types import SimpleNamespace as _SN

    from src.reasoning.react import ReActLoop

    sac = _SN(runtime_ctx=_SN(mode="agent"), _original_query="Bonjour", history=[])
    assert ReActLoop._record_document_workflow_evidence(sac, None, None) is None
    etat = ReActLoop._document_workflow_proof_state(sac)
    assert isinstance(etat, dict)


@pytest.mark.parametrize("nom", NOMS_RF5D1)
def test_le_reexport_et_la_signature_sont_inchanges(nom):
    import inspect as _inspect

    from src.reasoning.react import ReActLoop

    REFERENCE = {
        "_record_document_workflow_evidence": ["self", "action", "observation"],
        "_document_workflow_proof_state": ["self"],
    }
    assert hasattr(ReActLoop, nom), f"reexport disparu : {nom}"
    sig = _inspect.signature(getattr(ReActLoop, nom))
    assert list(sig.parameters) == REFERENCE[nom], (
        f"{nom} : signature publique modifiee -> {list(sig.parameters)}"
    )


def test_l_ecriture_multiligne_de_la_signature_n_a_pas_ete_coupee():
    """Piege de l'extraction : `self._document_delivery_reference_signature`
    etait ecrit sous DEUX formes, `= ()` et `= (` suivi d'une continuation.

    Remplacer la seconde avant la premiere aurait laisse une parenthese
    orpheline — le module n'aurait meme pas compile, mais le motif merite d'etre
    verrouille : les deux formes doivent exister dans le module extrait.
    """
    module = NOUVEAU.read_text(encoding="utf-8")
    assert "e.livraison.definir_reference_signature(())" in module, (
        "la forme `= ()` a disparu"
    )
    assert "e.livraison.definir_reference_signature(\n" in module, (
        "la forme multiligne a disparu"
    )
    ast.parse(module)     # et le module compile
