"""
network.py - Handlers réseau fragmentés depuis tool_system.py.

Handlers (13): network_scan, network_exec, network_list, network_info,
               network_wol, network_shutdown, network_set_credentials,
               network_port_scan, network_file_upload, network_file_download,
               network_file_edit, network_file_list, network_self_deploy.

Chaque handler est une fonction async standalone:
    async def handler_name(ctx: HandlerContext, **kwargs) -> HandlerResult

Câblage direct vers les fonctions handle_network_*() de network_hub.py.
"""

from __future__ import annotations

from typing import List

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Import lazy des fonctions network_hub ─────────────────────────────────

def _call(func_name: str):
    """Retourne la fonction handle_network_* depuis network_hub (import lazy)."""
    from ...tools import network_hub
    return getattr(network_hub, func_name)


# ─── Handlers ──────────────────────────────────────────────────────────────

async def network_scan_handler(
    ctx: HandlerContext,
    subnet: str = "",
    fast: bool = True,
) -> HandlerResult:
    """Scanne le réseau local et découvre les appareils actifs."""
    try:
        _subnet_label = subnet or "réseau local"
        logger.info("[cmd_start] network_scan {}", _subnet_label)
        fn = _call("handle_network_scan")
        result = await fn(subnet=subnet, fast=fast)
        logger.info("[cmd_done] exit:0")
        return HandlerResult.ok(result, handler_name="network_scan")
    except Exception as e:
        logger.info("[cmd_done] exit:1")
        return HandlerResult.fail(f"❌ Erreur network_scan: {e}", handler_name="network_scan")


def _require_network_authorization(authorization: str, tool_name: str):
    """Garde d'autorisation pour les outils réseau dangereux."""
    auth = (authorization or "").strip()
    if not auth:
        return HandlerResult.ok(
            f"⛔ AUTORISATION REQUISE — '{tool_name}' exécute des actions sur des machines distantes.\n"
            f"Cet outil ne s'exécute que sur demande explicite de l'utilisateur.\n"
            f"Fournir le paramètre: authorization='<raison explicite en français>'",
            handler_name=tool_name,
        )
    return None


async def network_exec_handler(
    ctx: HandlerContext,
    ip: str = "",
    command: str = "",
    username: str = "",
    password: str = "",
    key_path: str = "",
    authorization: str = "",
) -> HandlerResult:
    """Exécute une commande sur une machine distante (WinRM/SSH)."""
    guard = _require_network_authorization(authorization, "network_exec")
    if guard:
        return guard
    try:
        fn = _call("handle_network_exec")
        result = await fn(ip=ip, command=command, username=username, password=password, key_path=key_path)
        return HandlerResult.ok(result, handler_name="network_exec")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur network_exec: {e}", handler_name="network_exec")


async def network_list_handler(ctx: HandlerContext) -> HandlerResult:
    """Liste tous les appareils connus dans le registre réseau."""
    try:
        fn = _call("handle_network_list")
        result = await fn()
        return HandlerResult.ok(result, handler_name="network_list")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur network_list: {e}", handler_name="network_list")


async def network_info_handler(ctx: HandlerContext, ip: str = "") -> HandlerResult:
    """Obtient des informations système détaillées d'une machine distante."""
    try:
        fn = _call("handle_network_info")
        result = await fn(ip=ip)
        return HandlerResult.ok(result, handler_name="network_info")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur network_info: {e}", handler_name="network_info")


async def network_wol_handler(
    ctx: HandlerContext,
    ip: str = "",
    mac: str = "",
) -> HandlerResult:
    """Allume une machine éteinte via Wake-on-LAN."""
    try:
        fn = _call("handle_network_wol")
        result = await fn(ip=ip, mac=mac)
        return HandlerResult.ok(result, handler_name="network_wol")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur network_wol: {e}", handler_name="network_wol")


async def network_shutdown_handler(
    ctx: HandlerContext,
    ip: str = "",
    action: str = "shutdown",
    authorization: str = "",
) -> HandlerResult:
    """Éteint, redémarre ou met en veille une machine distante."""
    guard = _require_network_authorization(authorization, "network_shutdown")
    if guard:
        return guard
    try:
        fn = _call("handle_network_shutdown")
        result = await fn(ip=ip, action=action)
        return HandlerResult.ok(result, handler_name="network_shutdown")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur network_shutdown: {e}", handler_name="network_shutdown")


async def network_set_credentials_handler(
    ctx: HandlerContext,
    ip: str = "",
    username: str = "",
    password: str = "",
    key_path: str = "",
    label: str = "",
) -> HandlerResult:
    """Enregistre les credentials d'une machine dans le registre réseau."""
    try:
        fn = _call("handle_network_set_credentials")
        result = await fn(ip=ip, username=username, password=password, key_path=key_path, label=label)
        return HandlerResult.ok(result, handler_name="network_set_credentials")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur network_set_credentials: {e}", handler_name="network_set_credentials")


async def network_port_scan_handler(
    ctx: HandlerContext,
    ip: str = "",
    port_range: str = "1-1024",
) -> HandlerResult:
    """Scan détaillé des ports TCP d'une machine spécifique."""
    try:
        logger.info("[cmd_start] network_port_scan {} ports {}", ip, port_range)
        fn = _call("handle_network_port_scan")
        result = await fn(ip=ip, port_range=port_range)
        logger.info("[cmd_done] exit:0")
        return HandlerResult.ok(result, handler_name="network_port_scan")
    except Exception as e:
        logger.info("[cmd_done] exit:1")
        return HandlerResult.fail(f"❌ Erreur network_port_scan: {e}", handler_name="network_port_scan")


async def network_file_upload_handler(
    ctx: HandlerContext,
    ip: str = "",
    local_path: str = "",
    remote_path: str = "",
) -> HandlerResult:
    """Envoie un fichier local vers une machine distante."""
    try:
        fn = _call("handle_network_file_upload")
        result = await fn(ip=ip, local_path=local_path, remote_path=remote_path)
        return HandlerResult.ok(result, handler_name="network_file_upload")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur network_file_upload: {e}", handler_name="network_file_upload")


async def network_file_download_handler(
    ctx: HandlerContext,
    ip: str = "",
    remote_path: str = "",
    local_path: str = "",
) -> HandlerResult:
    """Télécharge un fichier depuis une machine distante."""
    try:
        fn = _call("handle_network_file_download")
        result = await fn(ip=ip, remote_path=remote_path, local_path=local_path)
        return HandlerResult.ok(result, handler_name="network_file_download")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur network_file_download: {e}", handler_name="network_file_download")


async def network_file_edit_handler(
    ctx: HandlerContext,
    ip: str = "",
    remote_path: str = "",
    content: str = "",
) -> HandlerResult:
    """Crée ou remplace le contenu d'un fichier sur une machine distante."""
    try:
        fn = _call("handle_network_file_edit")
        result = await fn(ip=ip, remote_path=remote_path, content=content)
        return HandlerResult.ok(result, handler_name="network_file_edit")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur network_file_edit: {e}", handler_name="network_file_edit")


async def network_file_list_handler(
    ctx: HandlerContext,
    ip: str = "",
    remote_path: str = "",
) -> HandlerResult:
    """Liste les fichiers dans un répertoire d'une machine distante."""
    try:
        fn = _call("handle_network_file_list")
        result = await fn(ip=ip, remote_path=remote_path)
        return HandlerResult.ok(result, handler_name="network_file_list")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur network_file_list: {e}", handler_name="network_file_list")


async def network_self_deploy_handler(
    ctx: HandlerContext,
    ip: str = "",
    remote_dir: str = "",
    launch: bool = False,
    authorization: str = "",
) -> HandlerResult:
    """Copie Lumena sur une machine distante et peut s'y lancer."""
    guard = _require_network_authorization(authorization, "network_self_deploy")
    if guard:
        return guard
    try:
        fn = _call("handle_network_self_deploy")
        result = await fn(ip=ip, remote_dir=remote_dir, launch=launch)
        return HandlerResult.ok(result, handler_name="network_self_deploy")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur network_self_deploy: {e}", handler_name="network_self_deploy")


# ─── Registry ──────────────────────────────────────────────────────────────

def get_network_handler_defs() -> List[HandlerDef]:
    """Retourne les 13 définitions de handlers réseau pour le registre V2."""
    return [
        HandlerDef(
            name="network_scan",
            description="Scanne le réseau local (toutes les IPs du sous-réseau). Détecte appareils actifs, IP, hostname, MAC, OS, ports ouverts. Utilise fast=true UNIQUEMENT si l'utilisateur veut un aperçu rapide (limite à 100 IPs).",
            parameters={
                "properties": {
                    "subnet": {"type": "string", "description": "Sous-réseau à scanner ex: 192.168.1.0/24 (auto-détecté si vide)", "default": ""},
                    "fast": {"type": "boolean", "description": "true=scan rapide 100 IPs max, false=scan COMPLET de tout le sous-réseau", "default": False},
                },
                "required": [],
            },
            handler=network_scan_handler,
            category="network",
            source_module="handlers.network",
        ),
        HandlerDef(
            name="network_exec",
            description="Exécute une commande sur une machine distante du réseau (WinRM Windows, SSH Linux/macOS).",
            parameters={
                "properties": {
                    "ip": {"type": "string", "description": "Adresse IP de la machine cible"},
                    "command": {"type": "string", "description": "Commande à exécuter"},
                    "username": {"type": "string", "description": "Nom d'utilisateur (optionnel si déjà enregistré)", "default": ""},
                    "password": {"type": "string", "description": "Mot de passe (optionnel si déjà enregistré)", "default": ""},
                    "key_path": {"type": "string", "description": "Chemin clé SSH (optionnel)", "default": ""},
                    "authorization": {"type": "string", "description": "Token d'autorisation requis pour exécuter cette commande réseau sensible", "default": ""},
                },
                "required": ["ip", "command"],
            },
            handler=network_exec_handler,
            category="network",
            source_module="handlers.network",
        ),
        HandlerDef(
            name="network_list",
            description="Liste tous les appareils connus dans le registre réseau de Lumena.",
            parameters={"properties": {}, "required": []},
            handler=network_list_handler,
            category="network",
            source_module="handlers.network",
        ),
        HandlerDef(
            name="network_info",
            description="Obtient des informations système détaillées d'une machine distante (CPU, RAM, disques, processus).",
            parameters={
                "properties": {
                    "ip": {"type": "string", "description": "Adresse IP de la machine cible"},
                },
                "required": ["ip"],
            },
            handler=network_info_handler,
            category="network",
            source_module="handlers.network",
        ),
        HandlerDef(
            name="network_wol",
            description="Allume une machine éteinte via Wake-on-LAN. Nécessite l'adresse MAC ou l'IP si connue dans le registre.",
            parameters={
                "properties": {
                    "ip": {"type": "string", "description": "IP de la machine (pour chercher la MAC dans le registre)", "default": ""},
                    "mac": {"type": "string", "description": "Adresse MAC de la machine (ex: AA-BB-CC-DD-EE-FF)", "default": ""},
                },
                "required": [],
            },
            handler=network_wol_handler,
            category="network",
            source_module="handlers.network",
        ),
        HandlerDef(
            name="network_shutdown",
            description="Éteint, redémarre ou met en veille une machine distante.",
            parameters={
                "properties": {
                    "ip": {"type": "string", "description": "IP de la machine cible"},
                    "action": {"type": "string", "description": "Action : shutdown | restart | sleep", "default": "shutdown"},
                    "authorization": {"type": "string", "description": "Token d'autorisation requis pour cette opération réseau sensible", "default": ""},
                },
                "required": ["ip"],
            },
            handler=network_shutdown_handler,
            category="network",
            source_module="handlers.network",
        ),
        HandlerDef(
            name="network_set_credentials",
            description="Enregistre les credentials (username/password/clé SSH) d'une machine dans le registre réseau local.",
            parameters={
                "properties": {
                    "ip": {"type": "string", "description": "IP de la machine"},
                    "username": {"type": "string", "description": "Nom d'utilisateur", "default": ""},
                    "password": {"type": "string", "description": "Mot de passe", "default": ""},
                    "key_path": {"type": "string", "description": "Chemin vers la clé SSH privée", "default": ""},
                    "label": {"type": "string", "description": "Nom/label de la machine (ex: PC-Bureau, NAS)", "default": ""},
                },
                "required": ["ip"],
            },
            handler=network_set_credentials_handler,
            category="network",
            source_module="handlers.network",
        ),
        HandlerDef(
            name="network_port_scan",
            description="Scan détaillé des ports TCP d'une machine. Identifie les services qui tournent.",
            parameters={
                "properties": {
                    "ip": {"type": "string", "description": "IP de la machine à scanner"},
                    "port_range": {"type": "string", "description": "Plage de ports ex: '1-1024' ou '22,80,443'", "default": "1-1024"},
                },
                "required": ["ip"],
            },
            handler=network_port_scan_handler,
            category="network",
            source_module="handlers.network",
        ),
        HandlerDef(
            name="network_file_upload",
            description="Envoie un fichier local vers une machine distante du réseau (WinRM ou SFTP/SCP).",
            parameters={
                "properties": {
                    "ip": {"type": "string", "description": "IP de la machine cible"},
                    "local_path": {"type": "string", "description": "Chemin local du fichier à envoyer"},
                    "remote_path": {"type": "string", "description": "Chemin de destination sur la machine distante"},
                },
                "required": ["ip", "local_path", "remote_path"],
            },
            handler=network_file_upload_handler,
            category="network",
            source_module="handlers.network",
        ),
        HandlerDef(
            name="network_file_download",
            description="Télécharge un fichier depuis une machine distante vers le PC local.",
            parameters={
                "properties": {
                    "ip": {"type": "string", "description": "IP de la machine source"},
                    "remote_path": {"type": "string", "description": "Chemin du fichier sur la machine distante"},
                    "local_path": {"type": "string", "description": "Chemin de destination local"},
                },
                "required": ["ip", "remote_path", "local_path"],
            },
            handler=network_file_download_handler,
            category="network",
            source_module="handlers.network",
        ),
        HandlerDef(
            name="network_file_edit",
            description="Crée ou remplace entièrement le contenu d'un fichier sur une machine distante.",
            parameters={
                "properties": {
                    "ip": {"type": "string", "description": "IP de la machine cible"},
                    "remote_path": {"type": "string", "description": "Chemin du fichier à créer/remplacer"},
                    "content": {"type": "string", "description": "Contenu textuel complet à écrire"},
                },
                "required": ["ip", "remote_path", "content"],
            },
            handler=network_file_edit_handler,
            category="network",
            source_module="handlers.network",
        ),
        HandlerDef(
            name="network_file_list",
            description="Liste les fichiers dans un répertoire d'une machine distante.",
            parameters={
                "properties": {
                    "ip": {"type": "string", "description": "IP de la machine cible"},
                    "remote_path": {"type": "string", "description": "Répertoire à lister sur la machine distante"},
                },
                "required": ["ip", "remote_path"],
            },
            handler=network_file_list_handler,
            category="network",
            source_module="handlers.network",
        ),
        HandlerDef(
            name="network_self_deploy",
            description="Se copie elle-même (Lumena) sur une machine distante et peut s'y lancer.",
            parameters={
                "properties": {
                    "ip": {"type": "string", "description": "IP de la machine cible"},
                    "remote_dir": {"type": "string", "description": "Répertoire de destination (ex: C:/lumena ou /opt/lumena)"},
                    "launch": {"type": "boolean", "description": "Lancer Lumena après le déploiement", "default": False},
                    "authorization": {"type": "string", "description": "Token d'autorisation requis pour le déploiement réseau", "default": ""},
                },
                "required": ["ip", "remote_dir"],
            },
            handler=network_self_deploy_handler,
            category="network",
            source_module="handlers.network",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
