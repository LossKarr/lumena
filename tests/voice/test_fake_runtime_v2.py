"""Voice V2 — tour complet via FakeRuntime + replay JSONL (sans audio réel).

Prouve, de bout en bout et de façon déterministe :
- final transcript -> start_llm -> chunks TTS -> playback (tour complet) ;
- interruption en plein milieu -> stop/clear/cancel/truncate ;
- les chunks de l'ancienne génération arrivés en retard sont rejetés ;
- le ledger ne retient que ce qui a été réellement joué ;
- arrêt propre via system.shutdown ;
- replay JSONL via le même reducer.
"""
import asyncio

import pytest

from src.voice.v2 import (
    TurnManager, VoiceEvent, FakeRuntime, Driver,
    parse_events, replay_sync,
)


def _full_tour_events():
    return [
        VoiceEvent("vad.speech_started", t=0),
        VoiceEvent("stt.final", t=100, data={"text": "ouvre le fichier"}),
        VoiceEvent("endpoint.decision", t=150, data={"state": "turn_complete"}),
    ]


# ── 1. Tour complet : final -> LLM -> chunks -> playback terminé ──────────────
def test_full_tour_completes():
    tm = TurnManager()
    rt = FakeRuntime(llm_answer="Premier point. Deuxieme point. Troisieme point.")
    d = Driver(tm, rt)
    d.push(*_full_tour_events())
    d.run()
    # 3 phrases jouées dans l'ordre
    assert [p["sequence"] for p in rt.played] == [0, 1, 2]
    assert [p["text"] for p in rt.played] == ["Premier point", "Deuxieme point", "Troisieme point"]
    assert "start_llm" in rt.commands_seen
    assert tm.state.mode == "wake_listening"   # playback.finished -> retour écoute
    assert rt.dropped == []


# ── 2. Interruption en plein milieu -> stop + ledger tronqué + suite jetée ────
def test_interruption_mid_tour_truncates_and_drops_stale():
    tm = TurnManager()
    rt = FakeRuntime(llm_answer="Un. Deux. Trois. Quatre. Cinq.")
    d = Driver(tm, rt)
    d.push(*_full_tour_events())
    # jouer au moins un chunk puis interrompre (file unique : un chunk en vol peut passer)
    d.run_until(lambda: len(rt.played) >= 1, max_ticks=50)
    gen = tm.state.current_generation_id
    assert gen is not None
    d.push(VoiceEvent("user.stop_word", t=500, data={"word": "stop"}))
    d.run()
    n = rt.commands_seen
    assert "stop_playback" in n and "clear_audio_queue" in n
    assert "cancel_tts" in n and "cancel_llm" in n and "truncate_conversation" in n
    # invariant fort : la SUITE de la réponse ne joue jamais après le stop
    played_texts = [p["text"] for p in rt.played]
    for tail in ("Trois", "Quatre", "Cinq"):
        assert tail not in played_texts, f"{tail} n'aurait pas dû être joué après le stop"
    assert rt._gens[gen].cancelled is True
    # ledger : tronqué, ne retient que ce qui a été réellement joué
    ps = rt.ledger.get(gen)
    assert ps is not None and ps.interrupted is True
    assert ps.played_ms == len(rt.played) * rt.chunk_ms
    # un chunk périmé de l'ancienne génération arrive en retard -> rejeté (jamais joué)
    d.push(VoiceEvent("tts.chunk_ready", t=600, data={
        "generation_id": gen, "sequence": 9, "text": "fantome", "duration_ms": 200,
    }))
    d.run()
    assert any(x["generation_id"] == gen for x in rt.dropped)
    assert all(p["text"] != "fantome" for p in rt.played)


# ── 3. Stop pendant un outil : voix coupée, outil NON annulé ──────────────────
def test_stop_during_tool_keeps_tool():
    tm = TurnManager()
    rt = FakeRuntime()
    d = Driver(tm, rt)
    d.push(VoiceEvent("tool.started", t=0, data={"tool": "deploy"}))
    d.push(VoiceEvent("user.stop_word", t=100, data={"word": "coupe"}))
    d.run()
    assert "stop_playback" in rt.commands_seen
    assert "cancel_tool_if_safe" not in rt.commands_seen
    assert tm.state.tool_active is True and tm.state.mode == "tool_running"


# ── 4. Arrêt propre via system.shutdown (boucle run async) ────────────────────
@pytest.mark.asyncio
async def test_clean_shutdown_via_event():
    tm = TurnManager()
    task = asyncio.create_task(tm.run())
    await tm.emit(VoiceEvent("vad.speech_started", t=0))
    await tm.shutdown()  # passe par la queue, pas de kill externe
    await asyncio.wait_for(task, timeout=1.0)   # run() se termine seul
    assert tm.state.mode == "idle"
    assert task.done()


# ── 5. Replay JSONL via le même reducer ───────────────────────────────────────
def test_replay_jsonl_drives_same_reducer():
    jsonl = """
    {"t": 0, "type": "vad.speech_started"}
    {"t": 120, "type": "stt.partial", "text": "je veux que tu"}
    {"t": 850, "type": "endpoint.decision", "state": "continue_expected", "reason": "incomplete"}
    {"t": 1600, "type": "stt.final", "text": "ouvre le fichier"}
    {"t": 1700, "type": "endpoint.decision", "state": "turn_complete"}
    """
    events = parse_events(jsonl)
    assert len(events) == 5
    tm = TurnManager()
    replay_sync(tm, events)
    # la pause au milieu n'a pas déclenché de réponse ; seul le final+turn_complete l'a fait
    assert any(c.name == "start_llm" for c in tm.emitted)
    assert tm.state.current_generation_id is not None
