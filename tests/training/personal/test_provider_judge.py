from __future__ import annotations

import json

import pytest

from src.training.personal.provider_judge import DedicatedJudgeAdapter


class _Client:
    def __init__(self):
        self.fallback_order = ["fallback"]
        self.messages = None
        self.closed = False

    async def chat(self, messages, **kwargs):
        self.messages = messages
        assert self.fallback_order == []
        assert kwargs["no_upgrade"] is True
        return json.dumps({"score": 8, "reason_codes": ["proof_ok"]})

    async def close(self):
        self.closed = True


def test_dedicated_judge_has_no_chat_context_and_disables_fallback(monkeypatch) -> None:
    from src.training.personal import provider_judge
    monkeypatch.setattr(provider_judge, "get_model_config", lambda name: type("Config", (), {"provider": provider_judge.ProviderType.OLLAMA})())
    client = _Client()
    result = DedicatedJudgeAdapter(cloud_allowed=False, client_factory=lambda name: client)("local-model", {"experience_id": "exp", "messages": []})
    assert result["score"] == 8
    serialized = json.dumps(client.messages)
    assert "memory" not in serialized and "workspace" not in serialized and "mood" not in serialized
    assert client.closed is True


def test_cloud_judge_requires_separate_consent(monkeypatch) -> None:
    from src.training.personal import provider_judge
    monkeypatch.setattr(provider_judge, "get_model_config", lambda name: type("Config", (), {"provider": provider_judge.ProviderType.OPENAI})())
    with pytest.raises(PermissionError, match="cloud"):
        DedicatedJudgeAdapter(cloud_allowed=False, client_factory=lambda name: _Client())("cloud-model", {"experience_id": "exp"})
