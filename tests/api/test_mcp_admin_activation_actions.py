"""
Tests Phase 20B-3 — Activation lifecycle UI mutations (propose / execute / deactivate).

Couverture obligatoire :
  - Auth (401/403)
  - Confirmation backend + phrase = server_id exact
  - caller_kind whitelist (propose)
  - Marker UUID4 format (execute uniquement)
  - server_id Phase 14 + Windows reserved (héritage 20B-2)
  - Catalog/Queue/InstallOrchestrator/RuntimeWatcher/ActivationService unavailable
  - Status checks (propose INSTALLED, execute INSTALLED, deactivate ACTIVE)
  - Dry-run propose / execute / deactivate STRICT
  - Live execute : _take_marker AVANT activate
  - Live deactivate : pas de marker
  - Validation croisée args["server_id"]
  - Marker consommé reste consommé en cas d'échec activate
  - Audit UI étendu sans fuite package_spec/version/notes/args/phrase/marker
  - Anti-fuite ActivationResult / DeactivationResult
  - Aucun subprocess / register_dynamic_handler direct dans mcp.py
  - Pattern mcp_activate:<server_id> réel Phase 19
  - registry_writer = runtime Lumena (pas ToolRegistry neuf)
  - HandlerAdapter facade (pas de classe instanciée)
  - RuntimeWatcher singleton
  - Runner factory (server_id, entry) 2 args
  - MCPInstallSpec construit npm/pypi→uv ; local et inconnu raise
  - mcp_root = install_orchestrator.install_root
  - Anti-fuite transport_unsupported (error_code générique activate_failed)
  - JS smoke : 3 boutons + 3 modals + extension purge + capture marker activate
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
_LIFESPAN_PATH = _REPO_ROOT / "web" / "routes" / "lifespan.py"


# ──────────────────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────────────────


class _FakeStatus:
    def __init__(self, value: str):
        self.value = value


class _FakeServerEntry:
    def __init__(self, server_id: str, status: str = "installed",
                 package_spec: str = "npm:fake-pkg"):
        self.server_id = server_id
        self.status = _FakeStatus(status)
        self.package_spec = package_spec
        self.version = f"SECRET_VERSION_LEAK_{server_id}"
        self.notes = f"SECRET_NOTES_LEAK_{server_id}"
        self.display_name = server_id
        self.owner_profile = "test"
        self.trust_score = 80
        self.added_at = "2026-06-01T00:00:00Z"
        self.updated_at = "2026-06-01T00:00:00Z"
        self.last_active_at = None


class _FakeCatalog:
    def __init__(self, entries: Optional[List[_FakeServerEntry]] = None):
        self._entries = {e.server_id: e for e in (entries or [])}

    def get_server(self, server_id):
        return self._entries.get(server_id)

    def set_status(self, server_id, status):
        if server_id in self._entries:
            self._entries[server_id].status = _FakeStatus(status)


class _FakeInstallOrchestrator:
    def __init__(self, install_root=None):
        from pathlib import Path as _P
        self.install_root = install_root or _P("/tmp/mcp_install_test_root")
        self.dry_run = False


class _FakeRuntimeWatcher:
    pass


class _FakeApprovalQueue:
    def __init__(self):
        self.propose_calls: List[Dict[str, Any]] = []

    def propose(self, **kwargs):
        return uuid.uuid4().hex


class _FakeProposal:
    def __init__(self, ticket_id, server_id):
        self.approval_ticket_id = ticket_id
        self.server_id = server_id


class _FakeActivationResult:
    def __init__(self, server_id, success=True):
        self.server_id = server_id
        self.success = success
        self.reason = "ok" if success else "SECRET_ACTIVATION_REASON_LEAK"


class _FakeDeactivationResult:
    def __init__(self, server_id, success=True):
        self.server_id = server_id
        self.success = success
        self.reason = "ok" if success else "SECRET_DEACTIVATION_REASON_LEAK"


class _FakeActivationService:
    def __init__(self, catalog=None, approval_queue=None, discovery=None,
                 adapter=None, registry_writer=None, runtime_watcher=None,
                 runner_factory=None, client_factory=None, dry_run=False):
        self._catalog = catalog
        self._registry_writer = registry_writer
        self._runtime_watcher = runtime_watcher
        self._runner_factory = runner_factory
        self._adapter = adapter
        self._dry_run = bool(dry_run)
        self.propose_calls: List[Dict[str, Any]] = []
        self.activate_calls: List[Dict[str, Any]] = []
        self.deactivate_calls: List[Dict[str, Any]] = []
        self.activate_should_raise = False
        self.activate_should_fail = False
        self.deactivate_should_raise = False
        self.deactivate_should_fail = False
        # Reçu pour vérification cross-test
        self.last_registry_writer_received = registry_writer
        self.last_runtime_watcher_received = runtime_watcher

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def propose_activation(self, *, server_id, caller_kind):
        self.propose_calls.append({"server_id": server_id, "caller_kind": caller_kind})
        return _FakeProposal(uuid.uuid4().hex, server_id)

    def activate(self, *, server_id, approval_result=None):
        self.activate_calls.append({
            "server_id": server_id, "approval_result": approval_result,
        })
        if self.activate_should_raise:
            raise RuntimeError("boom-activate-raw-message-LEAK")
        if self.activate_should_fail:
            return _FakeActivationResult(server_id, success=False)
        try:
            self._catalog.set_status(server_id, "active")
        except Exception:
            pass
        return _FakeActivationResult(server_id, success=True)

    def deactivate(self, server_id):
        self.deactivate_calls.append({"server_id": server_id})
        if self.deactivate_should_raise:
            raise RuntimeError("boom-deactivate-raw-message-LEAK")
        if self.deactivate_should_fail:
            return _FakeDeactivationResult(server_id, success=False)
        try:
            self._catalog.set_status(server_id, "installed")
        except Exception:
            pass
        return _FakeDeactivationResult(server_id, success=True)


class _FakeApprovalResult:
    def __init__(self, server_id, decision="APPROVED"):
        class _D:
            def __init__(self, v): self.value = v
        self.decision = _D(decision)
        self.args = {
            "server_id": server_id,
            "secret_args_marker": f"SHOULD_NEVER_LEAK_{server_id}",
        }
        self.reason = None


# ──────────────────────────────────────────────────────────────────────────────
# App helper
# ──────────────────────────────────────────────────────────────────────────────


def _new_marker() -> str:
    return uuid.uuid4().hex


def _make_app(
    *,
    installed: Optional[List[str]] = None,
    active: Optional[List[str]] = None,
    declared: Optional[List[str]] = None,
    activation_service: Optional[_FakeActivationService] = None,
    no_catalog: bool = False,
    no_queue: bool = False,
    no_install_orchestrator: bool = False,
    no_runtime_watcher: bool = False,
    no_activation_service: bool = False,
    with_auth_override: bool = True,
) -> tuple[FastAPI, _FakeCatalog, _FakeApprovalQueue, _FakeActivationService]:
    from web.routes import deps, mcp as mcp_routes

    entries = []
    for sid in installed or []:
        entries.append(_FakeServerEntry(sid, status="installed"))
    for sid in active or []:
        entries.append(_FakeServerEntry(sid, status="active"))
    for sid in declared or []:
        entries.append(_FakeServerEntry(sid, status="declared"))

    catalog = _FakeCatalog(entries=entries)
    queue = _FakeApprovalQueue()
    install_orch = _FakeInstallOrchestrator()
    watcher = _FakeRuntimeWatcher()
    activation = activation_service or _FakeActivationService(
        catalog=catalog,
        approval_queue=queue,
        runtime_watcher=watcher,
        dry_run=False,
    )

    deps._MCP_SERVER_CATALOG_SINGLETON = None if no_catalog else catalog
    deps._MCP_APPROVAL_QUEUE_SINGLETON = None if no_queue else queue
    deps._MCP_INSTALL_ORCHESTRATOR_SINGLETON = None if no_install_orchestrator else install_orch
    deps._MCP_RUNTIME_WATCHER_SINGLETON = None if no_runtime_watcher else watcher
    deps._MCP_ACTIVATION_SERVICE_SINGLETON = None if no_activation_service else activation

    mcp_routes._APPROVAL_RESULT_CACHE.clear()

    app = FastAPI()
    app.include_router(mcp_routes.router)
    if with_auth_override:
        app.dependency_overrides[deps.verify_admin_token] = lambda: None
    return app, catalog, queue, activation


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


_ACTIVATION_PATHS = [
    "/api/mcp/activation/propose",
    "/api/mcp/activation/execute",
    "/api/mcp/activation/deactivate",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _ACTIVATION_PATHS)
async def test_activation_route_requires_admin_token_401(path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app, *_ = _make_app(installed=["alice"], active=["bob"], with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(path, json={"confirmed": True})
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _ACTIVATION_PATHS)
async def test_activation_route_bad_token_403(path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app, *_ = _make_app(installed=["alice"], active=["bob"], with_auth_override=False)
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
async def test_propose_without_confirmed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(installed=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/propose",
            json={"server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_required"


@pytest.mark.asyncio
async def test_execute_without_confirmed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(installed=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "server_id": "alice",
                "confirmation_phrase": "alice",
                "marker": _new_marker(),
            },
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_deactivate_without_confirmed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(active=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/deactivate",
            json={"server_id": "alice", "confirmation_phrase": "alice"},
        )
    assert resp.status_code == 400


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — caller_kind whitelist (propose)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["cli", "discovery", "agent", "", None, "ADMIN_UI"])
async def test_propose_caller_kind_not_admin_ui_rejected(monkeypatch, tmp_path, kind):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _cat, _q, act = _make_app(installed=["alice"])
    payload = {"confirmed": True, "server_id": "alice"}
    if kind is not None:
        payload["caller_kind"] = kind
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/activation/propose", json=payload)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "caller_kind_invalid"
    assert act.propose_calls == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — confirmation_phrase = server_id exact (execute + deactivate)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase,expected",
    [("ALICE", "alice"), ("bob", "alice"), ("", "alice"), ("alice ", "alice")],
)
async def test_execute_confirmation_phrase_must_match_exact(
    monkeypatch, tmp_path, phrase, expected
):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(installed=[expected])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": expected,
                "confirmation_phrase": phrase, "marker": _new_marker(),
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_phrase_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", ["ALICE", "bob", "", "alice "])
async def test_deactivate_confirmation_phrase_must_match_exact(
    monkeypatch, tmp_path, phrase
):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(active=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/deactivate",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": phrase,
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_phrase_invalid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Server_id Phase 14 + Windows (réplication 20B-2)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sid",
    ["Alice", ".hidden", "-foo", "a" * 65, "", "..", "a/b", "a\\b",
     "con", "aux", "nul", "prn", "com1", "lpt9", "con.txt"],
)
async def test_server_id_invalid_format_rejected(monkeypatch, tmp_path, sid):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(installed=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/propose",
            json={"confirmed": True, "server_id": sid, "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "server_id_invalid_format"


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Marker validation
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_marker",
    ["", "abc", "X" * 32, uuid.uuid4().hex.upper(), uuid.uuid4().hex + "x"],
)
async def test_execute_marker_invalid_format_400(monkeypatch, tmp_path, bad_marker):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, *_ = _make_app(installed=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": bad_marker,
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "marker_invalid_format"


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — Singletons unavailable
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_propose_catalog_unavailable_503(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(no_catalog=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 503
    assert resp.json().get("detail", {}).get("error_code") == "catalog_unavailable"


@pytest.mark.asyncio
async def test_propose_queue_unavailable_503(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(installed=["alice"], no_queue=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 503
    assert resp.json().get("detail", {}).get("error_code") == "queue_unavailable"


@pytest.mark.asyncio
async def test_propose_install_orchestrator_unavailable_503(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(installed=["alice"], no_install_orchestrator=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 503
    assert resp.json().get("detail", {}).get("error_code") == "install_orchestrator_unavailable"


@pytest.mark.asyncio
async def test_propose_runtime_watcher_unavailable_503(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(installed=["alice"], no_runtime_watcher=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 503
    assert resp.json().get("detail", {}).get("error_code") == "runtime_watcher_unavailable"


@pytest.mark.asyncio
async def test_propose_activation_service_unavailable_503(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(installed=["alice"], no_activation_service=True)
    monkeypatch.setattr(
        mcp_routes, "_build_activation_service", lambda *a, **kw: None
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 503
    assert resp.json().get("detail", {}).get("error_code") == "activation_service_unavailable"


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — Status checks
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_propose_server_not_found_404(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(installed=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/propose",
            json={"confirmed": True, "server_id": "bob", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_propose_server_not_installed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(declared=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "server_id_not_installed"


@pytest.mark.asyncio
async def test_execute_server_not_installed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, *_ = _make_app(active=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": _new_marker(),
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "server_id_not_installed"


@pytest.mark.asyncio
async def test_deactivate_server_not_active_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, *_ = _make_app(installed=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/deactivate",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice",
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "server_id_not_active"


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — Dry-run propose
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dry_run_propose_does_not_call_service(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app, _cat, _q, act = _make_app(installed=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["would_propose"] is True
    assert d["live_mode"] is False
    assert d["forced_dry_run"] is True
    assert act.propose_calls == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — Dry-run execute STRICT (critique)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dry_run_execute_does_not_call_take_marker(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    from web.routes import mcp as mcp_routes

    spy = {"calls": 0}
    real_take = mcp_routes._take_marker

    def _spy_take(marker):
        spy["calls"] += 1
        return real_take(marker)

    monkeypatch.setattr(mcp_routes, "_take_marker", _spy_take)

    app, *_ = _make_app(installed=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": _new_marker(),
            },
        )
    assert resp.status_code == 200
    assert spy["calls"] == 0


@pytest.mark.asyncio
async def test_dry_run_execute_does_not_call_activate(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app, _cat, _q, act = _make_app(installed=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": _new_marker(),
            },
        )
    assert resp.status_code == 200
    assert act.activate_calls == []


@pytest.mark.asyncio
async def test_dry_run_execute_marker_still_in_cache(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(installed=["alice"])
    marker = mcp_routes._put_marker("aid", _FakeApprovalResult("alice"))
    cache_size_before = len(mcp_routes._APPROVAL_RESULT_CACHE)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": marker,
            },
        )
    assert len(mcp_routes._APPROVAL_RESULT_CACHE) == cache_size_before


@pytest.mark.asyncio
async def test_dry_run_execute_returns_whitelist(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app, *_ = _make_app(installed=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": _new_marker(),
            },
        )
    d = resp.json()
    allowed = {"would_execute", "server_id", "live_mode", "forced_dry_run"}
    assert set(d.keys()) <= allowed


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — Dry-run deactivate
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dry_run_deactivate_does_not_call_deactivate(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app, _cat, _q, act = _make_app(active=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/deactivate",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice",
            },
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["would_deactivate"] is True
    assert d["live_mode"] is False
    assert d["forced_dry_run"] is True
    assert act.deactivate_calls == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 12 — Live propose
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_live_propose_calls_service_once(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _cat, _q, act = _make_app(installed=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["proposed"] is True
    assert d["live_mode"] is True
    assert isinstance(d["ticket_id"], str) and len(d["ticket_id"]) == 32
    assert d["server_id"] == "alice"
    assert len(act.propose_calls) == 1


@pytest.mark.asyncio
async def test_live_propose_response_whitelist(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, *_ = _make_app(installed=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    d = resp.json()
    allowed = {"proposed", "ticket_id", "server_id", "live_mode"}
    assert set(d.keys()) <= allowed


# ══════════════════════════════════════════════════════════════════════════════
# Section 13 — Live execute : _take_marker AVANT activate
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

    app, _cat, _q, act = _make_app(installed=["alice"])
    marker = mcp_routes._put_marker("aid", _FakeApprovalResult("alice"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": marker,
            },
        )
    assert resp.status_code == 200, resp.text
    assert spy["calls"] == 1
    assert len(act.activate_calls) == 1


@pytest.mark.asyncio
async def test_live_execute_orchestrator_receives_real_approval_result(
    monkeypatch, tmp_path
):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, _cat, _q, act = _make_app(installed=["alice"])
    result = _FakeApprovalResult("alice")
    marker = mcp_routes._put_marker("aid", result)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": marker,
            },
        )
    assert resp.status_code == 200
    assert act.activate_calls[0]["approval_result"] is result


@pytest.mark.asyncio
async def test_live_execute_response_whitelist(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(installed=["alice"])
    marker = mcp_routes._put_marker("aid", _FakeApprovalResult("alice"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": marker,
            },
        )
    d = resp.json()
    allowed = {"activated", "server_id", "status", "live_mode"}
    assert set(d.keys()) <= allowed


# ══════════════════════════════════════════════════════════════════════════════
# Section 14 — Live deactivate (pas de marker)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_live_deactivate_calls_deactivate_once(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _cat, _q, act = _make_app(active=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/deactivate",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice",
            },
        )
    assert resp.status_code == 200, resp.text
    assert len(act.deactivate_calls) == 1
    d = resp.json()
    assert d["deactivated"] is True
    assert d["live_mode"] is True


@pytest.mark.asyncio
async def test_deactivate_does_not_validate_marker(monkeypatch, tmp_path):
    """Deactivate ne lit pas le marker côté body."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    spy = {"calls": 0}
    real_take = mcp_routes._take_marker

    def _spy_take(marker):
        spy["calls"] += 1
        return real_take(marker)

    monkeypatch.setattr(mcp_routes, "_take_marker", _spy_take)

    app, *_ = _make_app(active=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/deactivate",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice",
            },
        )
    assert resp.status_code == 200
    assert spy["calls"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# Section 15 — Marker mismatch / consumed irrecoverable
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_execute_marker_not_found_404(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, *_ = _make_app(installed=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": _new_marker(),
            },
        )
    assert resp.status_code == 404
    assert resp.json().get("detail", {}).get("error_code") == "marker_not_found_or_expired"


@pytest.mark.asyncio
async def test_execute_marker_server_id_mismatch_400_and_consumed(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(installed=["alice", "bob"])
    result = _FakeApprovalResult("bob")
    marker = mcp_routes._put_marker("aid", result)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": marker,
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "marker_server_id_mismatch"
    assert marker not in mcp_routes._APPROVAL_RESULT_CACHE


@pytest.mark.asyncio
async def test_live_execute_marker_consumed_irrecoverable_on_failure(
    monkeypatch, tmp_path
):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    act = _FakeActivationService(dry_run=False)
    act.activate_should_fail = True
    app, *_ = _make_app(installed=["alice"], activation_service=act)
    marker = mcp_routes._put_marker("aid", _FakeApprovalResult("alice"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": marker,
            },
        )
    assert resp.status_code == 500
    assert marker not in mcp_routes._APPROVAL_RESULT_CACHE
    events = _read_audit(audit_path)
    failed = [e for e in events if e["event"] == "ui_action_failed"]
    assert any(e.get("marker_consumed_irrecoverable") is True for e in failed)


@pytest.mark.asyncio
async def test_live_execute_orchestrator_raises_audit_no_leak(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    act = _FakeActivationService(dry_run=False)
    act.activate_should_raise = True
    app, *_ = _make_app(installed=["alice"], activation_service=act)
    marker = mcp_routes._put_marker("aid", _FakeApprovalResult("alice"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": marker,
            },
        )
    assert resp.status_code == 500
    blob = json.dumps(resp.json())
    assert "boom-activate-raw-message-LEAK" not in blob
    audit_blob = json.dumps(_read_audit(audit_path))
    assert "boom-activate-raw-message-LEAK" not in audit_blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 16 — Audit UI étendu
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_audit_propose_events(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, *_ = _make_app(installed=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/activation/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    events = _read_audit(audit_path)
    kinds = [e["event"] for e in events]
    assert "ui_action_requested" in kinds
    assert "ui_action_completed" in kinds
    for e in events:
        assert e["phase"] == "20B-3"
        assert e["action"] == "activation_propose"


@pytest.mark.asyncio
async def test_audit_never_contains_package_spec_version_notes(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(installed=["alice"])
    marker = mcp_routes._put_marker("a", _FakeApprovalResult("alice"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/activation/propose",
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
        await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": marker,
            },
        )
    blob = json.dumps(_read_audit(audit_path))
    assert "npm:fake-pkg" not in blob
    assert "SECRET_VERSION_LEAK" not in blob
    assert "SECRET_NOTES_LEAK" not in blob
    assert "package_spec" not in blob
    assert '"version"' not in blob
    assert '"notes"' not in blob


@pytest.mark.asyncio
async def test_audit_never_contains_args(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(installed=["alice"])
    marker = mcp_routes._put_marker("a", _FakeApprovalResult("alice"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": marker,
            },
        )
    blob = json.dumps(_read_audit(audit_path))
    assert "secret_args_marker" not in blob
    assert "SHOULD_NEVER_LEAK" not in blob


@pytest.mark.asyncio
async def test_audit_never_contains_marker_raw(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(installed=["alice"])
    secret_marker = mcp_routes._put_marker("a", _FakeApprovalResult("alice"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": secret_marker,
            },
        )
    blob = json.dumps(_read_audit(audit_path))
    assert secret_marker not in blob


@pytest.mark.asyncio
async def test_audit_actor_token_hash_sha256(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.delenv("LUMENA_TEST_MODE", raising=False)
    app, *_ = _make_app(installed=["alice"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/activation/propose",
            headers={"Authorization": "Bearer some-real-token-xyz"},
            json={"confirmed": True, "server_id": "alice", "caller_kind": "admin_ui"},
        )
    blob = json.dumps(_read_audit(audit_path))
    assert "some-real-token-xyz" not in blob
    for e in _read_audit(audit_path):
        if "actor_token_hash" in e:
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", e["actor_token_hash"])


# ══════════════════════════════════════════════════════════════════════════════
# Section 17 — Anti-fuite ActivationResult / DeactivationResult
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_activation_result_never_exposed(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    app, *_ = _make_app(installed=["alice"])
    marker = mcp_routes._put_marker("a", _FakeApprovalResult("alice"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice", "marker": marker,
            },
        )
    blob = resp.text
    assert "reason" not in blob or '"reason"' not in blob
    assert "SECRET_ACTIVATION_REASON_LEAK" not in blob


@pytest.mark.asyncio
async def test_deactivation_result_never_exposed(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    act = _FakeActivationService(dry_run=False)
    act.deactivate_should_fail = True
    app, *_ = _make_app(active=["alice"], activation_service=act)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/deactivate",
            json={
                "confirmed": True, "server_id": "alice",
                "confirmation_phrase": "alice",
            },
        )
    blob = resp.text
    assert "SECRET_DEACTIVATION_REASON_LEAK" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 18 — Aucun subprocess / register_dynamic_handler direct
# ══════════════════════════════════════════════════════════════════════════════


def test_web_routes_mcp_does_not_import_subprocess():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert "import subprocess" not in text
    assert "from subprocess import" not in text


def test_web_routes_mcp_no_subprocess_popen_call():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert "subprocess.Popen" not in text


def test_web_routes_mcp_no_register_dynamic_handler_direct():
    """Aucun appel direct register_dynamic_handler / unregister_dynamic_handler
    depuis web/routes/mcp.py — tout passe via MCPActivationService."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    for token in (
        ".register_dynamic_handler(",
        ".unregister_dynamic_handler(",
    ):
        assert token not in text, (
            f"{token} trouvé dans mcp.py — doit passer via MCPActivationService"
        )


def test_web_routes_mcp_no_fresh_tool_registry_instantiation():
    """Aucun ToolRegistry() neuf instancié dans mcp.py."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert "ToolRegistry()" not in text


def test_web_routes_mcp_no_fresh_handler_adapter_class():
    """Aucune HandlerAdapter() instanciée (la classe n'existe pas dans
    src/mcp/handler_adapter.py — il y a uniquement la fonction adapt_tool)."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert "HandlerAdapter()" not in text


# ══════════════════════════════════════════════════════════════════════════════
# Section 19 — Pattern Phase 19 réel
# ══════════════════════════════════════════════════════════════════════════════


def test_activate_tool_prefix_matches_phase19_contract():
    from web.routes import mcp as mcp_routes

    assert mcp_routes._ACTIVATE_TOOL_PREFIX == "mcp_activate:"


def test_extract_activate_server_id_valid():
    from web.routes import mcp as mcp_routes

    assert mcp_routes._extract_activate_server_id_from_tool_name(
        "mcp_activate:alice"
    ) == "alice"


def test_extract_activate_server_id_wrong_prefix():
    from web.routes import mcp as mcp_routes

    assert mcp_routes._extract_activate_server_id_from_tool_name(
        "mcp.activate:alice"
    ) is None
    assert mcp_routes._extract_activate_server_id_from_tool_name(
        "mcp_install:alice"
    ) is None


def test_extract_activate_server_id_invalid_format():
    from web.routes import mcp as mcp_routes

    assert mcp_routes._extract_activate_server_id_from_tool_name(
        "mcp_activate:Alice"
    ) is None
    assert mcp_routes._extract_activate_server_id_from_tool_name(
        "mcp_activate:con"
    ) is None


def test_no_mcp_dot_activate_in_production_backend():
    """Pas de heuristique "mcp.activate" (pattern Phase 19 réel = mcp_activate:)."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    for token in ['"mcp.activate"', "'mcp.activate'",
                  '"mcp.activate:"', "'mcp.activate:'"]:
        assert token not in text


def test_no_mcp_dot_activate_in_production_panels_js():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    for token in ['"mcp.activate"', "'mcp.activate'",
                  '"mcp.activate:"', "'mcp.activate:'"]:
        assert token not in text


# ══════════════════════════════════════════════════════════════════════════════
# Section 20 — Singletons séparés + registry_writer runtime
# ══════════════════════════════════════════════════════════════════════════════


def test_deps_has_runtime_watcher_and_activation_singletons():
    from web.routes import deps

    assert hasattr(deps, "_MCP_RUNTIME_WATCHER_SINGLETON")
    assert hasattr(deps, "_MCP_ACTIVATION_SERVICE_SINGLETON")
    assert hasattr(deps, "get_mcp_runtime_watcher_singleton")
    assert hasattr(deps, "get_mcp_activation_service_singleton")


def test_lifespan_initializes_runtime_watcher():
    text = _LIFESPAN_PATH.read_text(encoding="utf-8")
    assert "_MCP_RUNTIME_WATCHER_SINGLETON" in text
    assert "from src.mcp.runtime_watcher import RuntimeWatcher" in text


def test_lifespan_initializes_activation_service():
    text = _LIFESPAN_PATH.read_text(encoding="utf-8")
    assert "_MCP_ACTIVATION_SERVICE_SINGLETON" in text


def test_resolve_registry_writer_function_exists():
    from web.routes import mcp as mcp_routes

    assert hasattr(mcp_routes, "_resolve_registry_writer")


def test_resolve_registry_writer_returns_none_when_lumena_absent(monkeypatch):
    from web.routes import deps, mcp as mcp_routes

    monkeypatch.setattr(deps, "lumena", None, raising=False)
    assert mcp_routes._resolve_registry_writer() is None


def test_resolve_registry_writer_prefers_direct_attribute(monkeypatch):
    from web.routes import deps, mcp as mcp_routes

    class _FakeLumena:
        _tool_registry = "direct_registry"
        tool_system = type("_TS", (), {"_tool_registry": "via_ts"})()

    monkeypatch.setattr(deps, "lumena", _FakeLumena(), raising=False)
    assert mcp_routes._resolve_registry_writer() == "direct_registry"


def test_resolve_registry_writer_falls_back_to_tool_system(monkeypatch):
    from web.routes import deps, mcp as mcp_routes

    class _FakeLumena:
        _tool_registry = None
        tool_system = type("_TS", (), {"_tool_registry": "via_ts_only"})()

    monkeypatch.setattr(deps, "lumena", _FakeLumena(), raising=False)
    assert mcp_routes._resolve_registry_writer() == "via_ts_only"


# ══════════════════════════════════════════════════════════════════════════════
# Section 21 — Handler adapter facade
# ══════════════════════════════════════════════════════════════════════════════


def test_handler_adapter_facade_class_exists():
    from web.routes import mcp as mcp_routes

    assert hasattr(mcp_routes, "_MCPHandlerAdapterFacade")


def test_handler_adapter_facade_delegates_to_function(monkeypatch):
    from web.routes import mcp as mcp_routes
    from src.mcp import handler_adapter as ha

    call_log = []
    original = ha.adapt_tool

    def _spy(**kwargs):
        call_log.append(kwargs)
        return "ok"

    monkeypatch.setattr(ha, "adapt_tool", _spy)
    facade = mcp_routes._MCPHandlerAdapterFacade()
    result = facade.adapt_tool(server_id="x", tool_name="t")
    assert result == "ok"
    assert call_log == [{"server_id": "x", "tool_name": "t"}]


# ══════════════════════════════════════════════════════════════════════════════
# Section 22 — Runner factory + MCPInstallSpec transport
# ══════════════════════════════════════════════════════════════════════════════


def test_install_spec_npm_transport():
    from web.routes import mcp as mcp_routes

    entry = _FakeServerEntry("alice", package_spec="npm:my-pkg")
    spec = mcp_routes._build_install_spec_from_entry(entry)
    assert spec.transport == "npm"
    assert spec.package == "my-pkg"


def test_install_spec_pypi_to_uv_transport():
    from web.routes import mcp as mcp_routes

    entry = _FakeServerEntry("alice", package_spec="pypi:my-pkg")
    spec = mcp_routes._build_install_spec_from_entry(entry)
    assert spec.transport == "uv"
    assert spec.package == "my-pkg"


def test_install_spec_local_transport_missing_package_rejected_not_mapped_to_local():
    """Phase 5 contract : transport ∈ {"npm","uv"} strict.

    La branche local: DOIT lever ValueError AVANT de construire un
    MCPInstallSpec invalide. Le helper ne doit jamais retourner
    MCPInstallSpec(transport="local").
    """
    from web.routes import mcp as mcp_routes

    entry = _FakeServerEntry("alice", package_spec="local:alice")
    with pytest.raises(ValueError, match="transport_unsupported:local_missing"):
        mcp_routes._build_install_spec_from_entry(entry)


def test_install_spec_local_transport_maps_to_uv_package(monkeypatch, tmp_path):
    from web.routes import mcp as mcp_routes
    from src.mcp import local_package

    fake_pkg = type("Pkg", (), {
        "package_dir": tmp_path / "packages" / "alice",
        "module_name": "lumena_mcp_alice",
    })()
    monkeypatch.setattr(local_package, "resolve_local_mcp_package", lambda server_id: fake_pkg)

    entry = _FakeServerEntry("alice", package_spec="local:alice")
    spec = mcp_routes._build_install_spec_from_entry(entry)
    assert spec.transport == "uv"
    assert spec.package == str(fake_pkg.package_dir)
    assert spec.args == ["-m", "lumena_mcp_alice"]
    assert spec.require_wheels_only is False


def test_install_spec_unknown_transport_rejected():
    from web.routes import mcp as mcp_routes

    entry = _FakeServerEntry("alice", package_spec="foobar:x")
    with pytest.raises(ValueError, match="transport_unsupported:unknown"):
        mcp_routes._build_install_spec_from_entry(entry)


def test_install_spec_empty_package_rejected():
    from web.routes import mcp as mcp_routes

    entry = _FakeServerEntry("alice", package_spec="npm:")
    with pytest.raises(ValueError, match="transport_unsupported:empty_package"):
        mcp_routes._build_install_spec_from_entry(entry)


def test_install_spec_empty_package_spec_rejected():
    from web.routes import mcp as mcp_routes

    entry = _FakeServerEntry("alice", package_spec="")
    with pytest.raises(ValueError, match="transport_unsupported:unknown"):
        mcp_routes._build_install_spec_from_entry(entry)


def test_local_transport_missing_package_raises_before_mcpinstallspec():
    """Vérifie qu'aucun MCPInstallSpec n'est instancié pour local:.

    Spy sur MCPInstallSpec.__init__ pour confirmer 0 instanciation.
    """
    from web.routes import mcp as mcp_routes
    from src.mcp import sandbox_runner

    spy = {"calls": 0}
    original = sandbox_runner.MCPInstallSpec

    class _SpySpec(original):
        def __init__(self, *a, **kw):
            spy["calls"] += 1
            super().__init__(*a, **kw)

    sandbox_runner.MCPInstallSpec = _SpySpec
    try:
        entry = _FakeServerEntry("alice", package_spec="local:alice")
        with pytest.raises(ValueError):
            mcp_routes._build_install_spec_from_entry(entry)
        assert spy["calls"] == 0, (
            "MCPInstallSpec ne doit JAMAIS être instancié pour transport local"
        )
    finally:
        sandbox_runner.MCPInstallSpec = original


def test_no_mcpinstallspec_transport_local_assignment():
    """Grep statique strict : aucune assignation transport="local" /
    transport='local' / MCPInstallSpec(...transport="local"...) dans mcp.py.

    Le code production peut contenir :
      - raw.startswith("local:")
      - raise ValueError("transport_unsupported:local")
      - tests/commentaires expliquant le refus local
    """
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    forbidden_patterns = [
        'transport="local"',
        "transport='local'",
        'transport = "local"',
        "transport = 'local'",
    ]
    for token in forbidden_patterns:
        assert token not in text, (
            f"{token} trouvé dans mcp.py — MCPInstallSpec(transport=\"local\") "
            "interdit (Phase 5 contract : transport ∈ npm/uv strict)"
        )


def test_runner_factory_signature_is_two_args():
    """Grep statique sur _build_runner_factory : la closure interne doit
    avoir la signature (server_id, entry) imposée par Phase 19."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    # Le pattern recherché : def _factory(server_id, entry) ou
    # def _factory(server_id: ..., entry: ...) à l'intérieur de
    # _build_runner_factory.
    assert re.search(
        r"def\s+_factory\s*\(\s*server_id[^,]*,\s*entry", text
    ), "Runner factory closure doit avoir signature (server_id, entry)"


def test_runner_factory_uses_install_orchestrator_install_root(monkeypatch):
    """Le runner est construit avec mcp_root = install_root, pas le défaut."""
    from web.routes import mcp as mcp_routes
    from src.mcp import sandbox_runner
    from pathlib import Path as _P

    spy = {"calls": []}
    original = sandbox_runner.MCPSandboxRunner.__init__

    def _spy_init(self, *a, **kw):
        spy["calls"].append({"args": a, "kwargs": kw})
        # Ne pas appeler le vrai __init__ pour éviter side-effects
        self.spec = kw.get("spec")
        self._mcp_root = kw.get("mcp_root")

    monkeypatch.setattr(sandbox_runner.MCPSandboxRunner, "__init__", _spy_init)

    install_root = _P("/tmp/specific_install_root_for_test")
    factory = mcp_routes._build_runner_factory(install_root)
    entry = _FakeServerEntry("alice", package_spec="npm:my-pkg")
    factory("alice", entry)
    assert len(spy["calls"]) == 1
    assert spy["calls"][0]["kwargs"]["mcp_root"] == install_root


# ══════════════════════════════════════════════════════════════════════════════
# Section 23 — Activation execute local transport (anti-fuite)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_activation_execute_local_transport_fails_without_subprocess(
    monkeypatch, tmp_path
):
    """Server installé avec package_spec local: → activate échoue (runner factory raise)
    avec error_code générique activate_failed, sans appel subprocess."""
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes

    # ActivationService réel-ish : invoque le runner_factory
    class _RealishActivation(_FakeActivationService):
        def activate(self, *, server_id, approval_result=None):
            self.activate_calls.append({
                "server_id": server_id, "approval_result": approval_result,
            })
            # Appelle le runner_factory comme le ferait Phase 19
            entry = self._catalog.get_server(server_id)
            self._runner_factory(server_id, entry)  # raise ValueError
            return _FakeActivationResult(server_id, success=True)

    # Catalog avec entry local:
    from web.routes import deps
    catalog = _FakeCatalog([
        _FakeServerEntry(
            "alicelocal", status="installed",
            package_spec="local:/path/to/SECRET_PKG_LEAK",
        ),
    ])
    queue = _FakeApprovalQueue()
    install_orch = _FakeInstallOrchestrator()
    watcher = _FakeRuntimeWatcher()
    runner_factory = mcp_routes._build_runner_factory(install_orch.install_root)
    activation = _RealishActivation(
        catalog=catalog, approval_queue=queue, runtime_watcher=watcher,
        runner_factory=runner_factory, dry_run=False,
    )
    deps._MCP_SERVER_CATALOG_SINGLETON = catalog
    deps._MCP_APPROVAL_QUEUE_SINGLETON = queue
    deps._MCP_INSTALL_ORCHESTRATOR_SINGLETON = install_orch
    deps._MCP_RUNTIME_WATCHER_SINGLETON = watcher
    deps._MCP_ACTIVATION_SERVICE_SINGLETON = activation
    mcp_routes._APPROVAL_RESULT_CACHE.clear()

    app = FastAPI()
    app.include_router(mcp_routes.router)
    app.dependency_overrides[deps.verify_admin_token] = lambda: None

    marker = mcp_routes._put_marker("aid", _FakeApprovalResult("alicelocal"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alicelocal",
                "confirmation_phrase": "alicelocal", "marker": marker,
            },
        )
    assert resp.status_code == 500
    # error_code générique (anti canal latéral)
    assert resp.json().get("detail", {}).get("error_code") == "activate_failed"
    # Aucun "transport_unsupported" dans la réponse
    assert "transport_unsupported" not in resp.text


@pytest.mark.asyncio
async def test_activation_execute_local_transport_marker_consumed_irrecoverable(
    monkeypatch, tmp_path
):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes
    from web.routes import deps

    class _RealishActivation(_FakeActivationService):
        def activate(self, *, server_id, approval_result=None):
            self.activate_calls.append({
                "server_id": server_id, "approval_result": approval_result,
            })
            entry = self._catalog.get_server(server_id)
            self._runner_factory(server_id, entry)
            return _FakeActivationResult(server_id, success=True)

    catalog = _FakeCatalog([
        _FakeServerEntry("alicelocal", status="installed",
                         package_spec="local:/x"),
    ])
    queue = _FakeApprovalQueue()
    install_orch = _FakeInstallOrchestrator()
    watcher = _FakeRuntimeWatcher()
    activation = _RealishActivation(
        catalog=catalog, approval_queue=queue, runtime_watcher=watcher,
        runner_factory=mcp_routes._build_runner_factory(install_orch.install_root),
        dry_run=False,
    )
    deps._MCP_SERVER_CATALOG_SINGLETON = catalog
    deps._MCP_APPROVAL_QUEUE_SINGLETON = queue
    deps._MCP_INSTALL_ORCHESTRATOR_SINGLETON = install_orch
    deps._MCP_RUNTIME_WATCHER_SINGLETON = watcher
    deps._MCP_ACTIVATION_SERVICE_SINGLETON = activation
    mcp_routes._APPROVAL_RESULT_CACHE.clear()

    app = FastAPI()
    app.include_router(mcp_routes.router)
    app.dependency_overrides[deps.verify_admin_token] = lambda: None

    marker = mcp_routes._put_marker("aid", _FakeApprovalResult("alicelocal"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alicelocal",
                "confirmation_phrase": "alicelocal", "marker": marker,
            },
        )
    # Marker consommé
    assert marker not in mcp_routes._APPROVAL_RESULT_CACHE
    # Audit marker_consumed_irrecoverable=true
    events = _read_audit(audit_path)
    failed = [e for e in events if e["event"] == "ui_action_failed"]
    assert any(e.get("marker_consumed_irrecoverable") is True for e in failed)


@pytest.mark.asyncio
async def test_audit_local_transport_does_not_leak_package_spec(
    monkeypatch, tmp_path
):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes
    from web.routes import deps

    class _RealishActivation(_FakeActivationService):
        def activate(self, *, server_id, approval_result=None):
            self.activate_calls.append({
                "server_id": server_id, "approval_result": approval_result,
            })
            entry = self._catalog.get_server(server_id)
            self._runner_factory(server_id, entry)
            return _FakeActivationResult(server_id, success=True)

    catalog = _FakeCatalog([
        _FakeServerEntry("alicelocal", status="installed",
                         package_spec="local:/path/to/SECRET_PKG_LEAK_MARKER"),
    ])
    queue = _FakeApprovalQueue()
    install_orch = _FakeInstallOrchestrator()
    watcher = _FakeRuntimeWatcher()
    activation = _RealishActivation(
        catalog=catalog, approval_queue=queue, runtime_watcher=watcher,
        runner_factory=mcp_routes._build_runner_factory(install_orch.install_root),
        dry_run=False,
    )
    deps._MCP_SERVER_CATALOG_SINGLETON = catalog
    deps._MCP_APPROVAL_QUEUE_SINGLETON = queue
    deps._MCP_INSTALL_ORCHESTRATOR_SINGLETON = install_orch
    deps._MCP_RUNTIME_WATCHER_SINGLETON = watcher
    deps._MCP_ACTIVATION_SERVICE_SINGLETON = activation
    mcp_routes._APPROVAL_RESULT_CACHE.clear()

    app = FastAPI()
    app.include_router(mcp_routes.router)
    app.dependency_overrides[deps.verify_admin_token] = lambda: None

    marker = mcp_routes._put_marker("aid", _FakeApprovalResult("alicelocal"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alicelocal",
                "confirmation_phrase": "alicelocal", "marker": marker,
            },
        )
    blob = json.dumps(_read_audit(audit_path))
    assert "SECRET_PKG_LEAK_MARKER" not in blob
    assert "transport_unsupported" not in blob


@pytest.mark.asyncio
async def test_audit_local_transport_uses_generic_error_code(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import mcp as mcp_routes
    from web.routes import deps

    class _RealishActivation(_FakeActivationService):
        def activate(self, *, server_id, approval_result=None):
            self.activate_calls.append({
                "server_id": server_id, "approval_result": approval_result,
            })
            entry = self._catalog.get_server(server_id)
            self._runner_factory(server_id, entry)
            return _FakeActivationResult(server_id, success=True)

    catalog = _FakeCatalog([
        _FakeServerEntry("alicelocal", status="installed",
                         package_spec="local:/x"),
    ])
    queue = _FakeApprovalQueue()
    install_orch = _FakeInstallOrchestrator()
    watcher = _FakeRuntimeWatcher()
    activation = _RealishActivation(
        catalog=catalog, approval_queue=queue, runtime_watcher=watcher,
        runner_factory=mcp_routes._build_runner_factory(install_orch.install_root),
        dry_run=False,
    )
    deps._MCP_SERVER_CATALOG_SINGLETON = catalog
    deps._MCP_APPROVAL_QUEUE_SINGLETON = queue
    deps._MCP_INSTALL_ORCHESTRATOR_SINGLETON = install_orch
    deps._MCP_RUNTIME_WATCHER_SINGLETON = watcher
    deps._MCP_ACTIVATION_SERVICE_SINGLETON = activation
    mcp_routes._APPROVAL_RESULT_CACHE.clear()

    app = FastAPI()
    app.include_router(mcp_routes.router)
    app.dependency_overrides[deps.verify_admin_token] = lambda: None

    marker = mcp_routes._put_marker("aid", _FakeApprovalResult("alicelocal"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/activation/execute",
            json={
                "confirmed": True, "server_id": "alicelocal",
                "confirmation_phrase": "alicelocal", "marker": marker,
            },
        )
    events = _read_audit(audit_path)
    failed = [e for e in events if e["event"] == "ui_action_failed"]
    assert failed
    for e in failed:
        if "error_code" in e:
            assert e["error_code"] == "activate_failed"


# ══════════════════════════════════════════════════════════════════════════════
# Section 24 — Rate limit
# ══════════════════════════════════════════════════════════════════════════════


def test_rate_limit_activation_prefix_marked_expensive():
    text = _SERVER_PY_PATH.read_text(encoding="utf-8")
    assert "/api/mcp/activation/" in text
    m = re.search(r"_EXPENSIVE_PREFIXES\s*=\s*\(([^)]+)\)", text, re.DOTALL)
    assert m
    assert "/api/mcp/activation/" in m.group(1)


# ══════════════════════════════════════════════════════════════════════════════
# Section 25 — Health expose activation_service
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_health_exposes_activation_service_component(monkeypatch):
    app, *_ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    d = resp.json()
    assert "activation_service" in d.get("components", {})


@pytest.mark.asyncio
async def test_health_phase_at_least_20b3(monkeypatch):
    """Phase ≥ 20B-3 (élargie pour 20B-4+)."""
    app, *_ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    assert resp.json()["phase"] in ("20B-3", "20B-4", "20B-5", "20B-6", "21")


# ══════════════════════════════════════════════════════════════════════════════
# Section 26 — JS smoke
# ══════════════════════════════════════════════════════════════════════════════


def test_panels_js_has_activation_propose_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "submitMcpActivationPropose" in text
    assert "openMcpActivationProposeModal" in text


def test_panels_js_has_activation_execute_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "submitMcpActivationExecute" in text
    assert "openMcpActivationExecuteModal" in text


def test_panels_js_has_activation_deactivate_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "submitMcpActivationDeactivate" in text
    assert "openMcpActivationDeactivateModal" in text


def test_panels_js_activate_tool_prefix_defined():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "_MCP_ACTIVATE_TOOL_PREFIX" in text
    assert "mcp_activate:" in text


def test_panels_js_purge_includes_activate_ticket_prefix():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"function\s+_mcpPurgeExpiredInstallState\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*",
        text,
    )
    assert m
    body = m.group(1)
    assert "mcp_activate_ticket_" in body


def test_panels_js_activate_ticket_mapping_helpers():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "_mcpSetActivateTicketMapping" in text
    assert "_mcpGetServerIdForActivateTicket" in text
    assert "_mcpClearActivateTicketMapping" in text


def test_panels_js_get_server_id_for_activate_ticket_independent_of_marker():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"function\s+_mcpGetServerIdForActivateTicket\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*",
        text,
    )
    assert m
    body = m.group(1)
    assert "_mcpGetMarker" not in body
    assert "mcp_marker_" not in body


def test_panels_js_approve_captures_activate_marker():
    """submitMcpApprovalApprove essaie d'abord install puis activate mapping."""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"async\s+function\s+submitMcpApprovalApprove\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*window",
        text,
    )
    assert m
    body = m.group(1)
    assert "_mcpGetServerIdForActivateTicket" in body


def test_panels_js_no_local_storage_for_activation_state():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"Phase 20B-3[\s\S]*?(?=Phase 20B-4|/\* =====|window\.loadMcp\s*$|\Z)",
        text,
    )
    if m:
        section = m.group(0)
        assert "localStorage" not in section


def test_panels_js_deactivate_button_on_active_status():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "openMcpActivationDeactivateModal" in text
    assert "'active'" in text or '"active"' in text


def test_panels_js_propose_activation_button_on_installed():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "openMcpActivationProposeModal" in text
    assert "'installed'" in text or '"installed"' in text


def test_panels_js_deactivate_clears_no_marker():
    """Deactivate ne lit pas / ne clear pas de marker (action sans approval)."""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"async\s+function\s+submitMcpActivationDeactivate\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*window",
        text,
    )
    assert m
    body = m.group(1)
    # Pas d'usage de marker dans le body (le body envoyé ne contient pas marker)
    assert "marker:" not in body
