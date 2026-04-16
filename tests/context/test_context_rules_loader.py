"""Tests unitaires pour src/context/rules_loader.py"""
import json
import pytest
import yaml
from pathlib import Path

from src.context.rules_loader import (
    ProjectRule,
    ProjectRules,
    RulesLoader,
)


# ─── ProjectRule ───────────────────────────────────────────────────────────

class TestProjectRule:
    def test_defaults(self):
        r = ProjectRule(name="indent", description="2 spaces", content="Use 2 spaces")
        assert r.priority == 5
        assert r.enabled is True

    def test_custom_values(self):
        r = ProjectRule(name="test", description="d", content="c", priority=1, enabled=False)
        assert r.priority == 1
        assert r.enabled is False


# ─── ProjectRules.to_prompt ────────────────────────────────────────────────

class TestProjectRulesToPrompt:
    def test_empty_rules(self):
        rules = ProjectRules(project_name="Test")
        prompt = rules.to_prompt()
        assert "Test" in prompt

    def test_with_style_guide(self):
        rules = ProjectRules(project_name="Test", style_guide="PEP 8")
        prompt = rules.to_prompt()
        assert "PEP 8" in prompt

    def test_with_conventions(self):
        rules = ProjectRules(project_name="P", conventions=["snake_case", "docstrings"])
        prompt = rules.to_prompt()
        assert "snake_case" in prompt
        assert "docstrings" in prompt

    def test_with_always(self):
        rules = ProjectRules(project_name="P", always=["Add type hints"])
        prompt = rules.to_prompt()
        assert "Add type hints" in prompt

    def test_with_do_not(self):
        rules = ProjectRules(project_name="P", do_not=["Use print()"])
        prompt = rules.to_prompt()
        assert "Use print()" in prompt

    def test_limits_conventions_display(self):
        rules = ProjectRules(project_name="P", conventions=[f"rule_{i}" for i in range(10)])
        prompt = rules.to_prompt()
        # Only 5 conventions should be shown
        assert "rule_0" in prompt
        assert "rule_4" in prompt
        assert "rule_5" not in prompt  # truncated at 5

    def test_context_truncated_to_500(self):
        long_ctx = "x" * 700
        rules = ProjectRules(project_name="P", context=long_ctx)
        prompt = rules.to_prompt()
        # The context in prompt should not exceed 500 chars of the context
        context_part = prompt.split("**Context**:")[-1] if "**Context**:" in prompt else ""
        assert len(context_part.strip()) <= 505  # 500 + small buffer


# ─── RulesLoader — no rules file ───────────────────────────────────────────

class TestRulesLoaderNoFile:
    def test_loads_default_rules_when_no_file(self, tmp_path):
        loader = RulesLoader(tmp_path)
        rules = loader.get_rules()
        assert isinstance(rules, ProjectRules)
        assert rules.project_name == tmp_path.name

    def test_get_rules_for_prompt_empty_when_no_rules(self, tmp_path):
        loader = RulesLoader(tmp_path)
        prompt = loader.get_rules_for_prompt()
        assert prompt == ""


# ─── RulesLoader — YAML rules file ─────────────────────────────────────────

class TestRulesLoaderYAML:
    def test_loads_yaml_rules(self, tmp_path):
        rules_data = {
            "project_name": "MyProject",
            "language": "python",
            "style_guide": "PEP 8",
            "conventions": ["snake_case"],
            "do_not": ["use print"],
            "always": ["add type hints"],
            "context": "A great project",
        }
        rules_file = tmp_path / ".lumena_rules.yaml"
        rules_file.write_text(yaml.dump(rules_data), encoding="utf-8")

        loader = RulesLoader(tmp_path)
        rules = loader.get_rules()
        assert rules.project_name == "MyProject"
        assert rules.language == "python"
        assert rules.style_guide == "PEP 8"
        assert "snake_case" in rules.conventions
        assert "use print" in rules.do_not
        assert "add type hints" in rules.always
        assert rules.context == "A great project"

    def test_rules_for_prompt_non_empty_with_content(self, tmp_path):
        rules_data = {"project_name": "P", "conventions": ["rule1"], "context": "some ctx"}
        rules_file = tmp_path / ".lumena_rules.yaml"
        rules_file.write_text(yaml.dump(rules_data), encoding="utf-8")
        loader = RulesLoader(tmp_path)
        prompt = loader.get_rules_for_prompt()
        assert len(prompt) > 0


# ─── RulesLoader — JSON rules file ─────────────────────────────────────────

class TestRulesLoaderJSON:
    def test_loads_json_rules(self, tmp_path):
        rules_data = {"project_name": "JsonProj", "language": "typescript"}
        rules_file = tmp_path / ".lumena_rules.json"
        rules_file.write_text(json.dumps(rules_data), encoding="utf-8")

        loader = RulesLoader(tmp_path)
        rules = loader.get_rules()
        assert rules.project_name == "JsonProj"
        assert rules.language == "typescript"


# ─── RulesLoader — plain text (.cursorrules) ───────────────────────────────

class TestRulesLoaderPlainText:
    def test_loads_cursorrules_as_context(self, tmp_path):
        cursor_file = tmp_path / ".cursorrules"
        cursor_file.write_text("Always use TypeScript.\nNever write any test.", encoding="utf-8")

        loader = RulesLoader(tmp_path)
        rules = loader.get_rules()
        assert "Always use TypeScript" in rules.context


# ─── RulesLoader.create_template ───────────────────────────────────────────

class TestRulesLoaderCreateTemplate:
    def test_creates_file(self, tmp_path):
        loader = RulesLoader(tmp_path)
        path = loader.create_template()
        assert path.exists()
        assert path.name == ".lumena_rules"

    def test_template_contains_project_name(self, tmp_path):
        loader = RulesLoader(tmp_path)
        path = loader.create_template()
        content = path.read_text()
        assert tmp_path.name in content


# ─── RulesLoader.reload ────────────────────────────────────────────────────

class TestRulesLoaderReload:
    def test_reload_picks_up_new_file(self, tmp_path):
        loader = RulesLoader(tmp_path)
        assert loader.rules.project_name == tmp_path.name

        # Now create a rules file and reload
        rules_data = {"project_name": "MutatedProject"}
        (tmp_path / ".lumena_rules.yaml").write_text(yaml.dump(rules_data), encoding="utf-8")
        loader.reload()
        assert loader.rules.project_name == "MutatedProject"
