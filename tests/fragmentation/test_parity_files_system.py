"""
test_parity_files_system.py - Tests de parité legacy ↔ V2 pour P1.

Vérifie que les handlers fragmentés produisent le même output
que les handlers legacy de ToolRegistry (react.py).
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.registry_v2 import HandlerRegistryV2
from src.reasoning.handlers.files import get_file_handler_defs
from src.reasoning.handlers.system import get_system_handler_defs


@pytest.fixture
def v2_registry():
    """Crée un registre V2 avec tous les handlers P1."""
    reg = HandlerRegistryV2()
    reg.register_many(get_file_handler_defs())
    reg.register_many(get_system_handler_defs())
    return reg


@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return HandlerContext.for_testing(
        lumena_root=tmp_path,
        runtime_root=workspace,
    )


# ─── Smoke tests: chaque handler V2 s'exécute sans crash ──────────────────

class TestV2Registration:
    def test_all_p1_handlers_registered(self, v2_registry):
        """Tous les handlers P1 sont enregistrés."""
        expected_files = {
            "read_file", "write_file", "edit_file", "multi_edit_file",
            "apply_patch", "list_directory", "find_files", "delete_file",
            "create_zip", "open_file", "view_outline", "grep_search",
        }
        expected_system = {
            "run_command", "get_time", "screenshot", "get_token_stats",
            "parallel_tools",
        }
        all_expected = expected_files | expected_system
        registered = set(v2_registry.tool_names)
        missing = all_expected - registered
        assert not missing, f"Handlers P1 manquants: {missing}"

    def test_parity_report_baseline(self, v2_registry):
        """Le rapport de parité montre la couverture correcte."""
        legacy_names = [
            "read_file", "write_file", "edit_file", "multi_edit_file",
            "apply_patch", "list_directory", "find_files", "delete_file",
            "create_zip", "open_file", "view_outline",
            "run_command", "get_time", "screenshot", "get_token_stats",
            "parallel_tools",
        ]
        report = v2_registry.get_parity_report(legacy_names)
        assert report["coverage_pct"] == 100.0
        assert len(report["missing"]) == 0

    def test_categories(self, v2_registry):
        assert "files" in v2_registry.categories
        assert "system" in v2_registry.categories


class TestV2ExecutionSmoke:
    """Smoke tests: chaque handler V2 P1 s'exécute via registry.execute()."""

    @pytest.mark.asyncio
    async def test_read_file_via_registry(self, v2_registry, ctx):
        f = ctx.runtime_root / "test.txt"
        f.write_text("hello", encoding="utf-8")
        r = await v2_registry.execute("read_file", ctx, path="test.txt")
        assert r.success
        assert "hello" in r.output
        assert r.handler_name == "read_file"
        assert r.duration_ms > 0

    @pytest.mark.asyncio
    async def test_list_directory_via_registry(self, v2_registry, ctx):
        (ctx.runtime_root / "a.txt").write_text("a", encoding="utf-8")
        r = await v2_registry.execute("list_directory", ctx, path=".")
        assert r.success
        assert "a.txt" in r.output

    @pytest.mark.asyncio
    async def test_find_files_via_registry(self, v2_registry, ctx):
        (ctx.runtime_root / "needle.py").write_text("x", encoding="utf-8")
        r = await v2_registry.execute("find_files", ctx, pattern="needle", path=".")
        assert r.success
        assert "needle" in r.output

    @pytest.mark.asyncio
    async def test_get_time_via_registry(self, v2_registry, ctx):
        r = await v2_registry.execute("get_time", ctx)
        assert r.success
        assert ":" in r.output

    @pytest.mark.asyncio
    async def test_run_command_via_registry(self, v2_registry, ctx):
        r = await v2_registry.execute("run_command", ctx, command="echo parity_test")
        assert r.success
        assert "parity_test" in r.output

    @pytest.mark.asyncio
    async def test_unknown_tool(self, v2_registry, ctx):
        r = await v2_registry.execute("nonexistent_tool", ctx)
        assert not r.success
        assert "inconnu" in r.output.lower()


class TestLegacyFormatCompat:
    """Vérifie que to_legacy_tools_dict produit le bon format."""

    def test_legacy_dict_format(self, v2_registry, ctx):
        legacy = v2_registry.to_legacy_tools_dict(ctx)
        assert "read_file" in legacy
        assert "run_command" in legacy

        for name, entry in legacy.items():
            assert "name" in entry
            assert "description" in entry
            assert "parameters" in entry
            assert "handler" in entry
            assert callable(entry["handler"])

    @pytest.mark.asyncio
    async def test_legacy_wrapper_returns_str(self, v2_registry, ctx):
        """Le wrapper legacy retourne bien un str, pas un HandlerResult."""
        legacy = v2_registry.to_legacy_tools_dict(ctx)
        result = await legacy["get_time"]["handler"]()
        assert isinstance(result, str)
        assert ":" in result  # format heure

    def test_tools_description(self, v2_registry):
        desc = v2_registry.get_tools_description()
        assert "read_file" in desc
        assert "run_command" in desc
        assert "get_time" in desc
