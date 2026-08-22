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
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from .base import STTProvider, STTResult


class RealSTTAdapter(STTProvider):
    name = "real_whisper"
    locality = "local"

    def __init__(self, stt: Any = None, *, language: str = "fr"):
        # `stt` injectable (LumenaSTT réel OU fake en test). None => résolution paresseuse.
        self._stt = stt
        self.language = language
        self._transcribe_lock = asyncio.Lock()

    def _get_stt(self) -> Any:
        if self._stt is None:
            from src.voice.stt import get_stt  # noqa: PLC0415 — import paresseux volontaire
            self._stt = get_stt()
        return self._stt

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
            try:
                stt = self._get_stt()
            except Exception:
                return ""
            # bytes PCM16 → mémoire ; chemin/str → fichier (réutilise la cascade existante).
            if isinstance(audio, (bytes, bytearray)):
                return await stt.transcribe_memory(bytes(audio), fast=fast)
            if isinstance(audio, (str, Path)):
                return await stt.transcribe_file(str(audio))
            return ""

    async def transcribe_detailed(
        self, audio: Any, *, language: str = "fr", strict: bool = False
    ) -> dict:
        """Résultat structuré opt-in pour la dictée du compositeur."""
        async with self._transcribe_lock:
            try:
                stt = self._get_stt()
            except Exception as exc:
                if strict:
                    raise RuntimeError(f"STT indisponible: {exc}") from exc
                return {"text": "", "segments": [], "status": "stt_unavailable"}

            if isinstance(audio, (str, Path)):
                detailed = getattr(stt, "transcribe_file_detailed", None)
                if callable(detailed):
                    return await detailed(str(audio), strict=strict)
                text = await stt.transcribe_file(str(audio))
            elif isinstance(audio, (bytes, bytearray)):
                text = await stt.transcribe_memory(bytes(audio), fast=False)
            else:
                text = ""
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
                res = loader()
                res = await res if inspect.isawaitable(res) else res
                ok = res is not False        # load_model renvoie True/False
            else:
                await self.transcribe(b"\x00\x00" * 1600)   # silence court → force le chargement
                ok = True
        except Exception as e:
            return {"component": "stt", "ok": False,
                    "latency_ms": int((time.perf_counter() - t0) * 1000), "detail": str(e)}
        return {"component": "stt", "ok": bool(ok),
                "latency_ms": int((time.perf_counter() - t0) * 1000), "detail": "loaded"}
