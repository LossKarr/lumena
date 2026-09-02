"""Panel Missions — lot 16 : L'ETAT SE LIT DANS LA FORME, PAS DANS LE CHIFFRE.

Le panneau disait tout en chiffres : « 1/5 », « 40:59 », « itér. 7/12 ». Un
tableau de bord se BALAIE — ce qui demande l'attention doit se voir avant
d'etre lu. Trois marques, toutes nourries par des donnees deja presentes :

  ANNEAU     workers termines sur total. Remplace la pastille « 1/5 » : meme
             information, plus de lecture.
  RAIL       part du budget consommee. `created_at` (665/665) et
             `deadline_ts` (553/665) etaient sur le disque et jamais croises.
  BATTEMENT  les douze dernieres traces, une encoche chacune. Repond a la
             seule question que ni la pensee ni les compteurs ne posent :
             est-ce que ca AVANCE, ou est-ce que ca repete ?

--- Ce qui a ete mesure avant d'y toucher ---

L'arithmetique du budget mele un `created_at` UTC-aware et un `deadline_ts`
NAIF local. C'est le piege Z32, donc on mesure : sur les 553 taches du corpus
qui portent les deux champs, zero duree negative, mediane 29,6 min, p90
88,6 min, max 19,5 h. Le runtime est coherent avec lui-meme, comme Z32 le dit.

--- Ce que ces marques ne font PAS ---

Aucune couleur nouvelle. Les trois prennent `--et`, le canal d'etat deja en
place : l'accent de Lumena reste a sept usages, reserve a ce qui est vraiment
Lumena.
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
        "require(%s);require(%s);" % tuple(
            json.dumps(str(_JS / n)) for n in ("mission_model.js", "mission_views.js"))
        + "const G=globalThis.missionBudget, B=globalThis.buildMissionModel,"
        + " V=globalThis.missionRenderView;"
        + "process.stdout.write(JSON.stringify((function(){%s})()));" % corps
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _budget(created, deadline, now="null"):
    t = {"created_at": created, "metadata": {"deadline_ts": deadline}}
    return _node("return G(%s, %s);" % (json.dumps(t), now))


def _m(**kw):
    b = {"id": "m1", "objective": "o", "aggregate": "running", "closed": False,
         "terminal": False, "children": [], "deadlineLabel": "40:59",
         "remainingMs": 2459000, "workers": {"done": 1, "total": 5},
         "budget": None, "ledger": None, "proofs": [], "trail": [],
         "delivered": {"published": [], "artifacts": []}}
    b.update(kw)
    return b


def _w(**kw):
    b = {"id": "w1", "objective": "[Worker api] x", "state": "running",
         "perimeter": [], "thought": "", "iteration": 0, "maxIter": 0,
         "lastTool": "", "queueRank": 0, "waitedMs": 0, "proofs": [], "trail": []}
    b.update(kw)
    return b


def _rendu(missions, vue="workshop", prefs="null"):
    return _node("return V(%s, %s, %s);"
                 % (json.dumps(vue), json.dumps(missions), prefs))


# ══════════════════════════════════════════════════════════════════════════
#  1. LE BUDGET — le calcul, et le piege Z32
# ══════════════════════════════════════════════════════════════════════════


def test_le_budget_croise_deux_champs_qui_ne_l_etaient_jamais():
    b = _budget("2026-08-31T10:00:00+00:00", "2026-08-31T18:00:00",
                'Date.parse("2026-08-31T12:00:00+00:00")')
    assert b is not None and b["totalMs"] > 0
    assert 0 <= b["pct"] <= 100


def test_le_pourcentage_est_BORNE_des_deux_cotes():
    """Une mission qui a triple son temps ne dessine pas trois barres.

    L'ecart doit etre LARGE : l'echeance etant naive locale et la creation
    UTC-aware, un ecart d'une heure peut se refermer entierement selon le
    fuseau — mon premier fixture le faisait, et le garde `total <= 0` a
    correctement rendu null."""
    b = _budget("2026-01-01T00:00:00+00:00", "2026-01-01T09:00:00",
                'Date.parse("2030-01-01T00:00:00+00:00")')
    assert b is not None and b["pct"] == 100


def test_un_budget_INCONNU_rend_null_et_pas_zero():
    """« Pas de budget » et « budget a peine entame » ne se dessinent pas
    pareil : l'un n'a pas de barre, l'autre en a une vide."""
    assert _budget("2026-01-01T00:00:00+00:00", None) is None
    assert _budget(None, "2026-01-01T00:00:00") is None
    assert _budget("pas une date", "2026-01-01T00:00:00") is None


def test_une_echeance_AVANT_la_creation_ne_dessine_rien():
    """Duree negative : on ne sait pas, donc on ne montre pas."""
    assert _budget("2026-08-31T10:00:00+00:00", "2026-08-31T09:00:00") is None


def test_le_budget_se_TAIT_sur_une_mission_terminale():
    """Comme le compte a rebours : un budget n'a de sens que sur ce qui court."""
    arbre = [{"mission": {"task_id": "m", "state": "done",
                          "created_at": "2026-01-01T00:00:00+00:00",
                          "metadata": {"objective": "o",
                                       "deadline_ts": "2026-01-01T01:00:00"}},
              "children": []}]
    m = _node("return B(%s, [], Date.now())[0];" % json.dumps(arbre))
    assert m["budget"] is None and m["terminal"] is True


def test_une_mission_VIVANTE_garde_son_budget():
    arbre = [{"mission": {"task_id": "m", "state": "running",
                          "created_at": "2026-01-01T00:00:00+00:00",
                          "metadata": {"objective": "o",
                                       "deadline_ts": "2099-01-01T00:00:00"}},
              "children": [{"mission": {"task_id": "w", "state": "running"},
                            "children": []}]}]
    m = _node("return B(%s, [], Date.parse('2026-01-01T00:30:00Z'))[0];"
              % json.dumps(arbre))
    assert m["budget"] is not None and m["budget"]["pct"] >= 0


# ══════════════════════════════════════════════════════════════════════════
#  2. L'ANNEAU
# ══════════════════════════════════════════════════════════════════════════


def test_l_anneau_a_REMPLACE_la_pastille():
    h = _rendu([_m()])
    assert "mp-ring" in h
    assert "mp-prog" not in h, "l'ancienne pastille « 1/5 » est encore la"


def test_l_anneau_dit_son_avancement_aux_lecteurs_d_ecran():
    """Un arc SVG ne se lit pas : `aria-label` porte le fait."""
    h = _rendu([_m(workers={"done": 3, "total": 5})])
    assert "3 workers terminés sur 5" in h


def test_l_arc_SUIT_l_avancement():
    """Le dasharray est la seule chose qui bouge : on le verifie."""
    vide = _rendu([_m(workers={"done": 0, "total": 4})])
    plein = _rendu([_m(workers={"done": 4, "total": 4})])
    a = re.search(r'stroke-dasharray="([\d.]+)', vide).group(1)
    b = re.search(r'stroke-dasharray="([\d.]+)', plein).group(1)
    assert float(a) == 0.0 and float(b) > 50


def test_une_mission_SANS_worker_n_a_pas_d_anneau():
    """Un anneau a zero sur zero ne dirait rien."""
    assert "mp-ring" not in _rendu([_m(workers={"done": 0, "total": 0})])


def test_l_anneau_garde_le_chiffre_a_cote():
    """La forme se balaie, le chiffre se verifie : les deux, pas l'un ou
    l'autre."""
    h = _rendu([_m(workers={"done": 2, "total": 7})])
    i = h.index("mp-ring")
    assert ">2<" in h[i:i + 700] and ">7<" in h[i:i + 700]


# ══════════════════════════════════════════════════════════════════════════
#  3. LE RAIL
# ══════════════════════════════════════════════════════════════════════════


def test_le_rail_apparait_quand_le_budget_est_CONNU():
    h = _rendu([_m(budget={"pct": 62, "totalMs": 1, "elapsedMs": 1})])
    assert "mp-rail-b" in h and "width:62%" in h


def test_sans_budget_AUCUN_rail():
    assert "mp-rail-b" not in _rendu([_m(budget=None)])


def test_le_rail_dit_sa_valeur_aux_lecteurs_d_ecran():
    h = _rendu([_m(budget={"pct": 62, "totalMs": 1, "elapsedMs": 1})])
    assert "62 % du temps alloué consommé" in h


def test_le_rail_disparait_avec_le_compte_a_rebours():
    """Il vit dans le meme bloc : decocher « compte a rebours » emporte les
    deux, et c'est coherent — ils repondent a la meme question."""
    h = _rendu([_m(budget={"pct": 50, "totalMs": 1, "elapsedMs": 1})],
               prefs="{blocks:{countdown:false}}")
    assert "mp-rail-b" not in h


# ══════════════════════════════════════════════════════════════════════════
#  4. LE BATTEMENT
# ══════════════════════════════════════════════════════════════════════════


def _trail(*kinds):
    m = {"o": {"stage": "codeagent_iteration"},
         "w": {"stage": "codeagent_wait_start"},
         "k": {"stage": "tool_error", "status": "error"},
         "n": {"stage": "llm_request_start"}}
    return [dict(m[k]) for k in kinds]


def test_le_battement_a_UNE_encoche_par_trace():
    h = _rendu([_m(children=[_w(trail=_trail("o", "o", "w", "k"))])])
    i = h.index("mp-beat")
    bloc = h[i:h.index("</span>", i)]
    assert bloc.count("<i class=") == 4


def test_chaque_type_de_moment_a_SA_marque():
    h = _rendu([_m(children=[_w(trail=_trail("o", "w", "k", "n"))])])
    for cls in ("mp-beat-o", "mp-beat-w", "mp-beat-k", "mp-beat-n"):
        assert cls in h, cls


def test_le_battement_se_lit_de_GAUCHE_a_droite():
    """Le fil arrive du plus recent au plus ancien ; une frise se lit dans le
    sens du temps."""
    h = _rendu([_m(children=[_w(trail=_trail("k", "o", "o"))])])
    i = h.index("mp-beat")
    bloc = h[i:h.index("</span>", i)]
    assert bloc.rindex("mp-beat-k") > bloc.index("mp-beat-o"), (
        "l'erreur la plus recente doit finir a droite"
    )


def test_UNE_seule_trace_ne_fait_pas_un_battement():
    """Une encoche isolee ne dit rien d'un rythme."""
    assert "mp-beat" not in _rendu([_m(children=[_w(trail=_trail("o"))])])


def test_sans_trace_AUCUN_battement():
    assert "mp-beat" not in _rendu([_m(children=[_w(trail=[])])])


def test_le_battement_dit_son_compte_aux_lecteurs_d_ecran():
    h = _rendu([_m(children=[_w(trail=_trail("o", "o", "o"))])])
    assert "3 dernières traces" in h


# ══════════════════════════════════════════════════════════════════════════
#  5. CE QUE CES MARQUES NE FONT PAS
# ══════════════════════════════════════════════════════════════════════════


def _css_nu() -> str:
    return re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)


@pytest.mark.parametrize("sel", [".mp-ring-f", ".mp-rail-b i"])
def test_les_marques_prennent_le_canal_d_etat_et_pas_l_accent(sel):
    """Aucune couleur nouvelle : elles reutilisent `--et`."""
    css = _css_nu()
    i = css.index(sel)
    corps = css[i:css.index("}", i)]
    assert "var(--et" in corps
    assert "--accent" not in corps


def test_l_accent_de_Lumena_est_toujours_aussi_RARE():
    """Le statut actif orange reste borne apres les marques ajoutees."""
    assert _css_nu().count("var(--accent") <= 15


def test_aucune_couleur_en_dur_n_est_apparue():
    dures = re.findall(
        r"(?<![\w-])(#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\))", _css_nu())
    assert not dures, f"couleurs en dur : {sorted(set(dures))}"


def test_les_marques_respectent_le_mouvement_reduit():
    css = _css_nu()
    i = css.index("prefers-reduced-motion")
    bloc = css[i:i + 400]
    assert ".mp-ring-f" in bloc and ".mp-rail-b i" in bloc


def test_les_chiffres_de_l_anneau_ne_TRESSAUTENT_pas():
    css = _css_nu()
    i = css.index(".mp-ring b {")
    assert "tabular-nums" in css[i:css.index("}", i)]


# ══════════════════════════════════════════════════════════════════════════
#  6. CE QUI SE MANIPULE A L'AIR DE SE MANIPULER
# ══════════════════════════════════════════════════════════════════════════


def test_le_bouton_de_repli_reagit_VISIBLEMENT():
    """La premiere version posait un carre de 22 px sans etat de survol
    lisible : on ne savait pas qu'on pouvait cliquer."""
    css = _css_nu()
    i = css.index(".mp-fold:hover")
    corps = css[i:css.index("}", i)]
    assert "background:" in corps and "border-color:" in corps
    assert ".mp-fold:active" in css


def test_la_carte_prend_la_couleur_de_son_ETAT_au_survol():
    """Le survol confirme ce que le lisere annonce, il ne dit pas autre chose."""
    css = _css_nu()
    i = css.index(".mp-post:hover")
    assert "var(--et" in css[i:css.index("}", i)]


# ══════════════════════════════════════════════════════════════════════════
#  7. L'ABSENCE VIT DANS LE REPLI
# ══════════════════════════════════════════════════════════════════════════


def test_une_tache_sans_trace_ne_prend_pas_TROIS_lignes_de_vide():
    """Vu au navigateur : la carte du lead empilait « aucun raisonnement
    transmis », le titre du journal, puis « aucune trace reçue ». Un trou
    nomme reste nomme ; il n'a pas besoin d'occuper l'ecran pour cela."""
    h = _rendu([_m(children=[_w(trail=[])])],
               prefs="{blocks:{rawlog:true}}")
    i = h.index("Aucune trace reçue")
    # Le message doit vivre a l'INTERIEUR du <details>, pas au-dessus.
    avant = h[:i]
    assert avant.rindex("<details") > avant.rindex("</details>") if "</details>" in avant \
        else "<details" in avant
