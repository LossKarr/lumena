"""V3.1 — scoring multi-datasets dans datagouv_search.

Scope strict :
- score /100 + verdict + raisons dans la sortie handler
- tri principal par score décroissant
- required_format pénalise/favorise correctement
- dataset massif/vide pénalisé
- organisme + fraîcheur favorisés
- AUCUN appel browser/web_fetch/http_request
- AUCUN leak SIRENE/geo/join (V3.2+)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.datagouv import (
    _score_dataset_v3,
    datagouv_search_handler,
    get_datagouv_handler_defs,
)


# ─── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return HandlerContext.for_testing(
        lumena_root=tmp_path, runtime_root=workspace,
    )


def _recent_iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat()


def _build_dataset(
    slug: str, title: str, formats=("csv",), nb_res=None,
    org="Région X", desc_chars=100, last_modified_days_ago=180,
):
    if nb_res is None:
        resources = [{"format": f} for f in formats]
    else:
        resources = [{"format": formats[0] if formats else "xml"}] * nb_res
    return {
        "slug": slug,
        "title": title,
        "organization": ({"name": org} if org else None),
        "resources": resources,
        "description": "x" * desc_chars,
        "last_modified": (
            _recent_iso(last_modified_days_ago)
            if last_modified_days_ago is not None else None
        ),
    }


def _mock_service(datasets):
    svc = MagicMock()
    svc.search_datasets = AsyncMock(
        return_value={"data": datasets, "total": len(datasets)}
    )
    return svc


# ─── _score_dataset_v3 (unitaire) ───────────────────────────────────────


def test_score_v3_perfect_csv_recent_org():
    ds = _build_dataset(
        "perfect", "Parfait", formats=("csv",),
        org="INSEE", last_modified_days_ago=60, desc_chars=200,
    )
    score, reasons, verdict = _score_dataset_v3(ds, "csv")
    assert score >= 70
    assert verdict.startswith("✅")
    # raisons doivent contenir format présent + organisme
    rs = " ".join(reasons).lower()
    assert "csv" in rs and "présent" in rs
    assert "organisme" in rs


def test_score_v3_no_resources_penalized():
    ds = _build_dataset("empty", "Vide", nb_res=0, formats=())
    ds["resources"] = []
    score, reasons, verdict = _score_dataset_v3(ds, "csv")
    assert score < 40
    assert verdict.startswith("⛔")
    assert any("aucune ressource" in r.lower() for r in reasons)


def test_score_v3_massive_penalized():
    ds = _build_dataset("massive", "Massif", formats=("xml",), nb_res=200)
    score, reasons, verdict = _score_dataset_v3(ds, "csv")
    # pas de CSV + massif → bien en dessous du seuil acceptable
    assert score < 40
    assert any("massif" in r.lower() for r in reasons)
    assert any("pas de" in r.lower() for r in reasons)


def test_score_v3_no_required_format_when_demanded():
    ds = _build_dataset("xml-only", "X", formats=("xml",), org="X")
    score, _, _ = _score_dataset_v3(ds, "csv")
    score_with_csv, _, _ = _score_dataset_v3(
        _build_dataset("ok", "X", formats=("csv",), org="X"), "csv"
    )
    assert score < score_with_csv


def test_score_v3_recent_update_bonus():
    """Sans saturation : fixtures sans CSV pour comparer juste la fraîcheur."""
    fresh = _build_dataset("fresh", "F", formats=("xml",),
                            last_modified_days_ago=30, org=None, desc_chars=0)
    old = _build_dataset("old", "O", formats=("xml",),
                         last_modified_days_ago=2000, org=None, desc_chars=0)
    s_fresh, _, _ = _score_dataset_v3(fresh, "csv")
    s_old, _, _ = _score_dataset_v3(old, "csv")
    assert s_fresh > s_old


def test_score_v3_org_missing_no_bonus():
    """Sans saturation : fixtures sans CSV pour comparer juste l'organisme."""
    no_org = _build_dataset("no-org", "X", formats=("xml",), org=None,
                             desc_chars=0, last_modified_days_ago=None)
    with_org = _build_dataset("ok", "X", formats=("xml",), org="INSEE",
                               desc_chars=0, last_modified_days_ago=None)
    s_no, _, _ = _score_dataset_v3(no_org, "csv")
    s_yes, _, _ = _score_dataset_v3(with_org, "csv")
    assert s_yes > s_no


def test_score_v3_bounded_0_to_100():
    """Sécurité : pour des inputs absurdes, score reste dans [0, 100]."""
    crazy = {"slug": "x", "resources": [], "organization": None, "description": ""}
    s, _, _ = _score_dataset_v3(crazy, "csv")
    assert 0 <= s <= 100


# ─── handler : score affiché dans sortie ───────────────────────────────


@pytest.mark.asyncio
async def test_search_output_includes_score(ctx):
    datasets = [
        _build_dataset("good", "Bon", formats=("csv",)),
        _build_dataset("bad", "Mauvais", formats=("xml",), nb_res=100),
    ]
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(datasets),
    ):
        result = await datagouv_search_handler(
            ctx, query="x", required_format="csv"
        )
    assert result.success is True
    out = result.output
    # Score /100 affiché
    assert "/100" in out
    # Verdicts présents
    assert "✅" in out or "choisir ce dataset" in out
    assert "⛔" in out or "à éviter" in out
    # Raisons présentes
    assert "raisons" in out.lower()


@pytest.mark.asyncio
async def test_search_ranks_csv_nonmassive_before_massive(ctx):
    """Cible UI : CSV non massif doit passer AVANT massif sans CSV."""
    datasets = [
        _build_dataset("massive-xml", "Massif XML", formats=("xml",), nb_res=200),
        _build_dataset("ok-csv", "OK CSV", formats=("csv",), org="INSEE"),
    ]
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(datasets),
    ):
        result = await datagouv_search_handler(
            ctx, query="x", required_format="csv"
        )
    out = result.output
    assert out.find("ok-csv") < out.find("massive-xml"), (
        "Le dataset CSV non massif doit être listé avant le dataset massif sans CSV"
    )


@pytest.mark.asyncio
async def test_search_empty_dataset_penalized_in_output(ctx):
    datasets = [
        _build_dataset("ok", "OK", formats=("csv",)),
        _build_dataset("empty", "Vide", formats=()),
    ]
    datasets[1]["resources"] = []
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(datasets),
    ):
        result = await datagouv_search_handler(ctx, query="x", required_format="csv")
    out = result.output
    # Le dataset vide doit être après le bon
    assert out.find("ok") < out.find("empty")


@pytest.mark.asyncio
async def test_search_recent_org_favored(ctx):
    # Pour éviter la saturation à 100, on retire le CSV des 2 datasets
    fresh_with_org = _build_dataset(
        "fresh", "Fresh", formats=("xml",), org="INSEE",
        last_modified_days_ago=30, desc_chars=0,
    )
    stale_no_org = _build_dataset(
        "stale", "Stale", formats=("xml",), org=None,
        last_modified_days_ago=2000, desc_chars=0,
    )
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service([stale_no_org, fresh_with_org]),
    ):
        result = await datagouv_search_handler(ctx, query="x", required_format="csv")
    out = result.output
    assert out.find("fresh") < out.find("stale")


@pytest.mark.asyncio
async def test_search_legacy_without_required_format_still_works(ctx):
    """Rétro-compat : sans `required_format`, on a quand même un score."""
    datasets = [
        _build_dataset("a", "A", formats=("csv",)),
        _build_dataset("b", "B", formats=("xml",), nb_res=200),
    ]
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(datasets),
    ):
        result = await datagouv_search_handler(ctx, query="x")
    assert result.success is True
    # Score affiché même sans required_format
    assert "/100" in result.output


# ─── anti-leak V3.2+ ────────────────────────────────────────────────────


def test_no_v3_2_v3_3_v3_4_leak_in_handler_defs():
    """V3.1 ne doit pas exposer SIRENE / geo / join / scoring autonome."""
    names = {d.name for d in get_datagouv_handler_defs()}
    forbidden = {
        "sirene_search_company", "sirene_get_by_siret",
        "geo_search_address", "geo_reverse", "geo_commune_info",
        "data_join", "datagouv_score_results",
    }
    assert names.isdisjoint(forbidden), (
        f"Leak V3.2+ détecté : {names & forbidden}"
    )


def test_description_mentions_score_v3_1():
    defs = {d.name: d for d in get_datagouv_handler_defs()}
    desc = defs["datagouv_search"].description.lower()
    assert "v3.1" in desc or "score" in desc
    # Toujours dissuader browser/web_fetch/http_request
    assert "browser" in desc and "web_fetch" in desc and "http_request" in desc
