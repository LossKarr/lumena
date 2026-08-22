"""Supervision du pipeline Voice V2.

Ce module reste hardware-last : aucun provider audio n'est importé tant que
``start()`` n'est pas appelé. Le manager ne remplace pas le legacy ; le choix du
backend appartient à ``src.voice.lifecycle``.
"""
from __future__ import annotations

import asyncio
import os
import threading
from typing import Any, Awaitable, Callable, Dict, Optional

from loguru import logger


VoiceRunner = Callable[..., Awaitable[None]]


def normalize_voice_mode(value: Optional[str]) -> str:
    """Retourne un mode produit officiel. ``direct`` n'est jamais accepté."""
    mode = (value or "chat").strip().lower()
    return mode if mode in {"chat", "agent"} else "chat"


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        return max(minimum, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


async def _default_runner(core: Any, **kwargs: Any) -> None:
    # Import paresseux obligatoire : Voice OFF ne charge aucun provider audio.
    from .live import run_voice_v2_live  # noqa: PLC0415

    await run_voice_v2_live(core, **kwargs)


class VoiceV2Manager:
    """Démarre, supervise et arrête Voice V2 sans bloquer le serveur."""

    _instance: Optional["VoiceV2Manager"] = None
    _lock = threading.Lock()

    def __init__(
        self,
        runner: Optional[VoiceRunner] = None,
        *,
        max_restarts: Optional[int] = None,
        restart_backoff_s: Optional[float] = None,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._runner = runner or _default_runner
        self._sleep = sleep_fn
        self.max_restarts = (
            _env_int("LUMENA_VOICE_V2_MAX_RESTARTS", 2)
            if max_restarts is None else max(0, int(max_restarts))
        )
        self.restart_backoff_s = (
            _env_float("LUMENA_VOICE_V2_RESTART_BACKOFF_S", 2.0)
            if restart_backoff_s is None else max(0.0, float(restart_backoff_s))
        )
        self.task: Optional[asyncio.Task] = None
        self.running = False
        self.state = "stopped"
        self.mode = "chat"
        self.restarts = 0
        self.last_error: Optional[str] = None
        self._stop_requested = False

    @classmethod
    def get_instance(cls) -> "VoiceV2Manager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    async def start(self, core: Any, *, mode: Optional[str] = None) -> bool:
        if self.task is not None and not self.task.done():
            return True
        self.mode = normalize_voice_mode(mode or os.getenv("LUMENA_VOICE_V2_MODE", "chat"))
        self.restarts = 0
        self.last_error = None
        self._stop_requested = False
        self.running = True
        self.state = "starting"
        self.task = asyncio.create_task(self._supervise(core), name="lumena-voice-v2")
        await asyncio.sleep(0)
        return self.state != "error"

    async def _supervise(self, core: Any) -> None:
        try:
            while not self._stop_requested:
                try:
                    self.state = "running"
                    await self._runner(
                        core,
                        disable_tools=False,
                        llm_mode="agent" if self.mode == "agent" else "core_chat",
                    )
                    if self._stop_requested:
                        break
                    raise RuntimeError("le pipeline Voice V2 s'est arrêté sans demande")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self.last_error = str(exc)
                    logger.warning("Voice V2 pipeline error: {}", exc)
                    if self.restarts >= self.max_restarts:
                        self.state = "error"
                        return
                    self.restarts += 1
                    self.state = "restarting"
                    delay = self.restart_backoff_s * (2 ** (self.restarts - 1))
                    await self._sleep(delay)
        except asyncio.CancelledError:
            pass
        finally:
            self.running = False
            if self._stop_requested or self.state != "error":
                self.state = "stopped"

    async def stop(self) -> None:
        self._stop_requested = True
        task = self.task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.task = None
        self.running = False
        self.state = "stopped"

    def get_status(self) -> Dict[str, Any]:
        status = {}
        try:
            from .observability import get_voice_telemetry
            status.update(get_voice_telemetry().snapshot())
        except Exception:
            pass
        # Lifecycle fields are authoritative and cannot be overwritten by a
        # stale runtime snapshot from a previous Voice V2 session.
        status.update({
            "running": self.running,
            "backend": "v2",
            "state": self.state,
            "mode": self.mode,
            "restarts": self.restarts,
            "last_error": self.last_error,
            "wake_word": "Lumena",
        })
        return status
