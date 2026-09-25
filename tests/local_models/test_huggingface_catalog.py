import httpx
import pytest

from src.local_models.huggingface_catalog import HuggingFaceCatalog


@pytest.mark.asyncio
async def test_huggingface_search_filters_gguf_and_never_exposes_token(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "secret-value")

    def handler(request):
        assert request.url.path == "/api/models"
        assert request.url.params.get("filter") == "gguf"
        assert request.headers["authorization"] == "Bearer secret-value"
        return httpx.Response(
            200,
            json=[
                {
                    "id": "owner/model-GGUF",
                    "tags": ["gguf", "license:apache-2.0"],
                    "pipeline_tag": "text-generation",
                    "downloads": 12,
                    "gated": False,
                    "siblings": [{"rfilename": "model-Q4_K_M.gguf", "size": 1234}],
                }
            ],
        )

    source = HuggingFaceCatalog(transport=httpx.MockTransport(handler))
    models, meta = await source.search("model")
    assert models[0].reference.pull_reference == "hf.co/owner/model-GGUF"
    assert models[0].license == "apache-2.0"
    assert models[0].size_bytes == 1234
    assert "secret-value" not in repr(models) + repr(meta)


@pytest.mark.asyncio
async def test_non_gguf_result_is_not_installable():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=[{"id": "owner/model", "tags": ["safetensors"]}])
    )
    models, _ = await HuggingFaceCatalog(transport=transport).search()
    assert models == []
