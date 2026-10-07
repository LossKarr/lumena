"""Audio queue (discipline generation_id) + ledger conversationnel (V2.3).

Deux invariants durs :
1. La sortie audio ne joue QUE les chunks dont `generation_id == current`.
   Ce check est centralisé ICI, jamais dispersé dans un provider.
2. À l'interruption, on tronque la mémoire à ce qui a été RÉELLEMENT joué
   (l'utilisateur n'a pas entendu ce qui n'a pas été lu).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import re
from typing import Deque, Dict, List, Optional


@dataclass(frozen=True)
class AudioChunk:
    turn_id: str
    generation_id: str
    sequence: int
    text: str = ""
    duration_ms: int = 0
    audio_format: str = "pcm16"


class AudioOutputQueue:
    """File audio bornée. « Dernière génération gagne » + drop des chunks périmés."""

    def __init__(self, maxsize: int = 64):
        self.maxsize = maxsize
        self._q: Deque[AudioChunk] = deque()
        self.current_generation_id: Optional[str] = None
        self.dropped_stale = 0
        self.dropped_overflow = 0

    def set_generation(self, generation_id: str) -> None:
        """Nouvelle génération : les chunks d'avant sont jetés (last wins)."""
        if generation_id != self.current_generation_id:
            self.current_generation_id = generation_id
            self._q.clear()

    def push(self, chunk: AudioChunk) -> str:
        """Retourne 'queued' | 'dropped_stale' | 'dropped_overflow'."""
        if self.current_generation_id is None:
            self.current_generation_id = chunk.generation_id
        # Invariant 1 : un chunk d'une ancienne génération ne joue jamais.
        if chunk.generation_id != self.current_generation_id:
            self.dropped_stale += 1
            return "dropped_stale"
        if len(self._q) >= self.maxsize:
            self._q.popleft()  # drop le plus ancien (non critique) de la gén courante
            self.dropped_overflow += 1
        self._q.append(chunk)
        return "queued"

    def pop_playable(self) -> Optional[AudioChunk]:
        """Sort le prochain chunk jouable (de la génération courante uniquement)."""
        while self._q:
            chunk = self._q.popleft()
            if chunk.generation_id == self.current_generation_id:
                return chunk
            self.dropped_stale += 1
        return None

    def clear(self) -> None:
        self._q.clear()

    def __len__(self) -> int:
        return len(self._q)


@dataclass
class PlayedSpeech:
    turn_id: str
    generation_id: str
    text_played: str = ""
    text_unplayed: str = ""
    played_ms: int = 0
    interrupted: bool = False
    planned_text: str = ""
    synthesized_sequences: List[int] = field(default_factory=list)
    played_sequences: List[int] = field(default_factory=list)
    partially_played_sequences: List[int] = field(default_factory=list)
    heard_until_ms: int = 0


class ConversationAudioLedger:
    """Suit, par génération, ce qui a été RÉELLEMENT joué vs généré."""

    def __init__(self):
        self._by_gen: Dict[str, PlayedSpeech] = {}
        self.order: List[str] = []

    def register_generation(self, turn_id: str, generation_id: str, full_text: str) -> None:
        self._by_gen[generation_id] = PlayedSpeech(
            turn_id=turn_id, generation_id=generation_id,
            text_played="", text_unplayed=full_text, played_ms=0,
            planned_text=full_text,
        )
        self.order.append(generation_id)

    def on_chunk_played(
        self, generation_id: str, text: str, duration_ms: int,
        sequence: Optional[int] = None,
    ) -> None:
        ps = self._by_gen.get(generation_id)
        if ps is None:
            return
        ps.text_played = (ps.text_played + " " + text).strip() if ps.text_played else text
        ps.played_ms += int(duration_ms)
        ps.heard_until_ms = ps.played_ms
        if sequence is not None:
            ps.played_sequences.append(int(sequence))
        # Recalcule l'imprononcé : ce qui reste après le texte joué.
        if ps.text_unplayed and text and ps.text_unplayed.startswith(text):
            ps.text_unplayed = ps.text_unplayed[len(text):].strip()

    def on_chunk_interrupted(
        self, generation_id: str, text: str, elapsed_ms: int, duration_ms: int,
        sequence: Optional[int] = None,
    ) -> None:
        """Record the conservative, word-bounded part heard before cancellation.

        Audio backends do not expose an exact playhead.  We therefore use elapsed
        wall time against the synthesized duration, subtract a small output-buffer
        margin, and only commit complete words.  This prevents a resumed answer
        from repeating the whole current sentence while never claiming that a
        half-played word was heard.
        """
        ps = self._by_gen.get(generation_id)
        if ps is None or not text or duration_ms <= 0:
            return
        conservative_ms = max(0, int(elapsed_ms) - 120)
        fraction = min(1.0, conservative_ms / max(1, int(duration_ms)))
        tokens = list(re.finditer(r"\S+", text))
        heard_count = min(len(tokens), int(len(tokens) * fraction))
        if heard_count <= 0:
            return
        boundary = tokens[heard_count - 1].end()
        heard = text[:boundary].strip()
        if not heard:
            return
        ps.text_played = (
            (ps.text_played + " " + heard).strip() if ps.text_played else heard
        )
        ps.played_ms += conservative_ms
        ps.heard_until_ms = ps.played_ms
        ps.interrupted = True
        if sequence is not None:
            ps.partially_played_sequences.append(int(sequence))
        if ps.text_unplayed.startswith(heard):
            ps.text_unplayed = ps.text_unplayed[len(heard):].strip()

    def truncate(self, generation_id: str) -> Optional[PlayedSpeech]:
        """Marque interrompu et renvoie l'état réellement entendu (pour tronquer l'historique)."""
        ps = self._by_gen.get(generation_id)
        if ps is None:
            return None
        ps.interrupted = True
        return ps

    def on_chunk_synthesized(self, generation_id: str, sequence: int) -> None:
        ps = self._by_gen.get(generation_id)
        if ps is not None:
            ps.synthesized_sequences.append(int(sequence))

    def get(self, generation_id: str) -> Optional[PlayedSpeech]:
        return self._by_gen.get(generation_id)
