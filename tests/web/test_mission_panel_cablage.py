"""Panel Missions — lot 7 : le CABLAGE dans `panels.js`.

C'est l'etape qui rend le panneau visible. Elle touche un fichier de 7 400
lignes partage par tout le Control Panel, donc chaque point de greffe est
verrouille ici.

--- Les trois invariants du cablage ---

1. **Repli DELIBERE.** Si un des trois modules ne charge pas, l'utilisateur doit
   retrouver le panneau qu'il avait avant, pas un ecran vide. L'ancien rendu
   reste donc entier dans le fichier, jamais supprime.

2. **`index.html` n'est pas touche.** Il fait partie d'un chantier en cours.
   Les modules sont charges par des imports d'effet de bord depuis `panels.js`,
   et la feuille de style se lie elle-meme.

3. **Ecouteurs DELEGUES.** `rendre()` remplace tout l'innerHTML a chaque tour :
   des ecouteurs poses sur les boutons mourraient au premier re-rendu.

--- Pourquoi l'etranglement ---

Une mission a cinq workers produit des rafales d'evenements SSE. Re-peindre a
chacun ferait clignoter la pensee qu'on cherche justement a lire.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_PANELS = _ROOT / "web" / "static" / "js" / "panels.js"
_INDEX = _ROOT / "web" / "index.html"


def _code() -> str:
    """Source de panels.js sans les commentaires — le piege de la sous-chaine
    m'a deja pris six fois sur ce depot."""
    src = _PANELS.read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


# ══════════════════════════════════════════════════════════════════════════
#  1. Les modules sont charges — sans toucher index.html
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("module", ["mission_model.js", "mission_views.js", "mission_panel.js"])
def test_le_module_est_importe_par_panels(module):
    assert f"./{module}" in _code(), f"{module} n'est jamais charge"


def test_l_ordre_de_chargement_est_respecte():
    """Le chassis appelle le modele et les vues : ils doivent exister avant."""
    c = _code()
    assert c.index("mission_model.js") < c.index("mission_panel.js")
    assert c.index("mission_views.js") < c.index("mission_panel.js")


def test_index_html_reste_INTACT():
    idx = _INDEX.read_text(encoding="utf-8")
    for nouveau in ("mission_panel.js", "mission_views.js", "mission_model.js",
                    "mission-panel.css"):
        assert nouveau not in idx, f"{nouveau} ajoute a index.html (chantier concurrent)"


@pytest.mark.skipif(shutil.which("node") is None, reason="node indisponible")
def test_panels_reste_un_module_ES_valide():
    res = subprocess.run(["node", "--input-type=module", "--check"],
                         stdin=_PANELS.open("rb"), capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


# ══════════════════════════════════════════════════════════════════════════
#  2. Le panneau est rendu — et le repli existe
# ══════════════════════════════════════════════════════════════════════════


def test_le_chassis_est_appele_pour_rendre():
    c = _code()
    assert "missionPanel" in c and ".rendre(" in c


def test_la_feuille_est_liee_au_rendu():
    assert "lierFeuille()" in _code()


def test_le_repli_sur_l_ancien_rendu_EXISTE_toujours():
    """Sans lui, un module non charge laisserait l'utilisateur devant du vide."""
    c = _code()
    assert "_renderMissionNode" in c, "l'ancien rendu a ete supprime"
    assert "mission-empty" in c, "le message vide historique a disparu"


def test_le_rendu_v2_est_sous_try_except():
    """Une erreur du panneau v2 ne doit pas casser tout le Control Panel."""
    c = _code()
    i = c.index("lierFeuille()")
    fenetre = c[max(0, i - 300):i + 400]
    assert "try {" in fenetre and "catch" in fenetre


def test_le_badge_est_calcule_AVANT_le_rendu_v2():
    """Le compteur de l'onglet ne doit pas dependre du panneau."""
    c = _code()
    assert c.index("badge-missions") < c.index("lierFeuille()")


# ══════════════════════════════════════════════════════════════════════════
#  3. Le flux SSE alimente le modele
# ══════════════════════════════════════════════════════════════════════════


def test_les_evenements_sse_sont_pousses_dans_le_tampon():
    assert "pousserEvenement(ev)" in _code(), (
        "sans ca le panneau n'a ni pensee ni file d'attente : le modele reste vide"
    )


def test_le_re_rendu_est_ETRANGLE():
    """Cinq workers produisent des rafales ; re-peindre a chaque evenement
    ferait clignoter la pensee."""
    c = _code()
    assert "_mpRedrawT" in c and "setTimeout" in c
    m = re.search(r"_mpRedrawT = setTimeout\([\s\S]{0,320}?\}, (\d+)\)", c)
    assert m, "l'etranglement du re-rendu est introuvable"
    assert 200 <= int(m.group(1)) <= 2000, f"delai d'etranglement suspect : {m.group(1)} ms"


def test_l_alimentation_sse_ne_peut_pas_casser_le_flux():
    c = _code()
    i = c.index("pousserEvenement(ev)")
    assert "try {" in c[max(0, i - 120):i], "pousserEvenement n'est pas protege"


def test_le_dispatcher_historique_est_PRESERVE():
    """`_missionLog` alimente encore le journal brut, qui reste un bloc optionnel."""
    assert "_missionLog[ev.task_id]" in _code()


# ══════════════════════════════════════════════════════════════════════════
#  4. Les preferences sont pilotables depuis l'ecran
# ══════════════════════════════════════════════════════════════════════════


def test_les_ecouteurs_sont_DELEGUES_et_poses_une_seule_fois():
    c = _code()
    assert "_brancherPanelMissions" in c
    assert "_mpBranche" in c, "rien n'empeche d'empiler les ecouteurs a chaque rendu"
    assert "addEventListener" in c


@pytest.mark.parametrize("action", ["data-mp-view", "data-mp-density", "data-mp-reset",
                                    "data-mp-block"])
def test_chaque_commande_du_customizer_est_branchee(action):
    assert action in _code(), f"{action} n'est relie a rien"


def test_chaque_action_ECRIT_la_preference_avant_de_re_rendre():
    """La preference est la source de verite, jamais le DOM."""
    c = _code()
    i = c.index("_brancherPanelMissions")
    bloc = c[i:i + 2200]
    assert "ecrirePrefs" in bloc and "_renderMissionsFromCache()" in bloc
    assert bloc.index("ecrirePrefs") < bloc.rindex("_renderMissionsFromCache()")


def test_le_menu_reste_ouvert_apres_avoir_coche_une_case():
    """Sinon il faut rouvrir le menu entre chaque case — insupportable."""
    c = _code()
    i = c.index("data-mp-block")
    assert ".open = true" in c[i:i + 700]


def test_la_remise_a_zero_repart_des_valeurs_d_usine():
    c = _code()
    i = c.index("data-mp-reset")
    assert "ecrirePrefs({})" in c[i:i + 400]
