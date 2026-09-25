import json

import httpx
import pytest

from src.local_models.ollama_client import OllamaClient, OllamaClientError, validate_ollama_host


def _transport(handler):
    return httpx.MockTransport(handler)


def test_remote_host_requires_explicit_opt_in():
    with pytest.raises(OllamaClientError, match="ollama_remote_host_forbidden"):
        validate_ollama_host("http://example.com:11434")
    assert validate_ollama_host("http://10.0.0.4:11434", allow_remote=True) == "http://10.0.0.4:11434"


@pytest.mark.parametrize("host", ["file:///tmp/ollama", "http://user:pass@localhost:11434", "http://localhost/x"])
def test_invalid_hosts_fail_closed(host):
    with pytest.raises(OllamaClientError):
        validate_ollama_host(host)


@pytest.mark.asyncio
async def test_list_installed_uses_documented_tags_contract():
    def handler(request):
        assert request.url.path == "/api/tags"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "qwen3:8b",
                        "digest": "abc",
                        "size": 42,
                        "modified_at": "2026-01-01T00:00:00Z",
                        "details": {"family": "qwen3", "quantization_level": "Q4_K_M"},
                    }
                ]
            },
        )

    models = await OllamaClient(transport=_transport(handler)).list_installed()
    assert models[0].reference.canonical == "qwen3:8b"
    assert models[0].size_bytes == 42


@pytest.mark.asyncio
async def test_invalid_json_is_a_stable_error():
    client = OllamaClient(transport=_transport(lambda request: httpx.Response(200, content=b"not-json")))
    with pytest.raises(OllamaClientError, match="ollama_json_invalid"):
        await client.list_installed()


@pytest.mark.asyncio
async def test_pull_progress_never_moves_backwards():
    body = b"\n".join(
        json.dumps(item).encode()
        for item in [
            {"status": "pulling", "total": 100, "completed": 60},
            {"status": "pulling", "total": 100, "completed": 30},
            {"status": "success", "total": 100, "completed": 100},
        ]
    )
    client = OllamaClient(transport=_transport(lambda request: httpx.Response(200, content=body)))
    events = [event async for event in client.pull("qwen3:8b")]
    assert [event["percent"] for event in events] == [60.0, 60.0, 100.0]
    assert events[-1]["done"] is True


@pytest.mark.asyncio
async def test_pull_error_is_not_reported_as_success():
    body = b'{"status":"pulling"}\n{"error":"remote failure"}'
    client = OllamaClient(transport=_transport(lambda request: httpx.Response(200, content=body)))
    with pytest.raises(OllamaClientError, match="ollama_pull_failed"):
        _ = [event async for event in client.pull("qwen3:8b")]
