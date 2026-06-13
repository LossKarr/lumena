"""
Tests Phase 20B-1 — Approvals UI mutations (approve / reject).

Couverture obligatoire :
  - Auth (401/403)
  - Confirmation côté backend (confirmed:true requis)
  - Validation reason (reject) : trim, min 3, max 500
  - Kill switch LUMENA_MCP_LIVE : dry_run forcé si absent/falsy
  - Mode live : ApprovalQueue.approve/reject appelée pour de vrai
  - Cache marker UUID4 : put/take one-shot, TTL 5 min, max 256, jamais GET
  - Audit UI dédié data/mcp_admin_audit/audit.jsonl
  - actor_token_hash format sha256:... jamais token clair
  - reason JAMAIS dans l'audit (reason_length uniquement)
  - error_code court, jamais message brut d'exception
  - Réponse n'expose JAMAIS ApprovalResult brut (uniquement marker)
  - Rate limiting : /api/mcp/approvals/ dans _EXPENSIVE_PREFIXES
  - Grep statique : whitelist par handler nommé + encadrement par helpers
  - HTML/JS smoke : boutons + modals + bandeau live_mode
"""
from __future__ import annotations

import json
import re
import threading
import time
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
# Fakes minimaux pour ApprovalQueue (Phase 10 API)
# ──────────────────────────────────────────────────────────────────────────────


class _FakePendingAction:
    """Vue read-only d'une action PENDING (pas d'args)."""

    def __init__(self, action_id: str):
        self.id = action_id
        self.tool_name = "fake.tool"
        self.policy = None
        self.caller_kind = "test"
        self.risk_summary = ""
        self.proposed_at = "2026-06-01T00:00:00Z"


class _FakeApprovalResult:
    """Stand-in pour ApprovalResult. JAMAIS sérialisé vers l'UI."""

    def __init__(self, action_id: str):
        self.action_id = action_id
        self.outcome = "APPROVED"
        self.args = {"secret_arg_marker": "SHOULD_NEVER_LEAK_XYZ"}
        self.decision = "APPROVE"


class _FakeApprovalQueue:
    """Implémentation minimale : list_pending / approve / reject avec spy."""

    def __init__(self, pending_ids: Optional[List[str]] = None):
        self._pending = [_FakePendingAction(a) for a in (pending_ids or [])]
        self.approve_calls: List[str] = []
        self.reject_calls: List[Dict[str, Any]] = []
        self.approve_should_raise = False
        self.reject_should_return = True
        self.reject_should_raise = False

    def list_pending(self):
        return list(self._pending)

    def approve(self, action_id: str):
        self.approve_calls.append(action_id)
        if self.approve_should_raise:
            raise RuntimeError("boom-approve-raw-message-should-not-leak")
        self._pending = [a for a in self._pending if a.id != action_id]
        return _FakeApprovalResult(action_id)

    def reject(self, action_id: str, reason: str) -> bool:
        self.reject_calls.append({"action_id": action_id, "reason": reason})
        if self.reject_should_raise:
            raise RuntimeError("boom-reject-raw-message-should-not-leak")
        if not self.reject_should_return:
            return False
        self._pending = [a for a in self._pending if a.id != action_id]
        return True


# ──────────────────────────────────────────────────────────────────────────────
# Helpers app + monkey-patching
# ──────────────────────────────────────────────────────────────────────────────


def _make_app(
    *,
    pending_ids: Optional[List[str]] = None,
    fake_queue: Optional[_FakeApprovalQueue] = None,
    with_auth_override: bool = True,
) -> tuple[FastAPI, _FakeApprovalQueue]:
    from web.routes import deps, mcp as mcp_routes

    queue = fake_queue or _FakeApprovalQueue(pending_ids=pending_ids or [])
    # Phase 20B-1 : remplace singleton lifespan
    deps._MCP_APPROVAL_QUEUE_SINGLETON = queue

    # Force aussi le fallback lecture seule pour cohérence
    mcp_routes._APPROVAL_RESULT_CACHE.clear()

    app = FastAPI()
    app.include_router(mcp_routes.router)
    if with_auth_override:
        app.dependency_overrides[deps.verify_admin_token] = lambda: None
    return app, queue


def _clear_audit_path(monkeypatch, tmp_path):
    """Redirige le journal d'audit UI vers tmp_path et le wipe."""
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
# Section 1 — Auth (401/403)
# ══════════════════════════════════════════════════════════════════════════════


def _new_aid() -> str:
    """Génère un action_id UUID4 hex valide (32 lowercase hex)."""
    return uuid.uuid4().hex


# Pour les tests d'auth, on utilise un UUID4 fixe valide
_FIXED_AID_FOR_AUTH = "0123456789abcdef0123456789abcdef"
# 0123...cdef est pas UUID4 → on génère un vrai
_FIXED_AID_FOR_AUTH = uuid.uuid4().hex


_MUTATIVE_PATHS = [
    f"/api/mcp/approvals/{_FIXED_AID_FOR_AUTH}/approve",
    f"/api/mcp/approvals/{_FIXED_AID_FOR_AUTH}/reject",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _MUTATIVE_PATHS)
async def test_post_route_requires_admin_token_401(path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app, _ = _make_app(pending_ids=[_FIXED_AID_FOR_AUTH], with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(path, json={"confirmed": True, "reason": "abc"})
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _MUTATIVE_PATHS)
async def test_post_route_bad_token_403(path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app, _ = _make_app(pending_ids=[_FIXED_AID_FOR_AUTH], with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            path,
            headers={"Authorization": "Bearer wrong-token"},
            json={"confirmed": True, "reason": "abc"},
        )
    assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Confirmation côté backend obligatoire
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_approve_without_confirmed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    aid = _new_aid()
    app, queue = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/mcp/approvals/{aid}/approve", json={})
    assert resp.status_code == 400
    body = resp.json()
    detail = body.get("detail", {})
    assert detail.get("error_code") == "confirmation_required"
    assert queue.approve_calls == []


@pytest.mark.asyncio
async def test_approve_confirmed_false_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    aid = _new_aid()
    app, queue = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{aid}/approve", json={"confirmed": False}
        )
    assert resp.status_code == 400
    assert queue.approve_calls == []


@pytest.mark.asyncio
async def test_reject_without_confirmed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    aid = _new_aid()
    app, queue = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{aid}/reject", json={"reason": "valid reason"}
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_required"
    assert queue.reject_calls == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Validation reason (reject)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason_payload",
    [
        None,
        "",
        " ",
        "  ",
        "ab",
        "a" * 501,
        123,
        ["not", "a", "string"],
    ],
)
async def test_reject_invalid_reason_400(monkeypatch, tmp_path, reason_payload):
    _clear_audit_path(monkeypatch, tmp_path)
    aid = _new_aid()
    app, queue = _make_app(pending_ids=[aid])
    payload = {"confirmed": True}
    if reason_payload is not None:
        payload["reason"] = reason_payload
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(f"/api/mcp/approvals/{aid}/reject", json=payload)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "reason_invalid"
    assert queue.reject_calls == []


@pytest.mark.asyncio
async def test_reject_reason_trimmed_min_len_valid(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    aid = _new_aid()
    app, queue = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{aid}/reject",
            json={"confirmed": True, "reason": "   bug   "},
        )
    assert resp.status_code == 200
    assert queue.reject_calls == [{"action_id": aid, "reason": "bug"}]


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Kill switch dry_run forcé
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_approve_dry_run_does_not_mutate_queue(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    aid = _new_aid()
    app, queue = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{aid}/approve", json={"confirmed": True}
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["would_approve"] is True
    assert d["live_mode"] is False
    assert d["forced_dry_run"] is True
    assert d["marker"] is None
    assert queue.approve_calls == []


@pytest.mark.asyncio
async def test_approve_dry_run_falsy_values(monkeypatch, tmp_path):
    """Valeurs falsy (0, false, no, off, vide) → dry_run forcé."""
    _clear_audit_path(monkeypatch, tmp_path)
    for raw in ("", "0", "false", "no", "off", "  "):
        monkeypatch.setenv("LUMENA_MCP_LIVE", raw)
        aid = _new_aid()
        app, queue = _make_app(pending_ids=[aid])
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                f"/api/mcp/approvals/{aid}/approve", json={"confirmed": True}
            )
        assert resp.status_code == 200
        assert resp.json()["live_mode"] is False
        assert queue.approve_calls == []


@pytest.mark.asyncio
async def test_reject_dry_run_does_not_mutate_queue(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    aid = _new_aid()
    app, queue = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{aid}/reject",
            json={"confirmed": True, "reason": "not safe"},
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["would_reject"] is True
    assert d["live_mode"] is False
    assert d["forced_dry_run"] is True
    assert queue.reject_calls == []


@pytest.mark.asyncio
async def test_approve_dry_run_no_marker_created(monkeypatch, tmp_path):
    """Dry-run : aucune entrée dans le cache marker."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    from web.routes import mcp as mcp_routes

    aid = _new_aid()
    app, _ = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(f"/api/mcp/approvals/{aid}/approve", json={"confirmed": True})
    assert len(mcp_routes._APPROVAL_RESULT_CACHE) == 0


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Live mode : vraie mutation queue
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_approve_live_calls_queue_once(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    aid = _new_aid()
    app, queue = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{aid}/approve", json={"confirmed": True}
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["approved"] is True
    assert d["live_mode"] is True
    assert isinstance(d["marker"], str) and len(d["marker"]) == 32
    assert re.fullmatch(r"[0-9a-f]{32}", d["marker"])
    assert d["marker_ttl_s"] == 300
    assert queue.approve_calls == [aid]


@pytest.mark.asyncio
async def test_reject_live_calls_queue_once(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    aid = _new_aid()
    app, queue = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{aid}/reject",
            json={"confirmed": True, "reason": "denied"},
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["rejected"] is True
    assert d["live_mode"] is True
    assert queue.reject_calls == [{"action_id": aid, "reason": "denied"}]


@pytest.mark.asyncio
async def test_approve_live_unknown_action_404(monkeypatch, tmp_path):
    """action_id valide UUID4 mais pas dans pending → 404 approval_not_found."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    aid_present = _new_aid()
    aid_unknown = _new_aid()
    app, queue = _make_app(pending_ids=[aid_present])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{aid_unknown}/approve", json={"confirmed": True}
        )
    assert resp.status_code == 404
    assert resp.json().get("detail", {}).get("error_code") == "approval_not_found"
    assert queue.approve_calls == []


@pytest.mark.asyncio
async def test_approve_queue_unavailable_503(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    from web.routes import deps, mcp as mcp_routes

    deps._MCP_APPROVAL_QUEUE_SINGLETON = None
    monkeypatch.setattr(mcp_routes, "_get_approval_queue", lambda: None)
    app = FastAPI()
    app.include_router(mcp_routes.router)
    app.dependency_overrides[deps.verify_admin_token] = lambda: None
    aid = _new_aid()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{aid}/approve", json={"confirmed": True}
        )
    assert resp.status_code == 503
    assert resp.json().get("detail", {}).get("error_code") == "queue_unavailable"


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Cache marker (put/take one-shot, TTL, max size)
# ══════════════════════════════════════════════════════════════════════════════


def test_marker_put_take_one_shot():
    from web.routes import mcp as mcp_routes

    mcp_routes._APPROVAL_RESULT_CACHE.clear()
    aid = _new_aid()
    result = _FakeApprovalResult(aid)
    marker = mcp_routes._put_marker(aid, result)
    assert isinstance(marker, str) and len(marker) == 32
    got = mcp_routes._take_marker(marker)
    assert got is result
    got2 = mcp_routes._take_marker(marker)
    assert got2 is None


def test_marker_ttl_expiration(monkeypatch):
    from web.routes import mcp as mcp_routes

    mcp_routes._APPROVAL_RESULT_CACHE.clear()
    fake_clock = {"t": 1000.0}
    monkeypatch.setattr(
        mcp_routes.time, "monotonic", lambda: fake_clock["t"]
    )
    aid = _new_aid()
    result = _FakeApprovalResult(aid)
    marker = mcp_routes._put_marker(aid, result)
    fake_clock["t"] += mcp_routes._APPROVAL_CACHE_TTL_S + 1.0
    got = mcp_routes._take_marker(marker)
    assert got is None


def test_marker_max_size_eviction():
    from web.routes import mcp as mcp_routes

    mcp_routes._APPROVAL_RESULT_CACHE.clear()
    markers = []
    for i in range(mcp_routes._APPROVAL_CACHE_MAX_SIZE + 5):
        aid = _new_aid()
        m = mcp_routes._put_marker(aid, _FakeApprovalResult(aid))
        markers.append(m)
    assert len(mcp_routes._APPROVAL_RESULT_CACHE) <= mcp_routes._APPROVAL_CACHE_MAX_SIZE
    assert mcp_routes._take_marker(markers[0]) is None


def test_marker_cache_has_no_public_get_route():
    """Le module n'expose AUCUN moyen de lire le cache marker via une route HTTP.

    Vérifications :
      - aucune fonction `get_marker` / `read_marker` / `approval_marker` publique
      - aucune route GET ne mentionne `_take_marker` ou `_APPROVAL_RESULT_CACHE`
      - aucun `return _APPROVAL_RESULT_CACHE` ou similaire
    """
    from web.routes import mcp as mcp_routes

    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")

    # Aucun helper public d'accès au cache
    for forbidden_attr in ("get_marker", "read_marker", "approval_marker", "list_markers"):
        assert not hasattr(mcp_routes, forbidden_attr), (
            f"Helper public interdit présent : {forbidden_attr}"
        )

    # Aucun handler GET ne doit mentionner le cache ou _take_marker
    get_handler_pattern = re.compile(
        r"@router\.get\([^)]*\)\s*\n(?:async\s+def|def)\s+\w+\s*\([^)]*\):"
        r"(?P<body>[\s\S]*?)(?=^(?:@router\.|async\s+def|def))",
        re.MULTILINE,
    )
    for m in get_handler_pattern.finditer(text):
        body = m.group("body")
        assert "_APPROVAL_RESULT_CACHE" not in body, (
            "Une route GET référence le cache marker (interdit)"
        )
        assert "_take_marker" not in body, (
            "Une route GET appelle _take_marker (interdit)"
        )
        assert "_put_marker" not in body, (
            "Une route GET appelle _put_marker (interdit)"
        )

    # Aucun return direct du cache
    assert "return _APPROVAL_RESULT_CACHE" not in text
    assert "return dict(_APPROVAL_RESULT_CACHE" not in text
    assert "return list(_APPROVAL_RESULT_CACHE" not in text


def test_marker_helpers_are_module_private():
    from web.routes import mcp as mcp_routes

    assert hasattr(mcp_routes, "_put_marker")
    assert hasattr(mcp_routes, "_take_marker")
    assert hasattr(mcp_routes, "_expire_stale_markers_locked")
    # Convention Python : pas de helpers publics
    assert not hasattr(mcp_routes, "put_marker")
    assert not hasattr(mcp_routes, "take_marker")
    assert not hasattr(mcp_routes, "expire_stale_markers")


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — Audit UI dédié
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_audit_simulated_event_on_dry_run(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    aid = _new_aid()
    app, _ = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(f"/api/mcp/approvals/{aid}/approve", json={"confirmed": True})
    events = _read_audit(audit_path)
    assert len(events) == 1
    e = events[0]
    assert e["event"] == "ui_action_simulated"
    assert e["action"] == "approve"
    assert e["phase"] == "20B-1"
    assert e["live_mode"] is False


@pytest.mark.asyncio
async def test_audit_requested_and_completed_on_live_approve(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    aid = _new_aid()
    app, _ = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(f"/api/mcp/approvals/{aid}/approve", json={"confirmed": True})
    events = _read_audit(audit_path)
    kinds = [e["event"] for e in events]
    assert "ui_action_requested" in kinds
    assert "ui_action_completed" in kinds
    completed = [e for e in events if e["event"] == "ui_action_completed"][0]
    assert completed["outcome"] == "approved"
    assert "marker_emitted" in completed and len(completed["marker_emitted"]) == 32
    assert isinstance(completed.get("duration_s"), (int, float))


@pytest.mark.asyncio
async def test_audit_failed_event_on_queue_exception(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    aid = _new_aid()
    queue = _FakeApprovalQueue(pending_ids=[aid])
    queue.approve_should_raise = True
    app, _ = _make_app(fake_queue=queue)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{aid}/approve", json={"confirmed": True}
        )
    assert resp.status_code == 500
    body = resp.json()
    assert body.get("detail", {}).get("error_code") == "approve_failed"
    blob = json.dumps(body)
    assert "boom-approve-raw-message-should-not-leak" not in blob

    events = _read_audit(audit_path)
    failed = [e for e in events if e["event"] == "ui_action_failed"]
    assert failed
    assert failed[-1]["error_code"] == "approve_failed"
    audit_blob = json.dumps(events)
    assert "boom-approve-raw-message-should-not-leak" not in audit_blob


@pytest.mark.asyncio
async def test_audit_actor_token_hash_is_sha256_format(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.delenv("LUMENA_TEST_MODE", raising=False)
    aid = _new_aid()
    app, _ = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            f"/api/mcp/approvals/{aid}/approve",
            headers={"Authorization": "Bearer some-real-token-xyz"},
            json={"confirmed": True},
        )
    events = _read_audit(audit_path)
    assert events
    for e in events:
        h = e["actor_token_hash"]
        assert h.startswith("sha256:")
        assert len(h) == len("sha256:") + 64
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", h)
    blob = json.dumps(events)
    assert "some-real-token-xyz" not in blob


@pytest.mark.asyncio
async def test_audit_actor_token_hash_unknown_when_absent(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.delenv("LUMENA_TEST_MODE", raising=False)
    aid = _new_aid()
    app, _ = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(f"/api/mcp/approvals/{aid}/approve", json={"confirmed": True})
    events = _read_audit(audit_path)
    assert events
    for e in events:
        assert e["actor_token_hash"] == "sha256:unknown"


@pytest.mark.asyncio
async def test_audit_no_raw_reason_only_length(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    secret_reason = "SECRET-REASON-MARKER-DO-NOT-LEAK-12345"
    aid = _new_aid()
    app, _ = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            f"/api/mcp/approvals/{aid}/reject",
            json={"confirmed": True, "reason": secret_reason},
        )
    events = _read_audit(audit_path)
    blob = json.dumps(events)
    assert secret_reason not in blob, "reason raw MUST NOT appear in audit"
    has_len = any("reason_length" in e for e in events)
    assert has_len, "reason_length MUST be present"
    for e in events:
        if "reason_length" in e:
            assert e["reason_length"] == len(secret_reason)
            assert "reason" not in e


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — Réponse n'expose JAMAIS ApprovalResult brut
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_approve_response_never_contains_approval_result_fields(
    monkeypatch, tmp_path
):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    aid = _new_aid()
    app, _ = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{aid}/approve", json={"confirmed": True}
        )
    assert resp.status_code == 200
    blob = resp.text
    assert "SHOULD_NEVER_LEAK_XYZ" not in blob
    assert "secret_arg_marker" not in blob
    d = resp.json()
    allowed_keys = {"approved", "action_id", "live_mode", "marker", "marker_ttl_s"}
    assert set(d.keys()) <= allowed_keys


@pytest.mark.asyncio
async def test_reject_response_never_contains_extra_fields(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    aid = _new_aid()
    app, _ = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{aid}/reject",
            json={"confirmed": True, "reason": "bad call"},
        )
    assert resp.status_code == 200
    d = resp.json()
    allowed_keys = {"rejected", "action_id", "live_mode"}
    assert set(d.keys()) <= allowed_keys


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — Rate limiting : /api/mcp/approvals/ dans _EXPENSIVE_PREFIXES
# ══════════════════════════════════════════════════════════════════════════════


def test_rate_limit_approvals_prefix_marked_expensive():
    text = _SERVER_PY_PATH.read_text(encoding="utf-8")
    assert '/api/mcp/approvals/' in text
    # Vérifie qu'il est dans la constante _EXPENSIVE_PREFIXES
    m = re.search(r"_EXPENSIVE_PREFIXES\s*=\s*\(([^)]+)\)", text)
    assert m, "_EXPENSIVE_PREFIXES not found in server.py"
    prefix_list = m.group(1)
    assert '/api/mcp/approvals/' in prefix_list


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — Grep statique : whitelist par handler + encadrement helpers
# ══════════════════════════════════════════════════════════════════════════════


def _find_function_body(source: str, fn_name: str) -> Optional[str]:
    """Extrait le corps d'une fonction Python (top-level) par nom.

    Heuristique simple : depuis `def fn_name` ou `async def fn_name`
    jusqu'à la prochaine ligne commençant par def/async def/@ au même niveau.
    """
    pattern = re.compile(
        rf"^(?:async\s+def|def)\s+{re.escape(fn_name)}\s*\(", re.MULTILINE
    )
    m = pattern.search(source)
    if not m:
        return None
    start = m.start()
    rest = source[m.end():]
    next_match = re.search(r"^(?:async\s+def|def|@router\.|@app\.)", rest, re.MULTILINE)
    end = m.end() + (next_match.start() if next_match else len(rest))
    return source[start:end]


def test_grep_approve_calls_only_in_named_handler_and_guarded():
    """queue.approve(...) ne doit apparaître que dans mcp_approval_approve,
    et ce handler doit être encadré par _live_mode_enabled + _assert_confirmed."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    # Toutes les occurrences de `.approve(` dans le fichier
    occurrences = [m.start() for m in re.finditer(r"\.approve\(", text)]
    assert occurrences, "Expected at least one .approve( call (Phase 20B-1)"

    body = _find_function_body(text, "mcp_approval_approve")
    assert body is not None, "Handler mcp_approval_approve introuvable"
    # Garde-fous obligatoires dans ce handler
    assert "_live_mode_enabled" in body
    assert "_assert_confirmed" in body
    # La méthode .approve( doit être présente
    assert ".approve(" in body

    # Aucune autre fonction top-level ne doit appeler .approve(
    other_bodies_approve = re.findall(
        r"^(?:async\s+def|def)\s+(\w+)\s*\([^)]*\):[\s\S]*?(?=^(?:async\s+def|def|@router\.|@app\.))",
        text,
        re.MULTILINE,
    )
    for fn_name in other_bodies_approve:
        if fn_name == "mcp_approval_approve":
            continue
        body_other = _find_function_body(text, fn_name)
        if body_other is None:
            continue
        # Si une autre fonction appelle .approve(, c'est interdit
        assert ".approve(" not in body_other, (
            f".approve( doit être uniquement dans mcp_approval_approve, "
            f"trouvé aussi dans {fn_name}"
        )


def test_grep_reject_calls_only_in_named_handler_and_guarded():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    body = _find_function_body(text, "mcp_approval_reject")
    assert body is not None, "Handler mcp_approval_reject introuvable"
    assert "_live_mode_enabled" in body
    assert "_assert_confirmed" in body
    assert "_validate_reason" in body
    assert ".reject(" in body


def test_grep_no_trust_mutations_yet():
    """Phase 20B-5 (cumul) : approve/reject/install/activation/catalog/autoapprove autorisés.

    Trust update est désormais autorisé via Phase 20B-6. Seuls update_last_active,
    discover, MCPOrchestrator restent internes.
    """
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    forbidden = [
        ".update_last_active(",
        ".discover(",
        "MCPOrchestrator(",
    ]
    for token in forbidden:
        assert token not in text, (
            f"{token} trouvé dans mcp.py (interdit Phase 20B-6 cumul)"
        )


def test_grep_no_fernet_decrypt():
    """Aucun déchiffrement Fernet, aucun accès direct aux args chiffrés."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert "Fernet(" not in text
    assert ".decrypt(" not in text


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — HTML/JS smoke (boutons + modals + bandeau)
# ══════════════════════════════════════════════════════════════════════════════


def test_panels_js_has_approve_modal_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "openMcpApprovalApproveModal" in text


def test_panels_js_has_reject_modal_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "openMcpApprovalRejectModal" in text


def test_panels_js_has_submit_approve_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "submitMcpApprovalApprove" in text
    assert "/approvals/" in text and "/approve" in text


def test_panels_js_has_submit_reject_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "submitMcpApprovalReject" in text
    assert "/reject" in text


def test_panels_js_has_live_mode_banner_renderer():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "_mcpRenderLiveBanner" in text
    assert "mcp-live-banner" in text


def test_panels_js_reject_button_disabled_until_reason_valid():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "_mcpUpdateRejectButton" in text
    assert "mcp-reject-confirm-btn" in text


def test_panels_js_does_not_call_trust_mutations_yet():
    """Phase 20B-5 (cumul) : install/activation/catalog/autoapprove autorisés JS.
    Trust recompute reste interdit (Phase 20B-6)."""
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    forbidden = [
        "submitMcpTrustRecompute",
    ]
    for token in forbidden:
        assert token not in text, (
            f"{token} présent dans panels.js (interdit jusqu'à Phase 20B-6)"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section 12 — Singleton lifespan
# ══════════════════════════════════════════════════════════════════════════════


def test_deps_has_singleton_accessor():
    from web.routes import deps

    assert hasattr(deps, "_MCP_APPROVAL_QUEUE_SINGLETON")
    assert hasattr(deps, "get_mcp_approval_queue_singleton")


def test_lifespan_initializes_singleton():
    """Vérifie que lifespan.py contient le bloc d'init du singleton."""
    lifespan_path = _REPO_ROOT / "web" / "routes" / "lifespan.py"
    text = lifespan_path.read_text(encoding="utf-8")
    assert "_MCP_APPROVAL_QUEUE_SINGLETON" in text
    assert "from src.mcp.approval_queue import ApprovalQueue" in text


# ══════════════════════════════════════════════════════════════════════════════
# Section 13 — Action_id validation UUID4 stricte (Phase 10 alignment)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_action_id_uuid4_hex_accepted(monkeypatch, tmp_path):
    """uuid.uuid4().hex → accepté."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    aid = uuid.uuid4().hex
    app, _ = _make_app(pending_ids=[aid])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{aid}/approve", json={"confirmed": True}
        )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_action_id_abc_rejected_400(monkeypatch, tmp_path):
    """'abc' n'est pas un UUID4 hex → 400 action_id_invalid."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, queue = _make_app(pending_ids=[uuid.uuid4().hex])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/approvals/abc/approve", json={"confirmed": True}
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "action_id_invalid"
    assert queue.approve_calls == []


@pytest.mark.asyncio
async def test_action_id_all_zero_rejected_400(monkeypatch, tmp_path):
    """'0'*32 est 32 hex mais UUID(0) a version != 4 → 400."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, queue = _make_app(pending_ids=[uuid.uuid4().hex])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/approvals/" + ("0" * 32) + "/approve",
            json={"confirmed": True},
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "action_id_invalid"
    assert queue.approve_calls == []


@pytest.mark.asyncio
async def test_action_id_uuid1_rejected_400(monkeypatch, tmp_path):
    """uuid.uuid1().hex a version=1 → 400 action_id_invalid."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    aid_u1 = uuid.uuid1().hex
    app, queue = _make_app(pending_ids=[uuid.uuid4().hex])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{aid_u1}/approve", json={"confirmed": True}
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "action_id_invalid"
    assert queue.approve_calls == []


@pytest.mark.asyncio
async def test_action_id_uppercase_rejected_400(monkeypatch, tmp_path):
    """UUID4 en majuscules → 400 (regex impose lowercase)."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    aid = uuid.uuid4().hex.upper()
    app, queue = _make_app(pending_ids=[uuid.uuid4().hex])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{aid}/approve", json={"confirmed": True}
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "action_id_invalid"
    assert queue.approve_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_id",
    [
        "..",
        "../etc",
        "a-b-c",
        "x" * 31,
        "x" * 33,
        "g" * 32,  # 32 chars mais non-hex
        uuid.uuid4().hex + "extra",
    ],
)
async def test_action_id_various_invalid_400(monkeypatch, tmp_path, bad_id):
    """Variantes invalides : path traversal, longueur, non-hex, suffix."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, queue = _make_app(pending_ids=[uuid.uuid4().hex])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/approvals/{bad_id}/approve", json={"confirmed": True}
        )
    # 400 (validation) ou 404 (FastAPI route mismatch sur / dans path)
    assert resp.status_code in (400, 404)
    assert queue.approve_calls == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 14 — Health expose live_mode
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_health_exposes_live_mode_field(monkeypatch, tmp_path):
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    assert resp.status_code == 200
    d = resp.json()
    assert "live_mode" in d
    assert d["live_mode"] is False


@pytest.mark.asyncio
async def test_health_live_mode_true_when_env_set(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    assert resp.json()["live_mode"] is True
