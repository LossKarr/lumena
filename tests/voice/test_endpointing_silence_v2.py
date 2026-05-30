"""Voice V2 — endpointing piloté par le silence (vad.speech_ended + timer).

Logic-only, fake/pytest, sans hardware : `vad.speech_ended` ARME un timer de
silence ; `timer.endpoint` (réinjecté par un service de timer) TRANCHE via
`decide_endpoint`. Reprise de parole avant expiration = pause → désarmement.
"""
import asyncio

import pytest

from src.voice.v2 import (
    TurnManager, VoiceEvent, VoiceCommand,
    FakeVADProvider, FakeSTTProvider,
    pump_vad, pump_stt, EndpointTimerService,
)


def _names(cmds):
    return [c.name for c in cmds]


def _open_turn(tm: TurnManager):
    """Ouvre un tour utilisateur (mode user_speaking)."""
    tm.feed(VoiceEvent("vad.speech_started", t=0))
    assert tm.state.mode == "user_speaking"


# ── Armement du timer sur vad.speech_ended ─────────────────────────────────────
def test_speech_ended_arms_endpoint_timer():
    tm = TurnManager()
    _open_turn(tm)
    tm.feed(VoiceEvent("stt.final", t=100, data={"text": "ouvre le fichier"}))
    cmds = tm.feed(VoiceEvent("vad.speech_ended", t=200))
    assert "arm_endpoint_timer" in _names(cmds)
    arm = [c for c in cmds if c.name == "arm_endpoint_timer"][0]
    assert arm.data["turn_id"] == tm.state.current_turn_id
    assert arm.data["wait_ms"] > 0
    assert tm.state.endpoint_armed is True
    assert tm.state.endpoint_armed_turn == tm.state.current_turn_id


def test_speech_ended_outside_user_speaking_is_noop():
    tm = TurnManager()   # mode idle
    cmds = tm.feed(VoiceEvent("vad.speech_ended", t=10))
    assert cmds == [] and tm.state.endpoint_armed is False


# ── Le timer tranche : commande claire → start_llm ─────────────────────────────
def test_timer_endpoint_completes_turn_on_command():
    tm = TurnManager()
    _open_turn(tm)
    tm.feed(VoiceEvent("stt.final", t=100, data={"text": "ouvre le fichier"}))
    tm.feed(VoiceEvent("vad.speech_ended", t=200))
    turn = tm.state.current_turn_id
    cmds = tm.feed(VoiceEvent("timer.endpoint", t=400, data={"turn_id": turn, "pause_ms": 200}))
    assert "start_llm" in _names(cmds)
    start = [c for c in cmds if c.name == "start_llm"][0]
    assert start.data["text"] == "ouvre le fichier"
    assert tm.state.mode == "thinking"
    assert tm.state.endpoint_armed is False


# ── Le timer tranche : final neutre sans pause suffisante → attendre ───────────
def test_timer_endpoint_uncertain_neutral_waits():
    tm = TurnManager()
    _open_turn(tm)
    tm.feed(VoiceEvent("stt.final", t=100, data={"text": "il fait beau aujourd'hui"}))
    arm = [c for c in tm.feed(VoiceEvent("vad.speech_ended", t=200)) if c.name == "arm_endpoint_timer"][0]
    turn = tm.state.current_turn_id
    cmds = tm.feed(VoiceEvent("timer.endpoint", t=600,
                              data={"turn_id": turn, "pause_ms": arm.data["wait_ms"]}))
    assert "start_llm" not in _names(cmds)
    assert tm.state.mode == "user_paused"


# ── Le timer tranche : partiel seul (pas de final) → jamais d'action ───────────
def test_timer_endpoint_partial_only_never_acts():
    tm = TurnManager()
    _open_turn(tm)
    tm.feed(VoiceEvent("stt.partial", t=100, data={"text": "je veux que tu"}))
    tm.feed(VoiceEvent("vad.speech_ended", t=200))
    turn = tm.state.current_turn_id
    cmds = tm.feed(VoiceEvent("timer.endpoint", t=900, data={"turn_id": turn, "pause_ms": 700}))
    assert "start_llm" not in _names(cmds)        # finals = contenu ; pas de final → pas d'action
    assert tm.state.mode == "user_paused"


def test_late_final_after_endpoint_timer_can_complete_turn():
    tm = TurnManager()
    _open_turn(tm)
    tm.feed(VoiceEvent("stt.partial", t=100, data={"text": ""}))
    tm.feed(VoiceEvent("vad.speech_ended", t=200))
    turn = tm.state.current_turn_id
    early = tm.feed(VoiceEvent("timer.endpoint", t=500, data={"turn_id": turn, "pause_ms": 300}))
    assert "start_llm" not in _names(early)
    assert tm.state.mode == "user_paused"

    late = tm.feed(VoiceEvent("stt.final", t=1200, data={"text": "ouvre le fichier"}))
    assert "start_llm" in _names(late)
    start = [c for c in late if c.name == "start_llm"][0]
    assert start.data["text"] == "ouvre le fichier"


# ── Reprise de parole avant expiration → pause, désarmement ────────────────────
def test_final_while_endpoint_timer_armed_completes_immediately():
    tm = TurnManager()
    _open_turn(tm)
    tm.feed(VoiceEvent("vad.speech_ended", t=200))
    turn = tm.state.current_turn_id
    assert tm.state.endpoint_armed is True

    cmds = tm.feed(VoiceEvent("stt.final", t=900, data={"text": "ouvre le fichier"}))
    assert "start_llm" in _names(cmds)
    assert tm.state.endpoint_armed is False
    start = [c for c in cmds if c.name == "start_llm"][0]
    assert start.data["text"] == "ouvre le fichier"

    stale_timer = tm.feed(VoiceEvent("timer.endpoint", t=1200, data={"turn_id": turn, "pause_ms": 300}))
    assert stale_timer == []


def test_resumed_speech_cancels_armed_timer():
    tm = TurnManager()
    _open_turn(tm)
    tm.feed(VoiceEvent("stt.partial", t=100, data={"text": "donc"}))
    tm.feed(VoiceEvent("vad.speech_ended", t=200))
    assert tm.state.endpoint_armed is True
    turn = tm.state.current_turn_id
    # L'utilisateur reprend la parole AVANT l'expiration : ce n'était qu'une pause.
    cmds = tm.feed(VoiceEvent("vad.speech_started", t=300))
    assert "cancel_endpoint_timer" in _names(cmds)
    assert tm.state.endpoint_armed is False
    assert tm.state.mode == "user_speaking"        # même tour, pas de nouveau tour
    assert tm.state.current_turn_id == turn
    # Un timer en retard (annulé) qui arriverait quand même est ignoré.
    late = tm.feed(VoiceEvent("timer.endpoint", t=350, data={"turn_id": turn, "pause_ms": 600}))
    assert late == []


# ── Garde anti-stale : timer d'un ancien tour ignoré ───────────────────────────
def test_stale_timer_endpoint_ignored():
    tm = TurnManager()
    _open_turn(tm)
    tm.feed(VoiceEvent("stt.final", t=100, data={"text": "ouvre le fichier"}))
    tm.feed(VoiceEvent("vad.speech_ended", t=200))
    cmds = tm.feed(VoiceEvent("timer.endpoint", t=400, data={"turn_id": "u_999", "pause_ms": 200}))
    assert cmds == []                              # turn_id ne correspond pas → ignoré
    assert tm.state.endpoint_armed is True         # toujours armé


# ── Intégration : service de timer réel (asyncio) sur tour fake ────────────────
@pytest.mark.asyncio
async def test_endpoint_timer_service_fires_and_completes_turn():
    tm = TurnManager()
    timer = EndpointTimerService(tm, speed=0.01)   # 200 ms → ~2 ms en test
    tm._dispatcher = timer.dispatch
    task = asyncio.create_task(tm.run())

    vad = FakeVADProvider(script=[(0, "speech_started")])
    stt = FakeSTTProvider(script=[(120, "ouvre le fichier", True)])
    await pump_vad(vad, tm)
    await pump_stt(stt, tm)
    await tm.emit(VoiceEvent("vad.speech_ended", t=200))   # arme le timer → fire auto

    for _ in range(100):
        if any(c.name == "start_llm" for c in tm.emitted):
            break
        await asyncio.sleep(0.01)
    await tm.shutdown()
    await asyncio.wait_for(task, timeout=1.0)
    timer.cancel_all()

    start = [c for c in tm.emitted if c.name == "start_llm"][-1]
    assert start.data["text"] == "ouvre le fichier"


@pytest.mark.asyncio
async def test_endpoint_timer_service_cancelled_on_resumed_speech():
    """Le timer ne doit PAS conclure si la parole reprend (pause)."""
    tm = TurnManager()
    timer = EndpointTimerService(tm, speed=1.0)    # 600 ms : assez long pour intercaler
    tm._dispatcher = timer.dispatch
    task = asyncio.create_task(tm.run())

    tm_started = FakeVADProvider(script=[(0, "speech_started")])
    await pump_vad(tm_started, tm)
    await tm.emit(VoiceEvent("stt.partial", t=100, data={"text": "donc"}))
    await tm.emit(VoiceEvent("vad.speech_ended", t=200))   # arme (~600 ms)
    await asyncio.sleep(0.02)
    await tm.emit(VoiceEvent("vad.speech_started", t=300))  # reprise → cancel
    await asyncio.sleep(0.05)

    await tm.shutdown()
    await asyncio.wait_for(task, timeout=1.0)
    timer.cancel_all()

    assert not any(c.name == "start_llm" for c in tm.emitted)   # jamais conclu
