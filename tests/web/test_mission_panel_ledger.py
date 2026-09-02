"""Panel Missions — lot 9 : LE LEDGER ETAIT DEJA DANS LA CHARGE UTILE.

La vue Controle affichait deux colonnes sur trois vides, avec la mention
« Non exposé par l'API ». J'avais ecrit cette phrase moi-meme au lot 6, et
elle etait a moitie fausse.

--- Ce qui a ete mesure avant d'ecrire une ligne ---

Corpus reel, `data/task_orchestrator_state.json`, 665 taches :

    last_checkpoint     665 / 665   contient `ledger` : total_actions,
                                    successful_mutations, success_rate, recent
    completion_proof    132         delivery_proven, tests_green, browser_proven,
                                    missing_files, stub_files
    published_files      29
    artifacts           141

Et `TaskRecord.to_dict()` est un `asdict(self)` — donc `/api/missions` envoyait
DEJA tout cela au navigateur. Le panneau declarait absent ce qu'il tenait en
main. C'est, une fois de plus, le motif de tous les lots precedents : le fait
existe, il est calcule, il est meme transmis — puis jete avant la decision.

--- Ce qui reste vrai ---

Le PLAN du lead n'est persiste nulle part : il vit dans la boucle ReAct. Cette
moitie de l'annonce tenait. La colonne montre donc la PROGRESSION et dit, en
toutes lettres, que ce n'est pas un plan — dans les DEUX branches du rendu,
y compris quand il n'y a pas encore de checkpoint. La premiere version de mon
correctif ne mettait l'aveu que dans une branche : il disparaissait au demarrage
de la mission, c'est-a-dire pile au moment ou l'on pourrait croire qu'un plan
va s'afficher.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_JS = _ROOT / "web" / "static" / "js"
_CSS = _ROOT / "web" / "static" / "css" / "mission-panel.css"
# Par `paths.py`, jamais a la main : le chemin honore `LUMENA_DATA_DIR`,
# et un garde du depot interdit justement de le reconstruire ici.
from src.utils.paths import DATA_DIR  # noqa: E402

_ETAT = DATA_DIR / "task_orchestrator_state.json"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node indisponible")


def _node(corps: str):
    script = (
        "require(%s);require(%s);" % tuple(
            json.dumps(str(_JS / n)) for n in ("mission_model.js", "mission_views.js"))
        + "const L=globalThis.missionLedger, P=globalThis.missionProofs,"
        + " D=globalThis.missionDelivered, V=globalThis.missionRenderView;"
        + "process.stdout.write(JSON.stringify((function(){%s})()));" % corps
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# La forme EXACTE relevee dans le corpus, pas une forme inventee.
_CP_REEL = {
    "phase": "iteration", "iteration": 3,
    "ledger": {"total_actions": 2, "successful_mutations": 0, "success_rate": 1.0,
               "recent": [{"action": "web_search_brave", "target": None,
                           "success": True, "iteration": 0},
                          {"action": "write_file", "target": "app.js",
                           "success": False, "iteration": 1}]},
}
_PROOF_REEL = {
    "complete": True, "scope": "worker", "delivery_proven": True,
    "delegation_complete": True, "tests_required": True, "tests_green": True,
    "browser_required": False, "browser_proven": True,
    "assigned_files": ["calculator.py"], "missing_files": [], "stub_files": [],
    "invalid_files": [],
}


# ══════════════════════════════════════════════════════════════════════════
#  1. LE LEDGER
# ══════════════════════════════════════════════════════════════════════════


def test_le_ledger_est_lu_depuis_last_checkpoint():
    l = _node("return L(%s);" % json.dumps({"last_checkpoint": _CP_REEL}))
    assert l["actions"] == 2 and l["mutations"] == 0
    assert l["phase"] == "iteration" and l["iteration"] == 3


def test_le_taux_de_reussite_est_rendu_en_POURCENT():
    """Le runtime le stocke entre 0 et 1 ; « 1 » a l'ecran ne veut rien dire."""
    assert _node("return L(%s).successPct;" % json.dumps({"last_checkpoint": _CP_REEL})) == 100


def test_un_taux_absent_ne_devient_pas_ZERO():
    """0 % et « pas de mesure » sont deux choses differentes."""
    cp = {"ledger": {"total_actions": 1, "recent": []}}
    assert _node("return L(%s).successPct;" % json.dumps({"last_checkpoint": cp})) is None


def test_les_actions_recentes_sont_en_ordre_ANTI_chronologique():
    """La derniere action est celle qui interesse : elle est en tete."""
    r = _node("return L(%s).recent;" % json.dumps({"last_checkpoint": _CP_REEL}))
    assert r[0]["action"] == "write_file", "la plus recente doit venir en premier"
    assert r[0]["success"] is False


def test_une_cible_nulle_ne_devient_pas_la_chaine_null():
    r = _node("return L(%s).recent;" % json.dumps({"last_checkpoint": _CP_REEL}))
    assert r[1]["target"] == "", "`null` doit rendre du vide, jamais « null »"


def test_pas_de_checkpoint_rend_NULL_et_pas_un_ledger_vide():
    """Un ledger a zero action et « pas encore de ledger » ne se disent pas
    de la meme facon."""
    assert _node("return L({});") is None
    assert _node("return L({last_checkpoint: {}});") is None


# ══════════════════════════════════════════════════════════════════════════
#  2. LES PREUVES
# ══════════════════════════════════════════════════════════════════════════


def _p(meta):
    return _node("return P(%s);" % json.dumps({"metadata": meta}))


def test_les_preuves_sortent_de_completion_proof():
    libs = [x["lib"] for x in _p({"completion_proof": _PROOF_REEL})]
    assert "Livraison" in libs and "Tests" in libs


def test_une_preuve_NON_REQUISE_n_est_pas_affichee():
    """`browser_required: false` : la ligne n'a pas lieu d'etre."""
    libs = [x["lib"] for x in _p({"completion_proof": _PROOF_REEL})]
    assert "Navigateur" not in libs


def test_une_preuve_REQUISE_et_non_etablie_NE_DISPARAIT_PAS():
    """C'est tout l'objet : un trou nomme, jamais une omission silencieuse."""
    cp = dict(_PROOF_REEL, browser_required=True, browser_proven=False)
    nav = [x for x in _p({"completion_proof": cp}) if x["lib"] == "Navigateur"]
    assert nav and nav[0]["ok"] is False


def test_les_fichiers_manquants_ont_droit_a_leur_ligne():
    cp = dict(_PROOF_REEL, missing_files=["README.md"], stub_files=["app.js"])
    libs = [x["lib"] for x in _p({"completion_proof": cp})]
    assert "Manquant : README.md" in libs
    assert "Ébauche : app.js" in libs


def test_sans_completion_proof_un_verdict_de_tests_suffit():
    """41 taches du corpus ont `tests_green` sans `completion_proof`."""
    assert [x["lib"] for x in _p({"tests_green": True})] == ["Tests"]


def test_aucune_preuve_ne_donne_une_liste_vide_pas_une_erreur():
    assert _p({}) == []


# ══════════════════════════════════════════════════════════════════════════
#  3. LES LIVRABLES
# ══════════════════════════════════════════════════════════════════════════


def test_un_chemin_absolu_est_reduit_a_son_nom_lisible():
    d = _node("return D(%s);" % json.dumps(
        {"metadata": {"artifacts": ["C:\\\\Users\\\\x\\\\workspace\\\\veille.md"]}}))
    assert d["artifacts"][0]["nom"] == "veille.md"
    assert "workspace" in d["artifacts"][0]["full"], "le chemin complet reste disponible"


def test_publie_et_ecrit_sont_DEUX_faits_distincts():
    """Publier fige un instantane (lot Z24) : on ne confond pas les deux listes."""
    d = _node("return D(%s);" % json.dumps(
        {"metadata": {"published_files": ["a.md"], "artifacts": ["/x/b.md"]}}))
    assert d["published"] == ["a.md"]
    assert [a["nom"] for a in d["artifacts"]] == ["b.md"]


# ══════════════════════════════════════════════════════════════════════════
#  4. LA VUE CONTROLE MONTRE TOUT CELA
# ══════════════════════════════════════════════════════════════════════════


def _ctrl(mission):
    return _node("return V('control', %s, null);" % json.dumps([mission]))


def _m(**kw):
    base = {"id": "m", "objective": "o", "aggregate": "running", "children": [],
            "ledger": None, "proofs": [], "delivered": {"published": [], "artifacts": []}}
    base.update(kw)
    return base


def test_le_controle_affiche_les_chiffres_du_ledger():
    l = _node("return L(%s);" % json.dumps({"last_checkpoint": _CP_REEL}))
    h = _ctrl(_m(ledger=l))
    assert "actions" in h and "réussite" in h and "web_search_brave" in h


def test_l_aveu_sur_le_plan_survit_a_L_ABSENCE_de_ledger():
    """La premiere version de mon correctif ne mettait la note que dans la
    branche « il y a un ledger » : elle disparaissait au demarrage."""
    for l in (None, _node("return L(%s);" % json.dumps({"last_checkpoint": _CP_REEL}))):
        h = _ctrl(_m(ledger=l))
        assert "n’est persisté nulle part" in h, "l'aveu a disparu (ledger=%r)" % (l is None)


def test_le_controle_montre_les_preuves_etablies_ET_les_trous():
    p = _p({"completion_proof": dict(_PROOF_REEL, browser_required=True,
                                     browser_proven=False)})
    h = _ctrl(_m(proofs=p))
    assert "Livraison" in h and "Navigateur" in h and "non prouvé" in h


def test_le_controle_remonte_les_preuves_MANQUANTES_des_workers():
    """Le lead porte les preuves etablies ; un trou cote worker doit monter."""
    w = {"id": "w1", "objective": "[Worker api] x", "state": "running",
         "perimeter": [], "proofs": [{"cle": "tests", "lib": "Tests", "ok": False}],
         "thought": "", "iteration": 0, "maxIter": 0, "lastTool": "", "queueRank": 0}
    h = _ctrl(_m(children=[w]))
    assert "api · Tests" in h and "non prouvé" in h


def test_une_preuve_ETABLIE_d_un_worker_ne_pollue_pas_la_colonne():
    w = {"id": "w1", "objective": "[Worker api] x", "state": "running",
         "perimeter": [], "proofs": [{"cle": "tests", "lib": "Tests", "ok": True}],
         "thought": "", "iteration": 0, "maxIter": 0, "lastTool": "", "queueRank": 0}
    assert "api · Tests" not in _ctrl(_m(children=[w]))


def test_le_perimetre_et_les_preuves_COEXISTENT():
    """Ils se disputaient la troisieme colonne : l'un chassait l'autre, et on
    n'avait jamais les deux."""
    w = {"id": "w1", "objective": "[Worker api] x", "state": "running",
         "perimeter": ["src/api.py"], "proofs": [], "thought": "",
         "iteration": 0, "maxIter": 0, "lastTool": "", "queueRank": 0}
    h = _ctrl(_m(children=[w], proofs=_p({"completion_proof": _PROOF_REEL})))
    assert "src/api.py" in h and "Livraison" in h
    assert "Périmètre d’écriture" in h


def test_la_vue_tient_avec_une_mission_TOTALEMENT_vide():
    """Un backend pas encore a jour n'envoie rien de tout cela."""
    h = _node("return V('control', [{id:'m',objective:'o',children:[]}], null);")
    assert "mp-col" in h and "Progression" in h


# ══════════════════════════════════════════════════════════════════════════
#  5. LE CORPUS REEL — la mesure qui a declenche ce lot
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not _ETAT.exists(), reason="corpus absent de cette machine")
def test_le_ledger_existe_VRAIMENT_dans_les_donnees_persistees():
    """Ce test est la preuve que le lot 6 se trompait. S'il rougit un jour,
    c'est que le runtime a cesse de persister le ledger — et alors la colonne
    Progression doit redevenir une absence nommee."""
    taches = json.loads(_ETAT.read_text(encoding="utf-8"))["tasks"]
    avec = [t for t in taches if isinstance(t, dict)
            and isinstance(t.get("last_checkpoint"), dict)
            and t["last_checkpoint"].get("ledger")]
    assert avec, "aucune tache ne porte de ledger : la colonne mentirait"


@pytest.mark.skipif(not _ETAT.exists(), reason="corpus absent de cette machine")
def test_le_modele_digere_le_corpus_REEL_sans_broncher(tmp_path):
    """Trente taches tirees du disque, telles quelles.

    L'arbre passe par un FICHIER : trente taches reelles en argument de ligne
    de commande depassent la limite de longueur de Windows (WinError 206)."""
    taches = [t for t in json.loads(_ETAT.read_text(encoding="utf-8"))["tasks"]
              if isinstance(t, dict)][-30:]
    arbre = [{"mission": t, "children": []} for t in taches]
    f = tmp_path / "arbre.json"
    f.write_text(json.dumps(arbre), encoding="utf-8")
    out = _node(
        "const a=JSON.parse(require('fs').readFileSync(%s,'utf8'));"
        "return globalThis.buildMissionModel(a, [], 0).length;"
        % json.dumps(str(f)))
    assert out == len(arbre)


# ══════════════════════════════════════════════════════════════════════════
#  6. L'HABILLAGE
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("sel", [".mp-kpi", ".mp-act", ".mp-col-note",
                                 ".mp-proof.is-ko", ".mp-ctrl-foot"])
def test_chaque_element_neuf_a_son_style(sel):
    assert sel in _CSS.read_text(encoding="utf-8"), sel


def test_une_preuve_manquante_est_ROUGE_et_pas_grise():
    css = re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)
    i = css.index(".mp-proof.is-ko .mp-proof-ic")
    assert "var(--danger)" in css[i:css.index("}", i)]
