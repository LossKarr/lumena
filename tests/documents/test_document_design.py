from __future__ import annotations

from pathlib import Path

from src.documents.document_design import inject_document_design, normalize_design
from src.documents.template_catalog import TemplateCatalog
from src.documents.template_renderer import TemplateRenderer


ROOT = Path(__file__).parents[2]


def test_design_values_are_bounded_and_whitelisted():
    design = normalize_design({
        "accent": "javascript:alert(1)",
        "font": "url(evil)",
        "page_margin_mm": 999,
        "logo_width_px": -1,
        "logo_position": "outside",
        "logo_layout": "free",
        "logo_x_pct": 999,
        "logo_y_mm": -20,
    })
    assert design["accent"] == "#D97706"
    assert design["font"] == "modern"
    assert design["page_margin_mm"] == 35
    assert design["logo_width_px"] == 48
    assert design["logo_position"] == "left"
    assert design["logo_layout"] == "free"
    assert design["logo_x_pct"] == 100
    assert design["logo_y_mm"] == 0


def test_design_layer_is_professional_and_logo_optional():
    plain = inject_document_design("<html><head></head><body><h1>Test</h1></body></html>", {"accent": "#0F766E"})
    branded = inject_document_design(
        "<html><head></head><body><h1>Test</h1></body></html>",
        {"accent": "#0F766E", "logo_position": "right"},
        logo_data_uri="data:image/png;base64,AAAA",
    )
    assert "--doc-accent:#0F766E" in plain
    assert '<div class="lumena-document-brand">' not in plain
    assert '<div class="lumena-document-brand">' in branded
    assert "justify-content:flex-end" in branded


def test_free_logo_position_is_bounded_and_rendered_per_template():
    branded = inject_document_design(
        "<html><head></head><body><h1>Test</h1></body></html>",
        {"logo_layout": "free", "logo_x_pct": 63, "logo_y_mm": 47},
        logo_data_uri="data:image/png;base64,AAAA",
    )
    assert "position:absolute" in branded
    assert "left:63%" in branded
    assert "top:47mm" in branded
    assert "translateX(-63%)" in branded
    assert "width:max-content;max-width:100%;display:flex" in branded
    assert "width:128px; max-width:100%" in branded


def test_page_margin_is_visible_in_screen_preview_and_kept_for_print():
    rendered = inject_document_design(
        "<html><head></head><body>Test</body></html>",
        {"page_margin_mm": 27},
    )
    assert "@page { margin: 27mm; }" in rendered
    assert "@media screen { body { box-sizing:border-box; padding:27mm !important; } }" in rendered


def test_font_density_and_flow_alignment_override_builtin_template_css():
    rendered = inject_document_design(
        "<html><head><style>body{font-family:serif}td{font-size:30px}</style></head><body>Test</body></html>",
        {"font": "technical", "density": "compact", "logo_position": "right", "logo_layout": "flow"},
        logo_data_uri="data:image/png;base64,AAAA",
    )
    assert "body *:not(svg):not(path) { font-family:'Bahnschrift'" in rendered
    assert "td { font-size:9.5px !important" in rendered
    assert "padding:6px !important" in rendered
    assert "width:100%;box-sizing:border-box;position:relative;display:flex;justify-content:flex-end" in rendered


def test_every_builtin_has_editable_design_and_shared_quality_layer(tmp_path):
    catalog = TemplateCatalog(tmp_path / "studio", ROOT / "assets" / "templates")
    for record in catalog.list_templates():
        assert record.manifest.design["accent"].startswith("#")
        html = TemplateRenderer().render_html(
            record,
            catalog.read_source(record),
            catalog.read_sample_data(record),
        )
        assert 'id="lumena-document-design"' in html
        assert "print-color-adjust:exact" in html
