"""Authenticated API for Lumena's personal evolving model."""

from __future__ import annotations

from typing import Any
from pathlib import Path
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from src.training.personal.control_plane import PersonalModelControlPlane
from src.utils.paths import DATA_DIR
from web.routes import deps


router = APIRouter(tags=["personal-model"])
_PLANE: PersonalModelControlPlane | None = None


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SettingsPatch(_StrictModel):
    changes: dict[str, Any]


class PolicyPatch(_StrictModel):
    changes: dict[str, Any]
    approval_token: str = Field(default="", max_length=256)


class ApprovalRequest(_StrictModel):
    action: str = Field(min_length=1, max_length=64)
    resource: str = Field(min_length=1, max_length=160)


class TrainingCreateRequest(_StrictModel):
    base_model_id: str = Field(min_length=1, max_length=384)
    display_prefix: str = Field(default="lumena", min_length=1, max_length=48)
    bump: str = Field(default="patch", pattern="^(patch|minor|major)$")
    finetune: dict[str, Any] = Field(default_factory=dict)


class ApprovalAction(_StrictModel):
    approval_token: str = Field(min_length=32, max_length=256)


class VersionAction(ApprovalAction):
    lineage_id: str = Field(min_length=1, max_length=96)
    version: str = Field(min_length=5, max_length=32)


class VersionReference(_StrictModel):
    lineage_id: str = Field(min_length=1, max_length=96)
    version: str = Field(min_length=5, max_length=32)


class MigrationRequest(_StrictModel):
    sources: list[str] = Field(min_length=1, max_length=256)


class MigrationImportRequest(MigrationRequest):
    approval_token: str = Field(min_length=32, max_length=256)


class BackupRestoreRequest(ApprovalAction):
    backup_name: str = Field(min_length=1, max_length=200)


class VersionExportAction(VersionAction):
    quant_type: str = Field(default="Q4_K_M", pattern="^(Q4_K_M|Q5_K_M|Q8_0)$")


def get_personal_model_control_plane() -> PersonalModelControlPlane:
    global _PLANE
    if _PLANE is None:
        _PLANE = PersonalModelControlPlane(DATA_DIR / "personal_model")
        _PLANE.reconcile_training_jobs(actor="personal-model-api-startup")
    return _PLANE


def _translate(exc: Exception) -> HTTPException:
    code = str(exc)[:180] or type(exc).__name__
    if isinstance(exc, PermissionError):
        return HTTPException(status_code=403, detail=code)
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=code.strip("'"))
    if isinstance(exc, (ValueError, FileExistsError)):
        return HTTPException(status_code=409, detail=code)
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=code)
    return HTTPException(status_code=503, detail="personal_model_service_unavailable")


@router.get("/api/personal-model/status", dependencies=[Depends(deps.verify_admin_token)])
async def status_snapshot():
    return get_personal_model_control_plane().status()


@router.get("/api/personal-model/health", dependencies=[Depends(deps.verify_admin_token)])
async def learning_health():
    return get_personal_model_control_plane().health()


@router.get("/api/personal-model/experiences", dependencies=[Depends(deps.verify_admin_token)])
async def experience_stats():
    return get_personal_model_control_plane().experience_stats()


@router.get("/api/personal-model/settings", dependencies=[Depends(deps.verify_admin_token)])
async def training_settings():
    return get_personal_model_control_plane().training_settings()


@router.patch("/api/personal-model/settings", dependencies=[Depends(deps.verify_admin_token)])
async def update_training_settings(request: SettingsPatch):
    try:
        return get_personal_model_control_plane().update_training_settings(request.changes, actor="web-owner")
    except Exception as exc:
        raise _translate(exc) from None


@router.patch("/api/personal-model/policy", dependencies=[Depends(deps.verify_admin_token)])
async def update_learning_policy(request: PolicyPatch):
    try:
        return get_personal_model_control_plane().update_policy(request.changes, actor="web-owner", approval_token=request.approval_token)
    except Exception as exc:
        raise _translate(exc) from None


@router.get("/api/personal-model/recommendations", dependencies=[Depends(deps.verify_admin_token)])
async def recommendations():
    return {"recommendations": get_personal_model_control_plane().recommendations()}


@router.get("/api/personal-model/audit", dependencies=[Depends(deps.verify_admin_token)])
async def audit_trail(limit: int = Query(default=100, ge=1, le=500)):
    return {"events": get_personal_model_control_plane().audit_trail(limit)}


@router.post("/api/personal-model/approvals", dependencies=[Depends(deps.verify_admin_token)])
async def request_approval(request: ApprovalRequest):
    try:
        return get_personal_model_control_plane().approval_preview(action=request.action, resource=request.resource, actor="web-owner")
    except Exception as exc:
        raise _translate(exc) from None


@router.post("/api/personal-model/datasets/prepare", dependencies=[Depends(deps.verify_admin_token)])
async def prepare_dataset():
    try:
        return await asyncio.to_thread(get_personal_model_control_plane().prepare_dataset)
    except Exception as exc:
        raise _translate(exc) from None


@router.post("/api/personal-model/training", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(deps.verify_admin_token)])
async def create_training(request: TrainingCreateRequest):
    try:
        return await asyncio.to_thread(
            get_personal_model_control_plane().create_training,
            base_model_id=request.base_model_id, finetune=request.finetune,
            bump=request.bump, display_prefix=request.display_prefix,
        )
    except Exception as exc:
        raise _translate(exc) from None


@router.post("/api/personal-model/training/{run_id}/launch", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(deps.verify_admin_token)])
async def launch_training(run_id: str):
    try:
        return get_personal_model_control_plane().launch_training(run_id)
    except Exception as exc:
        raise _translate(exc) from None


@router.post("/api/personal-model/training/{run_id}/pause", dependencies=[Depends(deps.verify_admin_token)])
async def pause_training(run_id: str):
    try:
        return get_personal_model_control_plane().pause_training(run_id)
    except Exception as exc:
        raise _translate(exc) from None


@router.post("/api/personal-model/training/{run_id}/resume", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(deps.verify_admin_token)])
async def resume_training(run_id: str):
    try:
        return get_personal_model_control_plane().resume_training(run_id)
    except Exception as exc:
        raise _translate(exc) from None


@router.post("/api/personal-model/training/{run_id}/cancel", dependencies=[Depends(deps.verify_admin_token)])
async def cancel_training(run_id: str, request: ApprovalAction):
    try:
        return get_personal_model_control_plane().cancel_training(run_id, approval_token=request.approval_token)
    except Exception as exc:
        raise _translate(exc) from None


@router.post("/api/personal-model/versions/activate", dependencies=[Depends(deps.verify_admin_token)])
async def activate_version(request: VersionAction):
    try:
        return get_personal_model_control_plane().activate_version(request.lineage_id, request.version, approval_token=request.approval_token)
    except Exception as exc:
        raise _translate(exc) from None


@router.post("/api/personal-model/versions/default", dependencies=[Depends(deps.verify_admin_token)])
async def set_global_default(request: VersionAction):
    try:
        return get_personal_model_control_plane().set_global_default(
            request.lineage_id, request.version, approval_token=request.approval_token, actor="web-owner"
        )
    except Exception as exc:
        raise _translate(exc) from None


@router.post("/api/personal-model/versions/default/principal", dependencies=[Depends(deps.verify_admin_token)])
async def use_principal_default(request: ApprovalAction):
    try:
        return get_personal_model_control_plane().use_principal_default(
            approval_token=request.approval_token, actor="web-owner"
        )
    except Exception as exc:
        raise _translate(exc) from None


@router.post("/api/personal-model/versions/rollback", dependencies=[Depends(deps.verify_admin_token)])
async def rollback_version(request: VersionAction):
    try:
        return get_personal_model_control_plane().rollback_version(request.lineage_id, request.version, approval_token=request.approval_token)
    except Exception as exc:
        raise _translate(exc) from None


@router.post("/api/personal-model/versions/export", dependencies=[Depends(deps.verify_admin_token)])
async def export_version(request: VersionExportAction):
    try:
        return await asyncio.to_thread(
            get_personal_model_control_plane().export_version,
            request.lineage_id, request.version,
            approval_token=request.approval_token,
            actor="web-owner", quant_type=request.quant_type,
        )
    except Exception as exc:
        raise _translate(exc) from None


@router.post("/api/personal-model/versions/evaluate", dependencies=[Depends(deps.verify_admin_token)])
async def evaluate_version(request: VersionReference):
    try:
        return await asyncio.to_thread(
            get_personal_model_control_plane().evaluate_version,
            request.lineage_id, request.version, actor="web-owner",
        )
    except Exception as exc:
        raise _translate(exc) from None


@router.post("/api/personal-model/migration/preview", dependencies=[Depends(deps.verify_admin_token)])
async def migration_preview(request: MigrationRequest):
    try:
        return await asyncio.to_thread(
            get_personal_model_control_plane().migration_preview,
            [Path(item) for item in request.sources],
        )
    except Exception as exc:
        raise _translate(exc) from None


@router.get("/api/personal-model/migration/sources", dependencies=[Depends(deps.verify_admin_token)])
async def migration_sources():
    sources = await asyncio.to_thread(get_personal_model_control_plane().migration_sources)
    return {"sources": sources}


@router.post("/api/personal-model/migration/import", dependencies=[Depends(deps.verify_admin_token)])
async def migration_import(request: MigrationImportRequest):
    try:
        return await asyncio.to_thread(
            get_personal_model_control_plane().import_historical,
            [Path(item) for item in request.sources],
            approval_token=request.approval_token,
            actor="web-owner",
        )
    except Exception as exc:
        raise _translate(exc) from None


@router.post("/api/personal-model/backups", dependencies=[Depends(deps.verify_admin_token)])
async def create_backup(request: ApprovalAction):
    try:
        return await asyncio.to_thread(
            get_personal_model_control_plane().create_backup,
            approval_token=request.approval_token,
            actor="web-owner",
        )
    except Exception as exc:
        raise _translate(exc) from None


@router.get("/api/personal-model/backups", dependencies=[Depends(deps.verify_admin_token)])
async def list_backups():
    backups = await asyncio.to_thread(get_personal_model_control_plane().list_backups)
    return {"backups": backups}


@router.post("/api/personal-model/backups/restore", dependencies=[Depends(deps.verify_admin_token)])
async def restore_backup(request: BackupRestoreRequest):
    try:
        return await asyncio.to_thread(
            get_personal_model_control_plane().restore_backup,
            request.backup_name,
            approval_token=request.approval_token,
            actor="web-owner",
        )
    except Exception as exc:
        raise _translate(exc) from None


@router.post("/api/personal-model/data/delete", dependencies=[Depends(deps.verify_admin_token)])
async def delete_learning_data(request: ApprovalAction):
    try:
        return await asyncio.to_thread(
            get_personal_model_control_plane().delete_learning_data,
            approval_token=request.approval_token,
            actor="web-owner",
        )
    except Exception as exc:
        raise _translate(exc) from None
