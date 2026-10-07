import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.runtime.task_steering import consume_text_steering
from src.runtime.work_registry import ActiveWorkRegistry, classify_work_turn
from src.voice.v2 import ConversationAudioLedger, TurnManager, VoiceEvent
from src.voice.v2.live import VoiceV2Live
from src.voice.v2.providers import FakeSTTProvider, FakeTTSProvider, FakeVADProvider
from src.voice.v2.session import VoiceSessionIdentity, VoiceSessionRouter


@pytest.mark.parametrize("text, expected", [
    ("attends", "pause"),
    ("continue", "resume"),
    ("non, plutôt une voiture", "steer"),
    ("also add a dark mode", "steer"),
    ("¿et pourquoi?", "conversation"),
])
def test_natural_work_interruption_intents(text, expected):
    assert classify_work_turn(text) == expected


def test_three_successive_orientations_keep_order_and_initial_objective(tmp_path):
    path = tmp_path / "tasks.json"
    orchestrator = TaskOrchestrator(persistence_path=path)
    record = orchestrator.start_task(
        conversation_id="voice-v3", channel="voice", message_preview="site moto",
        metadata={"kind": "voice_turn", "objective": "Créer le site moto"},
    )
    orchestrator.mark_running(record.task_id)
    registry = ActiveWorkRegistry(orchestrator, "voice-v3")
    inputs = [
        "non, plutôt une voiture", "ajoute également un configurateur",
        "ne touche plus au panier",
    ]
    commands = [registry.steer(record.task_id, value) for value in inputs]
    assert [item["sequence"] for item in commands] == [1, 2, 3]
    text, ids = consume_text_steering(orchestrator, record.task_id)
    assert all(value in text for value in inputs)
    assert len(ids) == 3
    task = orchestrator.get_task(record.task_id)
    assert task["metadata"]["objective"] == "Créer le site moto"


def test_pending_orientations_survive_orchestrator_restart(tmp_path):
    path = tmp_path / "tasks.json"
    first = TaskOrchestrator(persistence_path=path)
    record = first.start_task(
        conversation_id="voice-v3", channel="voice", message_preview="mission",
        metadata={"kind": "mission", "objective": "objectif initial"},
    )
    first.mark_running(record.task_id)
    ActiveWorkRegistry(first, "voice-v3").steer(record.task_id, "ajoute les tests")
    second = TaskOrchestrator(persistence_path=path)
    text, ids = consume_text_steering(second, record.task_id)
    assert "ajoute les tests" in text
    assert len(ids) == 1


def test_false_barge_in_resumes_only_unheard_remainder():
    ledger = ConversationAudioLedger()
    ledger.register_generation("turn-1", "gen-1", "Une phrase. La suite utile.")
    ledger.on_chunk_played("gen-1", "Une phrase.", 500, sequence=0)
    record = ledger.truncate("gen-1")
    assert record.text_played == "Une phrase."
    assert record.text_unplayed == "La suite utile."

    tm = TurnManager(barge_in_on_vad=True)
    tm.state.set_mode("speaking")
    tm.state.current_generation_id = "gen-1"
    tm.feed(VoiceEvent("vad.speech_started", t=100))
    tm.feed(VoiceEvent("vad.speech_ended", t=150))
    commands = tm.feed(VoiceEvent(
        "timer.endpoint", t=450,
        data={"turn_id": tm.state.current_turn_id, "pause_ms": 300},
    ))
    resume = next(command for command in commands if command.name == "resume_interrupted_speech")
    assert resume.data["generation_id"] == "gen-1"


def test_one_hundred_interruptions_never_keep_stale_generation():
    for index in range(100):
        tm = TurnManager(barge_in_on_vad=True)
        tm.state.set_mode("speaking")
        tm.state.current_generation_id = f"g-{index}"
        commands = tm.feed(VoiceEvent("vad.speech_started", t=index))
        names = [command.name for command in commands]
        assert names[:4] == [
            "stop_playback", "clear_audio_queue", "cancel_tts", "cancel_llm",
        ]
        assert tm.state.current_generation_id is None


def test_paired_owner_voice_registry_can_resolve_authorized_cross_channel_work(tmp_path):
    orchestrator = TaskOrchestrator(persistence_path=tmp_path / "tasks.json")
    web = orchestrator.start_task(
        conversation_id="web-conversation", channel="web", message_preview="mission",
        metadata={
            "kind": "mission", "objective": "objectif web",
            "owner_user_id": "local:owner", "source_channel": "web",
        },
    )
    orchestrator.mark_running(web.task_id)

    class _Core:
        task_orchestrator = orchestrator

    identity = VoiceSessionIdentity(
        user_id="local:owner", owner_user_id="local:owner",
        user_role="owner", profile_id=None, trusted=True,
    )
    session = VoiceSessionRouter(
        _Core(), conversation_id="voice-conversation", identity=identity,
    )
    live = VoiceV2Live(
        _Core(), vad=FakeVADProvider(), stt=FakeSTTProvider(),
        tts=FakeTTSProvider(), session_router=session,
    )
    snapshot, ambiguous = live.work_registry.resolve()
    assert ambiguous == []
    assert snapshot is not None and snapshot.task_id == web.task_id


def test_guest_voice_registry_stays_confined_to_voice_conversation(tmp_path):
    orchestrator = TaskOrchestrator(persistence_path=tmp_path / "tasks.json")
    web = orchestrator.start_task(
        conversation_id="web-conversation", channel="web", message_preview="mission",
        metadata={"kind": "mission", "objective": "objectif web"},
    )
    orchestrator.mark_running(web.task_id)

    class _Core:
        task_orchestrator = orchestrator

    identity = VoiceSessionIdentity(
        user_id="voice:guest", owner_user_id="local:owner",
        user_role="guest", profile_id=None, trusted=False,
    )
    session = VoiceSessionRouter(
        _Core(), conversation_id="voice-conversation", identity=identity,
    )
    live = VoiceV2Live(
        _Core(), vad=FakeVADProvider(), stt=FakeSTTProvider(),
        tts=FakeTTSProvider(), session_router=session,
    )
    assert live.work_registry.active_ids() == []

