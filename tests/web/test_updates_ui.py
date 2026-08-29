from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_update_assets_are_loaded_and_banner_is_accessible() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert '/static/css/updates.css?v=2' in html
    assert '/static/js/updates.js?v=2' in html
    assert 'id="update-banner"' in html
    assert 'aria-live="polite"' in html


def test_configuration_mounts_the_real_update_center() -> None:
    panels = (ROOT / "web" / "static" / "js" / "panels.js").read_text(encoding="utf-8")

    assert "name:'Mises a jour'" in panels
    assert 'id="update-center"' in panels
    assert "window.renderUpdateCenter" in panels


def test_update_ui_exposes_apply_only_after_verified_transactional_staging() -> None:
    script = (ROOT / "web" / "static" / "js" / "updates.js").read_text(encoding="utf-8")

    assert "/api/updates/releases" in script
    assert "/api/updates/select" in script
    assert "/api/updates/download" in script
    assert "/api/updates/apply" in script
    assert "fullInstallerAction" in script
    assert "Télécharger l'installateur complet" in script
    assert "Télécharger et vérifier" in script
    assert "entry.installable" in script
    assert "state==='verified'" in script


def test_update_layout_has_mobile_constraints() -> None:
    css = (ROOT / "web" / "static" / "css" / "updates.css").read_text(encoding="utf-8")

    assert "width:min(430px,calc(100vw - 28px))" in css
    assert "@media(max-width:640px)" in css
    assert ".update-banner[hidden]{display:none}" in css


def test_update_status_is_localized_and_colored_by_meaning() -> None:
    script = (ROOT / "web" / "static" / "js" / "updates.js").read_text(encoding="utf-8")
    css = (ROOT / "web" / "static" / "css" / "updates.css").read_text(encoding="utf-8")

    assert "up_to_date:'À jour'" in script
    assert "available:'Mise à jour disponible'" in script
    assert "updateStateLabel(state)" in script
    assert ".update-state-pill.up_to_date" in css
    assert ".update-state-pill.available" in css
    assert "color:var(--ok)" in css
    assert "color:var(--accent)" in css
