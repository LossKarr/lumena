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


# =====================================================================
# Pipeline helper functions (extracted from server.py L1216-2013)
# =====================================================================

def _normalize_channel(channel: Optional[str]) -> str:
    value = (channel or "web").strip().lower()
    allowed = {"web", "ide", "telegram", "discord", "api", "whatsapp"}
    return value if value in allowed else "web"


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
    if request.ide_session_id:
        return f"conv:ide_session:{request.ide_session_id.strip().lower()}"
    normalized_client = (client_name or "").strip().lower()
    if normalized_client and normalized_client != "unknown":
        return f"conv:client:{normalized_client}"
    return f"conv:channel:{channel}"


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
    workspace_hint_parent = _normalize_existing_parent_dir(request.workspace_path)
    active_file_path = _normalize_existing_file(request.active_file_path)
    open_files = []
    for item in (request.open_files or [])[:30]:
        normalized = _normalize_existing_file(item)
        if normalized:
            open_files.append(normalized)

    incoming = {
        "workspace_path": _infer_workspace_path(
            workspace_path=workspace_path,
            workspace_hint_parent=workspace_hint_parent,
            active_file_path=active_file_path,
            open_files=open_files,
        ),
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

    if not merged.get("workspace_path"):
        merged["workspace_path"] = _infer_workspace_path(
            workspace_path=None,
            workspace_hint_parent=workspace_hint_parent,
            active_file_path=merged.get("active_file_path"),
            open_files=merged.get("open_files") or [],
        )
    if not merged.get("workspace_path"):
        merged["workspace_path"] = DEFAULT_WORKSPACE_PATH

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


def _apply_workspace_policy(
    request: ChatRequest,
    channel: str,
    ide_context: Dict[str, Any],
) -> Dict[str, Any]:
    if not (WORKSPACE_POLICY_V2_ENABLED and deps.RUNTIME_AVAILABLE and deps.resolve_workspace_for_request is not None):
        return {
            **ide_context,
            "resolved_date": datetime.now().strftime("%Y-%m-%d"),
            "resolution_reason": "legacy_ide_context",
            "workspace_policy": str(request.workspace_policy or "default").strip().lower() or "default",
        }

    requested_workspace = (request.workspace_path or "").strip() or ide_context.get("workspace_path")
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

    resolved = deps.resolve_workspace_for_request(
        workspace_policy=request.workspace_policy,
        requested_workspace=requested_workspace,
        default_workspace=DEFAULT_WORKSPACE_PATH,
        active_file_path=active_file_path,
        open_files=open_files,
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
    # .doc/.docx — just note presence, no heavy lib
    if ext in {".doc", ".docx"}:
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
            }
        except asyncio.TimeoutError as exc:
            if attempts <= max_retries:
                continue
            raise TimeoutError(
                f"step_timeout_after_{attempts}_attempts_{step_timeout_sec:.1f}s"
            ) from exc


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
    ide_context = _apply_workspace_policy(request, channel, _extract_ide_context(request, channel))
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
    tool_runtime_tokens = _push_tool_runtime_context(ide_context)

    try:
        call_method = "think_and_act" if request.use_agent else "chat"
        # Inject attachment context into message if files were uploaded
        effective_message = _build_effective_message(request.message, request.attachments)
        if request.use_agent:
            # Mode agent - on peut capturer les tool calls
            thinking_steps.append({"step": "thinking", "content": "Analyse de la requete..."})

            # Verifier si des outils seront utilises
            if deps.lumena.tool_system:
                # Simuler la detection d'outils
                keywords = {
                    "recherche": "web_search",
                    "google": "web_search",
                    "cherche": "web_search",
                    "fichier": "read_file",
                    "ouvre": "open_application",
                    "code": "code_search",
                    "memoire": "memory_recall",
                    "souviens": "memory_recall"
                }

                for kw, tool in keywords.items():
                    if kw in request.message.lower():
                        tool_calls.append({
                            "tool": tool,
                            "args": {"query": request.message},
                            "status": "executing"
                        })
                        thinking_steps.append({"step": "tool", "content": f"Utilisation de {tool}..."})

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

            # Marquer les outils comme termines
            for tc in tool_calls:
                tc["status"] = "success"
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
        llm_meta = _get_llm_meta()
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
        ide_context = _apply_workspace_policy(request, channel, _extract_ide_context(request, channel))
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

        # Envoyer le debut
        effective_message = _build_effective_message(request.message, request.attachments)
        yield _emit({"type": "start", "content": "Debut de la reflexion..."})

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
                            if len(thoughts_captured) > max_thoughts:
                                del thoughts_captured[: len(thoughts_captured) - max_thoughts]

                handler_id = stream_logger.add(capture_thought, format="{message}", level="DEBUG")

                try:
                    # Executer think_and_act dans un thread separe pour pouvoir streamer
                    result_container = {"response": None, "error": None, "call_meta": {}}

                    def run_agent():
                        local_tokens = {}
                        local_tool_tokens: Dict[str, Any] = {}
                        local_runtime_token = None
                        loop = None
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
                        if _task_orchestrator_enabled() and task_id and deps._TASK_ORCHESTRATOR.is_cancel_requested(task_id):  # type: ignore[union-attr]
                            result_container["error"] = "task_cancelled"
                            break
                        if global_timeout_sec > 0 and (time.time() - started_at) >= global_timeout_sec:
                            result_container["error"] = f"global_stream_timeout_{global_timeout_sec:.1f}s"
                            _request_task_cancel(task_id, result_container["error"])
                            break

                        # Envoyer les nouvelles pensees
                        new_thoughts: List[str] = []
                        with thoughts_lock:
                            if len(thoughts_captured) > last_count:
                                new_thoughts = thoughts_captured[last_count:]
                                last_count = len(thoughts_captured)
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
                                "thoughts": last_count,
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
                        if len(thoughts_captured) > last_count:
                            _remaining_thoughts = thoughts_captured[last_count:]
                            last_count = len(thoughts_captured)
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
                        _record_slo_outcome(
                            success=False,
                            latency_ms=int((time.perf_counter() - request_started_perf) * 1000),
                            timeout_unrecovered=_request_timeout_error(Exception(str(result_container["error"]))),
                            resumed=False,
                            workspace_error=workspace_resolution_error,
                            undo_success=None,
                        )
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
                try:
                    if global_timeout_sec > 0:
                        chat_coro_task = asyncio.ensure_future(
                            asyncio.wait_for(call_coro, timeout=global_timeout_sec)
                        )
                    else:
                        chat_coro_task = asyncio.ensure_future(call_coro)

                    while not chat_coro_task.done():
                        await asyncio.sleep(0.1)
                        # Drain heartbeat queue
                        while not _chat_hb_queue.empty():
                            try:
                                hb_ts = _chat_hb_queue.get_nowait()
                                yield _emit({"type": "heartbeat", "ts": hb_ts})
                                chat_last_emit_ref[0] = time.time()
                            except asyncio.QueueEmpty:
                                break

                    # Retrieve result or re-raise exception
                    response, call_meta = chat_coro_task.result()
                finally:
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
            llm_meta = _get_llm_meta()
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
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
