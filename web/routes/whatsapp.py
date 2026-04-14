"""FastAPI routes pour le webhook WhatsApp (Meta Cloud API)."""

from fastapi import APIRouter, Request, Response, HTTPException, Query
from loguru import logger

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])


def _get_channel():
    from . import deps
    ch = getattr(deps, "whatsapp_channel", None)
    if not ch or not ch.is_running:
        return None
    return ch


@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
):
    """Vérification du webhook par Meta (GET avec challenge)."""
    ch = _get_channel()
    if not ch:
        raise HTTPException(status_code=503, detail="WhatsApp channel not running")

    result = ch.verify_webhook(hub_mode, hub_verify_token, hub_challenge)
    if result is not None:
        return Response(content=result, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive_webhook(request: Request):
    """Réception des messages entrants de Meta."""
    ch = _get_channel()
    if not ch:
        return {"status": "ok"}

    raw_body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    if ch.app_secret and not ch.validate_signature(raw_body, signature):
        logger.warning("WhatsApp webhook: invalid signature")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}

    import asyncio
    asyncio.create_task(ch.handle_webhook(body))

    return {"status": "ok"}


@router.get("/status")
async def whatsapp_status():
    """Status du channel WhatsApp (pour le dashboard)."""
    ch = _get_channel()
    if not ch:
        return {"enabled": False, "running": False, "state": "not_configured"}
    return ch.get_runtime_status()
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
