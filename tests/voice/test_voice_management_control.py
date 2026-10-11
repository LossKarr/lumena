from __future__ import annotations

import asyncio
import json

import pytest

from src.voice.v2.management_control import (
    apply_voice_recommendation_transaction,
    rollback_last_voice_transaction,
    validate_voice_management_updates,
    wait_until_voice_safe,
)


def _validator(values):
    return dict(values), []


def test_validation_rejects_arbitrary_and_privacy_escalation():
    with pytest.raises(ValueError, match="out_of_scope"):
        validate_voice_management_updates({"OPENAI_API_KEY": "x"})
    with pytest.raises(ValueError, match="protected"):
        validate_voice_management_updates({"LUMENA_VOICE_CLOUD_ALLOWED": "1"})
    with pytest.raises(ValueError, match="protected"):
        validate_voice_management_updates({"LUMENA_VOICE_MANAGEMENT_MODE": "safe_auto"})
    assert validate_voice_management_updates({"LUMENA_VOICE_CLOUD_ALLOWED": "0"})


@pytest.mark.asyncio
async def test_incompatible_recommendation_cannot_be_applied(tmp_path):
    with pytest.raises(ValueError, match="incompatible"):
        await apply_voice_recommendation_transaction(
            {
                "compatible": False,
                "updates": {"LUMENA_VOICE_TTS_ENGINE": "qwen3"},
            },
            read_values=lambda: {"LUMENA_VOICE_TTS_ENGINE": "piper"},
            write_values=lambda _: None,
            validate_values=_validator,
            status_provider=lambda: {"running": False},
            explicitly_authorized=True,
            state_path=tmp_path / "state.json",
            audit_path=tmp_path / "audit.jsonl",
        )


@pytest.mark.asyncio
async def test_apply_is_atomic_verified_and_rollbackable(tmp_path, monkeypatch):
    values = {"LUMENA_VOICE_TTS_ENGINE": "qwen3"}
    writes = []
    restarts = []

    def write(update):
        writes.append(dict(update))
        values.update(update)

    async def restart():
        restarts.append(True)
        return True

    status = {"running": True, "settings_pending": False, "runtime_state": "idle"}
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "audit.jsonl"
    result = await apply_voice_recommendation_transaction(
        {"goal": "fast", "profile_id": "balanced", "updates": {"LUMENA_VOICE_TTS_ENGINE": "piper"}},
        read_values=lambda: values,
        write_values=write,
        validate_values=_validator,
        status_provider=lambda: status,
        restart_voice=restart,
        explicitly_authorized=True,
        state_path=state_path,
        audit_path=audit_path,
    )
    assert result["state"] == "verified"
    assert values["LUMENA_VOICE_TTS_ENGINE"] == "piper"
    assert len(restarts) == 1
    line = json.loads(audit_path.read_text(encoding="utf-8"))
    assert "prompt" not in line and "transcript" not in line

    restored = await rollback_last_voice_transaction(
        read_values=lambda: values,
        write_values=write,
        status_provider=lambda: status,
        restart_voice=restart,
        explicitly_authorized=True,
        state_path=state_path,
        audit_path=audit_path,
    )
    assert restored["state"] == "restored"
    assert values["LUMENA_VOICE_TTS_ENGINE"] == "qwen3"


@pytest.mark.asyncio
async def test_failed_restart_restores_before_value(tmp_path):
    values = {"LUMENA_VOICE_TTS_ENGINE": "qwen3"}

    def write(update):
        values.update(update)

    calls = [False, True]

    async def restart():
        return calls.pop(0)

    result = await apply_voice_recommendation_transaction(
        {"goal": "fast", "profile_id": "balanced", "updates": {"LUMENA_VOICE_TTS_ENGINE": "piper"}},
        read_values=lambda: values,
        write_values=write,
        validate_values=_validator,
        status_provider=lambda: {"running": True, "settings_pending": False, "runtime_state": "idle"},
        restart_voice=restart,
        explicitly_authorized=True,
        state_path=tmp_path / "state.json",
        audit_path=tmp_path / "audit.jsonl",
    )
    assert result["state"] == "rolled_back"
    assert values["LUMENA_VOICE_TTS_ENGINE"] == "qwen3"


@pytest.mark.asyncio
async def test_apply_requires_explicit_authorization(tmp_path):
    with pytest.raises(PermissionError, match="explicit"):
        await apply_voice_recommendation_transaction(
            {"updates": {"LUMENA_VOICE_TTS_ENGINE": "piper"}},
            read_values=lambda: {},
            write_values=lambda _: None,
            validate_values=_validator,
            status_provider=lambda: {},
            explicitly_authorized=False,
            state_path=tmp_path / "s",
            audit_path=tmp_path / "a",
        )


@pytest.mark.asyncio
async def test_observe_mode_refuses_even_explicit_mutation(tmp_path):
    with pytest.raises(PermissionError, match="observe_only"):
        await apply_voice_recommendation_transaction(
            {"updates": {"LUMENA_VOICE_TTS_ENGINE": "piper"}},
            read_values=lambda: {},
            write_values=lambda _: None,
            validate_values=_validator,
            status_provider=lambda: {},
            explicitly_authorized=True,
            management_mode="observe",
            state_path=tmp_path / "s",
            audit_path=tmp_path / "a",
        )


@pytest.mark.asyncio
async def test_safe_auto_can_apply_local_reversible_change_with_cooldown(tmp_path):
    values = {"LUMENA_VOICE_TTS_ENGINE": "qwen3"}
    state = tmp_path / "state.json"
    kwargs = dict(
        recommendation={"goal": "fast", "updates": {"LUMENA_VOICE_TTS_ENGINE": "piper"}},
        read_values=lambda: values,
        write_values=lambda u: values.update(u),
        validate_values=_validator,
        status_provider=lambda: {"running": False},
        management_mode="safe_auto",
        state_path=state,
        audit_path=tmp_path / "audit.jsonl",
    )
    result = await apply_voice_recommendation_transaction(**kwargs)
    assert result["ok"] is True
    values["LUMENA_VOICE_TTS_ENGINE"] = "qwen3"
    with pytest.raises(PermissionError, match="cooldown"):
        await apply_voice_recommendation_transaction(**kwargs)


@pytest.mark.asyncio
async def test_idempotent_change_does_not_restart(tmp_path):
    values = {"LUMENA_VOICE_TTS_ENGINE": "piper"}
    calls = []

    async def restart():
        calls.append(True)
        return True

    result = await apply_voice_recommendation_transaction(
        {"updates": {"LUMENA_VOICE_TTS_ENGINE": "piper"}},
        read_values=lambda: values,
        write_values=lambda u: values.update(u),
        validate_values=_validator,
        status_provider=lambda: {"running": True},
        restart_voice=restart,
        explicitly_authorized=True,
        state_path=tmp_path / "s",
        audit_path=tmp_path / "a",
    )
    assert result["state"] == "already_applied"
    assert calls == []


@pytest.mark.asyncio
async def test_safe_wait_observes_speaking_then_idle():
    states = iter(["thinking", "speaking", "speaking", "wake_listening"])
    current = {"runtime_state": "thinking"}

    def status():
        try:
            current["runtime_state"] = next(states)
        except StopIteration:
            pass
        return current

    assert await wait_until_voice_safe(status, timeout_s=1, poll_s=0.02)


@pytest.mark.asyncio
async def test_deferred_apply_returns_before_voice_finishes_then_restarts(tmp_path):
    values = {"LUMENA_VOICE_TTS_ENGINE": "qwen3"}
    state = {"running": True, "settings_pending": True, "runtime_state": "thinking"}
    restarted = asyncio.Event()

    async def restart():
        state["settings_pending"] = False
        restarted.set()
        return True

    result = await apply_voice_recommendation_transaction(
        {"goal": "fast", "updates": {"LUMENA_VOICE_TTS_ENGINE": "piper"}},
        read_values=lambda: values,
        write_values=lambda u: values.update(u),
        validate_values=_validator,
        status_provider=lambda: state,
        restart_voice=restart,
        defer_until_safe=True,
        explicitly_authorized=True,
        state_path=tmp_path / "state.json",
        audit_path=tmp_path / "audit.jsonl",
    )
    assert result["state"] == "scheduled"
    assert not restarted.is_set()
    state["runtime_state"] = "speaking"
    await asyncio.sleep(0.15)
    state["runtime_state"] = "wake_listening"
    await asyncio.wait_for(restarted.wait(), timeout=1)
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_deferred_transaction_blocks_concurrent_voice_change(tmp_path):
    values = {
        "LUMENA_VOICE_TTS_ENGINE": "qwen3",
        "LUMENA_STT_MODEL": "small",
    }
    status = {"running": True, "settings_pending": True, "runtime_state": "speaking"}
    restarted = asyncio.Event()

    async def restart():
        status["settings_pending"] = False
        restarted.set()
        return True

    first = await apply_voice_recommendation_transaction(
        {"goal": "fast", "updates": {"LUMENA_VOICE_TTS_ENGINE": "piper"}},
        read_values=lambda: values,
        write_values=lambda update: values.update(update),
        validate_values=_validator,
        status_provider=lambda: status,
        restart_voice=restart,
        defer_until_safe=True,
        explicitly_authorized=True,
        state_path=tmp_path / "state.json",
        audit_path=tmp_path / "audit.jsonl",
    )
    assert first["state"] == "scheduled"
    with pytest.raises(RuntimeError, match="voice_configuration_busy"):
        await apply_voice_recommendation_transaction(
            {"goal": "quality", "updates": {"LUMENA_STT_MODEL": "medium"}},
            read_values=lambda: values,
            write_values=lambda update: values.update(update),
            validate_values=_validator,
            status_provider=lambda: status,
            explicitly_authorized=True,
            state_path=tmp_path / "other-state.json",
            audit_path=tmp_path / "other-audit.jsonl",
        )
    status["runtime_state"] = "wake_listening"
    await asyncio.wait_for(restarted.wait(), timeout=2)
    await asyncio.sleep(0.05)
