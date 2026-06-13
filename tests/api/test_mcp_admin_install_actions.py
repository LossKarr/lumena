"""
Tests Phase 20B-2 — Install lifecycle UI mutations (propose / execute).

Couverture obligatoire :
  - Auth (401/403)
  - Confirmation côté backend (confirmed:true requis)
  - caller_kind whitelist = {"admin_ui"}
  - confirmation_phrase = server_id exact (case-sensitive)
  - validation server_id : réplique Phase 14 + Windows reserved
  - validation marker : regex UUID4 hex 32
  - Catalog / Queue / Orchestrator singletons (unavailable / publics uniquement)
  - dry-run STRICT : ZERO call _take_marker / execute_approved_install
  - live propose : appelle orchestrator.propose_install
  - live execute : _take_marker one-shot AVANT execute_approved_install
  - marker consommé reste consommé même si install échoue
  - validation croisée args["server_id"] == body.server_id
  - audit UI étendu sans fuite package_spec/version/notes/args/phrase raw/marker raw
  - InstallResult brut JAMAIS exposé
  - Aucun subprocess direct dans web/routes/mcp.py
  - Aucun "mcp.install" heuristique (production uniquement)
  - Pattern install réel = "mcp_install:<server_id>"
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
_DEPS_PATH = _REPO_ROOT / "web" / "routes" / "deps.py"
_LIFESPAN_PATH = _REPO_ROOT / "web" / "routes" / "lifespan.py"


# ──────────────────────────────────────────────────────────────────────────────
# Fakes Phase 14 / 18
# ──────────────────────────────────────────────────────────────────────────────


class _FakeStatus:
    def __init__(self, value: str):
        self.value = value


class _FakeServerEntry:
    def __init__(self, server_id: str, status: str = "declared"):
        self.server_id = server_id
        self.status = _FakeStatus(status)
        self.display_name = server_id
        self.package_spec = f"npm:secret-pkg-LEAK-{server_id}"
        self.version = f"SECRET_VERSION_LEAK_{server_id}"
        self.notes = f"SECRET_NOTES_LEAK_{server_id}"
        self.owner_profile = "test"
        self.trust_score = 80
        self.added_at = "2026-06-01T00:00:00Z"
        self.updated_at = "2026-06-01T00:00:00Z"
        self.last_active_at = None


class _FakeCatalog:
    def __init__(self, entries: Optional[List[_FakeServerEntry]] = None):
        self._entries = {e.server_id: e for e in (entries or [])}
        # spy
        self.add_server_calls: List[Dict[str, Any]] = []
        self.update_status_calls: List[Dict[str, Any]] = []

    def get_server(self, server_id: str) -> Optional[_FakeServerEntry]:
        return self._entries.get(server_id)

    def list_servers(self, **kwargs):
        return list(self._entries.values())

    def set_status(self, server_id: str, status: str) -> None:
        if server_id in self._entries:
            self._entries[server_id].status = _FakeStatus(status)


class _FakeInstallProposal:
    def __init__(self, ticket_id: str, server_id: str):
        self.approval_ticket_id = ticket_id
        self.server_id = server_id


class _FakeInstallResult:
    def __init__(self, server_id: str, success: bool, dry_run: bool = False):
        self.server_id = server_id
        self.success = success
        self.dry_run = dry_run
        self.reason = "ok" if success else "SECRET_INSTALL_RESULT_REASON_LEAK"
        self.target_path_relative = "SECRET_PATH_LEAK"
        self.duration_s = 0.42


class _FakeApprovalResult:
    """Stand-in pour ApprovalResult avec args["server_id"]."""

    def __init__(self, server_id: str, decision_value: str = "APPROVED"):
        self.decision = _FakeDecision(decision_value)
        self.args = {
            "server_id": server_id,
            "secret_args_marker": f"SHOULD_NEVER_LEAK_{server_id}",
        }
        self.reason = None


class _FakeDecision:
    def __init__(self, value: str):
        self.value = value


class _FakePendingAction:
    def __init__(self, action_id: str):
        self.id = action_id
        self.tool_name = "fake.tool"
        self.policy = None
        self.caller_kind = "test"
        self.risk_summary = ""
        self.proposed_at = "2026-06-01T00:00:00Z"


class _FakeApprovalQueue:
    def __init__(self, pending_ids: Optional[List[str]] = None):
        self._pending = [_FakePendingAction(a) for a in (pending_ids or [])]
        self.propose_calls: List[Dict[str, Any]] = []
        self.approve_calls: List[str] = []
        self.reject_calls: List[Dict[str, Any]] = []

    def list_pending(self):
        return list(self._pending)

    def approve(self, action_id):
        self.approve_calls.append(action_id)
        return _FakeApprovalResult(server_id="alice")

    def reject(self, action_id, reason):
        self.reject_calls.append({"action_id": action_id, "reason": reason})
        return True


class _FakeInstallOrchestrator:
    """Spy compatible MCPInstallOrchestrator (signature Phase 18)."""

    def __init__(
        self,
        catalog: Any = None,
        approval_queue: Any = None,
        dry_run: bool = True,
    ):
        self._catalog = catalog
        self._approval_queue = approval_queue
        self._dry_run = bool(dry_run)
        # Spy
        self.propose_calls: List[Dict[str, Any]] = []
        self.execute_calls: List[Dict[str, Any]] = []
        self.execute_should_raise = False
        self.execute_should_return_failure = False
        # Reçus pour vérification
        self.last_catalog_received = catalog
        self.last_queue_received = approval_queue

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def propose_install(self, server_id, *, caller_kind):
        self.propose_calls.append(
            {"server_id": server_id, "caller_kind": caller_kind}
        )
        if self._dry_run:
            return _FakeInstallProposal(
                ticket_id="dry" + uuid.uuid4().hex[3:], server_id=server_id
            )
        return _FakeInstallProposal(
            ticket_id=uuid.uuid4().hex, server_id=server_id
        )

    def execute_approved_install(self, server_id, approval_result):
        self.execute_calls.append({
            "server_id": server_id,
            "approval_result": approval_result,
        })
        if self.execute_should_raise:
            raise RuntimeError("boom-install-raw-message-LEAK")
        if self.execute_should_return_failure:
            return _FakeInstallResult(server_id=server_id, success=False)
        # Success : mute le catalog vers INSTALLED
        try:
            self._catalog.set_status(server_id, "installed")
        except Exception:
            pass
        return _FakeInstallResult(server_id=server_id, success=True)


# ──────────────────────────────────────────────────────────────────────────────
# App / fixtures
# ──────────────────────────────────────────────────────────────────────────────


def _new_action_id() -> str:
    return uuid.uuid4().hex


def _new_marker() -> str:
    return uuid.uuid4().hex


def _make_app(
    *,
    declared_servers: Optional[List[str]] = None,
    installed_servers: Optional[List[str]] = None,
    pending_ids: Optional[List[str]] = None,
    orchestrator: Optional[_FakeInstallOrchestrator] = None,
    with_auth_override: bool = True,
    no_catalog: bool = False,
    no_queue: bool = False,
    no_orchestrator: bool = False,
) -> tuple[FastAPI, _FakeCatalog, _FakeApprovalQueue, _FakeInstallOrchestrator]:
    from web.routes import deps, mcp as mcp_routes

    entries = []
    for sid in declared_servers or []:
        entries.append(_FakeServerEntry(sid, status="declared"))
    for sid in installed_servers or []:
        entries.append(_FakeServerEntry(sid, status="installed"))
    catalog = _FakeCatalog(entries=entries)
    queue = _FakeApprovalQueue(pending_ids=pending_ids or [])
    orch = orchestrator or _FakeInstallOrchestrator(
        catalog=catalog, approval_queue=queue, dry_run=False
    )

    deps._MCP_SERVER_CATALOG_SINGLETON = None if no_catalog else catalog
    deps._MCP_APPROVAL_QUEUE_SINGLETON = None if no_queue else queue
    deps._MCP_INSTALL_ORCHESTRATOR_SINGLETON = None if no_orchestrator else orch

    mcp_routes._APPROVAL_RESULT_CACHE.clear()

    app = FastAPI()
    app.include_router(mcp_routes.router)
    if with_auth_override:
        app.dependency_overrides[deps.verify_admin_token] = lambda: None
    return app, catalog, queue, orch


def _clear_audit_path(monkeypatch, tmp_path):
    from web.routes import mcp as mcp_routes

    target = tmp_path / "mcp_admin_audit" / "audit.jsonl"

    def _patched_path():
        return target

    monkeypatch.setattr(mcp_routes, "_ui_audit_path", _patched_path)
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


_INSTALL_PATHS = ["/api/mcp/install/propose", "/api/mcp/install/execute"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _INSTALL_PATHS)
async def test_install_route_requires_admin_token_401(path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app, *_ = _make_app(declared_servers=["alice"], with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(path, json={"confirmed": True})
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _INSTALL_PATHS)
async def test_install_route_bad_token_403(path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app, *_ = _make_app(declared_servers=["alice"], with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            path,
            headers={"Authorization": "Bearer wrong"},
            json={"confirmed": True},
        )
    assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Confirmation backend
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_propose_without_confirmed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/propose",
            json={"server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_required"


@pytest.mark.asyncio
async def test_execute_without_confirmed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": _new_marker(),
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_required"


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — caller_kind whitelist
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cli", "discovery", "agent", "", None, "ADMIN_UI"])
async def test_propose_caller_kind_not_admin_ui_rejected(monkeypatch, tmp_path, kind):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _cat, _q, orch = _make_app(declared_servers=["alice"])
    payload = {"confirmed": True, "server_id": "alice"}
    if kind is not None:
        payload["caller_kind"] = kind
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/install/propose", json=payload)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "caller_kind_invalid"
    assert orch.propose_calls == []


@pytest.mark.asyncio
async def test_propose_caller_kind_admin_ui_accepted_in_dry_run(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 200
    assert resp.json()["live_mode"] is False


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — confirmation_phrase = server_id exact (case-sensitive)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_execute_confirmation_phrase_must_match_server_id_exact(
    monkeypatch, tmp_path
):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "ALICE",  # case mismatch
                "marker": _new_marker(),
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_phrase_invalid"


@pytest.mark.asyncio
async def test_execute_confirmation_phrase_wrong_value_rejected(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "bob",
                "marker": _new_marker(),
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_phrase_invalid"


@pytest.mark.asyncio
async def test_execute_confirmation_phrase_missing_rejected(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "marker": _new_marker(),
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_phrase_invalid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Validation server_id (réplique Phase 14 + Windows)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "valid_sid", ["alice", "my.server", "my-server", "my_server", "3alpha", "a", "com10"]
)
async def test_server_id_phase14_valid(monkeypatch, tmp_path, valid_sid):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app, *_ = _make_app(declared_servers=[valid_sid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": valid_sid, "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_sid",
    [
        "Alice",         # uppercase
        ".hidden",       # dot start
        "-foo",          # dash start
        "_under",        # underscore start
        "a" * 65,        # too long
        "",              # empty
        "..",            # path traversal
        "a/b",           # slash
        "a\\b",          # backslash
        "a b",           # space
        "alice!",        # special char
    ],
)
async def test_server_id_phase14_invalid_format(monkeypatch, tmp_path, invalid_sid):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": invalid_sid, "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "server_id_invalid_format"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "windows_sid",
    [
        "con", "prn", "aux", "nul",
        "com1", "com5", "com9",
        "lpt1", "lpt5", "lpt9",
        "con.txt", "aux.log", "nul.json", "com1.foo.bar",
    ],
)
async def test_server_id_windows_reserved_rejected(monkeypatch, tmp_path, windows_sid):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": windows_sid, "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "server_id_invalid_format"


def test_server_id_validation_matches_phase14_behavior():
    """Cohérence : notre _validate_server_id_format ↔ Phase 14 _validate_server_id.

    Le test peut importer le helper privé Phase 14 (cas autorisé en test).
    En production, web/routes/mcp.py n'importe PAS ce helper.
    """
    from web.routes import mcp as mcp_routes
    try:
        from src.mcp.server_catalog import _validate_server_id as _phase14_validate
        from src.mcp.server_catalog import CatalogError
    except Exception:
        pytest.skip("Phase 14 helpers not importable")

    cases = [
        ("alice", True),
        ("my.server", True),
        ("my-server", True),
        ("3alpha", True),
        ("com10", True),
        ("Alice", False),
        (".hidden", False),
        ("-foo", False),
        ("a" * 65, False),
        ("", False),
        ("..", False),
        ("a/b", False),
        ("con", False),
        ("con.txt", False),
        ("com1", False),
        ("lpt9", False),
    ]
    for sid, expected_valid in cases:
        # Phase 14 : raise CatalogError si invalide, sinon return None
        phase14_valid = True
        try:
            _phase14_validate(sid)
        except CatalogError:
            phase14_valid = False
        except Exception:
            phase14_valid = False

        # 20B-2 : raise HTTPException 400 si invalide, sinon return str
        from fastapi import HTTPException
        web_valid = True
        try:
            mcp_routes._validate_server_id_format(sid)
        except HTTPException:
            web_valid = False

        assert phase14_valid == web_valid == expected_valid, (
            f"Cohérence cassée pour '{sid}': "
            f"phase14={phase14_valid}, web={web_valid}, expected={expected_valid}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Marker validation format
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_marker",
    ["", "abc", "X" * 32, uuid.uuid4().hex.upper(), uuid.uuid4().hex + "x", "0" * 31, "g" * 32],
)
async def test_execute_marker_invalid_format_400(monkeypatch, tmp_path, bad_marker):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": bad_marker,
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "marker_invalid_format"


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — Catalog / Queue / Orchestrator unavailable
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_propose_catalog_unavailable_503(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(no_catalog=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 503
    assert resp.json().get("detail", {}).get("error_code") == "catalog_unavailable"


@pytest.mark.asyncio
async def test_propose_queue_unavailable_503(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(declared_servers=["alice"], no_queue=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 503
    assert resp.json().get("detail", {}).get("error_code") == "queue_unavailable"


@pytest.mark.asyncio
async def test_install_propose_queue_singleton_missing_returns_503_and_does_not_build_orchestrator(
    monkeypatch, tmp_path
):
    """Phase 20B-2 : aucun fallback _get_approval_queue() côté install.

    Si le singleton ApprovalQueue manque, on retourne 503 AVANT de construire
    l'orchestrator. Vérifie aussi qu'aucune instanciation ad-hoc d'ApprovalQueue
    n'a eu lieu (cf. correction utilisateur).
    """
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    build_spy = {"calls": 0}
    real_build = mcp_routes._build_install_orchestrator

    def _spy_build(*a, **kw):
        build_spy["calls"] += 1
        return real_build(*a, **kw)

    monkeypatch.setattr(mcp_routes, "_build_install_orchestrator", _spy_build)

    fallback_spy = {"calls": 0}

    def _spy_fallback():
        fallback_spy["calls"] += 1
        return _FakeApprovalQueue(pending_ids=[])

    monkeypatch.setattr(mcp_routes, "_get_approval_queue", _spy_fallback)

    app, *_ = _make_app(declared_servers=["alice"], no_queue=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 503
    assert resp.json().get("detail", {}).get("error_code") == "queue_unavailable"
    assert build_spy["calls"] == 0, "Orchestrator ne doit pas être construit"
    assert fallback_spy["calls"] == 0, (
        "Aucun fallback _get_approval_queue() en Phase 20B-2 install"
    )


@pytest.mark.asyncio
async def test_propose_orchestrator_unavailable_503(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(declared_servers=["alice"], no_orchestrator=True)
    # Force aussi la factory à retourner None
    monkeypatch.setattr(
        mcp_routes, "_build_install_orchestrator", lambda *a, **kw: None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 503
    assert resp.json().get("detail", {}).get("error_code") == "orchestrator_unavailable"


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — server_not_found / status checks
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_propose_server_not_found_404(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": "bob", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 404
    assert resp.json().get("detail", {}).get("error_code") == "server_not_found"


@pytest.mark.asyncio
async def test_propose_server_not_declared_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(installed_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "server_id_not_declared"


@pytest.mark.asyncio
async def test_install_execute_queue_singleton_missing_returns_503_and_does_not_take_marker(
    monkeypatch, tmp_path
):
    """Phase 20B-2 : aucun fallback _get_approval_queue() côté execute.

    Si le singleton ApprovalQueue manque, 503 AVANT toute consommation marker.
    Le cache marker reste intact (l'admin pourra réessayer plus tard).
    """
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    take_spy = {"calls": 0}
    real_take = mcp_routes._take_marker

    def _spy_take(marker):
        take_spy["calls"] += 1
        return real_take(marker)

    monkeypatch.setattr(mcp_routes, "_take_marker", _spy_take)

    fallback_spy = {"calls": 0}

    def _spy_fallback():
        fallback_spy["calls"] += 1
        return _FakeApprovalQueue(pending_ids=[])

    monkeypatch.setattr(mcp_routes, "_get_approval_queue", _spy_fallback)

    app, *_ = _make_app(declared_servers=["alice"], no_queue=True)
    # Place un marker dans le cache pour vérifier qu'il n'est pas consommé
    marker = mcp_routes._put_marker("alice_action", _FakeApprovalResult("alice"))
    cache_size_before = len(mcp_routes._APPROVAL_RESULT_CACHE)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": marker,
            },
        )
    assert resp.status_code == 503
    assert resp.json().get("detail", {}).get("error_code") == "queue_unavailable"
    assert take_spy["calls"] == 0, "_take_marker ne doit PAS être appelé"
    assert fallback_spy["calls"] == 0, (
        "Aucun fallback _get_approval_queue() en Phase 20B-2 install"
    )
    # Le marker reste dans le cache : l'admin pourra réessayer
    assert len(mcp_routes._APPROVAL_RESULT_CACHE) == cache_size_before
    assert marker in mcp_routes._APPROVAL_RESULT_CACHE


@pytest.mark.asyncio
async def test_execute_server_already_installed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, *_ = _make_app(installed_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": _new_marker(),
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "server_already_installed"


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — Dry-run propose (kill switch)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dry_run_propose_does_not_call_orchestrator(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app, _cat, _q, orch = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["would_propose"] is True
    assert d["live_mode"] is False
    assert d["forced_dry_run"] is True
    assert orch.propose_calls == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — Dry-run execute STRICT (correction critique)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dry_run_execute_does_not_call_take_marker(monkeypatch, tmp_path):
    """Phase 20B-2 v4 : dry-run execute ne touche JAMAIS le cache marker."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    from web.routes import mcp as mcp_routes

    spy = {"calls": 0}
    real_take = mcp_routes._take_marker

    def _spy_take(marker):
        spy["calls"] += 1
        return real_take(marker)

    monkeypatch.setattr(mcp_routes, "_take_marker", _spy_take)

    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": _new_marker(),
            },
        )
    assert resp.status_code == 200
    assert spy["calls"] == 0, "dry-run execute ne doit PAS appeler _take_marker"


@pytest.mark.asyncio
async def test_dry_run_execute_does_not_call_orchestrator_execute(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app, _cat, _q, orch = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": _new_marker(),
            },
        )
    assert resp.status_code == 200
    assert orch.execute_calls == []


@pytest.mark.asyncio
async def test_dry_run_execute_marker_still_in_cache(monkeypatch, tmp_path):
    """Si on met un marker en cache puis on appelle execute dry-run, le marker reste."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(declared_servers=["alice"])
    # Place un marker dans le cache
    result = _FakeApprovalResult(server_id="alice")
    marker = mcp_routes._put_marker("alice_action", result)
    cache_size_before = len(mcp_routes._APPROVAL_RESULT_CACHE)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": marker,
            },
        )
    # Le cache n'a pas été touché
    assert len(mcp_routes._APPROVAL_RESULT_CACHE) == cache_size_before


@pytest.mark.asyncio
async def test_dry_run_execute_returns_whitelist(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": _new_marker(),
            },
        )
    assert resp.status_code == 200
    d = resp.json()
    allowed = {"would_execute", "server_id", "live_mode", "forced_dry_run"}
    assert set(d.keys()) <= allowed


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — Live propose : crée ticket
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_live_propose_calls_orchestrator_once(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _cat, _q, orch = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["proposed"] is True
    assert isinstance(d["ticket_id"], str) and len(d["ticket_id"]) == 32
    assert d["server_id"] == "alice"
    assert d["live_mode"] is True
    assert len(orch.propose_calls) == 1
    assert orch.propose_calls[0]["server_id"] == "alice"
    assert orch.propose_calls[0]["caller_kind"] == "admin_ui"


@pytest.mark.asyncio
async def test_live_propose_response_whitelist(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    d = resp.json()
    allowed = {"proposed", "ticket_id", "server_id", "live_mode"}
    assert set(d.keys()) <= allowed


# ══════════════════════════════════════════════════════════════════════════════
# Section 12 — Live execute : _take_marker AVANT execute_approved_install
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_live_execute_calls_take_marker_exactly_once(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    spy = {"calls": 0}
    real_take = mcp_routes._take_marker

    def _spy_take(marker):
        spy["calls"] += 1
        return real_take(marker)

    monkeypatch.setattr(mcp_routes, "_take_marker", _spy_take)

    app, _cat, _q, orch = _make_app(declared_servers=["alice"])
    result = _FakeApprovalResult(server_id="alice")
    marker = mcp_routes._put_marker("alice_action", result)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": marker,
            },
        )
    assert resp.status_code == 200, resp.text
    assert spy["calls"] == 1
    assert len(orch.execute_calls) == 1


@pytest.mark.asyncio
async def test_live_execute_orchestrator_receives_real_approval_result(
    monkeypatch, tmp_path
):
    """L'orchestrator doit recevoir le MÊME objet ApprovalResult que celui
    stocké dans le cache (pas une copie sérialisée)."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, _cat, _q, orch = _make_app(declared_servers=["alice"])
    result = _FakeApprovalResult(server_id="alice")
    marker = mcp_routes._put_marker("alice_action", result)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": marker,
            },
        )
    assert resp.status_code == 200
    assert orch.execute_calls[0]["approval_result"] is result


@pytest.mark.asyncio
async def test_live_execute_response_whitelist(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(declared_servers=["alice"])
    marker = mcp_routes._put_marker("alice_action", _FakeApprovalResult("alice"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": marker,
            },
        )
    assert resp.status_code == 200
    d = resp.json()
    allowed = {"executed", "server_id", "status", "live_mode"}
    assert set(d.keys()) <= allowed
    assert d["executed"] is True


# ══════════════════════════════════════════════════════════════════════════════
# Section 13 — Marker mismatch / not found / consumed irrecoverable
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_execute_marker_not_found_404(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": _new_marker(),  # marker non stocké
            },
        )
    assert resp.status_code == 404
    assert resp.json().get("detail", {}).get("error_code") == "marker_not_found_or_expired"


@pytest.mark.asyncio
async def test_execute_marker_server_id_mismatch_400(monkeypatch, tmp_path):
    """Marker valide mais args["server_id"] != body.server_id → 400 + marker consommé."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(declared_servers=["alice", "bob"])
    result = _FakeApprovalResult(server_id="bob")  # le marker est pour bob
    marker = mcp_routes._put_marker("some_action", result)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",  # mismatch
                "confirmation_phrase": "alice",
                "marker": marker,
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "marker_server_id_mismatch"
    # Marker consommé : pas dans le cache
    assert marker not in mcp_routes._APPROVAL_RESULT_CACHE


@pytest.mark.asyncio
async def test_live_execute_marker_consumed_irrecoverable_on_failure(
    monkeypatch, tmp_path
):
    """Même si install échoue, le marker reste consommé (one-shot strict)."""
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    orch = _FakeInstallOrchestrator(dry_run=False)
    orch.execute_should_return_failure = True
    app, *_ = _make_app(declared_servers=["alice"], orchestrator=orch)
    marker = mcp_routes._put_marker("alice_action", _FakeApprovalResult("alice"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": marker,
            },
        )
    assert resp.status_code == 500
    # Marker consommé même en échec
    assert marker not in mcp_routes._APPROVAL_RESULT_CACHE
    events = _read_audit(audit_path)
    failed = [e for e in events if e["event"] == "ui_action_failed"]
    assert any(e.get("marker_consumed_irrecoverable") is True for e in failed)


@pytest.mark.asyncio
async def test_live_execute_orchestrator_raises_audit_consumed_irrecoverable(
    monkeypatch, tmp_path
):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    orch = _FakeInstallOrchestrator(dry_run=False)
    orch.execute_should_raise = True
    app, *_ = _make_app(declared_servers=["alice"], orchestrator=orch)
    marker = mcp_routes._put_marker("alice_action", _FakeApprovalResult("alice"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": marker,
            },
        )
    assert resp.status_code == 500
    body = resp.json()
    # Aucun message brut
    blob = json.dumps(body)
    assert "boom-install-raw-message-LEAK" not in blob
    events = _read_audit(audit_path)
    audit_blob = json.dumps(events)
    assert "boom-install-raw-message-LEAK" not in audit_blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 14 — Audit UI étendu (whitelist stricte)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_audit_install_propose_events(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    events = _read_audit(audit_path)
    kinds = [e["event"] for e in events]
    assert "ui_action_requested" in kinds
    assert "ui_action_completed" in kinds
    for e in events:
        assert e["phase"] == "20B-2"
        assert e["action"] == "install_propose"
        assert e["target_server_id"] == "alice"


@pytest.mark.asyncio
async def test_audit_never_contains_package_spec(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(declared_servers=["alice"])
    marker = mcp_routes._put_marker("a", _FakeApprovalResult("alice"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
        await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": marker,
            },
        )
    blob = json.dumps(_read_audit(audit_path))
    assert "secret-pkg-LEAK" not in blob
    assert "package_spec" not in blob


@pytest.mark.asyncio
async def test_audit_never_contains_version(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    blob = json.dumps(_read_audit(audit_path))
    assert "SECRET_VERSION_LEAK" not in blob
    assert '"version"' not in blob


@pytest.mark.asyncio
async def test_audit_never_contains_notes(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/install/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    blob = json.dumps(_read_audit(audit_path))
    assert "SECRET_NOTES_LEAK" not in blob
    assert '"notes"' not in blob


@pytest.mark.asyncio
async def test_audit_never_contains_args(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(declared_servers=["alice"])
    marker = mcp_routes._put_marker("a", _FakeApprovalResult("alice"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": marker,
            },
        )
    blob = json.dumps(_read_audit(audit_path))
    assert "secret_args_marker" not in blob
    assert "SHOULD_NEVER_LEAK" not in blob


@pytest.mark.asyncio
async def test_audit_never_contains_confirmation_phrase_raw(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    sid = "alicesecret"  # confirmation_phrase = server_id
    app, *_ = _make_app(declared_servers=[sid])
    marker = mcp_routes._put_marker("a", _FakeApprovalResult(sid))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": sid,
                "confirmation_phrase": sid,
                "marker": marker,
            },
        )
    blob = json.dumps(_read_audit(audit_path))
    # Le server_id apparaît (target_server_id), c'est normal. Ce qu'on
    # vérifie ici c'est l'absence du champ confirmation_phrase brut.
    assert '"confirmation_phrase"' not in blob


@pytest.mark.asyncio
async def test_audit_never_contains_marker_raw(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(declared_servers=["alice"])
    secret_marker = mcp_routes._put_marker("a", _FakeApprovalResult("alice"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": secret_marker,
            },
        )
    blob = json.dumps(_read_audit(audit_path))
    assert secret_marker not in blob


@pytest.mark.asyncio
async def test_audit_actor_token_hash_sha256_format(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.delenv("LUMENA_TEST_MODE", raising=False)
    app, *_ = _make_app(declared_servers=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/install/propose",
            headers={"Authorization": "Bearer some-real-token-xyz"},
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    blob = json.dumps(_read_audit(audit_path))
    assert "some-real-token-xyz" not in blob
    for e in _read_audit(audit_path):
        if "actor_token_hash" in e:
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", e["actor_token_hash"])


# ══════════════════════════════════════════════════════════════════════════════
# Section 15 — Anti-fuite InstallResult
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_install_result_never_exposed_in_response(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(declared_servers=["alice"])
    marker = mcp_routes._put_marker("a", _FakeApprovalResult("alice"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/install/execute",
            json={
                "confirmed": True,
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": marker,
            },
        )
    blob = resp.text
    assert "SECRET_PATH_LEAK" not in blob
    assert "target_path_relative" not in blob
    assert "duration_s" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 16 — Aucun subprocess direct, aucun "mcp.install" heuristique
# ══════════════════════════════════════════════════════════════════════════════


def test_web_routes_mcp_does_not_import_subprocess():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert "import subprocess" not in text
    assert "from subprocess import" not in text


def test_web_routes_mcp_has_no_subprocess_popen_call():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert "subprocess.Popen" not in text


def test_no_mcp_dot_install_in_production_backend():
    """Grep négatif PRODUCTION uniquement (tests autorisés à utiliser
    le mauvais pattern comme cas négatif).

    Cible : pattern littéral string `"mcp.install"` ou `'mcp.install'`
    (ce qui correspondrait à une heuristique erronée). L'import légitime
    `src.mcp.install_orchestrator` contient `mcp.install` comme sous-chaîne
    de path et ne doit pas matcher.
    """
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    forbidden_patterns = [
        '"mcp.install"',
        "'mcp.install'",
        '"mcp.install:"',
        "'mcp.install:'",
    ]
    for token in forbidden_patterns:
        assert token not in text, (
            f"{token} trouvé dans mcp.py — utiliser 'mcp_install:' "
            "(pattern Phase 18 réel)"
        )


def test_no_mcp_dot_install_in_production_panels_js():
    """Grep négatif PRODUCTION uniquement (panels.js)."""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    forbidden_patterns = [
        '"mcp.install"',
        "'mcp.install'",
        '"mcp.install:"',
        "'mcp.install:'",
    ]
    for token in forbidden_patterns:
        assert token not in text, (
            f"{token} trouvé dans panels.js — utiliser 'mcp_install:' "
            "(pattern Phase 18 réel)"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section 17 — Pattern install réel Phase 18
# ══════════════════════════════════════════════════════════════════════════════


def test_install_tool_prefix_matches_phase18_contract():
    from web.routes import mcp as mcp_routes

    assert mcp_routes._INSTALL_TOOL_PREFIX == "mcp_install:"


def test_extract_install_server_id_valid():
    from web.routes import mcp as mcp_routes

    assert mcp_routes._extract_install_server_id_from_tool_name("mcp_install:alice") == "alice"
    assert mcp_routes._extract_install_server_id_from_tool_name("mcp_install:my.server") == "my.server"


def test_extract_install_server_id_empty_returns_none():
    from web.routes import mcp as mcp_routes

    assert mcp_routes._extract_install_server_id_from_tool_name("mcp_install:") is None


def test_extract_install_server_id_wrong_prefix_returns_none():
    from web.routes import mcp as mcp_routes

    assert mcp_routes._extract_install_server_id_from_tool_name("mcp.install:alice") is None
    assert mcp_routes._extract_install_server_id_from_tool_name("install:alice") is None
    assert mcp_routes._extract_install_server_id_from_tool_name("mcp_install_alice") is None


def test_extract_install_server_id_invalid_format_returns_none():
    from web.routes import mcp as mcp_routes

    assert mcp_routes._extract_install_server_id_from_tool_name("mcp_install:Alice") is None
    assert mcp_routes._extract_install_server_id_from_tool_name("mcp_install:con") is None
    assert mcp_routes._extract_install_server_id_from_tool_name("mcp_install:..") is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 18 — Singleton Catalog séparé (jamais via attribut privé)
# ══════════════════════════════════════════════════════════════════════════════


def test_deps_has_catalog_singleton_separate():
    from web.routes import deps

    assert hasattr(deps, "_MCP_SERVER_CATALOG_SINGLETON")
    assert hasattr(deps, "get_mcp_server_catalog_singleton")
    assert hasattr(deps, "_MCP_INSTALL_ORCHESTRATOR_SINGLETON")
    assert hasattr(deps, "get_mcp_install_orchestrator_singleton")


def test_mcp_py_does_not_access_approval_queue_catalog_private_attr():
    """Aucun accès à _APPROVAL_QUEUE._catalog ou autre attribut privé."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    forbidden = [
        "_MCP_APPROVAL_QUEUE_SINGLETON._catalog",
        "approval_queue._catalog",
        "queue._catalog",
        "_approval_queue._catalog",
    ]
    for token in forbidden:
        assert token not in text, f"Accès attribut privé interdit : {token}"


def test_lifespan_does_not_access_approval_queue_catalog_private_attr():
    text = _LIFESPAN_PATH.read_text(encoding="utf-8")
    forbidden = [
        "_MCP_APPROVAL_QUEUE_SINGLETON._catalog",
        "approval_queue._catalog",
    ]
    for token in forbidden:
        assert token not in text, f"Accès attribut privé interdit : {token}"


def test_install_orchestrator_built_with_public_singletons_only():
    """lifespan.py construit InstallOrchestrator avec catalog + approval_queue
    publics, pas via _attribut privé."""
    text = _LIFESPAN_PATH.read_text(encoding="utf-8")
    # Doit utiliser les singletons publics
    assert "deps._MCP_SERVER_CATALOG_SINGLETON" in text
    assert "deps._MCP_APPROVAL_QUEUE_SINGLETON" in text
    # Construction MCPInstallOrchestrator présente
    assert "MCPInstallOrchestrator" in text


# ══════════════════════════════════════════════════════════════════════════════
# Section 19 — Validation web/routes/mcp.py ne réplique PAS le helper privé
# ══════════════════════════════════════════════════════════════════════════════


def _find_function_body(source: str, fn_name: str) -> Optional[str]:
    """Extrait le corps d'une fonction Python (top-level) par nom.

    Heuristique : depuis `def fn_name` ou `async def fn_name` jusqu'à la
    prochaine ligne commençant par def/async def/@ au niveau 0.
    """
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


def test_install_handlers_do_not_fallback_to_get_approval_queue():
    """Phase 20B-2 : les handlers install n'utilisent JAMAIS
    _get_approval_queue() en fallback du singleton.

    Justification : install/propose et install/execute doivent partager la
    même ApprovalQueue que les routes /approvals/* (singleton lifespan),
    sinon un ticket pourrait être créé dans une queue distincte du flux UI.
    """
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    for handler_name in ("mcp_install_propose", "mcp_install_execute"):
        body = _find_function_body(text, handler_name)
        assert body is not None, f"Handler {handler_name} introuvable"
        # Doit utiliser le singleton
        assert "_get_approval_queue_singleton" in body, (
            f"{handler_name} doit appeler _get_approval_queue_singleton()"
        )
        # Ne doit PAS contenir d'appel _get_approval_queue() (fallback)
        # Le pattern recherché est l'appel littéral, pas le nom du singleton
        # _get_approval_queue_singleton qui le contient comme sous-chaîne.
        assert not re.search(
            r"_get_approval_queue\(\s*\)", body
        ), (
            f"{handler_name} ne doit PAS appeler _get_approval_queue() "
            f"(fallback interdit en Phase 20B-2)"
        )


def test_web_routes_mcp_does_not_import_phase14_private_validator():
    """Production : pas d'import du helper privé Phase 14 (lève CatalogError).
    La validation est répliquée localement pour lever HTTPException."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    forbidden = [
        "from src.mcp.server_catalog import _validate_server_id",
        "server_catalog._validate_server_id",
    ]
    for token in forbidden:
        assert token not in text, f"Import privé interdit en production : {token}"


# ══════════════════════════════════════════════════════════════════════════════
# Section 20 — Rate limit
# ══════════════════════════════════════════════════════════════════════════════


def test_rate_limit_install_prefix_marked_expensive():
    text = _SERVER_PY_PATH.read_text(encoding="utf-8")
    assert "/api/mcp/install/" in text
    m = re.search(
        r"_EXPENSIVE_PREFIXES\s*=\s*\(([^)]+)\)", text, re.DOTALL
    )
    assert m, "_EXPENSIVE_PREFIXES not found"
    assert "/api/mcp/install/" in m.group(1)


# ══════════════════════════════════════════════════════════════════════════════
# Section 21 — JS smoke : helpers sessionStorage + boutons + modals
# ══════════════════════════════════════════════════════════════════════════════


def test_panels_js_has_install_propose_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "submitMcpInstallPropose" in text
    assert "openMcpInstallProposeModal" in text


def test_panels_js_has_install_execute_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "submitMcpInstallExecute" in text
    assert "openMcpInstallExecuteModal" in text


def test_panels_js_ticket_mapping_uses_json_with_ttl():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    # Le setItem ticket utilise JSON.stringify + expires_at
    assert "mcp_install_ticket_" in text
    assert "expires_at" in text
    # Vérification structurelle : setTicketMapping construit du JSON
    assert "_mcpSetTicketMapping" in text


def test_panels_js_get_server_id_for_ticket_does_not_depend_on_marker():
    """_mcpGetServerIdForTicket ne référence NI _mcpGetMarker NI mcp_marker_."""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    # Extraire le corps de _mcpGetServerIdForTicket
    m = re.search(
        r"function\s+_mcpGetServerIdForTicket\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*",
        text,
    )
    assert m, "Fonction _mcpGetServerIdForTicket non trouvée"
    body = m.group(1)
    assert "_mcpGetMarker" not in body, (
        "_mcpGetServerIdForTicket ne doit pas dépendre du marker"
    )
    assert "mcp_marker_" not in body, (
        "_mcpGetServerIdForTicket ne doit pas lire mcp_marker_"
    )


def test_panels_js_purge_does_not_remove_valid_ticket_without_marker():
    """_mcpPurgeExpiredInstallState ne supprime QUE les entrées expirées."""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"function\s+_mcpPurgeExpiredInstallState\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*",
        text,
    )
    assert m, "Fonction _mcpPurgeExpiredInstallState non trouvée"
    body = m.group(1)
    # La branche de purge doit être basée sur expires_at uniquement
    assert "expires_at" in body
    # Ne doit PAS dépendre de l'existence d'un marker pour purger un ticket
    assert "_mcpGetMarker" not in body
    # Pas de logique "supprimer si pas de marker"
    assert "!_mcpGetMarker" not in body


def test_panels_js_purge_handles_both_namespaces():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"function\s+_mcpPurgeExpiredInstallState\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*",
        text,
    )
    assert m
    body = m.group(1)
    assert "mcp_install_ticket_" in body
    assert "mcp_marker_" in body


def test_panels_js_approve_captures_marker_after_lookup():
    """Dans submitMcpApprovalApprove, ordre strict : set marker → clear mapping."""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"async\s+function\s+submitMcpApprovalApprove\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*window",
        text,
    )
    assert m, "Fonction submitMcpApprovalApprove non trouvée"
    body = m.group(1)
    assert "_mcpGetServerIdForTicket" in body
    assert "_mcpSetMarker" in body
    assert "_mcpClearTicketMapping" in body
    # Ordre : set marker AVANT clear mapping
    pos_set = body.find("_mcpSetMarker")
    pos_clear = body.find("_mcpClearTicketMapping")
    assert 0 < pos_set < pos_clear, "set marker doit précéder clear mapping"


def test_panels_js_approve_keeps_local_create_mapping_until_execute():
    """Local-create approval must keep mapping so the marker is consumable."""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"async\s+function\s+submitMcpApprovalApprove\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*window",
        text,
    )
    assert m, "Fonction submitMcpApprovalApprove non trouvÃ©e"
    body = m.group(1)
    assert "localCreateNextServerId" in body
    assert "openMcpLocalCreateExecuteModal" in body
    assert "Materialiser local MCP maintenant" in body
    local_branch = re.search(
        r"else\s*\{\s*// Local-create approval[\s\S]*?localCreateNextTicketId=ticketId;\s*\}",
        body,
    )
    assert local_branch, "Branche local-create approve non trouvÃ©e"
    assert "_mcpClearLocalCreateTicketMapping" not in local_branch.group(0)


def test_panels_js_decisions_show_local_create_execute_when_marker_exists():
    """Approved local-create decisions need a fallback action button."""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "decisionToolName.startsWith(_MCP_LOCAL_CREATE_TOOL_PREFIX)" in text
    assert "_mcpGetMarker(localServerId)" in text
    assert "_mcpLocalCreateExecuteButton(localServerId,decisionActionId,'')" in text
    assert "${decisionActionBtn}" in text


def test_panels_js_uses_session_storage_only_no_local_storage():
    """Aucun localStorage utilisé pour le state install (sessionStorage uniquement)."""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    # Extraire la section Phase 20B-2
    m = re.search(
        r"Phase 20B-2[\s\S]*?(?=Phase 20B-3|/\* =====|window\.loadMcp\s*$|\Z)",
        text,
    )
    if m:
        section = m.group(0)
        assert "localStorage" not in section, (
            "Aucun localStorage autorisé en Phase 20B-2"
        )


def test_panels_js_install_propose_button_on_declared():
    """Bouton 'Proposer install' affiché sur les entries DECLARED."""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "openMcpInstallProposeModal" in text
    # heuristique : check d'un test sur status === 'declared'
    assert "'declared'" in text or '"declared"' in text


def test_panels_js_install_execute_uses_install_prefix():
    """Détection ticket install via pattern Phase 18 mcp_install:"""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "_MCP_INSTALL_TOOL_PREFIX" in text or "'mcp_install:'" in text
    assert "startsWith" in text


def test_panels_js_execute_clears_marker_and_mapping():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"async\s+function\s+submitMcpInstallExecute\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*window",
        text,
    )
    assert m
    body = m.group(1)
    assert "_mcpClearMarker" in body
    assert "_mcpClearTicketMapping" in body


def test_panels_js_purge_called_in_load_approvals():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"async\s+function\s+_loadMcpApprovals\s*\([^)]*\)\s*\{([\s\S]*?)(?=async\s+function|\nasync\s+function|/\* ====)",
        text,
    )
    assert m
    body = m.group(1)
    assert "_mcpPurgeExpiredInstallState" in body


def test_panels_js_ticket_mapping_ttl_constant_defined():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "_MCP_TICKET_MAPPING_TTL_S" in text
    # TTL doit être >= 600 s
    m = re.search(r"_MCP_TICKET_MAPPING_TTL_S\s*=\s*(\d+)", text)
    assert m
    assert int(m.group(1)) >= 600


# ══════════════════════════════════════════════════════════════════════════════
# Section 22 — Health expose install_orchestrator
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_health_exposes_install_orchestrator_component(monkeypatch, tmp_path):
    app, *_ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    assert resp.status_code == 200
    d = resp.json()
    assert "install_orchestrator" in d.get("components", {})


@pytest.mark.asyncio
async def test_health_phase_at_least_20b2(monkeypatch):
    """Phase ≥ 20B-2 (élargie pour 20B-3+)."""
    app, *_ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    d = resp.json()
    assert d["phase"] in ("20B-2", "20B-3", "20B-4", "20B-5", "20B-6", "21")
