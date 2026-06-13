"""
test_parity_web_memory.py - Tests de parité legacy ↔ V2 pour P2.

Vérifie que les handlers web + memory fragmentés sont correctement
enregistrés dans le registre V2 et fonctionnent via execute().
"""

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.react_config import Observation
from src.reasoning.handlers.registry_v2 import HandlerRegistryV2
from src.reasoning.handlers.files import get_file_handler_defs
from src.reasoning.handlers.system import get_system_handler_defs
from src.reasoning.handlers.memory import get_memory_handler_defs
from src.reasoning.handlers.web import get_web_handler_defs


@pytest.fixture
def v2_registry():
    """Crée un registre V2 avec tous les handlers P1 + P2."""
    reg = HandlerRegistryV2()
    reg.register_many(get_file_handler_defs())
    reg.register_many(get_system_handler_defs())
    reg.register_many(get_memory_handler_defs())
    reg.register_many(get_web_handler_defs())
    return reg


@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return HandlerContext.for_testing(
        lumena_root=tmp_path,
        runtime_root=workspace,
    )


@pytest.fixture
def ctx_with_journal(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    journal = [
        {"timestamp": "2026-03-04T10:00:00", "type": "action", "content": "Entry parity test"},
    ]
    (data_dir / "journal.json").write_text(json.dumps(journal), encoding="utf-8")
    # Patch paths.py constants in the memory handler module
    import src.reasoning.handlers.memory as mem_mod
    monkeypatch.setattr(mem_mod, "JOURNAL_JSON", data_dir / "journal.json")
    monkeypatch.setattr(mem_mod, "JOURNAL_DIR", data_dir / "memory" / "journal")
    return HandlerContext.for_testing(
        lumena_root=tmp_path,
        runtime_root=workspace,
    )


# ─── Registration checks ──────────────────────────────────────────────────

class TestV2RegistrationP2:
    def test_all_p2_handlers_registered(self, v2_registry):
        """Tous les handlers P2 (web + memory) sont enregistrés."""
        expected_memory = {
            "read_journal", "memory_search", "memory_stats", "memory_get",
            "learn_from_action", "suggest_instincts", "get_curiosity_status",
            "memory_add", "write_journal",
        }
        expected_web = {
            "web_search", "web_search_brave", "web_fetch", "deep_research",
            "web_crawl_campaign", "web_crawl_campaign_status",
            "web_crawl_campaign_pro_report", "web_crawl_campaign_explain",
        }
        all_expected = expected_memory | expected_web
        registered = set(v2_registry.tool_names)
        missing = all_expected - registered
        assert not missing, f"Handlers P2 manquants: {missing}"

    def test_p1_still_present(self, v2_registry):
        """Les handlers P1 sont toujours là après ajout P2."""
        p1_names = {
            "read_file", "write_file", "edit_file", "list_directory",
            "run_command", "get_time", "parallel_tools",
        }
        registered = set(v2_registry.tool_names)
        missing = p1_names - registered
        assert not missing, f"Handlers P1 disparus: {missing}"

    def test_total_count(self, v2_registry):
        """Le registre contient bien P1 files (19) + system + memory (11) + web (10) = 46."""
        count = len(v2_registry.tool_names)
        assert count == 46, f"Attendu 46, obtenu {count}"

    def test_parity_report_p1_p2(self, v2_registry):
        """Le rapport de parité montre 100% pour tous les outils P1+P2."""
        all_names = [
            # P1 - files
            "read_file", "write_file", "edit_file", "multi_edit_file",
            "apply_patch", "list_directory", "find_files", "delete_file",
            "create_zip", "open_file", "view_outline",
            # P1 - system
            "run_command", "get_time", "screenshot", "get_token_stats",
            "parallel_tools",
            # P2 - memory
            "read_journal", "memory_search", "memory_stats", "memory_get",
            "learn_from_action", "suggest_instincts", "get_curiosity_status",
            # P2 - web
            "web_search", "web_search_brave", "web_fetch", "deep_research",
            "web_crawl_campaign", "web_crawl_campaign_status",
            "web_crawl_campaign_pro_report", "web_crawl_campaign_explain",
        ]
        report = v2_registry.get_parity_report(all_names)
        assert report["coverage_pct"] == 100.0, f"Missing: {report['missing']}"
        assert len(report["missing"]) == 0

    def test_categories_p2(self, v2_registry):
        cats = v2_registry.categories
        assert "memory" in cats
        assert "web" in cats
        assert "files" in cats
        assert "system" in cats


# ─── Smoke execution via registry ─────────────────────────────────────────

class TestV2ExecutionSmokeP2:
    @pytest.mark.asyncio
    async def test_read_journal_via_registry(self, v2_registry, ctx_with_journal):
        r = await v2_registry.execute("read_journal", ctx_with_journal, date="2026-03-04")
        assert r.success
        assert "Entry parity test" in r.output
        assert r.handler_name == "read_journal"
        assert r.duration_ms > 0

    @pytest.mark.asyncio
    async def test_memory_search_via_registry_no_lumena(self, v2_registry, ctx):
        r = await v2_registry.execute("memory_search", ctx, query="test")
        assert r.success  # now returns journal fallback or "aucun souvenir"

    @pytest.mark.asyncio
    async def test_web_fetch_via_registry(self, v2_registry, ctx):
        html = "<html><body><p>Registry test</p></body></html>"
        mock_resp = MagicMock()
        mock_resp.read.return_value = html.encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            r = await v2_registry.execute("web_fetch", ctx, url="https://test.example.com")
            assert r.success
            assert "Registry test" in r.output

    @pytest.mark.asyncio
    async def test_unknown_tool_still_fails(self, v2_registry, ctx):
        r = await v2_registry.execute("nonexistent_p2_tool", ctx)
        assert not r.success


# ─── Legacy format compat ─────────────────────────────────────────────────

class TestLegacyFormatCompatP2:
    def test_legacy_dict_has_p2(self, v2_registry, ctx):
        legacy = v2_registry.to_legacy_tools_dict(ctx)
        assert "read_journal" in legacy
        assert "memory_search" in legacy
        assert "web_fetch" in legacy
        assert "web_search" in legacy

    @pytest.mark.asyncio
    async def test_legacy_read_journal_returns_observation(self, v2_registry, ctx_with_journal):
        legacy = v2_registry.to_legacy_tools_dict(ctx_with_journal)
        result = await legacy["read_journal"]["handler"](date="2026-03-04")
        assert isinstance(result, Observation)
        assert result.success is True
        assert "Entry parity test" in result.content

    def test_tools_description_includes_p2(self, v2_registry):
        desc = v2_registry.get_tools_description()
        assert "read_journal" in desc
        assert "memory_search" in desc
        assert "web_fetch" in desc
        assert "web_search" in desc
        assert "deep_research" in desc
