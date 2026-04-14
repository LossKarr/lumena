"""Tests unitaires pour src/skills/tools.py"""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from src.skills.tools import (
    Skill,
    list_skills,
    get_skill_info,
    create_skill,
    SKILL_TOOLS,
)


class TestSkill:
    def test_skill_creation(self, tmp_path):
        skill = Skill(
            name="test_skill",
            display_name="Test Skill",
            description="A test skill for unit tests",
            instructions="Do the tests",
            path=tmp_path,
        )
        assert skill.name == "test_skill"
        assert skill.display_name == "Test Skill"
        assert skill.path == tmp_path
        assert isinstance(skill.scripts, list)
        assert isinstance(skill.references, list)
        assert isinstance(skill.metadata, dict)

    def test_skill_default_lists(self, tmp_path):
        skill = Skill(
            name="s", display_name="S", description="d",
            instructions="i", path=tmp_path
        )
        assert skill.keywords == []
        assert skill.apply_to == []
        assert skill.assets == []


class TestSkillTools:
    def test_skill_tools_is_dict(self):
        assert isinstance(SKILL_TOOLS, dict)

    def test_skill_tools_keys_are_strings(self):
        for k in SKILL_TOOLS:
            assert isinstance(k, str)


class TestListSkills:
    def test_returns_string(self):
        result = list_skills()
        assert isinstance(result, str)

    def test_returns_something(self):
        result = list_skills()
        assert len(result) > 0


class TestGetSkillInfo:
    def test_nonexistent_skill_returns_message(self):
        result = get_skill_info("skill_that_does_not_exist_xyz123")
        assert isinstance(result, str)
        # Should mention not found or similar
        assert len(result) > 0


class TestCreateSkill:
    def test_create_skill_returns_string(self, tmp_path):
        import src.skills.tools as st
        original_dir = getattr(st, "_SKILLS_DIR", None)
        with patch.object(st, "_SKILLS_DIR", tmp_path / "skills", create=True):
            result = create_skill(name="my_new_test_skill", description="For testing")
            assert isinstance(result, str)
