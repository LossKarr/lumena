import pytest

from src.local_models.ollama_client import OllamaClientError
from src.local_models.verification import verify_local_model


class Client:
    def __init__(self, response="OK", fail=None):
        self.response, self.fail = response, fail

    async def show(self, reference):
        return {
            "capabilities": ["completion", "tools"],
            "model_info": {"general.architecture": "qwen", "qwen.context_length": 32768},
        }

    async def generate_canary(self, reference):
        if self.fail:
            raise OllamaClientError(self.fail)
        if not self.response:
            raise OllamaClientError("ollama_canary_empty")
        return self.response


@pytest.mark.asyncio
async def test_text_canary_records_only_observed_text_capability():
    result = await verify_local_model(Client(), "qwen3:8b")
    assert result.status == "verified"
    assert result.claimed_capabilities == ("completion", "tools")
    assert result.observed_capabilities == ("text",)
    assert result.proof_digest


@pytest.mark.asyncio
async def test_empty_canary_is_incompatible():
    result = await verify_local_model(Client(fail="ollama_canary_empty"), "qwen3:8b")
    assert result.status == "incompatible"
    assert result.error_code == "ollama_canary_empty"
