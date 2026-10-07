"""TurnManager — acteur unique du système vocal (V2.3).

Règles dures :
- une seule `asyncio.Queue[VoiceEvent]` ;
- toutes les sources font `await emit(event)` ;
- aucune source ne mute l'état ;
- une seule coroutine `run()` consomme la queue et applique `reduce()` ;
- tous les effets sortent en `VoiceCommand` (le TurnManager ne fait JAMAIS d'I/O).

`feed()` est l'entrée SYNCHRONE déterministe (replay/pytest) : même reducer que
la prod. La prod utilise `emit()` + `run()`.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, List, Optional

from .events import VoiceEvent, VoiceCommand
from .state import VoiceState, EndpointDecision
from .endpointing import decide_endpoint


def _default_turn_id(n: int) -> str:
    return f"u_{n}"


def _default_gen_id(n: int) -> str:
    return f"a_{n}"


class TurnManager:
    def __init__(
        self,
        *,
        speculative_enabled: bool = False,     # OFF par défaut (V2.3)
        barge_in_on_vad: bool = False,         # OFF : VAD pose pending + confirme par STT
        queue_maxsize: int = 256,
        dispatcher: Optional[Callable[[List[VoiceCommand]], Awaitable[None]]] = None,
    ):
        self.queue: "asyncio.Queue[VoiceEvent]" = asyncio.Queue(maxsize=queue_maxsize)
        self.state = VoiceState()
        self.speculative_enabled = speculative_enabled
        self.barge_in_on_vad = barge_in_on_vad
        self._dispatcher = dispatcher
        self._turn_seq = 0
        self._gen_seq = 0
        # Trace des commandes émises (pour tests/diagnostic).
        self.emitted: List[VoiceCommand] = []
        self._running = False

    # ── IDs ──────────────────────────────────────────────────────────────
    def _new_turn_id(self) -> str:
        self._turn_seq += 1
        return _default_turn_id(self._turn_seq)

    def _new_generation_id(self) -> str:
        self._gen_seq += 1
        return _default_gen_id(self._gen_seq)

    # ── Boucle acteur (prod) ─────────────────────────────────────────────
    async def emit(self, event: VoiceEvent) -> None:
        await self.queue.put(event)

    async def run(self) -> None:
        """Boucle acteur. S'arrête proprement sur `system.shutdown` (pas de kill externe)."""
        self._running = True
        try:
            while self._running:
                event = await self.queue.get()
                commands = self.reduce(event)
                if self._dispatcher and commands:
                    await self._dispatcher(commands)
                if not self._running:  # system.shutdown reçu
                    break
        except asyncio.CancelledError:
            self._running = False
            raise

    async def shutdown(self) -> None:
        """Demande un arrêt propre en passant par la queue (jamais de mutation directe)."""
        await self.queue.put(VoiceEvent("system.shutdown"))

    def stop(self) -> None:
        self._running = False

    # ── Entrée synchrone déterministe (replay/tests) ─────────────────────
    def feed(self, event: VoiceEvent) -> List[VoiceCommand]:
        """Applique le reducer et renvoie les commandes émises pour cet événement."""
        cmds = self.reduce(event)
        return cmds

    # ── Helpers de transition ────────────────────────────────────────────
    def _interrupt_commands(self, *, hard: bool, reason: str) -> List[VoiceCommand]:
        """Séquence d'annulation. NE coupe PAS l'outil (décision séparée)."""
        cmds = [
            VoiceCommand("stop_playback", {"reason": reason}),
            VoiceCommand("clear_audio_queue", {"reason": reason}),
            VoiceCommand("cancel_tts", {"generation_id": self.state.current_generation_id}),
            VoiceCommand("cancel_llm", {"generation_id": self.state.current_generation_id}),
            VoiceCommand("truncate_conversation", {"generation_id": self.state.current_generation_id}),
            VoiceCommand("show_status", {"state": "interrupted", "reason": reason}),
        ]
        # Un stop explicite ne reprend jamais. Un barge-in potentiel garde la
        # génération jusqu'au transcript final pour permettre une reprise sûre.
        self.state.interrupted_generation_id = (
            None if hard else self.state.current_generation_id
        )
        # L'outil continue : on coupe la voix, pas l'action.
        self.state.current_generation_id = None
        self.state.pending_barge_in = False
        # On reste en tool_running si un outil tourne ; sinon retour écoute.
        self.state.set_mode("tool_running" if self.state.tool_active else "wake_listening")
        return cmds

    def _apply_endpoint_decision(self, dec: EndpointDecision) -> List[VoiceCommand]:
        """Applique une décision de fin de tour (source externe OU timer de silence).

        SEUL endroit qui transforme une décision en `start_llm` : turn_complete +
        un final non vide. Sinon on attend (user_paused). Les finals pilotent le
        contenu — jamais d'action sur un simple partiel."""
        st = self.state
        st.endpoint_decision = dec
        if dec.state == "turn_complete" and st.final_transcript.strip():
            st.interrupted_generation_id = None
            st.current_generation_id = self._new_generation_id()
            st.set_mode("thinking")
            return [VoiceCommand("start_llm", {
                "turn_id": st.current_turn_id,
                "generation_id": st.current_generation_id,
                "text": st.final_transcript,
                "language": st.final_language,
                "language_probability": st.final_language_probability,
            })]
        # continue_expected / uncertain (ou pas de final) → NE PAS répondre, attendre.
        st.set_mode("user_paused")
        return []

    def _resume_interrupted_after_empty_transcript(self, reason: str) -> List[VoiceCommand]:
        """Close a proven empty interruption and resume its unheard tail once."""
        st = self.state
        generation = st.interrupted_generation_id
        st.interrupted_generation_id = None
        st.endpoint_armed = False
        st.endpoint_armed_turn = None
        st.endpoint_started_t = None
        st.transcription_pending = False
        st.transcription_turn_id = None
        st.transcription_terminal_reason = reason
        st.set_mode("tool_running" if st.tool_active else "wake_listening")
        if not generation:
            return []
        return [VoiceCommand("resume_interrupted_speech", {
            "generation_id": generation,
            "reason": reason,
        })]

    # ── Reducer : SEUL endroit qui mute l'état ───────────────────────────
    def reduce(self, event: VoiceEvent) -> List[VoiceCommand]:
        et = event.type
        st = self.state
        cmds: List[VoiceCommand] = []

        if et == "ui.mute":
            st.speech_muted = True
            cmds = [VoiceCommand("stop_playback"), VoiceCommand("show_status", {"state": "muted"})]

        elif et == "ui.unmute":
            st.speech_muted = False

        elif et == "vad.speech_started":
            if st.mode == "speaking":
                if self.barge_in_on_vad:
                    # Barge-in IMMÉDIAT sur VAD : on coupe la voix dès la parole détectée,
                    # sans attendre le STT final (latence minimale). La VAD est le signal,
                    # le STT confirmera ensuite le CONTENU du nouveau tour.
                    cmds = self._interrupt_commands(hard=False, reason="barge_in_vad")
                    st.set_mode("user_speaking")
                    st.current_turn_id = self._new_turn_id()
                    st.partial_transcript = ""
                    st.final_transcript = ""
                    st.final_language = None
                    st.final_language_probability = 0.0
                    st.transcription_pending = False
                    st.transcription_turn_id = None
                    st.transcription_terminal_reason = None
                    st.endpoint_decision = None
                    st.pending_barge_in = False
                    cmds.append(VoiceCommand("start_stt", {"turn_id": st.current_turn_id}))
                else:
                    # Parole pendant que Lumena parle : barge-in POTENTIEL, on ne coupe
                    # pas encore (anti faux barge-in) — confirmation par transcript.
                    st.pending_barge_in = True
            elif st.mode in ("user_speaking", "user_paused") and st.endpoint_armed:
                # La parole REPREND avant l'expiration du timer de silence : ce n'était
                # qu'une pause, pas une fin de tour. On désarme l'endpointing, même tour.
                st.endpoint_armed = False
                st.endpoint_armed_turn = None
                if st.endpoint_started_t is not None and event.t is not None:
                    observed = max(0, int(float(event.t) - float(st.endpoint_started_t)))
                    if 100 <= observed <= 3000:
                        previous = st.user_avg_pause_ms
                        st.user_avg_pause_ms = max(
                            250, min(1800, round(previous * 0.8 + observed * 0.2))
                        )
                        st.observed_pause_count += 1
                st.endpoint_started_t = None
                cmds = [VoiceCommand("cancel_endpoint_timer", {"turn_id": st.current_turn_id})]
            elif st.mode in ("idle", "wake_listening", "user_paused"):
                st.current_turn_id = self._new_turn_id()
                st.partial_transcript = ""
                st.final_transcript = ""
                st.final_language = None
                st.final_language_probability = 0.0
                st.transcription_pending = False
                st.transcription_turn_id = None
                st.transcription_terminal_reason = None
                st.endpoint_decision = None
                st.endpoint_armed = False
                st.endpoint_armed_turn = None
                st.endpoint_started_t = None
                st.set_mode("user_speaking")
                cmds = [VoiceCommand("start_stt", {"turn_id": st.current_turn_id})]

        elif et == "vad.speech_ended":
            # Fin de parole détectée par la VAD : on ARME un timer de silence dont la
            # durée dépend du contexte (endpointing heuristique). À son expiration,
            # `timer.endpoint` tranchera. On ne conclut RIEN ici (le silence peut être
            # une simple pause). Aucun effet hors user_speaking (pas de tour en cours).
            if st.mode == "user_speaking":
                text = st.final_transcript or st.partial_transcript
                dec = decide_endpoint(
                    text, is_final=bool(st.final_transcript.strip()),
                    user_avg_pause_ms=st.user_avg_pause_ms,
                )
                st.endpoint_armed = True
                st.endpoint_armed_turn = st.current_turn_id
                st.endpoint_started_t = event.t
                st.endpoint_max_wait_ms = dec.max_wait_ms
                cmds = [VoiceCommand("arm_endpoint_timer", {
                    "turn_id": st.current_turn_id,
                    "wait_ms": dec.min_wait_ms,
                    "max_wait_ms": dec.max_wait_ms,
                    "elapsed_ms": 0,
                    "endpoint_state": dec.state,
                    "endpoint_reason": dec.reason,
                    "endpoint_confidence": dec.confidence,
                    "user_avg_pause_ms": st.user_avg_pause_ms,
                })]

        elif et == "timer.endpoint":
            # Le timer de silence a expiré sans reprise de parole → on tranche.
            # Garde anti-stale : on ignore un timer d'un ancien tour / désarmé.
            turn = event.get("turn_id")
            if st.endpoint_armed and turn == st.endpoint_armed_turn == st.current_turn_id:
                text = st.final_transcript or st.partial_transcript
                if st.transcription_pending and st.transcription_turn_id == turn:
                    # Whisper owns the decision until it emits a terminal final.
                    # The source guarantees such a final for text, no-speech,
                    # filtered fragments, timeout and provider errors.
                    cmds = []
                elif not text.strip() and st.interrupted_generation_id:
                    cmds = self._resume_interrupted_after_empty_transcript(
                        st.transcription_terminal_reason or "empty_transcript"
                    )
                else:
                    dec = decide_endpoint(
                        text,
                        is_final=bool(st.final_transcript.strip()),
                        pause_ms=int(event.get("pause_ms", 0)),
                        user_avg_pause_ms=st.user_avg_pause_ms,
                    )
                    pause_ms = int(event.get("pause_ms", 0))
                    if (dec.state != "turn_complete" and st.final_transcript.strip()
                            and pause_ms < st.endpoint_max_wait_ms):
                        remaining = max(50, st.endpoint_max_wait_ms - pause_ms)
                        st.set_mode("user_paused")
                        cmds = [VoiceCommand("arm_endpoint_timer", {
                            "turn_id": st.current_turn_id,
                            "wait_ms": remaining,
                            "max_wait_ms": st.endpoint_max_wait_ms,
                            "elapsed_ms": pause_ms,
                            "endpoint_state": dec.state,
                            "endpoint_reason": dec.reason,
                            "endpoint_confidence": dec.confidence,
                            "user_avg_pause_ms": st.user_avg_pause_ms,
                        })]
                    else:
                        st.endpoint_armed = False
                        st.endpoint_armed_turn = None
                        st.endpoint_started_t = None
                        cmds = self._apply_endpoint_decision(dec)

        elif et == "stt.started":
            turn = event.get("turn_id") or st.current_turn_id
            if turn == st.current_turn_id:
                st.transcription_pending = True
                st.transcription_turn_id = turn
                st.transcription_terminal_reason = None

        elif et == "stt.partial":
            text = event.get("text", "")
            st.partial_transcript = text
            if st.mode == "speaking" and text.strip():
                # Barge-in confirmé (niveau 2) : parole réelle par-dessus le TTS.
                cmds = self._interrupt_commands(hard=False, reason="barge_in_partial")
                st.set_mode("user_speaking")
                st.current_turn_id = self._new_turn_id()
            # Sinon : les partiels pilotent le TIMING uniquement.
            # Génération spéculative : OFF par défaut → aucun start_llm sur partial.

        elif et == "stt.final":
            # Les finals pilotent le CONTENU. On attend l'endpoint pour agir.
            turn = event.get("turn_id") or st.current_turn_id
            if turn != st.current_turn_id:
                return []
            st.transcription_pending = False
            st.transcription_turn_id = None
            st.transcription_terminal_reason = str(
                event.get("terminal_reason", "text" if event.get("text", "").strip() else "no_speech")
            )
            st.final_transcript = event.get("text", "")
            st.final_language = event.get("language") or None
            try:
                st.final_language_probability = float(
                    event.get("language_probability", 0.0) or 0.0
                )
            except (TypeError, ValueError):
                st.final_language_probability = 0.0
            if st.endpoint_armed and not st.final_transcript.strip():
                cmds = self._resume_interrupted_after_empty_transcript(
                    st.transcription_terminal_reason or "no_speech"
                )
            elif st.endpoint_armed and st.final_transcript.strip():
                # VAD a déjà détecté la fin de parole et Whisper vient de rendre le
                # contenu final : conclure immédiatement au lieu d'attendre un timer
                # déjà devenu redondant. Le timer en retard sera ignoré (désarmé).
                st.endpoint_armed = False
                st.endpoint_armed_turn = None
                st.endpoint_started_t = None
                dec = decide_endpoint(
                    st.final_transcript, is_final=True, pause_ms=9999,
                    user_avg_pause_ms=st.user_avg_pause_ms,
                )
                cmds = self._apply_endpoint_decision(dec)
            elif st.mode == "user_paused" and st.final_transcript.strip():
                dec = decide_endpoint(
                    st.final_transcript, is_final=True, pause_ms=9999,
                    user_avg_pause_ms=st.user_avg_pause_ms,
                )
                cmds = self._apply_endpoint_decision(dec)

        elif et in ("activation.accepted", "activation.rejected"):
            # A transcript without authorization never reaches start_llm.  VAD may
            # already have opened a provisional turn, so close it deterministically.
            turn = st.current_turn_id
            st.partial_transcript = ""
            st.final_transcript = ""
            st.final_language = None
            st.final_language_probability = 0.0
            st.transcription_pending = False
            st.transcription_turn_id = None
            st.transcription_terminal_reason = et
            st.endpoint_decision = None
            st.endpoint_armed = False
            st.endpoint_armed_turn = None
            st.endpoint_started_t = None
            st.current_turn_id = None
            st.set_mode("tool_running" if st.tool_active else "wake_listening")
            cmds = [VoiceCommand("cancel_endpoint_timer", {"turn_id": turn})]
            if et == "activation.accepted":
                cmds.append(VoiceCommand("show_status", {"state": "activated"}))
            elif st.interrupted_generation_id:
                interrupted = st.interrupted_generation_id
                st.interrupted_generation_id = None
                cmds.append(VoiceCommand("resume_interrupted_speech", {
                    "generation_id": interrupted,
                    "reason": "activation_rejected",
                }))

        elif et == "endpoint.decision":
            # Décision poussée par une source EXTERNE (endpointer dédié).
            dec = EndpointDecision(
                state=event.get("state", "uncertain"),
                confidence=float(event.get("confidence", 0.0)),
                reason=event.get("reason", ""),
            )
            st.endpoint_armed = False
            st.endpoint_armed_turn = None
            st.endpoint_started_t = None
            cmds = self._apply_endpoint_decision(dec)

        elif et == "llm.response_started":
            st.set_mode("speaking")

        elif et == "tts.chunk_ready":
            # Le chunk porte SA génération d'origine (un chunk en retard garde l'ancien
            # id) ; la couche audio (AudioOutputQueue) filtre les périmés de façon centralisée.
            gen = event.get("generation_id") or st.current_generation_id
            cmds = [VoiceCommand("play_audio", {
                "turn_id": st.current_turn_id,
                "generation_id": gen,
                "sequence": event.get("sequence", 0),
                "text": event.get("text", ""),
                "duration_ms": event.get("duration_ms", 0),
            })]

        elif et == "system.shutdown":
            self._running = False
            st.set_mode("idle")

        elif et == "playback.finished":
            if st.mode == "speaking":
                st.set_mode("tool_running" if st.tool_active else "wake_listening")
                st.current_generation_id = None

        elif et == "user.stop_word":
            # Niveau 1 : coupe immédiate, sans attendre la transcription.
            cmds = self._interrupt_commands(hard=True, reason="stop_word")

        elif et == "user.barge_in":
            cmds = self._interrupt_commands(hard=False, reason="barge_in")

        elif et == "user.cancel_action":
            # « Annule » : tenter d'annuler l'outil si sûr (l'action, pas juste la voix).
            cmds = [VoiceCommand("cancel_tool_if_safe", {"reason": "user_cancel"})]

        elif et == "tool.started":
            st.tool_active = True
            st.set_mode("tool_running")

        elif et == "tool.finished":
            st.tool_active = False
            st.set_mode("wake_listening")

        elif et == "ui.cancel":
            cmds = [VoiceCommand("cancel_tool_if_safe", {"reason": "ui_cancel"})]

        elif et == "timer.false_interruption_timeout":
            # Faux barge-in (parole détectée, transcript vide) → reprendre.
            if st.pending_barge_in:
                st.pending_barge_in = False
                cmds = [VoiceCommand("resume_playback"), VoiceCommand("show_status", {"state": "speaking"})]

        # Événements non gérés dans la thin slice : aucun effet (déterministe).
        self.emitted.extend(cmds)
        return cmds
