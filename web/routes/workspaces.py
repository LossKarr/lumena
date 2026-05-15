"""Workspace management routes — list, detail, file content, delete."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query

from web.routes import deps
from src.utils.paths import WORKSPACE_DIR

router = APIRouter()


def _find_workspace_dir(slug: str) -> Path | None:
    if not WORKSPACE_DIR.exists():
        return None
    for date_dir in sorted(WORKSPACE_DIR.iterdir(), reverse=True):
        candidate = date_dir / slug
        if candidate.is_dir():
            return candidate
    return None


def _serving_info(slug: str) -> tuple[bool, str | None]:
    try:
        from web.routes.advanced import _SERVING_WORKSPACES
        if slug in _SERVING_WORKSPACES:
            return True, _SERVING_WORKSPACES[slug]["url"]
    except Exception:
        pass
    return False, None


def _detect_tech(ws_dir: Path, file_names: set[str]) -> List[str]:
    tech: List[str] = []
    if (ws_dir / "package.json").exists():
        tech.append("Node.js")
    if (ws_dir / "requirements.txt").exists() or any(f.endswith(".py") for f in file_names):
        tech.append("Python")
    if any(f.endswith((".ts", ".tsx")) for f in file_names):
        tech.append("TypeScript")
    if (ws_dir / "Cargo.toml").exists() or any(f.endswith(".rs") for f in file_names):
        tech.append("Rust")
    if (ws_dir / "go.mod").exists() or any(f.endswith(".go") for f in file_names):
        tech.append("Go")
    if any(f.lower() in ("dockerfile", ".dockerignore") for f in file_names):
        tech.append("Docker")
    if (ws_dir / "index.html").exists():
        tech.append("HTML")
    return tech


def _ws_summary(ws_dir: Path, date_str: str) -> Dict[str, Any]:
    files = [f for f in ws_dir.rglob("*") if f.is_file()]
    total_size = sum(f.stat().st_size for f in files)
    file_names = {f.name for f in files}
    is_serving, serve_url = _serving_info(ws_dir.name)
    return {
        "slug": ws_dir.name,
        "date": date_str,
        "path": str(ws_dir),
        "files_count": len(files),
        "total_size_kb": round(total_size / 1024, 1),
        "has_index_html": (ws_dir / "index.html").exists(),
        "has_package_json": (ws_dir / "package.json").exists(),
        "tech_stack": _detect_tech(ws_dir, file_names),
        "is_serving": is_serving,
        "serve_url": serve_url,
    }


@router.get("/api/workspaces")
async def list_workspaces(_auth=Depends(deps.verify_admin_token)):
    """Liste tous les workspaces CodeAgent (triés du plus récent au plus ancien)."""
    result: List[Dict[str, Any]] = []
    if not WORKSPACE_DIR.exists():
        return {"workspaces": result}
    for date_dir in sorted(WORKSPACE_DIR.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        for ws_dir in sorted(date_dir.iterdir(), reverse=True):
            if not ws_dir.is_dir():
                continue
            try:
                summary = _ws_summary(ws_dir, date_dir.name)
                if summary["files_count"] > 0:  # ignorer workspaces vides
                    result.append(summary)
            except Exception:
                pass
    return {"workspaces": result}


@router.get("/api/workspaces/{slug}")
async def get_workspace(slug: str, _auth=Depends(deps.verify_admin_token)):
    """Détail d'un workspace + arbre de fichiers (max 100 entrées)."""
    ws_dir = _find_workspace_dir(slug)
    if ws_dir is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{slug}' introuvable")

    all_files = sorted(f for f in ws_dir.rglob("*") if f.is_file())
    file_tree = []
    for f in all_files[:100]:
        rel = str(f.relative_to(ws_dir)).replace("\\", "/")
        file_tree.append({"path": rel, "size_bytes": f.stat().st_size})

    is_serving, serve_url = _serving_info(slug)
    return {
        "slug": slug,
        "path": str(ws_dir),
        "has_index_html": (ws_dir / "index.html").exists(),
        "has_package_json": (ws_dir / "package.json").exists(),
        "is_serving": is_serving,
        "serve_url": serve_url,
        "files": file_tree,
    }


@router.get("/api/workspaces/{slug}/file")
async def get_workspace_file(
    slug: str,
    path: str = Query(..., description="Chemin relatif au workspace"),
    _auth=Depends(deps.verify_admin_token),
):
    """Contenu textuel d'un fichier dans le workspace."""
    ws_dir = _find_workspace_dir(slug)
    if ws_dir is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{slug}' introuvable")

    # Path traversal guard
    try:
        target = (ws_dir / path).resolve()
        target.relative_to(ws_dir.resolve())
    except (ValueError, RuntimeError):
        raise HTTPException(status_code=400, detail="Chemin de fichier invalide")

    if not target.is_file():
        raise HTTPException(status_code=404, detail="Fichier introuvable")

    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"path": path, "content": content, "size_bytes": target.stat().st_size}


@router.delete("/api/workspaces/{slug}")
async def delete_workspace(slug: str, _auth=Depends(deps.verify_admin_token)):
    """Supprime un workspace (fichiers + stop serve si actif)."""
    ws_dir = _find_workspace_dir(slug)
    if ws_dir is None:
        raise HTTPException(status_code=404, detail=f"Workspace '{slug}' introuvable")

    # Stop serving if active
    try:
        from web.routes.advanced import _SERVING_WORKSPACES
        if slug in _SERVING_WORKSPACES:
            info = _SERVING_WORKSPACES.pop(slug)
            try:
                info["process"].terminate()
            except Exception:
                pass
    except Exception:
        pass

    shutil.rmtree(ws_dir, ignore_errors=True)
    return {"success": True, "slug": slug}
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
