import asyncio
import io

import pytest

from src.tools.search_hub import SearchHub
from src.tools import ddg_worker


def test_ddg_worker_json_transport_is_utf8_binary(monkeypatch):
    class BinaryStdout:
        def __init__(self):
            self.buffer = io.BytesIO()

    output = BinaryStdout()
    monkeypatch.setattr(ddg_worker.sys, "stdout", output)
    ddg_worker._write_result(
        {"ok": True, "results": [{"title": "Rénovation énergétique ✅"}]}
    )
    decoded = output.buffer.getvalue().decode("utf-8")
    assert "Rénovation énergétique ✅" in decoded


@pytest.mark.asyncio
async def test_ddg_subprocess_timeout_is_killed(monkeypatch):
    class FrozenProcess:
        returncode = None

        def __init__(self):
            self.killed = False

        async def communicate(self, payload):
            await asyncio.sleep(5)

        def kill(self):
            self.killed = True

        async def wait(self):
            self.returncode = -9
            return self.returncode

    process = FrozenProcess()

    async def fake_create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    hub = SearchHub()
    hub._ddg_timeout_s = 0.05

    with pytest.raises(asyncio.TimeoutError):
        await hub._run_ddg_subprocess("requête bloquée", 2)
    assert process.killed is True


@pytest.mark.asyncio
async def test_ddg_subprocess_is_killed_when_parent_is_cancelled(monkeypatch):
    class FrozenProcess:
        returncode = None

        def __init__(self):
            self.killed = False

        async def communicate(self, payload):
            await asyncio.sleep(5)

        def kill(self):
            self.killed = True

        async def wait(self):
            self.returncode = -9
            return self.returncode

    process = FrozenProcess()

    async def fake_create(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    hub = SearchHub()
    task = asyncio.create_task(hub._run_ddg_subprocess("annulation", 2))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.killed is True


@pytest.mark.asyncio
async def test_ddg_timeout_opens_circuit_and_guides_strategy(monkeypatch):
    hub = SearchHub()
    hub._ddg_breaker_s = 30.0
    calls = 0

    async def frozen(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise asyncio.TimeoutError

    monkeypatch.setattr(hub, "_run_ddg_subprocess", frozen)
    first = await hub._ddg_search("isolation thermique", 2)
    second = await hub._ddg_search("pompe à chaleur", 2)

    assert first["source"] == "error"
    assert "arrêté" in first["error"]
    assert "Change de stratégie" in first["error"]
    assert second["source"] == "error"
    assert "temporairement indisponible" in second["error"]
    assert calls == 1


@pytest.mark.asyncio
async def test_ddg_success_keeps_structured_urls(monkeypatch):
    hub = SearchHub()

    async def success(*args, **kwargs):
        return [{"title": "ADEME", "href": "https://ademe.fr", "body": "Guide"}]

    monkeypatch.setattr(hub, "_run_ddg_subprocess", success)
    result = await hub._ddg_search("énergie", 1)

    assert result == {
        "source": "DuckDuckGo",
        "query": "énergie",
        "results": [
            {"title": "ADEME", "url": "https://ademe.fr", "description": "Guide"}
        ],
    }
