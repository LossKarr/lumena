"""
test_skeleton.py - Tests unitaires du squelette Phase 1.

Valide que contracts.py, context.py, registry_v2.py, parity_tools.py
sont fonctionnels et importables avant de commencer la migration.
"""

import asyncio
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from src.reasoning.handlers.contracts import HandlerResult, HandlerTimer
from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.registry_v2 import HandlerRegistryV2, HandlerDef, HandlerFunc
from src.reasoning.handlers.parity_tools import ParityResult, parity_report_markdown
from src.reasoning.react_config import Observation


# ─── Tests contracts.py ───────────────────────────────────────────────────

class TestHandlerResult:
    def test_ok(self):
        r = HandlerResult.ok("hello world", handler_name="test")
        assert r.success is True
        assert r.output == "hello world"
        assert r.error is None
        assert r.handler_name == "test"

    def test_fail(self):
        r = HandlerResult.fail("something broke", handler_name="test")
        assert r.success is False
        assert r.error == "something broke"
        assert r.output == "something broke"  # output = error by default

    def test_fail_custom_output(self):
        r = HandlerResult.fail("internal err", output="❌ User-facing message")
        assert r.output == "❌ User-facing message"
        assert r.error == "internal err"

    def test_to_legacy_str(self):
        r = HandlerResult.ok("legacy compat")
        assert r.to_legacy_str() == "legacy compat"

    def test_frozen(self):
        r = HandlerResult.ok("immutable")
        with pytest.raises(AttributeError):
            r.output = "mutated"  # type: ignore


class TestHandlerTimer:
    def test_timer_measures_elapsed(self):
        import time
        with HandlerTimer() as t:
            time.sleep(0.01)
        assert t.elapsed_ms >= 5  # at least 5ms


# ─── Tests context.py ──────────────────────────────────────────────────────

class TestHandlerContext:
    def test_for_testing_creates_valid_context(self, tmp_path):
        ctx = HandlerContext.for_testing(
            lumena_root=tmp_path,
            runtime_root=tmp_path / "workspace",
        )
        assert ctx.lumena is None
        assert ctx.lumena_root == tmp_path
        assert ctx.runtime_root == tmp_path / "workspace"
        assert ctx.memory is None

    def test_is_ide_runtime_false_by_default(self, tmp_path):
        ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path)
        assert ctx.is_ide_runtime() is False

    def test_is_ide_runtime_true_with_workspace(self, tmp_path):
        ctx = HandlerContext.for_testing(
            lumena_root=tmp_path,
            runtime_root=tmp_path,
            ide_context={"workspace_path": str(tmp_path)},
        )
        assert ctx.is_ide_runtime() is True

    def test_resolve_path_fallback(self, tmp_path):
        ctx = HandlerContext(
            lumena_root=tmp_path,
            runtime_root=tmp_path,
            file_guardrails=None,  # pas de guardrails
        )
        resolved = ctx.resolve_path("hello.txt")
        assert resolved == (tmp_path / "hello.txt").resolve()

    def test_memory_property_none(self):
        ctx = HandlerContext()
        assert ctx.memory is None

    def test_memory_property_with_lumena(self):
        mock_lumena = MagicMock()
        mock_lumena.memory = "fake_memory"
        ctx = HandlerContext(lumena=mock_lumena)
        assert ctx.memory == "fake_memory"

    def test_from_tool_registry(self):
        """Vérifie que from_tool_registry copie les bons champs."""
        mock_registry = MagicMock()
        mock_registry.lumena = "core"
        mock_registry.lumena_root = Path("/fake/root")
        mock_registry.runtime_root = Path("/fake/workspace")
        mock_registry.ide_context = {"workspace_path": "/fake/workspace"}
        mock_registry.file_guardrails = "guardrails"
        mock_registry._mail_hub_instance = None
        mock_registry._critical_alert_hub_instance = None
        mock_registry._web_crawler_instance = None
        mock_registry._document_hub_instance = None
        mock_registry._search_hub_instance = None
        mock_registry._spotify_hub_instance = None
        mock_registry._notion_hub_instance = None
        mock_registry._opened_apps_history = []

        ctx = HandlerContext.from_tool_registry(mock_registry)
        assert ctx.lumena == "core"
        assert ctx.lumena_root == Path("/fake/root")
        assert ctx.file_guardrails == "guardrails"


# ─── Tests registry_v2.py ─────────────────────────────────────────────────

async def _dummy_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    return HandlerResult.ok(f"dummy called with {kwargs}")


async def _handler_result_fail(ctx: HandlerContext, **kwargs) -> HandlerResult:
    return HandlerResult.fail("explicit failure", handler_name="failing_tool")


async def _failing_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    raise RuntimeError("boom")


class TestHandlerRegistryV2:
    def test_register_and_get(self):
        reg = HandlerRegistryV2()
        hdef = HandlerDef(
            name="test_tool",
            description="A test tool",
            parameters={"properties": {}, "required": []},
            handler=_dummy_handler,
            category="test",
            source_module="test_skeleton",
        )
        reg.register(hdef)
        assert reg.has("test_tool")
        assert reg.get("test_tool") is hdef
        assert reg.count == 1

    def test_register_duplicate_raises(self):
        reg = HandlerRegistryV2()
        hdef = HandlerDef(
            name="dup",
            description="",
            parameters={},
            handler=_dummy_handler,
        )
        reg.register(hdef)
        with pytest.raises(ValueError, match="déjà enregistré"):
            reg.register(hdef)

    def test_tool_names(self):
        reg = HandlerRegistryV2()
        for name in ["a", "b", "c"]:
            reg.register(HandlerDef(name=name, description="", parameters={}, handler=_dummy_handler))
        assert sorted(reg.tool_names) == ["a", "b", "c"]

    def test_by_category(self):
        reg = HandlerRegistryV2()
        reg.register(HandlerDef(name="x", description="", parameters={}, handler=_dummy_handler, category="cat1"))
        reg.register(HandlerDef(name="y", description="", parameters={}, handler=_dummy_handler, category="cat1"))
        reg.register(HandlerDef(name="z", description="", parameters={}, handler=_dummy_handler, category="cat2"))
        assert len(reg.by_category("cat1")) == 2
        assert len(reg.by_category("cat2")) == 1
        assert len(reg.by_category("cat3")) == 0

    @pytest.mark.asyncio
    async def test_execute_success(self):
        reg = HandlerRegistryV2()
        reg.register(HandlerDef(name="dummy", description="", parameters={}, handler=_dummy_handler))
        ctx = HandlerContext()
        result = await reg.execute("dummy", ctx, foo="bar")
        assert result.success is True
        assert "foo" in result.output
        assert result.duration_ms > 0
        assert result.handler_name == "dummy"

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        reg = HandlerRegistryV2()
        ctx = HandlerContext()
        result = await reg.execute("nonexistent", ctx)
        assert result.success is False
        assert "inconnu" in result.output.lower()

    @pytest.mark.asyncio
    async def test_execute_handler_exception(self):
        reg = HandlerRegistryV2()
        reg.register(HandlerDef(name="boom", description="", parameters={}, handler=_failing_handler))
        ctx = HandlerContext()
        result = await reg.execute("boom", ctx)
        assert result.success is False
        assert "boom" in result.error

    def test_get_parity_report(self):
        reg = HandlerRegistryV2()
        reg.register(HandlerDef(name="a", description="", parameters={}, handler=_dummy_handler))
        reg.register(HandlerDef(name="b", description="", parameters={}, handler=_dummy_handler))

        report = reg.get_parity_report(["a", "b", "c"])
        assert report["coverage_pct"] == 66.7
        assert "c" in report["missing"]
        assert sorted(report["covered"]) == ["a", "b"]
        assert report["extra"] == []

    def test_get_tools_description(self):
        reg = HandlerRegistryV2()
        reg.register(HandlerDef(
            name="read_file",
            description="Lit un fichier",
            parameters={
                "properties": {"path": {"type": "string", "description": "Chemin du fichier"}},
                "required": ["path"],
            },
            handler=_dummy_handler,
        ))
        desc = reg.get_tools_description()
        assert "read_file" in desc
        assert "Lit un fichier" in desc
        assert "path" in desc
        assert "read_file(path)" in desc

    @pytest.mark.asyncio
    async def test_legacy_wrapper_preserves_failure_success_flag(self):
        reg = HandlerRegistryV2()
        reg.register(HandlerDef(
            name="failing_tool",
            description="Fails structurally",
            parameters={"properties": {}, "required": []},
            handler=_handler_result_fail,
        ))
        legacy = reg.to_legacy_tools_dict(HandlerContext())
        obs = await legacy["failing_tool"]["handler"]()
        assert isinstance(obs, Observation)
        assert obs.success is False
        assert "explicit failure" in obs.content


# ─── Tests parity_tools.py ─────────────────────────────────────────────────

class TestParityReport:
    def test_parity_report_markdown(self):
        results = [
            ParityResult(tool_name="a", legacy_output="x", v2_output="x", match=True),
            ParityResult(tool_name="b", legacy_output="x", v2_output="y", match=False, diff_summary="diff"),
        ]
        md = parity_report_markdown(results)
        assert "1/2" in md
        assert "50.0%" in md
        assert "✅" in md
        assert "❌" in md
