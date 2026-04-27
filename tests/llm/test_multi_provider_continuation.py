from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.llm.multi_provider import MultiProviderLLM
from src.llm.providers import ProviderType


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_name,method_name,trunc_reason,final_reason",
    [
        ("gpt-4o", "_chat_openai_result", "length", "stop"),
        ("kimi-k2.5", "_chat_moonshot_result", "length", "stop"),
        ("deepseek-v3", "_chat_deepseek_result", "length", "stop"),
        ("claude-sonnet-4", "_chat_anthropic_result", "max_tokens", "end_turn"),
        ("gemini-2.5-flash", "_chat_google_result", "MAX_TOKENS", "STOP"),
    ],
)
async def test_chat_continuation_is_applied_for_all_cloud_providers(
    monkeypatch,
    model_name: str,
    method_name: str,
    trunc_reason: str,
    final_reason: str,
):
    llm = MultiProviderLLM(model_name=model_name)

    responses = [
        {
            "text": "prefix-0123456789abcdef",
            "finish_reason": trunc_reason,
            "provider_used": llm.provider.value,
            "model_used": llm.model,
        },
        {
            "text": "0123456789abcdef-suffix",
            "finish_reason": final_reason,
            "provider_used": llm.provider.value,
            "model_used": llm.model,
        },
    ]

    async def fake_provider(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(llm, method_name, fake_provider)

    output = await llm.chat(
        [{"role": "user", "content": "genere une longue reponse"}],
        max_tokens=4000,
    )

    assert output == "prefix-0123456789abcdef-suffix"
    meta = llm.get_last_response_meta()
    assert meta["continuation_used"] is True
    assert meta["continuation_steps"] == 1
    assert meta["provider_requested"] == llm.provider.value
    assert meta["provider_used"] == llm.provider.value
    assert meta["fallback_used"] is False


@pytest.mark.asyncio
async def test_chat_fallback_is_explicit_and_traceable(monkeypatch):
    llm = MultiProviderLLM(model_name="gpt-4o")

    async def failing_openai(*args, **kwargs):
        raise RuntimeError("401 Unauthorized")

    async def fallback_ok(*args, **kwargs):
        return {
            "text": "fallback-local",
            "finish_reason": "stop",
            "provider_used": "ollama",
            "model_used": "lumena-v1",
        }

    # Le fallback intelligent utilise _chat_provider_result pour le provider suivant.
    # On fait échouer le provider primaire (inner) et on mock le provider result générique
    # pour simuler le fallback qui réussit.
    original_chat_provider_result = llm._chat_provider_result

    call_count = 0

    async def selective_provider_result(provider, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if provider == ProviderType.OPENAI:
            raise RuntimeError("401 Unauthorized")
        return {
            "text": "fallback-local",
            "finish_reason": "stop",
            "provider_used": provider.value,
            "model_used": "fallback-model",
        }

    monkeypatch.setattr(llm, "_chat_provider_result", selective_provider_result)

    output = await llm.chat([{"role": "user", "content": "ping"}])
    meta = llm.get_last_response_meta()

    assert output == "fallback-local"
    assert meta["provider_requested"] == "openai"
    assert meta["fallback_used"] is True
    assert "401" in (meta["fallback_reason"] or "")


@pytest.mark.asyncio
async def test_chat_continuation_warning_when_still_truncated(monkeypatch):
    llm = MultiProviderLLM(model_name="gpt-4o")
    llm.max_continuation_steps = 1

    responses = [
        {
            "text": "bloc-initial-0123456789abcdef",
            "finish_reason": "length",
            "provider_used": "openai",
            "model_used": llm.model,
        },
        {
            "text": "0123456789abcdef-suite",
            "finish_reason": "length",
            "provider_used": "openai",
            "model_used": llm.model,
        },
    ]

    async def fake_openai(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(llm, "_chat_openai_result", fake_openai)

    output = await llm.chat([{"role": "user", "content": "long"}], max_tokens=1000)
    meta = llm.get_last_response_meta()

    # Le warning n'est plus injecté dans le texte (évite de corrompre le code généré)
    # On vérifie qu'il est dans les métadonnées à la place
    assert "potentiellement incompl" not in output
    assert meta["continuation_warning"] is not None
    assert "potentiellement incompl" in meta["continuation_warning"] or "incomplète" in meta["continuation_warning"]
    assert meta["text_may_be_incomplete"] is True
    assert meta["continuation_used"] is True
    assert meta["continuation_steps"] == 1
    assert str(meta["finish_reason"]).lower() == "length"


@pytest.mark.asyncio
async def test_chat_auto_switches_to_reasoner_for_long_code_tasks(monkeypatch):
    llm = MultiProviderLLM(model_name="deepseek-v3")
    captured = {}

    async def fake_deepseek_result(*args, **kwargs):
        captured["model"] = kwargs.get("model")
        return {
            "text": "ok",
            "finish_reason": "stop",
            "provider_used": "deepseek",
            "model_used": kwargs.get("model") or llm.model,
        }

    monkeypatch.setattr(llm, "_chat_deepseek_result", fake_deepseek_result)

    output = await llm.chat(
        [{"role": "user", "content": "corrige ce code python et applique un patch propre"}],
        max_tokens=12000,
    )
    meta = llm.get_last_response_meta()

    assert output == "ok"
    assert "reasoner" in str(captured.get("model", "")).lower()
    assert meta["auto_switch_used"] is True
    assert meta["auto_switch_reason"] is not None


@pytest.mark.asyncio
async def test_chat_auto_switch_uplifts_inherited_deepseek_chat_budget(monkeypatch):
    llm = MultiProviderLLM(model_name="deepseek-v3")
    captured = {}

    async def fake_deepseek_result(*args, **kwargs):
        captured["model"] = kwargs.get("model")
        captured["max_tokens"] = kwargs.get("max_tokens")
        return {
            "text": "ok",
            "finish_reason": "stop",
            "provider_used": "deepseek",
            "model_used": kwargs.get("model") or llm.model,
        }

    monkeypatch.setattr(llm, "_chat_deepseek_result", fake_deepseek_result)

    output = await llm.chat(
        [{"role": "user", "content": "corrige ce code python et applique un patch propre"}],
        max_tokens=llm.max_output_tokens,  # hérité du modèle source deepseek-chat (= 8192)
    )
    meta = llm.get_last_response_meta()

    assert output == "ok"
    assert "reasoner" in str(captured.get("model", "")).lower()
    assert captured.get("max_tokens") == 65536
    assert meta["auto_switch_used"] is True


@pytest.mark.asyncio
async def test_chat_keeps_deepseek_v3_when_not_code_heavy(monkeypatch):
    llm = MultiProviderLLM(model_name="deepseek-v3")
    captured = {}

    async def fake_deepseek_result(*args, **kwargs):
        captured["model"] = kwargs.get("model")
        return {
            "text": "pong",
            "finish_reason": "stop",
            "provider_used": "deepseek",
            "model_used": kwargs.get("model") or llm.model,
        }

    monkeypatch.setattr(llm, "_chat_deepseek_result", fake_deepseek_result)

    output = await llm.chat(
        [{"role": "user", "content": "salut comment ca va"}],
        max_tokens=1000,
    )
    meta = llm.get_last_response_meta()

    assert output == "pong"
    assert "reasoner" not in str(captured.get("model", "")).lower()
    assert meta["auto_switch_used"] is False


@pytest.mark.asyncio
async def test_chat_does_not_autoswitch_for_markdown_edit_task(monkeypatch):
    llm = MultiProviderLLM(model_name="deepseek-v3")
    captured = {}

    async def fake_deepseek_result(*args, **kwargs):
        captured["model"] = kwargs.get("model")
        return {
            "text": "ok",
            "finish_reason": "stop",
            "provider_used": "deepseek",
            "model_used": kwargs.get("model") or llm.model,
        }

    monkeypatch.setattr(llm, "_chat_deepseek_result", fake_deepseek_result)

    output = await llm.chat(
        [{"role": "user", "content": "modifie ce fichier markdown rapport-hieroglyphes.md section 2"}],
        max_tokens=16384,
    )
    meta = llm.get_last_response_meta()

    assert output == "ok"
    assert "reasoner" not in str(captured.get("model", "")).lower()
    assert meta["auto_switch_used"] is False


@pytest.mark.asyncio
async def test_chat_uses_effective_intent_from_react_prompt(monkeypatch):
    llm = MultiProviderLLM(model_name="deepseek-v3")
    captured = {}

    async def fake_deepseek_result(*args, **kwargs):
        captured["model"] = kwargs.get("model")
        return {
            "text": "ok",
            "finish_reason": "stop",
            "provider_used": "deepseek",
            "model_used": kwargs.get("model") or llm.model,
        }

    monkeypatch.setattr(llm, "_chat_deepseek_result", fake_deepseek_result)

    react_prompt = (
        "SYSTEM BLOATED PROMPT WITH MANY CODE WORDS python patch diff html css\n"
        "## Requête actuelle:\n"
        "Fais un rapport historique sur les hiéroglyphes égyptiens.\n\n"
        "Maintenant, réfléchis et réponds:"
    )

    output = await llm.chat([{"role": "user", "content": react_prompt}], max_tokens=16384)
    meta = llm.get_last_response_meta()

    assert output == "ok"
    assert "reasoner" not in str(captured.get("model", "")).lower()
    assert meta["auto_switch_used"] is False
