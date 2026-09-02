"""Panel Missions — lot 8 : DISTINGUER UNE MISSION DE LA MISSION D'A COTE.

Le defaut, dit par l'utilisateur devant l'ecran : « on voit pas la difference
entre les missions ». Il avait raison, et la cause etait structurelle — deux
missions empilees produisaient exactement le meme en-tete : meme gris, meme
disposition, et il fallait lire les chiffres pour savoir laquelle demandait de
l'attention.

--- L'etat agrege ---

Une mission n'avait pas d'etat a elle. Ses workers en avaient un ; elle, non.
`aggregateState` en calcule un, et l'ORDRE compte. Il a ETE REVISE au lot 12,
apres avoir vu le panneau reel :

    failed > stalled > clos (done/cancelled) > late > waiting > running

Deux choses ont bouge, et pour deux raisons differentes.

`clos` est passe AVANT `late` parce que trois missions terminees 5/5
s'affichaient « échéance dépassée » avec un compte a rebours qui tournait
encore — quatorze jours de retard sur un travail fini. Une echeance ne
s'applique qu'a ce qui court.

`late` est descendu SOUS les etats deduits des workers parce que le retard a
deja son canal : le compte a rebours de l'en-tete passe en rouge. La pastille
d'agregat est le seul endroit ou un echec peut se voir — la lui prendre, c'est
masquer le seul fait sans autre porte-voix.

Ce que `late` garde : une mission dont tous les workers vont bien peut etre
perdue quand meme. C'est le cas qu'un agregat naif rate, et il est teste.

`waiting` n'est pas « au moins un worker attend » mais « TOUS les vivants
attendent » — c'est-a-dire : la mission n'avance pas. C'est exactement l'etat
que ce chantier existe pour rendre visible, et il etait peint en gris.

--- Le bandeau de synthese ---

Il n'apparait qu'a partir de DEUX missions : avec une seule il ne dirait rien
que l'en-tete ne dise deja. Et il NOMME les missions en difficulte au lieu de
les compter — un compteur n'aide personne a choisir ou regarder.

--- Comment c'est teste ---

Logique pure executee par node, patron du lot 5.4 (`mission_tree.js`). Aucun
DOM, aucun navigateur : ce sont de vraies fonctions appelees avec de vraies
entrees, pas des chaines cherchees dans un fichier.
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
    """Charge les trois modules puis evalue `corps`, qui doit rendre du JSON."""
    script = (
        "require(%s);require(%s);const P=require(%s);"
        % tuple(json.dumps(str(_JS / n)) for n in
                ("mission_model.js", "mission_views.js", "mission_panel.js"))
        + "const A=globalThis.missionAggregateState, S=globalThis.missionSynthese;"
        + "void P;"
        + "process.stdout.write(JSON.stringify((function(){%s})()));" % corps
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _w(state, rank=0):
    return {"state": state, "queueRank": rank, "children": []}


def _agg(kids, remaining="null"):
    return _node("return A(%s, %s);" % (json.dumps(kids), remaining))


# ══════════════════════════════════════════════════════════════════════════
#  1. L'ORDRE DE GRAVITE
# ══════════════════════════════════════════════════════════════════════════


def test_une_echeance_depassee_prime_sur_CINQ_workers_verts():
    """Le cas qu'un agregat naif rate : tout va bien, et pourtant c'est perdu."""
    verts = [_w("running")] * 5
    assert _agg(verts, "-1000") == "late"


def test_un_ECHEC_prime_sur_une_echeance_depassee():
    """DECISION INVERSEE (lot 12). Le lot 8 mettait `late` en tete de tout.

    Le retard a DEJA son canal visuel : le compte a rebours de l'en-tete passe
    en rouge et affiche le depassement. La pastille d'agregat est en revanche
    le SEUL endroit ou un echec peut se voir. Faire gagner `late` masquait
    donc le seul fait qui n'avait pas d'autre porte-voix — et un echec dit
    QUOI reparer, la ou le retard n'est qu'un symptome."""
    assert _agg([_w("failed")], "-1") == "failed"


def test_un_echec_prime_sur_le_reste():
    assert _agg([_w("running"), _w("failed"), _w("waiting")]) == "failed"


def test_une_echeance_ENCORE_valide_ne_declenche_rien():
    assert _agg([_w("running")], "60000") == "running"


def test_une_echeance_pile_a_zero_n_est_pas_depassee():
    """Strictement negatif : a la seconde pres, on ne condamne pas."""
    assert _agg([_w("running")], "0") == "running"


def test_pas_d_echeance_du_tout_ne_declenche_rien():
    assert _agg([_w("running")]) == "running"


# ══════════════════════════════════════════════════════════════════════════
#  2. « LA MISSION N'AVANCE PAS »
# ══════════════════════════════════════════════════════════════════════════


def test_tous_les_vivants_en_attente_donne_WAITING():
    assert _agg([_w("waiting"), _w("waiting"), _w("done")]) == "waiting"


def test_UN_SEUL_qui_travaille_suffit_a_faire_avancer_la_mission():
    """`waiting` n'est pas « au moins un attend » : c'est « aucun n'avance »."""
    assert _agg([_w("waiting"), _w("waiting"), _w("running")]) == "running"


def test_tout_termine_donne_DONE():
    assert _agg([_w("done"), _w("done")]) == "done"


def test_une_mission_sans_worker_reste_en_cours():
    """Le lead travaille seul : ce n'est ni fini ni bloque."""
    assert _agg([]) == "running"


def test_les_taches_terminees_ne_comptent_pas_comme_des_attentes():
    assert _agg([_w("done"), _w("done"), _w("running")]) == "running"


# ══════════════════════════════════════════════════════════════════════════
#  3. LE MODELE POSE L'AGREGAT SUR CHAQUE MISSION
# ══════════════════════════════════════════════════════════════════════════


def test_buildModel_attache_l_agregat_a_la_mission():
    tree = [{"mission": {"task_id": "m", "state": "running"},
             "children": [{"mission": {"task_id": "a", "state": "running"}, "children": []},
                          {"mission": {"task_id": "b", "state": "done"}, "children": []}]}]
    m = _node("return globalThis.buildMissionModel(%s, [], 0)[0];" % json.dumps(tree))
    assert m["aggregate"] == "running"


def test_l_agregat_suit_le_flux_SSE_et_pas_seulement_le_REST():
    """Un worker qui attend le verrou n'est `waiting` que grace aux traces."""
    tree = [{"mission": {"task_id": "m", "state": "running"},
             "children": [{"mission": {"task_id": "a", "state": "running"}, "children": []}]}]
    ev = [{"task_id": "a", "stage": "codeagent_wait_start"}]
    m = _node("return globalThis.buildMissionModel(%s, %s, 0)[0];"
              % (json.dumps(tree), json.dumps(ev)))
    assert m["aggregate"] == "waiting", (
        "sans le flux, une mission entierement bloquee passe pour active"
    )


# ══════════════════════════════════════════════════════════════════════════
#  4. LE BANDEAU DE SYNTHESE
# ══════════════════════════════════════════════════════════════════════════


def _miss(agg, kids=None):
    return {"aggregate": agg, "children": kids or [], "objective": "o", "id": "i"}


def _syn(missions):
    return _node("return S(%s);" % json.dumps(missions))


def test_une_seule_mission_n_a_PAS_de_bandeau():
    """Il ne dirait rien que l'en-tete ne dise deja."""
    assert _syn([_miss("running")]) == ""
    assert _syn([]) == ""


def test_deux_missions_declenchent_le_bandeau():
    assert "mp-sum" in _syn([_miss("running"), _miss("running")])


def test_les_missions_en_difficulte_sont_NOMMEES_pas_comptees():
    """Un compteur n'aide personne a choisir ou regarder."""
    h = _syn([_miss("late"), _miss("running"), _miss("waiting")])
    assert 'data-agg="late"' in h and 'data-agg="waiting"' in h
    assert "échéance dépassée" in h and "attend" in h


def test_quand_tout_va_bien_le_bandeau_le_DIT():
    h = _syn([_miss("running"), _miss("running")])
    assert "aucune mission en difficulté" in h
    assert "mp-sum-bad" not in h


def test_seuls_les_workers_VIVANTS_sont_comptes():
    kids = [_w("running"), _w("waiting"), _w("done"), _w("failed")]
    h = _syn([_miss("running", kids), _miss("running")])
    assert ">2</b> workers actifs" in h.replace('class="mp-mono"', "").replace("<b ", "<b")


def test_la_file_n_est_annoncee_que_si_elle_existe():
    sans = _syn([_miss("running", [_w("running")]), _miss("running")])
    avec = _syn([_miss("running", [_w("waiting", 1)]), _miss("running")])
    assert "en file" not in sans
    assert "en file" in avec


# ══════════════════════════════════════════════════════════════════════════
#  5. LES QUATRE VUES PORTENT TOUTES L'AGREGAT
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("vue", ["workshop", "ribbon", "control"])
def test_chaque_vue_teinte_son_en_tete(vue):
    m = [_miss("late")]
    h = _node("return globalThis.missionRenderView(%s, %s, null);"
              % (json.dumps(vue), json.dumps(m)))
    assert 'data-agg="late"' in h, f"la vue {vue} n'affiche pas l'etat de la mission"
    assert "échéance dépassée" in h


@pytest.mark.parametrize("vue", ["workshop", "ribbon", "control"])
def test_chaque_vue_montre_le_bandeau_quand_il_y_a_PLUSIEURS_missions(vue):
    h = _node("return globalThis.missionRenderView(%s, %s, null);"
              % (json.dumps(vue), json.dumps([_miss("running"), _miss("failed")])))
    assert "mp-sum" in h, f"la vue {vue} n'a pas de synthese"


def test_un_agregat_inconnu_ne_vide_pas_l_ecran():
    """Une version future du backend ne doit pas casser le panneau."""
    h = _node("return globalThis.missionRenderView('workshop', %s, null);"
              % json.dumps([_miss("cosmique")]))
    assert "mp-head" in h and "en cours" in h


# ══════════════════════════════════════════════════════════════════════════
#  6. L'HABILLAGE SUIT
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("agg,token", [
    ("running", "--accent"), ("waiting", "--warn"),
    ("failed", "--danger"), ("late", "--danger"), ("done", "--ok"),
])
def test_chaque_agregat_a_sa_couleur(agg, token):
    css = re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)
    i = css.index(f'[data-agg="{agg}"]')
    assert f"--et: var({token})" in css[i:css.index("}", i)]


def test_le_filet_de_mission_traverse_tout_l_en_tete():
    """Un filet de 3 px sur le cote d'une carte se voit ; sur un en-tete large,
    c'est la largeur entiere qui porte le signal."""
    css = re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)
    i = css.index(".mp-head::before")
    corps = css[i:css.index("}", i)]
    assert "left: 0" in corps and "right: 0" in corps
    assert "var(--et" in corps
