"""VoiceRuntime — dispatcher RÉEL du TurnManager (TTS synth + playback), gated.

Remplace `FakeRuntime` comme dispatcher : exécute les VoiceCommand via un
`TTSProvider` (synthèse seule) + un `LocalAudioPlayer` (playback cancellable),
et ré-émet les événements de suivi dans la queue du TurnManager (acteur unique).

GATING : `LUMENA_VOICE_V2_TTS=0` par défaut → `dispatch` est un NO-OP tant que le
flag n'est pas activé. Aucun branchement dans `assistant_loop`, pas de STT, pas de
WebRTC. Le LLM réel n'est pas câblé : un `respond_fn` injectable produit la réponse
(stub par défaut), car cette étape ne concerne que le chemin audio.

Statut : si la synthèse retombe sur `pyttsx3`, le runtime se signale `degraded`.
"""
from __future__ import annotations

import asyncio
import inspect
import os
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .events import VoiceEvent, VoiceCommand
from .ledger import ConversationAudioLedger
from .voice_profile import (
    VoiceProfile, apply_pronunciations, classify_dialogue_act, load_profile,
)
from .speech_normalizer import prepare_for_tts


def v2_tts_enabled() -> bool:
    """Flag global du branchement TTS V2. OFF par défaut."""
    return os.getenv("LUMENA_VOICE_V2_TTS", "0").strip() == "1"


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class VoiceRuntime:
    def __init__(self, tm: Any, tts_provider: Any, player: Any, *,
                 respond_fn: Optional[Callable[[str], Any]] = None,
                 is_muted_fn: Optional[Callable[[], bool]] = None,
                 voice_profile: Optional[VoiceProfile] = None,
                 enabled: Optional[bool] = None):
        self.tm = tm
        self.tts = tts_provider
        self.player = player
        self.ledger: ConversationAudioLedger = getattr(player, "ledger", None) or ConversationAudioLedger()
        # LLM stub par défaut : renvoie le texte tel quel (le LLM réel viendra plus tard).
        self._respond = respond_fn or (lambda text: text)
        self._is_muted = is_muted_fn or (lambda: False)
        self.voice_profile = voice_profile or load_profile(
            os.getenv("LUMENA_VOICE_PROFILE_PATH", "").strip() or None
        )
        # enabled None => lit le flag ; bool explicite => force (tests).
        self._enabled = v2_tts_enabled() if enabled is None else bool(enabled)
        self.status = "idle"
        self.degraded = False
        self.last_provider = ""
        self._seg_audio: Dict[tuple, Optional[str]] = {}  # (generation_id, sequence) -> chemin audio
        self._play_tasks: List[asyncio.Task] = []         # playbacks en cours (jamais awaités dans l'acteur)
        self._producer_tasks: List[asyncio.Task] = []     # producteurs de stream TTS (chunking)
        self._llm_tasks: Dict[str, asyncio.Task] = {}     # réponses LLM annulables, hors acteur
        self._gen_expected: Dict[str, int] = {}           # nb de segments attendus (set en fin de producteur)
        self._gen_played: Dict[str, int] = {}             # nb de segments réellement joués
        self._finished: set = set()                       # générations dont 'finished' a déjà été émis
        self._play_lock = asyncio.Lock()                  # un seul segment audible à la fois, sans bloquer l'acteur
        self._agent_gen_seq = 0                            # compteur de générations pour speak() hors-bande
        self._generation_started_at: Dict[str, float] = {}
        self._first_audio_seen: set[str] = set()
        self._metrics: Dict[str, Any] = {
            "llm_ms": None, "first_audio_ms": None, "interrupt_ms": None,
            "queue_depth": 0, "dialogue_act": "explanation",
        }

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def dispatch(self, commands: List[VoiceCommand]) -> None:
        if not self._enabled:
            return  # NO-OP tant que LUMENA_VOICE_V2_TTS != 1
        for cmd in commands:
            await self._handle(cmd)
            try:
                from .observability import get_voice_telemetry
                get_voice_telemetry().update(**self.status_report())
            except Exception:
                pass

    async def _handle(self, cmd: VoiceCommand) -> None:
        d = cmd.data
        if cmd.name == "start_llm":
            gen = d["generation_id"]; turn = d.get("turn_id"); text = d.get("text", "")
            self._generation_started_at[gen] = time.perf_counter()
            old = self._llm_tasks.pop(gen, None)
            if old is not None and not old.done():
                old.cancel()
            task = asyncio.create_task(self._respond_and_emit(gen, turn, text))
            self._llm_tasks[gen] = task
            task.add_done_callback(lambda _t, _g=gen: self._forget_llm_task(_g, _t))

        elif cmd.name == "cancel_llm":
            gen = d.get("generation_id")
            targets = [self._llm_tasks.get(gen)] if gen else list(self._llm_tasks.values())
            for task in targets:
                if task is not None and not task.done():
                    task.cancel()
            started = self._generation_started_at.get(gen) if gen else None
            if started is not None:
                self._metrics["interrupt_ms"] = (time.perf_counter() - started) * 1000.0

        elif cmd.name == "play_audio":
            if self._muted():
                self.player.stop()
                self.status = "muted"
                return
            # Playback EN TÂCHE DE FOND : l'acteur (TurnManager) ne doit jamais rester
            # bloqué sur un playback long, sinon stop_word/barge-in ne seraient pas traités.
            gen = d.get("generation_id"); seq = d.get("sequence", 0)
            text = d.get("text", ""); dur = d.get("duration_ms", 0)
            path = self._seg_audio.get((gen, seq))
            task = asyncio.ensure_future(self._play_and_report(gen, seq, text, dur, path))
            self._play_tasks.append(task)
            self._play_tasks = [t for t in self._play_tasks if not t.done()]

        elif cmd.name in ("stop_playback", "clear_audio_queue"):
            # Immédiat : stoppe le player ET annule playbacks ET producteurs, sans rien attendre.
            self.player.stop()
            pending = self._play_tasks + self._producer_tasks
            if cmd.name == "clear_audio_queue":
                pending += list(self._llm_tasks.values())
            for t in pending:
                if not t.done():
                    t.cancel()
            self._play_tasks = []
            self._producer_tasks = []
            if cmd.name == "clear_audio_queue":
                self._llm_tasks = {}
                self.player.set_generation("__cleared__")
            self.status = "interrupted"

        elif cmd.name == "truncate_conversation":
            gen = d.get("generation_id")
            if gen:
                self.ledger.truncate(gen)

        elif cmd.name == "show_status":
            self.status = d.get("state", self.status)
        # cancel_tts/cancel_tool_if_safe/request_confirmation : pas d'effet audio ici.

    async def _respond_and_emit(self, gen: str, turn: Any, text: str) -> None:
        """Resolve one response outside the actor, then emit only if still current."""
        try:
            answer = await _maybe_await(self._respond(text))
            if self._llm_tasks.get(gen) is not asyncio.current_task() or self._muted():
                return
            started = self._generation_started_at.get(gen)
            if started is not None:
                self._metrics["llm_ms"] = (time.perf_counter() - started) * 1000.0
            await self._emit_answer(gen, turn, str(answer or ""), status="thinking")
        except asyncio.CancelledError:
            return

    def _forget_llm_task(self, gen: str, task: asyncio.Task) -> None:
        if self._llm_tasks.get(gen) is task:
            self._llm_tasks.pop(gen, None)

    def _muted(self) -> bool:
        try:
            return bool(self._is_muted())
        except Exception:
            return False

    async def _emit_answer(self, gen: str, turn: Any, answer: str, *,
                           status: str = "speaking") -> None:
        """Synthétise + joue un texte sous une génération donnée (cœur commun).

        Utilisé par `start_llm` (réponse au tour) ET par `speak()` (parole hors-bande
        pilotée par l'orchestrateur task-aware : accusé, jalon, résultat, erreur)."""
        if self._muted() or not (answer or "").strip():
            return
        answer = prepare_for_tts(apply_pronunciations(answer, self.voice_profile))
        if not answer:
            return
        self._metrics["dialogue_act"] = classify_dialogue_act(answer)
        self.status = status
        self.ledger.register_generation(turn, gen, answer)
        self.player.set_generation(gen)
        self._gen_played[gen] = 0
        await self.tm.emit(VoiceEvent("llm.response_started", data={"generation_id": gen}))
        if getattr(self.tts, "supports_streaming", False):
            # CHUNKING : un producteur synthétise segment par segment (pipeline),
            # en tâche de fond pour ne pas bloquer l'acteur.
            task = asyncio.ensure_future(self._produce_stream(gen, answer))
            self._producer_tasks.append(task)
            self._producer_tasks = [t for t in self._producer_tasks if not t.done()]
        else:
            # Fallback mono-chunk (provider sans streaming).
            res = await self.tts.synthesize(answer, voice=self.voice_profile)
            if self._muted() or gen != self.player.current_generation_id:
                return
            self.last_provider = res.provider
            if res.degraded:
                self.degraded = True
            self._seg_audio[(gen, 0)] = res.audio_path
            self._gen_expected[gen] = 1 if res.ok else 0
            if res.ok:
                await self.tm.emit(VoiceEvent("tts.chunk_ready", data={
                    "generation_id": gen, "sequence": 0,
                    "text": answer, "duration_ms": res.duration_ms,
                }))
            await self._maybe_finish(gen)

    async def speak(self, text: str, *, turn: Any = "agent") -> str:
        """Fait parler Lumena hors-bande (sans passer par `respond_fn`/start_llm).

        Réutilisé par l'orchestrateur task-aware pour les jalons vocaux (accusé,
        « je travaille », résultat, erreur). Non bloquant : la synthèse part en fond.
        Renvoie l'id de génération alloué. Texte vide → no-op."""
        if self._muted() or not (text or "").strip():
            return ""
        self._agent_gen_seq += 1
        gen = f"agent_{self._agent_gen_seq}"
        self._generation_started_at[gen] = time.perf_counter()
        await self._emit_answer(gen, turn, text)
        return gen

    async def _produce_stream(self, gen: str, answer: str) -> None:
        """Producteur de stream TTS (chunking) : synthétise segment par segment.

        Émet un `tts.chunk_ready` par segment dès qu'il est prêt (pipeline : le
        reducer déclenche le play du segment N pendant qu'on synthétise N+1).
        En fin de stream, fige `_gen_expected[gen]` puis tente `_maybe_finish`
        (cas où tout a déjà été joué avant la fin de la synthèse).
        """
        count = 0
        try:
            async for ch in self.tts.stream(answer, voice=self.voice_profile):
                # La génération a pu changer (interruption) → on stoppe la synthèse.
                if gen != self.player.current_generation_id or self._muted():
                    return
                self.last_provider = ch.provider or self.last_provider
                if ch.degraded:
                    self.degraded = True
                self._seg_audio[(gen, ch.sequence)] = ch.audio_path
                count += 1
                await self.tm.emit(VoiceEvent("tts.chunk_ready", data={
                    "generation_id": gen, "sequence": ch.sequence,
                    "text": ch.text, "duration_ms": ch.duration_ms,
                }))
                self._metrics["queue_depth"] = max(
                    int(self._metrics.get("queue_depth") or 0), count - self._gen_played.get(gen, 0)
                )
        except asyncio.CancelledError:
            return  # interruption : aucun finished, le reducer gère la troncature
        # Synthèse terminée : on connaît le nombre exact de segments attendus.
        self._gen_expected[gen] = count
        await self._maybe_finish(gen)

    async def _maybe_finish(self, gen: str) -> None:
        """Émet `playback.finished` UNE fois, quand tous les segments attendus sont joués."""
        if gen in self._finished:
            return
        expected = self._gen_expected.get(gen)
        if expected is None:
            return  # synthèse pas encore finie → on ne connaît pas le total
        if self._gen_played.get(gen, 0) < expected:
            return  # tous les segments ne sont pas encore joués
        if gen != self.player.current_generation_id or self.player._stopped:
            return  # génération périmée/interrompue → pas de finished
        self._finished.add(gen)
        await self.tm.emit(VoiceEvent("playback.finished", data={"generation_id": gen}))

    async def _play_and_report(self, gen: str, seq: int, text: str, dur: int, path: Any) -> None:
        """Joue UN chunk en tâche de fond ; comptabilise et tente finished à la fin."""
        try:
            async with self._play_lock:
                r = await self.player.play(generation_id=gen, sequence=seq, text=text,
                                           duration_ms=dur, path=path)
        except asyncio.CancelledError:
            return  # interrompu → on n'émet rien (interruption gérée par le reducer)
        # Ne comptabilise QUE si la génération est toujours active et non stoppée.
        if r == "played" and gen == self.player.current_generation_id and not self.player._stopped:
            self.status = "speaking"
            self._gen_played[gen] = self._gen_played.get(gen, 0) + 1
            if gen not in self._first_audio_seen:
                self._first_audio_seen.add(gen)
                started = self._generation_started_at.get(gen)
                if started is not None:
                    self._metrics["first_audio_ms"] = (time.perf_counter() - started) * 1000.0
            await self.tm.emit(VoiceEvent("playback.chunk_played",
                                          data={"generation_id": gen, "sequence": seq}))
            await self._maybe_finish(gen)   # finished seulement quand TOUS les segments sont joués

    async def aclose(self) -> None:
        """Arrêt propre : stoppe le player, annule ET ATTEND playbacks + producteurs.

        L'attente (gather) est essentielle : sans elle, les tâches annulées n'ont pas
        le temps de fermer leurs ressources (transports subprocess Piper), d'où le
        warning `BaseSubprocessTransport.__del__ ... closed pipe` à la fermeture de la
        boucle. On attend donc leur terminaison effective avant de rendre la main.
        """
        try:
            self.player.stop()
        except Exception:
            pass
        tasks = [
            t for t in (self._play_tasks + self._producer_tasks + list(self._llm_tasks.values()))
            if not t.done()
        ]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._play_tasks = []
        self._producer_tasks = []
        self._llm_tasks = {}

    def status_report(self) -> Dict[str, Any]:
        """Statut pour l'UI : provider, dégradé (pyttsx3), état courant."""
        return {
            "enabled": self._enabled,
            "state": self.status,
            "provider": self.last_provider,
            "degraded": self.degraded,
            "voice_profile": self.voice_profile.id,
            "dialogue_act": self._metrics.get("dialogue_act"),
            "llm_ms": self._metrics.get("llm_ms"),
            "first_audio_ms": self._metrics.get("first_audio_ms"),
            "interrupt_ms": self._metrics.get("interrupt_ms"),
            "queue_depth": self._metrics.get("queue_depth", 0),
            "identity_degraded": self.last_provider == "pyttsx3",
        }
