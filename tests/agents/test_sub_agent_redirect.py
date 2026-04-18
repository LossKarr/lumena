"""
Tests pour la redirection smart run_command → read_file dans CodeAgent.
Vérifie que les commandes de lecture simple sont redirigées
et que les commandes avec pipe/redirection passent en run_command.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path


@pytest.fixture
def agent_with_workspace(tmp_path):
    """CodeAgent minimal avec un workspace et des fichiers."""
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent.__new__(CodeAgent)
    agent._task_workspace_root = tmp_path

    # Créer des fichiers de test
    (tmp_path / "index.html").write_text("<html>\n" * 400, encoding="utf-8")
    (tmp_path / "style.css").write_text("body { color: red; }\n", encoding="utf-8")
    css_dir = tmp_path / "css"
    css_dir.mkdir()
    (css_dir / "style.css").write_text(".nav { color: blue; }\n" * 50, encoding="utf-8")
    return agent


class TestSimpleRedirect:
    """Commandes de lecture simple → redirect vers read_file."""

    @pytest.mark.asyncio
    async def test_cat_redirects(self, agent_with_workspace):
        result = await agent_with_workspace._execute_loop_action(
            {"action": "run_command", "command": "cat index.html"}
        )
        assert "Lu:" in result.summary or "Lu:" in str(result)

    @pytest.mark.asyncio
    async def test_type_redirects(self, agent_with_workspace):
        result = await agent_with_workspace._execute_loop_action(
            {"action": "run_command", "command": "type style.css"}
        )
        assert "Lu:" in result.summary

    @pytest.mark.asyncio
    async def test_get_content_redirects(self, agent_with_workspace):
        result = await agent_with_workspace._execute_loop_action(
            {"action": "run_command", "command": "Get-Content style.css"}
        )
        assert "Lu:" in result.summary

    @pytest.mark.asyncio
    async def test_gc_alias_redirects(self, agent_with_workspace):
        result = await agent_with_workspace._execute_loop_action(
            {"action": "run_command", "command": "gc style.css"}
        )
        assert "Lu:" in result.summary

    @pytest.mark.asyncio
    async def test_less_redirects(self, agent_with_workspace):
        result = await agent_with_workspace._execute_loop_action(
            {"action": "run_command", "command": "less style.css"}
        )
        assert "Lu:" in result.summary

    @pytest.mark.asyncio
    async def test_more_redirects(self, agent_with_workspace):
        result = await agent_with_workspace._execute_loop_action(
            {"action": "run_command", "command": "more style.css"}
        )
        assert "Lu:" in result.summary


class TestNoRedirect:
    """Commandes avec pipe/redirection → PAS de redirect, passent en run_command."""

    @pytest.mark.asyncio
    async def test_get_content_pipe_select_string(self, agent_with_workspace):
        """Get-Content + Select-String → grep redirect (UTF-8 safe, pas run_command)."""
        # Le run_command ne doit PAS être appelé — la commande est redirigée vers grep
        agent_with_workspace._call_tool = AsyncMock(return_value="line 1: .nav { color: blue; }")
        result = await agent_with_workspace._execute_loop_action(
            {"action": "run_command", "command": 'Get-Content style.css | Select-String ".nav"'}
        )
        # Vérifie que grep a été appelé (pas run_command)
        # _call_tool peut être appelé via le handler grep interne
        for call in agent_with_workspace._call_tool.call_args_list:
            assert call[0][0] != "run_command", "Should redirect to grep, not run_command"

    @pytest.mark.asyncio
    async def test_type_pipe_findstr(self, agent_with_workspace):
        """type + findstr → run_command."""
        agent_with_workspace._call_tool = AsyncMock(return_value="result")
        result = await agent_with_workspace._execute_loop_action(
            {"action": "run_command", "command": 'type css\\style.css | findstr /n ".menu"'}
        )
        agent_with_workspace._call_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_head_not_redirected(self, agent_with_workspace):
        """head (retiré des patterns) → run_command."""
        agent_with_workspace._call_tool = AsyncMock(return_value="line1")
        result = await agent_with_workspace._execute_loop_action(
            {"action": "run_command", "command": "head -20 index.html"}
        )
        agent_with_workspace._call_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_tail_not_redirected(self, agent_with_workspace):
        """tail (retiré des patterns) → run_command."""
        agent_with_workspace._call_tool = AsyncMock(return_value="lastline")
        result = await agent_with_workspace._execute_loop_action(
            {"action": "run_command", "command": "tail -10 index.html"}
        )
        agent_with_workspace._call_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_content_measure_object(self, agent_with_workspace):
        """Get-Content + Measure-Object → run_command."""
        agent_with_workspace._call_tool = AsyncMock(return_value="42")
        result = await agent_with_workspace._execute_loop_action(
            {"action": "run_command", "command": "Get-Content index.html | Measure-Object -Line"}
        )
        agent_with_workspace._call_tool.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_content_select_last(self, agent_with_workspace):
        """Get-Content + Select-Object -Last → run_command (pas supported par read_file)."""
        agent_with_workspace._call_tool = AsyncMock(return_value="last lines")
        result = await agent_with_workspace._execute_loop_action(
            {"action": "run_command", "command": "Get-Content index.html | Select-Object -Last 20"}
        )
        agent_with_workspace._call_tool.assert_called_once()


class TestSmartRedirect:
    """Smart redirect: Get-Content + Select-Object -Index/-First → read_file(plage)."""

    @pytest.mark.asyncio
    async def test_select_object_index(self, agent_with_workspace):
        """Get-Content f | Select-Object -Index 10..20 → read_file(start=11, end=21)."""
        result = await agent_with_workspace._execute_loop_action(
            {"action": "run_command", "command": "Get-Content index.html | Select-Object -Index 10..20"}
        )
        assert "Lu:" in result.summary
        assert "L11-21" in result.summary

    @pytest.mark.asyncio
    async def test_select_object_first(self, agent_with_workspace):
        """Get-Content f | Select-Object -First 50 → read_file(start=1, end=50)."""
        result = await agent_with_workspace._execute_loop_action(
            {"action": "run_command", "command": "Get-Content index.html | Select-Object -First 50"}
        )
        assert "Lu:" in result.summary
        assert "L1-50" in result.summary

    @pytest.mark.asyncio
    async def test_gc_alias_select_index(self, agent_with_workspace):
        """gc f | Select-Object -Index 0..5 → read_file(start=1, end=6)."""
        result = await agent_with_workspace._execute_loop_action(
            {"action": "run_command", "command": "gc index.html | Select-Object -Index 0..5"}
        )
        assert "Lu:" in result.summary


class TestReadFileRange:
    """read_file avec start_line/end_line."""

    @pytest.mark.asyncio
    async def test_read_file_with_range(self, agent_with_workspace):
        result = await agent_with_workspace._execute_loop_action(
            {"action": "read_file", "path": "index.html", "start_line": 10, "end_line": 20}
        )
        assert "L10-20" in result.summary
        assert "11 lignes" in result.summary

    @pytest.mark.asyncio
    async def test_read_file_big_file_hint(self, agent_with_workspace):
        """Fichier > 300 lignes sans plage → summary contient 'GROS FICHIER'."""
        result = await agent_with_workspace._execute_loop_action(
            {"action": "read_file", "path": "index.html"}
        )
        assert "GROS FICHIER" in result.summary

    @pytest.mark.asyncio
    async def test_read_file_small_no_hint(self, agent_with_workspace):
        """Fichier < 300 lignes → summary normal."""
        result = await agent_with_workspace._execute_loop_action(
            {"action": "read_file", "path": "style.css"}
        )
        assert "GROS FICHIER" not in result.summary

    @pytest.mark.asyncio
    async def test_read_file_detail_complete(self, agent_with_workspace):
        """detail contient TOUJOURS le contenu complet (session_memory intégrité)."""
        result = await agent_with_workspace._execute_loop_action(
            {"action": "read_file", "path": "index.html"}
        )
        # 400 lines de <html>\n → detail doit avoir les 400 lignes numérotées
        lines_in_detail = result.detail.count("\n") + 1
        assert lines_in_detail >= 400
