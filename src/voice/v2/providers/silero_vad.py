"""Adaptateur optionnel Silero VAD ONNX, entièrement local et sans téléchargement.

Le paquet et son modèle doivent déjà appartenir à l'installation/Voice Pack. Le
module n'importe ni torch ni silero_vad avant ``load`` ; une absence donne un état
explicite et laisse l'appelant sélectionner le VAD énergétique.
"""
from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from typing import Any, Optional


@dataclass(frozen=True)
class SileroVADStatus:
    available: bool
    engine: str = "silero_onnx"
    reason: Optional[str] = None


class SileroSpeechProbability:
    """Callable PCM16 mono 16 kHz -> probabilité de parole Silero."""

    SAMPLE_RATE = 16000
    WINDOW_SAMPLES = 512

    def __init__(self, model: Any, torch_module: Any) -> None:
        self._model = model
        self._torch = torch_module

    @classmethod
    def probe(cls) -> SileroVADStatus:
        try:
            if importlib.util.find_spec("silero_vad") is None:
                return SileroVADStatus(False, reason="silero_vad_not_installed")
            if importlib.util.find_spec("torch") is None:
                return SileroVADStatus(False, reason="torch_not_installed")
        except (ImportError, ModuleNotFoundError, ValueError):
            return SileroVADStatus(False, reason="dependency_probe_failed")
        return SileroVADStatus(True)

    @classmethod
    def load(cls) -> tuple[Optional["SileroSpeechProbability"], SileroVADStatus]:
        status = cls.probe()
        if not status.available:
            return None, status
        try:
            import torch  # noqa: PLC0415 - dépendance lourde, chemin opt-in
            from silero_vad import load_silero_vad  # noqa: PLC0415

            # Le paquet distribue le modèle. Aucun URL et aucun hub ne sont utilisés.
            model = load_silero_vad(onnx=True)
            return cls(model, torch), status
        except Exception as exc:
            return None, SileroVADStatus(
                False, reason=f"load_failed:{type(exc).__name__}"
            )

    def __call__(self, frame: bytes) -> float:
        if not isinstance(frame, (bytes, bytearray, memoryview)):
            raise TypeError("Silero VAD expects PCM16 bytes")
        import array

        pcm = array.array("h")
        pcm.frombytes(bytes(frame))
        samples = [float(value) / 32768.0 for value in pcm]
        if len(samples) < self.WINDOW_SAMPLES:
            samples.extend([0.0] * (self.WINDOW_SAMPLES - len(samples)))
        elif len(samples) > self.WINDOW_SAMPLES:
            samples = samples[-self.WINDOW_SAMPLES:]
        tensor = self._torch.tensor(samples, dtype=self._torch.float32)
        result = self._model(tensor, self.SAMPLE_RATE)
        return float(result.item() if hasattr(result, "item") else result)

