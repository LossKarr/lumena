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


class IonosDbPreview(BaseModel):
    columns: Optional[list] = None        # liste de colonnes (optionnel)
    where: Optional[dict] = None          # {col: valeur} égalité simple (optionnel)
    limit: int = 20                       # borné 1..100 côté route


class IonosWriteConfig(BaseModel):
    enabled: bool = False                 # OFF par défaut
    tables: list = []                     # allowlist des tables writables


class IonosDbWrite(BaseModel):
    op: str                               # "insert" | "update" (DELETE interdit)
    values: dict                          # {col: valeur}
    where: Optional[dict] = None          # {col: valeur} — obligatoire pour UPDATE
    confirm: bool = False                 # confirmation explicite obligatoire


class IonosSandboxConfig(BaseModel):
    enabled: bool = False                 # OFF par défaut


class IonosSandboxTable(BaseModel):
    name: str                             # doit commencer par lumena_sandbox_
    columns: list = []                    # [{name, type, length?, nullable?, default?}]
    confirm: bool = False                 # confirmation explicite obligatoire


class IonosDeleteConfig(BaseModel):
    enabled: bool = False                 # OFF par défaut
    tables: list = []                     # allowlist SÉPARÉE des tables supprimables


class IonosReactWriteConfig(BaseModel):
    enabled: bool = False                 # OFF par défaut (autorise la proposition ReAct)


class IonosReactDeleteConfig(BaseModel):
    enabled: bool = False                 # OFF par défaut (autorise la proposition DELETE ReAct)


class IonosSandboxDropConfig(BaseModel):
    enabled: bool = False                 # OFF par défaut (autorise le DROP de tables sandbox vides)


class IonosSandboxClearConfig(BaseModel):
    enabled: bool = False                 # OFF par défaut (autorise le vidage de tables sandbox)


class IonosApproveAction(BaseModel):
    confirm: bool = False                 # confirmation humaine obligatoire


class IonosDbDelete(BaseModel):
    where: dict                           # {col: valeur} — OBLIGATOIRE, non vide
    confirm: bool = False                 # confirmation explicite obligatoire
    confirm_table: str = ""               # doit == nom de table (retapé en UI)


class IonosRestoreConfig(BaseModel):
    enabled: bool = False                 # OFF par défaut


class IonosRestoreRequest(BaseModel):
    confirm: bool = False                 # confirmation explicite obligatoire


class IonosDatabaseConfig(BaseModel):
    host: str
    name: str
    user: str
    password: str = ""  # vide en modification = conserver le secret existant
    port: int = 3306
    label: str = ""
    description: str = ""
    engine: str = "mariadb"
    version: str = ""


# ── Lazy deployer ────────────────────────────────────────────────────────

_deployer = None


def _get_deployer():
    # Singleton PARTAGÉ avec les handlers ReAct (src.reasoning.handlers.ionos) :
    # une seule instance par process → write_enabled/allowlist/_sites cohérents entre
    # propose_write (handler) et approve_pending_action (route). Injection test prioritaire.
    global _deployer
    if _deployer is None:
        from src.services.ionos_deployer import get_shared_deployer
        _deployer = get_shared_deployer()
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


@router.post(
    "/api/ionos/sites/{domain}/database/test",
    dependencies=[Depends(verify_admin_token)],
)
async def test_ionos_database(domain: str):
    """Tester la connexion à la BDD d'un site IONOS (PING, lecture seule).

    Ne renvoie jamais de mot de passe : seulement un statut non sensible.
    """
    deployer = _get_deployer()
    try:
        result = deployer.test_database_connection(domain)
        # `message` = diagnostic clair pour l'UI. L'erreur technique brute reste
        # côté serveur dans last_check.error et n'est PAS exposée ici.
        return JSONResponse({
            "domain": domain,
            "configured": result.get("configured", False),
            "ok": result.get("ok", False),
            "latency_ms": result.get("latency_ms"),
            "checked_at": result.get("checked_at"),
            "degraded": result.get("degraded", False),
            "via": result.get("via", "direct"),
            "message": result.get("message", ""),
        })
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur test BDD: {e}")


@router.get(
    "/api/ionos/sites/{domain}/database",
    dependencies=[Depends(verify_admin_token)],
)
async def get_ionos_database(domain: str):
    """Config BDD non sensible d'un site (pour l'UI). Jamais de mot de passe."""
    deployer = _get_deployer()
    try:
        cfg = deployer.get_site_database(domain)  # include_secret=False par défaut
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if cfg is None:
        return JSONResponse({"domain": domain, "configured": False})
    return JSONResponse({
        "domain": domain,
        "configured": True,
        "label": cfg.get("label", ""),
        "description": cfg.get("description", ""),
        "host": cfg.get("host", ""),
        "port": cfg.get("port", 3306),
        "name": cfg.get("name", ""),
        "user": cfg.get("user", ""),
        "engine": cfg.get("engine", ""),
        "version": cfg.get("version", ""),
        "last_check": cfg.get("last_check"),
    })


@router.post(
    "/api/ionos/sites/{domain}/database",
    dependencies=[Depends(verify_admin_token)],
)
async def set_ionos_database(domain: str, body: IonosDatabaseConfig):
    """Associer/modifier la BDD d'un site IONOS.

    password vide en modification = conserve le secret existant.
    Réponse sans password/password_encrypted.
    """
    deployer = _get_deployer()
    try:
        deployer.set_site_database(
            domain,
            host=body.host,
            name=body.name,
            user=body.user,
            password=body.password,
            port=body.port,
            label=body.label,
            description=body.description,
            engine=body.engine,
            version=body.version,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    cfg = deployer.get_site_database(domain)  # non sensible
    return JSONResponse({
        "status": "ok",
        "domain": domain,
        "configured": True,
        "host": cfg.get("host", ""),
        "name": cfg.get("name", ""),
        "user": cfg.get("user", ""),
        "engine": cfg.get("engine", ""),
    })


# ── Bridge BDD sécurisé : statut / installation / suppression (admin) ────

@router.get(
    "/api/ionos/sites/{domain}/database/bridge",
    dependencies=[Depends(verify_admin_token)],
)
async def get_ionos_bridge(domain: str):
    """Statut du bridge BDD (non sensible : installed/version/last_check)."""
    deployer = _get_deployer()
    try:
        return JSONResponse(await deployer.get_database_bridge_status(domain))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/api/ionos/sites/{domain}/database/bridge",
    dependencies=[Depends(verify_admin_token)],
)
async def install_ionos_bridge(domain: str):
    """Installer/réinstaller le bridge BDD sécurisé via SFTP. Aucun secret renvoyé."""
    deployer = _get_deployer()
    try:
        r = await deployer.install_database_bridge(domain)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(r)


@router.delete(
    "/api/ionos/sites/{domain}/database/bridge",
    dependencies=[Depends(verify_admin_token)],
)
async def remove_ionos_bridge(domain: str):
    """Supprimer le bridge BDD (fichiers + config). Laisse SFTP et config BDD intacts."""
    deployer = _get_deployer()
    try:
        r = await deployer.remove_database_bridge(domain)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(r)


@router.delete(
    "/api/ionos/sites/{domain}/database",
    dependencies=[Depends(verify_admin_token)],
)
async def clear_ionos_database(domain: str):
    """Retirer la BDD d'un site IONOS (laisse le site SFTP intact)."""
    deployer = _get_deployer()
    try:
        deployer.clear_site_database(domain)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse({"status": "ok", "domain": domain, "configured": False})


# ── Lecture BDD read-only via bridge (Étape 3E) ──────────────────────────

@router.get(
    "/api/ionos/sites/{domain}/database/tables",
    dependencies=[Depends(verify_admin_token)],
)
async def ionos_db_tables(domain: str):
    """Lister les tables (read-only). Aucun secret renvoyé."""
    deployer = _get_deployer()
    try:
        r = deployer.db_list_tables(domain)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(r)


@router.get(
    "/api/ionos/sites/{domain}/database/tables/{table}/schema",
    dependencies=[Depends(verify_admin_token)],
)
async def ionos_db_schema(domain: str, table: str):
    """Schéma d'une table (read-only). Aucun secret renvoyé."""
    deployer = _get_deployer()
    try:
        r = deployer.db_describe_table(domain, table)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(r)


@router.post(
    "/api/ionos/sites/{domain}/database/tables/{table}/preview",
    dependencies=[Depends(verify_admin_token)],
)
async def ionos_db_preview(domain: str, table: str, body: IonosDbPreview):
    """Aperçu read-only borné (SELECT structuré). limit défaut 20, max 100."""
    deployer = _get_deployer()
    limit = max(1, min(int(body.limit or 20), 100))   # borne couche route
    try:
        r = deployer.db_select(
            domain, table, columns=body.columns, where=body.where, limit=limit,
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(r)


# ── Écriture contrôlée (Étape 4.1 : config + INSERT/UPDATE, UI-only) ─────

@router.get(
    "/api/ionos/sites/{domain}/database/write-config",
    dependencies=[Depends(verify_admin_token)],
)
async def get_ionos_write_config(domain: str):
    """Config écriture non sensible : {enabled, tables}."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.get_site_write_config(domain))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/api/ionos/sites/{domain}/database/write-config",
    dependencies=[Depends(verify_admin_token)],
)
async def set_ionos_write_config(domain: str, body: IonosWriteConfig):
    """Activer/désactiver l'écriture + fixer l'allowlist des tables."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.set_site_write_config(domain, body.enabled, body.tables))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/api/ionos/sites/{domain}/database/tables/{table}/write",
    dependencies=[Depends(verify_admin_token)],
)
async def ionos_db_write(domain: str, table: str, body: IonosDbWrite):
    """Écriture INSERT/UPDATE contrôlée (confirm obligatoire). Aucun secret/snapshot."""
    deployer = _get_deployer()
    try:
        r = deployer.db_write(
            domain, body.op, table, body.values,
            where=body.where, confirm=body.confirm, source="ui",
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(r)


# ── CREATE TABLE sandbox contrôlé (Étape 4.2) ───────────────────────────

@router.get(
    "/api/ionos/sites/{domain}/database/sandbox-config",
    dependencies=[Depends(verify_admin_token)],
)
async def get_ionos_sandbox_config(domain: str):
    """Config sandbox : {enabled}."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.get_site_sandbox_config(domain))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/api/ionos/sites/{domain}/database/sandbox-config",
    dependencies=[Depends(verify_admin_token)],
)
async def set_ionos_sandbox_config(domain: str, body: IonosSandboxConfig):
    """Activer/désactiver la création de tables sandbox."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.set_site_sandbox_config(domain, body.enabled))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/api/ionos/sites/{domain}/database/sandbox-tables",
    dependencies=[Depends(verify_admin_token)],
)
async def create_ionos_sandbox_table(domain: str, body: IonosSandboxTable):
    """Créer une table sandbox (préfixe lumena_sandbox_, confirm obligatoire)."""
    deployer = _get_deployer()
    try:
        r = deployer.db_create_sandbox_table(domain, body.name, body.columns,
                                             confirm=body.confirm, source="ui")
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(r)


# ── DELETE contrôlé (Étape 4.4) ──────────────────────────────────────────
# Flag + allowlist DÉDIÉS (indépendants du write). WHERE obligatoire ;
# confirm + confirm_table exact ; snapshot obligatoire avant suppression.

@router.get(
    "/api/ionos/sites/{domain}/database/delete-config",
    dependencies=[Depends(verify_admin_token)],
)
async def get_ionos_delete_config(domain: str):
    """Config delete non sensible : {enabled, tables}. OFF + vide par défaut."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.get_site_delete_config(domain))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/api/ionos/sites/{domain}/database/delete-config",
    dependencies=[Depends(verify_admin_token)],
)
async def set_ionos_delete_config(domain: str, body: IonosDeleteConfig):
    """Activer/désactiver le DELETE + fixer l'allowlist DÉDIÉE des tables."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.set_site_delete_config(domain, body.enabled, body.tables))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/api/ionos/sites/{domain}/database/tables/{table}/delete",
    dependencies=[Depends(verify_admin_token)],
)
async def ionos_db_delete(domain: str, table: str, body: IonosDbDelete):
    """Suppression contrôlée (WHERE + confirm + confirm_table). Aucun secret/snapshot/valeur."""
    deployer = _get_deployer()
    try:
        r = deployer.db_delete(
            domain, table, where=body.where, confirm=body.confirm,
            confirm_table=body.confirm_table, source="ui",
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(r)


# ── Propositions ReAct INSERT/UPDATE (Étape 4.5A) ────────────────────────
# ReAct PROPOSE, l'humain EXÉCUTE. L'exécution se fait via approve (confirm humain),
# jamais par le modèle. Métadonnées seules exposées ; aucune valeur en clair.

@router.get(
    "/api/ionos/sites/{domain}/database/react-config",
    dependencies=[Depends(verify_admin_token)],
)
async def get_ionos_react_config(domain: str):
    """Config propositions ReAct : {enabled}. OFF par défaut."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.get_site_react_write_config(domain))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/api/ionos/sites/{domain}/database/react-config",
    dependencies=[Depends(verify_admin_token)],
)
async def set_ionos_react_config(domain: str, body: IonosReactWriteConfig):
    """Activer/désactiver la création de propositions ReAct write."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.set_site_react_write_config(domain, body.enabled))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/api/ionos/sites/{domain}/database/react-delete-config",
    dependencies=[Depends(verify_admin_token)],
)
async def get_ionos_react_delete_config(domain: str):
    """Config propositions DELETE ReAct (4.5B) : {enabled}. OFF par défaut."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.get_site_react_delete_config(domain))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/api/ionos/sites/{domain}/database/react-delete-config",
    dependencies=[Depends(verify_admin_token)],
)
async def set_ionos_react_delete_config(domain: str, body: IonosReactDeleteConfig):
    """Activer/désactiver la création de propositions DELETE ReAct (cumulatif kill-switch global)."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.set_site_react_delete_config(domain, body.enabled))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/api/ionos/sites/{domain}/database/sandbox-drop-config",
    dependencies=[Depends(verify_admin_token)],
)
async def get_ionos_sandbox_drop_config(domain: str):
    """Config DROP sandbox (4.6) : {enabled}. OFF par défaut."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.get_site_sandbox_drop_config(domain))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/api/ionos/sites/{domain}/database/sandbox-drop-config",
    dependencies=[Depends(verify_admin_token)],
)
async def set_ionos_sandbox_drop_config(domain: str, body: IonosSandboxDropConfig):
    """Activer/désactiver le DROP de tables sandbox vides (cumulatif kill-switch global)."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.set_site_sandbox_drop_config(domain, body.enabled))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/api/ionos/sites/{domain}/database/sandbox-clear-config",
    dependencies=[Depends(verify_admin_token)],
)
async def get_ionos_sandbox_clear_config(domain: str):
    """Config CLEAR (vidage) sandbox (4.7) : {enabled}. OFF par défaut."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.get_site_sandbox_clear_config(domain))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/api/ionos/sites/{domain}/database/sandbox-clear-config",
    dependencies=[Depends(verify_admin_token)],
)
async def set_ionos_sandbox_clear_config(domain: str, body: IonosSandboxClearConfig):
    """Activer/désactiver le vidage de tables sandbox."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.set_site_sandbox_clear_config(domain, body.enabled))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/api/ionos/sites/{domain}/database/pending-actions",
    dependencies=[Depends(verify_admin_token)],
)
async def list_ionos_pending_actions(domain: str):
    """Lister les propositions ReAct en attente (métadonnées NON sensibles)."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.list_pending_actions(domain))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/api/ionos/sites/{domain}/database/pending-actions/{proposal_id}/approve",
    dependencies=[Depends(verify_admin_token)],
)
async def approve_ionos_pending_action(domain: str, proposal_id: str, body: IonosApproveAction):
    """Approuver et exécuter une proposition (confirm humain → db_write source=react_approved)."""
    deployer = _get_deployer()
    try:
        r = deployer.approve_pending_action(domain, proposal_id, confirm=body.confirm, source="ui")
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(r)


@router.post(
    "/api/ionos/sites/{domain}/database/pending-actions/{proposal_id}/reject",
    dependencies=[Depends(verify_admin_token)],
)
async def reject_ionos_pending_action(domain: str, proposal_id: str):
    """Rejeter une proposition (aucune exécution)."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.reject_pending_action(domain, proposal_id, source="ui"))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Snapshot chiffré / rollback (Étape 4.3) ──────────────────────────────
# Aucune valeur en clair exposée : seules les métadonnées (noms de colonnes,
# compteurs, horodatages) transitent par l'API. restore_enabled OFF par défaut.

@router.get(
    "/api/ionos/sites/{domain}/database/restore-config",
    dependencies=[Depends(verify_admin_token)],
)
async def get_ionos_restore_config(domain: str):
    """Config restauration : {enabled}. OFF par défaut."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.get_site_restore_config(domain))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/api/ionos/sites/{domain}/database/restore-config",
    dependencies=[Depends(verify_admin_token)],
)
async def set_ionos_restore_config(domain: str, body: IonosRestoreConfig):
    """Activer/désactiver la restauration de snapshots pour le site."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.set_site_restore_config(domain, body.enabled))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get(
    "/api/ionos/sites/{domain}/database/snapshots",
    dependencies=[Depends(verify_admin_token)],
)
async def list_ionos_snapshots(domain: str):
    """Lister les snapshots (métadonnées NON sensibles : jamais de valeurs)."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.list_snapshots(domain))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/api/ionos/sites/{domain}/database/snapshots/{snapshot_id}/restore",
    dependencies=[Depends(verify_admin_token)],
)
async def restore_ionos_snapshot(domain: str, snapshot_id: str, body: IonosRestoreRequest):
    """Restaurer un snapshot (restore_enabled + confirm + guards write 4.1)."""
    deployer = _get_deployer()
    try:
        r = deployer.restore_snapshot(domain, snapshot_id, confirm=body.confirm, source="ui")
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return JSONResponse(r)


@router.delete(
    "/api/ionos/sites/{domain}/database/snapshots/{snapshot_id}",
    dependencies=[Depends(verify_admin_token)],
)
async def delete_ionos_snapshot(domain: str, snapshot_id: str):
    """Supprimer un snapshot (fichier chiffré + entrée d'index)."""
    deployer = _get_deployer()
    try:
        return JSONResponse(deployer.delete_snapshot(domain, snapshot_id))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.delete("/api/ionos/sites/{domain}", dependencies=[Depends(verify_admin_token)])
async def remove_ionos_site(domain: str):
    """Supprimer un site IONOS de la configuration."""
    deployer = _get_deployer()
    try:
        deployer.remove_site(domain)
        return JSONResponse({"status": "ok", "removed": domain})
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
