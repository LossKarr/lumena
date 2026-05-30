"""Contrats providers voix (V2 §1). Aucun I/O audio ici — uniquement les interfaces.

But : rendre les moteurs interchangeables tout en gardant UNE politique (VoiceProfile).
À ce stade, seul `TTSProvider` est exploité ; `STTProvider`/`RealtimeVoiceProvider`
sont déclarés pour figer le contrat (implémentations plus tard).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional


class CancelToken:
    """Jeton d'annulation coopératif (vérifié toutes les 20-50 ms en prod)."""
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


@dataclass
class TTSAudioChunk:
    sequence: int
    text: str = ""
    audio: bytes = b""
    audio_path: Optional[str] = None   # chemin fichier (TTS local fichier-based)
    duration_ms: int = 0
    audio_format: str = "pcm16"
    provider: str = ""
    degraded: bool = False


@dataclass
class AudioResult:
    ok: bool = True
    text: str = ""
    audio: bytes = b""
    audio_path: Optional[str] = None
    duration_ms: int = 0
    chunk_count: int = 0
    audio_format: str = "pcm16"
    provider: str = ""          # moteur effectif (ex. "xtts"/"piper"/"edge-tts"/"pyttsx3")
    degraded: bool = False      # True si fallback dégradé (ex. pyttsx3)


class TTSProvider(ABC):
    """Synthèse vocale. `voice` est un VoiceProfile (typé librement pour éviter un import dur)."""
    name: str = "base"
    locality: str = "local"          # "local" | "cloud"
    supports_streaming: bool = False
    supports_voice_clone: bool = False

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    async def synthesize(self, text: str, voice: Any, cancel: Optional[CancelToken] = None) -> AudioResult:
        ...

    async def stream(self, text: str, voice: Any,
                     cancel: Optional[CancelToken] = None) -> AsyncIterator[TTSAudioChunk]:
        """Streaming par défaut : un seul chunk dérivé de `synthesize` (override si vrai streaming)."""
        res = await self.synthesize(text, voice, cancel)
        if res.ok:
            yield TTSAudioChunk(0, res.text, res.audio, res.duration_ms, res.audio_format)


@dataclass
class VADEvent:
    """Événement de détection d'activité vocale (frontières de parole)."""
    kind: str = "speech_started"     # "speech_started" | "speech_ended"
    t: int = 0                        # horodatage relatif (ms)
    energy: float = 0.0               # niveau (diagnostic ; non requis par la logique)


@dataclass
class STTResult:
    """Résultat de transcription : partiel (timing) ou final (contenu)."""
    text: str = ""
    is_final: bool = False
    t: int = 0


class VADProvider(ABC):
    """Détection d'activité vocale. Émet des frontières (start/end), JAMAIS de texte.

    C'est la source du barge-in : la parole détectée pendant que Lumena parle peut
    couper la voix (`partials = timing, finals = content`). Aucun I/O audio ici —
    `stream()` consomme un flux audio abstrait et produit des `VADEvent`.
    """
    name: str = "base"
    locality: str = "local"

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    async def stream(self, audio: Any) -> AsyncIterator[VADEvent]:
        ...


class STTProvider(ABC):
    """Reconnaissance vocale (contrat figé ; implémentation locale plus tard)."""
    name: str = "base"
    locality: str = "local"

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    async def transcribe(self, audio: Any, *, language: str = "fr") -> str:
        ...

    async def stream(self, audio: Any, *, language: str = "fr") -> AsyncIterator[STTResult]:
        """Transcription incrémentale : partiels (timing) puis final (contenu).

        Défaut non-streamant : un seul `final` dérivé de `transcribe` (override pour
        un vrai streaming partiel/final)."""
        text = await self.transcribe(audio, language=language)
        yield STTResult(text=text, is_final=True)


class RealtimeVoiceProvider(ABC):
    """Conversation full-duplex cloud (contrat figé ; pas implémenté à ce stade)."""
    name: str = "base"
    locality: str = "cloud"

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    async def start_session(self, voice: Any, tools: Optional[list] = None,
                            cancel: Optional[CancelToken] = None) -> Any:
        ...
