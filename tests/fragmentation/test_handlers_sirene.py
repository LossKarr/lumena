"""Tests handler SIRENE V3.2 — mocks service, pas de réseau.

V3.2 scope strict :
- sirene_search_company
- sirene_get_by_siret
- AUCUN leak V3.3 (geo) / V3.4 (join)
- AUCUN drift datagouv / browser
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.sirene import (
    get_sirene_handler_defs,
    sirene_get_by_siret_handler,
    sirene_search_company_handler,
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
    svc.search_companies = AsyncMock(return_value={
        "results": [
            {
                "siren": "552032534",
                "nom_complet": "SNCF",
                "nom_raison_sociale": "SNCF",
                "siege": {
                    "siret": "55203253400600",
                    "adresse": "9 Rue Jean-Philippe Rameau",
                    "code_postal": "93200",
                    "libelle_commune": "SAINT-DENIS",
                },
                "nature_juridique": "5710",
                "activite_principale": "49.10Z",
                "date_creation": "1937-01-01",
                "etat_administratif": "A",
                "tranche_effectif_salarie": "53",
                "nombre_etablissements": 800,
                "nombre_etablissements_ouverts": 750,
                "dirigeants": [
                    {"nom_complet": "Jean Dupont", "qualite": "Président"},
                ],
            }
        ],
        "total_results": 1,
    })
    for k, v in overrides.items():
        setattr(svc, k, v)
    return svc


# ─── sirene_search_company ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_returns_company_block(ctx):
    with patch(
        "src.reasoning.handlers.sirene._get_service",
        return_value=_mock_service(),
    ):
        result = await sirene_search_company_handler(ctx, query="SNCF")
    assert isinstance(result, HandlerResult)
    assert result.success is True
    out = result.output
    assert "SNCF" in out
    assert "552032534" in out  # SIREN
    assert "49.10Z" in out     # NAF
    assert "Jean Dupont" in out  # dirigeant


@pytest.mark.asyncio
async def test_search_empty_results_gives_advice(ctx):
    svc = _mock_service()
    svc.search_companies = AsyncMock(return_value={"results": [], "total_results": 0})
    with patch(
        "src.reasoning.handlers.sirene._get_service",
        return_value=svc,
    ):
        result = await sirene_search_company_handler(ctx, query="xxnotfoundxx")
    assert result.success is True
    assert "Aucune entreprise" in result.output
    assert "Conseils" in result.output


@pytest.mark.asyncio
async def test_search_exception_returns_fail(ctx):
    svc = _mock_service()
    svc.search_companies = AsyncMock(side_effect=RuntimeError("API down"))
    with patch(
        "src.reasoning.handlers.sirene._get_service",
        return_value=svc,
    ):
        result = await sirene_search_company_handler(ctx, query="x")
    assert result.success is False
    assert "Erreur recherche SIRENE" in result.output


# ─── sirene_get_by_siret ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_by_siret_returns_company(ctx):
    svc = _mock_service()
    # Simuler le service.get_company_by_siret directement (déjà testé en unit)
    svc.get_company_by_siret = AsyncMock(return_value={
        "siren": "552032534",
        "nom_complet": "SNCF",
        "siege": {"siret": "55203253400600"},
        "nature_juridique": "5710",
        "activite_principale": "49.10Z",
        "date_creation": "1937-01-01",
        "etat_administratif": "A",
    })
    with patch(
        "src.reasoning.handlers.sirene._get_service",
        return_value=svc,
    ):
        result = await sirene_get_by_siret_handler(ctx, siret="55203253400600")
    assert result.success is True
    out = result.output
    assert "SNCF" in out
    assert "55203253400600" in out
    assert "49.10Z" in out


@pytest.mark.asyncio
async def test_get_by_siret_malformed_returns_fail(ctx):
    from src.services.sirene import SireneError
    svc = _mock_service()
    svc.get_company_by_siret = AsyncMock(side_effect=SireneError("SIRET invalide : 3"))
    with patch(
        "src.reasoning.handlers.sirene._get_service",
        return_value=svc,
    ):
        result = await sirene_get_by_siret_handler(ctx, siret="123")
    assert result.success is False
    assert "invalide" in result.output.lower() or "SIRET" in result.output


@pytest.mark.asyncio
async def test_get_by_siret_not_found_returns_ok_message(ctx):
    svc = _mock_service()
    svc.get_company_by_siret = AsyncMock(return_value=None)
    with patch(
        "src.reasoning.handlers.sirene._get_service",
        return_value=svc,
    ):
        result = await sirene_get_by_siret_handler(ctx, siret="00000000000000")
    assert result.success is True
    assert "Aucune entreprise" in result.output


# ─── isolation : pas de drift datagouv / browser ────────────────────────


@pytest.mark.asyncio
async def test_sirene_does_not_call_datagouv(ctx):
    """SIRENE est sur recherche-entreprises.api.gouv.fr, pas data.gouv."""
    with patch(
        "src.reasoning.handlers.sirene._get_service",
        return_value=_mock_service(),
    ), patch(
        "src.services.datagouv.get_datagouv_service"
    ) as datagouv_mock:
        result = await sirene_search_company_handler(ctx, query="SNCF")
        assert result.success is True
        datagouv_mock.assert_not_called()


# ─── HandlerDef format ─────────────────────────────────────────────────


def test_handler_defs_returns_2_entries():
    defs = get_sirene_handler_defs()
    names = {d.name for d in defs}
    assert names == {"sirene_search_company", "sirene_get_by_siret"}


def test_handler_defs_all_data_category():
    for d in get_sirene_handler_defs():
        assert d.category == "data"
        assert d.source_module == "handlers.sirene"


def test_handler_defs_required_params():
    by_name = {d.name: d for d in get_sirene_handler_defs()}
    assert by_name["sirene_search_company"].parameters["required"] == ["query"]
    assert by_name["sirene_get_by_siret"].parameters["required"] == ["siret"]


def test_no_v3_3_v3_4_leak():
    """V3.2 ne doit pas exposer geo (V3.3) ni join (V3.4)."""
    names = {d.name for d in get_sirene_handler_defs()}
    forbidden = {
        "geo_search_address", "geo_reverse", "geo_commune_info",
        "data_join", "sirene_export", "sirene_join",
    }
    assert names.isdisjoint(forbidden), f"Leak V3.3+ détecté : {names & forbidden}"


def test_description_warns_against_confusing_with_datagouv():
    """Description doit distinguer SIRENE de datagouv."""
    defs = {d.name: d for d in get_sirene_handler_defs()}
    desc = defs["sirene_search_company"].description.lower()
    assert "datagouv" in desc or "data.gouv" in desc
