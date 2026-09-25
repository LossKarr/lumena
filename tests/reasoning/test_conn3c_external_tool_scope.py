"""Run/fallback scope and async-generator ContextVar ownership."""
import asyncio
from unittest.mock import Mock

import pytest

from src.reasoning.external_tool_registry import ExternalToolCatalog, bind_external_catalog
from src.reasoning.external_tool_scope import external_tool_run, run_external_catalog, scoped_external_tools


def test_nested_scopes_capture_once_next_run_refreshes_and_explicit_binding_wins():
    catalogs = [ExternalToolCatalog(()), ExternalToolCatalog(())]
    factory = Mock(side_effect=catalogs)
    with external_tool_run():
        first = run_external_catalog("root", factory)
        with external_tool_run():
            assert run_external_catalog("root", factory) is first
        explicit = ExternalToolCatalog(())
        with bind_external_catalog(explicit):
            assert run_external_catalog("root", factory) is explicit
        assert run_external_catalog("root", factory) is first
    with external_tool_run():
        assert run_external_catalog("root", factory) is catalogs[1]
    assert factory.call_count == 2


@pytest.mark.asyncio
async def test_parallel_runs_are_isolated_while_children_and_fallback_share_the_run():
    factory = Mock(side_effect=lambda: ExternalToolCatalog(()))
    ready = asyncio.Event()
    captured = []

    @scoped_external_tools
    async def child():
        return await asyncio.to_thread(run_external_catalog, "root", factory)

    @scoped_external_tools
    async def run():
        catalog = run_external_catalog("root", factory)
        captured.append(catalog)
        if len(captured) == 2:
            ready.set()
        await ready.wait()
        assert await child() is catalog
        assert await child() is catalog  # model fallback, same run

    await asyncio.gather(run(), run())
    assert captured[0] is not captured[1]
    assert factory.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["exhaust", "close", "cancel"])
async def test_stream_can_be_resumed_and_closed_in_different_tasks_without_leaking_scope(finish):
    factory = Mock(side_effect=lambda: ExternalToolCatalog(()))
    cleanup = []
    entered = asyncio.Event()
    wait = asyncio.Event()

    @scoped_external_tools
    async def source():
        first = run_external_catalog("root", factory)
        try:
            yield first
            assert run_external_catalog("root", factory) is first
            if finish == "cancel":
                entered.set()
                await wait.wait()
            yield first
        finally:
            cleanup.append(run_external_catalog("root", factory))

    iterator = source()
    first = await asyncio.create_task(anext(iterator))
    assert run_external_catalog("root", factory) is not first
    if finish == "cancel":
        task = asyncio.create_task(anext(iterator))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    elif finish == "close":
        await asyncio.create_task(iterator.aclose())
    else:
        assert await asyncio.create_task(anext(iterator)) is first
        with pytest.raises(StopAsyncIteration):
            await asyncio.create_task(anext(iterator))
    assert cleanup == [first]
    assert run_external_catalog("root", factory) is not first


@pytest.mark.asyncio
async def test_coroutine_exception_restores_scope():
    factory = Mock(side_effect=lambda: ExternalToolCatalog(()))
    seen = []

    @scoped_external_tools
    async def fails():
        seen.append(run_external_catalog("root", factory))
        raise RuntimeError("sentinel")

    with pytest.raises(RuntimeError, match="sentinel"):
        await fails()
    assert run_external_catalog("root", factory) is not seen[0]
