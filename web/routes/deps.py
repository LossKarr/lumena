"""
Shared dependencies and global state for Lumena web routes.

All module-level globals that were in server.py live here so that
every route module (and lifespan) can import them by reference.
"""
import os
import uuid
import json
import threading
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import Header, HTTPException

# ── Runtime availability flags (set at import time) ──
try:
    from src.runtime import (
        ChannelEnvelope,
        RuntimeContext,
        SLOMonitor,
        SessionStore,
        TaskOrchestrator,
        pop_runtime_context,
        push_runtime_context,
        resolve_workspace_for_request,
        resolve_workspace_for_user,
    )
    RUNTIME_AVAILABLE = True
except Exception:
    RUNTIME_AVAILABLE = False
    ChannelEnvelope = None
    RuntimeContext = None
    SLOMonitor = None
    SessionStore = None
    TaskOrchestrator = None
    pop_runtime_context = None
    push_runtime_context = None
    resolve_workspace_for_request = None
    resolve_workspace_for_user = None

try:
    from src.autonomy.daemon import get_daemon
    AUTONOMY_DAEMON_AVAILABLE = True
except Exception:
    AUTONOMY_DAEMON_AVAILABLE = False
    get_daemon = None

try:
    from src.voice.manager import VoiceManager
except ImportError:
    VoiceManager = None

try:
    from src.telemetry import (
        get_trace_bus,
        publish_trace,
        push_trace_context,
        pop_trace_context,
        current_trace_context,
        get_file_edits_store,
    )
    TELEMETRY_AVAILABLE = True
except Exception:
    TELEMETRY_AVAILABLE = False

from src.core import LumenaCore
from src.utils.file_lock import ProcessFileLock, default_lock_path

# ── Global state (mutated by lifespan, read by routes) ──
lumena: Optional[LumenaCore] = None
setup_only_mode: bool = False  # P0: True when server boots without LLM (first launch, no .env)
telegram_channel = None
telegram_task = None
discord_channel_bot = None
discord_task = None
twitter_channel = None
twitter_task = None
whatsapp_channel = None
whatsapp_task = None
INSTANCE_ID = uuid.uuid4().hex
instance_lock: Optional[ProcessFileLock] = None

_IDE_CONTEXT_CACHE_LOCK = threading.Lock()
_IDE_CONTEXT_CACHE: Dict[str, Dict[str, Any]] = {}
_CONVERSATION_CACHE_LOCK = threading.Lock()
_CONVERSATION_CACHE: Dict[str, Dict[str, Any]] = {}
_SESSION_STATE_LOCK = threading.Lock()
_SESSION_STATE: Dict[str, Dict[str, Any]] = {}
_PIPELINE_METRICS_LOCK = threading.Lock()
_PIPELINE_METRICS: Dict[str, Any] = {
    "chat_requests_total": 0,
    "chat_success_total": 0,
    "stream_requests_total": 0,
    "stream_success_total": 0,
    "pipeline_errors_total": 0,
    "pipeline_timeouts_total": 0,
    "pipeline_cancelled_total": 0,
    "last_event": None,
    "last_event_ts": None,
    "last_error": None,
    "last_error_ts": None,
}
_TASK_ORCHESTRATOR = TaskOrchestrator() if (RUNTIME_AVAILABLE and TaskOrchestrator is not None) else None
_SESSION_STORE = SessionStore() if (RUNTIME_AVAILABLE and SessionStore is not None) else None
_SLO_MONITOR = None
_AUTONOMY_DAEMON = None
_AUTONOMY_STARTED_BY_WEB = False
_AUTONOMY_LAST_ERROR: Optional[str] = None
_TG_MODE_STATE_LOCK = threading.Lock()
_TG_MODE_STATE: Dict[str, str] = {}
_TG_MODE_STATE_LOADED = False
from src.utils.paths import TG_MODE_STATE_JSON
_TG_MODE_STATE_FILE = TG_MODE_STATE_JSON

# ── Security: Admin token ──
# NOTE: do NOT cache _ADMIN_TOKEN at module level — the wizard writes it
# to .env + os.environ AFTER the server is already running. Re-read every request.


async def verify_admin_token(
    authorization: Optional[str] = Header(None),
):
    """Verifie le token admin sur les routes sensibles.

    Re-reads LUMENA_ADMIN_TOKEN from os.environ on every call so that the token
    written by the setup wizard takes effect without a server restart.
    """
    admin_token = os.getenv("LUMENA_ADMIN_TOKEN", "")
    if not admin_token:
        # P0.3.1: fail-closed — si le token n'est pas configuré après le setup, on refuse
        setup_done = os.getenv("LUMENA_SETUP_COMPLETE", "") == "1"
        if setup_done and not setup_only_mode:
            raise HTTPException(
                status_code=401,
                detail="LUMENA_ADMIN_TOKEN non configuré. Relancez le setup.",
            )
        return  # setup pas terminé → accès libre pour le wizard
    # P0.12: In recovery mode (setup_only), bypass auth to allow re-setup
    if setup_only_mode:
        return
    candidate = (authorization or "").replace("Bearer ", "").strip()
    if not candidate:
        raise HTTPException(status_code=401, detail="Authorization header required")
    if candidate != admin_token:
        raise HTTPException(status_code=403, detail="Invalid admin token")


def get_lumena() -> Optional[LumenaCore]:
    return lumena


def get_task_orchestrator():
    return _TASK_ORCHESTRATOR


def get_session_store():
    return _SESSION_STORE
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
