from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.llm.codex_app_server import CodexNotification
from src.llm.codex_subscription import (
    ACCOUNT_LOGIN_CANCEL_METHOD,
    ACCOUNT_LOGIN_COMPLETED_NOTIFICATION,
    ACCOUNT_LOGIN_START_METHOD,
    ACCOUNT_LOGOUT_METHOD,
    ACCOUNT_RATE_LIMITS_READ_METHOD,
    ACCOUNT_READ_METHOD,
    ACCOUNT_RATE_LIMITS_UPDATED_NOTIFICATION,
    MODEL_LIST_METHOD,
    CodexAccountState,
    CodexModelSummary,
    CodexSubscriptionAccountError,
    CodexSubscriptionGateway,
    mask_codex_email,
    normalise_codex_account,
    normalise_codex_login_challenge,
    normalise_codex_models,
    normalise_codex_rate_limits,
    strip_codex_credentials,
)


def test_recursive_secret_stripping_and_email_masking():
    clean = strip_codex_credentials(
        {
            "account": {"email": "charles@example.com", "accessToken": "secret"},
            "refresh_token": "secret-2",
            "items": [{"apiKey": "secret-3", "value": 1}],
        }
    )
    assert clean == {
        "account": {"email": "charles@example.com"},
        "items": [{"value": 1}],
    }
    assert mask_codex_email("charles@example.com") == "c***@e***.com"
    assert mask_codex_email("invalid") == ""


@pytest.mark.parametrize(
    ("payload", "account_type", "usable"),
    [
        ({"account": {"type": "chatgpt", "email": "a@b.fr"}}, "chatgpt", True),
        ({"accountType": "business", "plan": "Business"}, "chatgpt_team", True),
        ({"account": {"type": "apiKey"}}, "api_key", False),
        ({"account": None}, "", False),
    ],
)
def test_account_normalization(payload, account_type, usable):
    account = normalise_codex_account(payload)
    assert account.account_type == account_type
    assert account.subscription_usable is usable
    if account_type:
        assert account.state is CodexAccountState.CONNECTED


def test_login_challenge_only_accepts_https_urls_and_no_tokens():
    challenge = normalise_codex_login_challenge(
        {
            "login": {
                "loginId": "login-1",
                "authUrl": "javascript:alert(1)",
                "verificationUri": "https://auth.openai.com/device",
                "userCode": "ABCD-EFGH",
                "expiresIn": "600",
                "accessToken": "must-disappear",
            }
        }
    )
    assert challenge.login_id == "login-1"
    assert challenge.auth_url == ""
    assert challenge.verification_url == "https://auth.openai.com/device"
    assert challenge.user_code == "ABCD-EFGH"
    assert challenge.expires_in_s == 600
    assert "must-disappear" not in str(challenge.to_dict())

    hostile = normalise_codex_login_challenge(
        {"loginId": "login-2", "authUrl": "https://openai.example/phishing"}
    )
    assert hostile.auth_url == ""


def test_rate_limits_normalize_and_detect_exhaustion():
    quota = normalise_codex_rate_limits(
        {
            "rateLimits": {
                "primary": {"usedPercent": 100, "resetsAt": 123},
                "secondary": {"percentUsed": 37.5, "resetAt": "later"},
                "accessToken": "secret",
            }
        }
    )
    assert quota.exhausted is True
    assert quota.primary_used_percent == 100.0
    assert quota.secondary_used_percent == 37.5
    assert "secret" not in str(quota.raw)


def test_models_are_dynamic_normalized_filtered_and_secret_free():
    models = normalise_codex_models(
        {
            "defaultModelId": "gpt-codex-pro",
            "models": [
                {
                    "id": "gpt-codex-pro",
                    "displayName": "GPT Codex Pro",
                    "description": "  Recommended   coding model ",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "medium"},
                        {"reasoningEffort": "high"},
                    ],
                    "inputModalities": ["text", "image"],
                    "accessToken": "must-disappear",
                },
                {"id": "hidden", "hidden": True},
                {"id": "disabled", "available": False},
                {"id": "gpt-codex-pro", "displayName": "duplicate"},
            ],
        }
    )
    assert models == (
        CodexModelSummary(
            model_id="gpt-codex-pro",
            display_name="GPT Codex Pro",
            description="Recommended coding model",
            is_default=True,
            reasoning_efforts=("medium", "high"),
            input_modalities=("text", "image"),
        ),
    )
    assert "must-disappear" not in str(models)


def test_models_accept_data_envelope_and_reject_malformed_payloads():
    models = normalise_codex_models(
        {"data": [{"model": "m1", "name": "Model One", "recommended": True}]}
    )
    assert models[0].model_id == "m1"
    assert models[0].is_default is True
    assert normalise_codex_models({"models": "not-a-list"}) == ()


class FakeSupervisor:
    def __init__(self, responses):
        self.responses = responses
        self.request = AsyncMock(side_effect=self._request)
        self.wait_for_notification = AsyncMock()

    async def _request(self, method, params):
        return self.responses.get(method)


@pytest.mark.asyncio
async def test_gateway_uses_official_account_methods_and_rejects_api_key():
    supervisor = FakeSupervisor(
        {
            ACCOUNT_READ_METHOD: {"account": {"type": "apiKey"}},
            ACCOUNT_LOGIN_START_METHOD: {
                "loginId": "l1",
                "authUrl": "https://auth.openai.com/start",
            },
            ACCOUNT_RATE_LIMITS_READ_METHOD: {"rateLimits": {}},
            ACCOUNT_LOGIN_CANCEL_METHOD: {},
            ACCOUNT_LOGOUT_METHOD: {},
        }
    )
    gateway = CodexSubscriptionGateway(supervisor)
    with pytest.raises(CodexSubscriptionAccountError, match="API key"):
        await gateway.require_chatgpt_account()
    challenge = await gateway.start_login()
    await gateway.cancel_login(challenge.login_id)
    await gateway.logout()
    await gateway.read_rate_limits()
    assert challenge.login_id == "l1"
    methods = [call.args[0] for call in supervisor.request.await_args_list]
    assert methods == [
        ACCOUNT_READ_METHOD,
        ACCOUNT_LOGIN_START_METHOD,
        ACCOUNT_LOGIN_CANCEL_METHOD,
        ACCOUNT_LOGOUT_METHOD,
        ACCOUNT_RATE_LIMITS_READ_METHOD,
    ]


@pytest.mark.asyncio
async def test_wait_for_login_filters_id_then_refreshes_account():
    supervisor = FakeSupervisor(
        {ACCOUNT_READ_METHOD: {"account": {"type": "chatgpt", "email": "a@b.fr"}}}
    )

    async def wait(method, predicate, timeout):
        assert method == ACCOUNT_LOGIN_COMPLETED_NOTIFICATION
        assert timeout == 4
        wrong = CodexNotification(method=method, params={"loginId": "other"})
        right = CodexNotification(method=method, params={"loginId": "wanted"})
        assert predicate(wrong) is False
        assert predicate(right) is True
        return right

    supervisor.wait_for_notification.side_effect = wait
    account = await CodexSubscriptionGateway(supervisor).wait_for_login(
        "wanted", timeout=4
    )
    assert account.subscription_usable is True
    supervisor.request.assert_awaited_once_with(
        ACCOUNT_READ_METHOD, {"refreshToken": True}
    )


@pytest.mark.asyncio
async def test_live_rate_limit_notification_is_normalized():
    supervisor = FakeSupervisor({})
    supervisor.wait_for_notification.return_value = CodexNotification(
        method=ACCOUNT_RATE_LIMITS_UPDATED_NOTIFICATION,
        params={"rateLimits": {"primary": {"usedPercent": 42}}},
    )
    quota = await CodexSubscriptionGateway(supervisor).wait_for_rate_limits_update(
        timeout=3
    )
    assert quota.primary_used_percent == 42
    supervisor.wait_for_notification.assert_awaited_once_with(
        ACCOUNT_RATE_LIMITS_UPDATED_NOTIFICATION, timeout=3
    )


@pytest.mark.asyncio
async def test_gateway_lists_only_models_returned_by_app_server():
    supervisor = FakeSupervisor(
        {MODEL_LIST_METHOD: {"models": [{"id": "account-model", "isDefault": True}]}}
    )
    models = await CodexSubscriptionGateway(supervisor).list_models()
    assert [model.model_id for model in models] == ["account-model"]
    supervisor.request.assert_awaited_once_with(MODEL_LIST_METHOD, {})
