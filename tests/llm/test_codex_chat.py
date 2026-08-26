from __future__ import annotations

import asyncio
from collections import deque

import pytest

from src.llm.codex_app_server import CodexNotification
from src.llm.codex_chat import (
    CodexChatSessionRegistry,
    CodexChatUnavailable,
    build_codex_chat_prompt,
    codex_chat_requires_agent,
    pop_codex_chat_delta_sink,
    push_codex_chat_delta_sink,
    run_chat_with_codex_subscription,
    should_route_chat_to_codex,
)
from src.llm.codex_subscription import (
    CodexSurface,
    CodexSubscriptionSettings,
    OpenAIAccessMode,
)


def _settings(*, model: str = "", enabled: bool = True):
    return CodexSubscriptionSettings(
        access_mode=(
            OpenAIAccessMode.CHATGPT_CODEX if enabled else OpenAIAccessMode.API
        ),
        default_model=model,
        surfaces=frozenset({CodexSurface.CODEAGENT, CodexSurface.CHAT}),
    )


def _notification(method: str, params: dict) -> CodexNotification:
    return CodexNotification(method=method, params=params)


def _successful_notifications(
    *, thread_id: str = "thread-1", turn_id: str = "turn-1", text: str = "Bonjour."
):
    first, second = text[: len(text) // 2], text[len(text) // 2 :]
    return [
        _notification(
            "item/agentMessage/delta",
            {"threadId": thread_id, "turnId": turn_id, "delta": first},
        ),
        _notification(
            "item/agentMessage/delta",
            {"threadId": thread_id, "turnId": turn_id, "delta": second},
        ),
        _notification(
            "item/completed",
            {
                "threadId": thread_id,
                "turnId": turn_id,
                "item": {"type": "agentMessage", "text": text},
            },
        ),
        _notification(
            "turn/completed",
            {
                "threadId": thread_id,
                "turn": {"id": turn_id, "status": "completed"},
            },
        ),
    ]


class FakeSupervisor:
    def __init__(self, notifications=()):
        self.is_running = True
        self.notifications = deque(notifications)
        self.requests: list[tuple[str, dict, float | None]] = []
        self.thread_starts = 0
        self.turn_starts = 0

    async def request(self, method, params=None, *, timeout=None):
        params = dict(params or {})
        self.requests.append((method, params, timeout))
        if method == "account/read":
            return {"account": {"type": "chatgpt", "plan": "plus"}}
        if method == "model/list":
            return {
                "models": [
                    {"id": "account-default", "isDefault": True},
                    {"id": "account-choice", "displayName": "Account Choice"},
                ]
            }
        if method == "thread/start":
            self.thread_starts += 1
            return {"thread": {"id": f"thread-{self.thread_starts}"}}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        if method == "turn/start":
            self.turn_starts += 1
            return {"turn": {"id": f"turn-{self.turn_starts}"}}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(f"unexpected method: {method}")

    async def next_notification(self, *, timeout=None):
        if not self.notifications:
            await asyncio.Event().wait()
        return self.notifications.popleft()


def test_chat_route_is_explicit_opt_in():
    assert should_route_chat_to_codex(_settings()) is True
    assert should_route_chat_to_codex(_settings(enabled=False)) is False
    code_only = CodexSubscriptionSettings(
        access_mode=OpenAIAccessMode.CHATGPT_CODEX,
        surfaces=frozenset({CodexSurface.CODEAGENT}),
    )
    assert should_route_chat_to_codex(code_only) is False


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Comment fonctionne ta mémoire ?", False),
        ("Peux-tu m'expliquer les closures Python ?", False),
        ("Crée un fichier rapport.md", True),
        ("Peux-tu modifier app.py ?", True),
        ("/agent ouvre le navigateur", True),
    ],
)
def test_action_detector_is_conservative(message, expected):
    assert codex_chat_requires_agent(message) is expected


@pytest.mark.asyncio
async def test_action_request_is_refused_without_any_rpc(tmp_path):
    supervisor = FakeSupervisor()
    result = await run_chat_with_codex_subscription(
        [{"role": "user", "content": "Crée rapport.md"}],
        user_message="Crée rapport.md",
        conversation_id="conv-action",
        cwd=tmp_path,
        settings=_settings(),
        supervisor=supervisor,
        registry=CodexChatSessionRegistry(tmp_path / "sessions.json"),
    )
    assert result.action_refused is True
    assert "mode Agent" in result.response
    assert result.meta["fallback_used"] is False
    assert supervisor.requests == []


@pytest.mark.asyncio
async def test_dynamic_model_context_read_only_stream_and_metadata(tmp_path):
    supervisor = FakeSupervisor(
        _successful_notifications(text="Réponse Lumena depuis Codex.")
    )
    streamed: list[str] = []
    token = push_codex_chat_delta_sink(streamed.append)
    try:
        result = await run_chat_with_codex_subscription(
            [
                {"role": "system", "content": "IDENTITÉ LUMENA ET MÉMOIRE"},
                {"role": "assistant", "content": "Ancienne réponse"},
                {"role": "user", "content": "Qui suis-je ?"},
            ],
            user_message="Qui suis-je ?",
            conversation_id="conv-1",
            cwd=tmp_path,
            settings=_settings(model="account-choice"),
            supervisor=supervisor,
            registry=CodexChatSessionRegistry(tmp_path / "sessions.json"),
        )
    finally:
        pop_codex_chat_delta_sink(token)

    assert result.response == "Réponse Lumena depuis Codex."
    assert "".join(streamed) == result.response
    assert result.model == "account-choice"
    assert result.meta["provider_used"] == "openai-codex"
    assert result.meta["access_source_used"] == "codex"
    assert result.meta["billing_source"] == "chatgpt_subscription"
    assert result.meta["fallback_used"] is False

    thread = next(params for method, params, _ in supervisor.requests if method == "thread/start")
    turn = next(params for method, params, _ in supervisor.requests if method == "turn/start")
    assert thread["approvalPolicy"] == "never"
    assert thread["sandbox"] == "read-only"
    assert thread["model"] == "account-choice"
    assert turn["cwd"] == str(tmp_path.resolve())
    assert turn["sandboxPolicy"] == {
        "type": "readOnly",
        "networkAccess": False,
    }
    prompt = turn["input"][0]["text"]
    assert "IDENTITÉ LUMENA ET MÉMOIRE" in prompt
    assert "Ancienne réponse" in prompt
    assert "Qui suis-je ?" in prompt


@pytest.mark.asyncio
async def test_unavailable_preference_uses_account_default_not_api_fallback(tmp_path):
    supervisor = FakeSupervisor(_successful_notifications())
    result = await run_chat_with_codex_subscription(
        [{"role": "user", "content": "Bonjour"}],
        user_message="Bonjour",
        conversation_id="conv-model",
        cwd=tmp_path,
        settings=_settings(model="retired-model"),
        supervisor=supervisor,
        registry=CodexChatSessionRegistry(tmp_path / "sessions.json"),
    )
    assert result.model == "account-default"
    assert result.meta["model_requested"] == "retired-model"
    assert result.meta["model_used"] == "account-default"
    assert result.meta["fallback_used"] is False


@pytest.mark.asyncio
async def test_second_turn_resumes_the_same_conversation(tmp_path):
    registry = CodexChatSessionRegistry(tmp_path / "sessions.json")
    supervisor = FakeSupervisor(_successful_notifications(text="Premier"))
    await run_chat_with_codex_subscription(
        [{"role": "system", "content": "Lumena"}, {"role": "user", "content": "Un"}],
        user_message="Un",
        conversation_id="same",
        cwd=tmp_path,
        settings=_settings(),
        supervisor=supervisor,
        registry=registry,
    )
    supervisor.notifications.extend(
        _successful_notifications(turn_id="turn-2", text="Deuxième")
    )
    result = await run_chat_with_codex_subscription(
        [
            {"role": "system", "content": "Lumena"},
            {"role": "assistant", "content": "Premier"},
            {"role": "user", "content": "Deux"},
        ],
        user_message="Deux",
        conversation_id="same",
        cwd=tmp_path,
        settings=_settings(),
        supervisor=supervisor,
        registry=registry,
    )
    assert result.response == "Deuxième"
    resume = next(params for method, params, _ in supervisor.requests if method == "thread/resume")
    assert resume["threadId"] == "thread-1"
    second_turn = [params for method, params, _ in supervisor.requests if method == "turn/start"][-1]
    assert "Premier" not in second_turn["input"][0]["text"]
    assert "Deux" in second_turn["input"][0]["text"]


@pytest.mark.asyncio
async def test_disconnected_chat_fails_closed_without_paid_api(tmp_path):
    supervisor = FakeSupervisor()
    supervisor.is_running = False
    with pytest.raises(CodexChatUnavailable, match="Aucun fallback API"):
        await run_chat_with_codex_subscription(
            [{"role": "user", "content": "Bonjour"}],
            user_message="Bonjour",
            conversation_id="conv-offline",
            cwd=tmp_path,
            settings=_settings(),
            supervisor=supervisor,
        )
    assert supervisor.requests == []


@pytest.mark.asyncio
async def test_cancellation_interrupts_the_exact_chat_turn(tmp_path):
    supervisor = FakeSupervisor()
    task = asyncio.create_task(
        run_chat_with_codex_subscription(
            [{"role": "user", "content": "Parle-moi longuement"}],
            user_message="Parle-moi longuement",
            conversation_id="conv-cancel",
            cwd=tmp_path,
            settings=_settings(),
            supervisor=supervisor,
            registry=CodexChatSessionRegistry(tmp_path / "sessions.json"),
            timeout_s=30,
        )
    )
    for _ in range(100):
        if any(method == "turn/start" for method, _, _ in supervisor.requests):
            break
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    interrupt = next(
        params for method, params, _ in supervisor.requests if method == "turn/interrupt"
    )
    assert interrupt == {"threadId": "thread-1", "turnId": "turn-1"}


def test_prompt_is_read_only_and_never_exposes_reasoning():
    prompt = build_codex_chat_prompt(
        [{"role": "system", "content": "Personnalité"}, {"role": "user", "content": "Salut"}],
        include_history=True,
        include_system=True,
    )
    assert "N'utilise aucun outil" in prompt
    assert "N'expose aucun raisonnement interne" in prompt
