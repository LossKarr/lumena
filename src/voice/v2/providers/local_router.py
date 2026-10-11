"""Policy router between high-fidelity Qwen and the universal local fallback."""
from __future__ import annotations

import os
from typing import Any, AsyncIterator

from .base import AudioResult, CancelToken, TTSAudioChunk, TTSProvider
from .local_tts import LocalTTSAdapter, _segments
from .qwen3_tts import Qwen3TTSProvider, qwen_segments
from ..qwen_runtime import qwen_conversation_qualified


def configured_tts_engine() -> str:
    value = os.getenv("LUMENA_VOICE_TTS_ENGINE", "auto").strip().lower()
    aliases = {"qwen": "qwen3", "qwen3-tts": "qwen3", "local": "piper"}
    value = aliases.get(value, value)
    return value if value in {"auto", "qwen3", "piper"} else "auto"


class LocalTTSRouter(TTSProvider):
    name = "local_router"
    locality = "local"
    supports_streaming = True
    supports_voice_clone = True

    def __init__(
        self, *, qwen: TTSProvider | None = None, fallback: TTSProvider | None = None,
        engine: str | None = None, qwen_qualified: bool | None = None,
    ) -> None:
        self.qwen = qwen or Qwen3TTSProvider()
        self.fallback = fallback or LocalTTSAdapter()
        self.engine = engine or configured_tts_engine()
        self._qwen_qualified = qwen_qualified
        self.fallback_count = 0
        self.last_effective_provider = ""
        self.last_fallback_reason = ""

    def _identity_ready(self, voice: Any) -> bool:
        local = getattr(voice, "local", None)
        return bool(
            getattr(voice, "reference_consent_confirmed", False)
            and getattr(local, "qwen_reference", "")
            and getattr(local, "qwen_reference_text", "")
        )

    def _use_qwen(self, voice: Any) -> bool:
        # Short-lived narration must never occupy the slow, single-owner Qwen
        # worker ahead of the user's final answer. Piper speaks these transient
        # messages while Qwen remains warm and available for valuable speech.
        if str(getattr(voice, "delivery_kind", "final") or "final") in {
            "status", "milestone",
        }:
            return False
        if self.engine == "piper" or not self._identity_ready(voice):
            return False
        if not self.qwen.is_available():
            return False
        if self.engine == "qwen3":
            return True
        qualified = self._qwen_qualified
        if qualified is None:
            try:
                qualified = qwen_conversation_qualified()
            except Exception:
                qualified = False
        return bool(qualified)

    def is_available(self) -> bool:
        return bool(self.fallback.is_available() or self.qwen.is_available())

    async def synthesize(
        self, text: str, voice: Any, cancel: CancelToken | None = None,
    ) -> AudioResult:
        use_qwen = self._use_qwen(voice)
        transient_fast_path = (
            self.engine != "piper"
            and str(getattr(voice, "delivery_kind", "final") or "final")
            in {"status", "milestone"}
        )
        if use_qwen:
            result = await self.qwen.synthesize(text, voice, cancel)
            if result.ok:
                self.last_effective_provider = result.provider or self.qwen.name
                self.last_fallback_reason = ""
                return result
            if cancel and cancel.cancelled:
                return result
            self.fallback_count += 1
            self.last_fallback_reason = "qwen_synthesis_failed"
        elif transient_fast_path:
            self.last_fallback_reason = "transient_fast_path"
        result = await self.fallback.synthesize(text, voice, cancel)
        self.last_effective_provider = result.provider or self.fallback.name
        return result

    async def stream(
        self, text: str, voice: Any, cancel: CancelToken | None = None,
    ) -> AsyncIterator[TTSAudioChunk]:
        prefer_qwen = self._use_qwen(voice)
        transient_fast_path = (
            not prefer_qwen
            and self.engine != "piper"
            and str(getattr(voice, "delivery_kind", "final") or "final")
            in {"status", "milestone"}
        )
        if transient_fast_path:
            self.last_fallback_reason = "transient_fast_path"
        segments = (
            qwen_segments(text)
            if prefer_qwen
            else _segments(text, target_chars=55, max_chars=90)
        )
        for sequence, segment in enumerate(segments):
            if cancel and cancel.cancelled:
                return
            result: AudioResult | None = None
            if prefer_qwen:
                result = await self.qwen.synthesize(segment, voice, cancel)
                if not result.ok:
                    if cancel and cancel.cancelled:
                        return
                    self.fallback_count += 1
                    self.last_fallback_reason = "qwen_synthesis_failed"
                    # Keep subsequent chunks fluid after the first Qwen failure.
                    prefer_qwen = False
            if result is None or not result.ok:
                result = await self.fallback.synthesize(segment, voice, cancel)
            if not result.ok:
                continue
            self.last_effective_provider = result.provider or (
                self.qwen.name if prefer_qwen else self.fallback.name
            )
            yield TTSAudioChunk(
                sequence=sequence, text=segment, audio=result.audio,
                audio_path=result.audio_path, duration_ms=result.duration_ms,
                audio_format=result.audio_format, sample_rate=result.sample_rate,
                channels=result.channels, provider=result.provider,
                degraded=result.degraded,
            )

    async def prewarm(self, text: str = "Bonjour.", voice: Any = None) -> dict[str, Any]:
        provider = self.qwen if self._use_qwen(voice) else self.fallback
        method = getattr(provider, "prewarm", None)
        if callable(method):
            return await method(text, voice)
        result = await provider.synthesize(text, voice)
        return {
            "component": "tts", "ok": result.ok, "provider": result.provider,
            "latency_ms": 0, "degraded": result.degraded,
        }

    async def stop(self) -> None:
        for provider in (self.qwen, self.fallback):
            method = getattr(provider, "stop", None)
            if callable(method):
                await method()

    async def aclose(self) -> None:
        for provider in (self.qwen, self.fallback):
            method = getattr(provider, "aclose", None)
            if callable(method):
                await method()

    def runtime_status(self) -> dict[str, Any]:
        qwen_status = self.qwen.runtime_status()
        fallback_status = self.fallback.runtime_status()
        qwen_worker = qwen_status.get("tts_worker", {})
        fallback_worker = fallback_status.get("tts_worker", {})
        effective_worker = (
            qwen_worker
            if self.last_effective_provider == "qwen3-tts" or qwen_worker.get("alive")
            else fallback_worker
        )
        return {
            **super().runtime_status(),
            "tts_engine_configured": self.engine,
            "tts_provider_effective": self.last_effective_provider,
            "tts_fallback_count": self.fallback_count,
            "tts_fallback_reason": self.last_fallback_reason,
            "qwen": qwen_status,
            "fallback": fallback_status,
            "tts_worker": effective_worker,
            "tts_isolated": bool(
                qwen_status.get("tts_isolated") or fallback_status.get("tts_isolated")
            ),
            "edge_tts": dict(fallback_status.get("edge_tts", {}) or {}),
            # Compatibility consumed by the existing Voice diagnostic.
            "tts_worker_alive": bool(
                qwen_status.get("tts_worker_alive") or fallback_status.get("tts_worker_alive")
            ),
            "tts_worker_restarts": int(
                qwen_status.get("tts_worker_restarts", 0) or 0
            ) + int(fallback_status.get("tts_worker_restarts", 0) or 0),
            "tts_worker_error": str(
                qwen_status.get("tts_worker_error") or fallback_status.get("tts_worker_error") or ""
            )[:64],
            "tts_locality": "local",
        }


def build_local_tts_provider() -> LocalTTSRouter:
    return LocalTTSRouter()
