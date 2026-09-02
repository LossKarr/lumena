"""Panel Missions — lot 7 : finition, isolement et accessibilite.

Les gardes de ce fichier ne verifient pas une fonctionnalite : ils verifient
que la STRUCTURE du chantier ne peut pas deriver apres coup. C'est la seule
chose qui distingue un decoupage tenu d'un decoupage annonce.

--- L'isolement ---

`panels.js` fait 7 400 lignes. Tout l'interet d'avoir sorti le modele et les
vues est qu'ils n'y retournent jamais. Si un jour l'un d'eux importe
`panels.js`, ou touche au DOM, le decoupage est mort — et personne ne le verra
sans ce test.

--- L'accessibilite ---

Le selecteur de vues est un groupe de boutons a etat. Une classe CSS `is-on`
dit au voyant quelle vue est active ; elle ne dit rien a un lecteur d'ecran.
C'est `aria-pressed` qui le dit.

--- Le theme ---

Un `var(--typo)` inexistant ne leve aucune erreur : il rend l'element
transparent, en silence. On verifie donc que chaque token utilise est defini
dans le bloc `:root` de base, pas seulement quelque part dans le fichier.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_JS = _ROOT / "web" / "static" / "js"
_CSS = _ROOT / "web" / "static" / "css" / "mission-panel.css"
_TOKENS = _ROOT / "web" / "static" / "css" / "tokens.css"

_PURS = ["mission_model.js", "mission_views.js"]
_TOUS = _PURS + ["mission_panel.js"]


def _code(nom: str) -> str:
    """Source sans commentaires — le piege de la sous-chaine m'a pris six fois."""
    src = (_JS / nom).read_text(encoding="utf-8")
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


# ══════════════════════════════════════════════════════════════════════════
#  1. ISOLEMENT — le coeur ne retourne jamais dans panels.js
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", _TOUS)
def test_aucun_module_du_panel_ne_depend_de_panels_js(nom):
    assert "panels" not in _code(nom), (
        f"{nom} reference panels.js : le decoupage est perdu, le coeur "
        f"redevient inextricable"
    )


@pytest.mark.parametrize("nom", _TOUS)
def test_aucun_module_du_panel_n_importe_quoi_que_ce_soit(nom):
    c = _code(nom)
    assert "require(" not in c and "\nimport " not in c and c[:7] != "import "


@pytest.mark.parametrize("nom", _PURS)
def test_le_modele_et_les_vues_ne_touchent_JAMAIS_au_dom(nom):
    """C'est ce qui les rend executables par node depuis pytest."""
    c = _code(nom)
    for interdit in ("document", "window.", "localStorage", "innerHTML",
                     "addEventListener", "querySelector"):
        assert interdit not in c, f"{nom} touche au DOM : {interdit}"


def test_SEUL_le_chassis_touche_au_dom():
    c = _code("mission_panel.js")
    assert "document" in c, "le chassis doit bien monter quelque chose"


@pytest.mark.parametrize("nom", _TOUS)
def test_chaque_module_expose_aussi_un_export_node(nom):
    """Sans ca, plus aucun de ces fichiers n'est testable hors navigateur."""
    assert "module.exports" in _code(nom), nom


# ══════════════════════════════════════════════════════════════════════════
#  2. ACCESSIBILITE
# ══════════════════════════════════════════════════════════════════════════


def _customizer(prefs_js: str = "null") -> str:
    import json
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.skip("node indisponible")
    script = (
        f"const P=require({json.dumps(str(_JS / 'mission_panel.js'))});"
        f"process.stdout.write(P.rendreCustomizer(P.normalise({prefs_js})));"
    )
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_la_vue_active_est_annoncee_aux_lecteurs_d_ecran():
    """Une classe CSS ne dit rien a un lecteur d'ecran ; `aria-pressed` si."""
    h = _customizer()
    assert h.count('aria-pressed="true"') == 1, (
        "exactement une vue doit etre marquee active"
    )
    assert 'aria-pressed="false"' in h


def test_changer_de_vue_deplace_le_marqueur_actif():
    h = _customizer("{view:'control'}")
    i = h.index('data-mp-view="control"')
    assert 'aria-pressed="true"' in h[max(0, i - 120):i + 60]


def test_le_groupe_de_vues_est_nomme():
    h = _customizer()
    assert 'role="group"' in h and 'aria-label=' in h


def test_tous_les_boutons_sont_de_type_button():
    """Sans `type`, un bouton dans un formulaire le soumettrait."""
    h = _customizer()
    assert h.count("<button") == h.count('<button type="button"')


def test_la_densite_est_un_etat_annonce_aussi():
    assert 'aria-pressed="true"' in _customizer("{density:'compact'}")


@pytest.mark.parametrize("selecteur", [
    ".mp-tab:focus-visible", ".mp-reset:focus-visible",
    ".mp-toggle input:focus-visible", ".mp-custo > summary:focus-visible",
])
def test_chaque_element_interactif_a_un_focus_VISIBLE(selecteur):
    """Naviguer au clavier sans voir ou on est, c'est ne pas pouvoir naviguer."""
    assert selecteur in _CSS.read_text(encoding="utf-8"), selecteur


def test_la_remise_a_zero_vit_DANS_le_panneau_et_pas_en_absolu():
    """La version precedente la calait avec un translateY en dur, qui se
    decalait des qu'on ajoutait un bloc a la liste."""
    h = _customizer()
    i_t, i_r = h.index("mp-toggles"), h.index("mp-reset")
    assert i_t < i_r < h.index("</details>")
    css = _CSS.read_text(encoding="utf-8")
    bloc = css[css.index(".mp-reset {"):css.index(".mp-reset {") + 320]
    assert "position: absolute" not in bloc and "translateY" not in bloc


# ══════════════════════════════════════════════════════════════════════════
#  3. THEME — un token inexistant ne leve rien, il efface l'element
# ══════════════════════════════════════════════════════════════════════════


def _tokens_du_bloc_racine() -> set:
    tk = _TOKENS.read_text(encoding="utf-8")
    m = re.search(r":root\s*\{(.*?)\n\}", tk, re.S)
    assert m, "bloc :root introuvable dans tokens.css"
    return set(re.findall(r"(--[\w-]+)\s*:", m.group(1)))


def test_chaque_token_utilise_est_defini_dans_le_bloc_de_BASE():
    """S'il n'est defini que dans le bloc clair, l'element disparait en sombre.

    Les proprietes que la feuille definit ELLE-MEME ne comptent pas : `--et`,
    le canal d'etat, est pose sur la carte puis herite par le lisere, la
    pastille et la pensee. C'est une variable locale, pas un token absent."""
    css = _CSS.read_text(encoding="utf-8")
    utilises = set(re.findall(r"var\((--[\w-]+)", css))
    locaux = set(re.findall(r"(--[\w-]+)\s*:", css))
    manquants = sorted(utilises - _tokens_du_bloc_racine() - locaux)
    assert not manquants, (
        f"tokens absents du :root de base — invisibles dans le theme par "
        f"defaut : {manquants}"
    )


def test_la_feuille_ne_contient_TOUJOURS_aucune_couleur_en_dur():
    """Le garde du lot 2, reverifie apres l'ajout des trois vues et du chassis."""
    css = re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)
    dures = re.findall(
        r"(?<![\w-])(#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\))", css)
    assert not dures, f"couleurs en dur : {sorted(set(dures))}"


def test_le_mouvement_reduit_est_respecte():
    assert "prefers-reduced-motion" in _CSS.read_text(encoding="utf-8")


def test_les_TROIS_vues_ont_toutes_leur_habillage():
    """La Constellation a ete retiree du panneau (lot 17) : son entree est
    partie d'ici avec elle."""
    css = _CSS.read_text(encoding="utf-8")
    for marqueur, vue in [(".mp-post", "Atelier"), (".mp-lane", "Ruban"),
                          (".mp-ctrl", "Contrôle")]:
        assert marqueur in css, f"la vue {vue} n'a pas de style"


def test_les_ecrans_etroits_sont_prevus_pour_toutes_les_vues():
    css = _CSS.read_text(encoding="utf-8")
    etroit = css[css.index("@media (max-width"):]
    for m in (".mp-grid", ".mp-ctrl"):
        assert m in etroit, f"{m} ne se replie pas sur ecran etroit"


# ══════════════════════════════════════════════════════════════════════════
#  4. LE CONTRAT DE DESIGN
# ══════════════════════════════════════════════════════════════════════════
#
#  Ces gardes n'existent pas pour empecher un bug : ils existent pour empecher
#  la feuille de redevenir fade. La premiere version l'etait pour une raison
#  precise et mesurable : `--accent` y etait peint dans SEIZE regles. Un accent
#  partout ne signale plus rien, et l'ecran devient uni.


def _sans_commentaires() -> str:
    return re.sub(r"/\*.*?\*/", "", _CSS.read_text(encoding="utf-8"), flags=re.S)


def test_l_accent_identifie_uniquement_le_statut_EN_COURS():
    """L'orange sert aussi d'etat, mais uniquement pour le travail actif."""
    css = _sans_commentaires()
    for regle in ('.mp-post[data-state="running"]', '[data-agg="running"]',
                  '.mp-seg-on'):
        i = css.index(regle)
        corps = css[i:css.index("}", i)]
        assert "--accent" in corps, f"{regle} doit identifier le travail actif"

    for regle in ('.mp-post[data-state="waiting"]',
                  '.mp-post[data-state="done"]',
                  '.mp-post[data-state="failed"]', ".mp-post-state"):
        i = css.index(regle)
        corps = css[i:css.index("}", i)]
        assert "--accent" not in corps, f"{regle} ne doit pas paraitre en cours"


def test_l_accent_reste_RARE():
    """L'orange actif reste borne malgre son nouveau role semantique."""
    n = _sans_commentaires().count("var(--accent")
    assert n <= 15, f"{n} usages de l'accent — il redevient du bruit"


def test_les_quatre_etats_ont_QUATRE_couleurs_distinctes():
    css = _sans_commentaires()
    # Tranche du DEBUT au premier vrai bloc : `i - 200` partait en negatif,
    # et Python decoupe alors depuis la fin — la tranche etait vide.
    bloc_etats = css[:css.index(".mp-mission")]
    for tok in ("--ok", "--warn", "--muted", "--danger"):
        assert f"--et: var({tok})" in bloc_etats, f"aucun etat n'utilise {tok}"


def test_ce_qui_travaille_BOUGE():
    """Un panneau temps reel immobile ne se lit pas comme du temps reel."""
    css = _sans_commentaires()
    assert "@keyframes mp-pulse" in css
    # `.mp-dot-on` apparait DEUX fois : d'abord dans le canal d'etat, puis
    # dans la regle d'animation. C'est la seconde qu'on verifie.
    i = css.rindex(".mp-dot-on")
    assert "animation:" in css[i:i + 120], "l'etat actif ne pulse pas"


def test_seule_l_annulation_recule_et_le_valide_reste_lisible():
    """Un succes est une information positive; seule l'annulation s'efface."""
    css = _sans_commentaires()
    i = css.index('opacity: 0.62')
    m = re.search(r"opacity:\s*([0-9.]+)", css[i:i + 40])
    assert 'data-state="cancelled"' in css[max(0, i - 160):i], (
        "une tache annulee doit rester visuellement secondaire"
    )
    assert m and float(m.group(1)) < 0.8
    done_i = css.index('.mp-post[data-state="done"]')
    assert "opacity" not in css[done_i:css.index("}", done_i)]


def test_l_etat_se_lit_a_UN_METRE():
    """Un point de 7 px ne se voit pas. C'est le lisere de la carte qui porte
    l'etat, et il vient du meme canal que tout le reste."""
    css = _sans_commentaires()
    i = css.index(".mp-post::before")
    corps = css[i:css.index("}", i)]
    assert "var(--et" in corps, "le lisere d'etat n'est pas relie au canal d'etat"
    assert "position: absolute" in corps and "width: 3px" in corps


def test_il_y_a_TROIS_niveaux_de_profondeur():
    """Mission en creux, carte posee dessus, pensee en creux dans la carte."""
    css = _sans_commentaires()
    def fond(sel):
        i = css.index(sel)
        return css[i:css.index("}", i)]
    assert "var(--bg-accent)" in fond(".mp-mission {")
    assert "var(--card)" in fond(".mp-post {") and "box-shadow" in fond(".mp-post {")
    assert "var(--bg-accent)" in fond(".mp-thought {")


def test_les_chiffres_ne_TRESSAUTENT_pas():
    """Un compte a rebours en chasse proportionnelle bouge a chaque seconde."""
    css = _sans_commentaires()
    i = css.index(".mp-countdown b")
    assert "tabular-nums" in css[i:css.index("}", i)]
