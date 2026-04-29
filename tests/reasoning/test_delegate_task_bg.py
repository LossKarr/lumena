"""
Tests du durcissement delegate_task_bg :
  - registre _bg_agent_tasks (register/unregister/cancel/is_active)
  - run_task_bg : statut running → done après complétion
  - run_task_bg : annulation réelle, statut cancelled
  - cleanup registre après done
  - cleanup registre après cancel
  - statut cohérent avant / pendant / après
  - pas de callback de progression après annulation
  - bg_cancel_handler route vers cancel_bg_agent_task pour ca_*
  - bg_status_handler statut cancelled
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.sub_agent import (
    _register_bg_agent,
    _unregister_bg_agent,
    cancel_bg_agent_task,
    is_bg_agent_active,
    _bg_agent_tasks,
)


# ─────────────────────────────────────────────────────────────────────────────
# Registre _bg_agent_tasks
# ─────────────────────────────────────────────────────────────────────────────

class TestBgAgentRegistry:
    def setup_method(self):
        _bg_agent_tasks.clear()

    def test_register_and_is_active(self):
        loop = asyncio.new_event_loop()
        try:
            async def _run():
                t = asyncio.create_task(asyncio.sleep(100))
                try:
                    _register_bg_agent("ca_1", t)
                    assert is_bg_agent_active("ca_1")
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
            async def _run():
                t = asyncio.create_task(asyncio.sleep(100))
                try:
                    _register_bg_agent("ca_2", t)
                    _unregister_bg_agent("ca_2")
                    assert not is_bg_agent_active("ca_2")
                finally:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
            loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_cancel_active_task(self):
        loop = asyncio.new_event_loop()
        try:
            cancelled = []

            async def _slow():
                try:
                    await asyncio.sleep(100)
                except asyncio.CancelledError:
                    cancelled.append(True)
                    raise

            async def _run():
                t = asyncio.create_task(_slow())
                _register_bg_agent("ca_3", t)
                await asyncio.sleep(0.01)
                result = cancel_bg_agent_task("ca_3")
                assert result is True
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                assert cancelled == [True]
                _unregister_bg_agent("ca_3")

            loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_cancel_nonexistent_returns_false(self):
        assert cancel_bg_agent_task("ca_nonexistent") is False

    def test_cancel_done_task_returns_false(self):
        loop = asyncio.new_event_loop()
        try:
            async def _run():
                t = asyncio.create_task(asyncio.sleep(0))
                await t
                _register_bg_agent("ca_4", t)
                result = cancel_bg_agent_task("ca_4")
                assert result is False
                _unregister_bg_agent("ca_4")
            loop.run_until_complete(_run())
        finally:
            loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# run_task_bg — statut et cleanup
# ─────────────────────────────────────────────────────────────────────────────

def _make_orchestrator():
    """Stub minimal de SubAgentOrchestrator pour tester run_task_bg."""
    from unittest.mock import MagicMock
    from src.agents.sub_agent import AgentResult

    orch = MagicMock()
    orch.task_counter = 0
    orch.pending_tasks = {}

    def _save():
        pass
    orch._save_to_disk = _save
    return orch


class TestRunTaskBg:
    def setup_method(self):
        _bg_agent_tasks.clear()

    def test_status_running_then_done(self):
        """Après exécution, statut passe de running → done."""
        loop = asyncio.new_event_loop()
        try:
            from src.agents.sub_agent import AgentResult, AgentType

            orch = _make_orchestrator()
            result_obj = AgentResult(
                success=True, output="tout bon", task_id="ca_x"
            )

            async def _fake_execute(task):
                await asyncio.sleep(0.01)
                return result_obj

            orch.execute_task = _fake_execute

            # Bind run_task_bg à notre orch mock
            from src.agents.sub_agent import SubAgentOrchestrator
            run_bg = SubAgentOrchestrator.run_task_bg.__get__(orch, type(orch))

            async def _run():
                task_id = await run_bg("do something", AgentType.GENERAL, {})
                assert orch.pending_tasks[task_id]["status"] == "running"
                assert is_bg_agent_active(task_id)
                # Laisser la tâche se terminer
                await asyncio.sleep(0.1)
                assert orch.pending_tasks[task_id]["status"] == "done"
                assert not is_bg_agent_active(task_id)

            loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_cleanup_after_done(self):
        """Le registre est nettoyé après complétion."""
        loop = asyncio.new_event_loop()
        try:
            from src.agents.sub_agent import AgentResult, AgentType

            orch = _make_orchestrator()
            result_obj = AgentResult(success=True, output="ok", task_id="ca_y")

            async def _fake_execute(task):
                return result_obj

            orch.execute_task = _fake_execute
            from src.agents.sub_agent import SubAgentOrchestrator
            run_bg = SubAgentOrchestrator.run_task_bg.__get__(orch, type(orch))

            async def _run():
                task_id = await run_bg("test", AgentType.GENERAL, {})
                await asyncio.sleep(0.05)
                assert not is_bg_agent_active(task_id)

            loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_cancel_sets_status_cancelled(self):
        """Annulation réelle → statut 'cancelled' dans pending_tasks."""
        loop = asyncio.new_event_loop()
        try:
            from src.agents.sub_agent import AgentType

            orch = _make_orchestrator()

            async def _never_ending(task):
                await asyncio.sleep(100)

            orch.execute_task = _never_ending
            from src.agents.sub_agent import SubAgentOrchestrator
            run_bg = SubAgentOrchestrator.run_task_bg.__get__(orch, type(orch))

            async def _run():
                task_id = await run_bg("long task", AgentType.GENERAL, {})
                await asyncio.sleep(0.01)
                assert is_bg_agent_active(task_id)
                result = cancel_bg_agent_task(task_id)
                assert result is True
                await asyncio.sleep(0.05)
                assert orch.pending_tasks[task_id]["status"] == "cancelled"
                assert not is_bg_agent_active(task_id)

            loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_cleanup_after_cancel(self):
        """Registre nettoyé après annulation."""
        loop = asyncio.new_event_loop()
        try:
            from src.agents.sub_agent import AgentType

            orch = _make_orchestrator()

            async def _never_ending(task):
                await asyncio.sleep(100)

            orch.execute_task = _never_ending
            from src.agents.sub_agent import SubAgentOrchestrator
            run_bg = SubAgentOrchestrator.run_task_bg.__get__(orch, type(orch))

            async def _run():
                task_id = await run_bg("long task", AgentType.GENERAL, {})
                await asyncio.sleep(0.01)
                cancel_bg_agent_task(task_id)
                await asyncio.sleep(0.05)
                assert not is_bg_agent_active(task_id)
                assert task_id not in _bg_agent_tasks

            loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_no_progress_callback_after_cancel(self):
        """Le progress_callback n'est PAS appelé après CancelledError."""
        loop = asyncio.new_event_loop()
        try:
            from src.agents.sub_agent import AgentType

            orch = _make_orchestrator()
            callback_calls = []

            async def _never_ending(task):
                await asyncio.sleep(100)

            orch.execute_task = _never_ending
            from src.agents.sub_agent import SubAgentOrchestrator
            run_bg = SubAgentOrchestrator.run_task_bg.__get__(orch, type(orch))

            def _cb(msg):
                callback_calls.append(msg)

            async def _run():
                task_id = await run_bg("task", AgentType.GENERAL, {}, progress_callback=_cb)
                await asyncio.sleep(0.01)
                cancel_bg_agent_task(task_id)
                await asyncio.sleep(0.05)
                # Aucun callback ne doit avoir été déclenché
                assert callback_calls == []

            loop.run_until_complete(_run())
        finally:
            loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# bg_cancel_handler — routing ca_*
# ─────────────────────────────────────────────────────────────────────────────

class TestBgCancelHandlerRouting:
    def setup_method(self):
        _bg_agent_tasks.clear()

    def test_cancel_agent_task_routes_correctly(self):
        """bg_cancel_handler appelle cancel_bg_agent_task pour ca_*."""
        loop = asyncio.new_event_loop()
        try:
            async def _run():
                # Enregistrer une fausse tâche active
                t = asyncio.create_task(asyncio.sleep(100))
                _register_bg_agent("ca_999_120000", t)

                from src.reasoning.handlers.agents import bg_cancel_handler
                from src.reasoning.handlers.context import HandlerContext
                ctx = HandlerContext()
                result = await bg_cancel_handler(ctx, task_id="ca_999_120000")

                assert result.success is True
                assert "annulée" in result.output
                assert result.status_code == "cancelled"
                try:
                    await t
                except asyncio.CancelledError:
                    pass

            loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_cancel_nonexistent_agent_task(self):
        """bg_cancel_handler retourne fail si tâche agent introuvable."""
        loop = asyncio.new_event_loop()
        try:
            async def _run():
                from src.reasoning.handlers.agents import bg_cancel_handler
                from src.reasoning.handlers.context import HandlerContext
                # Pas d'import background.manager disponible → on bypasse avec patch
                with patch(
                    "src.reasoning.handlers.agents.bg_cancel_handler.__module__",
                    new="test",
                ):
                    ctx = HandlerContext()
                    # cancel_bg_agent_task retournera False (inexistant)
                    # puis tente background.manager qui peut lever ImportError
                    result = await bg_cancel_handler(ctx, task_id="ca_does_not_exist")
                    # Fail ou ImportError → pas de succès
                    assert not result.success

            loop.run_until_complete(_run())
        finally:
            loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# bg_status_handler — statut cancelled
# ─────────────────────────────────────────────────────────────────────────────

class TestBgStatusCancelled:
    def test_cancelled_status_code(self):
        """bg_status_handler retourne status_code='cancelled' pour une tâche annulée."""
        loop = asyncio.new_event_loop()
        try:
            async def _run():
                from src.reasoning.handlers.agents import bg_status_handler
                from src.reasoning.handlers.context import HandlerContext
                from unittest.mock import MagicMock, patch

                mock_orch = MagicMock()
                mock_orch.pending_tasks = {
                    "ca_7": {
                        "status": "cancelled",
                        "description": "test task",
                        "started_at": "2026-04-29T12:00:00",
                        "finished_at": "2026-04-29T12:00:05",
                        "output": None,
                    }
                }

                ctx = HandlerContext()
                with patch(
                    "src.reasoning.handlers.agents.bg_status_handler",
                    wraps=bg_status_handler,
                ):
                    # Patch get_orchestrator dans sub_agent
                    with patch(
                        "src.agents.sub_agent.get_orchestrator",
                        return_value=mock_orch,
                    ):
                        result = await bg_status_handler(ctx, task_id="ca_7")

                assert result.status_code == "cancelled"
                assert "🚫" in result.output

            loop.run_until_complete(_run())
        finally:
            loop.close()
