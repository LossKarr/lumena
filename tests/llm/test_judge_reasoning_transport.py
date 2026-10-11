from __future__ import annotations

import pytest

from src.llm import multi_provider
from src.llm.multi_provider import MultiProviderLLM


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": 'analyse privee {"score":8,"reason_codes":["proof"]}',
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }


class _Http:
    async def post(self, *_args, **_kwargs):
        return _Response()


@pytest.mark.asyncio
async def test_deepseek_transporte_le_raisonnement_en_interne_sans_le_rendre_public(monkeypatch) -> None:
    monkeypatch.setattr(multi_provider, "get_api_key", lambda _provider: "test-key")
    llm = object.__new__(MultiProviderLLM)
    llm._http = _Http()
    llm._config = None

    result = await llm._chat_deepseek_result(
        [{"role": "user", "content": "fixture"}],
        model="deepseek-flash",
        max_tokens=2048,
    )

    assert result["text"] == ""
    assert result["reasoning_content"].endswith('{"score":8,"reason_codes":["proof"]}')
    assert await llm._chat_deepseek(
        [{"role": "user", "content": "fixture"}],
        model="deepseek-flash",
        max_tokens=2048,
    ) == ""

