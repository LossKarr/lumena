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
from .input_sources import MicConversationSource, EndpointTimerService
from .providers import LocalAudioPlayer
from .speech_normalizer import normalize_for_speech


def v2_live_enabled() -> bool:
    """Flag du branchement Voice V2 LIVE (vrai LLM). OFF par défaut."""
    return os.getenv("LUMENA_VOICE_V2_LIVE", "0").strip() == "1"


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


# ── Routeur rapide voix : éviter le gros agent ReAct pour le trivial ──────────
# Verbes d'ouverture d'app/site.
_OPEN_VERBS = ("ouvre", "ouvrir", "lance", "lancer", "demarre", "demarrer",
               "mets", "met", "va sur", "affiche", "ouvre moi")

# Cibles « lançables » directement (sans agent). clé normalisée → (nom parlé, cible).
# http(s) → navigateur par défaut ; sinon URI/protocole (os.startfile sous Windows).
_APP_TARGETS = {
    "google": ("Google", "https://www.google.com"),
    "youtube": ("YouTube", "https://www.youtube.com"),
    "gmail": ("Gmail", "https://mail.google.com"),
    "spotify": ("Spotify", "spotify:"),
    "xbox": ("Xbox", "xbox:"),
    "discord": ("Discord", "discord:"),
    "chrome": ("le navigateur", "https://www.google.com"),
    "firefox": ("le navigateur", "https://www.google.com"),
    "navigateur": ("le navigateur", "https://www.google.com"),
}

# Indices qu'une demande nécessite VRAIMENT l'agent (outils, multi-étapes, fichiers, code…).
_COMPLEX_HINTS = (
    "cherche", "recherche", "fichier", "dossier", "code", "ecris", "ecrire", "redige",
    "genere", "generer", "site", "navigue", "clique", "telecharge", "analyse", "resume",
    "resumer", "corrige", "debug", "installe", "supprime", "modifie", "deploie", "git",
    "commit", "screenshot", "capture", "base de donnee", "email", "mail", "envoie",
    "repo", "projet", "scrape", "remplis", "formulaire", "compile", "execute", "build",
)


def _route_voice(text: str) -> tuple:
    """Classe un tour voix → ('launch', clé) | ('direct', None) | ('agent', None).

    Conservateur : tout signal de complexité (outils/fichiers/multi-étapes) escalade
    vers l'agent. Le reste reste rapide : lancement d'app connu, sinon LLM direct court.
    """
    n = _norm(text)
    if not n:
        return ("direct", None)
    # 1) Complexité réelle → agent (priorité : « ouvre google ET cherche X » = agent).
    if any(h in n for h in _COMPLEX_HINTS):
        return ("agent", None)
    # 2) Lancement direct d'une app/site connu (jamais sur un fichier/dossier).
    if "fichier" not in n and "dossier" not in n:
        for key in _APP_TARGETS:
            if re.search(rf"\b{re.escape(key)}\b", n):
                if n.strip() == key or any(v in n for v in _OPEN_VERBS):
                    return ("launch", key)
    # 3) Sinon : question/conversation simple → LLM direct court (heure, date, qui es-tu…).
    return ("direct", None)


def _default_launch(target: str) -> None:
    """Lancement best-effort d'une app/site (jamais bloquant longtemps, jamais d'échec dur)."""
    import webbrowser  # noqa: PLC0415
    import os as _os   # noqa: PLC0415
    if target.startswith("http"):
        webbrowser.open(target)
        return
    startfile = getattr(_os, "startfile", None)   # Windows seulement
    if callable(startfile):
        startfile(target)
    else:
        webbrowser.open(target)


# Emojis / pictogrammes : Piper les lit mal (nom unicode ou bruit) → on les retire.
_RE_EMOJI = re.compile(
    "["
    "\U0001F300-\U0001FAFF"   # symboles & pictogrammes, emojis
    "\U00002600-\U000026FF"   # symboles divers
    "\U00002700-\U000027BF"   # dingbats
    "\U0001F1E6-\U0001F1FF"   # drapeaux régionaux
    "\U0000FE00-\U0000FE0F"   # sélecteurs de variation
    "]+",
    flags=re.UNICODE,
)


def _clean_for_speech(text: str) -> str:
    """Rend un texte LLM PARLABLE par Piper (le contenu reste, le formatage part).

    - supprime l'imprononçable (code/URLs/chemins…) via `normalize_for_speech` ;
    - retire le markdown (**, *, `, #, >), les puces de liste, les emojis ;
    - normalise apostrophes/guillemets typographiques et les tirets séparateurs
      (« - », «—», «–») en pauses naturelles ;
    - aplatit les sauts de ligne en phrases.
    Les mots composés (« peut-être », « mot-clé ») gardent leur trait d'union.
    """
    s = normalize_for_speech(text or "").spoken
    # NFC : accents PRÉCOMPOSÉS. Sinon un « à » décomposé (a + accent combinant) est lu
    # « a » puis l'accent à part → prononciation parasite des lettres accentuées.
    s = unicodedata.normalize("NFC", s)
    # Typographie + guillemets droits → ASCII/rien (Piper bute sur ' " « » et lit «guillemet»).
    for a, b in (("’", "'"), ("‘", "'"), ("“", ""), ("”", ""), ('"', ""),
                 ("«", ""), ("»", ""), ("…", "...")):
        s = s.replace(a, b)
    # Puces de liste en début de ligne (-, *, •) → rien.
    s = re.sub(r"(?m)^\s*[-*•]\s+", "", s)
    # Markdown emphase / titres / inline code / citations.
    s = re.sub(r"[*_`#>]+", " ", s)
    # Tirets SÉPARATEURS (entourés d'espaces) ou cadratins → virgule (pause).
    s = re.sub(r"\s+[-–—]\s+", ", ", s)
    s = s.replace("—", ", ").replace("–", ", ")
    # Emojis.
    s = _RE_EMOJI.sub("", s)
    # « / » → pause (sinon Piper lit « slash »).
    s = re.sub(r"\s*/\s*", ", ", s)
    # Sauts de ligne → pauses de phrase ; espaces/ponctuation propres.
    s = re.sub(r"\n{2,}", ". ", s)
    s = re.sub(r"\n", ". ", s)
    s = re.sub(r"\s+([,.!?;:])", r"\1", s)
    # Ponctuations collées par les substitutions (ex. « écran., page » → « écran, page »).
    s = re.sub(r"\.\s*,", ", ", s)
    s = re.sub(r"([?!:;])\s*\.", r"\1", s)   # évite « ? . » après une fin de ligne
    s = re.sub(r"\.{4,}", "...", s)
    s = re.sub(r"(?:,\s*){2,}", ", ", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"^[\s,]+", "", s)        # vire séparateurs en tête, garde la ponctuation finale
    return s.strip()


def _compact_for_voice(text: str, *, max_chars: int = 260, max_sentences: int = 2) -> str:
    """Version parlee courte d'un resultat agent.

    L'agent peut produire des listes longues et des bilans detailles utiles dans les logs.
    En vocal, on garde uniquement le signal actionnable pour ne pas bloquer le tour suivant.
    """
    s = _clean_for_speech(text)
    if not s:
        return ""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", s) if p.strip()]
    if parts:
        s = " ".join(parts[:max_sentences])
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars].rsplit(" ", 1)[0].strip(" ,;:")
    return f"{cut}."


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
                 disable_tools: bool = True,
                 llm_mode: str = "direct",
                 max_response_tokens: int = 220,
                 agent_max_iterations: int = 6,
                 launch_fn: Optional[Callable[[str], Any]] = None,
                 log: Callable[[str], None] = print):
        self.core = core
        self._log = log
        self._launch_fn = launch_fn or _default_launch
        self.tm = TurnManager(barge_in_on_vad=True)
        self.ledger = ConversationAudioLedger()
        self.disable_tools = disable_tools
        self.llm_mode = llm_mode
        self.max_response_tokens = max_response_tokens
        self.agent_max_iterations = agent_max_iterations
        self.player = player or LocalAudioPlayer(ledger=self.ledger)
        self.runtime = VoiceRuntime(self.tm, tts, self.player,
                                    respond_fn=respond_fn or self._llm_respond, enabled=True)
        self.timer = EndpointTimerService(self.tm)
        self.mic = MicConversationSource(vad, stt, self.tm, language=language,
                                         min_utterance_ms=min_utterance_ms)
        self.tm._dispatcher = self._dispatch
        self.turns = 0
        self.interruptions = 0
        # ── État task-aware (mode agent) ──
        self._task: Optional[asyncio.Task] = None     # tâche think_and_act en cours (1 à la fois)
        self._task_id: Optional[str] = None           # id côté task_orchestrator (best-effort)
        self._confirm_announced = False               # annonce de validation déjà faite ce tour
        self.cancellations = 0
        self.ack_text = "Je m'en occupe."
        self.heartbeat_s = 8.0

    async def _direct_llm_respond(self, text: str) -> str:
        """Chemin voix court : LLM direct, sans mémoire/hooks/tools/learning d'AgentService."""
        llm = getattr(self.core, "llm", None)
        if llm is None or not hasattr(llm, "chat"):
            raise RuntimeError("core.llm.chat indisponible")
        messages = [
            {
                "role": "system",
                "content": (
                    "Tu es Lumena en conversation vocale live. "
                    "Réponds en français, naturellement, en une ou deux phrases courtes. "
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

    async def _llm_respond(self, text: str) -> str:
        """Vrai LLM (voie rapide). Toute erreur → réponse vide (non bloquant pour la voix)."""
        try:
            if self.llm_mode == "direct":
                result = await self._direct_llm_respond(text)
            elif not self.disable_tools:
                result = await self.core.chat(text, source_channel="voice")
            else:
                sentinel = object()
                old_tool_system = getattr(self.core, "tool_system", sentinel)
                if old_tool_system is not sentinel:
                    self.core.tool_system = None
                try:
                    result = await self.core.chat(text, source_channel="voice")
                finally:
                    if old_tool_system is not sentinel:
                        self.core.tool_system = old_tool_system
        except Exception as e:
            self._log(f"[voice] LLM erreur: {e}")
            return ""
        answer = _clean_for_speech(_extract_text(result))
        self._log(f"[voice] réponse  : {answer!r}")
        return answer

    async def _dispatch(self, commands: List[VoiceCommand]) -> None:
        runtime_commands: List[VoiceCommand] = []
        timer_commands: List[VoiceCommand] = []
        for cmd in commands:
            if cmd.name == "start_stt":
                self._log(f"[voice] mode=listening (tour {cmd.data.get('turn_id')})")
            elif cmd.name == "start_llm":
                txt = cmd.data.get("text", "")
                self._log(f"[voice] transcript: {txt!r}")
                if self.llm_mode == "agent":
                    # Mode task-aware : l'orchestrateur gère le tour HORS de VoiceRuntime
                    # (think_and_act en tâche de fond → acteur jamais bloqué). On NE forward
                    # PAS start_llm au runtime.
                    await self._handle_agent_turn(txt)
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
            runtime_commands.append(cmd)
            timer_commands.append(cmd)
        # Composite : VoiceRuntime (TTS/playback) + timers d'endpointing.
        await self.runtime.dispatch(runtime_commands)
        await self.timer.dispatch(timer_commands)

    # ── Orchestration task-aware (mode agent) ────────────────────────────────
    async def _handle_agent_turn(self, text: str) -> None:
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
        if self._task is not None and not self._task.done():
            # Une seule tâche voix à la fois.
            await self.runtime.speak("Je termine d'abord ce que je fais.")
            return
        self.turns += 1
        self._confirm_announced = False
        self._task_id = None
        # ── ROUTEUR RAPIDE : agent SEULEMENT si nécessaire ──
        route, arg = _route_voice(text)
        self._log(f"[voice] route={route}" + (f" ({arg})" if arg else ""))
        if route == "launch":
            self._task = asyncio.create_task(self._do_launch(arg))
        elif route == "direct":
            self._task = asyncio.create_task(self._do_direct(text))
        else:
            self._task = asyncio.create_task(self._run_agent_task(text))

    async def _do_launch(self, key: str) -> None:
        """Lance une app/site connu SANS agent. Dit « je lance… » (jamais « c'est ouvert »)."""
        name, target = _APP_TARGETS[key]
        await self.runtime.speak(f"Je lance {name}.")     # présent : aucune affirmation de succès
        try:
            res = self._launch_fn(target)
            if inspect.isawaitable(res):
                await res
            self._log(f"[voice] launch {key} → {target}")
        except Exception as e:
            self._log(f"[voice] launch erreur ({key}): {e}")
            await self.runtime.speak("Je n'ai pas réussi à le lancer.")

    async def _do_direct(self, text: str) -> None:
        """Réponse conversationnelle/simple via LLM direct court (pas d'agent), compactée."""
        try:
            answer = await self._direct_llm_respond(text)
        except Exception as e:
            self._log(f"[voice] direct LLM erreur: {e}")
            await self.runtime.speak("Désolée, je n'ai pas pu répondre.")
            return
        spoken = _compact_for_voice(answer)
        if spoken:
            self._log(f"[voice] réponse : {spoken!r}")
            await self.runtime.speak(spoken)

    async def _run_agent_task(self, text: str) -> None:
        """Lance le VRAI pipeline (think_and_act) en fond + jalons vocaux + état tâche."""
        await self.tm.emit(VoiceEvent("tool.started"))
        await self.runtime.speak(self.ack_text)
        hb = asyncio.ensure_future(self._heartbeat())
        try:
            answer = await self.core.think_and_act(
                text, source_channel="voice", step_callback=self._on_step,
                max_iterations=self.agent_max_iterations)
        except asyncio.CancelledError:
            hb.cancel()
            raise
        except SystemExit:
            # Annulation coopérative honorée par ReAct (task_orchestrator_cancel).
            hb.cancel()
            await self.tm.emit(VoiceEvent("tool.finished"))
            await self.runtime.speak("J'ai arrêté la tâche.")
            return
        except Exception as e:
            hb.cancel()
            self._log(f"[voice] tâche erreur: {e}")
            await self.tm.emit(VoiceEvent("tool.finished"))
            await self.runtime.speak("Désolée, il y a eu une erreur.")
            return
        hb.cancel()
        await self.tm.emit(VoiceEvent("tool.finished"))
        spoken = _compact_for_voice(_extract_text(answer))
        if spoken:
            self._log(f"[voice] résultat : {spoken!r}")
            await self.runtime.speak(spoken)
        else:
            await self.runtime.speak("C'est fait.")

    async def _heartbeat(self) -> None:
        """« Je travaille toujours » périodique tant que la tâche tourne (jalon clé)."""
        try:
            while True:
                await asyncio.sleep(self.heartbeat_s)
                await self.runtime.speak("Je travaille toujours.")
        except asyncio.CancelledError:
            return

    def _on_step(self, tool_name: str, tool_args: dict) -> None:
        """Hook ReAct par étape (synchrone). Jalons clés seulement, pas de narration.

        Annonce UNE fois si un outil suggère une validation requise (propose-only)."""
        name = (tool_name or "").lower()
        self._log(f"[voice] étape: {tool_name}")
        if not self._confirm_announced and any(h in name for h in _CONFIRM_HINTS):
            self._confirm_announced = True
            asyncio.create_task(self.runtime.speak("Validation requise, regarde l'écran."))

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
            await self.runtime.speak("Il n'y a rien à annuler.")
            return
        to = getattr(self.core, "task_orchestrator", None)
        if to is None or not hasattr(to, "cancel_task"):
            await self.runtime.speak("Je ne peux pas annuler de manière sûre.")
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
                await self.runtime.speak("Il n'y a rien à annuler.")
                return
            else:
                # 3) ambigu → on refuse d'annuler à l'aveugle.
                await self.runtime.speak("Je ne peux pas annuler de manière sûre.")
                return
        try:
            to.cancel_task(target)
            self.cancellations += 1
            self._log(f"[voice] cancel_task({target})")
            await self.runtime.speak("J'annule la tâche.")
        except Exception as e:
            self._log(f"[voice] cancel erreur: {e}")
            await self.runtime.speak("Je n'ai pas pu annuler.")

    def _runtime_pending(self) -> bool:
        tasks = list(getattr(self.runtime, "_play_tasks", [])) + \
                list(getattr(self.runtime, "_producer_tasks", []))
        return any(not t.done() for t in tasks)

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
            await self.runtime.aclose()

    def stop(self) -> None:
        self.mic.stop()


async def run_voice_v2_live(core: Any, *, device: str = "cpu", compute: str = "int8",
                            energy_threshold: int = 300, hangover_ms: int = 700,
                            speaking_threshold: Optional[int] = None,
                            calibrate: bool = True, calibrate_ms: int = 800,
                            prewarm: bool = True, language: str = "fr",
                            disable_tools: bool = True,
                            llm_mode: str = "direct",
                            agent_max_iterations: int = 6,
                            log: Callable[[str], None] = print) -> None:
    """Entrée RÉELLE : construit les providers hardware (lazy) et lance la boucle live.

    Réservé au chemin gated (le launcher vérifie `LUMENA_VOICE_V2_LIVE=1`). Construit
    le STT Whisper (device/compute explicites pour éviter les defaults cuda), la VAD
    micro avec self-voice guard, le TTS local Piper. Aucun branchement legacy/UI.
    """
    # Imports hardware PARESSEUX (jamais au niveau module → isolation préservée).
    from .providers import RealVADProvider, RealSTTAdapter, LocalTTSAdapter  # noqa: PLC0415
    from src.voice.stt import LumenaSTT  # noqa: PLC0415

    spk = speaking_threshold if speaking_threshold is not None else int(energy_threshold * 2.7)
    # is_speaking_fn lié plus tard au TurnManager créé dans VoiceV2Live → placeholder mutable.
    state_ref: dict = {"tm": None}
    vad = RealVADProvider(energy_threshold=energy_threshold, silence_hangover_ms=hangover_ms,
                          speaking_threshold=spk,
                          is_speaking_fn=lambda: getattr(state_ref["tm"], "state", None)
                          and state_ref["tm"].state.mode == "speaking")
    stt = RealSTTAdapter(stt=LumenaSTT(device=device, compute_type=compute))
    tts = LocalTTSAdapter()

    live = VoiceV2Live(core, vad=vad, stt=stt, tts=tts, language=language,
                       disable_tools=disable_tools, llm_mode=llm_mode,
                       agent_max_iterations=agent_max_iterations, log=log)
    state_ref["tm"] = live.tm   # le guard voit maintenant le bon TurnManager

    if calibrate:
        log(f"[voice] calibration {calibrate_ms} ms (reste silencieux)...")
        res = await vad.calibrate(duration_ms=calibrate_ms)
        log(f"[voice] calibration: {res}")
    if prewarm:
        log("[voice] prewarm STT/TTS...")
        log(f"[voice] STT: {await stt.prewarm()}")
        log(f"[voice] TTS: {await tts.prewarm()}")

    log("[voice] LIVE prêt — parle (Ctrl+C pour arrêter).")
    await live.run()
