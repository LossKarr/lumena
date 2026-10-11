"""LocalAudioPlayer — playback réel cancellable + discipline generation_id (V2.3).

Enveloppe `LumenaTTS._play_audio` (import PARESSEUX) :
- ne joue QUE les chunks dont `generation_id == courant` (le reste = dropped_stale) ;
- `stop()` interrompt et passe par `tts.stop_speaking()` ;
- alimente le `ConversationAudioLedger` (ce qui a été réellement entendu) ;
- `play_fn`/`stop_fn` injectables → testable sans audio réel.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, List, Optional
import wave

from ..ledger import ConversationAudioLedger


class LocalAudioPlayer:
    def __init__(self, ledger: Optional[ConversationAudioLedger] = None,
                 play_fn: Optional[Callable] = None, stop_fn: Optional[Callable] = None,
                 audio_frontend: Any = None):
        self.ledger = ledger or ConversationAudioLedger()
        self._play_fn = play_fn      # async callable(path_or_text) ; None => résolution paresseuse
        self._stop_fn = stop_fn
        self.current_generation_id: Optional[str] = None
        self._stopped = False
        self.played: List[dict] = []
        self.dropped: List[dict] = []
        self._play_lock = asyncio.Lock()   # sérialise les chunks (pas de chevauchement audio)
        self._playback_started = False
        self.audio_frontend = audio_frontend

    def set_generation(self, generation_id: str) -> None:
        if (
            self.current_generation_id is not None
            and generation_id != self.current_generation_id
            and self._playback_started
            and not self._stopped
        ):
            # One physical output, one owner: a newer generation preempts audio
            # that is still audible from the previous acknowledgement/answer.
            self.stop()
        self.current_generation_id = generation_id
        self._stopped = False

    @property
    def is_playing(self) -> bool:
        """Return the physical playback state used by full-duplex barge-in."""
        return bool(self._playback_started and not self._stopped)

    def stop(self) -> None:
        self._stopped = True
        if self.audio_frontend is not None:
            end_reference = getattr(
                self.audio_frontend, "end_playback_reference", None
            )
            if callable(end_reference):
                end_reference()
        if self._stop_fn is None and not self._playback_started:
            return
        sf = self._resolve_stop_fn()
        if callable(sf):
            try:
                sf()
            except Exception:
                pass

    async def play(self, *, generation_id: str, sequence: int = 0, text: str = "",
                   duration_ms: int = 0, path: Any = None, audio: bytes = b"",
                   audio_format: str = "pcm16", sample_rate: int = 0,
                   channels: int = 0, cancel: Any = None) -> str:
        """'played' | 'cancelled' | 'stopped' | 'dropped_stale'.

        Sérialisé par `_play_lock` : les segments d'un même tour jouent dans l'ordre,
        sans chevauchement. L'état est re-vérifié APRÈS acquisition du verrou (il a pu
        changer en attendant — interruption pendant la lecture du segment précédent).
        """
        if cancel is not None and getattr(cancel, "cancelled", False):
            return "cancelled"
        async with self._play_lock:
            if cancel is not None and getattr(cancel, "cancelled", False):
                return "cancelled"
            if self._stopped:
                return "stopped"
            if generation_id != self.current_generation_id:
                self.dropped.append({"generation_id": generation_id, "sequence": sequence})
                return "dropped_stale"
            pf = self._resolve_play_fn()
            temporary: Optional[Path] = None
            target = self._playback_target(path, text)
            if audio:
                temporary = self._materialize_audio(
                    audio, audio_format=audio_format,
                    sample_rate=sample_rate, channels=channels,
                )
                target = temporary
            started_at = time.perf_counter()
            if pf is not None:
                self._playback_started = True
                try:
                    if self.audio_frontend is not None:
                        self.audio_frontend.register_playback_reference(target)
                    await pf(target)   # playback réel (ou fake en test)
                except asyncio.CancelledError:
                    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                    self.ledger.on_chunk_interrupted(
                        generation_id, text, elapsed_ms, duration_ms,
                        sequence=sequence,
                    )
                    raise
                finally:
                    self._playback_started = False
                    if self.audio_frontend is not None:
                        end_reference = getattr(
                            self.audio_frontend, "end_playback_reference", None
                        )
                        if callable(end_reference):
                            end_reference()
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            elif temporary is not None:
                temporary.unlink(missing_ok=True)
            if generation_id != self.current_generation_id or self._stopped:
                elapsed_ms = int((time.perf_counter() - started_at) * 1000)
                self.ledger.on_chunk_interrupted(
                    generation_id, text, elapsed_ms, duration_ms,
                    sequence=sequence,
                )
                if generation_id != self.current_generation_id:
                    self.dropped.append({
                        "generation_id": generation_id, "sequence": sequence,
                    })
                    return "dropped_stale"
                return "stopped"
            self.ledger.on_chunk_played(
                generation_id, text, duration_ms, sequence=sequence,
            )
            self.played.append({"generation_id": generation_id, "sequence": sequence, "text": text})
            return "played"

    @staticmethod
    def _playback_target(path: Any, text: str) -> Any:
        if isinstance(path, str):
            return Path(path)
        return path or text

    @staticmethod
    def _materialize_audio(
        audio: bytes, *, audio_format: str, sample_rate: int, channels: int,
    ) -> Path:
        """Materialise bounded provider bytes for the existing cancellable player."""
        if len(audio) > 64 * 1024 * 1024:
            raise ValueError("audio chunk too large")
        descriptor, name = tempfile.mkstemp(prefix="lumena-pcm-", suffix=".wav")
        os.close(descriptor)
        path = Path(name)
        try:
            if str(audio_format).lower() == "wav":
                path.write_bytes(audio)
            elif str(audio_format).lower() == "pcm16" and sample_rate > 0 and channels in {1, 2}:
                with wave.open(str(path), "wb") as output:
                    output.setnchannels(channels)
                    output.setsampwidth(2)
                    output.setframerate(sample_rate)
                    output.writeframes(audio)
            else:
                raise ValueError("unsupported in-memory audio format")
            return path
        except Exception:
            path.unlink(missing_ok=True)
            raise

    # ── Résolution paresseuse de la stack audio (jamais à l'import) ──
    def _resolve_play_fn(self) -> Optional[Callable]:
        if self._play_fn is None:
            from src.voice.tts import get_tts  # noqa: PLC0415 — lazy volontaire
            self._play_fn = get_tts()._play_audio
        return self._play_fn

    def _resolve_stop_fn(self) -> Optional[Callable]:
        if self._stop_fn is None:
            try:
                from src.voice.tts import get_tts  # noqa: PLC0415 — lazy volontaire
                self._stop_fn = get_tts().stop_speaking
            except Exception:
                self._stop_fn = lambda: None
        return self._stop_fn
