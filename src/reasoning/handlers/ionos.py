"""
ionos.py — Handlers IONOS pour le déploiement SFTP de sites web.

Handlers: deploy_to_ionos, update_ionos_files, ionos_add_site,
          ionos_remove_site, ionos_list_sites, ionos_list_files,
          ionos_delete_files.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ── Lazy deployer singleton ──────────────────────────────────────────────

_deployer = None


def _get_deployer():
    global _deployer
    if _deployer is None:
        from src.services.ionos_deployer import IonosDeployer
        _deployer = IonosDeployer()
    return _deployer


# ── Handlers ─────────────────────────────────────────────────────────────

async def deploy_to_ionos_handler(
    ctx: HandlerContext,
    site: str = "",
    project_dir: str = "",
    dry_run: str = "false",
) -> HandlerResult:
    """Déployer un projet web complet sur un hébergement IONOS via SFTP."""
    try:
        deployer = _get_deployer()

        # Resolve site
        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        if not site:
            return HandlerResult.fail(
                "Aucun site spécifié et LUMENA_IONOS_DEFAULT_SITE est vide. "
                "Précise le domaine (ex: deploy_to_ionos site=lumena.fr).",
                handler_name="deploy_to_ionos",
            )

        # Resolve project directory
        if not project_dir:
            from src.utils.project_registry import find_project
            found = find_project("")
            if found:
                project_dir = str(found)
            else:
                return HandlerResult.fail(
                    "Aucun projet trouvé. Précise project_dir ou crée un projet d'abord.",
                    handler_name="deploy_to_ionos",
                )

        is_dry = dry_run.lower() in ("true", "1", "yes", "oui")

        result = await deployer.deploy(
            site, Path(project_dir), dry_run=is_dry
        )

        if not result.success:
            return HandlerResult.fail(
                f"❌ Déploiement échoué sur {site}:\n" + "\n".join(result.errors),
                handler_name="deploy_to_ionos",
            )

        mode = " (DRY RUN)" if result.dry_run else ""
        return HandlerResult.ok(
            f"✅ Déploiement{mode} sur **{site}** terminé.\n"
            f"• Fichiers uploadés : {result.uploaded}\n"
            f"• Fichiers ignorés : {result.skipped}\n"
            f"• Taille totale : {result.total_bytes / 1024:.1f} Ko\n"
            f"• Durée : {result.duration_sec:.1f}s",
            handler_name="deploy_to_ionos",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur déploiement IONOS: {e}",
            handler_name="deploy_to_ionos",
        )


async def update_ionos_files_handler(
    ctx: HandlerContext,
    site: str = "",
    files: str = "",
) -> HandlerResult:
    """Mettre à jour des fichiers spécifiques sur un site IONOS."""
    try:
        deployer = _get_deployer()

        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        if not site:
            return HandlerResult.fail(
                "Aucun site spécifié.",
                handler_name="update_ionos_files",
            )

        if not files:
            return HandlerResult.fail(
                "Aucun fichier spécifié. Paramètre 'files' requis (chemins séparés par des virgules).",
                handler_name="update_ionos_files",
            )

        file_pairs = []
        for f in files.split(","):
            f = f.strip()
            if not f:
                continue
            p = Path(f)
            if not p.is_file():
                return HandlerResult.fail(
                    f"Fichier introuvable: {f}",
                    handler_name="update_ionos_files",
                )
            file_pairs.append((p.name, p))

        result = await deployer.upload_files(site, file_pairs)

        if not result.success:
            return HandlerResult.fail(
                f"❌ Upload échoué:\n" + "\n".join(result.errors),
                handler_name="update_ionos_files",
            )

        return HandlerResult.ok(
            f"✅ {result.uploaded} fichier(s) mis à jour sur **{site}**.",
            handler_name="update_ionos_files",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur update IONOS: {e}",
            handler_name="update_ionos_files",
        )


async def ionos_add_site_handler(
    ctx: HandlerContext,
    domain: str = "",
    host: str = "",
    user: str = "",
    password: str = "",
    port: str = "22",
    root: str = "/",
    label: str = "",
) -> HandlerResult:
    """Ajouter un nouveau site IONOS (credentials SFTP)."""
    try:
        if not domain or not host or not user or not password:
            return HandlerResult.fail(
                "Paramètres requis: domain, host, user, password.",
                handler_name="ionos_add_site",
            )

        deployer = _get_deployer()
        result = deployer.add_site(
            domain=domain, host=host, user=user,
            password=password, port=int(port),
            root=root, label=label,
        )
        return HandlerResult.ok(
            f"✅ Site IONOS ajouté: **{domain}** → {host}\n"
            f"Connexion SFTP testée avec succès.",
            handler_name="ionos_add_site",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur ajout site: {e}",
            handler_name="ionos_add_site",
        )


async def ionos_remove_site_handler(
    ctx: HandlerContext,
    domain: str = "",
) -> HandlerResult:
    """Supprimer un site IONOS de la configuration."""
    try:
        if not domain:
            return HandlerResult.fail(
                "Paramètre 'domain' requis.",
                handler_name="ionos_remove_site",
            )
        deployer = _get_deployer()
        deployer.remove_site(domain)
        return HandlerResult.ok(
            f"✅ Site **{domain}** supprimé de la configuration IONOS.",
            handler_name="ionos_remove_site",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur suppression: {e}",
            handler_name="ionos_remove_site",
        )


async def ionos_list_sites_handler(
    ctx: HandlerContext,
) -> HandlerResult:
    """Lister tous les sites IONOS configurés."""
    try:
        deployer = _get_deployer()
        sites = deployer.list_sites()

        if not sites:
            return HandlerResult.ok(
                "Aucun site IONOS configuré. Utilise `ionos_add_site` pour en ajouter un.",
                handler_name="ionos_list_sites",
            )

        lines = ["**Sites IONOS configurés :**\n"]
        for s in sites:
            deploy_info = f" (dernier déploiement: {s['last_deploy']})" if s["last_deploy"] else ""
            lines.append(
                f"• **{s['domain']}** — {s['host']}:{s['port']} "
                f"(user: {s['user']}, root: {s['root']}){deploy_info}"
            )
        return HandlerResult.ok(
            "\n".join(lines),
            handler_name="ionos_list_sites",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur listing: {e}",
            handler_name="ionos_list_sites",
        )


async def ionos_list_files_handler(
    ctx: HandlerContext,
    site: str = "",
    path: str = "/",
) -> HandlerResult:
    """Lister les fichiers présents sur un site IONOS."""
    try:
        deployer = _get_deployer()

        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        if not site:
            return HandlerResult.fail(
                "Aucun site spécifié.",
                handler_name="ionos_list_files",
            )

        files = await deployer.list_remote(site, path)

        if not files:
            return HandlerResult.ok(
                f"Aucun fichier trouvé sur **{site}** dans `{path}`.",
                handler_name="ionos_list_files",
            )

        lines = [f"**Fichiers sur {site} ({path}) :**\n"]
        for f in files:
            icon = "📁" if f.is_dir else "📄"
            size = f"({f.size:,} octets)" if not f.is_dir else ""
            lines.append(f"  {icon} {f.path} {size}")

        return HandlerResult.ok(
            "\n".join(lines),
            handler_name="ionos_list_files",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur listing fichiers: {e}",
            handler_name="ionos_list_files",
        )


async def ionos_delete_files_handler(
    ctx: HandlerContext,
    site: str = "",
    paths: str = "",
) -> HandlerResult:
    """Supprimer des fichiers sur un site IONOS."""
    try:
        deployer = _get_deployer()

        if not site:
            site = os.getenv("LUMENA_IONOS_DEFAULT_SITE", "")
        if not site:
            return HandlerResult.fail(
                "Aucun site spécifié.",
                handler_name="ionos_delete_files",
            )

        if not paths:
            return HandlerResult.fail(
                "Paramètre 'paths' requis (chemins distants séparés par des virgules).",
                handler_name="ionos_delete_files",
            )

        path_list = [p.strip() for p in paths.split(",") if p.strip()]
        result = await deployer.delete_remote(site, path_list)

        if not result["success"]:
            return HandlerResult.fail(
                f"❌ Suppression partielle:\n" + "\n".join(result["errors"]),
                handler_name="ionos_delete_files",
            )

        return HandlerResult.ok(
            f"✅ {result['deleted']} fichier(s) supprimé(s) sur **{site}**.",
            handler_name="ionos_delete_files",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur suppression: {e}",
            handler_name="ionos_delete_files",
        )


# ── Handler definitions ──────────────────────────────────────────────────

def get_ionos_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions de handlers IONOS pour le registre V2."""
    return [
        HandlerDef(
            name="deploy_to_ionos",
            description=(
                "Déployer/publier un projet web complet sur un hébergement IONOS via SFTP. "
                "Uploade tous les fichiers du projet sur le serveur distant."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Domaine du site IONOS cible (ex: lumena.fr). Vide = site par défaut.",
                    },
                    "project_dir": {
                        "type": "string",
                        "description": "Chemin du dossier projet local à déployer. Vide = dernier projet.",
                    },
                    "dry_run": {
                        "type": "string",
                        "description": "true = simuler sans uploader. false = déployer réellement.",
                    },
                },
                "required": [],
            },
            handler=deploy_to_ionos_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="update_ionos_files",
            description=(
                "Mettre à jour des fichiers spécifiques sur un site IONOS déjà déployé."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Domaine du site IONOS cible.",
                    },
                    "files": {
                        "type": "string",
                        "description": "Chemins locaux des fichiers à uploader, séparés par des virgules.",
                    },
                },
                "required": ["files"],
            },
            handler=update_ionos_files_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_add_site",
            description=(
                "Ajouter un nouveau site IONOS avec les credentials SFTP. "
                "Teste la connexion avant de sauvegarder."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Nom de domaine du site (ex: lumena.fr).",
                    },
                    "host": {
                        "type": "string",
                        "description": "Hostname SFTP (ex: access123456789.webspace-data.io).",
                    },
                    "user": {
                        "type": "string",
                        "description": "Username SFTP.",
                    },
                    "password": {
                        "type": "string",
                        "description": "Mot de passe SFTP.",
                    },
                    "port": {
                        "type": "string",
                        "description": "Port SFTP (défaut: 22).",
                    },
                    "root": {
                        "type": "string",
                        "description": "Dossier racine distant (défaut: /).",
                    },
                    "label": {
                        "type": "string",
                        "description": "Label descriptif du site.",
                    },
                },
                "required": ["domain", "host", "user", "password"],
            },
            handler=ionos_add_site_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_remove_site",
            description="Supprimer un site IONOS de la configuration Lumena.",
            parameters={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "Domaine du site à supprimer.",
                    },
                },
                "required": ["domain"],
            },
            handler=ionos_remove_site_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_list_sites",
            description="Lister tous les sites IONOS configurés dans Lumena.",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=ionos_list_sites_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_list_files",
            description="Lister les fichiers et dossiers présents sur un site IONOS distant.",
            parameters={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Domaine du site IONOS.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Chemin distant à lister (défaut: /).",
                    },
                },
                "required": [],
            },
            handler=ionos_list_files_handler,
            category="ionos",
            source_module="ionos",
        ),
        HandlerDef(
            name="ionos_delete_files",
            description="Supprimer des fichiers sur un site IONOS distant via SFTP.",
            parameters={
                "type": "object",
                "properties": {
                    "site": {
                        "type": "string",
                        "description": "Domaine du site IONOS.",
                    },
                    "paths": {
                        "type": "string",
                        "description": "Chemins distants à supprimer, séparés par des virgules.",
                    },
                },
                "required": ["paths"],
            },
            handler=ionos_delete_files_handler,
            category="ionos",
            source_module="ionos",
        ),
    ]
