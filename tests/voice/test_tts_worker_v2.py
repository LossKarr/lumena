from __future__ import annotations

import asyncio

import pytest

from src.voice.v2.providers.base import CancelToken
from src.voice.v2.providers.local_tts import LocalTTSAdapter
from src.voice.v2.tts_worker import IsolatedTTSWorker


@pytest.mark.asyncio
async def test_worker_health_synthesis_and_clean_shutdown():
    worker = IsolatedTTSWorker(engine_kind="test", timeout_s=5)
    try:
        assert await worker.healthcheck() is True
        path = await worker._synthesize("Bonjour Lumena")
        assert path is not None and path.exists()
        assert worker._last_provider == "test-local"
        assert worker.alive is True
    finally:
        await worker.aclose()
    assert worker.alive is False


@pytest.mark.asyncio
async def test_worker_recovers_after_process_disappears():
    worker = IsolatedTTSWorker(engine_kind="test", timeout_s=5, max_restarts=1)
    try:
        assert await worker.healthcheck() is True
        first_pid = worker._process.pid
        worker._process.terminate()
        worker._process.join(timeout=2)
        assert await worker.healthcheck() is True
        assert worker._process.pid != first_pid
    finally:
        await worker.aclose()


@pytest.mark.asyncio
async def test_cancel_terminates_inflight_worker_without_orphan():
    worker = IsolatedTTSWorker(engine_kind="test", timeout_s=5)
    token = CancelToken()
    task = asyncio.create_task(worker._synthesize("__sleep__", _cancel_token=token))
    await asyncio.sleep(0.15)
    token.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker.alive is False
    await worker.aclose()


@pytest.mark.asyncio
async def test_task_cancellation_kills_non_cooperative_native_synthesis():
    worker = IsolatedTTSWorker(engine_kind="test", timeout_s=5)
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    unhandled = []

    def capture_unhandled(_loop, context):
        unhandled.append(context)

    loop.set_exception_handler(capture_unhandled)
    try:
        task = asyncio.create_task(worker._synthesize("__sleep__"))
        await asyncio.sleep(0.15)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # L'ancien waiter avait le temps d'observer le process fermé et levait
        # ensuite "TTS worker stopped" hors de toute tâche attendue.
        await asyncio.sleep(0.2)
        assert unhandled == []
        assert worker.alive is False
    finally:
        loop.set_exception_handler(previous_handler)
        await worker.aclose()


@pytest.mark.asyncio
async def test_local_adapter_propagates_worker_format_and_provider():
    worker = IsolatedTTSWorker(engine_kind="test", timeout_s=5)
    adapter = LocalTTSAdapter(tts=worker)
    try:
        result = await adapter.synthesize("Une phrase.", voice=None)
        assert result.ok is True
        assert result.provider == "test-local"
        assert result.audio_format == "pcm16"
        assert result.sample_rate == 16000
        assert result.channels == 1
    finally:
        await adapter.aclose()
