"""LOT L2 — le contrat doit couvrir ce que la mission DEMANDE.

Run MemoNest (2026-08-13). L'objectif exigeait « une page d'accueil publique
expliquant le produit » et « VÉRIFIE AU NAVIGATEUR le parcours complet ». Le
contrat posé par le lead :

    models.py · auth.py · notes.py · app.py · tests/test_app.py

Cinq fichiers Python. **Zéro template, zéro CSS.** Aucun worker n'a donc produit
de frontend. Le lead l'a découvert APRÈS la publication et a écrit templates/ et
static/ lui-même, hors contrat et en deux passes séparées — d'où des classes qui
ne se répondent pas :

    HTML écrit : features-grid, feature-card, footer
    CSS écrit  : stats-grid,   stat-card,    site-footer

La page s'affichait sans mise en page. Le code était pourtant bon : 10 tests
verts, isolation A/B prouvée, site en ligne.

Pourquoi rien n'a alerté : les trois avertissements voisins raisonnent sur le
CONTENU du contrat. `missing_shared_stylesheet_warning` exige un HTML déjà
déclaré pour parler du CSS —

    if not has_html or has_css:
        return ""          # aucun HTML déclaré → il se TAIT

Personne ne comparait le contrat à l'OBJECTIF. C'est le motif racine de tout ce
chantier : le fait existait (l'objectif était en mémoire, lisible) et n'était
jamais confronté à la décision.

Mesure sur les **80 contrats réellement produits** : **1 seul** avertissement se
déclenche — MemoNest. Zéro faux positif.
"""
from __future__ import annotations

from src.subagents.mission_contract import objective_expects_ui_warning


_MEMONEST = {
    "project": "memonest",
    "files": [
        {"path": "models.py", "owner": "w_models"},
        {"path": "auth.py", "owner": "w_auth"},
        {"path": "notes.py", "owner": "w_notes"},
        {"path": "app.py", "owner": "w_app"},
        {"path": "tests/test_app.py", "owner": "w_tests"},
    ],
}


# ── le cas réel ─────────────────────────────────────────────────────────────

def test_the_memonest_contract_is_flagged():
    """LE test du lot : le contrat exact qui a coûté le frontend."""
    warn = objective_expects_ui_warning(_MEMONEST, True)
    assert warn
    assert ".html" in warn and "owner" in warn


def test_the_warning_names_the_fix():
    """Guidance, pas reproche : le lead doit savoir QUOI écrire."""
    warn = objective_expects_ui_warning(_MEMONEST, True)
    assert "templates/index.html" in warn
    assert "w_web" in warn


def test_a_contract_with_templates_says_nothing():
    data = {
        "files": [
            {"path": "app.py", "owner": "w_app"},
            {"path": "templates/index.html", "owner": "w_web"},
        ]
    }
    assert objective_expects_ui_warning(data, True) == ""


def test_the_htm_extension_counts_too():
    data = {"files": [{"path": "pages/accueil.htm", "owner": "w_web"}]}
    assert objective_expects_ui_warning(data, True) == ""


def test_windows_separators_are_understood():
    data = {"files": [{"path": "templates\\index.html", "owner": "w_web"}]}
    assert objective_expects_ui_warning(data, True) == ""


# ── NON-RÉGRESSION : le silence quand il le faut ────────────────────────────

def test_an_objective_without_ui_says_nothing():
    """Le garde-fou principal : un outil CLI ou une API pure n'est jamais
    inquiété. C'est `_objective_wants_browser` (corrigé par L1) qui décide."""
    assert objective_expects_ui_warning(_MEMONEST, False) == ""


def test_a_pure_effects_mission_says_nothing():
    """Mission d'EFFETS (H4) : aucun fichier attendu, que des actions. Lui
    réclamer un template n'aurait aucun sens."""
    data = {
        "files": [],
        "effects": [
            {"owner": "w1", "action": "memory_add", "desc": "mémo", "proof": "id"}
        ],
    }
    assert objective_expects_ui_warning(data, True) == ""


def test_a_contract_with_no_file_at_all_says_nothing():
    """Sans aucun fichier déclaré, ce n'est pas un contrat de CODE : soit une
    mission d'effets, soit un contrat vide que `validate_contract` refusera.
    Réclamer une page n'aiderait ni l'un ni l'autre."""
    assert objective_expects_ui_warning({}, True) == ""
    assert objective_expects_ui_warning({"files": []}, True) == ""


def test_a_contract_with_files_but_no_effects_is_still_flagged():
    """La sortie « effets » ne doit pas devenir une échappatoire : dès qu'il y a
    des fichiers de code, l'absence de page redevient suspecte."""
    data = {"files": [{"path": "app.py", "owner": "w_app"}], "effects": []}
    assert objective_expects_ui_warning(data, True)


# ── robustesse : une GUIDANCE ne doit jamais casser la pose du contrat ──────

def test_garbage_never_raises():
    """Robustesse : on vérifie qu'aucune entrée ne LÈVE, sans présumer du
    verdict — une guidance ne doit jamais empêcher de poser un contrat."""
    for bad in (None, {}, {"files": None}, {"files": [None, "x", 42]},
                {"files": [{}]}, {"files": [{"owner": "w1"}]}, {"files": "texte"}):
        assert isinstance(objective_expects_ui_warning(bad, True), str)
        assert objective_expects_ui_warning(bad, False) == ""


def test_a_malformed_file_entry_still_counts_as_a_declared_file():
    """`[{}]` déclare bien une entrée de fichier (mal formée) : le contrat n'est
    pas vide, donc l'absence de page reste signalée."""
    assert objective_expects_ui_warning({"files": [{}]}, True)


# ── le branchement : la guidance doit ARRIVER au lead ───────────────────────

def test_the_handler_wires_the_warning():
    """Un avertissement jamais appelé est un avertissement inexistant — le piège
    du `json` non importé (H4) et de l'`os` manquant (G1)."""
    import inspect

    from src.reasoning.handlers.missions import write_mission_contract_handler

    src = inspect.getsource(write_mission_contract_handler)
    assert "objective_expects_ui_warning" in src
    assert "_objective_wants_browser" in src


def test_the_warning_is_additive_never_blocking():
    """Comme ses trois voisins : il s'ajoute au message, il ne refuse rien.
    Un refus casserait les contrats où le lead a de bonnes raisons."""
    import inspect

    from src.reasoning.handlers.missions import write_mission_contract_handler

    src = inspect.getsource(write_mission_contract_handler)
    assert "parts.append(_ui_warn)" in src


def test_the_objective_is_read_from_a_variable_always_defined():
    """`_obj_c06` n'existe QUE dans un `if` : s'en servir ici lèverait
    NameError quand `project` est déjà rempli. On lit `lead_meta`, défini
    inconditionnellement."""
    import inspect

    from src.reasoning.handlers.missions import write_mission_contract_handler

    src = inspect.getsource(write_mission_contract_handler)
    ui_block = src.split("objective_expects_ui_warning")[-1]
    assert "lead_meta" in ui_block
    assert "_obj_c06" not in ui_block
