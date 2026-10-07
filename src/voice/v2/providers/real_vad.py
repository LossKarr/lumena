"""RealVADProvider — VAD local hybride, sous le contrat VADProvider.

HARDWARE-LAST : imports PARESSEUX de `pyaudio`/`audioop` (jamais à l'import du
module). Réservé au chemin gated `LUMENA_VOICE_V2_STT=1`, hors pytest. Pas de
Le RMS énergétique reste le fallback garanti. Une fonction de probabilité de parole
locale (Silero ONNX, quand son Voice Pack est présent) peut être injectée sans changer
la machine à états et sans téléchargement au runtime.

Machine à états simple :
- énergie > seuil  → `speech_started` (front montant, après silence) ;
- silence soutenu ≥ `silence_hangover_ms` → `speech_ended`.
L'audio entre start et end est capturé dans `last_utterance` (PCM16) pour que le
STT transcrive l'énoncé — la VAD donne le TIMING, Whisper donne le CONTENU.

Testable SANS micro : `frames` (itérable de frames PCM16) et `rms_fn` injectables.
"""
from __future__ import annotations

import asyncio
import inspect
import statistics
from typing import Any, AsyncIterator, Callable, Iterable, List, Optional

from .base import VADProvider, VADEvent


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def measure_noise_floor(samples: Iterable[float]) -> Optional[float]:
    """Plancher de bruit = médiane des énergies (robuste aux pics ponctuels).

    Renvoie None si aucun échantillon (→ fallback côté appelant)."""
    vals = [float(s) for s in samples]
    if not vals:
        return None
    return float(statistics.median(vals))


def calibrate_thresholds(noise_floor: float, *, noise_mult: float = 2.5,
                         energy_min: int = 180, energy_max: int = 800,
                         speaking_floor: int = 1200, speaking_mult: int = 4) -> tuple:
    """Dérive (energy_threshold, speaking_threshold) depuis le bruit ambiant.

    energy_threshold   = clamp(noise_floor * noise_mult, energy_min, energy_max)
    speaking_threshold = max(speaking_floor, energy_threshold * speaking_mult)
    Le plancher 1200 (validé en labo) garde l'anti-écho efficace même en milieu calme.
    """
    energy = int(_clamp(noise_floor * noise_mult, energy_min, energy_max))
    speaking = int(max(speaking_floor, energy * speaking_mult))
    return energy, speaking


class RealVADProvider(VADProvider):
    name = "real_energy_vad"
    locality = "local"

    SAMPLE_RATE = 16000
    SAMPLE_WIDTH = 2  # PCM16

    def __init__(self, *, energy_threshold: int = 300, frame_ms: int = 30,
                 silence_hangover_ms: int = 700, min_speech_ms: int = 150,
                 speaking_threshold: Optional[int] = None,
                 is_speaking_fn: Optional[Callable[[], bool]] = None,
                 partial_every_ms: int = 0,
                 input_device_index: Optional[int] = None,
                 frames: Optional[Iterable[bytes]] = None,
                 rms_fn: Optional[Callable[[bytes], float]] = None,
                 speech_probability_fn: Optional[Callable[[bytes], float]] = None,
                 speech_probability_threshold: float = 0.5,
                 speaking_guard_enabled: bool = True):
        self.energy_threshold = energy_threshold
        self.frame_ms = frame_ms
        self.silence_hangover_ms = silence_hangover_ms
        self.min_speech_ms = min_speech_ms
        # PARTIELS : si > 0, émet un VADEvent `speech_partial` toutes les `partial_every_ms`
        # PENDANT la parole, avec un snapshot de l'audio capturé jusqu'ici dans
        # `partial_utterance` (le consommateur le transcrit → stt.partial). 0 = désactivé
        # (défaut inchangé : seulement start/end + final).
        self.partial_every_ms = partial_every_ms
        self.partial_utterance: bytes = b""
        self.input_device_index = input_device_index
        # SELF-VOICE GUARD : pendant que Lumena parle (is_speaking_fn() == True), on
        # exige un seuil PLUS HAUT pour qu'un barge-in se déclenche → l'écho de Piper
        # réentendu par le micro (énergie modérée) ne passe pas, mais une vraie voix
        # nettement plus forte oui. Sans AEC, c'est le durcissement minimal logic-only.
        self.speaking_threshold = speaking_threshold
        # The guard policy is explicit because ``speaking_threshold=None`` has
        # historically meant "derive it during calibration".  A headset or an
        # active AEC disables the guard without changing that public contract.
        self._speaking_guard_enabled = bool(speaking_guard_enabled)
        self._is_speaking_fn = is_speaking_fn
        self._frames = frames          # None => micro réel (pyaudio) ; sinon source injectée (test)
        self._rms_fn = rms_fn          # None => audioop.rms (lazy) ; sinon injecté (test)
        self._speech_probability_fn = speech_probability_fn
        self.speech_probability_threshold = float(
            _clamp(speech_probability_threshold, 0.05, 0.99)
        )
        self.vad_engine = "silero_onnx" if speech_probability_fn is not None else "energy"
        self.vad_fallback_reason: Optional[str] = None
        self.last_speech_probability: Optional[float] = None
        self.last_utterance: bytes = b""
        self._buf: List[bytes] = []
        self._stop_requested = False

    def _effective_threshold(self) -> float:
        """Seuil courant : relevé à `speaking_threshold` quand Lumena parle."""
        if (self._speaking_guard_enabled and self.speaking_threshold is not None
                and self._is_speaking_fn is not None):
            try:
                if self._is_speaking_fn():
                    return self.speaking_threshold
            except Exception:
                pass
        return self.energy_threshold

    def is_available(self) -> bool:
        if self._frames is not None:
            return True               # source injectée (test) : toujours dispo
        try:
            import importlib.util
            return importlib.util.find_spec("pyaudio") is not None
        except Exception:
            return False

    def stop(self) -> None:
        """Demande l'arrêt de la capture en laissant `stream()` clôturer le tour ouvert."""
        self._stop_requested = True

    async def calibrate(self, *, duration_ms: int = 800,
                        frames: Optional[Iterable[bytes]] = None,
                        noise_mult: float = 2.5, energy_min: int = 180,
                        energy_max: int = 800, speaking_floor: int = 1200,
                        speaking_mult: int = 4) -> dict:
        """Calibration ONE-SHOT du seuil VAD depuis le bruit ambiant (opt-in).

        Mesure ~`duration_ms` de bruit (micro réel) ou consomme `frames` (test), puis
        APPLIQUE energy_threshold/speaking_threshold. Sur mesure vide ou erreur → on
        garde les seuils courants (fallback), `noise_floor=None`, `fallback=True`.
        Pas de ré-adaptation continue : un seul passage, avant l'écoute.
        """
        rms = self._resolve_rms()
        samples: List[float] = []
        try:
            if frames is not None:
                for f in frames:
                    samples.append(rms(f))
            else:
                self._stop_requested = False
                n = max(1, duration_ms // self.frame_ms)
                async for f in self._iter_frames():
                    samples.append(rms(f))
                    if len(samples) >= n:
                        break
        except Exception:
            samples = []

        noise_floor = measure_noise_floor(samples)
        if noise_floor is None:
            return {"noise_floor": None, "energy_threshold": self.energy_threshold,
                    "speaking_threshold": self.speaking_threshold, "fallback": True}

        energy, speaking = calibrate_thresholds(
            noise_floor, noise_mult=noise_mult, energy_min=energy_min,
            energy_max=energy_max, speaking_floor=speaking_floor, speaking_mult=speaking_mult)
        self.energy_threshold = energy
        self.speaking_threshold = speaking if self._speaking_guard_enabled else None
        return {"noise_floor": noise_floor, "energy_threshold": energy,
                "speaking_threshold": self.speaking_threshold, "fallback": False}

    def _resolve_rms(self) -> Callable[[bytes], float]:
        if self._rms_fn is None:
            import audioop  # noqa: PLC0415 — lazy volontaire
            self._rms_fn = lambda frame: audioop.rms(frame, self.SAMPLE_WIDTH)
        return self._rms_fn

    async def _is_voiced(self, frame: bytes, energy: float) -> bool:
        """Classe un frame, avec fallback énergétique borné en cas d'échec neuronal.

        Le self-voice guard demeure un garde d'énergie pendant le playback : sans AEC,
        un classifieur de parole seul ne peut distinguer l'utilisateur du TTS réentendu.
        """
        fallback = energy >= self._effective_threshold()
        if self._speech_probability_fn is None:
            return fallback
        try:
            probability = self._speech_probability_fn(frame)
            if inspect.isawaitable(probability):
                probability = await probability
            probability = float(probability)
            if not 0.0 <= probability <= 1.0:
                raise ValueError("speech probability outside [0, 1]")
            self.last_speech_probability = probability
            neural_voiced = probability >= self.speech_probability_threshold
            if (self._speaking_guard_enabled and self.speaking_threshold is not None
                    and self._is_speaking_fn is not None):
                try:
                    if self._is_speaking_fn():
                        return neural_voiced and energy >= self.speaking_threshold
                except Exception:
                    pass
            return neural_voiced
        except Exception as exc:
            # Un moteur optionnel ne doit jamais rendre le micro inutilisable.
            self.vad_engine = "energy"
            self.vad_fallback_reason = type(exc).__name__
            self._speech_probability_fn = None
            self.last_speech_probability = None
            return fallback

    def status(self) -> dict:
        """État sans audio ni texte, destiné au panneau et aux diagnostics."""
        return {
            "engine": self.vad_engine,
            "fallback_reason": self.vad_fallback_reason,
            "energy_threshold": self.energy_threshold,
            "speaking_threshold": self.speaking_threshold,
            "speaking_guard_enabled": self._speaking_guard_enabled,
            "speech_probability_threshold": (
                self.speech_probability_threshold
                if self.vad_engine == "silero_onnx" else None
            ),
        }

    async def stream(self, audio: Any = None) -> AsyncIterator[VADEvent]:
        """Émet `speech_started`/`speech_ended` ; capture l'énoncé dans `last_utterance`."""
        self._stop_requested = False
        rms = self._resolve_rms()
        hangover_frames = max(1, self.silence_hangover_ms // self.frame_ms)
        min_speech_frames = max(1, self.min_speech_ms // self.frame_ms)
        partial_frames = (self.partial_every_ms // self.frame_ms) if self.partial_every_ms > 0 else 0

        in_speech = False
        silence_run = 0
        speech_run = 0
        speech_frames = 0          # frames écoulées depuis le début du tour (pour les partiels)
        t_ms = 0

        async for frame in self._iter_frames():
            energy = rms(frame)
            voiced = await self._is_voiced(frame, energy)

            if not in_speech:
                if voiced:
                    speech_run += 1
                    if speech_run >= min_speech_frames:
                        in_speech = True
                        silence_run = 0
                        speech_frames = 1
                        self._buf = [frame]
                        yield VADEvent(kind="speech_started", t=t_ms, energy=float(energy))
                else:
                    speech_run = 0
            else:
                self._buf.append(frame)
                speech_frames += 1
                if voiced:
                    silence_run = 0
                else:
                    silence_run += 1
                    if silence_run >= hangover_frames:
                        in_speech = False
                        speech_run = 0
                        self.last_utterance = b"".join(self._buf)
                        self._buf = []
                        yield VADEvent(kind="speech_ended", t=t_ms, energy=float(energy))
                        t_ms += self.frame_ms
                        continue
                # Partiel périodique : snapshot de l'audio capturé jusqu'ici.
                if partial_frames and speech_frames % partial_frames == 0:
                    self.partial_utterance = b"".join(self._buf)
                    yield VADEvent(kind="speech_partial", t=t_ms, energy=float(energy))

            t_ms += self.frame_ms

        # Fin de flux pendant une parole : clôture propre.
        if in_speech:
            self.last_utterance = b"".join(self._buf)
            yield VADEvent(kind="speech_ended", t=t_ms, energy=0.0)

    async def _iter_frames(self) -> AsyncIterator[bytes]:
        if self._frames is not None:
            for frame in self._frames:        # source injectée (test) — déterministe
                yield frame
            return
        # Micro réel : capture pyaudio en exécuteur (lecture bloquante hors boucle event).
        import pyaudio  # noqa: PLC0415 — lazy volontaire
        loop = asyncio.get_running_loop()
        chunk = int(self.SAMPLE_RATE * self.frame_ms / 1000)
        pa = pyaudio.PyAudio()
        open_kwargs = {
            "format": pyaudio.paInt16, "channels": 1, "rate": self.SAMPLE_RATE,
            "input": True, "frames_per_buffer": chunk,
        }
        if self.input_device_index is not None:
            open_kwargs["input_device_index"] = int(self.input_device_index)
        stream = pa.open(**open_kwargs)
        try:
            while not self._stop_requested:
                frame = await loop.run_in_executor(
                    None, lambda: stream.read(chunk, exception_on_overflow=False))
                yield frame
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()
