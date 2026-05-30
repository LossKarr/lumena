"""LocalAudioPlayer — playback réel cancellable + discipline generation_id (V2.3).

Enveloppe `LumenaTTS._play_audio` (import PARESSEUX) :
- ne joue QUE les chunks dont `generation_id == courant` (le reste = dropped_stale) ;
- `stop()` interrompt et passe par `tts.stop_speaking()` ;
- alimente le `ConversationAudioLedger` (ce qui a été réellement entendu) ;
- `play_fn`/`stop_fn` injectables → testable sans audio réel.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, List, Optional

from ..ledger import ConversationAudioLedger


class LocalAudioPlayer:
    def __init__(self, ledger: Optional[ConversationAudioLedger] = None,
                 play_fn: Optional[Callable] = None, stop_fn: Optional[Callable] = None):
        self.ledger = ledger or ConversationAudioLedger()
        self._play_fn = play_fn      # async callable(path_or_text) ; None => résolution paresseuse
        self._stop_fn = stop_fn
        self.current_generation_id: Optional[str] = None
        self._stopped = False
        self.played: List[dict] = []
        self.dropped: List[dict] = []
        self._play_lock = asyncio.Lock()   # sérialise les chunks (pas de chevauchement audio)
        self._playback_started = False

    def set_generation(self, generation_id: str) -> None:
        self.current_generation_id = generation_id
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True
        if self._stop_fn is None and not self._playback_started:
            return
        sf = self._resolve_stop_fn()
        if callable(sf):
            try:
                sf()
            except Exception:
                pass

    async def play(self, *, generation_id: str, sequence: int = 0, text: str = "",
                   duration_ms: int = 0, path: Any = None, cancel: Any = None) -> str:
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
            if pf is not None:
                self._playback_started = True
                await pf(self._playback_target(path, text))   # playback réel (ou fake en test)
            self.ledger.on_chunk_played(generation_id, text, duration_ms)
            self.played.append({"generation_id": generation_id, "sequence": sequence, "text": text})
            return "played"

    @staticmethod
    def _playback_target(path: Any, text: str) -> Any:
        if isinstance(path, str):
            return Path(path)
        return path or text

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
