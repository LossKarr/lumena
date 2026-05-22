"""Backlog V2.4 (2026-05-19) :
1. Strip BOM UTF-8 sur en-têtes CSV (utf-8-sig détecté auto).
2. Preview Markdown dans ExportResult.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tools.data_workbench import (
    _clean_headers,
    _detect_encoding,
    _strip_bom,
    export_data,
    filter_rows,
    profile_file,
)


# ─── _strip_bom / _clean_headers ────────────────────────────────────────


def test_strip_bom_removes_utf8_bom():
    assert _strip_bom("﻿NUM_CONTRAT") == "NUM_CONTRAT"


def test_strip_bom_keeps_normal_string():
    assert _strip_bom("NUM_CONTRAT") == "NUM_CONTRAT"
    assert _strip_bom("") == ""


def test_clean_headers_strips_bom_and_whitespace():
    out = _clean_headers(["﻿NUM_CONTRAT", "  region ", "OK", ""])
    assert out == ["NUM_CONTRAT", "region", "OK", ""]


# ─── _detect_encoding détecte utf-8-sig ─────────────────────────────────


def test_detect_encoding_utf8_sig(tmp_path):
    f = tmp_path / "bom.csv"
    f.write_bytes(b"\xef\xbb\xbfNUM_CONTRAT;NATURE\n1;Travaux\n")
    enc = _detect_encoding(f)
    assert enc == "utf-8-sig"


# ─── profile : header sans BOM ──────────────────────────────────────────


def test_profile_csv_with_bom_header(tmp_path):
    f = tmp_path / "marches.csv"
    f.write_bytes(b"\xef\xbb\xbfNUM_CONTRAT;NATURE;MONTANT\n"
                  b"M001;Travaux;50000\n"
                  b"M002;Services;30000\n")
    r = profile_file(f)
    assert r.error is None
    cols = [c.name for c in r.columns]
    # NUM_CONTRAT sans BOM
    assert "NUM_CONTRAT" in cols
    assert "﻿NUM_CONTRAT" not in cols


# ─── filter : NUM_CONTRAT accepté sans ﻿ prefix ────────────────────


def test_filter_csv_with_bom_header(tmp_path):
    f = tmp_path / "marches.csv"
    f.write_bytes(b"\xef\xbb\xbfNUM_CONTRAT;NATURE\n"
                  b"M001;Travaux\n"
                  b"M002;Services\n"
                  b"M003;Travaux\n")
    # Sans le fix BOM, ce filtre échoue avec "colonne inconnue NUM_CONTRAT"
    r = filter_rows(
        f, where=[{"col": "NUM_CONTRAT", "op": "==", "value": "M001"}],
    )
    assert r.error is None
    assert r.total_matched == 1


# ─── export : preview présent ───────────────────────────────────────────


def test_export_returns_preview(tmp_path):
    src = Path(__file__).parent.parent / "fixtures" / "data_workbench" / "sample_communes_utf8.csv"
    out = tmp_path / "out.csv"
    r = export_data(
        src, out, output_format="csv",
        where=[{"col": "population_2024", "op": ">", "value": 500000}],
        sort=["-population_2024"],
    )
    assert r.error is None
    assert r.rows_exported == 3
    # Preview présent (≤ 5 lignes, ici 3)
    assert len(r.preview_rows) == 3
    assert r.preview_headers
    # Paris doit être dans le preview (tri desc population)
    assert any("Paris" in c for row in r.preview_rows for c in row)


def test_export_preview_capped_at_5_rows(tmp_path):
    src = Path(__file__).parent.parent / "fixtures" / "data_workbench" / "sample_communes_utf8.csv"
    out = tmp_path / "out.csv"
    r = export_data(src, out, output_format="csv")
    assert r.error is None
    assert r.rows_exported == 10
    # Preview borné à 5 lignes même si export contient 10
    assert len(r.preview_rows) == 5


# ─── export + BOM combiné ───────────────────────────────────────────────


def test_export_pipeline_with_bom_source(tmp_path):
    """End-to-end : source CSV avec BOM → filter → export CSV avec preview."""
    src = tmp_path / "bom_source.csv"
    src.write_bytes(b"\xef\xbb\xbfNUM_CONTRAT;NATURE;MONTANT\n"
                    b"M001;Travaux;50000\n"
                    b"M002;Services;30000\n"
                    b"M003;Travaux;75000\n")
    out = tmp_path / "filtered.csv"
    r = export_data(
        src, out, output_format="csv",
        where=[{"col": "NATURE", "op": "==", "value": "Travaux"}],
    )
    assert r.error is None
    assert r.rows_exported == 2
    # Preview avec headers nettoyés
    assert "NUM_CONTRAT" in r.preview_headers
    assert "﻿NUM_CONTRAT" not in r.preview_headers
