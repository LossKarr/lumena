"""Read-only Lumena chat routed through a connected Codex subscription.

This adapter is deliberately narrow. It borrows an App Server process that the
admin connected explicitly, preserves one Codex thread per Lumena conversation,
and never starts a process, logs in, executes Lumena tools, or falls back to a
paid API provider.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
import threading
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Sequence

from loguru import logger

from src.llm.codex_app_server import (
    CodexAppServerError,
    CodexAppServerRPCError,
    CodexAppServerSupervisor,
    CodexAppServerTimeout,
    codex_turn_execution_lock,
)
from src.llm.codex_subscription import (
    CodexSurface,
    CodexSubscriptionGateway,
    CodexSubscriptionSettings,
)
from src.utils.paths import DATA_DIR, ROOT_DIR
from src.utils.persistence import atomic_write_json, safe_read_json


THREAD_START_METHOD = "thread/start"
THREAD_RESUME_METHOD = "thread/resume"
TURN_START_METHOD = "turn/start"
TURN_INTERRUPT_METHOD = "turn/interrupt"

_SESSION_FILE = DATA_DIR / "codex" / "chat_sessions.json"
_ACTION_PREFIX_RE = re.compile(
    r"^\s*(?:s['’]il\s+te\s+pla[iî]t[, ]*)?"
    r"(?:peux[- ]tu|pourrais[- ]tu|je\s+veux\s+que\s+tu|merci\s+de\s+)?\s*"
    r"(?:cr[ée]e?r?|g[ée]n[èe]re?r?|construis|d[ée]veloppe|impl[ée]mente|"
    r"modifi(?:e|er)|[ée]dit(?:e|er)|corrig(?:e|er)|supprim(?:e|er)|effac(?:e|er)|d[ée]plac(?:e|er)|copi(?:e|er)|"
    r"[ée]cris|r[ée]dige|envoie|publie|t[ée]l[ée]charge|installe|"
    r"ouvre|ferme|lance|d[ée]marre|arr[êe]te|ex[ée]cute|"
    r"cherche|recherche|navigue|va\s+sur|clique|remplis|planifie|"
    r"m[ée]morise|retiens|fais(?:[- ]moi)?)\b",
    re.IGNORECASE,
)
_EXPLICIT_AGENT_RE = re.compile(r"(?:^|\s)(?:!agent|/agent|mode\s+agent)(?:\s|$)", re.I)
_QUESTION_PREFIX_RE = re.compile(
    r"^\s*(?:comment|pourquoi|quand|o[uù]|quel(?:le)?s?|qui|"
    r"qu['’]est[- ]ce|est[- ]ce|sais[- ]tu|peux[- ]tu\s+m['’]expliquer)\b",
    re.IGNORECASE,
)

DeltaSink = Callable[[str], Any | Awaitable[Any]]
_DELTA_SINK: ContextVar[DeltaSink | None] = ContextVar(
    "lumena_codex_chat_delta_sink", default=None
)


class CodexChatUnavailable(RuntimeError):
    """The explicit subscription chat rail cannot serve this turn."""


@dataclass(frozen=True)
class CodexChatLink:
    conversation_id: str
    thread_id: str
    model: str = ""
    cwd: str = ""
    system_hash: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class CodexChatResult:
    response: str
    thread_id: str = ""
    turn_id: str = ""
    model: str = ""
    deltas: tuple[str, ...] = ()
    action_refused: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


class CodexChatSessionRegistry:
    """Atomic conversation-to-thread links with no credential material."""

    def __init__(self, path: str | Path = _SESSION_FILE):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        payload = safe_read_json(self.path, default={"version": 1, "links": {}})
        if not isinstance(payload, dict):
            return {"version": 1, "links": {}}
        links = payload.get("links")
        if not isinstance(links, dict):
            payload["links"] = {}
        payload["version"] = 1
        return payload

    def get(self, conversation_id: str) -> CodexChatLink | None:
        key = str(conversation_id or "").strip()
        if not key:
            return None
        with self._lock:
            raw = self._read().get("links", {}).get(key)
        if not isinstance(raw, Mapping) or not str(raw.get("thread_id", "")).strip():
            return None
        return CodexChatLink(
            conversation_id=key,
            thread_id=str(raw.get("thread_id", "")).strip(),
            model=str(raw.get("model", "")).strip(),
            cwd=str(raw.get("cwd", "")).strip(),
            system_hash=str(raw.get("system_hash", "")).strip(),
            updated_at=str(raw.get("updated_at", "")).strip(),
        )

    def put(self, link: CodexChatLink) -> None:
        if not link.conversation_id.strip() or not link.thread_id.strip():
            raise ValueError("Codex chat link requires conversation and thread ids")
        with self._lock:
            payload = self._read()
            payload["links"][link.conversation_id] = asdict(link)
            atomic_write_json(self.path, payload)

    def delete(self, conversation_id: str) -> bool:
        key = str(conversation_id or "").strip()
        with self._lock:
            payload = self._read()
            existed = payload["links"].pop(key, None) is not None
            if existed:
                atomic_write_json(self.path, payload)
        return existed


def push_codex_chat_delta_sink(sink: DeltaSink) -> Token:
    return _DELTA_SINK.set(sink)


def pop_codex_chat_delta_sink(token: Token) -> None:
    _DELTA_SINK.reset(token)


async def _emit_delta(text: str) -> None:
    sink = _DELTA_SINK.get()
    if sink is None or not text:
        return
    result = sink(text)
    if inspect.isawaitable(result):
        await result


def should_route_chat_to_codex(settings: CodexSubscriptionSettings) -> bool:
    return settings.surface_requested(CodexSurface.CHAT)


def codex_chat_requires_agent(message: str) -> bool:
    """Conservatively identify requests that require Lumena's action rail."""

    text = str(message or "").strip()
    if not text:
        return False
    if _EXPLICIT_AGENT_RE.search(text):
        return True
    if _QUESTION_PREFIX_RE.match(text):
        return False
    return bool(_ACTION_PREFIX_RE.match(text))


def _id_from_result(result: Any, key: str) -> str:
    if not isinstance(result, Mapping):
        return ""
    nested = result.get(key)
    if isinstance(nested, Mapping):
        return str(nested.get("id", "") or "")
    return str(result.get(f"{key}Id", "") or "")


def _event_matches(params: Any, *, thread_id: str, turn_id: str) -> bool:
    if not isinstance(params, Mapping):
        return False
    event_thread = str(params.get("threadId", "") or "")
    event_turn = str(params.get("turnId", "") or "")
    turn = params.get("turn")
    if isinstance(turn, Mapping):
        event_turn = event_turn or str(turn.get("id", "") or "")
    return (not event_thread or event_thread == thread_id) and (
        not event_turn or event_turn == turn_id
    )


def _message_text(message: Mapping[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping):
                value = item.get("text") or item.get("content")
                if value:
                    parts.append(str(value))
        return "\n".join(parts)
    return str(content or "")


def _system_hash(messages: Sequence[Mapping[str, Any]]) -> str:
    system = "\n\n".join(
        _message_text(item) for item in messages if str(item.get("role", "")) == "system"
    )
    return hashlib.sha256(system.encode("utf-8")).hexdigest()


def build_codex_chat_prompt(
    messages: Sequence[Mapping[str, Any]],
    *,
    include_history: bool,
    include_system: bool,
) -> str:
    """Flatten Lumena's prepared context into one App Server text input."""

    systems = [
        _message_text(item).strip()
        for item in messages
        if str(item.get("role", "")) == "system" and _message_text(item).strip()
    ]
    non_system = [item for item in messages if str(item.get("role", "")) != "system"]
    selected = non_system if include_history else non_system[-1:]
    lines = [
        "Tu réponds comme Lumena dans son canal CHAT TEXTE en lecture seule.",
        "Les instructions Lumena ci-dessous définissent ton identité, ton style, sa mémoire et ses skills.",
        "N'expose aucun raisonnement interne. N'utilise aucun outil et ne prétends accomplir aucune action.",
    ]
    if include_system and systems:
        lines.extend(["", "=== CONTEXTE SYSTEME LUMENA ===", "\n\n".join(systems)])
    if selected:
        lines.extend(["", "=== CONVERSATION ==="])
        for item in selected:
            role = str(item.get("role", "user") or "user").upper()
            text = _message_text(item).strip()
            if text:
                lines.append(f"{role}: {text}")
    lines.extend(["", "Réponds maintenant uniquement avec la réponse finale de Lumena."])
    return "\n".join(lines)


def _select_model(models: Sequence[Any], configured: str) -> str:
    available = {str(item.model_id): item for item in models if item.model_id}
    if configured and configured in available:
        return configured
    default = next((item.model_id for item in models if item.is_default), "")
    return default or (next(iter(available)) if available else "")


async def _interrupt_turn(
    supervisor: CodexAppServerSupervisor, thread_id: str, turn_id: str
) -> None:
    if not thread_id or not turn_id or not supervisor.is_running:
        return
    try:
        await supervisor.request(
            TURN_INTERRUPT_METHOD,
            {"threadId": thread_id, "turnId": turn_id},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("[Chat/Codex] interruption non confirmee: {}", exc)


async def _start_or_resume_thread(
    supervisor: CodexAppServerSupervisor,
    *,
    link: CodexChatLink | None,
    cwd: Path,
    model: str,
) -> tuple[str, bool]:
    common: dict[str, Any] = {
        "cwd": str(cwd),
        "approvalPolicy": "never",
        "sandbox": "read-only",
        "serviceName": "lumena-chat",
    }
    if model:
        common["model"] = model
    if link is not None:
        try:
            resumed = await supervisor.request(
                THREAD_RESUME_METHOD,
                {"threadId": link.thread_id, **common},
                timeout=30,
            )
            thread_id = _id_from_result(resumed, "thread")
            if thread_id:
                return thread_id, True
        except (CodexAppServerRPCError, CodexAppServerError) as exc:
            logger.warning(
                "[Chat/Codex] reprise thread impossible; nouveau thread local: {}", exc
            )
    started = await supervisor.request(THREAD_START_METHOD, common, timeout=30)
    thread_id = _id_from_result(started, "thread")
    if not thread_id:
        raise CodexChatUnavailable("Codex n'a retourne aucun identifiant de conversation")
    return thread_id, False


async def run_chat_with_codex_subscription(
    messages: Sequence[Mapping[str, Any]],
    *,
    user_message: str,
    conversation_id: str,
    cwd: str | Path | None,
    settings: CodexSubscriptionSettings,
    supervisor: CodexAppServerSupervisor,
    registry: CodexChatSessionRegistry | None = None,
    timeout_s: float = 300.0,
) -> CodexChatResult:
    """Run one read-only chat turn through the selected ChatGPT account model."""

    if not should_route_chat_to_codex(settings):
        raise CodexChatUnavailable("Le chat Codex n'est pas active")
    if codex_chat_requires_agent(user_message):
        return CodexChatResult(
            response=(
                "Cette demande nécessite les outils de Lumena. Passe en mode Agent "
                "pour que je l'exécute ; le chat via abonnement Codex reste en lecture seule."
            ),
            model=settings.default_model or "auto",
            action_refused=True,
            meta={
                "provider_requested": "openai-codex",
                "provider_used": "openai-codex",
                "model_requested": settings.default_model or "auto",
                "model_used": settings.default_model or "auto",
                "fallback_used": False,
                "fallback_reason": None,
                "continuation_used": False,
                "continuation_steps": 0,
                "finish_reason": "action_requires_agent",
            },
        )
    if supervisor is None or not supervisor.is_running:
        raise CodexChatUnavailable(
            "Aucune session Codex connectée. Ouvre Configuration > Accès OpenAI "
            "et connecte ton compte ChatGPT. Aucun fallback API n'a été utilisé."
        )

    conversation_key = str(conversation_id or "").strip() or "local:default"
    workspace = Path(cwd or ROOT_DIR).resolve()
    if not workspace.is_dir():
        workspace = ROOT_DIR.resolve()
    store = registry or CodexChatSessionRegistry()

    async with codex_turn_execution_lock():
        gateway = CodexSubscriptionGateway(supervisor)
        try:
            await gateway.require_chatgpt_account()
            models = await gateway.list_models()
        except Exception as exc:
            raise CodexChatUnavailable(f"Session ChatGPT Codex inutilisable: {exc}") from exc
        if not models:
            raise CodexChatUnavailable("Le compte Codex ne retourne aucun modèle utilisable")
        model = _select_model(models, settings.default_model)
        link = store.get(conversation_key)
        system_hash = _system_hash(messages)
        thread_id, resumed = await _start_or_resume_thread(
            supervisor, link=link, cwd=workspace, model=model
        )
        if link is not None and not resumed:
            store.delete(conversation_key)
        prompt = build_codex_chat_prompt(
            messages,
            include_history=not resumed,
            include_system=(not resumed or link is None or link.system_hash != system_hash),
        )
        turn_params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}],
            "cwd": str(workspace),
            "approvalPolicy": "never",
            "sandboxPolicy": {
                "type": "readOnly",
                "networkAccess": False,
            },
        }
        if model:
            turn_params["model"] = model
        turn_result = await supervisor.request(TURN_START_METHOD, turn_params, timeout=30)
        turn_id = _id_from_result(turn_result, "turn")
        if not turn_id:
            raise CodexChatUnavailable("Codex n'a retourne aucun identifiant de tour")

        store.put(
            CodexChatLink(
                conversation_id=conversation_key,
                thread_id=thread_id,
                model=model,
                cwd=str(workspace),
                system_hash=system_hash,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
        )

        deltas: list[str] = []
        final_text = ""
        try:
            async with asyncio.timeout(timeout_s):
                while True:
                    notification = await supervisor.next_notification(timeout=30)
                    if not _event_matches(
                        notification.params, thread_id=thread_id, turn_id=turn_id
                    ):
                        continue
                    params = (
                        notification.params
                        if isinstance(notification.params, Mapping)
                        else {}
                    )
                    if notification.method == "item/agentMessage/delta":
                        delta = str(params.get("delta", "") or "")
                        if delta:
                            deltas.append(delta)
                            await _emit_delta(delta)
                    elif notification.method == "item/completed":
                        item = params.get("item")
                        if isinstance(item, Mapping) and item.get("type") == "agentMessage":
                            final_text = str(item.get("text", "") or final_text)
                    elif notification.method == "turn/completed":
                        turn = params.get("turn")
                        turn = turn if isinstance(turn, Mapping) else {}
                        status = str(turn.get("status", "") or "")
                        if status != "completed":
                            raise CodexChatUnavailable(
                                f"Le tour Codex s'est termine avec le statut {status or 'inconnu'}"
                            )
                        response = final_text.strip() or "".join(deltas).strip()
                        if not response:
                            raise CodexChatUnavailable("Codex a termine sans reponse textuelle")
                        meta = {
                            "provider_requested": "openai-codex",
                            "provider_used": "openai-codex",
                            "model_requested": settings.default_model or "auto",
                            "model_used": model or "server-default",
                            "fallback_used": False,
                            "fallback_reason": None,
                            "continuation_used": resumed,
                            "continuation_steps": 1 if resumed else 0,
                            "finish_reason": "stop",
                            "prompt_tokens": None,
                            "completion_tokens": None,
                        }
                        return CodexChatResult(
                            response=response,
                            thread_id=thread_id,
                            turn_id=turn_id,
                            model=model,
                            deltas=tuple(deltas),
                            meta=meta,
                        )
        except (asyncio.CancelledError, TimeoutError, CodexAppServerTimeout):
            await _interrupt_turn(supervisor, thread_id, turn_id)
            raise
