"""
Tests du squelette core_services — Phase 1.

Vérifie que ServiceContext, BaseService, et tous les services compilent
et peuvent être instanciés correctement.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from src.core_services.contracts import ServiceContext
from src.core_services.base_service import BaseService
from src.core_services.workspace_service import WorkspaceService
from src.core_services.voice_service import VoiceService
from src.core_services.code_service import CodeService
from src.core_services.web_service import WebService
from src.core_services.memory_service import MemoryService
from src.core_services.identity_service import IdentityService
from src.core_services.context_service import ContextService


@pytest.fixture
def tmp_data(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def ctx(tmp_data):
    tmp_data.mkdir(parents=True, exist_ok=True)
    return ServiceContext(
        data_dir=tmp_data,
        llm=MagicMock(),
        memory=MagicMock(),
    )


class TestServiceContext:
    def test_create_minimal(self, tmp_data):
        ctx = ServiceContext(data_dir=tmp_data)
        assert ctx.data_dir == tmp_data
        assert ctx.llm is None
        assert ctx.memory is None

    def test_create_full(self, ctx):
        assert ctx.llm is not None
        assert ctx.memory is not None

    def test_default_fields(self, tmp_data):
        ctx = ServiceContext(data_dir=tmp_data)
        assert ctx.auto_speak is False
        assert ctx.skills == {}
        assert ctx.friends == {}


class TestBaseService:
    def test_properties(self, ctx):
        class DummyService(BaseService):
            pass
        svc = DummyService(ctx)
        assert svc.data_dir == ctx.data_dir
        assert svc.llm == ctx.llm
        assert svc.memory == ctx.memory


class TestWorkspaceService:
    def test_instantiate(self, ctx):
        ws = WorkspaceService(ctx)
        assert ws.data_dir == ctx.data_dir

    def test_get_workspace_path(self, ctx):
        ws = WorkspaceService(ctx)
        path = ws.get_workspace_path()
        assert path.exists()
        assert "workspace" in str(path)

    def test_create_project_folder(self, ctx):
        ws = WorkspaceService(ctx)
        folder = ws.create_project_folder("Mon Projet")
        assert folder.exists()
        assert "projet-" in folder.name

    @pytest.mark.asyncio
    async def test_create_and_read_file(self, ctx):
        ws = WorkspaceService(ctx)
        filepath = await ws.create_file("test.txt", "contenu test")
        assert filepath.exists()
        content = await ws.read_file(str(filepath))
        assert content == "contenu test"

    def test_list_workspace_files(self, ctx):
        ws = WorkspaceService(ctx)
        workspace = ws.get_workspace_path()
        (workspace / "a.txt").write_text("a", encoding="utf-8")
        files = ws.list_workspace_files("*.txt")
        assert len(files) >= 1
        assert files[0]["name"] == "a.txt"


class TestVoiceService:
    def test_instantiate(self, ctx):
        vs = VoiceService(ctx)
        assert vs.tts is None  # ctx.tts is None by default

    def test_set_auto_speak(self, ctx):
        vs = VoiceService(ctx)
        vs.set_auto_speak(True)
        assert vs.auto_speak is True
        vs.set_auto_speak(False)
        assert vs.auto_speak is False

    @pytest.mark.asyncio
    async def test_speak_no_tts(self, ctx):
        vs = VoiceService(ctx)
        await vs.speak("hello")  # should not crash

    @pytest.mark.asyncio
    async def test_speak_response_with_tts(self, ctx):
        mock_tts = MagicMock()
        mock_tts.speak_async = AsyncMock()
        ctx.tts = mock_tts
        vs = VoiceService(ctx)
        await vs._speak_response("Hello world, ceci est un test de TTS")
        mock_tts.speak_async.assert_called_once()


class TestCodeService:
    def test_instantiate(self, ctx):
        cs = CodeService(ctx)
        assert cs.llm is not None

    def test_analyze_code_python(self, ctx):
        cs = CodeService(ctx)
        code = "import os\ndef hello():\n    pass\nclass Foo:\n    pass"
        result = cs.analyze_code(code)
        assert result["language"] == "python"
        assert "hello" in result["functions"]
        assert "Foo" in result["classes"]
        assert "os" in result["imports"]

    def test_search_code_no_index(self, ctx):
        cs = CodeService(ctx)
        assert cs.search_code("test") == ""

    @pytest.mark.asyncio
    async def test_explain_code(self, ctx):
        ctx.llm.chat = AsyncMock(return_value="Explication du code")
        cs = CodeService(ctx)
        result = await cs.explain_code("print('hello')")
        assert "Explication" in result

    @pytest.mark.asyncio
    async def test_debug_code(self, ctx):
        ctx.llm.chat = AsyncMock(return_value="Bug trouvé: ...")
        cs = CodeService(ctx)
        result = await cs.debug_code("x = 1/0", error="ZeroDivisionError")
        assert "Bug" in result


class TestWebService:
    def test_instantiate(self, ctx):
        ws = WebService(ctx)
        assert ws._last_mentioned_url is None

    def test_open_google_search(self, ctx):
        ws = WebService(ctx)
        # Patch webbrowser pour éviter d'ouvrir un vrai navigateur
        import webbrowser
        original = webbrowser.open
        webbrowser.open = MagicMock()
        try:
            url = ws.open_google_search("lumena AI")
            assert "google.com" in url
            assert ws._last_search_query == "lumena AI"
        finally:
            webbrowser.open = original

    @pytest.mark.asyncio
    async def test_summarize_url_failure(self, ctx):
        ws = WebService(ctx)
        result = await ws.summarize_url("http://invalid.test.local")
        assert "❌" in result


class TestMemoryService:
    def test_instantiate(self, ctx):
        ms = MemoryService(ctx)
        assert ms._permanent_memory == ""

    def test_load_memory_file(self, ctx):
        memory_file = ctx.data_dir / "MEMORY.md"
        memory_file.write_text("# Test memory content", encoding="utf-8")
        ms = MemoryService(ctx)
        ms._load_memory_file()
        assert "Test memory" in ms._permanent_memory

    def test_get_permanent_memory_context_empty(self, ctx):
        ms = MemoryService(ctx)
        assert ms.get_permanent_memory_context() == ""

    def test_get_permanent_memory_context_filled(self, ctx):
        ms = MemoryService(ctx)
        ms._permanent_memory = "some memory content"
        result = ms.get_permanent_memory_context()
        assert "MÉMOIRE PERMANENTE" in result
        assert "some memory" in result

    @pytest.mark.asyncio
    async def test_remember(self, ctx):
        ctx.memory.remember = MagicMock()
        ms = MemoryService(ctx)
        result = await ms.remember("test content")
        assert result is True
        ctx.memory.remember.assert_called_once()

    @pytest.mark.asyncio
    async def test_recall(self, ctx):
        ctx.memory.recall = MagicMock(return_value=["souvenir1"])
        ms = MemoryService(ctx)
        result = await ms.recall("test")
        assert len(result) == 1

    def test_get_memory_stats(self, ctx):
        ctx.memory.get_stats = MagicMock(return_value={"count": 42, "types": {}})
        ms = MemoryService(ctx)
        stats = ms.get_memory_stats()
        assert stats["available"] is True
        assert stats["count"] == 42

    def test_learn_fact(self, ctx):
        ctx.memory.learn_fact = MagicMock()
        ms = MemoryService(ctx)
        assert ms.learn_fact("key", "value") is True

    def test_get_fact(self, ctx):
        ctx.memory.get_fact = MagicMock(return_value="value")
        ms = MemoryService(ctx)
        assert ms.get_fact("key") == "value"

    def test_no_memory(self, tmp_data):
        tmp_data.mkdir(parents=True, exist_ok=True)
        ctx = ServiceContext(data_dir=tmp_data)
        ms = MemoryService(ctx)
        assert ms.get_memory_stats() == {"available": False}
        assert ms.learn_fact("k", "v") is False
        assert ms.get_fact("k") is None


class TestIdentityService:
    def test_instantiate(self, ctx):
        ids = IdentityService(ctx)
        assert ids._tg_contexts == {}

    def test_resolve_sender_non_telegram(self, ctx):
        ids = IdentityService(ctx)
        assert ids._resolve_sender_identity({"id": "1"}, "web") is None

    def test_resolve_sender_telegram_owner(self, ctx):
        ctx.memory.get_fact = MagicMock(side_effect=lambda k: None)
        ctx.memory.learn_fact = MagicMock()
        ids = IdentityService(ctx)
        result = ids._resolve_sender_identity({"id": "123", "name": "Alice"}, "telegram")
        assert result is not None
        assert result["is_owner"] is True
        assert result["name"] == "Alice"

    def test_detect_friend_rename_no_match(self, ctx):
        ids = IdentityService(ctx)
        sender = {"is_owner": True, "tg_id": "1"}
        assert ids._detect_friend_rename("bonjour", sender) is None

    def test_apply_friend_rename(self, ctx):
        ctx.memory.get_fact = MagicMock(side_effect=lambda k: {
            "telegram_known_ids": "111",
            "telegram_111_name": "Alice",
        }.get(k))
        ctx.memory.learn_fact = MagicMock()
        ids = IdentityService(ctx)
        result = ids._apply_friend_rename("alice", "Bob")
        assert result is not None
        assert result["old"] == "Alice"
        assert result["new"] == "Bob"

    def test_clear_tg_context(self, ctx):
        ids = IdentityService(ctx)
        from unittest.mock import MagicMock as MM
        mock_ctx = MM()
        mock_ctx.clear = MagicMock()
        ids._tg_contexts["123"] = mock_ctx
        ids.clear_tg_context("123")
        assert "123" not in ids._tg_contexts


class TestContextService:
    def test_instantiate(self, ctx):
        cs = ContextService(ctx)
        assert cs._skills == {}

    def test_get_last_active_skills_empty(self, ctx):
        cs = ContextService(ctx)
        assert cs.get_last_active_skills() == []

    def test_get_project_context_no_repomap(self, ctx):
        ctx.repo_map = None
        cs = ContextService(ctx)
        assert cs.get_project_context() == ""

    def test_get_rules_context_no_loader(self, ctx):
        ctx.rules_loader = None
        cs = ContextService(ctx)
        assert cs.get_rules_context() == ""

    def test_get_full_context(self, ctx):
        ctx.repo_map = None
        ctx.rules_loader = None
        cs = ContextService(ctx)
        result = cs.get_full_context(
            get_permanent_memory_context_fn=lambda: "\n\nmemory context"
        )
        assert "memory context" in result

    def test_build_channel_expectations(self, ctx):
        cs = ContextService(ctx)
        exp = cs._build_channel_expectations()
        assert "telegram" in exp
        assert "discord" in exp

    def test_get_capabilities(self, ctx):
        cs = ContextService(ctx)
        caps = cs.get_capabilities()
        assert len(caps) >= 2
        assert any("Chat" in c for c in caps)
