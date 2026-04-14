from pathlib import Path
import sys
import asyncio

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests._server_compat import server_module
from src.telemetry import get_trace_bus, reset_trace_bus_for_tests


@pytest.mark.asyncio
async def test_trace_recent_endpoint_returns_events(monkeypatch):
    monkeypatch.setenv("LUMENA_TRACE_ENABLED", "1")
    reset_trace_bus_for_tests()
    bus = get_trace_bus()
    bus.clear_for_tests()
    bus.publish(
        {
            "trace_id": "trace-a",
            "turn_id": "turn-a",
            "stage": "input_received",
            "status": "start",
        }
    )

    payload = await server_module.get_trace_recent(limit=50)
    assert payload["count"] >= 1
    assert any(item["stage"] == "input_received" for item in payload["events"])


@pytest.mark.asyncio
async def test_trace_stream_endpoint_emits_trace_events(monkeypatch):
    monkeypatch.setenv("LUMENA_TRACE_ENABLED", "1")
    reset_trace_bus_for_tests()
    bus = get_trace_bus()
    bus.clear_for_tests()

    response = await server_module.trace_stream()
    iterator = response.body_iterator

    next_chunk = asyncio.create_task(iterator.__anext__())
    await asyncio.sleep(0.05)
    bus.publish(
        {
            "trace_id": "trace-b",
            "turn_id": "turn-b",
            "stage": "llm_request_done",
            "status": "ok",
        }
    )

    chunk = await asyncio.wait_for(next_chunk, timeout=2.0)
    text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
    assert "event: trace" in text
    assert "llm_request_done" in text

    # Best-effort cleanup for async generator based StreamingResponse.
    close = getattr(iterator, "aclose", None)
    if callable(close):
        await close()
