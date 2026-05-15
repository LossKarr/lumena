"""Routes émotionnelles — état PAD, historique, override."""
import asyncio
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel

from web.routes.deps import verify_admin_token

router = APIRouter()

# ── WebSocket subscribers ────────────────────────────────────────────────────

_mood_subscribers: list[asyncio.Queue] = []


async def broadcast_mood_change(mood: str, pad: tuple) -> None:
    """Diffuse un changement d'humeur à tous les abonnés WebSocket."""
    payload = {"type": "mood_change", "mood": mood, "pad": list(pad)}
    dead = []
    for q in _mood_subscribers:
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        try:
            _mood_subscribers.remove(q)
        except ValueError:
            pass


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_emotion_manager():
    """Retourne l'instance EmotionManager ou None."""
    try:
        from src.emotion import get_emotion_manager
        return get_emotion_manager()
    except Exception:
        return None


# ── REST endpoints ───────────────────────────────────────────────────────────

@router.get("/api/emotion")
async def get_emotion_state():
    """Retourne l'état émotionnel courant (PAD + compteurs)."""
    mgr = _get_emotion_manager()
    if mgr is None:
        raise HTTPException(status_code=503, detail="EmotionManager indisponible")
    return JSONResponse(mgr.get_stats())


@router.get("/api/emotion/history")
async def get_emotion_history(limit: int = 100, offset: int = 0):
    """Retourne les dernières entrées JSONL de l'historique."""
    try:
        import src.utils.paths as _paths
        hist_file = _paths.EMOTION_HISTORY_FILE
        if not hist_file.exists():
            return JSONResponse({"entries": [], "total": 0})
        lines = hist_file.read_text(encoding="utf-8").splitlines()
        total = len(lines)
        slice_ = lines[max(0, total - limit - offset): total - offset if offset else None]
        entries = []
        for line in reversed(slice_):
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return JSONResponse({"entries": entries, "total": total})
    except Exception as e:
        logger.warning(f"emotion history read: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class MoodOverride(BaseModel):
    mood: str


@router.post("/api/emotion/mood", dependencies=[Depends(verify_admin_token)])
async def override_mood(body: MoodOverride):
    """Force l'humeur via admin (override)."""
    mgr = _get_emotion_manager()
    if mgr is None:
        raise HTTPException(status_code=503, detail="EmotionManager indisponible")
    try:
        from src.emotion import Mood
        mood_enum = Mood(body.mood)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Humeur inconnue: {body.mood}")
    msg = mgr.force_mood(mood_enum)
    mgr._save_state()
    mgr._append_history("admin_override")
    return JSONResponse({"ok": True, "message": msg, "mood": body.mood})


# ── WebSocket ────────────────────────────────────────────────────────────────

@router.websocket("/ws/emotion")
async def ws_emotion(websocket: WebSocket):
    """WebSocket push — reçoit les changements d'humeur en temps réel."""
    await websocket.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _mood_subscribers.append(q)

    # Enregistrer un callback sur l'EmotionManager si pas déjà fait
    mgr = _get_emotion_manager()
    if mgr is not None and broadcast_mood_change not in mgr._mood_change_callbacks:
        # Wrapper sync → async (callback est appelé depuis code sync)
        def _sync_cb(mood: str, pad: tuple):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(broadcast_mood_change(mood, pad))
            except Exception:
                pass
        mgr._mood_change_callbacks.append(_sync_cb)

    try:
        # Envoyer l'état courant immédiatement
        if mgr is not None:
            await websocket.send_json({
                "type": "state",
                **mgr.get_stats(),
            })
        while True:
            data = await q.get()
            await websocket.send_json(data)
    except (WebSocketDisconnect, Exception):
        try:
            _mood_subscribers.remove(q)
        except ValueError:
            pass
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
