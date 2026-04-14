"""
http_api.py - Handlers HTTP / API REST fragmentés depuis tool_system.py.

Handlers (5): http_request, http_api_register, http_api_list,
              http_upload_file, http_webhook_test.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Helpers ───────────────────────────────────────────────────────────────

def _get_http_handlers():
    """Import lazy des fonctions http_client."""
    from ...tools.http_client import (
        handle_http_request,
        handle_http_api_register,
        handle_http_api_list,
        handle_http_upload_file,
        handle_http_webhook_test,
    )
    return {
        "request": handle_http_request,
        "api_register": handle_http_api_register,
        "api_list": handle_http_api_list,
        "upload_file": handle_http_upload_file,
        "webhook_test": handle_http_webhook_test,
    }


# ─── Handlers ──────────────────────────────────────────────────────────────

async def http_request_handler(
    ctx: HandlerContext,
    url: str,
    method: str = "GET",
    headers: Optional[Dict[str, str]] = None,
    body: Optional[str] = None,
    json_body: Optional[Any] = None,
    timeout: int = 30,
    follow_redirects: bool = True,
) -> HandlerResult:
    """Effectue une requête HTTP/HTTPS vers n'importe quelle URL."""
    try:
        fns = _get_http_handlers()
        result = await fns["request"](
            url=url,
            method=method,
            headers=headers or {},
            body=body,
            json_body=json_body,
            timeout=timeout,
            follow_redirects=follow_redirects,
        )
        return HandlerResult.ok(str(result), handler_name="http_request")
    except ImportError:
        return HandlerResult.fail("❌ http_client non disponible.", handler_name="http_request")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur http_request: {e}", handler_name="http_request")


async def http_api_register_handler(
    ctx: HandlerContext,
    name: str,
    base_url: str,
    description: str = "",
    auth_type: str = "none",
    auth_token: str = "",
    auth_username: str = "",
    auth_password: str = "",
) -> HandlerResult:
    """Enregistre un endpoint API REST nommé pour un accès rapide ultérieur."""
    try:
        fns = _get_http_handlers()
        result = await fns["api_register"](
            name=name,
            base_url=base_url,
            description=description,
            auth_type=auth_type,
            auth_token=auth_token,
            auth_username=auth_username,
            auth_password=auth_password,
        )
        return HandlerResult.ok(str(result), handler_name="http_api_register")
    except ImportError:
        return HandlerResult.fail("❌ http_client non disponible.", handler_name="http_api_register")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur http_api_register: {e}", handler_name="http_api_register")


async def http_api_list_handler(ctx: HandlerContext) -> HandlerResult:
    """Liste toutes les APIs REST enregistrées dans Lumena."""
    try:
        fns = _get_http_handlers()
        result = await fns["api_list"]()
        return HandlerResult.ok(str(result), handler_name="http_api_list")
    except ImportError:
        return HandlerResult.fail("❌ http_client non disponible.", handler_name="http_api_list")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur http_api_list: {e}", handler_name="http_api_list")


async def http_upload_file_handler(
    ctx: HandlerContext,
    url: str,
    file_path: str,
    field_name: str = "file",
    extra_fields: Optional[Dict[str, str]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 120,
) -> HandlerResult:
    """Upload un fichier local vers un endpoint HTTP en multipart/form-data."""
    try:
        fns = _get_http_handlers()
        result = await fns["upload_file"](
            url=url,
            file_path=file_path,
            field_name=field_name,
            extra_fields=extra_fields or {},
            headers=headers or {},
            timeout=timeout,
        )
        return HandlerResult.ok(str(result), handler_name="http_upload_file")
    except ImportError:
        return HandlerResult.fail("❌ http_client non disponible.", handler_name="http_upload_file")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur http_upload_file: {e}", handler_name="http_upload_file")


async def http_webhook_test_handler(
    ctx: HandlerContext,
    url: str,
    payload: Optional[Any] = None,
    method: str = "POST",
    headers: Optional[Dict[str, str]] = None,
) -> HandlerResult:
    """Teste un webhook en envoyant un payload de test à l'URL spécifiée."""
    try:
        fns = _get_http_handlers()
        result = await fns["webhook_test"](
            url=url,
            payload=payload,
            method=method,
            headers=headers or {},
        )
        return HandlerResult.ok(str(result), handler_name="http_webhook_test")
    except ImportError:
        return HandlerResult.fail("❌ http_client non disponible.", handler_name="http_webhook_test")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur http_webhook_test: {e}", handler_name="http_webhook_test")


# ─── Registry ──────────────────────────────────────────────────────────────

def get_http_api_handler_defs() -> List[HandlerDef]:
    """Retourne les 5 définitions de handlers HTTP/API pour le registre V2."""
    return [
        HandlerDef(
            name="http_request",
            description=(
                "Effectue une requête HTTP/HTTPS (GET, POST, PUT, DELETE, PATCH…) "
                "vers n'importe quelle URL et retourne le statut, headers et corps de la réponse."
            ),
            parameters={
                "properties": {
                    "url": {"type": "string", "description": "URL complète de la requête"},
                    "method": {"type": "string", "description": "Méthode HTTP : GET | POST | PUT | DELETE | PATCH", "default": "GET"},
                    "headers": {"type": "object", "description": "Headers HTTP {clé: valeur}. Ex: {\"Authorization\": \"Bearer token\", \"Content-Type\": \"application/json\"}", "default": {}},
                    "body": {"type": "string", "description": "Corps de la requête en texte brut", "default": None},
                    "json_body": {"description": "Corps JSON (dict ou liste, sérialisé auto)", "default": None},
                    "timeout": {"type": "integer", "description": "Timeout en secondes", "default": 30},
                    "follow_redirects": {"type": "boolean", "description": "Suivre les redirections", "default": True},
                },
                "required": ["url"],
            },
            handler=http_request_handler,
            category="web",
            source_module="handlers.http_api",
        ),
        HandlerDef(
            name="http_api_register",
            description="Enregistre un endpoint API REST nommé (avec auth optionnelle) pour pouvoir l'utiliser rapidement par son nom.",
            parameters={
                "properties": {
                    "name": {"type": "string", "description": "Nom unique de l'API enregistrée"},
                    "base_url": {"type": "string", "description": "URL de base de l'API"},
                    "description": {"type": "string", "description": "Description de l'API", "default": ""},
                    "auth_type": {"type": "string", "description": "Type d'auth : none | bearer | basic", "default": "none"},
                    "auth_token": {"type": "string", "description": "Token Bearer (si auth_type=bearer)", "default": ""},
                    "auth_username": {"type": "string", "description": "Username (si auth_type=basic)", "default": ""},
                    "auth_password": {"type": "string", "description": "Mot de passe (si auth_type=basic)", "default": ""},
                },
                "required": ["name", "base_url"],
            },
            handler=http_api_register_handler,
            category="web",
            source_module="handlers.http_api",
        ),
        HandlerDef(
            name="http_api_list",
            description="Liste toutes les APIs REST enregistrées dans Lumena avec leurs URLs et types d'authentification.",
            parameters={"properties": {}, "required": []},
            handler=http_api_list_handler,
            category="web",
            source_module="handlers.http_api",
        ),
        HandlerDef(
            name="http_upload_file",
            description="Upload un fichier local vers un serveur HTTP en multipart/form-data. Idéal pour les APIs d'upload.",
            parameters={
                "properties": {
                    "url": {"type": "string", "description": "URL de l'endpoint d'upload"},
                    "file_path": {"type": "string", "description": "Chemin local du fichier à uploader"},
                    "field_name": {"type": "string", "description": "Nom du champ multipart", "default": "file"},
                    "extra_fields": {"type": "object", "description": "Champs additionnels du formulaire {clé: valeur}. Ex: {\"field1\": \"value1\"}", "default": {}},
                    "headers": {"type": "object", "description": "Headers HTTP {clé: valeur}. Ex: {\"Authorization\": \"Bearer token\"}", "default": {}},
                    "timeout": {"type": "integer", "description": "Timeout en secondes", "default": 120},
                },
                "required": ["url", "file_path"],
            },
            handler=http_upload_file_handler,
            category="web",
            source_module="handlers.http_api",
        ),
        HandlerDef(
            name="http_webhook_test",
            description="Teste un webhook en lui envoyant un payload de test. Vérifie qu'il répond correctement.",
            parameters={
                "properties": {
                    "url": {"type": "string", "description": "URL du webhook à tester"},
                    "payload": {"description": "Payload à envoyer (dict ou liste, sérialisé en JSON)", "default": None},
                    "method": {"type": "string", "description": "Méthode HTTP", "default": "POST"},
                    "headers": {"type": "object", "description": "Headers HTTP {clé: valeur}. Ex: {\"Authorization\": \"Bearer token\"}", "default": {}},
                },
                "required": ["url"],
            },
            handler=http_webhook_test_handler,
            category="web",
            source_module="handlers.http_api",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
