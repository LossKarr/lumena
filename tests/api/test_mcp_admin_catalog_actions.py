"""
Tests Phase 20B-4 — Catalog mutations UI (add / quarantine / restore / remove).

Couverture obligatoire :
  - Auth (401/403) sur 4 POST
  - Confirmation backend (confirmed:true requis sur tous)
  - confirmation_phrase = server_id exact (quarantine/restore/remove)
  - add : modal niveau 1 (pas de phrase, seulement confirmed)
  - server_id Phase 14 + Windows reserved
  - Validators répliqués Phase 14 : display_name, package_spec, owner_profile,
    version, trust_score (refus bool/float, range 0-100), notes
  - Test cohérence Phase 14 (import privé autorisé EN TEST uniquement)
  - target_status restore whitelist {"installed"} uniquement
  - server_not_found, server_already_exists
  - status checks : quarantine refusé sur REMOVED/QUARANTINED, restore refusé
    si non-QUARANTINED, remove refusé sur ACTIVE, REMOVED idempotent
  - Dry-run STRICT : 0 call add_server / update_status / remove_server
  - Live add : appelle add_server une fois avec kwargs corrects
  - Live quarantine : appelle update_status(QUARANTINED)
  - Live restore : appelle update_status(INSTALLED)
  - Live remove : appelle remove_server
  - Audit UI étendu sans fuite :
    - package_spec réduit à package_spec_transport (npm/pypi/local/unknown)
    - trust_score réduit à trust_score_set (bool)
    - JAMAIS display_name, version, notes raw, ServerEntry brut,
      confirmation_phrase raw, raw body, CatalogError message
  - Réponse whitelist (aucun ServerEntry brut)
  - Aucun subprocess / MCPSandboxRunner / MCPActivationService / ApprovalQueue
    dans les handlers Catalog
  - Aucun _take_marker / _put_marker dans les handlers Catalog
  - Aucun MCPServerCatalog() neuf dans les handlers
  - Aucun import du helper privé Phase 14
  - Rate limit /api/mcp/catalog/ dans _EXPENSIVE_PREFIXES
  - JS smoke : bouton "Ajouter server", 4 modals, 4 handlers
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest
from fastapi import FastAPI


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MCP_ROUTE_PATH = _REPO_ROOT / "web" / "routes" / "mcp.py"
_PANELS_JS_PATH = _REPO_ROOT / "web" / "static" / "js" / "panels.js"
_SERVER_PY_PATH = _REPO_ROOT / "web" / "server.py"


# ──────────────────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────────────────


class _FakeStatus:
    def __init__(self, value: str):
        self.value = value


class _FakeServerEntry:
    def __init__(self, server_id: str, status: str = "declared",
                 package_spec: str = "npm:fake-pkg"):
        self.server_id = server_id
        self.status = _FakeStatus(status)
        self.package_spec = package_spec
        self.version = f"SECRET_VERSION_LEAK_{server_id}"
        self.notes = f"SECRET_NOTES_LEAK_{server_id}"
        self.display_name = f"SECRET_DISPLAY_LEAK_{server_id}"
        self.owner_profile = "test"
        self.trust_score = 80
        self.added_at = "2026-06-01T00:00:00Z"
        self.updated_at = "2026-06-01T00:00:00Z"
        self.last_active_at = None


class _FakeCatalog:
    def __init__(self, entries: Optional[List[_FakeServerEntry]] = None):
        self._entries: Dict[str, _FakeServerEntry] = {
            e.server_id: e for e in (entries or [])
        }
        self.add_calls: List[Dict[str, Any]] = []
        self.update_status_calls: List[Dict[str, Any]] = []
        self.remove_calls: List[str] = []
        self.add_should_raise: Optional[str] = None
        self.update_status_should_raise: Optional[str] = None
        self.remove_should_raise: bool = False

    def get_server(self, server_id):
        return self._entries.get(server_id)

    def add_server(self, **kwargs):
        self.add_calls.append(kwargs)
        if self.add_should_raise is not None:
            raise ValueError(self.add_should_raise)
        sid = kwargs["server_id"]
        entry = _FakeServerEntry(
            sid, status="declared",
            package_spec=kwargs.get("package_spec", "npm:fake"),
        )
        entry.owner_profile = kwargs.get("owner_profile", "test")
        self._entries[sid] = entry
        return entry

    def update_status(self, server_id, new_status):
        self.update_status_calls.append({
            "server_id": server_id, "new_status": new_status,
        })
        if self.update_status_should_raise is not None:
            raise ValueError(self.update_status_should_raise)
        if server_id in self._entries:
            # Convertir l'enum vers la valeur string
            target_val = getattr(new_status, "value", str(new_status))
            self._entries[server_id].status = _FakeStatus(target_val)

    def remove_server(self, server_id):
        self.remove_calls.append(server_id)
        if self.remove_should_raise:
            raise RuntimeError("boom-remove-raw")
        if server_id in self._entries:
            self._entries[server_id].status = _FakeStatus("removed")
            return True
        return False


def _make_app(
    *,
    entries: Optional[List[_FakeServerEntry]] = None,
    no_catalog: bool = False,
    with_auth_override: bool = True,
) -> tuple[FastAPI, _FakeCatalog]:
    from web.routes import deps, mcp as mcp_routes

    catalog = _FakeCatalog(entries=entries or [])
    deps._MCP_SERVER_CATALOG_SINGLETON = None if no_catalog else catalog
    mcp_routes._APPROVAL_RESULT_CACHE.clear()

    app = FastAPI()
    app.include_router(mcp_routes.router)
    if with_auth_override:
        app.dependency_overrides[deps.verify_admin_token] = lambda: None
    return app, catalog


def _clear_audit_path(monkeypatch, tmp_path):
    from web.routes import mcp as mcp_routes
    target = tmp_path / "mcp_admin_audit" / "audit.jsonl"
    monkeypatch.setattr(mcp_routes, "_ui_audit_path", lambda: target)
    return target


def _read_audit(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Auth
# ══════════════════════════════════════════════════════════════════════════════


_CATALOG_PATHS = [
    "/api/mcp/catalog/add",
    "/api/mcp/catalog/alice/quarantine",
    "/api/mcp/catalog/alice/restore",
    "/api/mcp/catalog/alice/remove",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _CATALOG_PATHS)
async def test_catalog_route_requires_admin_token_401(path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alice")], with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(path, json={"confirmed": True})
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _CATALOG_PATHS)
async def test_catalog_route_bad_token_403(path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alice")], with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            path, headers={"Authorization": "Bearer wrong"},
            json={"confirmed": True},
        )
    assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Confirmation backend
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_add_without_confirmed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json={
            "server_id": "alice", "display_name": "Alice",
            "package_spec": "npm:x", "owner_profile": "default",
        })
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_required"


@pytest.mark.asyncio
async def test_quarantine_without_confirmed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice", status="installed")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/quarantine",
            json={"confirmation_phrase": "alice", "server_id": "alice"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_restore_without_confirmed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice", status="quarantined")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/restore",
            json={
                "confirmation_phrase": "alice", "server_id": "alice",
                "target_status": "installed",
            },
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_remove_without_confirmed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice", status="installed")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/remove",
            json={"confirmation_phrase": "alice", "server_id": "alice"},
        )
    assert resp.status_code == 400


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — confirmation_phrase = server_id exact (quarantine/restore/remove)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,extra",
    [
        ("/api/mcp/catalog/alice/quarantine", {}),
        ("/api/mcp/catalog/alice/restore", {"target_status": "installed"}),
        ("/api/mcp/catalog/alice/remove", {}),
    ],
)
@pytest.mark.parametrize("phrase", ["ALICE", "bob", "", "alice "])
async def test_phrase_must_match_server_id_exact(
    monkeypatch, tmp_path, path, extra, phrase
):
    _clear_audit_path(monkeypatch, tmp_path)
    status = "quarantined" if "restore" in path else "installed"
    app, _ = _make_app(entries=[_FakeServerEntry("alice", status=status)])
    body = {"confirmed": True, "confirmation_phrase": phrase, "server_id": "alice"}
    body.update(extra)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(path, json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_phrase_invalid"


@pytest.mark.asyncio
async def test_add_does_not_require_confirmation_phrase(monkeypatch, tmp_path):
    """add est modal niveau 1 : pas de phrase exigée, juste confirmed=true."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json={
            "confirmed": True,
            "server_id": "alice",
            "display_name": "Alice",
            "package_spec": "npm:alice-pkg",
            "owner_profile": "default",
        })
    assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — server_id Phase 14 + Windows
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sid",
    ["Alice", ".hidden", "-foo", "a" * 65, "", "..", "a/b", "a\\b",
     "con", "aux", "nul", "prn", "com1", "lpt9", "con.txt"],
)
async def test_server_id_invalid_in_add(monkeypatch, tmp_path, sid):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": sid,
            "display_name": "X", "package_spec": "npm:x",
            "owner_profile": "default",
        })
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "server_id_invalid_format"


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — display_name validation
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "display",
    ["", "x" * 201, None, 123, "with\x00null", "with\x1fcontrol", "tab\there"],
)
async def test_display_name_invalid_in_add(monkeypatch, tmp_path, display):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = {
        "confirmed": True, "server_id": "alice",
        "package_spec": "npm:x", "owner_profile": "default",
    }
    if display is not None:
        body["display_name"] = display
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "display_name_invalid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — package_spec validation (transports + path traversal + drive Win)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pkg",
    [
        "", "foobar:x", "X:windows-drive", "npm:..hidden",
        "npm:has space", "npm:has;semicolon", "pypi:with/slash",
        "local:UPPER", "npm:has\\backslash",
    ],
)
async def test_package_spec_invalid_in_add(monkeypatch, tmp_path, pkg):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice",
            "display_name": "X", "package_spec": pkg,
            "owner_profile": "default",
        })
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "package_spec_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pkg",
    [
        "npm:my-pkg", "npm:@scope/my-pkg", "pypi:my_pkg", "local:my-slug",
        "npm:m", "pypi:M",
    ],
)
async def test_package_spec_valid_in_add(monkeypatch, tmp_path, pkg):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice",
            "display_name": "X", "package_spec": pkg,
            "owner_profile": "default",
        })
    assert resp.status_code == 200, resp.text


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — owner_profile validation
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "owner",
    ["", "Has-Upper", "with space", "with.dot", "a" * 65, "héro"],
)
async def test_owner_profile_invalid_in_add(monkeypatch, tmp_path, owner):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice",
            "display_name": "X", "package_spec": "npm:x",
            "owner_profile": owner,
        })
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "owner_profile_invalid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — version validation
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_version_none_accepted_in_add(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice", "display_name": "X",
            "package_spec": "npm:x", "owner_profile": "default",
        })
    assert resp.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "version",
    ["", " ", "with space", "a" * 65, "ç", 123],
)
async def test_version_invalid_in_add(monkeypatch, tmp_path, version):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice", "display_name": "X",
            "package_spec": "npm:x", "owner_profile": "default",
            "version": version,
        })
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "version_invalid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — trust_score validation
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("ts", [-1, 101, True, False, 80.0, "80"])
async def test_trust_score_invalid_in_add(monkeypatch, tmp_path, ts):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice", "display_name": "X",
            "package_spec": "npm:x", "owner_profile": "default",
            "trust_score": ts,
        })
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "trust_score_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize("ts", [0, 50, 100])
async def test_trust_score_valid_in_add(monkeypatch, tmp_path, ts):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice", "display_name": "X",
            "package_spec": "npm:x", "owner_profile": "default",
            "trust_score": ts,
        })
    assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — notes validation
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("notes", ["héro", "with\x00null", "x" * 257, 42])
async def test_notes_invalid_in_add(monkeypatch, tmp_path, notes):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice", "display_name": "X",
            "package_spec": "npm:x", "owner_profile": "default",
            "notes": notes,
        })
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "notes_invalid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — target_status restore whitelist
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    ["active", "declared", "removed", "quarantined", "INSTALLED", "", None, 123],
)
async def test_restore_target_status_invalid(monkeypatch, tmp_path, target):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice", status="quarantined")])
    body = {
        "confirmed": True, "confirmation_phrase": "alice", "server_id": "alice",
    }
    if target is not None:
        body["target_status"] = target
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/alice/restore", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "target_status_invalid"


@pytest.mark.asyncio
async def test_restore_target_status_installed_accepted(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alice", status="quarantined")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/restore",
            json={
                "confirmed": True, "confirmation_phrase": "alice",
                "server_id": "alice", "target_status": "installed",
            },
        )
    assert resp.status_code == 200, resp.text


# ══════════════════════════════════════════════════════════════════════════════
# Section 12 — Catalog unavailable
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,extra",
    [
        ("/api/mcp/catalog/add", {
            "server_id": "alice", "display_name": "X", "package_spec": "npm:x",
            "owner_profile": "default",
        }),
        ("/api/mcp/catalog/alice/quarantine", {
            "confirmation_phrase": "alice", "server_id": "alice",
        }),
        ("/api/mcp/catalog/alice/restore", {
            "confirmation_phrase": "alice", "server_id": "alice",
            "target_status": "installed",
        }),
        ("/api/mcp/catalog/alice/remove", {
            "confirmation_phrase": "alice", "server_id": "alice",
        }),
    ],
)
async def test_catalog_unavailable_503(monkeypatch, tmp_path, path, extra):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(no_catalog=True)
    body = {"confirmed": True}
    body.update(extra)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(path, json=body)
    assert resp.status_code == 503
    assert resp.json().get("detail", {}).get("error_code") == "catalog_unavailable"


# ══════════════════════════════════════════════════════════════════════════════
# Section 13 — server_not_found / server_already_exists / status checks
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_quarantine_server_not_found(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/ghost/quarantine",
            json={
                "confirmed": True, "confirmation_phrase": "ghost",
                "server_id": "ghost",
            },
        )
    assert resp.status_code == 404
    assert resp.json().get("detail", {}).get("error_code") == "server_not_found"


@pytest.mark.asyncio
async def test_add_server_already_exists_409(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    catalog_existing = _FakeCatalog(entries=[_FakeServerEntry("alice")])
    catalog_existing.add_should_raise = "server_already_exists"
    from web.routes import deps, mcp as mcp_routes
    deps._MCP_SERVER_CATALOG_SINGLETON = catalog_existing
    mcp_routes._APPROVAL_RESULT_CACHE.clear()
    app = FastAPI()
    app.include_router(mcp_routes.router)
    app.dependency_overrides[deps.verify_admin_token] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice", "display_name": "X",
            "package_spec": "npm:x", "owner_profile": "default",
        })
    assert resp.status_code == 409
    assert resp.json().get("detail", {}).get("error_code") == "server_already_exists"


@pytest.mark.asyncio
async def test_quarantine_already_quarantined_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice", status="quarantined")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/quarantine",
            json={
                "confirmed": True, "confirmation_phrase": "alice",
                "server_id": "alice",
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "server_id_already_quarantined"


@pytest.mark.asyncio
async def test_quarantine_removed_refused(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice", status="removed")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/quarantine",
            json={
                "confirmed": True, "confirmation_phrase": "alice",
                "server_id": "alice",
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "server_id_invalid_status"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["declared", "installed", "active", "removed"])
async def test_restore_non_quarantined_refused(monkeypatch, tmp_path, status):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice", status=status)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/restore",
            json={
                "confirmed": True, "confirmation_phrase": "alice",
                "server_id": "alice", "target_status": "installed",
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "server_id_not_quarantined"


@pytest.mark.asyncio
async def test_remove_active_refused(monkeypatch, tmp_path):
    """remove sur ACTIVE refusé : force chemin deactivate 20B-3 d'abord."""
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice", status="active")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/remove",
            json={
                "confirmed": True, "confirmation_phrase": "alice",
                "server_id": "alice",
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "server_id_invalid_status"


@pytest.mark.asyncio
async def test_remove_already_removed_idempotent(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, cat = _make_app(entries=[_FakeServerEntry("alice", status="removed")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/remove",
            json={
                "confirmed": True, "confirmation_phrase": "alice",
                "server_id": "alice",
            },
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["removed"] is True
    assert d.get("idempotent") is True
    assert cat.remove_calls == []  # idempotent : pas d'appel remove_server


# ══════════════════════════════════════════════════════════════════════════════
# Section 14 — Dry-run STRICT : 0 mutation catalog
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dry_run_add_does_not_call_add_server(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app, cat = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice", "display_name": "X",
            "package_spec": "npm:x", "owner_profile": "default",
        })
    assert resp.status_code == 200
    assert resp.json()["live_mode"] is False
    assert cat.add_calls == []


@pytest.mark.asyncio
async def test_dry_run_quarantine_does_not_call_update_status(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app, cat = _make_app(entries=[_FakeServerEntry("alice", status="installed")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/quarantine",
            json={
                "confirmed": True, "confirmation_phrase": "alice",
                "server_id": "alice",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["would_quarantine"] is True
    assert cat.update_status_calls == []


@pytest.mark.asyncio
async def test_dry_run_restore_does_not_call_update_status(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app, cat = _make_app(entries=[_FakeServerEntry("alice", status="quarantined")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/restore",
            json={
                "confirmed": True, "confirmation_phrase": "alice",
                "server_id": "alice", "target_status": "installed",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["would_restore"] is True
    assert cat.update_status_calls == []


@pytest.mark.asyncio
async def test_dry_run_remove_does_not_call_remove_server(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app, cat = _make_app(entries=[_FakeServerEntry("alice", status="installed")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/remove",
            json={
                "confirmed": True, "confirmation_phrase": "alice",
                "server_id": "alice",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["would_remove"] is True
    assert cat.remove_calls == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 15 — Live add (1 call avec kwargs corrects)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_live_add_calls_add_server_with_kwargs(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, cat = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice", "display_name": "Alice",
            "package_spec": "npm:alice", "owner_profile": "default",
            "version": "1.0.0", "trust_score": 80,
            "notes": "test note",
        })
    assert resp.status_code == 200
    d = resp.json()
    assert d["added"] is True
    assert d["server_id"] == "alice"
    assert d["status"] == "DECLARED"
    assert d["live_mode"] is True
    assert len(cat.add_calls) == 1
    kwargs = cat.add_calls[0]
    assert kwargs["server_id"] == "alice"
    assert kwargs["display_name"] == "Alice"
    assert kwargs["package_spec"] == "npm:alice"
    assert kwargs["owner_profile"] == "default"
    assert kwargs["version"] == "1.0.0"
    assert kwargs["trust_score"] == 80
    assert kwargs["notes"] == "test note"


# ══════════════════════════════════════════════════════════════════════════════
# Section 16 — Live quarantine / restore / remove
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_live_quarantine_calls_update_status_quarantined(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, cat = _make_app(entries=[_FakeServerEntry("alice", status="installed")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/quarantine",
            json={
                "confirmed": True, "confirmation_phrase": "alice",
                "server_id": "alice",
            },
        )
    assert resp.status_code == 200
    assert len(cat.update_status_calls) == 1
    call = cat.update_status_calls[0]
    assert call["server_id"] == "alice"
    from src.mcp.server_catalog import ServerStatus
    assert call["new_status"] == ServerStatus.QUARANTINED


@pytest.mark.asyncio
async def test_live_restore_calls_update_status_installed(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, cat = _make_app(entries=[_FakeServerEntry("alice", status="quarantined")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/restore",
            json={
                "confirmed": True, "confirmation_phrase": "alice",
                "server_id": "alice", "target_status": "installed",
            },
        )
    assert resp.status_code == 200
    assert len(cat.update_status_calls) == 1
    call = cat.update_status_calls[0]
    from src.mcp.server_catalog import ServerStatus
    assert call["new_status"] == ServerStatus.INSTALLED


@pytest.mark.asyncio
async def test_live_remove_calls_remove_server(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, cat = _make_app(entries=[_FakeServerEntry("alice", status="installed")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/remove",
            json={
                "confirmed": True, "confirmation_phrase": "alice",
                "server_id": "alice",
            },
        )
    assert resp.status_code == 200
    assert cat.remove_calls == ["alice"]


# ══════════════════════════════════════════════════════════════════════════════
# Section 17 — Audit UI anti-fuite
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_audit_never_contains_display_name_raw(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice",
            "display_name": "SECRET_DISPLAY_NAME_LEAK_MARKER",
            "package_spec": "npm:alice", "owner_profile": "default",
        })
    blob = json.dumps(_read_audit(audit_path))
    assert "SECRET_DISPLAY_NAME_LEAK_MARKER" not in blob
    assert "display_name" not in blob


@pytest.mark.asyncio
async def test_audit_never_contains_package_spec_full(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice",
            "display_name": "X",
            "package_spec": "npm:SECRET_PKG_NAME_LEAK_MARKER",
            "owner_profile": "default",
        })
    blob = json.dumps(_read_audit(audit_path))
    assert "SECRET_PKG_NAME_LEAK_MARKER" not in blob
    # On expose le transport seulement, pas le package
    for e in _read_audit(audit_path):
        assert "package_spec" not in e  # le champ complet jamais
        if "package_spec_transport" in e:
            assert e["package_spec_transport"] == "npm"


@pytest.mark.asyncio
async def test_audit_never_contains_version_raw(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice",
            "display_name": "X", "package_spec": "npm:x",
            "owner_profile": "default",
            "version": "SECRET_VERSION_LEAK_MARKER",
        })
    blob = json.dumps(_read_audit(audit_path))
    assert "SECRET_VERSION_LEAK_MARKER" not in blob
    assert '"version"' not in blob


@pytest.mark.asyncio
async def test_audit_never_contains_notes_raw(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice",
            "display_name": "X", "package_spec": "npm:x",
            "owner_profile": "default",
            "notes": "SECRETNOTESLEAKMARKER",
        })
    blob = json.dumps(_read_audit(audit_path))
    assert "SECRETNOTESLEAKMARKER" not in blob
    assert '"notes"' not in blob


@pytest.mark.asyncio
async def test_audit_trust_score_reduced_to_bool(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice",
            "display_name": "X", "package_spec": "npm:x",
            "owner_profile": "default",
            "trust_score": 73,  # valeur arbitraire
        })
    events = _read_audit(audit_path)
    # Au moins un event a trust_score_set=True
    has_flag = any(e.get("trust_score_set") is True for e in events)
    assert has_flag
    # Aucun event n'expose la valeur 73
    for e in events:
        # trust_score brut JAMAIS
        assert "trust_score" not in e or e.get("trust_score_set") in (True, False)
        assert 73 not in [v for v in e.values() if isinstance(v, int)]


@pytest.mark.asyncio
async def test_audit_confirmation_phrase_never_raw(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alicesecret", status="installed")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/catalog/alicesecret/quarantine",
            json={
                "confirmed": True, "confirmation_phrase": "alicesecret",
                "server_id": "alicesecret",
            },
        )
    blob = json.dumps(_read_audit(audit_path))
    assert '"confirmation_phrase"' not in blob


@pytest.mark.asyncio
async def test_audit_includes_owner_profile_and_transport(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice", "display_name": "X",
            "package_spec": "pypi:somepackage", "owner_profile": "myowner",
        })
    events = _read_audit(audit_path)
    has_owner = any(e.get("owner_profile") == "myowner" for e in events)
    has_transport = any(e.get("package_spec_transport") == "pypi" for e in events)
    assert has_owner
    assert has_transport


@pytest.mark.asyncio
async def test_audit_actor_token_hash_sha256(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.delenv("LUMENA_TEST_MODE", raising=False)
    app, _ = _make_app(entries=[_FakeServerEntry("alice", status="installed")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/catalog/alice/quarantine",
            headers={"Authorization": "Bearer some-real-token-xyz"},
            json={
                "confirmed": True, "confirmation_phrase": "alice",
                "server_id": "alice",
            },
        )
    blob = json.dumps(_read_audit(audit_path))
    assert "some-real-token-xyz" not in blob
    for e in _read_audit(audit_path):
        if "actor_token_hash" in e:
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", e["actor_token_hash"])


# ══════════════════════════════════════════════════════════════════════════════
# Section 18 — Anti-fuite ServerEntry brut dans la réponse
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_response_never_contains_server_entry_raw_in_add(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/add", json={
            "confirmed": True, "server_id": "alice", "display_name": "X",
            "package_spec": "npm:x", "owner_profile": "default",
        })
    d = resp.json()
    allowed = {"added", "server_id", "status", "live_mode"}
    assert set(d.keys()) <= allowed


@pytest.mark.asyncio
async def test_response_never_contains_server_entry_raw_in_quarantine(
    monkeypatch, tmp_path
):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alice", status="installed")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/quarantine",
            json={
                "confirmed": True, "confirmation_phrase": "alice",
                "server_id": "alice",
            },
        )
    d = resp.json()
    allowed = {"quarantined", "server_id", "status", "live_mode"}
    assert set(d.keys()) <= allowed


@pytest.mark.asyncio
async def test_response_never_contains_secret_markers(monkeypatch, tmp_path):
    """ServerEntry contient SECRET_*_LEAK markers, jamais dans la réponse."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alice", status="installed")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/quarantine",
            json={
                "confirmed": True, "confirmation_phrase": "alice",
                "server_id": "alice",
            },
        )
    blob = resp.text
    assert "SECRET_VERSION_LEAK" not in blob
    assert "SECRET_NOTES_LEAK" not in blob
    assert "SECRET_DISPLAY_LEAK" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 19 — Aucun subprocess / runner / activation / queue / marker dans handlers
# ══════════════════════════════════════════════════════════════════════════════


def _find_function_body(source: str, fn_name: str) -> Optional[str]:
    pattern = re.compile(
        rf"^(?:async\s+def|def)\s+{re.escape(fn_name)}\s*\(", re.MULTILINE
    )
    m = pattern.search(source)
    if not m:
        return None
    start = m.start()
    rest = source[m.end():]
    next_match = re.search(
        r"^(?:async\s+def|def|@router\.|@app\.)", rest, re.MULTILINE
    )
    end = m.end() + (next_match.start() if next_match else len(rest))
    return source[start:end]


def test_catalog_handlers_do_not_use_marker_or_queue():
    """Aucun handler Catalog n'utilise _take_marker / _put_marker / ApprovalQueue."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    for fn in (
        "mcp_catalog_add",
        "mcp_catalog_quarantine",
        "mcp_catalog_restore",
        "mcp_catalog_remove",
    ):
        body = _find_function_body(text, fn)
        assert body is not None, f"Handler {fn} introuvable"
        for forbidden in (
            "_take_marker",
            "_put_marker",
            ".propose(",
            ".approve(",
            ".reject(",
            ".propose_install(",
            ".execute_approved_install(",
            ".propose_activation(",
            ".activate(",
            ".deactivate(",
            "MCPSandboxRunner",
            "MCPInstallOrchestrator(",
            "MCPActivationService(",
        ):
            assert forbidden not in body, (
                f"{forbidden} trouvé dans handler {fn} (interdit Phase 20B-4)"
            )


def test_catalog_handlers_do_not_instantiate_fresh_catalog():
    """Aucun handler Catalog ne fait MCPServerCatalog() neuf."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    for fn in (
        "mcp_catalog_add",
        "mcp_catalog_quarantine",
        "mcp_catalog_restore",
        "mcp_catalog_remove",
    ):
        body = _find_function_body(text, fn)
        assert body is not None
        assert "MCPServerCatalog()" not in body


def test_mcp_py_does_not_import_phase14_private_validators():
    """Production : pas d'import des helpers privés Phase 14."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    for token in (
        "from src.mcp.server_catalog import _validate_server_id",
        "from src.mcp.server_catalog import _validate_display_name",
        "from src.mcp.server_catalog import _validate_package_spec",
        "from src.mcp.server_catalog import _validate_owner_profile",
        "from src.mcp.server_catalog import _validate_version",
        "from src.mcp.server_catalog import _validate_trust_score",
        "from src.mcp.server_catalog import _validate_notes",
    ):
        assert token not in text


# ══════════════════════════════════════════════════════════════════════════════
# Section 20 — Test de cohérence Phase 14 (import privé EN TEST uniquement)
# ══════════════════════════════════════════════════════════════════════════════


def test_validation_consistency_with_phase14_helpers():
    """Compare les comportements web/routes/mcp.py vs Phase 14 sur un set commun.
    Import privé autorisé EN TEST uniquement.
    """
    from web.routes import mcp as mcp_routes
    try:
        from src.mcp.server_catalog import (
            _validate_display_name as _ph14_dn,
            _validate_package_spec as _ph14_pkg,
            _validate_owner_profile as _ph14_owner,
            _validate_version as _ph14_ver,
            _validate_trust_score as _ph14_ts,
            _validate_notes as _ph14_notes,
            CatalogError,
        )
    except Exception:
        pytest.skip("Phase 14 helpers indisponibles")
    from fastapi import HTTPException

    def _ph14_valid(fn, raw):
        try:
            fn(raw)
            return True
        except CatalogError:
            return False
        except Exception:
            return False

    def _web_valid(fn, raw):
        try:
            fn(raw)
            return True
        except HTTPException:
            return False

    cases = [
        ("display_name", "valid", True),
        ("display_name", "", False),
        ("display_name", "x" * 201, False),
        ("display_name", "with\x00null", False),
        ("package_spec", "npm:my-pkg", True),
        ("package_spec", "pypi:my_pkg", True),
        ("package_spec", "local:my-slug", True),
        ("package_spec", "foobar:x", False),
        ("package_spec", "", False),
        ("owner_profile", "valid", True),
        ("owner_profile", "Has-Upper", False),
        ("owner_profile", "", False),
        ("version", None, True),
        ("version", "1.0.0", True),
        ("version", "with space", False),
        ("trust_score", None, True),
        ("trust_score", 50, True),
        ("trust_score", -1, False),
        ("trust_score", 101, False),
        ("trust_score", True, False),
        ("trust_score", 80.0, False),
        ("notes", None, True),
        ("notes", "ok note", True),
        ("notes", "héro", False),
    ]
    web_fns = {
        "display_name": mcp_routes._validate_display_name_format,
        "package_spec": mcp_routes._validate_package_spec_format,
        "owner_profile": mcp_routes._validate_owner_profile_format,
        "version": mcp_routes._validate_version_format,
        "trust_score": mcp_routes._validate_trust_score_format,
        "notes": mcp_routes._validate_notes_format,
    }
    ph14_fns = {
        "display_name": _ph14_dn, "package_spec": _ph14_pkg,
        "owner_profile": _ph14_owner, "version": _ph14_ver,
        "trust_score": _ph14_ts, "notes": _ph14_notes,
    }
    for field, raw, expected in cases:
        web_ok = _web_valid(web_fns[field], raw)
        ph14_ok = _ph14_valid(ph14_fns[field], raw)
        assert web_ok == ph14_ok == expected, (
            f"Cohérence cassée pour {field}={raw!r} : "
            f"web={web_ok}, ph14={ph14_ok}, expected={expected}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section 21 — Rate limit
# ══════════════════════════════════════════════════════════════════════════════


def test_rate_limit_catalog_prefix_marked_expensive():
    text = _SERVER_PY_PATH.read_text(encoding="utf-8")
    assert "/api/mcp/catalog/" in text
    m = re.search(r"_EXPENSIVE_PREFIXES\s*=\s*\(([^)]+)\)", text, re.DOTALL)
    assert m
    assert "/api/mcp/catalog/" in m.group(1)


# ══════════════════════════════════════════════════════════════════════════════
# Section 22 — Health phase 20B-4
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_health_phase_at_least_20b4(monkeypatch):
    """Phase ≥ 20B-4 (élargie pour 20B-5+)."""
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    d = resp.json()
    assert d["phase"] in ("20B-4", "20B-5", "20B-6", "21")


# ══════════════════════════════════════════════════════════════════════════════
# Section 23 — JS smoke
# ══════════════════════════════════════════════════════════════════════════════


def test_panels_js_has_catalog_add_modal_and_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "openMcpCatalogAddModal" in text
    assert "submitMcpCatalogAdd" in text


def test_panels_js_has_catalog_quarantine_modal_and_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "openMcpCatalogQuarantineModal" in text
    assert "submitMcpCatalogQuarantine" in text


def test_panels_js_has_catalog_restore_modal_and_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "openMcpCatalogRestoreModal" in text
    assert "submitMcpCatalogRestore" in text


def test_panels_js_has_catalog_remove_modal_and_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "openMcpCatalogRemoveModal" in text
    assert "submitMcpCatalogRemove" in text


def test_panels_js_add_server_header_button():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "openMcpCatalogAddModal()" in text


def test_panels_js_no_local_storage_in_catalog_section():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"Phase 20B-4[\s\S]*?(?=Phase 20B-5|/\* =====|window\.loadMcp\s*$|\Z)",
        text,
    )
    if m:
        section = m.group(0)
        assert "localStorage" not in section
