"""Panel Missions — lot 3 : le CHASSIS et ses preferences.

C'est la piece qui rend le panneau AMOVIBLE : vue active, densite, blocs
affichables un par un, remise a zero. Meme patron que `overview.js`
(readLayout / saveLayout / renderCustomizer), deja en place cote produit.

--- Ce que ces tests protegent vraiment ---

La lecture des preferences doit etre DEFENSIVE. Un stockage corrompu, un
navigateur en navigation privee, un utilisateur qui a bloque le stockage local :
aucun de ces cas ne doit casser le panneau. C'est exactement le genre de chemin
qu'on n'essaie jamais a la main et qui casse chez quelqu'un d'autre.

Le tampon d'evenements doit etre BORNE : une mission longue produit des milliers
de traces, et on n'a besoin que du dernier etat de chaque tache.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_PANEL = _ROOT / "web" / "static" / "js" / "mission_panel.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node indisponible")


def _node(expr: str, prelude: str = "") -> object:
    script = (
        f"const P = require({json.dumps(str(_PANEL))});"
        f"{prelude}"
        f"process.stdout.write(JSON.stringify({expr}));"
    )
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert res.returncode == 0, f"node a echoue: {res.stderr or res.stdout}"
    return json.loads(res.stdout)


# ══════════════════════════════════════════════════════════════════════════
#  1. Valeurs d'usine
# ══════════════════════════════════════════════════════════════════════════


def test_le_chassis_est_du_javascript_valide():
    res = subprocess.run(["node", "--check", str(_PANEL)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_sans_preferences_on_part_sur_les_defauts():
    p = _node("P.normalise(null)")
    assert p["view"] == "workshop"
    assert p["density"] == "standard"
    assert p["blocks"]["thought"] is True


def test_la_pensee_est_visible_PAR_DEFAUT():
    """C'est la raison d'etre du chantier : elle ne doit pas etre a activer."""
    assert _node("P.normalise(null).blocks.thought") is True


# ══════════════════════════════════════════════════════════════════════════
#  2. Normalisation — on n'a jamais confiance dans ce qu'on relit
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("brut", [
    "null", "undefined", "42", "'chaine'", "[]", "{}", "{view:'nexistepas'}",
    "{density:'geante'}", "{blocks:'pas un objet'}", "{blocks:{thought:'oui'}}",
])
def test_une_preference_absurde_retombe_sur_les_defauts(brut):
    p = _node(f"P.normalise({brut})")
    assert p["view"] == "workshop" and p["density"] == "standard"
    assert isinstance(p["blocks"], dict) and p["blocks"]["thought"] is True


def test_une_preference_VALIDE_est_respectee():
    p = _node("P.normalise({density:'compact',blocks:{thought:false,queue:false}})")
    assert p["density"] == "compact"
    assert p["blocks"]["thought"] is False and p["blocks"]["queue"] is False
    assert p["blocks"]["perimeter"] is True, "les blocs non cites gardent leur defaut"


def test_une_cle_inconnue_est_ignoree():
    p = _node("P.normalise({blocks:{inventee:true}})")
    assert "inventee" not in p["blocks"]


# ══════════════════════════════════════════════════════════════════════════
#  3. Stockage — le chemin qu'on n'essaie jamais a la main
# ══════════════════════════════════════════════════════════════════════════


def test_un_stockage_qui_LEVE_ne_casse_pas_le_panneau():
    """Navigation privee, stockage bloque : ca arrive vraiment."""
    p = _node("P.lirePrefs(S)", prelude=(
        "const S={getItem(){throw new Error('refuse')},"
        "setItem(){throw new Error('refuse')}};"))
    assert p["view"] == "workshop"


def test_une_ecriture_impossible_rend_false_sans_lever():
    ok = _node("P.ecrirePrefs({density:'compact'}, S)", prelude=(
        "const S={getItem(){return null},setItem(){throw new Error('quota')}};"))
    assert ok is False


def test_un_json_corrompu_retombe_sur_les_defauts():
    p = _node("P.lirePrefs(S)", prelude="const S={getItem:()=>'{{{pas du json'};")
    assert p["view"] == "workshop"


def test_l_aller_retour_conserve_les_preferences():
    out = _node("[P.ecrirePrefs({density:'compact',blocks:{queue:false}}, S), "
                "P.lirePrefs(S).density, P.lirePrefs(S).blocks.queue]",
                prelude="let v=null;const S={getItem:()=>v,setItem:(k,x)=>{v=x}};")
    assert out == [True, "compact", False]


def test_seules_les_preferences_NORMALISEES_sont_ecrites():
    """On n'ecrit jamais une cle inventee dans le stockage de l'utilisateur."""
    out = _node("(P.ecrirePrefs({view:'nexistepas',blocks:{inventee:1}}, S), JSON.parse(v))",
                prelude="let v=null;const S={getItem:()=>v,setItem:(k,x)=>{v=x}};")
    assert out["view"] == "workshop"
    assert "inventee" not in out["blocks"]


# ══════════════════════════════════════════════════════════════════════════
#  4. Tampon d'evenements
# ══════════════════════════════════════════════════════════════════════════


def test_le_tampon_est_BORNE():
    """Une mission longue produit des milliers de traces."""
    n = _node("(P.viderEvenements(), "
              "Array.from({length:5000}).forEach((_,i)=>P.pousserEvenement({task_id:'w'+i})), "
              "P.evenements().length)")
    assert n <= 400, f"tampon non borne : {n}"


def test_un_evenement_sans_task_id_est_refuse():
    n = _node("(P.viderEvenements(), P.pousserEvenement({stage:'x'}), P.evenements().length)")
    assert n == 0


def test_le_tampon_garde_les_evenements_les_PLUS_RECENTS():
    dernier = _node("(P.viderEvenements(), "
                    "Array.from({length:900}).forEach((_,i)=>P.pousserEvenement({task_id:'w'+i})), "
                    "P.evenements()[P.evenements().length-1].task_id)")
    assert dernier == "w899"


# ══════════════════════════════════════════════════════════════════════════
#  5. Le customizer
# ══════════════════════════════════════════════════════════════════════════


def test_chaque_bloc_a_sa_case_a_cocher():
    html = _node("P.rendreCustomizer(P.normalise(null))")
    for cle, _lib in _node("P.BLOCS"):
        assert f'data-mp-block="{cle}"' in html, cle


def test_le_bouton_de_remise_a_zero_existe():
    assert 'data-mp-reset' in _node("P.rendreCustomizer(P.normalise(null))")


def test_la_vue_active_est_marquee():
    html = _node("P.rendreCustomizer(P.normalise(null))")
    assert 'data-mp-view="workshop"' in html and "is-on" in html


def test_les_cases_refletent_les_preferences():
    html = _node("P.rendreCustomizer(P.normalise({blocks:{thought:false}}))")
    i = html.index('data-mp-block="thought"')
    assert "checked" not in html[i:i + 40]


# ══════════════════════════════════════════════════════════════════════════
#  6. La feuille de style se lie toute seule
# ══════════════════════════════════════════════════════════════════════════


def test_la_feuille_est_liee_par_le_chassis_pas_par_index_html():
    """`index.html` fait partie d'un chantier concurrent : on n'y touche pas."""
    src = _PANEL.read_text(encoding="utf-8")
    assert "mission-panel.css" in src
    assert "?v=" in src, "la version doit etre pilotee cote JS, pour le cache"


def test_index_html_n_est_PAS_modifie():
    idx = (_ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for nouveau in ("mission_panel.js", "mission_views.js", "mission_model.js",
                    "mission-panel.css"):
        assert nouveau not in idx, (
            f"{nouveau} a ete ajoute a index.html — fichier du chantier concurrent"
        )


def test_seul_le_chassis_touche_au_dom():
    """Le modele et les vues doivent rester purs, donc testables hors navigateur."""
    js = _ROOT / "web" / "static" / "js"
    for pur in ("mission_model.js", "mission_views.js"):
        src = (js / pur).read_text(encoding="utf-8")
        code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        assert "document" not in code, f"{pur} touche au DOM"
