import pytest

from src.local_models.catalog_service import LocalModelCatalogService
from src.local_models.contracts import CatalogModel
from src.local_models.identifiers import parse_model_reference


class Source:
    def __init__(self, source, fail=False):
        self.source = source
        self.fail = fail
        self.calls = 0

    async def search(self, query, *, limit, offset):
        self.calls += 1
        if self.fail:
            raise RuntimeError("offline")
        ref = (
            parse_model_reference("owner/model-GGUF", "huggingface")
            if self.source == "huggingface"
            else parse_model_reference("qwen3:8b")
        )
        return [CatalogModel(ref, ref.canonical, provenance=(self.source,))], {"source": self.source}


@pytest.mark.asyncio
async def test_federated_search_keeps_sources_and_uses_cache():
    ollama, hf = Source("ollama"), Source("huggingface")
    service = LocalModelCatalogService(ollama_source=ollama, huggingface_source=hf, ttl_seconds=60)
    first = await service.search("code")
    second = await service.search("code")
    assert len(first["models"]) == 2
    assert second["cached"] is True
    assert ollama.calls == hf.calls == 1


@pytest.mark.asyncio
async def test_one_remote_source_can_fail_without_breaking_curated_results():
    service = LocalModelCatalogService(
        ollama_source=Source("ollama"), huggingface_source=Source("huggingface", fail=True)
    )
    result = await service.search()
    assert len(result["models"]) == 1
    assert result["sources"]["huggingface"]["available"] is False
