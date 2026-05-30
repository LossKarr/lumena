"""Voice V2 — STT/VAD logic-only (fakes), sans hardware ni WebRTC.

Prouve : contrats VAD/STT, pompes fake → événements TurnManager, barge-in
IMMÉDIAT sur VAD (point critique : couper pendant le TTS sans attendre le STT
final), et gating LUMENA_VOICE_V2_STT=0 par défaut.
"""
import asyncio

import pytest

from src.voice.v2 import (
    TurnManager, VoiceEvent, VoiceCommand,
    FakeVADProvider, FakeSTTProvider, VADEvent, STTResult,
    STTProvider, VADProvider,
    pump_vad, pump_stt, v2_stt_enabled,
)


def _names(cmds):
    return [c.name for c in cmds]


# ── Gating ────────────────────────────────────────────────────────────────────
def test_v2_stt_flag_off_by_default(monkeypatch):
    monkeypatch.delenv("LUMENA_VOICE_V2_STT", raising=False)
    assert v2_stt_enabled() is False


# ── Contrats / fakes ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fake_vad_streams_scripted_boundaries():
    vad = FakeVADProvider(script=[(0, "speech_started"), (700, "speech_ended")])
    assert vad.is_available() is True
    got = [(e.kind, e.t) async for e in vad.stream(audio=None)]
    assert got == [("speech_started", 0), ("speech_ended", 700)]


@pytest.mark.asyncio
async def test_fake_stt_streams_partials_then_final():
    stt = FakeSTTProvider(script=[(100, "ouvre le", False), (400, "ouvre le fichier", True)])
    out = [(r.text, r.is_final) async for r in stt.stream(audio=None)]
    assert out == [("ouvre le", False), ("ouvre le fichier", True)]
    assert await stt.transcribe(None) == "ouvre le fichier"   # dernier final


@pytest.mark.asyncio
async def test_stt_provider_default_stream_yields_single_final():
    class _OneShot(STTProvider):
        name = "oneshot"; locality = "local"
        def is_available(self): return True
        async def transcribe(self, audio, *, language="fr"): return "bonjour"

    results = [r async for r in _OneShot().stream(None)]
    assert len(results) == 1 and results[0].is_final and results[0].text == "bonjour"


# ── Pompes fake → événements TurnManager ───────────────────────────────────────
@pytest.mark.asyncio
async def test_pump_vad_drives_turn_manager_to_listening():
    tm = TurnManager()
    vad = FakeVADProvider(script=[(0, "speech_started")])
    task = asyncio.create_task(tm.run())
    await pump_vad(vad, tm)
    mode = "idle"
    for _ in range(50):
        mode = tm.state.mode
        if mode == "user_speaking":
            break
        await asyncio.sleep(0.01)
    await tm.shutdown()   # NB : shutdown remet mode=idle → on capture AVANT
    await asyncio.wait_for(task, timeout=1.0)
    assert mode == "user_speaking"
    assert any(c.name == "start_stt" for c in tm.emitted)


@pytest.mark.asyncio
async def test_pump_stt_emits_partial_then_final():
    tm = TurnManager()
    stt = FakeSTTProvider(script=[(100, "supprime la", False), (400, "supprime la base", True)])
    tm.feed(VoiceEvent("vad.speech_started", t=0))   # tour ouvert
    task = asyncio.create_task(tm.run())
    await pump_stt(stt, tm)
    for _ in range(50):
        if tm.state.final_transcript:
            break
        await asyncio.sleep(0.01)
    await tm.shutdown()
    await asyncio.wait_for(task, timeout=1.0)
    # Le partiel a piloté le timing, le final pilote le contenu — aucun start_llm sans endpoint.
    assert tm.state.partial_transcript == "supprime la"
    assert tm.state.final_transcript == "supprime la base"
    assert "start_llm" not in _names(tm.emitted)


@pytest.mark.asyncio
async def test_full_logic_tour_vad_then_stt_then_endpoint():
    tm = TurnManager()
    vad = FakeVADProvider(script=[(0, "speech_started")])
    stt = FakeSTTProvider(script=[(120, "ouvre le", False), (600, "ouvre le fichier", True)])
    task = asyncio.create_task(tm.run())
    await pump_vad(vad, tm)
    await pump_stt(stt, tm)
    await tm.emit(VoiceEvent("endpoint.decision", t=900, data={"state": "turn_complete"}))
    for _ in range(80):
        if any(c.name == "start_llm" for c in tm.emitted):
            break
        await asyncio.sleep(0.01)
    await tm.shutdown()
    await asyncio.wait_for(task, timeout=1.0)
    start = [c for c in tm.emitted if c.name == "start_llm"][-1]
    assert start.data["text"] == "ouvre le fichier"


# ── POINT CRITIQUE : barge-in IMMÉDIAT sur VAD pendant le TTS ──────────────────
def _drive_to_speaking(tm: TurnManager):
    tm.feed(VoiceEvent("vad.speech_started", t=0))
    tm.feed(VoiceEvent("stt.final", t=100, data={"text": "raconte-moi une histoire"}))
    cmds = tm.feed(VoiceEvent("endpoint.decision", t=150, data={"state": "turn_complete"}))
    assert "start_llm" in _names(cmds)
    tm.feed(VoiceEvent("llm.response_started", t=160))
    assert tm.state.mode == "speaking"


def test_vad_barge_in_cuts_immediately_without_waiting_stt_final():
    tm = TurnManager(barge_in_on_vad=True)
    _drive_to_speaking(tm)
    old_gen = tm.state.current_generation_id
    # Parole détectée PENDANT que Lumena parle → coupe IMMÉDIATE, sans stt.final.
    cmds = tm.feed(VoiceEvent("vad.speech_started", t=500))
    names = _names(cmds)
    assert "stop_playback" in names and "clear_audio_queue" in names
    assert "cancel_tts" in names and "truncate_conversation" in names
    assert "start_stt" in names
    # On a basculé sur un nouveau tour utilisateur, sans aucun stt.final entre-temps.
    assert tm.state.mode == "user_speaking"
    assert tm.state.current_generation_id is None
    assert tm.state.final_transcript == ""
    # La commande de troncature cible bien la génération coupée.
    trunc = [c for c in cmds if c.name == "truncate_conversation"][0]
    assert trunc.data.get("generation_id") == old_gen
    start_stt = [c for c in cmds if c.name == "start_stt"][0]
    assert start_stt.data.get("turn_id") == tm.state.current_turn_id


def test_default_vad_during_speaking_is_conservative_no_immediate_cut():
    tm = TurnManager()   # barge_in_on_vad=False par défaut
    _drive_to_speaking(tm)
    cmds = tm.feed(VoiceEvent("vad.speech_started", t=500))
    assert _names(cmds) == []                       # aucune coupe immédiate
    assert tm.state.pending_barge_in is True
    assert tm.state.mode == "speaking"               # voix non coupée


@pytest.mark.asyncio
async def test_vad_barge_in_through_pump_during_speaking():
    """Bout-en-bout : pendant 'speaking', la pompe VAD coupe le TTS immédiatement."""
    tm = TurnManager(barge_in_on_vad=True)
    _drive_to_speaking(tm)
    vad = FakeVADProvider(script=[(500, "speech_started")])
    task = asyncio.create_task(tm.run())
    await pump_vad(vad, tm)
    mode = "speaking"
    for _ in range(50):
        mode = tm.state.mode
        if mode == "user_speaking":
            break
        await asyncio.sleep(0.01)
    await tm.shutdown()   # NB : shutdown remet mode=idle → on capture AVANT
    await asyncio.wait_for(task, timeout=1.0)
    assert mode == "user_speaking"
    assert any(c.name == "stop_playback" for c in tm.emitted)
