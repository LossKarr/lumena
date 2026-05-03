"""
Tests de l'exception micro-fix dans la policy de délégation ReAct.

Valide :
  - _estimate_change_lines : estimation correcte par outil
  - _is_react_micro_fix : critères stricts (outil, taille, fichier config exclus)
  - _policy_check intégration :
      - micro-fix Python / JS / CSS / HTML autorisé
      - dépassement de budget bloqué
      - write_file toujours bloqué (pas un outil micro-fix)
      - fichiers build/config toujours bloqués
      - grosse tâche toujours déléguée au CodeAgent
      - CodeAgent jamais bloqué
      - aucune régression sur les tests policy existants
"""
from __future__ import annotations

import pytest
from src.reasoning.tool_registry import ToolRegistry, _is_react_micro_fix, _estimate_change_lines


@pytest.fixture
def registry():
    return ToolRegistry()


@pytest.fixture(autouse=True)
def _enforce_mode(monkeypatch):
    monkeypatch.setenv("LUMENA_STRICT_CODE_DELEGATION", "enforce")
    monkeypatch.setenv("LUMENA_REACT_ALLOW_PROJECT_SHELL", "1")


@pytest.fixture
def fake_project(monkeypatch):
    def _find(path):
        p = str(path).replace("\\", "/")
        if "demo-project" in p:
            return {"slug": "demo-project", "path": "/fake/demo-project"}
        return None
    monkeypatch.setattr("src.reasoning.tool_registry.find_project_by_path", _find, raising=False)
    monkeypatch.setattr("src.utils.project_registry.find_project_by_path", _find)
    return _find


# ─────────────────────────────────────────────────────────────────────────────
# _estimate_change_lines
# ─────────────────────────────────────────────────────────────────────────────

class TestEstimateChangeLines:
    def test_str_replace_small(self):
        result = _estimate_change_lines("str_replace", {"old_str": "foo\nbar", "new_str": "baz"})
        assert result == 2  # max(2 lignes old, 1 ligne new)

    def test_str_replace_single_line(self):
        result = _estimate_change_lines("str_replace", {"old_str": "x = 1", "new_str": "x = 2"})
        assert result == 1

    def test_str_replace_empty_new(self):
        result = _estimate_change_lines("str_replace", {"old_str": "a\nb\nc", "new_str": ""})
        assert result == 3

    def test_edit_file_small(self):
        content = "\n".join(f"line{i}" for i in range(10))
        result = _estimate_change_lines("edit_file", {"content": content})
        assert result == 10

    def test_edit_file_large(self):
        content = "\n".join(f"line{i} = {i}" for i in range(60))
        result = _estimate_change_lines("edit_file", {"content": content})
        assert result == 60

    def test_apply_patch_small(self):
        patch = "+++ a/file.py\n--- b/file.py\n+added_a\n+added_b\n-removed_x\n"
        result = _estimate_change_lines("apply_patch", {"patch": patch})
        assert result == 2  # max(2 added, 1 removed)

    def test_edit_by_lines_small(self):
        changes = [
            {"line": 5, "content": "x = 1\n"},
            {"line": 6, "content": "y = 2\n"},
        ]
        result = _estimate_change_lines("edit_by_lines", {"changes": changes})
        assert result == 2

    def test_unknown_tool_returns_none(self):
        assert _estimate_change_lines("write_file", {}) is None
        assert _estimate_change_lines("delete_file", {}) is None

    def test_none_args_returns_none(self):
        assert _estimate_change_lines("str_replace", None) is None


# ─────────────────────────────────────────────────────────────────────────────
# _is_react_micro_fix — critères
# ─────────────────────────────────────────────────────────────────────────────

class TestIsReactMicroFix:
    def test_str_replace_python_small(self):
        assert _is_react_micro_fix(
            "str_replace",
            {"old_str": "import foo\n", "new_str": "import foo\nimport sys\n"},
            "/project/main.py",
        )

    def test_edit_file_css_small(self):
        assert _is_react_micro_fix(
            "edit_file",
            {"content": "body { color: blue; }\n.header { font-size: 14px; }\n"},
            "/project/style.css",
        )

    def test_edit_file_js_small(self):
        assert _is_react_micro_fix(
            "edit_file",
            {"content": "const x = 1;\n"},
            "/project/app.js",
        )

    def test_edit_by_lines_html_small(self):
        assert _is_react_micro_fix(
            "edit_by_lines",
            {"changes": [{"line": 10, "content": "<h1>Titre</h1>\n"}]},
            "/project/index.html",
        )

    def test_apply_patch_python_small(self):
        patch = "+++ a/app.py\n--- b/app.py\n" + "\n".join(f"+line{i}" for i in range(5)) + "\n"
        assert _is_react_micro_fix("apply_patch", {"patch": patch}, "/project/app.py")

    def test_str_replace_too_large(self):
        old = "\n".join(f"line{i}" for i in range(35))
        assert not _is_react_micro_fix(
            "str_replace", {"old_str": old, "new_str": old}, "/project/app.py"
        )

    def test_edit_file_too_large(self):
        large = "\n".join(f"x{i} = {i}" for i in range(50))
        assert not _is_react_micro_fix(
            "edit_file", {"content": large}, "/project/app.py"
        )

    def test_write_file_not_micro_fix(self):
        assert not _is_react_micro_fix(
            "write_file", {"content": "x = 1\n"}, "/project/app.py"
        )

    def test_delete_file_not_micro_fix(self):
        assert not _is_react_micro_fix(
            "delete_file", {}, "/project/app.py"
        )

    def test_dockerfile_excluded(self):
        assert not _is_react_micro_fix(
            "str_replace",
            {"old_str": "FROM python:3.11\n", "new_str": "FROM python:3.12\n"},
            "/project/Dockerfile",
        )

    def test_package_json_excluded(self):
        assert not _is_react_micro_fix(
            "str_replace",
            {"old_str": '"version": "1.0.0"', "new_str": '"version": "1.0.1"'},
            "/project/package.json",
        )

    def test_pyproject_toml_excluded(self):
        assert not _is_react_micro_fix(
            "str_replace",
            {"old_str": 'version = "1.0.0"', "new_str": 'version = "1.0.1"'},
            "/project/pyproject.toml",
        )

    def test_requirements_txt_excluded(self):
        assert not _is_react_micro_fix(
            "str_replace",
            {"old_str": "requests==2.28.0\n", "new_str": "requests==2.31.0\n"},
            "/project/requirements.txt",
        )

    def test_exactly_at_budget_limit(self):
        content = "\n".join(f"line{i}" for i in range(30))
        assert _is_react_micro_fix("edit_file", {"content": content}, "/project/app.py")

    def test_one_over_budget_blocked(self):
        content = "\n".join(f"line{i}" for i in range(31))
        assert not _is_react_micro_fix("edit_file", {"content": content}, "/project/app.py")


# ─────────────────────────────────────────────────────────────────────────────
# _policy_check — intégration
# ─────────────────────────────────────────────────────────────────────────────

class TestPolicyMicroFixIntegration:
    def test_str_replace_python_in_project_allowed(self, registry, fake_project):
        from src.reasoning.caller_context import REACT
        obs = registry._policy_check(
            "str_replace",
            {"file_path": "/fake/demo-project/app.py", "old_str": "import foo\n", "new_str": "import bar\n"},
            REACT,
        )
        assert obs is None

    def test_edit_file_css_in_project_allowed(self, registry, fake_project):
        from src.reasoning.caller_context import REACT
        obs = registry._policy_check(
            "edit_file",
            {"file_path": "/fake/demo-project/style.css", "content": "body { color: blue; }\n"},
            REACT,
        )
        assert obs is None

    def test_edit_file_html_in_project_allowed(self, registry, fake_project):
        from src.reasoning.caller_context import REACT
        obs = registry._policy_check(
            "edit_file",
            {"file_path": "/fake/demo-project/index.html", "content": "<h1>Hello</h1>\n"},
            REACT,
        )
        assert obs is None

    def test_str_replace_js_in_project_allowed(self, registry, fake_project):
        from src.reasoning.caller_context import REACT
        obs = registry._policy_check(
            "str_replace",
            {"file_path": "/fake/demo-project/app.js", "old_str": "var x = 1\n", "new_str": "const x = 1\n"},
            REACT,
        )
        assert obs is None

    def test_large_edit_file_python_blocked(self, registry, fake_project):
        """Dépassement du budget → délégation CodeAgent."""
        from src.reasoning.caller_context import REACT
        large = "\n".join(f"x{i} = {i}" for i in range(50))
        obs = registry._policy_check(
            "edit_file",
            {"file_path": "/fake/demo-project/app.py", "content": large},
            REACT,
        )
        assert obs is not None
        assert obs.success is False

    def test_write_file_python_still_blocked(self, registry, fake_project):
        """write_file n'est pas un outil micro-fix — toujours bloqué."""
        from src.reasoning.caller_context import REACT
        obs = registry._policy_check(
            "write_file",
            {"path": "/fake/demo-project/app.py", "content": "x = 1\n"},
            REACT,
        )
        assert obs is not None
        assert obs.success is False

    def test_dockerfile_str_replace_blocked(self, registry, fake_project):
        """Dockerfile exclu même avec contenu court."""
        from src.reasoning.caller_context import REACT
        obs = registry._policy_check(
            "str_replace",
            {"file_path": "/fake/demo-project/Dockerfile", "old_str": "FROM python:3.11\n", "new_str": "FROM python:3.12\n"},
            REACT,
        )
        assert obs is not None
        assert obs.success is False

    def test_package_json_str_replace_blocked(self, registry, fake_project):
        from src.reasoning.caller_context import REACT
        obs = registry._policy_check(
            "str_replace",
            {"file_path": "/fake/demo-project/package.json", "old_str": '"version": "1.0.0"', "new_str": '"version": "1.0.1"'},
            REACT,
        )
        assert obs is not None
        assert obs.success is False

    def test_codeagent_always_allowed(self, registry, fake_project):
        """CodeAgent n'est jamais bloqué par la policy."""
        from src.reasoning.caller_context import CODEAGENT
        obs = registry._policy_check(
            "str_replace",
            {"file_path": "/fake/demo-project/app.py", "old_str": "x = 1", "new_str": "x = 2"},
            CODEAGENT,
        )
        assert obs is None

    def test_large_str_replace_in_project_blocked(self, registry, fake_project):
        """Gros chantier (>30 lignes) → délégation forcée."""
        from src.reasoning.caller_context import REACT
        big_old = "\n".join(f"func_{i}()" for i in range(40))
        big_new = "\n".join(f"new_func_{i}()" for i in range(40))
        obs = registry._policy_check(
            "str_replace",
            {"file_path": "/fake/demo-project/service.py", "old_str": big_old, "new_str": big_new},
            REACT,
        )
        assert obs is not None
        assert obs.success is False

    def test_out_of_project_always_allowed(self, registry, fake_project):
        """Hors projet connu → ReAct peut toujours éditer."""
        from src.reasoning.caller_context import REACT
        large = "\n".join(f"x{i} = {i}" for i in range(200))
        obs = registry._policy_check(
            "edit_file",
            {"file_path": "/tmp/notes.py", "content": large},
            REACT,
        )
        assert obs is None
