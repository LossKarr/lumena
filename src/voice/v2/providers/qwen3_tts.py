"""Qwen3-TTS provider backed by the external, isolated runtime."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time
from typing import Any, AsyncIterator

from src.utils.paths import DATA_DIR, ROOT_DIR

from .base import AudioResult, CancelToken, TTSAudioChunk, TTSProvider
from .local_tts import _audio_metadata, _segments
from ..qwen_runtime import cached_qwen_runtime_status, resolve_qwen_paths
from ..qwen_worker_client import QwenWorkerClient


QWEN_TARGET_CHARS = 48
QWEN_MAX_CHARS = 70
QWEN_FIRST_MAX_CHARS = 32


def qwen_segments(text: str) -> list[str]:
    """Bound file-based Qwen synthesis and release its first audio quickly."""
    return _segments(
        text,
        target_chars=QWEN_TARGET_CHARS,
        max_chars=QWEN_MAX_CHARS,
        first_max_chars=QWEN_FIRST_MAX_CHARS,
    )


class Qwen3TTSProvider(TTSProvider):
    name = "qwen3-tts"
    locality = "local"
    supports_streaming = True  # segmented prefetch; native PCM streaming is false.
    supports_voice_clone = True

    def __init__(self, worker: Any = None, *, cache_dir: Path | None = None) -> None:
        self._worker = worker
        self.cache_dir = Path(cache_dir or (DATA_DIR / "voice" / "qwen-audio"))
        self._last_error_code = ""

    def _get_worker(self) -> QwenWorkerClient:
        if self._worker is not None:
            return self._worker
        paths = resolve_qwen_paths()
        python = paths.get("python")
        model_dir = paths.get("model_dir")
        if not isinstance(python, Path) or not isinstance(model_dir, Path):
            raise RuntimeError("qwen_components_missing")
        try:
            timeout_s = float(os.getenv("LUMENA_QWEN_TTS_TIMEOUT_S", "90"))
        except ValueError:
            timeout_s = 90.0
        self._worker = QwenWorkerClient(
            python=python,
            worker_script=ROOT_DIR / "src" / "voice" / "qwen_worker_entry.py",
            model_dir=model_dir,
            sox_dir=paths.get("sox_dir") if isinstance(paths.get("sox_dir"), Path) else None,
            device=os.getenv("LUMENA_QWEN_TTS_DEVICE", "auto"),
            dtype=os.getenv("LUMENA_QWEN_TTS_DTYPE", "auto"),
            request_timeout_s=timeout_s,
        )
        return self._worker

    def is_available(self) -> bool:
        if self._worker is not None:
            return True
        try:
            return bool(cached_qwen_runtime_status().get("ready"))
        except Exception:
            return False

    @staticmethod
    def _identity(voice: Any) -> tuple[str, str]:
        local = getattr(voice, "local", None)
        reference = str(getattr(local, "qwen_reference", "") or "")
        reference_text = str(getattr(local, "qwen_reference_text", "") or "")
        if not reference or not Path(reference).is_file() or not reference_text:
            raise RuntimeError("qwen_voice_identity_missing")
        if not bool(getattr(voice, "reference_consent_confirmed", False)):
            raise RuntimeError("qwen_voice_consent_missing")
        return reference, reference_text

    async def synthesize(
        self, text: str, voice: Any, cancel: CancelToken | None = None,
    ) -> AudioResult:
        if cancel and cancel.cancelled:
            return AudioResult(ok=False, text=text, provider=self.name)
        if not text or not any(ch.isalnum() for ch in text):
            return AudioResult(ok=False, text=text, provider=self.name)
        output: Path | None = None
        try:
            reference, reference_text = self._identity(voice)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix="qwen-", suffix=".wav", dir=self.cache_dir)
            os.close(descriptor)
            output = Path(name)
            output.unlink(missing_ok=True)
            result = await self._get_worker().synthesize(
                text=text, language=str(getattr(voice, "language", "fr")),
                reference_path=reference, reference_text=reference_text,
                output_path=str(output), cancel=cancel,
            )
            path = Path(str(result.get("path", "")))
            if path != output or not path.is_file():
                raise RuntimeError("qwen_worker_output_invalid")
            audio_format, sample_rate, channels, duration_ms = _audio_metadata(path)
            if not sample_rate or not duration_ms:
                raise RuntimeError("qwen_audio_invalid")
            self._last_error_code = ""
            return AudioResult(
                ok=True, text=text, audio_path=str(path), provider=self.name,
                audio_format=audio_format, sample_rate=sample_rate,
                channels=channels, duration_ms=duration_ms,
            )
        except Exception as exc:
            if output is not None:
                output.unlink(missing_ok=True)
            self._last_error_code = str(exc)[:64]
            return AudioResult(ok=False, text=text, provider=self.name)

    async def stream(
        self, text: str, voice: Any, cancel: CancelToken | None = None,
    ) -> AsyncIterator[TTSAudioChunk]:
        for sequence, segment in enumerate(qwen_segments(text)):
            if cancel and cancel.cancelled:
                return
            result = await self.synthesize(segment, voice, cancel)
            if not result.ok:
                return
            yield TTSAudioChunk(
                sequence=sequence, text=segment, audio_path=result.audio_path,
                duration_ms=result.duration_ms, audio_format=result.audio_format,
                sample_rate=result.sample_rate, channels=result.channels,
                provider=self.name,
            )

    async def prewarm(self, text: str = "Bonjour.", voice: Any = None) -> dict[str, Any]:
        """Load the isolated model before a turn and report the real cost."""
        started = time.perf_counter()
        result = await self.synthesize(text, voice)
        latency_ms = int((time.perf_counter() - started) * 1000)
        if result.audio_path:
            Path(result.audio_path).unlink(missing_ok=True)
        return {
            "component": "tts",
            "ok": bool(result.ok),
            "provider": result.provider or self.name,
            "latency_ms": latency_ms,
            "degraded": bool(result.degraded),
            "detail": "" if result.ok else (self._last_error_code or "qwen_prewarm_failed"),
        }

    async def aclose(self) -> None:
        closer = getattr(self._worker, "aclose", None)
        if callable(closer):
            await closer()

    def runtime_status(self) -> dict[str, Any]:
        worker_status = getattr(self._worker, "status", lambda: {})()
        runtime = cached_qwen_runtime_status()
        return {
            **super().runtime_status(),
            "runtime": runtime,
            "installed": bool(runtime.get("installed")),
            "ready": bool(runtime.get("ready")),
            "qualified": bool(
                isinstance(runtime.get("performance"), dict)
                and runtime["performance"].get("qualified") is True
            ),
            "tts_isolated": True,
            "tts_streaming_mode": "segmented_files",
            "tts_native_pcm_streaming": False,
            "tts_worker": worker_status,
            "tts_worker_alive": bool(worker_status.get("alive", False)),
            "tts_worker_restarts": int(worker_status.get("restarts", 0) or 0),
            "tts_worker_error": str(
                worker_status.get("last_error_code") or self._last_error_code or ""
            )[:64],
            "tts_locality": "local",
        }
