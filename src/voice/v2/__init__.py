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
from .speech_normalizer import normalize_for_speech, prepare_for_tts, SpeechText
from .speech_planner import SpeechPlan, SentenceCommitter, plan_speech, wants_full_voice_detail
from .dialogue_policy import VoiceDialoguePolicy
from .work_registry import (
    ActiveWorkRegistry, WorkNotificationTracker, WorkSnapshot, classify_work_turn,
)
from .prewarm import (
    Prewarmer, ComponentStatus, ComponentUnavailable,
    overall_state, ready_message, detect_voice_capabilities, detect_voice_hardware,
    PREPARING, READY, DEGRADED, ERROR, SKIPPED,
)
from .privacy import VoicePrivacyError, purge_voice_data
from .certification import (
    SCENARIOS, VoiceCertificationError, add_machine, finalize_campaign,
    load_report, new_campaign, record_scenario, save_report,
    validate_campaign, verify_final_attestation,
)
from .voice_profile import (
    VoiceProfile, VoicePersona, VoiceLocalEngines, VoiceCloudMapping,
    LUMENA_DEFAULT, load_profile, save_profile, get_voice_profile_status,
    apply_pronunciations, classify_dialogue_act,
)
from .providers import (
    CancelToken, TTSAudioChunk, AudioResult, VADEvent, STTResult,
    TTSProvider, STTProvider, VADProvider, RealtimeVoiceProvider,
    FakeTTSProvider, FakeVADProvider, FakeSTTProvider,
    LocalTTSAdapter, LocalAudioPlayer,
    RealSTTAdapter, RealVADProvider, measure_noise_floor, calibrate_thresholds,
    SileroSpeechProbability, SileroVADStatus,
    LocalWakeWordProvider, WakeWordDetection,
)
from .voice_runtime import VoiceRuntime, v2_tts_enabled
from .speech_coordinator import SpeechCoordinator, SpeechRequest, SPEECH_PRIORITIES
from .activity_narrator import PublicActivityNarrator
from .activation import VoiceActivationGate, ActivationDecision, activation_gate_from_env
from .language_policy import (
    LanguageDecision, LanguagePolicy, LanguageState, normalize_language,
)
from .language_guard import LanguageCheck, check_response_language, detect_text_language
from .audio_frontend import (
    AudioFrontend, AudioFrontendStatus, WebRTCLocalAudioProcessor,
    create_local_audio_processor, server_aec_component_available,
)
from .voice_pack import VoicePackManager, VoicePackError
from .input_sources import (
    pump_vad, pump_stt, v2_stt_enabled, EndpointTimerService, MicConversationSource,
)
from .live import (
    VoiceV2Live, run_voice_v2_live, v2_live_enabled,
    resolve_voice_agent_max_iterations, _extract_text,
)
from .supervisor import VoiceV2Manager, normalize_voice_mode
from .session import VoiceSessionIdentity, VoiceSessionRouter, parse_mode_switch
from src.runtime.voice_security import (
    VoiceConfirmationBroker, get_voice_confirmation_broker,
)

__all__ = [
    "VoiceEvent", "VoiceCommand",
    "VoiceState", "EndpointDecision",
    "AudioChunk", "AudioOutputQueue", "ConversationAudioLedger", "PlayedSpeech",
    "decide_endpoint",
    "TurnManager",
    "parse_events", "load_events_jsonl", "replay_sync",
    "FakeRuntime", "Driver",
    "normalize_for_speech", "prepare_for_tts", "SpeechText",
    "SpeechPlan", "SentenceCommitter", "plan_speech", "wants_full_voice_detail",
    "VoiceDialoguePolicy",
    "ActiveWorkRegistry", "WorkNotificationTracker", "WorkSnapshot", "classify_work_turn",
    "Prewarmer", "ComponentStatus", "ComponentUnavailable",
    "overall_state", "ready_message", "detect_voice_capabilities", "detect_voice_hardware",
    "PREPARING", "READY", "DEGRADED", "ERROR", "SKIPPED",
    "VoiceProfile", "VoicePersona", "VoiceLocalEngines", "VoiceCloudMapping",
    "LUMENA_DEFAULT", "load_profile", "save_profile", "get_voice_profile_status",
    "apply_pronunciations", "classify_dialogue_act",
    "CancelToken", "TTSAudioChunk", "AudioResult", "VADEvent", "STTResult",
    "TTSProvider", "STTProvider", "VADProvider", "RealtimeVoiceProvider",
    "FakeTTSProvider", "FakeVADProvider", "FakeSTTProvider",
    "LocalTTSAdapter", "LocalAudioPlayer",
    "RealSTTAdapter", "RealVADProvider", "measure_noise_floor", "calibrate_thresholds",
    "SileroSpeechProbability", "SileroVADStatus",
    "LocalWakeWordProvider", "WakeWordDetection",
    "VoiceRuntime", "v2_tts_enabled",
    "SpeechCoordinator", "SpeechRequest", "SPEECH_PRIORITIES",
    "PublicActivityNarrator",
    "VoiceActivationGate", "ActivationDecision", "activation_gate_from_env",
    "LanguageDecision", "LanguagePolicy", "LanguageState", "normalize_language",
    "LanguageCheck", "check_response_language", "detect_text_language",
    "AudioFrontend", "AudioFrontendStatus", "WebRTCLocalAudioProcessor",
    "create_local_audio_processor", "server_aec_component_available",
    "VoicePackManager", "VoicePackError", "VoicePrivacyError", "purge_voice_data",
    "SCENARIOS", "VoiceCertificationError", "add_machine", "finalize_campaign",
    "load_report", "new_campaign", "record_scenario", "save_report",
    "validate_campaign", "verify_final_attestation",
    "pump_vad", "pump_stt", "v2_stt_enabled",
    "EndpointTimerService", "MicConversationSource",
    "VoiceV2Live", "run_voice_v2_live", "v2_live_enabled",
    "resolve_voice_agent_max_iterations",
    "VoiceV2Manager", "normalize_voice_mode",
    "VoiceSessionIdentity", "VoiceSessionRouter", "parse_mode_switch",
    "VoiceConfirmationBroker", "get_voice_confirmation_broker",
]
