import asyncio

import pytest

from src.runtime.context import get_current_runtime_context
from src.runtime.task_orchestrator import TaskOrchestrator
from src.runtime.task_steering import consume_text_steering
from src.voice.v2 import TurnManager, VoiceEvent
from src.voice.v2.session import VoiceSessionRouter
from src.voice.v2.speech_planner import plan_speech
from src.voice.v2.work_registry import ActiveWorkRegistry


class _ConversationCore:
    def __init__(self):
        self.contexts = []

    async def chat(self, text, source_channel="web"):
        ctx = get_current_runtime_context()
        self.contexts.append((text, ctx.conversation_id, ctx.mode, source_channel))
        return f"Réponse à: {text}"


@pytest.mark.asyncio
async def test_matrix_20_turn_conversation_keeps_one_voice_session():
    core = _ConversationCore()
    router = VoiceSessionRouter(core, mode="chat", conversation_id="voice-long")
    turns = ["Parle-moi de Paris"] + ["Et ensuite, qu'en penses-tu ?" for _ in range(19)]
    for turn in turns:
        await router.respond_chat(turn)
    assert len(core.contexts) == 20
    assert {ctx[1] for ctx in core.contexts} == {"voice-long"}
    assert {ctx[2] for ctx in core.contexts} == {"chat"}
    assert {ctx[3] for ctx in core.contexts} == {"voice"}


def test_matrix_20_barge_ins_are_deterministic_and_leave_no_generation():
    for _ in range(20):
        tm = TurnManager(barge_in_on_vad=True)
        tm.state.set_mode("speaking")
        tm.state.current_generation_id = "answer"
        commands = tm.feed(VoiceEvent("vad.speech_started"))
        assert [c.name for c in commands][:4] == [
            "stop_playback", "clear_audio_queue", "cancel_tts", "cancel_llm",
        ]
        assert tm.state.current_generation_id is None


def test_matrix_sensitive_claim_is_held_until_canonical_proof():
    raw = "Les tests sont verts. Le rapport est disponible."
    held = plan_speech(raw, canonical_verified=False)
    proven = plan_speech(raw, canonical_verified=True)
    assert "tests sont verts" not in held.spoken.lower()
    assert "tests sont verts" in proven.spoken.lower()


def test_matrix_100_mixed_status_steering_and_interruptions_without_agents():
    orch = TaskOrchestrator(persistence_path=None)
    rec = orch.start_task(
        conversation_id="voice-mixed", channel="voice", message_preview="long",
        metadata={"kind": "voice_turn", "objective": "long"},
    )
    orch.mark_running(rec.task_id)
    registry = ActiveWorkRegistry(orch, "voice-mixed")
    for i in range(100):
        branch = i % 4
        if branch == 0:
            assert "travail est" in registry.status_text(rec.task_id).lower()
        elif branch == 1:
            registry.steer(rec.task_id, f"contrainte {i}")
            text, ids = consume_text_steering(orch, rec.task_id)
            assert f"contrainte {i}" in text and len(ids) == 1
        elif branch == 2:
            tm = TurnManager(barge_in_on_vad=True)
            tm.state.set_mode("speaking")
            assert any(c.name == "stop_playback" for c in tm.feed(VoiceEvent("vad.speech_started")))
        else:
            snap = registry.snapshot(rec.task_id)
            assert snap is not None and snap.state == "running"
    commands = orch.get_task(rec.task_id)["metadata"].get("steering_commands") or []
    assert all(c["status"] == "applied" for c in commands)


def test_matrix_screen_text_is_unchanged_by_speech_projection():
    screen = "**Rapport** dans C:\\secret\\report.json avec token=SUPERSECRET123."
    before = screen[:]
    spoken = plan_speech(screen, canonical_verified=True)
    assert screen == before
    assert "SUPERSECRET123" not in spoken.spoken
    assert "C:\\secret" not in spoken.spoken

