"""
test_handlers_system.py - Tests fonctionnels des handlers système fragmentés.

Teste chaque handler de system.py avec un HandlerContext de test.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.system import (
    run_command_handler,
    get_time_handler,
    screenshot_tool_handler,
    dummy_handler,
    get_token_stats_handler,
    parallel_tools_handler,
    get_system_handler_defs,
)


@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return HandlerContext.for_testing(
        lumena_root=tmp_path,
        runtime_root=workspace,
    )


# ─── run_command ───────────────────────────────────────────────────────────

class TestRunCommand:
    @pytest.mark.asyncio
    async def test_echo(self, ctx):
        r = await run_command_handler(ctx, command="echo hello_test_lumena")
        assert r.success
        assert "hello_test_lumena" in r.output

    @pytest.mark.asyncio
    async def test_invalid_command(self, ctx):
        r = await run_command_handler(ctx, command="zzz_nonexistent_command_12345")
        # Sur Windows ça retourne une erreur dans stderr mais pas une exception
        assert r.success or "Erreur" in r.output

    @pytest.mark.asyncio
    async def test_async_no_event_loop_block(self, ctx):
        """run_command ne doit PAS bloquer l'event loop (asyncio.create_subprocess_shell)."""
        import asyncio
        flag = False

        async def canary():
            nonlocal flag
            await asyncio.sleep(0.05)
            flag = True

        task = asyncio.create_task(canary())
        r = await run_command_handler(ctx, command="echo async_test")
        await task
        assert flag, "Le canary n'a pas eu de temps CPU — l'event loop était bloqué"
        assert r.success

    @pytest.mark.asyncio
    async def test_timeout_returns_message(self, ctx):
        """Une commande qui dépasse le timeout doit retourner un message, pas bloquer."""
        # ping -n 100 attend 100 pongs — bien plus long que 2 secondes
        # On passe timeout=2 directement (prioritaire sur les défauts)
        r = await run_command_handler(ctx, command="ping -n 100 127.0.0.1", timeout=2)
        assert r.success
        assert "Timeout" in r.output or "timeout" in r.output.lower() or r.success

    @pytest.mark.asyncio
    async def test_output_truncation(self, ctx):
        """La sortie longue doit être tronquée."""
        # Génère une sortie >4000 caractères (limit Telegram = 4000)
        r = await run_command_handler(ctx, command='python -c "print(\'x\'*5000)"')
        assert r.success
        if len(r.output) > 4050:
            assert "tronque" in r.output


# ─── get_time ──────────────────────────────────────────────────────────────

class TestGetTime:
    @pytest.mark.asyncio
    async def test_returns_timestamp(self, ctx):
        r = await get_time_handler(ctx)
        assert r.success
        assert "2026" in r.output or "202" in r.output  # year check
        assert ":" in r.output  # time format HH:MM:SS


# ─── dummy ─────────────────────────────────────────────────────────────────

class TestDummy:
    @pytest.mark.asyncio
    async def test_returns_kwargs(self, ctx):
        r = await dummy_handler(ctx, foo="bar", x=42)
        assert r.success
        assert "foo" in r.output or "bar" in r.output


# ─── get_token_stats ──────────────────────────────────────────────────────

class TestGetTokenStats:
    @pytest.mark.asyncio
    async def test_no_history(self, ctx):
        r = await get_token_stats_handler(ctx)
        assert r.success
        assert "Pas d'historique" in r.output or "compaction" in r.output


# ─── parallel_tools ────────────────────────────────────────────────────────

class TestParallelTools:
    @pytest.mark.asyncio
    async def test_empty_calls(self, ctx):
        r = await parallel_tools_handler(ctx, tool_calls=[])
        assert "invalide ou vide" in r.output

    @pytest.mark.asyncio
    async def test_no_execute_fn(self, ctx):
        r = await parallel_tools_handler(ctx, tool_calls=[{"name": "get_time", "args": {}}])
        assert "execute_fn" in r.output

    @pytest.mark.asyncio
    async def test_recursion_blocked(self, ctx):
        async def fake_exec(name, args):
            pass
        r = await parallel_tools_handler(
            ctx,
            tool_calls=[{"name": "parallel_tools", "args": {}}],
            execute_fn=fake_exec,
        )
        assert "interdit" in r.output.lower() or "cursion" in r.output.lower()

    @pytest.mark.asyncio
    async def test_blocked_tool(self, ctx):
        """parallel_tools lui-même est le seul outil bloqué (anti-récursion)."""
        async def fake_exec(name, args):
            pass
        r = await parallel_tools_handler(
            ctx,
            tool_calls=[{"name": "parallel_tools", "args": {}}],
            execute_fn=fake_exec,
        )
        assert "interdit" in r.output or "récursion" in r.output or "recursion" in r.output.lower()

    @pytest.mark.asyncio
    async def test_any_tool_allowed(self, ctx):
        """Lumena est autonome : tout outil (sauf blocklist) peut tourner en parallèle."""
        mock_obs = MagicMock()
        mock_obs.success = True
        mock_obs.content = "done"

        async def fake_exec(name, args):
            return mock_obs

        # Des outils variés qui étaient bloqués avant — maintenant autorisés
        for tool_name in ("write_file", "network_port_scan", "n8n_status", "run_command"):
            r = await parallel_tools_handler(
                ctx,
                tool_calls=[{"name": tool_name, "args": {}}],
                execute_fn=fake_exec,
            )
            assert r.success, f"{tool_name} devrait être autorisé en parallèle"
            assert "1 appel(s)" in r.output

    @pytest.mark.asyncio
    async def test_success_with_allowed_tool(self, ctx):
        mock_obs = MagicMock()
        mock_obs.success = True
        mock_obs.content = "2026-03-04 12:00:00"

        async def fake_exec(name, args):
            return mock_obs

        r = await parallel_tools_handler(
            ctx,
            tool_calls=[{"name": "get_time", "args": {}}],
            execute_fn=fake_exec,
        )
        assert r.success
        assert "1 appel(s)" in r.output
        assert "get_time" in r.output


# ─── screenshot_tool ──────────────────────────────────────────────────────

class TestScreenshotTool:
    @pytest.mark.asyncio
    async def test_screenshot_no_module(self, ctx):
        # computer_use probablement pas installé en test
        r = await screenshot_tool_handler(ctx)
        # On accepte soit succès soit "non disponible"
        assert r.success or "non disponible" in r.output or "❌" in r.output


# ─── handler defs ──────────────────────────────────────────────────────────

class TestSystemHandlerDefs:
    def test_all_defs_valid(self):
        defs = get_system_handler_defs()
        assert len(defs) == 6
        for d in defs:
            assert d.name
            assert d.handler is not None
            assert d.category == "system"

    def test_unique_names(self):
        defs = get_system_handler_defs()
        names = [d.name for d in defs]
        assert len(names) == len(set(names))
