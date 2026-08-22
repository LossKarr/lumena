"""Structural contracts for the production Overview command center."""
from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "web" / "index.html"
MAIN = ROOT / "web" / "static" / "js" / "main.js"
NAVIGATION = ROOT / "web" / "static" / "js" / "navigation.js"
OVERVIEW_JS = ROOT / "web" / "static" / "js" / "overview.js"
OVERVIEW_CSS = ROOT / "web" / "static" / "css" / "overview.css"
THREE_VENDOR = ROOT / "web" / "static" / "vendor" / "three.module.min.js"
THREE_CORE_VENDOR = ROOT / "web" / "static" / "vendor" / "three.core.min.js"
SVG_LOADER_VENDOR = ROOT / "web" / "static" / "vendor" / "SVGLoader.js"
LOGO_3D = ROOT / "web" / "static" / "branding" / "lumena-logo-3d.svg"
PACKAGE = ROOT / "web" / "package.json"
TOOL_CATEGORIES = ROOT / "src" / "reasoning" / "tool_categories.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_overview_assets_and_new_loader_are_wired() -> None:
    html = _text(INDEX)
    main = _text(MAIN)
    navigation = _text(NAVIGATION)

    assert '/static/css/overview.css?v=2' in html
    assert 'id="panel-overview"' in html
    assert 'id="ov-core-canvas"' in html
    assert 'id="ov-widget-grid"' in html
    assert "from './overview.js?v=2'" in main
    assert "loadOverview, stopOverview" in main
    assert "panelName!=='overview'&&window.stopOverview" in navigation


def test_overview_covers_every_navigable_product_panel() -> None:
    html = _text(INDEX)
    js = _text(OVERVIEW_JS)
    available_panels = set(re.findall(r'id="panel-([a-z0-9-]+)"', html))
    navigable_panels = set(
        re.findall(r'class="nav-item[^\"]*"[^>]*data-panel="([a-z0-9-]+)"', html)
    )
    overview_surface = html[html.index('id="panel-overview"'):html.index('<!-- ======= REPO MAP =======')]
    overview_surface += js

    assert navigable_panels <= available_panels
    missing = {
        panel for panel in navigable_panels - {"overview"}
        if not re.search(rf"['\"]{re.escape(panel)}['\"]", overview_surface)
    }
    assert not missing, f"Overview does not expose navigable panel(s): {sorted(missing)}"


def test_overview_exposes_tool_audit_and_honest_navigation_states() -> None:
    js = _text(OVERVIEW_JS)
    category_source = _text(TOOL_CATEGORIES)
    semantic_categories = set(
        re.findall(r'^\s{4}"([a-z_]+)"\s*:\s*ToolCategoryContract', category_source, re.MULTILINE)
    )

    assert len(semantic_categories) >= 20
    for metric in (
        "total_tools", "categories", "contract_callable_any_context",
        "drift_count", "broken_count",
    ):
        assert f"vm.audit.{metric}" in js
    for label in (
        "Outils audités", "Domaines d’outils", "Outils appelables",
        "Drift contractuel", "Défauts structurels",
    ):
        assert label in js
    assert "return {label:'OUVRIR',cls:'is-neutral'}" in js
    for panel in ("ionos", "stripe-overview", "stripe-payments", "stripe-subscriptions", "stripe-products", "logs", "console", "config", "product-docs"):
        assert re.search(rf"['\"]{re.escape(panel)}['\"],panelState\(\)", js)
    assert "twitter_running" in js and "Twitter / X" in js
    assert "Discord" in js and "NON TRACÉ" in js


def test_overview_reads_only_existing_api_routes() -> None:
    js = _text(OVERVIEW_JS)
    routes = set(re.findall(r"['\"](/api/[^'\"]+?)['\"]", js))
    expected = {
        "/api/status", "/api/models", "/api/missions?limit=40",
        "/api/tasks?limit=30", "/api/alerts?limit=20",
        "/api/document-studio/library?limit=8", "/api/trace/recent?limit=40",
        "/api/providers", "/api/voice/status", "/api/mcp/health",
        "/api/mcp/observability/overview", "/api/peers", "/api/workspaces/serving",
        "/api/system/reliability", "/api/runtime/audit?format=summary",
        "/api/daemon/activity", "/api/journal?limit=8",
        "/api/sessions?limit=8", "/api/hooks", "/api/training",
        "/api/finetuning/status",
    }
    assert expected <= routes
    assert "method:" not in js, "Overview must remain a read-only surface"
    assert "Promise.allSettled" in js
    assert "AbortController" in js


def test_overview_never_invents_host_resource_metrics() -> None:
    combined = (_text(INDEX) + _text(OVERVIEW_JS)).lower()
    for unsupported in ("espace disque", "disk usage", "cpu usage", "ram usage"):
        assert unsupported not in combined
    assert "source(s) indisponible(s)" in combined
    assert "aucun état n’est supposé sain" in combined
    assert "AUCUNE PREVIEW" in _text(OVERVIEW_JS)
    assert "AUCUN PAIR" in _text(OVERVIEW_JS)
    assert "hasSloSamples" in _text(OVERVIEW_JS)


def test_overview_layout_is_configurable_and_persisted() -> None:
    html = _text(INDEX)
    js = _text(OVERVIEW_JS)
    widgets = set(re.findall(r'data-ov-widget="([a-z-]+)"', html))
    assert widgets == {
        "work", "attention", "activity", "systems", "deliverables", "health",
        "operations", "capabilities", "sources",
    }
    assert all(f"['{name}'," in js for name in widgets)
    assert "lumena_overview_layout_v1" in js
    assert "lumena_overview_density_v1" in js
    assert "draggable=\"true\"" in html


def test_three_is_local_pinned_and_has_a_non_webgl_fallback() -> None:
    package = json.loads(_text(PACKAGE))
    js = _text(OVERVIEW_JS)
    css = _text(OVERVIEW_CSS)
    assert package["dependencies"]["three"] == "^0.185.1"
    assert THREE_VENDOR.stat().st_size > 300_000
    assert THREE_CORE_VENDOR.stat().st_size > 300_000
    assert SVG_LOADER_VENDOR.stat().st_size > 70_000
    assert LOGO_3D.stat().st_size > 8_000
    assert "'/static/vendor/three.module.min.js'" in js
    assert "'/static/vendor/SVGLoader.js'" in js
    assert "'/static/branding/lumena-logo-3d.svg'" in js
    assert "new THREE.ExtrudeGeometry" in js
    assert "new THREE.HemisphereLight" in js
    assert "window.WebGLRenderingContext" in js
    assert "is-fallback" in js and ".overview-core.is-fallback" in css
    assert "prefers-reduced-motion" in css
    assert "cancelAnimationFrame" in js
    assert "coreRuntime.stop()" in js
    assert "prefers-reduced-motion: reduce" in js
    assert "runtime.dispose" in js


def test_overview_responsive_contract() -> None:
    html = _text(INDEX)
    css = _text(OVERVIEW_CSS)
    assert "@media(max-width:1180px)" in css
    assert "@media(max-width:860px)" in css
    assert "@media(max-width:560px)" in css
    assert ".overview-widget{grid-column:1/-1}" in css
    assert "minmax(0,1fr)" in css
    assert 'class="overview-icon-label">Actualiser<' in html
    assert 'class="overview-icon-label">Personnaliser<' in html
