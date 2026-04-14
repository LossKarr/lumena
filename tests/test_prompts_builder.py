"""Tests unitaires pour src/prompts/builder.py"""
import pytest

from src.prompts.builder import PromptBuilder, PromptSection, PromptTemplate


class TestPromptSection:
    def test_all_values_unique(self):
        values = [s.value for s in PromptSection]
        assert len(values) == len(set(values))

    def test_key_sections_present(self):
        names = {s.name for s in PromptSection}
        assert "IDENTITY" in names
        assert "TOOLS" in names
        assert "RULES" in names


class TestPromptTemplate:
    def test_creation(self):
        tpl = PromptTemplate(
            section=PromptSection.IDENTITY,
            content="I am Lumena",
            priority=100
        )
        assert tpl.content == "I am Lumena"
        assert tpl.enabled is True


class TestPromptBuilder:
    def test_default_templates_defined(self):
        pb = PromptBuilder()
        assert PromptSection.IDENTITY in pb.templates
        assert PromptSection.RULES in pb.templates

    def test_build_returns_string(self):
        pb = PromptBuilder()
        result = pb.build()
        assert isinstance(result, str)
        assert len(result) > 20

    def test_build_includes_identity(self):
        pb = PromptBuilder()
        result = pb.build()
        assert "LUMENA" in result or "Lumena" in result

    def test_add_custom_section(self):
        pb = PromptBuilder()
        pb.add_custom_section("Custom content for testing", priority=50)
        result = pb.build()
        assert "Custom content for testing" in result

    def test_set_section_content(self):
        pb = PromptBuilder()
        pb.set_section(PromptSection.IDENTITY, "## Custom Identity\nI am custom.")
        result = pb.build()
        assert "Custom Identity" in result

    def test_build_with_tools(self):
        pb = PromptBuilder()
        tools_text = "- web_search: Search the web"
        pb.set_tools_section(tools_text)
        result = pb.build()
        assert "web_search" in result

    def test_clear_custom_sections(self):
        pb = PromptBuilder()
        pb.add_custom_section("my section", priority=50)
        pb.clear_custom_sections()
        result = pb.build()
        assert "my section" not in result
