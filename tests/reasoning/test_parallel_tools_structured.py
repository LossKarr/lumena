"""
Tests des résultats structurés de parallel_tools.

Valide :
  - parallel_tools_handler produit SubToolResult par sous-appel
  - succès total, partiel et échec total → HandlerResult.success cohérent
  - sub_results transmis via _parallel_tools_wrapper dans l'Observation
  - ToolRegistry.execute() retourne l'Observation structurée directement
  - Ledger expansion : les sub_results génèrent des entrées individuelles
"""
from __future__ import annotations

import asyncio
import pytest

from src.reasoning.handlers.contracts import HandlerResult, SubToolResult
from src.reasoning.handlers.context import HandlerContext
from src.reasoning.react_config import Observation


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_obs(content: str, success: bool = True) -> Observation:
    return Observation(content=content, success=success)


async def _execute_fn_ok(name: str, args: dict) -> Observation:
    return _make_obs(f"ok: {name}", success=True)


async def _execute_fn_fail(name: str, args: dict) -> Observation:
    return _make_obs(f"fail: {name}", success=False)


async def _execute_fn_raise(name: str, args: dict) -> Observation:
    raise RuntimeError(f"crash in {name}")


# ─────────────────────────────────────────────────────────────────────────────
# parallel_tools_handler — résultats structurés
# ─────────────────────────────────────────────────────────────────────────────

class TestParallelToolsStructured:
    from src.reasoning.handlers.system import parallel_tools_handler as _handler

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def _ctx(self) -> HandlerContext:
        return HandlerContext()

    def test_total_success_sub_results(self):
        from src.reasoning.handlers.system import parallel_tools_handler
        calls = [
            {"name": "tool_a", "args": {"x": 1}},
            {"name": "tool_b", "args": {"y": 2}},
        ]
        result = self._run(parallel_tools_handler(self._ctx(), tool_calls=calls, execute_fn=_execute_fn_ok))
        assert result.success is True
        assert len(result.sub_results) == 2
        assert all(isinstance(s, SubToolResult) for s in result.sub_results)
        assert result.sub_results[0].tool_name == "tool_a"
        assert result.sub_results[1].tool_name == "tool_b"
        assert result.sub_results[0].success is True
        assert result.sub_results[0].status_code == "success"

    def test_partial_success_sub_results(self):
        from src.reasoning.handlers.system import parallel_tools_handler

        async def _mixed(name: str, args: dict) -> Observation:
            return _make_obs("ok" if name == "tool_a" else "fail", success=(name == "tool_a"))

        calls = [{"name": "tool_a", "args": {}}, {"name": "tool_b", "args": {}}]
        result = self._run(parallel_tools_handler(self._ctx(), tool_calls=calls, execute_fn=_mixed))
        assert result.success is False  # partial → False
        assert result.sub_results[0].success is True
        assert result.sub_results[1].success is False
        assert result.sub_results[1].status_code == "failed"

    def test_total_failure_sub_results(self):
        from src.reasoning.handlers.system import parallel_tools_handler
        calls = [{"name": "tool_a", "args": {}}, {"name": "tool_b", "args": {}}]
        result = self._run(parallel_tools_handler(self._ctx(), tool_calls=calls, execute_fn=_execute_fn_fail))
        assert result.success is False
        assert all(not s.success for s in result.sub_results)

    def test_exception_sub_result(self):
        from src.reasoning.handlers.system import parallel_tools_handler
        calls = [{"name": "boom", "args": {}}]
        result = self._run(parallel_tools_handler(self._ctx(), tool_calls=calls, execute_fn=_execute_fn_raise))
        assert result.success is False
        assert result.sub_results[0].status_code == "exception"
        assert "crash in boom" in result.sub_results[0].content

    def test_args_stored_in_sub_result(self):
        from src.reasoning.handlers.system import parallel_tools_handler
        calls = [{"name": "t", "args": {"channel": "123", "msg": "hello"}}]
        result = self._run(parallel_tools_handler(self._ctx(), tool_calls=calls, execute_fn=_execute_fn_ok))
        assert result.sub_results[0].args == {"channel": "123", "msg": "hello"}

    def test_text_summary_still_present(self):
        from src.reasoning.handlers.system import parallel_tools_handler
        calls = [{"name": "t", "args": {}}]
        result = self._run(parallel_tools_handler(self._ctx(), tool_calls=calls, execute_fn=_execute_fn_ok))
        assert "parallel_tools" in result.output
        assert "t" in result.output


# ─────────────────────────────────────────────────────────────────────────────
# _parallel_tools_wrapper → Observation structurée
# ─────────────────────────────────────────────────────────────────────────────

class TestParallelToolsWrapperObservation:
    """Vérifie que le wrapper retourne une Observation avec sub_results peuplés."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def _build_registry_with_parallel(self):
        """Construit un ToolRegistry minimal avec parallel_tools câblé."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from src.reasoning.tool_registry import ToolRegistry
        import types

        reg = ToolRegistry.__new__(ToolRegistry)
        reg.tools = {}
        reg._observation_cache = {}
        reg._observation_cache_hits = {}
        reg._OBS_CACHE_MAX = 256
        reg._OBS_CACHE_MAX_HITS = 8
        reg._CACHEABLE_TOOLS = frozenset()
        reg._allowed_tools = None
        reg._tools_desc_cache = None
        reg._sig_cache = {}
        reg._failed_modules = []
        reg._v2_registry = None

        # Fausse execute pour les sous-appels
        async def _fake_execute(name, args):
            return Observation(content=f"result-{name}", success=True)

        reg.execute = _fake_execute

        from src.reasoning.handlers.context import HandlerContext
        ctx = HandlerContext()
        reg._v2_context = ctx

        from src.reasoning.handlers.system import parallel_tools_handler as _pt_handler
        _self_execute = reg.execute

        async def _parallel_tools_wrapper(**kw):
            filtered = {}
            if "tool_calls" in kw:
                filtered["tool_calls"] = kw["tool_calls"]
            filtered["execute_fn"] = _self_execute
            result = await _pt_handler(ctx, **filtered)
            return Observation(
                content=result.output,
                success=result.success,
                sub_results=result.sub_results,
            )

        reg.tools["parallel_tools"] = {"handler": _parallel_tools_wrapper}
        return reg

    def test_observation_carries_sub_results(self):
        reg = self._build_registry_with_parallel()
        handler = reg.tools["parallel_tools"]["handler"]
        calls = [{"name": "send_msg", "args": {"ch": "1"}}, {"name": "pin_msg", "args": {}}]
        obs = self._run(handler(tool_calls=calls))
        assert isinstance(obs, Observation)
        assert len(obs.sub_results) == 2
        assert obs.sub_results[0].tool_name == "send_msg"
        assert obs.sub_results[1].tool_name == "pin_msg"

    def test_bad_args_returns_observation_with_error_text(self):
        reg = self._build_registry_with_parallel()
        handler = reg.tools["parallel_tools"]["handler"]
        # Appel sans tool_calls → _pt_handler retourne un HandlerResult avec message d'erreur
        obs = self._run(handler(bad_arg="oops"))
        assert isinstance(obs, Observation)
        # Le handler retourne un message d'erreur via output (success=True côté HandlerResult.ok)
        assert "Erreur" in obs.content or "parallel_tools" in obs.content


# ─────────────────────────────────────────────────────────────────────────────
# ToolRegistry.execute() early-return pour Observation
# ─────────────────────────────────────────────────────────────────────────────

class TestToolRegistryEarlyReturn:
    """Vérifie que execute() retourne l'Observation directement quand le handler en produit une."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_observation_passthrough(self):
        """Un handler qui retourne une Observation ne doit pas être re-wrappé."""
        from src.reasoning.tool_registry import ToolRegistry

        reg = ToolRegistry.__new__(ToolRegistry)
        reg.tools = {}
        reg._observation_cache = {}
        reg._observation_cache_hits = {}
        reg._OBS_CACHE_MAX = 256
        reg._OBS_CACHE_MAX_HITS = 8
        reg._CACHEABLE_TOOLS = frozenset()
        reg._allowed_tools = None
        reg._tools_desc_cache = None
        reg._sig_cache = {}
        reg._failed_modules = []
        reg._tool_modules = {}
        reg._autonomy_level = "normal"

        _expected = Observation(content="structured", success=True, sub_results=(
            SubToolResult(tool_name="x", success=True, content="ok"),
        ))

        async def _direct_obs_handler():
            return _expected

        reg.tools["my_tool"] = {"handler": _direct_obs_handler, "name": "my_tool"}

        result = self._run(reg.execute("my_tool", {}))
        assert isinstance(result, Observation)
        assert result.content == "structured"
        assert len(result.sub_results) == 1
        assert result.sub_results[0].tool_name == "x"


# ─────────────────────────────────────────────────────────────────────────────
# Ledger expansion — interaction avec sub_results
# ─────────────────────────────────────────────────────────────────────────────

class TestLedgerExpansion:
    """Vérifie que parallel_tools génère des entrées individuelles dans le ledger."""

    def test_sub_results_produce_ledger_entries(self):
        from src.runtime.execution_ledger import ExecutionLedger

        ledger = ExecutionLedger()
        sub = SubToolResult(
            tool_name="discord_send",
            success=True,
            content="Message envoyé",
            status_code="success",
            args={"channel_id": "123", "content": "hello"},
        )
        obs = Observation(content="⚡ parallel_tools: 1 appel(s)", success=True, sub_results=(sub,))

        # Simuler ce que react.py fait lors de l'expansion
        def _ledger_extract_target(tool_name, args):
            return args.get("channel_id") or args.get("path") or ""

        def _ledger_extract_proof(tool_name, content, success):
            return content[:80] if success else None

        for _sub in obs.sub_results:
            _sub_target = _ledger_extract_target(_sub.tool_name, _sub.args)
            _sub_proof = _ledger_extract_proof(_sub.tool_name, _sub.content, _sub.success)
            ledger.append(
                iteration=1,
                action=_sub.tool_name,
                target=_sub_target,
                success=_sub.success,
                proof=_sub_proof,
                meta={"duration_ms": 0.0, "via": "parallel_tools"},
            )

        entries = ledger._entries
        assert len(entries) == 1
        assert entries[0].action == "discord_send"
        assert entries[0].success is True
        assert entries[0].target == "123"
        assert entries[0].meta["via"] == "parallel_tools"

    def test_failed_sub_result_in_ledger(self):
        from src.runtime.execution_ledger import ExecutionLedger

        ledger = ExecutionLedger()
        sub = SubToolResult(
            tool_name="write_file",
            success=False,
            content="Permission denied",
            status_code="failed",
            args={"path": "/protected/file.txt"},
        )
        obs = Observation(content="...", success=False, sub_results=(sub,))

        for _sub in obs.sub_results:
            ledger.append(
                iteration=2,
                action=_sub.tool_name,
                target=_sub.args.get("path", ""),
                success=_sub.success,
                proof=None,
                meta={"via": "parallel_tools"},
            )

        entries = ledger._entries
        assert entries[0].success is False
        assert entries[0].target == "/protected/file.txt"
