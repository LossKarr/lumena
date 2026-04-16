"""
Tests de regression:
- Parsing d'outils robuste avec JSON imbrique
- Coherence write/read/edit/list avec redirection workspace
- Aggregation complete des reponses Anthropic/Gemini
"""

from pathlib import Path
import sys
import importlib
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.tools.tool_system import LumenaToolSystem
from src.llm import multi_provider


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, payload):
        self._response = _FakeResponse(payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        return self._response


def _patch_http_post(monkeypatch, payload, llm_instance=None):
    """Patch both legacy httpx.AsyncClient AND the persistent self._http.post."""
    fake_client = _FakeAsyncClient(payload)
    monkeypatch.setattr(
        multi_provider.httpx,
        "AsyncClient",
        lambda *args, **kwargs: fake_client,
    )
    # Also patch persistent client on the instance if provided
    if llm_instance is not None:
        monkeypatch.setattr(llm_instance, "_http", fake_client)


class _FakeMemoryProvider:
    def __init__(self):
        self.remember_calls = []
        self.recall_calls = []

    def remember(self, content, memory_type="episodic", importance=0.5):
        self.remember_calls.append((content, memory_type, importance))
        return "mem_test"

    def recall(self, query, limit=5):
        self.recall_calls.append((query, limit))
        return [SimpleNamespace(content="memo-from-bound-provider")]


def test_parse_tool_calls_supports_nested_json():
    tool_system = LumenaToolSystem()
    # Enregistre un stub write_file pour que le parser le reconnaisse
    tool_system.tools["write_file"] = {
        "name": "write_file",
        "description": "stub",
        "parameters": {"path": {"type": "string"}, "content": {"type": "string"}},
    }
    text = (
        '[TOOL:write_file] {"path":"demo/index.html",'
        '"content":"<style>body{color:red;}</style>"}'
    )

    calls = tool_system.parse_tool_calls_from_text(text)

    assert len(calls) == 1
    assert calls[0].name == "write_file"
    assert calls[0].arguments["path"] == "demo/index.html"
    assert "body{color:red;}" in calls[0].arguments["content"]


def test_workspace_path_preserves_relative_subdirs(tmp_path: Path):
    from src.tools.file_guardrails import WorkspaceFileGuardrails

    guardrails = WorkspaceFileGuardrails(lumena_root=tmp_path)
    sanitized = guardrails.sanitize_workspace_relative_path(Path("site/assets/app.js"))

    assert tuple(sanitized.parts) == ("site", "assets", "app.js")


@pytest.mark.asyncio
async def test_write_read_edit_and_list_use_workspace_resolution(tmp_path: Path):
    from src.reasoning.handlers.context import HandlerContext
    from src.reasoning.handlers.files import (
        write_file_handler, read_file_handler, edit_file_handler, list_directory_handler,
    )
    from src.tools.file_guardrails import WorkspaceFileGuardrails

    ctx = HandlerContext(
        lumena_root=tmp_path,
        runtime_root=tmp_path,
        file_guardrails=WorkspaceFileGuardrails(tmp_path),
    )

    write_result = await write_file_handler(ctx, path="demo/index.html", content="<html><body>Bonjour</body></html>")
    assert "✅" in write_result.output

    read_result = await read_file_handler(ctx, path="demo/index.html")
    assert "Bonjour" in read_result.output

    edit_result = await edit_file_handler(ctx, file_path="demo/index.html", old_content="Bonjour", new_content="Salut")
    assert "✅" in edit_result.output

    read_after_edit = await read_file_handler(ctx, path="demo/index.html")
    assert "Salut" in read_after_edit.output

    list_result = await list_directory_handler(ctx, path="demo")
    assert "index.html" in list_result.output


@pytest.mark.asyncio
async def test_tool_system_memory_bind_uses_bound_provider(tmp_path: Path):
    tool_system = LumenaToolSystem(lumena_root=tmp_path)
    memory_provider = _FakeMemoryProvider()
    tool_system.bind_memory(memory_provider)

    # Vérifie que le provider est stocké correctement
    assert tool_system._memory_provider is memory_provider
    # bind_memory ne remplace pas _canonical_memory_provider
    assert tool_system._canonical_memory_provider is None

    # Test direct du provider (sans passer par handlers legacy)
    memory_provider.remember("memoire critique", memory_type="semantic", importance=0.5)
    assert memory_provider.remember_calls
    assert memory_provider.remember_calls[0][0] == "memoire critique"
    assert memory_provider.remember_calls[0][1] == "semantic"

    results = memory_provider.recall("memoire")
    assert memory_provider.recall_calls
    assert memory_provider.recall_calls[0] == ("memoire", 5)
    assert results[0].content == "memo-from-bound-provider"


@pytest.mark.asyncio
async def test_chat_anthropic_concatenates_all_text_blocks(monkeypatch):
    llm = multi_provider.MultiProviderLLM()

    monkeypatch.setattr(multi_provider, "get_api_key", lambda provider: "test-key")
    _patch_http_post(
        monkeypatch,
        {
            "content": [
                {"type": "text", "text": "partie-1 "},
                {"type": "text", "text": "partie-2"},
            ]
        },
        llm_instance=llm,
    )

    result = await llm._chat_anthropic(messages=[{"role": "user", "content": "ping"}])
    assert result == "partie-1 partie-2"


@pytest.mark.asyncio
async def test_chat_google_concatenates_all_text_parts(monkeypatch):
    llm = multi_provider.MultiProviderLLM()

    monkeypatch.setattr(multi_provider, "get_api_key", lambda provider: "test-key")
    _patch_http_post(
        monkeypatch,
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "ligne-1 "},
                            {"text": "ligne-2"},
                        ]
                    }
                }
            ]
        },
        llm_instance=llm,
    )

    result = await llm._chat_google(messages=[{"role": "user", "content": "ping"}])
    assert result == "ligne-1 ligne-2"


@pytest.mark.asyncio
async def test_server_lifespan_fails_fast_when_core_init_fails(monkeypatch):
    """P0.9: init failure with SETUP_COMPLETE=1 → setup_only_mode (not crash)."""
    import web.routes.lifespan as lifespan_module
    import web.routes.deps as deps_module
    server_module = importlib.import_module("web.server")

    class _FailingCore:
        is_initialized = False
        async def initialize(self):
            return False

    async def _fake_initialize(*args, **kwargs):
        return _FailingCore()

    monkeypatch.setattr(lifespan_module, "initialize_lumena", _fake_initialize)
    monkeypatch.setenv("LUMENA_SINGLE_INSTANCE", "0")

    # P0.9: should NOT raise anymore — should enter setup_only_mode instead
    old = deps_module.setup_only_mode
    try:
        async with server_module.lifespan(server_module.app):
            assert deps_module.setup_only_mode is True
    finally:
        deps_module.setup_only_mode = old
