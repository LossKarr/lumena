"""Voice System V2 — thin slice (logique de tours de parole, sans audio réel).

Package ISOLÉ : n'importe ni ne modifie le code voix existant (stt/tts/loop).
Pur Python, déterministe, testable en pytest via replay event-level.

Cf. REPO/plans/VOICE_SYSTEM_V2_PLAN.md (Addendum V2.3 — modèle de concurrence).
Cœur : TurnManager acteur unique + une seule file d'événements ; aucune source
externe ne mute l'état ; tous les effets sortent en VoiceCommand explicites.
"""

from .events import VoiceEvent, VoiceCommand
from .state import VoiceState, EndpointDecision
from .ledger import AudioChunk, AudioOutputQueue, ConversationAudioLedger, PlayedSpeech
from .endpointing import decide_endpoint
from .turn_manager import TurnManager
from .replay import parse_events, load_events_jsonl, replay_sync
from .fake_runtime import FakeRuntime, Driver
from .speech_normalizer import normalize_for_speech, SpeechText
from .prewarm import (
    Prewarmer, ComponentStatus, ComponentUnavailable,
    overall_state, ready_message, detect_voice_capabilities,
    PREPARING, READY, DEGRADED, ERROR, SKIPPED,
)
from .voice_profile import (
    VoiceProfile, VoicePersona, VoiceLocalEngines, VoiceCloudMapping,
    LUMENA_DEFAULT, load_profile, save_profile,
)
from .providers import (
    CancelToken, TTSAudioChunk, AudioResult, VADEvent, STTResult,
    TTSProvider, STTProvider, VADProvider, RealtimeVoiceProvider,
    FakeTTSProvider, FakeVADProvider, FakeSTTProvider,
    LocalTTSAdapter, LocalAudioPlayer,
    RealSTTAdapter, RealVADProvider, measure_noise_floor, calibrate_thresholds,
)
from .voice_runtime import VoiceRuntime, v2_tts_enabled
from .input_sources import (
    pump_vad, pump_stt, v2_stt_enabled, EndpointTimerService, MicConversationSource,
)
from .live import VoiceV2Live, run_voice_v2_live, v2_live_enabled, _extract_text

__all__ = [
    "VoiceEvent", "VoiceCommand",
    "VoiceState", "EndpointDecision",
    "AudioChunk", "AudioOutputQueue", "ConversationAudioLedger", "PlayedSpeech",
    "decide_endpoint",
    "TurnManager",
    "parse_events", "load_events_jsonl", "replay_sync",
    "FakeRuntime", "Driver",
    "normalize_for_speech", "SpeechText",
    "Prewarmer", "ComponentStatus", "ComponentUnavailable",
    "overall_state", "ready_message", "detect_voice_capabilities",
    "PREPARING", "READY", "DEGRADED", "ERROR", "SKIPPED",
    "VoiceProfile", "VoicePersona", "VoiceLocalEngines", "VoiceCloudMapping",
    "LUMENA_DEFAULT", "load_profile", "save_profile",
    "CancelToken", "TTSAudioChunk", "AudioResult", "VADEvent", "STTResult",
    "TTSProvider", "STTProvider", "VADProvider", "RealtimeVoiceProvider",
    "FakeTTSProvider", "FakeVADProvider", "FakeSTTProvider",
    "LocalTTSAdapter", "LocalAudioPlayer",
    "RealSTTAdapter", "RealVADProvider", "measure_noise_floor", "calibrate_thresholds",
    "VoiceRuntime", "v2_tts_enabled",
    "pump_vad", "pump_stt", "v2_stt_enabled",
    "EndpointTimerService", "MicConversationSource",
    "VoiceV2Live", "run_voice_v2_live", "v2_live_enabled",
]
