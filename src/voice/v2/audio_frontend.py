"""Pluggable acoustic front-end with honest capability reporting.

No DSP is emulated here. Without an audited native processor, capture is passed
through and the status explicitly reports that server-side AEC is inactive.
"""
from __future__ import annotations

import importlib.util
import os
import time
import wave
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Deque, Optional


@dataclass
class AudioFrontendStatus:
    profile: str = "hands_free"
    processor: str = "passthrough"
    aec_available: bool = False
    aec_active: bool = False
    noise_suppression_active: bool = False
    gain_control_active: bool = False
    playback_reference_frames: int = 0
    capture_frames: int = 0
    degraded_reason: str = "server_aec_unavailable"
    last_error: str = ""


def server_aec_component_available() -> bool:
    for module in (
        "pywebrtc_audio", "webrtc_audio_processing",
        "webrtc_audio_processing_python",
    ):
        try:
            if importlib.util.find_spec(module) is not None:
                return True
        except (ImportError, ModuleNotFoundError, ValueError):
            pass
    return False


class WebRTCLocalAudioProcessor:
    """Local WebRTC AEC/NS/AGC with bounded, in-memory far-end audio."""

    name = "pywebrtc-audio"

    def __init__(
        self, *, sample_rate: int = 16000, stream_delay_ms: int = 40,
        clock: Any = time.perf_counter,
    ) -> None:
        from pywebrtc_audio import AudioProcessor  # noqa: PLC0415

        self.sample_rate = int(sample_rate)
        self._clock = clock
        self._processor = AudioProcessor(
            sample_rate=self.sample_rate,
            num_channels=1,
            echo_cancellation=True,
            noise_suppression=True,
            high_pass_filter=True,
            auto_gain_control=True,
            ns_level=2,
            stream_delay_ms=int(stream_delay_ms),
        )
        self._references: Deque[dict[str, Any]] = deque(maxlen=12)
        self._capture_started_at: Optional[float] = None

    def begin_capture(self) -> None:
        self._capture_started_at = float(self._clock())

    @staticmethod
    def _read_wav_mono(path: Path, target_rate: int) -> bytes:
        import audioop  # noqa: PLC0415 - Python 3.12 is pinned by the installer

        with wave.open(str(path), "rb") as stream:
            channels = stream.getnchannels()
            width = stream.getsampwidth()
            rate = stream.getframerate()
            pcm = stream.readframes(stream.getnframes())
        if width != 2:
            pcm = audioop.lin2lin(pcm, width, 2)
            width = 2
        if channels > 1:
            pcm = audioop.tomono(pcm, width, 0.5, 0.5)
        if rate != target_rate:
            pcm, _ = audioop.ratecv(pcm, width, 1, rate, target_rate, None)
        return pcm

    def process_playback_reference(
        self, audio: Any, *, sample_rate: Optional[int] = None
    ) -> None:
        path = Path(audio) if isinstance(audio, (str, Path)) else None
        if path is None or path.suffix.lower() != ".wav" or not path.is_file():
            return
        pcm = self._read_wav_mono(path, self.sample_rate)
        if pcm:
            self._references.append({
                "started_at": float(self._clock()), "ended_at": None, "pcm": pcm,
            })

    def end_playback_reference(self) -> None:
        if self._references and self._references[-1]["ended_at"] is None:
            self._references[-1]["ended_at"] = float(self._clock())

    def process_capture(self, pcm: bytes, *, sample_rate: int = 16000) -> bytes:
        import audioop  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415

        if not pcm:
            return pcm
        original_rate = int(sample_rate or self.sample_rate)
        if original_rate != self.sample_rate:
            near_bytes, _ = audioop.ratecv(
                pcm, 2, 1, original_rate, self.sample_rate, None,
            )
        else:
            near_bytes = pcm
        near = np.frombuffer(near_bytes, dtype=np.int16).copy()
        far = np.zeros(len(near), dtype=np.int16)
        capture_start = self._capture_started_at
        if capture_start is None:
            capture_start = float(self._clock()) - len(near) / self.sample_rate
        capture_end = capture_start + len(near) / self.sample_rate
        for reference in list(self._references):
            ref_start = float(reference["started_at"])
            ref_pcm = np.frombuffer(reference["pcm"], dtype=np.int16)
            natural_end = ref_start + len(ref_pcm) / self.sample_rate
            ref_end = min(
                natural_end,
                float(reference["ended_at"])
                if reference["ended_at"] is not None else capture_end,
            )
            overlap_start = max(capture_start, ref_start)
            overlap_end = min(capture_end, ref_end)
            if overlap_end <= overlap_start:
                continue
            near_start = max(0, round((overlap_start - capture_start) * self.sample_rate))
            ref_offset = max(0, round((overlap_start - ref_start) * self.sample_rate))
            count = min(
                len(near) - near_start,
                len(ref_pcm) - ref_offset,
                max(0, round((overlap_end - overlap_start) * self.sample_rate)),
            )
            if count > 0:
                far[near_start:near_start + count] = ref_pcm[ref_offset:ref_offset + count]
        cleaned = self._processor.process(near, far)
        result = bytes(cleaned.astype(np.int16, copy=False).tobytes())
        self._capture_started_at = None
        self._references.clear()
        if original_rate != self.sample_rate:
            result, _ = audioop.ratecv(
                result, 2, 1, self.sample_rate, original_rate, None,
            )
        return result


def create_local_audio_processor(*, profile: str) -> Any:
    if str(profile or "").strip().lower() != "hands_free":
        return None
    try:
        return WebRTCLocalAudioProcessor()
    except Exception:
        return None


class AudioFrontend:
    """Own capture processing and the playback reference for a session."""

    def __init__(self, processor: Any = None, *, profile: str = "hands_free") -> None:
        normalized = str(profile or "hands_free").strip().lower()
        if normalized not in {"hands_free", "headset", "push_to_talk"}:
            normalized = "hands_free"
        self.processor = processor
        self.status = AudioFrontendStatus(
            profile=normalized,
            processor=getattr(processor, "name", type(processor).__name__) if processor else "passthrough",
            aec_available=bool(processor) or server_aec_component_available(),
            aec_active=bool(processor and normalized == "hands_free"),
            noise_suppression_active=bool(processor and normalized == "hands_free"),
            gain_control_active=bool(processor and normalized == "hands_free"),
            degraded_reason="" if processor or normalized != "hands_free" else "server_aec_unavailable",
        )

    @classmethod
    def from_env(cls, processor: Any = None) -> "AudioFrontend":
        profile = os.getenv("LUMENA_VOICE_ACOUSTIC_PROFILE", "hands_free")
        component_available = server_aec_component_available()
        if processor is None:
            processor = create_local_audio_processor(profile=profile)
        frontend = cls(processor=processor, profile=profile)
        if (
            str(profile).strip().lower() == "hands_free"
            and processor is None and component_available
        ):
            frontend.status.degraded_reason = "processor_init_failed"
        return frontend

    def begin_capture(self) -> None:
        if not self.status.aec_active or self.processor is None:
            return
        method = getattr(self.processor, "begin_capture", None)
        if callable(method):
            try:
                method()
            except Exception as exc:
                self.status.last_error = str(exc)[:240]

    def end_playback_reference(self) -> None:
        if not self.status.aec_active or self.processor is None:
            return
        method = getattr(self.processor, "end_playback_reference", None)
        if callable(method):
            try:
                method()
            except Exception as exc:
                self.status.last_error = str(exc)[:240]

    def process_capture(self, pcm: bytes, *, sample_rate: int = 16000) -> bytes:
        self.status.capture_frames += 1
        if not self.status.aec_active or self.processor is None:
            return pcm
        try:
            method = getattr(self.processor, "process_capture")
            return bytes(method(pcm, sample_rate=sample_rate))
        except Exception as exc:
            self.status.aec_active = False
            self.status.noise_suppression_active = False
            self.status.gain_control_active = False
            self.status.degraded_reason = "processor_error"
            self.status.last_error = str(exc)[:240]
            return pcm

    def register_playback_reference(
        self, audio: Any, *, sample_rate: Optional[int] = None
    ) -> None:
        self.status.playback_reference_frames += 1
        if not self.status.aec_active or self.processor is None:
            return
        try:
            method = getattr(self.processor, "process_playback_reference")
            method(audio, sample_rate=sample_rate)
        except Exception as exc:
            self.status.aec_active = False
            self.status.degraded_reason = "playback_reference_error"
            self.status.last_error = str(exc)[:240]

    def report(self) -> dict:
        return asdict(self.status)
