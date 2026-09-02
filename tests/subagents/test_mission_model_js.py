"""Panel Missions — lot 1 : le MODELE, teste via node.

Meme patron que `test_mission_tree_js.py` (lot 5.4) : la logique est PURE et
sans DOM, donc elle s'execute hors navigateur et se verrouille ici. L'UI, elle,
se valide en runtime.

Ce que le modele doit garantir :

  - la PENSEE de l'agent arrive jusqu'aux vues (c'est tout l'objet du chantier) ;
  - l'ATTENTE du verrou CodeAgent est distinguee du travail — sans quoi
    « en file » et « en train de reflechir » restent indistincts a l'ecran ;
  - il est TOLERANT : un serveur pas encore a jour n'envoie ni `thought` ni
    `codeagent_wait_*`, et le panneau doit continuer de fonctionner.

Cette derniere exigence n'est pas cosmetique : sans elle, mettre a jour le
client avant le serveur casserait le panneau.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_MODEL = _ROOT / "web" / "static" / "js" / "mission_model.js"
_TREE = _ROOT / "web" / "static" / "js" / "mission_tree.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node indisponible")


def _node(expr: str, prelude: str = "") -> object:
    """Evalue `expr` dans node avec le modele charge, rend le JSON du resultat."""
    script = (
        f"const M = require({json.dumps(str(_MODEL))});"
        f"const T = require({json.dumps(str(_TREE))});"
        f"{prelude}"
        f"process.stdout.write(JSON.stringify({expr}));"
    )
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         cwd=str(_ROOT), timeout=30)
    assert res.returncode == 0, f"node a echoue: {res.stderr or res.stdout}"
    return json.loads(res.stdout)


# ══════════════════════════════════════════════════════════════════════════
#  1. Le fichier respecte le patron du depot
# ══════════════════════════════════════════════════════════════════════════


def test_le_modele_est_du_javascript_valide():
    res = subprocess.run(["node", "--check", str(_MODEL)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_le_modele_ne_touche_AUCUN_dom():
    """S'il touchait au DOM, il ne serait plus testable ici — et le lot 7
    exige que le coeur reste pur."""
    src = _MODEL.read_text(encoding="utf-8")
    for interdit in ("document.", "window.", "innerHTML", "querySelector", "addEventListener"):
        assert interdit not in src, f"le modele touche au DOM : {interdit}"


def test_le_modele_n_importe_rien():
    src = _MODEL.read_text(encoding="utf-8")
    assert "require(" not in src and "import " not in src


# ══════════════════════════════════════════════════════════════════════════
#  2. La pensee arrive — la raison d'etre du chantier
# ══════════════════════════════════════════════════════════════════════════


def test_la_pensee_traverse_le_reducteur():
    out = _node(
        "M.reduceEvents([{task_id:'w1',stage:'codeagent_iteration',"
        "thought:'Je relis parse.py.',iteration:6,max_iter:12,tool_name:'edit_lines'}]).w1"
    )
    assert out["thought"] == "Je relis parse.py."
    assert out["iteration"] == 6 and out["maxIter"] == 12
    assert out["lastTool"] == "edit_lines"


def test_la_derniere_pensee_ecrase_la_precedente():
    out = _node(
        "M.reduceEvents(["
        "{task_id:'w1',stage:'codeagent_iteration',thought:'ancienne'},"
        "{task_id:'w1',stage:'codeagent_iteration',thought:'nouvelle'}]).w1.thought"
    )
    assert out == "nouvelle"


def test_une_trace_SANS_pensee_n_efface_pas_la_precedente():
    """Toutes les traces ne portent pas de pensee : un outil ne doit pas vider
    l'ecran de ce que l'agent venait d'expliquer."""
    out = _node(
        "M.reduceEvents(["
        "{task_id:'w1',stage:'codeagent_iteration',thought:'je cherche'},"
        "{task_id:'w1',stage:'tool_call',tool_name:'read_file'}]).w1"
    )
    assert out["thought"] == "je cherche"
    assert out["lastTool"] == "read_file"


# ══════════════════════════════════════════════════════════════════════════
#  3. L'attente du CodeAgent devient visible
# ══════════════════════════════════════════════════════════════════════════


def test_un_worker_en_attente_est_marque():
    out = _node("M.reduceEvents([{task_id:'w2',stage:'codeagent_wait_start'}]).w2")
    assert out["waitingSince"]


def test_la_fin_d_attente_libere_et_CUMULE_la_duree():
    out = _node(
        "M.reduceEvents(["
        "{task_id:'w2',stage:'codeagent_wait_start'},"
        "{task_id:'w2',stage:'codeagent_wait_end',duration_ms:4200}]).w2"
    )
    assert not out["waitingSince"]
    assert out["waitedMs"] == 4200


def test_une_iteration_libere_aussi_l_attente():
    """Si l'agent code, il n'attend plus — meme si le wait_end s'est perdu."""
    out = _node(
        "M.reduceEvents(["
        "{task_id:'w2',stage:'codeagent_wait_start'},"
        "{task_id:'w2',stage:'codeagent_iteration',thought:'j y suis'}]).w2"
    )
    assert not out["waitingSince"]


def test_un_wait_end_ORPHELIN_ne_casse_rien():
    """Le client peut se connecter au milieu d'un run : il rate le wait_start."""
    out = _node("M.reduceEvents([{task_id:'w2',stage:'codeagent_wait_end',duration_ms:900}]).w2")
    assert out["waitedMs"] == 900 and not out["waitingSince"]


def test_les_rangs_de_file_suivent_l_ordre_d_arrivee():
    out = _node(
        "M.queueRanks(M.reduceEvents(["
        "{task_id:'a',stage:'codeagent_wait_start'},"
        "{task_id:'b',stage:'codeagent_wait_start'},"
        "{task_id:'c',stage:'codeagent_wait_start'},"
        "{task_id:'b',stage:'codeagent_wait_end',duration_ms:10}]))"
    )
    assert out == {"a": 1, "c": 2}, out


# ══════════════════════════════════════════════════════════════════════════
#  4. TOLERANCE — un serveur pas a jour ne casse pas le panneau
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("entree", ["null", "undefined", "[]", "'pas un tableau'", "42"])
def test_un_flux_absurde_rend_un_objet_vide(entree):
    assert _node(f"M.reduceEvents({entree})") == {}


def test_un_evenement_sans_task_id_est_ignore():
    assert _node("M.reduceEvents([{stage:'codeagent_iteration',thought:'x'}])") == {}


def test_un_backend_SANS_les_champs_neufs_donne_un_modele_neutre():
    """Le cas « client a jour, serveur pas encore » : aucune exception, des
    valeurs vides que les vues savent afficher."""
    out = _node(
        "M.buildModel(T.buildMissionTree(["
        "{task_id:'lead',state:'running',metadata:{objective:'Construis X'}},"
        "{task_id:'w1',state:'running',metadata:{parent_id:'lead'}}]), [], 0)[0]"
    )
    assert out["objective"] == "Construis X"
    assert out["children"][0]["thought"] == ""
    assert out["children"][0]["queueRank"] == 0
    assert out["remainingMs"] is None


# ══════════════════════════════════════════════════════════════════════════
#  5. Le modele complet
# ══════════════════════════════════════════════════════════════════════════


def test_le_modele_compte_les_workers_termines():
    out = _node(
        "M.buildModel(T.buildMissionTree(["
        "{task_id:'lead',state:'running',metadata:{}},"
        "{task_id:'a',state:'done',metadata:{parent_id:'lead'}},"
        "{task_id:'b',state:'failed',metadata:{parent_id:'lead'}},"
        "{task_id:'c',state:'running',metadata:{parent_id:'lead'}}]), [], 0)[0].workers"
    )
    assert out == {"done": 2, "total": 3}


def test_le_perimetre_est_TOUJOURS_un_tableau():
    for meta, attendu in [
        ("{allowed_files:['a.py','b.py']}", ["a.py", "b.py"]),
        ("{allowed_files:'seul.py'}", ["seul.py"]),
        ("{}", []),
    ]:
        out = _node(f"M.perimeter({{metadata:{meta}}})")
        assert out == attendu, (meta, out)


def test_l_objectif_n_est_JAMAIS_tronque():
    """Le panneau actuel coupait a mi-phrase — c'est l'un des manques releves."""
    long = "x" * 900
    out = _node(f"M.objective({{metadata:{{objective:{json.dumps(long)}}}}})")
    assert out == long


def test_l_objectif_retombe_sur_le_preview_si_la_metadata_manque():
    out = _node("M.objective({message_preview:'de secours'})")
    assert out == "de secours"


def test_le_temps_restant_se_calcule_sur_l_echeance():
    out = _node(
        "M.remainingMs({metadata:{deadline_ts:'2026-08-29T12:00:00'}}, "
        "Date.parse('2026-08-29T11:30:00'))"
    )
    assert out == 30 * 60 * 1000


def test_une_echeance_absente_ou_illisible_rend_null():
    assert _node("M.remainingMs({metadata:{}}, 0)") is None
    assert _node("M.remainingMs({metadata:{deadline_ts:'pas une date'}}, 0)") is None


@pytest.mark.parametrize("ms,attendu", [
    (65000, "1:05"), (3900000, "1:05:00"), (-65000, "-1:05"), (0, "0:00"),
])
def test_le_formatage_de_duree(ms, attendu):
    assert _node(f"M.formatDuration({ms})") == attendu


def test_une_duree_nulle_rend_une_chaine_vide():
    assert _node("M.formatDuration(null)") == ""
