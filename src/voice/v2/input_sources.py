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
                 suppress_input_fn: Optional[Callable[[], bool]] = None):
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

    def _input_suppressed(self) -> bool:
        try:
            return bool(self._suppress_input_fn())
        except Exception:
            return False

    def _utterance_ms(self, n_bytes: int) -> float:
        rate = getattr(self.vad, "SAMPLE_RATE", 16000)
        width = getattr(self.vad, "SAMPLE_WIDTH", 2)
        return (n_bytes / (rate * width)) * 1000.0

    async def _transcribe(self, audio: bytes, *, fast: bool) -> str:
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
                if self.emit_partials:
                    snap = getattr(self.vad, "partial_utterance", b"")
                    if snap:
                        text = await self._transcribe(snap, fast=self.partial_fast)
                        if text:
                            await self.tm.emit(VoiceEvent("stt.partial", t=ev.t,
                                                          data={"text": text}))
                continue
            et = _VAD_EVENT_TYPE.get(ev.kind)
            if et is None:
                continue
            await self.tm.emit(VoiceEvent(et, t=ev.t))
            if ev.kind == "speech_ended":
                # Énoncé clos : transcrire l'audio capturé → contenu.
                utterance = getattr(self.vad, "last_utterance", b"")
                if not utterance:
                    continue
                dur_ms = self._utterance_ms(len(utterance))
                if dur_ms < self.min_utterance_ms:
                    # Fragment trop court → on ne transcrit pas (anti-bruit).
                    self.fragments_skipped += 1
                    continue
                self._save_utterance(utterance)
                text = await self._transcribe(utterance, fast=self.final_fast)
                if text:
                    await self.tm.emit(VoiceEvent("stt.final", t=ev.t, data={"text": text}))

    def stop(self) -> None:
        self._running = False
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
                self._arm(cmd.data.get("turn_id"), int(cmd.data.get("wait_ms", 0)))
            elif cmd.name == "cancel_endpoint_timer":
                self._cancel(cmd.data.get("turn_id"))

    def _arm(self, turn_id: Optional[str], wait_ms: int) -> None:
        self._cancel(turn_id)                    # un seul timer armé par tour
        self._timers[turn_id] = asyncio.ensure_future(self._fire(turn_id, wait_ms))

    def _cancel(self, turn_id: Optional[str]) -> None:
        task = self._timers.pop(turn_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _fire(self, turn_id: Optional[str], wait_ms: int) -> None:
        try:
            await asyncio.sleep((wait_ms / 1000.0) * self.speed)
        except asyncio.CancelledError:
            return                               # parole reprise → pas de timer.endpoint
        self._timers.pop(turn_id, None)
        await self.tm.emit(VoiceEvent("timer.endpoint", data={"turn_id": turn_id, "pause_ms": wait_ms}))

    def cancel_all(self) -> None:
        for task in self._timers.values():
            if not task.done():
                task.cancel()
        self._timers.clear()
