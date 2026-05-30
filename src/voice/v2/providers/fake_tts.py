"""FakeTTSProvider — TTS déterministe sans audio réel (tests + thin slice).

Découpe le texte en phrases, « synthétise » des octets factices, respecte le
CancelToken. Aucune dépendance audio.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, List, Optional

from .base import TTSProvider, TTSAudioChunk, AudioResult, CancelToken


def _sentences(text: str) -> List[str]:
    parts = [p.strip() for p in text.replace("!", ".").replace("?", ".").split(".")]
    return [p for p in parts if p]


class FakeTTSProvider(TTSProvider):
    name = "fake"
    locality = "local"
    supports_streaming = True
    supports_voice_clone = False

    def __init__(self, chunk_ms: int = 200, available: bool = True):
        self.chunk_ms = chunk_ms
        self._available = available

    def is_available(self) -> bool:
        return self._available

    async def synthesize(self, text: str, voice: Any, cancel: Optional[CancelToken] = None) -> AudioResult:
        if cancel and cancel.cancelled:
            return AudioResult(ok=False, text=text)
        chunks = _sentences(text)
        audio = b"".join(c.encode("utf-8") for c in chunks)  # octets factices déterministes
        return AudioResult(
            ok=True, text=text, audio=audio,
            duration_ms=len(chunks) * self.chunk_ms, chunk_count=len(chunks),
        )

    async def stream(self, text: str, voice: Any,
                     cancel: Optional[CancelToken] = None) -> AsyncIterator[TTSAudioChunk]:
        for i, sentence in enumerate(_sentences(text)):
            if cancel and cancel.cancelled:
                return  # annulation coopérative : on arrête d'émettre
            yield TTSAudioChunk(
                sequence=i, text=sentence,
                audio=sentence.encode("utf-8"), duration_ms=self.chunk_ms,
            )
