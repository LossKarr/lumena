from __future__ import annotations

import pytest

from src.documents.web_document_search import DocumentWebSearch


class FakeSearchHub:
    def __init__(self):
        self.query = ""

    async def web_search(self, query: str, count: int = 8):
        self.query = query
        return {
            "source": "fake",
            "results": [
                {"title": "Rapport public", "url": "https://example.org/r.pdf", "description": "PDF officiel"},
                {"title": "Page HTML", "url": "https://example.org/page", "description": "pas un document"},
                {"title": "Tableau XLSX", "url": "https://example.org/download", "description": "fichier .xlsx annuel"},
            ],
        }


@pytest.mark.asyncio
async def test_search_enriches_query_and_filters_candidates():
    hub = FakeSearchHub()
    result = await DocumentWebSearch(hub).search("budget 2026", formats=["pdf", "xlsx"])
    assert "filetype:pdf" in hub.query
    assert "filetype:xlsx" in hub.query
    assert [item["detected_format"] for item in result["candidates"]] == ["pdf", "xlsx"]
    assert result["provider"] == "fake"


@pytest.mark.asyncio
async def test_search_is_discovery_only():
    result = await DocumentWebSearch(FakeSearchHub()).search("rapport", formats=["pdf"])
    assert set(result["candidates"][0]) == {"title", "url", "description", "detected_format", "source"}
