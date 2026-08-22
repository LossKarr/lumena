"""Chat routes — /api/chat and /api/chat/stream with full pipeline."""
import asyncio
import hashlib
import inspect
import json
import os
import time
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

from src.llm.codex_chat import (
    pop_codex_chat_delta_sink,
    push_codex_chat_delta_sink,
)
from src.llm.execution_router import (
    consume_codex_response_meta,
    reset_codex_response_meta,
)
from web.routes import deps
from web.routes.schemas import ChatRequest, ChatResponse
from web.routes.lifespan import (
    _env_flag,
    _env_int,
    _env_float,
    _env_non_negative_int,
    _iso_to_timestamp,
    _notify_autonomy_user_interaction,
    DEFAULT_WORKSPACE_PATH,
    RUNTIME_CONTEXT_V2_ENABLED,
    WORKSPACE_POLICY_V2_ENABLED,
    TASK_ORCHESTRATOR_V1_ENABLED,
    STREAM_EVENT_V2_ENABLED,
    OMNICHANNEL_ENVELOPE_V1_ENABLED,
)
from web.routes.system import (
    _get_llm_meta,
    _get_agent_meta,
    _default_agent_meta,
    _safe_file_edits_for_trace,
    _record_slo_outcome,
    _request_timeout_error,
    _is_workspace_resolution_error,
    _file_cards_enabled,
)

router = APIRouter()


class WorkspacePolicyError(RuntimeError):
    """Erreur explicite de resolution workspace pour les routes chat."""

    def __init__(self, detail: str, *, status_code: int = 409):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


# =====================================================================
# Pipeline helper functions (extracted from server.py L1216-2013)
# =====================================================================

def _normalize_channel(channel: Optional[str]) -> str:
    value = (channel or "web").strip().lower()
    allowed = {"web", "ide", "telegram", "discord", "api", "whatsapp"}
    return value if value in allowed else "web"


def _session_user_id(request: ChatRequest) -> str:
    return (request.user_id or "local:owner").strip() or "local:owner"


def _session_common_meta(request: ChatRequest, mode: str) -> Dict[str, Any]:
    return {
        "mode": mode,
        "use_agent": bool(request.use_agent),
        "attachments_count": len(request.attachments or []),
        "owner_user_id": request.owner_user_id,
        "user_role": request.user_role,
        "profile_id": request.profile_id,
        "client_instance_id": request.client_instance_id,
    }


def _session_file_edit_summary(file_edits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for edit in (file_edits or [])[:50]:
        out.append(
            {
                "action": edit.get("action"),
                "file_path": edit.get("workspace_relative") or edit.get("file_path"),
                "additions": edit.get("additions", 0),
                "deletions": edit.get("deletions", 0),
                "summary": edit.get("summary", ""),
            }
        )
    return out


def _record_session_user_message(
    request: ChatRequest,
    *,
    envelope: Dict[str, Any],
    channel: str,
    client_name: str,
    mode: str,
    task_id: Optional[str],
    trace_id: Optional[str],
    ide_context: Dict[str, Any],
) -> None:
    store = getattr(deps, "_SESSION_STORE", None)
    conv_id = str(envelope.get("conversation_id") or "").strip()
    if store is None or not conv_id:
        return
    try:
        store.record_message(
            conversation_id=conv_id,
            role="user",
            content=request.message or "",
            channel=channel,
            client=client_name,
            user_id=_session_user_id(request),
            owner_user_id=request.owner_user_id,
            profile_id=request.profile_id,
            request_id=envelope.get("request_id"),
            message_id=envelope.get("message_id"),
            task_id=task_id,
            trace_id=trace_id,
            workspace_path=ide_context.get("workspace_path"),
            status="running",
            metadata={
                **_session_common_meta(request, mode),
                "message_source": "api_chat_stream" if mode == "agent" else "api_chat",
            },
        )
        store.record_event(
            conversation_id=conv_id,
            event_type="request_started",
            status="running",
            summary=request.message,
            channel=channel,
            client=client_name,
            user_id=_session_user_id(request),
            request_id=envelope.get("request_id"),
            task_id=task_id,
            trace_id=trace_id,
            workspace_path=ide_context.get("workspace_path"),
            metadata=_session_common_meta(request, mode),
        )
    except Exception as exc:
        logger.warning("session_store: failed to record user message: {}", exc)


def _record_session_assistant_message(
    request: ChatRequest,
    *,
    envelope: Dict[str, Any],
    channel: str,
    client_name: str,
    mode: str,
    task_id: Optional[str],
    trace_id: Optional[str],
    ide_context: Dict[str, Any],
    response: str,
    llm_meta: Dict[str, Any],
    agent_meta: Dict[str, Any],
    file_edits: List[Dict[str, Any]],
    edit_session_id: Optional[str],
    created_documents: Optional[List[Dict[str, Any]]] = None,
) -> None:
    store = getattr(deps, "_SESSION_STORE", None)
    conv_id = str(envelope.get("conversation_id") or "").strip()
    if store is None or not conv_id:
        return
    try:
        meta = {
            **_session_common_meta(request, mode),
            **dict(llm_meta or {}),
            **dict(agent_meta or {}),
            "edit_session_id": edit_session_id,
            "file_edits": _session_file_edit_summary(file_edits),
            "created_documents": [
                {"name": item.get("name"), "path": item.get("path"), "type": item.get("type")}
                for item in (created_documents or [])[:50]
            ],
        }
        store.record_message(
            conversation_id=conv_id,
            role="assistant",
            content=response or "",
            channel=channel,
            client=client_name,
            user_id=_session_user_id(request),
            owner_user_id=request.owner_user_id,
            profile_id=request.profile_id,
            request_id=envelope.get("request_id"),
            task_id=task_id,
            trace_id=trace_id,
            model_used=llm_meta.get("model_used"),
            provider_used=llm_meta.get("provider_used"),
            workspace_path=ide_context.get("workspace_path"),
            status="done",
            metadata=meta,
        )
        store.record_event(
            conversation_id=conv_id,
            event_type="response_sent",
            status="done",
            summary=response,
            channel=channel,
            client=client_name,
            user_id=_session_user_id(request),
            request_id=envelope.get("request_id"),
            task_id=task_id,
            trace_id=trace_id,
            workspace_path=ide_context.get("workspace_path"),
            metadata=meta,
        )
    except Exception as exc:
        logger.warning("session_store: failed to record assistant message: {}", exc)


def _record_session_error(
    request: ChatRequest,
    *,
    envelope: Dict[str, Any],
    channel: str,
    client_name: str,
    mode: str,
    task_id: Optional[str],
    trace_id: Optional[str],
    ide_context: Dict[str, Any],
    status: str,
    error: Any,
) -> None:
    store = getattr(deps, "_SESSION_STORE", None)
    conv_id = str(envelope.get("conversation_id") or "").strip()
    if store is None or not conv_id:
        return
    try:
        store.record_event(
            conversation_id=conv_id,
            event_type="request_failed" if status != "cancelled" else "request_cancelled",
            status=status,
            summary=str(error),
            channel=channel,
            client=client_name,
            user_id=_session_user_id(request),
            request_id=envelope.get("request_id"),
            task_id=task_id,
            trace_id=trace_id,
            workspace_path=ide_context.get("workspace_path"),
            metadata={**_session_common_meta(request, mode), "error": str(error)[:1000]},
        )
        store.update_status(
            conv_id,
            status,
            channel=channel,
            client=client_name,
            user_id=_session_user_id(request),
            task_id=task_id,
            trace_id=trace_id,
            workspace_path=ide_context.get("workspace_path"),
        )
    except Exception as exc:
        logger.warning("session_store: failed to record error: {}", exc)


def _normalize_existing_dir(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        p = Path(path).expanduser().resolve()
    except Exception:
        return None
    return str(p) if p.exists() and p.is_dir() else None


def _normalize_existing_file(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        p = Path(path).expanduser().resolve()
    except Exception:
        return None
    return str(p) if p.exists() and p.is_file() else None


def _normalize_existing_parent_dir(path: Optional[str]) -> Optional[str]:
    """Return the nearest existing parent directory for a path hint."""
    if not path:
        return None
    try:
        p = Path(path).expanduser().resolve()
    except Exception:
        return None

    current = p if p.is_dir() else p.parent
    for _ in range(8):
        if current.exists() and current.is_dir():
            return str(current)
        if current.parent == current:
            break
        current = current.parent
    return None


def _infer_workspace_from_file_path(file_path: Optional[str]) -> Optional[str]:
    """Infer a project root from a concrete file path."""
    normalized = _normalize_existing_file(file_path)
    if not normalized:
        return None

    markers = (
        ".git",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "composer.json",
    )
    current = Path(normalized).parent
    for _ in range(10):
        if any((current / marker).exists() for marker in markers):
            return str(current)
        if current.parent == current:
            break
        current = current.parent
    return str(Path(normalized).parent)


def _combine_open_files(current: List[str], cached: List[str], limit: int = 30) -> List[str]:
    seen = set()
    combined: List[str] = []
    for item in list(current) + list(cached):
        value = str(item or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        combined.append(value)
        if len(combined) >= limit:
            break
    return combined


def _infer_workspace_path(
    *,
    workspace_path: Optional[str],
    workspace_hint_parent: Optional[str],
    active_file_path: Optional[str],
    open_files: List[str],
) -> Optional[str]:
    if workspace_path:
        return workspace_path
    if active_file_path:
        inferred = _infer_workspace_from_file_path(active_file_path)
        if inferred:
            return inferred
    for candidate in open_files:
        inferred = _infer_workspace_from_file_path(candidate)
        if inferred:
            return inferred
    return workspace_hint_parent


def _build_ide_context_cache_key(request: ChatRequest) -> str:
    channel = _normalize_channel(request.channel)
    if channel != "ide":
        return ""
    if request.ide_session_id:
        return f"ide:session:{request.ide_session_id.strip().lower()}"
    if request.client:
        return f"ide:client:{request.client.strip().lower()}"
    return "ide:default"


def _cleanup_ide_context_cache_locked() -> None:
    ttl_sec = _env_int("LUMENA_IDE_CONTEXT_TTL_SEC", 7200, minimum=60)
    max_items = _env_int("LUMENA_IDE_CONTEXT_MAX_ITEMS", 256, minimum=16)
    now = time.time()

    stale_keys = [
        key
        for key, payload in deps._IDE_CONTEXT_CACHE.items()
        if (now - float(payload.get("updated_at", 0.0))) > float(ttl_sec)
    ]
    for key in stale_keys:
        deps._IDE_CONTEXT_CACHE.pop(key, None)

    if len(deps._IDE_CONTEXT_CACHE) <= max_items:
        return

    ordered_keys = sorted(
        deps._IDE_CONTEXT_CACHE.keys(),
        key=lambda key: float(deps._IDE_CONTEXT_CACHE[key].get("updated_at", 0.0)),
    )
    for key in ordered_keys[: max(0, len(deps._IDE_CONTEXT_CACHE) - max_items)]:
        deps._IDE_CONTEXT_CACHE.pop(key, None)


def _load_cached_ide_context(cache_key: str) -> Dict[str, Any]:
    if not cache_key:
        return {}
    with deps._IDE_CONTEXT_CACHE_LOCK:
        _cleanup_ide_context_cache_locked()
        payload = deps._IDE_CONTEXT_CACHE.get(cache_key) or {}
        context = payload.get("context") or {}
        if not isinstance(context, dict):
            return {}
        return {
            "workspace_path": context.get("workspace_path"),
            "active_file_path": context.get("active_file_path"),
            "open_files": list(context.get("open_files") or []),
        }


def _store_cached_ide_context(cache_key: str, context: Dict[str, Any]) -> None:
    if not cache_key:
        return
    meaningful = bool(
        context.get("workspace_path") or context.get("active_file_path") or (context.get("open_files") or [])
    )
    if not meaningful:
        return
    with deps._IDE_CONTEXT_CACHE_LOCK:
        _cleanup_ide_context_cache_locked()
        deps._IDE_CONTEXT_CACHE[cache_key] = {
            "updated_at": time.time(),
            "context": {
                "workspace_path": context.get("workspace_path"),
                "active_file_path": context.get("active_file_path"),
                "open_files": list(context.get("open_files") or []),
            },
        }


def _cleanup_conversation_cache_locked() -> None:
    ttl_sec = _env_int("LUMENA_CONVERSATION_TTL_SEC", 86400, minimum=300)
    max_items = _env_int("LUMENA_CONVERSATION_MAX_ITEMS", 1024, minimum=64)
    now = time.time()

    stale_keys = [
        key
        for key, payload in deps._CONVERSATION_CACHE.items()
        if (now - float(payload.get("updated_at", 0.0))) > float(ttl_sec)
    ]
    for key in stale_keys:
        deps._CONVERSATION_CACHE.pop(key, None)

    if len(deps._CONVERSATION_CACHE) <= max_items:
        return

    ordered_keys = sorted(
        deps._CONVERSATION_CACHE.keys(),
        key=lambda key: float(deps._CONVERSATION_CACHE[key].get("updated_at", 0.0)),
    )
    for key in ordered_keys[: max(0, len(deps._CONVERSATION_CACHE) - max_items)]:
        deps._CONVERSATION_CACHE.pop(key, None)


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


def _build_conversation_cache_key(request: ChatRequest, channel: str, client_name: str) -> str:
    uid = (getattr(request, "user_id", None) or "local:owner").strip() or "local:owner"
    if request.ide_session_id:
        return f"conv:ide:{request.ide_session_id.strip().lower()}:{uid}"
    normalized_client = (client_name or "").strip().lower()
    if normalized_client and normalized_client != "unknown":
        return f"conv:client:{normalized_client}:{uid}"
    return f"conv:channel:{channel}:{uid}"


def _load_cached_conversation_id(cache_key: str) -> Optional[str]:
    if not cache_key:
        return None
    with deps._CONVERSATION_CACHE_LOCK:
        _cleanup_conversation_cache_locked()
        payload = deps._CONVERSATION_CACHE.get(cache_key) or {}
        conversation_id = str(payload.get("conversation_id") or "").strip()
        return conversation_id or None


def _store_cached_conversation_id(cache_key: str, conversation_id: Optional[str]) -> None:
    conv_id = str(conversation_id or "").strip()
    if not cache_key or not conv_id:
        return
    with deps._CONVERSATION_CACHE_LOCK:
        _cleanup_conversation_cache_locked()
        deps._CONVERSATION_CACHE[cache_key] = {
            "updated_at": time.time(),
            "conversation_id": conv_id,
        }


def _conversation_id_from_task(task_id: Optional[str]) -> Optional[str]:
    tid = str(task_id or "").strip()
    if not tid or not _task_orchestrator_enabled():
        return None
    try:
        task = deps._TASK_ORCHESTRATOR.get_task(tid)  # type: ignore[union-attr]
        if not task:
            return None
        conv_id = str(task.get("conversation_id") or "").strip()
        return conv_id or None
    except Exception:
        return None


def _trim_session_events(events: List[Dict[str, Any]], limit: int = 40) -> List[Dict[str, Any]]:
    bounded = max(5, int(limit))
    if len(events) <= bounded:
        return events
    return events[-bounded:]


def _update_session_state(
    conversation_id: Optional[str],
    *,
    channel: str,
    client: str,
    request_id: Optional[str],
    task_id: Optional[str],
    trace_id: Optional[str],
    workspace_path: Optional[str],
    resolved_date: Optional[str],
    status: str,
    error: Optional[str] = None,
) -> None:
    conv_id = str(conversation_id or "").strip()
    if not conv_id:
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    event = {
        "ts": now_iso,
        "channel": channel,
        "client": client,
        "request_id": request_id,
        "task_id": task_id,
        "trace_id": trace_id,
        "status": status,
        "workspace_path": workspace_path,
        "resolved_date": resolved_date,
    }
    if error:
        event["error"] = str(error)[:500]

    with deps._SESSION_STATE_LOCK:
        _cleanup_session_state_locked()
        current = deps._SESSION_STATE.get(conv_id) or {
            "conversation_id": conv_id,
            "created_at": now_iso,
            "updated_at": now_iso,
            "last_channel": channel,
            "last_client": client,
            "last_request_id": request_id,
            "last_task_id": task_id,
            "last_trace_id": trace_id,
            "resolved_workspace": workspace_path,
            "resolved_date": resolved_date,
            "status": status,
            "last_error": None,
            "events": [],
        }
        current["updated_at"] = now_iso
        current["last_channel"] = channel
        current["last_client"] = client
        current["last_request_id"] = request_id
        current["last_task_id"] = task_id
        current["last_trace_id"] = trace_id
        current["resolved_workspace"] = workspace_path
        current["resolved_date"] = resolved_date
        current["status"] = status
        if error:
            current["last_error"] = str(error)[:500]
        events = list(current.get("events") or [])
        events.append(event)
        current["events"] = _trim_session_events(events)
        deps._SESSION_STATE[conv_id] = current


def _get_session_state(conversation_id: str) -> Optional[Dict[str, Any]]:
    conv_id = str(conversation_id or "").strip()
    if not conv_id:
        return None
    with deps._SESSION_STATE_LOCK:
        _cleanup_session_state_locked()
        payload = deps._SESSION_STATE.get(conv_id)
        if not payload:
            return None
        return dict(payload)


def _extract_ide_context(request: ChatRequest, channel: str) -> Dict[str, Any]:
    if channel != "ide":
        return {}

    workspace_path = _normalize_existing_dir(request.workspace_path)
    active_file_path = _normalize_existing_file(request.active_file_path)
    open_files = []
    for item in (request.open_files or [])[:30]:
        normalized = _normalize_existing_file(item)
        if normalized:
            open_files.append(normalized)

    # Signaux bruts uniquement — pas d'inférence ici.
    # resolve_workspace_for_user() (via _apply_workspace_policy) est la seule
    # source de vérité pour la résolution finale du workspace.
    incoming = {
        "workspace_path": workspace_path,
        "active_file_path": active_file_path,
        "open_files": open_files,
    }

    cache_key = _build_ide_context_cache_key(request)
    cached = _load_cached_ide_context(cache_key)
    merged = {
        "workspace_path": incoming.get("workspace_path") or cached.get("workspace_path"),
        "active_file_path": incoming.get("active_file_path") or cached.get("active_file_path"),
        "open_files": _combine_open_files(
            incoming.get("open_files") or [],
            cached.get("open_files") or [],
        ),
    }

    _store_cached_ide_context(cache_key, merged)
    return {
        "workspace_path": merged.get("workspace_path"),
        "active_file_path": merged.get("active_file_path"),
        "open_files": merged.get("open_files") or [],
    }


def _stream_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    if STREAM_EVENT_V2_ENABLED:
        merged = {"schema_version": 2}
        merged.update(payload)
        return merged
    return payload


def _stream_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize stream payload while keeping backward compatibility."""
    normalized = dict(payload or {})
    if STREAM_EVENT_V2_ENABLED:
        original_type = str(normalized.get("type") or "").strip()
        mapped_type = {
            "thought": "thinking",
            "action": "thinking",
            "observation": "thinking",
            "tool": "tool_call",
        }.get(original_type, original_type)
        if mapped_type and mapped_type != original_type:
            normalized["type"] = mapped_type
            normalized.setdefault("event_subtype", original_type)
    return _stream_event(normalized)


def _build_channel_envelope(request: ChatRequest, channel: str) -> Dict[str, Any]:
    if not (OMNICHANNEL_ENVELOPE_V1_ENABLED and deps.RUNTIME_AVAILABLE and deps.ChannelEnvelope is not None):
        envelope = {
            "channel": channel,
            "client": (request.client or "unknown").strip() or "unknown",
            "request_id": (request.request_id or "").strip() or None,
            "conversation_id": (request.conversation_id or "").strip() or None,
            "message_id": (request.message_id or "").strip() or None,
            "task_id": (request.task_id or "").strip() or None,
            "client_caps": dict(request.client_caps or {}),
        }
    else:
        envelope_model = deps.ChannelEnvelope.from_request(
            channel=channel,
            client=request.client,
            request_id=request.request_id,
            conversation_id=request.conversation_id,
            message_id=request.message_id,
            task_id=request.task_id,
            client_caps=request.client_caps,
        )
        envelope = envelope_model.to_dict()

    client_name = (envelope.get("client") or "unknown").strip() or "unknown"
    cache_key = _build_conversation_cache_key(request, channel, client_name)

    explicit_conv = str(request.conversation_id or "").strip() or None
    task_conv = _conversation_id_from_task(envelope.get("task_id"))
    cached_conv = _load_cached_conversation_id(cache_key)

    if explicit_conv:
        envelope["conversation_id"] = explicit_conv
    elif task_conv:
        envelope["conversation_id"] = task_conv
    elif cached_conv:
        envelope["conversation_id"] = cached_conv

    _store_cached_conversation_id(cache_key, envelope.get("conversation_id"))
    return envelope


def _extract_dated_workspace(msg: str, default_workspace: str) -> Optional[str]:
    """Extrait une date du message utilisateur et résout le workspace correspondant.

    Patterns reconnus :
    - "du 26/04", "le 26/04/2026", "du 26-04"
    - "hier", "avant-hier", "avant hier"
    - "d'hier", "yesterday"

    Retourne le chemin du workspace daté s'il existe, sinon None.
    """
    import re as _re
    from datetime import date, timedelta
    from pathlib import Path as _P

    today = date.today()
    target_date: Optional[date] = None

    # Pattern 1: "du DD/MM" ou "du DD/MM/YYYY" ou "le DD/MM"
    _date_match = _re.search(
        r"(?:du|le|de|from)\s+(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?",
        msg,
    )
    if _date_match:
        _d, _m = int(_date_match.group(1)), int(_date_match.group(2))
        _y_raw = _date_match.group(3)
        _y = int(_y_raw) if _y_raw else today.year
        if _y < 100:
            _y += 2000
        try:
            target_date = date(_y, _m, _d)
        except ValueError:
            pass

    # Pattern 2: "avant-hier" / "avant hier" (AVANT "hier" pour éviter le faux match)
    if target_date is None and _re.search(r"avant[- ]hier", msg):
        target_date = today - timedelta(days=2)

    # Pattern 3: "hier" / "d'hier" / "yesterday"
    if target_date is None and _re.search(r"\bhier\b|yesterday", msg):
        target_date = today - timedelta(days=1)

    if target_date is None or target_date == today:
        return None

    # Construire le chemin workspace de cette date.
    # DEFAULT_WORKSPACE_PATH = workspace/ (racine), les projets sont dans workspace/YYYY-MM-DD/
    _ws_base = _P(default_workspace)
    _dated_dir = _ws_base / target_date.strftime("%Y-%m-%d")
    if not _dated_dir.is_dir():
        return None

    return str(_dated_dir)


def _apply_workspace_policy(
    request: ChatRequest,
    channel: str,
    ide_context: Dict[str, Any],
) -> Dict[str, Any]:
    # P1 — le chemin V2 est la seule vérité. Si le runtime n'est pas disponible, on fail explicitement.
    if not (WORKSPACE_POLICY_V2_ENABLED and deps.RUNTIME_AVAILABLE and deps.resolve_workspace_for_user is not None):
        logger.error(
            "[workspace] Runtime V2 indisponible (V2_ENABLED={} RUNTIME={} resolver={}) — résolution workspace impossible.",
            WORKSPACE_POLICY_V2_ENABLED,
            deps.RUNTIME_AVAILABLE,
            deps.resolve_workspace_for_user is not None,
        )
        raise WorkspacePolicyError(
            "workspace_runtime_unavailable: le runtime V2 n'est pas initialisé. "
            "Vérifiez LUMENA_RUNTIME_CONTEXT_V2 et LUMENA_WORKSPACE_POLICY_V2.",
            status_code=503,
        )

    user_id = (getattr(request, "user_id", None) or "local:owner").strip() or "local:owner"

    # Workspace base par utilisateur (pour extraction de date et résolution)
    from src.runtime.user_profile import MULTI_USER_ENABLED as _MU_WS
    if _MU_WS:
        from src.runtime.user_profile import _safe_user_id as _safe_uid_ws
        from src.utils.paths import DATA_DIR as _DATA_DIR_WS
        _user_ws_base = str(_DATA_DIR_WS / "users" / _safe_uid_ws(user_id) / "workspaces")
    else:
        _user_ws_base = DEFAULT_WORKSPACE_PATH

    # ── Chemin V2 — resolve_workspace_for_user est la SEULE source de vérité ──
    # Aucun appel à _infer_workspace_path() autorisé ici.
    requested_workspace = (request.workspace_path or "").strip() or ide_context.get("workspace_path")

    # ── DATE EXTRACTION: si le message mentionne une date explicite (ex: "du 26/04",
    # "hier", "d'avant-hier"), chercher le projet dans le workspace de cette date
    # au lieu du workspace du jour. Cela évite le FALLBACK quand l'utilisateur
    # parle d'un projet créé à une date antérieure.
    if not requested_workspace:
        _msg_lower = (request.message or "").lower()
        _extracted_ws = _extract_dated_workspace(_msg_lower, _user_ws_base)
        if _extracted_ws:
            requested_workspace = _extracted_ws
            logger.info(
                "[workspace] date extraite du message → {}",
                _extracted_ws[:120],
            )

    # Si aucun workspace demandé, tenter le projet actif récent du canal avant le fallback date.
    if not requested_workspace:
        try:
            import os as _os_wp
            _lum_wp = deps.lumena
            _id_svc_wp = getattr(_lum_wp, "_identity_svc", None) if _lum_wp else None
            if _id_svc_wp is not None:
                from src.core_services.identity_service import IdentityService as _IDS_WP
                _conv_id = getattr(request, "conversation_id", None) or "default"
                _ck_wp = f"{channel}:{_conv_id}"
                _rpc_wp = _id_svc_wp.get_recent_code_context(_ck_wp)
                if not _rpc_wp:
                    _rpc_wp = _id_svc_wp.get_recent_code_context(f"{channel}:default")
                if _rpc_wp:
                    _rpc_path_wp = _rpc_wp.get("workspace_path", "")
                    if _rpc_path_wp and _os_wp.path.isdir(_rpc_path_wp):
                        requested_workspace = _rpc_path_wp
                        logger.debug(
                            "[workspace] recent_context → {} (canal={}, conv={})",
                            _rpc_path_wp[:80], channel, _conv_id,
                        )
        except Exception as _wp_exc:
            logger.debug("[workspace] recent_context lookup échoué: {}", _wp_exc)
    active_file_path = (
        ide_context.get("active_file_path")
        or _normalize_existing_file(request.active_file_path)
    )
    incoming_open_files: List[str] = []
    for item in (request.open_files or [])[:30]:
        normalized = _normalize_existing_file(item)
        if normalized:
            incoming_open_files.append(normalized)
    open_files = _combine_open_files(
        ide_context.get("open_files") or [],
        incoming_open_files,
    )

    resolved = deps.resolve_workspace_for_user(
        user_id,
        workspace_policy=request.workspace_policy,
        requested_workspace=requested_workspace,
        active_file_path=active_file_path,
        open_files=open_files,
    )

    logger.debug(
        "[workspace] V2 -> {} (reason={}, fallback={})",
        resolved.resolved_workspace, resolved.resolution_reason, resolved.used_fallback,
    )

    # 4.3: Signal d'observabilité — fallback sur requête qui ressemble à du code
    if resolved.used_fallback:
        _q_lower = (request.message or "").lower()
        _CODE_KW_FALLBACK = (
            "corrige", "fix", "bug", "code", "jeu", "game", "script", "fichier",
            "write_file", "edit_file", "projet", "project", "refais", "delegate",
            "compile", "python", "javascript", "html", "css", "app", "site",
        )
        if any(k in _q_lower for k in _CODE_KW_FALLBACK):
            logger.warning(
                "[workspace] FALLBACK sur requête code — canal={} requête={!r}",
                channel, _q_lower[:120],
            )

    # P1 strict : policy=explicit avec workspace invalide → refus, pas de fallback silencieux
    if resolved.used_fallback and resolved.workspace_policy == "explicit":
        logger.warning(
            "[workspace] P1 refus strict — policy=explicit mais workspace introuvable: {!r}",
            requested_workspace,
        )
        raise WorkspacePolicyError(
            f"workspace_explicit_invalid: le workspace demandé '{requested_workspace}' "
            "n'existe pas ou n'est pas un dossier valide. "
            "Envoyez un chemin existant ou utilisez policy=default.",
            status_code=422,
        )

    return {
        "workspace_path": resolved.resolved_workspace,
        "active_file_path": active_file_path,
        "open_files": open_files,
        "resolved_date": resolved.resolved_date,
        "resolution_reason": resolved.resolution_reason,
        "workspace_policy": resolved.workspace_policy,
        "workspace_used_fallback": bool(resolved.used_fallback),
        "channel": channel,
    }


def _build_runtime_context(
    request: ChatRequest,
    channel: str,
    envelope: Dict[str, Any],
    ide_context: Dict[str, Any],
) -> Optional[Any]:
    if not (RUNTIME_CONTEXT_V2_ENABLED and deps.RUNTIME_AVAILABLE and deps.RuntimeContext is not None):
        return None
    return deps.RuntimeContext.build(
        channel=channel,
        client=envelope.get("client"),
        request_id=envelope.get("request_id"),
        conversation_id=envelope.get("conversation_id"),
        message_id=envelope.get("message_id"),
        workspace_policy=ide_context.get("workspace_policy") or request.workspace_policy,
        task_id=envelope.get("task_id"),
        client_caps=envelope.get("client_caps") or {},
        workspace_path=request.workspace_path,
        active_file_path=ide_context.get("active_file_path"),
        open_files=ide_context.get("open_files") or [],
        resolved_workspace=ide_context.get("workspace_path"),
        resolved_date=ide_context.get("resolved_date"),
        resolution_reason=ide_context.get("resolution_reason"),
        # Phase 0 — propagation identité
        user_id=request.user_id,
        owner_user_id=request.owner_user_id,
        user_role=request.user_role,
        profile_id=request.profile_id,
        instance_id=request.client_instance_id,
        mode="agent" if request.use_agent else "chat",
    )


def _push_request_runtime_context(runtime_context: Optional[Any]) -> Optional[Any]:
    if runtime_context is None:
        return None
    if not callable(deps.push_runtime_context):
        return None
    try:
        return deps.push_runtime_context(runtime_context)
    except Exception:
        return None


def _pop_request_runtime_context(token: Optional[Any]) -> None:
    if token is None:
        return
    if not callable(deps.pop_runtime_context):
        return
    try:
        deps.pop_runtime_context(token)
    except Exception:
        pass


def _task_orchestrator_enabled() -> bool:
    return bool(TASK_ORCHESTRATOR_V1_ENABLED and deps._TASK_ORCHESTRATOR is not None)


# ── Attachment content extraction ────────────────────────────────────────────
_TEXT_EXTS = {".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".json", ".xml", ".csv", ".log", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh", ".bat", ".ps1", ".sql", ".env"}
_MAX_INLINE_CHARS = 30_000


def _extract_file_content(file_path: str, content_type: str) -> Optional[str]:
    """Extract text content from an uploaded file for LLM injection."""
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return None
    ext = p.suffix.lower()
    # Text-based files
    if ext in _TEXT_EXTS or (content_type and content_type.startswith("text/")):
        try:
            raw = p.read_text(encoding="utf-8", errors="replace")
            if len(raw) > _MAX_INLINE_CHARS:
                raw = raw[:_MAX_INLINE_CHARS] + f"\n... [tronqué — {len(raw)} chars total]"
            return raw
        except Exception:
            return None
    # PDF
    if ext == ".pdf" or content_type == "application/pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(p))
            pages = []
            total = 0
            for page in reader.pages:
                text = (page.extract_text() or "").strip()
                if text:
                    pages.append(text)
                    total += len(text)
                    if total > _MAX_INLINE_CHARS:
                        pages.append(f"... [tronqué — {len(reader.pages)} pages total]")
                        break
            return "\n\n---\n\n".join(pages) if pages else None
        except Exception as e:
            logger.debug("[attachment] PDF extraction failed for {}: {}", p.name, e)
            return None
    # Reuse the existing bounded reader for modern Office/OpenDocument files.
    if ext in {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".rtf"}:
        try:
            from src.perception.document_reader import DocumentReader

            chunks = DocumentReader().read(p)
            raw = "\n\n".join(chunk.content for chunk in chunks if chunk.content)
            if len(raw) > _MAX_INLINE_CHARS:
                raw = raw[:_MAX_INLINE_CHARS] + f"\n... [tronqué — {len(raw)} chars total]"
            return raw or None
        except Exception as e:
            logger.debug("[attachment] document extraction failed for {}: {}", p.name, e)
            return None
    # Legacy binary .doc stays path-only: no deterministic parser is available.
    if ext == ".doc":
        return None
    return None


def _build_effective_message(message: str, attachments: Optional[List[Dict[str, Any]]]) -> str:
    """Build message with injected file attachment context."""
    if not attachments:
        return message
    att_sections = []
    for att in attachments:
        att_path = att.get("path", "")
        att_name = att.get("name", "fichier")
        att_type = att.get("type", "")
        if not att_path:
            # Fallback: try to resolve from url
            url = att.get("url", "")
            if url.startswith("/api/uploads/"):
                from src.utils.paths import RECEIVED_DOCS_DIR
                fname = url.split("/")[-1]
                candidate = RECEIVED_DOCS_DIR / fname
                if candidate.exists():
                    att_path = str(candidate)
        if not att_path:
            continue
        content = _extract_file_content(att_path, att_type)
        if content:
            att_sections.append(
                f"[Fichier joint: {att_name} ({att_type})]\n"
                f"--- CONTENU DU FICHIER {att_name} ---\n{content}\n--- FIN DU FICHIER ---"
            )
        else:
            att_sections.append(
                f"[Fichier joint: {att_name} ({att_type}) — chemin: {att_path}]"
            )
    if not att_sections:
        return message
    return "\n\n".join(att_sections) + "\n\n" + message


def _inject_mission_reminders(message: str) -> str:
    """Polish web — préfixe au message un rappel des missions inter-Lumena terminées.

    Une mission confiée à un autre Lumena depuis le web ne peut pas être notifiée
    par push : au prochain message, A le signale ici (« au fait, B a fini X »).
    Les rappels présentés sont acquittés. Jamais bloquant.
    """
    try:
        from src.runtime.peer_mission_tracker import (
            pending_web_reminders, ack_web_reminders, list_pending,
        )
        done = pending_web_reminders()
        # P1 — missions ENCORE EN COURS (web) : on informe sans acquitter, pour
        # qu'un « alors ? » lise le contexte au lieu de re-déléguer / recréer.
        running = [m for m in list_pending() if (m.get("channel") == "web")]
        if not done and not running:
            return message

        lines, ids = [], []
        for m in done[:5]:
            ids.append(m.get("task_id"))
            obj = (m.get("objective") or "").strip()[:120]
            peer = m.get("peer_name") or "un autre Lumena"
            if m.get("status") == "completed":
                res = (m.get("result") or "").strip()[:400]
                dest = (m.get("artifacts_dir") or "").strip()
                where = f" Fichiers reçus dans : {dest}." if dest else ""
                lines.append(f"- ✅ « {obj} » ({peer}) : TERMINÉ.{where} Résultat : {res}")
            else:
                # Statut terminal non-« completed » (refused, failed, timeout…) :
                # message RICHE et actionnable (pourquoi + comment débloquer + qui),
                # pas juste le mot brut. Sinon A doit DEVINER la raison du refus.
                from src.runtime.peer_mission_tracker import _build_completion_text
                lines.append("- " + _build_completion_text(m))
        for m in running[:5]:
            obj = (m.get("objective") or "").strip()[:120]
            peer = m.get("peer_name") or "un autre Lumena"
            # W2 — vrai dossier de réception (pas le placeholder « <pair> »).
            try:
                from src.runtime.peer_artifacts import reception_dir_for
                where = reception_dir_for(m.get("peer_name") or peer).name
            except Exception:
                where = "recu-de-<pair>"
            lines.append(
                f"- ⏳ « {obj} » ({peer}) : ENCORE EN COURS chez le pair. "
                "NE relance PAS, NE recrée PAS toi-même : les fichiers arriveront seuls "
                f"dans workspace/{where}/. Dis simplement que c'est en cours."
            )
        note = (
            "[NOTE SYSTÈME — état des missions confiées à d'autres Lumena. Mentionne-le "
            "naturellement et brièvement au début de ta réponse, avant de traiter sa demande. "
            "Ne re-délègue pas et ne refais pas une mission déjà confiée :]\n"
            + "\n".join(lines) + "\n---\n"
        )
        ack_web_reminders([i for i in ids if i])  # n'acquitte QUE les terminées
        return note + message
    except Exception:
        return message


def _request_task_cancel(task_id: Optional[str], reason: str) -> None:
    tid = str(task_id or "").strip()
    if not tid or not _task_orchestrator_enabled():
        return
    try:
        deps._TASK_ORCHESTRATOR.cancel_task(tid)  # type: ignore[union-attr]
        logger.warning("[task] cancel requested task={} reason={}", tid, reason)
    except Exception as e:
        logger.debug("cancel_task skipped task={} err={}", tid, e)


def _classify_error_kind(exc_or_message: Any) -> str:
    if isinstance(exc_or_message, TimeoutError):
        return "timeout"
    text = str(exc_or_message or "").strip().lower()
    if "cancel" in text:
        return "cancelled"
    if "timeout" in text:
        return "timeout"
    return "failed"


def _mark_task_error_state(task_id: Optional[str], exc_or_message: Any) -> str:
    kind = _classify_error_kind(exc_or_message)
    tid = str(task_id or "").strip()
    if not tid or not _task_orchestrator_enabled():
        return kind
    try:
        if kind == "cancelled":
            deps._TASK_ORCHESTRATOR.cancel_task(tid)  # type: ignore[union-attr]
        elif kind == "timeout":
            deps._TASK_ORCHESTRATOR.mark_waiting_io(tid, str(exc_or_message))  # type: ignore[union-attr]
        else:
            deps._TASK_ORCHESTRATOR.mark_failed(tid, str(exc_or_message))  # type: ignore[union-attr]
    except Exception as e:
        logger.debug("mark_task_error_state skipped task={} err={}", tid, e)
    return kind


def _record_pipeline_event(
    event_name: str,
    *,
    error_kind: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    with deps._PIPELINE_METRICS_LOCK:
        deps._PIPELINE_METRICS[event_name] = int(deps._PIPELINE_METRICS.get(event_name, 0)) + 1
        deps._PIPELINE_METRICS["last_event"] = event_name
        deps._PIPELINE_METRICS["last_event_ts"] = now_iso
        if error_kind in {"timeout", "cancelled", "failed"}:
            key = f"pipeline_{error_kind}_total"
            deps._PIPELINE_METRICS[key] = int(deps._PIPELINE_METRICS.get(key, 0)) + 1
        if error:
            deps._PIPELINE_METRICS["last_error"] = str(error)[:500]
            deps._PIPELINE_METRICS["last_error_ts"] = now_iso


def _push_tool_runtime_context(ide_context: Dict[str, Any]) -> Dict[str, Any]:
    if not ide_context:
        return {}
    if not deps.lumena or not getattr(deps.lumena, "tool_system", None):
        return {}
    push_fn = getattr(deps.lumena.tool_system, "push_runtime_context", None)
    if not callable(push_fn):
        return {}
    try:
        return push_fn(
            workspace_root=ide_context.get("workspace_path"),
            active_file_path=ide_context.get("active_file_path"),
            open_files=ide_context.get("open_files") or [],
        ) or {}
    except Exception as e:
        logger.debug("push_runtime_context skipped: {}", e)
        return {}


def _pop_tool_runtime_context(tokens: Dict[str, Any]) -> None:
    if not tokens:
        return
    if not deps.lumena or not getattr(deps.lumena, "tool_system", None):
        return
    pop_fn = getattr(deps.lumena.tool_system, "pop_runtime_context", None)
    if not callable(pop_fn):
        return
    try:
        pop_fn(tokens)
    except Exception as e:
        logger.debug("pop_runtime_context skipped: {}", e)


async def _call_lumena_with_context(method_name: str, message: str, channel: str, ide_context: Dict[str, Any]) -> str:
    """
    Call lumena.chat / lumena.think_and_act with backward-compatible kwargs.
    Some test doubles and old integrations only accept (message).
    """
    if not deps.lumena:
        raise RuntimeError("Lumena not initialized")
    method = getattr(deps.lumena, method_name)
    kwargs: Dict[str, Any] = {}
    try:
        signature = inspect.signature(method)
        params = signature.parameters
        if "source_channel" in params:
            kwargs["source_channel"] = channel
        if "ide_context" in params:
            kwargs["ide_context"] = ide_context
    except Exception:
        kwargs = {"source_channel": channel, "ide_context": ide_context}
    return await method(message, **kwargs) if kwargs else await method(message)


async def _call_lumena_with_step_timeout_and_retry(
    *,
    method_name: str,
    message: str,
    channel: str,
    ide_context: Dict[str, Any],
    step_timeout_sec: float,
    max_retries: int,
) -> tuple[str, Dict[str, Any]]:
    attempts = 0
    while True:
        attempts += 1
        reset_codex_response_meta()
        try:
            call_coro = _call_lumena_with_context(
                method_name,
                message,
                channel,
                ide_context,
            )
            if step_timeout_sec > 0:
                response = await asyncio.wait_for(call_coro, timeout=step_timeout_sec)
            else:
                response = await call_coro
            return response, {
                "attempts": attempts,
                "retry_count": max(0, attempts - 1),
                "step_timeout_sec": step_timeout_sec,
                "llm_response_meta": consume_codex_response_meta(),
            }
        except asyncio.TimeoutError as exc:
            consume_codex_response_meta()
            if attempts <= max_retries:
                continue
            raise TimeoutError(
                f"step_timeout_after_{attempts}_attempts_{step_timeout_sec:.1f}s"
            ) from exc
        except BaseException:
            consume_codex_response_meta()
            raise


def _llm_meta_for_completed_call(
    fallback_meta: Dict[str, Any], call_meta: Dict[str, Any]
) -> Dict[str, Any]:
    """Prefer request-local execution metadata over the historical API client."""

    result = dict(fallback_meta or {})
    routed = (call_meta or {}).get("llm_response_meta")
    if isinstance(routed, dict) and routed:
        result.update(routed)
    return result


async def _call_lumena_with_auto_resume_on_timeout(
    *,
    method_name: str,
    message: str,
    channel: str,
    ide_context: Dict[str, Any],
    step_timeout_sec: float,
    max_retries: int,
    task_id: Optional[str],
) -> tuple[str, Dict[str, Any]]:
    try:
        response, meta = await _call_lumena_with_step_timeout_and_retry(
            method_name=method_name,
            message=message,
            channel=channel,
            ide_context=ide_context,
            step_timeout_sec=step_timeout_sec,
            max_retries=max_retries,
        )
        return response, {
            **meta,
            "auto_resumed": False,
            "auto_resume_reason": None,
        }
    except TimeoutError as exc:
        if not _env_flag("LUMENA_TIMEOUT_AUTO_RESUME", True):
            raise
        if not _task_orchestrator_enabled() or not task_id:
            raise

        deps._TASK_ORCHESTRATOR.mark_waiting_io(task_id, str(exc))  # type: ignore[union-attr]
        resume_payload = deps._TASK_ORCHESTRATOR.resume_task(task_id)  # type: ignore[union-attr]
        if not resume_payload.get("success"):
            raise

        backoff_sec = _env_float("LUMENA_TIMEOUT_RESUME_BACKOFF_SEC", 0.4, minimum=0.0)
        if backoff_sec > 0:
            await asyncio.sleep(backoff_sec)

        resume_step_timeout_sec = _env_float(
            "LUMENA_TIMEOUT_RESUME_STEP_SEC",
            (step_timeout_sec * 1.5) if step_timeout_sec > 0 else 0.0,
            minimum=0.0,
        )
        response, meta = await _call_lumena_with_step_timeout_and_retry(
            method_name=method_name,
            message=message,
            channel=channel,
            ide_context=ide_context,
            step_timeout_sec=resume_step_timeout_sec,
            max_retries=0,
        )
        return response, {
            **meta,
            "auto_resumed": True,
            "auto_resume_reason": str(exc),
            "resume_step_timeout_sec": resume_step_timeout_sec,
        }


def _preview_message(message: str, max_len: int = 100) -> str:
    text = (message or "").replace("\n", " ").replace("\r", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


# =====================================================================
# Module-level stream dedup state
# =====================================================================

_chat_stream_lock = asyncio.Lock()
_chat_active_hashes: set[str] = set()
_CANCEL_TOKENS: Dict[str, threading.Event] = {}


# =====================================================================
# POST /api/chat/cancel
# =====================================================================

@router.post("/api/chat/cancel")
async def cancel_chat_stream(body: Dict[str, Any], _auth=Depends(deps.verify_admin_token)):
    """Annule un stream SSE en cours via son stream_id."""
    stream_id = str(body.get("stream_id") or "").strip()
    if not stream_id:
        return JSONResponse({"cancelled": False, "detail": "stream_id manquant"})
    ev = _CANCEL_TOKENS.pop(stream_id, None)
    if ev:
        ev.set()
        logger.info("[cancel] Stream {} annulé par l'utilisateur", stream_id)
        return JSONResponse({"cancelled": True})
    return JSONResponse({"cancelled": False, "detail": "stream introuvable"})


# =====================================================================
# POST /api/chat
# =====================================================================

@router.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, _auth=Depends(deps.verify_admin_token)):
    """Envoie un message a Lumena."""
    if deps.setup_only_mode:
        raise HTTPException(
            status_code=503,
            detail="Lumena est en mode configuration. Compl\u00e9tez le wizard de setup d'abord.",
        )
    if not deps.lumena:
        raise HTTPException(status_code=503, detail="Lumena not initialized")
    _notify_autonomy_user_interaction(request.message)

    tool_calls = []
    thinking_steps = []
    trace_tokens = {}
    trace_ctx = {}
    trace_id: Optional[str] = None
    channel = _normalize_channel(request.channel)
    envelope = _build_channel_envelope(request, channel)
    client_name = (envelope.get("client") or "unknown").strip() or "unknown"
    task_id = envelope.get("task_id")
    if _task_orchestrator_enabled():
        record = deps._TASK_ORCHESTRATOR.start_task(  # type: ignore[union-attr]
            conversation_id=(envelope.get("conversation_id") or "conv_unknown"),
            channel=channel,
            message_preview=request.message,
            metadata={
                "request_id": envelope.get("request_id"),
                "client": client_name,
                "source": "api_chat",
            },
            task_id=task_id,
        )
        deps._TASK_ORCHESTRATOR.mark_running(record.task_id)  # type: ignore[union-attr]
        task_id = record.task_id
        envelope["task_id"] = task_id
    try:
        ide_context = _apply_workspace_policy(request, channel, _extract_ide_context(request, channel))
    except WorkspacePolicyError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e
    runtime_context = _build_runtime_context(request, channel, envelope, ide_context)
    runtime_context_token = _push_request_runtime_context(runtime_context)
    tool_runtime_tokens: Dict[str, Any] = {}
    task_finished_ok = False
    mode = "agent" if request.use_agent else "chat"
    step_timeout_sec = _env_float("LUMENA_TASK_STEP_TIMEOUT_SEC", 120.0, minimum=0.0)
    max_retries = _env_non_negative_int("LUMENA_TASK_STEP_TIMEOUT_RETRIES", 1)
    global_timeout_sec = _env_float("LUMENA_CHAT_GLOBAL_TIMEOUT_SEC", 0.0, minimum=0.0)
    request_started_perf = time.perf_counter()
    workspace_resolution_error = _is_workspace_resolution_error(ide_context)
    logger.info(
        "[api/chat] start channel={} client={} mode={} req={} conv={} task={} len={} workspace={} active_file={} msg='{}'",
        channel,
        client_name,
        mode,
        envelope.get("request_id") or "-",
        envelope.get("conversation_id") or "-",
        task_id or "-",
        len(request.message or ""),
        ide_context.get("workspace_path") or "-",
        ide_context.get("active_file_path") or "-",
        _preview_message(request.message),
    )
    _record_pipeline_event("chat_requests_total")
    if deps.TELEMETRY_AVAILABLE:
        trace_tokens = deps.push_trace_context(
            channel=channel,
            client=client_name,
            mode=mode,
            request_id=envelope.get("request_id"),
            conversation_id=envelope.get("conversation_id"),
            task_id=task_id,
        )
        trace_ctx = deps.current_trace_context() or {}
        trace_id = trace_ctx.get("trace_id")
        deps.publish_trace(
            stage="input_received",
            status="start",
            mode=mode,
            summary=request.message,
        )
    _update_session_state(
        envelope.get("conversation_id"),
        channel=channel,
        client=client_name,
        request_id=envelope.get("request_id"),
        task_id=task_id,
        trace_id=trace_id,
        workspace_path=ide_context.get("workspace_path"),
        resolved_date=ide_context.get("resolved_date"),
        status="running",
    )
    _record_session_user_message(
        request,
        envelope=envelope,
        channel=channel,
        client_name=client_name,
        mode=mode,
        task_id=task_id,
        trace_id=trace_id,
        ide_context=ide_context,
    )
    tool_runtime_tokens = _push_tool_runtime_context(ide_context)

    try:
        call_method = "think_and_act" if request.use_agent else "chat"
        # Inject attachment context into message if files were uploaded
        effective_message = _build_effective_message(request.message, request.attachments)
        if request.use_agent:
            call_coro = _call_lumena_with_auto_resume_on_timeout(
                method_name=call_method,
                message=effective_message,
                channel=channel,
                ide_context=ide_context,
                step_timeout_sec=step_timeout_sec,
                max_retries=max_retries,
                task_id=task_id,
            )
            if global_timeout_sec > 0:
                response, call_meta = await asyncio.wait_for(call_coro, timeout=global_timeout_sec)
            else:
                response, call_meta = await call_coro
        else:
            call_coro = _call_lumena_with_auto_resume_on_timeout(
                method_name=call_method,
                message=effective_message,
                channel=channel,
                ide_context=ide_context,
                step_timeout_sec=step_timeout_sec,
                max_retries=max_retries,
                task_id=task_id,
            )
            if global_timeout_sec > 0:
                response, call_meta = await asyncio.wait_for(call_coro, timeout=global_timeout_sec)
            else:
                response, call_meta = await call_coro

        if call_meta.get("retry_count", 0):
            thinking_steps.append(
                {
                    "step": "checkpoint",
                    "content": f"Retry applique apres timeout etape: {call_meta['retry_count']}",
                }
            )
        if call_meta.get("auto_resumed"):
            thinking_steps.append(
                {
                    "step": "checkpoint",
                    "content": "Reprise automatique apres timeout etape",
                }
            )

        mood = None
        if deps.lumena.emotion_manager:
            mood = deps.lumena.emotion_manager.get_mood().value
        llm_meta = _llm_meta_for_completed_call(_get_llm_meta(), call_meta)
        agent_meta = _get_agent_meta() if request.use_agent else _default_agent_meta()
        trace_id = trace_ctx.get("trace_id")
        file_edits, edit_session_id, undo_available = _safe_file_edits_for_trace(
            trace_id,
            consume=True,
        )
        if deps.TELEMETRY_AVAILABLE:
            deps.publish_trace(
                stage="output_sent",
                status="ok",
                mode=mode,
                provider=llm_meta.get("provider_used"),
                model=llm_meta.get("model_used"),
                summary=response,
            )
        logger.info(
            "[api/chat] done channel={} client={} mode={} trace_id={} chars={}",
            channel,
            client_name,
            mode,
            (trace_ctx or {}).get("trace_id"),
            len(response or ""),
        )
        _update_session_state(
            envelope.get("conversation_id"),
            channel=channel,
            client=client_name,
            request_id=envelope.get("request_id"),
            task_id=task_id,
            trace_id=trace_id,
            workspace_path=ide_context.get("workspace_path"),
            resolved_date=ide_context.get("resolved_date"),
            status="done",
        )
        _record_slo_outcome(
            success=True,
            latency_ms=int((time.perf_counter() - request_started_perf) * 1000),
            timeout_unrecovered=False,
            resumed=bool(call_meta.get("auto_resumed") or call_meta.get("retry_count", 0)),
            workspace_error=workspace_resolution_error,
            undo_success=(bool(undo_available) if file_edits else None),
        )
        _record_pipeline_event("chat_success_total")

        task_finished_ok = True
        # Build created_documents for inline rendering
        _created_docs = []
        for _edit in file_edits:
            _act = _edit.get("action", "")
            if _act in ("created", "wrote"):
                _fp = _edit.get("workspace_relative") or _edit.get("file_path", "")
                _nm = _fp.rsplit("/", 1)[-1] if "/" in _fp else _fp.rsplit("\\", 1)[-1] if "\\" in _fp else _fp
                _doc: Dict[str, Any] = {"name": _nm, "path": _fp}
                _ext = _nm.rsplit(".", 1)[-1].lower() if "." in _nm else ""
                if _ext in ("png", "jpg", "jpeg", "gif", "webp", "svg"):
                    _doc["url"] = f"/api/files/workspace/{_fp}"
                    _doc["type"] = "image"
                _ct = _edit.get("after_content")
                if _ct and len(_ct) <= 3000:
                    _doc["content"] = _ct
                _created_docs.append(_doc)

        # Auto-serve workspace si index.html créé par CodeAgent
        try:
            from web.routes.advanced import _SERVING_WORKSPACES, _serve_workspace_dir
            from src.utils.paths import WORKSPACE_DIR as _WSDIR
            for _edit in file_edits:
                _fp_raw = _edit.get("file_path", "")
                if (_edit.get("action") in ("created", "wrote")
                        and (_fp_raw.endswith("index.html")
                             or _fp_raw.endswith("/index.html")
                             or _fp_raw.endswith("\\index.html"))):
                    _ws_root = Path(_fp_raw).parent
                    try:
                        _ws_root.relative_to(_WSDIR)
                        _ws_slug = _ws_root.name
                        if _ws_slug not in _SERVING_WORKSPACES:
                            _info = await _serve_workspace_dir(str(_ws_root), _ws_slug)
                        else:
                            _info = _SERVING_WORKSPACES[_ws_slug]
                        response = (response or "") + f"\n\n[Voir le projet en direct]({_info['url']})"
                    except Exception:
                        pass
                    break
        except Exception:
            pass

        _record_session_assistant_message(
            request,
            envelope=envelope,
            channel=channel,
            client_name=client_name,
            mode=mode,
            task_id=task_id,
            trace_id=trace_id,
            ide_context=ide_context,
            response=response,
            llm_meta=llm_meta,
            agent_meta=agent_meta,
            file_edits=file_edits,
            edit_session_id=edit_session_id,
            created_documents=_created_docs,
        )

        return ChatResponse(
            response=response,
            mood=mood,
            timestamp=datetime.now().isoformat(),
            tool_calls=tool_calls,
            thinking_steps=thinking_steps,
            **llm_meta,
            **agent_meta,
            file_edits=file_edits,
            edit_session_id=edit_session_id,
            undo_available=undo_available,
            created_documents=_created_docs,
            request_id=envelope.get("request_id"),
            conversation_id=envelope.get("conversation_id"),
            task_id=task_id,
            trace_id=trace_id,
        )
    except Exception as e:
        error_kind = _mark_task_error_state(task_id, e)
        _record_pipeline_event(
            "pipeline_errors_total",
            error_kind=error_kind,
            error=str(e),
        )
        if deps.TELEMETRY_AVAILABLE:
            deps.publish_trace(
                stage="pipeline_error",
                status="error",
                mode=mode,
                error=str(e),
            )
        logger.error(
            "[api/chat] error channel={} client={} mode={} err={}",
            channel,
            client_name,
            mode,
            str(e),
        )
        _update_session_state(
            envelope.get("conversation_id"),
            channel=channel,
            client=client_name,
            request_id=envelope.get("request_id"),
            task_id=task_id,
            trace_id=trace_id,
            workspace_path=ide_context.get("workspace_path"),
            resolved_date=ide_context.get("resolved_date"),
            status=("cancelled" if error_kind == "cancelled" else "error"),
            error=str(e),
        )
        _record_session_error(
            request,
            envelope=envelope,
            channel=channel,
            client_name=client_name,
            mode=mode,
            task_id=task_id,
            trace_id=trace_id,
            ide_context=ide_context,
            status=("cancelled" if error_kind == "cancelled" else "error"),
            error=e,
        )
        _record_slo_outcome(
            success=False,
            latency_ms=int((time.perf_counter() - request_started_perf) * 1000),
            timeout_unrecovered=_request_timeout_error(e),
            resumed=False,
            workspace_error=workspace_resolution_error,
            undo_success=None,
        )
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if _task_orchestrator_enabled() and task_id and task_finished_ok:
            deps._TASK_ORCHESTRATOR.mark_done(task_id, result_summary="chat_done")  # type: ignore[union-attr]
        _pop_tool_runtime_context(tool_runtime_tokens)
        _pop_request_runtime_context(runtime_context_token)
        if deps.TELEMETRY_AVAILABLE and trace_tokens:
            deps.pop_trace_context(trace_tokens)


# =====================================================================
# POST /api/chat/stream
# =====================================================================

@router.post("/api/chat/stream")
async def chat_stream(request: ChatRequest, _auth=Depends(deps.verify_admin_token)):
    """Chat avec streaming SSE des pensees en temps reel."""
    if deps.setup_only_mode:
        raise HTTPException(
            status_code=503,
            detail="Lumena est en mode configuration. Compl\u00e9tez le wizard de setup d'abord.",
        )
    if not deps.lumena:
        raise HTTPException(status_code=503, detail="Lumena not initialized")

    msg_hash = hashlib.sha256(request.message.encode()).hexdigest()[:16]
    async with _chat_stream_lock:
        if msg_hash in _chat_active_hashes:
            raise HTTPException(status_code=429, detail="Duplicate request in progress")
        _chat_active_hashes.add(msg_hash)

    _notify_autonomy_user_interaction(request.message)

    async def generate():
        stream_id = str(uuid.uuid4())
        cancel_event = threading.Event()
        _CANCEL_TOKENS[stream_id] = cancel_event

        channel = _normalize_channel(request.channel)
        envelope = _build_channel_envelope(request, channel)
        client_name = (envelope.get("client") or "unknown").strip() or "unknown"
        task_id = envelope.get("task_id")
        if _task_orchestrator_enabled():
            record = deps._TASK_ORCHESTRATOR.start_task(  # type: ignore[union-attr]
                conversation_id=(envelope.get("conversation_id") or "conv_unknown"),
                channel=channel,
                message_preview=request.message,
                metadata={
                    "request_id": envelope.get("request_id"),
                    "client": client_name,
                    "source": "api_chat_stream",
                },
                task_id=task_id,
            )
            deps._TASK_ORCHESTRATOR.mark_running(record.task_id)  # type: ignore[union-attr]
            task_id = record.task_id
            envelope["task_id"] = task_id
        try:
            ide_context = _apply_workspace_policy(request, channel, _extract_ide_context(request, channel))
        except WorkspacePolicyError as e:
            error_payload = {
                "type": "error",
                "content": e.detail,
                "channel": channel,
                "client": client_name,
                "request_id": envelope.get("request_id"),
                "conversation_id": envelope.get("conversation_id"),
                "task_id": task_id,
                "trace_id": None,
            }
            yield f"data: {json.dumps(_stream_payload(error_payload))}\n\n"
            return
        runtime_context = _build_runtime_context(request, channel, envelope, ide_context)
        runtime_context_token = _push_request_runtime_context(runtime_context)
        mode = "agent" if request.use_agent else "chat"
        step_timeout_sec = _env_float("LUMENA_TASK_STEP_TIMEOUT_SEC", 120.0, minimum=0.0)
        max_retries = _env_non_negative_int("LUMENA_TASK_STEP_TIMEOUT_RETRIES", 1)
        global_timeout_sec = _env_float("LUMENA_STREAM_GLOBAL_TIMEOUT_SEC", 0.0, minimum=0.0)
        request_started_perf = time.perf_counter()
        workspace_resolution_error = _is_workspace_resolution_error(ide_context)
        trace_tokens = {}
        trace_ctx = {}
        trace_id = None
        file_edit_cursor = 0
        tool_runtime_tokens: Dict[str, Any] = {}
        task_finished_ok = False
        call_meta: Dict[str, Any] = {}

        def _emit(payload: Dict[str, Any]) -> str:
            merged = dict(payload or {})
            merged.setdefault("channel", channel)
            merged.setdefault("client", client_name)
            merged.setdefault("request_id", envelope.get("request_id"))
            merged.setdefault("conversation_id", envelope.get("conversation_id"))
            merged.setdefault("task_id", task_id)
            merged.setdefault("trace_id", trace_id)
            return f"data: {json.dumps(_stream_payload(merged))}\n\n"

        logger.info(
            "[api/chat/stream] start channel={} client={} mode={} req={} conv={} task={} len={} workspace={} active_file={} msg='{}'",
            channel,
            client_name,
            mode,
            envelope.get("request_id") or "-",
            envelope.get("conversation_id") or "-",
            task_id or "-",
            len(request.message or ""),
            ide_context.get("workspace_path") or "-",
            ide_context.get("active_file_path") or "-",
            _preview_message(request.message),
        )
        _record_pipeline_event("stream_requests_total")
        if deps.TELEMETRY_AVAILABLE:
            trace_tokens = deps.push_trace_context(
                channel=channel,
                client=client_name,
                mode=mode,
                request_id=envelope.get("request_id"),
                conversation_id=envelope.get("conversation_id"),
                task_id=task_id,
            )
            trace_ctx = deps.current_trace_context()
            trace_id = (trace_ctx or {}).get("trace_id")
            deps.publish_trace(
                stage="input_received",
                status="start",
                mode=mode,
                summary=request.message,
            )
        _update_session_state(
            envelope.get("conversation_id"),
            channel=channel,
            client=client_name,
            request_id=envelope.get("request_id"),
            task_id=task_id,
            trace_id=trace_id,
            workspace_path=ide_context.get("workspace_path"),
            resolved_date=ide_context.get("resolved_date"),
            status="running",
        )
        _record_session_user_message(
            request,
            envelope=envelope,
            channel=channel,
            client_name=client_name,
            mode=mode,
            task_id=task_id,
            trace_id=trace_id,
            ide_context=ide_context,
        )

        # Envoyer le debut
        effective_message = _build_effective_message(request.message, request.attachments)
        # Polish web : rappel des missions inter-Lumena terminées (au prochain message).
        effective_message = _inject_mission_reminders(effective_message)
        yield _emit({"type": "start", "content": "Debut de la reflexion..."})
        yield _emit({"type": "stream_id", "stream_id": stream_id})

        try:
            if request.use_agent:
                # Mode agent avec ReAct
                checkpoint_payload = {
                    "phase": "analysis",
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                if _task_orchestrator_enabled() and task_id:
                    deps._TASK_ORCHESTRATOR.mark_checkpoint(  # type: ignore[union-attr]
                        task_id,
                        checkpoint_payload,
                    )
                yield _emit({"type": "checkpoint", "checkpoint": checkpoint_payload})
                yield _emit({"type": "thinking", "content": " Analyse de la requete..."})

                # Capturer les pensees pendant think_and_act
                # On va intercepter les logs
                from loguru import logger as stream_logger

                thoughts_captured: List[str] = []
                thoughts_lock = threading.Lock()
                agent_thread_id = {"value": None}
                max_thoughts = _env_int("LUMENA_SSE_MAX_THOUGHTS", 600, minimum=50)
                heartbeat_interval = float(_env_int("LUMENA_SSE_HEARTBEAT_SEC", 5, minimum=1))
                last_stream_emit = time.time()
                # `thoughts_captured` est une fenêtre glissante bornée à max_thoughts
                # → sa longueur plafonne. On suit donc séparément :
                #  - "dropped"  : nb d'items retirés en tête (pour un curseur ABSOLU monotone)
                #  - "pensees"  : compteur de raisonnement réel (hors sortie console / tokens)
                # Sans ça, le stream se figeait à 600 (UI "freezée") dès qu'une commande
                # console remplissait le buffer.
                _stream_meta = {"dropped": 0, "pensees": 0}

                # Creer un handler personnalise pour capturer les pensees
                def capture_thought(message):
                    record = message.record
                    stream_thread_id = agent_thread_id.get("value")
                    record_thread = record.get("thread")
                    record_thread_id = getattr(record_thread, "id", None)
                    msg = record["message"]
                    # [cmd_*] logs come from asyncio.to_thread (different thread)
                    # so skip the thread filter for terminal streaming events
                    _is_cmd = "[cmd_" in msg
                    if (
                        not _is_cmd
                        and stream_thread_id is not None
                        and record_thread_id is not None
                        and record_thread_id != stream_thread_id
                    ):
                        return
                    _CAPTURE_PATTERNS = (
                        "Thought:", "Action:", "Observation:", " Outil",
                        "TODO_STATE:", "LLM_RETRY:",
                        # Token streaming
                        "[FINAL_TOKEN]",
                        # Project/code generation
                        "[create_project]", "[code_agent]", "[sub_agent]",
                        "[website_builder]", "[project]",
                        # File events
                        "\u2705",  # checkmark
                        "Wave ", "Plan:", "Phase ", "Phase1", "Phase2",
                        # Model switching
                        "Auto-switch model", "auto-switch",
                        # CodeAgent iteration markers
                        "[CodeAgent]",
                        # ReAct iteration markers
                        "Iteration ", "ReAct Loop",
                        # Memory/context
                        "M\u00e9moire inject\u00e9e", "handlers loaded",
                        # LLM call status
                        "\u23f3 LLM en cours", "\U0001f4e5 LLM RESPONSE SIZE",
                        # Terminal streaming
                        "[cmd_start]", "[cmd_output]", "[cmd_output_err]", "[cmd_done]",
                        # File read viewer
                        "[file_read]",
                        # Video generation pipeline
                        "[video]", "[remotion]",
                    )

                    if any(p in msg for p in _CAPTURE_PATTERNS):
                        with thoughts_lock:
                            thoughts_captured.append(msg)
                            # Compteur "pensées" = raisonnement réel. On EXCLUT les
                            # lignes de sortie console et les tokens de la réponse finale
                            # (sinon il explose et atteint le plafond sur le moindre
                            # install/listing → ancien symptôme "bloqué à 600").
                            if not any(_s in msg for _s in ("[cmd_output]", "[cmd_output_err]", "[FINAL_TOKEN]")):
                                _stream_meta["pensees"] += 1
                            # Fenêtre glissante : on mémorise combien d'items sont retirés
                            # en tête, pour garder un curseur d'émission absolu et monotone.
                            if len(thoughts_captured) > max_thoughts:
                                _removed = len(thoughts_captured) - max_thoughts
                                del thoughts_captured[:_removed]
                                _stream_meta["dropped"] += _removed

                handler_id = stream_logger.add(capture_thought, format="{message}", level="DEBUG")

                try:
                    # Executer think_and_act dans un thread separe pour pouvoir streamer
                    result_container = {"response": None, "error": None, "call_meta": {}}

                    def run_agent():
                        local_tokens = {}
                        local_tool_tokens: Dict[str, Any] = {}
                        local_runtime_token = None
                        loop = None
                        # Enregistrer le cancel_event dans le registre react.py
                        # pour que la boucle ReAct le vérifie entre chaque itération.
                        _run_tid = threading.current_thread().ident
                        try:
                            from src.reasoning.react import _REACT_CANCEL_EVENTS as _rce
                            if _run_tid:
                                _rce[_run_tid] = cancel_event
                        except Exception:
                            pass
                        try:
                            local_runtime_token = _push_request_runtime_context(runtime_context)
                            local_tool_tokens = _push_tool_runtime_context(ide_context)
                            if deps.TELEMETRY_AVAILABLE:
                                local_tokens = deps.push_trace_context(
                                    trace_id=trace_ctx.get("trace_id"),
                                    turn_id=trace_ctx.get("turn_id"),
                                    channel=trace_ctx.get("channel", channel),
                                    client=trace_ctx.get("client", client_name),
                                    mode=trace_ctx.get("mode", "agent"),
                                    request_id=trace_ctx.get("request_id", envelope.get("request_id")),
                                    conversation_id=trace_ctx.get("conversation_id", envelope.get("conversation_id")),
                                    task_id=trace_ctx.get("task_id", task_id),
                                    force=True,
                                )
                            loop = asyncio.new_event_loop()
                            result_container["loop"] = loop
                            asyncio.set_event_loop(loop)
                            response, _call_meta = loop.run_until_complete(
                                _call_lumena_with_auto_resume_on_timeout(
                                    method_name="think_and_act",
                                    message=effective_message,
                                    channel=channel,
                                    ide_context=ide_context,
                                    step_timeout_sec=step_timeout_sec,
                                    max_retries=max_retries,
                                    task_id=task_id,
                                )
                            )
                            result_container["response"] = response
                            result_container["call_meta"] = _call_meta or {}
                        except Exception as e:
                            result_container["error"] = str(e)
                        finally:
                            # Désenregistrer le cancel_event du registre react.py
                            try:
                                from src.reasoning.react import _REACT_CANCEL_EVENTS as _rce
                                if _run_tid:
                                    _rce.pop(_run_tid, None)
                            except Exception:
                                pass
                            _pop_tool_runtime_context(local_tool_tokens)
                            _pop_request_runtime_context(local_runtime_token)
                            if loop is not None:
                                try:
                                    pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
                                    for task in pending:
                                        task.cancel()
                                    if pending:
                                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                                except Exception:
                                    pass
                                try:
                                    loop.run_until_complete(loop.shutdown_asyncgens())
                                except Exception:
                                    pass
                                try:
                                    loop.run_until_complete(loop.shutdown_default_executor())
                                except Exception:
                                    pass
                                try:
                                    loop.close()
                                except Exception:
                                    pass
                            if deps.TELEMETRY_AVAILABLE and local_tokens:
                                deps.pop_trace_context(local_tokens)

                    thread = threading.Thread(target=run_agent, daemon=True)
                    thread.start()
                    agent_thread_id["value"] = thread.ident
                    started_at = time.time()

                    # Streamer les pensees pendant que l'agent travaille
                    last_count = 0
                    _streaming_final_tokens = False
                    while thread.is_alive():
                        # Poll rapide (50ms) pendant le streaming réponse finale,
                        # sinon 300ms pour ne pas gaspiller de CPU pendant le raisonnement.
                        _poll_ms = 0.05 if _streaming_final_tokens else 0.3
                        await asyncio.sleep(_poll_ms)
                        if cancel_event.is_set():
                            # Annulation coopérative : cancel les tâches asyncio de la boucle
                            # du thread agent. La boucle ReAct vérifie cancel_event entre
                            # chaque itération via _REACT_CANCEL_EVENTS et s'arrête proprement.
                            # Pas d'arrêt forcé (ctypes) : on laisse le thread terminer son
                            # opération courante sans risque de laisser un état incohérent.
                            _th_loop = result_container.get("loop")
                            if _th_loop and not _th_loop.is_closed():
                                _th_loop.call_soon_threadsafe(
                                    lambda l=_th_loop: [t.cancel() for t in asyncio.all_tasks(l)]
                                )
                            result_container["error"] = "user_cancelled"
                            break
                        if _task_orchestrator_enabled() and task_id and deps._TASK_ORCHESTRATOR.is_cancel_requested(task_id):  # type: ignore[union-attr]
                            result_container["error"] = "task_cancelled"
                            break
                        if global_timeout_sec > 0 and (time.time() - started_at) >= global_timeout_sec:
                            result_container["error"] = f"global_stream_timeout_{global_timeout_sec:.1f}s"
                            _request_task_cancel(task_id, result_container["error"])
                            break

                        # Envoyer les nouvelles pensees.
                        # last_count = curseur ABSOLU (index global monotone), PAS
                        # len(thoughts_captured) : la liste est une fenêtre glissante
                        # bornée, sa longueur plafonne. Le total réel vu = dropped + len.
                        new_thoughts: List[str] = []
                        with thoughts_lock:
                            _total_seen = _stream_meta["dropped"] + len(thoughts_captured)
                            if _total_seen > last_count:
                                _start = max(0, last_count - _stream_meta["dropped"])
                                new_thoughts = thoughts_captured[_start:]
                                last_count = _total_seen
                        if new_thoughts:
                            for thought in new_thoughts:
                                # Token streaming — réponse finale mot par mot
                                if "[FINAL_TOKEN]" in thought:
                                    _streaming_final_tokens = True
                                    token_text = thought.split("[FINAL_TOKEN]", 1)[1]
                                    yield _emit({"type": "token", "content": token_text})
                                # Formater la pensee
                                elif "TODO_STATE:" in thought:
                                    try:
                                        todos = json.loads(thought.split("TODO_STATE:", 1)[1].strip())
                                        yield _emit({"type": "todo_update", "todos": todos})
                                    except Exception:
                                        pass
                                elif "LLM_RETRY:" in thought:
                                    content = thought.split("LLM_RETRY:", 1)[1].strip()
                                    yield _emit({"type": "llm_retry", "content": content})
                                elif "[file_read]" in thought:
                                    raw = thought.split("[file_read]", 1)[1].strip()
                                    _fr_parts = raw.split("|", 2)
                                    yield _emit({
                                        "type": "file_read",
                                        "path": _fr_parts[0].strip() if len(_fr_parts) > 0 else "",
                                        "lines": _fr_parts[1].strip() if len(_fr_parts) > 1 else "",
                                        "content": (_fr_parts[2].strip()[:2000] if len(_fr_parts) > 2 else ""),
                                    })
                                elif "Thought:" in thought:
                                    content = thought.split("Thought:", 1)[1].strip()
                                    yield _emit({"type": "thought", "content": content})
                                elif "Action:" in thought:
                                    content = thought.split("Action:", 1)[1].strip()
                                    yield _emit({"type": "action", "content": content})
                                elif " Outil" in thought:
                                    yield _emit({"type": "tool", "content": thought})
                                elif "Observation:" in thought:
                                    content = thought.split("Observation:", 1)[1].strip()[:200]
                                    yield _emit({"type": "observation", "content": content})
                                # Terminal streaming live
                                elif "[cmd_start]" in thought:
                                    cmd = thought.split("[cmd_start]", 1)[1].strip()
                                    yield _emit({"type": "terminal_open", "content": cmd})
                                elif "[cmd_output]" in thought:
                                    line = thought.split("[cmd_output]", 1)[1].strip()
                                    yield _emit({"type": "terminal_output", "content": line, "stream": "stdout"})
                                elif "[cmd_output_err]" in thought:
                                    line = thought.split("[cmd_output_err]", 1)[1].strip()
                                    yield _emit({"type": "terminal_output", "content": line, "stream": "stderr"})
                                elif "[cmd_done]" in thought:
                                    info = thought.split("[cmd_done]", 1)[1].strip()
                                    yield _emit({"type": "terminal_close", "content": info})
                                # File creation events (create_project / code_agent)
                                elif "\u2705" in thought and ("[create_project]" in thought or "[code_agent]" in thought or "[sub_agent]" in thought or "[website" in thought):
                                    # Extract filename: "[create_project] ✅ css/variables.css"
                                    parts = thought.split("\u2705", 1)
                                    filename = parts[1].strip() if len(parts) > 1 else thought
                                    yield _emit({"type": "action", "content": f"write_file({filename})"})
                                # Wave / phase announcements
                                elif "Wave " in thought and ("[create_project]" in thought or "fichier" in thought):
                                    # "[create_project] Wave 2: 14 fichiers (parallele, sem=8)"
                                    parts = thought.split("]", 1)
                                    label = parts[1].strip() if len(parts) > 1 else thought
                                    yield _emit({"type": "thinking", "content": label})
                                elif "Phase " in thought and "[create_project]" in thought:
                                    parts = thought.split("]", 1)
                                    label = parts[1].strip() if len(parts) > 1 else thought
                                    yield _emit({"type": "thinking", "content": label})
                                elif "Plan:" in thought and "[create_project]" in thought:
                                    parts = thought.split("]", 1)
                                    label = parts[1].strip()[:120] if len(parts) > 1 else thought[:120]
                                    yield _emit({"type": "thinking", "content": label})
                                elif "Auto-switch model" in thought or "auto-switch" in thought.lower():
                                    # "Auto-switch model for this turn: deepseek-chat -> deepseek-reasoner"
                                    yield _emit({"type": "thinking", "content": thought.split(" | ")[-1] if " | " in thought else thought})
                                elif "\u23f3 LLM en cours" in thought:
                                    # LLM call starting — extract useful info
                                    yield _emit({"type": "thinking", "content": thought.replace("\u23f3 ", "")})
                                elif "\U0001f4e5 LLM RESPONSE SIZE" in thought:
                                    yield _emit({"type": "thinking", "content": thought.replace("\U0001f4e5 ", "")})
                                elif "[CodeAgent]" in thought:
                                    _ca_detail = thought.split("[CodeAgent]", 1)[1].strip() if "[CodeAgent]" in thought else thought
                                    if _ca_detail.startswith("💭"):
                                        # Pensée/raisonnement du CodeAgent
                                        yield _emit({"type": "thinking", "content": _ca_detail[2:].strip()})
                                    else:
                                        # Action du CodeAgent (iter, write_file, etc.)
                                        yield _emit({"type": "agent_step", "content": _ca_detail})
                                elif "Iteration " in thought:
                                    yield _emit({"type": "thinking", "content": thought})
                                else:
                                    # Generic: emit as thinking
                                    yield _emit({"type": "thinking", "content": thought})
                            last_stream_emit = time.time()

                        if trace_id:
                            live_edits, _, _ = _safe_file_edits_for_trace(trace_id, consume=False)
                            if len(live_edits) > file_edit_cursor:
                                for edit in live_edits[file_edit_cursor:]:
                                    # Strip large content from live SSE events to avoid bandwidth issues;
                                    # full content is sent in the final 'done' payload
                                    live_edit = dict(edit)
                                    live_edit.pop("before_content", None)
                                    live_edit.pop("after_content", None)
                                    yield _emit({"type": "file_edit", "edit": live_edit})
                                file_edit_cursor = len(live_edits)
                                last_stream_emit = time.time()

                        if (time.time() - last_stream_emit) >= heartbeat_interval:
                            heartbeat_ts = datetime.now(timezone.utc).isoformat()
                            # Extraire la derniere action CodeAgent des pensees capturees
                            _last_agent_action = ""
                            with thoughts_lock:
                                for _t in reversed(thoughts_captured):
                                    if "[CodeAgent]" in _t:
                                        _last_agent_action = _t.split("[CodeAgent]", 1)[1].strip()
                                        break
                            checkpoint_payload = {
                                "phase": "running",
                                "ts": heartbeat_ts,
                                "thoughts": _stream_meta["pensees"],
                                "file_edits": file_edit_cursor,
                                "action_detail": _last_agent_action,
                            }
                            if _task_orchestrator_enabled() and task_id:
                                deps._TASK_ORCHESTRATOR.mark_checkpoint(  # type: ignore[union-attr]
                                    task_id,
                                    checkpoint_payload,
                                )
                            yield _emit({"type": "checkpoint", "checkpoint": checkpoint_payload})
                            yield _emit({"type": "heartbeat", "ts": heartbeat_ts})
                            last_stream_emit = time.time()

                    thread.join(timeout=0.2)

                    # Flush remaining captured thoughts (generated between last check and thread exit)
                    _remaining_thoughts: List[str] = []
                    with thoughts_lock:
                        _total_seen = _stream_meta["dropped"] + len(thoughts_captured)
                        if _total_seen > last_count:
                            _start = max(0, last_count - _stream_meta["dropped"])
                            _remaining_thoughts = thoughts_captured[_start:]
                            last_count = _total_seen
                    if _remaining_thoughts:
                        for thought in _remaining_thoughts:
                            if "[FINAL_TOKEN]" in thought:
                                token_text = thought.split("[FINAL_TOKEN]", 1)[1]
                                yield _emit({"type": "token", "content": token_text})
                            elif "[file_read]" in thought:
                                raw = thought.split("[file_read]", 1)[1].strip()
                                _fr_parts = raw.split("|", 2)
                                yield _emit({
                                    "type": "file_read",
                                    "path": _fr_parts[0].strip() if len(_fr_parts) > 0 else "",
                                    "lines": _fr_parts[1].strip() if len(_fr_parts) > 1 else "",
                                    "content": (_fr_parts[2].strip()[:2000] if len(_fr_parts) > 2 else ""),
                                })
                            elif "Thought:" in thought:
                                content = thought.split("Thought:", 1)[1].strip()
                                yield _emit({"type": "thought", "content": content})
                            elif "Action:" in thought:
                                content = thought.split("Action:", 1)[1].strip()
                                yield _emit({"type": "action", "content": content})
                            elif "Observation:" in thought:
                                content = thought.split("Observation:", 1)[1].strip()[:200]
                                yield _emit({"type": "observation", "content": content})
                            elif " Outil" in thought:
                                yield _emit({"type": "tool", "content": thought})
                            elif "[cmd_start]" in thought:
                                yield _emit({"type": "terminal_open", "content": thought.split("[cmd_start]", 1)[1].strip()})
                            elif "[cmd_output]" in thought:
                                yield _emit({"type": "terminal_output", "content": thought.split("[cmd_output]", 1)[1].strip(), "stream": "stdout"})
                            elif "[cmd_output_err]" in thought:
                                yield _emit({"type": "terminal_output", "content": thought.split("[cmd_output_err]", 1)[1].strip(), "stream": "stderr"})
                            elif "[cmd_done]" in thought:
                                yield _emit({"type": "terminal_close", "content": thought.split("[cmd_done]", 1)[1].strip()})
                            else:
                                yield _emit({"type": "thinking", "content": thought})

                    if result_container["error"]:
                        error_kind = _mark_task_error_state(task_id, result_container["error"])
                        _record_pipeline_event(
                            "pipeline_errors_total",
                            error_kind=error_kind,
                            error=str(result_container["error"]),
                        )
                        _update_session_state(
                            envelope.get("conversation_id"),
                            channel=channel,
                            client=client_name,
                            request_id=envelope.get("request_id"),
                            task_id=task_id,
                            trace_id=trace_id,
                            workspace_path=ide_context.get("workspace_path"),
                            resolved_date=ide_context.get("resolved_date"),
                            status=("cancelled" if error_kind == "cancelled" else "error"),
                            error=str(result_container["error"]),
                        )
                        _record_session_error(
                            request,
                            envelope=envelope,
                            channel=channel,
                            client_name=client_name,
                            mode=mode,
                            task_id=task_id,
                            trace_id=trace_id,
                            ide_context=ide_context,
                            status=("cancelled" if error_kind == "cancelled" else "error"),
                            error=result_container["error"],
                        )
                        _record_slo_outcome(
                            success=False,
                            latency_ms=int((time.perf_counter() - request_started_perf) * 1000),
                            timeout_unrecovered=_request_timeout_error(Exception(str(result_container["error"]))),
                            resumed=False,
                            workspace_error=workspace_resolution_error,
                            undo_success=None,
                        )
                        if result_container["error"] != "user_cancelled":
                            yield _emit({"type": "error", "content": result_container["error"]})
                        return
                    else:
                        response = result_container["response"] or ""
                        call_meta = dict(result_container.get("call_meta") or {})
                finally:
                    stream_logger.remove(handler_id)
            else:
                # Mode chat simple
                checkpoint_payload = {
                    "phase": "chat",
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                if _task_orchestrator_enabled() and task_id:
                    deps._TASK_ORCHESTRATOR.mark_checkpoint(  # type: ignore[union-attr]
                        task_id,
                        checkpoint_payload,
                    )
                yield _emit({"type": "checkpoint", "checkpoint": checkpoint_payload})
                yield _emit({"type": "thinking", "content": " Reflexion..."})
                tool_runtime_tokens = _push_tool_runtime_context(ide_context)
                chat_heartbeat_interval = float(_env_int("LUMENA_SSE_HEARTBEAT_SEC", 5, minimum=1))
                chat_last_emit_ref = [time.time()]

                # Lancer le heartbeat SSE en tache de fond pour ne pas bloquer
                # le generateur pendant l'appel LLM long (qui peut durer >120s)
                _chat_hb_stop = asyncio.Event()

                async def _chat_heartbeat_loop():
                    while not _chat_hb_stop.is_set():
                        await asyncio.sleep(0.3)
                        if _chat_hb_stop.is_set():
                            break
                        if (time.time() - chat_last_emit_ref[0]) >= chat_heartbeat_interval:
                            pass  # yielding from here is not possible; handled below

                # Because we cannot yield from a background task, use a shared queue
                _chat_hb_queue: asyncio.Queue = asyncio.Queue()
                _chat_delta_queue: asyncio.Queue[str] = asyncio.Queue()

                def _codex_delta_sink(delta: str) -> None:
                    if delta:
                        _chat_delta_queue.put_nowait(delta)

                async def _chat_heartbeat_producer():
                    while True:
                        await asyncio.sleep(chat_heartbeat_interval)
                        await _chat_hb_queue.put(datetime.now(timezone.utc).isoformat())

                # Run chat coroutine and heartbeat producer concurrently
                call_coro = _call_lumena_with_auto_resume_on_timeout(
                    method_name="chat",
                    message=effective_message,
                    channel=channel,
                    ide_context=ide_context,
                    step_timeout_sec=step_timeout_sec,
                    max_retries=max_retries,
                    task_id=task_id,
                )

                hb_task = asyncio.ensure_future(_chat_heartbeat_producer())
                _codex_sink_token = push_codex_chat_delta_sink(_codex_delta_sink)
                try:
                    if global_timeout_sec > 0:
                        chat_coro_task = asyncio.ensure_future(
                            asyncio.wait_for(call_coro, timeout=global_timeout_sec)
                        )
                    else:
                        chat_coro_task = asyncio.ensure_future(call_coro)

                    while not chat_coro_task.done():
                        await asyncio.sleep(0.1)
                        while not _chat_delta_queue.empty():
                            try:
                                delta = _chat_delta_queue.get_nowait()
                                yield _emit({"type": "token", "content": delta})
                                chat_last_emit_ref[0] = time.time()
                            except asyncio.QueueEmpty:
                                break
                        # Drain heartbeat queue
                        while not _chat_hb_queue.empty():
                            try:
                                hb_ts = _chat_hb_queue.get_nowait()
                                yield _emit({"type": "heartbeat", "ts": hb_ts})
                                chat_last_emit_ref[0] = time.time()
                            except asyncio.QueueEmpty:
                                break

                    # Flush deltas received between the final poll and completion.
                    while not _chat_delta_queue.empty():
                        try:
                            delta = _chat_delta_queue.get_nowait()
                            yield _emit({"type": "token", "content": delta})
                        except asyncio.QueueEmpty:
                            break

                    # Retrieve result or re-raise exception
                    response, call_meta = chat_coro_task.result()
                finally:
                    pop_codex_chat_delta_sink(_codex_sink_token)
                    hb_task.cancel()
                    try:
                        await hb_task
                    except asyncio.CancelledError:
                        pass

            if call_meta.get("retry_count", 0):
                yield _emit(
                    {
                        "type": "checkpoint",
                        "checkpoint": {
                            "phase": "retry",
                            "retry_count": call_meta["retry_count"],
                            "step_timeout_sec": call_meta.get("step_timeout_sec", step_timeout_sec),
                        },
                    }
                )
            if call_meta.get("auto_resumed"):
                yield _emit(
                    {
                        "type": "checkpoint",
                        "checkpoint": {
                            "phase": "auto_resume",
                            "reason": call_meta.get("auto_resume_reason"),
                            "resume_step_timeout_sec": call_meta.get("resume_step_timeout_sec"),
                        },
                    }
                )

            # Obtenir le mood
            mood = None
            if deps.lumena.emotion_manager:
                mood = deps.lumena.emotion_manager.get_mood().value
            llm_meta = _llm_meta_for_completed_call(_get_llm_meta(), call_meta)
            agent_meta = _get_agent_meta() if request.use_agent else _default_agent_meta()
            file_edits, edit_session_id, undo_available = _safe_file_edits_for_trace(
                trace_id,
                consume=True,
            )

            # Build created_documents from file_edits for inline document rendering
            created_documents = []
            for edit in file_edits:
                action = edit.get("action", "")
                if action in ("created", "wrote"):
                    fp = edit.get("workspace_relative") or edit.get("file_path", "")
                    name = fp.rsplit("/", 1)[-1] if "/" in fp else fp.rsplit("\\", 1)[-1] if "\\" in fp else fp
                    doc: Dict[str, Any] = {"name": name, "path": fp}
                    ext_ = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                    if ext_ in ("png", "jpg", "jpeg", "gif", "webp", "svg"):
                        doc["url"] = f"/api/files/workspace/{fp}"
                        doc["type"] = "image"
                    content = edit.get("after_content")
                    if content and len(content) <= 3000:
                        doc["content"] = content
                    created_documents.append(doc)

            # Auto-serve workspace si index.html créé par CodeAgent
            try:
                from web.routes.advanced import _SERVING_WORKSPACES, _serve_workspace_dir
                from src.utils.paths import WORKSPACE_DIR as _WSDIR
                for _edit in file_edits:
                    _fp_raw = _edit.get("file_path", "")
                    if (_edit.get("action") in ("created", "wrote")
                            and (_fp_raw.endswith("index.html")
                                 or _fp_raw.endswith("/index.html")
                                 or _fp_raw.endswith("\\index.html"))):
                        _ws_root = Path(_fp_raw).parent
                        try:
                            _ws_root.relative_to(_WSDIR)
                            _ws_slug = _ws_root.name
                            if _ws_slug not in _SERVING_WORKSPACES:
                                _info = await _serve_workspace_dir(str(_ws_root), _ws_slug)
                            else:
                                _info = _SERVING_WORKSPACES[_ws_slug]
                            response = (response or "") + f"\n\n[Voir le projet en direct]({_info['url']})"
                        except Exception:
                            pass
                        break
            except Exception:
                pass

            # Envoyer la reponse finale
            # FT-6: Ajouter content_hash pour le feedback 👍/👎
            _feedback_hash = ""
            try:
                import hashlib as _hlib
                _ch_combined = f"{request.message.strip()}\n---\n{(response or '').strip()}"
                _feedback_hash = _hlib.sha256(_ch_combined.encode("utf-8")).hexdigest()[:16]
            except Exception:
                pass

            done_payload = {
                "type": "done",
                "response": response,
                "mood": mood,
                "file_edits": file_edits,
                "edit_session_id": edit_session_id,
                "undo_available": undo_available,
                "created_documents": created_documents,
                "request_id": envelope.get("request_id"),
                "conversation_id": envelope.get("conversation_id"),
                "task_id": task_id,
                "trace_id": trace_id,
                "content_hash": _feedback_hash,
                **llm_meta,
                **agent_meta,
            }
            if deps.TELEMETRY_AVAILABLE:
                deps.publish_trace(
                    stage="output_sent",
                    status="ok",
                    mode=mode,
                    provider=llm_meta.get("provider_used"),
                    model=llm_meta.get("model_used"),
                    summary=response,
                )
            logger.info(
                "[api/chat/stream] done channel={} client={} mode={} trace_id={} chars={}",
                channel,
                client_name,
                mode,
                trace_id,
                len(response or ""),
            )
            _update_session_state(
                envelope.get("conversation_id"),
                channel=channel,
                client=client_name,
                request_id=envelope.get("request_id"),
                task_id=task_id,
                trace_id=trace_id,
                workspace_path=ide_context.get("workspace_path"),
                resolved_date=ide_context.get("resolved_date"),
                status="done",
            )
            _record_session_assistant_message(
                request,
                envelope=envelope,
                channel=channel,
                client_name=client_name,
                mode=mode,
                task_id=task_id,
                trace_id=trace_id,
                ide_context=ide_context,
                response=response,
                llm_meta=llm_meta,
                agent_meta=agent_meta,
                file_edits=file_edits,
                edit_session_id=edit_session_id,
                created_documents=created_documents,
            )
            _record_slo_outcome(
                success=True,
                latency_ms=int((time.perf_counter() - request_started_perf) * 1000),
                timeout_unrecovered=False,
                resumed=bool(call_meta.get("auto_resumed") or call_meta.get("retry_count", 0)),
                workspace_error=workspace_resolution_error,
                undo_success=(bool(undo_available) if file_edits else None),
            )
            _record_pipeline_event("stream_success_total")
            task_finished_ok = True
            yield _emit(done_payload)

        except Exception as e:
            error_kind = _mark_task_error_state(task_id, e)
            _record_pipeline_event(
                "pipeline_errors_total",
                error_kind=error_kind,
                error=str(e),
            )
            _update_session_state(
                envelope.get("conversation_id"),
                channel=channel,
                client=client_name,
                request_id=envelope.get("request_id"),
                task_id=task_id,
                trace_id=trace_id,
                workspace_path=ide_context.get("workspace_path"),
                resolved_date=ide_context.get("resolved_date"),
                status=("cancelled" if error_kind == "cancelled" else "error"),
                error=str(e),
            )
            _record_session_error(
                request,
                envelope=envelope,
                channel=channel,
                client_name=client_name,
                mode=mode,
                task_id=task_id,
                trace_id=trace_id,
                ide_context=ide_context,
                status=("cancelled" if error_kind == "cancelled" else "error"),
                error=e,
            )
            _record_slo_outcome(
                success=False,
                latency_ms=int((time.perf_counter() - request_started_perf) * 1000),
                timeout_unrecovered=_request_timeout_error(e),
                resumed=False,
                workspace_error=workspace_resolution_error,
                undo_success=None,
            )
            if deps.TELEMETRY_AVAILABLE:
                deps.publish_trace(
                    stage="pipeline_error",
                    status="error",
                    mode=mode,
                    error=str(e),
                )
            logger.error(
                "[api/chat/stream] error channel={} client={} mode={} err={}",
                channel,
                client_name,
                mode,
                str(e),
            )
            yield _emit({"type": "error", "content": str(e)})
        finally:
            # Toujours signaler l'annulation au thread agent pour éviter les runs orphelins.
            # Couvre : déconnexion client avant stream_id, erreur réseau, GeneratorExit, etc.
            cancel_event.set()
            _CANCEL_TOKENS.pop(stream_id, None)
            _chat_active_hashes.discard(msg_hash)
            if _task_orchestrator_enabled() and task_id and task_finished_ok:
                deps._TASK_ORCHESTRATOR.mark_done(task_id, result_summary="chat_stream_done")  # type: ignore[union-attr]
            _pop_tool_runtime_context(tool_runtime_tokens)
            _pop_request_runtime_context(runtime_context_token)
            if deps.TELEMETRY_AVAILABLE and trace_tokens:
                deps.pop_trace_context(trace_tokens)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
# =====================================================================
# POST /api/chat/feedback  — FT-6
# =====================================================================

@router.post("/api/chat/feedback")
async def chat_feedback(body: dict, _auth=Depends(deps.verify_admin_token)):
    """FT-6: Enregistre un feedback 👍/👎 sur une réponse de Lumena.

    Met à jour le quality_flag dans le training pool pour le fine-tuning.
    positive_explicit (👍) → +2 pts dans le judge pipeline
    negative_explicit (👎) → exclu du dataset d'entraînement
    """
    content_hash = body.get("content_hash", "")
    flag = body.get("flag", "")
    _VALID = {"positive_explicit", "negative_explicit"}
    if flag not in _VALID:
        raise HTTPException(422, f"flag invalide (valides: {_VALID})")
    if not content_hash or len(content_hash) != 16:
        raise HTTPException(422, "content_hash invalide")

    try:
        from src.learning.conversation_logger import update_quality_flag
        updated = update_quality_flag(content_hash, flag)
        return {"success": True, "updated": updated, "hash": content_hash, "flag": flag}
    except Exception as e:
        raise HTTPException(500, str(e))


# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
