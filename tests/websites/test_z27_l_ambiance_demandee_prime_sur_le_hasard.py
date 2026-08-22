"""LOT Z27 — une ambiance demandée prime sur une palette tirée au sort.

Dernier volet de Z21. Mesuré AVANT correctif, en appelant le vrai code :

    « boulangerie artisanale, ambiance SOMBRE et elegante, dark mode »  -> LIGHT
    « Site vitrine, dark mode imperatif demande par le client »          -> LIGHT

Et le brief injecté au worker ordonne, juste au-dessus de la palette :

    « Applique EXACTEMENT ces choix. Ne substitue PAS par tes valeurs. »

Lumena sommait donc le worker d'ignorer l'ambiance que l'utilisateur avait
exigée. Même famille que Z26 : passer outre ce que l'utilisateur a dit, avec
autorité.

Cause : `select_pro_palette` score les 98 palettes sur des mots-clés de DOMAINE,
puis tranche au hasard (seedé MD5) entre ex æquo. Rien ne regarde le thème
demandé. Sur 98 palettes, 20 seulement sont sombres.

Correctif : quand l'ambiance est explicite, on restreint le vivier au thème voulu
PUIS on score dedans — le domaine choisit toujours, mais parmi les bonnes.

⚠️ Ces tests portent sur le THÈME, jamais sur le NOM de palette : la sélection
tire au sort entre ex æquo et n'est déterministe que seedée par
`build_design_directives`. Asserter un nom serait un test instable.
"""

import pytest

from src.tools.ui_ux_knowledge import (
    PRO_PALETTES,
    get_design_for_project,
    requested_theme,
    select_pro_palette,
)
from src.tools.website_builder import build_design_directives


# ── La détection : exigeante à dessein ───────────────────────────────────────


@pytest.mark.parametrize("msg", [
    "Site boulangerie, ambiance SOMBRE et elegante, dark mode",
    "Portfolio, theme sombre obligatoire",
    "Landing page avec un fond noir",
    "Site vitrine, dark mode imperatif demande par le client",
    "Refais-le en sombre",
    "Palette sombre s'il te plait",
])
def test_une_demande_de_sombre_est_vue(msg):
    assert requested_theme(msg) == "dark"


@pytest.mark.parametrize("msg", [
    "Landing page SaaS en mode clair, fond blanc",
    "Theme clair pour le site",
    "Version claire uniquement",
])
def test_une_demande_de_clair_est_vue(msg):
    assert requested_theme(msg) == "light"


@pytest.mark.parametrize("msg", [
    "Un roman a l'ambiance d'une histoire sombre et triste",
    "Site sur les periodes sombres de l'Histoire",
    "Boulangerie artisanale a Lyon",
    "",
])
def test_sombre_sans_qualifieur_de_design_ne_compte_pas(msg):
    """« sombre » nu n'est pas une consigne de design — sinon un site sur une
    période sombre de l'Histoire basculerait en dark mode."""
    assert requested_theme(msg) is None


@pytest.mark.parametrize("msg", [
    "Boutique en ligne, surtout PAS de dark mode",
    "Sans mode sombre",
    "Aucun theme sombre",
])
def test_une_negation_annule_la_demande(msg):
    assert requested_theme(msg) != "dark"


def test_une_demande_contradictoire_ne_devine_pas():
    """Dark ET light demandés : on ne tranche pas à sa place."""
    assert requested_theme("Je veux un dark mode et aussi un mode clair") is None


# ── L'effet : le cas mesuré ──────────────────────────────────────────────────


def test_le_cas_mesure_bascule_enfin_en_sombre():
    """LE lot."""
    d = get_design_for_project(
        "Site pour une boulangerie artisanale, ambiance SOMBRE et elegante, dark mode"
    )
    assert d["is_dark"] is True


def test_le_second_cas_mesure_aussi():
    d = get_design_for_project("Site vitrine, dark mode imperatif demande par le client")
    assert d["is_dark"] is True


def test_sans_mot_cle_de_domaine_le_theme_tient_quand_meme():
    """Le repli final doit rester DANS le vivier restreint. Sans cette garde,
    « site vitrine, dark mode » — qui ne score sur aucun domaine — reperdait le
    thème au tout dernier moment."""
    for _ in range(25):
        assert select_pro_palette("dark mode")["theme"] == "dark"


def test_une_demande_de_clair_reste_claire():
    for _ in range(25):
        assert select_pro_palette("mode clair, fond blanc")["theme"] == "light"


def test_sans_demande_le_comportement_est_inchange():
    """Z27 doit être strictement inerte quand rien n'est demandé : sinon il
    change le design de toutes les missions passées."""
    p = select_pro_palette("Site pour une boulangerie artisanale")
    assert p in PRO_PALETTES


def test_le_domaine_choisit_toujours_dans_le_bon_theme():
    """On restreint le vivier, on ne remplace pas le scoring : un sujet très
    typé doit garder sa palette de domaine quand elle est déjà sombre."""
    p = select_pro_palette("portfolio photographe, theme sombre, fond noir")
    assert p["theme"] == "dark"
    assert "photo" in (p["name"] + p["product_type"]).lower()


# ── Le brief dit d'où vient le thème ─────────────────────────────────────────


def test_le_brief_signale_une_ambiance_demandee():
    """Le brief ordonne « Applique EXACTEMENT ces choix » : le worker doit savoir
    qu'ici, obéir c'est obéir à l'utilisateur, pas à un tirage."""
    txt = build_design_directives("Site boulangerie, ambiance sombre demandee")
    ligne = next(l for l in txt.splitlines() if l.startswith("Thème"))
    assert "DARK" in ligne
    assert "DEMANDÉ EXPLICITEMENT" in ligne


def test_le_brief_reste_muet_sans_demande():
    txt = build_design_directives("Site pour une boulangerie artisanale")
    ligne = next(l for l in txt.splitlines() if l.startswith("Thème"))
    assert "DEMANDÉ EXPLICITEMENT" not in ligne


def test_le_brief_reste_deterministe():
    """Propriété historique du générateur (seed MD5) : deux appels identiques
    produisent le même brief."""
    m = "Site boulangerie, ambiance sombre demandee"
    assert build_design_directives(m) == build_design_directives(m)


def test_la_note_ne_leve_jamais():
    from src.tools.website_builder import _theme_origin_note
    assert _theme_origin_note("") == ""
    assert _theme_origin_note(None) == ""
