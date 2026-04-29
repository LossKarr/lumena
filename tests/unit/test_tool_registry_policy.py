"""Tests unitaires de la policy de délégation forcée dans ToolRegistry."""
import pytest
from src.reasoning.tool_registry import ToolRegistry


@pytest.fixture
def registry():
    return ToolRegistry()


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch):
    monkeypatch.setenv("LUMENA_STRICT_CODE_DELEGATION", "enforce")
    monkeypatch.delenv("LUMENA_REACT_ALLOW_PROJECT_SHELL", raising=False)


@pytest.fixture
def fake_project(monkeypatch):
    """Injecte un projet fictif dans find_project_by_path."""
    def fake_find(path):
        p = str(path).replace("\\", "/")
        if "demo-project" in p:
            return {"slug": "demo-project", "path": "/fake/demo-project"}
        return None
    monkeypatch.setattr(
        "src.reasoning.tool_registry.find_project_by_path",
        fake_find,
        raising=False,
    )
    # Le module importe à la demande via `from ..utils.project_registry import find_project_by_path`
    # donc on patche plutôt le symbole source :
    monkeypatch.setattr(
        "src.utils.project_registry.find_project_by_path",
        fake_find,
    )
    return fake_find


# ── Tests ────────────────────────────────────────────────────────────

def test_non_react_caller_allowed(registry, fake_project):
    from src.reasoning.caller_context import CODEAGENT
    obs = registry._policy_check("write_file", {"path": "/fake/demo-project/app.py"}, CODEAGENT)
    assert obs is None


def test_unknown_caller_allowed(registry, fake_project):
    from src.reasoning.caller_context import UNKNOWN
    obs = registry._policy_check("write_file", {"path": "/fake/demo-project/app.py"}, UNKNOWN)
    assert obs is None


def test_read_only_tool_allowed(registry, fake_project):
    from src.reasoning.caller_context import REACT
    obs = registry._policy_check("read_file", {"path": "/fake/demo-project/app.py"}, REACT)
    assert obs is None


def test_react_mutation_outside_project_allowed(registry, fake_project):
    from src.reasoning.caller_context import REACT
    obs = registry._policy_check("write_file", {"path": "/tmp/notes.py"}, REACT)
    assert obs is None


def test_react_mutation_code_in_project_blocked(registry, fake_project):
    from src.reasoning.caller_context import REACT
    obs = registry._policy_check("write_file", {"path": "/fake/demo-project/app.py"}, REACT)
    assert obs is not None
    assert obs.success is False
    assert "CodeAgent" in obs.content or "delegate" in obs.content.lower()


def test_react_mutation_config_in_project_blocked(registry, fake_project):
    from src.reasoning.caller_context import REACT
    obs = registry._policy_check("write_file", {"path": "/fake/demo-project/package.json"}, REACT)
    assert obs is not None
    assert obs.success is False


def test_react_mutation_dockerfile_blocked(registry, fake_project):
    from src.reasoning.caller_context import REACT
    obs = registry._policy_check("edit_file", {"file_path": "/fake/demo-project/Dockerfile"}, REACT)
    assert obs is not None


def test_react_mutation_markdown_in_project_allowed(registry, fake_project):
    """ReAct peut écrire un README.md dans un projet."""
    from src.reasoning.caller_context import REACT
    obs = registry._policy_check("write_file", {"path": "/fake/demo-project/README.md"}, REACT)
    assert obs is None


def test_react_mutation_pdf_in_project_allowed(registry, fake_project):
    """CV.pdf dans un projet → ReAct autorisé (binaire)."""
    from src.reasoning.caller_context import REACT
    obs = registry._policy_check("write_file", {"path": "/fake/demo-project/CV.pdf"}, REACT)
    assert obs is None


def test_react_mutation_svg_in_project_allowed(registry, fake_project):
    from src.reasoning.caller_context import REACT
    obs = registry._policy_check("write_file", {"path": "/fake/demo-project/logo.svg"}, REACT)
    assert obs is None


def test_run_command_in_project_blocked(registry, fake_project):
    from src.reasoning.caller_context import REACT
    obs = registry._policy_check(
        "run_command",
        {"command": "rm -rf src", "cwd": "/fake/demo-project"},
        REACT,
    )
    assert obs is not None


def test_run_command_in_project_allowed_when_shell_flag_enabled(registry, fake_project, monkeypatch):
    monkeypatch.setenv("LUMENA_REACT_ALLOW_PROJECT_SHELL", "1")
    from src.reasoning.caller_context import REACT
    obs = registry._policy_check(
        "run_command",
        {"command": "node server.js", "cwd": "/fake/demo-project"},
        REACT,
    )
    assert obs is None


def test_run_shell_in_project_allowed_when_shell_flag_enabled(registry, fake_project, monkeypatch):
    monkeypatch.setenv("LUMENA_REACT_ALLOW_PROJECT_SHELL", "true")
    from src.reasoning.caller_context import REACT
    obs = registry._policy_check(
        "run_shell",
        {"command": "npm start", "cwd": "/fake/demo-project"},
        REACT,
    )
    assert obs is None


def test_run_command_curl_outside_allowed(registry, fake_project):
    from src.reasoning.caller_context import REACT
    obs = registry._policy_check(
        "run_command",
        {"command": "curl https://example.com"},
        REACT,
    )
    assert obs is None


def test_no_path_extractable_allowed(registry, fake_project):
    from src.reasoning.caller_context import REACT
    obs = registry._policy_check("write_file", {"content": "hello"}, REACT)
    assert obs is None


def test_off_mode_disables_policy(registry, fake_project, monkeypatch):
    monkeypatch.setenv("LUMENA_STRICT_CODE_DELEGATION", "off")
    from src.reasoning.caller_context import REACT
    obs = registry._policy_check("write_file", {"path": "/fake/demo-project/app.py"}, REACT)
    assert obs is None


def test_warn_mode_allows_but_logs(registry, fake_project, monkeypatch):
    monkeypatch.setenv("LUMENA_STRICT_CODE_DELEGATION", "warn")
    from src.reasoning.caller_context import REACT
    obs = registry._policy_check("write_file", {"path": "/fake/demo-project/app.py"}, REACT)
    assert obs is None  # warn ne bloque pas


def test_category_contract_requires_workspace_for_delegate_task(registry, monkeypatch):
    from src.reasoning.caller_context import REACT

    monkeypatch.setattr(
        "src.reasoning.tool_registry.get_current_runtime_context",
        lambda: None,
    )
    obs = registry._category_contract_check(
        "delegate_task",
        {"description": "Corrige le projet existant avec plusieurs changements importants"},
        REACT,
    )
    assert obs is not None
    assert obs.success is False
    assert "workspace_path requis" in obs.content


def test_category_contract_accepts_runtime_workspace_for_delegate_task(registry, monkeypatch, tmp_path):
    from src.reasoning.caller_context import REACT

    runtime_ctx = type(
        "RuntimeCtx",
        (),
        {"workspace_path": str(tmp_path), "resolved_workspace": str(tmp_path)},
    )()
    monkeypatch.setattr(
        "src.reasoning.tool_registry.get_current_runtime_context",
        lambda: runtime_ctx,
    )
    monkeypatch.setattr(
        "src.reasoning.react_config.get_current_runtime_context",
        lambda: runtime_ctx,
        raising=False,
    )
    registry.ide_context = registry._normalize_ide_context(
        {"workspace_path": str(tmp_path)}
    )
    obs = registry._category_contract_check(
        "delegate_task",
        {"description": "Corrige le projet existant avec plusieurs changements importants"},
        REACT,
    )
    assert obs is None


def test_category_contract_accepts_absolute_file_path_parent(registry, monkeypatch, tmp_path):
    from src.reasoning.caller_context import REACT

    target = tmp_path / "notes.txt"
    monkeypatch.setattr(
        "src.reasoning.tool_registry.get_current_runtime_context",
        lambda: None,
    )
    obs = registry._category_contract_check(
        "write_file",
        {"path": str(target)},
        REACT,
    )
    assert obs is None
