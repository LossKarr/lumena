"""Panel Missions — lot C : LA CHRONOLOGIE QUI EXISTAIT DEJA.

`checkpoint_history` est persiste sur **669 taches sur 670** (mediane 9 entrees,
p90 26, max 40) et `TaskRecord.to_dict()` etant un `asdict()`, il arrivait DEJA
dans le navigateur a chaque rafraichissement. Le panneau ne lisait que
`last_checkpoint` — l'instantane — et jetait la suite.

Cinquieme occurrence du motif de ce chantier : le fait existe, il est calcule,
il est meme transmis, puis jete avant l'affichage.

═══════════════════════════════════════════════════════════════════════════════
  LA FORME A ETE CHOISIE AVANT LA COULEUR
═══════════════════════════════════════════════════════════════════════════════

Le travail de cette donnee est une TENDANCE DANS LE TEMPS, une seule serie. La
colonne Progression est deja une tuile de statistique (libelle + valeur), et le
contrat d'une tuile est `libelle · valeur · delta · tendance` — la tendance
etant une sparkline de douze points. On COMPLETE la tuile ; on n'ajoute pas un
graphique de plus.

Douze tombe juste : la mediane des historiques reels est 9.

`total_actions` est monotone a **99,9 %** sur 6 833 points mesures. Une courbe
de croissance a donc un sens ; ce n'est pas du bruit qu'on lisserait.

═══════════════════════════════════════════════════════════════════════════════
  LA COULEUR A ETE CALCULEE, PAS ESTIMEE
═══════════════════════════════════════════════════════════════════════════════

Contrastes mesures sur la surface de carte `--card` (#131a26) :

    --muted-strong  #4e4e5a   2,13:1   sous le seuil de 3:1
    --muted         #636370   2,95:1   sous le seuil
    --et                      4,6 a 11:1 selon l'etat

D'ou UNE seule teinte en DEUX intensites : `--et` attenue pour le trace et
l'aire, plein pour le dernier point. Aucune teinte nouvelle, la courbe
appartient visuellement a sa mission, et le contraste tient dans les quatre
etats.

Une seule serie : donc PAS de legende — le libelle de la tuile la nomme.
Et pas d'infobulle : les valeurs sont deja lisibles a cote (le compteur
d'actions, le journal). Une infobulle qui serait le SEUL acces a une valeur
serait une faute ; ici elle serait un doublon.
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

from src.utils.paths import DATA_DIR  # noqa: E402

_ETAT = DATA_DIR / "task_orchestrator_state.json"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node indisponible")


def _node(corps: str):
    script = (
        "require(%s);require(%s);" % tuple(
            json.dumps(str(_JS / n)) for n in ("mission_model.js", "mission_views.js"))
        + "const T=globalThis.missionTimeline, B=globalThis.buildMissionModel,"
        + " V=globalThis.missionRenderView;"
        + "process.stdout.write(JSON.stringify((function(){%s})()));" % corps
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _hist(n, fin=False, base=0):
    """Forme EXACTE relevee au corpus, pas une forme inventee."""
    h = [{"ts": "2026-09-01T10:00:00", "payload": {"phase": "start", "status": "running"}}]
    for i in range(1, n + 1):
        h.append({"ts": "2026-09-01T10:%02d:00" % i,
                  "payload": {"phase": "iteration", "iteration": i,
                              "ledger": {"total_actions": base + i,
                                         "successful_mutations": i // 2,
                                         "success_rate": 0.9, "recent": []}}})
    if fin:
        h.append({"ts": "2026-09-01T11:00:00",
                  "payload": {"phase": "done",
                              "ledger": {"total_actions": base + n + 1,
                                         "success_rate": 0.95, "recent": []}}})
    return h


def _tl(h):
    return _node("return T(%s);" % json.dumps({"checkpoint_history": h}))


# ══════════════════════════════════════════════════════════════════════════
#  1. LA CHRONOLOGIE
# ══════════════════════════════════════════════════════════════════════════


def test_la_chronologie_persistee_est_enfin_LUE():
    """Elle arrivait deja dans le navigateur ; personne ne la regardait."""
    tl = _tl(_hist(5))
    assert tl is not None and len(tl["points"]) == 6


def test_on_garde_DOUZE_points_pas_quarante():
    """Le contrat de la sparkline d'une tuile. La mediane des historiques
    reels est 9 — douze couvre le cas courant sans ecraser la carte."""
    tl = _tl(_hist(39))
    assert len(tl["points"]) == 12


def test_le_TOTAL_reste_visible_meme_si_on_n_en_dessine_que_douze():
    """Afficher « 12 » sur une mission de quarante points laisserait croire
    qu'il ne s'est pas passe grand-chose."""
    tl = _tl(_hist(39))
    assert tl["total"] == 40 and len(tl["points"]) == 12


def test_UN_seul_point_n_est_pas_une_tendance():
    """Une sparkline a un point est un mensonge graphique."""
    assert _tl([{"payload": {"phase": "start"}}]) is None
    assert _tl([]) is None


def test_une_tache_SANS_historique_ne_dessine_rien():
    assert _node("return T({});") is None
    assert _node("return T(null);") is None


def test_les_bornes_servent_a_l_echelle():
    tl = _tl(_hist(6, base=10))
    assert tl["min"] == 0 and tl["max"] == 16


def test_la_phase_FINALE_est_retenue():
    """C'est elle qui distingue « terminee » de « a l'arret »."""
    assert _tl(_hist(4, fin=True))["phaseFinale"] == "done"
    assert _tl(_hist(4))["phaseFinale"] == "iteration"


def test_un_historique_INCOMPLET_ne_casse_rien():
    """Un backend plus ancien n'a ni `ledger` ni `iteration`."""
    tl = _tl([{"ts": "x", "payload": {"phase": "start"}},
              {"ts": "y", "payload": {}}])
    assert tl is not None and tl["min"] == 0 and tl["max"] == 0


def test_le_taux_de_reussite_ABSENT_ne_devient_pas_zero():
    tl = _tl([{"payload": {"ledger": {"total_actions": 1}}},
              {"payload": {"ledger": {"total_actions": 2}}}])
    assert tl["points"][0]["successPct"] is None


def test_buildModel_attache_la_chronologie():
    arbre = [{"mission": {"task_id": "m", "state": "running",
                          "metadata": {"objective": "o"},
                          "checkpoint_history": _hist(4)},
              "children": []}]
    m = _node("return B(%s, [], 0)[0];" % json.dumps(arbre))
    assert m["timeline"] is not None and len(m["timeline"]["points"]) == 5


# ══════════════════════════════════════════════════════════════════════════
#  2. LA SPARKLINE
# ══════════════════════════════════════════════════════════════════════════


def _m(**kw):
    b = {"id": "m1", "objective": "o", "aggregate": "running", "closed": False,
         "terminal": False, "children": [], "deadlineLabel": "", "remainingMs": None,
         "workers": {"done": 0, "total": 0}, "budget": None, "proofs": [],
         "delivered": {"published": [], "artifacts": []}, "trail": [],
         "ledger": {"actions": 9, "mutations": 4, "successPct": 90, "phase": "iteration",
                    "iteration": 9, "recent": []},
         "timeline": None}
    b.update(kw)
    return b


def _ctrl(mission):
    return _node("return V('control', %s, null);" % json.dumps([mission]))


def test_la_sparkline_apparait_quand_il_y_a_une_tendance():
    h = _ctrl(_m(timeline=_tl(_hist(6))))
    assert 'class="mp-spark"' in h and "<path" in h


def test_AUCUNE_sparkline_sans_chronologie():
    assert "mp-spark" not in _ctrl(_m(timeline=None))


def test_elle_dessine_UN_point_par_releve():
    h = _ctrl(_m(timeline=_tl(_hist(5))))
    trace = re.search(r'class="mp-spark-l" d="([^"]+)"', h).group(1)
    assert trace.count("L") == 5 and trace.count("M") == 1   # 6 points


def test_elle_n_a_NI_axe_NI_grille():
    """C'est une sparkline, pas un graphique."""
    h = _ctrl(_m(timeline=_tl(_hist(6))))
    i = h.index("mp-spark")
    bloc = h[i:h.index("</span>", i)]
    assert "<line" not in bloc and "<text" not in bloc


def test_le_DERNIER_point_est_marque():
    """Le contrat de la tuile : la tendance en teinte sourde, la periode
    courante en accent."""
    h = _ctrl(_m(timeline=_tl(_hist(6))))
    assert 'class="mp-spark-p"' in h and "<circle" in h


def test_le_point_final_est_bien_au_BOUT_du_trace():
    h = _ctrl(_m(timeline=_tl(_hist(6))))
    trace = re.search(r'class="mp-spark-l" d="([^"]+)"', h).group(1)
    dernier = trace.split("L")[-1].strip().split()
    cx = re.search(r'mp-spark-p" cx="([\d.]+)" cy="([\d.]+)"', h).groups()
    assert float(cx[0]) == float(dernier[0])
    assert float(cx[1]) == float(dernier[1])


def test_le_trait_ne_s_EPAISSIT_pas_quand_le_svg_s_etire():
    """Le SVG prend toute la largeur de la colonne ; sans cela le trait
    grossirait avec elle."""
    h = _ctrl(_m(timeline=_tl(_hist(6))))
    assert 'vector-effect="non-scaling-stroke"' in h


def test_une_courbe_PLATE_ne_divise_pas_par_zero():
    """Toutes les valeurs egales : l'etendue vaut zero."""
    plat = [{"payload": {"ledger": {"total_actions": 7}}} for _ in range(5)]
    h = _ctrl(_m(timeline=_tl(plat)))
    assert "NaN" not in h and "Infinity" not in h
    assert "mp-spark" in h


# ══════════════════════════════════════════════════════════════════════════
#  3. CE QU'ELLE DIT SANS COULEUR
# ══════════════════════════════════════════════════════════════════════════


def test_elle_se_DIT_aux_lecteurs_d_ecran():
    """Un trace SVG ne se lit pas : `aria-label` porte le fait."""
    h = _ctrl(_m(timeline=_tl(_hist(6))))
    assert "points de reprise" in h and "actions" in h


def test_le_mot_final_distingue_TERMINEE_de_a_l_arret():
    fini = _ctrl(_m(timeline=_tl(_hist(4, fin=True))))
    plat = _ctrl(_m(timeline=_tl([{"payload": {"ledger": {"total_actions": 3}}}] * 4)))
    assert "terminée" in fini
    assert "à l’arrêt" in plat


def test_elle_n_a_PAS_de_legende():
    """Une seule serie : le libelle de la tuile la nomme."""
    h = _ctrl(_m(timeline=_tl(_hist(6))))
    i = h.index("mp-spark")
    assert "legend" not in h[i:i + 700].lower()


def test_le_svg_interne_est_CACHE_aux_lecteurs_d_ecran():
    """Le `role=img` porte deja le sens ; annoncer les balises en double
    serait du bruit."""
    h = _ctrl(_m(timeline=_tl(_hist(6))))
    i = h.index("mp-spark")
    assert 'role="img"' in h[i:i + 200]
    assert 'aria-hidden="true"' in h[i:i + 400]


# ══════════════════════════════════════════════════════════════════════════
#  4. LA COULEUR — calculee, et sans teinte nouvelle
# ══════════════════════════════════════════════════════════════════════════


def _css_nu() -> str:
    return re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)


@pytest.mark.parametrize("sel", [".mp-spark-l", ".mp-spark-a", ".mp-spark-p"])
def test_la_sparkline_reprend_le_canal_d_etat(sel):
    """Aucune teinte nouvelle. Et surtout PAS `--muted-strong`, mesure a
    2,13:1 sur la carte — sous le seuil de 3:1."""
    css = _css_nu()
    i = css.index(sel)
    corps = css[i:css.index("}", i)]
    assert "var(--et" in corps
    assert "--muted-strong" not in corps


def test_UNE_teinte_en_DEUX_intensites():
    """Le trace est attenue, le point final est plein : c'est ce qui fait
    l'emphase, pas une seconde couleur."""
    css = _css_nu()
    i = css.index(".mp-spark-l")
    ligne = css[i:css.index("}", i)]
    j = css.index(".mp-spark-p")
    point = css[j:css.index("}", j)]
    m = re.search(r"opacity:\s*([0-9.]+)", ligne)
    assert m and float(m.group(1)) < 1
    assert "opacity" not in point


def test_le_point_final_est_ISOLE_du_trace():
    """La regle des marques qui se chevauchent : un anneau de surface, pas
    une bordure de couleur."""
    css = _css_nu()
    i = css.index(".mp-spark-p")
    corps = css[i:css.index("}", i)]
    assert "stroke: var(--bg-accent)" in corps and "stroke-width: 2" in corps


def test_le_trait_reste_FIN():
    css = _css_nu()
    i = css.index(".mp-spark-l")
    assert "stroke-width: 1.5" in css[i:css.index("}", i)]


# Le plafond global de `--accent` appartient a `test_l_accent_reste_RARE`
# (fichier `finition`), re-ancre a 15 quand l'orange a pris le sens « en
# cours ». Le dupliquer ici avec un autre chiffre creerait deux verites. Ce
# que ce fichier doit garantir tient dans le test ci-dessus : la sparkline
# n'en consomme AUCUN.


def test_aucune_couleur_en_dur():
    dures = re.findall(
        r"(?<![\w-])(#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\))", _css_nu())
    assert not dures, f"couleurs en dur : {sorted(set(dures))}"


def test_la_valeur_d_une_TUILE_prend_des_chiffres_proportionnels():
    """La chasse fixe fait paraitre « 121 » lache en grande taille. Elle ne se
    justifie que la ou des chiffres s'alignent verticalement — ou changent en
    place, comme le compte a rebours, qui la garde."""
    css = _css_nu()
    i = css.index(".mp-kpi b")
    assert "proportional-nums" in css[i:css.index("}", i)]
    j = css.index(".mp-countdown b")
    assert "tabular-nums" in css[j:css.index("}", j)], (
        "le compte a rebours change en place : sans chasse fixe il sauterait"
    )


# ══════════════════════════════════════════════════════════════════════════
#  5. LE CORPUS REEL — la mesure qui a declenche le lot
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.skipif(not _ETAT.exists(), reason="corpus absent de cette machine")
def test_la_chronologie_existe_VRAIMENT_dans_les_donnees():
    """Si le runtime cessait de persister `checkpoint_history`, la colonne
    devrait redevenir une absence nommee."""
    taches = [t for t in json.loads(_ETAT.read_text(encoding="utf-8"))["tasks"]
              if isinstance(t, dict)]
    avec = [t for t in taches if len(t.get("checkpoint_history") or []) >= 2]
    assert len(avec) > len(taches) * 0.5, (
        f"seulement {len(avec)} taches sur {len(taches)} portent une chronologie"
    )


@pytest.mark.skipif(not _ETAT.exists(), reason="corpus absent de cette machine")
def test_le_modele_digere_les_historiques_REELS(tmp_path):
    """Les historiques passent par un FICHIER : quarante d'entre eux en
    argument de ligne de commande depassent la limite de longueur de Windows
    (WinError 206). Meme piege qu'au lot 9."""
    taches = [t for t in json.loads(_ETAT.read_text(encoding="utf-8"))["tasks"]
              if isinstance(t, dict) and len(t.get("checkpoint_history") or []) >= 2][-40:]
    f = tmp_path / "hists.json"
    f.write_text(json.dumps([t["checkpoint_history"] for t in taches]), encoding="utf-8")
    out = _node("const hs=JSON.parse(require('fs').readFileSync(%s,'utf8'));"
                "return hs.map(function (h) {"
                "  var tl = T({checkpoint_history: h});"
                "  return tl ? tl.points.length : 0; });" % json.dumps(str(f)))
    assert len(out) == len(taches)
    assert all(2 <= n <= 12 for n in out), out
