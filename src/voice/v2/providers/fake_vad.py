"""FakeVADProvider — VAD déterministe sans audio réel (tests + thin slice).

Rejoue une liste scriptée de frontières de parole (`speech_started`/`speech_ended`)
sous forme de `VADEvent`. Aucune dépendance audio, aucun hardware.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Iterable, List, Optional, Tuple

from .base import VADProvider, VADEvent


class FakeVADProvider(VADProvider):
    name = "fake_vad"
    locality = "local"

    def __init__(self, script: Optional[Iterable[Tuple[int, str]]] = None,
                 available: bool = True):
        # script = liste de (t_ms, kind) ; kind ∈ {"speech_started","speech_ended"}.
        self._script: List[Tuple[int, str]] = list(script or [])
        self._available = available

    def is_available(self) -> bool:
        return self._available

    async def stream(self, audio: Any = None) -> AsyncIterator[VADEvent]:
        for t, kind in self._script:
            yield VADEvent(kind=kind, t=t)
