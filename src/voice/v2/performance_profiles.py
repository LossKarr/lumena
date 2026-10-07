"""Profils de performance Voice V2, ordonnés du plus léger au plus exigeant.

Ces profils ne modifient que le moteur vocal. Ils ne touchent jamais à l'identité,
au rôle, au micro choisi, à la langue ou au mode Chat/Agent de l'utilisateur.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class VoicePerformanceProfile:
    id: str
    order: int
    label: str
    tagline: str
    description: str
    quality: int
    responsiveness: int
    requirement: str
    privacy: str
    settings: Mapping[str, str]
    requires_cuda: bool = False
    min_ram_gb: float = 0.0


VOICE_PERFORMANCE_PROFILES: tuple[VoicePerformanceProfile, ...] = (
    VoicePerformanceProfile(
        id="essential",
        order=1,
        label="Essentiel",
        tagline="PC modeste",
        description="Reconnaissance minimale, entièrement locale et très légère.",
        quality=1,
        responsiveness=5,
        requirement="CPU 4 cœurs · 4 Go RAM",
        privacy="Local strict",
        settings={
            "LUMENA_STT_MODEL": "tiny",
            "LUMENA_STT_DEVICE": "cpu",
            "LUMENA_STT_COMPUTE": "int8",
            "LUMENA_STT_PARTIAL_EVERY_MS": "0",
            "LUMENA_TTS_MODE": "offline",
            "LUMENA_VOICE_CLOUD_ALLOWED": "0",
            "LUMENA_VOICE_VAD_ENGINE": "energy",
        },
        min_ram_gb=4.0,
    ),
    VoicePerformanceProfile(
        id="light",
        order=2,
        label="Léger",
        tagline="Rapide et local",
        description="Meilleure compréhension que le minimum avec une charge réduite.",
        quality=2,
        responsiveness=5,
        requirement="CPU 4 cœurs · 8 Go RAM",
        privacy="Local strict",
        settings={
            "LUMENA_STT_MODEL": "base",
            "LUMENA_STT_DEVICE": "cpu",
            "LUMENA_STT_COMPUTE": "int8",
            "LUMENA_STT_PARTIAL_EVERY_MS": "0",
            "LUMENA_TTS_MODE": "offline",
            "LUMENA_VOICE_CLOUD_ALLOWED": "0",
            "LUMENA_VOICE_VAD_ENGINE": "energy",
        },
        min_ram_gb=8.0,
    ),
    VoicePerformanceProfile(
        id="balanced",
        order=3,
        label="Équilibré",
        tagline="Recommandé",
        description="Conversation fluide et français précis sur un ordinateur récent.",
        quality=3,
        responsiveness=4,
        requirement="CPU 8 cœurs · 16 Go RAM",
        privacy="Voix entièrement locale",
        settings={
            "LUMENA_STT_MODEL": "small",
            "LUMENA_STT_DEVICE": "cpu",
            "LUMENA_STT_COMPUTE": "int8",
            "LUMENA_STT_PARTIAL_EVERY_MS": "0",
            "LUMENA_TTS_MODE": "offline",
            "LUMENA_VOICE_CLOUD_ALLOWED": "0",
            "LUMENA_VOICE_VAD_ENGINE": "auto",
        },
        min_ram_gb=16.0,
    ),
    VoicePerformanceProfile(
        id="precision",
        order=4,
        label="Précision",
        tagline="Qualité CPU",
        description="Transcription plus précise, avec une réponse sensiblement plus lente.",
        quality=4,
        responsiveness=2,
        requirement="CPU 8 cœurs · 16 Go RAM",
        privacy="Voix entièrement locale",
        settings={
            "LUMENA_STT_MODEL": "medium",
            "LUMENA_STT_DEVICE": "cpu",
            "LUMENA_STT_COMPUTE": "int8",
            "LUMENA_STT_PARTIAL_EVERY_MS": "0",
            "LUMENA_TTS_MODE": "offline",
            "LUMENA_VOICE_CLOUD_ALLOWED": "0",
            "LUMENA_VOICE_VAD_ENGINE": "auto",
        },
        min_ram_gb=16.0,
    ),
    VoicePerformanceProfile(
        id="studio_gpu",
        order=5,
        label="Studio GPU",
        tagline="Qualité maximale",
        description="Whisper large accéléré par CUDA pour la meilleure reconnaissance.",
        quality=5,
        responsiveness=4,
        requirement="CUDA prêt · GPU 8 Go VRAM · 16 Go RAM",
        privacy="Voix entièrement locale",
        settings={
            "LUMENA_STT_MODEL": "large-v3-turbo",
            "LUMENA_STT_DEVICE": "cuda",
            "LUMENA_STT_COMPUTE": "float16",
            "LUMENA_STT_PARTIAL_EVERY_MS": "0",
            "LUMENA_TTS_MODE": "offline",
            "LUMENA_VOICE_CLOUD_ALLOWED": "0",
            "LUMENA_VOICE_VAD_ENGINE": "auto",
        },
        requires_cuda=True,
        min_ram_gb=16.0,
    ),
)

_BY_ID = {profile.id: profile for profile in VOICE_PERFORMANCE_PROFILES}


def get_voice_performance_profile(profile_id: str) -> Optional[VoicePerformanceProfile]:
    return _BY_ID.get(str(profile_id or "").strip().lower())


def profile_compatibility(
    profile: VoicePerformanceProfile, hardware: Mapping[str, Any]
) -> tuple[bool, list[str]]:
    """Évalue les prérequis réels, sans croire une simple présence de GPU."""
    reasons: list[str] = []
    ram_gb = float(hardware.get("ram_gb", 0.0) or 0.0)
    if profile.min_ram_gb and ram_gb and ram_gb < profile.min_ram_gb:
        reasons.append(f"{profile.min_ram_gb:g} Go de RAM requis")
    if profile.requires_cuda and not bool(hardware.get("cuda_ready", False)):
        reasons.append("CUDA vocal indisponible (cuBLAS/cuDNN requis)")
    return not reasons, reasons


def active_voice_performance_profile(values: Mapping[str, str]) -> Optional[str]:
    """Retourne un profil uniquement si toutes ses valeurs correspondent exactement."""
    for profile in VOICE_PERFORMANCE_PROFILES:
        if all(str(values.get(key, "")) == expected for key, expected in profile.settings.items()):
            return profile.id
    return None


def recommended_voice_performance_profile(hardware: Mapping[str, Any]) -> str:
    """Choix conservateur : la qualité maximale n'est recommandée qu'avec CUDA prêt."""
    if bool(hardware.get("cuda_ready", False)):
        gpu = hardware.get("gpu", {}) if isinstance(hardware.get("gpu"), Mapping) else {}
        if float(gpu.get("vram_gb", 0.0) or 0.0) >= 8.0:
            return "studio_gpu"
    cpu_count = int(hardware.get("cpu_count", 0) or 0)
    ram_gb = float(hardware.get("ram_gb", 0.0) or 0.0)
    if cpu_count >= 8 and ram_gb >= 16.0:
        return "balanced"
    if ram_gb >= 8.0:
        return "light"
    return "essential"


def serialize_voice_performance_profiles(
    values: Mapping[str, str], hardware: Mapping[str, Any]
) -> dict[str, Any]:
    active = active_voice_performance_profile(values)
    recommended = recommended_voice_performance_profile(hardware)
    profiles = []
    for profile in VOICE_PERFORMANCE_PROFILES:
        compatible, reasons = profile_compatibility(profile, hardware)
        profiles.append({
            "id": profile.id,
            "order": profile.order,
            "label": profile.label,
            "tagline": profile.tagline,
            "description": profile.description,
            "quality": profile.quality,
            "responsiveness": profile.responsiveness,
            "requirement": profile.requirement,
            "privacy": profile.privacy,
            "settings": dict(profile.settings),
            "compatible": compatible,
            "incompatibility_reasons": reasons,
            "active": profile.id == active,
            "recommended": profile.id == recommended,
        })
    return {
        "schema_version": 1,
        "active_profile": active or "custom",
        "recommended_profile": recommended,
        "profiles": profiles,
    }
