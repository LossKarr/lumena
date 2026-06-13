"""
Tests pour GET /api/runtime/audit (Phase 2).

Vérifie :
  - Auth obligatoire (401 sans token, 403 mauvais token)
  - format=summary par défaut (pas de champ `tools`)
  - format=full retourne tools[]
  - drift_only filtre correctement
  - tool=<name> filtre par nom
  - format invalide → 400
  - Réponse JSON valide
  - Aucune mutation sur le runtime
"""
from __future__ import annotations

from dataclasses import asdict
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI


def _make_app(with_auth_override=True):
    """Construit une app FastAPI minimale avec uniquement le router system.

    Args:
        with_auth_override: True → bypass auth pour tester l'endpoint logic.
                            False → garde l'auth réelle (pour tester 401/403).
    """
    from web.routes import deps, system
    app = FastAPI()
    app.include_router(system.router)
    if with_auth_override:
        app.dependency_overrides[deps.verify_admin_token] = lambda: None
    return app


def _build_fake_report():
    """Fake AuditFullReport pour tests d'endpoint sans bootstrap LumenaCore."""
    from src.runtime.drift_checker import (
        AuditFullReport,
        ContractCallableMatrix,
        ToolAuditEntry,
    )
    matrix_ok = ContractCallableMatrix(
        react_with_workspace=True,
        react_without_workspace=True,
        autonomy_with_workspace=True,
        autonomy_without_workspace=False,
        codeagent_with_workspace=True,
        codeagent_without_workspace=True,
    )
    matrix_drift = ContractCallableMatrix(
        react_with_workspace=False,
        react_without_workspace=False,
        autonomy_with_workspace=False,
        autonomy_without_workspace=False,
        codeagent_with_workspace=False,
        codeagent_without_workspace=False,
    )
    return AuditFullReport(
        ts="2026-05-31T12:00:00+00:00",
        total_tools=3,
        advertised_count=2,
        contract_callable_any_context=2,
        drift_count=1,
        broken_count=0,
        categories=2,
        tools=[
            ToolAuditEntry(
                name="read_file",
                category="files",
                semantic_category="files",
                source_module="",
                advertised_in_prompt=True,
                contract_callable_for_context=matrix_ok,
                refusal_reasons={"autonomy.without_workspace": "requires_workspace=True"},
                dry_call_status="static_check_ok",
                drift_detected=False,
            ),
            ToolAuditEntry(
                name="drifted_tool",
                category="files",
                semantic_category="files",
                source_module="",
                advertised_in_prompt=True,
                contract_callable_for_context=matrix_drift,
                refusal_reasons={},
                dry_call_status="static_check_ok",
                drift_detected=True,
            ),
            ToolAuditEntry(
                name="hidden_tool",
                category="files",
                semantic_category="files",
                source_module="",
                advertised_in_prompt=False,
                contract_callable_for_context=matrix_ok,
                refusal_reasons={},
                dry_call_status="static_check_ok",
                drift_detected=False,
            ),
        ],
    )


# ──────────────────────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_requires_admin_token_401(monkeypatch):
    """Sans header Authorization avec token configuré → 401."""
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app = _make_app(with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/runtime/audit")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_audit_requires_admin_token_403(monkeypatch):
    """Mauvais token → 403."""
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app = _make_app(with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/runtime/audit",
            headers={"Authorization": "Bearer wrong-token"},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_audit_passes_with_correct_token(monkeypatch):
    """Bon token → 200 (avec audit_registry mocké)."""
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")

    fake = _build_fake_report()
    with patch("src.runtime.drift_checker.audit_registry", return_value=fake), \
         patch("src.reasoning.tool_registry.ToolRegistry"):
        app = _make_app(with_auth_override=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/runtime/audit",
                headers={"Authorization": "Bearer real-admin-secret"},
            )
    assert resp.status_code == 200


# ──────────────────────────────────────────────────────────────────────────────
# format=summary (défaut)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_summary_format_no_tools_field():
    """format=summary (défaut) → aucun champ `tools`."""
    fake = _build_fake_report()
    with patch("src.runtime.drift_checker.audit_registry", return_value=fake), \
         patch("src.reasoning.tool_registry.ToolRegistry"):
        app = _make_app(with_auth_override=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/runtime/audit")
    assert resp.status_code == 200
    data = resp.json()
    assert "tools" not in data, f"format=summary ne doit pas inclure 'tools', reçu: {data}"
    # Champs summary attendus
    for field in (
        "ts", "total_tools", "advertised_count",
        "contract_callable_any_context",
        "drift_count", "broken_count", "categories",
    ):
        assert field in data, f"champ '{field}' manquant"
    assert data["total_tools"] == 3
    assert data["drift_count"] == 1


@pytest.mark.asyncio
async def test_audit_explicit_summary_no_tools_field():
    """format=summary explicite → idem."""
    fake = _build_fake_report()
    with patch("src.runtime.drift_checker.audit_registry", return_value=fake), \
         patch("src.reasoning.tool_registry.ToolRegistry"):
        app = _make_app(with_auth_override=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/runtime/audit?format=summary")
    assert resp.status_code == 200
    assert "tools" not in resp.json()


# ──────────────────────────────────────────────────────────────────────────────
# format=full
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_full_format_has_tools_list():
    """format=full → champ tools présent + non vide."""
    fake = _build_fake_report()
    with patch("src.runtime.drift_checker.audit_registry", return_value=fake), \
         patch("src.reasoning.tool_registry.ToolRegistry"):
        app = _make_app(with_auth_override=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/runtime/audit?format=full")
    assert resp.status_code == 200
    data = resp.json()
    assert "tools" in data
    assert isinstance(data["tools"], list)
    assert len(data["tools"]) == 3
    # Vérifier champs entry
    entry = data["tools"][0]
    for field in (
        "name", "category", "semantic_category", "source_module",
        "advertised_in_prompt", "contract_callable_for_context",
        "refusal_reasons", "dry_call_status", "drift_detected",
    ):
        assert field in entry, f"champ entry '{field}' manquant"


@pytest.mark.asyncio
async def test_audit_full_format_matrix_shape():
    """contract_callable_for_context contient les 6 clés attendues."""
    fake = _build_fake_report()
    with patch("src.runtime.drift_checker.audit_registry", return_value=fake), \
         patch("src.reasoning.tool_registry.ToolRegistry"):
        app = _make_app(with_auth_override=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/runtime/audit?format=full")
    data = resp.json()
    matrix = data["tools"][0]["contract_callable_for_context"]
    for key in (
        "react_with_workspace", "react_without_workspace",
        "autonomy_with_workspace", "autonomy_without_workspace",
        "codeagent_with_workspace", "codeagent_without_workspace",
    ):
        assert key in matrix


# ──────────────────────────────────────────────────────────────────────────────
# Filtres
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_drift_only_filter():
    """drift_only=true → tools filtré aux drifted."""
    fake = _build_fake_report()
    with patch("src.runtime.drift_checker.audit_registry", return_value=fake), \
         patch("src.reasoning.tool_registry.ToolRegistry"):
        app = _make_app(with_auth_override=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/runtime/audit?format=full&drift_only=true")
    data = resp.json()
    assert len(data["tools"]) == 1
    assert data["tools"][0]["name"] == "drifted_tool"
    assert data["tools"][0]["drift_detected"] is True


@pytest.mark.asyncio
async def test_audit_filter_by_tool_name():
    """tool=read_file → 1 entrée."""
    fake = _build_fake_report()
    with patch("src.runtime.drift_checker.audit_registry", return_value=fake), \
         patch("src.reasoning.tool_registry.ToolRegistry"):
        app = _make_app(with_auth_override=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/runtime/audit?format=full&tool=read_file")
    data = resp.json()
    assert len(data["tools"]) == 1
    assert data["tools"][0]["name"] == "read_file"


@pytest.mark.asyncio
async def test_audit_filter_unknown_tool_returns_empty():
    """tool=xxx inconnu → tools vide."""
    fake = _build_fake_report()
    with patch("src.runtime.drift_checker.audit_registry", return_value=fake), \
         patch("src.reasoning.tool_registry.ToolRegistry"):
        app = _make_app(with_auth_override=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/runtime/audit?format=full&tool=nonexistent_xyz"
            )
    data = resp.json()
    assert data["tools"] == []


# ──────────────────────────────────────────────────────────────────────────────
# Format invalide
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_invalid_format_returns_400():
    """format=foo → 400."""
    with patch("src.reasoning.tool_registry.ToolRegistry"):
        app = _make_app(with_auth_override=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/runtime/audit?format=foo")
    assert resp.status_code == 400
    assert "format" in resp.json().get("error", "").lower()


# ──────────────────────────────────────────────────────────────────────────────
# Idempotence et invariance
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_idempotent():
    """2 GET successifs → mêmes données structurelles (ts fixé via mock)."""
    fake = _build_fake_report()
    with patch("src.runtime.drift_checker.audit_registry", return_value=fake), \
         patch("src.reasoning.tool_registry.ToolRegistry"):
        app = _make_app(with_auth_override=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp1 = await client.get("/api/runtime/audit?format=full")
            resp2 = await client.get("/api/runtime/audit?format=full")
    assert resp1.status_code == 200 and resp2.status_code == 200
    assert resp1.json() == resp2.json()


@pytest.mark.asyncio
async def test_audit_response_json_serializable():
    """La réponse complète doit être sérialisable JSON sans erreur."""
    import json
    fake = _build_fake_report()
    with patch("src.runtime.drift_checker.audit_registry", return_value=fake), \
         patch("src.reasoning.tool_registry.ToolRegistry"):
        app = _make_app(with_auth_override=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/runtime/audit?format=full")
    # round-trip JSON
    data = resp.json()
    json.dumps(data)  # ne doit pas lever


@pytest.mark.asyncio
async def test_audit_summary_counts_match_fake_report():
    """Les counts du summary correspondent au fake report fourni."""
    fake = _build_fake_report()
    with patch("src.runtime.drift_checker.audit_registry", return_value=fake), \
         patch("src.reasoning.tool_registry.ToolRegistry"):
        app = _make_app(with_auth_override=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/runtime/audit?format=summary")
    data = resp.json()
    assert data["total_tools"] == fake.total_tools
    assert data["advertised_count"] == fake.advertised_count
    assert data["drift_count"] == fake.drift_count
    assert data["broken_count"] == fake.broken_count
    assert data["categories"] == fake.categories


# ──────────────────────────────────────────────────────────────────────────────
# Cascade registry runtime : prouver que le registry existant est utilisé
# ──────────────────────────────────────────────────────────────────────────────


class _SentinelRegistry:
    """Sentinel pour prouver via identité Python que ce registry précis
    est passé à audit_registry()."""


@pytest.mark.asyncio
async def test_audit_uses_lumena_tool_registry_when_present(monkeypatch):
    """Si deps.lumena._tool_registry existe → c'est lui qui est audité.

    Aucun ToolRegistry(lumena=...) n'est instancié dans ce cas.
    """
    from web.routes import deps as deps_module

    sentinel = _SentinelRegistry()
    fake_lumena = type("FakeLumena", (), {"_tool_registry": sentinel})()
    monkeypatch.setattr(deps_module, "lumena", fake_lumena)

    received: list = []

    def _spy_audit(registry):
        received.append(registry)
        return _build_fake_report()

    # Ce mock prouve que si ToolRegistry est instancié, le test échoue
    def _forbid_ctor(*args, **kwargs):
        raise AssertionError(
            "ToolRegistry ne doit PAS être instancié si _tool_registry présent"
        )

    with patch("src.runtime.drift_checker.audit_registry", side_effect=_spy_audit), \
         patch("src.reasoning.tool_registry.ToolRegistry", side_effect=_forbid_ctor):
        app = _make_app(with_auth_override=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/runtime/audit")

    assert resp.status_code == 200
    assert len(received) == 1
    assert received[0] is sentinel, (
        "audit_registry doit recevoir EXACTEMENT le sentinel attaché à deps.lumena"
    )


@pytest.mark.asyncio
async def test_audit_uses_tool_system_tool_registry_as_fallback(monkeypatch):
    """Si deps.lumena._tool_registry absent mais tool_system._tool_registry présent
    → c'est lui qui est audité."""
    from web.routes import deps as deps_module

    sentinel = _SentinelRegistry()
    fake_tool_system = type("FakeToolSystem", (), {"_tool_registry": sentinel})()
    fake_lumena = type("FakeLumena", (), {"tool_system": fake_tool_system})()
    # IMPORTANT : pas de _tool_registry sur fake_lumena
    monkeypatch.setattr(deps_module, "lumena", fake_lumena)

    received: list = []

    def _spy_audit(registry):
        received.append(registry)
        return _build_fake_report()

    def _forbid_ctor(*args, **kwargs):
        raise AssertionError(
            "ToolRegistry ne doit PAS être instancié si tool_system._tool_registry présent"
        )

    with patch("src.runtime.drift_checker.audit_registry", side_effect=_spy_audit), \
         patch("src.reasoning.tool_registry.ToolRegistry", side_effect=_forbid_ctor):
        app = _make_app(with_auth_override=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/runtime/audit")

    assert resp.status_code == 200
    assert len(received) == 1
    assert received[0] is sentinel


@pytest.mark.asyncio
async def test_audit_falls_back_to_new_registry_when_none_attached(monkeypatch):
    """Si aucun registry n'est attaché → fallback ToolRegistry(lumena=...)."""
    from web.routes import deps as deps_module

    # FakeLumena sans _tool_registry ni tool_system
    fake_lumena = type("FakeLumena", (), {})()
    monkeypatch.setattr(deps_module, "lumena", fake_lumena)

    received: list = []
    sentinel = _SentinelRegistry()
    ctor_calls: list = []

    def _ctor_spy(lumena=None, **kwargs):
        ctor_calls.append({"lumena": lumena, "kwargs": kwargs})
        return sentinel

    def _spy_audit(registry):
        received.append(registry)
        return _build_fake_report()

    with patch("src.runtime.drift_checker.audit_registry", side_effect=_spy_audit), \
         patch("src.reasoning.tool_registry.ToolRegistry", side_effect=_ctor_spy):
        app = _make_app(with_auth_override=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/runtime/audit")

    assert resp.status_code == 200
    # Fallback: ctor a été appelé exactement une fois avec lumena=fake
    assert len(ctor_calls) == 1
    assert ctor_calls[0]["lumena"] is fake_lumena
    # Le registry passé à audit est bien celui fabriqué par le ctor
    assert len(received) == 1
    assert received[0] is sentinel
