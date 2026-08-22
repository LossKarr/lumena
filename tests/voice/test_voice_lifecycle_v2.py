import asyncio

import pytest

from src.core_services.agent_service import _should_auto_speak
from src.voice.lifecycle import VoiceLifecycleManager, select_voice_backend
from src.voice.v2.supervisor import VoiceV2Manager, normalize_voice_mode


class _FakeBackend:
    def __init__(self, name: str):
        self.name = name
        self.running = False
        self.state = "stopped"
        self.start_calls = []
        self.stop_calls = 0
        self.task = None

    async def start(self, core, **kwargs):
        self.start_calls.append((core, kwargs))
        self.running = True
        self.state = "running"
        return True

    async def stop(self):
        self.stop_calls += 1
        self.running = False
        self.state = "stopped"

    def get_status(self):
        return {"running": self.running, "backend": self.name, "state": self.state}


def test_product_modes_never_accept_direct():
    assert normalize_voice_mode("chat") == "chat"
    assert normalize_voice_mode("agent") == "agent"
    assert normalize_voice_mode("direct") == "chat"
    assert normalize_voice_mode("unknown") == "chat"
    assert normalize_voice_mode(None) == "chat"


def test_backend_selection_keeps_legacy_as_default():
    assert select_voice_backend(v2_enabled=False) == "legacy"
    assert select_voice_backend(v2_enabled=True) == "v2"


@pytest.mark.asyncio
async def test_v2_manager_starts_official_chat_and_stops_cleanly():
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def runner(core, **kwargs):
        calls.append((core, kwargs))
        entered.set()
        await release.wait()

    mgr = VoiceV2Manager(runner=runner, max_restarts=0)
    assert await mgr.start("core", mode="direct") is True
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert calls == [("core", {"disable_tools": False, "llm_mode": "core_chat"})]
    assert mgr.get_status()["mode"] == "chat"
    await mgr.stop()
    assert mgr.running is False
    assert mgr.state == "stopped"


@pytest.mark.asyncio
async def test_v2_manager_routes_agent_without_parallel_direct_path():
    entered = asyncio.Event()

    async def runner(_core, **kwargs):
        assert kwargs == {"disable_tools": False, "llm_mode": "agent"}
        entered.set()
        await asyncio.Event().wait()

    mgr = VoiceV2Manager(runner=runner, max_restarts=0)
    await mgr.start(object(), mode="agent")
    await asyncio.wait_for(entered.wait(), timeout=1)
    await mgr.stop()


@pytest.mark.asyncio
async def test_v2_supervision_is_bounded():
    calls = 0
    sleeps = []

    async def failing_runner(_core, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("micro down")

    async def no_wait(delay):
        sleeps.append(delay)

    mgr = VoiceV2Manager(
        runner=failing_runner,
        max_restarts=2,
        restart_backoff_s=0.5,
        sleep_fn=no_wait,
    )
    await mgr.start(object())
    await asyncio.wait_for(mgr.task, timeout=1)
    assert calls == 3
    assert sleeps == [0.5, 1.0]
    assert mgr.restarts == 2
    assert mgr.state == "error"
    assert mgr.running is False
    assert "micro down" in mgr.last_error


@pytest.mark.asyncio
async def test_lifecycle_v2_off_is_exact_legacy(monkeypatch):
    monkeypatch.setenv("LUMENA_VOICE_V2_AUTO", "0")
    legacy = _FakeBackend("legacy")
    v2 = _FakeBackend("v2")
    mgr = VoiceLifecycleManager(legacy=legacy, v2=v2)
    assert await mgr.start("core") is True
    assert len(legacy.start_calls) == 1
    assert v2.start_calls == []
    assert mgr.get_status()["backend"] == "legacy"
    await mgr.stop()


@pytest.mark.asyncio
async def test_lifecycle_v2_on_never_starts_legacy_concurrently(monkeypatch):
    monkeypatch.setenv("LUMENA_VOICE_V2_AUTO", "1")
    monkeypatch.setenv("LUMENA_VOICE_V2_MODE", "agent")
    legacy = _FakeBackend("legacy")
    v2 = _FakeBackend("v2")
    v2.task = asyncio.create_task(asyncio.Event().wait())
    mgr = VoiceLifecycleManager(legacy=legacy, v2=v2)
    assert await mgr.start("core") is True
    assert legacy.start_calls == []
    assert v2.start_calls[0][1] == {"mode": "agent"}
    assert mgr.get_status()["backend"] == "v2"
    await mgr.stop()
    v2.task.cancel()


@pytest.mark.asyncio
async def test_lifecycle_fallback_waits_for_terminal_v2_failure(monkeypatch):
    monkeypatch.setenv("LUMENA_VOICE_V2_AUTO", "1")
    monkeypatch.setenv("LUMENA_VOICE_V2_FALLBACK_LEGACY", "1")
    legacy = _FakeBackend("legacy")
    v2 = _FakeBackend("v2")

    async def v2_start(core, **kwargs):
        v2.start_calls.append((core, kwargs))
        v2.running = False
        v2.state = "error"
        v2.task = asyncio.create_task(asyncio.sleep(0))
        return True

    v2.start = v2_start
    mgr = VoiceLifecycleManager(legacy=legacy, v2=v2)
    await mgr.start("core")
    assert legacy.start_calls == []
    await asyncio.wait_for(mgr._monitor_task, timeout=1)
    assert len(legacy.start_calls) == 1
    assert mgr.active_backend == "legacy"
    assert mgr.get_status()["fallback_used"] is True
    await mgr.stop()


@pytest.mark.asyncio
async def test_lifecycle_falls_back_when_v2_start_fails_immediately(monkeypatch):
    monkeypatch.setenv("LUMENA_VOICE_V2_AUTO", "1")
    monkeypatch.setenv("LUMENA_VOICE_V2_FALLBACK_LEGACY", "1")
    legacy = _FakeBackend("legacy")
    v2 = _FakeBackend("v2")

    async def rejected_start(_core, **_kwargs):
        v2.state = "error"
        return False

    v2.start = rejected_start
    mgr = VoiceLifecycleManager(legacy=legacy, v2=v2)
    assert await mgr.start("core") is True
    assert len(legacy.start_calls) == 1
    assert mgr.active_backend == "legacy"
    assert mgr.get_status()["fallback_used"] is True
    await mgr.stop()


def test_voice_runtime_owns_voice_audio_without_changing_other_channels():
    assert _should_auto_speak(True, "voice") is False
    assert _should_auto_speak(True, "web") is True
    assert _should_auto_speak(True, "telegram") is True
    assert _should_auto_speak(False, "web") is False
