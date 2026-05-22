"""Tests unitaires SireneService — mocks httpx, pas d'appel réseau."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.sirene import SireneError, SireneService, get_sirene_service


@pytest.fixture
def service():
    return SireneService()


# ─── normalisation SIRET ────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("12345678901234", "12345678901234"),
    ("123 456 789 01234", "12345678901234"),
    ("123-456-789-01234", "12345678901234"),
    ("12 34 56 78 90 12 34", "12345678901234"),
    ("123456789", "123456789"),  # SIREN (9), pas SIRET
    ("abc12345", "12345"),
    ("", ""),
])
def test_normalize_siret(raw, expected):
    assert SireneService._normalize_siret(raw) == expected


# ─── search_companies ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_companies_returns_data(service):
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "results": [{"siren": "123456789", "nom_complet": "Dupont SAS"}],
        "total_results": 1,
        "page": 1,
        "per_page": 10,
    }
    fake_response.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient") as mock_client:
        mock_ctx = mock_client.return_value.__aenter__.return_value
        mock_ctx.get = AsyncMock(return_value=fake_response)
        result = await service.search_companies("Dupont")
        assert result["total_results"] == 1
        assert result["results"][0]["siren"] == "123456789"


@pytest.mark.asyncio
async def test_search_clamps_per_page_to_25(service):
    fake_response = MagicMock()
    fake_response.json.return_value = {"results": [], "total_results": 0}
    fake_response.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient") as mock_client:
        mock_ctx = mock_client.return_value.__aenter__.return_value
        mock_ctx.get = AsyncMock(return_value=fake_response)
        await service.search_companies("x", per_page=999)
        kwargs = mock_ctx.get.call_args.kwargs
        assert kwargs["params"]["per_page"] == 25


# ─── get_company_by_siret ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_siret_validates_length(service):
    with pytest.raises(SireneError, match="14 chiffres"):
        await service.get_company_by_siret("123")


@pytest.mark.asyncio
async def test_get_by_siret_tolerates_spaces(service):
    """'123 456 789 01234' → 14 chiffres après normalisation."""
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "results": [{"siren": "123456789", "nom_complet": "X"}],
        "total_results": 1,
    }
    fake_response.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient") as mock_client:
        mock_ctx = mock_client.return_value.__aenter__.return_value
        mock_ctx.get = AsyncMock(return_value=fake_response)
        result = await service.get_company_by_siret("123 456 789 01234")
        assert result is not None
        assert result["siren"] == "123456789"
        # Le paramètre q doit être le SIRET normalisé
        called_params = mock_ctx.get.call_args.kwargs["params"]
        assert called_params["q"] == "12345678901234"


@pytest.mark.asyncio
async def test_get_by_siret_returns_none_when_not_found(service):
    fake_response = MagicMock()
    fake_response.json.return_value = {"results": [], "total_results": 0}
    fake_response.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient") as mock_client:
        mock_ctx = mock_client.return_value.__aenter__.return_value
        mock_ctx.get = AsyncMock(return_value=fake_response)
        result = await service.get_company_by_siret("12345678901234")
        assert result is None


# ─── rate limiter ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limiter_throttles(service):
    rl = service._rate_limiter
    rl._max = 3
    rl._period = 0.5
    t0 = time.monotonic()
    for _ in range(4):
        await rl.acquire()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.4


# ─── SSRF guard ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_blocks_metadata_url():
    """Si on injecte un host privé via base_url custom, assert_url_safe bloque."""
    svc = SireneService(base_url="http://169.254.169.254")
    with pytest.raises(Exception):
        await svc.search_companies("x")


# ─── singleton ──────────────────────────────────────────────────────────


def test_singleton_returns_same_instance():
    a = get_sirene_service()
    b = get_sirene_service()
    assert a is b
