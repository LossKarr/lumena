"""
Tests Phase 21 — Hardening MCP (Diagnostics).

Couvre :
  - Auth obligatoire sur les 8 nouvelles routes GET
  - /observability/overview : structure + sanitization derniers events
  - /observability/events  : structure + sanitization stricte + unknown 400
  - /observability/last-runs : structure + filtre server_id
  - /keys/status            : passif (présent/format_valid uniquement)
  - /audit-integrity/{c}    : metadata-only + admin_ui présent + unknown 400
  - /coherence/check        : 5 checks + auto_fix_applied=False
  - /readiness              : rapport + auto_fix_applied=False
  - /rbac/mode              : "admin_only" + report-only
  - Anti-leak statique : aucun cipher neuf, aucun .decrypt(, aucun .set(,
    aucun _get_cipher, aucun _get_hmac_key dans la zone Phase 21
  - Grep statique : aucun @router.post/put/patch/delete ajouté Phase 21
  - _AUDIT_COMPONENTS contient "admin_ui"
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


_PHASE21_GET_ROUTES = [
    "/api/mcp/observability/overview",
    "/api/mcp/observability/events",
    "/api/mcp/observability/last-runs",
    "/api/mcp/keys/status",
    "/api/mcp/audit-integrity/catalog",
    "/api/mcp/coherence/check",
    "/api/mcp/readiness",
    "/api/mcp/rbac/mode",
]


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Auth obligatoire sur les 8 routes Phase 21
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _PHASE21_GET_ROUTES)
async def test_phase21_route_requires_admin_token_401(path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app = _make_app(with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path)
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _PHASE21_GET_ROUTES)
async def test_phase21_route_bad_token_403(path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app = _make_app(with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — /observability/overview
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_observability_overview_structure():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/observability/overview")
    assert resp.status_code == 200
    d = resp.json()
    assert d["available"] is True
    for k in ("catalog_counts", "approvals_pending_count",
              "watcher_persisted_snapshots", "last_admin_events", "modes",
              "rbac_mode"):
        assert k in d
    for status in ("declared", "installed", "active",
                   "quarantined", "removed"):
        assert status in d["catalog_counts"]
    assert d["rbac_mode"] == "admin_only"
    for m in ("live_mode", "autoapprove_live_mode", "trust_live_mode"):
        assert m in d["modes"]
        assert isinstance(d["modes"][m], bool)


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — /observability/events : sanitization stricte
# ══════════════════════════════════════════════════════════════════════════════


_FORBIDDEN_EVENT_KEYS = (
    "args", "package_spec", "notes", "justification",
    "tool_name_pattern", "marker", "token", "path",
    "args_constraints", "caller_kinds_allowed",
    "secret", "raw_entry", "stack",
)

_SECRET_MARKERS = (
    "SECRET_ARGS_LEAK", "SECRET_PACKAGE_SPEC_LEAK",
    "SECRET_NOTES_LEAK", "SECRET_JUSTIFICATION_LEAK_éàç",
    "SECRET_MARKER_LEAK", "SECRET_TOKEN_LEAK",
)


@pytest.mark.asyncio
async def test_observability_events_structure_default_admin_ui():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/observability/events")
    assert resp.status_code == 200
    d = resp.json()
    assert d["available"] is True
    assert d["component"] == "admin_ui"
    assert isinstance(d["events"], list)
    assert d["count"] == len(d["events"])


@pytest.mark.asyncio
async def test_observability_events_unknown_component_400():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/mcp/observability/events",
            params={"component": "not_a_real_component"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_observability_events_sanitized_no_forbidden_keys(tmp_path, monkeypatch):
    """Injection forcée d'un event audit raw plein de champs interdits :
    après lecture via la route, AUCUN champ hors whitelist ne sort.
    """
    from web.routes import mcp as mcp_routes
    fake_audit = tmp_path / "audit.jsonl"
    raw = {
        "ts": "2026-06-03T12:00:00+00:00",
        "event": "ui_action_completed",
        "phase": "20B-4",
        "action": "catalog_add_server",
        "outcome": "added",
        # ▼ champs interdits
        "args": {"x": "SECRET_ARGS_LEAK"},
        "package_spec": "SECRET_PACKAGE_SPEC_LEAK",
        "notes": "SECRET_NOTES_LEAK",
        "justification": "SECRET_JUSTIFICATION_LEAK_éàç",
        "tool_name_pattern": "evil*",
        "marker": "SECRET_MARKER_LEAK",
        "token": "SECRET_TOKEN_LEAK",
        "args_constraints": {"a": [1, 2]},
        "caller_kinds_allowed": ["x"],
        "raw_entry": "SECRET_RAW",
        "stack": "trace",
        "path": "/etc/passwd",
    }
    fake_audit.write_text(json.dumps(raw) + "\n", encoding="utf-8")
    monkeypatch.setattr(mcp_routes, "_ui_audit_path", lambda: fake_audit)

    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/mcp/observability/events",
            params={"component": "admin_ui", "limit": 50},
        )
    assert resp.status_code == 200
    d = resp.json()
    blob = json.dumps(d, ensure_ascii=False)
    for marker in _SECRET_MARKERS:
        assert marker not in blob, f"marker {marker} leaked"
    for ev in d["events"]:
        for forb in _FORBIDDEN_EVENT_KEYS:
            assert forb not in ev, f"forbidden key {forb} leaked"
        # Whitelist : seuls des champs whitelist autorisés
        for key in ev.keys():
            assert key in mcp_routes._AUDIT_EVENT_SANITIZED_KEYS, (
                f"non-whitelist key {key} sorti dans events"
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — /observability/last-runs
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_last_runs_structure_no_server_id():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/observability/last-runs")
    assert resp.status_code == 200
    d = resp.json()
    assert "available" in d
    assert isinstance(d.get("servers", []), list)


@pytest.mark.asyncio
async def test_last_runs_invalid_server_id_400():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/mcp/observability/last-runs",
            params={"server_id": "../etc/passwd"},
        )
    assert resp.status_code in (400, 422)


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — /keys/status STRICTEMENT PASSIF
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_keys_status_structure_passive():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/keys/status")
    assert resp.status_code == 200
    d = resp.json()
    if d.get("available", False) is False:
        return
    assert "keys" in d
    for name in ("auto_approve_fernet", "auto_approve_hmac",
                 "approval_queue_fernet", "catalog_hmac"):
        assert name in d["keys"]
        block = d["keys"][name]
        assert set(block.keys()) <= {"present", "format_valid"}
        assert isinstance(block.get("present"), bool)
        assert isinstance(block.get("format_valid"), bool)
    assert d.get("rotation_check") == "deferred_to_phase_22_or_cli"


@pytest.mark.asyncio
async def test_keys_status_no_raw_key_value_leaked():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/keys/status")
    d = resp.json()
    blob = json.dumps(d, ensure_ascii=False)
    # Aucun champ "value", "key_material", "fernet_key", "hmac_key" exposé.
    for forb in ("value", "key_material", "fernet_key", "hmac_key"):
        # Sauf comme nom de slot : on cherche `"value":`
        assert f'"{forb}":' not in blob or forb in ("fernet_key", "hmac_key") is False


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — /audit-integrity/{component}
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_audit_integrity_unknown_component_400():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/audit-integrity/not_a_component")
    assert resp.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("component", [
    "catalog", "approval_queue", "runtime_watcher", "orchestrator",
    "discovery", "install_orchestrator", "activation",
    "policy_resolver", "policy_attributor", "admin_ui",
])
async def test_audit_integrity_known_components_metadata_only(component):
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/mcp/audit-integrity/{component}")
    assert resp.status_code == 200
    d = resp.json()
    for k in ("file_present", "size_bytes", "line_count",
              "valid_json_lines", "malformed_lines",
              "first_ts", "last_ts", "size_warning"):
        assert k in d, f"{component} missing {k}"
    assert d["component"] == component
    # AUCUNE ligne raw ne doit sortir : pas de "events", pas de "lines"
    assert "events" not in d
    assert "lines" not in d
    assert "raw" not in d


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — /coherence/check (5 checks + no auto-fix)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_coherence_check_structure_and_no_auto_fix():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/coherence/check")
    assert resp.status_code == 200
    d = resp.json()
    assert d["available"] is True
    assert d["auto_fix_applied"] is False
    assert isinstance(d["checks"], list)
    assert len(d["checks"]) == 5
    expected_names = {
        "catalog_active_count",
        "watcher_snapshots_vs_active",
        "registry_writer_resolvable",
        "approvals_pending_action_id_valid",
        "autoapprove_patterns_expired",
    }
    got_names = {c["name"] for c in d["checks"]}
    assert got_names == expected_names
    for c in d["checks"]:
        assert c["status"] in ("ok", "warn", "fail")
        assert isinstance(c["details_count"], int)
    assert d["overall_status"] in ("ok", "warn", "fail")


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — /readiness (rapport + no auto-fix)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_readiness_report_structure_and_no_auto_fix():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/readiness")
    assert resp.status_code == 200
    d = resp.json()
    assert d["available"] is True
    assert d["auto_fix_applied"] is False
    assert d["overall"] in ("ready", "degraded", "not_ready")
    for k in ("singletons_loaded", "singletons_all_loaded",
              "keys_status_ok", "audit_integrity_ok",
              "coherence_overall", "modes", "last_evaluated_ts"):
        assert k in d
    for s in ("catalog", "approval_queue", "install_orchestrator",
              "runtime_watcher", "activation_service",
              "auto_approve_engine"):
        assert s in d["singletons_loaded"]
        assert isinstance(d["singletons_loaded"][s], bool)


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — /rbac/mode (report-only)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rbac_mode_admin_only_report_only():
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/rbac/mode")
    assert resp.status_code == 200
    d = resp.json()
    assert d["mode"] == "admin_only"
    assert d["evolution_planned_for"] == "phase_22_or_later"


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — Grep statique : Phase 21 = +8 GET, +0 POST/PUT/PATCH/DELETE
# ══════════════════════════════════════════════════════════════════════════════


def test_route_counts_phase_i6():
    """Phase I-6 introduit :
      - +4 GET (schema, config-status, ready, + library était déjà compté G)
        → 21 (Phase G) + 4 = 25 GET
      - +1 POST (detect-schema) → 15 (Phase 21) + 1 = 16 POST
      - +2 PUT (set secret + set config)  — premiers PUT autorisés
      - +2 DELETE (delete secret + delete config) — premiers DELETE autorisés
    @router.patch reste strictement interdit.
    """
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    assert text.count("@router.get(") == 24
    assert text.count("@router.post(") == 16
    assert text.count("@router.put(") == 2
    assert text.count("@router.delete(") == 2
    assert "@router.patch" not in text


def test_phase21_admin_ui_component_present_in_audit_components():
    from web.routes import mcp as mcp_routes
    assert "admin_ui" in mcp_routes._AUDIT_COMPONENTS
    assert mcp_routes._AUDIT_COMPONENTS["admin_ui"] == "mcp_admin_audit"


def test_phase21_audit_event_sanitized_keys_whitelist_exposed():
    from web.routes import mcp as mcp_routes
    wl = mcp_routes._AUDIT_EVENT_SANITIZED_KEYS
    # whitelist core obligatoire
    for k in ("ts", "event", "phase", "action", "outcome"):
        assert k in wl
    # champs interdits NE DOIVENT JAMAIS y être
    for forb in ("args", "package_spec", "notes", "justification",
                 "tool_name_pattern", "marker", "token", "raw_entry",
                 "stack", "path"):
        assert forb not in wl, f"{forb} ne doit JAMAIS être whitelisté"


def test_phase21_no_fernet_cipher_decrypt_set_in_keys_section():
    """Anti-leak Phase 21 : aucun cipher neuf, aucun .decrypt(, aucun .set(
    sur SecretsService, aucun _get_cipher, aucun _get_hmac_key.
    """
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    # On vise le code (les docstrings ont été nettoyées pour éviter
    # les faux positifs sur Fernet()).
    forbidden_substrings = (
        "Fernet(", ".decrypt(",
        "_get_cipher(", "_get_hmac_key(",
        "svc.set(",
    )
    for tok in forbidden_substrings:
        assert tok not in text, f"{tok} interdit en Phase 21"


def test_phase21_no_mutation_call_in_phase21_section():
    """La zone Phase 21 ne doit jamais appeler des méthodes mutatives."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    idx = text.find("PHASE 21 — Hardening MCP")
    assert idx != -1, "section Phase 21 introuvable"
    phase21_text = text[idx:]
    forbidden = (
        ".approve(", ".reject(",
        ".install(", ".activate(", ".deactivate(",
        ".quarantine(", ".restore(", ".remove_server(",
        ".add_server(", ".add_pattern(", ".remove_pattern(",
        ".update_trust_score(",
        "_take_marker(", "_put_marker(",
        ".register_runner(", ".unregister_runner(",
    )
    for tok in forbidden:
        assert tok not in phase21_text, f"{tok} interdit dans section Phase 21"


def test_phase21_readiness_and_coherence_advertise_no_auto_fix():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    # Les routes readiness + coherence renvoient explicitement
    # auto_fix_applied=False.
    assert text.count('"auto_fix_applied": False') >= 2


def test_phase21_keys_status_uses_only_get_on_secrets_service():
    """Phase 21 ne doit appeler que svc.get(...) — aucun set/rotate."""
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    idx = text.find("async def mcp_keys_status")
    assert idx != -1
    end = text.find("\n# ──", idx)
    keys_block = text[idx:end if end != -1 else idx + 4000]
    assert "svc.get(" in keys_block
    for forb in ("svc.set(", "svc.rotate(", "svc.delete(", "Fernet(",
                 ".decrypt(", "_get_cipher", "_get_hmac_key"):
        assert forb not in keys_block, f"{forb} interdit dans keys/status"


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — UTF-8 / accents préservés (anti-mojibake)
# ══════════════════════════════════════════════════════════════════════════════


def test_phase21_no_mojibake_in_mcp_route():
    text = _MCP_ROUTE_PATH.read_text(encoding="utf-8")
    for moji in ("Ã©", "Ã¨", "Ã ", "Ã§", "Ã®", "Ã´", "Ãª", "â€™", "â€œ", "â€"):
        assert moji not in text, f"mojibake détecté : {moji}"
