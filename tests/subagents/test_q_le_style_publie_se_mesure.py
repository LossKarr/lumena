"""LOT Q — une page qui passe tous les gardes peut arriver illisible.

Run Fibrance (2026-08-14, mission task_a8317f76). La chaîne complète s'est
déroulée sans une seule alerte : contrat posé, 3 workers, CodeAgent ×3,
`serve_website` sur le port 8081 (pas 8080 — le piège a été évité),
`browser_navigate`, `browser_click_index` sur le bouton de thème,
`browser_evaluate`, `publish_mission_workspace`. Le thème basculait vraiment.
Tous les verrous ont dit oui.

Et la page était inutilisable : menu affiché en puces, liens bleus soulignés,
contenu débordant hors de l'écran.

    le HTML écrivait        le CSS stylait
    .nav-menu               .nav-links
    .cards / .card          .skills-grid / .skill-card
    .gallery                .gallery-grid
    .pricing                .pricing-grid / .price
    .reveal                 .fade-in / .visible
    .header-inner           .container

    12 classes sur 15 ne se rencontraient JAMAIS.

**Ce que l'audit a réfuté.** Premier réflexe : accuser le découpage — HTML et
CSS chez deux workers, chacun son vocabulaire. Mes 4 premiers cas étaient tous
cassés, la cause semblait tenir. Sur le corpus COMPLET (135 contrats, 72 avec
HTML+CSS) elle s'effondre :

    owners SÉPARÉS   6 projets réels   3 à 100 % · 3 sous 20 %
    owner COMMUN    35 projets        13 sous 50 % (37 %)

Un propriétaire unique ne protège de rien (14 %, 25 %, 38 %…) et la séparation
n'empêche pas la perfection (converto, memogame, palindrotest à 100 %). **Aucune
règle de découpage n'est fondée : aucune n'est posée.**

Ce qui reste : sur 47 projets web mesurables, **19 sont sous 50 %** — et ce
chiffre n'a jamais été calculé ni dit. C'est le motif du chantier, en pire :
d'habitude le fait existait et était jeté ; ici il n'existait pas.
"""
from __future__ import annotations

import inspect

import pytest

from src.subagents.style_coverage import (
    css_classes,
    html_classes,
    style_coverage,
    style_coverage_note,
)


# ── la mesure elle-même ─────────────────────────────────────────────────────

def _site(tmp_path, html: str, css: str = ""):
    tmp_path.joinpath("index.html").write_text(html, encoding="utf-8")
    if css:
        tmp_path.joinpath("style.css").write_text(css, encoding="utf-8")
    return tmp_path


def test_the_exact_fibrance_mismatch(tmp_path):
    """Les vrais noms du run : aucun ne se rencontre."""
    root = _site(
        tmp_path,
        '<nav class="nav-menu"><ul class="cards"></ul></nav>'
        '<div class="gallery reveal"><p class="pricing"></p></div>',
        ".nav-links{}\n.skill-card{}\n.gallery-grid{}\n.fade-in{}\n.price{}",
    )
    m = style_coverage(root)
    assert m["percent"] == 0
    assert set(m["unstyled"]) == {"nav-menu", "cards", "gallery", "reveal", "pricing"}


def test_a_page_whose_vocabulary_matches_scores_full(tmp_path):
    root = _site(
        tmp_path,
        '<div class="card"><p class="lead"></p><a class="cta"></a></div>',
        ".card{}\n.lead{}\n.cta{}",
    )
    assert style_coverage(root)["percent"] == 100


def test_inline_style_blocks_count(tmp_path):
    """Une page d'un seul fichier est légitime — l'ignorer ferait passer un
    site correct pour un site cassé."""
    root = _site(
        tmp_path,
        "<style>.card{color:red}.lead{}.cta{}</style>"
        '<div class="card"><p class="lead"></p><a class="cta"></a></div>',
    )
    assert style_coverage(root)["percent"] == 100


def test_template_placeholders_are_not_class_names(tmp_path):
    """`class="{{ css }}"` est une valeur calculée, pas un nom de classe."""
    root = _site(
        tmp_path,
        '<div class="{{ dynamique }}"><p class="a b c"></p></div>',
        ".a{}\n.b{}\n.c{}",
    )
    m = style_coverage(root)
    assert m["total"] == 3 and m["percent"] == 100


@pytest.mark.parametrize("n", [0, 1, 2])
def test_too_few_classes_says_nothing(tmp_path, n):
    """Un pourcentage sur deux classes n'apprend rien à personne."""
    classes = " ".join(f"c{i}" for i in range(n))
    root = _site(tmp_path, f'<div class="{classes}"></div>' if n else "<div></div>")
    assert style_coverage(root) is None
    assert style_coverage_note(root) == ""


def test_a_folder_without_html_is_not_a_website(tmp_path):
    """Missions d'effets, CLI, documents : rien ne change pour elles."""
    tmp_path.joinpath("rapport.md").write_text("# Bilan", encoding="utf-8")
    assert style_coverage(tmp_path) is None
    assert style_coverage_note(tmp_path) == ""


@pytest.mark.parametrize("bad", ["", "   ", None, 42, "/chemin/inexistant/xyz"])
def test_the_probe_never_raises(bad):
    assert style_coverage(bad) is None
    assert style_coverage_note(bad) == ""
    assert html_classes(bad) == set()
    assert css_classes(bad) == set()


# ── la phrase rendue ────────────────────────────────────────────────────────

def test_a_broken_page_is_named_with_its_orphans(tmp_path):
    root = _site(
        tmp_path,
        '<div class="a b c d e f g h"></div>',
        ".a{}",
    )
    note = style_coverage_note(root)
    assert "1/8" in note and "12 %" in note
    assert "`.b`" in note
    assert "vérifie le rendu" in note


def test_an_empty_root_is_refused_before_touching_the_disk():
    """`Path("")` vaut `Path(".")` : un rglob dessus balaye le dépôt entier.
    Mesuré à 47 s sur une seule chaîne vide, avant ce garde — et c'est le même
    piège que `snapshot_mission_files(None)` plus tôt dans la journée."""
    import time

    for vide in ("", "   ", None):
        debut = time.perf_counter()
        assert style_coverage(vide) is None
        assert time.perf_counter() - debut < 0.5, f"{vide!r} a scanné le disque"


def test_a_healthy_page_gets_a_short_line_not_a_warning(tmp_path):
    root = _site(tmp_path, '<div class="a b c"></div>', ".a{}\n.b{}\n.c{}")
    note = style_coverage_note(root)
    assert "100 %" in note
    assert "seulement" not in note
    assert "**" not in note


def test_the_note_never_forbids_anything(tmp_path):
    """Comme les constats du LOT N : on informe, on ne commande pas."""
    root = _site(tmp_path, '<div class="a b c d"></div>', ".a{}")
    note = style_coverage_note(root).lower()
    for interdit in ("⛔", "interdit", "tu dois", "refusé", "obligatoire"):
        assert interdit not in note


# ── le branchement ──────────────────────────────────────────────────────────

def test_publication_carries_the_measure():
    from src.reasoning.handlers import missions

    src = inspect.getsource(missions.publish_mission_workspace_handler)
    assert "style_coverage_note" in src
    assert "_style_note" in src


def test_publication_survives_a_broken_measure():
    """La publication est le chokepoint de vérité : elle ne doit jamais échouer
    à cause d'une mesure d'agrément."""
    from src.reasoning.handlers import missions

    src = inspect.getsource(missions.publish_mission_workspace_handler)
    bloc = src.split("LOT Q")[1][:900]
    assert "try:" in bloc and "except Exception" in bloc


def test_no_grouping_rule_was_introduced():
    """Verrou sur l'audit : les données ne justifient AUCUNE contrainte de
    découpage. Si quelqu'un veut en ajouter une un jour, qu'il refasse la
    mesure d'abord — 3 projets séparés sur 6 sont à 100 %."""
    from src.subagents import mission_contract

    src = inspect.getsource(mission_contract)
    for interdiction in ("même owner", "meme owner", "same_owner", "css_owner"):
        assert interdiction not in src
