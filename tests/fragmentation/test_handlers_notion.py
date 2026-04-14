"""
Tests unitaires pour handlers/notion.py — 7 handlers.

Convention: chaque handler reçoit un HandlerContext et retourne HandlerResult.
Le hub est mocké via ctx._notion_hub (méthodes async).
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.notion import (
    notion_search_handler,
    notion_read_page_handler,
    notion_create_page_handler,
    notion_update_page_handler,
    notion_list_databases_handler,
    notion_query_database_handler,
    notion_add_to_database_handler,
    get_notion_handler_defs,
)


@pytest.fixture
def ctx(tmp_path):
    c = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path / "workspace")
    hub = AsyncMock()
    c._notion_hub = hub
    return c


# ─── notion_search ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notion_search_success(ctx):
    ctx._notion_hub.search.return_value = {
        "count": 2,
        "results": [
            {"id": "p1", "title": "Page A", "type": "page", "url": "https://notion.so/p1"},
            {"id": "p2", "title": "Page B", "type": "database", "url": "https://notion.so/p2"},
        ]
    }
    r = await notion_search_handler(ctx, query="test")
    assert r.success
    assert "Page A" in r.output


@pytest.mark.asyncio
async def test_notion_search_empty(ctx):
    ctx._notion_hub.search.return_value = {
        "count": 0, "results": []
    }
    r = await notion_search_handler(ctx, query="nothing")
    assert r.success
    assert "Aucun" in r.output or "0" in r.output


@pytest.mark.asyncio
async def test_notion_search_failure(ctx):
    ctx._notion_hub.search.side_effect = Exception("API error")
    r = await notion_search_handler(ctx, query="test")
    assert not r.success


# ─── notion_read_page ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notion_read_page_success(ctx):
    ctx._notion_hub.read_page.return_value = {
        "id": "p1", "title": "My Page",
        "content": "Some content here", "url": "https://notion.so/p1"
    }
    r = await notion_read_page_handler(ctx, page_id="p1")
    assert r.success
    assert "My Page" in r.output


@pytest.mark.asyncio
async def test_notion_read_page_failure(ctx):
    ctx._notion_hub.read_page.side_effect = Exception("page not found")
    r = await notion_read_page_handler(ctx, page_id="bad")
    assert not r.success


# ─── notion_create_page ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notion_create_page_success(ctx):
    ctx._notion_hub.create_page.return_value = {
        "id": "new1", "title": "New Page", "url": "https://notion.so/new1"
    }
    r = await notion_create_page_handler(ctx, parent_id="db1", title="New Page", content="Hello")
    assert r.success
    assert "New Page" in r.output


@pytest.mark.asyncio
async def test_notion_create_page_failure(ctx):
    ctx._notion_hub.create_page.side_effect = Exception("permission denied")
    r = await notion_create_page_handler(ctx, parent_id="db1", title="X", content="Y")
    assert not r.success


# ─── notion_update_page ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notion_update_page_success(ctx):
    ctx._notion_hub.update_page.return_value = {
        "id": "p1", "blocks_added": 3
    }
    r = await notion_update_page_handler(ctx, page_id="p1", content="Updated content")
    assert r.success
    assert "p1" in r.output


@pytest.mark.asyncio
async def test_notion_update_page_failure(ctx):
    ctx._notion_hub.update_page.side_effect = Exception("invalid property")
    r = await notion_update_page_handler(ctx, page_id="p1", content="X")
    assert not r.success


# ─── notion_list_databases ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notion_list_databases_success(ctx):
    ctx._notion_hub.list_databases.return_value = {
        "count": 1,
        "databases": [{"id": "db1", "title": "Tasks", "url": "https://notion.so/db1"}]
    }
    r = await notion_list_databases_handler(ctx)
    assert r.success
    assert "Tasks" in r.output


@pytest.mark.asyncio
async def test_notion_list_databases_empty(ctx):
    ctx._notion_hub.list_databases.return_value = {
        "count": 0, "databases": []
    }
    r = await notion_list_databases_handler(ctx)
    assert r.success
    assert "Aucune" in r.output or "0" in r.output


# ─── notion_query_database ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notion_query_database_success(ctx):
    ctx._notion_hub.query_database.return_value = {
        "count": 2,
        "rows": [
            {"id": "r1", "properties": {"Name": "Task 1"}},
            {"id": "r2", "properties": {"Name": "Task 2"}},
        ]
    }
    r = await notion_query_database_handler(ctx, database_id="db1")
    assert r.success
    assert "2" in r.output or "Task" in r.output


@pytest.mark.asyncio
async def test_notion_query_database_failure(ctx):
    ctx._notion_hub.query_database.return_value = {
        "error": "db not found", "rows": [], "count": 0
    }
    r = await notion_query_database_handler(ctx, database_id="bad")
    assert not r.success


# ─── notion_add_to_database ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notion_add_to_database_success(ctx):
    ctx._notion_hub.add_to_database.return_value = {
        "id": "row1", "url": "https://notion.so/row1"
    }
    r = await notion_add_to_database_handler(
        ctx, database_id="db1", properties_json='{"Name": "New Row"}'
    )
    assert r.success
    assert "row1" in r.output or "ajouté" in r.output.lower()


@pytest.mark.asyncio
async def test_notion_add_to_database_failure(ctx):
    ctx._notion_hub.add_to_database.return_value = {
        "error": "schema mismatch"
    }
    r = await notion_add_to_database_handler(
        ctx, database_id="db1", properties_json='{}'
    )
    assert not r.success


# ─── HandlerDefs ───────────────────────────────────────────────────────────

def test_handler_defs_count():
    defs = get_notion_handler_defs()
    assert len(defs) == 7


def test_handler_defs_names():
    defs = get_notion_handler_defs()
    names = [d.name for d in defs]
    assert len(names) == len(set(names))


def test_handler_defs_expected_names():
    expected = {
        "notion_search", "notion_read_page", "notion_create_page",
        "notion_update_page", "notion_list_databases",
        "notion_query_database", "notion_add_to_database",
    }
    defs = get_notion_handler_defs()
    actual = {d.name for d in defs}
    assert actual == expected


def test_handler_defs_have_handlers():
    for d in get_notion_handler_defs():
        assert callable(d.handler), f"{d.name} handler not callable"
