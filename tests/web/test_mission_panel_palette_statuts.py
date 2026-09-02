"""La couleur des Missions doit exprimer le résultat, pas le type de carte."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "web/static/css/mission-panel.css").read_text(encoding="utf-8")
VIEWS = (ROOT / "web/static/js/mission_views.js").read_text(encoding="utf-8")
SCENE = (ROOT / "web/static/js/mission_scene.js").read_text(encoding="utf-8")


def test_palette_met_en_cours_orange_valide_vert_et_echec_rouge():
    assert '.mp-post[data-state="running"], .mp-dot-on   { --et: var(--accent); }' in CSS
    assert '.mp-post[data-state="done"],    .mp-dot-done { --et: var(--ok); }' in CSS
    assert '.mp-post[data-state="failed"],  .mp-dot-fail { --et: var(--danger); }' in CSS
    assert '[data-agg="running"] { --et: var(--accent); }' in CSS
    assert '[data-agg="done"] { --et: var(--ok); }' in CSS
    assert '.mp-seg-on { background: var(--accent); }' in CSS


def test_une_reussite_ne_recule_plus_visuellement_comme_une_annulation():
    faded = CSS[CSS.index('.mp-post[data-state="cancelled"] {'):]
    faded = faded[:faded.index("}")]
    assert 'data-state="done"' not in faded
    assert ".mp-seg-done { background: var(--ok); }" in CSS


def test_libelles_et_scene_suivent_la_meme_semantique():
    assert "running: { cls: 'on', lib: 'en cours' }" in VIEWS
    assert "done: { cls: 'done', lib: 'validé' }" in VIEWS
    assert "state === 'done') return color(THREE, stage, '--ok'" in SCENE
    assert "return color(THREE, stage, '--accent'" in SCENE
