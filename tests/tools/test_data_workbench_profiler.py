"""Tests unitaires src/tools/data_workbench.py — profilage CSV/XLSX/JSON.

V2.1 : profilage seul. Pas de filter/aggregate/export (V2.2+).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tools.data_workbench import (
    ProfileResult,
    _detect_encoding,
    _detect_separator,
    _infer_dtype,
    _is_territory_column,
    profile_file,
)


FIXTURES = Path(__file__).parent.parent / "fixtures" / "data_workbench"


# ─── détection encodage / séparateur ────────────────────────────────────


def test_detect_encoding_utf8():
    assert _detect_encoding(FIXTURES / "sample_communes_utf8.csv") == "utf-8"


def test_detect_encoding_latin1_fallback():
    # Le fichier latin-1 n'est PAS un utf-8 valide → fallback latin-1
    enc = _detect_encoding(FIXTURES / "sample_communes_latin1.csv")
    assert enc in ("latin-1", "cp1252")


def test_detect_separator_semicolon():
    sep = _detect_separator(FIXTURES / "sample_communes_utf8.csv", "utf-8")
    assert sep == ";"


# ─── inférence dtype ─────────────────────────────────────────────────────


def test_infer_dtype_int():
    assert _infer_dtype(["1", "2", "3", "100"]) == "int"


def test_infer_dtype_float():
    assert _infer_dtype(["1.5", "2.3", "100.0"]) == "float"


def test_infer_dtype_date_iso():
    assert _infer_dtype(["2024-01-15", "2024-02-15"]) == "date"


def test_infer_dtype_date_fr():
    assert _infer_dtype(["15/01/2024", "20/02/2024"]) == "date"


def test_infer_dtype_text_fallback():
    assert _infer_dtype(["Paris", "Lyon", "Marseille"]) == "text"


def test_infer_dtype_empty():
    assert _infer_dtype(["", "", ""]) == "text"


# ─── détection territoire ────────────────────────────────────────────────


@pytest.mark.parametrize("name", [
    "code_insee", "commune", "departement", "région", "siren", "siret",
    "code_postal", "INSEE", "Code_Departement",
])
def test_is_territory_column_positive(name):
    assert _is_territory_column(name) is True


@pytest.mark.parametrize("name", ["population", "valeur", "objet", "id"])
def test_is_territory_column_negative(name):
    assert _is_territory_column(name) is False


# ─── profile_file CSV ────────────────────────────────────────────────────


def test_profile_csv_utf8_semicolon():
    r = profile_file(FIXTURES / "sample_communes_utf8.csv")
    assert r.error is None
    assert r.format == "csv"
    assert r.rows == 10
    assert r.cols == 6
    assert r.encoding_used == "utf-8"
    assert r.separator_used == ";"
    # Colonnes
    by_name = {c.name: c for c in r.columns}
    assert by_name["code_insee"].is_territory is True
    assert by_name["commune"].is_territory is True
    assert by_name["region"].is_territory is True
    assert by_name["population_2024"].is_numeric_probable is True
    assert by_name["date_recensement"].is_date_probable is True
    # 5 exemples
    assert len(r.sample_rows) == 5


def test_profile_csv_latin1():
    r = profile_file(FIXTURES / "sample_communes_latin1.csv")
    assert r.error is None
    assert r.format == "csv"
    assert r.encoding_used in ("latin-1", "cp1252")
    assert r.rows >= 3
    # Accents préservés (Côte-d-Or)
    found_accent = any(
        any(ch in v for ch in "éèôîçâ") for row in r.sample_rows for v in row.values()
    )
    assert found_accent, "Accent latin-1 mal décodé"


# ─── profile_file XLSX ──────────────────────────────────────────────────


def test_profile_xlsx():
    r = profile_file(FIXTURES / "sample_marches.xlsx")
    assert r.error is None
    assert r.format == "xlsx"
    assert r.rows == 4
    assert r.cols == 5
    by_name = {c.name: c for c in r.columns}
    assert by_name["siret_acheteur"].is_territory is True
    # 1 valeur null sur 4 → 25%
    assert by_name["montant"].null_count == 1


# ─── profile_file JSON ──────────────────────────────────────────────────


def test_profile_json_with_data_key():
    r = profile_file(FIXTURES / "sample_data.json")
    assert r.error is None
    assert r.format == "json"
    assert r.rows == 4
    by_name = {c.name: c for c in r.columns}
    assert "code_insee" in by_name
    assert by_name["code_insee"].is_territory is True


# ─── erreurs / formats non supportés ────────────────────────────────────


def test_profile_missing_file():
    r = profile_file(FIXTURES / "does_not_exist.csv")
    assert r.error is not None
    assert "introuvable" in r.error


def test_profile_xls_legacy_refused(tmp_path):
    fake_xls = tmp_path / "legacy.xls"
    fake_xls.write_bytes(b"fake")
    r = profile_file(fake_xls)
    assert r.error is not None
    assert ".xls" in r.error
    assert "V2.1" in r.error


def test_profile_unsupported_format(tmp_path):
    fake = tmp_path / "data.parquet"
    fake.write_bytes(b"fake")
    r = profile_file(fake)
    assert r.error is not None
    assert "parquet" in r.error.lower()


# ─── max_rows truncation ────────────────────────────────────────────────


def test_max_rows_truncates_csv():
    r = profile_file(FIXTURES / "sample_communes_utf8.csv", max_rows=3)
    assert r.rows == 3
    assert r.truncated is True
    assert any("Tronqué" in lim for lim in r.limits)


# ─── provenance sidecar ─────────────────────────────────────────────────


def test_provenance_loaded_when_sidecar_present(tmp_path):
    # Copier le csv + ajouter sidecar
    src = FIXTURES / "sample_communes_utf8.csv"
    dst = tmp_path / "data.csv"
    dst.write_bytes(src.read_bytes())
    sidecar = dst.with_suffix(".csv.datagouv.json")
    sidecar.write_text(
        json.dumps({
            "schema_version": 1,
            "resource_url": "https://www.data.gouv.fr/api/1/datasets/r/abc",
            "resource_id": "abc",
            "md5": "deadbeef",
            "downloaded_at": "2026-05-19T10:00:00+00:00",
        }),
        encoding="utf-8",
    )
    r = profile_file(dst)
    assert r.provenance is not None
    assert r.provenance["resource_id"] == "abc"
    assert r.provenance["md5"] == "deadbeef"


def test_provenance_none_when_no_sidecar(tmp_path):
    src = FIXTURES / "sample_communes_utf8.csv"
    dst = tmp_path / "data.csv"
    dst.write_bytes(src.read_bytes())
    r = profile_file(dst)
    assert r.provenance is None


# ─── return type ─────────────────────────────────────────────────────────


def test_profile_result_is_dataclass():
    r = profile_file(FIXTURES / "sample_communes_utf8.csv")
    assert isinstance(r, ProfileResult)
