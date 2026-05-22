"""Tests unitaires V2.4 — export_data (CSV/JSON/XLSX) + sidecar provenance."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.tools.data_workbench import (
    FilterError,
    MAX_EXPORT_ROWS,
    export_data,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "data_workbench"


# ─── formats whitelistés ────────────────────────────────────────────────


def test_export_csv_filter(tmp_path):
    out = tmp_path / "out.csv"
    r = export_data(
        FIXTURES / "sample_communes_utf8.csv",
        out,
        output_format="csv",
        where=[{"col": "region", "op": "==", "value": "Île-de-France"}],
    )
    assert r.error is None
    assert r.rows_exported == 1
    assert out.exists()
    with out.open("r", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)
        rows = list(reader)
    assert headers[1] == "commune"
    assert any("Paris" in r for r in rows)


def test_export_json_filter(tmp_path):
    out = tmp_path / "out.json"
    r = export_data(
        FIXTURES / "sample_communes_utf8.csv",
        out,
        output_format="json",
        where=[{"col": "population_2024", "op": ">", "value": 500000}],
    )
    assert r.error is None
    assert r.rows_exported == 3
    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 3


def test_export_xlsx(tmp_path):
    out = tmp_path / "out.xlsx"
    r = export_data(
        FIXTURES / "sample_communes_utf8.csv",
        out,
        output_format="xlsx",
    )
    assert r.error is None
    assert out.exists() and out.stat().st_size > 0


# ─── projection colonnes ────────────────────────────────────────────────


def test_export_with_columns_projection(tmp_path):
    out = tmp_path / "subset.csv"
    r = export_data(
        FIXTURES / "sample_communes_utf8.csv",
        out,
        output_format="csv",
        columns=["commune", "population_2024"],
    )
    assert r.error is None
    with out.open("r", encoding="utf-8") as f:
        first = f.readline().strip()
    assert "commune" in first and "population_2024" in first
    assert "code_insee" not in first


def test_export_projection_unknown_column_raises(tmp_path):
    with pytest.raises(FilterError, match="inconnue"):
        export_data(
            FIXTURES / "sample_communes_utf8.csv",
            tmp_path / "x.csv",
            output_format="csv",
            columns=["ghost_col"],
        )


# ─── pipeline aggregate ─────────────────────────────────────────────────


def test_export_with_aggregate(tmp_path):
    out = tmp_path / "agg.csv"
    r = export_data(
        FIXTURES / "sample_communes_utf8.csv",
        out,
        output_format="csv",
        group_by="region",
        agg="sum",
        agg_col="population_2024",
        sort=["-result"],
        limit=3,
    )
    assert r.error is None
    assert r.rows_exported <= 3
    with out.open("r", encoding="utf-8") as f:
        headers = f.readline().strip().split(",")
    assert "region" in headers
    assert "result" in headers
    assert "_count" in headers


def test_export_aggregate_requires_agg(tmp_path):
    with pytest.raises(FilterError, match="agg"):
        export_data(
            FIXTURES / "sample_communes_utf8.csv",
            tmp_path / "x.csv",
            output_format="csv",
            group_by="region",
            # pas d'agg → doit lever
        )


# ─── format interdit / source manquante ────────────────────────────────


def test_export_forbidden_format_raises(tmp_path):
    with pytest.raises(FilterError, match="interdit"):
        export_data(
            FIXTURES / "sample_communes_utf8.csv",
            tmp_path / "x.parquet",
            output_format="parquet",
        )


def test_export_missing_source_returns_error(tmp_path):
    r = export_data(
        FIXTURES / "does_not_exist.csv",
        tmp_path / "out.csv",
        output_format="csv",
    )
    assert r.error is not None


# ─── sidecar provenance ────────────────────────────────────────────────


def test_export_sidecar_has_all_fields(tmp_path):
    out = tmp_path / "out.csv"
    r = export_data(
        FIXTURES / "sample_communes_utf8.csv",
        out,
        output_format="csv",
        where=[{"col": "region", "op": "==", "value": "Île-de-France"}],
        columns=["commune", "population_2024"],
        sort=["-population_2024"],
        limit=10,
    )
    assert r.error is None
    sidecar = out.with_suffix(".csv.export_meta.json")
    assert sidecar.exists()
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert meta["schema_version"] == 1
    assert meta["output_format"] == "csv"
    assert meta["rows_exported"] == 1
    assert meta["source_md5"]                # hash présent
    assert meta["headers"] == ["commune", "population_2024"]
    ops = meta["operations"]
    assert ops["where"]
    assert ops["columns"] == ["commune", "population_2024"]
    assert ops["sort"]
    assert ops["limit"] == 10
    assert meta["exported_at"]               # ISO timestamp présent


# ─── XLSX / JSON source ────────────────────────────────────────────────


def test_export_from_xlsx_to_csv(tmp_path):
    out = tmp_path / "from_xlsx.csv"
    r = export_data(
        FIXTURES / "sample_marches.xlsx",
        out,
        output_format="csv",
    )
    assert r.error is None
    assert out.exists()


def test_export_from_json_to_csv(tmp_path):
    out = tmp_path / "from_json.csv"
    r = export_data(
        FIXTURES / "sample_data.json",
        out,
        output_format="csv",
    )
    assert r.error is None
    assert out.exists()


# ─── limit / max_export_rows ───────────────────────────────────────────


def test_export_respects_limit(tmp_path):
    out = tmp_path / "limited.csv"
    r = export_data(
        FIXTURES / "sample_communes_utf8.csv",
        out,
        output_format="csv",
        limit=3,
    )
    assert r.error is None
    assert r.rows_exported == 3
    with out.open("r", encoding="utf-8") as f:
        # 1 header + 3 lignes
        assert sum(1 for _ in f) == 4
