from datetime import datetime
from pathlib import Path

from src.runtime.workspace_policy import resolve_workspace_for_request


def test_workspace_policy_strict_default_uses_dated_workspace(tmp_path: Path):
    result = resolve_workspace_for_request(
        workspace_policy="strict_default",
        requested_workspace=str(tmp_path / "external"),
        default_workspace=str(tmp_path / "workspace"),
        active_file_path=None,
        open_files=[],
    )

    today = datetime.now().strftime("%Y-%m-%d")
    assert result.workspace_policy == "strict_default"
    assert result.resolved_workspace.endswith(today)
    assert Path(result.resolved_workspace).exists()
    assert result.resolution_reason == "strict_default_forced"


def test_workspace_policy_explicit_keeps_requested_workspace(tmp_path: Path):
    explicit = tmp_path / "explicit_workspace"
    explicit.mkdir(parents=True, exist_ok=True)

    result = resolve_workspace_for_request(
        workspace_policy="explicit",
        requested_workspace=str(explicit),
        default_workspace=str(tmp_path / "workspace"),
        active_file_path=None,
        open_files=[],
    )

    assert result.resolved_workspace == str(explicit.resolve())
    assert result.used_fallback is False
    assert result.resolution_reason == "explicit_valid"


def test_workspace_policy_default_falls_back_to_dated_default(tmp_path: Path):
    result = resolve_workspace_for_request(
        workspace_policy="default",
        requested_workspace=None,
        default_workspace=str(tmp_path / "workspace"),
        active_file_path=None,
        open_files=[],
    )

    today = datetime.now().strftime("%Y-%m-%d")
    assert result.resolved_workspace.endswith(today)
    assert result.used_fallback is True
    assert result.resolution_reason == "default_fallback_default_workspace"
