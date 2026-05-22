"""Tests unitaires GeoGouvService — mocks httpx, pas de réseau."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.geo_gouv import GeoError, GeoGouvService, get_geo_gouv_service


@pytest.fixture
def service():
    return GeoGouvService()


# ─── _validate_coords ───────────────────────────────────────────────────


def test_validate_coords_ok(service):
    # Pas d'exception
    service._validate_coords(2.3522, 48.8566)


@pytest.mark.parametrize("lon,lat", [
    (200, 48),     # lon > 180
    (-200, 48),    # lon < -180
    (2.3, 91),     # lat > 90
    (2.3, -91),    # lat < -90
    ("abc", 48),   # non-numérique
])
def test_validate_coords_invalid(service, lon, lat):
    with pytest.raises(GeoError):
        service._validate_coords(lon, lat)


# ─── _validate_insee ────────────────────────────────────────────────────


@pytest.mark.parametrize("code", ["75056", "13055", "2A004", "2B033"])
def test_validate_insee_valid(service, code):
    assert service._validate_insee(code).upper() == code.upper()


@pytest.mark.parametrize("bad", ["", "7505", "750560", "abcde", "2C004"])
def test_validate_insee_invalid(service, bad):
    with pytest.raises(GeoError):
        service._validate_insee(bad)


# ─── search_address ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_address_returns_features(service):
    fake = MagicMock()
    fake.json.return_value = {
        "features": [
            {
                "geometry": {"coordinates": [2.3522, 48.8566]},
                "properties": {
                    "label": "8 Rue de Rivoli 75004 Paris",
                    "score": 0.95,
                    "postcode": "75004",
                    "city": "Paris",
                    "citycode": "75104",
                },
            }
        ]
    }
    fake.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient") as mock_client:
        mock_ctx = mock_client.return_value.__aenter__.return_value
        mock_ctx.get = AsyncMock(return_value=fake)
        r = await service.search_address("8 rue de rivoli")
        assert r["features"][0]["properties"]["city"] == "Paris"


@pytest.mark.asyncio
async def test_search_address_clamps_limit(service):
    fake = MagicMock()
    fake.json.return_value = {"features": []}
    fake.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient") as mock_client:
        mock_ctx = mock_client.return_value.__aenter__.return_value
        mock_ctx.get = AsyncMock(return_value=fake)
        await service.search_address("x", limit=99)
        called = mock_ctx.get.call_args.kwargs["params"]
        assert called["limit"] == 20


# ─── reverse_geocode ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reverse_geocode_validates_first(service):
    with pytest.raises(GeoError):
        await service.reverse_geocode(999, 48)


@pytest.mark.asyncio
async def test_reverse_geocode_returns_features(service):
    fake = MagicMock()
    fake.json.return_value = {
        "features": [
            {
                "geometry": {"coordinates": [2.3522, 48.8566]},
                "properties": {"label": "X", "distance": 12.5},
            }
        ]
    }
    fake.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient") as mock_client:
        mock_ctx = mock_client.return_value.__aenter__.return_value
        mock_ctx.get = AsyncMock(return_value=fake)
        r = await service.reverse_geocode(2.3522, 48.8566)
        assert r["features"][0]["properties"]["label"] == "X"


# ─── get_commune_info ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_commune_info_returns_first(service):
    fake = MagicMock()
    fake.json.return_value = [
        {"nom": "Paris", "code": "75056", "population": 2102650}
    ]
    fake.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient") as mock_client:
        mock_ctx = mock_client.return_value.__aenter__.return_value
        mock_ctx.get = AsyncMock(return_value=fake)
        c = await service.get_commune_info("75056")
        assert c["nom"] == "Paris"


@pytest.mark.asyncio
async def test_commune_info_validates_insee(service):
    with pytest.raises(GeoError):
        await service.get_commune_info("ABC")


@pytest.mark.asyncio
async def test_commune_info_none_if_empty(service):
    fake = MagicMock()
    fake.json.return_value = []
    fake.raise_for_status = MagicMock()
    with patch("httpx.AsyncClient") as mock_client:
        mock_ctx = mock_client.return_value.__aenter__.return_value
        mock_ctx.get = AsyncMock(return_value=fake)
        assert await service.get_commune_info("99999") is None


# ─── rate limiter / SSRF / singleton ──────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limiter_throttles(service):
    rl = service._rate_limiter
    rl._max = 3
    rl._period = 0.5
    t0 = time.monotonic()
    for _ in range(4):
        await rl.acquire()
    assert time.monotonic() - t0 >= 0.4


@pytest.mark.asyncio
async def test_blocks_private_host():
    svc = GeoGouvService(ban_url="http://169.254.169.254")
    with pytest.raises(Exception):
        await svc.search_address("x")


def test_singleton():
    a = get_geo_gouv_service()
    b = get_geo_gouv_service()
    assert a is b
