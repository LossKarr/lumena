"""Administrative API for certified Lumena GitHub updates."""
from __future__ import annotations

import os
import sys

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from src.runtime.update_service import UpdateService, UpdateServiceError
from web.routes import deps

router = APIRouter(tags=["updates"])


class VersionSelection(BaseModel):
    version: str


def _service() -> UpdateService:
    service = deps.get_update_service_singleton()
    if service is None:
        service = UpdateService()
        deps._UPDATE_SERVICE_SINGLETON = service
    return service


def _http_error(exc: UpdateServiceError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def collect_update_busy_reasons() -> list[str]:
    reasons: list[str] = []
    orchestrator = deps.get_task_orchestrator()
    if orchestrator is not None:
        try:
            backlog = int(orchestrator.stats().get("backlog_tasks") or 0)
            if backlog:
                reasons.append(f"{backlog} tache(s) en file ou active(s)")
        except Exception:
            reasons.append("etat des taches impossible a certifier")
    core = deps.get_lumena()
    if core is not None:
        try:
            from src.subagents.manager import get_mission_manager
            running = get_mission_manager(core).running_count()
            if running:
                reasons.append(f"{running} mission(s) ou worker(s) actif(s)")
        except Exception:
            reasons.append("etat des missions impossible a certifier")
    try:
        from src.runtime.peer_mission_worker import mission_load
        peer = mission_load()
        peer_count = int(peer.get("running") or 0) + int(peer.get("waiting") or 0)
        if peer_count:
            reasons.append(f"{peer_count} mission(s) P2P active(s) ou en attente")
    except Exception:
        pass
    return reasons


@router.get("/api/updates/status", dependencies=[Depends(deps.verify_admin_token)])
async def update_status():
    return _service().status()


@router.get("/api/updates/releases", dependencies=[Depends(deps.verify_admin_token)])
async def update_releases(force: bool = False):
    try:
        return {"releases": [entry.as_dict() for entry in await _service().list_releases(force=force)]}
    except UpdateServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/api/updates/check", dependencies=[Depends(deps.verify_admin_token)])
async def check_updates():
    try:
        return await _service().check(force=True)
    except UpdateServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/api/updates/select", dependencies=[Depends(deps.verify_admin_token)])
async def select_update(body: VersionSelection):
    try:
        return await _service().prepare_version(body.version)
    except UpdateServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/api/updates/download", dependencies=[Depends(deps.verify_admin_token)])
async def download_update():
    try:
        return await _service().download_selected()
    except UpdateServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/api/updates/apply", dependencies=[Depends(deps.verify_admin_token)])
async def apply_update(request: Request):
    if not request.client or request.client.host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="application de mise a jour reservee a localhost")
    root = _service().root
    restart = [sys.executable, str(root / "run_desktop.py")]
    port = int(os.getenv("LUMENA_PORT", "8080"))
    try:
        return await _service().launch_apply(
            busy_reasons=collect_update_busy_reasons(), parent_pid=os.getpid(),
            restart_command=restart, health_url=f"http://127.0.0.1:{port}/api/health",
        )
    except UpdateServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/api/updates/rollback", dependencies=[Depends(deps.verify_admin_token)])
async def rollback_update(request: Request):
    if not request.client or request.client.host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="rollback reserve a localhost")
    service = _service()
    port = int(os.getenv("LUMENA_PORT", "8080"))
    try:
        return await service.launch_rollback(
            busy_reasons=collect_update_busy_reasons(), parent_pid=os.getpid(),
            restart_command=[sys.executable, str(service.root / "run_desktop.py")],
            health_url=f"http://127.0.0.1:{port}/api/health",
        )
    except UpdateServiceError as exc:
        raise _http_error(exc) from exc
