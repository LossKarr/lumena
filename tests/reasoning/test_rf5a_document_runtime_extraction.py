"""RF-5a — matrice de VALEURS DE RETOUR des 17 lectrices documentaires.

Lot RF-5a du plan `plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md`.
Ecrit AVANT l'extraction ; la reference est capturee sur le code d'origine.

Les 17 methodes du sous-lot sont pures : zero mutation, zero appel sortant.
La preuve compare donc directement ce qu'elles RETOURNENT.

Onze d'entre elles ne sont nommees par AUCUN test du depot. Pour celles-la,
cette matrice est la seule preuve : elles sont marquees `SANS_TEST` ci-dessous
et chacune est exercee nommement, sur plusieurs entrees dont les cas degenerés
(None, chaine vide, JSON casse, type inattendu).
"""
from __future__ import annotations

import ast
import builtins
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple

import pytest

RACINE = Path(__file__).resolve().parents[2]
REACT = RACINE / "src" / "reasoning" / "react.py"
NOUVEAU = RACINE / "src" / "reasoning" / "document_runtime.py"

SANS_TEST = {
    "_document_tool_events", "_document_catalog_rows", "_document_parallel_calls",
    "_document_revision_patch", "_document_revision_changed_fields",
    "_document_patch_scalar_values", "_document_paths_match",
    "_document_verification_text", "_latest_document_batch_proofs",
    "_nested_document_bypass", "_studio_attempted_kinds",
}


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


PREUVE_LOT = (
    '{"template_id": "facture_std", "document_id": "d1", "path": "out/f.pdf", '
    '"kind": "facture"}'
)

# --- historiques reutilisables ---
HIST_VIDE: List[Any] = []
HIST_SIMPLE = [
    _etape("generate_studio_document", {"kind": "facture"}, _obs("ok", True)),
    _etape("read_file", {"path": "a.txt"}, _obs("contenu", True)),
]
HIST_SANS_OBS = [_etape("generate_studio_document", {"kind": "devis"}, None)]
HIST_SANS_ACTION = [SimpleNamespace(action=None, observation=_obs("x"))]
HIST_PARALLELE = [
    _etape("parallel_tools", {}, _obs("", True, sub=[
        _sub("generate_studio_document", {"kind": "facture"}, True, "ok1"),
        _sub("create_pdf", {"title": "r"}, False, "echec"),
    ])),
]
HIST_LOT = [
    _etape("generate_studio_documents", {}, _obs("", True, sub=[
        _sub("generate_studio_documents", {"kind": "facture"}, True, PREUVE_LOT),
        _sub("generate_studio_documents", {"kind": "devis"}, False, "echec"),
    ])),
]


def _route(kinds=()):
    from src.documents.document_intent import DocumentRoute
    import dataclasses
    champs = {f.name for f in dataclasses.fields(DocumentRoute)}
    base: Dict[str, Any] = {}
    if "requested_kinds" in champs:
        base["requested_kinds"] = tuple(kinds)
    try:
        return DocumentRoute(**base)
    except Exception:
        return SimpleNamespace(requested_kinds=tuple(kinds))


# Chaque cas : (methode, args positionnels, etat)
#   etat = dict des attributs a poser sur l'objet passe en 1er argument,
#          ou None pour les @staticmethod.
CAS: Dict[str, Tuple[str, tuple, Any]] = {}


def _c(nom, methode, args=(), etat=None):
    CAS[nom] = (methode, args, etat)


# ── _document_tool_events (SANS_TEST) ────────────────────────────────────────
_c("evt_01_vide", "_document_tool_events", (), {"history": HIST_VIDE})
_c("evt_02_simple", "_document_tool_events", (), {"history": HIST_SIMPLE})
_c("evt_03_sans_observation", "_document_tool_events", (), {"history": HIST_SANS_OBS})
_c("evt_04_sans_action", "_document_tool_events", (), {"history": HIST_SANS_ACTION})
_c("evt_05_parallele", "_document_tool_events", (), {"history": HIST_PARALLELE})
_c("evt_06_pas_d_attribut", "_document_tool_events", (), {})

# ── _document_catalog_evidence_key ───────────────────────────────────────────
_c("cle_01_complet", "_document_catalog_evidence_key",
   ({"origin": " Studio ", "limit": "5", "sort": " NOM "},))
_c("cle_02_none", "_document_catalog_evidence_key", (None,))
_c("cle_03_limite_invalide", "_document_catalog_evidence_key", ({"limit": "abc"},))
_c("cle_04_vide", "_document_catalog_evidence_key", ({},))

# ── _document_catalog_rows (SANS_TEST) ───────────────────────────────────────
_c("rows_01_json", "_document_catalog_rows",
   ('{"models": [{"id": "a"}, {"id": "b"}, "pas_un_dict"]}',))
_c("rows_02_dict", "_document_catalog_rows", ({"models": [{"id": "z"}]},))
_c("rows_03_json_casse", "_document_catalog_rows", ("{pas du json",))
_c("rows_04_sans_models", "_document_catalog_rows", ('{"autre": 1}',))
_c("rows_05_none", "_document_catalog_rows", (None,))

# ── _document_parallel_calls (SANS_TEST) ─────────────────────────────────────
_c("par_01_liste", "_document_parallel_calls",
   ({"tool_calls": [{"name": "create_pdf", "args": {"t": 1}},
                    {"tool": "create_docx", "x": 2}]},))
_c("par_02_json", "_document_parallel_calls",
   ({"tool_calls": '[{"name": "create_pdf", "arguments": {"t": 1}}]'},))
_c("par_03_json_casse", "_document_parallel_calls", ({"tool_calls": "{casse"},))
_c("par_04_none", "_document_parallel_calls", (None,))
_c("par_05_non_liste", "_document_parallel_calls", ({"tool_calls": {"a": 1}},))

# ── _duplicate_document_mutation ─────────────────────────────────────────────
_c("dup_01_identiques", "_duplicate_document_mutation",
   ("generate_studio_document", {"kind": "facture"},
    "generate_studio_document", {"kind": "facture"}))
_c("dup_02_differents", "_duplicate_document_mutation",
   ("generate_studio_document", {"kind": "facture"},
    "generate_studio_document", {"kind": "devis"}))
_c("dup_03_outil_non_mutant", "_duplicate_document_mutation",
   ("read_file", {"p": 1}, "read_file", {"p": 1}))
_c("dup_04_noms_differents", "_duplicate_document_mutation",
   ("generate_studio_document", {}, "revise_studio_document", {}))
_c("dup_05_ordre_des_cles", "_duplicate_document_mutation",
   ("apply_document_edit", {"a": 1, "b": 2},
    "apply_document_edit", {"b": 2, "a": 1}))

# ── _document_open_payload ───────────────────────────────────────────────────
_c("open_01_json", "_document_open_payload", (_obs('{"id": "d1"}'),))
_c("open_02_dict", "_document_open_payload", (_obs({"id": "d2"}),))
_c("open_03_casse", "_document_open_payload", (_obs("{casse"),))
_c("open_04_liste", "_document_open_payload", (_obs('[1, 2]'),))
_c("open_05_none", "_document_open_payload", (None,))

# ── _document_revision_patch (SANS_TEST) ─────────────────────────────────────
_c("patch_01_dict", "_document_revision_patch", ({"data": {"x": 1}},))
_c("patch_02_json", "_document_revision_patch", ({"data": '{"x": 2}'},))
_c("patch_03_literal_python", "_document_revision_patch", ({"data": "{'x': 3}"},))
_c("patch_04_illisible", "_document_revision_patch", ({"data": "<<>>"},))
_c("patch_05_vide", "_document_revision_patch", ({"data": "   "},))
_c("patch_06_none", "_document_revision_patch", (None,))

# ── _document_revision_changed_fields (SANS_TEST) ────────────────────────────
_c("chg_01_direct", "_document_revision_changed_fields",
   ({"changed_fields": {"total": 10}},))
_c("chg_02_repli_args", "_document_revision_changed_fields",
   ({"args": {"data": {"total": 20}}},))
_c("chg_03_changed_vide", "_document_revision_changed_fields",
   ({"changed_fields": {}, "args": {"data": {"t": 1}}},))
_c("chg_04_pas_un_dict", "_document_revision_changed_fields", ("texte",))

# ── _document_patch_scalar_values (SANS_TEST) ────────────────────────────────
_c("scal_01_imbrique", "_document_patch_scalar_values",
   ({"a": 1, "b": {"c": " deux ", "d": [3, None, ""]}},))
_c("scal_02_liste", "_document_patch_scalar_values", ([1, [2, [3]]],))
_c("scal_03_scalaire", "_document_patch_scalar_values", (" x ",))
_c("scal_04_none", "_document_patch_scalar_values", (None,))
_c("scal_05_vide", "_document_patch_scalar_values", ({},))

# ── _document_paths_match (SANS_TEST) ────────────────────────────────────────
_c("path_01_egaux", "_document_paths_match", ("a/b/c.pdf", "a/b/c.pdf"))
_c("path_02_normalisation", "_document_paths_match", ("a/./b/../b/c.pdf", "a/b/c.pdf"))
_c("path_03_casse", "_document_paths_match", ("A/B/C.PDF", "a/b/c.pdf"))
_c("path_04_vide", "_document_paths_match", ("", "a.pdf"))
_c("path_05_differents", "_document_paths_match", ("a.pdf", "b.pdf"))

# ── _document_verification_text (SANS_TEST) ──────────────────────────────────
_c("txt_01_cesure", "_document_verification_text", ("mon-\n  tant total",))
_c("txt_02_espaces", "_document_verification_text", ("  A   B \n C  ",))
_c("txt_03_unicode", "_document_verification_text", ("\uff21\uff22 \u00e9",))
_c("txt_04_none", "_document_verification_text", (None,))
_c("txt_05_nombre", "_document_verification_text", (1234,))

# ── _latest_document_batch_proofs (SANS_TEST) ────────────────────────────────
_c("lot_01_vide", "_latest_document_batch_proofs", (), {"history": HIST_VIDE})
_c("lot_02_avec_preuve", "_latest_document_batch_proofs", (), {"history": HIST_LOT})
_c("lot_03_sans_attribut", "_latest_document_batch_proofs", (), {})
_c("lot_04_evidence_prioritaire", "_latest_document_batch_proofs", (),
   {"history": HIST_LOT, "_document_workflow_evidence": {"batch_proofs": {}}})

# ── _document_web_rights_evidence ────────────────────────────────────────────
_c("web_01_vide", "_document_web_rights_evidence", (), {"history": HIST_VIDE})
_c("web_02_simple", "_document_web_rights_evidence", (), {"history": HIST_SIMPLE})
_c("web_03_sans_attribut", "_document_web_rights_evidence", (), {})

# ── _nested_document_bypass (SANS_TEST) ──────────────────────────────────────
_c("byp_01_direct", "_nested_document_bypass", ("generate_studio_document", None))
_c("byp_02_non_parallele", "_nested_document_bypass", ("read_file", {}))
_c("byp_03_parallele_json", "_nested_document_bypass",
   ("parallel_tools", {"tool_calls": '[{"name": "generate_studio_document"}]'}))
_c("byp_04_parallele_casse", "_nested_document_bypass",
   ("parallel_tools", {"tool_calls": "{casse"}))
_c("byp_05_parallele_sans_studio", "_nested_document_bypass",
   ("parallel_tools", {"tool_calls": [{"name": "read_file"}]}))

# ── _studio_attempted_kinds (SANS_TEST) ──────────────────────────────────────
_c("att_01_avec_kind", "_studio_attempted_kinds",
   ("generate_studio_document", _route(("facture",))), {"history": HIST_SIMPLE})
_c("att_02_sans_historique", "_studio_attempted_kinds",
   ("generate_studio_document", _route(("devis",))), {"history": HIST_VIDE})
_c("att_03_repli_une_seule_demande", "_studio_attempted_kinds",
   ("generate_studio_document", _route(("devis",))),
   {"history": [_etape("generate_studio_document", {}, _obs("ok"))]})

# ── _merge_mission_document_evidence ─────────────────────────────────────────
_c("mrg_01_les_deux", "_merge_mission_document_evidence", ("Rapport libre.", "Preuve X"))
_c("mrg_02_sans_reponse", "_merge_mission_document_evidence", ("", "Preuve X"))
_c("mrg_03_sans_preuve", "_merge_mission_document_evidence", ("Rapport libre.", ""))
_c("mrg_04_preuve_deja_incluse", "_merge_mission_document_evidence",
   ("Rapport avec Preuve X dedans.", "Preuve X"))

# ── _document_plan_required_kinds ────────────────────────────────────────────
_c("req_01_facture", "_document_plan_required_kinds", ("Generer la facture Dupont",))
_c("req_02_bc_majuscule", "_document_plan_required_kinds", ("Preparer le BC du client",))
_c("req_03_bc_minuscule", "_document_plan_required_kinds", ("le mot bc en prose",))
_c("req_04_vide", "_document_plan_required_kinds", ("",))


def valeur(nom: str):
    """Appelle la methode et retourne son resultat NORMALISE en texte.

    FAIL-CLOSED : aucune exception n'est rattrapee.
    """
    from src.reasoning.react import ReActLoop

    methode, args, etat = CAS[nom]
    fonction = getattr(ReActLoop, methode)
    if etat is None:
        resultat = fonction(*args)
    else:
        sac = SimpleNamespace(**etat)
        resultat = fonction(sac, *args)
    if hasattr(resultat, "__next__"):          # generateur
        resultat = tuple(resultat)
    return repr(resultat)


# ════════════════════════════════════════════════════════════════════════
#  La reference, capturee AVANT extraction
# ════════════════════════════════════════════════════════════════════════

BASELINE = {
    "att_01_avec_kind": "('facture',)",
    "att_02_sans_historique": "()",
    "att_03_repli_une_seule_demande": "('devis',)",
    "byp_01_direct": "''",
    "byp_02_non_parallele": "''",
    "byp_03_parallele_json": "''",
    "byp_04_parallele_casse": "''",
    "byp_05_parallele_sans_studio": "''",
    "chg_01_direct": "{'total': 10}",
    "chg_02_repli_args": "{'total': 20}",
    "chg_03_changed_vide": "{'t': 1}",
    "chg_04_pas_un_dict": "{}",
    "cle_01_complet": "('studio', 5, 'nom')",
    "cle_02_none": "('', 0, '')",
    "cle_03_limite_invalide": "('', 0, '')",
    "cle_04_vide": "('', 0, '')",
    "dup_01_identiques": "True",
    "dup_02_differents": "False",
    "dup_03_outil_non_mutant": "False",
    "dup_04_noms_differents": "False",
    "dup_05_ordre_des_cles": "True",
    "evt_01_vide": "()",
    "evt_02_simple": "(('generate_studio_document', {'kind': 'facture'}, True, True, 'ok'), ('read_file', {'path': 'a.txt'}, True, True, 'contenu'))",
    "evt_03_sans_observation": "(('generate_studio_document', {'kind': 'devis'}, False, False, ''),)",
    "evt_04_sans_action": "()",
    "evt_05_parallele": "(('generate_studio_document', {'kind': 'facture'}, True, True, 'ok1'), ('create_pdf', {'title': 'r'}, False, True, 'echec'))",
    "evt_06_pas_d_attribut": "()",
    "lot_01_vide": "()",
    "lot_02_avec_preuve": "(DocumentDeliveryProof(kind='facture', document_id='d1', filename='f.pdf', path='out/f.pdf', sha256='', template_id='facture_std', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=False, thumbnail_path='', page_count=0),)",
    "lot_03_sans_attribut": "()",
    "lot_04_evidence_prioritaire": "(DocumentDeliveryProof(kind='facture', document_id='d1', filename='f.pdf', path='out/f.pdf', sha256='', template_id='facture_std', format='pdf', size=0, logo_id='', render_status='not_checked', render_verified=False, thumbnail_path='', page_count=0),)",
    "mrg_01_les_deux": "'Rapport libre.\\n\\nPreuves documentaires:\\nPreuve X'",
    "mrg_02_sans_reponse": "''",
    "mrg_03_sans_preuve": "'Rapport libre.'",
    "mrg_04_preuve_deja_incluse": "'Rapport avec Preuve X dedans.'",
    "open_01_json": "{'id': 'd1'}",
    "open_02_dict": "{'id': 'd2'}",
    "open_03_casse": "None",
    "open_04_liste": "None",
    "open_05_none": "None",
    "par_01_liste": "(('create_pdf', {'t': 1}), ('create_docx', {'x': 2}))",
    "par_02_json": "(('create_pdf', {'t': 1}),)",
    "par_03_json_casse": "()",
    "par_04_none": "()",
    "par_05_non_liste": "()",
    "patch_01_dict": "{'x': 1}",
    "patch_02_json": "{'x': 2}",
    "patch_03_literal_python": "{'x': 3}",
    "patch_04_illisible": "{}",
    "patch_05_vide": "{}",
    "patch_06_none": "{}",
    "path_01_egaux": "True",
    "path_02_normalisation": "True",
    "path_03_casse": "True",
    "path_04_vide": "False",
    "path_05_differents": "False",
    "req_01_facture": "('facture',)",
    "req_02_bc_majuscule": "('bon_commande',)",
    "req_03_bc_minuscule": "()",
    "req_04_vide": "()",
    "rows_01_json": "({'id': 'a'}, {'id': 'b'})",
    "rows_02_dict": "({'id': 'z'},)",
    "rows_03_json_casse": "()",
    "rows_04_sans_models": "()",
    "rows_05_none": "()",
    "scal_01_imbrique": "('1', 'deux', '3')",
    "scal_02_liste": "('1', '2', '3')",
    "scal_03_scalaire": "('x',)",
    "scal_04_none": "()",
    "scal_05_vide": "()",
    "txt_01_cesure": "'mon-tant total'",
    "txt_02_espaces": "'a b c'",
    "txt_03_unicode": "'ab é'",
    "txt_04_none": "''",
    "txt_05_nombre": "'1234'",
    "web_01_vide": "(False, False)",
    "web_02_simple": "(False, False)",
    "web_03_sans_attribut": "(False, False)"
}


# ══════════════════════════════════════════════════════════════════════════
#  1. Les 78 comparaisons de valeur de retour
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", sorted(CAS))
def test_la_valeur_de_retour_est_identique_a_la_reference(nom):
    assert valeur(nom) == BASELINE[nom], f"{nom} : la valeur de retour a change"


def test_la_matrice_couvre_les_dix_sept_methodes():
    import collections

    par_methode = collections.Counter(CAS[n][0] for n in CAS)
    assert len(par_methode) == 17, f"{len(par_methode)} methodes exercees au lieu de 17"
    assert min(par_methode.values()) >= 3, (
        f"methode sous-exercee : {min(par_methode, key=par_methode.get)}"
    )


def test_les_onze_methodes_sans_test_sont_toutes_exercees():
    """Onze des dix-sept ne sont nommees par AUCUN test du depot.

    Pour celles-la, cette matrice est la SEULE preuve. Ce test empeche qu'on
    retire silencieusement leurs cas.
    """
    import collections

    par_methode = collections.Counter(CAS[n][0] for n in CAS)
    absentes = sorted(m for m in SANS_TEST if m not in par_methode)
    assert absentes == [], f"methodes sans test et sans cas : {absentes}"
    for m in SANS_TEST:
        assert par_methode[m] >= 3, f"{m} : {par_methode[m]} cas, insuffisant"


def test_la_matrice_discrimine():
    """Une matrice dont tous les cas rendent la meme valeur ne prouve rien."""
    distinctes = {v for v in BASELINE.values()}
    assert len(distinctes) >= 30, f"matrice trop pauvre : {len(distinctes)} valeurs"


# ══════════════════════════════════════════════════════════════════════════
#  2. Fermeture du module extrait
# ══════════════════════════════════════════════════════════════════════════


def _noms_libres(chemin: Path) -> list[str]:
    arbre = ast.parse(chemin.read_text(encoding="utf-8"))

    def args_de(f):
        a = f.args
        noms = {x.arg for x in list(a.args) + list(a.kwonlyargs) + list(a.posonlyargs)}
        if a.vararg:
            noms.add(a.vararg.arg)
        if a.kwarg:
            noms.add(a.kwarg.arg)
        return noms

    lies = set(dir(builtins))
    for n in ast.walk(arbre):
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            for al in n.names:
                lies.add((al.asname or al.name).split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lies.add(n.name)
            lies |= args_de(n)
        elif isinstance(n, ast.Lambda):
            lies |= args_de(n)
        elif isinstance(n, ast.ClassDef):
            lies.add(n.name)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            lies.add(n.id)
        elif isinstance(n, ast.NamedExpr) and isinstance(n.target, ast.Name):
            lies.add(n.target.id)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            lies.add(n.name)
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            lies.add(n.target.id)

    charges = {n.id for n in ast.walk(arbre)
               if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return sorted(charges - lies)


def test_le_module_extrait_n_a_aucun_nom_global_non_resolu():
    assert _noms_libres(NOUVEAU) == []


def test_le_module_extrait_ne_reference_ni_self_ni_la_classe():
    """Les deux formes que le chantier a apprises a chercher.

    `self.X` et `getattr(self, ...)` d'un cote ; `ReActLoop.X(...)` de l'autre —
    cette derniere est la cinquieme famille de dependance, revelee par RF-4, et
    elle constituait ICI **cent pour cent** du cablage interne de la famille
    documentaire : 71 appels, zero `self.X`.
    """
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


def test_le_contrat_d_etat_tient_en_deux_noms():
    """Quatre des dix-sept prenaient `self`, mais aucune ne lisait un attribut
    par `self.X` : toutes passaient par `getattr(self, ...)`, invisible a un
    balayage d'attributs.

    Les etats reellement lus sont exactement deux. Ce test empeche qu'un
    troisieme se glisse dans les signatures sans decision explicite.
    """
    import inspect as _inspect

    from src.reasoning import document_runtime

    attendus = {
        "_document_tool_events": ["historique"],
        "_latest_document_batch_proofs": ["historique", "preuves_workflow"],
        "_document_web_rights_evidence": ["historique"],
        "_studio_attempted_kinds": ["historique", "studio_tool", "route"],
    }
    for nom, params in attendus.items():
        sig = _inspect.signature(getattr(document_runtime, nom))
        assert list(sig.parameters) == params, (
            f"{nom} : contrat d'etat modifie -> {list(sig.parameters)}"
        )


# ══════════════════════════════════════════════════════════════════════════
#  3. Ce que les reexports doivent preserver
# ══════════════════════════════════════════════════════════════════════════


NOMS_RF5A = [
    "_document_tool_events", "_document_catalog_evidence_key", "_document_catalog_rows",
    "_document_parallel_calls", "_duplicate_document_mutation", "_document_open_payload",
    "_document_revision_patch", "_document_revision_changed_fields",
    "_document_patch_scalar_values", "_document_paths_match", "_document_verification_text",
    "_latest_document_batch_proofs", "_document_web_rights_evidence",
    "_nested_document_bypass", "_studio_attempted_kinds",
    "_merge_mission_document_evidence", "_document_plan_required_kinds",
]

STATIQUES = {
    "_document_catalog_evidence_key", "_document_catalog_rows", "_document_parallel_calls",
    "_duplicate_document_mutation", "_document_open_payload", "_document_revision_patch",
    "_document_revision_changed_fields", "_document_patch_scalar_values",
    "_document_paths_match", "_document_verification_text", "_nested_document_bypass",
    "_merge_mission_document_evidence", "_document_plan_required_kinds",
}


@pytest.mark.parametrize("nom", NOMS_RF5A)
def test_le_reexport_existe_toujours_sur_la_classe(nom):
    """283 sites d'appel ecrivent `ReActLoop._document_x(...)`, dont 196 dans
    les tests. Aucun ne doit bouger (invariants 4 et 19)."""
    from src.reasoning.react import ReActLoop

    assert hasattr(ReActLoop, nom), f"reexport disparu : {nom}"


@pytest.mark.parametrize("nom", sorted(STATIQUES))
def test_la_forme_du_descripteur_est_preservee(nom):
    """Invariant 13 : `@staticmethod` fait partie du contrat."""
    import inspect as _inspect

    from src.reasoning.react import ReActLoop

    brut = _inspect.getattr_static(ReActLoop, nom)
    assert isinstance(brut, staticmethod), f"{nom} n'est plus un staticmethod : {type(brut)}"


@pytest.mark.parametrize("nom", NOMS_RF5A)
def test_la_signature_publique_est_inchangee(nom):
    """Les signatures relevees sur `react.py` AVANT le lot."""
    import inspect as _inspect

    from src.reasoning.react import ReActLoop

    REFERENCE = {
        "_document_tool_events": ["self"],
        "_document_catalog_evidence_key": ["args"],
        "_document_catalog_rows": ["content"],
        "_document_parallel_calls": ["tool_args"],
        "_duplicate_document_mutation": ["primary_name", "primary_args",
                                          "queued_name", "queued_args"],
        "_document_open_payload": ["observation"],
        "_document_revision_patch": ["args"],
        "_document_revision_changed_fields": ["record"],
        "_document_patch_scalar_values": ["value"],
        "_document_paths_match": ["left", "right"],
        "_document_verification_text": ["value"],
        "_latest_document_batch_proofs": ["self"],
        "_document_web_rights_evidence": ["self"],
        "_nested_document_bypass": ["tool_name", "tool_args"],
        "_studio_attempted_kinds": ["self", "studio_tool", "route"],
        "_merge_mission_document_evidence": ["free_answer", "evidence"],
        "_document_plan_required_kinds": ["task_desc"],
    }
    sig = _inspect.signature(getattr(ReActLoop, nom))
    assert list(sig.parameters) == REFERENCE[nom], (
        f"{nom} : signature publique modifiee -> {list(sig.parameters)}"
    )


def test_le_reexport_generateur_reste_paresseux():
    """`_document_tool_events` est le SEUL generateur des dix-sept.

    Sa coquille doit faire `yield from`, pas `return`. Avec `return`, le
    `getattr(self, "history", [])` serait evalue a l'APPEL et non a la premiere
    iteration : un historique modifie entre les deux donnerait un resultat
    different. C'est invisible aux 78 comparaisons, qui iterent aussitot.
    """
    from types import SimpleNamespace

    from src.reasoning.react import ReActLoop

    sac = SimpleNamespace(history=[])
    generateur = ReActLoop._document_tool_events(sac)
    assert hasattr(generateur, "__next__"), "la coquille ne rend plus un generateur"

    # L'historique est pose APRES l'appel : un generateur paresseux le voit.
    sac.history = [
        SimpleNamespace(
            action=SimpleNamespace(tool_name="generate_studio_document",
                                   tool_args={"kind": "facture"}),
            observation=SimpleNamespace(content="ok", success=True),
        )
    ]
    evenements = tuple(generateur)
    assert len(evenements) == 1, (
        "le generateur a fige l'historique a l'appel : la paresse est perdue"
    )
    assert evenements[0][0] == "generate_studio_document"


def test_react_conserve_dix_sept_coquilles_et_aucun_corps():
    """Les corps sont partis ; les points d'entree historiques restent."""
    source = REACT.read_text(encoding="utf-8")
    assert "from .document_runtime import (" in source, (
        "react.py n'importe pas le module extrait"
    )
    for nom in NOMS_RF5A:
        assert f"def {nom}(" in source, f"coquille disparue : {nom}"
        assert f"_rt_{nom}(" in source, f"la coquille de {nom} n'appelle pas le module"
