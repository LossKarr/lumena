"""Tests handlers V3.3 — geo_search_address / geo_reverse / geo_commune_info.

Mocks service, pas de réseau.
Pas de leak V3.4 (data_join).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.geo_gouv import (
    geo_commune_info_handler,
    geo_reverse_handler,
    geo_search_address_handler,
    get_geo_gouv_handler_defs,
)


@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return HandlerContext.for_testing(
        lumena_root=tmp_path, runtime_root=workspace,
    )


def _mock_service(**overrides):
    svc = MagicMock()
    svc.search_address = AsyncMock(return_value={
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
    })
    svc.reverse_geocode = AsyncMock(return_value={
        "features": [
            {
                "geometry": {"coordinates": [2.3522, 48.8566]},
                "properties": {
                    "label": "Place du Louvre Paris",
                    "postcode": "75001",
                    "city": "Paris",
                    "citycode": "75101",
                    "distance": 15.2,
                },
            }
        ]
    })
    svc.get_commune_info = AsyncMock(return_value={
        "nom": "Paris",
        "code": "75056",
        "codesPostaux": ["75001", "75002", "75003"],
        "siren": "217500016",
        "codeEpci": "200054781",
        "codeDepartement": "75",
        "codeRegion": "11",
        "population": 2102650,
        "surface": 10540,  # hectares
    })
    for k, v in overrides.items():
        setattr(svc, k, v)
    return svc


# ─── geo_search_address ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_address_returns_block(ctx):
    with patch(
        "src.reasoning.handlers.geo_gouv._get_service",
        return_value=_mock_service(),
    ):
        result = await geo_search_address_handler(ctx, query="8 rue de rivoli")
    assert result.success is True
    out = result.output
    assert "Paris" in out
    assert "2.3522" in out
    assert "48.8566" in out
    assert "75104" in out  # citycode INSEE


@pytest.mark.asyncio
async def test_search_address_empty_gives_advice(ctx):
    svc = _mock_service()
    svc.search_address = AsyncMock(return_value={"features": []})
    with patch(
        "src.reasoning.handlers.geo_gouv._get_service",
        return_value=svc,
    ):
        result = await geo_search_address_handler(ctx, query="zzz")
    assert result.success is True
    assert "Aucune adresse" in result.output


@pytest.mark.asyncio
async def test_search_address_exception_returns_fail(ctx):
    svc = _mock_service()
    svc.search_address = AsyncMock(side_effect=RuntimeError("BAN down"))
    with patch(
        "src.reasoning.handlers.geo_gouv._get_service",
        return_value=svc,
    ):
        result = await geo_search_address_handler(ctx, query="x")
    assert result.success is False
    assert "Erreur" in result.output


# ─── geo_reverse ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reverse_returns_address(ctx):
    with patch(
        "src.reasoning.handlers.geo_gouv._get_service",
        return_value=_mock_service(),
    ):
        result = await geo_reverse_handler(ctx, lon=2.3522, lat=48.8566)
    assert result.success is True
    out = result.output
    assert "Place du Louvre" in out
    assert "75001" in out
    assert "15m" in out or "15.2" in out or "15" in out  # distance


@pytest.mark.asyncio
async def test_reverse_invalid_coords_returns_fail(ctx):
    from src.services.geo_gouv import GeoError
    svc = _mock_service()
    svc.reverse_geocode = AsyncMock(side_effect=GeoError("Longitude hors plage"))
    with patch(
        "src.reasoning.handlers.geo_gouv._get_service",
        return_value=svc,
    ):
        result = await geo_reverse_handler(ctx, lon=999, lat=48)
    assert result.success is False
    assert "Longitude" in result.output or "❌" in result.output


# ─── geo_commune_info ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_commune_info_returns_block(ctx):
    with patch(
        "src.reasoning.handlers.geo_gouv._get_service",
        return_value=_mock_service(),
    ):
        result = await geo_commune_info_handler(ctx, code_insee="75056")
    assert result.success is True
    out = result.output
    assert "Paris" in out
    assert "75056" in out
    assert "2102650" in out  # population
    assert "km²" in out


@pytest.mark.asyncio
async def test_commune_info_invalid_insee_returns_fail(ctx):
    from src.services.geo_gouv import GeoError
    svc = _mock_service()
    svc.get_commune_info = AsyncMock(side_effect=GeoError("Code INSEE invalide"))
    with patch(
        "src.reasoning.handlers.geo_gouv._get_service",
        return_value=svc,
    ):
        result = await geo_commune_info_handler(ctx, code_insee="ABC")
    assert result.success is False
    assert "INSEE" in result.output or "❌" in result.output


@pytest.mark.asyncio
async def test_commune_info_not_found_returns_ok(ctx):
    svc = _mock_service()
    svc.get_commune_info = AsyncMock(return_value=None)
    with patch(
        "src.reasoning.handlers.geo_gouv._get_service",
        return_value=svc,
    ):
        result = await geo_commune_info_handler(ctx, code_insee="99999")
    assert result.success is True
    assert "Aucune commune" in result.output


# ─── isolation : pas de drift datagouv / sirene / browser ─────────────


@pytest.mark.asyncio
async def test_geo_does_not_call_datagouv_or_sirene(ctx):
    with patch(
        "src.reasoning.handlers.geo_gouv._get_service",
        return_value=_mock_service(),
    ), patch("src.services.datagouv.get_datagouv_service") as dg_mock, \
         patch("src.services.sirene.get_sirene_service") as sr_mock:
        result = await geo_search_address_handler(ctx, query="paris")
        assert result.success is True
        dg_mock.assert_not_called()
        sr_mock.assert_not_called()


# ─── HandlerDef format ────────────────────────────────────────────────


def test_handler_defs_returns_3_entries():
    defs = get_geo_gouv_handler_defs()
    names = {d.name for d in defs}
    assert names == {"geo_search_address", "geo_reverse", "geo_commune_info"}


def test_handler_defs_all_web_category():
    for d in get_geo_gouv_handler_defs():
        assert d.category == "web"
        assert d.source_module == "handlers.geo_gouv"


def test_handler_defs_required_params():
    by_name = {d.name: d for d in get_geo_gouv_handler_defs()}
    assert by_name["geo_search_address"].parameters["required"] == ["query"]
    assert by_name["geo_reverse"].parameters["required"] == ["lon", "lat"]
    assert by_name["geo_commune_info"].parameters["required"] == ["code_insee"]


def test_no_v3_4_leak():
    """V3.3 ne doit pas exposer data_join (V3.4) ni geo_export, etc."""
    names = {d.name for d in get_geo_gouv_handler_defs()}
    forbidden = {"data_join", "geo_export", "geo_batch", "geo_route"}
    assert names.isdisjoint(forbidden), f"Leak V3.4+ détecté : {names & forbidden}"


def test_description_distinguishes_from_sirene_datagouv():
    defs = {d.name: d for d in get_geo_gouv_handler_defs()}
    desc = defs["geo_search_address"].description.lower()
    assert "sirene" in desc or "datagouv" in desc or "data.gouv" in desc
