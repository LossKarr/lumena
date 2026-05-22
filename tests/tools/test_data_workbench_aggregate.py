"""Tests unitaires V2.3 — aggregate_data + unique_values."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tools.data_workbench import (
    DEFAULT_GROUPS,
    DEFAULT_UNIQUE_LIMIT,
    FilterError,
    aggregate_data,
    unique_values,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "data_workbench"


# ─── aggregate : agrégations whitelistées ───────────────────────────────


def test_aggregate_count_by_region():
    r = aggregate_data(
        FIXTURES / "sample_communes_utf8.csv",
        group_by="region",
        agg="count",
    )
    assert r.error is None
    # 10 communes réparties en plusieurs régions
    by_group = {tuple(row[c] for c in r.group_by): row["result"] for row in r.rows}
    # Auvergne-Rhône-Alpes = 3 (L'Abergement x2, Lyon)
    assert by_group[("Auvergne-Rhône-Alpes",)] == 3
    # Île-de-France = 1 (Paris)
    assert by_group[("Île-de-France",)] == 1


def test_aggregate_sum_population_by_region():
    r = aggregate_data(
        FIXTURES / "sample_communes_utf8.csv",
        group_by="region",
        agg="sum",
        agg_col="population_2024",
    )
    assert r.error is None
    by_group = {tuple(row[c] for c in r.group_by): row["result"] for row in r.rows}
    # PACA = Marseille (873076) + Nice (348085) = 1221161
    assert by_group[("Provence-Alpes-Côte d'Azur",)] == pytest.approx(1221161)


def test_aggregate_mean_population_by_region():
    r = aggregate_data(
        FIXTURES / "sample_communes_utf8.csv",
        group_by="region",
        agg="mean",
        agg_col="population_2024",
    )
    assert r.error is None
    by_group = {tuple(row[c] for c in r.group_by): row["result"] for row in r.rows}
    # Île-de-France = Paris uniquement
    assert by_group[("Île-de-France",)] == pytest.approx(2102650)


def test_aggregate_avg_alias_works():
    r = aggregate_data(
        FIXTURES / "sample_communes_utf8.csv",
        group_by="region", agg="avg", agg_col="population_2024",
    )
    assert r.error is None
    assert r.agg == "mean"


def test_aggregate_min_max_median():
    for agg in ("min", "max", "median"):
        r = aggregate_data(
            FIXTURES / "sample_communes_utf8.csv",
            group_by="region", agg=agg, agg_col="population_2024",
        )
        assert r.error is None, f"agg={agg} failed: {r.error}"
        assert len(r.rows) > 0


# ─── aggregate : pré-filtre + sort + limit ──────────────────────────────


def test_aggregate_with_where_prefilter():
    r = aggregate_data(
        FIXTURES / "sample_communes_utf8.csv",
        group_by="region", agg="count",
        where=[{"col": "population_2024", "op": ">", "value": 500000}],
    )
    by_group = {tuple(row[c] for c in r.group_by): row["result"] for row in r.rows}
    # Seules Paris, Marseille, Lyon > 500k → 3 régions distinctes
    assert sum(by_group.values()) == 3


def test_aggregate_sort_by_result_desc():
    r = aggregate_data(
        FIXTURES / "sample_communes_utf8.csv",
        group_by="region", agg="sum", agg_col="population_2024",
        sort=["-result"],
    )
    # IDF en tête (Paris seul = 2.1M)
    assert r.rows[0][r.group_by[0]] == "Île-de-France"


def test_aggregate_limit_truncates():
    r = aggregate_data(
        FIXTURES / "sample_communes_utf8.csv",
        group_by="region", agg="count", limit=2,
    )
    assert len(r.rows) == 2
    assert r.truncated_at_limit is True


# ─── aggregate : group_by multi-colonnes ────────────────────────────────


def test_aggregate_group_by_two_columns():
    r = aggregate_data(
        FIXTURES / "sample_communes_utf8.csv",
        group_by=["region", "departement"],
        agg="count",
    )
    assert r.error is None
    assert len(r.group_by) == 2


def test_aggregate_group_by_too_many_columns_refused():
    with pytest.raises(FilterError, match="3 colonnes"):
        aggregate_data(
            FIXTURES / "sample_communes_utf8.csv",
            group_by=["region", "departement", "commune", "code_insee"],
            agg="count",
        )


# ─── aggregate : erreurs whitelist / colonnes ──────────────────────────


def test_aggregate_forbidden_agg_raises():
    with pytest.raises(FilterError, match="interdite"):
        aggregate_data(
            FIXTURES / "sample_communes_utf8.csv",
            group_by="region", agg="exec",
        )


def test_aggregate_missing_agg_col_for_sum_raises():
    with pytest.raises(FilterError, match="agg_col"):
        aggregate_data(
            FIXTURES / "sample_communes_utf8.csv",
            group_by="region", agg="sum",
        )


def test_aggregate_unknown_group_column_raises():
    with pytest.raises(FilterError, match="inconnue"):
        aggregate_data(
            FIXTURES / "sample_communes_utf8.csv",
            group_by="ghost_col", agg="count",
        )


def test_aggregate_unknown_agg_column_raises():
    with pytest.raises(FilterError, match="inconnue"):
        aggregate_data(
            FIXTURES / "sample_communes_utf8.csv",
            group_by="region", agg="sum", agg_col="ghost_num",
        )


def test_aggregate_empty_group_by_raises():
    with pytest.raises(FilterError, match="vide"):
        aggregate_data(
            FIXTURES / "sample_communes_utf8.csv",
            group_by=[], agg="count",
        )


# ─── aggregate : fichier ────────────────────────────────────────────────


def test_aggregate_missing_file():
    r = aggregate_data(
        FIXTURES / "does_not_exist.csv",
        group_by="region", agg="count",
    )
    assert r.error is not None


def test_aggregate_on_xlsx():
    r = aggregate_data(
        FIXTURES / "sample_marches.xlsx",
        group_by="siret_acheteur", agg="sum", agg_col="montant",
    )
    assert r.error is None
    assert len(r.rows) >= 1


def test_aggregate_on_json():
    r = aggregate_data(
        FIXTURES / "sample_data.json",
        group_by="code_insee", agg="count",
    )
    assert r.error is None
    assert len(r.rows) == 4


# ─── unique_values ──────────────────────────────────────────────────────


def test_unique_values_basic():
    r = unique_values(
        FIXTURES / "sample_communes_utf8.csv",
        column="region",
    )
    assert r.error is None
    # 7 régions distinctes dans la fixture (AURA, PACA, IDF, Occitanie, PdL, Grand Est, N-Aquitaine)
    assert r.total_unique == 7
    # Tri fréquence décroissante : Auvergne-Rhône-Alpes (3) en tête
    assert r.values[0][0] == "Auvergne-Rhône-Alpes"
    assert r.values[0][1] == 3


def test_unique_values_limit_truncates():
    r = unique_values(
        FIXTURES / "sample_communes_utf8.csv",
        column="region", limit=2,
    )
    assert len(r.values) == 2
    assert r.truncated_at_limit is True


def test_unique_values_unknown_column_raises():
    with pytest.raises(FilterError, match="inconnue"):
        unique_values(
            FIXTURES / "sample_communes_utf8.csv",
            column="ghost_col",
        )


def test_unique_values_missing_file():
    r = unique_values(
        FIXTURES / "does_not_exist.csv",
        column="x",
    )
    assert r.error is not None


def test_unique_values_on_xlsx():
    r = unique_values(
        FIXTURES / "sample_marches.xlsx",
        column="siret_acheteur",
    )
    assert r.error is None
    # 3 sirets distincts dans la fixture (12345678901234 apparaît 2x)
    assert r.total_unique == 3
    # Le plus fréquent (12345678901234) en tête
    assert r.values[0][1] == 2
