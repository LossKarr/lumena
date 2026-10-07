"""Sources d'entrée V2 — pompes VAD/STT → événements TurnManager (logic-only).

Une « source » consomme un provider (VAD ou STT) et `await tm.emit(...)` les
`VoiceEvent` correspondants dans la file UNIQUE du TurnManager. Elle ne mute
jamais l'état : tout passe par la queue (modèle acteur V2.3).

GATING : `LUMENA_VOICE_V2_STT=0` par défaut. À ce stade tout est logic-only
(providers fakes) ; aucun hardware, aucun WebRTC, aucun branchement assistant_loop.
Le flag gardera la source HARDWARE réelle (faster-whisper / VAD micro) plus tard.
"""
from __future__ import annotations

import asyncio
import os
import wave
from typing import Callable
from pathlib import Path
from typing import Any, Dict, List, Optional

from .events import VoiceEvent, VoiceCommand
from .activation import VoiceActivationGate


def v2_stt_enabled() -> bool:
    """Flag global du branchement STT/VAD V2. OFF par défaut."""
    return os.getenv("LUMENA_VOICE_V2_STT", "0").strip() == "1"


# VADEvent.kind → type d'événement TurnManager.
_VAD_EVENT_TYPE = {
    "speech_started": "vad.speech_started",
    "speech_ended": "vad.speech_ended",
}


async def pump_vad(provider: Any, tm: Any, audio: Any = None) -> None:
    """Émet `vad.speech_started`/`vad.speech_ended` depuis un VADProvider."""
    async for ev in provider.stream(audio):
        et = _VAD_EVENT_TYPE.get(ev.kind)
        if et is None:
            continue
        await tm.emit(VoiceEvent(et, t=ev.t))


async def pump_stt(provider: Any, tm: Any, audio: Any = None, *, language: str = "fr") -> None:
    """Émet `stt.partial` (partiels) et `stt.final` (final) depuis un STTProvider.

    Partiels = timing, final = contenu (V2.3). La décision d'endpoint reste au
    TurnManager : la source ne décide rien."""
    async for res in provider.stream(audio, language=language):
        et = "stt.final" if res.is_final else "stt.partial"
        await tm.emit(VoiceEvent(et, t=res.t, data={"text": res.text}))


class MicConversationSource:
    """Orchestrateur micro RÉEL : VADProvider → frontières, STTProvider → contenu.

    Sépare clairement TIMING (VAD) et CONTENU (STT) : la VAD émet
    `vad.speech_started`/`vad.speech_ended` ; à la fin d'un énoncé, on transcrit
    l'audio capturé (`vad.last_utterance`) et on émet `stt.final`. Tout passe par
    `tm.emit` (acteur unique). Aucun I/O ici sauf via les providers injectés.

    Réservé au chemin gated `LUMENA_VOICE_V2_STT=1`, hors pytest. Les providers
    réels chargent le hardware en paresseux ; ici on ne fait qu'orchestrer.
    """
    def __init__(self, vad: Any, stt: Any, tm: Any, *, language: str = "fr",
                 min_utterance_ms: int = 300, emit_partials: bool = False,
                 partial_fast: bool = True, final_fast: bool = False,
                 save_utterances_dir: Optional[str | Path] = None,
                 suppress_input_fn: Optional[Callable[[], bool]] = None,
                 activation_gate: Optional[VoiceActivationGate] = None,
                 audio_frontend: Any = None,
                 wake_word_provider: Any = None,
                 stt_timeout_s: Optional[float] = None):
        self.vad = vad
        self.stt = stt
        self.tm = tm
        self.language = language
        # Partiels (opt-in) : sur `speech_partial`, transcrire le snapshot en cours
        # → `stt.partial` (timing/fluidité). Le final reste produit sur speech_ended.
        self.emit_partials = emit_partials
        # Les partiels restent rapides/instables (timing). Le final privilégie la précision :
        # Whisper beam=5 et sans prompt de commandes quand le provider expose `fast=False`.
        self.partial_fast = partial_fast
        self.final_fast = final_fast
        # Filtre anti-fragments : un énoncé plus court que ce seuil est ignoré AVANT
        # transcription (évite les Whisper à vide sur des bruits/clics de 0,2-0,8 s,
        # et les tours fantômes). La VAD continue d'émettre les frontières (timing
        # honnête) ; seul le CONTENU est filtré.
        self.min_utterance_ms = min_utterance_ms
        self.fragments_skipped = 0
        self._running = False
        self.save_utterances_dir = Path(save_utterances_dir) if save_utterances_dir else None
        self.saved_utterances: List[Path] = []
        self._suppress_input_fn = suppress_input_fn or (lambda: False)
        self._utterance_suppressed = False
        self.activation_gate = activation_gate
        self.last_transcription_detail: dict = {}
        self.audio_frontend = audio_frontend
        self.wake_word_provider = wake_word_provider
        if stt_timeout_s is None:
            try:
                stt_timeout_s = float(os.getenv("LUMENA_STT_TIMEOUT_S", "30"))
            except (TypeError, ValueError):
                stt_timeout_s = 30.0
        self.stt_timeout_s = max(1.0, min(120.0, float(stt_timeout_s)))

    def _input_suppressed(self) -> bool:
        try:
            return bool(self._suppress_input_fn())
        except Exception:
            return False

    def _take_audio_buffer(self, name: str) -> bytes:
        """Copy then clear a provider buffer before any await or persistence."""
        value = bytes(getattr(self.vad, name, b"") or b"")
        try:
            setattr(self.vad, name, b"")
        except Exception:
            pass
        return value

    def _utterance_ms(self, n_bytes: int) -> float:
        rate = getattr(self.vad, "SAMPLE_RATE", 16000)
        width = getattr(self.vad, "SAMPLE_WIDTH", 2)
        return (n_bytes / (rate * width)) * 1000.0

    async def _transcribe(self, audio: bytes, *, fast: bool) -> str:
        if not fast:
            detailed = getattr(self.stt, "transcribe_detailed", None)
            if callable(detailed):
                try:
                    result = await detailed(audio, language=self.language, strict=False)
                    if isinstance(result, dict):
                        self.last_transcription_detail = dict(result)
                        return str(result.get("text", "") or "")
                except TypeError:
                    pass
        try:
            return await self.stt.transcribe(audio, language=self.language, fast=fast)
        except TypeError:
            # Fakes/tests ou providers historiques qui ne connaissent pas encore `fast`.
            return await self.stt.transcribe(audio, language=self.language)

    def _save_utterance(self, utterance: bytes) -> None:
        if self.save_utterances_dir is None:
            return
        self.save_utterances_dir.mkdir(parents=True, exist_ok=True)
        path = self.save_utterances_dir / f"utterance_{len(self.saved_utterances) + 1:03d}.wav"
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(getattr(self.vad, "SAMPLE_WIDTH", 2))
            wf.setframerate(getattr(self.vad, "SAMPLE_RATE", 16000))
            wf.writeframes(utterance)
        self.saved_utterances.append(path)

    def _final_event_data(self, text: str, **values: Any) -> dict:
        data = {"text": text, **values}
        language = self.last_transcription_detail.get("language", "")
        probability = self.last_transcription_detail.get("language_probability", 0.0)
        if language:
            data["language"] = language
            data["language_probability"] = probability
        return data

    def _current_turn_id(self) -> Optional[str]:
        return getattr(getattr(self.tm, "state", None), "current_turn_id", None)

    @staticmethod
    def _record_timing(event: str, **context: Any) -> None:
        try:
            from .observability import get_voice_telemetry  # noqa: PLC0415
            get_voice_telemetry().record_timing(event, **context)
        except Exception:
            pass

    async def _emit_stt_final(
        self, *, text: str, t: int, turn_id: Optional[str], terminal_reason: str,
        **values: Any,
    ) -> None:
        data = self._final_event_data(
            text, turn_id=turn_id, terminal_reason=terminal_reason, **values
        )
        await self.tm.emit(VoiceEvent("stt.final", t=t, data=data))
        self._record_timing(
            "stt.final", turn_id=turn_id or "", reason=terminal_reason,
            result="text" if text.strip() else "empty",
        )

    async def run(self, audio: Any = None) -> None:
        self._running = True
        async for ev in self.vad.stream(audio):
            if not self._running and ev.kind != "speech_ended":
                break
            if ev.kind == "speech_started" or self._input_suppressed():
                self._utterance_suppressed = self._input_suppressed()
            if self._utterance_suppressed:
                if ev.kind == "speech_ended":
                    self._utterance_suppressed = False
                continue
            if ev.kind == "speech_partial":
                # Partiel : transcription best-effort du snapshot en cours → stt.partial.
                if self.emit_partials and (
                    self.activation_gate is None or self.activation_gate.is_active
                ):
                    snap = self._take_audio_buffer("partial_utterance")
                    if snap:
                        text = await self._transcribe(snap, fast=self.partial_fast)
                        if text:
                            await self.tm.emit(VoiceEvent("stt.partial", t=ev.t,
                                                          data={"text": text}))
                continue
            et = _VAD_EVENT_TYPE.get(ev.kind)
            if et is None:
                continue
            if ev.kind == "speech_started" and self.audio_frontend is not None:
                begin_capture = getattr(self.audio_frontend, "begin_capture", None)
                if callable(begin_capture):
                    begin_capture()
            await self.tm.emit(VoiceEvent(et, t=ev.t))
            if ev.kind == "speech_ended":
                # Énoncé clos : transcrire l'audio capturé → contenu.
                turn_id = self._current_turn_id()
                self._record_timing("utterance.ended", turn_id=turn_id or "")
                await self.tm.emit(VoiceEvent(
                    "stt.started", t=ev.t, data={"turn_id": turn_id}
                ))
                self._record_timing("stt.started", turn_id=turn_id or "")
                utterance = self._take_audio_buffer("last_utterance")
                if not utterance:
                    await self._emit_stt_final(
                        text="", t=ev.t, turn_id=turn_id, terminal_reason="no_audio"
                    )
                    continue
                dur_ms = self._utterance_ms(len(utterance))
                if dur_ms < self.min_utterance_ms:
                    # Fragment trop court → on ne transcrit pas (anti-bruit).
                    self.fragments_skipped += 1
                    await self._emit_stt_final(
                        text="", t=ev.t, turn_id=turn_id,
                        terminal_reason="fragment_too_short",
                    )
                    continue
                self._save_utterance(utterance)
                if self.audio_frontend is not None:
                    utterance = self.audio_frontend.process_capture(
                        utterance, sample_rate=getattr(self.vad, "SAMPLE_RATE", 16000)
                    )
                try:
                    text = await asyncio.wait_for(
                        self._transcribe(utterance, fast=self.final_fast),
                        timeout=self.stt_timeout_s,
                    )
                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError:
                    await self._emit_stt_final(
                        text="", t=ev.t, turn_id=turn_id,
                        terminal_reason="stt_timeout",
                    )
                    continue
                except Exception as exc:
                    await self._emit_stt_final(
                        text="", t=ev.t, turn_id=turn_id,
                        terminal_reason="stt_error", error_type=type(exc).__name__,
                    )
                    continue
                if text:
                    if self.activation_gate is None:
                        await self._emit_stt_final(
                            text=text, t=ev.t, turn_id=turn_id, terminal_reason="text"
                        )
                        continue
                    wake_detection = None
                    if (
                        self.wake_word_provider is not None
                        and self.activation_gate.mode == "wake_phrase"
                        and not self.activation_gate.is_active
                    ):
                        wake_detection = await self.wake_word_provider.detect(utterance)
                    decision = self.activation_gate.evaluate(
                        text,
                        wake_detected=bool(
                            wake_detection and wake_detection.detected
                        ),
                        speaker_match=(
                            wake_detection.speaker_match if wake_detection else None
                        ),
                    )
                    if not decision.accepted:
                        # The activation decision is terminal even when no text is
                        # forwarded to the LLM.
                        self._record_timing(
                            "stt.final", turn_id=turn_id or "",
                            reason=decision.reason, result="rejected",
                        )
                        await self.tm.emit(VoiceEvent(
                            "activation.rejected", t=ev.t,
                            data={"reason": decision.reason, "turn_id": turn_id},
                        ))
                    elif decision.text:
                        await self._emit_stt_final(
                            text=decision.text, t=ev.t, turn_id=turn_id,
                            terminal_reason="text", activation=decision.reason,
                        )
                    else:
                        self._record_timing(
                            "stt.final", turn_id=turn_id or "",
                            reason=decision.reason, result="accepted",
                        )
                        await self.tm.emit(VoiceEvent(
                            "activation.accepted", t=ev.t,
                            data={"reason": decision.reason, "turn_id": turn_id},
                        ))
                else:
                    await self._emit_stt_final(
                        text="", t=ev.t, turn_id=turn_id, terminal_reason="no_speech"
                    )

    def stop(self) -> None:
        self._running = False
        self._take_audio_buffer("partial_utterance")
        self._take_audio_buffer("last_utterance")
        stop = getattr(self.vad, "stop", None)
        if callable(stop):
            stop()


class EndpointTimerService:
    """Service de timer de silence : exécute les commandes `arm_endpoint_timer`.

    Le TurnManager ne fait pas d'I/O : il ÉMET `arm_endpoint_timer`/`cancel_endpoint_timer`.
    Ce service (côté effets) les exécute en planifiant un `timer.endpoint` après `wait_ms`,
    réinjecté dans la file UNIQUE. `cancel_endpoint_timer` annule le timer en vol (parole
    reprise). Logic-only : `asyncio` pur, aucun hardware. Branchable comme dispatcher.
    """
    def __init__(self, tm: Any, *, speed: float = 1.0):
        self.tm = tm
        self.speed = speed                       # 1.0 = temps réel ; <1 accélère les tests
        self._timers: Dict[Optional[str], asyncio.Task] = {}

    async def dispatch(self, commands: List[VoiceCommand]) -> None:
        for cmd in commands:
            if cmd.name == "arm_endpoint_timer":
                self._arm(
                    cmd.data.get("turn_id"), int(cmd.data.get("wait_ms", 0)),
                    int(cmd.data.get("elapsed_ms", 0)),
                )
            elif cmd.name == "cancel_endpoint_timer":
                self._cancel(cmd.data.get("turn_id"))

    def _arm(self, turn_id: Optional[str], wait_ms: int, elapsed_ms: int = 0) -> None:
        self._cancel(turn_id)                    # un seul timer armé par tour
        self._timers[turn_id] = asyncio.ensure_future(
            self._fire(turn_id, wait_ms, elapsed_ms)
        )

    def _cancel(self, turn_id: Optional[str]) -> None:
        task = self._timers.pop(turn_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _fire(self, turn_id: Optional[str], wait_ms: int, elapsed_ms: int = 0) -> None:
        try:
            await asyncio.sleep((wait_ms / 1000.0) * self.speed)
        except asyncio.CancelledError:
            return                               # parole reprise → pas de timer.endpoint
        self._timers.pop(turn_id, None)
        await self.tm.emit(VoiceEvent(
            "timer.endpoint",
            data={"turn_id": turn_id, "pause_ms": elapsed_ms + wait_ms},
        ))

    def cancel_all(self) -> None:
        for task in self._timers.values():
            if not task.done():
                task.cancel()
        self._timers.clear()
