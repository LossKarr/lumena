"""Thread-safe Voice V2 telemetry and immediate audio control."""
from __future__ import annotations

from threading import Lock
from typing import Any, Callable, Dict, Optional
import inspect
import time


_DICTATION_LEASE_S = 90.0


class VoiceTelemetryRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._status: Dict[str, Any] = {}
        self._stop_audio: Optional[Callable[[], Any]] = None
        self._test_voice: Optional[Callable[[], Any]] = None
        self._transcribe: Optional[Callable[[Any], Any]] = None
        self._transcribe_detailed: Optional[Callable[[Any], Any]] = None
        self._transcriber_owner: Any = None
        self._dictation_until = 0.0

    def update(self, **values: Any) -> None:
        safe = {k: v for k, v in values.items() if not callable(v)}
        with self._lock:
            self._status.update(safe)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def register_stop_audio(self, callback: Optional[Callable[[], Any]]) -> None:
        with self._lock:
            self._stop_audio = callback

    def register_test_voice(self, callback: Optional[Callable[[], Any]]) -> None:
        with self._lock:
            self._test_voice = callback

    def register_transcribe(self, callback: Optional[Callable[[Any], Any]]) -> None:
        """Compatibilité : un callback simple non nul remplace toute paire périmée."""
        with self._lock:
            self._transcribe = callback
            if callback is not None:
                self._transcribe_detailed = None
                self._transcriber_owner = None

    def register_transcribe_detailed(
        self, callback: Optional[Callable[[Any], Any]]
    ) -> None:
        """Compatibilité : un callback détaillé non nul remplace toute paire périmée."""
        with self._lock:
            self._transcribe_detailed = callback
            if callback is not None:
                self._transcribe = None
                self._transcriber_owner = None

    def register_transcribers(
        self,
        simple: Optional[Callable[[Any], Any]],
        detailed: Optional[Callable[[Any], Any]],
        *,
        owner: Any,
    ) -> None:
        """Publie atomiquement la paire STT appartenant à un runtime Voice V2."""
        with self._lock:
            self._transcribe = simple
            self._transcribe_detailed = detailed
            self._transcriber_owner = owner

    def clear_transcribers(self, *, owner: Any) -> bool:
        """Retire la paire uniquement si le runtime appelant en est toujours propriétaire."""
        with self._lock:
            if self._transcriber_owner is not owner:
                return False
            self._transcribe = None
            self._transcribe_detailed = None
            self._transcriber_owner = None
            return True

    def set_dictation_active(self, active: bool, *, lease_s: Optional[float] = None) -> None:
        with self._lock:
            lease = _DICTATION_LEASE_S if lease_s is None else max(
                5.0, min(600.0, float(lease_s))
            )
            self._dictation_until = time.monotonic() + lease if active else 0.0
            self._status["dictation_active"] = bool(active)

    def is_dictation_active(self) -> bool:
        with self._lock:
            active = self._dictation_until > time.monotonic()
            if not active:
                self._status["dictation_active"] = False
            return active

    def stop_audio(self) -> bool:
        with self._lock:
            callback = self._stop_audio
        if callback is None:
            return False
        try:
            callback()
            return True
        except Exception:
            return False

    async def test_voice(self) -> bool:
        with self._lock:
            callback = self._test_voice
        if callback is None:
            return False
        try:
            result = callback()
            if inspect.isawaitable(result):
                await result
            return True
        except Exception:
            return False

    async def transcribe(self, audio: Any) -> Optional[str]:
        with self._lock:
            callback = self._transcribe
        if callback is None:
            return None
        try:
            result = callback(audio)
            if inspect.isawaitable(result):
                result = await result
            return str(result or "").strip()
        except Exception:
            return ""

    async def transcribe_detailed(self, audio: Any) -> Optional[Dict[str, Any]]:
        """Appel structuré opt-in ; les erreurs restent visibles par la route HTTP."""
        with self._lock:
            callback = self._transcribe_detailed
        if callback is None:
            return None
        result = callback(audio)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            return {
                "text": str(result or "").strip(), "segments": [],
                "status": "ok" if result else "no_speech",
            }
        return dict(result)


_REGISTRY = VoiceTelemetryRegistry()


def get_voice_telemetry() -> VoiceTelemetryRegistry:
    return _REGISTRY
