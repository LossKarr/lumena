"""Panel Missions — lots 4 et 5 : Ruban et Controle.

Les trois vues partagent UN modele et UNE signature `render(modele, prefs)`.
Changer de vue ne retouche jamais la donnee : c'est tout l'interet du decoupage.

--- Ce que chaque vue doit prouver ici ---

**Ruban** — c'est la seule qui rend visible la serialisation du CodeAgent. La
part hachuree est du temps d'ATTENTE, pas du travail. On ne dessine que ce
qu'on mesure : sans evenements `codeagent_wait_*`, la piste reste pleine au
lieu d'inventer une chronologie.

**Controle** — degradation gracieuse ASSUMEE. Le plan du lead et le ledger ne
sont pas exposes par l'API (lot 5.a, isole parce qu'il touche `react.py`). Les
colonnes le DISENT au lieu d'afficher un faux avancement. C'est la regle de la
maison : mieux vaut un trou nomme qu'une preuve inventee.

--- Une quatrieme vue a existe ---

« Constellation » — le lead au centre, les workers en orbite, le CodeAgent en
porte unique — a ete RETIREE du panneau au lot 17, a la demande. Ses tests de
rendu sont partis avec elle : garder une promesse sur une fonction absente ne
protege rien. La couche 3D qui se montait par-dessus (`mission_scene.js`) reste
sur le disque, dormante, et les tests de son contrat de montage sont marques en
attente plutot que supprimes — ils redeviendront verifiables si la vue revient.
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

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node indisponible")

_LEAD = ("{task_id:'lead',state:'running',metadata:{objective:'Construis LogTriage',"
         "deadline_ts:'2026-08-29T12:00:00'}}")
_W = ("{{task_id:'{i}',state:'{s}',metadata:{{parent_id:'lead',"
      "objective:'[Worker {i}] contrat',allowed_files:['{f}']}}}}")
_TOUS = "[" + ",".join([
    _LEAD,
    _W.format(i="w_data", s="done", f="corpus.json"),
    _W.format(i="w_ia", s="running", f="modele.py"),
    _W.format(i="w_api", s="running", f="api.py"),
]) + "]"
_EV = ("[{task_id:'w_ia',stage:'codeagent_iteration',thought:'Le lissage de Laplace.',"
       "iteration:6,max_iter:12,tool_name:'edit_lines'},"
       "{task_id:'w_api',stage:'codeagent_wait_start'},"
       "{task_id:'w_data',stage:'codeagent_wait_end',duration_ms:41000}]")


def _rendu(vue: str, missions: str = _TOUS, events: str = _EV,
           prefs: str = "null", now: str = "0") -> str:
    script = (
        f"const V=require({json.dumps(str(_JS / 'mission_views.js'))});"
        f"const M=require({json.dumps(str(_JS / 'mission_model.js'))});"
        f"const T=require({json.dumps(str(_JS / 'mission_tree.js'))});"
        f"const m=M.buildModel(T.buildMissionTree({missions}),{events},{now});"
        f"process.stdout.write(V.render({json.dumps(vue)},m,{prefs}));"
    )
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert res.returncode == 0, f"node a echoue: {res.stderr or res.stdout}"
    return res.stdout


VUES = ["workshop", "ribbon", "control"]


# ══════════════════════════════════════════════════════════════════════════
#  1. Les quatre vues existent et partagent le meme contrat
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("vue", VUES)
def test_la_vue_rend_quelque_chose(vue):
    html = _rendu(vue)
    assert html and "mp-mission" in html


@pytest.mark.parametrize("vue", VUES)
def test_la_vue_se_marque_dans_le_dom(vue):
    assert f'data-view="{vue}"' in _rendu(vue)


@pytest.mark.parametrize("vue", VUES)
def test_aucune_mission_ne_leve_jamais(vue):
    assert "Aucune mission" in _rendu(vue, missions="[]", events="[]")


@pytest.mark.parametrize("vue", VUES)
def test_un_backend_sans_les_champs_neufs_rend_quand_meme(vue):
    """Client a jour, serveur pas encore : aucune vue ne doit se vider."""
    assert "mp-mission" in _rendu(vue, events="[]")


@pytest.mark.parametrize("vue", VUES)
def test_l_objectif_est_ECHAPPE_dans_TOUTES_les_vues(vue):
    m = "[{task_id:'lead',state:'running',metadata:{objective:'<script>x</script>'}}]"
    assert "<script>x</script>" not in _rendu(vue, missions=m, events="[]")


def test_une_vue_INCONNUE_retombe_sur_l_atelier():
    """Une preference ecrite par une version future ne doit pas vider l'ecran."""
    assert "mp-mission" in _rendu("vue_du_futur")


# ══════════════════════════════════════════════════════════════════════════
#  2. Ruban — la file du CodeAgent devient visible
# ══════════════════════════════════════════════════════════════════════════


def test_le_ruban_dessine_une_piste_par_worker():
    html = _rendu("ribbon")
    assert html.count('class="mp-lane"') == 3


def test_le_ruban_distingue_l_attente_du_travail():
    html = _rendu("ribbon")
    assert "mp-seg-wait" in html, "le segment d'attente doit exister"
    assert "attente du CodeAgent" in html, "la legende doit le nommer"


def test_le_ruban_chiffre_l_attente_cumulee():
    html = _rendu("ribbon")
    assert "41 s" in html, "l'attente mesuree doit etre affichee, pas seulement dessinee"


def test_le_ruban_dit_quand_il_n_a_RIEN_mesure():
    """On ne dessine pas une chronologie inventee faute de donnees."""
    html = _rendu("ribbon", events="[]")
    assert "Aucune attente mesurée" in html


def test_le_ruban_explique_la_serialisation():
    assert "chacun leur tour" in _rendu("ribbon")


# ══════════════════════════════════════════════════════════════════════════
#  3. Controle — la degradation gracieuse est DITE
# ══════════════════════════════════════════════════════════════════════════


def test_le_controle_a_ses_trois_colonnes():
    html = _rendu("control")
    assert html.count('class="mp-col') >= 3


def test_le_controle_AVOUE_que_le_plan_n_est_pas_expose():
    """Mieux vaut un trou nomme qu'un faux avancement.

    CE TEST A ETE REECRIT (lot 9). Sa premiere version gravait une affirmation
    a moitie fausse : la vue annoncait « Non exposé par l'API » pour le plan ET
    pour le ledger. Verifie sur le corpus reel — 665 taches — le ledger est sur
    le disque dans `last_checkpoint.ledger`, et `to_dict()` de la tache etant un
    `asdict()`, `/api/missions` le transmettait deja au navigateur. Le panneau
    declarait absent ce qu'il tenait en main.

    Ce qui reste vrai : le PLAN du lead n'est persiste nulle part. La colonne
    montre donc la progression reelle et dit, en toutes lettres, que ce n'est
    pas un plan."""
    html = _rendu("control")
    assert "n’est persisté nulle part" in html, (
        "l'absence du plan doit rester NOMMEE, pas silencieuse"
    )
    assert "progression réelle, pas une intention" in html


def test_le_controle_liste_le_perimetre_reel():
    html = _rendu("control")
    for f in ("corpus.json", "modele.py", "api.py"):
        assert f in html, f


def test_le_controle_montre_la_pensee_et_l_avancement():
    html = _rendu("control")
    assert "Le lissage de Laplace." in html
    assert "mp-gauge" in html and "6/12" in html


def test_le_controle_signale_le_rang_dans_la_file():
    assert "file 1" in _rendu("control")
# ══════════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════════
#  5. Les preferences valent pour TOUTES les vues
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("vue", VUES)
def test_masquer_la_pensee_vaut_dans_chaque_vue(vue):
    html = _rendu(vue, prefs="{blocks:{thought:false}}")
    assert "mp-thought" not in html, vue


@pytest.mark.parametrize("vue", ["workshop", "ribbon", "control"])
def test_masquer_la_file_vaut_dans_chaque_vue(vue):
    assert "mp-queue-bar" not in _rendu(vue, prefs="{blocks:{queue:false}}")


@pytest.mark.parametrize("vue", VUES)
def test_masquer_le_compte_a_rebours_vaut_dans_chaque_vue(vue):
    assert "mp-countdown" not in _rendu(vue, prefs="{blocks:{countdown:false}}")


# ══════════════════════════════════════════════════════════════════════════
#  6. Purete — invariant du lot 7
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("interdit", ["document.", "window.", "localStorage", "require("])
def test_les_vues_restent_pures(interdit):
    src = (_JS / "mission_views.js").read_text(encoding="utf-8")
    assert interdit not in src


def test_le_chassis_expose_bien_les_quatre_vues():
    src = (_JS / "mission_panel.js").read_text(encoding="utf-8")
    for v in VUES:
        assert f"'{v}'" in src, v


# ══════════════════════════════════════════════════════════════════════════
#  4 bis. Constellation — le volet ne DESIGNE plus au hasard
# ══════════════════════════════════════════════════════════════════════════
#
#  La version precedente retombait sur `kids[0]` et intitulait quand meme le
#  volet « Nœud actif ». Quand personne ne travaillait, elle designait donc un
#  worker au hasard comme actif — une fausse designation, pas une absence.

_TOUS_FINIS = "[" + ",".join([
    _LEAD,
    _W.format(i="w_a", s="done", f="a.py"),
    _W.format(i="w_b", s="done", f="b.py"),
]) + "]"
_TOUS_EN_FILE = "[" + ",".join([
    _LEAD,
    _W.format(i="w_a", s="running", f="a.py"),
]) + "]"
_SANS_WORKER = "[" + _LEAD + "]"
