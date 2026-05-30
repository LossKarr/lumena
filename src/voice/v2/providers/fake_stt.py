"""FakeSTTProvider — STT déterministe sans audio réel (tests + thin slice).

Rejoue une liste scriptée de résultats (partiels puis final) sous forme de
`STTResult`. `transcribe()` renvoie le dernier final. Aucune dépendance audio.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Iterable, List, Optional, Tuple

from .base import STTProvider, STTResult


class FakeSTTProvider(STTProvider):
    name = "fake_stt"
    locality = "local"

    def __init__(self, script: Optional[Iterable[Tuple[int, str, bool]]] = None,
                 available: bool = True):
        # script = liste de (t_ms, text, is_final). Les partiels pilotent le timing,
        # le final pilote le contenu (V2.3 : partials = timing, finals = content).
        self._script: List[Tuple[int, str, bool]] = list(script or [])
        self._available = available

    def is_available(self) -> bool:
        return self._available

    async def transcribe(self, audio: Any = None, *, language: str = "fr") -> str:
        finals = [text for _, text, is_final in self._script if is_final]
        return finals[-1] if finals else ""

    async def stream(self, audio: Any = None, *, language: str = "fr") -> AsyncIterator[STTResult]:
        for t, text, is_final in self._script:
            yield STTResult(text=text, is_final=is_final, t=t)
