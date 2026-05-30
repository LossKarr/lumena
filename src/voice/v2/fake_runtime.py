"""FakeRuntime — exécute les VoiceCommand sans audio réel (V2.3).

Boucle un tour complet de façon DÉTERMINISTE, sans micro/TTS/WebRTC/provider :
- fake LLM : produit une réponse texte fixe ;
- fake TTS : découpe la réponse en chunks ;
- fake playback : « joue » les chunks via l'AudioOutputQueue + alimente le ledger ;
- streaming modélisé : chaque chunk joué déclenche le suivant (donc l'annulation
  stoppe réellement la suite) ;
- discipline `generation_id` centralisée dans l'AudioOutputQueue.

Le `Driver` est pas-à-pas (`tick`) pour permettre d'injecter une interruption
au milieu d'un tour, exactement comme en prod.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, List, Optional

from .events import VoiceEvent, VoiceCommand
from .ledger import AudioChunk, AudioOutputQueue, ConversationAudioLedger
from .turn_manager import TurnManager

_CLEARED = "__cleared__"


@dataclass
class _GenState:
    chunks: List[str]
    idx: int = 0
    cancelled: bool = False


@dataclass
class FakeRuntime:
    """Exécute les commandes émises par le TurnManager et renvoie des événements suivants."""
    llm_answer: str = "Voici la reponse. Premier point. Deuxieme point."
    chunk_ms: int = 200
    audio_queue: AudioOutputQueue = field(default_factory=lambda: AudioOutputQueue(maxsize=64))
    ledger: ConversationAudioLedger = field(default_factory=ConversationAudioLedger)

    # Observabilité (pour les tests)
    played: List[Dict] = field(default_factory=list)        # chunks réellement joués
    dropped: List[Dict] = field(default_factory=list)        # chunks rejetés (périmés)
    commands_seen: List[str] = field(default_factory=list)
    _gens: Dict[str, _GenState] = field(default_factory=dict)

    def _chunks_for(self, text: str) -> List[str]:
        # Découpe naïve par phrase (suffisant pour le fake).
        parts = [p.strip() for p in text.replace("!", ".").replace("?", ".").split(".")]
        return [p for p in parts if p]

    def execute(self, cmd: VoiceCommand) -> List[VoiceEvent]:
        """Exécute UNE commande, renvoie les événements de suivi (déterministe)."""
        self.commands_seen.append(cmd.name)
        d = cmd.data

        if cmd.name == "start_llm":
            gen = d["generation_id"]; turn = d.get("turn_id")
            chunks = self._chunks_for(self.llm_answer)
            self._gens[gen] = _GenState(chunks=chunks)
            self.ledger.register_generation(turn, gen, self.llm_answer)
            self.audio_queue.set_generation(gen)
            follow = [VoiceEvent("llm.response_started", data={"generation_id": gen})]
            if chunks:
                follow.append(VoiceEvent("tts.chunk_ready", data={
                    "generation_id": gen, "sequence": 0,
                    "text": chunks[0], "duration_ms": self.chunk_ms,
                }))
            return follow

        if cmd.name == "play_audio":
            gen = d.get("generation_id"); seq = d.get("sequence", 0)
            text = d.get("text", ""); dur = d.get("duration_ms", self.chunk_ms)
            res = self.audio_queue.push(AudioChunk(d.get("turn_id"), gen, seq, text, dur))
            if res != "queued":
                self.dropped.append({"generation_id": gen, "sequence": seq, "reason": res})
                return []
            # « playback » immédiat du chunk
            self.audio_queue.pop_playable()
            self.ledger.on_chunk_played(gen, text, dur)
            self.played.append({"generation_id": gen, "sequence": seq, "text": text})
            gs = self._gens.get(gen)
            follow: List[VoiceEvent] = [VoiceEvent("playback.chunk_played", data={
                "generation_id": gen, "sequence": seq,
            })]
            if gs and not gs.cancelled:
                gs.idx = seq + 1
                if gs.idx < len(gs.chunks):
                    follow.append(VoiceEvent("tts.chunk_ready", data={
                        "generation_id": gen, "sequence": gs.idx,
                        "text": gs.chunks[gs.idx], "duration_ms": self.chunk_ms,
                    }))
                else:
                    follow.append(VoiceEvent("playback.finished", data={"generation_id": gen}))
            return follow

        if cmd.name == "clear_audio_queue":
            self.audio_queue.clear()
            self.audio_queue.set_generation(_CLEARED)  # invalide les anciens chunks
            return []

        if cmd.name in ("cancel_tts", "cancel_llm"):
            gen = d.get("generation_id")
            gs = self._gens.get(gen) if gen else None
            if gs:
                gs.cancelled = True
            return []

        if cmd.name == "truncate_conversation":
            gen = d.get("generation_id")
            if gen:
                self.ledger.truncate(gen)
            return []

        # stop_playback / resume_playback / show_status / speak_backchannel /
        # cancel_tool_if_safe / request_confirmation / start_stt / stop_stt :
        # pas d'effet audio à simuler dans le fake.
        return []


class Driver:
    """Pilote pas-à-pas : feed événement -> commandes -> runtime -> événements suivants."""

    def __init__(self, tm: TurnManager, runtime: FakeRuntime):
        self.tm = tm
        self.rt = runtime
        self.pending: Deque[VoiceEvent] = deque()

    def push(self, *events: VoiceEvent) -> None:
        self.pending.extend(events)

    def tick(self) -> bool:
        """Traite UN événement (+ ses suivis immédiats empilés). False si rien à faire."""
        if not self.pending:
            return False
        event = self.pending.popleft()
        commands = self.tm.feed(event)
        for cmd in commands:
            follow = self.rt.execute(cmd)
            self.pending.extend(follow)
        return True

    def run(self, max_ticks: int = 1000) -> int:
        n = 0
        while self.pending and n < max_ticks:
            self.tick()
            n += 1
        return n

    def run_until(self, predicate: Callable[[], bool], max_ticks: int = 1000) -> bool:
        n = 0
        while self.pending and n < max_ticks:
            self.tick()
            n += 1
            if predicate():
                return True
        return predicate()
