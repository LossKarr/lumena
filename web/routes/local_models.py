"""Authenticated Web API for Lumena's local-model manager."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import StreamingResponse

from src.local_models.identifiers import IdentifierError, parse_model_reference
from src.local_models.manager import LocalModelManagerError, get_local_model_manager
from src.local_models.ollama_client import OllamaClientError
from src.local_models.contracts import JobState
from web.routes import deps

router = APIRouter(tags=["local-models"])
_JOB_ID = re.compile(r"[0-9a-f]{32}\Z")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ModelTarget(_StrictModel):
    source: Literal["ollama", "huggingface"]
    ref: str = Field(min_length=1, max_length=384)


class InstallRequest(ModelTarget):
    idempotency_key: str = Field(default="", max_length=128)
    enable_after_install: bool = True
    expected_bytes: int | None = Field(default=None, ge=0)


class SelectRequest(ModelTarget):
    role: Literal["primary", "code", "vision", "web"] = "primary"


class DeleteRequest(ModelTarget):
    ticket: str = Field(min_length=32, max_length=128)


class SearchResponse(BaseModel):
    models: list[dict]
    sources: dict
    cached: bool = False
    stale: bool = False


def _manager():
    return get_local_model_manager()


def _http_error(exc: Exception) -> HTTPException:
    code = getattr(exc, "code", str(exc))[:120]
    if code in {
        "model_reference_invalid",
        "model_reference_unsafe",
        "model_reference_shape_invalid",
        "model_reference_component_invalid",
        "model_tag_invalid",
        "catalog_source_invalid",
    }:
        return HTTPException(status_code=400, detail=code)
    if code in {"job_not_found", "model_not_installed"}:
        return HTTPException(status_code=404, detail=code)
    if code in {
        "idempotency_key_conflict",
        "active_model_cannot_be_disabled",
        "active_model_cannot_be_deleted",
        "model_job_in_progress",
        "model_not_enabled",
        "model_not_verified",
    }:
        return HTTPException(status_code=409, detail=code)
    if code.startswith("delete_ticket_"):
        return HTTPException(status_code=403, detail=code)
    if code == "local_model_disk_insufficient":
        return HTTPException(status_code=507, detail=code)
    return HTTPException(status_code=503, detail=code or "local_model_service_unavailable")


@router.get("/api/local-models/status", dependencies=[Depends(deps.verify_admin_token)])
async def local_models_status():
    return await _manager().status()


@router.get("/api/local-models/installed", dependencies=[Depends(deps.verify_admin_token)])
async def local_models_installed():
    try:
        return {"models": await _manager().installed()}
    except (OllamaClientError, LocalModelManagerError) as exc:
        raise _http_error(exc) from None


@router.get("/api/local-models/running", dependencies=[Depends(deps.verify_admin_token)])
async def local_models_running():
    try:
        return {"models": await _manager().client.list_running()}
    except OllamaClientError as exc:
        raise _http_error(exc) from None


@router.get("/api/local-models/search", response_model=SearchResponse, dependencies=[Depends(deps.verify_admin_token)])
async def local_models_search(
    q: str = Query(default="", max_length=160),
    source: Literal["all", "ollama", "huggingface"] = "all",
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=10000),
):
    try:
        return await _manager().search(q, source=source, limit=limit, offset=offset)
    except (ValueError, OllamaClientError) as exc:
        raise _http_error(exc) from None


@router.get("/api/local-models/resolve", dependencies=[Depends(deps.verify_admin_token)])
async def local_models_resolve(source: Literal["ollama", "huggingface"], ref: str = Query(max_length=384)):
    try:
        reference = parse_model_reference(ref, source)
        installed = await _manager().client.list_installed()
        present = next(
            (
                item
                for item in installed
                if item.reference.pull_reference == reference.pull_reference
                or item.reference.canonical == reference.canonical
            ),
            None,
        )
        details = await _manager().client.show(reference.pull_reference) if present else None
        return {
            "reference": reference.as_dict(),
            "installed": present.as_dict() if present else None,
            "details": details,
        }
    except (IdentifierError, OllamaClientError) as exc:
        raise _http_error(exc) from None


@router.get("/api/local-models/recommendations", dependencies=[Depends(deps.verify_admin_token)])
async def local_models_recommendations(
    q: str = Query(default="", max_length=160),
    intent: str = Query(default="general", max_length=64),
    source: Literal["all", "ollama", "huggingface"] = "all",
    limit: int = Query(default=5, ge=1, le=20),
):
    try:
        return await _manager().recommend(q, intent=intent, source=source, limit=limit)
    except (ValueError, OllamaClientError) as exc:
        raise _http_error(exc) from None


@router.get("/api/local-models/jobs", dependencies=[Depends(deps.verify_admin_token)])
async def local_model_jobs(limit: int = Query(default=100, ge=1, le=500)):
    return {"jobs": [job.as_dict() for job in _manager().job_store.list(limit)]}


@router.get("/api/local-models/jobs/{job_id}", dependencies=[Depends(deps.verify_admin_token)])
async def local_model_job(job_id: str):
    if not _JOB_ID.fullmatch(job_id):
        raise HTTPException(status_code=404, detail="job_not_found")
    job = _manager().job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    return job.as_dict()


@router.get("/api/local-models/audit", dependencies=[Depends(deps.verify_admin_token)])
async def local_model_audit(limit: int = Query(default=100, ge=1, le=500)):
    return {"events": _manager().audit.recent(limit)}


@router.post(
    "/api/local-models/install",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(deps.verify_admin_token)],
)
async def local_model_install(request: InstallRequest):
    try:
        job = _manager().install(
            request.ref,
            source=request.source,
            idempotency_key=request.idempotency_key,
            enable_after_install=request.enable_after_install,
            expected_bytes=request.expected_bytes,
            caller_kind="web",
        )
        return job.as_dict()
    except (IdentifierError, LocalModelManagerError) as exc:
        raise _http_error(exc) from None


@router.post("/api/local-models/jobs/{job_id}/cancel", dependencies=[Depends(deps.verify_admin_token)])
async def local_model_cancel(job_id: str):
    if not _JOB_ID.fullmatch(job_id):
        raise HTTPException(status_code=404, detail="job_not_found")
    try:
        return _manager().cancel_job(job_id).as_dict()
    except LocalModelManagerError as exc:
        raise _http_error(exc) from None


@router.post("/api/local-models/enable", dependencies=[Depends(deps.verify_admin_token)])
async def local_model_enable(request: ModelTarget):
    try:
        return await _manager().enable(request.ref, request.source)
    except (IdentifierError, LocalModelManagerError, OllamaClientError) as exc:
        raise _http_error(exc) from None


@router.post("/api/local-models/disable", dependencies=[Depends(deps.verify_admin_token)])
async def local_model_disable(request: ModelTarget):
    try:
        current = getattr(getattr(deps.lumena, "llm", None), "model_name", "")
        return _manager().disable(request.ref, request.source, current_model_key=current)
    except (IdentifierError, LocalModelManagerError) as exc:
        raise _http_error(exc) from None


@router.post("/api/local-models/select", dependencies=[Depends(deps.verify_admin_token)])
async def local_model_select(request: SelectRequest):
    try:
        runtime_llm = getattr(deps.lumena, "llm", None)
        return await _manager().select(request.ref, request.source, runtime_llm=runtime_llm, role=request.role)
    except (IdentifierError, LocalModelManagerError, OllamaClientError) as exc:
        raise _http_error(exc) from None


@router.post("/api/local-models/verify", dependencies=[Depends(deps.verify_admin_token)])
async def local_model_verify(request: ModelTarget):
    try:
        return await _manager().verify(request.ref, request.source)
    except (IdentifierError, LocalModelManagerError, OllamaClientError) as exc:
        raise _http_error(exc) from None


@router.post("/api/local-models/unload", dependencies=[Depends(deps.verify_admin_token)])
async def local_model_unload(request: ModelTarget):
    try:
        return await _manager().unload(request.ref, request.source)
    except (IdentifierError, LocalModelManagerError, OllamaClientError) as exc:
        raise _http_error(exc) from None


@router.post("/api/local-models/delete-ticket", dependencies=[Depends(deps.verify_admin_token)])
async def local_model_delete_ticket(request: ModelTarget):
    try:
        current = getattr(getattr(deps.lumena, "llm", None), "model_name", "")
        return await _manager().prepare_delete(request.ref, request.source, current_model_key=current)
    except (IdentifierError, LocalModelManagerError, OllamaClientError) as exc:
        raise _http_error(exc) from None


@router.post("/api/local-models/delete", dependencies=[Depends(deps.verify_admin_token)])
async def local_model_delete(request: DeleteRequest):
    try:
        current = getattr(getattr(deps.lumena, "llm", None), "model_name", "")
        return await _manager().delete(
            request.ref, request.ticket, request.source, current_model_key=current, caller_kind="web"
        )
    except (IdentifierError, LocalModelManagerError, OllamaClientError) as exc:
        raise _http_error(exc) from None


@router.get("/api/local-models/events", dependencies=[Depends(deps.verify_admin_token)])
async def local_model_events(job_id: str = Query(min_length=32, max_length=32)):
    if not _JOB_ID.fullmatch(job_id):
        raise HTTPException(status_code=404, detail="job_not_found")
    if _manager().job_store.get(job_id) is None:
        raise HTTPException(status_code=404, detail="job_not_found")

    async def stream():
        previous = ""
        while True:
            job = _manager().job_store.get(job_id)
            if job is None:
                yield 'event: error\ndata: {"error_code":"job_not_found"}\n\n'
                return
            payload = json.dumps(job.as_dict(), ensure_ascii=False, separators=(",", ":"))
            if payload != previous:
                yield f"event: progress\ndata: {payload}\n\n"
                previous = payload
            if job.state in {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED, JobState.UNKNOWN_INTERRUPTED}:
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    )
