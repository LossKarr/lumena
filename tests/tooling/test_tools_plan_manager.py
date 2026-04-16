"""Tests unitaires pour src/tools/plan_manager.py"""
import pytest
import asyncio
from unittest.mock import patch
from pathlib import Path

from src.tools.plan_manager import (
    _slugify,
    _parse_tasks_from_content,
    handle_plan_create,
    handle_plan_list,
)


class TestSlugify:
    def test_basic_slug(self):
        result = _slugify("Hello World")
        assert " " not in result
        assert result.lower() == result

    def test_special_chars_removed(self):
        result = _slugify("Test: Plan! 2024")
        assert "!" not in result
        assert ":" not in result

    def test_empty_string(self):
        result = _slugify("")
        assert isinstance(result, str)


class TestParseTasksFromContent:
    def test_parses_markdown_checkboxes(self):
        content = "# Plan\n- [ ] Task 1\n- [ ] Task 2\n- [x] Task done"
        tasks = _parse_tasks_from_content(content)
        assert len(tasks) >= 2

    def test_returns_list_of_tuples(self):
        content = "- [ ] Do something\n- [x] Done thing"
        tasks = _parse_tasks_from_content(content)
        assert isinstance(tasks, list)
        if tasks:
            assert isinstance(tasks[0], tuple)

    def test_empty_content(self):
        tasks = _parse_tasks_from_content("")
        assert isinstance(tasks, list)


class TestHandlePlanCreate:
    @pytest.mark.asyncio
    async def test_create_with_title(self, tmp_path):
        with patch("src.tools.plan_manager._PLANS_DIR", tmp_path / "plans"):
            with patch("src.tools.plan_manager._ARCHIVES_DIR", tmp_path / "archive"):
                result = await handle_plan_create(
                    title="Test Plan",
                    description="A test plan for unit testing"
                )
                assert isinstance(result, str)
                assert len(result) > 0

    @pytest.mark.asyncio
    async def test_create_missing_title(self, tmp_path):
        with patch("src.tools.plan_manager._PLANS_DIR", tmp_path / "plans"):
            result = await handle_plan_create(title="", description="")
            assert isinstance(result, str)


class TestHandlePlanList:
    @pytest.mark.asyncio
    async def test_list_empty(self, tmp_path):
        with patch("src.tools.plan_manager._PLANS_DIR", tmp_path / "plans"):
            result = await handle_plan_list()
            assert isinstance(result, str)
