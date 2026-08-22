"""System routes — health, status, auth, trace, upload."""
import asyncio
import json
import os
import re as _re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from loguru import logger

from web.routes import deps
from web.routes.schemas import UndoEditsRequest, FileEditItem
from web.routes.lifespan import (
    _env_flag,
    _env_int,
    _env_float,
    _env_non_negative_int,
    _iso_to_timestamp,
    TASK_ORCHESTRATOR_V1_ENABLED,
    _get_autonomy_daemon_instance,
)

router = APIRouter()

# ── Paths ────────────────────────────────────────────────────────────────────
_web_dir = Path(__file__).resolve().parent.parent
from src.utils.paths import ROOT_DIR, RECEIVED_DOCS_DIR, JOURNAL_JSON, ALERTS_DIR, SCHEDULER_DIR
_PROJECT_ROOT = ROOT_DIR
_UPLOAD_DIR = RECEIVED_DOCS_DIR
_UPLOAD_MAX_SIZE = 20 * 1024 * 1024  # 20MB
_UPLOAD_ALLOWED_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".pdf", ".txt", ".md", ".py", ".js", ".ts", ".html",
    ".css", ".json", ".xml", ".csv", ".doc", ".docx", ".xlsx",
    ".pptx", ".rtf", ".odt", ".ods",
}

# ── Module-level SLO monitor cache ──────────────────────────────────────────
_SLO_MONITOR = None


# =====================================================================
# Helper functions (extracted from server.py L2008-2408)
# =====================================================================

def _preview_message(message: str, max_len: int = 100) -> str:
    text = (message or "").replace("\n", " ").replace("\r", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _default_llm_meta() -> Dict[str, Any]:
    return {
        "provider_requested": "unknown",
        "provider_used": "unknown",
        "model_requested": "unknown",
        "model_used": "unknown",
        "fallback_used": False,
        "fallback_reason": None,
        "continuation_used": False,
        "continuation_steps": 0,
        "finish_reason": None,
        "prompt_tokens": None,
        "completion_tokens": None,
    }


def _get_llm_meta() -> Dict[str, Any]:
    if not deps.lumena or not getattr(deps.lumena, "llm", None):
        return _default_llm_meta()
    llm = deps.lumena.llm
    if hasattr(llm, "get_last_response_meta"):
        try:
            meta = llm.get_last_response_meta() or {}
            base = _default_llm_meta()
            for key in base.keys():
                if key in meta:
                    base[key] = meta[key]
            return base
        except Exception:
            return _default_llm_meta()
    return _default_llm_meta()


def _default_agent_meta() -> Dict[str, Any]:
    return {
        "agent_output_incomplete": False,
        "agent_output_warning": None,
        "agent_repair_attempts": 0,
    }


def _get_agent_meta() -> Dict[str, Any]:
    if not deps.lumena:
        return _default_agent_meta()
    if hasattr(deps.lumena, "get_last_agent_meta"):
        try:
            meta = deps.lumena.get_last_agent_meta() or {}
            base = _default_agent_meta()
            for key in base.keys():
                if key in meta:
                    base[key] = meta[key]
            return base
        except Exception:
            return _default_agent_meta()
    return _default_agent_meta()


def _get_telegram_meta() -> Dict[str, Any]:
    if deps.telegram_channel and hasattr(deps.telegram_channel, "get_runtime_status"):
        try:
            status = deps.telegram_channel.get_runtime_status() or {}
            return {
                "telegram_enabled": bool(status.get("enabled", False)),
                "telegram_running": bool(status.get("running", False)),
                "telegram_conflict_seen": bool(status.get("conflict_seen", False)),
                "telegram_last_error": status.get("last_error"),
                "telegram_transient_error": bool(status.get("transient_error", False)),
                "telegram_transient_backoff_sec": float(status.get("transient_backoff_sec", 0.0)),
            }
        except Exception:
            pass

    # Fallback if channel is not initialized yet.
    telegram_disabled = _env_flag("LUMENA_DISABLE_TELEGRAM", False)
    has_token = bool(os.getenv("TELEGRAM_TOKEN"))
    return {
        "telegram_enabled": (not telegram_disabled) and has_token,
        "telegram_running": False,
        "telegram_conflict_seen": False,
        "telegram_last_error": "telegram disabled by env" if telegram_disabled else None,
        "telegram_transient_error": False,
        "telegram_transient_backoff_sec": 0.0,
    }


def _get_twitter_meta() -> Dict[str, Any]:
    if deps.twitter_channel and hasattr(deps.twitter_channel, "get_runtime_status"):
        try:
            status = deps.twitter_channel.get_runtime_status() or {}
            return {
                "twitter_enabled": bool(status.get("enabled", False)),
                "twitter_running": bool(status.get("running", False)),
                "twitter_handle": status.get("handle"),
                "twitter_last_error": status.get("last_error"),
            }
        except Exception:
            pass

    twitter_disabled = _env_flag("LUMENA_DISABLE_TWITTER", False)
    has_token = bool(os.getenv("TWITTER_BEARER_TOKEN") or os.getenv("TWITTER_API_KEY"))
    return {
        "twitter_enabled": (not twitter_disabled) and has_token,
        "twitter_running": bool(deps.twitter_channel and getattr(deps.twitter_channel, "is_running", False)),
        "twitter_handle": None,
        "twitter_last_error": "twitter disabled by env" if twitter_disabled else None,
    }


def _get_whatsapp_meta() -> Dict[str, Any]:
    if deps.whatsapp_channel and hasattr(deps.whatsapp_channel, "get_runtime_status"):
        try:
            status = deps.whatsapp_channel.get_runtime_status() or {}
            return {
                "whatsapp_enabled": bool(status.get("enabled", False)),
                "whatsapp_running": bool(status.get("running", False)),
                "whatsapp_state": status.get("state", "unknown"),
                "whatsapp_last_error": status.get("last_error"),
                "whatsapp_dedup_cache_size": int(status.get("dedup_cache_size", 0)),
            }
        except Exception:
            pass

    wa_disabled = _env_flag("LUMENA_DISABLE_WHATSAPP", False)
    has_token = bool(os.getenv("WHATSAPP_ACCESS_TOKEN"))
    has_phone = bool(os.getenv("WHATSAPP_PHONE_NUMBER_ID"))
    return {
        "whatsapp_enabled": (not wa_disabled) and has_token and has_phone,
        "whatsapp_running": False,
        "whatsapp_state": "disabled" if wa_disabled else "not_configured",
        "whatsapp_last_error": "whatsapp disabled by env" if wa_disabled else None,
        "whatsapp_dedup_cache_size": 0,
    }



def _get_trace_meta() -> Dict[str, Any]:
    if not deps.TELEMETRY_AVAILABLE:
        return {
            "trace_enabled": False,
            "trace_buffer_size": 0,
            "trace_events_in_buffer": 0,
            "trace_stream_clients": 0,
        }
    try:
        bus = deps.get_trace_bus()
        stats = bus.get_stats()
        return {
            "trace_enabled": bool(stats.get("trace_enabled", False)),
            "trace_buffer_size": int(stats.get("trace_buffer_size", 0)),
            "trace_events_in_buffer": int(stats.get("trace_events_in_buffer", 0)),
            "trace_stream_clients": int(stats.get("trace_stream_clients", 0)),
        }
    except Exception:
        return {
            "trace_enabled": False,
            "trace_buffer_size": 0,
            "trace_events_in_buffer": 0,
            "trace_stream_clients": 0,
        }


def _get_skills_meta() -> Dict[str, Any]:
    if not deps.lumena:
        return {
            "skills_loaded": 0,
            "skills_last_active": [],
            "skills_auto_activation": False,
        }

    skills_loaded = 0
    try:
        runtime_skills = getattr(deps.lumena, "_skills", {}) or {}
        if isinstance(runtime_skills, dict):
            skills_loaded = len(runtime_skills)
    except Exception:
        skills_loaded = 0

    skills_last_active: List[str] = []
    try:
        if hasattr(deps.lumena, "get_last_active_skills"):
            skills_last_active = list(deps.lumena.get_last_active_skills() or [])
    except Exception:
        skills_last_active = []

    return {
        "skills_loaded": skills_loaded,
        "skills_last_active": skills_last_active,
        "skills_auto_activation": bool(getattr(deps.lumena, "skills_auto_activation", False)),
    }


def _task_orchestrator_enabled() -> bool:
    return bool(TASK_ORCHESTRATOR_V1_ENABLED and deps._TASK_ORCHESTRATOR is not None)


def _get_task_meta() -> Dict[str, Any]:
    if not _task_orchestrator_enabled():
        return {
            "tasks_enabled": False,
            "tasks_total": 0,
            "tasks_backlog": 0,
            "tasks_waiting_io": 0,
            "tasks_done": 0,
            "tasks_failed": 0,
            "tasks_cancelled": 0,
            "tasks_conversations": 0,
        }
    try:
        stats = deps._TASK_ORCHESTRATOR.stats()  # type: ignore[union-attr]
        return {
            "tasks_enabled": True,
            "tasks_total": int(stats.get("total_tasks", 0)),
            "tasks_backlog": int(stats.get("backlog_tasks", 0)),
            "tasks_waiting_io": int(stats.get("waiting_io_tasks", 0)),
            "tasks_done": int(stats.get("done_tasks", 0)),
            "tasks_failed": int(stats.get("failed_tasks", 0)),
            "tasks_cancelled": int(stats.get("cancelled_tasks", 0)),
            "tasks_conversations": int(stats.get("active_conversations", 0)),
        }
    except Exception:
        return {
            "tasks_enabled": True,
            "tasks_total": 0,
            "tasks_backlog": 0,
            "tasks_waiting_io": 0,
            "tasks_done": 0,
            "tasks_failed": 0,
            "tasks_cancelled": 0,
            "tasks_conversations": 0,
        }


def _get_pipeline_meta() -> Dict[str, Any]:
    with deps._PIPELINE_METRICS_LOCK:
        payload = dict(deps._PIPELINE_METRICS)
    return {
        "pipeline_chat_requests_total": int(payload.get("chat_requests_total", 0)),
        "pipeline_chat_success_total": int(payload.get("chat_success_total", 0)),
        "pipeline_stream_requests_total": int(payload.get("stream_requests_total", 0)),
        "pipeline_stream_success_total": int(payload.get("stream_success_total", 0)),
        "pipeline_errors_total": int(payload.get("pipeline_errors_total", 0)),
        "pipeline_timeouts_total": int(payload.get("pipeline_timeouts_total", 0)),
        "pipeline_cancelled_total": int(payload.get("pipeline_cancelled_total", 0)),
        "pipeline_last_event": payload.get("last_event"),
        "pipeline_last_event_ts": payload.get("last_event_ts"),
        "pipeline_last_error": payload.get("last_error"),
        "pipeline_last_error_ts": payload.get("last_error_ts"),
    }


def _cleanup_session_state_locked() -> None:
    ttl_sec = _env_int("LUMENA_SESSION_STATE_TTL_SEC", 172800, minimum=300)
    max_items = _env_int("LUMENA_SESSION_STATE_MAX_ITEMS", 2048, minimum=64)
    now_ts = time.time()

    stale_keys: List[str] = []
    for key, payload in deps._SESSION_STATE.items():
        updated_raw = payload.get("updated_at") or payload.get("created_at")
        updated_ts = _iso_to_timestamp(updated_raw)
        if updated_ts <= 0:
            continue
        if (now_ts - updated_ts) > float(ttl_sec):
            stale_keys.append(key)
    for key in stale_keys:
        deps._SESSION_STATE.pop(key, None)

    if len(deps._SESSION_STATE) <= max_items:
        return

    ordered_keys = sorted(
        deps._SESSION_STATE.keys(),
        key=lambda key: _iso_to_timestamp(
            str(
                (deps._SESSION_STATE.get(key) or {}).get("updated_at")
                or (deps._SESSION_STATE.get(key) or {}).get("created_at")
                or ""
            )
        ),
    )
    for key in ordered_keys[: max(0, len(deps._SESSION_STATE) - max_items)]:
        deps._SESSION_STATE.pop(key, None)


def _get_session_meta() -> Dict[str, Any]:
    with deps._SESSION_STATE_LOCK:
        _cleanup_session_state_locked()
        total = len(deps._SESSION_STATE)
        active = sum(
            1
            for payload in deps._SESSION_STATE.values()
            if str(payload.get("status") or "").strip().lower() in {"running", "waiting_io", "checkpointed"}
        )
    return {
        "sessions_total": total,
        "sessions_active": active,
    }


def _get_slo_monitor():
    global _SLO_MONITOR
    if _SLO_MONITOR is not None:
        return _SLO_MONITOR
    if not (deps.RUNTIME_AVAILABLE and deps.SLOMonitor is not None):
        return None
    try:
        _SLO_MONITOR = deps.SLOMonitor(
            window_size=_env_int("LUMENA_SLO_WINDOW_SIZE", 300, minimum=20),
            alert_consecutive=_env_int("LUMENA_SLO_ALERT_CONSECUTIVE", 3, minimum=1),
            success_rate_min=_env_float("LUMENA_SLO_SUCCESS_RATE_MIN", 0.92, minimum=0.0),
            timeout_rate_max=_env_float("LUMENA_SLO_TIMEOUT_RATE_MAX", 0.02, minimum=0.0),
            latency_median_max_ms=_env_int("LUMENA_SLO_LATENCY_MEDIAN_MAX_MS", 8000, minimum=1),
            latency_p95_max_ms=_env_int("LUMENA_SLO_LATENCY_P95_MAX_MS", 35000, minimum=1),
            workspace_errors_max=_env_non_negative_int("LUMENA_SLO_WORKSPACE_ERRORS_MAX", 0),
            undo_success_rate_min=_env_float("LUMENA_SLO_UNDO_SUCCESS_RATE_MIN", 1.0, minimum=0.0),
        )
    except Exception:
        _SLO_MONITOR = None
    return _SLO_MONITOR


def _request_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    return "timeout" in str(exc or "").lower()


def _is_workspace_resolution_error(ide_context: Dict[str, Any]) -> bool:
    workspace_path = str(ide_context.get("workspace_path") or "").strip()
    resolved_date = str(ide_context.get("resolved_date") or "").strip()
    policy = str(ide_context.get("workspace_policy") or "default").strip().lower()
    if not workspace_path or not resolved_date:
        return True
    if policy in {"default", "strict_default"}:
        normalized = workspace_path.replace("/", "\\").rstrip("\\")
        return not normalized.endswith(resolved_date)
    return False


def _record_slo_outcome(
    *,
    success: bool,
    latency_ms: int,
    timeout_unrecovered: bool,
    resumed: bool,
    workspace_error: bool,
    undo_success: Optional[bool],
) -> None:
    monitor = _get_slo_monitor()
    if monitor is None:
        return
    try:
        payload = monitor.record(
            success=success,
            latency_ms=latency_ms,
            timeout_unrecovered=timeout_unrecovered,
            resumed=resumed,
            workspace_error=workspace_error,
            undo_success=undo_success,
        )
        triggered = list(payload.get("triggered_alerts") or [])
        for alert in triggered:
            logger.warning(
                "[SLO] alert metric={} streak={} samples={}",
                alert.get("metric"),
                alert.get("streak"),
                alert.get("samples"),
            )
    except Exception:
        pass


def _get_slo_meta() -> Dict[str, Any]:
    monitor = _get_slo_monitor()
    if monitor is None:
        return {
            "slo_enabled": False,
            "slo_window_size": 0,
            "slo_samples": 0,
            "slo_success_rate": 0.0,
            "slo_timeout_unrecovered_rate": 0.0,
            "slo_latency_median_ms": 0,
            "slo_latency_p95_ms": 0,
            "slo_workspace_error_count": 0,
            "slo_undo_success_rate": None,
            "slo_breaches": [],
            "slo_breach_streaks": {},
            "slo_alerts_recent": [],
        }
    try:
        snapshot = monitor.snapshot()
        return {
            "slo_enabled": True,
            "slo_window_size": int(snapshot.get("window_size", 0)),
            "slo_samples": int(snapshot.get("samples", 0)),
            "slo_success_rate": float(snapshot.get("success_rate", 0.0)),
            "slo_timeout_unrecovered_rate": float(snapshot.get("timeout_unrecovered_rate", 0.0)),
            "slo_latency_median_ms": int(snapshot.get("latency_median_ms", 0)),
            "slo_latency_p95_ms": int(snapshot.get("latency_p95_ms", 0)),
            "slo_workspace_error_count": int(snapshot.get("workspace_error_count", 0)),
            "slo_undo_success_rate": snapshot.get("undo_success_rate"),
            "slo_breaches": list(snapshot.get("breaches") or []),
            "slo_breach_streaks": dict(snapshot.get("breach_streaks") or {}),
            "slo_alerts_recent": list(snapshot.get("alerts_recent") or []),
        }
    except Exception:
        return {
            "slo_enabled": True,
            "slo_window_size": 0,
            "slo_samples": 0,
            "slo_success_rate": 0.0,
            "slo_timeout_unrecovered_rate": 0.0,
            "slo_latency_median_ms": 0,
            "slo_latency_p95_ms": 0,
            "slo_workspace_error_count": 0,
            "slo_undo_success_rate": None,
            "slo_breaches": [],
            "slo_breach_streaks": {},
            "slo_alerts_recent": [],
        }


def _get_autonomy_meta() -> Dict[str, Any]:
    base = {
        "autonomy_enabled_on_web": bool(getattr(deps, "AUTONOMY_ON_WEB_ENABLED", False)),
        "autonomy_available": bool(deps.AUTONOMY_DAEMON_AVAILABLE),
        "autonomy_running": False,
        "autonomy_action_execution": False,
        "autonomy_actions_last_hour": 0,
        "autonomy_user_present": False,
        "autonomy_uptime": None,
        "autonomy_last_error": getattr(deps, "_AUTONOMY_LAST_ERROR", None),
    }
    daemon = _get_autonomy_daemon_instance()
    if daemon is None:
        return base
    try:
        status = daemon.get_status() or {}
        base.update(
            {
                "autonomy_running": bool(status.get("running", False)),
                "autonomy_action_execution": bool(status.get("autonomy_action_execution", False)),
                "autonomy_actions_last_hour": int(status.get("actions_last_hour", 0)),
                "autonomy_user_present": bool(status.get("user_present", False)),
                "autonomy_uptime": status.get("uptime"),
            }
        )
    except Exception as e:
        base["autonomy_last_error"] = str(e)
    return base


def _file_cards_enabled() -> bool:
    return _env_flag("LUMENA_CHAT_FILE_CARDS", True)


def _safe_file_edits_for_trace(trace_id: Optional[str], *, consume: bool) -> tuple[List[Dict[str, Any]], Optional[str], bool]:
    if not _file_cards_enabled():
        return [], None, False
    if not deps.TELEMETRY_AVAILABLE:
        return [], None, False
    if not trace_id:
        return [], None, False

    try:
        store = deps.get_file_edits_store()
        if consume:
            raw_items = store.consume_session_edits(trace_id)
        else:
            raw_items = store.peek_session_edits(trace_id, after_index=0)

        items: List[Dict[str, Any]] = []
        for raw in raw_items:
            try:
                model = FileEditItem(**raw)
                if hasattr(model, "model_dump"):
                    items.append(model.model_dump())
                else:
                    items.append(model.dict())
            except Exception:
                continue

        session_id = store.get_session_id_for_trace(trace_id)
        undo_available = store.has_undo_for_trace(trace_id)
        return items, session_id, undo_available
    except Exception:
        return [], None, False


# =====================================================================
# Route handlers
# =====================================================================

@router.get("/api/auth/config")
async def get_auth_config(request: Request):
    """Retourne le token admin uniquement depuis localhost."""
    client_host = request.client.host if request.client else None
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Localhost only")
    return {"admin_token": os.getenv("LUMENA_ADMIN_TOKEN", "")}


@router.get("/")
async def root():
    """Page d'accueil."""
    return FileResponse(_web_dir / "index.html")


@router.get("/api/health")
async def health():
    """Lightweight health probe — returns 200 instantly."""
    return {"status": "ok"}


@router.post("/api/shutdown")
async def shutdown_server(request: Request, _auth=Depends(deps.verify_admin_token)):
    """Graceful shutdown — localhost only."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(status_code=403, detail="Shutdown uniquement depuis localhost.")

    import subprocess
    import threading
    import time

    pid = os.getpid()

    def _do_shutdown():
        # Laisser la réponse HTTP partir
        time.sleep(0.8)

        # Libérer le lock avant de tuer le processus (taskkill /F bypasse le lifespan)
        try:
            if deps.instance_lock:
                deps.instance_lock.release()
                deps.instance_lock = None
                print("[LOCK] Instance lock released (pre-shutdown)")
        except Exception:
            pass

        if sys.platform == "win32":
            # Windows : on lance un processus DÉTACHÉ (hors de notre arbre)
            # qui fait taskkill /F /T sur notre PID.
            # /F = force, /T = tree → tue Python + Stripe CLI + n8n + Node
            # + tous les sous-processus. La console se ferme quand plus rien n'y tourne.
            # DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP = le killer
            # survit à notre mort et n'est PAS dans notre arbre de processus.
            try:
                subprocess.Popen(
                    f'taskkill /F /T /PID {pid}',
                    shell=True,
                    creationflags=(
                        subprocess.DETACHED_PROCESS
                        | subprocess.CREATE_NEW_PROCESS_GROUP
                        | subprocess.CREATE_NO_WINDOW
                    ),
                )
            except Exception:
                pass
            # taskkill /T nous tue aussi, mais au cas où :
            time.sleep(3.0)
            os._exit(0)
        else:
            # Linux/Mac : SIGTERM → uvicorn graceful → lifespan finally
            import signal
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
            time.sleep(6.0)
            os._exit(0)

    t = threading.Thread(target=_do_shutdown, daemon=False)
    t.start()

    return {"status": "shutting_down", "message": "Lumena s'arrête..."}


@router.get("/api/preflight", dependencies=[Depends(deps.verify_admin_token)])
async def preflight(request: Request):
    """Diagnostic système complet (wizard premier lancement). Localhost only."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(
            status_code=403,
            detail="Preflight uniquement accessible depuis localhost.",
        )
    import asyncio
    from src.utils.health_check import HealthChecker
    checker = HealthChecker()
    result = await asyncio.to_thread(checker.check_all)
    return result.to_dict()


@router.get("/api/system/reliability")
async def system_reliability():
    """Métriques runtime de fiabilité (routing, policy, stickiness, outils).

    Lecture seule, non authentifiée : consommé par le HUD / monitoring.
    """
    try:
        from src.utils.reliability_metrics import get_metrics
        return get_metrics().snapshot()
    except Exception as e:
        logger.warning("[reliability] snapshot erreur: {}", e)
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.get(
    "/api/runtime/audit",
    dependencies=[Depends(deps.verify_admin_token)],
)
async def runtime_audit(
    format: str = "summary",
    tool: Optional[str] = None,
    drift_only: bool = False,
):
    """Audit runtime du ToolRegistry — détection de drift contractuel.

    Lecture seule stricte : aucune mutation, aucun side effect, aucun
    handler exécuté. Voir src/runtime/drift_checker.py pour les garanties.

    Query params:
        format: "summary" (défaut) ou "full".
                summary → réponse sans champ `tools`.
                full → inclut la liste complète des outils audités.
        tool: nom d'un outil pour filtrer (uniquement avec format=full).
        drift_only: si True, ne retourne que les outils en drift (uniquement
                    avec format=full).

    Returns:
        JSON conforme à AuditSummary ou AuditFullReport selon format.
    """
    from dataclasses import asdict
    from src.runtime.drift_checker import (
        audit_registry,
        filter_by_tool_name,
        filter_drift_only,
        to_summary,
    )

    if format not in ("summary", "full"):
        return JSONResponse(
            status_code=400,
            content={"error": f"unknown format '{format}', expected 'summary' or 'full'"},
        )

    # Cascade pour récupérer le ToolRegistry runtime réel :
    #   1. deps.lumena._tool_registry  (si attaché directement au core)
    #   2. deps.lumena.tool_system._tool_registry  (via ToolSystem facade)
    #   3. fallback : nouvelle instance ToolRegistry(lumena=deps.lumena)
    # L'audit reste read-only sur n'importe lequel (drift_checker garanti).
    registry = None
    try:
        lumena_core = deps.lumena
        registry = getattr(lumena_core, "_tool_registry", None)
        if registry is None:
            tool_system = getattr(lumena_core, "tool_system", None)
            if tool_system is not None:
                registry = getattr(tool_system, "_tool_registry", None)
        if registry is None:
            from src.reasoning.tool_registry import ToolRegistry
            registry = ToolRegistry(lumena=lumena_core)
    except Exception as e:
        logger.warning("[runtime_audit] resolve registry erreur: {}", e)
        return JSONResponse(
            status_code=500,
            content={"error": f"registry resolve failed: {e}"},
        )

    full = audit_registry(registry)

    if format == "full":
        if drift_only:
            full = filter_drift_only(full)
        if tool:
            full = filter_by_tool_name(full, tool)
        return asdict(full)

    # format=summary : pas de champ `tools` dans la réponse
    summary = to_summary(full)
    return asdict(summary)


@router.get("/api/status", dependencies=[Depends(deps.verify_admin_token)])
async def get_status():
    """Retourne le status de Lumena."""
    telegram_meta = _get_telegram_meta()
    twitter_meta = _get_twitter_meta()
    whatsapp_meta = _get_whatsapp_meta()
    trace_meta = _get_trace_meta()
    autonomy_meta = _get_autonomy_meta()
    skills_meta = _get_skills_meta()
    task_meta = _get_task_meta()
    pipeline_meta = _get_pipeline_meta()
    session_meta = _get_session_meta()
    slo_meta = _get_slo_meta()

    if not deps.lumena:
        return {
            "status": "not_initialized",
            "status_source": "degraded",
            "status_poll_recommended_ms": 12000,
            "instance_id": deps.INSTANCE_ID,
            "modules": {},
            "active_modules": 0,
            "total_modules": 0,
            "server_time": datetime.now(timezone.utc).isoformat(),
            **telegram_meta,
            **twitter_meta,
            **whatsapp_meta,
            **trace_meta,
            **autonomy_meta,
            **skills_meta,
            **task_meta,
            **pipeline_meta,
            **session_meta,
            **slo_meta,
        }

    # Stats des modules
    modules = {
        "repo_map": deps.lumena.repo_map is not None,
        "code_index": deps.lumena.code_index is not None,
        "rules_loader": deps.lumena.rules_loader is not None,
        "hook_system": deps.lumena.hook_system is not None,
        "instinct_system": deps.lumena.instinct_system is not None,
        "emotion_manager": deps.lumena.emotion_manager is not None,
        "memory": deps.lumena.memory is not None,
    }

    # Stats supplementaires
    stats = {
        "status": "ok",
        "instance_id": deps.INSTANCE_ID,
        "modules": modules,
        "active_modules": sum(1 for v in modules.values() if v),
        "total_modules": len(modules),
        "server_time": datetime.now(timezone.utc).isoformat(),
        **telegram_meta,
        **twitter_meta,
        **whatsapp_meta,
        **trace_meta,
        **autonomy_meta,
        **skills_meta,
        **task_meta,
        **pipeline_meta,
        **session_meta,
        **slo_meta,
    }
    stats["status_source"] = (
        "live" if stats["active_modules"] == stats["total_modules"] else "degraded"
    )
    stats["status_poll_recommended_ms"] = 3000 if stats["status_source"] == "live" else 12000

    # Mood via get_stats()
    if deps.lumena.emotion_manager:
        em_stats = deps.lumena.emotion_manager.get_stats()
        stats["mood"] = em_stats.get("mood", "neutral")
        stats["energy"] = em_stats.get("energy", "medium")

    # Memory
    if deps.lumena.memory:
        mem_stats = deps.lumena.memory.get_stats()
        stats["memory_count"] = mem_stats.get("count", mem_stats.get("total_memories", 0))

    # RepoMap
    if deps.lumena.repo_map:
        repo_stats = deps.lumena.repo_map.get_stats()
        stats["files_count"] = repo_stats.total_files
        stats["symbols_count"] = repo_stats.total_symbols

    # Instincts
    if deps.lumena.instinct_system:
        inst_stats = deps.lumena.instinct_system.get_stats()
        stats["instincts_count"] = inst_stats["total_instincts"]

    # Tool count
    _ts = getattr(deps.lumena, "tool_system", None)
    if _ts:
        try:
            stats["tool_count"] = _ts.tool_count
        except Exception:
            pass

    # Journal total (lightweight: count entries without parsing content)
    try:
        if JOURNAL_JSON.exists():
            _jdata = json.loads(JOURNAL_JSON.read_text(encoding="utf-8", errors="replace"))
            stats["journal_total"] = len(_jdata) if isinstance(_jdata, list) else 0
    except Exception:
        pass

    # Scheduler tasks count (active = not cancelled)
    try:
        _conv_path = SCHEDULER_DIR / "conversation_tasks.json"
        if _conv_path.exists():
            _tdata = json.loads(_conv_path.read_text(encoding="utf-8", errors="replace"))
            _raw = _tdata.get("tasks", {}) if isinstance(_tdata, dict) else {}
            stats["scheduler_tasks_active"] = sum(
                1 for t in _raw.values()
                if isinstance(t, dict) and not t.get("cancelled_at")
            )
    except Exception:
        pass

    # Alerts count
    try:
        _alert_path = ALERTS_DIR / "critical_alerts.log"
        _alert_count = 0
        if _alert_path.exists():
            _alert_count = sum(1 for ln in _alert_path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip())
        stats["alerts_total"] = _alert_count
    except Exception:
        pass

    return stats


@router.get("/api/trace/recent")
async def get_trace_recent(limit: int = 200, token: Optional[str] = None, _auth=Depends(deps.verify_admin_token)):
    """Retourne les evenements de trace recents."""
    bounded_limit = max(1, min(int(limit), 1000))
    if not deps.TELEMETRY_AVAILABLE:
        return {"events": [], "count": 0, "limit": bounded_limit}
    bus = deps.get_trace_bus()
    events = bus.recent(bounded_limit)
    return {"events": events, "count": len(events), "limit": bounded_limit}


@router.get("/api/trace/stream")
async def trace_stream(token: Optional[str] = None, _auth=Depends(deps.verify_admin_token)):
    """Flux SSE des evenements de trace en temps reel."""
    if not deps.TELEMETRY_AVAILABLE:
        async def disabled():
            payload = {"type": "error", "message": "trace disabled"}
            yield f"event: trace\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        return StreamingResponse(
            disabled(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    bus = deps.get_trace_bus()

    async def generate():
        subscriber_id = None
        queue = None
        heartbeat_sec = max(3, int(bus.get_stats().get("trace_heartbeat_sec", 10)))
        try:
            subscriber_id, queue = bus.subscribe(max_queue=300)
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat_sec)
                    yield f"event: trace\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    heartbeat = {"type": "heartbeat", "ts": datetime.now(timezone.utc).isoformat()}
                    yield f"event: heartbeat\ndata: {json.dumps(heartbeat, ensure_ascii=False)}\n\n"
        finally:
            if subscriber_id:
                bus.unsubscribe(subscriber_id)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/edits/undo")
async def undo_edits(request: UndoEditsRequest, _auth=Depends(deps.verify_admin_token)):
    """Annule les modifications d'une session d'edition (ou d'un fichier)."""
    if not _file_cards_enabled():
        raise HTTPException(status_code=503, detail="file cards disabled")
    if not deps.TELEMETRY_AVAILABLE:
        raise HTTPException(status_code=503, detail="telemetry unavailable")

    try:
        store = deps.get_file_edits_store()
        if request.file_path:
            result = store.undo_file(request.session_id, request.file_path)
        else:
            result = store.undo_session(request.session_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not result.get("success", False):
        return JSONResponse(status_code=400, content=result)
    return JSONResponse(content=result)


@router.post("/api/upload")
async def upload_files(files: List[UploadFile] = File(...), _auth=Depends(deps.verify_admin_token)):
    """Upload files (images, documents) to attach to chat messages."""
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for file in files:
        if not file.filename:
            continue
        # Sanitize filename
        safe_name = _re.sub(r'[^\w\-.]', '_', file.filename)
        ext = '.' + safe_name.rsplit('.', 1)[-1].lower() if '.' in safe_name else ''
        if ext not in _UPLOAD_ALLOWED_EXTS:
            results.append({"name": file.filename, "error": "type non autorise"})
            continue
        # Read and check size
        content = await file.read()
        if len(content) > _UPLOAD_MAX_SIZE:
            results.append({"name": file.filename, "error": "trop gros (max 20MB)"})
            continue
        # Save with unique prefix
        unique_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
        dest = _UPLOAD_DIR / unique_name
        dest.write_bytes(content)
        uploaded = {
            "name": file.filename,
            "url": f"/api/uploads/{unique_name}",
            "path": str(dest),
            "size": len(content),
            "type": file.content_type or "",
        }
        try:
            from src.documents.studio import get_document_studio

            record, duplicate = get_document_studio().importer.import_file(
                dest,
                source_kind="chat_upload",
                source_uri=f"/api/uploads/{unique_name}",
                metadata={"original_filename": file.filename},
            )
            uploaded["document_id"] = record.id
            uploaded["document_duplicate"] = duplicate
        except Exception as exc:
            # The historical chat upload remains available even when a file is not a
            # supported/safe document. Indexing is an additive best-effort mirror.
            logger.debug("[document-studio] chat upload not indexed: {}", exc)
        results.append(uploaded)
    return {"files": results}


@router.get("/api/uploads/{filename}")
async def serve_uploaded_file(filename: str, _auth=Depends(deps.verify_admin_token)):
    """Serve an uploaded file."""
    # Sanitize: only allow safe chars
    if not _re.match(r'^[a-f0-9]{8}_[\w\-. ]+$', filename):
        raise HTTPException(status_code=400, detail="invalid filename")
    path = _UPLOAD_DIR / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    # Ensure path doesn't escape the upload dir (path traversal protection)
    try:
        path.resolve().relative_to(_UPLOAD_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="access denied")
    return FileResponse(path)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
