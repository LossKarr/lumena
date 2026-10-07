import time

from src.reasoning.public_activity import (
    PublicActivityBus,
    build_public_activity_event,
    notify_step_callback,
)
from src.reasoning.response_parser import parse_response


def test_parser_keeps_optional_public_update_out_of_private_thought():
    thought, action, _, _ = parse_response(
        "THOUGHT: Je dois inspecter un secret interne.\n"
        "PUBLIC_UPDATE: Je vérifie d'abord comment cette option est chargée.\n"
        "ACTION: read_file\nACTION_INPUT: {\"path\":\"config.py\"}"
    )
    assert thought.content == "Je dois inspecter un secret interne."
    assert action.public_update == "Je vérifie d'abord comment cette option est chargée."


def test_absent_public_update_preserves_existing_parse_contract():
    _, action, _, _ = parse_response(
        "THOUGHT: Lire.\nACTION: read_file\nACTION_INPUT: {\"path\":\"x.py\"}"
    )
    assert action.public_update is None
    assert action.tool_name == "read_file"
    assert action.tool_args == {"path": "x.py"}


def test_public_activity_refuses_private_secret_path_and_unproved_success():
    rejected = [
        "THOUGHT: je vais tout révéler",
        "Ma clé API_KEY=secret est prête.",
        r"Je lis C:\\Users\\alice\\secret.txt.",
        "Les tests sont passés.",
    ]
    for text in rejected:
        assert build_public_activity_event(text, tool_name="read_file") is None


def test_success_requires_bounded_proof_reference():
    event = build_public_activity_event(
        "Les tests sont passés.", tool_name="run_tests", phase="success",
        proof_refs=("ledger:test:sha256:abcd",),
    )
    assert event is not None
    assert event.proof_refs == ("ledger:test:sha256:abcd",)
    assert "public_update" not in event.trace_digest()


def test_bounded_bus_drops_oldest_without_blocking():
    bus = PublicActivityBus(max_events=2)
    for index in range(3):
        event = build_public_activity_event(
            f"Je vérifie le point {index}.", tool_name="read_file",
        )
        bus.publish(event)
    assert len(bus) == 2
    assert bus.dropped == 1
    assert [event.public_update for event in bus.drain()] == [
        "Je vérifie le point 1.", "Je vérifie le point 2.",
    ]


def test_legacy_and_event_callbacks_remain_compatible_and_fail_open():
    legacy_calls = []
    event_calls = []
    notify_step_callback(lambda name, args: legacy_calls.append((name, args)), "read_file", {})
    notify_step_callback(
        lambda name, args, event: event_calls.append((name, event.public_update)),
        "read_file", {}, public_update="Je vérifie la configuration.",
    )
    notify_step_callback(lambda *_: (_ for _ in ()).throw(RuntimeError("boom")), "read_file", {})
    assert legacy_calls == [("read_file", {})]
    assert event_calls == [("read_file", "Je vérifie la configuration.")]


def test_callback_adapter_hot_path_is_sub_millisecond_without_callback():
    started = time.perf_counter()
    for _ in range(1_000):
        notify_step_callback(None, "read_file", {})
    average_ms = (time.perf_counter() - started) * 1_000 / 1_000
    assert average_ms < 1.0
