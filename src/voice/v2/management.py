"""Operational self-management for Voice V2.

This module turns existing Voice runtime facts into a bounded snapshot,
diagnostics and deterministic recommendations.  It never records audio,
transcripts, prompts, secrets or private filesystem paths.  The LLM may explain
the returned facts, but it is not the source of the decision.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import secrets
import threading
import time
from typing import Any, Mapping, Optional

from .performance_profiles import (
    VOICE_PERFORMANCE_PROFILES,
    active_voice_performance_profile,
    get_voice_performance_profile,
    profile_compatibility,
    recommended_voice_performance_profile,
)


VOICE_MANAGEMENT_SCHEMA = 1
VOICE_GOALS = frozenset(
    {
        "balanced",
        "fast",
        "quality",
        "local_only",
        "low_resources",
        "accurate_stt",
        "hands_free",
        "push_to_talk",
    }
)
VOICE_MANAGED_KEYS = frozenset(key for profile in VOICE_PERFORMANCE_PROFILES for key in profile.settings) | frozenset(
    {
        "LUMENA_VOICE_ACOUSTIC_PROFILE",
        "LUMENA_VOICE_ACTIVATION_MODE",
    }
)
VOICE_PROTECTED_KEYS = frozenset(
    {
        "LUMENA_VOICE_MANAGEMENT_MODE",
        "LUMENA_VOICE_CLOUD_ALLOWED",
        "LUMENA_VOICE_IMPROVEMENT_OPT_IN",
        "LUMENA_VOICE_SESSION_TRUSTED",
        "LUMENA_VOICE_SESSION_ROLE",
        "LUMENA_VOICE_SESSION_USER_ID",
        "LUMENA_VOICE_PROFILE_PATH",
        "LUMENA_VOICE_PACK_TRUSTED_KEYS_JSON",
        "LUMENA_XTTS_ALLOW_RESTRICTED",
    }
)


def _bounded(value: Any, limit: int = 96) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ")[:limit]


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return default


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _timing(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    return {
        "count": max(0, int(_number(raw.get("count")))),
        "p50": None if raw.get("p50") is None else round(_number(raw.get("p50")), 3),
        "p95": None if raw.get("p95") is None else round(_number(raw.get("p95")), 3),
    }


def _hardware(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    gpu = _mapping(raw.get("gpu"))
    cuda = _mapping(raw.get("cuda_runtime"))
    return {
        "profile": _bounded(raw.get("profile"), 32) or "unknown",
        "cpu_count": max(0, int(_number(raw.get("cpu_count")))),
        "ram_gb": round(_number(raw.get("ram_gb")), 1),
        "gpu": {
            "available": bool(gpu.get("available")),
            "name": _bounded(gpu.get("name"), 96) or None,
            "vram_gb": round(_number(gpu.get("vram_gb")), 1),
            "vram_free_gb": round(_number(gpu.get("vram_free_gb")), 1),
            "driver_version": _bounded(gpu.get("driver_version"), 32) or None,
        },
        "cuda_runtime": {
            "cublas": bool(cuda.get("cublas")),
            "cudnn": bool(cuda.get("cudnn")),
        },
        "cuda_ready": bool(raw.get("cuda_ready")),
        "npu_runtime": _bounded(raw.get("npu_runtime"), 32) or None,
    }


def _qwen(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    runtime = _mapping(raw.get("runtime"))
    performance = _mapping(runtime.get("performance"))
    worker = _mapping(raw.get("tts_worker"))
    return {
        "installed": bool(raw.get("installed", runtime.get("installed"))),
        "ready": bool(raw.get("ready", runtime.get("ready"))),
        "qualified": bool(raw.get("qualified", performance.get("qualified"))),
        "performance": {
            "qualified": bool(performance.get("qualified")),
            "first_audio_ms": (
                None if performance.get("first_audio_ms") is None else int(_number(performance.get("first_audio_ms")))
            ),
            "rtf": None if performance.get("rtf") is None else round(_number(performance.get("rtf")), 3),
            "measured_at": _bounded(performance.get("measured_at"), 32) or None,
        },
        "streaming_mode": _bounded(raw.get("tts_streaming_mode"), 32) or "unknown",
        "native_pcm_streaming": bool(raw.get("tts_native_pcm_streaming")),
        "worker": {
            "alive": bool(worker.get("alive")),
            "restarts": max(0, int(_number(worker.get("restarts")))),
            "last_error_code": _bounded(worker.get("last_error_code"), 64) or None,
        },
        "error_code": _bounded(runtime.get("error"), 64) or None,
    }


def build_voice_operational_snapshot(
    status: Mapping[str, Any],
    *,
    env_values: Optional[Mapping[str, str]] = None,
    hardware: Optional[Mapping[str, Any]] = None,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """Build a path-free and text-free Voice operational snapshot."""
    status = _mapping(status)
    values = dict(env_values or {})
    resolved_hardware = _hardware(
        hardware or status.get("hardware_profile") or _mapping(status.get("capabilities")).get("hardware")
    )
    saved_runtime = _mapping(status.get("saved_runtime_options"))
    effective_runtime = _mapping(status.get("effective_runtime_options"))
    saved_voice = _mapping(status.get("saved_voice_settings"))
    effective_voice = _mapping(status.get("effective_voice_settings"))
    runtime = effective_runtime or _mapping(status.get("runtime_options")) or saved_runtime
    voice = effective_voice or saved_voice
    qwen = _qwen(status.get("qwen"))
    fallback = _mapping(status.get("fallback"))
    edge = _mapping(status.get("edge_tts"))
    audio_frontend = _mapping(status.get("audio_frontend"))
    timings = _mapping(status.get("timings"))
    active_profile = active_voice_performance_profile(values) if values else None
    recommended = recommended_voice_performance_profile(resolved_hardware)

    snapshot = {
        "schema_version": VOICE_MANAGEMENT_SCHEMA,
        "observed_at": datetime.fromtimestamp(time.time() if now is None else float(now), tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "lifecycle": {
            "running": bool(status.get("running")),
            "state": _bounded(status.get("state"), 32) or "unknown",
            "backend": _bounded(status.get("backend"), 16) or "unknown",
            "requested_backend": _bounded(status.get("requested_backend"), 16) or "unknown",
            "mode": _bounded(status.get("mode"), 16) or "unknown",
            "restarts": max(0, int(_number(status.get("restarts")))),
            "last_error_code": _bounded(status.get("last_error"), 64) or None,
        },
        "configuration": {
            "active_profile": active_profile or "custom",
            "hardware_recommended_profile": recommended,
            "settings_pending": bool(status.get("settings_pending")),
            "saved": {
                "runtime": {
                    "device": _bounded(saved_runtime.get("device"), 16) or None,
                    "compute": _bounded(saved_runtime.get("compute"), 24) or None,
                    "language": _bounded(saved_runtime.get("language"), 16) or None,
                    "model": _bounded(saved_runtime.get("model"), 48) or None,
                    "input_device_index": saved_runtime.get("input_device_index"),
                },
                "voice": {
                    "tts_mode": _bounded(saved_voice.get("tts_mode"), 16) or None,
                    "tts_engine": _bounded(saved_voice.get("tts_engine"), 16) or None,
                    "vad_engine": _bounded(saved_voice.get("vad_engine"), 24) or None,
                    "acoustic_profile": _bounded(saved_voice.get("acoustic_profile"), 24) or None,
                    "cloud_tts_allowed": bool(saved_voice.get("cloud_tts_allowed")),
                },
            },
            "effective": {
                "runtime": {
                    "device": _bounded(runtime.get("device"), 16) or None,
                    "compute": _bounded(runtime.get("compute"), 24) or None,
                    "language": _bounded(runtime.get("language"), 16) or None,
                    "model": _bounded(runtime.get("model"), 48) or None,
                    "input_device_index": runtime.get("input_device_index"),
                },
                "voice": {
                    "tts_mode": _bounded(voice.get("tts_mode"), 16) or None,
                    "tts_engine": _bounded(voice.get("tts_engine"), 16) or None,
                    "vad_engine": _bounded(voice.get("vad_engine"), 24) or None,
                    "acoustic_profile": _bounded(voice.get("acoustic_profile"), 24) or None,
                    "cloud_tts_allowed": bool(voice.get("cloud_tts_allowed")),
                },
            },
        },
        "engine": {
            "requested": _bounded(status.get("tts_engine_configured") or voice.get("tts_engine"), 24) or "unknown",
            "effective": _bounded(status.get("tts_provider_effective") or status.get("provider"), 32) or "unknown",
            "fallback_count": max(0, int(_number(status.get("tts_fallback_count")))),
            "fallback_reason": _bounded(status.get("tts_fallback_reason"), 64) or None,
            "degraded": bool(status.get("degraded")),
            "worker_alive": bool(status.get("tts_worker_alive")),
            "worker_error_code": _bounded(status.get("tts_worker_error"), 64) or None,
        },
        "hardware": resolved_hardware,
        "components": {
            "qwen3_tts": qwen,
            "piper": {
                "available": bool(
                    _mapping(fallback.get("capabilities")).get("piper")
                    or fallback.get("piper_available")
                    or _mapping(status.get("capabilities")).get("piper_available")
                ),
            },
            "edge_tts": {
                "available": bool(
                    edge.get("available", _mapping(status.get("capabilities")).get("edge_tts_available"))
                ),
                "circuit_state": _bounded(edge.get("circuit_state"), 24) or "unknown",
                "last_error_code": _bounded(edge.get("last_error_code"), 64) or None,
            },
            "whisper": {
                "available": bool(_mapping(status.get("capabilities")).get("faster_whisper_available")),
            },
            "vad": {
                "engine": _bounded(_mapping(status.get("vad")).get("engine") or voice.get("vad_engine"), 24)
                or "unknown",
                "fallback_reason": _bounded(_mapping(status.get("vad")).get("fallback_reason"), 64) or None,
            },
            "audio_frontend": {
                "profile": _bounded(audio_frontend.get("profile"), 24) or "unknown",
                "aec_available": bool(audio_frontend.get("aec_available")),
                "aec_active": bool(audio_frontend.get("aec_active")),
                "noise_suppression_active": bool(audio_frontend.get("noise_suppression_active")),
                "gain_control_active": bool(audio_frontend.get("gain_control_active")),
                "degraded_reason": _bounded(audio_frontend.get("degraded_reason"), 64) or None,
            },
        },
        "timings": {
            "event_count": max(0, int(_number(timings.get("event_count")))),
            "interruption_count": max(0, int(_number(timings.get("interruption_count")))),
            **{
                name: _timing(timings.get(name))
                for name in (
                    "endpointing_ms",
                    "stt_ms",
                    "llm_ms",
                    "tts_ms",
                    "playback_gap_ms",
                    "synthesis_wait_gap_ms",
                    "artificial_gap_ms",
                )
            },
        },
        "privacy": {
            "cloud_tts_allowed": bool(
                _mapping(status.get("privacy")).get("cloud_tts_allowed", voice.get("cloud_tts_allowed"))
            ),
            "raw_audio_logged": False,
            "transcript_logged": False,
            "improvement_data_opt_in": bool(_mapping(status.get("privacy")).get("improvement_data_opt_in")),
        },
    }
    return snapshot


def diagnose_voice_snapshot(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Return stable operational issue codes and evidence, without prose claims."""
    lifecycle = _mapping(snapshot.get("lifecycle"))
    config = _mapping(snapshot.get("configuration"))
    engine = _mapping(snapshot.get("engine"))
    components = _mapping(snapshot.get("components"))
    qwen = _mapping(components.get("qwen3_tts"))
    frontend = _mapping(components.get("audio_frontend"))
    timings = _mapping(snapshot.get("timings"))
    issues: list[dict[str, Any]] = []

    def add(code: str, severity: str, evidence: Mapping[str, Any], action: str) -> None:
        issues.append(
            {
                "code": code,
                "severity": severity,
                "evidence": dict(evidence),
                "suggested_action": action,
            }
        )

    if lifecycle.get("state") == "error" or lifecycle.get("last_error_code"):
        add(
            "voice_runtime_error",
            "blocking",
            {
                "state": lifecycle.get("state"),
                "error_code": lifecycle.get("last_error_code"),
            },
            "restart_or_repair_voice",
        )
    if config.get("settings_pending"):
        add("voice_settings_pending", "notice", {}, "restart_voice_when_idle")
    if engine.get("fallback_reason") or int(engine.get("fallback_count", 0) or 0) > 0:
        add(
            "tts_fallback_active",
            "degraded",
            {
                "effective": engine.get("effective"),
                "reason": engine.get("fallback_reason"),
                "count": engine.get("fallback_count"),
            },
            "inspect_requested_tts",
        )
    if qwen.get("installed") and not qwen.get("ready"):
        add(
            "qwen_installed_not_ready",
            "degraded",
            {
                "error_code": qwen.get("error_code"),
            },
            "repair_qwen_runtime",
        )
    if qwen.get("ready") and not qwen.get("qualified"):
        add(
            "qwen_not_conversation_qualified",
            "notice",
            {
                "first_audio_ms": _mapping(qwen.get("performance")).get("first_audio_ms"),
                "rtf": _mapping(qwen.get("performance")).get("rtf"),
            },
            "use_piper_for_conversation",
        )
    if (
        not frontend.get("aec_available")
        and _mapping(config.get("effective")).get("voice", {}).get("acoustic_profile") == "hands_free"
    ):
        add(
            "hands_free_without_aec",
            "notice",
            {
                "degraded_reason": frontend.get("degraded_reason"),
            },
            "prefer_headset_or_push_to_talk",
        )
    gap = _mapping(timings.get("synthesis_wait_gap_ms"))
    if int(gap.get("count", 0) or 0) >= 2 and _number(gap.get("p95")) > 1000:
        add(
            "tts_synthesis_gap_high",
            "degraded",
            {
                "p95_ms": gap.get("p95"),
                "samples": gap.get("count"),
            },
            "prefer_low_latency_tts",
        )
    first_audio = _mapping(qwen.get("performance")).get("first_audio_ms")
    if qwen.get("qualified") is False and first_audio is None and qwen.get("installed"):
        add("qwen_performance_unknown", "unknown", {}, "run_bounded_voice_benchmark")

    rank = {"blocking": 0, "degraded": 1, "notice": 2, "unknown": 3}
    issues.sort(key=lambda item: (rank.get(str(item["severity"]), 9), str(item["code"])))
    return {
        "schema_version": VOICE_MANAGEMENT_SCHEMA,
        "healthy": not any(item["severity"] in {"blocking", "degraded"} for item in issues),
        "issues": issues,
    }


def _profile_for_goal(goal: str, snapshot: Mapping[str, Any]) -> str:
    hardware = _mapping(snapshot.get("hardware"))
    cpu = int(_number(hardware.get("cpu_count")))
    ram = _number(hardware.get("ram_gb"))
    cuda = bool(hardware.get("cuda_ready"))
    gpu = _mapping(hardware.get("gpu"))
    qwen = _mapping(_mapping(snapshot.get("components")).get("qwen3_tts"))
    if goal == "low_resources":
        return "essential" if ram < 8 else "light"
    if goal == "fast":
        return "balanced" if cpu >= 8 and ram >= 16 else ("light" if ram >= 8 else "essential")
    if goal in {"quality", "accurate_stt"}:
        if cuda and _number(gpu.get("vram_free_gb")) >= 8 and qwen.get("qualified"):
            return "studio_gpu"
        return "precision" if ram >= 16 and cpu >= 8 else ("light" if ram >= 8 else "essential")
    return recommended_voice_performance_profile(hardware)


def recommend_voice_configuration(
    snapshot: Mapping[str, Any],
    goal: str = "balanced",
) -> dict[str, Any]:
    goal = str(goal or "balanced").strip().lower()
    if goal not in VOICE_GOALS:
        raise ValueError("unsupported_voice_goal")
    profile_id = _profile_for_goal(goal, snapshot)
    profile = get_voice_performance_profile(profile_id)
    if profile is None:
        raise RuntimeError("voice_profile_unavailable")
    updates = dict(profile.settings)
    qwen = _mapping(_mapping(snapshot.get("components")).get("qwen3_tts"))
    reasons: list[str] = [f"goal:{goal}", f"profile:{profile_id}"]
    limitations: list[str] = []
    confidence = "high"

    if goal in {"fast", "low_resources"}:
        updates["LUMENA_VOICE_TTS_ENGINE"] = "piper"
        reasons.append("piper_low_latency_local")
    elif goal in {"quality", "accurate_stt"}:
        if qwen.get("qualified"):
            updates["LUMENA_VOICE_TTS_ENGINE"] = "auto"
            reasons.append("qwen_conversation_qualified")
        else:
            updates["LUMENA_VOICE_TTS_ENGINE"] = "piper"
            limitations.append("qwen_not_conversation_qualified")
            confidence = "medium"
    if goal == "local_only":
        updates["LUMENA_VOICE_CLOUD_ALLOWED"] = "0"
        reasons.append("local_only_constraint")
    if goal == "hands_free":
        updates["LUMENA_VOICE_ACOUSTIC_PROFILE"] = "hands_free"
        updates["LUMENA_VOICE_ACTIVATION_MODE"] = "wake_phrase"
        frontend = _mapping(_mapping(snapshot.get("components")).get("audio_frontend"))
        if not frontend.get("aec_available"):
            limitations.append("server_aec_unavailable")
            confidence = "medium"
    if goal == "push_to_talk":
        updates["LUMENA_VOICE_ACOUSTIC_PROFILE"] = "push_to_talk"
        updates["LUMENA_VOICE_ACTIVATION_MODE"] = "push_to_talk"
        reasons.append("explicit_push_to_talk")

    compatible, incompatibilities = profile_compatibility(profile, _mapping(snapshot.get("hardware")))
    if not compatible:
        limitations.extend(incompatibilities)
        confidence = "low"
    timings = _mapping(snapshot.get("timings"))
    if int(timings.get("event_count", 0) or 0) < 3:
        limitations.append("insufficient_runtime_samples")
        confidence = "medium" if confidence == "high" else confidence
    return {
        "schema_version": VOICE_MANAGEMENT_SCHEMA,
        "goal": goal,
        "profile_id": profile_id,
        "compatible": compatible,
        "confidence": confidence,
        "updates": updates,
        "reasons": reasons,
        "limitations": list(dict.fromkeys(limitations)),
        "requires_voice_restart": True,
    }


@dataclass
class _Ticket:
    payload: dict[str, Any]
    expires_at: float


class VoiceRecommendationBroker:
    """Short-lived one-shot recommendation tickets; never persisted as authority."""

    def __init__(self, *, ttl_s: float = 300.0, clock=time.monotonic) -> None:
        self._ttl_s = max(10.0, min(1800.0, float(ttl_s)))
        self._clock = clock
        self._lock = threading.Lock()
        self._tickets: dict[str, _Ticket] = {}

    def issue(self, recommendation: Mapping[str, Any]) -> dict[str, Any]:
        token = secrets.token_urlsafe(24)
        expires_at = self._clock() + self._ttl_s
        with self._lock:
            self._tickets[token] = _Ticket(deepcopy(dict(recommendation)), expires_at)
            self._purge_locked()
        return {"recommendation_id": token, "expires_in_s": int(self._ttl_s)}

    def consume(self, token: str) -> Optional[dict[str, Any]]:
        with self._lock:
            item = self._tickets.pop(str(token or ""), None)
            self._purge_locked()
        if item is None or item.expires_at < self._clock():
            return None
        return deepcopy(item.payload)

    def _purge_locked(self) -> None:
        now = self._clock()
        expired = [key for key, value in self._tickets.items() if value.expires_at < now]
        for key in expired:
            self._tickets.pop(key, None)
        while len(self._tickets) > 128:
            self._tickets.pop(next(iter(self._tickets)))


_BROKER = VoiceRecommendationBroker()


def get_voice_recommendation_broker() -> VoiceRecommendationBroker:
    return _BROKER
