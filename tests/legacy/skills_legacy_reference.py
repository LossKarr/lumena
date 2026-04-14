"""
Tests: Skills
Unit tests for Lumena skills system.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSkillsExist:
    """Presence checks for skills."""

    def test_skills_directory_exists(self):
        """Ensure skills directory exists."""
        skills_dir = Path(__file__).parent.parent / "skills"
        assert skills_dir.exists()
        assert skills_dir.is_dir()

    def test_required_skills_exist(self):
        """Ensure required skills are present."""
        skills_dir = Path(__file__).parent.parent / "skills"

        required_skills = [
            "coding_agent.md",
            "github.md",
            "project_analyzer.md",
            "skill-creator/SKILL.md",
        ]

        for skill_name in required_skills:
            skill_path = skills_dir / skill_name
            assert skill_path.exists(), f"Missing skill: {skill_name}"

    def test_skill_count(self):
        """Ensure there are enough skills."""
        skills_dir = Path(__file__).parent.parent / "skills"
        skills = list(skills_dir.glob("*.md"))
        assert len(skills) >= 7


class TestSkillFormat:
    """Skill file format checks."""

    def test_skill_has_frontmatter(self):
        """Ensure each skill has frontmatter."""
        skills_dir = Path(__file__).parent.parent / "skills"

        for skill_path in skills_dir.glob("*.md"):
            content = skill_path.read_text(encoding="utf-8")
            assert content.startswith("---"), f"{skill_path.name} has no frontmatter"

    def test_skill_frontmatter_has_name(self):
        """Ensure frontmatter includes a name field."""
        import yaml

        skills_dir = Path(__file__).parent.parent / "skills"

        for skill_path in skills_dir.glob("*.md"):
            content = skill_path.read_text(encoding="utf-8")
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = yaml.safe_load(parts[1])
                    assert "name" in frontmatter, f"{skill_path.name} has no name"


@pytest.mark.asyncio
class TestSkillReload:
    """Hot-reload checks for skills."""

    async def test_reload_skills_tool(self):
        """Validate reload_skills tool execution."""
        from src.tools.tool_system import LumenaToolSystem, ToolCall

        ts = LumenaToolSystem()
        result = await ts.execute_tool(ToolCall(name="reload_skills", arguments={}))

        assert result.success is True
        assert "skills" in result.output.lower()
