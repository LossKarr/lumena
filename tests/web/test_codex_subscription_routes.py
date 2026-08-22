from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.llm.codex_app_server import CodexAppServerTimeout
from src.llm.codex_subscription import (
    CodexAPIFallback,
    CodexAccountState,
    CodexAccountSummary,
    CodexCLIState,
    CodexPreflightResult,
    CodexProbeAttempt,
    CodexLoginChallenge,
    CodexModelSummary,
    CodexQuotaSummary,
    CodexSubscriptionSettings,
    CodexSurface,
    OpenAIAccessMode,
    load_codex_subscription_settings,
)
from src.llm.codex_collaboration import (
    CodexCollaborationRegistry,
    CodexHandoff,
    CodexShareMode,
    CodexThreadSummary,
    CollaborationLink,
)
from web.routes import codex_subscription as routes
from web.routes.deps import verify_admin_token


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[verify_admin_token] = lambda: None
    return app


def _gateway(account_type="chatgpt"):
    account = CodexAccountSummary(
        state=CodexAccountState.CONNECTED,
        account_type=account_type,
        plan_type="Plus",
        email_masked="c***@e***.com",
    )
    return SimpleNamespace(
        require_chatgpt_account=AsyncMock(return_value=account),
        read_account=AsyncMock(return_value=account),
        read_rate_limits=AsyncMock(return_value=CodexQuotaSummary(exhausted=False)),
        list_models=AsyncMock(
            return_value=(
                CodexModelSummary(
                    model_id="account-model",
                    display_name="Account Model",
                    is_default=True,
                ),
            )
        ),
        start_login=AsyncMock(
            return_value=CodexLoginChallenge(
                login_id="l1", auth_url="https://auth.openai.com/start"
            )
        ),
        cancel_login=AsyncMock(),
        wait_for_login=AsyncMock(return_value=account),
        logout=AsyncMock(),
    )


def test_access_selection_is_hot_reloaded_without_restart(monkeypatch):
    from web.routes import config as config_routes

    writer = MagicMock()
    monkeypatch.setattr(config_routes, "_write_env_values", writer)
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "api")
    monkeypatch.setenv("LUMENA_CODEX_DEFAULT_MODEL", "previous-model")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent")

    routes._persist_access_selection(
        {
            "LUMENA_OPENAI_ACCESS_MODE": "chatgpt_codex",
            "LUMENA_CODEX_DEFAULT_MODEL": "account-model",
            "LUMENA_CODEX_SURFACES": "codeagent,chat,agent,missions",
        }
    )
    codex = load_codex_subscription_settings()
    assert codex.enabled is True
    assert codex.default_model == "account-model"
    assert codex.surfaces == frozenset(
        {
            CodexSurface.CODEAGENT,
            CodexSurface.CHAT,
            CodexSurface.AGENT,
            CodexSurface.MISSIONS,
        }
    )

    routes._persist_access_selection({"LUMENA_OPENAI_ACCESS_MODE": "api"})
    api = load_codex_subscription_settings()
    assert api.enabled is False
    assert api.default_model == "account-model"
    assert api.surfaces == codex.surfaces
    assert writer.call_count == 2
    assert writer.call_args_list[0].args[0] == {
        "LUMENA_OPENAI_ACCESS_MODE": "chatgpt_codex",
        "LUMENA_CODEX_DEFAULT_MODEL": "account-model",
        "LUMENA_CODEX_SURFACES": "codeagent,chat,agent,missions",
    }
    assert writer.call_args_list[1].args[0] == {
        "LUMENA_OPENAI_ACCESS_MODE": "api"
    }


@pytest.mark.asyncio
async def test_idempotent_read_recycles_once_after_transport_timeout(monkeypatch):
    failed = SimpleNamespace(is_running=True)
    replacement = SimpleNamespace(is_running=True)
    first_gateway = SimpleNamespace(
        read_account=AsyncMock(side_effect=CodexAppServerTimeout("wedged"))
    )
    second_gateway = SimpleNamespace(
        read_account=AsyncMock(return_value="connected")
    )
    recycle = AsyncMock(return_value=replacement)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    monkeypatch.setattr(
        routes,
        "CodexSubscriptionGateway",
        lambda supervisor: first_gateway if supervisor is failed else second_gateway,
    )
    monkeypatch.setattr(routes, "_recycle_supervisor_after_timeout", recycle)

    async def read_account(gateway):
        return await gateway.read_account()

    result = await routes._idempotent_gateway_read(
        request, failed, read_account
    )

    assert result == "connected"
    recycle.assert_awaited_once_with(request, failed)
    first_gateway.read_account.assert_awaited_once()
    second_gateway.read_account.assert_awaited_once()


@pytest.mark.asyncio
async def test_idempotent_read_never_retries_more_than_once(monkeypatch):
    failed = SimpleNamespace(is_running=True)
    replacement = SimpleNamespace(is_running=True)
    first_gateway = SimpleNamespace(
        read_account=AsyncMock(side_effect=CodexAppServerTimeout("first"))
    )
    second_gateway = SimpleNamespace(
        read_account=AsyncMock(side_effect=CodexAppServerTimeout("second"))
    )
    recycle = AsyncMock(return_value=replacement)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    monkeypatch.setattr(
        routes,
        "CodexSubscriptionGateway",
        lambda supervisor: first_gateway if supervisor is failed else second_gateway,
    )
    monkeypatch.setattr(routes, "_recycle_supervisor_after_timeout", recycle)

    async def read_account(gateway):
        return await gateway.read_account()

    with pytest.raises(CodexAppServerTimeout, match="second"):
        await routes._idempotent_gateway_read(request, failed, read_account)
    recycle.assert_awaited_once_with(request, failed)


@pytest.mark.asyncio
async def test_non_timeout_account_error_is_not_replayed(monkeypatch):
    failed = SimpleNamespace(is_running=True)
    gateway = SimpleNamespace(
        read_account=AsyncMock(side_effect=RuntimeError("account rejected"))
    )
    recycle = AsyncMock()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    monkeypatch.setattr(routes, "CodexSubscriptionGateway", lambda _sup: gateway)
    monkeypatch.setattr(routes, "_recycle_supervisor_after_timeout", recycle)

    async def read_account(current):
        return await current.read_account()

    with pytest.raises(RuntimeError, match="account rejected"):
        await routes._idempotent_gateway_read(request, failed, read_account)
    recycle.assert_not_awaited()


@pytest.mark.asyncio
async def test_recycle_replaces_only_failed_supervisor_under_lock(monkeypatch):
    failed = SimpleNamespace(is_running=True, stop=AsyncMock())
    replacement = SimpleNamespace(is_running=True)
    state = SimpleNamespace(codex_app_server=failed)
    request = SimpleNamespace(app=SimpleNamespace(state=state))
    create = AsyncMock(return_value=replacement)
    detach = MagicMock()
    monkeypatch.setattr(routes, "_create_supervisor", create)
    monkeypatch.setattr(routes, "detach_shared_codex_app_server", detach)

    result = await routes._recycle_supervisor_after_timeout(request, failed)

    assert result is replacement
    failed.stop.assert_awaited_once()
    detach.assert_called_once_with(failed)
    create.assert_awaited_once_with(request)
    assert state.codex_app_server_recycle_count == 1


def test_status_is_passive_and_does_not_start_process():
    with TestClient(_app()) as client:
        response = client.get("/api/codex-subscription/account/status")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "running": False,
        "account": None,
        "quota": None,
    }


def test_diagnostic_is_passive_bounded_and_contains_no_local_secret(monkeypatch):
    app = _app()
    supervisor = SimpleNamespace(
        is_running=True,
        snapshot=lambda: SimpleNamespace(
            state="running",
            pid=123,
            pending_requests=0,
            queued_notifications=0,
            dropped_notifications=0,
            restart_count=1,
            request_count=9,
            request_error_count=1,
            request_timeout_count=1,
            turn_count=2,
            last_latency_ms=12.5,
            average_latency_ms=9.5,
            stderr_tail="Bearer abcdefghijklmnop",
            last_error="",
        ),
    )
    app.state.codex_app_server = supervisor
    preflight = CodexPreflightResult(
        state=CodexCLIState.READY,
        executable=r"C:\Users\private\codex.exe",
        source="configured",
        version="1.2.3",
        schema_files=2,
        detail="ready",
        attempts=(
            CodexProbeAttempt(
                path=r"C:\Users\private\codex.exe",
                source="configured",
                state=CodexCLIState.READY,
                version="1.2.3",
                schema_files=2,
            ),
        ),
    )
    settings = CodexSubscriptionSettings(
        access_mode=OpenAIAccessMode.CHATGPT_CODEX,
        cli_path=r"C:\Users\private\codex.exe",
        default_model="account-model",
        surfaces=frozenset({CodexSurface.CODEAGENT, CodexSurface.AGENT}),
        api_fallback=CodexAPIFallback.NEVER,
    )
    monkeypatch.setattr(
        routes, "probe_codex_cli_async", AsyncMock(return_value=preflight)
    )
    monkeypatch.setattr(routes, "load_codex_subscription_settings", lambda: settings)
    monkeypatch.setattr(routes, "CodexSubscriptionGateway", lambda _sup: _gateway())
    with TestClient(app) as client:
        response = client.get("/api/codex-subscription/diagnostic")
    assert response.status_code == 200
    body = response.json()
    rendered = str(body)
    assert body["compatibility"]["compatible"] is True
    assert body["compatibility"]["numeric_minimum"] is None
    assert body["transport"]["turn_count"] == 2
    assert body["transport"]["recycle_count"] == 0
    assert body["configuration"]["api_fallback"] == "never"
    assert "private" not in rendered
    assert "abcdefghijklmnop" not in rendered
    assert "[REDACTED]" in rendered


def test_adopt_returns_only_masked_account_and_quota(monkeypatch):
    app = _app()
    supervisor = SimpleNamespace(is_running=True)
    app.state.codex_app_server = supervisor
    gateway = _gateway()
    monkeypatch.setattr(routes, "_start_supervisor", AsyncMock(return_value=supervisor))
    monkeypatch.setattr(routes, "CodexSubscriptionGateway", lambda _sup: gateway)
    with TestClient(app) as client:
        response = client.post("/api/codex-subscription/adopt")
    assert response.status_code == 200
    body = response.json()
    assert body["account"]["email_masked"] == "c***@e***.com"
    assert "token" not in str(body).lower()


def test_login_start_cancel_wait_and_logout_confirmation(monkeypatch):
    app = _app()
    supervisor = SimpleNamespace(is_running=True)
    gateway = _gateway()
    monkeypatch.setattr(routes, "_start_supervisor", AsyncMock(return_value=supervisor))
    monkeypatch.setattr(routes, "_running_supervisor", lambda _request: supervisor)
    monkeypatch.setattr(routes, "CodexSubscriptionGateway", lambda _sup: gateway)
    with TestClient(app) as client:
        started = client.post("/api/codex-subscription/login/start")
        waited = client.get(
            "/api/codex-subscription/login/wait",
            params={"login_id": "l1", "timeout_s": 5},
        )
        cancelled = client.post(
            "/api/codex-subscription/login/cancel", json={"login_id": "l1"}
        )
        refused = client.post(
            "/api/codex-subscription/logout",
            json={"confirm_shared_codex_logout": False},
        )
        logged_out = client.post(
            "/api/codex-subscription/logout",
            json={"confirm_shared_codex_logout": True},
        )
    assert started.status_code == 200
    assert started.json()["challenge"]["auth_url"].startswith("https://")
    assert waited.status_code == 200
    assert cancelled.json()["cancelled"] is True
    assert refused.status_code == 409
    assert logged_out.json()["logged_out"] is True


def test_routes_require_admin_auth():
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        response = client.get("/api/codex-subscription/account/status")
    assert response.status_code in {401, 403}


def test_models_route_uses_connected_account_and_dynamic_catalog(monkeypatch):
    app = _app()
    supervisor = SimpleNamespace(is_running=True)
    app.state.codex_app_server = supervisor
    gateway = _gateway()
    monkeypatch.setattr(routes, "_running_supervisor", lambda _request: supervisor)
    monkeypatch.setattr(routes, "CodexSubscriptionGateway", lambda _sup: gateway)
    monkeypatch.setattr(
        routes,
        "load_codex_subscription_settings",
        lambda: SimpleNamespace(
            default_model="missing-static-model",
            access_mode=OpenAIAccessMode.API,
        ),
    )
    with TestClient(app) as client:
        response = client.get("/api/codex-subscription/models")
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "openai-codex"
    assert body["access_mode"] == "api"
    assert body["selected_model"] == "account-model"
    assert [model["model_id"] for model in body["models"]] == ["account-model"]
    gateway.require_chatgpt_account.assert_awaited_once()
    gateway.list_models.assert_awaited_once()


def test_models_route_reconnects_configured_subscription_without_config_panel(
    monkeypatch,
):
    app = _app()
    supervisor = SimpleNamespace(is_running=True)
    start = AsyncMock(return_value=supervisor)
    gateway = _gateway()
    monkeypatch.setattr(routes, "_start_supervisor", start)
    monkeypatch.setattr(routes, "CodexSubscriptionGateway", lambda _sup: gateway)
    monkeypatch.setattr(
        routes,
        "load_codex_subscription_settings",
        lambda: SimpleNamespace(
            enabled=False,
            default_model="account-model",
            access_mode=OpenAIAccessMode.API,
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/codex-subscription/models")

    assert response.status_code == 200
    start.assert_awaited_once()
    assert response.json()["selected_model"] == "account-model"


def test_models_route_does_not_start_codex_when_never_configured(monkeypatch):
    app = _app()
    start = AsyncMock()
    monkeypatch.setattr(routes, "_start_supervisor", start)
    monkeypatch.setattr(
        routes,
        "load_codex_subscription_settings",
        lambda: SimpleNamespace(
            enabled=False,
            default_model="",
            access_mode=OpenAIAccessMode.API,
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/codex-subscription/models")

    assert response.status_code == 409
    start.assert_not_awaited()


def test_picker_selects_dynamic_codex_model_and_enables_complete_lumena_surfaces(monkeypatch):
    app = _app()
    supervisor = SimpleNamespace(is_running=True)
    gateway = _gateway()
    persisted = MagicMock()
    app.state.codex_app_server = supervisor
    monkeypatch.setattr(routes, "CodexSubscriptionGateway", lambda _sup: gateway)
    monkeypatch.setattr(
        routes,
        "load_codex_subscription_settings",
        lambda: SimpleNamespace(
            surfaces=frozenset({CodexSurface.CODEAGENT, CodexSurface.MISSIONS})
        ),
    )
    monkeypatch.setattr(routes, "_persist_access_selection", persisted)

    with TestClient(app) as client:
        response = client.post(
            "/api/codex-subscription/model/select",
            json={"selection_id": "codex:account-model"},
        )

    assert response.status_code == 200
    assert response.json()["engine"] == "codex"
    assert response.json()["model"] == "codex:account-model"
    persisted.assert_called_once_with(
        {
            "LUMENA_OPENAI_ACCESS_MODE": "chatgpt_codex",
            "LUMENA_CODEX_DEFAULT_MODEL": "account-model",
            "LUMENA_CODEX_SURFACES": "codeagent,chat,agent,missions",
        }
    )
    gateway.require_chatgpt_account.assert_awaited_once()
    gateway.list_models.assert_awaited_once()


def test_picker_rejects_codex_model_not_exposed_by_account(monkeypatch):
    app = _app()
    app.state.codex_app_server = SimpleNamespace(is_running=True)
    monkeypatch.setattr(routes, "CodexSubscriptionGateway", lambda _sup: _gateway())
    persisted = MagicMock()
    monkeypatch.setattr(routes, "_persist_access_selection", persisted)

    with TestClient(app) as client:
        response = client.post(
            "/api/codex-subscription/model/select",
            json={"selection_id": "codex:not-on-account"},
        )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "codex_model_unavailable"
    persisted.assert_not_called()


def test_picker_restores_historical_api_mode_without_erasing_codex_preferences(
    monkeypatch,
):
    app = _app()
    lumena = SimpleNamespace(llm=object())
    historical = AsyncMock(
        return_value={
            "success": True,
            "model": "api-model",
            "display_name": "API Model",
            "message": "Modele API actif",
        }
    )
    persisted = MagicMock()
    monkeypatch.setattr(routes, "get_lumena", lambda: lumena)
    monkeypatch.setattr(routes, "_switch_historical_api_model", historical)
    monkeypatch.setattr(routes, "_persist_access_selection", persisted)

    with TestClient(app) as client:
        response = client.post(
            "/api/codex-subscription/model/select",
            json={"selection_id": "api-model"},
        )

    assert response.status_code == 200
    assert response.json()["engine"] == "api"
    historical.assert_awaited_once_with("api-model")
    persisted.assert_called_once_with({"LUMENA_OPENAI_ACCESS_MODE": "api"})


def test_picker_rolls_back_api_llm_when_access_mode_persistence_fails(monkeypatch):
    app = _app()
    previous = object()
    replacement = object()
    lumena = SimpleNamespace(llm=previous)

    async def switch_api(_model_name):
        lumena.llm = replacement
        return {"success": True, "display_name": "API Model"}

    monkeypatch.setattr(routes, "get_lumena", lambda: lumena)
    monkeypatch.setattr(routes, "_switch_historical_api_model", switch_api)
    monkeypatch.setattr(
        routes,
        "_persist_access_selection",
        MagicMock(side_effect=OSError("disk unavailable")),
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/codex-subscription/model/select",
            json={"selection_id": "api-model"},
        )

    assert response.status_code == 500
    assert response.json()["detail"]["code"] == "api_selection_persist_failed"
    assert lumena.llm is previous


def test_picker_selection_body_rejects_unknown_fields():
    with TestClient(_app()) as client:
        response = client.post(
            "/api/codex-subscription/model/select",
            json={"selection_id": "api-model", "api_key": "must-not-pass"},
        )
    assert response.status_code == 422


def test_http_errors_redact_credentials():
    error = routes._http_error(503, "failed", "Bearer abcdefghijklmnop")
    assert "abcdefghijklmnop" not in str(error.detail)
    assert "[REDACTED]" in str(error.detail)


class _CollaborationService:
    def __init__(self):
        self.link = AsyncMock(
            return_value=CollaborationLink(thread_id="thr-1", workspace=str(routes.ROOT_DIR))
        )
        self.discover_threads = AsyncMock(
            return_value=(
                (
                    CodexThreadSummary(
                        thread_id="thr-1",
                        cwd=str(routes.ROOT_DIR),
                        name="Audit Lumena",
                    ),
                ),
                "cursor-2",
            )
        )
        self.read_thread = AsyncMock(
            return_value={
                "id": "thr-1",
                "cwd": str(routes.ROOT_DIR),
                "turns": [
                    {
                        "id": "turn-1",
                        "status": "completed",
                        "items": [
                            {"type": "agentMessage", "text": "Termine"},
                            {"type": "reasoning", "text": "cache"},
                        ],
                    }
                ],
            }
        )
        self.create_handoff = AsyncMock(
            return_value=CodexHandoff(
                thread_id="thr-1",
                workspace=str(routes.ROOT_DIR),
                completed=("Termine",),
            )
        )
        self.start_turn = AsyncMock(return_value=("turn-1", None))
        self.steer = AsyncMock(return_value="turn-1")
        self.interrupt = AsyncMock()
        self.fork_ephemeral = AsyncMock(return_value="fork-1")


def test_collaboration_settings_are_local_and_explicit(tmp_path):
    app = _app()
    app.state.codex_collaboration_registry = CodexCollaborationRegistry(
        tmp_path / "collaboration.json"
    )
    with TestClient(app) as client:
        initial = client.get("/api/codex-subscription/collaboration/settings")
        changed = client.post(
            "/api/codex-subscription/collaboration/settings",
            json={"share_mode": "workspace"},
        )
        final = client.get("/api/codex-subscription/collaboration/settings")
    assert initial.json()["share_mode"] == CodexShareMode.SELECTED.value
    assert changed.json()["share_mode"] == CodexShareMode.WORKSPACE.value
    assert final.json()["share_mode"] == CodexShareMode.WORKSPACE.value


def test_collaboration_list_link_read_and_dissociate(monkeypatch, tmp_path):
    app = _app()
    registry = CodexCollaborationRegistry(tmp_path / "collaboration.json")
    app.state.codex_collaboration_registry = registry
    service = _CollaborationService()
    monkeypatch.setattr(routes, "_collaboration_service", lambda _request: service)
    with TestClient(app) as client:
        listed = client.get("/api/codex-subscription/collaboration/threads")
        linked = client.post(
            "/api/codex-subscription/collaboration/link", json={"thread_id": "thr-1"}
        )
        registry.put(CollaborationLink(thread_id="thr-1", workspace=str(routes.ROOT_DIR)))
        read = client.get("/api/codex-subscription/collaboration/thread/thr-1")
        unlinked = client.delete("/api/codex-subscription/collaboration/link/thr-1")
    assert listed.json()["threads"][0]["name"] == "Audit Lumena"
    assert listed.json()["next_cursor"] == "cursor-2"
    assert linked.status_code == 200
    assert "cache" not in str(read.json())
    assert read.json()["turns"][0]["items"][0]["text"] == "Termine"
    assert unlinked.json()["dissociated"] is True


def test_handoff_memory_requires_explicit_approval(monkeypatch):
    app = _app()
    service = _CollaborationService()
    remember = MagicMock()
    monkeypatch.setattr(routes, "_collaboration_service", lambda _request: service)
    monkeypatch.setattr(
        routes,
        "get_lumena",
        lambda: SimpleNamespace(memory=SimpleNamespace(remember=remember)),
    )
    with TestClient(app) as client:
        plain = client.post(
            "/api/codex-subscription/collaboration/handoff",
            json={"thread_id": "thr-1", "approve_memory": False},
        )
        approved = client.post(
            "/api/codex-subscription/collaboration/handoff",
            json={"thread_id": "thr-1", "approve_memory": True},
        )
    assert plain.json()["memory_saved"] is False
    assert approved.json()["memory_saved"] is True
    remember.assert_called_once()


def test_collaboration_turn_controls_are_explicit(monkeypatch):
    app = _app()
    service = _CollaborationService()
    monkeypatch.setattr(routes, "_collaboration_service", lambda _request: service)
    with TestClient(app) as client:
        started = client.post(
            "/api/codex-subscription/collaboration/turn/start",
            json={"thread_id": "thr-1", "instruction": "Relis les tests", "write": False},
        )
        steered = client.post(
            "/api/codex-subscription/collaboration/turn/steer",
            json={
                "thread_id": "thr-1",
                "turn_id": "turn-1",
                "instruction": "Priorise pytest",
            },
        )
        interrupted = client.post(
            "/api/codex-subscription/collaboration/turn/interrupt",
            json={"thread_id": "thr-1", "turn_id": "turn-1"},
        )
        forked = client.post(
            "/api/codex-subscription/collaboration/thread/thr-1/fork"
        )
    assert started.json()["write"] is False
    assert steered.json()["turn_id"] == "turn-1"
    assert interrupted.json()["interrupted"] is True
    assert forked.json() == {"ok": True, "thread_id": "fork-1", "ephemeral": True}
