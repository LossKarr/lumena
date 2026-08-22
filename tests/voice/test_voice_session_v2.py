import json

import pytest

from src.runtime.channel_envelope import ChannelEnvelope
from src.runtime.context import RuntimeContext, get_current_runtime_context
from src.voice.v2.session import (
    VoiceSessionIdentity,
    VoiceSessionRouter,
    parse_mode_switch,
)


class _ContextCore:
    def __init__(self):
        self.calls = []

    async def chat(self, text, source_channel="web"):
        ctx = get_current_runtime_context()
        self.calls.append(("chat", text, source_channel, ctx))
        return "chat-ok"

    async def think_and_act(self, text, source_channel="web", **kwargs):
        ctx = get_current_runtime_context()
        self.calls.append(("agent", text, source_channel, ctx, kwargs))
        return "agent-ok"


def test_channel_envelope_voice_and_mode_are_first_class(monkeypatch):
    monkeypatch.delenv("LUMENA_WEB_ONLY", raising=False)
    envelope = ChannelEnvelope.from_request(
        channel="voice", client="mic", request_id=None,
        conversation_id="conv-voice", message_id=None, task_id=None,
        client_caps={}, mode="agent",
    )
    assert envelope.channel == "voice"
    assert envelope.mode == "agent"
    assert envelope.to_dict()["mode"] == "agent"


def test_runtime_context_mode_is_additive_and_normalized():
    common = dict(
        channel="voice", client="mic", request_id=None,
        conversation_id="conv", message_id=None, workspace_policy=None,
        task_id=None, client_caps=None, workspace_path=None,
        active_file_path=None, open_files=None, resolved_workspace=None,
        resolved_date=None, resolution_reason=None,
    )
    assert RuntimeContext.build(**common).mode == "chat"
    assert RuntimeContext.build(**common, mode="agent").mode == "agent"
    assert RuntimeContext.build(**common, mode="direct").mode == "chat"


def test_mode_switch_is_explicit_not_keyword_based():
    assert parse_mode_switch("Passe en mode agent") == "agent"
    assert parse_mode_switch("Lumena, repasse en mode chat") == "chat"
    assert parse_mode_switch("C'est quoi le mode agent ?") is None
    assert parse_mode_switch("Utilise un agent pour répondre") is None
    assert parse_mode_switch("Le mode chat est-il rapide ?") is None


def test_untrusted_voice_is_guest_even_if_owner_is_requested(monkeypatch):
    monkeypatch.setenv("LUMENA_VOICE_SESSION_TRUSTED", "0")
    monkeypatch.setenv("LUMENA_VOICE_SESSION_ROLE", "owner")
    monkeypatch.setenv("LUMENA_VOICE_SESSION_USER_ID", "local:owner")
    identity = VoiceSessionIdentity.from_env()
    assert identity.trusted is False
    assert identity.user_role == "guest"


def test_trusted_voice_role_requires_explicit_pairing(monkeypatch):
    monkeypatch.setenv("LUMENA_VOICE_SESSION_TRUSTED", "1")
    monkeypatch.setenv("LUMENA_VOICE_SESSION_ROLE", "owner")
    monkeypatch.setenv("LUMENA_VOICE_SESSION_USER_ID", "local:owner")
    identity = VoiceSessionIdentity.from_env()
    assert identity.trusted is True
    assert identity.user_role == "owner"
    assert identity.user_id == "local:owner"


@pytest.mark.asyncio
async def test_chat_and_agent_use_same_conversation_and_official_core_paths(tmp_path):
    core = _ContextCore()
    identity = VoiceSessionIdentity("voice:guest", "local:owner", "guest", None, False)
    router = VoiceSessionRouter(
        core, mode="chat", conversation_id="voice-conv", identity=identity,
        state_path=tmp_path / "voice-session.json",
    )
    assert await router.respond_chat("bonjour") == "chat-ok"
    router.set_mode("agent")
    assert await router.respond_agent("fais ceci", max_iterations=4) == "agent-ok"

    chat_ctx = core.calls[0][3]
    agent_ctx = core.calls[1][3]
    assert core.calls[0][:3] == ("chat", "bonjour", "voice")
    assert core.calls[1][:3] == ("agent", "fais ceci", "voice")
    assert chat_ctx.channel == agent_ctx.channel == "voice"
    assert chat_ctx.conversation_id == agent_ctx.conversation_id == "voice-conv"
    assert chat_ctx.mode == "chat"
    assert agent_ctx.mode == "agent"
    assert chat_ctx.user_role == agent_ctx.user_role == "guest"
    assert get_current_runtime_context() is None


@pytest.mark.asyncio
async def test_agent_final_ready_callback_is_forwarded_when_supported():
    core = _ContextCore()
    router = VoiceSessionRouter(core, mode="agent")
    callback = lambda text: text
    await router.respond_agent("fais ceci", final_ready_callback=callback)
    assert core.calls[-1][4]["final_ready_callback"] is callback


@pytest.mark.asyncio
async def test_runtime_context_is_always_popped_after_failure():
    class _Boom:
        async def chat(self, _text, source_channel="web"):
            assert get_current_runtime_context().channel == "voice"
            raise RuntimeError("boom")

    router = VoiceSessionRouter(_Boom())
    with pytest.raises(RuntimeError, match="boom"):
        await router.respond_chat("test")
    assert get_current_runtime_context() is None


def test_mode_persists_only_for_explicit_product_state_path(tmp_path):
    state = tmp_path / "session.json"
    first = VoiceSessionRouter(_ContextCore(), mode="chat", state_path=state)
    assert first.handle_mode_command("active le mode agent") == "Mode Agent activé."
    assert json.loads(state.read_text(encoding="utf-8"))["mode"] == "agent"
    second = VoiceSessionRouter(_ContextCore(), mode="chat", state_path=state)
    assert second.mode == "agent"


def test_each_envelope_keeps_conversation_but_rotates_request_and_message_ids():
    router = VoiceSessionRouter(_ContextCore(), conversation_id="stable")
    first = router.build_envelope()
    second = router.build_envelope()
    assert first.conversation_id == second.conversation_id == "stable"
    assert first.request_id != second.request_id
    assert first.message_id != second.message_id
