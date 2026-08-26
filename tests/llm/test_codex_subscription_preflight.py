from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.llm.codex_subscription import (
    CODEX_PROTOCOL_FAMILY,
    CODEX_SURFACE_STAGES,
    CodexAPIFallback,
    CodexCLIState,
    CodexSurface,
    CodexSurfaceStage,
    OpenAIAccessMode,
    codex_cli_compatibility,
    discover_codex_cli_candidates,
    load_codex_subscription_settings,
    probe_codex_cli,
    probe_codex_cli_async,
)


def _completed(command, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def _fake_executable(tmp_path: Path) -> Path:
    executable = tmp_path / "codex.exe"
    executable.write_bytes(b"fake")
    return executable


def _ready_runner(command, **kwargs):
    assert kwargs["shell"] is False
    if command[1:] == ["--version"]:
        return _completed(command, stdout="codex-cli 1.2.3\n")
    if command[1:] == ["app-server", "--help"]:
        return _completed(command, stdout="Codex app-server\n")
    assert command[1:4] == ["app-server", "generate-json-schema", "--out"]
    output_dir = Path(command[4])
    (output_dir / "protocol.json").write_text(
        json.dumps({"title": "CodexAppServer"}), encoding="utf-8"
    )
    return _completed(command)


def test_settings_are_off_and_fail_closed_by_default():
    settings = load_codex_subscription_settings({})
    assert settings.access_mode is OpenAIAccessMode.API
    assert settings.enabled is False
    assert settings.surfaces == frozenset({CodexSurface.CODEAGENT})
    assert settings.api_fallback is CodexAPIFallback.NEVER
    assert settings.api_rescue_enabled is True
    assert settings.rescue_configured is False
    assert settings.surface_requested(CodexSurface.CODEAGENT) is False


def test_settings_parse_explicit_mode_surfaces_and_ask_fallback():
    settings = load_codex_subscription_settings(
        {
            "LUMENA_OPENAI_ACCESS_MODE": "chatgpt_codex",
            "LUMENA_CODEX_CLI_PATH": " C:/tools/codex.exe ",
            "LUMENA_CODEX_DEFAULT_MODEL": " gpt-example ",
            "LUMENA_CODEX_SURFACES": "codeagent, chat collaboration;missions",
            "LUMENA_CODEX_API_FALLBACK": "ask",
        }
    )
    assert settings.enabled is True
    assert settings.cli_path == "C:/tools/codex.exe"
    assert settings.default_model == "gpt-example"
    assert settings.surfaces == frozenset(
        {
            CodexSurface.CODEAGENT,
            CodexSurface.CHAT,
            CodexSurface.COLLABORATION,
            CodexSurface.MISSIONS,
        }
    )
    assert settings.api_fallback is CodexAPIFallback.ASK
    assert settings.api_rescue_enabled is True
    assert settings.rescue_configured is True
    assert settings.surface_requested(CodexSurface.CHAT) is True


def test_invalid_mode_and_fallback_never_enable_or_spend():
    settings = load_codex_subscription_settings(
        {
            "LUMENA_OPENAI_ACCESS_MODE": "unknown",
            "LUMENA_CODEX_API_FALLBACK": "always",
            "LUMENA_CODEX_SURFACES": "unknown",
        }
    )
    assert settings.access_mode is OpenAIAccessMode.API
    assert settings.api_fallback is CodexAPIFallback.NEVER
    assert settings.surfaces == frozenset()


def test_rescue_is_independent_from_primary_mode_but_explicitly_disableable():
    configured = load_codex_subscription_settings(
        {
            "LUMENA_OPENAI_ACCESS_MODE": "api",
            "LUMENA_CODEX_DEFAULT_MODEL": "account-model",
            "LUMENA_CODEX_API_RESCUE": "1",
        }
    )
    disabled = load_codex_subscription_settings(
        {
            "LUMENA_OPENAI_ACCESS_MODE": "api",
            "LUMENA_CODEX_DEFAULT_MODEL": "account-model",
            "LUMENA_CODEX_API_RESCUE": "0",
        }
    )

    assert configured.enabled is False
    assert configured.rescue_configured is True
    assert disabled.rescue_configured is False


def test_surface_matrix_keeps_agent_and_missions_behind_tool_bridge():
    assert CODEX_SURFACE_STAGES[CodexSurface.CODEAGENT] is CodexSurfaceStage.PILOT
    assert (
        CODEX_SURFACE_STAGES[CodexSurface.AGENT]
        is CodexSurfaceStage.REQUIRES_TOOL_BRIDGE
    )
    assert (
        CODEX_SURFACE_STAGES[CodexSurface.MISSIONS]
        is CodexSurfaceStage.REQUIRES_TOOL_BRIDGE
    )


def test_cli_compatibility_is_capability_based_not_a_fabricated_version_floor():
    contract = codex_cli_compatibility(observed_version="1.2.3", ready=True)
    assert contract["policy"] == "capability_probe"
    assert contract["numeric_minimum"] is None
    assert contract["compatible"] is True
    assert contract["observed_version"] == "1.2.3"
    assert "codex app-server generate-json-schema" in contract["required_capabilities"]


def test_discovery_prefers_configured_path_and_deduplicates(tmp_path):
    executable = _fake_executable(tmp_path)
    candidates = discover_codex_cli_candidates(
        str(executable),
        environ={},
        which=lambda _name: str(executable),
        platform="win32",
    )
    assert candidates == (
        type(candidates[0])(path=str(executable.resolve()), source="configured"),
    )


def test_discovery_is_bounded_when_nothing_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    candidates = discover_codex_cli_candidates(
        None,
        environ={},
        which=lambda _name: None,
        platform="linux",
    )
    assert candidates == ()


def test_discovery_finds_codex_desktop_user_runtime_on_windows(tmp_path):
    runtime = tmp_path / "OpenAI" / "Codex" / "bin" / "build-hash" / "codex.exe"
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"fake")
    candidates = discover_codex_cli_candidates(
        None,
        environ={"LOCALAPPDATA": str(tmp_path)},
        which=lambda _name: None,
        platform="win32",
    )
    assert any(
        item.path == str(runtime.resolve()) and item.source == "codex_desktop"
        for item in candidates
    )


def test_probe_reports_missing_configured_binary(tmp_path):
    result = probe_codex_cli(
        str(tmp_path / "missing-codex.exe"),
        environ={},
        which=lambda _name: None,
        platform="win32",
    )
    assert result.state is CodexCLIState.NOT_FOUND
    assert result.ready is False
    assert result.attempts[0].source == "configured"


def test_probe_classifies_permission_denied(tmp_path):
    executable = _fake_executable(tmp_path)

    def denied(_command, **_kwargs):
        raise PermissionError(13, "Access denied")

    result = probe_codex_cli(
        str(executable),
        environ={},
        which=lambda _name: None,
        platform="win32",
        runner=denied,
    )
    assert result.state is CodexCLIState.INACCESSIBLE
    assert "denied" in result.detail.lower()


def test_probe_classifies_version_timeout(tmp_path):
    executable = _fake_executable(tmp_path)

    def timed_out(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, 1)

    result = probe_codex_cli(
        str(executable),
        environ={},
        which=lambda _name: None,
        platform="win32",
        runner=timed_out,
    )
    assert result.state is CodexCLIState.TIMED_OUT


def test_probe_classifies_cli_failure(tmp_path):
    executable = _fake_executable(tmp_path)

    def broken(command, **_kwargs):
        return _completed(command, returncode=1, stderr="fatal startup error")

    result = probe_codex_cli(
        str(executable),
        environ={},
        which=lambda _name: None,
        platform="win32",
        runner=broken,
    )
    assert result.state is CodexCLIState.BROKEN
    assert "fatal startup error" in result.detail


def test_probe_classifies_missing_app_server(tmp_path):
    executable = _fake_executable(tmp_path)

    def no_app_server(command, **_kwargs):
        if command[1:] == ["--version"]:
            return _completed(command, stdout="codex-cli 1.0.0")
        return _completed(command, returncode=2, stderr="unknown command app-server")

    result = probe_codex_cli(
        str(executable),
        environ={},
        which=lambda _name: None,
        platform="win32",
        runner=no_app_server,
    )
    assert result.state is CodexCLIState.APP_SERVER_UNSUPPORTED
    assert result.version == "1.0.0"


def test_probe_rejects_invalid_generated_schema(tmp_path):
    executable = _fake_executable(tmp_path)

    def invalid_schema(command, **kwargs):
        if command[1:] == ["--version"]:
            return _completed(command, stdout="codex-cli 1.1.0")
        if command[1:] == ["app-server", "--help"]:
            return _completed(command)
        Path(command[4], "broken.json").write_text("{", encoding="utf-8")
        return _completed(command)

    result = probe_codex_cli(
        str(executable),
        environ={},
        which=lambda _name: None,
        platform="win32",
        runner=invalid_schema,
    )
    assert result.state is CodexCLIState.PROTOCOL_INCOMPATIBLE


def test_probe_ready_validates_version_command_and_schema(tmp_path):
    executable = _fake_executable(tmp_path)
    result = probe_codex_cli(
        str(executable),
        environ={},
        which=lambda _name: None,
        platform="win32",
        runner=_ready_runner,
    )
    assert result.state is CodexCLIState.READY
    assert result.ready is True
    assert result.version == "1.2.3"
    assert result.protocol_family == CODEX_PROTOCOL_FAMILY
    assert result.schema_files == 1
    payload = result.to_dict()
    assert payload["ready"] is True
    assert payload["state"] == "ready"
    assert payload["attempts"][0]["state"] == "ready"


def test_probe_continues_after_bad_configured_path(tmp_path):
    executable = _fake_executable(tmp_path)
    result = probe_codex_cli(
        str(tmp_path / "stale.exe"),
        environ={},
        which=lambda _name: str(executable),
        platform="win32",
        runner=_ready_runner,
    )
    assert result.ready is True
    assert [attempt.state for attempt in result.attempts] == [
        CodexCLIState.NOT_FOUND,
        CodexCLIState.READY,
    ]


def test_probe_redacts_secret_like_error_text(tmp_path):
    executable = _fake_executable(tmp_path)

    def secret_failure(command, **_kwargs):
        return _completed(
            command,
            returncode=1,
            stderr="Authorization: Bearer sess_abcdefghijklmnopqrstuvwxyz",
        )

    result = probe_codex_cli(
        str(executable),
        environ={},
        which=lambda _name: None,
        platform="win32",
        runner=secret_failure,
    )
    assert "sess_" not in result.detail
    assert "[REDACTED]" in result.detail


@pytest.mark.asyncio
async def test_async_probe_runs_same_contract(tmp_path):
    executable = _fake_executable(tmp_path)
    result = await probe_codex_cli_async(
        str(executable),
        environ={},
        which=lambda _name: None,
        platform="win32",
        runner=_ready_runner,
    )
    assert result.ready is True
