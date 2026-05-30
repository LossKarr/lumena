"""VoiceEvent / VoiceCommand — contrats du TurnManager (V2.3).

- VoiceEvent  : « quelque chose est arrivé » (VAD, STT, TTS, playback, UI, outils,
  timers, providers). Les sources ne font qu'`emit(event)`.
- VoiceCommand: « demande à un composant d'agir ». Le TurnManager NE lit pas le micro,
  NE joue pas l'audio, N'appelle pas le LLM : il décide et émet des commandes.

Le `type`/`name` sont des chaînes (mêmes valeurs que le JSONL de replay) pour que
prod et tests rejouent exactement les mêmes événements.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass(frozen=True)
class VoiceEvent:
    """Un fait observé. `t` = horodatage ms (déterministe en replay)."""
    type: str
    t: int = 0
    data: Dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)


@dataclass(frozen=True)
class VoiceCommand:
    """Un effet de bord demandé par le TurnManager (jamais exécuté par lui)."""
    name: str
    data: Dict[str, Any] = field(default_factory=dict)


# ── Types d'événements connus (référence ; non exhaustif imposé) ──────────────
EVENT_TYPES = frozenset({
    "vad.speech_started", "vad.speech_stopped", "vad.speech_ended",
    "stt.partial", "stt.final",
    "endpoint.decision",
    "llm.response_started", "llm.response_delta", "llm.response_done",
    "tts.chunk_ready",
    "playback.chunk_played", "playback.finished",
    "user.stop_word", "user.barge_in", "user.cancel_action",
    "tool.started", "tool.progress", "tool.finished",
    "ui.mute", "ui.unmute", "ui.cancel",
    "timer.endpoint", "timer.endpoint_min_elapsed", "timer.endpoint_max_elapsed",
    "timer.false_interruption_timeout", "timer.conversation_idle",
    "provider.error",
})

# ── Noms de commandes connus ──────────────────────────────────────────────────
COMMAND_NAMES = frozenset({
    "start_stt", "stop_stt",
    "start_llm", "cancel_llm",
    "start_tts", "cancel_tts",
    "play_audio", "stop_playback", "clear_audio_queue", "resume_playback",
    "truncate_conversation",
    "arm_endpoint_timer", "cancel_endpoint_timer",
    "speak_backchannel",
    "cancel_tool_if_safe",
    "request_confirmation",
    "show_status",
})
