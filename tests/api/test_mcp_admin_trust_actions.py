"""
Tests Phase 20B-6 — Trust score manual update UI.

Point central :
  Modifier trust_score peut indirectement débloquer des chaînes d'autorisation
  futures (notamment via patterns AutoApprove). Donc double opt-in obligatoire
  + justification obligatoire.

20B-6 = manual update (pas un recompute automatique).
Aucun import score_package / TrustReport / PackageMetadata côté production.

Couverture :
  - Auth (401/403)
  - Confirmation backend + phrase = server_id exact
  - Validation trust_score stricte : None/absent refusé, refus bool/float/négatif/>100
  - Validation justification obligatoire (10..256 trim, UTF-8 lisible accents OK,
    refus caractères de contrôle)
  - body.server_id == path
  - Catalog unavailable 503, server_not_found 404
  - Status REMOVED refusé ; QUARANTINED/DECLARED/INSTALLED/ACTIVE autorisés
  - Double opt-in matrice 2×2 LUMENA_MCP_LIVE × LUMENA_MCP_TRUST_LIVE
  - Dry-run STRICT : 0 call update_trust_score
  - Live double opt-in : 1 call exactement
  - Idempotent no-op live : 0 call si valeur identique
  - trust_score_old null accepté (server sans score précédent)
  - Audit anti-fuite : justification raw, display_name, package_spec,
    TrustReport.factors, ServerEntry brut jamais loggués
  - Réponse whitelist (dry-run, live mutation, idempotent no-op)
  - Aucun appel ApprovalQueue / Install / Activation / AutoApprove / marker /
    subprocess dans handler Trust
  - Aucun import score_package / TrustReport / PackageMetadata
  - Aucun nouveau singleton
  - JS smoke : bouton, modal, handler
"""
from __future__ import annotations

import json
import re
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
# Fakes
# ──────────────────────────────────────────────────────────────────────────────


class _FakeStatus:
    def __init__(self, value: str):
        self.value = value


class _FakeServerEntry:
    def __init__(self, server_id: str, status: str = "installed",
                 trust_score: Optional[int] = 50):
        self.server_id = server_id
        self.status = _FakeStatus(status)
        self.trust_score = trust_score
        self.display_name = f"SECRET_DISPLAY_LEAK_{server_id}"
        self.package_spec = f"npm:SECRET_PKG_LEAK_{server_id}"
        self.version = f"SECRET_VERSION_LEAK_{server_id}"
        self.notes = f"SECRET_NOTES_LEAK_{server_id}"
        self.owner_profile = "test"
        self.added_at = "2026-06-01T00:00:00Z"
        self.updated_at = "2026-06-01T00:00:00Z"
        self.last_active_at = None


class _FakeCatalog:
    def __init__(self, entries: Optional[List[_FakeServerEntry]] = None):
        self._entries: Dict[str, _FakeServerEntry] = {
            e.server_id: e for e in (entries or [])
        }
        self.update_trust_score_calls: List[Dict[str, Any]] = []
        self.update_should_raise: Optional[str] = None

    def get_server(self, server_id):
        return self._entries.get(server_id)

    def update_trust_score(self, server_id, trust_score):
        self.update_trust_score_calls.append({
            "server_id": server_id, "trust_score": trust_score,
        })
        if self.update_should_raise is not None:
            raise ValueError(self.update_should_raise)
        if server_id in self._entries:
            self._entries[server_id].trust_score = trust_score
        return self._entries.get(server_id)


def _make_app(
    *,
    entries: Optional[List[_FakeServerEntry]] = None,
    no_catalog: bool = False,
    with_auth_override: bool = True,
) -> tuple[FastAPI, _FakeCatalog]:
    from web.routes import deps, mcp as mcp_routes
    cat = _FakeCatalog(entries=entries or [])
    deps._MCP_SERVER_CATALOG_SINGLETON = None if no_catalog else cat
    app = FastAPI()
    app.include_router(mcp_routes.router)
    if with_auth_override:
        app.dependency_overrides[deps.verify_admin_token] = lambda: None
    return app, cat


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


def _valid_body(sid: str = "alice") -> Dict[str, Any]:
    return {
        "confirmed": True,
        "confirmation_phrase": sid,
        "server_id": sid,
        "trust_score": 80,
        "justification": "Revision securite suite a audit interne",
    }


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Auth
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_trust_route_requires_admin_token_401(monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app, _ = _make_app(
        entries=[_FakeServerEntry("alice")], with_auth_override=False
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json={"confirmed": True}
        )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_trust_route_bad_token_403(monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app, _ = _make_app(
        entries=[_FakeServerEntry("alice")], with_auth_override=False
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update",
            headers={"Authorization": "Bearer wrong"},
            json={"confirmed": True},
        )
    assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Confirmation backend
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_trust_without_confirmed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice")])
    body = _valid_body()
    del body["confirmed"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/alice/trust/update", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_required"


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Phrase = server_id exact
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("phrase", ["ALICE", "bob", "", "alice ", " alice"])
async def test_trust_phrase_must_match_server_id_exact(monkeypatch, tmp_path, phrase):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice")])
    body = _valid_body()
    body["confirmation_phrase"] = phrase
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/alice/trust/update", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_phrase_invalid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — server_id Phase 14 + Windows
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sid", ["Alice", ".hidden", "a/b", "con", "com1"],
)
async def test_trust_invalid_server_id_format(monkeypatch, tmp_path, sid):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/catalog/{sid}/trust/update",
            json={
                "confirmed": True, "confirmation_phrase": sid,
                "server_id": sid, "trust_score": 50,
                "justification": "valid justification x",
            },
        )
    # 400 (validation server_id) ou 404 (route mismatch FastAPI)
    assert resp.status_code in (400, 404)


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — trust_score strict (None/absent refusé)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ts_value,present",
    [
        (None, True),       # explicit None refusé
        (None, False),      # absent refusé
        (True, True),       # bool refusé
        (False, True),      # bool False refusé
        (50.0, True),       # float refusé
        ("50", True),       # str refusé
        (-1, True),         # négatif refusé
        (101, True),        # >100 refusé
    ],
)
async def test_trust_score_strict_invalid(monkeypatch, tmp_path, ts_value, present):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice")])
    body = _valid_body()
    if not present:
        del body["trust_score"]
    else:
        body["trust_score"] = ts_value
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/alice/trust/update", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "trust_score_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize("ts_value", [0, 50, 100])
async def test_trust_score_strict_valid_at_bounds(monkeypatch, tmp_path, ts_value):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alice", trust_score=10)])
    body = _valid_body()
    body["trust_score"] = ts_value
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/alice/trust/update", json=body)
    assert resp.status_code == 200, resp.text


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Justification obligatoire
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "justif,present",
    [
        (None, True),
        (None, False),
        (123, True),
        ("", True),
        ("   ", True),
    ],
)
async def test_justification_required(monkeypatch, tmp_path, justif, present):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice")])
    body = _valid_body()
    if not present:
        del body["justification"]
    else:
        body["justification"] = justif
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/alice/trust/update", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "justification_required"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "justif",
    [
        "short",                  # 5 chars < 10
        "abcdefghi",              # 9 chars < 10
        "x" * 257,                # > 256
        "valid text\x00null",     # control char (NUL)
        "with\x1fcontrol",        # C0 control
        "with\x7fdelete",         # DEL
        "with\rcarriage",         # CR
        "with\nnewline",          # LF
    ],
)
async def test_justification_invalid(monkeypatch, tmp_path, justif):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice")])
    body = _valid_body()
    body["justification"] = justif
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/alice/trust/update", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "justification_invalid"


@pytest.mark.asyncio
async def test_justification_with_french_accents_accepted(monkeypatch, tmp_path):
    """UTF-8 lisible accepté, notamment accents français."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alice", trust_score=10)])
    body = _valid_body()
    body["justification"] = "Révision sécurité après audit"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/alice/trust/update", json=body)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "justif",
    [
        "Révision sécurité après audit",       # accents fr
        "Mise à jour suite à un événement",    # accents
        "Évaluation 中文 mixed UTF-8",         # mix
        "x" * 10,                              # min boundary
        "x" * 256,                             # max boundary
        "Trimmed   valid text   ",             # trim valide
    ],
)
async def test_justification_valid_cases(monkeypatch, tmp_path, justif):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alice", trust_score=10)])
    body = _valid_body()
    body["justification"] = justif
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/alice/trust/update", json=body)
    assert resp.status_code == 200, resp.text


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — body.server_id != path
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_body_server_id_mismatch_path_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice")])
    body = _valid_body("alice")
    body["server_id"] = "bob"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/catalog/alice/trust/update", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "server_id_invalid_format"


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — Catalog unavailable / server_not_found
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_catalog_unavailable_503(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(no_catalog=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    assert resp.status_code == 503
    assert resp.json().get("detail", {}).get("error_code") == "catalog_unavailable"


@pytest.mark.asyncio
async def test_server_not_found_404(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    assert resp.status_code == 404
    assert resp.json().get("detail", {}).get("error_code") == "server_not_found"


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — Status REMOVED refusé, autres autorisés
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_status_removed_refused(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(entries=[_FakeServerEntry("alice", status="removed")])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "server_id_invalid_status"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ["declared", "installed", "active", "quarantined"],
)
async def test_status_non_removed_allowed(monkeypatch, tmp_path, status):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, _ = _make_app(
        entries=[_FakeServerEntry("alice", status=status, trust_score=10)]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    assert resp.status_code == 200, resp.text


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — Double opt-in matrice 2×2
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "live,trust_live,expected_dry",
    [
        (None, None, True),
        ("1", None, True),
        (None, "1", True),
        ("1", "1", False),
        ("0", "1", True),
        ("1", "0", True),
        ("false", "true", True),
        ("yes", "yes", False),
    ],
)
async def test_double_optin_matrix(monkeypatch, tmp_path, live, trust_live, expected_dry):
    _clear_audit_path(monkeypatch, tmp_path)
    if live is None:
        monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    else:
        monkeypatch.setenv("LUMENA_MCP_LIVE", live)
    if trust_live is None:
        monkeypatch.delenv("LUMENA_MCP_TRUST_LIVE", raising=False)
    else:
        monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", trust_live)
    app, cat = _make_app(entries=[_FakeServerEntry("alice", trust_score=10)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    assert resp.status_code == 200
    d = resp.json()
    if expected_dry:
        assert d.get("would_update_trust_score") is True
        assert d["forced_dry_run"] is True
        assert cat.update_trust_score_calls == []
    else:
        assert d["updated"] is True
        assert len(cat.update_trust_score_calls) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — Dry-run STRICT : 0 call update_trust_score
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dry_run_does_not_call_update_trust_score(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    monkeypatch.delenv("LUMENA_MCP_TRUST_LIVE", raising=False)
    app, cat = _make_app(entries=[_FakeServerEntry("alice", trust_score=10)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    assert resp.status_code == 200
    assert resp.json()["would_update_trust_score"] is True
    assert cat.update_trust_score_calls == []


@pytest.mark.asyncio
async def test_dry_run_only_live_set_does_not_call(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.delenv("LUMENA_MCP_TRUST_LIVE", raising=False)
    app, cat = _make_app(entries=[_FakeServerEntry("alice", trust_score=10)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    assert cat.update_trust_score_calls == []


@pytest.mark.asyncio
async def test_dry_run_only_trust_set_does_not_call(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, cat = _make_app(entries=[_FakeServerEntry("alice", trust_score=10)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    assert cat.update_trust_score_calls == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 12 — Live double opt-in : 1 call exactement
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_live_double_optin_calls_update_once(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, cat = _make_app(entries=[_FakeServerEntry("alice", trust_score=10)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["updated"] is True
    assert d["trust_score_old"] == 10
    assert d["trust_score_new"] == 80
    assert d["idempotent"] is False
    assert d["live_mode"] is True
    assert d["trust_live_mode"] is True
    assert len(cat.update_trust_score_calls) == 1
    assert cat.update_trust_score_calls[0] == {"server_id": "alice", "trust_score": 80}


# ══════════════════════════════════════════════════════════════════════════════
# Section 13 — Idempotent no-op live : 0 call si valeur identique
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_idempotent_noop_live_does_not_call_update(monkeypatch, tmp_path):
    """Si trust_score_proposed == trust_score_current, aucun call
    update_trust_score (no-op préférence v2). Audit outcome=noop."""
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, cat = _make_app(entries=[_FakeServerEntry("alice", trust_score=80)])
    body = _valid_body()
    body["trust_score"] = 80  # même valeur
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=body
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["updated"] is False
    assert d["idempotent"] is True
    assert d["trust_score_old"] == 80
    assert d["trust_score_new"] == 80
    assert cat.update_trust_score_calls == []
    # Audit : outcome="noop"
    events = _read_audit(audit_path)
    completed = [e for e in events if e["event"] == "ui_action_completed"]
    assert completed
    assert completed[-1]["outcome"] == "noop"
    assert completed[-1]["idempotent"] is True


@pytest.mark.asyncio
async def test_idempotent_noop_with_null_old_and_zero_new_calls_update(
    monkeypatch, tmp_path
):
    """trust_score_old=None, trust_score_new=0 → mutation (différent)."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, cat = _make_app(entries=[_FakeServerEntry("alice", trust_score=None)])
    body = _valid_body()
    body["trust_score"] = 0
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=body
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["updated"] is True
    assert d["trust_score_old"] is None
    assert d["trust_score_new"] == 0
    assert d["idempotent"] is False
    assert len(cat.update_trust_score_calls) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Section 14 — trust_score_old null accepté
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_trust_score_old_null_in_dry_run_response(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    monkeypatch.delenv("LUMENA_MCP_TRUST_LIVE", raising=False)
    app, _ = _make_app(entries=[_FakeServerEntry("alice", trust_score=None)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["trust_score_old"] is None
    assert d["trust_score_proposed"] == 80


@pytest.mark.asyncio
async def test_trust_score_old_null_in_live_response(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alice", trust_score=None)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["updated"] is True
    assert d["trust_score_old"] is None
    assert d["trust_score_new"] == 80


# ══════════════════════════════════════════════════════════════════════════════
# Section 15 — Audit UI étendu (whitelist + anti-fuite)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_audit_completed_event_on_live_update(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alice", trust_score=10)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/mcp/catalog/alice/trust/update", json=_valid_body())
    events = _read_audit(audit_path)
    kinds = [e["event"] for e in events]
    assert "ui_action_requested" in kinds
    assert "ui_action_completed" in kinds
    completed = [e for e in events if e["event"] == "ui_action_completed"][-1]
    assert completed["phase"] == "20B-6"
    assert completed["action"] == "trust_score_manual_update"
    assert completed["outcome"] == "updated"
    assert completed["trust_score_old"] == 10
    assert completed["trust_score_new"] == 80
    assert completed["idempotent"] is False


@pytest.mark.asyncio
async def test_audit_never_contains_justification_raw_with_accents(
    monkeypatch, tmp_path
):
    """Marqueur SECRET_JUSTIFICATION_LEAK avec accents — jamais loggué."""
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alice", trust_score=10)])
    body = _valid_body()
    secret = "Révision SECRETJUSTIFLEAKMARKER après audit"
    body["justification"] = secret
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/mcp/catalog/alice/trust/update", json=body)
    blob = json.dumps(_read_audit(audit_path), ensure_ascii=False)
    assert "SECRETJUSTIFLEAKMARKER" not in blob
    assert "Révision" not in blob  # accent UTF-8 jamais loggué non plus
    # Seul justification_length présent
    for e in _read_audit(audit_path):
        if "justification_length" in e:
            assert e["justification_length"] == len(secret.strip())
            assert "justification" not in e


@pytest.mark.asyncio
async def test_audit_never_contains_display_name_or_package_spec(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alice", trust_score=10)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/mcp/catalog/alice/trust/update", json=_valid_body())
    blob = json.dumps(_read_audit(audit_path))
    assert "SECRET_DISPLAY_LEAK" not in blob
    assert "SECRET_PKG_LEAK" not in blob
    assert "SECRET_VERSION_LEAK" not in blob
    assert "SECRET_NOTES_LEAK" not in blob
    assert "display_name" not in blob
    assert "package_spec" not in blob
    assert '"version"' not in blob
    assert '"notes"' not in blob


@pytest.mark.asyncio
async def test_audit_never_contains_confirmation_phrase_raw(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alicesecret", trust_score=10)])
    body = _valid_body("alicesecret")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/catalog/alicesecret/trust/update", json=body
        )
    blob = json.dumps(_read_audit(audit_path))
    assert '"confirmation_phrase"' not in blob


@pytest.mark.asyncio
async def test_audit_actor_token_hash_sha256(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    monkeypatch.delenv("LUMENA_TEST_MODE", raising=False)
    app, _ = _make_app(entries=[_FakeServerEntry("alice", trust_score=10)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/catalog/alice/trust/update",
            headers={"Authorization": "Bearer some-real-token-xyz"},
            json=_valid_body(),
        )
    blob = json.dumps(_read_audit(audit_path))
    assert "some-real-token-xyz" not in blob
    for e in _read_audit(audit_path):
        if "actor_token_hash" in e:
            assert re.fullmatch(r"sha256:[0-9a-f]{64}", e["actor_token_hash"])


# ══════════════════════════════════════════════════════════════════════════════
# Section 16 — Anti-fuite réponse : ServerEntry brut + SECRET markers
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_response_whitelist_live_mutation(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alice", trust_score=10)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    d = resp.json()
    allowed = {
        "updated", "server_id", "trust_score_old", "trust_score_new",
        "idempotent", "live_mode", "trust_live_mode",
    }
    assert set(d.keys()) <= allowed


@pytest.mark.asyncio
async def test_response_whitelist_dry_run(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app, _ = _make_app(entries=[_FakeServerEntry("alice", trust_score=10)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    d = resp.json()
    allowed = {
        "would_update_trust_score", "server_id", "trust_score_old",
        "trust_score_proposed", "live_mode", "trust_live_mode", "forced_dry_run",
    }
    assert set(d.keys()) <= allowed


@pytest.mark.asyncio
async def test_response_never_contains_secret_markers(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, _ = _make_app(entries=[_FakeServerEntry("alice", trust_score=10)])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    blob = resp.text
    assert "SECRET_DISPLAY_LEAK" not in blob
    assert "SECRET_PKG_LEAK" not in blob
    assert "SECRET_NOTES_LEAK" not in blob
    assert "SECRET_VERSION_LEAK" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 17 — Grep statique : aucun import score_package / TrustReport
# ══════════════════════════════════════════════════════════════════════════════


def test_mcp_py_no_score_package_import():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    forbidden = [
        "from src.mcp.trust_scoring import score_package",
        "from src.mcp.trust_scoring import TrustReport",
        "from src.mcp.trust_scoring import PackageMetadata",
        "from src.mcp.trust_scoring import TrustFactor",
        "score_package(",
        "TrustReport(",
        "PackageMetadata(",
    ]
    for token in forbidden:
        assert token not in text, (
            f"{token} trouvé dans mcp.py (interdit Phase 20B-6 — "
            "aucun import score_package / TrustReport / PackageMetadata)"
        )


def test_mcp_py_no_phase14_private_import_for_trust():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert (
        "from src.mcp.server_catalog import _validate_trust_score" not in text
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 18 — Handler trust : aucun appel autre que catalog.update_trust_score
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


def test_trust_handler_no_other_mcp_calls():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    body = _find_function_body(text, "mcp_catalog_trust_update")
    assert body is not None
    forbidden = [
        ".propose(", ".approve(", ".reject(",
        ".propose_install(", ".execute_approved_install(",
        ".propose_activation(", ".activate(", ".deactivate(",
        ".add_server(", ".remove_server(",
        ".add_pattern(", ".remove_pattern(",
        "_take_marker", "_put_marker",
        "MCPSandboxRunner", "MCPInstallOrchestrator(",
        "MCPActivationService(", "AutoApproveEngine(",
        "score_package", "TrustReport", "PackageMetadata",
    ]
    for token in forbidden:
        assert token not in body, (
            f"{token} trouvé dans handler trust (interdit Phase 20B-6)"
        )


def test_trust_handler_does_not_instantiate_fresh_catalog():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    body = _find_function_body(text, "mcp_catalog_trust_update")
    assert body is not None
    assert "MCPServerCatalog()" not in body


def test_no_subprocess_in_mcp_py():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert "import subprocess" not in text


def test_no_put_or_patch_for_trust():
    """Trust update reste un POST (Phase 20B-6). Phase I-6 ajoute des PUT
    mais ils ne ciblent JAMAIS les routes trust."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert "@router.patch" not in text
    import re as _re
    put_paths = _re.findall(r'@router\.put\(\s*"([^"]+)"', text)
    for path in put_paths:
        assert "trust" not in path, (
            f"PUT vers trust interdit : {path}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section 19 — Aucun nouveau singleton
# ══════════════════════════════════════════════════════════════════════════════


def test_deps_has_no_new_trust_singleton():
    text = _DEPS_PATH.read_text(encoding="utf-8")
    forbidden = [
        "_MCP_TRUST_SINGLETON",
        "_MCP_TRUST_SCORING_SINGLETON",
        "get_mcp_trust_singleton",
    ]
    for token in forbidden:
        assert token not in text, (
            f"{token} trouvé dans deps.py (aucun nouveau singleton Phase 20B-6)"
        )


def test_lifespan_does_not_init_trust_singleton():
    text = _LIFESPAN_PATH.read_text(encoding="utf-8")
    forbidden = [
        "_MCP_TRUST_SINGLETON",
        "_MCP_TRUST_SCORING_SINGLETON",
    ]
    for token in forbidden:
        assert token not in text, (
            f"{token} trouvé dans lifespan.py (aucun nouveau singleton Phase 20B-6)"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section 20 — Health phase 20B-6 + trust_live_mode
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_health_phase_20b6(monkeypatch):
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    d = resp.json()
    # Phase 21 substitue "20B-6" par "21" sur la route /health, sans changer
    # la doctrine 20B-6 (mutations Trust). On accepte les deux valeurs.
    assert d["phase"] in ("20B-6", "21")


@pytest.mark.asyncio
async def test_health_exposes_trust_live_mode_false_when_env_absent(monkeypatch):
    monkeypatch.delenv("LUMENA_MCP_TRUST_LIVE", raising=False)
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    d = resp.json()
    assert "trust_live_mode" in d
    assert d["trust_live_mode"] is False


@pytest.mark.asyncio
async def test_health_trust_live_mode_true_when_env_set(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    assert resp.json()["trust_live_mode"] is True


# ══════════════════════════════════════════════════════════════════════════════
# Section 21 — JS smoke
# ══════════════════════════════════════════════════════════════════════════════


def test_panels_js_has_trust_update_modal_and_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "openMcpTrustUpdateModal" in text
    assert "submitMcpTrustUpdate" in text


def test_panels_js_trust_button_on_catalog_entries():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "Ajuster trust_score" in text


def test_panels_js_trust_modal_validates_justification_length():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "_MCP_TRUST_JUSTIFICATION_MIN_LEN" in text
    assert "_MCP_TRUST_JUSTIFICATION_MAX_LEN" in text


def test_panels_js_trust_modal_requires_phrase_equal_to_server_id():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"function\s+_mcpUpdateTrustButton\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*",
        text,
    )
    assert m
    body = m.group(1)
    assert "expectedServerId" in body


def test_panels_js_trust_handler_posts_to_correct_route():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "/api/mcp/catalog/" in text
    assert "/trust/update" in text


def test_panels_js_no_local_storage_in_trust_section():
    """Aucun localStorage utilisé pour le state trust."""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"Phase 20B-6[\s\S]*?(?=Phase 20B-7|/\* =====|window\.loadMcp\s*$|\Z)",
        text,
    )
    if m:
        section = m.group(0)
        assert "localStorage" not in section


def test_panels_js_trust_modal_uses_double_optin_not_just_live():
    """Le modal Trust doit calculer trustDoubleOptin = liveMode AND trustLiveMode,
    pas se baser uniquement sur window._mcpLiveMode (qui ignore l'opt-in trust)."""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"function\s+openMcpTrustUpdateModal\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*",
        text,
    )
    assert m, "openMcpTrustUpdateModal introuvable"
    body = m.group(1)
    # Doit lire les deux flags
    assert "window._mcpLiveMode" in body
    assert "window._mcpTrustLiveMode" in body
    # Doit calculer trustDoubleOptin = combo
    assert "trustDoubleOptin" in body
    assert re.search(r"trustDoubleOptin\s*=\s*[a-zA-Z_]+\s*&&\s*[a-zA-Z_]+", body), (
        "trustDoubleOptin doit être calculé par AND des deux flags"
    )


def test_panels_js_trust_modal_drysuffix_uses_double_optin():
    """drySuffix doit être basé sur trustDoubleOptin, pas seulement liveMode."""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"function\s+openMcpTrustUpdateModal\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*",
        text,
    )
    assert m
    body = m.group(1)
    # drySuffix doit être calculé via trustDoubleOptin
    assert re.search(
        r"drySuffix\s*=\s*trustDoubleOptin\s*\?", body
    ), "drySuffix doit être ternaire basé sur trustDoubleOptin"


def test_panels_js_trust_modal_banner_three_cases():
    """3 cas de bandeau distincts :
      - LIVE actif (rouge, double opt-in OK)
      - dry-run avec LUMENA_MCP_LIVE manquant
      - dry-run avec LUMENA_MCP_TRUST_LIVE manquant
    """
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"function\s+openMcpTrustUpdateModal\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*",
        text,
    )
    assert m
    body = m.group(1)
    # Bandeau LIVE
    assert "Mode LIVE actif" in body or "Mode LIVE" in body
    # Bandeau dry-run TRUST manquant explicitement
    assert "LUMENA_MCP_TRUST_LIVE manquant" in body
    # Bandeau dry-run LIVE manquant explicitement
    assert "LUMENA_MCP_LIVE manquant" in body


def test_panels_js_loadMcp_stores_trust_live_mode_in_window():
    """loadMcp() doit stocker health.trust_live_mode dans window._mcpTrustLiveMode."""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"export\s+async\s+function\s+loadMcp\s*\(\s*\)\s*\{([\s\S]*?)\nwindow\.loadMcp",
        text,
    )
    assert m, "loadMcp introuvable"
    body = m.group(1)
    assert "window._mcpTrustLiveMode" in body
    assert re.search(r"_mcpTrustLiveMode\s*=\s*!!d\.trust_live_mode", body), (
        "loadMcp doit lire d.trust_live_mode depuis /health"
    )


def test_panels_js_trust_score_uses_number_not_parseint():
    """trust_score : Number() + Number.isInteger pour refuser "50.5".

    parseInt convertirait "50.5" en 50 silencieusement, masquant la saisie
    invalide. On exige Number() qui retourne NaN sur "abc" + isInteger qui
    refuse les décimaux.
    """
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"async\s+function\s+submitMcpTrustUpdate\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*window\.submitMcpTrustUpdate",
        text,
    )
    assert m, "submitMcpTrustUpdate introuvable"
    body = m.group(1)
    # parseInt sur scoreRaw / trustScore interdit
    assert "parseInt(scoreRaw" not in body, "parseInt sur scoreRaw interdit"
    assert "parseInt(score" not in body or "parseInt(scoreRaw" not in body
    # Number + isInteger requis
    assert "Number(scoreRaw)" in body or "Number(score" in body
    assert "Number.isInteger" in body


# ══════════════════════════════════════════════════════════════════════════════
# Section 22 — update_trust_score failure raises mapped error_code
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_update_trust_score_raises_trust_score_invalid_mapped(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    cat = _FakeCatalog(entries=[_FakeServerEntry("alice", trust_score=10)])
    cat.update_should_raise = "context_invalid:trust_score_range"
    from web.routes import deps, mcp as mcp_routes
    deps._MCP_SERVER_CATALOG_SINGLETON = cat
    app = FastAPI()
    app.include_router(mcp_routes.router)
    app.dependency_overrides[deps.verify_admin_token] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "trust_score_invalid"


@pytest.mark.asyncio
async def test_update_trust_score_other_failure_maps_to_generic(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    cat = _FakeCatalog(entries=[_FakeServerEntry("alice", trust_score=10)])
    cat.update_should_raise = "unexpected_internal_error_boom-RAW-LEAK"
    from web.routes import deps, mcp as mcp_routes
    deps._MCP_SERVER_CATALOG_SINGLETON = cat
    app = FastAPI()
    app.include_router(mcp_routes.router)
    app.dependency_overrides[deps.verify_admin_token] = lambda: None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/catalog/alice/trust/update", json=_valid_body()
        )
    assert resp.status_code == 500
    assert resp.json().get("detail", {}).get("error_code") == "update_trust_score_failed"
    # Aucun message brut leaké
    blob = resp.text
    assert "boom-RAW-LEAK" not in blob
