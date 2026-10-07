import asyncio

import pytest

from src.voice.v2.speech_coordinator import SpeechCoordinator


class _Runtime:
    def __init__(self):
        self.started = []
        self.events = {}

    async def speak(self, text, *, turn):
        generation = f"g{len(self.started) + 1}"
        self.started.append((generation, text, turn))
        self.events[generation] = asyncio.Event()
        return generation

    async def wait_finished(self, generation, *, timeout_s):
        await asyncio.wait_for(self.events[generation].wait(), timeout_s)
        return True

    def finish(self, generation):
        self.events[generation].set()


@pytest.mark.asyncio
async def test_speech_is_serial_and_priority_ordered():
    runtime = _Runtime()
    coordinator = SpeechCoordinator(runtime)
    await coordinator.say("jalon", kind="milestone", interruptible=False)
    await asyncio.sleep(0)
    await coordinator.say("statut", kind="status")
    await coordinator.say("final", kind="final")
    runtime.finish("g1")
    for _ in range(50):
        if len(runtime.started) >= 2:
            break
        await asyncio.sleep(0.005)
    assert runtime.started[1][1] == "final"
    runtime.finish("g2")
    for _ in range(50):
        if len(runtime.started) >= 3:
            break
        await asyncio.sleep(0.005)
    assert runtime.started[2][1] == "statut"
    runtime.finish("g3")
    assert await coordinator.wait_idle()
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_higher_priority_preempts_interruptible_speech():
    runtime = _Runtime()
    stops = []
    coordinator = SpeechCoordinator(runtime, stop_fn=lambda: stops.append("stop"))
    await coordinator.say("ancien jalon", kind="milestone")
    await asyncio.sleep(0)
    await coordinator.say("validation requise", kind="confirmation")
    for _ in range(50):
        if len(runtime.started) >= 2:
            break
        await asyncio.sleep(0.005)
    assert stops == ["stop"]
    assert runtime.started[1][1] == "validation requise"
    runtime.finish("g2")
    assert await coordinator.wait_idle()
    assert coordinator.status()["preemptions"] == 1
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_replace_key_keeps_only_latest_pending_milestone():
    runtime = _Runtime()
    coordinator = SpeechCoordinator(runtime)
    await coordinator.say("bloquant", kind="user", interruptible=False)
    await asyncio.sleep(0)
    for index in range(20):
        await coordinator.say(
            f"jalon {index}", kind="milestone", replace_key="activity",
        )
    runtime.finish("g1")
    for _ in range(50):
        if len(runtime.started) >= 2:
            break
        await asyncio.sleep(0.005)
    assert runtime.started[1][1] == "jalon 19"
    runtime.finish("g2")
    assert await coordinator.wait_idle()
    assert coordinator.status()["replaced"] == 19
    await coordinator.aclose()


@pytest.mark.asyncio
async def test_queue_is_bounded_under_stress_and_shutdown_leaves_no_worker():
    runtime = _Runtime()
    coordinator = SpeechCoordinator(runtime, max_queue=8)
    await coordinator.say("bloquant", kind="user", interruptible=False)
    await asyncio.sleep(0)
    for index in range(300):
        await coordinator.say(f"message {index}", kind="milestone")
    assert coordinator.status()["queue_depth"] <= 8
    await coordinator.aclose()
    assert coordinator._worker is None
