"""
Lumena — HTTP Client Hub
========================
Client HTTP complet pour Lumena : toutes méthodes, authentification, headers custom,
upload multipart, parsing auto JSON/XML/HTML, proxy, timeout, retry, webhooks.

Utilisé pour appeler n'importe quelle API REST (Stripe, GitHub, Discord, Binance,
OpenAI, webhooks internes, etc.) directement depuis une conversation Lumena.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode, urlparse

from loguru import logger
from ..utils.persistence import atomic_write_json, safe_read_json

# ─────────────────────────────────────────────────────────────
# STORE DE CREDENTIALS / ENV KEYS POUR LES APIS CONNUES
# Lumena mémorise automatiquement les API keys configurées.
# ─────────────────────────────────────────────────────────────
from src.utils.paths import APIS_REGISTRY_JSON as _APIS_REGISTRY_PATH


def _load_apis_registry() -> dict:
    return safe_read_json(_APIS_REGISTRY_PATH, default={"apis": {}})


def _save_apis_registry(reg: dict) -> None:
    _APIS_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_APIS_REGISTRY_PATH, reg)


def _resolve_api_key(name: str) -> str:
    """
    Cherche une API key dans :
    1. Le registre APIs sauvegardé
    2. Les variables d'environnement (ex: STRIPE_API_KEY, GITHUB_TOKEN, etc.)
    """
    reg = _load_apis_registry()
    stored = reg["apis"].get(name, {}).get("key", "")
    if stored:
        return stored
    # Mapping noms courants → variables d'env
    ENV_MAP = {
        "stripe": "STRIPE_API_KEY",
        "github": "GITHUB_TOKEN",
        "discord": "DISCORD_TOKEN",
        "openai": "OPENAI_API_KEY",
        "binance": "BINANCE_API_KEY",
        "telegram": "TELEGRAM_BOT_TOKEN",
        "anthropic": "ANTHROPIC_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "notion": "NOTION_API_KEY",
        "slack": "SLACK_BOT_TOKEN",
        "twilio": "TWILIO_AUTH_TOKEN",
        "sendgrid": "SENDGRID_API_KEY",
        "cloudflare": "CLOUDFLARE_API_KEY",
        "aws": "AWS_SECRET_ACCESS_KEY",
        "google": "GOOGLE_API_KEY",
        "whatsapp": "WHATSAPP_ACCESS_TOKEN",
    }
    env_name = ENV_MAP.get(name.lower(), name.upper() + "_API_KEY")
    return os.getenv(env_name, "")


# ─────────────────────────────────────────────────────────────
# PARSING DES RÉPONSES
# ─────────────────────────────────────────────────────────────

def _parse_response(content: bytes, content_type: str, truncate: int = 8000) -> str:
    """Parse intelligemment une réponse HTTP selon son content-type."""
    ct = content_type.lower()

    if "json" in ct:
        try:
            data = json.loads(content.decode("utf-8", errors="replace"))
            pretty = json.dumps(data, ensure_ascii=False, indent=2)
            if len(pretty) > truncate:
                return pretty[:truncate] + f"\n... [tronqué à {truncate} chars]"
            return pretty
        except Exception as e:
            logger.debug(f"JSON pretty print: {e}")

    if "xml" in ct or "html" in ct:
        text = content.decode("utf-8", errors="replace")
        # Nettoyer les tags HTML basiques pour la lisibilité
        if "html" in ct:
            clean = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
            clean = re.sub(r"<style[^>]*>.*?</style>", "", clean, flags=re.DOTALL | re.IGNORECASE)
            clean = re.sub(r"<[^>]+>", " ", clean)
            clean = re.sub(r"\s{3,}", "\n", clean).strip()
            if len(clean) > truncate:
                return clean[:truncate] + f"\n... [tronqué]"
            return clean
        if len(text) > truncate:
            return text[:truncate] + f"\n... [tronqué]"
        return text

    # Binaire (image, PDF, etc.) → ne pas afficher le contenu brut
    if any(x in ct for x in ("image/", "audio/", "video/", "application/pdf", "application/zip")):
        return f"[Contenu binaire : {len(content)} octets, type={content_type}]"

    text = content.decode("utf-8", errors="replace")
    if len(text) > truncate:
        return text[:truncate] + f"\n... [tronqué]"
    return text


def _format_headers_display(headers: dict) -> str:
    """Affiche les headers en masquant les valeurs sensibles."""
    SENSITIVE = {"authorization", "x-api-key", "api-key", "token", "secret", "password", "cookie"}
    out = []
    for k, v in headers.items():
        if any(s in k.lower() for s in SENSITIVE):
            v = v[:6] + "***" if len(v) > 6 else "***"
        out.append(f"  {k}: {v}")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────
# CŒUR : exécution HTTP avec retry
# ─────────────────────────────────────────────────────────────

async def _do_request(
    method: str,
    url: str,
    headers: dict,
    body: Optional[bytes],
    timeout: float,
    retries: int,
    verify_ssl: bool,
) -> tuple[int, dict, bytes]:
    """Exécute la requête HTTP avec retry exponentiel. Retourne (status, headers, body)."""
    import urllib.request
    import urllib.error
    import ssl

    ssl_ctx = ssl.create_default_context()
    if not verify_ssl:
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    loop = asyncio.get_running_loop()

    def _sync_request() -> tuple[int, dict, bytes]:
        req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
        last_exc = None
        for attempt in range(max(1, retries)):
            try:
                with urllib.request.urlopen(req, context=ssl_ctx, timeout=timeout) as resp:
                    resp_headers = dict(resp.headers)
                    resp_body = resp.read()
                    return resp.status, resp_headers, resp_body
            except urllib.error.HTTPError as e:
                resp_body = e.read() if hasattr(e, "read") else b""
                return e.code, dict(e.headers), resp_body
            except Exception as e:
                last_exc = e
                if attempt < retries - 1:
                    time.sleep(0.5 * (2 ** attempt))
        raise last_exc or RuntimeError("Requête échouée après tous les retries")

    return await loop.run_in_executor(None, _sync_request)


# ─────────────────────────────────────────────────────────────
# HANDLERS PUBLICS
# ─────────────────────────────────────────────────────────────

async def handle_http_request(**kwargs) -> str:
    """
    Effectue une requête HTTP vers n'importe quelle URL/API.
    Supporte GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS.
    Gère automatiquement JSON, form-data, auth Bearer/Basic/API-key.

    Paramètres :
    - url : URL complète (ex: https://api.stripe.com/v1/charges)
    - method : GET | POST | PUT | PATCH | DELETE | HEAD (défaut: GET)
    - body : Corps de la requête (dict ou string JSON)
    - headers : Dict de headers additionnels
    - auth_type : none | bearer | basic | api_key | api_name (défaut: none)
    - auth_value : Token/clé/user:pass selon auth_type
    - api_name : Nom d'une API enregistrée (stripe/github/discord/binance...)
    - params : Query parameters (dict)
    - content_type : application/json | application/x-www-form-urlencoded | multipart/form-data
    - timeout : Timeout en secondes (défaut: 30)
    - retries : Nombre de tentatives (défaut: 1)
    - download_to : Si spécifié, sauvegarde la réponse dans ce chemin local
    """
    url = kwargs.get("url", "").strip()
    if not url:
        return "url est requis"

    # Validation URL de base
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"URL invalide — scheme '{parsed.scheme}' non supporté (utilisez http ou https)"

    method = kwargs.get("method", "GET").strip().upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        return f"Méthode '{method}' non supportée"

    body_raw = kwargs.get("json_body", None) or kwargs.get("body", None)
    extra_headers: dict = kwargs.get("headers", {}) or {}
    auth_type: str = kwargs.get("auth_type", "none").strip().lower()
    auth_value: str = kwargs.get("auth_value", "").strip()
    api_name: str = kwargs.get("api_name", "").strip().lower()
    params: dict = kwargs.get("params", {}) or {}
    content_type: str = kwargs.get("content_type", "").strip()
    timeout: float = float(kwargs.get("timeout", 30))
    retries: int = int(kwargs.get("retries", 1))
    download_to: str = kwargs.get("download_to", "").strip()
    verify_ssl: bool = kwargs.get("verify_ssl", True)
    show_headers: bool = kwargs.get("show_headers", False)

    # ── Résolution API key depuis le nom de l'API ──
    _PLACEHOLDER_PATTERNS = ("your_", "_token_here", "my_token", "insert_", "<token", "<your")
    API_AUTH_TYPES = {
        "stripe": "bearer",
        "github": "bearer",
        "openai": "bearer",
        "anthropic": "api_key",
        "deepseek": "bearer",
        "discord": "bot",
        "notion": "bearer",
        "slack": "bearer",
        "cloudflare": "bearer",
        "sendgrid": "bearer",
        "binance": "api_key",
    }
    # Détection de l'API depuis l'URL si api_name non fourni
    URL_API_MAP = {
        "discord.com": "discord",
        "api.stripe.com": "stripe",
        "api.github.com": "github",
        "api.openai.com": "openai",
        "api.telegram.org": "telegram",
        "slack.com": "slack",
        "api.notion.com": "notion",
        "api.anthropic.com": "anthropic",
        "api.deepseek.com": "deepseek",
        "api.cloudflare.com": "cloudflare",
        "api.binance.com": "binance",
        "graph.facebook.com": "whatsapp",
    }
    if not api_name:
        for domain, name in URL_API_MAP.items():
            if domain in url:
                api_name = name
                break

    # Nettoyer les placeholders dans auth_value passé directement
    if auth_value and any(p in auth_value.lower() for p in _PLACEHOLDER_PATTERNS):
        auth_value = ""
    if api_name and not auth_value:
        auth_value = _resolve_api_key(api_name)
        if auth_value and auth_type == "none":
            auth_type = API_AUTH_TYPES.get(api_name, "bearer")

    # ── Construction des headers ──
    headers: dict = {
        "User-Agent": "Lumena-Agent/1.0",
        "Accept": "application/json, */*;q=0.8",
    }
    # Copier les extra_headers SAUF Authorization (on la gère nous-mêmes)
    headers.update({k: str(v) for k, v in extra_headers.items() if k.lower() != "authorization"})

    # Auth — si on a résolu un vrai token, il prime TOUJOURS sur les headers manuels
    if auth_type == "bot" and auth_value:
        headers["Authorization"] = f"Bot {auth_value}"
    elif auth_type == "bearer" and auth_value:
        headers["Authorization"] = f"Bearer {auth_value}"
    elif auth_type == "basic" and auth_value:
        import base64
        encoded = base64.b64encode(auth_value.encode()).decode()
        headers["Authorization"] = f"Basic {encoded}"
    elif auth_type == "api_key" and auth_value:
        # Certaines APIs utilisent X-API-Key
        headers["X-API-Key"] = auth_value
    elif auth_type == "api_name" and api_name:
        key = _resolve_api_key(api_name)
        if key:
            headers["Authorization"] = f"Bearer {key}"

    # ── Ajout des query params à l'URL ──
    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urlencode({k: str(v) for k, v in params.items()})

    # ── Corps de la requête ──
    body_bytes: Optional[bytes] = None
    if body_raw is not None and method not in ("GET", "HEAD"):
        if isinstance(body_raw, dict):
            ct = content_type or "application/json"
            if "json" in ct:
                body_bytes = json.dumps(body_raw).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
            elif "form" in ct:
                body_bytes = urlencode(body_raw).encode("utf-8")
                headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
            else:
                body_bytes = json.dumps(body_raw).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
        elif isinstance(body_raw, str):
            body_bytes = body_raw.encode("utf-8")
            if not content_type:
                # Détecter si c'est du JSON
                try:
                    json.loads(body_raw)
                    headers.setdefault("Content-Type", "application/json")
                except Exception:
                    headers.setdefault("Content-Type", "text/plain")
            else:
                headers.setdefault("Content-Type", content_type)
        elif isinstance(body_raw, bytes):
            body_bytes = body_raw

        if body_bytes:
            headers["Content-Length"] = str(len(body_bytes))

    # ── Exécution ──
    try:
        start = time.monotonic()
        status, resp_headers, resp_body = await _do_request(
            method=method,
            url=url,
            headers=headers,
            body=body_bytes,
            timeout=timeout,
            retries=retries,
            verify_ssl=verify_ssl,
        )
        elapsed = time.monotonic() - start
    except Exception as e:
        return f"✗ Erreur réseau : {type(e).__name__}: {e}"

    # ── Sauvegarde fichier si demandé ──
    if download_to:
        try:
            Path(download_to).parent.mkdir(parents=True, exist_ok=True)
            Path(download_to).write_bytes(resp_body)
            saved_msg = f"\n💾 Réponse sauvegardée dans : {download_to} ({len(resp_body)} octets)"
        except Exception as e:
            saved_msg = f"\n⚠️ Impossible de sauvegarder : {e}"
    else:
        saved_msg = ""

    # ── Parsing réponse ──
    resp_ct = resp_headers.get("Content-Type", resp_headers.get("content-type", "text/plain"))
    parsed_body = _parse_response(resp_body, resp_ct)

    # ── Résumé ──
    emoji = "✓" if 200 <= status < 300 else ("⚠️" if 300 <= status < 400 else "✗")
    lines = [
        f"{emoji} {method} {url}",
        f"   Status : {status}  |  {elapsed*1000:.0f}ms  |  {len(resp_body)} octets",
    ]

    if show_headers:
        lines.append(f"\n── Headers réponse ──\n{_format_headers_display(resp_headers)}")

    lines.append(f"\n── Réponse ──\n{parsed_body}")
    lines.append(saved_msg)

    return "\n".join(lines)


async def handle_http_api_register(**kwargs) -> str:
    """
    Enregistre une API key dans le registre local de Lumena.
    Les credentials sont réutilisés automatiquement lors des prochains appels.
    Exemples : stripe, github, discord, binance, openai, notion...
    """
    name = kwargs.get("name", "").strip().lower()
    key = kwargs.get("key", "").strip()
    base_url = kwargs.get("base_url", "").strip()
    note = kwargs.get("note", "").strip()

    if not name or not key:
        return "name et key sont requis"

    reg = _load_apis_registry()
    reg["apis"][name] = {
        "key": key,
        "base_url": base_url,
        "note": note,
        "registered_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _save_apis_registry(reg)

    masked = key[:6] + "***" if len(key) > 6 else "***"
    return f"✓ API '{name}' enregistrée (key={masked})"


async def handle_http_api_list(**kwargs) -> str:
    """
    Liste toutes les APIs enregistrées dans le registre Lumena.
    """
    reg = _load_apis_registry()
    apis = reg.get("apis", {})
    if not apis:
        return "Aucune API enregistrée. Utilise http_api_register pour en ajouter."

    lines = [f"🔑 APIs enregistrées ({len(apis)})\n"]
    for name, info in sorted(apis.items()):
        key = info.get("key", "")
        masked = key[:6] + "***" if len(key) > 6 else "***"
        lines.append(
            f"  {name:20s}  key={masked}  "
            f"{'  url=' + info['base_url'] if info.get('base_url') else ''}"
            f"{'  note=' + info['note'] if info.get('note') else ''}"
        )
    return "\n".join(lines)


async def handle_http_upload_file(**kwargs) -> str:
    """
    Upload un fichier local via HTTP multipart/form-data vers une URL.
    Utile pour uploader vers des APIs (stockage, traitement d'images, docs, etc.)
    - url : URL d'upload
    - file_path : chemin local du fichier
    - field_name : nom du champ form (défaut: 'file')
    - extra_fields : champs additionnels (dict)
    - auth_type / auth_value : même système que http_request
    - api_name : nom de l'API enregistrée
    """
    url = kwargs.get("url", "").strip()
    file_path = kwargs.get("file_path", "").strip()
    field_name = kwargs.get("field_name", "file").strip()
    extra_fields: dict = kwargs.get("extra_fields", {}) or {}
    auth_type = kwargs.get("auth_type", "none").strip().lower()
    auth_value = kwargs.get("auth_value", "").strip()
    api_name = kwargs.get("api_name", "").strip().lower()
    timeout = float(kwargs.get("timeout", 60))

    if not url or not file_path:
        return "url et file_path sont requis"

    if not Path(file_path).exists():
        return f"Fichier introuvable : {file_path}"

    if api_name and not auth_value:
        auth_value = _resolve_api_key(api_name)

    # Construire multipart manuellement (pas de dépendance externe)
    import uuid
    boundary = uuid.uuid4().hex
    file_data = Path(file_path).read_bytes()
    filename = Path(file_path).name
    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    parts = []
    # Champs additionnels
    for key, val in extra_fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{val}\r\n".encode("utf-8")
        )
    # Fichier
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
        + file_data
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))

    body_bytes = b"".join(p if isinstance(p, bytes) else p for p in parts)

    headers: dict = {
        "User-Agent": "Lumena-Agent/1.0",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body_bytes)),
    }
    if auth_type == "bearer" and auth_value:
        headers["Authorization"] = f"Bearer {auth_value}"
    elif auth_type == "api_key" and auth_value:
        headers["X-API-Key"] = auth_value

    try:
        start = time.monotonic()
        status, resp_headers, resp_body = await _do_request(
            method="POST", url=url, headers=headers, body=body_bytes,
            timeout=timeout, retries=1, verify_ssl=True,
        )
        elapsed = time.monotonic() - start
    except Exception as e:
        return f"✗ Erreur upload : {e}"

    resp_ct = resp_headers.get("Content-Type", "text/plain")
    parsed = _parse_response(resp_body, resp_ct)
    emoji = "✓" if 200 <= status < 300 else "✗"
    return (
        f"{emoji} Upload {filename} → {url}\n"
        f"   Status={status}  {elapsed*1000:.0f}ms  {len(file_data)} octets\n\n"
        f"{parsed}"
    )


async def handle_http_webhook_test(**kwargs) -> str:
    """
    Envoie un payload test vers un webhook (Discord, Slack, Make.com, Zapier, n8n...).
    - url : URL du webhook
    - payload : dict JSON à envoyer
    - platform : discord | slack | generic (adapte le format automatiquement)
    """
    url = kwargs.get("url", "").strip()
    payload: Any = kwargs.get("payload", {})
    platform = kwargs.get("platform", "generic").strip().lower()
    message = kwargs.get("message", "Test Lumena webhook ✓").strip()

    if not url:
        return "url requis"

    # Format automatique selon la plateforme
    if platform == "discord":
        body = payload or {"content": message, "username": "Lumena"}
    elif platform == "slack":
        body = payload or {"text": message}
    else:
        body = payload or {"message": message, "source": "Lumena", "ts": time.time()}

    return await handle_http_request(
        url=url,
        method="POST",
        body=body,
        content_type="application/json",
        timeout=15,
        retries=2,
    )
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
