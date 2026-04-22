"""Lecture/écriture des fichiers texte de configuration Lumena (.lumena_rules, README.md, HEARTBEAT.md)."""
from __future__ import annotations
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from web.routes.deps import verify_admin_token

from src.utils.paths import ROOT_DIR

_PROJECT_ROOT = ROOT_DIR

router = APIRouter()

# Fichiers autorisés (relatif à la racine du projet)
_ALLOWED_FILES = {
    "lumena_rules": ".lumena_rules",
    "readme":       "README.md",
    "heartbeat":    "HEARTBEAT.md",
}


def _resolve(key: str) -> Path:
    if key not in _ALLOWED_FILES:
        raise HTTPException(status_code=400, detail=f"Fichier inconnu : {key}")
    return _PROJECT_ROOT / _ALLOWED_FILES[key]


@router.get("/api/docs/{key}")
async def get_doc(key: str, _auth=Depends(verify_admin_token)):
    """Retourne le contenu brut d'un fichier."""
    path = _resolve(key)
    if not path.exists():
        return {"success": True, "content": "", "exists": False, "key": key, "filename": _ALLOWED_FILES[key]}
    content = path.read_text(encoding="utf-8", errors="replace")
    return {"success": True, "content": content, "exists": True, "key": key, "filename": _ALLOWED_FILES[key]}


@router.put("/api/docs/{key}", dependencies=[Depends(verify_admin_token)])
async def save_doc(key: str, request: Request):
    """Sauvegarde le contenu d'un fichier."""
    path = _resolve(key)
    body = await request.json()
    content = body.get("content", "")
    if not isinstance(content, str):
        raise HTTPException(status_code=400, detail="content doit être une chaîne")
    path.write_text(content, encoding="utf-8")
    return {"success": True, "key": key, "bytes": len(content.encode("utf-8"))}


@router.get("/api/docs")
async def list_docs(_auth=Depends(verify_admin_token)):
    """Liste les fichiers disponibles avec leur taille."""
    result = []
    for key, rel in _ALLOWED_FILES.items():
        path = _PROJECT_ROOT / rel
        result.append({
            "key": key,
            "filename": rel,
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
        })
    return {"success": True, "files": result}
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
