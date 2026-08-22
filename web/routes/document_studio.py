"""Document Studio API: templates, previews, library, import and web acquisition."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from src.documents.studio import get_document_studio
from src.documents.template_models import TemplateValidationError
from src.documents.template_security import resolve_within
from src.utils.paths import WORKSPACE_DIR
from web.routes import deps


router = APIRouter(prefix="/api/document-studio", tags=["document-studio"])


def _fail(exc: Exception) -> HTTPException:
    status = 404 if isinstance(exc, KeyError) else 400
    return HTTPException(status_code=status, detail=str(exc))


@router.get("/templates", dependencies=[Depends(deps.verify_admin_token)])
async def list_templates():
    studio = get_document_studio()
    templates = []
    for record in studio.catalog.list_templates():
        item = record.to_public_dict()
        default = studio.catalog.get_default(record.manifest.kind, record.manifest.format)
        item["is_default"] = bool(default and default.manifest.id == record.manifest.id)
        templates.append(item)
    return {"templates": templates}


@router.get("/logos", dependencies=[Depends(deps.verify_admin_token)])
async def list_logos():
    logos = get_document_studio().logos.list_logos()
    return {"logos": logos, "active_id": next((item["id"] for item in logos if item["active"]), None)}


@router.post("/logos", dependencies=[Depends(deps.verify_admin_token)])
async def upload_logo(file: UploadFile = File(...), name: str = Query("", max_length=80)):
    try:
        payload = await file.read(5 * 1024 * 1024 + 1)
        return get_document_studio().logos.add(
            payload,
            filename=file.filename or "logo",
            name=name,
        )
    except Exception as exc:
        raise _fail(exc)


@router.put("/logos/{logo_id}/active", dependencies=[Depends(deps.verify_admin_token)])
async def activate_logo(logo_id: str):
    try:
        return {"logo": get_document_studio().logos.set_active(logo_id)}
    except Exception as exc:
        raise _fail(exc)


@router.delete("/logos/{logo_id}", dependencies=[Depends(deps.verify_admin_token)])
async def delete_logo(logo_id: str):
    try:
        get_document_studio().logos.delete(logo_id)
        return {"ok": True}
    except Exception as exc:
        raise _fail(exc)


@router.get("/logos/{logo_id}/content", dependencies=[Depends(deps.verify_admin_token)])
async def get_logo_content(logo_id: str):
    try:
        return FileResponse(get_document_studio().logos.content_path(logo_id), media_type="image/png")
    except Exception as exc:
        raise _fail(exc)


@router.get("/templates/{template_id}", dependencies=[Depends(deps.verify_admin_token)])
async def get_template(template_id: str):
    try:
        return studio_detail(template_id)
    except Exception as exc:
        raise _fail(exc)


def studio_detail(template_id: str) -> dict[str, Any]:
    return get_document_studio().template_detail(template_id)


@router.get("/template-imports", dependencies=[Depends(deps.verify_admin_token)])
async def list_template_imports():
    return {"drafts": get_document_studio().template_imports.list_drafts()}


@router.post("/template-imports", dependencies=[Depends(deps.verify_admin_token)])
async def create_template_import(
    file: UploadFile = File(...),
    name: str = Query("", max_length=120),
    kind: str = Query("", max_length=80),
    category: str = Query("custom", max_length=80),
):
    suffix = Path(file.filename or "template").suffix
    fd, tmp_name = tempfile.mkstemp(prefix="lumena-template-", suffix=suffix)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        size = 0
        with tmp.open("wb") as handle:
            while chunk := await file.read(64 * 1024):
                size += len(chunk)
                if size > 50 * 1024 * 1024:
                    raise HTTPException(413, "template source too large")
                handle.write(chunk)
        draft = get_document_studio().template_imports.create(
            tmp, filename=file.filename or "template", name=name, kind=kind, category=category,
        )
        return {"draft": draft.to_dict()}
    except HTTPException:
        raise
    except Exception as exc:
        raise _fail(exc)
    finally:
        tmp.unlink(missing_ok=True)


@router.get("/template-imports/{draft_id}", dependencies=[Depends(deps.verify_admin_token)])
async def get_template_import(draft_id: str):
    try:
        return {"draft": get_document_studio().template_imports.get(draft_id).to_dict()}
    except Exception as exc:
        raise _fail(exc)


@router.put("/template-imports/{draft_id}", dependencies=[Depends(deps.verify_admin_token)])
async def update_template_import(draft_id: str, payload: dict[str, Any] = Body(...)):
    try:
        draft = get_document_studio().template_imports.update(draft_id, payload)
        return {"draft": draft.to_dict()}
    except Exception as exc:
        raise _fail(exc)


@router.post("/template-imports/{draft_id}/preview", dependencies=[Depends(deps.verify_admin_token)])
async def preview_template_import(draft_id: str):
    try:
        return {"html": get_document_studio().template_imports.preview_html(draft_id)}
    except Exception as exc:
        raise _fail(exc)


@router.post("/template-imports/{draft_id}/publish", dependencies=[Depends(deps.verify_admin_token)])
async def publish_template_import(draft_id: str, payload: dict[str, Any] = Body(default={})):
    try:
        record = get_document_studio().template_imports.publish(
            draft_id, template_id=str(payload.get("template_id", "")),
        )
        return {"template": record.to_public_dict()}
    except Exception as exc:
        raise _fail(exc)


@router.delete("/template-imports/{draft_id}", dependencies=[Depends(deps.verify_admin_token)])
async def delete_template_import(draft_id: str):
    try:
        get_document_studio().template_imports.delete(draft_id)
        return {"ok": True}
    except Exception as exc:
        raise _fail(exc)


@router.post("/templates/{template_id}/clone", dependencies=[Depends(deps.verify_admin_token)])
async def clone_template(template_id: str, payload: dict[str, Any] = Body(...)):
    try:
        record = get_document_studio().catalog.clone_builtin(
            template_id, str(payload.get("id", "")), name=str(payload.get("name", "") or "") or None
        )
        return record.to_public_dict()
    except Exception as exc:
        raise _fail(exc)


@router.put("/templates/{template_id}", dependencies=[Depends(deps.verify_admin_token)])
async def save_template(template_id: str, payload: dict[str, Any] = Body(...)):
    try:
        catalog = get_document_studio().catalog
        current = catalog.get(template_id)
        if current.manifest.renderer == "html-jinja":
            record = catalog.save_custom(
                template_id,
                manifest_data=dict(payload.get("manifest") or {}),
                template_source=str(payload.get("source", "")),
                sample_data=dict(payload.get("sample_data") or {}),
            )
        else:
            record = catalog.update_custom_native(
                template_id,
                manifest_data=dict(payload.get("manifest") or {}),
                sample_data=dict(payload.get("sample_data") or {}),
            )
        return record.to_public_dict()
    except Exception as exc:
        raise _fail(exc)


@router.get("/templates/{template_id}/versions", dependencies=[Depends(deps.verify_admin_token)])
async def list_template_versions(template_id: str):
    try:
        return {"versions": get_document_studio().catalog.list_versions(template_id)}
    except Exception as exc:
        raise _fail(exc)


@router.post("/templates/{template_id}/restore/{version}", dependencies=[Depends(deps.verify_admin_token)])
async def restore_template(template_id: str, version: int):
    try:
        return get_document_studio().catalog.restore(template_id, version).to_public_dict()
    except Exception as exc:
        raise _fail(exc)


@router.put("/defaults/{kind}/{output_format}", dependencies=[Depends(deps.verify_admin_token)])
async def set_default(kind: str, output_format: str, payload: dict[str, Any] = Body(...)):
    try:
        get_document_studio().catalog.set_default(kind, output_format, payload.get("template_id"))
        return {"ok": True, "template_id": payload.get("template_id") or None}
    except Exception as exc:
        raise _fail(exc)


@router.post("/templates/{template_id}/preview", dependencies=[Depends(deps.verify_admin_token)])
async def create_preview(template_id: str, force: bool = False):
    try:
        result = await get_document_studio().previews.generate(template_id, force=force)
        result["thumbnail_url"] = f"/api/document-studio/previews/{result['content_hash']}/thumbnail.webp"
        result["pdf_url"] = f"/api/document-studio/previews/{result['content_hash']}/preview.pdf"
        return result
    except Exception as exc:
        raise _fail(exc)


@router.post("/templates/{template_id}/preview-draft", dependencies=[Depends(deps.verify_admin_token)])
async def preview_template_draft(template_id: str, payload: dict[str, Any] = Body(...)):
    try:
        studio = get_document_studio()
        record = studio.catalog.get(template_id)
        html = studio.renderer.render_html(
            record,
            str(payload.get("source", "")),
            dict(payload.get("sample_data") or {}),
            design=dict(payload.get("design") or record.manifest.design),
            logo_data_uri=studio.logos.active_data_uri(),
        )
        return {"html": html}
    except Exception as exc:
        raise _fail(exc)


@router.get("/previews/{content_hash}/{filename}", dependencies=[Depends(deps.verify_admin_token)])
async def get_preview_file(content_hash: str, filename: str):
    if filename not in {"thumbnail.webp", "preview.pdf", "preview.html"}:
        raise HTTPException(404, "preview file not found")
    try:
        path = resolve_within(get_document_studio().previews.cache_root, Path(content_hash) / filename)
    except Exception as exc:
        raise _fail(exc)
    if not path.is_file():
        raise HTTPException(404, "preview not generated")
    media = {".webp": "image/webp", ".pdf": "application/pdf", ".html": "text/html"}[path.suffix]
    return FileResponse(path, media_type=media)


@router.post("/generate", dependencies=[Depends(deps.verify_admin_token)])
async def generate_document(payload: dict[str, Any] = Body(...)):
    try:
        return await get_document_studio().generate(
            template_id=str(payload.get("template_id", "")),
            kind=str(payload.get("kind", "")),
            output_format=str(payload.get("format", "pdf")),
            data=dict(payload.get("data") or {}),
            filename=str(payload.get("filename", "")),
        )
    except Exception as exc:
        raise _fail(exc)


@router.get("/library", dependencies=[Depends(deps.verify_admin_token)])
async def list_library(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), format: str = ""):
    records = get_document_studio().library.list(limit=limit, offset=offset, format=format)
    return {"documents": [record.to_dict(include_content=False) for record in records]}


@router.get("/library/search", dependencies=[Depends(deps.verify_admin_token)])
async def search_library(
    q: str = "", formats: str = "", source: str = "", date_from: str = "",
    date_to: str = "", template_id: str = "", mission_id: str = "",
):
    records = get_document_studio().library.search(
        q, formats=[f for f in formats.split(",") if f], source=source,
        date_from=date_from, date_to=date_to, template_id=template_id, mission_id=mission_id,
    )
    return {"documents": [record.to_dict(include_content=False) for record in records]}


@router.get("/library/{document_id}", dependencies=[Depends(deps.verify_admin_token)])
async def get_library_record(document_id: str):
    record = get_document_studio().library.get(document_id)
    if record is None:
        raise HTTPException(404, "document not found")
    return record.to_dict()


@router.get("/library/{document_id}/download", dependencies=[Depends(deps.verify_admin_token)])
async def download_library_record(document_id: str):
    record = get_document_studio().library.get(document_id)
    if record is None or not Path(record.path).is_file():
        raise HTTPException(404, "document not found")
    return FileResponse(record.path, filename=record.filename, media_type=record.mime_type)


@router.get("/library/{document_id}/thumbnail", dependencies=[Depends(deps.verify_admin_token)])
async def get_library_thumbnail(document_id: str):
    try:
        path = await get_document_studio().document_previews.thumbnail(document_id)
    except Exception as exc:
        raise _fail(exc)
    if path is None:
        raise HTTPException(404, "preview not supported for this format")
    return FileResponse(path, media_type="image/webp")


@router.get("/library/{document_id}/history", dependencies=[Depends(deps.verify_admin_token)])
async def get_library_history(document_id: str):
    studio = get_document_studio()
    if studio.library.get(document_id) is None:
        raise HTTPException(404, "document not found")
    return {"transformations": studio.library.list_transformations(document_id)}


@router.post("/library/{document_id}/edit/preview", dependencies=[Depends(deps.verify_admin_token)])
async def preview_document_edit(document_id: str, payload: dict[str, Any] = Body(...)):
    try:
        return get_document_studio().edits.preview(document_id, list(payload.get("operations") or [])).to_dict()
    except Exception as exc:
        raise _fail(exc)


@router.post("/library/{document_id}/edit/apply", dependencies=[Depends(deps.verify_admin_token)])
async def apply_document_edit(document_id: str, payload: dict[str, Any] = Body(...)):
    try:
        return get_document_studio().edits.apply(document_id, list(payload.get("operations") or []))
    except Exception as exc:
        raise _fail(exc)


@router.post("/library/{document_id}/revise/preview", dependencies=[Depends(deps.verify_admin_token)])
async def preview_studio_revision(document_id: str, payload: dict[str, Any] = Body(...)):
    try:
        studio = get_document_studio()
        return studio.preview_revision(
            document_id,
            data=studio.parse_json_object(payload.get("data", {}), field="data"),
            replace_data=bool(payload.get("replace_data", False)),
        )
    except Exception as exc:
        raise _fail(exc)


@router.post("/library/{document_id}/revise", dependencies=[Depends(deps.verify_admin_token)])
async def apply_studio_revision(document_id: str, payload: dict[str, Any] = Body(...)):
    try:
        studio = get_document_studio()
        return await studio.revise(
            document_id,
            data=studio.parse_json_object(payload.get("data", {}), field="data"),
            replace_data=bool(payload.get("replace_data", False)),
            output_format=str(payload.get("output_format", "")),
            filename=str(payload.get("filename", "")),
        )
    except Exception as exc:
        raise _fail(exc)


@router.get("/conversions")
async def conversion_capabilities():
    return {"conversions": get_document_studio().conversions.capabilities()}


@router.post("/library/{document_id}/convert", dependencies=[Depends(deps.verify_admin_token)])
async def convert_document(document_id: str, payload: dict[str, Any] = Body(...)):
    try:
        return get_document_studio().conversions.convert(document_id, str(payload.get("format", "")))
    except Exception as exc:
        raise _fail(exc)


@router.get("/delivery/capabilities")
async def delivery_capabilities():
    return {"connectors": get_document_studio().delivery.capabilities()}


@router.post("/library/{document_id}/export", dependencies=[Depends(deps.verify_admin_token)])
async def export_document(document_id: str, payload: dict[str, Any] = Body(default={})):
    try:
        return get_document_studio().delivery.export_local(document_id, str(payload.get("filename", "")))
    except Exception as exc:
        raise _fail(exc)


@router.post("/import", dependencies=[Depends(deps.verify_admin_token)])
async def import_upload(file: UploadFile = File(...)):
    suffix = Path(file.filename or "document").suffix
    fd, tmp_name = tempfile.mkstemp(prefix="lumena-doc-", suffix=suffix)
    os.close(fd)
    Path(tmp_name).unlink(missing_ok=True)
    tmp = Path(tmp_name)
    try:
        size = 0
        with tmp.open("wb") as handle:
            while chunk := await file.read(64 * 1024):
                size += len(chunk)
                if size > 50 * 1024 * 1024:
                    raise HTTPException(413, "document too large")
                handle.write(chunk)
        record, duplicate = get_document_studio().importer.import_file(
            tmp, source_kind="studio_upload", source_uri=file.filename or "upload"
        )
        return {"record": record.to_dict(include_content=False), "duplicate": duplicate}
    except HTTPException:
        raise
    except Exception as exc:
        raise _fail(exc)
    finally:
        tmp.unlink(missing_ok=True)


@router.post("/library/index-workspace", dependencies=[Depends(deps.verify_admin_token)])
async def index_workspace(payload: dict[str, Any] = Body(default={})):
    try:
        return get_document_studio().importer.import_directory(
            WORKSPACE_DIR,
            recursive=bool(payload.get("recursive", True)),
            max_files=int(payload.get("max_files", 500)),
            source_kind="workspace_scan",
        )
    except Exception as exc:
        raise _fail(exc)


@router.post("/web/search", dependencies=[Depends(deps.verify_admin_token)])
async def search_web_documents(payload: dict[str, Any] = Body(...)):
    try:
        return await get_document_studio().web_search.search(
            str(payload.get("query", "")), formats=payload.get("formats"), count=int(payload.get("count", 12))
        )
    except Exception as exc:
        raise _fail(exc)


@router.post("/web/inspect", dependencies=[Depends(deps.verify_admin_token)])
async def inspect_web_document(payload: dict[str, Any] = Body(...)):
    try:
        info = await get_document_studio().downloader.inspect(str(payload.get("url", "")))
        return info.__dict__
    except Exception as exc:
        raise _fail(exc)


@router.post("/web/download", dependencies=[Depends(deps.verify_admin_token)])
async def download_web_document(payload: dict[str, Any] = Body(...)):
    try:
        record, duplicate = await get_document_studio().downloader.download(str(payload.get("url", "")))
        return {"record": record.to_dict(include_content=False), "duplicate": duplicate}
    except Exception as exc:
        raise _fail(exc)
