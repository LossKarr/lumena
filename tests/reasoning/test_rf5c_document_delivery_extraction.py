"""RF-5c — matrice d'ETAT de la verite de livraison documentaire.

Lot RF-5c du plan `plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md`.
Ecrit AVANT l'extraction ; la reference est capturee sur le code d'origine.

Cinq methodes, 208 lignes, ilot ferme par le graphe de dependance :
`_structured_document_delivery_manifest` et `_document_delivery_truth_required`
sont des feuilles ; les trois autres ne dependent que du manifeste.

Le mini-plan en annoncait six. `_document_workflow_pending_action` a ete
ecartee : elle depend de `_document_workflow_proof_state` (261 lignes,
RF-5d), ce qui aurait fait passer la cloture de 208 a 435 lignes. Troisieme
correction de composition du chantier — et la premiere que le graphe attrape
AVANT qu'une ligne soit ecrite.

--- Ce sous-lot porte le truth-lock de livraison ---

Invariant du plan : *une livraison n'est annoncee que pour des fichiers
existants et verifies*. La matrice exerce donc explicitement les trois refus :
manifeste incomplet, preuve non verifiee, nombre de pages insuffisant.

--- HERMETICITE : la lecon de l'incident RF-3 ---

`_ensure_document_delivery_reference` ECRIT UN RECU SUR DISQUE via
`save_delivery_reference`. Laisser cette ecriture s'executer rendrait la
matrice dependante d'un magasin persistant — exactement le defaut qui a fait
deriver les 20 empreintes de RF-3 de -63 caracteres en un jour.

L'ecriture est donc EPINGLEE : `save_delivery_reference` rend un recu
deterministe, et `get_document_studio` n'est jamais atteint. Le chemin de
decision reste integralement parcouru ; seule sa sortie disque est figee.
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


def _etape(tool_name, tool_args=None, observation=None):
    return SimpleNamespace(
        action=SimpleNamespace(tool_name=tool_name, tool_args=tool_args or {}),
        observation=observation,
    )


def _sub(tool_name, args=None, success=True, content=""):
    return SimpleNamespace(tool_name=tool_name, args=args or {},
                           success=success, content=content)


def _preuve(kind, *, template_id="", doc="d", chemin=None, verifie=True, pages=2):
    """Charge utile d'une preuve de generation Studio."""
    return json.dumps({
        "kind": kind,
        "document_id": doc,
        "path": chemin or f"out/{kind}.pdf",
        "filename": f"{kind}.pdf",
        "template_id": template_id,
        "render_verified": verifie,
        "page_count": pages,
    })


# Requetes reelles : on ne fabrique pas de route, on la fait resoudre.
Q_FACTURE = "Genere la facture Dupont"
Q_DEUX = "Genere la facture ET le devis pour Dupont"
Q_REVISION = (
    "Genere la facture et le devis. Sur le deuxieme document, remplace la "
    "valeur du champ total par TEST-REVISION-2026, puis verifie la nouvelle "
    "version."
)

HIST_FACTURE_OK = [
    _etape("generate_studio_document", {"kind": "facture"},
           _obs(_preuve("facture"), True)),
]
HIST_FACTURE_NON_VERIFIEE = [
    _etape("generate_studio_document", {"kind": "facture"},
           _obs(_preuve("facture", verifie=False), True)),
]
HIST_DEUX_OK = [
    _etape("generate_studio_document", {"kind": "facture"},
           _obs(_preuve("facture", doc="d1"), True)),
    _etape("generate_studio_document", {"kind": "devis"},
           _obs(_preuve("devis", doc="d2"), True)),
]
HIST_DEUX_PARTIEL = [
    _etape("generate_studio_document", {"kind": "facture"},
           _obs(_preuve("facture", doc="d1"), True)),
]
HIST_ECHEC = [
    _etape("generate_studio_document", {"kind": "facture"},
           _obs("❌ echec de generation", False)),
]
HIST_GENERIQUE = [
    _etape("create_pdf", {"title": "facture"}, _obs("✅ PDF cree", True)),
]


CAS: Dict[str, Tuple[str, tuple, Dict[str, Any]]] = {}


def _c(nom, methode, args=(), **etat):
    CAS[nom] = (methode, args, etat)


# ── _structured_document_delivery_manifest ───────────────────────────────────
_c("man_01_complet", "_structured_document_delivery_manifest", (),
   _original_query=Q_FACTURE, history=HIST_FACTURE_OK)
_c("man_02_deux_complets", "_structured_document_delivery_manifest", (),
   _original_query=Q_DEUX, history=HIST_DEUX_OK)
_c("man_03_partiel", "_structured_document_delivery_manifest", (),
   _original_query=Q_DEUX, history=HIST_DEUX_PARTIEL)
_c("man_04_non_verifie", "_structured_document_delivery_manifest", (),
   _original_query=Q_FACTURE, history=HIST_FACTURE_NON_VERIFIEE)
_c("man_05_historique_vide", "_structured_document_delivery_manifest", (),
   _original_query=Q_FACTURE, history=[])
_c("man_06_echec", "_structured_document_delivery_manifest", (),
   _original_query=Q_FACTURE, history=HIST_ECHEC)
_c("man_07_sans_demande", "_structured_document_delivery_manifest", (),
   _original_query="Bonjour, comment vas-tu ?", history=[])
_c("man_08_outil_generique", "_structured_document_delivery_manifest", (),
   _original_query=Q_FACTURE, history=HIST_GENERIQUE)

# ── _structured_document_delivery_progress ───────────────────────────────────
_c("prg_01_complet", "_structured_document_delivery_progress", (),
   _original_query=Q_FACTURE, history=HIST_FACTURE_OK)
_c("prg_02_deux_complets", "_structured_document_delivery_progress", (),
   _original_query=Q_DEUX, history=HIST_DEUX_OK)
_c("prg_03_partiel", "_structured_document_delivery_progress", (),
   _original_query=Q_DEUX, history=HIST_DEUX_PARTIEL)
_c("prg_04_repli_generique", "_structured_document_delivery_progress", (),
   _original_query=Q_FACTURE, history=HIST_GENERIQUE)
_c("prg_05_sans_demande", "_structured_document_delivery_progress", (),
   _original_query="Bonjour", history=[])
_c("prg_06_echec", "_structured_document_delivery_progress", (),
   _original_query=Q_FACTURE, history=HIST_ECHEC)

# ── _ensure_document_delivery_reference — le TRUTH-LOCK ──────────────────────
_c("ref_01_complet_persiste", "_ensure_document_delivery_reference", (),
   _original_query=Q_FACTURE, history=HIST_FACTURE_OK)
_c("ref_02_manifeste_incomplet_refuse", "_ensure_document_delivery_reference", (),
   _original_query=Q_DEUX, history=HIST_DEUX_PARTIEL)
_c("ref_03_preuve_non_verifiee_refuse", "_ensure_document_delivery_reference", (),
   _original_query=Q_FACTURE, history=HIST_FACTURE_NON_VERIFIEE)
_c("ref_04_aucune_demande_refuse", "_ensure_document_delivery_reference", (),
   _original_query="Bonjour", history=[])
_c("ref_05_historique_vide_refuse", "_ensure_document_delivery_reference", (),
   _original_query=Q_FACTURE, history=[])
_c("ref_06_reference_deja_posee", "_ensure_document_delivery_reference", (),
   _original_query=Q_FACTURE, history=HIST_FACTURE_OK,
   _document_delivery_reference_id="deja-pose")
_c("ref_07_deux_documents", "_ensure_document_delivery_reference", (),
   _original_query=Q_DEUX, history=HIST_DEUX_OK)

# ── _document_workflow_target ────────────────────────────────────────────────
_c("tgt_01_sans_revision", "_document_workflow_target", (),
   _original_query=Q_DEUX, history=HIST_DEUX_OK)
_c("tgt_02_avec_revision", "_document_workflow_target", (),
   _original_query=Q_REVISION, history=HIST_DEUX_OK)
_c("tgt_03_manifeste_incomplet", "_document_workflow_target", (),
   _original_query=Q_REVISION, history=HIST_DEUX_PARTIEL)
_c("tgt_04_cache_prioritaire", "_document_workflow_target", (),
   _original_query=Q_REVISION, history=HIST_DEUX_OK,
   _document_workflow_target_proof="CIBLE-EN-CACHE")
_c("tgt_05_historique_vide", "_document_workflow_target", (),
   _original_query=Q_REVISION, history=[])

# ── _document_delivery_truth_required ────────────────────────────────────────
def _route(q):
    from src.documents.document_intent import resolve_document_route
    return resolve_document_route(q, mode="agent")


_c("trh_01_facture_1", "_document_delivery_truth_required", ("__ROUTE__" + Q_FACTURE, 1))
_c("trh_02_facture_0", "_document_delivery_truth_required", ("__ROUTE__" + Q_FACTURE, 0))
_c("trh_03_chat_1", "_document_delivery_truth_required", ("__ROUTE__Bonjour", 1))
_c("trh_04_deux_2", "_document_delivery_truth_required", ("__ROUTE__" + Q_DEUX, 2))


RECU_FIGE = {"id": "recu-epingle-rf5c", "path": "out/recu.json"}


def instantane(nom: str) -> Dict[str, Any]:
    """Applique le scenario et retourne l'ETAT MUTE.

    FAIL-CLOSED : aucune exception n'est rattrapee.

    L'ecriture disque de `save_delivery_reference` est EPINGLEE (voir l'en-tete)
    pour que la matrice reste hermetique.
    """
    from src.documents import document_delivery_bundle
    from src.reasoning.react import ReActLoop

    methode, args, etat_source = CAS[nom]
    etat = dict(etat_source)

    boucle = object.__new__(ReActLoop)
    boucle.runtime_ctx = SimpleNamespace(mode="agent")
    for cle, valeur in etat.items():
        setattr(boucle, cle, copy.deepcopy(valeur) if cle.startswith("_document_") else valeur)

    args_reels = tuple(
        _route(a[len("__ROUTE__"):]) if isinstance(a, str) and a.startswith("__ROUTE__") else a
        for a in args
    )

    import inspect as _inspect

    # `_document_delivery_truth_required` est un @staticmethod : il ne recoit
    # PAS de sac d'etat. La forme du descripteur fait partie du contrat
    # (invariant 13) et le harnais doit la respecter.
    est_statique = isinstance(
        _inspect.getattr_static(ReActLoop, methode), staticmethod)

    vrai_save = document_delivery_bundle.save_delivery_reference
    document_delivery_bundle.save_delivery_reference = lambda *a, **k: dict(RECU_FIGE)
    try:
        fonction = getattr(ReActLoop, methode)
        resultat = (fonction(*args_reels) if est_statique
                    else fonction(boucle, *args_reels))
    finally:
        document_delivery_bundle.save_delivery_reference = vrai_save

    if hasattr(resultat, "__next__"):
        resultat = tuple(resultat)
    return {
        "retour": repr(resultat),
        "reference_id": repr(getattr(boucle, "_document_delivery_reference_id", None)),
        "reference_signature": repr(getattr(boucle, "_document_delivery_reference_signature", None)),
        "cible_workflow": repr(getattr(boucle, "_document_workflow_target_proof", None)),
    }


# ════════════════════════════════════════════════════════════════════════
#  La reference, capturee AVANT extraction
# ════════════════════════════════════════════════════════════════════════

BASELINE = {
    "man_01_complet": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "((DocumentDeliveryProof(kind='facture', document_id='d', filename='facture.pdf', path='out/facture.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2),), (), ())"
    },
    "man_02_deux_complets": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "((DocumentDeliveryProof(kind='facture', document_id='d1', filename='facture.pdf', path='out/facture.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2), DocumentDeliveryProof(kind='devis', document_id='d2', filename='devis.pdf', path='out/devis.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2)), (), ())"
    },
    "man_03_partiel": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "((DocumentDeliveryProof(kind='facture', document_id='d1', filename='facture.pdf', path='out/facture.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2),), ('devis',), ())"
    },
    "man_04_non_verifie": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "((DocumentDeliveryProof(kind='facture', document_id='d', filename='facture.pdf', path='out/facture.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=False, thumbnail_path='', page_count=2),), (), ('facture',))"
    },
    "man_05_historique_vide": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "((), ('facture',), ())"
    },
    "man_06_echec": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "((), ('facture',), ())"
    },
    "man_07_sans_demande": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "((), (), ())"
    },
    "man_08_outil_generique": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "((), ('facture',), ())"
    },
    "prg_01_complet": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "(1, 1, ())"
    },
    "prg_02_deux_complets": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "(2, 2, ())"
    },
    "prg_03_partiel": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "(2, 1, ('devis',))"
    },
    "prg_04_repli_generique": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "(1, 1, ())"
    },
    "prg_05_sans_demande": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "(0, 0, ())"
    },
    "prg_06_echec": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "(1, 0, ('facture',))"
    },
    "ref_01_complet_persiste": {
        "cible_workflow": "None",
        "reference_id": "'recu-epingle-rf5c'",
        "reference_signature": "(('facture', 'out/facture.pdf', 'not_checked', True),)",
        "retour": "'recu-epingle-rf5c'"
    },
    "ref_02_manifeste_incomplet_refuse": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "''"
    },
    "ref_03_preuve_non_verifiee_refuse": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "''"
    },
    "ref_04_aucune_demande_refuse": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "''"
    },
    "ref_05_historique_vide_refuse": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "''"
    },
    "ref_06_reference_deja_posee": {
        "cible_workflow": "None",
        "reference_id": "'recu-epingle-rf5c'",
        "reference_signature": "(('facture', 'out/facture.pdf', 'not_checked', True),)",
        "retour": "'recu-epingle-rf5c'"
    },
    "ref_07_deux_documents": {
        "cible_workflow": "None",
        "reference_id": "'recu-epingle-rf5c'",
        "reference_signature": "(('facture', 'out/facture.pdf', 'not_checked', True), ('devis', 'out/devis.pdf', 'not_checked', True))",
        "retour": "'recu-epingle-rf5c'"
    },
    "tgt_01_sans_revision": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "None"
    },
    "tgt_02_avec_revision": {
        "cible_workflow": "DocumentDeliveryProof(kind='devis', document_id='d2', filename='devis.pdf', path='out/devis.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2)",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "DocumentDeliveryProof(kind='devis', document_id='d2', filename='devis.pdf', path='out/devis.pdf', sha256='', template_id='', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=True, thumbnail_path='', page_count=2)"
    },
    "tgt_03_manifeste_incomplet": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "None"
    },
    "tgt_04_cache_prioritaire": {
        "cible_workflow": "'CIBLE-EN-CACHE'",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "'CIBLE-EN-CACHE'"
    },
    "tgt_05_historique_vide": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "None"
    },
    "trh_01_facture_1": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "True"
    },
    "trh_02_facture_0": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "False"
    },
    "trh_03_chat_1": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "False"
    },
    "trh_04_deux_2": {
        "cible_workflow": "None",
        "reference_id": "None",
        "reference_signature": "None",
        "retour": "True"
    }
}


NOMS_RF5C = [
    "_structured_document_delivery_progress", "_structured_document_delivery_manifest",
    "_ensure_document_delivery_reference", "_document_workflow_target",
    "_document_delivery_truth_required",
]


# ══════════════════════════════════════════════════════════════════════════
#  1. Les 30 comparaisons d'etat
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", sorted(CAS))
def test_l_etat_mute_est_identique_a_la_reference(nom):
    obtenu = instantane(nom)
    attendu = BASELINE[nom]
    for cle in ("retour", "reference_id", "reference_signature", "cible_workflow"):
        assert obtenu[cle] == attendu[cle], (
            f"{nom} : {cle} a change\n  attendu : {attendu[cle]}\n  obtenu  : {obtenu[cle]}"
        )


def test_le_harnais_est_rejouable():
    for nom in sorted(CAS):
        assert instantane(nom) == instantane(nom), f"{nom} : harnais non rejouable"


def test_la_matrice_couvre_les_cinq_methodes_et_discrimine():
    import collections
    import json as _json

    par = collections.Counter(CAS[n][0] for n in CAS)
    assert len(par) == 5, f"{len(par)} methodes exercees au lieu de 5"
    assert min(par.values()) >= 4, f"methode sous-exercee : {min(par, key=par.get)}"
    distincts = {_json.dumps(v, sort_keys=True) for v in BASELINE.values()}
    assert len(distincts) >= 15, f"matrice trop pauvre : {len(distincts)} etats"


# ══════════════════════════════════════════════════════════════════════════
#  2. Le TRUTH-LOCK de livraison
# ══════════════════════════════════════════════════════════════════════════


def test_une_livraison_n_est_annoncee_que_si_le_manifeste_est_COMPLET():
    """Invariant du plan : *une livraison n'est annoncee que pour des fichiers
    existants et verifies*.

    Les quatre refus sont exerces nommement : manifeste incomplet, preuve non
    verifiee, aucune demande, historique vide. Aucun ne doit produire de
    reference.
    """
    refus = [
        "ref_02_manifeste_incomplet_refuse",
        "ref_03_preuve_non_verifiee_refuse",
        "ref_04_aucune_demande_refuse",
        "ref_05_historique_vide_refuse",
    ]
    for nom in refus:
        v = instantane(nom)
        assert v["retour"] == repr(""), f"{nom} : une reference a ete produite"
        assert v["reference_id"] == repr(None), (
            f"{nom} : l'etat porte une reference alors que la livraison est refusee"
        )

    # et le chemin d'acceptation existe bel et bien
    acceptes = ["ref_01_complet_persiste", "ref_07_deux_documents"]
    for nom in acceptes:
        v = instantane(nom)
        assert v["retour"] != repr(""), f"{nom} : le chemin d'acceptation ne passe plus"
        assert v["reference_id"] != repr(None)


def test_une_exception_ne_devient_jamais_une_reference():
    """Invariant 6 : une exception ne devient jamais une autorisation.

    On fait echouer la persistance et on verifie que la methode rend "" — pas
    une reference, pas une exception qui remonte.
    """
    from types import SimpleNamespace as _SN

    from src.documents import document_delivery_bundle
    from src.reasoning.react import ReActLoop

    methode, args, etat = CAS["ref_01_complet_persiste"]
    boucle = object.__new__(ReActLoop)
    boucle.runtime_ctx = _SN(mode="agent")
    for cle, valeur in etat.items():
        setattr(boucle, cle, valeur)

    vrai = document_delivery_bundle.save_delivery_reference

    def _explose(*a, **k):
        raise RuntimeError("persistance impossible (simule RF-5c)")

    document_delivery_bundle.save_delivery_reference = _explose
    try:
        resultat = ReActLoop._ensure_document_delivery_reference(boucle)
    finally:
        document_delivery_bundle.save_delivery_reference = vrai

    assert resultat == "", "l'echec de persistance a produit une reference"
    assert getattr(boucle, "_document_delivery_reference_id", None) is None, (
        "l'etat porte une reference alors que la persistance a echoue"
    )


# ══════════════════════════════════════════════════════════════════════════
#  3. HERMETICITE — la lecon de l'incident RF-3
# ══════════════════════════════════════════════════════════════════════════


def test_la_matrice_n_ecrit_jamais_sur_le_disque():
    """`_ensure_document_delivery_reference` ecrit un recu sur disque.

    L'incident RF-3 a montre qu'une preuve dependant d'un magasin persistant
    n'est pas une preuve mais un instantane : les 20 empreintes du prompt ont
    derive de -63 caracteres en un jour. Ce test verifie que l'ecriture reelle
    n'est JAMAIS atteinte pendant la matrice.
    """
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
        f"{appels['n']} ecriture(s) disque reelle(s) pendant la matrice : "
        "l'epinglage ne tient pas et la reference derivera"
    )


# ══════════════════════════════════════════════════════════════════════════
#  4. Fermeture, contrat d'etat et reexports
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


def test_l_entree_de_livraison_porte_celle_du_catalogue():
    """Chaque sous-lot garde SON contrat : RF-5c n'elargit pas les 14 champs
    figes de RF-5b, il les porte dans un champ `catalogue`."""
    import dataclasses

    from src.reasoning.document_runtime import (
        EntreeDocumentCatalogue,
        EntreeLivraisonDocument,
    )

    champs = {c.name: c for c in dataclasses.fields(EntreeLivraisonDocument)}
    assert len(champs) == 9, f"{len(champs)} champs au lieu de 9"
    assert EntreeLivraisonDocument.__dataclass_params__.frozen
    assert "catalogue" in champs
    assert len(dataclasses.fields(EntreeDocumentCatalogue)) == 14, (
        "le contrat de RF-5b a ete elargi : chaque sous-lot doit garder le sien"
    )
    non_appelables = [n for n, c in champs.items()
                      if n != "catalogue" and "Callable" not in str(c.type)]
    assert non_appelables == [], f"champs pre-calcules : {non_appelables}"


def test_la_fabrique_de_livraison_est_une_FONCTION_DE_MODULE():
    """La lecon de RF-5b, appliquee sans la reapprendre : 80 tests rouges."""
    import inspect as _inspect
    from types import SimpleNamespace as _SN

    import src.reasoning.react as react_mod

    assert hasattr(react_mod, "_entree_livraison_document")
    assert not hasattr(react_mod.ReActLoop, "_entree_livraison_document"), (
        "la fabrique est une methode : le duck-typing des sites d'appel est casse"
    )
    assert list(_inspect.signature(
        react_mod._entree_livraison_document).parameters) == ["etat"]

    entree = react_mod._entree_livraison_document(_SN())
    assert entree.obtenir_reference_id() == ""
    assert entree.obtenir_reference_signature() == ()
    assert entree.obtenir_cible_workflow() is None
    assert entree.obtenir_preuves_workflow() == {}


def test_les_cinq_methodes_acceptent_un_sac_d_etat_qui_n_est_pas_un_ReActLoop():
    from types import SimpleNamespace as _SN

    from src.reasoning.react import ReActLoop

    sac = _SN(runtime_ctx=_SN(mode="agent"), _original_query="Bonjour", history=[])
    assert ReActLoop._structured_document_delivery_manifest(sac) == ((), (), ())
    assert ReActLoop._structured_document_delivery_progress(sac) == (0, 0, ())
    assert ReActLoop._ensure_document_delivery_reference(sac) == ""
    assert ReActLoop._document_workflow_target(sac) is None


def test_les_trois_mutations_du_truth_lock_restent_portees_par_react():
    """Invariant 5."""
    import inspect as _inspect

    import src.reasoning.react as react_mod

    source = _inspect.getsource(react_mod._entree_livraison_document)
    for ecriture in (
        "etat._document_delivery_reference_id = valeur",
        "etat._document_delivery_reference_signature = valeur",
        "etat._document_workflow_target_proof = valeur",
    ):
        assert ecriture in source, f"mutation absente de react.py : {ecriture}"

    module = NOUVEAU.read_text(encoding="utf-8")
    for appel in ("e.definir_reference_id(", "e.definir_reference_signature(",
                  "e.definir_cible_workflow("):
        assert appel in module, f"le module n'utilise pas la fermeture : {appel}"


@pytest.mark.parametrize("nom", NOMS_RF5C)
def test_le_reexport_et_la_signature_sont_inchanges(nom):
    import inspect as _inspect

    from src.reasoning.react import ReActLoop

    REFERENCE = {
        "_structured_document_delivery_progress": ["self"],
        "_structured_document_delivery_manifest": ["self"],
        "_ensure_document_delivery_reference": ["self"],
        "_document_workflow_target": ["self"],
        "_document_delivery_truth_required": ["route", "requested_count"],
    }
    assert hasattr(ReActLoop, nom), f"reexport disparu : {nom}"
    sig = _inspect.signature(getattr(ReActLoop, nom))
    assert list(sig.parameters) == REFERENCE[nom], (
        f"{nom} : signature publique modifiee -> {list(sig.parameters)}"
    )


def test_document_delivery_truth_required_reste_un_staticmethod():
    """Invariant 13 : la forme du descripteur fait partie du contrat.

    C'est aussi ce qui a fait echouer la premiere version du harnais, qui lui
    passait un sac d'etat qu'elle ne prend pas.
    """
    import inspect as _inspect

    from src.reasoning.react import ReActLoop

    brut = _inspect.getattr_static(ReActLoop, "_document_delivery_truth_required")
    assert isinstance(brut, staticmethod), f"forme changee : {type(brut)}"
