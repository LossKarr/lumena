"""Providers voix V2 — contrats interchangeables (TTS d'abord).

`base` et `fake_tts` sont légers. `local_tts` adapte le TTS existant DERRIÈRE le
contrat avec un import PARESSEUX : importer ce module ne charge pas la stack audio ;
celle-ci n'est chargée qu'au premier usage réel (étape ultérieure).
"""
from .base import (
    CancelToken, TTSAudioChunk, AudioResult, VADEvent, STTResult,
    TTSProvider, STTProvider, VADProvider, RealtimeVoiceProvider,
)
from .fake_tts import FakeTTSProvider
from .fake_vad import FakeVADProvider
from .fake_stt import FakeSTTProvider
from .local_tts import LocalTTSAdapter
from .local_player import LocalAudioPlayer
from .real_stt import RealSTTAdapter
from .real_vad import RealVADProvider, measure_noise_floor, calibrate_thresholds

__all__ = [
    "CancelToken", "TTSAudioChunk", "AudioResult", "VADEvent", "STTResult",
    "TTSProvider", "STTProvider", "VADProvider", "RealtimeVoiceProvider",
    "FakeTTSProvider", "FakeVADProvider", "FakeSTTProvider",
    "LocalTTSAdapter", "LocalAudioPlayer",
    "RealSTTAdapter", "RealVADProvider", "measure_noise_floor", "calibrate_thresholds",
]
