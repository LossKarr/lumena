"""Orchestrateur unique des backends vocaux legacy et Voice V2."""
from __future__ import annotations

import asyncio
import os
import threading
from typing import Any, Dict, Optional

from loguru import logger

from .manager import VoiceManager as LegacyVoiceManager
from .v2.supervisor import VoiceV2Manager


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name, "").strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def select_voice_backend(*, v2_enabled: bool) -> str:
    """V2 est opt-in ; sinon le backend historique reste la valeur exacte."""
    return "v2" if v2_enabled else "legacy"


class VoiceLifecycleManager:
    """API compatible avec VoiceManager, avec ownership audio exclusif."""

    _instance: Optional["VoiceLifecycleManager"] = None
    _lock = threading.Lock()

    def __init__(
        self,
        *,
        legacy: Optional[Any] = None,
        v2: Optional[Any] = None,
    ) -> None:
        self.legacy = legacy or LegacyVoiceManager.get_instance()
        self.v2 = v2 or VoiceV2Manager.get_instance()
        self.active_backend = "none"
        self._monitor_task: Optional[asyncio.Task] = None
        self._stop_requested = False
        self._fallback_used = False

    @classmethod
    def get_instance(cls) -> "VoiceLifecycleManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def running(self) -> bool:
        return bool(getattr(self.v2, "running", False) or getattr(self.legacy, "running", False))

    async def start(self, core: Any) -> bool:
        if self.running:
            return True
        self._stop_requested = False
        self._fallback_used = False
        backend = select_voice_backend(v2_enabled=_env_flag("LUMENA_VOICE_V2_AUTO", False))
        if backend == "legacy":
            self.active_backend = "legacy"
            return bool(await self.legacy.start(core))

        self.active_backend = "v2"
        ok = bool(await self.v2.start(core, mode=os.getenv("LUMENA_VOICE_V2_MODE", "chat")))
        if not ok:
            if not _env_flag("LUMENA_VOICE_V2_FALLBACK_LEGACY", True):
                return False
            self._fallback_used = True
            self.active_backend = "legacy"
            return bool(await self.legacy.start(core))
        self._monitor_task = asyncio.create_task(
            self._monitor_v2(core), name="lumena-voice-v2-monitor"
        )
        return True

    async def _monitor_v2(self, core: Any) -> None:
        task = getattr(self.v2, "task", None)
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            return
        if self._stop_requested or getattr(self.v2, "state", "") != "error":
            return
        if not _env_flag("LUMENA_VOICE_V2_FALLBACK_LEGACY", True):
            return
        logger.warning("Voice V2 exhausted restarts; switching to legacy voice backend")
        self._fallback_used = True
        self.active_backend = "legacy"
        await self.legacy.start(core)

    async def stop(self) -> None:
        self._stop_requested = True
        monitor = self._monitor_task
        if monitor is not None and not monitor.done():
            monitor.cancel()
            try:
                await monitor
            except asyncio.CancelledError:
                pass
        self._monitor_task = None
        await self.v2.stop()
        await self.legacy.stop()
        self.active_backend = "none"

    def get_status(self) -> Dict[str, Any]:
        selected = self.v2 if self.active_backend == "v2" else self.legacy
        status = dict(selected.get_status()) if hasattr(selected, "get_status") else {}
        status.update({
            "running": self.running,
            "backend": self.active_backend,
            "fallback_used": self._fallback_used,
        })
        if self.active_backend == "legacy":
            status.setdefault("state", "running" if self.running else "stopped")
            status.setdefault("mode", "legacy")
        return status
