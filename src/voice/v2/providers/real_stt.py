"""RealSTTAdapter — adapte le `LumenaSTT` EXISTANT (faster-whisper) au contrat STTProvider.

HARDWARE-LAST : import PARESSEUX de `src.voice.stt` (faster-whisper) — importer ce
module reste léger ; le moteur n'est chargé qu'au PREMIER usage réel. Réservé au
chemin gated `LUMENA_VOICE_V2_STT=1`, hors pytest. En test, on injecte un moteur
fake (`stt=`), jamais le vrai modèle.

Transcription par énoncé : `transcribe()` route bytes→`transcribe_memory`,
chemin→`transcribe_file`. `stream()` minimal = un seul `final` (pas de partiels
streamés ici : la VAD fournit le TIMING, Whisper fournit le CONTENU une fois
l'énoncé capturé). Les partiels Whisper en flux continu = étape ultérieure.
"""
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from .base import STTProvider, STTResult


class RealSTTAdapter(STTProvider):
    name = "real_whisper"
    locality = "local"

    def __init__(self, stt: Any = None, *, language: str = "fr", timeout_s: Optional[float] = None):
        # `stt` injectable (LumenaSTT réel OU fake en test). None => résolution paresseuse.
        self._stt = stt
        self.language = language
        self._transcribe_lock = asyncio.Lock()
        raw_timeout = timeout_s if timeout_s is not None else os.getenv("LUMENA_STT_TIMEOUT_S", "45")
        try:
            self.timeout_s = max(0.05, min(300.0, float(raw_timeout)))
        except (TypeError, ValueError):
            self.timeout_s = 45.0
        self.last_latency_ms = 0
        self.last_status = "idle"

    async def _call_engine(self, method: Any, *args: Any, **kwargs: Any) -> Any:
        """Run blocking Whisper work outside the application event loop."""
        def invoke() -> Any:
            result = method(*args, **kwargs)
            if inspect.isawaitable(result):
                return asyncio.run(result)
            return result

        return await asyncio.wait_for(
            asyncio.to_thread(invoke), timeout=self.timeout_s
        )

    def _publish_status(self, *, status: str, started: float, error: str = "") -> None:
        self.last_latency_ms = int((time.perf_counter() - started) * 1000)
        self.last_status = status
        try:
            from ..observability import get_voice_telemetry  # noqa: PLC0415
            get_voice_telemetry().update(
                stt_provider=self.name,
                stt_locality=self.locality,
                stt_status=status,
                stt_latency_ms=self.last_latency_ms,
                stt_error=error[:240],
            )
        except Exception:
            pass

    def _get_stt(self) -> Any:
        if self._stt is None:
            from src.voice.stt import get_stt  # noqa: PLC0415 — import paresseux volontaire
            self._stt = get_stt()
        return self._stt

    @staticmethod
    def _language_kwargs(method: Any, language: str) -> dict:
        try:
            return {"language": language} if "language" in inspect.signature(method).parameters else {}
        except (TypeError, ValueError):
            return {}

    def is_available(self) -> bool:
        # Léger : présence de faster-whisper sans charger le modèle.
        try:
            if importlib.util.find_spec("faster_whisper") is None:
                return False
            return self._get_stt() is not None
        except Exception:
            return False

    async def transcribe(self, audio: Any, *, language: str = "fr", fast: bool = True) -> str:
        async with self._transcribe_lock:
            started = time.perf_counter()
            try:
                stt = self._get_stt()
                if isinstance(audio, (bytes, bytearray)):
                    method = stt.transcribe_memory
                    result = await self._call_engine(
                        method, bytes(audio), fast=fast,
                        **self._language_kwargs(method, language or self.language),
                    )
                elif isinstance(audio, (str, Path)):
                    method = stt.transcribe_file
                    result = await self._call_engine(
                        method, str(audio),
                        **self._language_kwargs(method, language or self.language),
                    )
                else:
                    result = ""
                self._publish_status(status="ok" if result else "no_speech", started=started)
                return str(result or "")
            except asyncio.TimeoutError:
                self._publish_status(status="timeout", started=started, error="timeout")
                return ""
            except Exception as exc:
                self._publish_status(status="error", started=started, error=str(exc))
                return ""

    async def transcribe_detailed(
        self, audio: Any, *, language: str = "fr", strict: bool = False
    ) -> dict:
        """Résultat structuré opt-in pour la dictée du compositeur."""
        async with self._transcribe_lock:
            started = time.perf_counter()
            try:
                stt = self._get_stt()
            except Exception as exc:
                if strict:
                    raise RuntimeError(f"STT indisponible: {exc}") from exc
                return {"text": "", "segments": [], "status": "stt_unavailable"}

            if isinstance(audio, (str, Path)):
                detailed = getattr(stt, "transcribe_file_detailed", None)
                if callable(detailed):
                    result = await self._call_engine(
                        detailed, str(audio), strict=strict,
                        **self._language_kwargs(detailed, language or self.language),
                    )
                    self._publish_status(status=str(result.get("status", "ok")), started=started)
                    return result
                method = stt.transcribe_file
                text = await self._call_engine(
                    method, str(audio),
                    **self._language_kwargs(method, language or self.language),
                )
            elif isinstance(audio, (bytes, bytearray)):
                detailed = getattr(stt, "transcribe_memory_detailed", None)
                if callable(detailed):
                    result = await self._call_engine(
                        detailed, bytes(audio), fast=False,
                        **self._language_kwargs(detailed, language or self.language),
                    )
                    self._publish_status(status=str(result.get("status", "ok")), started=started)
                    return result
                method = stt.transcribe_memory
                text = await self._call_engine(
                    method, bytes(audio), fast=False,
                    **self._language_kwargs(method, language or self.language),
                )
            else:
                text = ""
            self._publish_status(status="ok" if text else "no_speech", started=started)
            return {
                "text": str(text or "").strip(), "segments": [],
                "status": "ok" if text else "no_speech",
            }

    async def stream(self, audio: Any, *, language: str = "fr") -> AsyncIterator[STTResult]:
        # Minimal réel : on transcrit l'énoncé capturé et on émet UN final.
        text = await self.transcribe(audio, language=language, fast=False)
        if text:
            yield STTResult(text=text, is_final=True)

    async def prewarm(self) -> dict:
        """Précharge le modèle Whisper AVANT le 1er tour (réduit la latence perçue).

        Appelle `load_model()` si présent, sinon force le chargement via une courte
        transcription de silence. Renvoie un statut {ok, latency_ms, detail}. Ne joue
        aucun audio, ne touche pas la prod. Toute erreur → ok=False (non bloquant)."""
        t0 = time.perf_counter()
        try:
            stt = self._get_stt()
        except Exception as e:
            return {"component": "stt", "ok": False, "latency_ms": 0, "detail": f"indispo: {e}"}
        try:
            loader = getattr(stt, "load_model", None)
            if callable(loader):
                res = await self._call_engine(loader)
                ok = res is not False        # load_model renvoie True/False
            else:
                await self.transcribe(b"\x00\x00" * 1600)   # silence court → force le chargement
                ok = True
        except Exception as e:
            return {"component": "stt", "ok": False,
                    "latency_ms": int((time.perf_counter() - t0) * 1000), "detail": str(e)}
        return {"component": "stt", "ok": bool(ok),
                "latency_ms": int((time.perf_counter() - t0) * 1000), "detail": "loaded"}
