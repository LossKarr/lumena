"""
Lifespan management for the Lumena FastAPI application.

Handles startup (Lumena init, Telegram, Discord, IDE bridge, Voice, Autonomy)
and shutdown (graceful cleanup of all subsystems).
"""
import os
import sys
import uuid
import json
import asyncio
import threading
import time
from pathlib import Path
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from loguru import logger
from fastapi import FastAPI

from src.core import LumenaCore, initialize_lumena
from src.utils.file_lock import ProcessFileLock, default_lock_path
from web.routes import deps

from src.utils.paths import ROOT_DIR, DATA_DIR, WORKSPACE_DIR, INSTANCE_ROLE, MULTI_INSTANCE_ENABLED

# ── Instance role ── (primary | worker | standalone) ─────────────────────────
_INSTANCE_ROLE: str = INSTANCE_ROLE
_IS_WORKER: bool = _INSTANCE_ROLE == "worker"

_PROJECT_ROOT = ROOT_DIR


# ── Env helpers ──────────────────────────────────────────────────────────────

def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _channel_msg_metadata(msg: Any) -> Dict[str, Any]:
    metadata = getattr(msg, "metadata", {}) or {}
    return metadata if isinstance(metadata, dict) else {}


def _channel_msg_conversation_id(channel: str, msg: Any, fallback_id: Optional[str] = None) -> str:
    metadata = _channel_msg_metadata(msg)
    explicit = str(metadata.get("conversation_id") or "").strip()
    if explicit:
        return explicit
    base = (
        fallback_id
        or getattr(msg, "chat_id", None)
        or getattr(msg, "channel_id", None)
        or getattr(msg, "user_id", None)
        or uuid.uuid4().hex
    )
    return f"{channel}_chat_{base}"


def _channel_msg_user_id(channel: str, msg: Any, fallback_id: Optional[str] = None) -> str:
    user_id = fallback_id or getattr(msg, "user_id", None) or getattr(msg, "chat_id", None) or "unknown"
    return f"{channel}:{user_id}"


def _record_channel_session_message(
    *,
    channel: str,
    client: str,
    conversation_id: str,
    role: str,
    content: str,
    user_id: str,
    message_id: Optional[str] = None,
    status: str = "running",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    store = getattr(deps, "_SESSION_STORE", None)
    conv_id = str(conversation_id or "").strip()
    if store is None or not conv_id:
        return
    try:
        store.record_message(
            conversation_id=conv_id,
            role=role,
            content=content or "",
            channel=channel,
            client=client,
            user_id=user_id or f"{channel}:unknown",
            message_id=message_id,
            request_id=message_id,
            status=status,
            metadata=metadata or {},
        )
        store.record_event(
            conversation_id=conv_id,
            event_type="request_started" if role == "user" else "response_sent",
            status=status,
            summary=content or "",
            channel=channel,
            client=client,
            user_id=user_id or f"{channel}:unknown",
            request_id=message_id,
            metadata=metadata or {},
        )
    except Exception as exc:
        logger.debug(f"session_store: external channel record skipped: {exc}")


def _record_channel_session_error(
    *,
    channel: str,
    client: str,
    conversation_id: str,
    user_id: str,
    error: Any,
    message_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    store = getattr(deps, "_SESSION_STORE", None)
    conv_id = str(conversation_id or "").strip()
    if store is None or not conv_id:
        return
    try:
        store.record_event(
            conversation_id=conv_id,
            event_type="request_failed",
            status="error",
            summary=str(error),
            channel=channel,
            client=client,
            user_id=user_id or f"{channel}:unknown",
            request_id=message_id,
            metadata=metadata or {},
        )
    except Exception as exc:
        logger.debug(f"session_store: external channel error skipped: {exc}")


# ── TG mode helpers ─────────────────────────────────────────────────────────

def _normalize_tg_mode(raw: Optional[str]) -> str:
    mode = str(raw or "").strip().lower()
    if mode in {"agent", "chat", "auto"}:
        return mode
    return "auto"


def _load_tg_mode_state() -> None:
    with deps._TG_MODE_STATE_LOCK:
        if deps._TG_MODE_STATE_LOADED:
            return
        deps._TG_MODE_STATE_LOADED = True
        try:
            if not deps._TG_MODE_STATE_FILE.exists():
                return
            payload = json.loads(deps._TG_MODE_STATE_FILE.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return
            deps._TG_MODE_STATE.clear()
            for chat_id, mode in payload.items():
                deps._TG_MODE_STATE[str(chat_id)] = _normalize_tg_mode(str(mode))
        except Exception as exc:
            logger.debug(f"TG mode state load skipped: {exc}")


def _save_tg_mode_state(snapshot: Dict[str, str]) -> None:
    try:
        deps._TG_MODE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        temp_path = deps._TG_MODE_STATE_FILE.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(deps._TG_MODE_STATE_FILE)
    except Exception as exc:
        logger.debug(f"TG mode state save skipped: {exc}")


def _get_tg_mode(chat_id: str) -> str:
    _load_tg_mode_state()
    with deps._TG_MODE_STATE_LOCK:
        return deps._TG_MODE_STATE.get(str(chat_id), "auto")


def _set_tg_mode(chat_id: str, mode: str) -> str:
    _load_tg_mode_state()
    normalized = _normalize_tg_mode(mode)
    with deps._TG_MODE_STATE_LOCK:
        key = str(chat_id)
        if normalized == "auto":
            deps._TG_MODE_STATE.pop(key, None)
        else:
            deps._TG_MODE_STATE[key] = normalized
        snapshot = dict(deps._TG_MODE_STATE)
    _save_tg_mode_state(snapshot)
    return normalized


def _trim_mode_remainder(remainder: str) -> str:
    raw = str(remainder or "").strip(" \t:;,.!?-")
    if not raw:
        return ""
    simplified = raw.lower().replace(chr(8217), "'")
    meta_only = {
        "et reste y",
        "reste y",
        "et reste",
        "stp",
        "svp",
        "ok",
        "okay",
        "merci",
        "maintenant",
        "jusqu'a nouvel ordre",
        "jusqu'à nouvel ordre",
    }
    return "" if simplified in meta_only else raw


def _extract_mode_prefix_remainder(raw_text: str, prefix: str) -> Optional[str]:
    lowered = raw_text.lower().replace(chr(8217), "'")
    normalized_prefix = prefix.lower().replace(chr(8217), "'")
    if not lowered.startswith(normalized_prefix):
        return None
    remainder = raw_text[len(prefix):]
    return _trim_mode_remainder(remainder)


def _drop_bot_call_prefix(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    lowered = text.lower().replace(chr(8217), "'")
    for prefix in ("lumena", "lumi", "hey lumena", "hey lumi"):
        normalized_prefix = prefix.replace(chr(8217), "'")
        if lowered.startswith(normalized_prefix):
            remainder = text[len(prefix):].lstrip(" ,:;-")
            if remainder:
                return remainder
    return text


def _parse_telegram_mode_control(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {"mode": None, "status_only": False, "one_shot": False, "remainder": ""}

    normalized = raw.lower().replace(chr(8217), "'")
    if normalized in {
        "/mode",
        "mode ?",
        "mode?",
        "mode actuel",
        "quel mode",
        "tu es en quel mode",
        "t'es en quel mode",
        "tu es dans quel mode",
    }:
        return {"mode": None, "status_only": True, "one_shot": False, "remainder": ""}

    one_shot_markers = (
        "pour ce message",
        "juste ce message",
        "uniquement ce message",
        "pour cette fois",
        "juste pour cette fois",
        "cette fois",
    )
    one_shot = any(marker in normalized for marker in one_shot_markers)

    prefixes = [
        ("/mode agent", "agent"),
        ("/agent", "agent"),
        ("passe en mode agent", "agent"),
        ("reste en mode agent", "agent"),
        ("reste en agent", "agent"),
        ("toujours en mode agent", "agent"),
        ("toujours agent", "agent"),
        ("mode agent", "agent"),
        ("/mode chat", "chat"),
        ("/chat", "chat"),
        ("passe en mode chat", "chat"),
        ("reste en mode chat", "chat"),
        ("reste en chat", "chat"),
        ("reponds en mode chat", "chat"),
        ("reponds normal", "chat"),
        ("mode normal", "chat"),
        ("mode chat", "chat"),
        ("/mode auto", "auto"),
        ("/auto", "auto"),
        ("passe en mode auto", "auto"),
        ("remets en mode auto", "auto"),
        ("detection auto", "auto"),
        ("mode auto", "auto"),
    ]

    candidates = [raw]
    stripped_call = _drop_bot_call_prefix(raw)
    if stripped_call != raw:
        candidates.append(stripped_call)

    for candidate in candidates:
        for prefix, mode in prefixes:
            remainder = _extract_mode_prefix_remainder(candidate, prefix)
            if remainder is None:
                continue
            return {
                "mode": mode,
                "status_only": False,
                "one_shot": one_shot,
                "remainder": remainder,
            }

    return {"mode": None, "status_only": False, "one_shot": False, "remainder": ""}


def _format_tg_mode_ack(mode: str, one_shot: bool = False) -> str:
    normalized = _normalize_tg_mode(mode)
    if one_shot:
        if normalized == "agent":
            return "OK, je passe en mode Agent pour ce message uniquement."
        if normalized == "chat":
            return "OK, je passe en mode Chat pour ce message uniquement."
        return "OK, je repasse en mode Auto pour ce message."

    if normalized == "agent":
        return "OK, je reste en mode Agent pour ce chat jusqu'a nouvel ordre."
    if normalized == "chat":
        return "OK, je reste en mode Chat pour ce chat jusqu'a nouvel ordre."
    return "OK, je repasse en mode Auto pour ce chat."


# ── Env & path helpers ───────────────────────────────────────────────────────

def _resolve_lock_path(env_var: str, default_filename: str) -> Path:
    fallback = str(default_lock_path(default_filename))
    return Path(os.getenv(env_var, fallback))


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(str(raw).strip()))
    except Exception:
        return default


def _env_non_negative_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return max(0, int(default))
    try:
        return max(0, int(str(raw).strip()))
    except Exception:
        return max(0, int(default))


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None:
        return max(minimum, float(default))
    try:
        return max(minimum, float(str(raw).strip()))
    except Exception:
        return max(minimum, float(default))


def _iso_to_timestamp(value: Optional[str]) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        normalized = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except Exception:
        return 0.0


def _resolve_default_workspace_path() -> str:
    base = _PROJECT_ROOT
    raw = os.getenv("LUMENA_DEFAULT_WORKSPACE", "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = (base / candidate).resolve()
        else:
            candidate = candidate.resolve()
    else:
        from src.utils.paths import WORKSPACE_DIR
        candidate = WORKSPACE_DIR
    try:
        candidate.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return str(candidate)


# ── Module-level constants (computed once at import time) ────────────────────

DEFAULT_WORKSPACE_PATH = _resolve_default_workspace_path()
RUNTIME_CONTEXT_V2_ENABLED = _env_flag("LUMENA_RUNTIME_CONTEXT_V2", True)
WORKSPACE_POLICY_V2_ENABLED = _env_flag("LUMENA_WORKSPACE_POLICY_V2", True)
TASK_ORCHESTRATOR_V1_ENABLED = _env_flag("LUMENA_TASK_ORCHESTRATOR_V1", False)
STREAM_EVENT_V2_ENABLED = _env_flag("LUMENA_STREAM_EVENT_V2", True)
OMNICHANNEL_ENVELOPE_V1_ENABLED = _env_flag("LUMENA_OMNICHANNEL_ENVELOPE_V1", True)
AUTONOMY_ON_WEB_ENABLED = _env_flag("LUMENA_WEB_AUTONOMY_ENABLED", True)


# ── Autonomy helpers ─────────────────────────────────────────────────────────

def _get_autonomy_daemon_instance() -> Optional[Any]:
    if deps._AUTONOMY_DAEMON is not None:
        return deps._AUTONOMY_DAEMON
    if not (deps.AUTONOMY_DAEMON_AVAILABLE and callable(deps.get_daemon)):
        return None
    try:
        data_dir = DATA_DIR
        deps._AUTONOMY_DAEMON = deps.get_daemon(data_dir)
    except Exception:
        deps._AUTONOMY_DAEMON = None
    return deps._AUTONOMY_DAEMON


async def _start_autonomy_daemon_if_enabled() -> None:
    if _IS_WORKER:
        print("[AUTONOMY] Skipped — instance role=worker (scheduler et daemon désactivés)")
        deps._AUTONOMY_LAST_ERROR = "worker_role"
        return
    if not AUTONOMY_ON_WEB_ENABLED:
        deps._AUTONOMY_LAST_ERROR = None
        return
    daemon = _get_autonomy_daemon_instance()
    if daemon is None:
        deps._AUTONOMY_LAST_ERROR = "autonomy daemon unavailable"
        return
    try:
        if not bool(getattr(daemon, "running", False)):
            await daemon.start()
            deps._AUTONOMY_STARTED_BY_WEB = True
        deps._AUTONOMY_LAST_ERROR = None
    except Exception as e:
        deps._AUTONOMY_LAST_ERROR = str(e)


async def _stop_autonomy_daemon_if_started() -> None:
    daemon = _get_autonomy_daemon_instance()
    if daemon is None:
        deps._AUTONOMY_STARTED_BY_WEB = False
        return
    if not deps._AUTONOMY_STARTED_BY_WEB:
        return
    try:
        await daemon.stop()
    except Exception:
        pass
    finally:
        deps._AUTONOMY_STARTED_BY_WEB = False


def _notify_autonomy_user_interaction(message: str) -> None:
    daemon = _get_autonomy_daemon_instance()
    if daemon is None:
        return
    interaction_fn = getattr(daemon, "user_interaction", None)
    if not callable(interaction_fn):
        return
    try:
        interaction_fn(message)
    except Exception:
        pass


def _get_autonomy_meta() -> Dict[str, Any]:
    base = {
        "autonomy_enabled_on_web": bool(AUTONOMY_ON_WEB_ENABLED),
        "autonomy_available": bool(deps.AUTONOMY_DAEMON_AVAILABLE),
        "autonomy_running": False,
        "autonomy_action_execution": False,
        "autonomy_actions_last_hour": 0,
        "autonomy_user_present": False,
        "autonomy_uptime": None,
        "autonomy_last_error": deps._AUTONOMY_LAST_ERROR,
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


# ── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestion du cycle de vie de l'application (startup/shutdown)."""
    _stripe_bg_task = None
    _n8n_bg_task = None
    _peer_network_task = None
    _heartbeat_task = None  # stocké pour cancellation propre au shutdown

    try:
        # ensure_instance_id() DOIT être appelé avant le lock pour garantir
        # que l'INSTANCE_ID stable (depuis .env) est utilisé partout.
        from src.utils.paths import ensure_instance_id as _ensure_iid
        _stable_iid = _ensure_iid()
        deps.INSTANCE_ID = _stable_iid  # mise à jour du singleton deps

        if _env_flag("LUMENA_SINGLE_INSTANCE", True):
            # Multi-instance : lock nommé par instance_id stable pour éviter les collisions
            if MULTI_INSTANCE_ENABLED:
                _lock_filename = f"lumena_web_{_stable_iid}.lock"
            else:
                _lock_filename = "lumena_web.lock"
            lock_path = _resolve_lock_path("LUMENA_INSTANCE_LOCK_PATH", _lock_filename)
            deps.instance_lock = ProcessFileLock(
                lock_path,
                lock_name="lumena-web",
                owner_id=f"web:{_stable_iid}",
            )
            if not deps.instance_lock.acquire():
                holder = deps.instance_lock.read_lock_info()
                holder_pid = holder.get("pid", "unknown")
                raise RuntimeError(
                    f"Lumena web already running (lock={lock_path}, owner_pid={holder_pid})"
                )
            print(f"[LOCK] Instance lock acquired: {lock_path} (id={_stable_iid})")

        # === STARTUP ===

        # --- Boot-time .env backup (rotation 10) ---
        _env_path = _PROJECT_ROOT / ".env"
        if _env_path.exists() and _env_path.stat().st_size > 0:
            _backup_dir = DATA_DIR / "env_backups"
            _backup_dir.mkdir(parents=True, exist_ok=True)
            _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            import shutil
            shutil.copy2(_env_path, _backup_dir / f".env.{_ts}")
            # Rotation: keep only 10 newest
            _existing = sorted(_backup_dir.glob(".env.*"), key=lambda p: p.stat().st_mtime)
            for _old in _existing[:-10]:
                _old.unlink(missing_ok=True)
            logger.info("[BOOT] .env backup -> data/env_backups/.env.{}", _ts)

        # Instance ID auto-gen + répertoires critiques
        from src.utils.paths import ensure_instance_id, validate_instance_dirs
        _iid = ensure_instance_id()
        _dir_errors = validate_instance_dirs(create=True)
        if _dir_errors:
            logger.warning("[PATHS] Répertoires non créés : {}", _dir_errors)
        else:
            logger.debug("[PATHS] Instance {} — répertoires OK", _iid)

        print(" Initialisation de Lumena...")
        _setup_complete = os.getenv("LUMENA_SETUP_COMPLETE", "").strip() == "1"

        # Enregistrer dynamiquement les modèles Ollama installés
        try:
            from src.llm.providers import sync_ollama_models
            _ollama_count = sync_ollama_models()
            if _ollama_count:
                print(f" {_ollama_count} modele(s) Ollama detecte(s)")
        except Exception as _oe:
            logger.debug("[BOOT] sync_ollama_models skip: {}", _oe)

        try:
            deps.lumena = await initialize_lumena()
            initialized = deps.lumena.is_initialized
        except Exception as _init_err:
            initialized = False
            logger.error("[BOOT] initialize_lumena() a échoué: {}", _init_err)

        if not initialized and not _setup_complete:
            # P0: Premier lancement sans .env / sans LLM configuré → mode setup-only
            # Le serveur tourne, le wizard est accessible, mais le chat est désactivé
            deps.setup_only_mode = True
            print(" Mode setup-only — completez le wizard pour activer Lumena")
            logger.warning(
                "[BOOT] Lumena non initialisée (LUMENA_SETUP_COMPLETE absent). "
                "Mode setup-only actif — wizard accessible sur /setup"
            )
        elif not initialized and _setup_complete:
            # P0.9: Setup terminé mais init échoue → repassage en mode setup (recovery)
            # Au lieu de crash, on permet à l'utilisateur de re-configurer via le wizard
            deps.setup_only_mode = True
            logger.warning(
                "[BOOT] LLM indisponible malgré LUMENA_SETUP_COMPLETE=1. "
                "Repassage en mode setup-only pour permettre la reconfiguration."
            )
            print(" Mode setup-only (recovery) — LLM indisponible, reconfiguration possible via le wizard")
        else:
            deps.setup_only_mode = False
            print(" Lumena prete!")

        # P0.3.4: warning if auth not configured after setup
        if os.getenv("LUMENA_SETUP_COMPLETE", "") == "1" and not os.getenv("LUMENA_ADMIN_TOKEN", "").strip():
            logger.warning(
                "⚠ LUMENA_ADMIN_TOKEN est vide alors que le setup est terminé. "
                "Les routes protégées refuseront tout accès (401). "
                "Configurez LUMENA_ADMIN_TOKEN dans .env."
            )

        # Phase 20B-1 : singleton ApprovalQueue partagé pour les mutations UI.
        # Init au startup pour que ApprovalQueue.approve/reject voient le même
        # état entre requêtes (la queue persiste sur disque ; le singleton évite
        # les races sur les caches internes). Si le module n'est pas
        # importable, le singleton reste None : les routes mutatives Phase
        # 20B-1 répondent {"error_code": "queue_unavailable"}.
        try:
            from src.mcp.approval_queue import ApprovalQueue as _MCP_ApprovalQueue
            deps._MCP_APPROVAL_QUEUE_SINGLETON = _MCP_ApprovalQueue()
            print("[MCP] ApprovalQueue singleton initialisé (Phase 20B-1)")
        except Exception as _mcp_aq_err:
            deps._MCP_APPROVAL_QUEUE_SINGLETON = None
            logger.debug("[MCP] ApprovalQueue singleton non initialisé: {}", _mcp_aq_err)

        # Phase 20B-2 : singleton MCPServerCatalog SÉPARÉ (source de vérité Phase 14).
        # Aucun helper ne doit accéder au catalog via un attribut privé d'un
        # ou autre attribut privé. Le Catalog est sa propre instance partagée.
        try:
            from src.mcp.server_catalog import MCPServerCatalog as _MCP_ServerCatalog
            deps._MCP_SERVER_CATALOG_SINGLETON = _MCP_ServerCatalog()
            print("[MCP] ServerCatalog singleton initialisé (Phase 20B-2)")
        except Exception as _mcp_cat_err:
            deps._MCP_SERVER_CATALOG_SINGLETON = None
            logger.debug("[MCP] ServerCatalog singleton non initialisé: {}", _mcp_cat_err)

        # Phase 20B-2 : singleton MCPInstallOrchestrator partagé.
        # Construit via singletons publics catalog + approval_queue uniquement
        # (aucun accès à des attributs privés). dry_run reflète l'état initial
        # de LUMENA_MCP_LIVE ; les handlers reconstruisent à la demande si le
        # mode bascule (le coût d'instanciation est négligeable).
        try:
            from src.mcp.install_orchestrator import (
                MCPInstallOrchestrator as _MCP_InstallOrchestrator,
            )
            _mcp_catalog_for_install = deps._MCP_SERVER_CATALOG_SINGLETON
            _mcp_queue_for_install = deps._MCP_APPROVAL_QUEUE_SINGLETON
            if (_mcp_catalog_for_install is not None
                    and _mcp_queue_for_install is not None):
                _mcp_live_init = os.environ.get(
                    "LUMENA_MCP_LIVE", ""
                ).strip().lower() in ("1", "true", "yes", "on")
                deps._MCP_INSTALL_ORCHESTRATOR_SINGLETON = _MCP_InstallOrchestrator(
                    catalog=_mcp_catalog_for_install,
                    approval_queue=_mcp_queue_for_install,
                    dry_run=not _mcp_live_init,
                )
                print(
                    "[MCP] InstallOrchestrator singleton initialisé "
                    f"(Phase 20B-2, dry_run={not _mcp_live_init})"
                )
            else:
                deps._MCP_INSTALL_ORCHESTRATOR_SINGLETON = None
                logger.debug(
                    "[MCP] InstallOrchestrator non initialisé : "
                    "catalog ou approval_queue indisponible"
                )
        except Exception as _mcp_inst_err:
            deps._MCP_INSTALL_ORCHESTRATOR_SINGLETON = None
            logger.debug(
                "[MCP] InstallOrchestrator singleton non initialisé: {}",
                _mcp_inst_err,
            )

        # Phase 20B-3 : singleton RuntimeWatcher (snapshots + runners inter-requêtes).
        try:
            from src.mcp.runtime_watcher import RuntimeWatcher as _MCP_RuntimeWatcher
            deps._MCP_RUNTIME_WATCHER_SINGLETON = _MCP_RuntimeWatcher()
            print("[MCP] RuntimeWatcher singleton initialisé (Phase 20B-3)")
        except Exception as _mcp_rw_err:
            deps._MCP_RUNTIME_WATCHER_SINGLETON = None
            logger.debug(
                "[MCP] RuntimeWatcher singleton non initialisé: {}", _mcp_rw_err
            )

        # ── Phase I-1 : pré-création du sandbox MCP + tool_registry forcé ──
        # 1) data/mcp/ : sandbox root où les serveurs MCP sont installés isolés.
        try:
            _mcp_root_env = os.environ.get("LUMENA_MCP_ROOT", "").strip()
            from src.utils.paths import DATA_DIR as _DATA_DIR
            _mcp_root_path = (
                Path(_mcp_root_env) if _mcp_root_env
                else _DATA_DIR / "mcp"
            )
            _mcp_root_path.mkdir(parents=True, exist_ok=True)
            print(f"[MCP] Sandbox root prêt: {_mcp_root_path}")
        except Exception as _mcp_root_err:
            logger.debug(
                "[MCP] Sandbox root non créé: {}", _mcp_root_err
            )

        # 2) lumena._tool_registry forcé au boot pour que _build_activation_service
        # puisse récupérer le registry_writer. Sans ça, ActivationService=None
        # jusqu'au premier chat (bug timing Phase 20B-3).
        try:
            if (deps.lumena is not None
                    and getattr(deps.lumena, "_tool_registry", None) is None):
                from src.reasoning.tool_registry import ToolRegistry as _BootTR
                _boot_tr = _BootTR(lumena=deps.lumena)
                deps.lumena._tool_registry = _boot_tr
                # Lier au tool_system si dispo (cohérence avec agent_service.py).
                _ts = getattr(deps.lumena, "tool_system", None)
                if _ts is not None and hasattr(_ts, "bind_tool_registry"):
                    try:
                        _ts.bind_tool_registry(_boot_tr)
                    except Exception:  # noqa: BLE001
                        pass
                print(
                    f"[MCP] tool_registry forcé au boot "
                    f"({len(_boot_tr.tools)} tools chargés)"
                )
        except Exception as _mcp_tr_err:
            logger.debug(
                "[MCP] tool_registry boot-init non créé: {}", _mcp_tr_err
            )

        # Phase 20B-3 : singleton MCPActivationService.
        # Construit via les dépendances publiques :
        #   - catalog + approval_queue : singletons 20B-1/20B-2
        #   - install_orchestrator : pour install_root (cohérence Phase 18)
        #   - runtime_watcher : singleton 20B-3
        #   - discovery (require_server_callable=False), adapter, registry_writer
        #     (runtime Lumena tool_registry), runner_factory, client_factory :
        #     résolus dans la factory _build_activation_service côté mcp.py.
        # Au boot, on tente une construction simple via la factory exposée par
        # le module mcp.py. Si une dépendance manque (ex: lumena.tool_system
        # pas encore prêt), le singleton reste None et les handlers
        # reconstruisent à la demande.
        try:
            from web.routes.mcp import _build_activation_service as _mcp_build_act
            _mcp_live_init = os.environ.get(
                "LUMENA_MCP_LIVE", ""
            ).strip().lower() in ("1", "true", "yes", "on")
            if (deps._MCP_SERVER_CATALOG_SINGLETON is not None
                    and deps._MCP_APPROVAL_QUEUE_SINGLETON is not None
                    and deps._MCP_INSTALL_ORCHESTRATOR_SINGLETON is not None
                    and deps._MCP_RUNTIME_WATCHER_SINGLETON is not None):
                deps._MCP_ACTIVATION_SERVICE_SINGLETON = _mcp_build_act(
                    catalog=deps._MCP_SERVER_CATALOG_SINGLETON,
                    queue=deps._MCP_APPROVAL_QUEUE_SINGLETON,
                    install_orchestrator=deps._MCP_INSTALL_ORCHESTRATOR_SINGLETON,
                    runtime_watcher=deps._MCP_RUNTIME_WATCHER_SINGLETON,
                    dry_run=not _mcp_live_init,
                )
                if deps._MCP_ACTIVATION_SERVICE_SINGLETON is not None:
                    print(
                        "[MCP] ActivationService singleton initialisé "
                        f"(Phase 20B-3, dry_run={not _mcp_live_init})"
                    )
                else:
                    logger.debug(
                        "[MCP] ActivationService singleton non initialisé : "
                        "factory a retourné None (dépendance Lumena tool_registry "
                        "probablement indisponible — handlers reconstruiront)"
                    )
            else:
                deps._MCP_ACTIVATION_SERVICE_SINGLETON = None
                logger.debug(
                    "[MCP] ActivationService singleton non initialisé : "
                    "singletons amont incomplets"
                )
        except Exception as _mcp_act_err:
            deps._MCP_ACTIVATION_SERVICE_SINGLETON = None
            logger.debug(
                "[MCP] ActivationService singleton non initialisé: {}",
                _mcp_act_err,
            )

        # Fix Q (Phase I-7) : auto-activate au boot DÉPLACÉ après Phase I-6.
        # L'ancienne position (juste après création ActivationService) faisait
        # tourner Fix K avant l'initialisation des credentials/config services
        # → SLACK_BOT_TOKEN jamais résolu → client_initialize_failed garanti.
        # Voir bloc auto-activate après "Phase I-6 initialisés" plus bas.

        # Phase 20B-5 : singleton AutoApproveEngine (CRUD patterns Phase 11).
        # Mutations de policy future — double opt-in obligatoire
        # (LUMENA_MCP_LIVE=1 ET LUMENA_MCP_AUTOAPPROVE_LIVE=1) au moment des
        # routes mutatives. L'init reste sans condition pour exposer GET.
        try:
            from src.mcp.auto_approve import AutoApproveEngine as _MCP_AutoApproveEngine
            deps._MCP_AUTO_APPROVE_ENGINE_SINGLETON = _MCP_AutoApproveEngine()
            print("[MCP] AutoApproveEngine singleton initialisé (Phase 20B-5)")
        except Exception as _mcp_aa_err:
            deps._MCP_AUTO_APPROVE_ENGINE_SINGLETON = None
            logger.debug(
                "[MCP] AutoApproveEngine singleton non initialisé: {}",
                _mcp_aa_err,
            )

        # Phase I-6 : singletons CredentialsService + ConfigService + Resolver
        # + AutonomyOrchestrator. Tous wired ensemble (chaque service unique).
        try:
            from src.services.secrets_service import get_secrets_service
            from src.mcp.credentials_service import MCPCredentialsService
            from src.mcp.config_service import MCPConfigService
            from src.mcp.secrets_resolver_service import MCPSecretsResolverService
            from src.mcp.autonomy_orchestrator import MCPAutonomyOrchestrator
            _sec = get_secrets_service()
            _creds = MCPCredentialsService(_sec)
            _cfg = MCPConfigService()
            _resolver = MCPSecretsResolverService(
                credentials_service=_creds,
                secrets_service=_sec,
            )
            _orch = MCPAutonomyOrchestrator(
                credentials_service=_creds,
                config_service=_cfg,
                secrets_resolver=_resolver,
            )
            deps._MCP_CREDENTIALS_SERVICE_SINGLETON = _creds
            deps._MCP_CONFIG_SERVICE_SINGLETON = _cfg
            deps._MCP_SECRETS_RESOLVER_SINGLETON = _resolver
            deps._MCP_AUTONOMY_ORCHESTRATOR_SINGLETON = _orch
            # Fix Q (Phase I-7) : l'ActivationService a été créé en Phase 20B-3
            # AVANT cette Phase I-6 — il a donc capturé credentials=None /
            # config=None au constructeur. On le patche maintenant pour que
            # activate() puisse résoudre les secrets et les injecter dans
            # runner.start(runtime_env_secrets=...). Sans ce patch, le
            # SLACK_BOT_TOKEN reste invisible au child Node →
            # client_initialize_failed garanti.
            _act_singleton = getattr(
                deps, "_MCP_ACTIVATION_SERVICE_SINGLETON", None,
            )
            if _act_singleton is not None:
                try:
                    _act_singleton._credentials_service = _creds
                    _act_singleton._config_service = _cfg
                except Exception:
                    pass
            print(
                "[MCP] CredentialsService + ConfigService + SecretsResolver "
                "+ AutonomyOrchestrator initialisés (Phase I-6)"
            )

            # ── Fix K+Q (Phase I-7) : auto-activate au boot ───────────────
            # Déplacé ici APRÈS la Phase I-6 pour que les credentials/config
            # soient résolus correctement et injectés dans le runner Node.
            # Garde-fous :
            #   - LUMENA_MCP_AUTOACTIVATE_AT_BOOT=0 désactive (default ON)
            #   - Exception per-server n'arrête pas le scan
            #   - kill switch LUMENA_MCP_ACTIVATION_DISABLED respecté par
            #     ActivationService lui-même
            if _env_flag("LUMENA_MCP_AUTOACTIVATE_AT_BOOT", True):
                _act = deps._MCP_ACTIVATION_SERVICE_SINGLETON
                _cat = deps._MCP_SERVER_CATALOG_SINGLETON
                if _act is not None and _cat is not None:
                    # ── Fix S (Phase I-7) : réconciliation ACTIVE fantôme ──
                    # Un MCP activé lors d'une session précédente reste en
                    # statut ACTIVE dans le catalog persisté, alors que son
                    # process est mort au reboot. Sans reset, le scan
                    # INSTALLED ci-dessous l'ignore ET activate() refuse
                    # (status_not_installed:active) → coincé définitivement.
                    # On reset ACTIVE→INSTALLED pour tout serveur dont le
                    # process ne tourne pas dans cette session.
                    try:
                        from src.mcp.server_catalog import (
                            ServerStatus as _SrvStatus,
                        )
                        _stale = _cat.list_servers(
                            status_filter=_SrvStatus.ACTIVE
                        )
                        for _stale_entry in _stale:
                            _stale_sid = getattr(
                                _stale_entry, "server_id", None,
                            )
                            if not isinstance(_stale_sid, str):
                                continue
                            try:
                                if _act.is_running(_stale_sid):
                                    continue  # vraiment actif, ne pas toucher
                            except Exception:
                                pass
                            try:
                                _cat.update_status(
                                    _stale_sid, _SrvStatus.INSTALLED,
                                )
                                logger.debug(
                                    "[MCP] Fix S: statut ACTIVE fantôme "
                                    "reset → INSTALLED pour '{}'",
                                    _stale_sid,
                                )
                            except Exception:
                                pass
                    except Exception as _reconcile_err:
                        logger.debug(
                            "[MCP] Fix S reconciliation failed: {}",
                            _reconcile_err,
                        )
                    try:
                        from src.mcp.server_catalog import ServerStatus
                        from src.mcp.approval_queue import (
                            ApprovalResult as _BootApprovalResult,
                            ApprovalDecision as _BootApprovalDecision,
                        )
                        _installed = _cat.list_servers(
                            status_filter=ServerStatus.INSTALLED
                        )
                    except Exception as _list_err:
                        logger.debug(
                            "[MCP] auto-activate boot scan failed: {}",
                            _list_err,
                        )
                        _installed = []
                    _ok, _ko = 0, 0
                    for _entry in _installed:
                        _sid = getattr(_entry, "server_id", None)
                        if not isinstance(_sid, str):
                            continue
                        try:
                            _boot_approval = _BootApprovalResult(
                                decision=_BootApprovalDecision.APPROVED,
                                args={
                                    "action": "activate",
                                    "server_id": _sid,
                                    "reason": "boot_auto_activation",
                                },
                                reason="boot_auto_activation",
                            )
                            _res = _act.activate(
                                _sid, approval_result=_boot_approval,
                            )
                            if bool(getattr(_res, "success", False)):
                                _ok += 1
                            else:
                                _ko += 1
                                logger.debug(
                                    "[MCP] auto-activate '{}' failed: {}",
                                    _sid,
                                    getattr(_res, "reason", "?"),
                                )
                        except Exception as _act_err:
                            _ko += 1
                            logger.debug(
                                "[MCP] auto-activate '{}' exception: {}",
                                _sid,
                                _act_err,
                            )
                    if _ok or _ko:
                        print(
                            f"[MCP] Auto-activate boot: {_ok} actif(s), "
                            f"{_ko} échec(s) (Fix K+Q)"
                        )
        except Exception as _mcp_i6_err:
            deps._MCP_CREDENTIALS_SERVICE_SINGLETON = None
            deps._MCP_CONFIG_SERVICE_SINGLETON = None
            deps._MCP_SECRETS_RESOLVER_SINGLETON = None
            deps._MCP_AUTONOMY_ORCHESTRATOR_SINGLETON = None
            logger.debug(
                "[MCP] Phase I-6 services non initialisés: {}",
                _mcp_i6_err,
            )

        # Phase 26 : ReAct <-> MCP loop integration. Disabled by default.
        try:
            if _env_flag("LUMENA_MCP_REACT_INTEGRATION_ENABLED", False):
                from src.mcp.react_integration import (
                    MCPReActIntegration,
                    MCPReActIntegrationDeps,
                )
                from src.mcp.local_creation_ticket import (
                    MCPLocalCreationTicketOrchestrator,
                )
                from src.mcp.local_creation_executor import (
                    MCPLocalCreationExecutor,
                )
                from src.mcp.catalog_add_orchestrator import (
                    MCPCatalogAddOrchestrator,
                )

                _discovery_reports_dir = DATA_DIR / "mcp_discovery_reports"
                if deps.lumena is None:
                    raise RuntimeError("lumena_unavailable")
                _mcp_approval_queue_for_react = (
                    deps.get_mcp_approval_queue_singleton()
                )
                _mcp_catalog_for_react = deps.get_mcp_server_catalog_singleton()
                _mcp_local_creation_orchestrator = (
                    MCPLocalCreationTicketOrchestrator(
                        _mcp_approval_queue_for_react
                    )
                    if _mcp_approval_queue_for_react is not None
                    else None
                )
                _mcp_local_creation_executor = (
                    MCPLocalCreationExecutor(catalog=_mcp_catalog_for_react)
                    if _mcp_catalog_for_react is not None
                    else None
                )
                _mcp_catalog_add_orchestrator = (
                    MCPCatalogAddOrchestrator(
                        catalog=_mcp_catalog_for_react,
                        approval_queue=_mcp_approval_queue_for_react,
                    )
                    if (
                        _mcp_catalog_for_react is not None
                        and _mcp_approval_queue_for_react is not None
                    )
                    else None
                )
                # Phase I-7 : expose le singleton pour la route /approve
                # afin qu'elle puisse dispatcher mcp_catalog_add:* vers
                # execute_approved_catalog_add().
                deps._MCP_CATALOG_ADD_ORCHESTRATOR_SINGLETON = (
                    _mcp_catalog_add_orchestrator
                )
                deps.lumena.mcp_react_integration = MCPReActIntegration(
                    deps=MCPReActIntegrationDeps(
                        catalog=_mcp_catalog_for_react,
                        approval_queue=_mcp_approval_queue_for_react,
                        catalog_add_orchestrator=_mcp_catalog_add_orchestrator,
                        install_orchestrator=deps.get_mcp_install_orchestrator_singleton(),
                        activation_service=deps.get_mcp_activation_service_singleton(),
                        local_creation_orchestrator=_mcp_local_creation_orchestrator,
                        local_creation_executor=_mcp_local_creation_executor,
                        auto_approve_engine=deps.get_mcp_auto_approve_engine_singleton(),
                        runtime_watcher=deps.get_mcp_runtime_watcher_singleton(),
                        policy_resolver=None,
                        policy_attributor=None,
                        discovery_reports_dir=(
                            _discovery_reports_dir
                            if _discovery_reports_dir.exists()
                            else None
                        ),
                    ),
                    audit_log_path=DATA_DIR / "mcp_react_integration" / "audit.jsonl",
                )
                print("[MCP] ReAct integration initialisee (Phase 26)")
            else:
                if deps.lumena is not None:
                    deps.lumena.mcp_react_integration = None
        except Exception as _mcp_react_err:
            if deps.lumena is not None:
                deps.lumena.mcp_react_integration = None
            logger.debug(
                "[MCP] ReAct integration non initialisee: {}",
                _mcp_react_err,
            )

        # ── Phase I-1 fix : attach immédiat de l'intégration MCP ────────────
        # Le Phase I-1 force lumena._tool_registry au boot pour résoudre le
        # bug timing ActivationService. MAIS agent_service.py n'appelle
        # attach_to_tool_registry QUE quand il crée un nouveau registry —
        # donc avec un registry pré-créé au boot, l'attach n'a jamais lieu.
        # Conséquence : add_mcp/request_mcp_capability/... invisibles au LLM.
        # On force l'attach ici, idempotent (attach_to_tool_registry retourne
        # already_attached si déjà fait).
        try:
            if (deps.lumena is not None
                    and getattr(deps.lumena, "_tool_registry", None) is not None
                    and getattr(deps.lumena, "mcp_react_integration", None) is not None):
                _attach_result = deps.lumena.mcp_react_integration.attach_to_tool_registry(
                    deps.lumena._tool_registry
                )
                if isinstance(_attach_result, tuple) and len(_attach_result) >= 2:
                    _ok, _reason = _attach_result[0], _attach_result[1]
                    _tools_dict = getattr(deps.lumena._tool_registry, "tools", {}) or {}
                    _phase_f = sum(
                        1 for n in ("add_mcp", "disable_mcp", "remove_mcp",
                                    "set_mcp_preference", "set_mcp_category")
                        if n in _tools_dict
                    )
                    _phase_26 = sum(
                        1 for n in ("request_mcp_capability", "request_mcp_ticket",
                                    "run_mcp_autonomy", "resume_mcp_task")
                        if n in _tools_dict
                    )
                    print(
                        f"[MCP] ReAct attach au boot: {_reason} — "
                        f"Phase 26: {_phase_26}/4, Phase F: {_phase_f}/5"
                    )
        except Exception as _mcp_attach_err:
            logger.debug(
                "[MCP] attach au boot a échoué: {}", _mcp_attach_err,
            )

        await _start_autonomy_daemon_if_enabled()
        autonomy_meta = _get_autonomy_meta()
        if autonomy_meta.get("autonomy_running"):
            print("[AUTONOMY] Daemon intégré actif")
        elif autonomy_meta.get("autonomy_enabled_on_web"):
            print(f"[AUTONOMY] Non actif ({autonomy_meta.get('autonomy_last_error') or 'startup skipped'})")

        # ── Registre multi-instance ───────────────────────────────────────
        try:
            from src.runtime.peer_network_autonomy import start_peer_network_autonomy
            _peer_network_task = start_peer_network_autonomy()
            if _peer_network_task:
                print("[PEERS] Autonomie reseau active")
        except Exception as _peer_auto_err:
            logger.warning("[PEERS] Autonomie reseau non demarree: {}", _peer_auto_err)

        # ── A1.5 — Annonce mDNS automatique (être découvrable sans config) ──
        # Diagnostic explicite : on dit POURQUOI si l'annonce ne démarre pas.
        # NB: l'API zeroconf synchrone lève EventLoopBlocked si appelée DANS la
        # boucle asyncio → on l'exécute dans un thread (executor).
        try:
            from src.runtime.mdns_discovery import (
                start_mdns_advertise_from_env, is_mdns_enabled, is_mdns_available,
            )
            _loop = asyncio.get_event_loop()
            _mdns_ok = await _loop.run_in_executor(None, start_mdns_advertise_from_env)
            if _mdns_ok:
                print("[PEERS] Annonce mDNS active (_lumena._tcp.local)")
            elif not is_mdns_enabled():
                print("[PEERS] mDNS desactive (LUMENA_MDNS_DISCOVERY != 1)")
            elif not is_mdns_available():
                print("[PEERS] mDNS active mais zeroconf ABSENT du venv (pip install zeroconf)")
            else:
                print("[PEERS] mDNS: annonce non demarree (erreur reseau/interface)")
        except Exception as _mdns_err:
            print(f"[PEERS] mDNS erreur au demarrage: {_mdns_err}")

        _instance_registry = None
        if MULTI_INSTANCE_ENABLED:
            try:
                from src.runtime.instance_registry import InstanceRecord, get_registry
                from src.utils.paths import INSTANCE_ID, INSTANCE_NAME, WORKSPACE_DIR as _WS_DIR
                _instance_registry = get_registry()
                _instance_registry.cleanup_stale()
                _port_env = int(os.getenv("LUMENA_PORT", os.getenv("PORT", "8080")))
                _record = InstanceRecord(
                    instance_id=INSTANCE_ID,
                    instance_name=INSTANCE_NAME,
                    pid=os.getpid(),
                    port=_port_env,
                    role=_INSTANCE_ROLE,
                    data_dir=str(DATA_DIR),
                    workspace_dir=str(_WS_DIR),
                    started_at=datetime.now(timezone.utc).isoformat(),
                    last_seen=datetime.now(timezone.utc).isoformat(),
                    host=os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", ""),
                )
                _instance_registry.register(_record)
                print(f"[REGISTRY] Instance enregistrée — role={_INSTANCE_ROLE} port={_port_env}")

                # Heartbeat toutes les 30 s
                async def _heartbeat_loop():
                    while True:
                        try:
                            _instance_registry.update_heartbeat(INSTANCE_ID)
                        except Exception:
                            pass
                        await asyncio.sleep(30)

                _heartbeat_task = asyncio.create_task(_heartbeat_loop())
            except Exception as _reg_err:
                logger.warning("[REGISTRY] Enregistrement instance échoué: {}", _reg_err)

        def _register_runtime_channel(channel: Any) -> None:
            try:
                from src.channels.manager import get_channel_manager

                get_channel_manager().register_channel(channel)
            except Exception as register_err:
                logger.debug("[BOOT] channel manager register skip: {}", register_err)

        # Demarrer Telegram en arriere-plan
        try:
            if deps.setup_only_mode:
                raise ImportError("setup_only")
            if _IS_WORKER:
                print("[TELEGRAM] Skipped — instance role=worker (intégrations externes désactivées)")
                raise ImportError("worker_role")
            from src.channels.telegram_channel import TelegramChannel

            if _env_flag("LUMENA_WEB_ONLY", False):
                print("[TELEGRAM] Disabled by env LUMENA_WEB_ONLY=1 (web uniquement)")
            elif _env_flag("LUMENA_DISABLE_TELEGRAM", False):
                print("[TELEGRAM] Disabled by env LUMENA_DISABLE_TELEGRAM=1")
            else:
                deps.telegram_channel = TelegramChannel()
                _register_runtime_channel(deps.telegram_channel)
                if deps.telegram_channel.is_available:
                    # Keywords qui declenchent le mode Agent
                    AGENT_KEYWORDS = [
                        # Actions systeme
                        "ouvre", "ferme", "lance", "demarre", "démarre", "arrete", "arrête", "stop",
                        "kill", "execute", "exécute", "run", "start",
                        # Fichiers
                        "fichier", "dossier", "cree", "crée", "supprime", "copie", "deplace", "déplace",
                        "ecris", "écris", "modifie", "sauvegarde", "enregistre", "lis", "affiche",
                        # Recherche
                        "recherche", "cherche", "trouve", "google", "web", "fouille",
                        # Memoire (avec et sans accents)
                        "memorise", "mémorise", "souviens", "rappelle", "retiens", "apprends",
                        "memoire", "mémoire", "memoires", "mémoires", "journal",
                        # Code
                        "code", "script", "programme", "compile", "workspace",
                        # Systeme
                        "spotify", "discord", "chrome", "navigateur", "application",
                        # Planification
                        "réfléchis", "planifie", "génère",
                        # Commandes explicites
                        "!agent", "/agent", "mode agent"
                    ]

                    def needs_agent_mode(text: str) -> bool:
                        """Detecte si le message necessite le mode Agent."""
                        # Skip agent mode for photo messages (already processed by Vision)
                        if text.startswith("[📷") or "📸 Description de l'image" in text:
                            return False
                        text_lower = text.lower()
                        return any(kw in text_lower for kw in AGENT_KEYWORDS)

                    # Storage for background agent tasks to prevent garbage collection
                    _agent_tasks: set = set()

                    def _build_telegram_runtime_context(chat_id: str, text: str, msg: Any = None) -> Optional[Any]:
                        if not (deps.RUNTIME_AVAILABLE and deps.RuntimeContext is not None):
                            return None
                        try:
                            metadata = getattr(msg, "metadata", {}) or {}
                            return deps.RuntimeContext.build(
                                channel="telegram",
                                client="telegram_bot",
                                request_id=f"tg_req_{uuid.uuid4().hex}",
                                conversation_id=f"tg_chat_{chat_id}",
                                message_id=f"tg_msg_{metadata.get('message_id') or uuid.uuid4().hex}",
                                workspace_policy="default",
                                task_id=None,
                                client_caps={
                                    "telegram_chat_id": str(chat_id),
                                    "chat_id": str(chat_id),
                                    "telegram_message_id": str(metadata.get("message_id") or ""),
                                    "telegram_has_document": bool(metadata.get("has_document", False)),
                                    "telegram_document_path": str(metadata.get("document_path") or ""),
                                    "telegram_text_preview": str(text or "")[:240],
                                },
                                workspace_path=None,
                                active_file_path=None,
                                open_files=[],
                                resolved_workspace=None,
                                resolved_date=None,
                                resolution_reason="telegram_runtime_context",
                            )
                        except Exception:
                            return None

                    async def _run_agent_in_background(
                        text: str,
                        chat_id: str,
                        sender: Optional[Dict[str, Any]] = None,
                    ):
                        """Execute Agent mode in background, send response when done."""
                        import threading
                        from loguru import logger as log

                        log.info(f"[AGENT-BG] Starting background agent task for chat_id={chat_id}")
                        collected_thoughts = []
                        thought_lock = threading.Lock()

                        def realtime_sink(message):
                            msg_text = message.record["message"]
                            with thought_lock:
                                if "Thought:" in msg_text:
                                    content = msg_text.split("Thought:", 1)[1].strip()[:150]
                                    collected_thoughts.append(("thought", content))
                                elif " Outil" in msg_text or "Parsed tool call:" in msg_text:
                                    if ":" in msg_text:
                                        tool_part = msg_text.split(":", 1)[1].strip()[:50]
                                        collected_thoughts.append(("tool", tool_part))
                                elif "Observation:" in msg_text:
                                    obs = msg_text.split("Observation:", 1)[1].strip()[:100]
                                    collected_thoughts.append(("observation", obs))

                        handler_id = log.add(realtime_sink, format="{message}", level="DEBUG")
                        runtime_token = None

                        try:
                            runtime_context = _build_telegram_runtime_context(chat_id, text)
                            if runtime_context is not None and callable(deps.push_runtime_context):
                                try:
                                    runtime_token = deps.push_runtime_context(runtime_context)
                                except Exception:
                                    runtime_token = None

                            log.info(f"[AGENT-BG] Calling think_and_act...")
                            response = await deps.lumena.think_and_act(
                                text,
                                source_channel="telegram",
                                sender=sender,
                            )
                            log.info(f"[AGENT-BG] think_and_act returned: {type(response)} - {repr(response)[:200] if response else 'None/Empty'}")

                            # Send thinking summary - COPY data outside lock, then send
                            thinking_msg = None
                            with thought_lock:
                                if collected_thoughts:
                                    msg_parts = []
                                    for t_type, content in collected_thoughts[:5]:
                                        if t_type == "thought":
                                            msg_parts.append(f"💭 _{content[:80]}_")
                                        elif t_type == "tool":
                                            msg_parts.append(f"🔧 `{content}`")
                                        elif t_type == "observation":
                                            msg_parts.append(f"👁 {content[:60]}...")

                                    if msg_parts:
                                        thinking_msg = "🧠 *Processus de reflexion:*\n" + "\n".join(msg_parts)
                            
                            # Send thinking summary OUTSIDE the lock
                            if thinking_msg:
                                try:
                                    log.info(f"[AGENT-BG] Sending thinking summary...")
                                    await deps.telegram_channel.send_message(
                                        thinking_msg,
                                        chat_id,
                                        parse_mode="Markdown"
                                    )
                                except Exception as e:
                                    log.warning(f"[AGENT-BG] Failed to send thinking summary: {e}")

                            # Send final response
                            if response:
                                log.info(f"[AGENT-BG] Sending final response ({len(response)} chars) to chat_id={chat_id}")
                                _record_channel_session_message(
                                    channel="telegram",
                                    client="telegram_bot",
                                    conversation_id=f"tg_chat_{chat_id}",
                                    role="assistant",
                                    content=response,
                                    user_id=f"telegram:{chat_id}",
                                    status="done",
                                    metadata={"mode": "agent", "background": True},
                                )
                                try:
                                    if len(response) > 4096:
                                        chunks = [response[i:i + 4000] for i in range(0, len(response), 4000)]
                                        for i, chunk in enumerate(chunks):
                                            log.info(f"[AGENT-BG] Sending chunk {i+1}/{len(chunks)}")
                                            await deps.telegram_channel.send_message(chunk, chat_id)
                                    else:
                                        await deps.telegram_channel.send_message(response, chat_id)
                                    log.info(f"[AGENT-BG] Response sent successfully")
                                except Exception as send_err:
                                    log.error(f"[AGENT-BG] Failed to send response: {send_err}")
                                    # Retry with plain text (no markdown)
                                    try:
                                        plain_response = response.replace("*", "").replace("_", "").replace("`", "")
                                        await deps.telegram_channel.send_message(f"[Réponse Agent]\n{plain_response}", chat_id)
                                        log.info(f"[AGENT-BG] Response sent as plain text (fallback)")
                                    except Exception as retry_err:
                                        log.error(f"[AGENT-BG] Fallback send also failed: {retry_err}")
                            else:
                                log.warning(f"[AGENT-BG] No response to send (response was None/empty)")
                                await deps.telegram_channel.send_message("⚠️ Mode Agent terminé mais aucune réponse générée.", chat_id)

                        except Exception as e:
                            _record_channel_session_error(
                                channel="telegram",
                                client="telegram_bot",
                                conversation_id=f"tg_chat_{chat_id}",
                                user_id=f"telegram:{chat_id}",
                                error=e,
                                metadata={"mode": "agent", "background": True},
                            )
                            log.error(f"[AGENT-BG] Agent background task error: {e}")
                            import traceback
                            log.error(f"[AGENT-BG] Traceback: {traceback.format_exc()}")
                            try:
                                await deps.telegram_channel.send_message(
                                    f"❌ Erreur mode Agent: {e}",
                                    chat_id
                                )
                            except Exception as send_err:
                                log.error(f"[AGENT-BG] Failed to send error message: {send_err}")
                        finally:
                            if runtime_token is not None and callable(deps.pop_runtime_context):
                                try:
                                    deps.pop_runtime_context(runtime_token)
                                except Exception:
                                    pass
                            log.remove(handler_id)
                            log.info(f"[AGENT-BG] Background task completed for chat_id={chat_id}")

                    # Callback pour messages Telegram avec detection Agent et pensees EN TEMPS REEL
                    async def telegram_callback(msg):
                        from loguru import logger as log
                        text = msg.content
                        chat_id = msg.chat_id
                        runtime_token = None
                        _notify_autonomy_user_interaction(text)

                        runtime_context = _build_telegram_runtime_context(chat_id, text, msg)
                        if runtime_context is not None and callable(deps.push_runtime_context):
                            try:
                                runtime_token = deps.push_runtime_context(runtime_context)
                            except Exception:
                                runtime_token = None

                        try:
                            stripped = (text or "").strip()
                            lower = stripped.lower()
                            if lower.startswith("/senddoc ") or lower.startswith("/senddocument ") or lower.startswith("/sendfile "):
                                try:
                                    _, raw_path = stripped.split(" ", 1)
                                    requested = raw_path.strip().strip('"').strip("'")
                                    if not requested:
                                        await deps.telegram_channel.send_message(
                                            "❌ Usage: /senddoc <chemin_local_du_fichier>",
                                            chat_id,
                                        )
                                        return None

                                    candidate = Path(requested).expanduser()
                                    if not candidate.is_absolute():
                                        candidate = (_PROJECT_ROOT / candidate).resolve()
                                    else:
                                        candidate = candidate.resolve()

                                    if not candidate.exists() or not candidate.is_file():
                                        await deps.telegram_channel.send_message(
                                            f"❌ Fichier introuvable: {candidate}",
                                            chat_id,
                                        )
                                        return None

                                    sent_ok = await deps.telegram_channel.send_document(
                                        str(candidate),
                                        chat_id,
                                        caption=f"📤 Document demandé: {candidate.name}",
                                    )
                                    if sent_ok:
                                        await deps.telegram_channel.send_message(
                                            f"✅ Document envoyé: {candidate.name}",
                                            chat_id,
                                        )
                                    else:
                                        await deps.telegram_channel.send_message(
                                            f"❌ Échec envoi document: {candidate.name}",
                                            chat_id,
                                        )
                                    return None
                                except Exception as doc_send_err:
                                    await deps.telegram_channel.send_message(
                                        f"❌ Erreur senddoc: {doc_send_err}",
                                        chat_id,
                                    )
                                    return None

                            mode_control = _parse_telegram_mode_control(stripped)
                            forced_mode: Optional[str] = None
                            sticky_mode = _get_tg_mode(chat_id)

                            if mode_control.get("status_only"):
                                await deps.telegram_channel.send_message(
                                    f"Mode courant pour ce chat: {sticky_mode.upper()}",
                                    chat_id,
                                )
                                return None

                            requested_mode = mode_control.get("mode")
                            if requested_mode:
                                remainder = str(mode_control.get("remainder") or "").strip()
                                one_shot = bool(mode_control.get("one_shot"))

                                if one_shot:
                                    forced_mode = _normalize_tg_mode(requested_mode)
                                    if not remainder:
                                        await deps.telegram_channel.send_message(
                                            _format_tg_mode_ack(forced_mode, one_shot=True),
                                            chat_id,
                                        )
                                        return None
                                    await deps.telegram_channel.send_message(
                                        _format_tg_mode_ack(forced_mode, one_shot=True) + " J'applique aussi ta demande.",
                                        chat_id,
                                    )
                                else:
                                    sticky_mode = _set_tg_mode(chat_id, requested_mode)
                                    if not remainder:
                                        await deps.telegram_channel.send_message(
                                            _format_tg_mode_ack(sticky_mode),
                                            chat_id,
                                        )
                                        return None
                                    await deps.telegram_channel.send_message(
                                        _format_tg_mode_ack(sticky_mode) + " J'applique aussi ta demande.",
                                        chat_id,
                                    )

                                text = remainder
                                stripped = text.strip()
                                lower = stripped.lower()

                            is_photo = text.startswith("[📷") or "📸 Description de l'image" in text
                            auto_agent = needs_agent_mode(text)
                            if forced_mode in {"agent", "chat"}:
                                route_mode = forced_mode
                            elif sticky_mode in {"agent", "chat"}:
                                route_mode = sticky_mode
                            else:
                                route_mode = "agent" if auto_agent else "chat"
                            is_agent = route_mode == "agent"
                            log.info(
                                f"[TELEGRAM-CB] Message received: is_photo={is_photo}, "
                                f"is_agent={is_agent}, mode={route_mode}, sticky={sticky_mode}, "
                                f"chat_id={chat_id}, text_preview={text[:100]}..."
                            )
                            _tg_metadata = _channel_msg_metadata(msg)
                            _tg_message_id = str(_tg_metadata.get("message_id") or "")
                            _tg_conv_id = _channel_msg_conversation_id("telegram", msg, chat_id)
                            _tg_user_id = _channel_msg_user_id("telegram", msg, getattr(msg, "user_id", None) or chat_id)
                            _record_channel_session_message(
                                channel="telegram",
                                client="telegram_bot",
                                conversation_id=_tg_conv_id,
                                role="user",
                                content=text,
                                user_id=_tg_user_id,
                                message_id=_tg_message_id,
                                status="running",
                                metadata={"mode": route_mode, **_tg_metadata},
                            )

                            if is_agent:
                                # Mode Agent - EXECUTE EN ARRIERE-PLAN pour ne pas bloquer Telegram
                                log.info(f"[TELEGRAM-CB] Launching Agent mode in background")
                                await deps.telegram_channel.send_message(
                                    "🤖 *Mode Agent active*\n_Je reflechis... (vous pouvez continuer a m'envoyer des messages)_",
                                    chat_id,
                                    parse_mode="Markdown"
                                )

                                # Lancer en arriere-plan avec create_task
                                _tg_sender = {
                                    "id": msg.user_id,
                                    "name": msg.username,
                                    "username": (msg.metadata or {}).get("username", ""),
                                }
                                task = asyncio.create_task(_run_agent_in_background(text, chat_id, _tg_sender))
                                _agent_tasks.add(task)
                                task.add_done_callback(_agent_tasks.discard)

                                # Retourner None immediatement pour liberer le handler Telegram
                                return None

                            else:
                                # Mode Chat simple - execution synchrone (rapide)
                                log.info(f"[TELEGRAM-CB] Using Chat mode (synchronous)")
                                import queue
                                tool_queue = queue.Queue()

                                def capture_tools(message):
                                    msg_text = message.record["message"]
                                    if "Parsed tool call:" in msg_text or "Execution outil:" in msg_text:
                                        tool_name = msg_text.split(":", 1)[1].strip()[:60] if ":" in msg_text else msg_text[:60]
                                        tool_queue.put(tool_name)

                                handler_id = log.add(capture_tools, format="{message}", level="DEBUG")

                                try:
                                    # Construire le sender pour que Lumena identifie
                                    # qui parle (propriétaire vs ami Telegram)
                                    _tg_sender = {
                                        "id": msg.user_id,
                                        "name": msg.username,
                                        "username": (msg.metadata or {}).get("username", ""),
                                    }
                                    response = await deps.lumena.chat(text, source_channel="telegram", sender=_tg_sender)
                                    _record_channel_session_message(
                                        channel="telegram",
                                        client="telegram_bot",
                                        conversation_id=_tg_conv_id,
                                        role="assistant",
                                        content=response or "",
                                        user_id=_tg_user_id,
                                        message_id=_tg_message_id,
                                        status="done",
                                        metadata={"mode": "chat", **_tg_metadata},
                                    )

                                    # Envoyer un resume des outils utilises (si plusieurs)
                                    tools_used = []
                                    while not tool_queue.empty():
                                        try:
                                            tools_used.append(tool_queue.get_nowait())
                                        except Exception:
                                            break

                                    if len(tools_used) >= 1:
                                        tools_msg = "🔧 *Outil utilise:*\n" + "\n".join([f"▸ `{t[:50]}`" for t in tools_used[:5]])
                                        await deps.telegram_channel.send_message(tools_msg, chat_id, parse_mode="Markdown")
                                finally:
                                    log.remove(handler_id)

                            return response
                        finally:
                            if runtime_token is not None and callable(deps.pop_runtime_context):
                                try:
                                    deps.pop_runtime_context(runtime_token)
                                except Exception:
                                    pass

                    deps.telegram_channel.set_message_callback(telegram_callback)

                    if deps.lumena and getattr(deps.lumena, "tool_system", None):
                        bind_sender = getattr(deps.lumena.tool_system, "bind_telegram_document_sender", None)
                        if callable(bind_sender):
                            bind_sender(deps.telegram_channel.send_document)

                    # Injecter les callbacks scheduler Telegram (envoi de messages)
                    try:
                        from src.tools.task_scheduler import bind_scheduler_callbacks
                        bind_scheduler_callbacks(
                            telegram_send=lambda cid, txt: deps.telegram_channel.send_message(txt, cid),
                        )
                    except Exception as _sch_tg_err:
                        print(f"[SCHEDULER] Callback Telegram non injecté: {_sch_tg_err}")

                    success = await deps.telegram_channel.start()
                    if success:
                        async def keep_telegram_alive():
                            while deps.telegram_channel and deps.telegram_channel.is_running:
                                await asyncio.sleep(1)

                        deps.telegram_task = asyncio.create_task(keep_telegram_alive())
                        print("[TELEGRAM] Bot demarre en arriere-plan")
                        print("   -> Mode Agent auto-detecte pour les actions complexes")
                        print("   -> Pensees envoyees en temps reel sur Telegram")
                    else:
                        reason = getattr(deps.telegram_channel, "last_error", None) or "startup blocked"
                        print(f"[TELEGRAM] Disabled ({reason})")
                else:
                    token_present = bool((os.getenv("TELEGRAM_TOKEN") or "").strip())
                    if not token_present:
                        print("[TELEGRAM] Non configuré (TELEGRAM_TOKEN manquant)")
                    else:
                        print("[TELEGRAM] Indisponible (python-telegram-bot absent ou canal désactivé)")
        except Exception as e:
            print(f" Telegram non demarre: {e}")

        # ── Scheduler callbacks + restore (inconditionnel, web ou Telegram) ──
        try:
            from src.tools.task_scheduler import bind_scheduler_callbacks, restore_conv_tasks

            async def _scheduler_lumena_think(prompt: str, chat_id: str) -> str:
                return await deps.lumena.think_and_act(prompt, source_channel="web") or ""

            bind_scheduler_callbacks(lumena_think=_scheduler_lumena_think)
            n = restore_conv_tasks()
            if n:
                print(f"[SCHEDULER] {n} tâche(s) conversationnelle(s) restaurée(s)")
        except Exception as _sch_err:
            print(f"[SCHEDULER] Callbacks non injectés: {_sch_err}")

        # Demarrer Discord en arriere-plan
        try:
            if deps.setup_only_mode:
                raise ImportError("setup_only")
            if _IS_WORKER:
                print("[DISCORD] Skipped — instance role=worker")
                raise ImportError("worker_role")
            from src.channels.discord_channel import DiscordChannel as _DiscordChan

            if _env_flag("LUMENA_DISABLE_DISCORD", False):
                print("[DISCORD] Disabled by env LUMENA_DISABLE_DISCORD=1")
            else:
                deps.discord_channel_bot = _DiscordChan()
                _register_runtime_channel(deps.discord_channel_bot)
                if deps.discord_channel_bot.is_available:

                    async def discord_stream_callback(msg):
                        _dc_metadata = _channel_msg_metadata(msg)
                        _dc_conv_id = _channel_msg_conversation_id("discord", msg, getattr(msg, "channel_id", None))
                        _dc_user_id = _channel_msg_user_id("discord", msg, getattr(msg, "user_id", None))
                        _dc_message_id = str(_dc_metadata.get("message_id") or "")
                        _record_channel_session_message(
                            channel="discord",
                            client="discord_bot",
                            conversation_id=_dc_conv_id,
                            role="user",
                            content=msg.content,
                            user_id=_dc_user_id,
                            message_id=_dc_message_id,
                            status="running",
                            metadata=_dc_metadata,
                        )
                        collected_response = []
                        try:
                            async for token in deps.lumena.chat_stream(
                                msg.content,
                                source_channel="discord",
                                channel_id=msg.channel_id,
                                user_id=msg.user_id,
                                username=msg.username,
                                active_users=msg.metadata.get("active_users_in_channel"),
                                image_paths=msg.metadata.get("discord_image_paths"),
                                is_admin=msg.metadata.get("is_discord_admin", False),
                                channel_name=msg.metadata.get("channel_name"),
                                channel_topic=msg.metadata.get("channel_topic"),
                                available_channels=msg.metadata.get("available_channels"),
                            ):
                                collected_response.append(token)
                                yield token
                            _record_channel_session_message(
                                channel="discord",
                                client="discord_bot",
                                conversation_id=_dc_conv_id,
                                role="assistant",
                                content="".join(collected_response),
                                user_id=_dc_user_id,
                                message_id=_dc_message_id,
                                status="done",
                                metadata=_dc_metadata,
                            )
                        except Exception as exc:
                            _record_channel_session_error(
                                channel="discord",
                                client="discord_bot",
                                conversation_id=_dc_conv_id,
                                user_id=_dc_user_id,
                                error=exc,
                                message_id=_dc_message_id,
                                metadata=_dc_metadata,
                            )
                            raise

                    deps.discord_channel_bot.set_stream_callback(discord_stream_callback)

                    success_dc = await deps.discord_channel_bot.start()
                    if success_dc:
                        async def keep_discord_alive():
                            while deps.discord_channel_bot and deps.discord_channel_bot.is_running:
                                await asyncio.sleep(1)

                        deps.discord_task = asyncio.create_task(keep_discord_alive())
                        print("[DISCORD] Bot demarre en arriere-plan (ping + streaming + contexte par salon)")
                    else:
                        print("[DISCORD] Echec du demarrage")
                else:
                    print("[DISCORD] Non configure (DISCORD_TOKEN manquant dans .env)")
        except Exception as e:
            print(f" Discord non demarre: {e}")

        # Démarrer Twitter/X en arrière-plan
        try:
            if deps.setup_only_mode:
                raise ImportError("setup_only")
            if _IS_WORKER:
                print("[TWITTER] Skipped — instance role=worker")
                raise ImportError("worker_role")
            from src.channels.twitter_channel import TwitterChannel
            import src.channels.twitter_channel as _tw_mod
            _tw_disabled = _env_flag("LUMENA_DISABLE_TWITTER", False)
            _tw_bearer = os.getenv("TWITTER_BEARER_TOKEN")
            _tw_key = os.getenv("TWITTER_API_KEY")
            if _tw_disabled:
                print("[TWITTER] Désactivé via LUMENA_DISABLE_TWITTER=1")
            elif not _tw_bearer and not _tw_key:
                print("[TWITTER] Non configuré (TWITTER_BEARER_TOKEN / TWITTER_API_KEY manquants)")
            else:
                deps.twitter_channel = TwitterChannel()
                _register_runtime_channel(deps.twitter_channel)
                _tw_mod._instance = deps.twitter_channel  # partage le singleton avec les handlers ReAct
                if deps.twitter_channel.is_available:

                    async def twitter_message_callback(msg):
                        if deps.lumena:
                            reply = await deps.lumena.chat(
                                msg.content,
                                source_channel="twitter",
                                user_id=msg.user_id,
                                username=msg.username,
                            )
                            if reply and hasattr(deps.twitter_channel, "reply_to_tweet"):
                                tweet_id = msg.metadata.get("tweet_id")
                                if tweet_id:
                                    await deps.twitter_channel.reply_to_tweet(tweet_id, reply)

                    deps.twitter_channel.set_message_callback(twitter_message_callback)
                    success_tw = await deps.twitter_channel.start()
                    if success_tw:
                        async def keep_twitter_alive():
                            while deps.twitter_channel and deps.twitter_channel.is_running:
                                await asyncio.sleep(5)

                        deps.twitter_task = asyncio.create_task(keep_twitter_alive())
                        _handle = getattr(getattr(deps.twitter_channel, "_me", None), "username", "?")
                        print(f"[TWITTER] Démarré — @{_handle} (mentions polling actif)")
                    else:
                        print(f"[TWITTER] Échec du démarrage: {deps.twitter_channel.last_error}")
                else:
                    print(f"[TWITTER] Non disponible: {deps.twitter_channel.last_error}")
        except Exception as e:
            print(f"[TWITTER] Erreur démarrage: {e}")

        # ─── WhatsApp ──────────────────────────────────────────
        try:
            if deps.setup_only_mode:
                raise ImportError("setup_only")
            if _IS_WORKER:
                print("[WHATSAPP] Skipped — instance role=worker")
                raise ImportError("worker_role")
            from src.channels.whatsapp_channel import WhatsAppChannel

            if _env_flag("LUMENA_WEB_ONLY", False):
                print("[WHATSAPP] Disabled by env LUMENA_WEB_ONLY=1")
            elif _env_flag("LUMENA_DISABLE_WHATSAPP", False):
                print("[WHATSAPP] Disabled by env LUMENA_DISABLE_WHATSAPP=1")
            else:
                deps.whatsapp_channel = WhatsAppChannel()
                _register_runtime_channel(deps.whatsapp_channel)
                if deps.whatsapp_channel.is_available:
                    async def whatsapp_callback(msg):
                        sender = msg.chat_id or msg.user_id
                        text = msg.content
                        _wa_metadata = _channel_msg_metadata(msg)
                        _wa_conv_id = _channel_msg_conversation_id("whatsapp", msg, sender)
                        _wa_user_id = _channel_msg_user_id("whatsapp", msg, sender)
                        _wa_message_id = str(_wa_metadata.get("message_id") or "")
                        _record_channel_session_message(
                            channel="whatsapp",
                            client="whatsapp_cloud",
                            conversation_id=_wa_conv_id,
                            role="user",
                            content=text,
                            user_id=_wa_user_id,
                            message_id=_wa_message_id,
                            status="running",
                            metadata=_wa_metadata,
                        )
                        try:
                            response = await deps.lumena.chat(
                                text,
                                source_channel="whatsapp",
                                sender=sender,
                            )
                            _record_channel_session_message(
                                channel="whatsapp",
                                client="whatsapp_cloud",
                                conversation_id=_wa_conv_id,
                                role="assistant",
                                content=response or "",
                                user_id=_wa_user_id,
                                message_id=_wa_message_id,
                                status="done",
                                metadata=_wa_metadata,
                            )
                            return response
                        except Exception as e:
                            _record_channel_session_error(
                                channel="whatsapp",
                                client="whatsapp_cloud",
                                conversation_id=_wa_conv_id,
                                user_id=_wa_user_id,
                                error=e,
                                message_id=_wa_message_id,
                                metadata=_wa_metadata,
                            )
                            logger.error(f"WhatsApp chat error: {e}")
                            return f"❌ Erreur: {e}"

                    deps.whatsapp_channel.set_message_callback(whatsapp_callback)
                    success_wa = await deps.whatsapp_channel.start()
                    if success_wa:
                        print("[WHATSAPP] [ OK ] Connected")
                        # Bind WhatsApp send to scheduler
                        try:
                            from src.tools.task_scheduler import bind_scheduler_callbacks
                            bind_scheduler_callbacks(
                                whatsapp_send=lambda phone, txt: deps.whatsapp_channel.send_message(txt, phone),
                            )
                        except Exception as _sch_wa_err:
                            print(f"[SCHEDULER] Callback WhatsApp non injecté: {_sch_wa_err}")
                        # Bind document sender to tool system
                        bind_wa_doc = getattr(deps.lumena.tool_system, "bind_whatsapp_document_sender", None)
                        if callable(bind_wa_doc):
                            bind_wa_doc(deps.whatsapp_channel.send_document)
                    else:
                        status = deps.whatsapp_channel.get_runtime_status()
                        print(f"[WHATSAPP] [ SKIP ] {status.get('last_error', 'not available')}")
                else:
                    print("[WHATSAPP] [ SKIP ] Not configured (missing WHATSAPP_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID)")
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"WhatsApp init error: {e}")
            print(f"[WHATSAPP] [ ERROR ] {e}")

        # Démarrer le serveur WebSocket IDE sur port 8245
        try:
            if deps.setup_only_mode:
                raise ImportError("setup_only")
            if _IS_WORKER:
                print("[IDE-Bridge] Skipped — instance role=worker")
                raise ImportError("worker_role")
            from src.tools.ide_bridge import get_ide_bridge
            ide_bridge = get_ide_bridge()
            asyncio.create_task(ide_bridge.start_server())
            print(f"[IDE-Bridge] WebSocket IDE demarre sur ws://127.0.0.1:8245")
        except Exception as e:
            print(f"[IDE-Bridge] Non demarre: {e}")

        # Démarrer l'assistant vocal si LUMENA_VOICE_AUTO=1
        try:
            if _IS_WORKER:
                print("[VOICE] Skipped — instance role=worker")
            elif _env_flag("LUMENA_VOICE_AUTO", False) and deps.VoiceManager:
                _vm = deps.VoiceManager.get_instance()
                _voice_ok = await _vm.start(deps.lumena)
                if _voice_ok:
                    print("[VOICE] Assistant vocal démarré — en écoute continue (wake word: 'Lumena')")
                else:
                    print("[VOICE] Impossible de démarrer l'assistant vocal (micro indisponible ?)")
            else:
                print("[VOICE] Désactivé (LUMENA_VOICE_AUTO=0 ou VoiceManager absent)")
        except Exception as e:
            print(f"[VOICE] Erreur démarrage: {e}")

        # ── Stripe CLI auto-start (non-bloquant) ─────────────────────────

        async def _start_stripe_cli_bg():
            if _IS_WORKER:
                print("[STRIPE] Skipped — instance role=worker")
                return
            try:
                stripe_key = os.getenv("STRIPE_API_KEY", "").strip()
                stripe_cli_auto = _env_flag("STRIPE_CLI_AUTO", default=True)
                if stripe_key and stripe_cli_auto:
                    from src.services.stripe_cli import get_stripe_cli_service
                    _svc = get_stripe_cli_service()
                    if not _svc.is_installed():
                        # Auto-install Stripe CLI si absente
                        import sys, shutil, subprocess as _sp
                        print("[STRIPE] CLI non trouvee — tentative d'installation automatique...")
                        if sys.platform == "win32":
                            # Essayer winget d'abord (Windows 10/11)
                            _winget_ok = _sp.run(["winget", "install", "--id", "Stripe.StripeCLI", "--accept-source-agreements", "--accept-package-agreements", "--silent"], capture_output=True, timeout=120).returncode == 0 if shutil.which("winget") else False
                            if not _winget_ok:
                                # Fallback: télécharger le zip depuis GitHub (Windows Server)
                                import zipfile, tempfile
                                _stripe_dir = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "Stripe"
                                _stripe_dir.mkdir(parents=True, exist_ok=True)
                                _stripe_zip = Path(tempfile.gettempdir()) / "stripe_cli.zip"
                                print("[STRIPE] Telechargement depuis GitHub...")
                                try:
                                    import httpx
                                    # Résoudre la dernière version via la redirection GitHub
                                    _resp = httpx.head("https://github.com/stripe/stripe-cli/releases/latest", follow_redirects=True, timeout=30)
                                    _ver = str(_resp.url).rstrip("/").split("/")[-1]  # ex: v1.40.6
                                    _zip_url = f"https://github.com/stripe/stripe-cli/releases/download/{_ver}/stripe_{_ver.lstrip('v')}_windows_x86_64.zip"
                                    print(f"[STRIPE] Version: {_ver} -> {_zip_url}")
                                    _dl = httpx.get(_zip_url, follow_redirects=True, timeout=120)
                                    if _dl.status_code != 200:
                                        raise RuntimeError(f"HTTP {_dl.status_code}")
                                    _stripe_zip.write_bytes(_dl.content)
                                    with zipfile.ZipFile(_stripe_zip) as zf:
                                        zf.extractall(_stripe_dir)
                                    _stripe_zip.unlink(missing_ok=True)
                                    # Ajouter au PATH de cette session
                                    os.environ["PATH"] = str(_stripe_dir) + os.pathsep + os.environ.get("PATH", "")
                                    print(f"[STRIPE] CLI extraite dans {_stripe_dir}")
                                except Exception as _e:
                                    print(f"[STRIPE] Echec telechargement: {_e}")
                        else:
                            _sp.run(["bash", "-c", "curl -s https://packages.stripe.dev/api/security/keypair/stripe-cli-gpg/public | gpg --dearmor | sudo tee /usr/share/keyrings/stripe.gpg >/dev/null && echo 'deb [signed-by=/usr/share/keyrings/stripe.gpg] https://packages.stripe.dev/stripe-cli-debian-local stable main' | sudo tee /etc/apt/sources.list.d/stripe.list && sudo apt-get update -qq && sudo apt-get install -y stripe"], capture_output=True, timeout=120)
                    if _svc.is_installed() or _svc.find_cli():
                        _started = await _svc.start()
                        if _started:
                            print(f"[STRIPE] CLI demarree — webhooks -> {_svc.forward_url}")
                        else:
                            print("[STRIPE] CLI installee mais demarrage echoue (stripe login requis ?)")
                    else:
                        print("[STRIPE] CLI non trouvee apres tentative d'install — webhooks locaux desactives")
                elif stripe_key and not stripe_cli_auto:
                    print("[STRIPE] CLI auto-start desactive (STRIPE_CLI_AUTO=0)")
            except Exception as e:
                print(f"[STRIPE] Erreur demarrage CLI: {e}")

        _stripe_bg_task = asyncio.create_task(_start_stripe_cli_bg())

        # ── n8n auto-start (Docker, non-bloquant) ───────────────────────

        async def _start_n8n_bg():
            try:
                if _IS_WORKER:
                    print("[N8N] Skipped — instance role=worker")
                    return
                n8n_auto = _env_flag("N8N_AUTO_START", default=True)
                if not n8n_auto:
                    print("[N8N] Auto-start desactive (N8N_AUTO_START=0)")
                    return
                from src.services.n8n_bridge import ensure_n8n_running
                msg = await ensure_n8n_running()
                print(f"[N8N] {msg}")
            except Exception as e:
                print(f"[N8N] Erreur demarrage: {e}")

        _n8n_bg_task = asyncio.create_task(_start_n8n_bg())

        # ── Emotion WebSocket push callback ──────────────────────────────

        if deps.lumena and getattr(deps.lumena, "emotion_manager", None):
            from web.routes.emotion import broadcast_mood_change
            _emgr = deps.lumena.emotion_manager

            def _emotion_ws_cb(mood: str, pad: tuple):
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.ensure_future(broadcast_mood_change(mood, pad))
                except Exception:
                    pass

            if _emotion_ws_cb not in _emgr._mood_change_callbacks:
                _emgr._mood_change_callbacks.append(_emotion_ws_cb)

        yield  # L'application tourne ici

    finally:
        # === SHUTDOWN ===
        print(" Arret de Lumena...")
        if deps.telegram_task:
            deps.telegram_task.cancel()
            try:
                await deps.telegram_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            deps.telegram_task = None

        if deps.telegram_channel:
            try:
                await deps.telegram_channel.stop()
            except Exception:
                pass
            deps.telegram_channel = None

        if deps.discord_task:
            deps.discord_task.cancel()
            try:
                await deps.discord_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        if deps.discord_channel_bot:
            try:
                await deps.discord_channel_bot.stop()
            except Exception:
                pass

        if deps.twitter_task:
            deps.twitter_task.cancel()
            try:
                await deps.twitter_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        if deps.twitter_channel:
            try:
                await deps.twitter_channel.stop()
            except Exception:
                pass

        if deps.whatsapp_task:
            deps.whatsapp_task.cancel()
            try:
                await deps.whatsapp_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        if deps.whatsapp_channel:
            try:
                await deps.whatsapp_channel.stop()
                print("[WHATSAPP] Stopped")
            except Exception:
                pass

        await _stop_autonomy_daemon_if_started()

        # Arreter Stripe CLI si active
        if _stripe_bg_task and not _stripe_bg_task.done():
            _stripe_bg_task.cancel()
        try:
            from src.services.stripe_cli import get_stripe_cli_service
            _svc = get_stripe_cli_service()
            if _svc.is_running:
                await _svc.stop()
                print("[STRIPE] CLI arretee")
        except Exception:
            pass

        # Arreter n8n si demarré par Lumena
        if _n8n_bg_task and not _n8n_bg_task.done():
            _n8n_bg_task.cancel()
        try:
            from src.services.n8n_bridge import stop_n8n
            _n8n_msg = await stop_n8n()
            if "arrêté" in _n8n_msg.lower() or "arret" in _n8n_msg.lower():
                print(f"[N8N] {_n8n_msg}")
        except Exception:
            pass

        # Arrêter l'assistant vocal si actif
        try:
            if deps.VoiceManager:
                _vm = deps.VoiceManager.get_instance()
                if _vm.running:
                    await _vm.stop()
        except Exception:
            pass

        # Arrêter les serveurs HTTP workspace live
        try:
            from web.routes.advanced import _SERVING_WORKSPACES
            for _slug, _info in list(_SERVING_WORKSPACES.items()):
                try:
                    _info["process"].terminate()
                except Exception:
                    pass
            _SERVING_WORKSPACES.clear()
        except Exception:
            pass

        # A1.5 — Arrêt de l'annonce mDNS (executor : API zeroconf sync)
        try:
            from src.runtime.mdns_discovery import stop_mdns_advertise
            await asyncio.get_event_loop().run_in_executor(None, stop_mdns_advertise)
        except Exception:
            pass

        # Arrêt du heartbeat multi-instance + dé-enregistrement
        if _peer_network_task and not _peer_network_task.done():
            try:
                from src.runtime.peer_network_autonomy import stop_peer_network_autonomy
                await stop_peer_network_autonomy()
            except Exception:
                _peer_network_task.cancel()
                try:
                    await _peer_network_task
                except asyncio.CancelledError:
                    pass

        if _heartbeat_task and not _heartbeat_task.done():
            _heartbeat_task.cancel()
            try:
                await _heartbeat_task
            except asyncio.CancelledError:
                pass
        if MULTI_INSTANCE_ENABLED:
            try:
                from src.runtime.instance_registry import get_registry as _get_reg
                from src.utils.paths import INSTANCE_ID as _IID
                _get_reg().unregister(_IID)
                logger.debug("[REGISTRY] Instance {} dé-enregistrée", _IID)
            except Exception:
                pass

        # Phase 20B-1 : libération du singleton ApprovalQueue (best-effort).
        try:
            deps._MCP_APPROVAL_QUEUE_SINGLETON = None
        except Exception:
            pass
        # Phase I-8 (Fix AW) : arrêt de TOUS les serveurs MCP actifs AVANT
        # de libérer les singletons. Sans ça, les subprocess (npm/python)
        # devenaient orphelins à la fermeture de Lumena sur Windows et le
        # catalogue gardait des statuts ACTIVE fantômes.
        try:
            _act_shutdown = getattr(
                deps, "_MCP_ACTIVATION_SERVICE_SINGLETON", None,
            )
            if _act_shutdown is not None and callable(
                getattr(_act_shutdown, "shutdown_all", None)
            ):
                _mcp_stopped = _act_shutdown.shutdown_all()
                _ok_n = sum(1 for ok in _mcp_stopped.values() if ok)
                print(
                    f"[MCP] Shutdown: {_ok_n}/{len(_mcp_stopped)} "
                    "serveur(s) MCP arrêté(s) proprement"
                )
        except Exception as _mcp_shutdown_err:
            logger.debug(
                "[MCP] Shutdown des serveurs MCP en échec: {}",
                _mcp_shutdown_err,
            )
        # Phase 20B-2 : libération singletons Catalog + InstallOrchestrator.
        try:
            deps._MCP_INSTALL_ORCHESTRATOR_SINGLETON = None
            deps._MCP_SERVER_CATALOG_SINGLETON = None
        except Exception:
            pass
        # Phase 20B-3 : libération singletons RuntimeWatcher + ActivationService.
        try:
            deps._MCP_ACTIVATION_SERVICE_SINGLETON = None
            deps._MCP_RUNTIME_WATCHER_SINGLETON = None
        except Exception:
            pass
        # Phase 20B-5 : libération singleton AutoApproveEngine.
        try:
            deps._MCP_AUTO_APPROVE_ENGINE_SINGLETON = None
        except Exception:
            pass

        # Phase 26 : release integration ReAct MCP.
        try:
            if deps.lumena is not None:
                deps.lumena.mcp_react_integration = None
        except Exception:
            pass

        if deps.instance_lock:
            deps.instance_lock.release()
            deps.instance_lock = None
            print("[LOCK] Instance lock released")

        # Fermer IDE WebSocket bridge
        try:
            from src.tools.ide_bridge import get_ide_bridge
            _ide = get_ide_bridge()
            await _ide.stop_server()
        except Exception:
            pass

        print(" Lumena arretee proprement")
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
