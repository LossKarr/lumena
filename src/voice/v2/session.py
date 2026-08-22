"""Session officielle du canal voix : identité, mode et RuntimeContext par tour."""
from __future__ import annotations

import json
import inspect
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from src.runtime.channel_envelope import ChannelEnvelope
from src.runtime.context import RuntimeContext, pop_runtime_context, push_runtime_context
from src.utils.paths import DATA_DIR, INSTANCE_ID
from src.utils.persistence import atomic_write_text


_VALID_MODES = {"chat", "agent"}
_VALID_TRUSTED_ROLES = {"owner", "admin", "user"}


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", (text or "").strip().lower())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def parse_mode_switch(text: str) -> Optional[str]:
    """Détecte uniquement une commande explicite, jamais une mention ordinaire."""
    value = re.sub(r"\s+", " ", _plain(text)).strip(" .!?;,:")
    agent_patterns = (
        r"^(?:lumena[, ]+)?passe en mode agent$",
        r"^(?:lumena[, ]+)?active le mode agent$",
        r"^(?:lumena[, ]+)?reste en mode agent$",
    )
    chat_patterns = (
        r"^(?:lumena[, ]+)?passe en mode chat$",
        r"^(?:lumena[, ]+)?repasse en mode chat$",
        r"^(?:lumena[, ]+)?active le mode chat$",
        r"^(?:lumena[, ]+)?mode normal$",
    )
    if any(re.fullmatch(pattern, value) for pattern in agent_patterns):
        return "agent"
    if any(re.fullmatch(pattern, value) for pattern in chat_patterns):
        return "chat"
    return None


@dataclass(frozen=True)
class VoiceSessionIdentity:
    user_id: str
    owner_user_id: str
    user_role: str
    profile_id: Optional[str]
    trusted: bool

    @classmethod
    def from_env(cls) -> "VoiceSessionIdentity":
        trusted = _env_flag("LUMENA_VOICE_SESSION_TRUSTED", False)
        requested_role = os.getenv("LUMENA_VOICE_SESSION_ROLE", "guest").strip().lower()
        role = requested_role if trusted and requested_role in _VALID_TRUSTED_ROLES else "guest"
        default_user = "local:owner" if role == "owner" else "voice:guest"
        return cls(
            user_id=os.getenv("LUMENA_VOICE_SESSION_USER_ID", default_user).strip() or default_user,
            owner_user_id=os.getenv("LUMENA_OWNER_USER_ID", "local:owner").strip() or "local:owner",
            user_role=role,
            profile_id=os.getenv("LUMENA_VOICE_PROFILE_ID", "").strip() or None,
            trusted=trusted,
        )


class VoiceSessionRouter:
    """Route Chat/Agent vers le Core officiel sous un contexte vocal explicite."""

    def __init__(
        self,
        core: Any,
        *,
        mode: str = "chat",
        conversation_id: Optional[str] = None,
        identity: Optional[VoiceSessionIdentity] = None,
        state_path: Optional[Path] = None,
    ) -> None:
        self.core = core
        self.identity = identity or VoiceSessionIdentity.from_env()
        self.conversation_id = (
            conversation_id
            or os.getenv("LUMENA_VOICE_CONVERSATION_ID", "").strip()
            or f"voice:{INSTANCE_ID}:primary"
        )
        self.state_path = state_path
        self.mode = self._load_mode(mode)

    @classmethod
    def for_product(cls, core: Any, *, mode: str = "chat") -> "VoiceSessionRouter":
        return cls(core, mode=mode, state_path=DATA_DIR / "voice" / "session.json")

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        value = (mode or "chat").strip().lower()
        return value if value in _VALID_MODES else "chat"

    def _load_mode(self, fallback: str) -> str:
        if self.state_path is None:
            return self._normalize_mode(fallback)
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            return self._normalize_mode(payload.get("mode", fallback))
        except (OSError, ValueError, TypeError, AttributeError):
            return self._normalize_mode(fallback)

    def set_mode(self, mode: str) -> str:
        self.mode = self._normalize_mode(mode)
        if self.state_path is not None:
            atomic_write_text(
                self.state_path,
                json.dumps({"mode": self.mode}, ensure_ascii=False, indent=2) + "\n",
            )
        return self.mode

    def handle_mode_command(self, text: str) -> Optional[str]:
        requested = parse_mode_switch(text)
        if requested is None:
            return None
        self.set_mode(requested)
        if requested == "agent":
            return "Mode Agent activé."
        return "Mode Chat activé."

    def build_envelope(self) -> ChannelEnvelope:
        return ChannelEnvelope.from_request(
            channel="voice",
            client="voice-v2-local",
            request_id=None,
            conversation_id=self.conversation_id,
            message_id=None,
            task_id=None,
            client_caps={
                "session_id": self.conversation_id,
                "voice_session_trusted": self.identity.trusted,
            },
            mode=self.mode,
        )

    def build_runtime_context(self) -> RuntimeContext:
        envelope = self.build_envelope()
        if envelope.channel != "voice":
            raise RuntimeError("canal voice désactivé par la politique runtime")
        return RuntimeContext.build(
            channel="voice",
            client=envelope.client,
            request_id=envelope.request_id,
            conversation_id=envelope.conversation_id,
            message_id=envelope.message_id,
            workspace_policy="default",
            task_id=envelope.task_id,
            client_caps=envelope.client_caps,
            workspace_path=None,
            active_file_path=None,
            open_files=None,
            resolved_workspace=None,
            resolved_date=None,
            resolution_reason="voice_session",
            user_id=self.identity.user_id,
            owner_user_id=self.identity.owner_user_id,
            user_role=self.identity.user_role,
            profile_id=self.identity.profile_id,
            instance_id=INSTANCE_ID,
            mode=self.mode,
        )

    async def _under_context(self, call: Callable[[], Any]) -> Any:
        token = push_runtime_context(self.build_runtime_context())
        try:
            return await call()
        finally:
            pop_runtime_context(token)

    async def respond_chat(self, text: str) -> str:
        return await self._under_context(
            lambda: self.core.chat(text, source_channel="voice")
        )

    async def respond_agent(
        self,
        text: str,
        *,
        step_callback: Any = None,
        max_iterations: Optional[int] = None,
        final_ready_callback: Any = None,
        task_orchestrator: Any = None,
        task_id: Optional[str] = None,
    ) -> str:
        kwargs = {
            "source_channel": "voice",
            "step_callback": step_callback,
            "max_iterations": max_iterations,
        }
        _params = {}
        _has_kwargs = False
        try:
            _params = inspect.signature(self.core.think_and_act).parameters
            _has_kwargs = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in _params.values())
            if (
                "final_ready_callback" in _params
                or _has_kwargs
            ):
                kwargs["final_ready_callback"] = final_ready_callback
        except (TypeError, ValueError):
            pass
        if task_orchestrator is not None and ("task_orchestrator" in _params or _has_kwargs):
            kwargs["task_orchestrator"] = task_orchestrator
        if task_id and ("task_id" in _params or _has_kwargs):
            kwargs["task_id"] = task_id
        return await self._under_context(
            lambda: self.core.think_and_act(
                text,
                **kwargs,
            )
        )
