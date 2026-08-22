"""Admin-only ChatGPT subscription actions backed by Codex App Server."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from src.llm.codex_app_server import (
    CodexAppServerConfig,
    CodexAppServerError,
    CodexAppServerSupervisor,
    CodexAppServerTimeout,
    attach_shared_codex_app_server,
    codex_compatibility_config_overrides,
    detach_shared_codex_app_server,
    redact_codex_diagnostic,
)
from src.llm.codex_subscription import (
    CodexCLIState,
    CodexSurface,
    CodexSubscriptionAccountError,
    CodexSubscriptionGateway,
    OpenAIAccessMode,
    codex_cli_compatibility,
    load_codex_subscription_settings,
    probe_codex_cli_async,
)
from src.llm.codex_collaboration import (
    CodexCollaborationRegistry,
    CodexCollaborationService,
    CodexShareMode,
    WorkspaceWriterLease,
    sanitise_thread_for_ui,
)
from src.utils.paths import ROOT_DIR
from web.routes.deps import get_lumena, verify_admin_token


router = APIRouter(prefix="/api/codex-subscription", tags=["codex-subscription"])
_GatewayResult = TypeVar("_GatewayResult")


class _StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginCancelBody(_StrictBody):
    login_id: str = Field(min_length=1, max_length=256)


class LogoutBody(_StrictBody):
    confirm_shared_codex_logout: bool = False


class CollaborationSettingsBody(_StrictBody):
    share_mode: CodexShareMode


class CollaborationLinkBody(_StrictBody):
    thread_id: str = Field(min_length=1, max_length=256)


class CollaborationHandoffBody(CollaborationLinkBody):
    approve_memory: bool = False


class CollaborationTurnBody(CollaborationLinkBody):
    instruction: str = Field(min_length=1, max_length=12000)
    write: bool = False


class CollaborationSteerBody(CollaborationLinkBody):
    turn_id: str = Field(min_length=1, max_length=256)
    instruction: str = Field(min_length=1, max_length=8000)


class CollaborationInterruptBody(CollaborationLinkBody):
    turn_id: str = Field(min_length=1, max_length=256)


class ModelSelectionBody(_StrictBody):
    selection_id: str = Field(min_length=1, max_length=512)


def _http_error(status: int, code: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"code": code, "message": redact_codex_diagnostic(detail, limit=512)},
    )


async def _get_lock(request: Request) -> asyncio.Lock:
    lock = getattr(request.app.state, "codex_app_server_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        request.app.state.codex_app_server_lock = lock
    return lock


async def _get_model_selection_lock(request: Request) -> asyncio.Lock:
    lock = getattr(request.app.state, "codex_model_selection_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        request.app.state.codex_model_selection_lock = lock
    return lock


def _persist_access_selection(updates: dict[str, str]) -> None:
    """Persist only non-secret access preferences, then hot-reload this process."""
    from web.routes.config import _write_env_values

    _write_env_values(updates)
    for key, value in updates.items():
        os.environ[key] = value


async def _switch_historical_api_model(model_name: str) -> dict:
    """Reuse the historical API switch without changing its public contract."""
    from web.routes.models import ModelSwitchRequest, switch_model

    return await switch_model(ModelSwitchRequest(model_name=model_name))


def _surfaces_for_global_picker(settings) -> str:
    """Enable the complete Lumena runtime when a Codex model is selected.

    The global picker is shared by Chat and Agent mode. Enabling Chat alone
    made the Agent ON surface continue through the historical API provider even
    though the header displayed a Codex model.
    """

    enabled = set(settings.surfaces)
    enabled.update(
        {
            CodexSurface.CHAT,
            CodexSurface.AGENT,
            CodexSurface.MISSIONS,
        }
    )
    return ",".join(
        surface.value for surface in CodexSurface if surface in enabled
    )


async def _model_catalog_supervisor(
    request: Request, settings
) -> CodexAppServerSupervisor:
    current = getattr(request.app.state, "codex_app_server", None)
    if current is not None and current.is_running:
        return current
    if bool(getattr(settings, "enabled", False)) or bool(
        getattr(settings, "default_model", "")
    ):
        return await _start_supervisor(request)
    return _running_supervisor(request)


async def _create_supervisor(request: Request) -> CodexAppServerSupervisor:
    settings = load_codex_subscription_settings()
    preflight = await probe_codex_cli_async(settings.cli_path or None)
    if preflight.state is not CodexCLIState.READY:
        raise _http_error(
            409,
            preflight.state.value,
            preflight.detail or "Codex CLI App Server is not ready",
        )
    supervisor = CodexAppServerSupervisor(
        CodexAppServerConfig.from_executable(
            preflight.executable,
            config_overrides=codex_compatibility_config_overrides(),
        )
    )
    try:
        await supervisor.start()
    except CodexAppServerError as exc:
        await supervisor.stop()
        raise _http_error(503, "app_server_start_failed", str(exc)) from exc
    request.app.state.codex_app_server = supervisor
    attach_shared_codex_app_server(supervisor)
    return supervisor


async def _start_supervisor(request: Request) -> CodexAppServerSupervisor:
    lock = await _get_lock(request)
    async with lock:
        current = getattr(request.app.state, "codex_app_server", None)
        if current is not None and current.is_running:
            return current
        if current is not None:
            await current.stop()
            detach_shared_codex_app_server(current)
            request.app.state.codex_app_server = None
        return await _create_supervisor(request)


async def _recycle_supervisor_after_timeout(
    request: Request,
    failed: CodexAppServerSupervisor,
) -> CodexAppServerSupervisor:
    """Replace one live-but-unresponsive App Server under the lifecycle lock."""

    lock = await _get_lock(request)
    async with lock:
        current = getattr(request.app.state, "codex_app_server", None)
        if current is not None and current is not failed and current.is_running:
            return current
        if current is not None:
            try:
                await current.stop()
            finally:
                detach_shared_codex_app_server(current)
                request.app.state.codex_app_server = None
        request.app.state.codex_app_server_recycle_count = int(
            getattr(request.app.state, "codex_app_server_recycle_count", 0) or 0
        ) + 1
        return await _create_supervisor(request)


async def _idempotent_gateway_read(
    request: Request,
    supervisor: CodexAppServerSupervisor,
    operation: Callable[[CodexSubscriptionGateway], Awaitable[_GatewayResult]],
) -> _GatewayResult:
    """Run a safe read and retry once on a wedged App Server transport."""

    try:
        return await operation(CodexSubscriptionGateway(supervisor))
    except CodexAppServerTimeout:
        replacement = await _recycle_supervisor_after_timeout(request, supervisor)
        return await operation(CodexSubscriptionGateway(replacement))


def _running_supervisor(request: Request) -> CodexAppServerSupervisor:
    supervisor = getattr(request.app.state, "codex_app_server", None)
    if supervisor is None or not supervisor.is_running:
        raise _http_error(409, "app_server_not_running", "Codex App Server is not running")
    return supervisor


def _collaboration_registry(request: Request) -> CodexCollaborationRegistry:
    registry = getattr(request.app.state, "codex_collaboration_registry", None)
    if registry is None:
        registry = CodexCollaborationRegistry()
        request.app.state.codex_collaboration_registry = registry
    return registry


def _collaboration_service(request: Request) -> CodexCollaborationService:
    return CodexCollaborationService(
        _running_supervisor(request), registry=_collaboration_registry(request)
    )


def _active_collaboration_leases(request: Request) -> dict[str, WorkspaceWriterLease]:
    leases = getattr(request.app.state, "codex_collaboration_leases", None)
    if leases is None:
        leases = {}
        request.app.state.codex_collaboration_leases = leases
    return leases


def _release_collaboration_lease(request: Request, thread_id: str, turn_id: str) -> bool:
    lease = _active_collaboration_leases(request).pop(f"{thread_id}:{turn_id}", None)
    if lease is None:
        return False
    lease.release()
    return True


def _collaboration_workspace(request: Request, thread_id: str):
    link = _collaboration_registry(request).get(thread_id)
    return link.workspace if link is not None else ROOT_DIR


@router.get("/preflight", dependencies=[Depends(verify_admin_token)])
async def codex_preflight():
    settings = load_codex_subscription_settings()
    result = await probe_codex_cli_async(settings.cli_path or None)
    return {"ok": result.ready, "preflight": result.to_dict()}


def _diagnostic_preflight(result) -> dict:
    return {
        "state": result.state.value,
        "ready": result.ready,
        "source": result.source,
        "version": result.version,
        "protocol_family": result.protocol_family,
        "schema_files": result.schema_files,
        "detail": redact_codex_diagnostic(result.detail, limit=512),
        "attempts": [
            {
                "source": attempt.source,
                "state": attempt.state.value,
                "version": attempt.version,
                "schema_files": attempt.schema_files,
            }
            for attempt in result.attempts
        ],
    }


def _diagnostic_transport(supervisor) -> dict | None:
    if supervisor is None:
        return None
    snapshot = supervisor.snapshot()
    raw = asdict(snapshot) if is_dataclass(snapshot) else vars(snapshot)
    state = raw.get("state", "")
    if hasattr(state, "value"):
        state = state.value
    numeric_keys = (
        "pending_requests",
        "queued_notifications",
        "dropped_notifications",
        "restart_count",
        "request_count",
        "request_error_count",
        "request_timeout_count",
        "turn_count",
        "last_latency_ms",
        "average_latency_ms",
    )
    payload = {"state": str(state), "running": bool(supervisor.is_running)}
    for key in numeric_keys:
        payload[key] = raw.get(key, 0)
    payload["last_error"] = redact_codex_diagnostic(
        str(raw.get("last_error") or ""), limit=512
    )
    payload["stderr_tail"] = redact_codex_diagnostic(
        str(raw.get("stderr_tail") or ""), limit=1024
    )
    return payload


@router.get("/diagnostic", dependencies=[Depends(verify_admin_token)])
async def codex_diagnostic(request: Request):
    """Export a passive, bounded support snapshot without local credentials."""

    settings = load_codex_subscription_settings()
    preflight = await probe_codex_cli_async(settings.cli_path or None)
    supervisor = getattr(request.app.state, "codex_app_server", None)
    running = bool(supervisor is not None and supervisor.is_running)
    account = None
    quota = None
    account_error = ""
    if running:
        gateway = CodexSubscriptionGateway(supervisor)
        try:
            account_summary, quota_summary = await asyncio.gather(
                gateway.read_account(), gateway.read_rate_limits()
            )
            account = account_summary.to_dict()
            quota = quota_summary.to_dict()
        except (CodexAppServerError, CodexSubscriptionAccountError) as exc:
            account_error = redact_codex_diagnostic(str(exc), limit=512)

    collaboration = getattr(
        request.app.state, "codex_collaboration_registry", None
    )
    links = collaboration.list_links() if collaboration is not None else ()
    share_mode = (
        collaboration.share_mode().value
        if collaboration is not None
        else CodexShareMode.SELECTED.value
    )
    transport = _diagnostic_transport(supervisor)
    if transport is not None:
        transport["recycle_count"] = int(
            getattr(request.app.state, "codex_app_server_recycle_count", 0) or 0
        )
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "local_single_user",
        "compatibility": codex_cli_compatibility(
            observed_version=preflight.version,
            ready=preflight.ready,
        ),
        "preflight": _diagnostic_preflight(preflight),
        "configuration": {
            "access_mode": settings.access_mode.value,
            "surfaces": sorted(surface.value for surface in settings.surfaces),
            "api_fallback": settings.api_fallback.value,
            "model_selected": bool(settings.default_model),
        },
        "account": account,
        "quota": quota,
        "account_error": account_error,
        "transport": transport,
        "collaboration": {
            "share_mode": share_mode,
            "linked_threads": len(links),
        },
    }


@router.get("/account/status", dependencies=[Depends(verify_admin_token)])
async def codex_status(request: Request):
    supervisor = getattr(request.app.state, "codex_app_server", None)
    if supervisor is None or not supervisor.is_running:
        return {"ok": True, "running": False, "account": None, "quota": None}
    async def read_status(gateway: CodexSubscriptionGateway):
        return await asyncio.gather(
            gateway.read_account(), gateway.read_rate_limits()
        )

    try:
        account, quota = await _idempotent_gateway_read(
            request, supervisor, read_status
        )
    except (CodexAppServerError, CodexSubscriptionAccountError) as exc:
        raise _http_error(503, "codex_status_failed", str(exc)) from exc
    return {
        "ok": True,
        "running": True,
        "account": account.to_dict(),
        "quota": quota.to_dict(),
        "transport": _running_supervisor(request).snapshot().__dict__,
    }


@router.post("/adopt", dependencies=[Depends(verify_admin_token)])
async def codex_adopt_existing_session(request: Request):
    supervisor = await _start_supervisor(request)

    async def read_adopt(gateway: CodexSubscriptionGateway):
        account = await gateway.require_chatgpt_account(refresh=True)
        quota = await gateway.read_rate_limits()
        return account, quota

    try:
        account, quota = await _idempotent_gateway_read(
            request, supervisor, read_adopt
        )
    except CodexSubscriptionAccountError as exc:
        raise _http_error(409, "chatgpt_session_unavailable", str(exc)) from exc
    except CodexAppServerError as exc:
        raise _http_error(503, "codex_account_failed", str(exc)) from exc
    return {"ok": True, "account": account.to_dict(), "quota": quota.to_dict()}


@router.post("/login/start", dependencies=[Depends(verify_admin_token)])
async def codex_login_start(request: Request):
    supervisor = await _start_supervisor(request)
    try:
        challenge = await CodexSubscriptionGateway(supervisor).start_login()
    except (CodexAppServerError, CodexSubscriptionAccountError) as exc:
        raise _http_error(503, "codex_login_start_failed", str(exc)) from exc
    request.app.state.codex_login_id = challenge.login_id
    return {"ok": True, "challenge": challenge.to_dict()}


@router.get("/login/wait", dependencies=[Depends(verify_admin_token)])
async def codex_login_wait(
    request: Request,
    login_id: str = Query(min_length=1, max_length=256),
    timeout_s: float = Query(default=60.0, ge=1.0, le=120.0),
):
    supervisor = _running_supervisor(request)
    try:
        account = await CodexSubscriptionGateway(supervisor).wait_for_login(
            login_id, timeout=timeout_s
        )
    except (CodexAppServerError, CodexSubscriptionAccountError) as exc:
        raise _http_error(408, "codex_login_incomplete", str(exc)) from exc
    request.app.state.codex_login_id = None
    return {"ok": True, "account": account.to_dict()}


@router.post("/login/cancel", dependencies=[Depends(verify_admin_token)])
async def codex_login_cancel(request: Request, body: LoginCancelBody):
    supervisor = _running_supervisor(request)
    try:
        await CodexSubscriptionGateway(supervisor).cancel_login(body.login_id)
    except (CodexAppServerError, CodexSubscriptionAccountError) as exc:
        raise _http_error(503, "codex_login_cancel_failed", str(exc)) from exc
    request.app.state.codex_login_id = None
    return {"ok": True, "cancelled": True}


@router.get("/quota", dependencies=[Depends(verify_admin_token)])
async def codex_quota(request: Request):
    supervisor = _running_supervisor(request)

    async def read_quota(gateway: CodexSubscriptionGateway):
        return await gateway.read_rate_limits()

    try:
        quota = await _idempotent_gateway_read(request, supervisor, read_quota)
    except CodexAppServerError as exc:
        raise _http_error(503, "codex_quota_failed", str(exc)) from exc
    return {"ok": True, "quota": quota.to_dict()}


@router.get("/models", dependencies=[Depends(verify_admin_token)])
async def codex_models(request: Request):
    settings = load_codex_subscription_settings()
    supervisor = await _model_catalog_supervisor(request, settings)
    async def read_models(gateway: CodexSubscriptionGateway):
        await gateway.require_chatgpt_account()
        return await gateway.list_models()

    try:
        models = await _idempotent_gateway_read(request, supervisor, read_models)
    except CodexSubscriptionAccountError as exc:
        raise _http_error(409, "chatgpt_session_unavailable", str(exc)) from exc
    except CodexAppServerError as exc:
        raise _http_error(503, "codex_models_failed", str(exc)) from exc
    available_ids = {model.model_id for model in models}
    selected = settings.default_model if settings.default_model in available_ids else ""
    if not selected:
        selected = next((model.model_id for model in models if model.is_default), "")
    return {
        "ok": True,
        "provider": "openai-codex",
        "access_mode": settings.access_mode.value,
        "selected_model": selected,
        "models": [model.to_dict() for model in models],
    }


@router.post("/model/select", dependencies=[Depends(verify_admin_token)])
async def codex_model_select(
    request: Request,
    body: ModelSelectionBody,
):
    """Select a namespaced Codex model or restore a historical API model."""
    selection_id = body.selection_id.strip()
    lock = await _get_model_selection_lock(request)
    async with lock:
        if selection_id.startswith("codex:"):
            model_id = selection_id.removeprefix("codex:").strip()
            if not model_id:
                raise _http_error(400, "invalid_model", "Modele Codex vide")

            settings = load_codex_subscription_settings()
            supervisor = await _model_catalog_supervisor(request, settings)

            async def read_models(gateway: CodexSubscriptionGateway):
                await gateway.require_chatgpt_account()
                return await gateway.list_models()

            try:
                models = await _idempotent_gateway_read(
                    request, supervisor, read_models
                )
            except CodexSubscriptionAccountError as exc:
                raise _http_error(
                    409, "chatgpt_session_unavailable", str(exc)
                ) from exc
            except CodexAppServerError as exc:
                raise _http_error(503, "codex_models_failed", str(exc)) from exc

            selected = next(
                (model for model in models if model.model_id == model_id), None
            )
            if selected is None:
                raise _http_error(
                    400,
                    "codex_model_unavailable",
                    f"Le modele Codex '{model_id}' n'est pas disponible pour ce compte",
                )

            updates = {
                "LUMENA_OPENAI_ACCESS_MODE": OpenAIAccessMode.CHATGPT_CODEX.value,
                "LUMENA_CODEX_DEFAULT_MODEL": model_id,
                "LUMENA_CODEX_SURFACES": _surfaces_for_global_picker(settings),
            }
            try:
                _persist_access_selection(updates)
            except Exception as exc:
                raise _http_error(
                    500, "codex_selection_persist_failed", str(exc)
                ) from exc
            return {
                "success": True,
                "engine": "codex",
                "access_mode": OpenAIAccessMode.CHATGPT_CODEX.value,
                "model": f"codex:{model_id}",
                "display_name": selected.display_name or model_id,
                "message": (
                    f"Modele Codex {selected.display_name or model_id} actif "
                    "via l'abonnement ChatGPT"
                ),
            }

        lumena = get_lumena()
        previous_llm = getattr(lumena, "llm", None) if lumena is not None else None
        switched = await _switch_historical_api_model(selection_id)
        try:
            _persist_access_selection(
                {"LUMENA_OPENAI_ACCESS_MODE": OpenAIAccessMode.API.value}
            )
        except Exception as exc:
            if lumena is not None:
                lumena.llm = previous_llm
            raise _http_error(
                500, "api_selection_persist_failed", str(exc)
            ) from exc
        return {
            **switched,
            "engine": "api",
            "access_mode": OpenAIAccessMode.API.value,
        }


@router.get("/collaboration/settings", dependencies=[Depends(verify_admin_token)])
async def codex_collaboration_settings(request: Request):
    registry = _collaboration_registry(request)
    return {
        "ok": True,
        "share_mode": registry.share_mode().value,
        "links": [asdict(link) for link in registry.list_links()],
    }


@router.post("/collaboration/settings", dependencies=[Depends(verify_admin_token)])
async def codex_collaboration_settings_update(
    request: Request, body: CollaborationSettingsBody
):
    mode = _collaboration_registry(request).set_share_mode(body.share_mode)
    return {"ok": True, "share_mode": mode.value}


@router.get("/collaboration/threads", dependencies=[Depends(verify_admin_token)])
async def codex_collaboration_threads(
    request: Request,
    cursor: str = Query(default="", max_length=2048),
    limit: int = Query(default=25, ge=1, le=100),
):
    try:
        registry = _collaboration_registry(request)
        threads, next_cursor = await _collaboration_service(request).discover_threads(
            ROOT_DIR,
            cursor=cursor,
            limit=limit,
            include_other_workspaces=(
                registry.share_mode() is CodexShareMode.ALL_LOCAL
            ),
        )
    except Exception as exc:
        raise _http_error(503, "codex_collaboration_list_failed", str(exc)) from exc
    linked = {link.thread_id for link in _collaboration_registry(request).list_links()}
    return {
        "ok": True,
        "workspace": str(ROOT_DIR.resolve()),
        "threads": [
            {**asdict(thread), "linked": thread.thread_id in linked}
            for thread in threads
        ],
        "next_cursor": next_cursor or None,
    }


@router.post("/collaboration/link", dependencies=[Depends(verify_admin_token)])
async def codex_collaboration_link(request: Request, body: CollaborationLinkBody):
    try:
        link = await _collaboration_service(request).link(body.thread_id, ROOT_DIR)
    except PermissionError as exc:
        raise _http_error(403, "codex_collaboration_workspace_refused", str(exc)) from exc
    except Exception as exc:
        raise _http_error(409, "codex_collaboration_link_failed", str(exc)) from exc
    return {"ok": True, "link": asdict(link)}


@router.delete(
    "/collaboration/link/{thread_id}", dependencies=[Depends(verify_admin_token)]
)
async def codex_collaboration_unlink(request: Request, thread_id: str):
    removed = _collaboration_registry(request).delete(thread_id)
    return {"ok": True, "dissociated": removed}


@router.get(
    "/collaboration/thread/{thread_id}", dependencies=[Depends(verify_admin_token)]
)
async def codex_collaboration_read(request: Request, thread_id: str):
    try:
        thread = await _collaboration_service(request).read_thread(
            thread_id,
            _collaboration_workspace(request, thread_id),
            include_turns=True,
        )
    except PermissionError as exc:
        raise _http_error(403, "codex_collaboration_consent_required", str(exc)) from exc
    except Exception as exc:
        raise _http_error(409, "codex_collaboration_read_failed", str(exc)) from exc
    return {"ok": True, **sanitise_thread_for_ui(thread)}


@router.post("/collaboration/handoff", dependencies=[Depends(verify_admin_token)])
async def codex_collaboration_handoff(
    request: Request, body: CollaborationHandoffBody
):
    try:
        handoff = await _collaboration_service(request).create_handoff(
            body.thread_id,
            _collaboration_workspace(request, body.thread_id),
            approve_memory=body.approve_memory,
        )
    except PermissionError as exc:
        raise _http_error(403, "codex_collaboration_consent_required", str(exc)) from exc
    except Exception as exc:
        raise _http_error(409, "codex_collaboration_handoff_failed", str(exc)) from exc
    if body.approve_memory:
        lumena = get_lumena()
        memory = getattr(lumena, "memory", None) if lumena is not None else None
        remember = getattr(memory, "remember", None)
        if callable(remember):
            remember(
                "Passation Codex approuvee: "
                + json.dumps(asdict(handoff), ensure_ascii=False),
                memory_type="semantic",
                importance=0.65,
            )
    return {"ok": True, "handoff": asdict(handoff), "memory_saved": body.approve_memory}


@router.post("/collaboration/turn/start", dependencies=[Depends(verify_admin_token)])
async def codex_collaboration_turn_start(
    request: Request, body: CollaborationTurnBody
):
    try:
        turn_id, lease = await _collaboration_service(request).start_turn(
            body.thread_id,
            _collaboration_workspace(request, body.thread_id),
            body.instruction,
            write=body.write,
        )
    except PermissionError as exc:
        raise _http_error(403, "codex_collaboration_consent_required", str(exc)) from exc
    except Exception as exc:
        raise _http_error(409, "codex_collaboration_turn_refused", str(exc)) from exc
    if lease is not None:
        _active_collaboration_leases(request)[f"{body.thread_id}:{turn_id}"] = lease
    return {"ok": True, "thread_id": body.thread_id, "turn_id": turn_id, "write": body.write}


@router.post("/collaboration/turn/steer", dependencies=[Depends(verify_admin_token)])
async def codex_collaboration_turn_steer(
    request: Request, body: CollaborationSteerBody
):
    try:
        turn_id = await _collaboration_service(request).steer(
            body.thread_id, body.turn_id, body.instruction
        )
    except Exception as exc:
        raise _http_error(409, "codex_collaboration_steer_failed", str(exc)) from exc
    return {"ok": True, "turn_id": turn_id}


@router.post("/collaboration/turn/interrupt", dependencies=[Depends(verify_admin_token)])
async def codex_collaboration_turn_interrupt(
    request: Request, body: CollaborationInterruptBody
):
    try:
        await _collaboration_service(request).interrupt(body.thread_id, body.turn_id)
    except Exception as exc:
        raise _http_error(409, "codex_collaboration_interrupt_failed", str(exc)) from exc
    released = _release_collaboration_lease(request, body.thread_id, body.turn_id)
    return {"ok": True, "interrupted": True, "writer_released": released}


@router.post("/collaboration/turn/release", dependencies=[Depends(verify_admin_token)])
async def codex_collaboration_turn_release(
    request: Request, body: CollaborationInterruptBody
):
    released = _release_collaboration_lease(request, body.thread_id, body.turn_id)
    return {"ok": True, "writer_released": released}


@router.post(
    "/collaboration/thread/{thread_id}/fork", dependencies=[Depends(verify_admin_token)]
)
async def codex_collaboration_fork(request: Request, thread_id: str):
    try:
        fork_id = await _collaboration_service(request).fork_ephemeral(thread_id)
    except Exception as exc:
        raise _http_error(409, "codex_collaboration_fork_failed", str(exc)) from exc
    return {"ok": True, "thread_id": fork_id, "ephemeral": True}


@router.post("/logout", dependencies=[Depends(verify_admin_token)])
async def codex_logout(request: Request, body: LogoutBody):
    if not body.confirm_shared_codex_logout:
        raise _http_error(
            409,
            "shared_logout_confirmation_required",
            "Logout disconnects the Codex account shared by local Codex clients",
        )
    supervisor = _running_supervisor(request)
    try:
        await CodexSubscriptionGateway(supervisor).logout()
    except CodexAppServerError as exc:
        raise _http_error(503, "codex_logout_failed", str(exc)) from exc
    return {"ok": True, "logged_out": True}
