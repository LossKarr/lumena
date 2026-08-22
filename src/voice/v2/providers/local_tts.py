"""LocalTTSAdapter — adapte le `LumenaTTS` EXISTANT derrière le contrat TTSProvider.

IMPORTANT (V2 §1, étapes) :
- import PARESSEUX : ce module ne charge la stack audio (`src.voice.tts`) qu'au
  PREMIER usage réel (`is_available`/`synthesize`) — l'importer reste léger ;
- ne remplace PAS `assistant_loop` ; c'est seulement le pont vers le moteur local ;
- le vrai branchement audio est une étape ULTÉRIEURE et contrôlée. À ce stade,
  on teste l'adaptateur avec un `tts` injecté (fake/mock), jamais le moteur réel.
"""
from __future__ import annotations

import os
import re
import inspect
from typing import Any, AsyncIterator, List, Optional

from .base import TTSProvider, AudioResult, TTSAudioChunk, CancelToken


def _cloud_allowed() -> bool:
    """V2 local-first : le cloud (Edge-TTS) n'est autorisé que si explicitement activé."""
    return os.getenv("LUMENA_VOICE_CLOUD_ALLOWED", "0").strip() == "1"


def _segments(text: str) -> List[str]:
    """Découpe en phrases pour le streaming (ponctuation . ! ? … ; saut de ligne)."""
    parts = re.split(r"(?<=[.!?…])\s+|\n+", (text or "").strip())
    # Piper peut produire un WAV invalide sur des segments sans contenu vocal réel
    # ("...", emoji seuls, ponctuation markdown). On ne garde que les segments
    # contenant au moins une lettre ou un chiffre.
    return [p.strip() for p in parts if p and p.strip() and any(ch.isalnum() for ch in p)]


class LocalTTSAdapter(TTSProvider):
    name = "local_lumena"
    locality = "local"
    supports_streaming = True          # synthèse par phrase (chunking V2)
    supports_voice_clone = True        # XTTS référence (lumena_voice.wav)

    def __init__(self, tts: Any = None):
        # `tts` injectable (LumenaTTS réel OU fake en test). None => résolution paresseuse.
        self._tts = tts

    def _get_tts(self) -> Any:
        if self._tts is None:
            # LAZY : charge la stack audio uniquement ici (jamais à l'import du module).
            from src.voice.tts import get_tts  # noqa: PLC0415 — import paresseux volontaire
            self._tts = get_tts()
        return self._tts

    def is_available(self) -> bool:
        try:
            return self._get_tts() is not None
        except Exception:
            return False

    async def _synthesize_for_profile(self, tts: Any, text: str, voice: Any, *, local_only: bool):
        kwargs = {"local_only": local_only}
        try:
            parameters = inspect.signature(tts._synthesize).parameters
            if "allow_xtts" in parameters:
                kwargs["allow_xtts"] = bool(
                    voice is not None
                    and getattr(voice, "reference_consent_confirmed", False)
                )
            if "piper_model" in parameters and voice is not None:
                kwargs["piper_model"] = getattr(
                    getattr(voice, "local", None), "piper_model", None
                )
        except (TypeError, ValueError):
            pass
        return await tts._synthesize(text, **kwargs)

    async def synthesize(self, text: str, voice: Any, cancel: Optional[CancelToken] = None) -> AudioResult:
        if cancel and cancel.cancelled:
            return AudioResult(ok=False, text=text)
        if not any(ch.isalnum() for ch in (text or "")):
            return AudioResult(ok=False, text=text)
        try:
            tts = self._get_tts()
        except Exception:
            return AudioResult(ok=False, text=text, audio_path=None,
                               chunk_count=0, audio_format="", duration_ms=0)
        # SYNTHÈSE SEULE : `_synthesize` produit le fichier SANS jouer (V2 possède le playback).
        # local-first : interdit Edge-TTS (cloud) tant que LUMENA_VOICE_CLOUD_ALLOWED != 1.
        path = await self._synthesize_for_profile(
            tts, text, voice, local_only=not _cloud_allowed()
        )
        provider = getattr(tts, "_last_provider", "") or ""
        return AudioResult(
            ok=path is not None,
            text=text,
            audio_path=str(path) if path else None,
            provider=provider,
            degraded=(provider == "pyttsx3"),   # fallback robotique -> statut dégradé
        )

    async def stream(self, text: str, voice: Any,
                     cancel: Optional[CancelToken] = None) -> AsyncIterator[TTSAudioChunk]:
        """Synthèse PAR PHRASE (chunking) : un TTSAudioChunk par segment, SANS jouer.

        Permet le pipeline (jouer le segment N pendant qu'on synthétise N+1) et la
        troncature fine. local-first : Edge interdit si cloud non autorisé.
        """
        try:
            tts = self._get_tts()
        except Exception:
            return
        local_only = not _cloud_allowed()
        for i, seg in enumerate(_segments(text)):
            if cancel is not None and getattr(cancel, "cancelled", False):
                return
            path = await self._synthesize_for_profile(tts, seg, voice, local_only=local_only)
            provider = getattr(tts, "_last_provider", "") or ""
            if path is None:
                continue  # segment non synthétisable -> on saute (best-effort)
            yield TTSAudioChunk(
                sequence=i, text=seg, audio_path=str(path),
                provider=provider, degraded=(provider == "pyttsx3"),
            )

    async def prewarm(self, text: str = "Bonjour.") -> dict:
        """Initialise le moteur TTS via une mini-synthèse (SANS playback).

        Utilise `_synthesize` (produit le fichier, ne joue rien) pour charger Piper/XTTS
        avant le 1er tour. Renvoie {ok, latency_ms, provider, degraded}. local-first :
        Edge interdit si cloud non autorisé. Toute erreur → ok=False (non bloquant)."""
        import time  # noqa: PLC0415
        t0 = time.perf_counter()
        try:
            tts = self._get_tts()
        except Exception as e:
            return {"component": "tts", "ok": False, "latency_ms": 0,
                    "provider": "", "degraded": False, "detail": str(e)}
        try:
            path = await self._synthesize_for_profile(
                tts, text, None, local_only=not _cloud_allowed()
            )
        except Exception as e:
            return {"component": "tts", "ok": False,
                    "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "provider": "", "degraded": False, "detail": str(e)}
        provider = getattr(tts, "_last_provider", "") or ""
        return {"component": "tts", "ok": path is not None,
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "provider": provider, "degraded": (provider == "pyttsx3")}

    async def stop(self) -> None:
        """Arrêt de lecture (passe par l'API existante)."""
        try:
            tts = self._get_tts()
        except Exception:
            return
        stop_speaking = getattr(tts, "stop_speaking", None)
        if callable(stop_speaking):
            stop_speaking()
