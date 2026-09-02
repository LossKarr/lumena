"""Panel Missions — lot 12 : REPLIER une mission, et pouvoir l'ARRETER.

Trois demandes de l'utilisateur devant le panneau reel, dans l'ordre ou elles
sont arrivees :

  1. « il faudrait pouvoir reduire les fenetres de mission, c'est trop de trucs
     ouverts » — deux missions font onze cartes, et rien ne permettait d'en
     fermer une.

  2. « on ne peut plus arreter une mission depuis l'UI, ce n'est pas normal » —
     une REGRESSION que j'ai introduite. `cancelMissionUi` n'a jamais cesse
     d'exister ni de marcher : c'est le BOUTON qui a disparu quand le rendu v2
     a remplace `_renderMissionNode`. Une capacite orpheline — exactement le
     motif que ce panneau passe son temps a fermer ailleurs, cette fois cause
     par moi.

  3. Une capture : trois missions terminees 5/5, toutes marquees « ÉCHÉANCE
     DÉPASSÉE », avec un compte a rebours qui egrenait `-346:30:51`. Quatorze
     jours de retard sur un travail fini.

--- La regle de repli ---

Une mission CLOSE se replie seule : elle n'a plus rien a montrer qui vaille la
moitie de l'ecran. Une mission vivante reste ouverte. Mais un choix EXPLICITE
de l'utilisateur gagne toujours : `prefs.folded` ne contient que des decisions
prises, et l'absence d'entree veut dire « pas encore decide », jamais
« deplie ».

Repliee, la mission ne fait pas un `display:none` : son corps n'est pas RENDU
du tout. Avec dix missions closes, c'est du travail qu'on ne fait pas.
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
_PANELS = _ROOT / "web" / "static" / "js" / "panels.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node indisponible")

VUES = ["workshop", "ribbon", "control"]


def _node(corps: str):
    script = (
        "require(%s);require(%s);const P=require(%s);" % tuple(
            json.dumps(str(_JS / n)) for n in
            ("mission_model.js", "mission_views.js", "mission_panel.js"))
        + "const V=globalThis.missionRenderView, F=globalThis.missionEstReplie;"
        + "process.stdout.write(JSON.stringify((function(){%s})()));" % corps
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _m(agg="running", **kw):
    b = {"id": "m1", "objective": "Objectif", "aggregate": agg,
         "closed": False, "terminal": False,
         "children": [], "deadlineLabel": "12:00", "remainingMs": 720000,
         "workers": {"done": 0, "total": 0}, "ledger": None, "proofs": [],
         "delivered": {"published": [], "artifacts": []}}
    b.update(kw)
    return b


def _w(state="running", rank=0):
    return {"id": "w1", "objective": "[Worker api] x", "state": state,
            "perimeter": [], "thought": "", "iteration": 0, "maxIter": 0,
            "lastTool": "", "queueRank": rank, "waitedMs": 0, "proofs": []}


def _rendu(missions, prefs="null", vue="workshop"):
    return _node("return V(%s, %s, %s);"
                 % (json.dumps(vue), json.dumps(missions), prefs))


def _replie(m, prefs="null"):
    return _node("return F(%s, %s);" % (json.dumps(m), prefs))


# ══════════════════════════════════════════════════════════════════════════
#  1. LA REGLE PAR DEFAUT
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("agg", ["done", "cancelled"])
def test_une_mission_CLOSE_se_replie_seule(agg):
    """Elle n'a plus rien a montrer qui vaille la moitie de l'ecran."""
    assert _replie(_m(agg)) is True


@pytest.mark.parametrize("agg", ["running", "waiting", "late", "failed", "stalled"])
def test_une_mission_VIVANTE_reste_ouverte(agg):
    assert _replie(_m(agg)) is False


def test_un_agregat_inconnu_reste_OUVERT():
    """Dans le doute, on montre : cacher est la decision risquee."""
    assert _replie(_m("cosmique")) is False


# ══════════════════════════════════════════════════════════════════════════
#  2. LE CHOIX DE L'UTILISATEUR GAGNE
# ══════════════════════════════════════════════════════════════════════════


def test_deplier_explicitement_une_mission_CLOSE_marche():
    assert _replie(_m("done"), "{folded:{m1:false}}") is False


def test_replier_explicitement_une_mission_VIVANTE_marche():
    assert _replie(_m("running"), "{folded:{m1:true}}") is True


def test_l_absence_d_entree_n_est_PAS_un_choix():
    """« Pas encore decide » n'est pas « deplie » : la regle doit s'appliquer."""
    assert _replie(_m("done"), "{folded:{autre:false}}") is True


# ══════════════════════════════════════════════════════════════════════════
#  3. CE QUE LE REPLI CHANGE A L'ECRAN
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("vue", VUES)
def test_les_quatre_vues_respectent_le_repli(vue):
    h = _rendu([_m("done", children=[_w("done")])], vue=vue)
    assert 'data-folded="1"' in h, f"la vue {vue} ignore le repli"


@pytest.mark.parametrize("vue", VUES)
def test_le_corps_n_est_pas_RENDU_du_tout(vue):
    """Ce n'est pas un `display:none` : c'est du travail qu'on ne fait pas."""
    ouvert = _rendu([_m("running", children=[_w()])], vue=vue)
    ferme = _rendu([_m("running", children=[_w()])], "{folded:{m1:true}}", vue=vue)
    assert len(ferme) < len(ouvert) * 0.75, (
        f"vue {vue} : le repli n'economise presque rien "
        f"({len(ferme)} contre {len(ouvert)} caracteres)"
    )


def test_l_en_tete_survit_au_repli():
    """Repliee, la mission doit rester identifiable et actionnable."""
    h = _rendu([_m("running", children=[_w()])], "{folded:{m1:true}}")
    for morceau in ("mp-head", "Objectif", "data-mp-fold", "mp-agg"):
        assert morceau in h, morceau


def test_repliee_la_mission_garde_une_ligne_de_FAIT():
    """Un titre seul ne dirait pas s'il se passe quelque chose dedans."""
    h = _rendu([_m("running", children=[_w("running"), _w("waiting", 1)])],
               "{folded:{m1:true}}")
    assert "mp-fold-sum" in h
    assert "2 workers" in h and "actif" in h and "en file" in h


def test_une_mission_SANS_worker_n_a_pas_de_ligne_de_fait():
    h = _rendu([_m("running")], "{folded:{m1:true}}")
    assert "mp-fold-sum" not in h


def test_le_bouton_annonce_son_etat_aux_lecteurs_d_ecran():
    ouvert = _rendu([_m("running")])
    ferme = _rendu([_m("running")], "{folded:{m1:true}}")
    assert 'aria-expanded="true"' in ouvert and "Replier" in ouvert
    assert 'aria-expanded="false"' in ferme and "Déplier" in ferme


# ══════════════════════════════════════════════════════════════════════════
#  4. ARRETER UNE MISSION — la capacite etait ORPHELINE
# ══════════════════════════════════════════════════════════════════════════


def test_le_handler_d_annulation_n_a_JAMAIS_disparu():
    """Il n'a jamais cesse de marcher : c'est le bouton qui manquait."""
    src = _PANELS.read_text(encoding="utf-8")
    assert "export async function cancelMissionUi" in src
    assert "method: 'DELETE'" in src


def test_le_bouton_ARRETER_est_revenu():
    assert "data-mp-cancel" in _rendu([_m("running")])


@pytest.mark.parametrize("vue", VUES)
def test_on_peut_arreter_depuis_les_QUATRE_vues(vue):
    assert "data-mp-cancel" in _rendu([_m("running")], vue=vue)


def test_on_peut_arreter_une_mission_REPLIEE():
    """Sinon replier une mission reviendrait a perdre le moyen de l'arreter."""
    assert "data-mp-cancel" in _rendu([_m("running")], "{folded:{m1:true}}")


@pytest.mark.parametrize("agg", ["done", "cancelled", "failed"])
def test_on_n_arrete_pas_ce_qui_est_DEJA_TERMINAL(agg):
    """`terminal`, pas `closed` : les deux notions ont ete SEPAREES au lot 13.

    `terminal` = la mission ne tournera plus (terminee, annulee, echouee) :
    son compte a rebours n'a plus d'objet et on ne peut plus l'arreter.
    `closed` = elle se replie d'elle-meme, et un ECHEC ne se replie pas :
    c'est justement ce qu'il faut regarder. Les confondre laissait sept
    missions echouees egrener leur retard."""
    assert "data-mp-cancel" not in _rendu([_m(agg, terminal=True)])


def test_un_ECHEC_est_terminal_mais_reste_DEPLIE():
    """Les deux notions ne se recouvrent pas, et c'est le seul cas ou ca se
    voit : on ne peut plus l'arreter, mais on doit le voir."""
    h = _rendu([_m("failed", terminal=True, closed=False, children=[_w("failed")])])
    assert "data-mp-cancel" not in h
    assert 'data-folded' not in h


def test_le_bouton_DIT_que_l_arret_est_cooperatif():
    """« s'arrete au prochain checkpoint » : l'API ne tue rien en plein vol,
    et l'utilisateur doit le savoir avant de cliquer."""
    h = _rendu([_m("running")])
    assert "prochain checkpoint" in h


def test_le_bouton_est_RELIE_au_handler_existant():
    src = re.sub(r"/\*.*?\*/", "", _PANELS.read_text(encoding="utf-8"), flags=re.S)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    i = src.index("data-mp-cancel")
    assert "cancelMissionUi(" in src[i:i + 600], (
        "le bouton n'appelle pas le handler : on aurait rebranche du vide"
    )


# ══════════════════════════════════════════════════════════════════════════
#  5. LE CABLAGE
# ══════════════════════════════════════════════════════════════════════════


def _code_panels() -> str:
    src = re.sub(r"/\*.*?\*/", "", _PANELS.read_text(encoding="utf-8"), flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


@pytest.mark.parametrize("action", ["data-mp-fold", "data-mp-foldall", "data-mp-cancel"])
def test_chaque_commande_neuve_est_branchee(action):
    assert action in _code_panels(), f"{action} n'est relie a rien"


def test_le_repli_ECRIT_un_choix_explicite():
    """Le DOM n'est jamais la source de verite : la preference l'est."""
    c = _code_panels()
    i = c.index("cible.dataset.mpFold)")
    bloc = c[i:i + 700]
    assert "p.folded[id]" in bloc and "ecrirePrefs(p)" in bloc


def test_tout_replier_bascule_dans_les_DEUX_sens():
    """Un bouton qui ne fait qu'un sens oblige a cliquer mission par mission
    pour revenir."""
    c = _code_panels()
    i = c.index("mpFoldall")
    bloc = c[i:i + 700]
    assert "every" in bloc and "!toutReplie" in bloc


def test_l_arret_ne_passe_PAS_par_la_preference():
    """Arreter une mission est un acte serveur, pas un reglage d'affichage."""
    c = _code_panels()
    i = c.index("cible.dataset.mpCancel")
    # Jusqu'au `return;`, pas une fenetre a la louche : 260 caracteres
    # debordaient sur la branche suivante et attrapaient son `ecrirePrefs`.
    j = c.index("return;", i)
    bloc = c[i:j]
    assert "ecrirePrefs" not in bloc
    assert "cancelMissionUi(" in bloc
    assert j - i < 200, "l'arret doit court-circuiter tout de suite"


# ══════════════════════════════════════════════════════════════════════════
#  6. LA PREFERENCE EST BORNEE
# ══════════════════════════════════════════════════════════════════════════


def test_le_dictionnaire_de_repli_est_BORNE():
    """Sans borne, une entree s'accumule par mission vue et le stockage local
    grossit sans fin."""
    gros = {"m%d" % i: True for i in range(200)}
    out = _node("const P=require(%s);return Object.keys(P.normalise({folded:%s}).folded).length;"
                % (json.dumps(str(_JS / "mission_panel.js")), json.dumps(gros)))
    assert out <= 80, f"{out} entrees conservees : le stockage grossit sans fin"


def test_un_stockage_CORROMPU_ne_casse_pas_le_repli():
    for mauvais in ('{folded: "oui"}', '{folded: null}', '{folded: {m1: "peut-etre"}}'):
        out = _node("const P=require(%s);return P.normalise(%s).folded;"
                    % (json.dumps(str(_JS / "mission_panel.js")), mauvais))
        assert out == {}, mauvais


def test_la_remise_a_zero_deplie_tout():
    out = _node("const P=require(%s);return P.normalise({}).folded;"
                % json.dumps(str(_JS / "mission_panel.js")))
    assert out == {}


# ══════════════════════════════════════════════════════════════════════════
#  7. UNE MISSION FINIE NE COURT PLUS APRES SON ECHEANCE
# ══════════════════════════════════════════════════════════════════════════


def test_le_compte_a_rebours_se_TAIT_sur_une_mission_close():
    """Vu sur le panneau reel : `-346:30:51` sur un travail termine 5/5, en
    rouge, en haut de l'ecran — l'element le plus voyant disait une chose sans
    objet."""
    tree = [{"mission": {"task_id": "m", "state": "running",
                         "metadata": {"deadline_ts": "2020-01-01T00:00:00"}},
             "children": [{"mission": {"task_id": "a", "state": "done"},
                           "children": []}]}]
    m = _node("return globalThis.buildMissionModel(%s, [], Date.now())[0];"
              % json.dumps(tree))
    assert m["closed"] is True
    assert m["aggregate"] == "done", "une mission achevee ne peut pas etre en retard"
    assert m["deadlineLabel"] == "", "le decompte tourne encore sur un travail fini"


def test_une_mission_VIVANTE_garde_son_compte_a_rebours():
    """Le correctif ne doit pas faire taire ce qui compte vraiment."""
    tree = [{"mission": {"task_id": "m", "state": "running",
                         "metadata": {"deadline_ts": "2099-01-01T00:00:00"}},
             "children": [{"mission": {"task_id": "a", "state": "running"},
                           "children": []}]}]
    m = _node("return globalThis.buildMissionModel(%s, [], Date.now())[0];"
              % json.dumps(tree))
    assert m["closed"] is False and m["deadlineLabel"]


# ══════════════════════════════════════════════════════════════════════════
#  8. L'HABILLAGE
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("sel", [".mp-fold", ".mp-fold-sum", ".mp-stop",
                                 ".mp-fold:focus-visible", ".mp-stop:focus-visible"])
def test_chaque_element_neuf_a_son_style(sel):
    assert sel in _CSS.read_text(encoding="utf-8"), sel


def test_repliee_l_objectif_tient_sur_DEUX_lignes():
    """Sur le panneau reel, un brief entier s'affichait en gras sur huit lignes
    et noyait tout le reste."""
    css = re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)
    i = css.index('.mp-mission[data-folded] .mp-obj')
    assert "-webkit-line-clamp: 2" in css[i:css.index("}", i)]


def test_arreter_est_teinte_en_DANGER_au_survol():
    css = re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)
    i = css.index(".mp-stop:hover")
    assert "var(--danger)" in css[i:css.index("}", i)]
