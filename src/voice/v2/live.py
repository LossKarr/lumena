"""Voice V2 LIVE — assembleur de la boucle conversationnelle réelle (flag-gated).

Branche les briques DÉJÀ validées (TurnManager barge-in + MicConversationSource +
VoiceRuntime + EndpointTimerService) sur le VRAI LLM via `core.chat`. Remplace le
stub echo des smoke tests. Logs console uniquement (pas d'UI/SSE), pas de
`think_and_act`/outils, aucun changement du flux legacy.

GATING : `LUMENA_VOICE_V2_LIVE=1` requis (le launcher le vérifie). Imports hardware
PARESSEUX : importer ce module ne charge ni pyaudio ni faster-whisper ; `core` est
injecté (jamais importé ici). L'assembleur `VoiceV2Live` accepte des providers
injectables → testable en pytest avec des fakes, sans micro ni modèle.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import os
import inspect
import re
import unicodedata
from typing import Any, Awaitable, Callable, List, Optional

from .events import VoiceEvent, VoiceCommand
from .turn_manager import TurnManager
from .ledger import ConversationAudioLedger
from .voice_runtime import VoiceRuntime
from .speech_coordinator import SpeechCoordinator
from .activity_narrator import PublicActivityNarrator
from .input_sources import MicConversationSource, EndpointTimerService
from .providers import LocalAudioPlayer
from .speech_normalizer import prepare_for_tts as _clean_for_speech
from .speech_planner import plan_speech, wants_full_voice_detail
from .dialogue_policy import VoiceDialoguePolicy
from .session import VoiceSessionRouter
from .work_registry import ActiveWorkRegistry, WorkNotificationTracker, classify_work_turn
from .observability import get_voice_telemetry
from .activation import VoiceActivationGate, activation_gate_from_env
from .language_guard import check_response_language
from .language_policy import LanguagePolicy
from .audio_frontend import AudioFrontend


def v2_live_enabled() -> bool:
    """Flag du branchement Voice V2 LIVE (vrai LLM). OFF par défaut."""
    return os.getenv("LUMENA_VOICE_V2_LIVE", "0").strip() == "1"


def resolve_voice_agent_max_iterations(value: Optional[int] = None) -> int:
    """Resolve the voice-only ReAct budget without allowing an infinite loop."""
    raw = value if value is not None else os.getenv(
        "LUMENA_VOICE_AGENT_MAX_ITERATIONS", "35"
    )
    try:
        return max(5, min(100, int(raw)))
    except (TypeError, ValueError):
        return 35


def resolve_barge_in_speaking_threshold(
    audio_frontend: AudioFrontend,
    *,
    energy_threshold: int,
    requested_threshold: Optional[int] = None,
) -> Optional[int]:
    """Select the echo guard used only while Lumena is speaking.

    A headset already separates playback from capture and an active AEC
    processor removes the playback reference. Raising the microphone threshold
    in either case hides real user interruptions. A hands-free session without
    AEC keeps the conservative guard because speakers can otherwise interrupt
    Lumena with her own voice.
    """
    status = audio_frontend.status
    if status.profile in {"headset", "push_to_talk"} or status.aec_active:
        return None
    if requested_threshold is not None:
        return max(int(energy_threshold), int(requested_threshold))
    return max(int(energy_threshold), int(energy_threshold * 2.7))


def _extract_text(result: Any) -> str:
    """Extrait le texte d'une réponse LLM de façon robuste.

    `core.chat` renvoie déjà `str`, mais on tolère un objet `.text`/`.content`/
    `.message` ou tout autre type (→ `str(result)`)."""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    for attr in ("text", "content", "message"):
        val = getattr(result, attr, None)
        if isinstance(val, str):
            return val
    return str(result)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _is_stop_request(text: str) -> bool:
    n = _norm(text)
    if not n:
        return False
    phrases = (
        "tu peux couper",
        "coupe",
        "coupe toi",
        "arrete",
        "stop",
        "c est bon tu peux couper",
        "merci tu peux couper",
    )
    return any(p in n for p in phrases)


def _is_cancel_request(text: str) -> bool:
    """« Annule l'action / la tâche » — distinct de « tais-toi » (qui ne coupe que la voix)."""
    n = _norm(text)
    if not n:
        return False
    phrases = (
        "annule",
        "annule la tache",
        "annule l action",
        "stop l action",
        "arrete la tache",
        "laisse tomber",
        "abandonne",
    )
    return any(p in n for p in phrases)


# Indices (dans le nom d'outil) qu'une validation utilisateur est requise (propose-only).
_CONFIRM_HINTS = ("propose", "confirm", "valider", "approuve", "write", "ecriture")


def _compact_for_voice(text: str, *, max_chars: int = 260, max_sentences: int = 2) -> str:
    """Version parlee courte d'un resultat agent.

    L'agent peut produire des listes longues et des bilans detailles utiles dans les logs.
    En vocal, on garde uniquement le signal actionnable pour ne pas bloquer le tour suivant.
    """
    return plan_speech(
        text,
        canonical_verified=True,
        max_chars=max_chars,
        max_sentences=max_sentences,
    ).spoken


def _current_time_context(now: Optional[datetime] = None) -> str:
    """Contexte temporel bref pour le LLM direct voix (évite hallucination date/heure)."""
    dt = now or datetime.now().astimezone()
    return dt.strftime("%A %d %B %Y, %H:%M:%S %Z")


class VoiceV2Live:
    """Assembleur de la boucle live. Providers injectables (réels en prod, fakes en test).

    Le `respond_fn` par défaut appelle `core.chat(text, source_channel="voice")` (voie
    rapide, 1 appel, pas d'outils). Le dispatcher est COMPOSITE (VoiceRuntime + timers
    d'endpointing), comme le smoke. État reporté en logs console via `log`.
    """
    def __init__(self, core: Any, *, vad: Any, stt: Any, tts: Any,
                 player: Any = None, language: str = "fr",
                 respond_fn: Optional[Callable[[str], Awaitable[str]]] = None,
                 min_utterance_ms: int = 300,
                 emit_partials: bool = False,
                 disable_tools: bool = True,
                 llm_mode: str = "core_chat",
                 max_response_tokens: int = 220,
                 agent_max_iterations: int = 6,
                 session_router: Optional[VoiceSessionRouter] = None,
                 language_policy: Optional[LanguagePolicy] = None,
                 audio_frontend: Optional[AudioFrontend] = None,
                 activation_gate: Optional[VoiceActivationGate] = None,
                 wake_word_provider: Any = None,
                 log: Callable[[str], None] = print):
        self.core = core
        self._log = log
        self.activation_gate = activation_gate
        self.tm = TurnManager(barge_in_on_vad=True)
        self.ledger = ConversationAudioLedger()
        self.disable_tools = disable_tools
        self.llm_mode = llm_mode
        session_mode = "agent" if llm_mode == "agent" else "chat"
        self.session = session_router or VoiceSessionRouter(core, mode=session_mode)
        language_path = (
            self.session.state_path.with_name("languages.json")
            if self.session.state_path is not None else None
        )
        self.language_policy = language_policy or LanguagePolicy(
            default_language=language,
            user_id=self.session.identity.user_id,
            channel="voice",
            state_path=language_path,
        )
        self.max_response_tokens = max_response_tokens
        self.agent_max_iterations = agent_max_iterations
        self.audio_frontend = audio_frontend or AudioFrontend.from_env()
        self.vad = vad
        self.player = player or LocalAudioPlayer(
            ledger=self.ledger, audio_frontend=self.audio_frontend
        )
        if getattr(self.player, "audio_frontend", None) is None:
            self.player.audio_frontend = self.audio_frontend
        self.runtime = VoiceRuntime(self.tm, tts, self.player,
                                    respond_fn=respond_fn or self._llm_respond,
                                    is_muted_fn=self._is_global_muted,
                                    enabled=True)
        self.speech = SpeechCoordinator(self.runtime, stop_fn=self.player.stop)
        self.activity_narrator = PublicActivityNarrator(self.speech)
        self.dialogue = VoiceDialoguePolicy()
        self.ack_text = self.dialogue.message("working", "fr")
        self.timer = EndpointTimerService(self.tm)
        telemetry = get_voice_telemetry()
        self.mic = MicConversationSource(
            vad, stt, self.tm, language=language,
            min_utterance_ms=min_utterance_ms,
            emit_partials=emit_partials,
            suppress_input_fn=self._suppress_micro_input,
            activation_gate=activation_gate,
            audio_frontend=self.audio_frontend,
            wake_word_provider=wake_word_provider,
        )
        self.tm._dispatcher = self._dispatch
        self.turns = 0
        self.interruptions = 0
        # ── État task-aware (mode agent) ──
        self._task: Optional[asyncio.Task] = None     # tâche think_and_act en cours (1 à la fois)
        self._task_id: Optional[str] = None           # id côté task_orchestrator (best-effort)
        # A paired owner may orient their active work across Web, Telegram and
        # Voice.  An unpaired/guest session remains strictly confined to its own
        # conversation.  Owner filtering prevents one local profile from seeing
        # another owner's work.
        cross_channel_owner = bool(
            self.session.identity.trusted
            and self.session.identity.user_role in {"owner", "admin"}
        )
        self.work_registry = ActiveWorkRegistry(
            getattr(core, "task_orchestrator", None),
            None if cross_channel_owner else self.session.conversation_id,
            owner_user_id=(
                self.session.identity.owner_user_id if cross_channel_owner else None
            ),
        )
        self._notification_tracker = WorkNotificationTracker(
            getattr(core, "task_orchestrator", None), self.session.conversation_id,
        )
        self._notification_task: Optional[asyncio.Task] = None
        self._conversation_tasks: List[asyncio.Task] = []
        self._confirm_announced = False               # annonce de validation déjà faite ce tour
        self.cancellations = 0
        # No periodic speech. Useful milestones are event-driven; a fixed heartbeat
        # competes with the real answer and makes the conversation feel mechanical.
        self.heartbeat_s = 0.0
        telemetry.update(
            state="idle", mode=self.session.mode, task_id=None,
            session_role=self.session.identity.user_role,
            session_trusted=self.session.identity.trusted,
            conversation_id=self.session.conversation_id,
            cloud_allowed=os.getenv("LUMENA_VOICE_CLOUD_ALLOWED", "0").strip() == "1",
            barge_in_mode=self._barge_in_mode(),
            audio_frontend=self.audio_frontend.report(),
            vad=(vad.status() if callable(getattr(vad, "status", None)) else {
                "engine": getattr(vad, "name", "unknown")
            }),
            wake_word=(
                wake_word_provider.status()
                if callable(getattr(wake_word_provider, "status", None))
                else {"available": False, "engine": "transcript_phrase_fallback"}
            ),
        )
        telemetry.register_stop_audio(self._stop_audio_now)
        telemetry.register_test_voice(
            lambda: self.speech.say(
                "Bonjour, c'est la voix locale de Lumena.",
                kind="user", turn="voice_test",
            )
        )
        telemetry.register_push_to_talk(
            activation_gate.arm
            if activation_gate is not None and activation_gate.mode == "push_to_talk"
            else None
        )
        self._telemetry_transcriber_owner = object()
        telemetry.register_transcribers(
            lambda audio: stt.transcribe(audio, language=language, fast=False),
            lambda audio: stt.transcribe_detailed(
                audio, language=language, strict=True,
            ),
            owner=self._telemetry_transcriber_owner,
        )

    def _barge_in_mode(self) -> str:
        status = getattr(self.audio_frontend, "status", None)
        if (
            getattr(status, "profile", "hands_free") == "hands_free"
            and not bool(getattr(status, "aec_active", False))
        ):
            return "guarded_no_aec"
        return "full_duplex"

    def _suppress_micro_input(self) -> bool:
        """Reserve microphone capture only for explicit browser dictation.

        Hands-free playback without server AEC is guarded by the VAD's higher
        speaking threshold. Suppressing capture here prevented genuine barge-in.
        """
        telemetry = get_voice_telemetry()
        return telemetry.is_dictation_active()

    def _stop_audio_now(self) -> None:
        self.speech.stop_audio(clear_queue=True)
        try:
            asyncio.get_running_loop().create_task(
                self.tm.emit(VoiceEvent("user.stop_word", data={"word": "ui"}))
            )
        except RuntimeError:
            pass

    def _is_global_muted(self) -> bool:
        ctx = getattr(self.core, "_svc_ctx", None)
        if ctx is not None:
            return bool(getattr(ctx, "global_mute", False))
        return bool(getattr(self.core, "global_mute", False))

    @staticmethod
    def _language_name(language: str) -> str:
        return {"fr": "français", "en": "anglais", "es": "espagnol"}.get(
            str(language or "fr").lower(), str(language or "fr")
        )

    async def _direct_llm_respond(self, text: str, *, language: str = "fr") -> str:
        """Chemin voix court : LLM direct, sans mémoire/hooks/tools/learning d'AgentService."""
        llm = getattr(self.core, "llm", None)
        if llm is None or not hasattr(llm, "chat"):
            raise RuntimeError("core.llm.chat indisponible")
        messages = [
            {
                "role": "system",
                "content": (
                    "Tu es Lumena en conversation vocale live. "
                    f"Réponds en {self._language_name(language)}, naturellement, "
                    "en une ou deux phrases courtes. "
                    "Pas de markdown, pas de liste, pas d'emoji. "
                    f"Date et heure actuelles: {_current_time_context()}. "
                    "Si l'utilisateur demande le jour ou l'heure, utilise uniquement cette valeur. "
                    "Si l'utilisateur demande une action avec outils/fichiers, dis brièvement "
                    "que ce mode voix live ne lance pas encore d'actions."
                ),
            },
            {"role": "user", "content": text},
        ]
        chat = llm.chat
        kwargs = {"temperature": 0.5, "max_tokens": self.max_response_tokens}
        if "no_upgrade" in inspect.signature(chat).parameters:
            kwargs["no_upgrade"] = True
        return _extract_text(await chat(messages, **kwargs))

    async def _repair_response_language(self, answer: str, language: str) -> str:
        llm = getattr(self.core, "llm", None)
        if llm is None or not hasattr(llm, "chat"):
            return ""
        messages = [
            {"role": "system", "content": (
                f"Réécris strictement cette réponse en {self._language_name(language)}. "
                "Conserve les faits, les noms propres et les citations demandées. "
                "Réponds uniquement avec la version corrigée, sans markdown."
            )},
            {"role": "user", "content": answer},
        ]
        try:
            repaired = _extract_text(await llm.chat(
                messages, temperature=0.1, max_tokens=self.max_response_tokens,
                no_upgrade=True,
            ))
        except TypeError:
            repaired = _extract_text(await llm.chat(
                messages, temperature=0.1, max_tokens=self.max_response_tokens,
            ))
        except Exception:
            return ""
        return repaired if check_response_language(repaired, language).compliant else ""

    async def _guard_response_language(self, answer: str, language: str) -> str:
        check = check_response_language(answer, language)
        telemetry = get_voice_telemetry()
        telemetry.update(
            response_language=language,
            generated_language=check.detected,
            generated_language_confidence=check.confidence,
            language_guard=check.reason,
        )
        if check.compliant:
            return answer
        repaired = await self._repair_response_language(answer, language)
        telemetry.update(
            language_guard="repaired" if repaired else "blocked",
            language_guard_repairs=1,
        )
        return repaired

    async def _llm_respond(self, text: str, *, language: str = "fr") -> str:
        """Vrai LLM (voie rapide). Toute erreur → réponse vide (non bloquant pour la voix)."""
        try:
            if self.llm_mode == "direct":  # benchmark explicite uniquement
                result = await self._direct_llm_respond(text, language=language)
            else:
                result = await self.session.respond_chat(text)
        except Exception as e:
            self._log(f"[voice] LLM erreur: {e}")
            return ""
        guarded = await self._guard_response_language(_extract_text(result), language)
        detailed = wants_full_voice_detail(text)
        answer = plan_speech(
            guarded,
            canonical_verified=False,
            max_chars=1200 if detailed else 620,
            max_sentences=12 if detailed else 6,
        ).spoken
        self._log(f"[voice] réponse préparée ({len(answer)} caractères)")
        return answer

    async def _dispatch(self, commands: List[VoiceCommand]) -> None:
        runtime_commands: List[VoiceCommand] = []
        timer_commands: List[VoiceCommand] = []
        for cmd in commands:
            if cmd.name == "start_stt":
                self._log(f"[voice] mode=listening (tour {cmd.data.get('turn_id')})")
            elif cmd.name == "start_llm":
                txt = cmd.data.get("text", "")
                self._log(f"[voice] transcription reçue ({len(txt)} caractères)")
                decision = self.language_policy.decide(
                    txt,
                    detected_language=cmd.data.get("language"),
                    detected_confidence=cmd.data.get("language_probability", 0.0),
                )
                get_voice_telemetry().update(
                    input_language=decision.state.detected_input_language,
                    input_language_confidence=decision.state.detected_confidence,
                    response_language=decision.response_language,
                    language_scope=decision.scope,
                    language_reason=decision.reason,
                )
                data = dict(cmd.data)
                data["language"] = decision.response_language
                cmd = VoiceCommand(cmd.name, data)
                mode_ack = self.session.handle_mode_command(txt)
                if mode_ack is not None:
                    get_voice_telemetry().update(mode=self.session.mode)
                    await self.speech.say(mode_ack, kind="user")
                    continue
                if self.session.mode == "agent":
                    # Mode task-aware : l'orchestrateur gère le tour HORS de VoiceRuntime
                    # (think_and_act en tâche de fond → acteur jamais bloqué). On NE forward
                    # PAS start_llm au runtime.
                    await self._handle_agent_turn(txt, language=decision.response_language)
                    continue
                if _is_stop_request(txt):
                    self._log("[voice] arrêt demandé par la voix")
                    self.stop()
                    asyncio.create_task(self.tm.shutdown())
                    continue
                self.turns += 1
            elif cmd.name == "stop_playback":
                self.interruptions += 1
                self._log("[voice] barge-in: voix coupée")
            elif cmd.name == "cancel_tool_if_safe":
                # « Annule la tâche » : annulation COOPÉRATIVE (jamais au milieu d'un outil).
                await self._cancel_current_task()
                # (pas d'effet runtime/timer pour cette commande)
                continue
            elif cmd.name == "arm_endpoint_timer":
                get_voice_telemetry().update(
                    endpoint_state=cmd.data.get("endpoint_state"),
                    endpoint_reason=cmd.data.get("endpoint_reason"),
                    endpoint_confidence=cmd.data.get("endpoint_confidence"),
                    endpoint_wait_ms=cmd.data.get("wait_ms"),
                    user_avg_pause_ms=cmd.data.get("user_avg_pause_ms"),
                    vad=(self.vad.status()
                         if callable(getattr(self.vad, "status", None))
                         else {"engine": getattr(self.vad, "name", "unknown")}),
                )
            runtime_commands.append(cmd)
            timer_commands.append(cmd)
        # Composite : VoiceRuntime (TTS/playback) + timers d'endpointing.
        await self.runtime.dispatch(runtime_commands)
        await self.timer.dispatch(timer_commands)

    # ── Orchestration task-aware (mode agent) ────────────────────────────────
    async def _handle_agent_turn(self, text: str, *, language: str = "fr") -> None:
        """Décide quoi faire d'un tour en mode agent : stop voix / annulation / nouvelle tâche."""
        if _is_cancel_request(text):
            # « Annule » → passe par l'événement → reducer → cancel_tool_if_safe.
            self._log("[voice] annulation demandée")
            await self.tm.emit(VoiceEvent("user.cancel_action"))
            return
        if _is_stop_request(text):
            # « Tais-toi » = couper la VOIX uniquement (la tâche continue).
            self._log("[voice] stop voix (tâche conservée)")
            self.player.stop()
            return
        work_intent = classify_work_turn(text)
        if work_intent == "status":
            await self.speech.say(
                self.work_registry.status_text(self._task_id),
                kind="status", replace_key="work_status",
            )
            return
        if work_intent in {"pause", "resume", "steer"}:
            snap, ambiguous = self.work_registry.resolve(self._task_id)
            if ambiguous:
                await self.speech.say(
                    f"J'ai {len(ambiguous)} travaux actifs. Precise lequel tu veux modifier."
                , kind="user")
                return
            if snap is None:
                await self.speech.say("Je n'ai aucun travail actif a modifier.", kind="user")
                return
            if work_intent == "pause":
                self.work_registry.pause(snap.task_id)
                await self.speech.say(
                    self.dialogue.message("pause", language), kind="status", language=language
                )
            elif work_intent == "resume":
                self.work_registry.resume(snap.task_id)
                await self.speech.say(
                    self.dialogue.message("resume", language), kind="status", language=language
                )
            else:
                self.work_registry.steer(snap.task_id, text)
                await self.speech.say(
                    self.dialogue.message("steer", language), kind="status", language=language,
                )
            return
        if self._task is not None and not self._task.done():
            # Conversation plane stays available while the execution plane works.
            task = asyncio.create_task(self._run_side_conversation(text, language=language))
            self._conversation_tasks.append(task)
            task.add_done_callback(lambda done: self._forget_side_conversation(done))
            return
        self.turns += 1
        self._confirm_announced = False
        self._task_id = self._start_voice_task_record(text)
        get_voice_telemetry().update(task_id=self._task_id, state="tool_running")
        # En mode Agent, toute action passe par ReAct/ToolRegistry. Aucun fast path
        # os.startfile/webbrowser ne peut contourner permissions, leases ou traces.
        self._task = asyncio.create_task(self._run_agent_task(
            text, language=language, detailed=wants_full_voice_detail(text)
        ))

    def _start_voice_task_record(self, text: str) -> Optional[str]:
        orchestrator = getattr(self.core, "task_orchestrator", None)
        if orchestrator is None or not hasattr(orchestrator, "start_task"):
            return None
        try:
            record = orchestrator.start_task(
                conversation_id=self.session.conversation_id,
                channel="voice",
                message_preview=text,
                metadata={"kind": "voice_turn", "objective": text, "source_channel": "voice"},
            )
            orchestrator.mark_running(record.task_id)
            return str(record.task_id)
        except Exception as exc:
            self._log(f"[voice] task registry indisponible: {exc}")
            return None

    async def _run_side_conversation(self, text: str, *, language: str = "fr") -> None:
        try:
            answer = await self.session.respond_chat(text)
            guarded = await self._guard_response_language(_extract_text(answer), language)
            plan = plan_speech(guarded, canonical_verified=False, max_sentences=3)
            if plan.spoken:
                await self.speech.say(
                    plan.spoken, kind="user", turn="conversation", language=language
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self._log(f"[voice] conversation parallele erreur: {exc}")
            await self.speech.say("Je n'ai pas pu repondre a cette question.", kind="error")

    def _forget_side_conversation(self, task: asyncio.Task) -> None:
        self._conversation_tasks = [t for t in self._conversation_tasks if t is not task]

    async def _run_agent_task(
        self, text: str, *, language: str = "fr", detailed: bool = False
    ) -> None:
        """Lance le VRAI pipeline (think_and_act) en fond + jalons vocaux + état tâche."""
        await self.tm.emit(VoiceEvent("tool.started"))
        await self.speech.say(
            self.dialogue.message("working", language), kind="status",
            replace_key="agent_ack", language=language,
        )
        hb = asyncio.ensure_future(self._heartbeat()) if self.heartbeat_s > 0 else None
        final_scheduled = {"value": False}

        def _on_final_ready(canonical_answer: str):
            final_scheduled["value"] = True
            return self._speak_agent_result(
                canonical_answer, language=language, detailed=detailed
            )

        try:
            answer = await self.session.respond_agent(
                text, step_callback=self._on_step,
                max_iterations=self.agent_max_iterations,
                final_ready_callback=_on_final_ready,
                task_orchestrator=getattr(self.core, "task_orchestrator", None),
                task_id=self._task_id)
        except asyncio.CancelledError:
            if hb is not None:
                hb.cancel()
            raise
        except SystemExit:
            # Annulation coopérative honorée par ReAct (task_orchestrator_cancel).
            if hb is not None:
                hb.cancel()
            await self.tm.emit(VoiceEvent("tool.finished"))
            await self.speech.say("J'ai arrêté la tâche.", kind="final")
            get_voice_telemetry().update(state="wake_listening", task_id=None)
            return
        except Exception as e:
            if hb is not None:
                hb.cancel()
            self._log(f"[voice] tâche erreur: {e}")
            _orch = getattr(self.core, "task_orchestrator", None)
            if self._task_id and _orch is not None:
                try:
                    _orch.mark_failed(self._task_id, str(e))
                except Exception:
                    pass
            await self.tm.emit(VoiceEvent("tool.finished"))
            await self.speech.say("Désolée, il y a eu une erreur.", kind="error")
            get_voice_telemetry().update(state="error", task_id=None, last_error=str(e))
            return
        if hb is not None:
            hb.cancel()
        await self.tm.emit(VoiceEvent("tool.finished"))
        if not final_scheduled["value"]:
            await self._speak_agent_result(
                _extract_text(answer), language=language, detailed=detailed
            )
        _orch = getattr(self.core, "task_orchestrator", None)
        if self._task_id and _orch is not None:
            try:
                _rec = _orch.get_task(self._task_id) or {}
                if _rec.get("state") not in {"done", "failed", "cancelled"}:
                    _orch.mark_done(self._task_id, result_summary=_extract_text(answer))
            except Exception:
                pass
        get_voice_telemetry().update(state="wake_listening", task_id=None)

    async def _speak_agent_result(
        self, answer: str, *, language: str = "fr", detailed: bool = False
    ) -> None:
        answer = await self._guard_response_language(answer, language)
        plan = plan_speech(
            answer,
            canonical_verified=True,
            max_chars=1200 if detailed else 620,
            max_sentences=12 if detailed else 6,
        )
        if plan.spoken:
            self._log(f"[voice] résultat vocal préparé ({len(plan.spoken)} caractères)")
            await self.speech.say(
                plan.spoken, kind="final", turn="agent_final", language=language
            )
        else:
            await self.speech.say("C'est fait.", kind="final", turn="agent_final")

    async def _heartbeat(self) -> None:
        """Optional compatibility heartbeat; disabled by default."""
        if self.heartbeat_s <= 0:
            return
        try:
            while True:
                await asyncio.sleep(self.heartbeat_s)
                await self.speech.say(
                    "Je travaille toujours.", kind="milestone", replace_key="heartbeat",
                )
        except asyncio.CancelledError:
            return

    async def _notification_loop(self) -> None:
        """Only meaningful state transitions; deduplicated by the tracker."""
        try:
            while True:
                await asyncio.sleep(1.0)
                for notice in self._notification_tracker.collect():
                    await self.speech.say(
                        notice, kind="status", turn="mission_notification",
                        replace_key="mission_notification",
                    )
        except asyncio.CancelledError:
            return

    def _on_step(self, tool_name: str, tool_args: dict, activity_event: Any = None) -> None:
        """Hook ReAct par étape (synchrone). Jalons clés seulement, pas de narration.

        Annonce UNE fois si un outil suggère une validation requise (propose-only)."""
        name = (tool_name or "").lower()
        self._log(f"[voice] étape: {tool_name}")
        if activity_event is not None:
            self._log(f"[voice] activité publique: {activity_event.trace_digest()}")
            asyncio.create_task(self.activity_narrator.narrate(activity_event))
        if not self._confirm_announced and any(h in name for h in _CONFIRM_HINTS):
            self._confirm_announced = True
            asyncio.create_task(self.speech.say(
                "Validation requise, regarde l'écran.", kind="confirmation",
            ))

    def _running_voice_task_ids(self) -> list:
        to = getattr(self.core, "task_orchestrator", None)
        if to is None or not hasattr(to, "list_all_tasks"):
            return []
        try:
            tasks = to.list_all_tasks(state_filter="running") or []
        except Exception:
            return []
        ids = []
        for t in tasks:
            tid = t.get("id") or t.get("task_id")
            if tid is None:
                continue
            chan = (t.get("source_channel") or t.get("channel") or "").lower()
            if chan in ("", "voice"):     # canal voix (ou non renseigné → on tolère)
                ids.append(tid)
        return ids

    async def _cancel_current_task(self) -> None:
        """Annulation coopérative best-effort (cf. garde-fous task_id)."""
        if self._task is None or self._task.done():
            await self.speech.say("Il n'y a rien à annuler.", kind="user")
            return
        to = getattr(self.core, "task_orchestrator", None)
        if to is None or not hasattr(to, "cancel_task"):
            await self.speech.say("Je ne peux pas annuler de manière sûre.", kind="security")
            return
        # 1) task_id explicite si on l'a et qu'il tourne encore.
        target = None
        if self._task_id and hasattr(to, "get_task"):
            try:
                rec = to.get_task(self._task_id)
            except Exception:
                rec = None
            if rec and (rec.get("state") or rec.get("status")) == "running":
                target = self._task_id
        # 2) sinon, best-effort sur la tâche voix running unique.
        if target is None:
            running = self._running_voice_task_ids()
            if len(running) == 1:
                target = running[0]
            elif len(running) == 0:
                await self.speech.say("Il n'y a rien à annuler.", kind="user")
                return
            else:
                # 3) ambigu → on refuse d'annuler à l'aveugle.
                await self.speech.say("Je ne peux pas annuler de manière sûre.", kind="security")
                return
        try:
            to.cancel_task(target)
            self.cancellations += 1
            self._log(f"[voice] cancel_task({target})")
            await self.speech.say("J'annule la tâche.", kind="status")
        except Exception as e:
            self._log(f"[voice] cancel erreur: {e}")
            await self.speech.say("Je n'ai pas pu annuler.", kind="error")

    def _runtime_pending(self) -> bool:
        tasks = list(getattr(self.runtime, "_play_tasks", [])) + \
                list(getattr(self.runtime, "_producer_tasks", [])) + \
                list(getattr(self.runtime, "_llm_tasks", {}).values()) + \
                list(self._conversation_tasks)
        speech_status = self.speech.status()
        return (
            any(not t.done() for t in tasks)
            or speech_status["queue_depth"] > 0
            or speech_status["current_kind"] is not None
        )

    async def _settle(self, settle_seconds: float) -> None:
        """Laisse la dernière réponse finir (synthèse + playback) avant l'arrêt."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + settle_seconds
        while loop.time() < deadline:
            if self.tm.queue.empty() and not self._runtime_pending():
                return
            await asyncio.sleep(0.05)

    async def run(self, audio: Any = None, settle_seconds: float = 2.0) -> None:
        """Lance l'acteur + la source micro jusqu'à fin de flux/arrêt, puis ferme proprement."""
        run_task = asyncio.create_task(self.tm.run())
        self._notification_task = asyncio.create_task(self._notification_loop())
        try:
            await self.mic.run(audio)
        finally:
            self.mic.stop()
            await self._settle(settle_seconds)   # ne pas couper la dernière réponse
            await self.tm.shutdown()
            try:
                await asyncio.wait_for(run_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            self.timer.cancel_all()
            if self._notification_task is not None:
                self._notification_task.cancel()
                await asyncio.gather(self._notification_task, return_exceptions=True)
                self._notification_task = None
            for side_task in self._conversation_tasks:
                if not side_task.done():
                    side_task.cancel()
            if self._conversation_tasks:
                await asyncio.gather(*self._conversation_tasks, return_exceptions=True)
            self._conversation_tasks = []
            get_voice_telemetry().register_stop_audio(None)
            get_voice_telemetry().register_test_voice(None)
            get_voice_telemetry().register_push_to_talk(None)
            get_voice_telemetry().clear_transcribers(
                owner=self._telemetry_transcriber_owner
            )
            get_voice_telemetry().set_dictation_active(False)
            get_voice_telemetry().update(state="stopped", task_id=None)
            await self.speech.aclose()
            await self.runtime.aclose()

    def stop(self) -> None:
        self.mic.stop()


async def run_voice_v2_live(core: Any, *, device: str = "cpu", compute: str = "int8",
                            model: str = "small",
                            energy_threshold: int = 300, hangover_ms: int = 700,
                            speaking_threshold: Optional[int] = None,
                            calibrate: bool = True, calibrate_ms: int = 800,
                            prewarm: bool = True, language: str = "fr",
                            disable_tools: bool = True,
                            llm_mode: str = "core_chat",
                            agent_max_iterations: Optional[int] = None,
                            partial_every_ms: Optional[int] = None,
                            input_device_index: Optional[int] = None,
                            log: Callable[[str], None] = print) -> None:
    """Entrée RÉELLE : construit les providers hardware (lazy) et lance la boucle live.

    Réservé au chemin gated (le launcher vérifie `LUMENA_VOICE_V2_LIVE=1`). Construit
    le STT Whisper (device/compute explicites pour éviter les defaults cuda), la VAD
    micro avec self-voice guard, le TTS local Piper. Aucun branchement legacy/UI.
    """
    # Imports hardware PARESSEUX (jamais au niveau module → isolation préservée).
    from .providers import (  # noqa: PLC0415
        RealVADProvider, RealSTTAdapter, LocalTTSAdapter,
        SileroSpeechProbability,
        LocalWakeWordProvider,
    )
    from src.voice.stt import LumenaSTT  # noqa: PLC0415

    audio_frontend = AudioFrontend.from_env()
    spk = resolve_barge_in_speaking_threshold(
        audio_frontend,
        energy_threshold=energy_threshold,
        requested_threshold=speaking_threshold,
    )
    # is_speaking_fn lié plus tard au TurnManager créé dans VoiceV2Live → placeholder mutable.
    state_ref: dict = {"tm": None}
    if input_device_index is None:
        try:
            _raw_input_device = os.getenv("LUMENA_VOICE_INPUT_DEVICE", "").strip()
            input_device_index = int(_raw_input_device) if _raw_input_device else None
        except (TypeError, ValueError):
            input_device_index = None
    if partial_every_ms is None:
        try:
            partial_every_ms = int(os.getenv("LUMENA_STT_PARTIAL_EVERY_MS", "0"))
        except (TypeError, ValueError):
            partial_every_ms = 0
    partial_every_ms = max(0, min(5000, partial_every_ms))
    vad_engine = os.getenv("LUMENA_VOICE_VAD_ENGINE", "auto").strip().lower()
    if vad_engine not in {"auto", "energy", "silero"}:
        vad_engine = "auto"
    probability_fn = None
    vad_fallback_reason = None
    if vad_engine in {"auto", "silero"}:
        probability_fn, silero_status = SileroSpeechProbability.load()
        vad_fallback_reason = silero_status.reason
    try:
        probability_threshold = float(
            os.getenv("LUMENA_VOICE_VAD_SPEECH_THRESHOLD", "0.5")
        )
    except (TypeError, ValueError):
        probability_threshold = 0.5
    vad = RealVADProvider(energy_threshold=energy_threshold, silence_hangover_ms=hangover_ms,
                          speaking_threshold=spk,
                          speaking_guard_enabled=spk is not None,
                          partial_every_ms=partial_every_ms,
                          input_device_index=input_device_index,
                          speech_probability_fn=probability_fn,
                          speech_probability_threshold=probability_threshold,
                          is_speaking_fn=lambda: getattr(state_ref["tm"], "state", None)
                          and state_ref["tm"].state.mode == "speaking")
    if probability_fn is None and vad_engine in {"auto", "silero"}:
        vad.vad_fallback_reason = vad_fallback_reason
    # LOT VOICE-3 : `model_size` etait le seul des trois a ne pas etre transmis, si
    # bien que le STT retombait sur son defaut fige a l'import (`small`) malgre un
    # `.env` demandant `large-v3-turbo`.
    stt = RealSTTAdapter(
        stt=LumenaSTT(device=device, compute_type=compute, model_size=model)
    )
    tts = LocalTTSAdapter()
    wake_word = LocalWakeWordProvider.from_env()

    initial_mode = "agent" if llm_mode == "agent" else "chat"
    session = VoiceSessionRouter.for_product(core, mode=initial_mode)
    resolved_agent_iterations = resolve_voice_agent_max_iterations(agent_max_iterations)
    live = VoiceV2Live(core, vad=vad, stt=stt, tts=tts, language=language,
                       disable_tools=disable_tools, llm_mode=llm_mode,
                       emit_partials=partial_every_ms > 0,
                       agent_max_iterations=resolved_agent_iterations,
                       session_router=session,
                       audio_frontend=audio_frontend,
                       activation_gate=activation_gate_from_env(),
                       wake_word_provider=wake_word, log=log)
    state_ref["tm"] = live.tm   # le guard voit maintenant le bon TurnManager
    get_voice_telemetry().update(vad=vad.status())

    if calibrate:
        log(f"[voice] calibration {calibrate_ms} ms (reste silencieux)...")
        res = await vad.calibrate(duration_ms=calibrate_ms)
        log(f"[voice] calibration: {res}")
    if prewarm:
        log("[voice] prewarm STT/TTS...")
        log(f"[voice] STT: {await stt.prewarm()}")
        log(f"[voice] TTS: {await tts.prewarm(voice=live.runtime.voice_profile)}")

    log("[voice] LIVE prêt — parle (Ctrl+C pour arrêter).")
    await live.run()
