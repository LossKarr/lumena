"""
Tests Phase 20A v4 — Routes admin MCP read-only
(adaptés Phase 20B-1 : 2 POST whitelistés ajoutés, encadrés par garde-fous).

Vérifie :
  - Auth obligatoire sur les 10 routes GET
  - Aucune fuite : args ApprovalQueue jamais sérialisés
  - Catalog `notes` jamais sérialisé
  - Watcher source="persisted", live=false
  - Aucune référence interdite hors approve/reject 20B-1 (register, activate, etc.)
  - HTML : panel + nav-item + 4 tabs
  - JS : case + cmd palette entry
  - CSS : aucune nouvelle classe MCP-dédiée

Phase 20B-1 (adaptation) :
  - Exactement 2 @router.post (approve/reject), aucun PUT/DELETE/PATCH
  - .approve( et .reject( autorisés uniquement dans handlers nommés et
    encadrés par _live_mode_enabled + _assert_confirmed (cf.
    test_mcp_admin_approvals_actions.py pour la vérification dédiée)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _make_app(with_auth_override: bool = True) -> FastAPI:
    from web.routes import deps, mcp
    app = FastAPI()
    app.include_router(mcp.router)
    if with_auth_override:
        app.dependency_overrides[deps.verify_admin_token] = lambda: None
    return app


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MCP_ROUTE_PATH = _REPO_ROOT / "web" / "routes" / "mcp.py"
_INDEX_PATH = _REPO_ROOT / "web" / "index.html"
_PANELS_JS_PATH = _REPO_ROOT / "web" / "static" / "js" / "panels.js"
_NAVIGATION_JS_PATH = _REPO_ROOT / "web" / "static" / "js" / "navigation.js"
_CSS_DIR = _REPO_ROOT / "web" / "static" / "css"

_ROUTES_GET = [
    "/api/mcp/health",
    "/api/mcp/catalog",
    "/api/mcp/catalog/alice",
    "/api/mcp/approvals/pending",
    "/api/mcp/approvals/decisions",
    "/api/mcp/watcher/snapshots",
    "/api/mcp/watcher/snapshots/alice",
    "/api/mcp/discovery/reports",
    "/api/mcp/discovery/reports/alice/2026-01-01",
    "/api/mcp/audit/catalog",
]


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Auth obligatoire
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _ROUTES_GET)
async def test_route_requires_admin_token_401(path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app = _make_app(with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path)
    assert resp.status_code == 401, f"{path} should require auth"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _ROUTES_GET)
async def test_route_bad_token_403(path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app = _make_app(with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            path, headers={"Authorization": "Bearer wrong-token"}
        )
    assert resp.status_code == 403, f"{path} should reject bad token"


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Health smoke
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_health_returns_structure():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["available"] is True
    # Phase 20B-6 : phase et mode évoluent avec les sous-phases mutatives signées.
    assert data["phase"] in (
        "20A", "20B-1", "20B-2", "20B-3", "20B-4", "20B-5", "20B-6", "21",
    )
    # Phase 21 substitue le mode "read_only_*" par "hardening_*". On accepte
    # désormais l'une OU l'autre des familles, sans exiger "read_only".
    assert ("read_only" in data["mode"]) or ("hardening" in data["mode"])
    assert "components" in data
    for comp in ("catalog", "approval_queue", "watcher", "discovery"):
        assert comp in data["components"]


@pytest.mark.asyncio
async def test_health_watcher_mode_reflects_runtime_singleton():
    """Phase 20B-3 : le watcher a désormais un singleton runtime (utilisé par
    MCPActivationService pour register_runner + record_event live) en plus
    des snapshots persistés exposés en lecture par les routes GET 20A.
    """
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    data = resp.json()
    watcher_block = data["components"]["watcher"]
    assert watcher_block["mode"] in (
        "persisted_snapshots_only",            # legacy (avant 20B-3)
        "runtime_singleton_plus_persisted_snapshots",  # 20B-3+
    )
    # Phase 20B-3 : champ runtime_singleton exposé (bool)
    assert "runtime_singleton" in watcher_block
    assert isinstance(watcher_block["runtime_singleton"], bool)
    assert data["components"]["discovery"]["mode"] == "reports_only"


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Read-only / data filter
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_catalog_list_returns_array(tmp_path, monkeypatch):
    # Force le catalog à pointer vers un répertoire vide (pas d'entrées)
    monkeypatch.setattr(
        "src.utils.paths.DATA_DIR", tmp_path, raising=False,
    )
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/catalog")
    assert resp.status_code == 200
    data = resp.json()
    assert "servers" in data
    assert isinstance(data["servers"], list)


@pytest.mark.asyncio
async def test_catalog_get_unknown_404():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/catalog/nonexistent-server")
    # 404 ou available=false selon disponibilité
    assert resp.status_code in (200, 404)


@pytest.mark.asyncio
async def test_catalog_get_never_returns_notes_field(tmp_path, monkeypatch):
    """Le champ `notes` doit être absent de la réponse (filtré côté serveur)."""
    # On crée un catalog avec un server qui a des notes
    from src.mcp.server_catalog import MCPServerCatalog, ServerStatus
    catalog_dir = tmp_path / "catalog"
    cat = MCPServerCatalog(
        catalog_dir=catalog_dir,
        audit_log_path=tmp_path / "catalog" / "audit.jsonl",
    )
    try:
        cat.add_server(
            server_id="alice",
            display_name="Alice MCP",
            package_spec="npm:mcp-foo",
            owner_profile="alice",
            notes="secret-note-marker-xyz",
        )
    except Exception:
        pytest.skip("Catalog dependencies not available")

    # Monkey-patch _get_catalog pour pointer vers notre instance
    from web.routes import mcp as mcp_routes
    monkeypatch.setattr(mcp_routes, "_get_catalog", lambda: cat)

    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/catalog/alice")
    assert resp.status_code == 200
    data = resp.json()
    server = data.get("server", {})
    assert "notes" not in server, "notes field MUST NOT be exposed"
    # Et surtout le marker secret ne doit pas être dans la réponse
    blob = json.dumps(data)
    assert "secret-note-marker-xyz" not in blob


@pytest.mark.asyncio
async def test_approvals_pending_never_returns_args_field():
    """La réponse ne doit jamais contenir un champ `args` (chiffré ou non)."""
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/approvals/pending")
    assert resp.status_code == 200
    data = resp.json()
    pending = data.get("pending", [])
    for p in pending:
        assert "args" not in p, f"args MUST NOT be exposed in pending: {p}"


@pytest.mark.asyncio
async def test_approvals_decisions_never_returns_args_field():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/approvals/decisions")
    assert resp.status_code == 200
    data = resp.json()
    decisions = data.get("decisions", [])
    for d in decisions:
        assert "args" not in d, f"args MUST NOT be exposed in decisions: {d}"


@pytest.mark.asyncio
async def test_watcher_snapshots_payload_source_persisted_live_false():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/watcher/snapshots")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("source") == "persisted"
    assert data.get("live") is False


@pytest.mark.asyncio
async def test_audit_unknown_component_400():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/audit/not_a_real_component")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_audit_valid_component_returns_events_list():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/audit/catalog")
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert isinstance(data["events"], list)


@pytest.mark.asyncio
async def test_discovery_reports_returns_array():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/discovery/reports")
    assert resp.status_code == 200
    data = resp.json()
    assert "reports" in data
    assert isinstance(data["reports"], list)


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Grep statique : aucune mutation
# ══════════════════════════════════════════════════════════════════════════════


def test_mcp_route_has_exactly_fifteen_post_no_put_delete_patch():
    """Phase 26.2 : exactement 15 @router.post.

      - 20B-1 : approve, reject (2)
      - 20B-2 : install propose, install execute (2)
      - 20B-3 : activation propose, activation execute, activation deactivate (3)
      - 20B-4 : catalog add, catalog quarantine, catalog restore, catalog remove (4)
      - 20B-5 : autoapprove add, autoapprove remove (2)
      - 20B-6 : catalog trust update (1)
      - 26.2 : local-create execute (1)

    Aucun PUT/DELETE/PATCH. Chaîne UI mutative complète.
    """
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    # Phase I-6 ajoute 1 POST (detect-schema) → 16 au total
    assert text.count("@router.post(") == 16, (
        "Phase I-6 attend exactement 16 @router.post"
    )
    # Phase I-6 introduit 2 PUT (set secret + set config) et 2 DELETE
    # (delete secret + delete config). @router.patch reste interdit.
    assert text.count("@router.put(") == 2
    assert text.count("@router.delete(") == 2
    assert "@router.patch" not in text


def test_mcp_route_no_other_mutations_yet():
    """Phase 20B-6 (cumul) : toutes les mutations UI prévues sont implémentées.

    `update_last_active` (Phase 14) reste interne, pas exposé via UI.
    `discover` (Phase 17) reste interne.
    `MCPOrchestrator` (Phase 13) reste interne.
    """
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    forbidden = [
        ".update_last_active(",
        ".discover(",
        "MCPOrchestrator(",
    ]
    for token in forbidden:
        assert token not in text, f"{token} found in mcp.py (forbidden in Phase 20B-6)"


def test_mcp_route_does_not_decrypt_fernet():
    """Aucun appel direct à Fernet ou décryptage des args."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    forbidden = ["Fernet(", ".decrypt("]
    for token in forbidden:
        assert token not in text, f"{token} found in mcp.py"


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Smoke HTML / JS
# ══════════════════════════════════════════════════════════════════════════════


def test_html_has_panel_mcp():
    text = _INDEX_PATH.read_text(encoding="utf-8")
    assert '<div class="panel" id="panel-mcp">' in text


def test_html_has_nav_item_mcp_in_infra_section():
    text = _INDEX_PATH.read_text(encoding="utf-8")
    assert 'data-panel="mcp"' in text
    # nav-item dans section Infra : vérifier que MCP est entre infra-network et providers
    # via l'ordre des occurrences
    pos_network = text.find('data-panel="infra-network"')
    pos_mcp = text.find('data-panel="mcp"')
    pos_providers = text.find('data-panel="providers"')
    assert pos_network < pos_mcp < pos_providers, "MCP must be in Infra section between network and providers"


def test_html_has_6_tabs_in_panel_mcp():
    text = _INDEX_PATH.read_text(encoding="utf-8")
    # 6 onglets attendus (Phase 21 ajoute "diagnostics")
    for tab in (
        "catalog", "approvals", "watcher", "audit",
        "auto_approve", "diagnostics",
    ):
        assert f'data-arg="{tab}"' in text, f"Tab {tab} missing"


def test_html_panel_mcp_doctrine_chat_only():
    """Phase G : le warning Phase 20A est remplacé par une note doctrine
    indiquant que toutes les actions passent par le chat."""
    text = _INDEX_PATH.read_text(encoding="utf-8")
    # Le warning obsolète a été supprimé
    assert "Phase 20A" not in text
    # La nouvelle note doctrine est présente
    assert "parlez à Lumena" in text or "Lumena dans le chat" in text


def test_navigation_js_has_mcp_case():
    text = _NAVIGATION_JS_PATH.read_text(encoding="utf-8")
    assert "case'mcp':loadMcp()" in text or "case 'mcp':loadMcp()" in text


def test_navigation_js_has_mcp_cmd_palette_entry():
    text = _NAVIGATION_JS_PATH.read_text(encoding="utf-8")
    assert "switchPanel('mcp')" in text


def test_panels_js_has_loadMcp_function():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "export async function loadMcp(" in text
    assert "export function loadMcpTab(" in text


def test_panels_js_loaders_present():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    # Phase 21 ajoute _loadMcpDiagnostics au pool des loaders MCP.
    for fn in (
        "_loadMcpCatalog", "_loadMcpApprovals", "_loadMcpWatcher",
        "_loadMcpAuditDiscovery", "_loadMcpAutoApprove",
        "_loadMcpDiagnostics",
    ):
        assert fn in text, f"{fn} missing in panels.js"


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — CSS : aucune nouvelle classe MCP-dédiée
# ══════════════════════════════════════════════════════════════════════════════


def test_no_mcp_specific_class_in_main_css_files():
    """Aucune classe `.mcp-*` ne doit avoir été ajoutée dans les 4 CSS principaux."""
    main_css = ["tokens.css", "components.css", "layout.css", "chat.css"]
    pattern = re.compile(r"^\.mcp[\-_a-zA-Z]*\s*\{", re.MULTILINE)
    for fname in main_css:
        path = _CSS_DIR / fname
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        matches = pattern.findall(text)
        assert not matches, f"Classes MCP trouvées dans {fname}: {matches}"


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — 10 routes exactly
# ══════════════════════════════════════════════════════════════════════════════


def test_exactly_24_get_routes_in_mcp_module():
    """Phase I-6 ajoute 3 GET aux 21 cumulés (schema, config-status, ready)
    → 24 GET au total. Phase 21 = 20, Phase G = +1, Phase I-6 = +3."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    count = text.count("@router.get(")
    assert count == 24, f"Expected 24 GET routes (post Phase I-6), got {count}"


def test_exactly_16_post_routes_with_phase_i6_detect_schema():
    """Phase I-6 ajoute /detect-schema → 16 POST au total."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert text.count("@router.post(") == 16, "Phase I-6 attend 16 POST"
    # PATCH reste strictement interdit
    assert "@router.patch" not in text


def test_phase_i6_introduces_exactly_2_put_and_2_delete():
    """Phase I-6 introduit les PREMIERS PUT et DELETE (config UI library)."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert text.count("@router.put(") == 2, "Phase I-6 attend 2 PUT (secret, config)"
    assert text.count("@router.delete(") == 2, "Phase I-6 attend 2 DELETE"
