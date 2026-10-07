"""Provider local de wake word, borné et sans confiance implicite.

Le moteur acoustique est injectable afin qu'un Voice Pack signé puisse fournir un
modèle Lumena. En son absence, le produit conserve la phrase détectée par le STT et
le push-to-talk ; ce provider ne télécharge jamais de modèle.
"""
from __future__ import annotations

from dataclasses import dataclass
import inspect
import os
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class WakeWordDetection:
    detected: bool
    score: float = 0.0
    engine: str = "unavailable"
    reason: str = ""
    # Signal ergonomique seulement. Il n'est jamais transformé en rôle ou permission.
    speaker_match: Optional[bool] = None


class LocalWakeWordProvider:
    locality = "local"

    def __init__(
        self,
        detector: Optional[Callable[[bytes], Any]] = None,
        *,
        engine: str = "unavailable",
        threshold: float = 0.65,
        model_path: Optional[str | Path] = None,
        unavailable_reason: str = "model_not_configured",
    ) -> None:
        self._detector = detector
        self.engine = str(engine or "unavailable")
        self.threshold = max(0.05, min(0.99, float(threshold)))
        self.model_path = Path(model_path) if model_path else None
        self.unavailable_reason = unavailable_reason
        self.last_detection = WakeWordDetection(False, engine=self.engine, reason="not_run")

    @classmethod
    def from_env(cls) -> "LocalWakeWordProvider":
        model = os.getenv("LUMENA_VOICE_WAKE_MODEL", "").strip()
        try:
            threshold = float(os.getenv("LUMENA_VOICE_WAKE_THRESHOLD", "0.65"))
        except (TypeError, ValueError):
            threshold = 0.65
        # Loading a backend with an unknown/version-dependent API here would be unsafe.
        # A signed Voice Pack wires its verified detector explicitly.
        reason = "model_not_configured" if not model else "provider_not_wired"
        return cls(
            threshold=threshold, model_path=model or None,
            engine="transcript_phrase_fallback", unavailable_reason=reason,
        )

    def is_available(self) -> bool:
        return self._detector is not None and (
            self.model_path is None or self.model_path.exists()
        )

    async def detect(self, audio: bytes) -> WakeWordDetection:
        if not self.is_available():
            result = WakeWordDetection(
                False, engine=self.engine, reason=self.unavailable_reason
            )
            self.last_detection = result
            return result
        try:
            raw = self._detector(bytes(audio))
            if inspect.isawaitable(raw):
                raw = await raw
            speaker_match = None
            if isinstance(raw, dict):
                score = float(raw.get("score", 0.0))
                speaker_match = raw.get("speaker_match")
            else:
                score = float(raw)
            score = max(0.0, min(1.0, score))
            result = WakeWordDetection(
                score >= self.threshold, score=score, engine=self.engine,
                reason="threshold_met" if score >= self.threshold else "below_threshold",
                speaker_match=(bool(speaker_match) if speaker_match is not None else None),
            )
        except Exception as exc:
            result = WakeWordDetection(
                False, engine=self.engine,
                reason=f"detector_error:{type(exc).__name__}",
            )
        self.last_detection = result
        return result

    def status(self) -> dict:
        return {
            "available": self.is_available(), "engine": self.engine,
            "threshold": self.threshold, "reason": (
                None if self.is_available() else self.unavailable_reason
            ),
            "model_present": bool(self.model_path and self.model_path.is_file()),
            "speaker_identity_is_authorization": False,
        }

