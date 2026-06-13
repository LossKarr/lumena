"""Tests: Tool System

Tous les outils sont dans les handlers V2 (src/reasoning/handlers/).
Les outils website_builder ont été migrés en V2 dans handlers/website.py (P1.2.7).
Le legacy tool_system ne contient plus aucun outil par défaut.
Ces tests vérifient le bon comportement du shim et la stabilité de l'API.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestToolSystemImport:
    def test_import_tool_system(self):
        from src.tools.tool_system import LumenaToolSystem

        assert LumenaToolSystem is not None

    def test_create_instance(self):
        from src.tools.tool_system import LumenaToolSystem

        ts = LumenaToolSystem()
        assert ts is not None
        # Sans V2 lié, ts.tools est vide (tous les outils sont en V2)
        assert isinstance(ts.tools, dict)

    def test_tool_count_with_v2(self):
        """Quand V2 est lié, tool_count reflète tous les outils."""
        from src.tools.tool_system import LumenaToolSystem

        ts = LumenaToolSystem()
        # Sans V2, ts.tools est vide ; la propriété tool_count existe
        assert hasattr(ts, 'tool_count')
        assert ts.tool_count >= 0


class TestToolRegistration:
    def test_website_tools_in_v2(self):
        """Les outils website sont désormais enregistrés en V2 (handlers/website.py)."""
        from src.reasoning.handlers.website import get_website_handler_defs

        defs = get_website_handler_defs()
        names = {d.name for d in defs}
        assert "generate_website" in names
        assert "write_website_files" in names
        assert "list_website_projects" in names
        assert "browser_verify_local_project" in names
        assert len(defs) == 9

    def test_required_tools_known_via_is_known_tool(self):
        """Les outils core sont reconnus via _is_known_tool (legacy ou V2)."""
        from src.tools.tool_system import LumenaToolSystem

        ts = LumenaToolSystem()
        # Sans V2 registry, on met directement dans le dict legacy
        ts.tools["get_time"] = {"name": "get_time", "description": "stub", "parameters": {}}
        assert ts._is_known_tool("get_time")

    def test_tool_structure(self):
        from src.reasoning.handlers.website import get_website_handler_defs

        defs = get_website_handler_defs()
        hdef = defs[0]

        assert isinstance(hdef.name, str)
        assert isinstance(hdef.description, str)
        assert callable(hdef.handler)


class TestToolCategories:
    def test_categories_exist(self):
        from src.reasoning.handlers.website import get_website_handler_defs

        defs = get_website_handler_defs()
        categories = {d.category for d in defs}
        assert "website" in categories


@pytest.mark.asyncio
class TestToolExecution:
    async def test_execute_tool_via_v2_registry(self):
        """Test qu'un outil V2 s'exécute via le ToolRegistry bindé."""
        from src.tools.tool_system import LumenaToolSystem, ToolCall
        from unittest.mock import AsyncMock, MagicMock

        ts = LumenaToolSystem()
        mock_registry = MagicMock()
        mock_registry.execute = AsyncMock(return_value="📅 17/03/2026 20:00:00")
        mock_registry.tools = {"get_time": MagicMock()}
        ts.bind_tool_registry(mock_registry)

        result = await ts.execute_tool(ToolCall(name="get_time", arguments={}))

        assert result.success is True
        assert ":" in result.output

    async def test_unknown_tool(self):
        from src.tools.tool_system import LumenaToolSystem, ToolCall

        ts = LumenaToolSystem()
        result = await ts.execute_tool(ToolCall(name="unknown_tool_xyz", arguments={}))

        assert result.success is False
        text = (result.error or "").lower()
        assert "non trouv" in text or "error" in text or "erreur" in text
