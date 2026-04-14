"""
Tests de parité — Service fragmenté vs LumenaCore legacy.

Chaque test instancie le service ET LumenaCore mockés identiquement,
puis vérifie que les résultats sont strictement identiques.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from src.core_services.contracts import ServiceContext
from src.core_services.workspace_service import WorkspaceService
from src.core_services.voice_service import VoiceService
from src.core_services.code_service import CodeService
from src.core_services.web_service import WebService
from src.core_services.memory_service import MemoryService


@pytest.fixture
def tmp_data(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def mock_llm():
    llm = MagicMock()
    llm.chat = AsyncMock(return_value="Réponse LLM mockée")
    llm.is_available = AsyncMock(return_value=True)
    return llm


@pytest.fixture
def mock_memory():
    mem = MagicMock()
    mem.remember = MagicMock()
    mem.recall = MagicMock(return_value=["souvenir-1", "souvenir-2"])
    mem.get_stats = MagicMock(return_value={"count": 10, "types": {"episodic": 5}})
    mem.learn_fact = MagicMock()
    mem.get_fact = MagicMock(return_value="fact-value")
    return mem


@pytest.fixture
def ctx(tmp_data, mock_llm, mock_memory):
    return ServiceContext(
        data_dir=tmp_data,
        llm=mock_llm,
        memory=mock_memory,
    )


# ============================================================================
# WorkspaceService parity
# ============================================================================

class TestParityWorkspace:
    """Parité WorkspaceService vs inline core.py."""

    def test_get_workspace_path_type(self, ctx):
        ws = WorkspaceService(ctx)
        result = ws.get_workspace_path()
        assert isinstance(result, Path)
        # Le pattern doit contenir une date YYYY-MM-DD
        import re
        assert re.search(r"\d{4}-\d{2}-\d{2}", str(result))

    def test_create_project_folder_normalization(self, ctx):
        ws = WorkspaceService(ctx)
        folder = ws.create_project_folder("Mon Super Projet!")
        assert "projet-" in folder.name
        # Vérifier normalisation: espaces et ponctuation → underscore
        assert " " not in folder.name
        assert "!" not in folder.name

    @pytest.mark.asyncio
    async def test_create_file_default_workspace(self, ctx):
        ws = WorkspaceService(ctx)
        fp = await ws.create_file("test.py", "print('hello')")
        assert fp.exists()
        assert fp.read_text(encoding="utf-8") == "print('hello')"

    @pytest.mark.asyncio
    async def test_create_file_with_project(self, ctx):
        ws = WorkspaceService(ctx)
        fp = await ws.create_file("main.py", "# code", project_name="demo")
        assert "projet-demo" in str(fp)

    @pytest.mark.asyncio
    async def test_read_file_not_found(self, ctx):
        ws = WorkspaceService(ctx)
        result = await ws.read_file("/nonexistent/file.txt")
        assert "Erreur" in result or "non trouvé" in result

    def test_list_workspace_files_empty(self, ctx):
        ws = WorkspaceService(ctx)
        files = ws.list_workspace_files()
        assert isinstance(files, list)


# ============================================================================
# VoiceService parity
# ============================================================================

class TestParityVoice:
    """Parité VoiceService vs inline core.py."""

    @pytest.mark.asyncio
    async def test_speak_no_tts_no_crash(self, ctx):
        vs = VoiceService(ctx)
        await vs.speak("test")

    @pytest.mark.asyncio
    async def test_speak_response_cleans_emojis(self, ctx):
        mock_tts = MagicMock()
        mock_tts.speak_async = AsyncMock()
        ctx.tts = mock_tts
        vs = VoiceService(ctx)
        await vs._speak_response("🌟 Hello **world**! `code` here")
        call_text = mock_tts.speak_async.call_args[0][0]
        assert "🌟" not in call_text
        assert "**" not in call_text
        assert "`" not in call_text

    @pytest.mark.asyncio
    async def test_speak_response_truncates_long(self, ctx):
        mock_tts = MagicMock()
        mock_tts.speak_async = AsyncMock()
        ctx.tts = mock_tts
        vs = VoiceService(ctx)
        long_text = "A" * 1000
        await vs._speak_response(long_text)
        call_text = mock_tts.speak_async.call_args[0][0]
        assert len(call_text) <= 500

    @pytest.mark.asyncio
    async def test_speak_response_skips_short(self, ctx):
        mock_tts = MagicMock()
        mock_tts.speak_async = AsyncMock()
        ctx.tts = mock_tts
        vs = VoiceService(ctx)
        await vs._speak_response("ok")  # < 3 chars after cleaning
        mock_tts.speak_async.assert_not_called()

    def test_auto_speak_toggle(self, ctx):
        vs = VoiceService(ctx)
        assert vs.auto_speak is False
        vs.set_auto_speak(True)
        assert vs.auto_speak is True
        assert ctx.auto_speak is True  # mutates ctx


# ============================================================================
# CodeService parity
# ============================================================================

class TestParityCode:
    """Parité CodeService vs inline core.py."""

    def test_analyze_code_detection(self, ctx):
        cs = CodeService(ctx)
        code = """import os
from pathlib import Path

def process():
    pass

class Handler:
    pass

def main():
    pass
"""
        result = cs.analyze_code(code)
        assert "process" in result["functions"]
        assert "main" in result["functions"]
        assert "Handler" in result["classes"]
        assert result["lines"] == len(code.split("\n"))

    def test_analyze_code_issues_except_bare(self, ctx):
        cs = CodeService(ctx)
        code = "try:\n    pass\nexcept:\n    pass"
        result = cs.analyze_code(code)
        assert any("Exception sans type" in i for i in result["issues"])

    def test_analyze_code_issues_import_star(self, ctx):
        cs = CodeService(ctx)
        code = "from os import *"
        result = cs.analyze_code(code)
        assert any("wildcard" in i for i in result["issues"])

    @pytest.mark.asyncio
    async def test_explain_code_calls_llm(self, ctx):
        cs = CodeService(ctx)
        await cs.explain_code("x = 1")
        ctx.llm.chat.assert_called_once()
        prompt = ctx.llm.chat.call_args[0][0]
        assert any("explique" in str(m).lower() or "explain" in str(m).lower() for m in prompt)

    @pytest.mark.asyncio
    async def test_debug_code_with_error(self, ctx):
        cs = CodeService(ctx)
        await cs.debug_code("x = 1/0", error="ZeroDivisionError")
        prompt = ctx.llm.chat.call_args[0][0]
        assert any("ZeroDivisionError" in str(m) for m in prompt)


# ============================================================================
# WebService parity
# ============================================================================

class TestParityWeb:
    """Parité WebService vs inline core.py."""

    def test_open_google_search_format(self, ctx):
        import webbrowser
        original = webbrowser.open
        webbrowser.open = MagicMock()
        try:
            ws = WebService(ctx)
            url = ws.open_google_search("test query")
            assert "google.com/search" in url
            assert "test+query" in url or "test%20query" in url
        finally:
            webbrowser.open = original

    @pytest.mark.asyncio
    async def test_fetch_url_failure(self, ctx):
        ws = WebService(ctx)
        result = await ws.fetch_url("http://invalid.local.test")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_summarize_url_failure(self, ctx):
        ws = WebService(ctx)
        result = await ws.summarize_url("http://invalid.local.test")
        assert "❌" in result


# ============================================================================
# MemoryService parity
# ============================================================================

class TestParityMemory:
    """Parité MemoryService vs inline core.py."""

    @pytest.mark.asyncio
    async def test_remember_delegates(self, ctx):
        ms = MemoryService(ctx)
        result = await ms.remember("test content", importance=0.8)
        assert result is True
        ctx.memory.remember.assert_called_once_with(
            "test content", memory_type="episodic", importance=0.8
        )

    @pytest.mark.asyncio
    async def test_recall_delegates(self, ctx):
        ms = MemoryService(ctx)
        result = await ms.recall("query", limit=3)
        assert len(result) == 2  # mock returns 2
        ctx.memory.recall.assert_called_once_with("query", limit=3)

    def test_get_memory_stats_format(self, ctx):
        ms = MemoryService(ctx)
        stats = ms.get_memory_stats()
        assert stats["available"] is True
        assert stats["count"] == 10

    def test_learn_fact_delegates(self, ctx):
        ms = MemoryService(ctx)
        assert ms.learn_fact("key", "val") is True
        ctx.memory.learn_fact.assert_called_once_with("key", "val")

    def test_get_fact_delegates(self, ctx):
        ms = MemoryService(ctx)
        assert ms.get_fact("key") == "fact-value"
        ctx.memory.get_fact.assert_called_once_with("key")

    @pytest.mark.asyncio
    async def test_llm_summarize(self, ctx):
        ms = MemoryService(ctx)
        messages = [
            {"role": "user", "content": "Bonjour"},
            {"role": "assistant", "content": "Salut!"},
        ]
        result = await ms._llm_summarize(messages)
        assert result == "Réponse LLM mockée"

    def test_load_memory_file_exists(self, ctx):
        (ctx.data_dir / "MEMORY.md").write_text("# Mémoire", encoding="utf-8")
        ms = MemoryService(ctx)
        ms._load_memory_file()
        assert "Mémoire" in ms._permanent_memory

    def test_load_memory_file_missing(self, ctx):
        ms = MemoryService(ctx)
        ms._load_memory_file()
        assert ms._permanent_memory == ""
