"""
image_gen.py — Routes API de génération d'images.

Routes:
  POST /api/images/generate — Générer une image
  GET  /api/images/models   — Lister les modèles disponibles
  GET  /api/files/workspace/{file_path:path} — Servir un fichier workspace (images, docs)
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from src.utils.paths import WORKSPACE_DIR
from web.routes import deps

router = APIRouter(tags=["images"])


# ── BLOCKER A: File serving route with path traversal guard ───────────────

_ALLOWED_EXTENSIONS = frozenset({
    "png", "jpg", "jpeg", "gif", "webp", "svg", "ico",
    "pdf", "docx", "xlsx", "pptx",
    "html", "css", "js", "json", "txt", "md", "csv",
    "mp4", "mp3", "wav", "ogg",
})


@router.get("/api/files/workspace/{file_path:path}")
async def serve_workspace_file(file_path: str):
    """Sert un fichier du workspace avec garde path-traversal."""
    # Normaliser et résoudre le chemin
    requested = (WORKSPACE_DIR / file_path).resolve()

    # Path traversal guard: le chemin résolu DOIT être sous WORKSPACE_DIR
    workspace_resolved = WORKSPACE_DIR.resolve()
    if not str(requested).startswith(str(workspace_resolved)):
        raise HTTPException(status_code=403, detail="Accès refusé (path traversal)")

    if not requested.exists() or not requested.is_file():
        raise HTTPException(status_code=404, detail="Fichier introuvable")

    # Extension guard
    ext = requested.suffix.lstrip(".").lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=403, detail=f"Extension non autorisée: .{ext}")

    # MIME type mapping
    mime_map = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
        "ico": "image/x-icon",
        "pdf": "application/pdf",
        "html": "text/html", "css": "text/css", "js": "application/javascript",
        "json": "application/json", "txt": "text/plain", "md": "text/plain",
        "csv": "text/csv",
    }
    media_type = mime_map.get(ext)

    return FileResponse(str(requested), media_type=media_type)


# ── P4: Image generation API routes ──────────────────────────────────────

@router.post("/api/images/generate", dependencies=[Depends(deps.verify_admin_token)])
async def api_generate_image(request: Request):
    """Génère une image depuis un prompt."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON invalide")

    prompt = body.get("prompt", "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Le champ 'prompt' est requis")

    from src.services.image_gen import ImageGenService, ImageGenError

    svc = ImageGenService.get_instance()
    try:
        result = await svc.generate(
            prompt,
            model=body.get("model", "auto"),
            size=body.get("size", "1024x1024"),
            quality=body.get("quality", "hd"),
            style=body.get("style", ""),
            template=body.get("template", ""),
        )
        from src.services.image_gen import _slugify
        slug = _slugify(prompt)
        filepath = svc.save_to_workspace(result, slug)

        return JSONResponse({
            "status": "ok",
            "file": str(filepath),
            "url": f"/api/files/workspace/images/{filepath.relative_to(WORKSPACE_DIR / 'images')}",
            "model": result.model,
            "provider": result.provider,
            "size": f"{result.width}x{result.height}",
            "format": result.format,
            "cost_usd": result.cost_estimate,
            "generation_time_ms": result.generation_time_ms,
        })
    except ImageGenError as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=422)
    except Exception as e:
        return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)


@router.get("/api/images/models", dependencies=[Depends(deps.verify_admin_token)])
async def api_list_image_models():
    """Liste tous les modèles de génération d'images."""
    from src.services.image_gen import ImageGenService
    svc = ImageGenService.get_instance()
    models = svc.get_available_models()
    available = [m for m in models if m["available"]]
    return JSONResponse({
        "total": len(models),
        "available": len(available),
        "models": models,
    })
