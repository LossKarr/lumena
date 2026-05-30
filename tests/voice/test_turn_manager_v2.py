"""Thin slice Voice V2 — tests déterministes (replay event-level, sans audio réel).

Couvre les cas prioritaires de l'Addendum V2.3 :
- pause au milieu d'une phrase -> ne pas répondre ;
- stop word pendant TTS -> stop immédiat ;
- barge-in transcript vide -> reprendre ;
- partial contredit par final -> pas d'action sur le partial ;
- vieux chunk audio -> jeté ;
- outil + "coupe la voix" -> outil continue, voix stop ;
- outil + "annule" -> commande d'annulation ;
- troncature du ledger à l'interruption ;
- endpointing heuristique ;
- acteur file unique (emit/run) déterministe.
"""
import asyncio

import pytest

from src.voice.v2 import (
    TurnManager, VoiceEvent,
    AudioChunk, AudioOutputQueue, ConversationAudioLedger,
    decide_endpoint,
)


def _names(cmds):
    return [c.name for c in cmds]


# ── Helper : amener le TurnManager en état "speaking" ─────────────────────────
def _drive_to_speaking(tm: TurnManager):
    tm.feed(VoiceEvent("vad.speech_started", t=0))
    tm.feed(VoiceEvent("stt.final", t=100, data={"text": "ouvre le fichier"}))
    cmds = tm.feed(VoiceEvent("endpoint.decision", t=150, data={"state": "turn_complete"}))
    assert "start_llm" in _names(cmds)
    tm.feed(VoiceEvent("llm.response_started", t=200))
    assert tm.state.mode == "speaking"
    assert tm.state.current_generation_id is not None


# ── 1. Pause au milieu d'une phrase -> ne pas répondre ────────────────────────
def test_pause_mid_phrase_no_response():
    tm = TurnManager()
    tm.feed(VoiceEvent("vad.speech_started", t=0))
    tm.feed(VoiceEvent("stt.partial", t=120, data={"text": "je veux que tu"}))
    cmds = tm.feed(VoiceEvent("endpoint.decision", t=850, data={"state": "continue_expected"}))
    assert "start_llm" not in _names(cmds)
    assert tm.state.mode == "user_paused"
    # aucune génération démarrée
    assert tm.state.current_generation_id is None


# ── 2. Stop word pendant TTS -> stop immédiat ─────────────────────────────────
def test_stop_word_during_tts():
    tm = TurnManager()
    _drive_to_speaking(tm)
    tm.feed(VoiceEvent("tts.chunk_ready", t=250, data={"sequence": 0, "text": "Il y a"}))
    cmds = tm.feed(VoiceEvent("user.stop_word", t=300, data={"word": "stop"}))
    n = _names(cmds)
    assert "stop_playback" in n and "clear_audio_queue" in n
    assert "cancel_tts" in n and "cancel_llm" in n
    assert "truncate_conversation" in n
    # pas d'outil -> retour écoute
    assert tm.state.mode == "wake_listening"


# ── 3. Barge-in transcript vide -> reprendre ──────────────────────────────────
def test_false_barge_in_resumes():
    tm = TurnManager()
    _drive_to_speaking(tm)
    # parole détectée pendant que Lumena parle : pas de coupe immédiate
    cmds = tm.feed(VoiceEvent("vad.speech_started", t=260))
    assert _names(cmds) == []
    assert tm.state.pending_barge_in is True
    assert tm.state.mode == "speaking"  # pas coupée
    # le transcript ne vient jamais (bruit) -> timeout -> reprise
    cmds = tm.feed(VoiceEvent("timer.false_interruption_timeout", t=600))
    assert "resume_playback" in _names(cmds)
    assert tm.state.pending_barge_in is False


# ── 4. Partial contredit par final -> pas d'action sur le partial ─────────────
def test_partial_never_triggers_action():
    tm = TurnManager()  # speculative OFF par défaut
    tm.feed(VoiceEvent("vad.speech_started", t=0))
    c1 = tm.feed(VoiceEvent("stt.partial", t=100, data={"text": "supprime la"}))
    c2 = tm.feed(VoiceEvent("stt.partial", t=300, data={"text": "supprime pas la"}))
    # aucun start_llm / action sur un partial
    assert "start_llm" not in _names(c1) and "start_llm" not in _names(c2)
    assert tm.state.mode == "user_speaking"


# ── 5. Vieux chunk audio -> jeté (discipline generation_id) ───────────────────
def test_audio_queue_drops_stale_generation():
    q = AudioOutputQueue(maxsize=8)
    q.set_generation("a_1")
    assert q.push(AudioChunk("u_1", "a_1", 0, "bonjour", 200)) == "queued"
    # nouvelle génération (interruption + nouvelle réponse)
    q.set_generation("a_2")
    # un chunk de l'ancienne génération arrive en retard -> jeté
    assert q.push(AudioChunk("u_1", "a_1", 1, "monde", 200)) == "dropped_stale"
    assert q.push(AudioChunk("u_2", "a_2", 0, "oui", 150)) == "queued"
    assert q.dropped_stale == 1


# ── 6. Outil en cours + "coupe la voix" -> outil continue, voix stop ──────────
def test_tool_running_stop_word_keeps_tool():
    tm = TurnManager()
    tm.feed(VoiceEvent("tool.started", t=0, data={"tool": "deploy"}))
    assert tm.state.mode == "tool_running" and tm.state.tool_active is True
    cmds = tm.feed(VoiceEvent("user.stop_word", t=100, data={"word": "coupe"}))
    n = _names(cmds)
    assert "stop_playback" in n
    assert "cancel_tool_if_safe" not in n          # l'outil n'est PAS annulé
    assert tm.state.tool_active is True             # outil toujours actif
    assert tm.state.mode == "tool_running"          # on reste sur l'outil


# ── 7. Outil en cours + "annule" -> commande d'annulation outil ───────────────
def test_tool_running_cancel_action():
    tm = TurnManager()
    tm.feed(VoiceEvent("tool.started", t=0, data={"tool": "deploy"}))
    cmds = tm.feed(VoiceEvent("user.cancel_action", t=100))
    assert "cancel_tool_if_safe" in _names(cmds)


# ── 8. Troncature du ledger à l'interruption ──────────────────────────────────
def test_ledger_truncation_keeps_only_played():
    led = ConversationAudioLedger()
    full = "Il y a trois points importants. Le premier... Le deuxieme..."
    led.register_generation("u_1", "a_1", full)
    led.on_chunk_played("a_1", "Il y a trois points importants.", 4120)
    ps = led.truncate("a_1")
    assert ps is not None
    assert ps.text_played == "Il y a trois points importants."
    assert ps.played_ms == 4120
    assert ps.interrupted is True
    assert "Le deuxieme" not in ps.text_played


# ── 9. Endpointing heuristique ────────────────────────────────────────────────
def test_endpointing_heuristics():
    assert decide_endpoint("je veux que tu").state == "continue_expected"
    assert decide_endpoint("euh").state == "continue_expected"
    assert decide_endpoint("ouvre le fichier", is_final=True).state == "turn_complete"
    assert decide_endpoint("est-ce que tu peux m'aider ?").state == "turn_complete"
    assert decide_endpoint("oui", is_final=True).state == "turn_complete"
    assert decide_endpoint("").state == "uncertain"


# ── 10. Acteur file unique : emit + run draine et dispatch ────────────────────
@pytest.mark.asyncio
async def test_actor_single_queue_drains_and_dispatches():
    dispatched = []

    async def dispatcher(cmds):
        dispatched.extend(c.name for c in cmds)

    tm = TurnManager(dispatcher=dispatcher)
    task = asyncio.create_task(tm.run())
    await tm.emit(VoiceEvent("vad.speech_started", t=0))
    await tm.emit(VoiceEvent("stt.final", t=100, data={"text": "ouvre le fichier"}))
    await tm.emit(VoiceEvent("endpoint.decision", t=150, data={"state": "turn_complete"}))
    # laisser la boucle consommer
    for _ in range(20):
        if "start_llm" in dispatched:
            break
        await asyncio.sleep(0.01)
    tm.stop()
    await tm.emit(VoiceEvent("vad.speech_started", t=999))  # débloque la queue.get()
    try:
        await asyncio.wait_for(task, timeout=1)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        task.cancel()
    assert "start_stt" in dispatched
    assert "start_llm" in dispatched


# ── 11. Isolation du code v2 : aucun module v2 n'importe la stack audio ───────
def test_v2_source_does_not_import_audio_stack():
    """Garantie réelle (AST) : les modules de `src/voice/v2/` n'IMPORTENT NI stt/tts/
    assistant_loop/manager, NI les libs audio lourdes. On analyse les vrais imports,
    pas les littéraux (`prewarm` mentionne 'faster_whisper' comme chaîne find_spec).
    (Le package parent `src/voice/__init__.py` charge stt/tts — hors périmètre v2.)
    """
    import ast
    from pathlib import Path
    import src.voice.v2 as v2

    forbidden_roots = {
        "pyaudio", "faster_whisper", "TTS", "pygame", "edge_tts", "torch", "pyttsx3",
    }
    forbidden_voice = {"stt", "tts", "assistant_loop", "manager"}  # frères dans src.voice

    pkg_dir = Path(v2.__file__).parent
    offenders = {}
    for py in sorted(pkg_dir.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=py.name)
        hits = []
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
                # `from .. import stt` (remontée vers le package voice parent)
                if node.level >= 2:
                    mods += [a.name for a in node.names]
            for m in mods:
                root = (m or "").split(".")[0]
                if root in forbidden_roots:
                    hits.append(m)
                if (m or "") in forbidden_voice or root in forbidden_voice:
                    hits.append(m)
        if hits:
            offenders[py.name] = hits
    assert not offenders, f"modules v2 important la stack audio: {offenders}"


# ── 12. Le code v2 n'est référencé NULLE PART dans la stack voix existante ────
def test_v2_not_wired_into_existing_voice_stack():
    """Contrainte : ne pas brancher v2 dans stt/tts/assistant_loop/manager."""
    from pathlib import Path
    import src.voice as voice_pkg

    voice_dir = Path(voice_pkg.__file__).parent
    for name in ("stt.py", "tts.py", "assistant_loop.py", "manager.py"):
        f = voice_dir / name
        if not f.exists():
            continue
        src = f.read_text(encoding="utf-8")
        assert "voice.v2" not in src and "from .v2" not in src and "import v2" not in src, \
            f"{name} référence v2 — branchement interdit à ce stade"
