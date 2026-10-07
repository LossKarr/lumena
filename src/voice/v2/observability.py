"""Thread-safe Voice V2 telemetry and immediate audio control."""
from __future__ import annotations

from collections import deque
from threading import Lock
from typing import Any, Callable, Deque, Dict, List, Optional
import inspect
import math
import time


_DICTATION_LEASE_S = 90.0


_TIMING_EVENTS = frozenset({
    "utterance.ended", "stt.started", "stt.final",
    "llm.started", "llm.first_token", "llm.response_started",
    "tts.segment_started", "tts.segment_ready",
    "playback.started", "playback.finished", "interruption.received",
    "speech.resumed",
})


def _percentile(values: List[float], percentile: float) -> Optional[float]:
    """Nearest-rank percentile, deterministic for small operational windows."""
    if not values:
        return None
    ordered = sorted(max(0.0, float(value)) for value in values)
    rank = max(1, math.ceil((percentile / 100.0) * len(ordered)))
    return round(ordered[min(rank - 1, len(ordered) - 1)], 3)


class VoiceTimingWindow:
    """Bounded, text-free chronology for one Voice V2 process.

    Only identifiers, sequence numbers and monotonic timestamps are accepted.
    Transcriptions, prompts and audio never enter this structure.
    """

    def __init__(self, *, max_events: int = 512, clock: Callable[[], float] = time.perf_counter) -> None:
        self._lock = Lock()
        self._clock = clock
        self._events: Deque[Dict[str, Any]] = deque(maxlen=max(32, min(4096, int(max_events))))

    def record(
        self,
        event: str,
        *,
        turn_id: Any = "",
        generation_id: Any = "",
        task_id: Any = "",
        sequence: Optional[int] = None,
        reason: Any = "",
        result: Any = "",
        provider: Any = "",
        state: Any = "",
        at: Optional[float] = None,
    ) -> None:
        if event not in _TIMING_EVENTS:
            raise ValueError(f"Unsupported voice timing event: {event}")
        item: Dict[str, Any] = {
            "event": event,
            "at": float(self._clock() if at is None else at),
            "turn_id": str(turn_id or "")[:96],
            "generation_id": str(generation_id or "")[:96],
            "task_id": str(task_id or "")[:96],
        }
        if sequence is not None:
            item["sequence"] = int(sequence)
        for key, value in (
            ("reason", reason), ("result", result),
            ("provider", provider), ("state", state),
        ):
            if value not in (None, ""):
                item[key] = str(value)[:96]
        with self._lock:
            self._events.append(item)

    def events(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._events]

    @staticmethod
    def _durations(
        events: List[Dict[str, Any]],
        start_name: str,
        end_names: set[str],
        key_fields: tuple[str, ...],
    ) -> List[float]:
        starts: Dict[tuple, float] = {}
        values: List[float] = []
        for item in events:
            key = tuple(str(item.get(field, "")) for field in key_fields)
            if item["event"] == start_name:
                starts[key] = item["at"]
            elif item["event"] in end_names and key in starts:
                values.append((item["at"] - starts.pop(key)) * 1000.0)
        return values

    def summary(self) -> Dict[str, Any]:
        events = self.events()
        series = {
            "endpointing_ms": self._durations(
                events, "utterance.ended", {"stt.final"}, ("turn_id",)
            ),
            "stt_ms": self._durations(events, "stt.started", {"stt.final"}, ("turn_id",)),
            "llm_ms": self._durations(
                events, "llm.started", {"llm.first_token", "llm.response_started"},
                ("generation_id",),
            ),
            "tts_ms": self._durations(
                events, "tts.segment_started", {"tts.segment_ready"},
                ("generation_id", "sequence"),
            ),
        }

        ready: Dict[tuple, float] = {}
        finished: Dict[tuple, float] = {}
        playback_gaps: List[float] = []
        synthesis_waits: List[float] = []
        artificial_gaps: List[float] = []
        for item in events:
            gen = str(item.get("generation_id", ""))
            seq = int(item.get("sequence", -1))
            key = (gen, seq)
            if item["event"] == "tts.segment_ready":
                ready[key] = item["at"]
            elif item["event"] == "playback.finished":
                finished[key] = item["at"]
            elif item["event"] == "playback.started" and seq > 0:
                previous_end = finished.get((gen, seq - 1))
                if previous_end is None:
                    continue
                gap = max(0.0, (item["at"] - previous_end) * 1000.0)
                playback_gaps.append(gap)
                if ready.get(key, item["at"]) > previous_end:
                    synthesis_waits.append(gap)
                else:
                    artificial_gaps.append(gap)

        result: Dict[str, Any] = {
            "event_count": len(events),
            "interruption_count": sum(
                1 for item in events if item["event"] == "interruption.received"
            ),
        }
        for name, values in series.items():
            result[name] = {
                "count": len(values), "p50": _percentile(values, 50),
                "p95": _percentile(values, 95),
            }
        for name, values in (
            ("playback_gap_ms", playback_gaps),
            ("synthesis_wait_gap_ms", synthesis_waits),
            ("artificial_gap_ms", artificial_gaps),
        ):
            result[name] = {
                "count": len(values), "p50": _percentile(values, 50),
                "p95": _percentile(values, 95),
            }
        return result


class VoiceTelemetryRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._status: Dict[str, Any] = {}
        self._stop_audio: Optional[Callable[[], Any]] = None
        self._test_voice: Optional[Callable[[], Any]] = None
        self._push_to_talk: Optional[Callable[[], Any]] = None
        self._transcribe: Optional[Callable[[Any], Any]] = None
        self._transcribe_detailed: Optional[Callable[[Any], Any]] = None
        self._transcriber_owner: Any = None
        self._dictation_until = 0.0
        self._timings = VoiceTimingWindow()

    def update(self, **values: Any) -> None:
        safe = {k: v for k, v in values.items() if not callable(v)}
        with self._lock:
            self._status.update(safe)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            result = dict(self._status)
        result["timings"] = self._timings.summary()
        return result

    def reset(self) -> None:
        """Clear runtime callbacks and bounded metadata after a privacy purge."""
        with self._lock:
            self._status.clear()
            self._stop_audio = None
            self._test_voice = None
            self._push_to_talk = None
            self._transcribe = None
            self._transcribe_detailed = None
            self._transcriber_owner = None
            self._dictation_until = 0.0
            self._timings = VoiceTimingWindow()

    def record_timing(self, event: str, **context: Any) -> None:
        self._timings.record(event, **context)

    def timing_events(self) -> List[Dict[str, Any]]:
        return self._timings.events()

    def register_stop_audio(self, callback: Optional[Callable[[], Any]]) -> None:
        with self._lock:
            self._stop_audio = callback

    def register_test_voice(self, callback: Optional[Callable[[], Any]]) -> None:
        with self._lock:
            self._test_voice = callback

    def register_push_to_talk(self, callback: Optional[Callable[[], Any]]) -> None:
        with self._lock:
            self._push_to_talk = callback

    def arm_push_to_talk(self) -> bool:
        with self._lock:
            callback = self._push_to_talk
        if callback is None:
            return False
        try:
            callback()
            return True
        except Exception:
            return False

    def register_transcribe(self, callback: Optional[Callable[[Any], Any]]) -> None:
        """Compatibilité : un callback simple non nul remplace toute paire périmée."""
        with self._lock:
            self._transcribe = callback
            if callback is not None:
                self._transcribe_detailed = None
                self._transcriber_owner = None

    def register_transcribe_detailed(
        self, callback: Optional[Callable[[Any], Any]]
    ) -> None:
        """Compatibilité : un callback détaillé non nul remplace toute paire périmée."""
        with self._lock:
            self._transcribe_detailed = callback
            if callback is not None:
                self._transcribe = None
                self._transcriber_owner = None

    def register_transcribers(
        self,
        simple: Optional[Callable[[Any], Any]],
        detailed: Optional[Callable[[Any], Any]],
        *,
        owner: Any,
    ) -> None:
        """Publie atomiquement la paire STT appartenant à un runtime Voice V2."""
        with self._lock:
            self._transcribe = simple
            self._transcribe_detailed = detailed
            self._transcriber_owner = owner

    def clear_transcribers(self, *, owner: Any) -> bool:
        """Retire la paire uniquement si le runtime appelant en est toujours propriétaire."""
        with self._lock:
            if self._transcriber_owner is not owner:
                return False
            self._transcribe = None
            self._transcribe_detailed = None
            self._transcriber_owner = None
            return True

    def set_dictation_active(self, active: bool, *, lease_s: Optional[float] = None) -> None:
        with self._lock:
            lease = _DICTATION_LEASE_S if lease_s is None else max(
                5.0, min(600.0, float(lease_s))
            )
            self._dictation_until = time.monotonic() + lease if active else 0.0
            self._status["dictation_active"] = bool(active)

    def is_dictation_active(self) -> bool:
        with self._lock:
            active = self._dictation_until > time.monotonic()
            if not active:
                self._status["dictation_active"] = False
            return active

    def stop_audio(self) -> bool:
        with self._lock:
            callback = self._stop_audio
        if callback is None:
            return False
        try:
            callback()
            return True
        except Exception:
            return False

    async def test_voice(self) -> bool:
        with self._lock:
            callback = self._test_voice
        if callback is None:
            return False
        try:
            result = callback()
            if inspect.isawaitable(result):
                await result
            return True
        except Exception:
            return False

    async def transcribe(self, audio: Any) -> Optional[str]:
        with self._lock:
            callback = self._transcribe
        if callback is None:
            return None
        try:
            result = callback(audio)
            if inspect.isawaitable(result):
                result = await result
            return str(result or "").strip()
        except Exception:
            return ""

    async def transcribe_detailed(self, audio: Any) -> Optional[Dict[str, Any]]:
        """Appel structuré opt-in ; les erreurs restent visibles par la route HTTP."""
        with self._lock:
            callback = self._transcribe_detailed
        if callback is None:
            return None
        result = callback(audio)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            return {
                "text": str(result or "").strip(), "segments": [],
                "status": "ok" if result else "no_speech",
            }
        return dict(result)


_REGISTRY = VoiceTelemetryRegistry()


def get_voice_telemetry() -> VoiceTelemetryRegistry:
    return _REGISTRY
