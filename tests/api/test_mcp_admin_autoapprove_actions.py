"""
Tests Phase 20B-5 — AutoApprove patterns CRUD UI.

Point central :
  Créer un pattern AutoApprove = créer une autorisation FUTURE qui peut
  court-circuiter ApprovalQueue. Doctrine plus stricte que 20B-1/2/3/4.

Couverture :
  - Auth (401/403)
  - Confirmation backend + phrase fixe CREATE-AUTOAPPROVE-PATTERN (add)
  - Phrase = pattern_id complet 32 chars (remove)
  - Validators répliqués Phase 11 : profile, kind, tool_name_pattern (whitelist
    + policy bornée), caller_kinds_allowed, policy, quota, expires_at
  - Pré-validation args_constraints stricte (10 clés whitelist, types,
    taille/profondeur, max 4096 chars JSON) → unique error_code
    args_constraints_invalid (anti canal latéral)
  - Engine unavailable 503
  - Double opt-in obligatoire : LUMENA_MCP_LIVE=1 ET LUMENA_MCP_AUTOAPPROVE_LIVE=1
  - Dry-run STRICT : 0 call add_pattern / remove_pattern
  - Live add : 1 call add_pattern, retourne pattern_id UUID4
  - Live remove : idempotent True/False propagé
  - GET list/détail : métadonnées agrégées only
  - Anti-fuite : tool_name_pattern raw, args_constraints raw,
    caller_kinds_allowed liste — JAMAIS exposés
  - Aucun déchiffrement Fernet côté route
  - Aucun import privé Phase 11 en production
  - Test cohérence Phase 11 (import privé EN TEST uniquement)
  - Aucun appel ApprovalQueue/Install/Activation/Catalog mutation dans handlers
  - Aucun update/PUT/PATCH
  - JS smoke : 5ème onglet, 2 modals, 2 handlers, bandeau double opt-in
  - Rate limit, health phase 20B-5 + composant + autoapprove_live_mode
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest
from fastapi import FastAPI


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MCP_ROUTE_PATH = _REPO_ROOT / "web" / "routes" / "mcp.py"
_PANELS_JS_PATH = _REPO_ROOT / "web" / "static" / "js" / "panels.js"
_INDEX_PATH = _REPO_ROOT / "web" / "index.html"
_SERVER_PY_PATH = _REPO_ROOT / "web" / "server.py"


# ──────────────────────────────────────────────────────────────────────────────
# Fakes
# ──────────────────────────────────────────────────────────────────────────────


class _FakePolicy:
    def __init__(self, value: str):
        self.value = value


class _FakeAutoApprovePattern:
    """Stand-in pour AutoApprovePattern Phase 11."""

    def __init__(
        self,
        pattern_id: Optional[str] = None,
        profile: str = "default",
        kind: str = "email_send",
        tool_name_pattern: str = "mcp__alice__send_email",
        policy_value: str = "external_write_recoverable",
        caller_kinds: Optional[List[str]] = None,
        args_constraints: Optional[Dict[str, Any]] = None,
        quota_max_per_day: int = 10,
        expires_at: Optional[str] = None,
    ):
        self.id = pattern_id or uuid.uuid4().hex
        self.profile = profile
        self.kind = kind
        # SECRET markers pour anti-fuite tests
        self.tool_name_pattern = tool_name_pattern or "SECRET_TOOL_PATTERN_LEAK_RAW"
        self.policy = _FakePolicy(policy_value)
        self.caller_kinds_allowed = caller_kinds or ["react", "codeagent"]
        self.args_constraints = args_constraints or {
            "to_allowlist": ["SECRET_ARGS_CONSTRAINTS_LEAK_RAW@example.com"],
            "amount_max_eur": 100,
        }
        self.quota_max_per_day = quota_max_per_day
        self.expires_at = expires_at or (
            datetime.now(timezone.utc) + timedelta(days=30)
        ).isoformat()
        self.created_at = datetime.now(timezone.utc).isoformat()


class _FakeAutoApproveEngine:
    def __init__(self, patterns: Optional[List[_FakeAutoApprovePattern]] = None):
        self._patterns = {p.id: p for p in (patterns or [])}
        self.add_calls: List[Dict[str, Any]] = []
        self.remove_calls: List[str] = []
        self.add_should_raise = False
        self.add_should_return_invalid = False
        self.remove_should_raise = False

    def list_patterns(self, profile: Optional[str] = None):
        out = []
        for p in self._patterns.values():
            if profile is not None and p.profile != profile:
                continue
            out.append(p)
        return out

    def get_pattern(self, pattern_id: str):
        return self._patterns.get(pattern_id)

    def add_pattern(self, **kwargs):
        self.add_calls.append(kwargs)
        if self.add_should_raise:
            raise RuntimeError("boom-add-pattern-raw-LEAK")
        if self.add_should_return_invalid:
            return None
        pid = uuid.uuid4().hex
        p = _FakeAutoApprovePattern(
            pattern_id=pid,
            profile=kwargs.get("profile", "default"),
            kind=kwargs.get("kind", "x"),
            tool_name_pattern=kwargs.get("tool_name_pattern", "mcp__x__y"),
            caller_kinds=kwargs.get("caller_kinds_allowed", ["react"]),
            args_constraints=kwargs.get("args_constraints", {"to_allowlist": ["x"]}),
            quota_max_per_day=kwargs.get("quota_max_per_day", 10),
            expires_at=kwargs.get("expires_at"),
        )
        self._patterns[pid] = p
        return pid

    def remove_pattern(self, pattern_id: str) -> bool:
        self.remove_calls.append(pattern_id)
        if self.remove_should_raise:
            raise RuntimeError("boom-remove-pattern-raw-LEAK")
        if pattern_id in self._patterns:
            del self._patterns[pattern_id]
            return True
        return False


def _make_app(
    *,
    patterns: Optional[List[_FakeAutoApprovePattern]] = None,
    engine: Optional[_FakeAutoApproveEngine] = None,
    no_engine: bool = False,
    with_auth_override: bool = True,
) -> tuple[FastAPI, _FakeAutoApproveEngine]:
    from web.routes import deps, mcp as mcp_routes
    eng = engine or _FakeAutoApproveEngine(patterns=patterns or [])
    deps._MCP_AUTO_APPROVE_ENGINE_SINGLETON = None if no_engine else eng
    app = FastAPI()
    app.include_router(mcp_routes.router)
    if with_auth_override:
        app.dependency_overrides[deps.verify_admin_token] = lambda: None
    return app, eng


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


_ADD_PHRASE = "CREATE-AUTOAPPROVE-PATTERN"


def _future_iso(days: int = 30) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _valid_add_body() -> Dict[str, Any]:
    return {
        "confirmed": True,
        "confirmation_phrase": _ADD_PHRASE,
        "profile": "default",
        "kind": "email_send",
        "policy": "external_write_recoverable",
        "tool_name_pattern": "mcp__alice__send_email",
        "caller_kinds_allowed": ["react"],
        "args_constraints": {"to_allowlist": ["alice@example.com"]},
        "quota_max_per_day": 10,
        "expires_at": _future_iso(),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Auth
# ══════════════════════════════════════════════════════════════════════════════


_AA_PATHS_POST = [
    "/api/mcp/autoapprove/add",
    f"/api/mcp/autoapprove/{uuid.uuid4().hex}/remove",
]
_AA_PATHS_GET = [
    "/api/mcp/autoapprove/patterns",
    f"/api/mcp/autoapprove/patterns/{uuid.uuid4().hex}",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _AA_PATHS_POST)
async def test_aa_post_requires_admin_token_401(path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app, _ = _make_app(with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(path, json={"confirmed": True})
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _AA_PATHS_POST)
async def test_aa_post_bad_token_403(path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app, _ = _make_app(with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            path, headers={"Authorization": "Bearer wrong"},
            json={"confirmed": True},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _AA_PATHS_GET)
async def test_aa_get_requires_admin_token_401(path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app, _ = _make_app(with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path)
    assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Confirmation backend
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_add_without_confirmed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    del body["confirmed"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_required"


@pytest.mark.asyncio
async def test_remove_without_confirmed_400(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    pid = uuid.uuid4().hex
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/autoapprove/{pid}/remove",
            json={"confirmation_phrase": pid, "pattern_id": pid},
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_required"


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Add phrase fixe CREATE-AUTOAPPROVE-PATTERN
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase",
    ["", "create-autoapprove-pattern", "CREATE-AUTOAPPROVE", "wrong",
     "CREATE-AUTOAPPROVE-PATTERN ", " CREATE-AUTOAPPROVE-PATTERN"],
)
async def test_add_phrase_must_be_exact_fixed(monkeypatch, tmp_path, phrase):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    body["confirmation_phrase"] = phrase
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_phrase_invalid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Remove phrase = pattern_id complet 32 chars
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phrase_factory",
    [
        lambda pid: "",
        lambda pid: pid[:12],         # 12 premiers chars insuffisants
        lambda pid: pid[:-1],          # 31 chars
        lambda pid: pid.upper(),       # case mismatch
        lambda pid: uuid.uuid4().hex,  # autre uuid
    ],
)
async def test_remove_phrase_must_be_exact_pattern_id(
    monkeypatch, tmp_path, phrase_factory
):
    _clear_audit_path(monkeypatch, tmp_path)
    pid = uuid.uuid4().hex
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/autoapprove/{pid}/remove",
            json={
                "confirmed": True,
                "confirmation_phrase": phrase_factory(pid),
                "pattern_id": pid,
            },
        )
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "confirmation_phrase_invalid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — pattern_id UUID4 strict
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_pid",
    ["", "abc", "0" * 32, uuid.uuid4().hex.upper(), uuid.uuid4().hex + "x"],
)
async def test_remove_invalid_pattern_id_400(monkeypatch, tmp_path, bad_pid):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/autoapprove/{bad_pid}/remove",
            json={"confirmed": True, "confirmation_phrase": bad_pid, "pattern_id": bad_pid},
        )
    # 400 (validation) ou 404 (FastAPI route mismatch)
    assert resp.status_code in (400, 404)


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — profile regex Phase 11
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "profile",
    ["", "Has-Upper", "has space", "héro", "a" * 65, "with.dot", None, 123],
)
async def test_profile_invalid(monkeypatch, tmp_path, profile):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    if profile is None:
        del body["profile"]
    else:
        body["profile"] = profile
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "profile_invalid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — kind non-vide
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["", " ", None, 123, "a" * 65])
async def test_kind_invalid(monkeypatch, tmp_path, kind):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    if kind is None:
        del body["kind"]
    else:
        body["kind"] = kind
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "kind_invalid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — policy whitelist enum
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "policy",
    ["", "invalid_policy", "READ_ONLY", "Read_Only", None, 123],
)
async def test_policy_invalid(monkeypatch, tmp_path, policy):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    if policy is None:
        del body["policy"]
    else:
        body["policy"] = policy
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "policy_invalid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — tool_name_pattern + policy bornée
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tnp",
    ["", " ", "x" * 7, "*", "mcp__*", "**", "not_mcp_prefix",
     "mcp__alice__", "mcp__alice"],
)
async def test_tool_name_pattern_invalid(monkeypatch, tmp_path, tnp):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    body["tool_name_pattern"] = tnp
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "tool_name_pattern_invalid"


@pytest.mark.asyncio
async def test_tool_name_pattern_glob_rejected_for_write_policy(monkeypatch, tmp_path):
    """Glob mcp__server__* refusé pour les policies write/secrets."""
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    body["policy"] = "external_write_recoverable"
    body["tool_name_pattern"] = "mcp__alice__*"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "tool_name_pattern_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize("policy_val", ["read_only", "external_read"])
async def test_tool_name_pattern_glob_accepted_for_read_policies(
    monkeypatch, tmp_path, policy_val
):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    app, _ = _make_app()
    body = _valid_add_body()
    body["policy"] = policy_val
    body["tool_name_pattern"] = "mcp__alice__*"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_tool_name_pattern_exact_accepted(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    app, _ = _make_app()
    body = _valid_add_body()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — caller_kinds_allowed
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ck",
    [[], None, "react", ["unknown"], ["react", "react"], ["react", 123],
     ["react"] * 10],
)
async def test_caller_kinds_invalid(monkeypatch, tmp_path, ck):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    if ck is None:
        del body["caller_kinds_allowed"]
    else:
        body["caller_kinds_allowed"] = ck
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "caller_kinds_allowed_invalid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — args_constraints (point central v2, ~25 tests)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ac",
    [
        None, "string", 123, [], (), {},  # types invalides / vide
    ],
)
async def test_args_constraints_invalid_type_or_empty(monkeypatch, tmp_path, ac):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    if ac is None:
        del body["args_constraints"]
    else:
        body["args_constraints"] = ac
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "args_constraints_invalid"


@pytest.mark.asyncio
async def test_args_constraints_unknown_key_rejected(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    body["args_constraints"] = {"unknown_key_evil": ["x"]}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "args_constraints_invalid"


@pytest.mark.asyncio
async def test_args_constraints_too_many_keys(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    body["args_constraints"] = {
        f"key_{i}": "x" for i in range(11)
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "args_constraints_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    ["not_a_list", 42, True, {"nested": "dict"}, None],
)
async def test_args_constraints_to_allowlist_wrong_type(monkeypatch, tmp_path, value):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    body["args_constraints"] = {"to_allowlist": value}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "args_constraints_invalid"


@pytest.mark.asyncio
async def test_args_constraints_list_too_long(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    body["args_constraints"] = {"to_allowlist": ["x"] * 65}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "args_constraints_invalid"


@pytest.mark.asyncio
async def test_args_constraints_string_in_list_too_long(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    body["args_constraints"] = {"to_allowlist": ["x" * 257]}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "args_constraints_invalid"


@pytest.mark.asyncio
async def test_args_constraints_string_with_control_char_rejected(
    monkeypatch, tmp_path
):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    body["args_constraints"] = {"to_allowlist": ["with\x00null"]}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "args_constraints_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key,value",
    [
        ("subject_max_chars", "100"),     # str instead of int
        ("subject_max_chars", True),       # bool instead of int
        ("subject_max_chars", 80.5),       # float instead of int
        ("subject_max_chars", -1),         # negative
        ("subject_max_chars", 0),          # zero
        ("subject_max_chars", 99_999_999), # too large
        ("body_max_chars", "100"),
        ("amount_max_eur", "100"),
        ("amount_max_eur", True),          # bool refused
        ("amount_max_eur", -1),
        ("amount_max_usd", -0.01),
    ],
)
async def test_args_constraints_int_number_invalid(monkeypatch, tmp_path, key, value):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    body["args_constraints"] = {key: value}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "args_constraints_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", ["true", 1, 0, "yes", None],
)
async def test_args_constraints_attachments_forbidden_must_be_bool(
    monkeypatch, tmp_path, value
):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    body["args_constraints"] = {"attachments_forbidden": value}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "args_constraints_invalid"


@pytest.mark.asyncio
async def test_args_constraints_nested_dict_rejected(monkeypatch, tmp_path):
    """DSL Phase 11 est plate. Dict imbriqué refusé."""
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    # nested dict en value pour une clé list → refus
    body["args_constraints"] = {"to_allowlist": [{"nested": "evil"}]}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "args_constraints_invalid"


@pytest.mark.asyncio
async def test_args_constraints_total_json_too_large(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    body["args_constraints"] = {
        "to_allowlist": ["x" * 200 for _ in range(64)],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "args_constraints_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ac",
    [
        {"to_allowlist": ["alice@example.com"]},
        {"channel_allowlist": ["#general"]},
        {"subject_max_chars": 100, "body_max_chars": 2000},
        {"amount_max_eur": 100.5},
        {"attachments_forbidden": True},
        {"to_allowlist": ["x@y.com"], "attachments_forbidden": False, "amount_max_eur": 50},
    ],
)
async def test_args_constraints_valid_cases_pass_web_validation(
    monkeypatch, tmp_path, ac
):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    app, _ = _make_app()
    body = _valid_add_body()
    body["args_constraints"] = ac
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 200, resp.text


# ══════════════════════════════════════════════════════════════════════════════
# Section 12 — quota_max_per_day
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "q", [-1, 0, True, False, "10", 10.5, 1_000_001, None],
)
async def test_quota_max_per_day_invalid(monkeypatch, tmp_path, q):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    if q is None:
        del body["quota_max_per_day"]
    else:
        body["quota_max_per_day"] = q
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "quota_max_per_day_invalid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 13 — expires_at ISO 8601 + futur
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "expires",
    ["", "not-an-iso", "2024-01-01", "2020-01-01T00:00:00+00:00",
     "2026-06-01T00:00:00", None, 123],
)
async def test_expires_at_invalid(monkeypatch, tmp_path, expires):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    if expires is None:
        del body["expires_at"]
    else:
        body["expires_at"] = expires
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "expires_at_invalid"


# ══════════════════════════════════════════════════════════════════════════════
# Section 14 — Engine unavailable 503
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_add_engine_unavailable_503(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app(no_engine=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=_valid_add_body())
    assert resp.status_code == 503
    assert resp.json().get("detail", {}).get("error_code") == "engine_unavailable"


@pytest.mark.asyncio
async def test_remove_engine_unavailable_503(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    pid = uuid.uuid4().hex
    app, _ = _make_app(no_engine=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/autoapprove/{pid}/remove",
            json={"confirmed": True, "confirmation_phrase": pid, "pattern_id": pid},
        )
    assert resp.status_code == 503
    assert resp.json().get("detail", {}).get("error_code") == "engine_unavailable"


# ══════════════════════════════════════════════════════════════════════════════
# Section 15 — Double opt-in obligatoire (matrice 2×2)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "live,aa_live,expected_dry",
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
async def test_double_optin_matrix_add(monkeypatch, tmp_path, live, aa_live, expected_dry):
    _clear_audit_path(monkeypatch, tmp_path)
    if live is None:
        monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    else:
        monkeypatch.setenv("LUMENA_MCP_LIVE", live)
    if aa_live is None:
        monkeypatch.delenv("LUMENA_MCP_AUTOAPPROVE_LIVE", raising=False)
    else:
        monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", aa_live)
    app, eng = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=_valid_add_body())
    assert resp.status_code == 200
    d = resp.json()
    if expected_dry:
        assert d.get("would_add") is True
        assert d["forced_dry_run"] is True
        assert eng.add_calls == []
    else:
        assert d["added"] is True
        assert len(eng.add_calls) == 1


# ══════════════════════════════════════════════════════════════════════════════
# Section 16 — Dry-run STRICT
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_dry_run_add_does_not_call_add_pattern(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    monkeypatch.delenv("LUMENA_MCP_AUTOAPPROVE_LIVE", raising=False)
    app, eng = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=_valid_add_body())
    assert resp.status_code == 200
    assert resp.json()["would_add"] is True
    assert eng.add_calls == []


@pytest.mark.asyncio
async def test_dry_run_remove_does_not_call_remove_pattern(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    monkeypatch.delenv("LUMENA_MCP_AUTOAPPROVE_LIVE", raising=False)
    pid = uuid.uuid4().hex
    app, eng = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/autoapprove/{pid}/remove",
            json={"confirmed": True, "confirmation_phrase": pid, "pattern_id": pid},
        )
    assert resp.status_code == 200
    assert resp.json()["would_remove"] is True
    assert eng.remove_calls == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 17 — Live add / remove
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_live_add_calls_add_pattern_once(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    app, eng = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=_valid_add_body())
    assert resp.status_code == 200
    d = resp.json()
    assert d["added"] is True
    assert isinstance(d["pattern_id"], str) and len(d["pattern_id"]) == 32
    assert d["live_mode"] is True
    assert d["autoapprove_live_mode"] is True
    assert len(eng.add_calls) == 1


@pytest.mark.asyncio
async def test_live_remove_calls_remove_pattern_idempotent_true(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    existing = _FakeAutoApprovePattern()
    app, eng = _make_app(patterns=[existing])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/autoapprove/{existing.id}/remove",
            json={
                "confirmed": True,
                "confirmation_phrase": existing.id,
                "pattern_id": existing.id,
            },
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["removed"] is True
    assert d["idempotent"] is False  # pattern existait avant
    assert eng.remove_calls == [existing.id]


@pytest.mark.asyncio
async def test_live_remove_unknown_pattern_idempotent(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    pid = uuid.uuid4().hex
    app, eng = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/autoapprove/{pid}/remove",
            json={"confirmed": True, "confirmation_phrase": pid, "pattern_id": pid},
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["removed"] is True
    assert d["idempotent"] is True


# ══════════════════════════════════════════════════════════════════════════════
# Section 18 — GET list / détail (métadonnées agrégées only)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_list_exposes_aggregated_metadata_only(monkeypatch, tmp_path):
    p = _FakeAutoApprovePattern()
    app, _ = _make_app(patterns=[p])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/autoapprove/patterns")
    assert resp.status_code == 200
    d = resp.json()
    assert len(d["patterns"]) == 1
    pat = d["patterns"][0]
    # Aggregated only
    allowed_keys = {
        "pattern_id", "profile", "kind", "policy", "quota_max_per_day",
        "expires_at", "created_at", "caller_kinds_count",
        "args_constraints_keys_count",
        "args_constraints_allowlists_total_entries",
        "tool_name_pattern_present",
    }
    assert set(pat.keys()) <= allowed_keys


@pytest.mark.asyncio
async def test_get_list_never_exposes_raw_pattern_data(monkeypatch, tmp_path):
    """Marqueurs SECRET_*_LEAK_RAW absents de la réponse."""
    p = _FakeAutoApprovePattern()  # contient SECRET_*_LEAK_RAW
    app, _ = _make_app(patterns=[p])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/autoapprove/patterns")
    blob = resp.text
    assert "SECRET_TOOL_PATTERN_LEAK_RAW" not in blob
    assert "SECRET_ARGS_CONSTRAINTS_LEAK_RAW" not in blob
    assert "tool_name_pattern" not in blob.replace("tool_name_pattern_present", "X")
    assert "args_constraints" not in blob.replace(
        "args_constraints_keys_count", "X"
    ).replace("args_constraints_allowlists_total_entries", "X")
    assert "caller_kinds_allowed" not in blob


@pytest.mark.asyncio
async def test_get_detail_404_when_not_found(monkeypatch, tmp_path):
    pid = uuid.uuid4().hex
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/mcp/autoapprove/patterns/{pid}")
    assert resp.status_code == 404
    # Format error_code whitelist garanti
    assert resp.json().get("detail", {}).get("error_code") == "pattern_not_found"


@pytest.mark.asyncio
async def test_get_detail_404_with_error_code_when_get_pattern_raises(
    monkeypatch, tmp_path
):
    """Si engine.get_pattern() raise, on retourne 404 avec error_code whitelist
    (pas un detail plain string legacy)."""

    class _RaisingEngine(_FakeAutoApproveEngine):
        def get_pattern(self, pattern_id):
            raise RuntimeError("boom-get-pattern-raw-LEAK")

    pid = uuid.uuid4().hex
    app, _ = _make_app(engine=_RaisingEngine())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/mcp/autoapprove/patterns/{pid}")
    assert resp.status_code == 404
    body = resp.json()
    # Format error_code whitelist (pas un detail plain string)
    detail = body.get("detail")
    assert isinstance(detail, dict), (
        f"detail doit être dict, got {type(detail).__name__}: {detail!r}"
    )
    assert detail.get("error_code") == "pattern_not_found"
    # Aucun message brut d'exception
    blob = json.dumps(body)
    assert "boom-get-pattern-raw-LEAK" not in blob


@pytest.mark.asyncio
async def test_get_detail_exposes_aggregated_metadata_only(monkeypatch, tmp_path):
    p = _FakeAutoApprovePattern()
    app, _ = _make_app(patterns=[p])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/mcp/autoapprove/patterns/{p.id}")
    assert resp.status_code == 200
    pat = resp.json()["pattern"]
    blob = json.dumps(pat)
    assert "SECRET_TOOL_PATTERN_LEAK_RAW" not in blob
    assert "SECRET_ARGS_CONSTRAINTS_LEAK_RAW" not in blob


@pytest.mark.asyncio
async def test_get_list_filter_by_profile(monkeypatch, tmp_path):
    p1 = _FakeAutoApprovePattern(profile="alice")
    p2 = _FakeAutoApprovePattern(profile="bob")
    app, _ = _make_app(patterns=[p1, p2])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/autoapprove/patterns?profile=alice")
    d = resp.json()
    assert len(d["patterns"]) == 1
    assert d["patterns"][0]["profile"] == "alice"


# ══════════════════════════════════════════════════════════════════════════════
# Section 19 — Anti-fuite audit critique
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_audit_never_contains_args_constraints_raw(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    app, _ = _make_app()
    body = _valid_add_body()
    body["args_constraints"] = {
        "to_allowlist": ["SECRET_DSL_LEAK_MARKER@example.com"],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/mcp/autoapprove/add", json=body)
    blob = json.dumps(_read_audit(audit_path))
    assert "SECRET_DSL_LEAK_MARKER" not in blob
    # Le champ args_constraints raw n'apparaît pas (seulement les agrégats)
    for e in _read_audit(audit_path):
        assert "args_constraints" not in e or e.get("args_constraints_keys_count") is not None
        # check exact champs interdits absents
        assert "to_allowlist" not in e


@pytest.mark.asyncio
async def test_audit_never_contains_tool_name_pattern_raw(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    app, _ = _make_app()
    body = _valid_add_body()
    body["tool_name_pattern"] = "mcp__SECRET_TOOL_PATTERN_LEAK__send"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/mcp/autoapprove/add", json=body)
    blob = json.dumps(_read_audit(audit_path))
    assert "SECRET_TOOL_PATTERN_LEAK" not in blob
    assert "tool_name_pattern" not in blob.replace(
        "tool_name_pattern_present", "X"
    )


@pytest.mark.asyncio
async def test_audit_never_contains_caller_kinds_list_raw(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    app, _ = _make_app()
    body = _valid_add_body()
    body["caller_kinds_allowed"] = ["react", "codeagent", "autonomy"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/mcp/autoapprove/add", json=body)
    for e in _read_audit(audit_path):
        # Pas de liste caller_kinds_allowed dans l'audit
        assert "caller_kinds_allowed" not in e
        # Seulement le count
        if "caller_kinds_count" in e:
            assert e["caller_kinds_count"] == 3


@pytest.mark.asyncio
async def test_audit_never_contains_confirmation_phrase_raw(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/mcp/autoapprove/add", json=_valid_add_body())
    blob = json.dumps(_read_audit(audit_path))
    assert '"confirmation_phrase"' not in blob


@pytest.mark.asyncio
async def test_audit_actor_token_hash_sha256(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    monkeypatch.delenv("LUMENA_TEST_MODE", raising=False)
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/mcp/autoapprove/add",
            headers={"Authorization": "Bearer some-real-token-xyz"},
            json=_valid_add_body(),
        )
    blob = json.dumps(_read_audit(audit_path))
    assert "some-real-token-xyz" not in blob


@pytest.mark.asyncio
async def test_audit_exception_message_never_leaks(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    eng = _FakeAutoApproveEngine()
    eng.add_should_raise = True
    app, _ = _make_app(engine=eng)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=_valid_add_body())
    assert resp.status_code == 500
    blob = json.dumps(resp.json())
    assert "boom-add-pattern-raw-LEAK" not in blob
    audit_blob = json.dumps(_read_audit(audit_path))
    assert "boom-add-pattern-raw-LEAK" not in audit_blob


@pytest.mark.asyncio
async def test_audit_includes_double_optin_flags(monkeypatch, tmp_path):
    audit_path = _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post("/api/mcp/autoapprove/add", json=_valid_add_body())
    events = _read_audit(audit_path)
    assert events
    for e in events:
        assert "live_mode" in e
        assert "autoapprove_live_mode" in e


# ══════════════════════════════════════════════════════════════════════════════
# Section 20 — Réponse whitelist (anti-fuite)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_add_response_whitelist(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=_valid_add_body())
    d = resp.json()
    allowed = {"added", "pattern_id", "profile", "live_mode", "autoapprove_live_mode"}
    assert set(d.keys()) <= allowed


@pytest.mark.asyncio
async def test_remove_response_whitelist(monkeypatch, tmp_path):
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    existing = _FakeAutoApprovePattern()
    app, _ = _make_app(patterns=[existing])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            f"/api/mcp/autoapprove/{existing.id}/remove",
            json={
                "confirmed": True, "confirmation_phrase": existing.id,
                "pattern_id": existing.id,
            },
        )
    d = resp.json()
    allowed = {"removed", "pattern_id", "idempotent", "live_mode", "autoapprove_live_mode"}
    assert set(d.keys()) <= allowed


# ══════════════════════════════════════════════════════════════════════════════
# Section 21 — Aucun Fernet/decrypt côté route
# ══════════════════════════════════════════════════════════════════════════════


def test_mcp_py_no_fernet_decrypt_in_autoapprove_handlers():
    """Le pattern reste chiffré côté backend. Aucun Fernet/decrypt dans
    les handlers AutoApprove."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")

    def _find_body(name):
        m = re.search(
            rf"^(?:async\s+def|def)\s+{re.escape(name)}\s*\(", text, re.MULTILINE
        )
        if not m:
            return None
        start = m.start()
        rest = text[m.end():]
        nxt = re.search(r"^(?:async\s+def|def|@router\.|@app\.)", rest, re.MULTILINE)
        end = m.end() + (nxt.start() if nxt else len(rest))
        return text[start:end]

    for fn in (
        "mcp_autoapprove_list_patterns",
        "mcp_autoapprove_get_pattern",
        "mcp_autoapprove_add",
        "mcp_autoapprove_remove",
    ):
        body = _find_body(fn)
        assert body is not None, f"Handler {fn} introuvable"
        assert "Fernet(" not in body
        assert ".decrypt(" not in body
        assert "_get_cipher" not in body


# ══════════════════════════════════════════════════════════════════════════════
# Section 22 — Aucun import privé Phase 11
# ══════════════════════════════════════════════════════════════════════════════


def test_mcp_py_no_phase11_private_imports():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    for token in (
        "from src.mcp.auto_approve import _validate_profile",
        "from src.mcp.auto_approve import _validate_kind",
        "from src.mcp.auto_approve import _validate_tool_name_pattern",
        "from src.mcp.auto_approve import _validate_caller_kinds",
        "from src.mcp.auto_approve import _validate_args_constraints",
        "from src.mcp.auto_approve import _validate_quota",
        "from src.mcp.auto_approve import _validate_expires_at",
        "from src.mcp.auto_approve import _KNOWN_CONSTRAINT_KEYS",
        "from src.mcp.auto_approve import _VALID_CALLER_KINDS",
    ):
        assert token not in text


def test_mcp_py_imports_mcppolicy_public():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert "from src.mcp.policy import MCPPolicy" in text


# ══════════════════════════════════════════════════════════════════════════════
# Section 23 — Test cohérence Phase 11 (import privé EN TEST uniquement)
# ══════════════════════════════════════════════════════════════════════════════


def test_validation_consistency_with_phase11_helpers():
    from web.routes import mcp as mcp_routes
    try:
        from src.mcp.auto_approve import (
            _validate_profile as _ph11_profile,
            _validate_kind as _ph11_kind,
            _validate_caller_kinds as _ph11_caller_kinds,
            _validate_quota as _ph11_quota,
            AutoApproveError,
        )
    except Exception:
        pytest.skip("Phase 11 helpers indisponibles")
    from fastapi import HTTPException

    def _ph11_valid(fn, raw):
        try:
            fn(raw)
            return True
        except AutoApproveError:
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
        ("profile", "valid_p", True),
        ("profile", "Has-Upper", False),
        ("profile", "", False),
        ("kind", "valid_kind", True),
        ("kind", "", False),
        ("caller_kinds", ["react"], True),
        ("caller_kinds", [], False),
        ("caller_kinds", ["unknown"], False),
        ("caller_kinds", "react", False),
        ("quota", 10, True),
        ("quota", 0, False),
        ("quota", -1, False),
        ("quota", True, False),
    ]
    web_fns = {
        "profile": mcp_routes._validate_profile_format,
        "kind": mcp_routes._validate_kind_format,
        "caller_kinds": mcp_routes._validate_caller_kinds_allowed_format,
        "quota": mcp_routes._validate_quota_max_per_day_format,
    }
    ph11_fns = {
        "profile": _ph11_profile,
        "kind": _ph11_kind,
        "caller_kinds": _ph11_caller_kinds,
        "quota": _ph11_quota,
    }
    for field, raw, expected in cases:
        web_ok = _web_valid(web_fns[field], raw)
        ph11_ok = _ph11_valid(ph11_fns[field], raw)
        assert web_ok == ph11_ok == expected, (
            f"Cohérence cassée pour {field}={raw!r} : "
            f"web={web_ok}, ph11={ph11_ok}, expected={expected}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# Section 24 — Aucun appel ApprovalQueue/Install/Activation/Catalog/marker
# ══════════════════════════════════════════════════════════════════════════════


def test_autoapprove_handlers_no_other_mcp_calls():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")

    def _find_body(name):
        m = re.search(
            rf"^(?:async\s+def|def)\s+{re.escape(name)}\s*\(", text, re.MULTILINE
        )
        if not m:
            return None
        start = m.start()
        rest = text[m.end():]
        nxt = re.search(r"^(?:async\s+def|def|@router\.|@app\.)", rest, re.MULTILINE)
        end = m.end() + (nxt.start() if nxt else len(rest))
        return text[start:end]

    for fn in (
        "mcp_autoapprove_list_patterns",
        "mcp_autoapprove_get_pattern",
        "mcp_autoapprove_add",
        "mcp_autoapprove_remove",
    ):
        body = _find_body(fn)
        assert body is not None
        for forbidden in (
            ".propose(", ".approve(", ".reject(",
            ".propose_install(", ".execute_approved_install(",
            ".propose_activation(", ".activate(", ".deactivate(",
            ".add_server(", ".update_status(", ".remove_server(",
            "_take_marker", "_put_marker",
            "MCPSandboxRunner", "MCPInstallOrchestrator(",
            "MCPActivationService(", "MCPServerCatalog()",
        ):
            assert forbidden not in body, (
                f"{forbidden} trouvé dans handler {fn} (interdit Phase 20B-5)"
            )


def test_autoapprove_handlers_no_subprocess():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert "import subprocess" not in text


def test_no_update_pattern_or_patch_phase_i6():
    """Phase 11 immutable : aucun update_pattern, aucun PATCH.

    Phase I-6 introduit des PUT pour les routes Library config (set d'un
    secret/config field). On vérifie que ces PUT NE TOUCHENT PAS à
    autoapprove (pas de mutation des patterns existants)."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert "@router.patch" not in text
    assert ".update_pattern(" not in text
    # Les PUT existent (Phase I-6) mais aucun ne cible /autoapprove
    import re as _re
    put_lines = _re.findall(r'@router\.put\(\s*"([^"]+)"', text)
    for path in put_lines:
        assert "autoapprove" not in path, (
            f"PUT vers autoapprove interdit (Phase 11) : {path}"
        )


def test_autoapprove_no_fresh_engine_instantiation():
    """Aucun AutoApproveEngine() neuf dans les handlers."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")

    def _find_body(name):
        m = re.search(
            rf"^(?:async\s+def|def)\s+{re.escape(name)}\s*\(", text, re.MULTILINE
        )
        if not m:
            return None
        start = m.start()
        rest = text[m.end():]
        nxt = re.search(r"^(?:async\s+def|def|@router\.|@app\.)", rest, re.MULTILINE)
        end = m.end() + (nxt.start() if nxt else len(rest))
        return text[start:end]

    for fn in (
        "mcp_autoapprove_list_patterns",
        "mcp_autoapprove_get_pattern",
        "mcp_autoapprove_add",
        "mcp_autoapprove_remove",
    ):
        body = _find_body(fn)
        assert body is not None
        assert "AutoApproveEngine()" not in body


# ══════════════════════════════════════════════════════════════════════════════
# Section 25 — Rate limit
# ══════════════════════════════════════════════════════════════════════════════


def test_rate_limit_autoapprove_prefix_marked_expensive():
    text = _SERVER_PY_PATH.read_text(encoding="utf-8")
    assert "/api/mcp/autoapprove/" in text
    m = re.search(r"_EXPENSIVE_PREFIXES\s*=\s*\(([^)]+)\)", text, re.DOTALL)
    assert m
    assert "/api/mcp/autoapprove/" in m.group(1)


# ══════════════════════════════════════════════════════════════════════════════
# Section 26 — Health phase 20B-5 + composant + autoapprove_live_mode
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_health_phase_20b5(monkeypatch):
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    assert resp.json()["phase"] in ("20B-5", "20B-6", "21")


@pytest.mark.asyncio
async def test_health_exposes_auto_approve_engine_component(monkeypatch):
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    d = resp.json()
    assert "auto_approve_engine" in d.get("components", {})


@pytest.mark.asyncio
async def test_health_exposes_autoapprove_live_mode(monkeypatch):
    monkeypatch.delenv("LUMENA_MCP_AUTOAPPROVE_LIVE", raising=False)
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    d = resp.json()
    assert "autoapprove_live_mode" in d
    assert d["autoapprove_live_mode"] is False


@pytest.mark.asyncio
async def test_health_autoapprove_live_mode_true_when_env_set(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    app, _ = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/health")
    assert resp.json()["autoapprove_live_mode"] is True


# ══════════════════════════════════════════════════════════════════════════════
# Section 27 — JS smoke
# ══════════════════════════════════════════════════════════════════════════════


def test_panels_js_has_autoapprove_loader():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "_loadMcpAutoApprove" in text


def test_panels_js_has_autoapprove_add_modal_and_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "openMcpAutoApproveAddModal" in text
    assert "submitMcpAutoApproveAdd" in text


def test_panels_js_has_autoapprove_remove_modal_and_handler():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "openMcpAutoApproveRemoveModal" in text
    assert "submitMcpAutoApproveRemove" in text


def test_panels_js_dispatches_auto_approve_tab():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "case'auto_approve'" in text or "case 'auto_approve'" in text


def test_panels_js_has_fixed_phrase_constant():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "CREATE-AUTOAPPROVE-PATTERN" in text
    assert "_MCP_AUTOAPPROVE_ADD_PHRASE" in text


def test_panels_js_remove_requires_full_pattern_id():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    m = re.search(
        r"function\s+_mcpUpdateAutoApproveRemoveButton\s*\([^)]*\)\s*\{([\s\S]*?)\n\}\s*",
        text,
    )
    assert m
    body = m.group(1)
    # La validation utilise === avec le pattern_id complet attendu
    assert "===" in body
    assert "expectedPatternId" in body


def test_index_html_has_auto_approve_tab():
    text = _INDEX_PATH.read_text(encoding="utf-8")
    assert 'data-arg="auto_approve"' in text


def test_index_html_has_5_mcp_tabs():
    text = _INDEX_PATH.read_text(encoding="utf-8")
    for tab in ("catalog", "approvals", "watcher", "audit", "auto_approve"):
        assert f'data-arg="{tab}"' in text


def test_panels_js_double_optin_banner_present():
    text = _PANELS_JS_PATH.read_text(encoding="utf-8")
    assert "LUMENA_MCP_AUTOAPPROVE_LIVE" in text
    assert "Dry-run forcé" in text or "dry-run forcé" in text.lower() or "Dry-run forc" in text


# ══════════════════════════════════════════════════════════════════════════════
# Section 28 — Anti canal latéral args_constraints_invalid unifié
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ac,reason_internal",
    [
        ({"unknown_key": "x"}, "unknown_key"),
        ({"to_allowlist": "not_list"}, "wrong_type"),
        ({"to_allowlist": ["x" * 257]}, "too_long"),
        ({"subject_max_chars": -1}, "negative"),
    ],
)
async def test_args_constraints_invalid_uses_unified_error_code(
    monkeypatch, tmp_path, ac, reason_internal
):
    """Toutes les erreurs args_constraints utilisent le même error_code
    pour éviter le canal latéral (l'admin ne peut pas inférer quelle règle
    a échoué à partir du code retourné)."""
    _clear_audit_path(monkeypatch, tmp_path)
    app, _ = _make_app()
    body = _valid_add_body()
    body["args_constraints"] = ac
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=body)
    assert resp.status_code == 400
    assert resp.json().get("detail", {}).get("error_code") == "args_constraints_invalid"


@pytest.mark.asyncio
async def test_engine_add_pattern_failure_uses_generic_error_code(monkeypatch, tmp_path):
    """Si engine.add_pattern raise (validation Phase 11 post-pré-validation web),
    on retourne add_pattern_failed générique (pas un code spécialisé qui
    révélerait la règle Phase 11 qui a échoué)."""
    _clear_audit_path(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_AUTOAPPROVE_LIVE", "1")
    eng = _FakeAutoApproveEngine()
    eng.add_should_raise = True
    app, _ = _make_app(engine=eng)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post("/api/mcp/autoapprove/add", json=_valid_add_body())
    assert resp.status_code == 500
    assert resp.json().get("detail", {}).get("error_code") == "add_pattern_failed"
