from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_personal_model_panel_has_product_pages_and_no_browser_dialogs() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    js = (ROOT / "web" / "static" / "js" / "personal-models.js").read_text(encoding="utf-8")
    assert 'id="panel-finetuning"' in html
    for page in ("overview", "heritage", "training", "versions", "judging", "data", "advanced"):
        assert f'data-pm-page="{page}"' in html
    assert "alert(" not in js
    assert "confirm(" not in js
    assert "pm-modal" in html
    for control in (
        "pm-model-prefix", "pm-frequency", "pm-duration", "pm-battery",
        "pm-pause-voice", "pm-pause-work", "pm-backup-select",
        "pm-migration-sources", "pm-delete-learning-data",
    ):
        assert f'id="{control}"' in html
    assert 'data-pm-day="0"' in html and 'data-pm-day="6"' in html
    assert "event.key==='Tab'" in js
    assert "document.addEventListener('change'" in js


def test_personal_model_panel_reads_real_backend_and_uses_theme_tokens() -> None:
    js = (ROOT / "web" / "static" / "js" / "personal-models.js").read_text(encoding="utf-8")
    css = (ROOT / "web" / "static" / "css" / "personal-models.css").read_text(encoding="utf-8")
    assert "'/status'" in js and "'/recommendations'" in js
    assert "Math.random" not in js
    for token in ("var(--panel)", "var(--text)", "var(--muted)", "var(--accent)", "var(--border)"):
        assert token in css
    assert "overflow:auto" in css


def test_personal_model_code_is_extracted_from_legacy_panels_bundle() -> None:
    navigation = (ROOT / "web" / "static" / "js" / "navigation.js").read_text(encoding="utf-8")
    main = (ROOT / "web" / "static" / "js" / "main.js").read_text(encoding="utf-8")
    assert "window.loadPersonalModels" in navigation
    assert "./personal-models.js" in main
