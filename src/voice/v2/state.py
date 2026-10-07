"""VoiceState + EndpointDecision (V2.3)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Modes possibles du TurnManager.
VOICE_MODES = (
    "idle",
    "wake_listening",
    "user_speaking",
    "user_paused",
    "thinking",
    "speaking",
    "tool_running",
    "interrupted",
    "muted",
    "error",
)


@dataclass
class EndpointDecision:
    """Décision de fin de tour. `state` = silence vs vraie fin de pensée."""
    state: str  # "turn_complete" | "continue_expected" | "uncertain"
    confidence: float = 0.0
    min_wait_ms: int = 300
    max_wait_ms: int = 2500
    reason: str = ""


@dataclass
class VoiceState:
    """État unique du TurnManager. Muté UNIQUEMENT dans le reducer."""
    mode: str = "idle"
    current_turn_id: Optional[str] = None
    current_generation_id: Optional[str] = None
    # Génération coupée par un barge-in potentiel, gardée jusqu'au transcript
    # final afin de reprendre la partie non entendue si le signal était du bruit.
    interrupted_generation_id: Optional[str] = None
    partial_transcript: str = ""
    final_transcript: str = ""
    final_language: Optional[str] = None
    final_language_probability: float = 0.0
    # Vrai entre le début explicite d'une transcription et son événement final,
    # y compris pour no-speech, fragment filtré, timeout et erreur. Un timer de
    # silence ne peut jamais décider d'une reprise pendant cette fenêtre.
    transcription_pending: bool = False
    transcription_turn_id: Optional[str] = None
    transcription_terminal_reason: Optional[str] = None
    endpoint_decision: Optional[EndpointDecision] = None
    cloud_active: bool = False
    speech_muted: bool = False
    tool_active: bool = False
    # Interne : une parole utilisateur a été détectée pendant que Lumena parlait,
    # mais pas encore confirmée par un transcript (anti faux barge-in).
    pending_barge_in: bool = False
    # Interne : un timer de silence (endpointing) est armé depuis `vad.speech_ended`.
    # Tant qu'il n'a pas expiré, on n'a pas conclu la fin du tour. `endpoint_armed_turn`
    # fige le tour concerné (un timer en retard d'un ancien tour est ignoré).
    endpoint_armed: bool = False
    endpoint_armed_turn: Optional[str] = None
    endpoint_started_t: Optional[float] = None
    endpoint_max_wait_ms: int = 2500
    # Moyenne mobile explicable, mise à jour seulement quand l'utilisateur reprend
    # après une pause. Bornée pour qu'une session atypique ne rende pas l'UI molle.
    user_avg_pause_ms: int = 600
    observed_pause_count: int = 0

    def set_mode(self, mode: str) -> None:
        assert mode in VOICE_MODES, f"mode inconnu: {mode}"
        self.mode = mode
