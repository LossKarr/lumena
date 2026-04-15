"""Routes API IONOS — gestion des sites et déploiements SFTP."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

from web.routes.deps import verify_admin_token

router = APIRouter()

# ── Pydantic models ──────────────────────────────────────────────────────


class IonosSiteCreate(BaseModel):
    domain: str
    host: str
    user: str
    password: str
    port: int = 22
    root: str = "/"
    label: str = ""


class IonosDeployRequest(BaseModel):
    project_dir: str
    dry_run: bool = False


# ── Lazy deployer ────────────────────────────────────────────────────────

_deployer = None


def _get_deployer():
    global _deployer
    if _deployer is None:
        from src.services.ionos_deployer import IonosDeployer
        _deployer = IonosDeployer()
    return _deployer


# ── Routes ───────────────────────────────────────────────────────────────


@router.get("/api/ionos/sites")
async def list_ionos_sites():
    """Liste les sites IONOS configurés (sans credentials)."""
    deployer = _get_deployer()
    return JSONResponse({"sites": deployer.list_sites()})


@router.get("/api/ionos/sites/{domain}/files", dependencies=[Depends(verify_admin_token)])
async def list_ionos_files(domain: str, path: str = "/"):
    """Liste les fichiers distants d'un site IONOS."""
    deployer = _get_deployer()
    try:
        files = await deployer.list_remote(domain, path)
        return JSONResponse({
            "domain": domain,
            "path": path,
            "files": [
                {
                    "path": f.path,
                    "size": f.size,
                    "is_dir": f.is_dir,
                    "modified": f.modified,
                }
                for f in files
            ],
        })
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur SFTP: {e}")


@router.post("/api/ionos/sites", dependencies=[Depends(verify_admin_token)])
async def add_ionos_site(body: IonosSiteCreate):
    """Ajouter ou modifier un site IONOS."""
    deployer = _get_deployer()
    try:
        result = deployer.add_site(
            domain=body.domain,
            host=body.host,
            user=body.user,
            password=body.password,
            port=body.port,
            root=body.root,
            label=body.label,
        )
        return JSONResponse(result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur connexion SFTP: {e}")


@router.post("/api/ionos/deploy/{domain}", dependencies=[Depends(verify_admin_token)])
async def deploy_to_ionos(domain: str, body: IonosDeployRequest):
    """Déployer un projet sur un site IONOS via SFTP."""
    deployer = _get_deployer()
    try:
        result = await deployer.deploy(
            domain, Path(body.project_dir), dry_run=body.dry_run
        )
        return JSONResponse({
            "success": result.success,
            "uploaded": result.uploaded,
            "skipped": result.skipped,
            "errors": result.errors,
            "total_bytes": result.total_bytes,
            "duration_sec": result.duration_sec,
            "dry_run": result.dry_run,
        })
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur déploiement: {e}")


@router.delete("/api/ionos/sites/{domain}", dependencies=[Depends(verify_admin_token)])
async def remove_ionos_site(domain: str):
    """Supprimer un site IONOS de la configuration."""
    deployer = _get_deployer()
    try:
        deployer.remove_site(domain)
        return JSONResponse({"status": "ok", "removed": domain})
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
