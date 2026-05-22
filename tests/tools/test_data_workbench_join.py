"""Tests unitaires V3.4 — data_join (inner/left/right/outer)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.tools.data_workbench import (
    DEFAULT_JOIN_LIMIT,
    FilterError,
    MAX_JOIN_OUTPUT_ROWS,
    data_join,
)


# ─── helpers fixtures ──────────────────────────────────────────────────


def _write_csv(path: Path, headers, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for r in rows:
            w.writerow(r)


@pytest.fixture
def communes_csv(tmp_path):
    p = tmp_path / "communes.csv"
    _write_csv(p, ["code_insee", "commune", "population"], [
        ["75056", "Paris", "2102650"],
        ["13055", "Marseille", "873076"],
        ["69123", "Lyon", "522250"],
        ["44109", "Nantes", "320732"],
    ])
    return p


@pytest.fixture
def marches_csv(tmp_path):
    p = tmp_path / "marches.csv"
    _write_csv(p, ["code_insee", "marche", "montant"], [
        ["75056", "M001", "50000"],
        ["75056", "M002", "30000"],
        ["13055", "M003", "12000"],
        ["99999", "M004", "9999"],  # commune absente de communes.csv
    ])
    return p


# ─── inner join ────────────────────────────────────────────────────────


def test_inner_join_basic(communes_csv, marches_csv):
    r = data_join(
        communes_csv, marches_csv,
        on_left="code_insee", how="inner",
    )
    assert r.error is None
    # 3 lignes joined : 75056×2 + 13055×1
    assert r.total_joined == 3
    # Pas de M004 (99999 absent du left)
    flat = " ".join(c for row in r.rows for c in row)
    assert "M004" not in flat


def test_inner_join_columns_no_dup_key(communes_csv, marches_csv):
    r = data_join(communes_csv, marches_csv, on_left="code_insee", how="inner")
    # code_insee une seule fois, pas de code_insee_right
    assert "code_insee" in r.columns
    assert "code_insee_right" not in r.columns


def test_inner_join_no_match_empty(tmp_path, communes_csv):
    other = tmp_path / "other.csv"
    _write_csv(other, ["code_insee", "x"], [["00000", "z"]])
    r = data_join(communes_csv, other, on_left="code_insee", how="inner")
    assert r.total_joined == 0
    assert r.rows == []


# ─── left / right / outer ──────────────────────────────────────────────


def test_left_join_keeps_unmatched_left(communes_csv, marches_csv):
    r = data_join(communes_csv, marches_csv, on_left="code_insee", how="left")
    # 4 communes : Paris×2, Marseille×1, Lyon×1 unmatched, Nantes×1 unmatched = 5
    assert r.total_joined == 5


def test_right_join_keeps_unmatched_right(communes_csv, marches_csv):
    r = data_join(communes_csv, marches_csv, on_left="code_insee", how="right")
    # 4 marchés : 3 matchés + M004 (99999) unmatched
    # right pur émet toutes les lignes droite
    assert r.total_joined >= 4


def test_outer_join_keeps_all(communes_csv, marches_csv):
    r = data_join(communes_csv, marches_csv, on_left="code_insee", how="outer")
    # 3 matchés + 2 left unmatched (Lyon, Nantes) + 1 right unmatched (M004) = 6
    assert r.total_joined == 6


# ─── colonnes différentes left vs right ────────────────────────────────


def test_join_with_different_key_names(tmp_path):
    left = tmp_path / "left.csv"
    right = tmp_path / "right.csv"
    _write_csv(left, ["insee", "nom"], [["75056", "Paris"]])
    _write_csv(right, ["code_commune", "data"], [["75056", "X"]])
    r = data_join(left, right, on_left="insee", on_right="code_commune", how="inner")
    assert r.total_joined == 1
    # Les 2 clés sont conservées car noms différents
    assert "insee" in r.columns
    assert "code_commune" in r.columns


def test_join_column_collision_suffixed(tmp_path):
    left = tmp_path / "left.csv"
    right = tmp_path / "right.csv"
    _write_csv(left, ["id", "nom"], [["1", "A"]])
    _write_csv(right, ["id", "nom"], [["1", "B"]])
    r = data_join(left, right, on_left="id", how="inner")
    # 'nom' collide → nom_right doit apparaître
    assert "nom" in r.columns
    assert "nom_right" in r.columns
    # Ligne : id=1, nom=A, nom_right=B
    row = r.rows[0]
    by_col = dict(zip(r.columns, row))
    assert by_col["nom"] == "A"
    assert by_col["nom_right"] == "B"


# ─── erreurs whitelist / colonne inconnue ──────────────────────────────


def test_invalid_how_raises(communes_csv, marches_csv):
    with pytest.raises(FilterError, match="interdit"):
        data_join(
            communes_csv, marches_csv,
            on_left="code_insee", how="cross",
        )


def test_unknown_on_left_raises(communes_csv, marches_csv):
    with pytest.raises(FilterError, match="absente du fichier gauche"):
        data_join(
            communes_csv, marches_csv,
            on_left="ghost_col", how="inner",
        )


def test_unknown_on_right_raises(communes_csv, marches_csv):
    with pytest.raises(FilterError, match="absente du fichier droit"):
        data_join(
            communes_csv, marches_csv,
            on_left="code_insee", on_right="ghost", how="inner",
        )


# ─── limit / fichier introuvable ───────────────────────────────────────


def test_limit_truncates_output(communes_csv, marches_csv):
    r = data_join(
        communes_csv, marches_csv,
        on_left="code_insee", how="outer", limit=2,
    )
    assert len(r.rows) == 2
    assert r.truncated_at_output is True


def test_missing_left_file_returns_error(tmp_path, marches_csv):
    r = data_join(
        tmp_path / "ghost.csv", marches_csv,
        on_left="code_insee",
    )
    assert r.error is not None
    assert "left" in r.error.lower()


def test_missing_right_file_returns_error(tmp_path, communes_csv):
    r = data_join(
        communes_csv, tmp_path / "ghost.csv",
        on_left="code_insee",
    )
    assert r.error is not None
    assert "right" in r.error.lower()


# ─── XLSX / JSON cross-format ──────────────────────────────────────────


def test_join_csv_to_json(tmp_path, communes_csv):
    right_json = tmp_path / "right.json"
    right_json.write_text(json.dumps({
        "data": [
            {"code_insee": "75056", "label": "Capitale"},
            {"code_insee": "13055", "label": "Phocéenne"},
        ]
    }), encoding="utf-8")
    r = data_join(communes_csv, right_json, on_left="code_insee", how="inner")
    assert r.error is None
    assert r.total_joined == 2
