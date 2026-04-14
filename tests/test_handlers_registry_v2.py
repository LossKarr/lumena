"""Tests unitaires pour src/reasoning/handlers/registry_v2.py"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.reasoning.handlers.registry_v2 import (
    HandlerDef,
    HandlerRegistryV2,
    HandlerContext,
    HandlerResult,
)


class TestHandlerDef:
    def test_fields_exist(self):
        fields = set(HandlerDef.__dataclass_fields__)
        assert "name" in fields
        assert "description" in fields
        assert "parameters" in fields
        assert "handler" in fields
        assert "category" in fields

    def test_create_handler_def(self):
        async def my_handler(ctx: HandlerContext) -> HandlerResult:
            return HandlerResult(success=True, output="ok")

        hd = HandlerDef(
            name="test_tool",
            description="A test tool",
            parameters={},
            handler=my_handler,
            category="test",
        )
        assert hd.name == "test_tool"
        assert callable(hd.handler)
        assert hd.category == "test"


class TestHandlerRegistryV2:
    @pytest.fixture
    def registry(self):
        return HandlerRegistryV2()

    def test_init_empty(self, registry):
        assert registry.count == 0

    def test_register_handler(self, registry):
        async def dummy_handler(ctx: HandlerContext) -> HandlerResult:
            return HandlerResult(success=True, output="dummy")

        hd = HandlerDef(
            name="dummy_tool",
            description="Dummy",
            parameters={},
            handler=dummy_handler,
            category="test",
        )
        registry.register(hd)
        assert registry.count == 1
        assert registry.has("dummy_tool")

    def test_register_many(self, registry):
        async def h1(ctx: HandlerContext) -> HandlerResult:
            return HandlerResult(success=True, output="h1")

        async def h2(ctx: HandlerContext) -> HandlerResult:
            return HandlerResult(success=True, output="h2")

        defs = [
            HandlerDef(name="tool1", description="T1", parameters={}, handler=h1, category="c"),
            HandlerDef(name="tool2", description="T2", parameters={}, handler=h2, category="c"),
        ]
        registry.register_many(defs)
        assert registry.count == 2

    def test_get_handler(self, registry):
        async def my_h(ctx: HandlerContext) -> HandlerResult:
            return HandlerResult(success=True, output="x")

        hd = HandlerDef(name="my_tool", description="My", parameters={}, handler=my_h, category="x")
        registry.register(hd)
        retrieved = registry.get("my_tool")
        assert retrieved is not None
        assert retrieved.name == "my_tool"

    def test_get_nonexistent_returns_none(self, registry):
        result = registry.get("nonexistent_tool")
        assert result is None

    def test_has_false_for_missing(self, registry):
        assert registry.has("missing_tool") is False

    def test_tool_names(self, registry):
        async def h(ctx: HandlerContext) -> HandlerResult:
            return HandlerResult(success=True, output="x")

        registry.register(HandlerDef(name="a_tool", description="A", parameters={}, handler=h, category="c"))
        assert "a_tool" in registry.tool_names

    def test_categories(self, registry):
        async def h(ctx: HandlerContext) -> HandlerResult:
            return HandlerResult(success=True, output="x")

        registry.register(HandlerDef(name="cat_tool", description="C", parameters={}, handler=h, category="mycategory"))
        cats = registry.categories
        assert "mycategory" in cats

    def test_by_category(self, registry):
        async def h(ctx: HandlerContext) -> HandlerResult:
            return HandlerResult(success=True, output="x")

        registry.register(HandlerDef(name="t1", description="T", parameters={}, handler=h, category="alpha"))
        registry.register(HandlerDef(name="t2", description="T", parameters={}, handler=h, category="beta"))
        alpha = registry.by_category("alpha")
        assert len(alpha) == 1
        assert alpha[0].name == "t1"

    @pytest.mark.asyncio
    async def test_execute_handler(self, registry):
        async def h(ctx: HandlerContext) -> HandlerResult:
            return HandlerResult(success=True, output="executed")

        registry.register(HandlerDef(name="exec_tool", description="E", parameters={}, handler=h, category="c"))
        ctx = HandlerContext.for_testing()
        result = await registry.execute("exec_tool", ctx)
        assert result.success is True
        assert result.output == "executed"


class TestHandlerContext:
    def test_for_testing_factory(self):
        ctx = HandlerContext.for_testing()
        assert ctx is not None
        assert ctx.lumena_root is not None
        assert ctx.runtime_root is not None

    def test_resolve_path(self):
        ctx = HandlerContext.for_testing()
        p = ctx.resolve_path("test_file.txt")
        assert p is not None
