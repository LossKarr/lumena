from pathlib import Path

from src.agents.sub_agent import CodeAgent, _run_command_looks_mutating


def _agent_with_workspace(repo: Path, workspace: Path) -> CodeAgent:
    agent = CodeAgent.__new__(CodeAgent)
    agent._task_workspace_root = workspace
    agent._project_root = lambda: repo
    return agent


def test_workspace_resolve_strips_project_name_prefix(tmp_path: Path):
    repo = tmp_path / "lumena"
    workspace = repo / "workspace" / "mission-control-sim"
    workspace.mkdir(parents=True)
    agent = _agent_with_workspace(repo, workspace)

    assert agent._resolve_path("mission-control-sim/index.html") == workspace / "index.html"


def test_workspace_resolve_strips_repeated_workspace_prefix(tmp_path: Path):
    repo = tmp_path / "lumena"
    workspace = repo / "workspace" / "mission-control-sim"
    workspace.mkdir(parents=True)
    agent = _agent_with_workspace(repo, workspace)

    assert agent._resolve_path("workspace/index.html") == workspace / "index.html"
    assert agent._resolve_path("workspace/mission-control-sim/index.html") == workspace / "index.html"


def test_mutating_run_command_resets_read_loop_signal():
    assert _run_command_looks_mutating(
        'Move-Item -LiteralPath "workspace/mission-control-sim/index.html" -Destination "index.html"',
        "Exit:0",
    )


def test_failed_or_readonly_run_command_is_not_mutating():
    assert not _run_command_looks_mutating("node --check js/main.js", "Exit:0")
    assert not _run_command_looks_mutating("rmdir workspace /s /q", "Executable non autorise")
