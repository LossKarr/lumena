import httpx
import pytest

from src.local_models.ollama_library import OllamaLibrarySource


@pytest.mark.asyncio
async def test_public_search_discovers_current_official_library_entries():
    html = b"""<a href="/library/qwen3.8"><h2>qwen3.8</h2></a>
    <a href="/library/gemma4"><h2>gemma4</h2></a>"""
    source = OllamaLibrarySource(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=html)))
    models, metadata = await source.search("future", limit=20)
    names = {item.reference.canonical for item in models}
    assert {"qwen3.8:latest", "gemma4:latest"} <= names
    assert metadata["public_search_available"] is True
    assert metadata["partial"] is True


@pytest.mark.asyncio
async def test_public_search_failure_falls_back_to_curated_catalogue():
    source = OllamaLibrarySource(
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
    )
    models, metadata = await source.search("qwen", limit=100)
    assert models
    assert metadata["public_search_available"] is False
    assert metadata["public_search_error"] == "ollama_library_unavailable"


@pytest.mark.asyncio
async def test_public_search_can_be_disabled():
    source = OllamaLibrarySource(public_search=False)
    models, metadata = await source.search("qwen", limit=100)
    assert models
    assert metadata["public_search_available"] is False
    assert metadata["public_search_error"] is None
