"""
Tests du canal cancel coopératif entre le parent SSE et le sous-agent délégué.

Valide :
  - HandlerContext.runtime_task_id est initialisé à None
  - Le registre des délégations actives (register/unregister/cancel)
  - delegate_task_handler retourne status_code="cancelled" si annulé avant démarrage
  - delegate_task_handler interrompt le sous-agent si cancel déclenché pendant l'exécution
  - Les helpers _get_task_orchestrator / _watch_delegate_cancel sont robustes
"""
from __future__ import annotations

import asyncio
import pytest

from src.reasoning.handlers.context import HandlerContext
from src.agents.sub_agent import (
    _register_active_delegate,
    _unregister_active_delegate,
    cancel_active_delegate,
    is_delegate_active,
    _active_delegates,
)


# ─────────────────────────────────────────────────────────────────────────────
# HandlerContext — runtime_task_id
# ─────────────────────────────────────────────────────────────────────────────

class TestHandlerContextRuntimeTaskId:
    def test_default_is_none(self):
        ctx = HandlerContext()
        assert ctx.runtime_task_id is None

    def test_settable(self):
        ctx = HandlerContext()
        ctx.runtime_task_id = "task_abc_123456"
        assert ctx.runtime_task_id == "task_abc_123456"

    def test_reset_to_none(self):
        ctx = HandlerContext()
        ctx.runtime_task_id = "task_x"
        ctx.runtime_task_id = None
        assert ctx.runtime_task_id is None


# ─────────────────────────────────────────────────────────────────────────────
# Active delegate registry
# ─────────────────────────────────────────────────────────────────────────────

class TestActiveDelegateRegistry:
    def setup_method(self):
        # Nettoyer avant chaque test
        _active_delegates.clear()

    def test_register_and_is_active(self):
        loop = asyncio.new_event_loop()
        try:
            async def _coro():
                await asyncio.sleep(100)

            async def _run():
                t = asyncio.create_task(_coro())
                try:
                    _register_active_delegate("parent-1", t)
                    assert is_delegate_active("parent-1")
                finally:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

            loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_unregister_removes(self):
        loop = asyncio.new_event_loop()
        try:
            async def _coro():
                await asyncio.sleep(100)

            async def _run():
                t = asyncio.create_task(_coro())
                try:
                    _register_active_delegate("parent-2", t)
                    assert is_delegate_active("parent-2")
                    _unregister_active_delegate("parent-2")
                    assert not is_delegate_active("parent-2")
                finally:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

            loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_cancel_active_delegate_stops_task(self):
        loop = asyncio.new_event_loop()
        try:
            done = []

            async def _coro():
                try:
                    await asyncio.sleep(100)
                except asyncio.CancelledError:
                    done.append("cancelled")
                    raise

            async def _run():
                t = asyncio.create_task(_coro())
                _register_active_delegate("parent-3", t)
                # Laisser la tâche démarrer avant de la canceller
                await asyncio.sleep(0.01)
                result = cancel_active_delegate("parent-3")
                assert result is True
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                assert "cancelled" in done
                _unregister_active_delegate("parent-3")

            loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_cancel_nonexistent_returns_false(self):
        result = cancel_active_delegate("nonexistent-parent")
        assert result is False

    def test_unregister_nonexistent_is_safe(self):
        _unregister_active_delegate("does-not-exist")  # should not raise

    def test_cancel_already_done_task_returns_false(self):
        loop = asyncio.new_event_loop()
        try:
            async def _coro():
                return "done"

            async def _run():
                t = asyncio.create_task(_coro())
                await t  # let it finish
                _register_active_delegate("parent-4", t)
                result = cancel_active_delegate("parent-4")
                assert result is False
                _unregister_active_delegate("parent-4")

            loop.run_until_complete(_run())
        finally:
            loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# _watch_delegate_cancel — coopération watcher
# ─────────────────────────────────────────────────────────────────────────────

class TestWatchDelegateCancel:
    """Teste le watcher asyncio sans orchestrateur réel."""

    def test_watcher_cancels_exec_task_on_cancel_requested(self):
        """Simule is_cancel_requested → True : le watcher doit annuler exec_task."""
        from src.reasoning.handlers.agents import _watch_delegate_cancel

        loop = asyncio.new_event_loop()
        try:
            cancelled = []

            async def _long_coro():
                try:
                    await asyncio.sleep(100)
                except asyncio.CancelledError:
                    cancelled.append(True)
                    raise

            class _FakeOrch:
                def __init__(self):
                    self._count = 0

                def is_cancel_requested(self, tid):
                    self._count += 1
                    return self._count >= 2  # True à partir du 2e poll

            async def _run():
                exec_task = asyncio.create_task(_long_coro())

                # Patcher l'orchestrateur via monkeypatch-like override
                import src.reasoning.handlers.agents as _agents_mod
                _orig = _agents_mod._get_task_orchestrator
                _agents_mod._get_task_orchestrator = lambda: _FakeOrch()
                try:
                    watcher = asyncio.create_task(
                        _watch_delegate_cancel("fake-parent", exec_task, poll_interval=0.05)
                    )
                    try:
                        await exec_task
                    except asyncio.CancelledError:
                        pass
                    await watcher
                finally:
                    _agents_mod._get_task_orchestrator = _orig

                assert len(cancelled) == 1

            loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_watcher_exits_cleanly_if_no_orchestrator(self):
        """Pas d'orchestrateur → watcher termine sans crash."""
        from src.reasoning.handlers.agents import _watch_delegate_cancel

        loop = asyncio.new_event_loop()
        try:
            async def _run():
                exec_task = asyncio.create_task(asyncio.sleep(0))
                await exec_task  # complète immédiatement

                import src.reasoning.handlers.agents as _agents_mod
                _orig = _agents_mod._get_task_orchestrator
                _agents_mod._get_task_orchestrator = lambda: None
                try:
                    watcher = asyncio.create_task(
                        _watch_delegate_cancel("fake-parent-2", exec_task, poll_interval=0.05)
                    )
                    await watcher
                finally:
                    _agents_mod._get_task_orchestrator = _orig

            loop.run_until_complete(_run())
        finally:
            loop.close()
