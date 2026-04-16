from pathlib import Path
import sys
import asyncio

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.telemetry import (
    get_trace_bus,
    publish_trace,
    push_trace_context,
    pop_trace_context,
    reset_trace_bus_for_tests,
)


def test_trace_bus_ring_buffer_respects_maxlen(monkeypatch):
    monkeypatch.setenv("LUMENA_TRACE_ENABLED", "1")
    monkeypatch.setenv("LUMENA_TRACE_BUFFER_SIZE", "3")
    reset_trace_bus_for_tests()
    bus = get_trace_bus()
    bus.clear_for_tests()

    for idx in range(5):
        bus.publish(
            {
                "trace_id": "t",
                "turn_id": "u",
                "stage": f"s{idx}",
                "status": "ok",
            }
        )

    recent = bus.recent(10)
    assert len(recent) == 3
    assert [item["stage"] for item in recent] == ["s2", "s3", "s4"]


@pytest.mark.asyncio
async def test_trace_bus_subscriber_drops_oldest_when_queue_full(monkeypatch):
    monkeypatch.setenv("LUMENA_TRACE_ENABLED", "1")
    monkeypatch.setenv("LUMENA_TRACE_BUFFER_SIZE", "50")
    reset_trace_bus_for_tests()
    bus = get_trace_bus()
    bus.clear_for_tests()

    subscriber_id, queue = bus.subscribe(max_queue=2)
    try:
        for idx in range(3):
            bus.publish(
                {
                    "trace_id": "trace-x",
                    "turn_id": "turn-x",
                    "stage": f"stage-{idx}",
                    "status": "ok",
                }
            )
        await asyncio.sleep(0.05)

        first = queue.get_nowait()
        second = queue.get_nowait()
        assert first["stage"] == "stage-1"
        assert second["stage"] == "stage-2"
    finally:
        bus.unsubscribe(subscriber_id)


def test_publish_trace_uses_contextvars(monkeypatch):
    monkeypatch.setenv("LUMENA_TRACE_ENABLED", "1")
    reset_trace_bus_for_tests()
    bus = get_trace_bus()
    bus.clear_for_tests()

    tokens = push_trace_context(
        trace_id="trace-1",
        turn_id="turn-1",
        channel="web",
        client="cursor-ide-local",
        mode="chat",
        request_id="req-1",
        conversation_id="conv-1",
        task_id="task-1",
        force=True,
    )
    try:
        publish_trace(stage="input_received", status="start", summary="hello")
    finally:
        pop_trace_context(tokens)

    recent = bus.recent(5)
    assert recent
    event = recent[-1]
    assert event["trace_id"] == "trace-1"
    assert event["turn_id"] == "turn-1"
    assert event["channel"] == "web"
    assert event["client"] == "cursor-ide-local"
    assert event["mode"] == "chat"
    assert event["request_id"] == "req-1"
    assert event["conversation_id"] == "conv-1"
    assert event["task_id"] == "task-1"
    assert event["stage"] == "input_received"
