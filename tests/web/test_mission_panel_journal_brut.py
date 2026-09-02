"""Panel Missions — lot 15 : UNE CASE QUI NE PILOTAIT RIEN.

La case « Journal brut » du menu Affichage existait, se cochait, se persistait
dans `localStorage` — et aucune vue ne la lisait. Mesure au moment de la
decouverte, en comptant `_on(prefs, '<bloc>')` dans `mission_views.js` :

    thought      1 usage
    perimeter    2 usages
    queue        3 usages
    countdown    1 usage
    rawlog       0 usage      <- un interrupteur relie a rien

Quatrieme occurrence du motif dans la meme soiree, et la plus betement visible.
La donnee etait pourtant deja la : le tampon d'evenements SSE est passe au
modele a chaque rendu. Il ne gardait simplement aucune trace — il COMPTAIT les
evenements (`cur.events += 1`) sans en conserver un seul.

--- Le garde qui compte vraiment ---

`test_AUCUNE_case_ne_pilote_le_vide` ferme la classe entiere : toute case
ajoutee au customizer devra etre lue par au moins une vue, sinon la suite
rougit. C'est le seul test de ce fichier qui protege l'avenir ; les autres
verifient le journal lui-meme.
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

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node indisponible")


def _node(corps: str):
    script = (
        "require(%s);require(%s);const P=require(%s);" % tuple(
            json.dumps(str(_JS / n)) for n in
            ("mission_model.js", "mission_views.js", "mission_panel.js"))
        + "const B=globalThis.buildMissionModel, V=globalThis.missionRenderView;"
        + "process.stdout.write(JSON.stringify((function(){%s})()));" % corps
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# Le fixture doit donner a CHAQUE case quelque chose a masquer, sinon le test
# « decocher change l'ecran » passe au vert pour de mauvaises raisons — c'est
# exactement ce qui m'est arrive sur `perimeter` : pas de `allowed_files`, donc
# rien a cacher, donc aucune difference.
_ARBRE = [{"mission": {"task_id": "m", "state": "running",
                       "metadata": {"objective": "o",
                                    "deadline_ts": "2099-01-01T00:00:00"}},
           "children": [{"mission": {"task_id": "w1", "state": "running",
                                     "metadata": {"objective": "[Worker api] x",
                                                  "allowed_files": ["src/api.py"]}},
                         "children": []}]}]


def _ev(task="w1", n=3):
    ev = [{"task_id": task, "stage": "codeagent_iteration",
           "tool_name": "edit_file", "summary": "fichier_%d.py" % i,
           "thought": "Je relis avant d’écrire."}
          for i in range(n)]
    # Une attente du verrou : sans elle, la case « File du CodeAgent » n'a
    # rien a masquer non plus.
    ev.insert(0, {"task_id": task, "stage": "codeagent_wait_start"})
    return ev


def _rendu(events, prefs="null", vue="workshop"):
    return _node("const m=B(%s, %s, 0);return V(%s, m, %s);"
                 % (json.dumps(_ARBRE), json.dumps(events), json.dumps(vue), prefs))


_TOUT = "{blocks:{thought:true,perimeter:true,queue:true,countdown:true,rawlog:true}}"


# ══════════════════════════════════════════════════════════════════════════
#  1. LE GARDE QUI FERME LA CLASSE DE DEFAUT
# ══════════════════════════════════════════════════════════════════════════


def test_AUCUNE_case_ne_pilote_le_vide():
    """Le seul test de ce fichier qui protege l'AVENIR.

    Toute case ajoutee au customizer doit etre lue par au moins une vue. Sans
    ce garde, on peut offrir a l'utilisateur un interrupteur relie a rien — et
    personne ne s'en apercoit, parce que rien ne casse."""
    chassis = (_JS / "mission_panel.js").read_text(encoding="utf-8")
    vues = re.sub(r"/\*.*?\*/", "",
                  (_JS / "mission_views.js").read_text(encoding="utf-8"), flags=re.S)
    bloc = chassis[chassis.index("var BLOCS = ["):chassis.index("];", chassis.index("var BLOCS = ["))]
    cases = re.findall(r"\['([\w]+)',", bloc)
    assert len(cases) >= 5, f"BLOCS mal lu : {cases}"
    orphelines = [c for c in cases if ("_on(prefs, '%s')" % c) not in vues]
    assert not orphelines, (
        f"cases du menu Affichage que personne ne lit : {orphelines} — "
        f"un interrupteur relie a rien"
    )


def test_chaque_case_a_bien_UN_effet_visible():
    """Complement du precedent : on le PROUVE en rendant deux fois."""
    tout = _rendu(_ev(), _TOUT)
    for c in ("thought", "perimeter", "queue", "countdown", "rawlog"):
        sans = _rendu(_ev(), "{blocks:{%s:false}}" % c)
        assert sans != tout, f"decocher « {c} » ne change rien a l'ecran"


# ══════════════════════════════════════════════════════════════════════════
#  2. LE MODELE GARDE UN FIL, BORNE
# ══════════════════════════════════════════════════════════════════════════


def test_le_modele_garde_enfin_une_trace():
    """Il comptait les evenements sans en conserver un seul."""
    m = _node("return B(%s, %s, 0)[0].children[0];" % (json.dumps(_ARBRE), json.dumps(_ev())))
    assert len(m["trail"]) == 4 and m["events"] == 4


def test_le_fil_est_BORNE():
    """Une mission longue produit des milliers d'evenements ; douze lignes
    suffisent a comprendre ce qui vient de se passer."""
    m = _node("return B(%s, %s, 0)[0].children[0];"
              % (json.dumps(_ARBRE), json.dumps(_ev(n=50))))
    assert len(m["trail"]) == 12
    assert m["events"] == 51, "le COMPTEUR, lui, ne doit pas etre borne"


def test_le_plus_recent_est_en_TETE():
    """C'est ce qui vient de se passer qui interesse, pas le debut."""
    ev = [{"task_id": "w1", "stage": "premier"}, {"task_id": "w1", "stage": "dernier"}]
    m = _node("return B(%s, %s, 0)[0].children[0];" % (json.dumps(_ARBRE), json.dumps(ev)))
    assert m["trail"][0]["stage"] == "dernier"


def test_le_fil_ne_MELANGE_pas_les_taches():
    ev = _ev("w1", 2) + _ev("m", 2)
    m = _node("return B(%s, %s, 0)[0];" % (json.dumps(_ARBRE), json.dumps(ev)))
    assert len(m["trail"]) == 3 and len(m["children"][0]["trail"]) == 3


def test_une_tache_SANS_evenement_a_un_fil_vide_pas_null():
    m = _node("return B(%s, [], 0)[0].children[0];" % json.dumps(_ARBRE))
    assert m["trail"] == []


def test_un_evenement_INCOMPLET_ne_casse_rien():
    """Un backend pas encore a jour n'enverra ni `tool_name` ni `summary`."""
    ev = [{"task_id": "w1"}]
    m = _node("return B(%s, %s, 0)[0].children[0].trail[0];"
              % (json.dumps(_ARBRE), json.dumps(ev)))
    assert m["stage"] == "" and m["tool"] == "" and m["summary"] == ""


# ══════════════════════════════════════════════════════════════════════════
#  3. CE QUE LA CASE AFFICHE
# ══════════════════════════════════════════════════════════════════════════


def test_la_case_cochee_MONTRE_le_journal():
    h = _rendu(_ev(), _TOUT)
    assert "mp-log" in h and "Journal brut" in h
    assert "edit_file" in h and "fichier_0.py" in h


def test_la_case_decochee_le_CACHE():
    assert "mp-log" not in _rendu(_ev(), "{blocks:{rawlog:false}}")


def test_le_journal_est_REPLIE_par_defaut():
    """C'est un outil de diagnostic, pas une lecture."""
    h = _rendu(_ev(), _TOUT)
    i = h.index("<details class=\"mp-log\"")
    assert " open" not in h[i:i + 40]


def test_le_journal_montre_ce_que_le_flux_a_DIT():
    """Sans reformuler : c'est tout l'interet d'un journal BRUT, et la demande
    d'origine du chantier etait « on ne voit pas leurs pensees alors qu'elles
    sont dans les logs »."""
    ev = [{"task_id": "w1", "stage": "browser_open", "tool_name": "browser_open",
           "summary": "http://127.0.0.1:8245/"}]
    h = _rendu(ev, _TOUT)
    assert "browser_open" in h and "127.0.0.1:8245" in h


def test_une_erreur_du_flux_se_VOIT():
    ev = [{"task_id": "w1", "stage": "tool_error", "status": "error",
           "tool_name": "run_command", "summary": "exit 1"}]
    h = _rendu(ev, _TOUT)
    assert "is-ko" in h


def test_le_journal_est_ECHAPPE():
    ev = [{"task_id": "w1", "stage": "x", "summary": "<img onerror=alert(1)>"}]
    h = _rendu(ev, _TOUT)
    assert "<img" not in h and "&lt;img" in h


def test_une_tache_sans_trace_le_DIT_au_lieu_de_disparaitre():
    h = _rendu([], _TOUT)
    assert "Aucune trace reçue" in h


def test_le_compteur_affiche_le_TOTAL_pas_les_douze_gardees():
    """Sinon toute mission longue afficherait « 12 » et on croirait qu'il ne
    s'est rien passe."""
    h = _rendu(_ev(n=50), _TOUT)
    assert "Journal brut · 51" in h


# ══════════════════════════════════════════════════════════════════════════
#  4. DANS QUELLES VUES
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("vue", ["workshop", "control"])
def test_le_journal_est_disponible_la_ou_il_y_a_une_TACHE(vue):
    assert "mp-log" in _rendu(_ev(), _TOUT, vue=vue)


def test_le_RUBAN_n_a_pas_de_journal_et_c_est_normal():
    """Il n'affiche pas de tache : c'est une vue de chronologie, une piste par
    worker. Y coller un journal n'aurait ou se poser."""
    assert "mp-log" not in _rendu(_ev(), _TOUT, vue="ribbon")


# ══════════════════════════════════════════════════════════════════════════
#  5. L'HABILLAGE
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("sel", [".mp-log", ".mp-log-list", ".mp-log-l.is-ko",
                                 ".mp-log > summary:focus-visible"])
def test_chaque_element_a_son_style(sel):
    assert sel in _CSS.read_text(encoding="utf-8"), sel


def test_le_journal_ne_peut_pas_ETIRER_la_carte():
    """Douze lignes de monospace dans une carte de 138 px la feraient exploser."""
    css = re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)
    i = css.index(".mp-log-list")
    corps = css[i:css.index("}", i)]
    assert "max-height" in corps and "overflow-y: auto" in corps
