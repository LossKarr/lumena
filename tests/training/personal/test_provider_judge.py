from __future__ import annotations

import json

import pytest

from src.training.personal.provider_judge import (
    DedicatedJudgeAdapter,
    _attempt_budgets,
    _decode_judge_response,
)


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


class _DirectReasoningClient:
    def __init__(self, results):
        self.fallback_order = ["fallback"]
        self.results = list(results)
        self.calls = []
        self.closed = False

    async def _chat_provider_result(self, **kwargs):
        self.calls.append(kwargs)
        assert self.fallback_order == []
        return self.results.pop(0)

    async def chat(self, *_args, **_kwargs):
        raise AssertionError("le juge ne doit pas emprunter chat() et ses fallbacks")

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


@pytest.mark.parametrize("raw", [
    {"score": 8, "reason_codes": ["ok"]},
    '```json\n{"score": 8, "reason_codes": ["ok"]}\n```',
    'Analyse interne terminée. {"score": 8, "reason_codes": ["ok"]}',
])
def test_decode_accepte_les_formes_json_bornees(raw) -> None:
    assert _decode_judge_response(raw)["score"] == 8


@pytest.mark.parametrize("raw", ["", "aucun json", "[]"])
def test_decode_refuse_les_reponses_inexploitables(raw) -> None:
    with pytest.raises(ValueError, match="judge_response"):
        _decode_judge_response(raw)


def test_modele_de_raisonnement_reessaie_sans_fallback_et_extrait_le_verdict_prive(monkeypatch) -> None:
    from src.training.personal import provider_judge

    config = type("Config", (), {
        "provider": provider_judge.ProviderType.DEEPSEEK,
        "model_id": "reasoning-model-id",
        "max_output_tokens": 8192,
        "capabilities": frozenset({"reasoning"}),
    })()
    monkeypatch.setattr(provider_judge, "get_model_config", lambda _name: config)
    client = _DirectReasoningClient([
        {"text": "", "finish_reason": "length", "reasoning_content": "analyse incomplete"},
        {
            "text": "",
            "finish_reason": "stop",
            "reasoning_content": 'prive {"score":8,"reason_codes":["proof_ok"]}',
        },
    ])

    result = DedicatedJudgeAdapter(
        cloud_allowed=True,
        client_factory=lambda _name: client,
    )("reasoning-model", {"experience_id": "exp"})

    assert result == {"score": 8, "reason_codes": ["proof_ok"]}
    assert [call["max_tokens"] for call in client.calls] == [2048, 4096]
    assert all(call["model"] == "reasoning-model-id" for call in client.calls)
    assert client.closed is True


def test_modele_texte_reessaie_une_fois_avec_budget_borne(monkeypatch) -> None:
    from src.training.personal import provider_judge

    config = type("Config", (), {
        "provider": provider_judge.ProviderType.OLLAMA,
        "model_id": "local-model:latest",
        "max_output_tokens": 4096,
        "capabilities": frozenset(),
    })()
    monkeypatch.setattr(provider_judge, "get_model_config", lambda _name: config)
    client = _DirectReasoningClient([
        {"text": "pas du json", "finish_reason": "stop"},
        {"text": '{"score":6,"reason_codes":["partial"]}', "finish_reason": "stop"},
    ])

    result = DedicatedJudgeAdapter(
        cloud_allowed=False,
        client_factory=lambda _name: client,
    )("local-model", {"experience_id": "exp"})

    assert result["score"] == 6
    assert [call["max_tokens"] for call in client.calls] == [512, 1024]


def test_deux_reponses_invalides_echouent_sans_troisieme_appel(monkeypatch) -> None:
    from src.training.personal import provider_judge

    config = type("Config", (), {
        "provider": provider_judge.ProviderType.OLLAMA,
        "model_id": "local-model:latest",
        "max_output_tokens": 4096,
        "capabilities": frozenset(),
    })()
    monkeypatch.setattr(provider_judge, "get_model_config", lambda _name: config)
    client = _DirectReasoningClient([
        {"text": "", "finish_reason": "length"},
        {"text": "toujours invalide", "finish_reason": "stop"},
    ])

    with pytest.raises(ValueError, match="judge_response_invalid_json"):
        DedicatedJudgeAdapter(
            cloud_allowed=False,
            client_factory=lambda _name: client,
        )("local-model", {"experience_id": "exp"})

    assert len(client.calls) == 2
    assert client.closed is True


def test_un_modele_local_reasoner_sans_capacite_catalogue_recoit_un_budget_adapte() -> None:
    config = type("Config", (), {
        "name": "local-reasoner",
        "model_id": "deepseek-r1:14b",
        "display_name": "DeepSeek R1 local",
        "max_output_tokens": 8192,
        "capabilities": frozenset(),
    })()
    assert _attempt_budgets(config) == (2048, 4096)
