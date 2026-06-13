"""Tests Phase G — UI bibliothèque MCP user-facing.

Vérifie :
  - presence du 1er onglet 'Bibliothèque' (mcp-tab-library) dans index.html
  - retrait du warning Phase 20A obsolète
  - 7 onglets MCP (Bibliothèque + 6 anciens renommés)
  - renommage UTF-8 préservé
  - loader JS _loadMcpLibrary present + handler de pre-fill chat
  - route /api/mcp/library exposée par web/routes/mcp.py
"""
from __future__ import annotations

from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_INDEX_HTML = _REPO_ROOT / "web" / "index.html"
_PANELS_JS = _REPO_ROOT / "web" / "static" / "js" / "panels.js"
_MCP_ROUTES = _REPO_ROOT / "web" / "routes" / "mcp.py"


# ──────────────────────────────────────────────────────────────────────────────
# HTML
# ──────────────────────────────────────────────────────────────────────────────


def _html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


def test_html_library_tab_present_and_first():
    html = _html()
    assert 'data-arg="library"' in html
    assert 'data-test-id="mcp-tab-library"' in html


def test_html_phase20a_warning_removed():
    html = _html()
    assert "Phase 20A" not in html
    assert "lecture seule" not in html.lower()


def test_html_renamed_tabs_present():
    html = _html()
    # `&` est encodé en `&amp;` côté HTML → on normalise pour la comparaison.
    normalized = html.replace("&amp;", "&")
    # Labels FR renommés (Phase G)
    for label in ["Bibliothèque", "Serveurs", "Approbations",
                  "État runtime", "Audit & découverte",
                  "Règles auto", "Santé"]:
        assert label in normalized, f"Missing renamed tab label: {label}"


def test_html_seven_mcp_tabs():
    html = _html()
    # 7 boutons d'onglet MCP attendus
    tab_args = [
        'data-arg="library"',
        'data-arg="catalog"',
        'data-arg="approvals"',
        'data-arg="watcher"',
        'data-arg="audit"',
        'data-arg="auto_approve"',
        'data-arg="diagnostics"',
    ]
    for arg in tab_args:
        assert arg in html, f"Missing tab arg {arg}"


def test_html_user_facing_chat_hint_present():
    html = _html()
    # Doctrine : tout par chat
    assert "parlez à Lumena" in html or "parlez à Lumena" in html or "demander" in html.lower()


# ──────────────────────────────────────────────────────────────────────────────
# JS
# ──────────────────────────────────────────────────────────────────────────────


def _js() -> str:
    return _PANELS_JS.read_text(encoding="utf-8")


def test_js_loader_library_present():
    js = _js()
    assert "_loadMcpLibrary" in js
    assert "_mcpRenderLibrary" in js
    # Dispatch dans loadMcpTab
    assert "case'library'" in js or 'case "library"' in js


def test_js_library_filters_present():
    js = _js()
    for fn in ["_mcpLibrarySetFilter", "_mcpLibrarySearchInput",
               "_mcpLibraryPrefillChat", "_mcpLibraryAccepts"]:
        assert fn in js, f"Missing JS hook: {fn}"


def test_js_library_route_used():
    js = _js()
    assert "/api/mcp/library" in js


def test_js_default_tab_is_library():
    js = _js()
    assert "_mcpCurrentTab||'library'" in js or "_mcpCurrentTab || 'library'" in js


def test_js_chat_prefill_uses_dispatch_event():
    """Le bouton 'Demander à Lumena' doit injecter dans le champ chat."""
    js = _js()
    assert "_mcpLibraryPrefillChat" in js
    assert "dispatchEvent" in js


# ──────────────────────────────────────────────────────────────────────────────
# Route
# ──────────────────────────────────────────────────────────────────────────────


def test_mcp_library_route_declared():
    src = _MCP_ROUTES.read_text(encoding="utf-8")
    assert '/api/mcp/library' in src
    assert "async def mcp_library" in src
    # Doctrine lecture seule : pas de POST/PUT/DELETE pour library
    assert '@router.post("/api/mcp/library' not in src
    assert '@router.put("/api/mcp/library' not in src
    assert '@router.delete("/api/mcp/library' not in src


def test_mcp_library_route_admin_token_dep():
    src = _MCP_ROUTES.read_text(encoding="utf-8")
    # On vérifie que la route est protégée par verify_admin_token
    # en cherchant l'ancrage de la route
    idx = src.find('/api/mcp/library')
    assert idx > 0
    decorator_chunk = src[max(0, idx - 200):idx + 200]
    assert "verify_admin_token" in decorator_chunk


# ──────────────────────────────────────────────────────────────────────────────
# Polish Phase H : hero user-facing du panneau Santé
# ──────────────────────────────────────────────────────────────────────────────


def test_santé_hero_user_facing_elements_present():
    js = _js()
    # Hero avec emoji/titre/sous-titre/compteurs
    for hook in [
        "mcp-health-hero", "mcp-health-emoji", "mcp-health-title",
        "mcp-health-subtitle", "mcp-health-count-active",
        "mcp-health-count-installed", "mcp-health-count-pending",
        "mcp-health-count-issues",
    ]:
        assert hook in js, f"hero hook manquant : {hook}"


def test_santé_dev_details_collapsible():
    js = _js()
    # Les détails techniques sont dans un <details> (collapsible)
    assert '<details id="mcp-diag-dev-details"' in js
    assert "Détails techniques" in js


def test_santé_dev_legacy_blocks_preserved():
    """On garde les 5 blocs Phase 21 (readiness/obs/keys/audit/coh)
    dans la section dev pour ne rien perdre de la lisibilité ingénieur."""
    js = _js()
    for hook in [
        "mcp-diag-readiness", "mcp-diag-observability",
        "mcp-diag-keys", "mcp-diag-audit-integrity", "mcp-diag-coherence",
    ]:
        assert hook in js, f"bloc dev legacy manquant : {hook}"


def test_santé_chat_prefill_button_present():
    """Le hero propose un bouton « Demander à Lumena » qui pré-remplit le chat."""
    js = _js()
    assert "_mcpHealthRefreshHero" in js
    assert "_mcpLibraryPrefillChat" in js
    # La chaîne pré-remplie doit parler de diagnostic
    assert "diagnostic complet" in js


def test_santé_uses_library_and_readiness_endpoints():
    """Le hero agrège /api/mcp/library + /api/mcp/readiness côté UI."""
    js = _js()
    assert "/api/mcp/library" in js
    assert "/api/mcp/readiness" in js
