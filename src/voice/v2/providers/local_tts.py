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
import wave
from pathlib import Path
from typing import Any, AsyncIterator, List, Optional

from .base import TTSProvider, AudioResult, TTSAudioChunk, CancelToken
from ..voice_profile import classify_dialogue_act


def _cloud_allowed() -> bool:
    """V2 local-first : le cloud (Edge-TTS) n'est autorisé que si explicitement activé."""
    return os.getenv("LUMENA_VOICE_CLOUD_ALLOWED", "0").strip() == "1"


def _restricted_xtts_allowed() -> bool:
    """XTTS-v2 weights are CPML/non-commercial: require explicit local opt-in."""
    return os.getenv("LUMENA_XTTS_ALLOW_RESTRICTED", "0").strip() == "1"


def _segments(text: str, *, target_chars: int = 100, max_chars: int = 180) -> List[str]:
    """Build speakable chunks long enough to hide synthesis of the next chunk.

    A TTS request for every tiny sentence leaves the speaker silent while the
    worker synthesizes the following one. Keep sentence boundaries inside each
    chunk, but coalesce short neighbours within a bounded interruption unit.
    """
    parts = re.split(r"(?<=[.!?…])\s+|\n+", (text or "").strip())
    # Piper peut produire un WAV invalide sur des segments sans contenu vocal réel
    # ("...", emoji seuls, ponctuation markdown). On ne garde que les segments
    # contenant au moins une lettre ou un chiffre.
    speakable = [
        p.strip() for p in parts
        if p and p.strip() and any(ch.isalnum() for ch in p)
    ]
    chunks: List[str] = []
    pending = ""
    for part in speakable:
        combined = f"{pending} {part}".strip() if pending else part
        if pending and len(pending) >= target_chars and len(combined) > max_chars:
            chunks.append(pending)
            pending = part
        elif len(combined) <= max_chars:
            pending = combined
        else:
            if pending:
                chunks.append(pending)
            pending = part
    if pending:
        if chunks and len(pending) < target_chars:
            combined = f"{chunks[-1]} {pending}"
            if len(combined) <= max_chars:
                chunks[-1] = combined
            else:
                chunks.append(pending)
        else:
            chunks.append(pending)
    return chunks


def _audio_metadata(path: Any) -> tuple[str, int, int, int]:
    candidate = Path(path) if path else None
    if candidate is None:
        return "", 0, 0, 0
    if candidate.suffix.lower() != ".wav":
        return candidate.suffix.lower().lstrip("."), 0, 0, 0
    try:
        with wave.open(str(candidate), "rb") as stream:
            rate = int(stream.getframerate())
            channels = int(stream.getnchannels())
            frames = int(stream.getnframes())
            duration_ms = int(frames * 1000 / rate) if rate else 0
            return "pcm16", rate, channels, duration_ms
    except (OSError, EOFError, wave.Error):
        return "wav", 0, 0, 0


class LocalTTSAdapter(TTSProvider):
    name = "local_lumena"
    locality = "local"
    supports_streaming = True          # pipeline borné par phrase ; format déclaré par chunk
    supports_voice_clone = True        # XTTS référence (lumena_voice.wav)

    def __init__(self, tts: Any = None, *, isolated: Optional[bool] = None):
        # `tts` injectable (LumenaTTS réel OU fake en test). None => résolution paresseuse.
        self._tts = tts
        self._isolated = (tts is None) if isolated is None else bool(isolated)

    def _get_tts(self) -> Any:
        if self._tts is None:
            if self._isolated:
                from ..tts_worker import IsolatedTTSWorker  # noqa: PLC0415
                try:
                    timeout_s = float(os.getenv("LUMENA_TTS_WORKER_TIMEOUT_S", "90"))
                except (TypeError, ValueError):
                    timeout_s = 90.0
                self._tts = IsolatedTTSWorker(
                    timeout_s=timeout_s,
                )
            else:
                # Compatibilite explicite pour diagnostic du moteur dans le process.
                from src.voice.tts import get_tts  # noqa: PLC0415
                self._tts = get_tts()
        return self._tts

    def is_available(self) -> bool:
        try:
            return self._get_tts() is not None
        except Exception:
            return False

    async def _synthesize_for_profile(self, tts: Any, text: str, voice: Any, *, local_only: bool,
                                      cancel: Optional[CancelToken] = None):
        kwargs = {"local_only": local_only}
        try:
            parameters = inspect.signature(tts._synthesize).parameters
            if "allow_xtts" in parameters:
                kwargs["allow_xtts"] = bool(
                    voice is not None
                    and getattr(voice, "reference_consent_confirmed", False)
                    and _restricted_xtts_allowed()
                )
            if "piper_model" in parameters and voice is not None:
                local = getattr(voice, "local", None)
                resolver = getattr(local, "piper_model_for", None)
                if callable(resolver):
                    model = resolver(getattr(voice, "language", "fr"))
                else:
                    model = getattr(local, "piper_model", None)
                # Never pronounce another language with the French fallback by
                # accident. This unavailable sentinel still permits an explicit
                # multilingual XTTS or cloud provider when their policy allows it.
                kwargs["piper_model"] = model or "unsupported_language"
            if "prosody" in parameters and voice is not None:
                dialogue_act = classify_dialogue_act(text)
                kwargs["prosody"] = dict(
                    getattr(getattr(voice, "persona", None), "prosody", {}).get(
                        dialogue_act, {}
                    )
                )
            if "_cancel_token" in parameters:
                kwargs["_cancel_token"] = cancel
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
            tts, text, voice, local_only=not _cloud_allowed(), cancel=cancel
        )
        provider = getattr(tts, "_last_provider", "") or ""
        audio_format, sample_rate, channels, duration_ms = _audio_metadata(path)
        return AudioResult(
            ok=path is not None,
            text=text,
            audio_path=str(path) if path else None,
            provider=provider,
            audio_format=audio_format,
            sample_rate=sample_rate,
            channels=channels,
            duration_ms=duration_ms,
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
            path = await self._synthesize_for_profile(
                tts, seg, voice, local_only=local_only, cancel=cancel
            )
            provider = getattr(tts, "_last_provider", "") or ""
            if path is None:
                continue  # segment non synthétisable -> on saute (best-effort)
            audio_format, sample_rate, channels, duration_ms = _audio_metadata(path)
            yield TTSAudioChunk(
                sequence=i, text=seg, audio_path=str(path),
                provider=provider, degraded=(provider == "pyttsx3"),
                audio_format=audio_format, sample_rate=sample_rate,
                channels=channels, duration_ms=duration_ms,
            )

    async def prewarm(self, text: str = "Bonjour.", voice: Any = None) -> dict:
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
                tts, text, voice, local_only=not _cloud_allowed()
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

    async def aclose(self) -> None:
        try:
            tts = self._get_tts()
        except Exception:
            return
        closer = getattr(tts, "aclose", None)
        if callable(closer):
            result = closer()
            if inspect.isawaitable(result):
                await result

    def runtime_status(self) -> dict:
        tts = self._tts
        return {
            "tts_isolated": bool(self._isolated),
            "tts_worker_alive": bool(getattr(tts, "alive", False)) if tts is not None else False,
            "tts_worker_restarts": int(getattr(tts, "_restarts", 0)) if tts is not None else 0,
            "tts_worker_error": str(getattr(tts, "last_error", "") or "")[:240],
            "tts_locality": "local" if not _cloud_allowed() else "local_or_opt_in_cloud",
        }
