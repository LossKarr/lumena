"""Voice V2 LIVE — assembleur testé SANS hardware ni vrai LLM.

Prouve : la boucle live appelle `core.chat` avec le transcript, l'extraction de texte
est robuste, le flag est OFF par défaut, et le module n'importe aucune lib hardware
au niveau module (les providers réels restent paresseux).
"""
import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.voice.v2 import (
    VoiceV2Live, v2_live_enabled, VoiceEvent,
    FakeTTSProvider, LocalAudioPlayer, ConversationAudioLedger,
)
from src.voice.v2.live import (
    _extract_text, _is_stop_request, _is_cancel_request, _current_time_context,
    _clean_for_speech, _compact_for_voice, resolve_voice_agent_max_iterations,
)


# ── Nettoyage pour la voix (markdown / emojis / tirets / apostrophes) ──────────
def test_clean_for_speech_strips_markdown_emoji_and_bullets():
    raw = ("**C'est pour quoi comme type de site ?** 😊\n"
           "- Portfolio / CV\n"
           "- E-commerce (avec paiements) 🚀")
    out = _clean_for_speech(raw)
    assert "*" not in out and "#" not in out          # markdown retiré
    assert "😊" not in out and "🚀" not in out          # emojis retirés
    assert "Portfolio" in out and "E-commerce" in out  # contenu conservé
    assert "\n" not in out                             # aplati en phrases


def test_clean_for_speech_normalizes_separators_and_apostrophes():
    raw = "Oui, chef — d’accord – je m’occupe du reste"
    out = _clean_for_speech(raw)
    assert "—" not in out and "–" not in out   # tirets cadratins → pauses
    assert "’" not in out and "'" in out            # apostrophe typographique → ASCII
    assert "occupe du reste" in out


def test_clean_for_speech_keeps_compound_hyphen_words():
    assert "peut-être" in _clean_for_speech("c'est peut-être bon")
    assert "mot-clé" in _clean_for_speech("donne un mot-clé")


def test_clean_for_speech_precomposes_accents_nfc():
    raw = "déjà prête"               # forme cible NFC
    decomposed = "déjà prête"      # mêmes mots en NFD (accents combinants)
    import unicodedata
    decomposed = unicodedata.normalize("NFD", raw)   # vraie forme NFD (accents séparés)
    assert decomposed != raw                          # bien décomposé au départ
    out = _clean_for_speech(decomposed)
    assert all(not unicodedata.combining(ch) for ch in out)   # aucun accent combinant restant
    assert out == raw                                # accents recomposés → lecture correcte


def test_clean_for_speech_strips_ascii_quotes_and_glued_punctuation():
    raw = 'boutons "Recherche Google" et écran., page d\'accueil'
    out = _clean_for_speech(raw)
    assert '"' not in out                            # guillemets droits retirés
    assert "., " not in out and "écran, page" in out  # ponctuation collée nettoyée


# ── Extraction robuste du texte LLM ────────────────────────────────────────────
def test_compact_for_voice_keeps_agent_results_short():
    raw = (
        "C'est fait, l'application Xbox est ouverte sur ton PC. "
        "J'ai lance la commande start shell AppsFolder. "
        "Details: navigation, verification, captures, logs et autres informations longues."
    )
    out = _compact_for_voice(raw, max_chars=90, max_sentences=2)
    assert len(out) <= 91
    assert out.count(".") <= 2
    assert "Details" not in out


def test_extract_text_handles_str_object_and_none():
    assert _extract_text("bonjour") == "bonjour"
    assert _extract_text(None) == ""
    assert _extract_text(type("R", (), {"text": "via text"})()) == "via text"
    assert _extract_text(type("R", (), {"content": "via content"})()) == "via content"
    assert _extract_text(123) == "123"          # fallback str()


def test_stop_request_detection():
    assert _is_stop_request("non c'est bon merci tu peux couper")
    assert _is_stop_request("STOP")
    assert not _is_stop_request("comment tu vas")


def test_current_time_context_is_injected_format():
    ctx = _current_time_context(datetime(2026, 5, 26, 17, 22, 30, tzinfo=timezone.utc))
    assert "2026" in ctx and "17:22:30" in ctx


# ── Flag OFF par défaut ─────────────────────────────────────────────────────────
def test_v2_live_flag_off_by_default(monkeypatch):
    monkeypatch.delenv("LUMENA_VOICE_V2_LIVE", raising=False)
    assert v2_live_enabled() is False


# ── Boucle live : core.chat appelé avec le transcript ──────────────────────────
class _ScriptedVAD:
    SAMPLE_RATE = 16000
    SAMPLE_WIDTH = 2
    last_utterance = b"\x00" * 12000   # 375 ms > min_utterance

    async def stream(self, audio=None):
        from src.voice.v2 import VADEvent
        yield VADEvent(kind="speech_started", t=0)
        yield VADEvent(kind="speech_ended", t=60)

    def stop(self):
        pass


class _FakeSTT:
    def is_available(self): return True
    async def transcribe(self, audio, *, language="fr", fast=True):
        return "ouvre le fichier"


class _FakeCore:
    def __init__(self):
        self.calls = []
        self.tool_system = object()
        self.tool_system_seen = []
        self.llm = _FakeLLM()
    async def chat(self, message, source_channel="web", sender=None):
        self.tool_system_seen.append(self.tool_system)
        self.calls.append((message, source_channel))
        return "voici ta réponse"


class _FakeLLM:
    def __init__(self):
        self.calls = []
    async def chat(self, messages, temperature=0.7, max_tokens=65536, no_upgrade=False, **kwargs):
        self.calls.append((messages, {
            "temperature": temperature,
            "max_tokens": max_tokens,
            "no_upgrade": no_upgrade,
            **kwargs,
        }))
        return "réponse courte"


@pytest.mark.asyncio
async def test_live_uses_official_core_chat_by_default():
    core = _FakeCore()
    played = []
    player = LocalAudioPlayer(ledger=ConversationAudioLedger(),
                              play_fn=lambda x: _async_append(played, x), stop_fn=lambda: None)
    logs = []
    live = VoiceV2Live(core, vad=_ScriptedVAD(), stt=_FakeSTT(), tts=FakeTTSProvider(),
                       player=player, log=logs.append)
    await live.run()
    assert core.calls == [("ouvre le fichier", "voice")]
    assert core.llm.calls == []
    assert live.turns == 1
    # La réponse a été synthétisée puis jouée (segments du FakeTTS).
    assert played and any("voici ta réponse" in p for p in played)


@pytest.mark.asyncio
async def test_live_core_chat_never_mutates_global_tools():
    core = _FakeCore()
    player = LocalAudioPlayer(ledger=ConversationAudioLedger(),
                              play_fn=lambda x: _noop(), stop_fn=lambda: None)
    live = VoiceV2Live(core, vad=_ScriptedVAD(), stt=_FakeSTT(), tts=FakeTTSProvider(),
                       player=player, llm_mode="core_chat")
    await live.run()
    assert core.calls == [("ouvre le fichier", "voice")]
    assert core.tool_system_seen == [core.tool_system]
    assert core.tool_system is not None


@pytest.mark.asyncio
async def test_direct_llm_requires_explicit_benchmark_mode():
    core = _FakeCore()
    played = []
    player = LocalAudioPlayer(ledger=ConversationAudioLedger(),
                              play_fn=lambda x: _async_append(played, x), stop_fn=lambda: None)
    live = VoiceV2Live(core, vad=_ScriptedVAD(), stt=_FakeSTT(), tts=FakeTTSProvider(),
                       player=player, llm_mode="direct")
    await live.run()
    assert core.calls == []
    assert len(core.llm.calls) == 1
    assert played


@pytest.mark.asyncio
async def test_live_llm_error_is_non_blocking():
    class _BoomCore:
        async def chat(self, message, source_channel="web", sender=None):
            raise RuntimeError("LLM down")
    logs = []
    live = VoiceV2Live(_BoomCore(), vad=_ScriptedVAD(), stt=_FakeSTT(),
                       tts=FakeTTSProvider(),
                       player=LocalAudioPlayer(play_fn=lambda x: _noop(), stop_fn=lambda: None),
                       log=logs.append)
    await live.run()      # ne doit pas lever
    assert any("LLM erreur" in m for m in logs)


@pytest.mark.asyncio
async def test_live_stop_phrase_stops_without_calling_llm():
    class _StopSTT:
        def is_available(self): return True
        async def transcribe(self, audio, *, language="fr", fast=True):
            return "merci tu peux couper"

    core = _FakeCore()
    logs = []
    live = VoiceV2Live(core, vad=_ScriptedVAD(), stt=_StopSTT(),
                       tts=FakeTTSProvider(),
                       player=LocalAudioPlayer(play_fn=lambda x: _noop(), stop_fn=lambda: None),
                       log=logs.append)
    await live.run()
    assert core.calls == []
    assert any("arrêt demandé" in m for m in logs)


def _async_append(lst, x):
    async def _():
        lst.append(x)
    return _()


def _noop():
    async def _():
        return None
    return _()


# ── Mode AGENT (task-aware) : think_and_act en fond + jalons + annulation ──────
class _FakeTaskOrch:
    def __init__(self, running=None):
        self.running = running or []
        self.cancelled = []
    def list_all_tasks(self, limit=200, state_filter=None):
        if state_filter:
            return [t for t in self.running if t.get("state") == state_filter]
        return list(self.running)
    def get_task(self, task_id):
        return next((t for t in self.running if t.get("id") == task_id), None)
    def cancel_task(self, task_id):
        self.cancelled.append(task_id)
        return {"ok": True}
    def is_cancel_requested(self, task_id):
        return task_id in self.cancelled


class _FakeAgentCore:
    def __init__(self, result="J'ai trouvé trois fichiers.", step_tools=None,
                 error=None, hang=None, task_orchestrator=None):
        self.think_calls = []
        self.chat_calls = []                      # flux texte/AgentService : NE doit PAS être touché
        self.result = result
        self.step_tools = step_tools or []
        self.error = error
        self.hang = hang
        self.task_orchestrator = task_orchestrator
        self.llm = _FakeLLM()                     # pour la voie « direct » du routeur
    async def chat(self, message, source_channel="web", sender=None):
        self.chat_calls.append((message, source_channel))
        return "réponse texte"
    async def think_and_act(self, query, source_channel="web", sender=None,
                            step_callback=None, max_iterations=None):
        self.think_calls.append((query, source_channel, max_iterations))
        for tool in self.step_tools:
            if step_callback:
                step_callback(tool, {})
        if self.hang is not None:
            await self.hang.wait()
        if self.error:
            raise RuntimeError(self.error)
        return self.result


def _build_agent_live(core, **kw):
    player = LocalAudioPlayer(ledger=ConversationAudioLedger(),
                              play_fn=lambda x: _noop(), stop_fn=lambda: None)
    live = VoiceV2Live(core, vad=_ScriptedVAD(), stt=_FakeSTT(), tts=FakeTTSProvider(),
                       player=player, llm_mode="agent", log=lambda m: None, **kw)
    live.heartbeat_s = 999      # pas de heartbeat parasite dans les tests
    spoken = []
    orig = live.runtime.speak
    async def _rec(text, **k):
        spoken.append(text)
        return await orig(text, **k)
    live.runtime.speak = _rec
    events = []
    base_emit = live.tm.emit
    async def _tap(ev):
        events.append(ev.type)
        await base_emit(ev)
    live.tm.emit = _tap
    return live, spoken, events


def test_cancel_request_detection():
    assert _is_cancel_request("annule la tâche")
    assert _is_cancel_request("laisse tomber")
    assert not _is_cancel_request("comment tu vas")
    assert not _is_cancel_request("tais-toi")     # ça, c'est un stop voix


@pytest.mark.asyncio
async def test_agent_simple_command_uses_react_and_tool_registry_path():
    core = _FakeAgentCore()
    live, spoken, events = _build_agent_live(core)
    run_task = asyncio.create_task(live.tm.run())
    await live._handle_agent_turn("ouvre google")
    await asyncio.wait_for(live._task, timeout=2.0)
    await live.tm.shutdown(); await asyncio.wait_for(run_task, timeout=2.0)
    await live.runtime.aclose()
    assert core.think_calls == [("ouvre google", "voice", 6)]
    assert core.chat_calls == []


@pytest.mark.asyncio
async def test_agent_simple_question_stays_in_explicit_agent_mode():
    core = _FakeAgentCore()
    live, spoken, events = _build_agent_live(core)
    run_task = asyncio.create_task(live.tm.run())
    await live._handle_agent_turn("quelle heure est-il ?")
    await asyncio.wait_for(live._task, timeout=2.0)
    await live.tm.shutdown(); await asyncio.wait_for(run_task, timeout=2.0)
    await live.runtime.aclose()
    assert core.think_calls == [("quelle heure est-il ?", "voice", 6)]
    assert core.chat_calls == []
    assert core.llm.calls == []


@pytest.mark.asyncio
async def test_agent_complex_request_uses_react():
    core = _FakeAgentCore(result="C'est analysé.")
    live, spoken, events = _build_agent_live(core)
    run_task = asyncio.create_task(live.tm.run())
    await live._handle_agent_turn("analyse le projet et corrige le bug")
    await asyncio.wait_for(live._task, timeout=2.0)
    await live.tm.shutdown(); await asyncio.wait_for(run_task, timeout=2.0)
    await live.runtime.aclose()
    assert len(core.think_calls) == 1                   # ReAct appelé
    assert core.llm.calls == []                         # pas la voie direct


@pytest.mark.asyncio
async def test_agent_runs_think_and_act_and_speaks_milestones():
    core = _FakeAgentCore(result="J'ai trouvé trois fichiers.")
    live, spoken, events = _build_agent_live(core)
    run_task = asyncio.create_task(live.tm.run())
    await live._handle_agent_turn("cherche les fichiers")
    await asyncio.wait_for(live._task, timeout=2.0)
    await live.tm.shutdown()
    await asyncio.wait_for(run_task, timeout=2.0)
    await live.runtime.aclose()

    assert core.think_calls == [("cherche les fichiers", "voice", 6)]
    assert "tool.started" in events and "tool.finished" in events
    assert live.ack_text in spoken                      # accusé "je m'en occupe"
    assert "J'ai trouvé trois fichiers." in spoken      # résultat parlé


@pytest.mark.asyncio
async def test_agent_speech_starts_when_canonical_final_is_ready():
    release = asyncio.Event()
    final_seen = asyncio.Event()

    class _EarlyFinalCore(_FakeAgentCore):
        async def think_and_act(self, query, source_channel="web", sender=None,
                                step_callback=None, max_iterations=None,
                                final_ready_callback=None):
            self.think_calls.append((query, source_channel, max_iterations))
            result = "Le resultat canonique est pret."
            if final_ready_callback:
                pending = final_ready_callback(result)
                if asyncio.iscoroutine(pending):
                    asyncio.create_task(pending)
            final_seen.set()
            await release.wait()  # simule hooks memoire/learning encore actifs
            return result

    core = _EarlyFinalCore()
    live, spoken, events = _build_agent_live(core)
    run_task = asyncio.create_task(live.tm.run())
    await live._handle_agent_turn("fais le travail")
    await asyncio.wait_for(final_seen.wait(), timeout=1.0)
    for _ in range(30):
        if "Le resultat canonique est pret." in spoken:
            break
        await asyncio.sleep(0.01)
    assert "Le resultat canonique est pret." in spoken
    assert live._task is not None and not live._task.done()
    release.set()
    await asyncio.wait_for(live._task, timeout=1.0)
    assert spoken.count("Le resultat canonique est pret.") == 1
    await live.tm.shutdown(); await asyncio.wait_for(run_task, timeout=1.0)
    await live.runtime.aclose()


@pytest.mark.asyncio
async def test_normal_conversation_stays_available_during_agent_work():
    release = asyncio.Event()
    core = _FakeAgentCore(hang=release)
    live, spoken, events = _build_agent_live(core)
    run_task = asyncio.create_task(live.tm.run())
    await live._handle_agent_turn("analyse le projet")
    await asyncio.sleep(0)
    assert live._task is not None and not live._task.done()
    await live._handle_agent_turn("comment vas-tu pendant ce travail ?")
    for _ in range(50):
        if core.chat_calls:
            break
        await asyncio.sleep(0.01)
    assert core.chat_calls == [("comment vas-tu pendant ce travail ?", "voice")]
    assert live._task is not None and not live._task.done()
    release.set()
    await asyncio.wait_for(live._task, timeout=1.0)
    await live.tm.shutdown(); await asyncio.wait_for(run_task, timeout=1.0)
    await live.runtime.aclose()


@pytest.mark.asyncio
async def test_repeated_status_questions_do_not_launch_agents():
    from src.runtime.task_orchestrator import TaskOrchestrator

    release = asyncio.Event()
    orch = TaskOrchestrator(persistence_path=None)
    core = _FakeAgentCore(hang=release, task_orchestrator=orch)
    live, spoken, events = _build_agent_live(core)
    run_task = asyncio.create_task(live.tm.run())
    await live._handle_agent_turn("analyse le projet")
    await asyncio.sleep(0)
    for _ in range(10):
        await live._handle_agent_turn("tu en es où ?")
    assert len(core.think_calls) == 1
    assert core.chat_calls == []
    assert any("travail est" in s.lower() for s in spoken)
    release.set()
    await asyncio.wait_for(live._task, timeout=1.0)
    await live.tm.shutdown(); await asyncio.wait_for(run_task, timeout=1.0)
    await live.runtime.aclose()


def test_agent_periodic_heartbeat_is_disabled_by_default():
    live = VoiceV2Live(
        _FakeAgentCore(), vad=_ScriptedVAD(), stt=_FakeSTT(), tts=FakeTTSProvider(),
        player=LocalAudioPlayer(play_fn=lambda x: _noop(), stop_fn=lambda: None),
        llm_mode="agent",
    )
    assert live.heartbeat_s == 0.0


@pytest.mark.asyncio
async def test_agent_max_iterations_is_configurable_for_voice_only():
    core = _FakeAgentCore(result="ok")
    live, spoken, events = _build_agent_live(core, agent_max_iterations=3)
    run_task = asyncio.create_task(live.tm.run())
    await live._handle_agent_turn("analyse le projet")    # complexe → agent
    await asyncio.wait_for(live._task, timeout=2.0)
    await live.tm.shutdown()
    await asyncio.wait_for(run_task, timeout=2.0)
    await live.runtime.aclose()
    assert core.think_calls == [("analyse le projet", "voice", 3)]   # plafond voix transmis


def test_production_voice_agent_budget_matches_standard_react_and_is_bounded(monkeypatch):
    monkeypatch.delenv("LUMENA_VOICE_AGENT_MAX_ITERATIONS", raising=False)
    assert resolve_voice_agent_max_iterations() == 35
    monkeypatch.setenv("LUMENA_VOICE_AGENT_MAX_ITERATIONS", "72")
    assert resolve_voice_agent_max_iterations() == 72
    monkeypatch.setenv("LUMENA_VOICE_AGENT_MAX_ITERATIONS", "999")
    assert resolve_voice_agent_max_iterations() == 100
    monkeypatch.setenv("LUMENA_VOICE_AGENT_MAX_ITERATIONS", "invalid")
    assert resolve_voice_agent_max_iterations() == 35


@pytest.mark.asyncio
async def test_agent_task_error_speaks_short_message():
    core = _FakeAgentCore(error="boom")
    live, spoken, events = _build_agent_live(core)
    run_task = asyncio.create_task(live.tm.run())
    await live._handle_agent_turn("analyse le projet")    # complexe → agent
    await asyncio.wait_for(live._task, timeout=2.0)
    await live.tm.shutdown(); await asyncio.wait_for(run_task, timeout=2.0)
    await live.runtime.aclose()
    assert "tool.finished" in events
    assert any("erreur" in s.lower() for s in spoken)


@pytest.mark.asyncio
async def test_agent_confirmation_is_announced_only():
    core = _FakeAgentCore(result="Proposition prête.", step_tools=["ionos_db_write_propose"])
    live, spoken, events = _build_agent_live(core)
    run_task = asyncio.create_task(live.tm.run())
    await live._handle_agent_turn("modifie la base")
    await asyncio.wait_for(live._task, timeout=2.0)
    await asyncio.sleep(0.05)        # laisse le speak() programmé par _on_step partir
    await live.tm.shutdown(); await asyncio.wait_for(run_task, timeout=2.0)
    await live.runtime.aclose()
    assert any("Validation requise" in s for s in spoken)   # annonce seulement
    # Pas de boucle oui/non : aucune question de confirmation parlée.
    assert not any("oui ou non" in s.lower() for s in spoken)


@pytest.mark.asyncio
async def test_agent_actor_not_blocked_and_barge_in_does_not_cancel():
    hang = asyncio.Event()
    to = _FakeTaskOrch(running=[{"id": "t1", "source_channel": "voice", "state": "running"}])
    core = _FakeAgentCore(hang=hang, task_orchestrator=to)
    live, spoken, events = _build_agent_live(core)
    run_task = asyncio.create_task(live.tm.run())
    await live._handle_agent_turn("analyse un gros fichier")   # complexe → agent
    await asyncio.sleep(0.05)                 # la tâche tourne (hang)
    assert core.think_calls and not live._task.done()

    # Barge-in PENDANT la tâche : coupe la voix, ne touche PAS la tâche.
    t0 = asyncio.get_running_loop().time()
    await live.tm.emit(VoiceEvent("user.stop_word", data={"word": "stop"}))
    for _ in range(30):
        if live.runtime.status == "interrupted":
            break
        await asyncio.sleep(0.01)
    elapsed = asyncio.get_running_loop().time() - t0
    assert elapsed < 0.3                       # acteur non bloqué par la tâche longue
    assert to.cancelled == []                  # barge-in n'annule PAS la tâche
    assert not live._task.done()               # tâche toujours vivante

    hang.set()                                 # on libère la tâche
    await asyncio.wait_for(live._task, timeout=2.0)
    await live.tm.shutdown(); await asyncio.wait_for(run_task, timeout=2.0)
    await live.runtime.aclose()


@pytest.mark.asyncio
async def test_agent_cancel_request_calls_cancel_task():
    hang = asyncio.Event()
    to = _FakeTaskOrch(running=[{"id": "t1", "source_channel": "voice", "state": "running"}])
    core = _FakeAgentCore(hang=hang, task_orchestrator=to)
    live, spoken, events = _build_agent_live(core)
    run_task = asyncio.create_task(live.tm.run())
    await live._handle_agent_turn("analyse le projet")        # complexe → agent
    await asyncio.sleep(0.05)
    # « Annule » → user.cancel_action → cancel_tool_if_safe → cancel_task(t1).
    await live._handle_agent_turn("annule la tâche")
    for _ in range(40):
        if to.cancelled:
            break
        await asyncio.sleep(0.01)
    assert to.cancelled == ["t1"]
    assert any("annule" in s.lower() for s in spoken)

    hang.set()
    try:
        await asyncio.wait_for(live._task, timeout=2.0)
    except Exception:
        pass
    await live.tm.shutdown(); await asyncio.wait_for(run_task, timeout=2.0)
    await live.runtime.aclose()


@pytest.mark.asyncio
async def test_agent_stop_action_prefers_cancel_over_voice_stop():
    hang = asyncio.Event()
    to = _FakeTaskOrch(running=[{"id": "t1", "source_channel": "voice", "state": "running"}])
    core = _FakeAgentCore(hang=hang, task_orchestrator=to)
    live, spoken, events = _build_agent_live(core)
    run_task = asyncio.create_task(live.tm.run())
    await live._handle_agent_turn("analyse le projet")       # complexe → agent
    await asyncio.sleep(0.05)

    await live._handle_agent_turn("stop l'action")
    for _ in range(40):
        if to.cancelled:
            break
        await asyncio.sleep(0.01)

    assert to.cancelled == ["t1"]
    assert live.runtime.status != "interrupted"

    hang.set()
    try:
        await asyncio.wait_for(live._task, timeout=2.0)
    except Exception:
        pass
    await live.tm.shutdown(); await asyncio.wait_for(run_task, timeout=2.0)
    await live.runtime.aclose()


@pytest.mark.asyncio
async def test_agent_cancel_when_nothing_running_is_safe():
    to = _FakeTaskOrch(running=[])
    core = _FakeAgentCore(task_orchestrator=to)
    live, spoken, events = _build_agent_live(core)
    run_task = asyncio.create_task(live.tm.run())
    await live._cancel_current_task()          # aucune tâche en cours
    await live.tm.shutdown(); await asyncio.wait_for(run_task, timeout=2.0)
    await live.runtime.aclose()
    assert to.cancelled == []
    assert any("rien à annuler" in s.lower() for s in spoken)


# ── Isolation : live.py n'importe aucune lib hardware au niveau module ─────────
def test_live_module_no_top_level_hardware_import():
    import src.voice.v2.live as mod
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    top = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            top.append(node.module or "")
    forbidden = {"pyaudio", "audioop", "faster_whisper"}
    assert not any((m or "").split(".")[0] in forbidden or "voice.stt" in (m or "")
                   or m == "src.core" for m in top), top
