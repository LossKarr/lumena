"""LOT Z7 — une moyenne ne désigne personne, donc elle n'appelle aucune action.

Run « Palier » (2026-08-15) — un SaaS front complet, 7 fichiers, 48 minutes,
tout produit, tout publié. La mesure du LOT Q a bien été rendue :

    🎨 Style : seulement 14/38 classes du HTML ont une règle CSS (37 %).

Ce chiffre est exact et il ne décrit RIEN. La réalité était :

    app.html    14/14 = 100 %      ← la page produit, impeccable
    index.html   1/25 =   4 %      ← la page publique, nue

Deux situations opposées, moyennées en une seule qui ne correspond à aucune des
deux. Le lead ne pouvait pas rattraper une page qu'aucun chiffre ne nommait — et
de fait, sur les DEUX missions à couverture basse de tout le corpus (40 % sur
Cadran, 37 % sur Palier), aucune correction n'a jamais été tentée, alors qu'il
restait 24 appels d'outil de marge sur la première.

Z7 fait deux choses : la mesure devient par page, et elle est rendue dès
`serve_website` — l'instant où la mission s'apprête à regarder, avec 12 à 139
outils devant elle, au lieu de l'accusé de publication qui clôt tout.
"""

from pathlib import Path

import pytest

from src.subagents.style_coverage import (
    style_coverage,
    style_coverage_by_page,
    style_coverage_note,
)


def _projet(tmp_path: Path, pages: dict, css: str = "") -> Path:
    for nom, contenu in pages.items():
        cible = tmp_path / nom
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(contenu, encoding="utf-8")
    if css:
        (tmp_path / "styles.css").write_text(css, encoding="utf-8")
    return tmp_path


_NUE = "<div class='hero'><h1 class='hero-title'>x</h1><a class='btn-primary'>y</a></div>"
_STYLEE = "<div class='app'><nav class='side'></nav><main class='zone'></main></div>"
_CSS = ".app{}\n.side{}\n.zone{}\n"


# ── Le cas Palier, reproduit à l'identique ───────────────────────────────────


def test_les_deux_pages_de_palier_sont_distinguees(tmp_path):
    """Le cœur du lot : 100 % et 4 % ne doivent plus être moyennés en 37 %."""
    _projet(tmp_path, {"index.html": _NUE, "app.html": _STYLEE}, _CSS)
    pages = style_coverage_by_page(tmp_path)
    assert len(pages) == 2
    par_nom = {p["page"]: p["percent"] for p in pages}
    assert par_nom["app.html"] == 100
    assert par_nom["index.html"] == 0


def test_la_page_la_plus_mal_couverte_vient_en_premier(tmp_path):
    """C'est elle qu'il faut nommer : une moyenne rassurante la ferait disparaître."""
    _projet(tmp_path, {"index.html": _NUE, "app.html": _STYLEE}, _CSS)
    assert style_coverage_by_page(tmp_path)[0]["page"] == "index.html"


def test_la_note_nomme_le_fichier_a_corriger(tmp_path):
    """Sans nom de fichier, le constat n'est pas actionnable."""
    _projet(tmp_path, {"index.html": _NUE, "app.html": _STYLEE}, _CSS)
    note = style_coverage_note(tmp_path)
    assert "`index.html`" in note
    assert "app.html" in note  # le détail par page cite aussi celle qui va bien


def test_la_note_ne_se_contente_plus_dune_moyenne(tmp_path):
    _projet(tmp_path, {"index.html": _NUE, "app.html": _STYLEE}, _CSS)
    note = style_coverage_note(tmp_path)
    assert "Par page" in note


def test_la_note_cite_les_classes_de_la_pire_page_pas_du_projet(tmp_path):
    """Citer des classes d'une autre page enverrait corriger le mauvais fichier."""
    _projet(tmp_path, {"index.html": _NUE, "app.html": _STYLEE}, _CSS)
    note = style_coverage_note(tmp_path)
    assert "`.hero`" in note
    assert "`.app`" not in note


def test_une_page_parfaite_ne_masque_plus_une_page_nue(tmp_path):
    """La régression exacte de Palier : le global disait 37 %, rien n'a bougé."""
    _projet(tmp_path, {"index.html": _NUE, "app.html": _STYLEE}, _CSS)
    note = style_coverage_note(tmp_path)
    assert "seulement" in note or "n'a que" in note


# ── Le style inline d'une page ne doit pas habiller les autres ───────────────


def test_le_style_inline_ne_compte_que_pour_sa_propre_page(tmp_path):
    """Piège subtil : compter le `<style>` des autres pages ferait passer une
    page nue pour habillée — précisément ce qu'on cherche à voir."""
    _projet(
        tmp_path,
        {
            "a.html": "<style>.hero{}.hero-title{}.btn-primary{}</style>" + _NUE,
            "b.html": _NUE,
        },
    )
    par_nom = {p["page"]: p["percent"] for p in style_coverage_by_page(tmp_path)}
    assert par_nom["a.html"] == 100
    assert par_nom["b.html"] == 0


def test_une_feuille_partagee_profite_bien_a_toutes_les_pages(tmp_path):
    """L'inverse doit rester vrai : un `.css` commun couvre tout le monde."""
    _projet(tmp_path, {"a.html": _STYLEE, "b.html": _STYLEE}, _CSS)
    assert all(p["percent"] == 100 for p in style_coverage_by_page(tmp_path))


# ── Inertie : la mesure ne doit pas parler quand la question ne se pose pas ──


def test_aucune_page_html_aucune_mesure(tmp_path):
    (tmp_path / "notes.md").write_text("rien à styler", encoding="utf-8")
    assert style_coverage_by_page(tmp_path) == []
    assert style_coverage_note(tmp_path) == ""


def test_moins_de_trois_classes_la_page_est_ignoree(tmp_path):
    """Un pourcentage sur deux classes n'apprend rien (seuil hérité du LOT Q)."""
    _projet(tmp_path, {"index.html": "<div class='a'><p class='b'></p></div>"})
    assert style_coverage_by_page(tmp_path) == []


def test_une_racine_vide_ne_declenche_aucun_scan(tmp_path):
    """`Path("")` vaut `Path(".")` : un rglob dessus balaye le dépôt entier
    (mesuré à 47 s). Défaut déjà attrapé deux fois — il reste verrouillé."""
    assert style_coverage_by_page("") == []
    assert style_coverage_by_page(None) == []


def test_un_dossier_inexistant_ne_leve_pas(tmp_path):
    assert style_coverage_by_page(tmp_path / "absent") == []


def test_un_html_illisible_ne_fait_pas_tomber_la_mesure(tmp_path):
    (tmp_path / "ok.html").write_text(_STYLEE, encoding="utf-8")
    (tmp_path / "casse.html").write_bytes(b"\xff\xfe\x00 pas de l'utf-8 \x00")
    (tmp_path / "styles.css").write_text(_CSS, encoding="utf-8")
    pages = style_coverage_by_page(tmp_path)
    assert any(p["page"] == "ok.html" and p["percent"] == 100 for p in pages)


# ── La forme du résultat ─────────────────────────────────────────────────────


def test_chaque_entree_porte_les_champs_attendus(tmp_path):
    _projet(tmp_path, {"index.html": _NUE, "app.html": _STYLEE}, _CSS)
    for p in style_coverage_by_page(tmp_path):
        assert set(p) == {"page", "total", "styled", "percent", "unstyled"}
        assert 0 <= p["percent"] <= 100
        assert p["styled"] <= p["total"]


def test_le_chemin_est_relatif_au_projet(tmp_path):
    """Un chemin absolu dans un message d'agent est du bruit, pas une adresse."""
    _projet(tmp_path, {"pages/index.html": _NUE, "pages/app.html": _STYLEE}, _CSS)
    for p in style_coverage_by_page(tmp_path):
        assert p["page"].startswith("pages/")
        assert not Path(p["page"]).is_absolute()


def test_les_classes_orphelines_sont_triees_et_completes(tmp_path):
    _projet(tmp_path, {"index.html": _NUE}, _CSS)
    orphelines = style_coverage_by_page(tmp_path)[0]["unstyled"]
    assert orphelines == sorted(orphelines)
    assert {"hero", "hero-title", "btn-primary"} <= set(orphelines)


def test_lordre_est_stable_a_couverture_egale(tmp_path):
    """Deux runs successifs doivent rendre le même ordre — sinon les messages
    changent sans raison et on ne peut plus comparer deux exécutions."""
    _projet(tmp_path, {"b.html": _NUE, "a.html": _NUE}, "")
    assert [p["page"] for p in style_coverage_by_page(tmp_path)] == ["a.html", "b.html"]


# ── L'ancienne mesure globale reste intacte ──────────────────────────────────


def test_la_mesure_globale_nest_pas_modifiee(tmp_path):
    """Z7 ajoute une vue, il n'en retire aucune : `style_coverage` est inchangée."""
    _projet(tmp_path, {"index.html": _NUE, "app.html": _STYLEE}, _CSS)
    globale = style_coverage(tmp_path)
    assert globale["total"] == 6
    assert globale["styled"] == 3
    assert globale["percent"] == 50


def test_une_page_unique_garde_le_message_dorigine(tmp_path):
    """Le cas prouvé au LOT Q (boussole, cadran) ne doit pas changer de forme."""
    _projet(tmp_path, {"index.html": _NUE}, "")
    note = style_coverage_note(tmp_path)
    assert "Par page" not in note
    assert "seulement" in note


def test_un_projet_entierement_style_reste_sobre(tmp_path):
    _projet(tmp_path, {"a.html": _STYLEE, "b.html": _STYLEE}, _CSS)
    note = style_coverage_note(tmp_path)
    assert "seulement" not in note
    assert "100 %" in note


@pytest.mark.parametrize("pct_attendu,css", [(100, _CSS), (0, "")])
def test_les_extremes_sont_rendus_exactement(tmp_path, pct_attendu, css):
    _projet(tmp_path, {"a.html": _STYLEE}, css)
    assert style_coverage_by_page(tmp_path)[0]["percent"] == pct_attendu


# ── Z7b : la mesure arrive dès la preview, pas seulement à la publication ────


def test_serve_website_porte_la_mesure():
    """Le point d'injection : `serve_website` précède TOUJOURS la publication
    (9 fois sur 9 dans le corpus), avec 12 à 139 appels d'outil de marge."""
    source = Path("src/tools/website_builder.py").read_text(encoding="utf-8")
    bloc = source[: source.index("✅ Serveur de preview lancé")]
    assert "style_coverage_note" in bloc


def test_la_publication_porte_toujours_la_mesure():
    """Z7 ajoute un point d'appel, il n'en remplace aucun."""
    source = Path("src/reasoning/handlers/missions.py").read_text(encoding="utf-8")
    assert "style_coverage_note" in source


def test_la_mesure_de_preview_ne_peut_pas_casser_la_preview():
    """Une mesure d'appoint ne doit jamais empêcher un serveur de démarrer."""
    source = Path("src/tools/website_builder.py").read_text(encoding="utf-8")
    i = source.index("style_coverage_note")
    autour = source[i - 400 : i + 400]
    assert "try:" in autour
    assert "except Exception" in autour
