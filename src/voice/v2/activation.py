"""Local activation gate for Voice V2 microphone transcripts.

The gate runs after local STT and before a transcript can reach the TurnManager.
It is deliberately small and deterministic: this is a phrase gate backed by the
configured local STT, not a claimed neural wake-word or speaker-ID engine.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time
import unicodedata
from typing import Callable


VALID_ACTIVATION_MODES = frozenset({"open_mic", "wake_phrase", "push_to_talk"})


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", text or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


@dataclass(frozen=True)
class ActivationDecision:
    accepted: bool
    text: str = ""
    reason: str = ""
    activated: bool = False


class VoiceActivationGate:
    """Authorize locally transcribed speech before it reaches reasoning.

    ``wake_phrase`` opens a short conversation window after the configured
    phrase. ``push_to_talk`` only accepts speech after :meth:`arm` was called.
    ``open_mic`` is an explicit compatibility mode.
    """

    def __init__(
        self,
        *,
        mode: str = "wake_phrase",
        wake_phrase: str = "Lumena",
        conversation_window_s: float = 20.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        resolved_mode = str(mode or "").strip().lower()
        if resolved_mode not in VALID_ACTIVATION_MODES:
            raise ValueError(f"mode d'activation invalide: {mode!r}")
        phrase = _normalize(wake_phrase)
        if resolved_mode == "wake_phrase" and not phrase:
            raise ValueError("la phrase d'activation ne peut pas être vide")
        self.mode = resolved_mode
        self.wake_phrase = wake_phrase.strip() or "Lumena"
        self._normalized_phrase = phrase
        self.conversation_window_s = max(1.0, min(300.0, float(conversation_window_s)))
        self._clock = clock
        self._active_until = 0.0
        self._push_to_talk_armed = False

    @property
    def is_active(self) -> bool:
        if self.mode == "open_mic":
            return True
        if self.mode == "push_to_talk":
            return self._push_to_talk_armed
        return self._clock() <= self._active_until

    def arm(self) -> None:
        """Arm one utterance in push-to-talk mode."""
        self._push_to_talk_armed = True

    def close(self) -> None:
        self._active_until = 0.0
        self._push_to_talk_armed = False

    def evaluate(
        self, text: str, *, wake_detected: bool = False,
        speaker_match: bool | None = None,
    ) -> ActivationDecision:
        raw = (text or "").strip()
        if not raw:
            return ActivationDecision(False, reason="empty_transcript")
        if self.mode == "open_mic":
            return ActivationDecision(True, text=raw, reason="open_mic")
        if self.mode == "push_to_talk":
            if not self._push_to_talk_armed:
                return ActivationDecision(False, reason="push_to_talk_not_armed")
            self._push_to_talk_armed = False
            return ActivationDecision(True, text=raw, reason="push_to_talk")

        normalized = _normalize(raw)
        phrase_pattern = r"(?:^|\s)" + re.escape(self._normalized_phrase) + r"(?:\s|$)"
        match = re.search(phrase_pattern, normalized)
        if match or wake_detected:
            # Preserve the user's wording for the LLM.  The normalised match is
            # only the authorization check; configured wake-phrase words are
            # removed from the original transcript when they can be located.
            words = [re.escape(word) for word in self.wake_phrase.split() if word]
            raw_pattern = r"(?:^|\W)" + r"\W+".join(words) + r"(?:\W|$)"
            remaining = re.sub(raw_pattern, " ", raw, count=1, flags=re.IGNORECASE).strip(" ,.!?:;-")
            if match and not remaining and normalized != self._normalized_phrase:
                # Accent-insensitive fallback for unusual configured phrases.
                remaining = (normalized[:match.start()] + " " + normalized[match.end():]).strip()
            self._active_until = self._clock() + self.conversation_window_s
            return ActivationDecision(
                True,
                text=remaining,
                reason=(
                    "wake_word_speaker_signal" if wake_detected and speaker_match is True
                    else "wake_word" if wake_detected else "wake_phrase"
                ),
                activated=True,
            )
        if self.is_active:
            self._active_until = self._clock() + self.conversation_window_s
            return ActivationDecision(True, text=raw, reason="conversation_window")
        return ActivationDecision(False, reason="wake_phrase_missing")


def activation_gate_from_env() -> VoiceActivationGate:
    """Build the production gate from validated, bounded environment values."""
    import os

    mode = os.getenv("LUMENA_VOICE_ACTIVATION_MODE", "wake_phrase")
    phrase = os.getenv("LUMENA_VOICE_WAKE_PHRASE", "Lumena")
    try:
        window = float(os.getenv("LUMENA_VOICE_CONVERSATION_WINDOW_S", "20"))
    except (TypeError, ValueError):
        window = 20.0
    return VoiceActivationGate(
        mode=mode,
        wake_phrase=phrase,
        conversation_window_s=window,
    )
