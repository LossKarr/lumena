"""Tests unitaires src/tools/data_workbench.py — filter/sort/limit (V2.2).

Pas d'aggregate/export ici (V2.3/V2.4).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tools.data_workbench import (
    DEFAULT_FILTER_LIMIT,
    FilterError,
    MAX_FILTER_LIMIT,
    filter_rows,
)


FIXTURES = Path(__file__).parent.parent / "fixtures" / "data_workbench"


# ─── filtres texte ─────────────────────────────────────────────────────


def test_filter_eq_text():
    r = filter_rows(
        FIXTURES / "sample_communes_utf8.csv",
        where=[{"col": "region", "op": "==", "value": "Île-de-France"}],
    )
    assert r.error is None
    assert r.total_matched == 1
    # Paris est l'unique commune IDF dans la fixture
    paris_row = r.rows[0]
    assert "Paris" in paris_row


def test_filter_neq_text():
    r = filter_rows(
        FIXTURES / "sample_communes_utf8.csv",
        where=[{"col": "region", "op": "!=", "value": "Île-de-France"}],
    )
    assert r.error is None
    assert r.total_matched == 9  # 10 lignes - 1 IDF


def test_filter_contains_case_insensitive():
    r = filter_rows(
        FIXTURES / "sample_communes_utf8.csv",
        where=[{"col": "commune", "op": "contains", "value": "lyon"}],
    )
    assert r.error is None
    assert r.total_matched == 1


def test_filter_startswith():
    r = filter_rows(
        FIXTURES / "sample_communes_utf8.csv",
        where=[{"col": "commune", "op": "startswith", "value": "N"}],
    )
    # Nice + Nantes
    assert r.total_matched == 2


def test_filter_in_list():
    r = filter_rows(
        FIXTURES / "sample_communes_utf8.csv",
        where=[{"col": "region", "op": "in",
                "value": ["Île-de-France", "Occitanie"]}],
    )
    assert r.total_matched == 2  # Paris + Toulouse


def test_filter_not_in_list():
    r = filter_rows(
        FIXTURES / "sample_communes_utf8.csv",
        where=[{"col": "region", "op": "not_in",
                "value": ["Île-de-France"]}],
    )
    assert r.total_matched == 9


# ─── filtres numériques ────────────────────────────────────────────────


def test_filter_gt_numeric():
    r = filter_rows(
        FIXTURES / "sample_communes_utf8.csv",
        where=[{"col": "population_2024", "op": ">", "value": 500000}],
    )
    # Paris, Marseille, Lyon (> 500k)
    assert r.total_matched == 3


def test_filter_lt_numeric():
    r = filter_rows(
        FIXTURES / "sample_communes_utf8.csv",
        where=[{"col": "population_2024", "op": "<", "value": 1000}],
    )
    # L'Abergement-Clémenciat 833, L'Abergement-de-Varey 255
    assert r.total_matched == 2


def test_filter_ge_numeric():
    r = filter_rows(
        FIXTURES / "sample_communes_utf8.csv",
        where=[{"col": "population_2024", "op": ">=", "value": 873076}],
    )
    # Paris + Marseille >= 873076
    assert r.total_matched == 2


# ─── filtres date (ISO triable lex) ────────────────────────────────────


def test_filter_date_iso_ge():
    r = filter_rows(
        FIXTURES / "sample_communes_utf8.csv",
        where=[{"col": "date_recensement", "op": ">=", "value": "2024-01-01"}],
    )
    # Toutes les lignes ont date_recensement = 2024-01-01 dans la fixture
    assert r.total_matched == 10


# ─── conditions multiples (ET logique) ────────────────────────────────


def test_filter_multiple_conditions_and():
    r = filter_rows(
        FIXTURES / "sample_communes_utf8.csv",
        where=[
            {"col": "region", "op": "==", "value": "Auvergne-Rhône-Alpes"},
            {"col": "population_2024", "op": ">", "value": 200000},
        ],
    )
    # Lyon (522250) est seul AURA > 200k
    assert r.total_matched == 1
    assert "Lyon" in r.rows[0]


# ─── sort ──────────────────────────────────────────────────────────────


def test_sort_desc_numeric():
    r = filter_rows(
        FIXTURES / "sample_communes_utf8.csv",
        sort=["-population_2024"],
    )
    # Paris (2102650) doit être en tête
    assert "Paris" in r.rows[0]


def test_sort_asc_text():
    r = filter_rows(
        FIXTURES / "sample_communes_utf8.csv",
        sort=["commune"],
    )
    # B < L < M : Bordeaux doit être en tête alphabétique
    assert r.rows[0][1] == "Bordeaux"
    # Et Toulouse en queue
    assert r.rows[-1][1] == "Toulouse"


def test_sort_object_form_desc():
    r = filter_rows(
        FIXTURES / "sample_communes_utf8.csv",
        sort=[{"col": "population_2024", "order": "desc"}],
    )
    assert "Paris" in r.rows[0]


# ─── limit ─────────────────────────────────────────────────────────────


def test_default_limit():
    r = filter_rows(FIXTURES / "sample_communes_utf8.csv")
    assert len(r.rows) <= DEFAULT_FILTER_LIMIT
    assert r.total_matched == 10  # fichier de 10 lignes


def test_limit_truncates():
    r = filter_rows(FIXTURES / "sample_communes_utf8.csv", limit=3)
    assert len(r.rows) == 3
    assert r.total_matched == 10
    assert r.truncated_at_limit is True


def test_limit_clamped_to_max():
    r = filter_rows(FIXTURES / "sample_communes_utf8.csv", limit=99999)
    assert len(r.rows) == 10  # mais pas plus


# ─── erreurs : colonne inconnue, op interdit ──────────────────────────


def test_unknown_column_raises_filter_error():
    with pytest.raises(FilterError) as exc:
        filter_rows(
            FIXTURES / "sample_communes_utf8.csv",
            where=[{"col": "ghost_column", "op": "==", "value": "x"}],
        )
    msg = str(exc.value)
    assert "Colonne inconnue" in msg
    assert "ghost_column" in msg


def test_forbidden_operator_raises_filter_error():
    with pytest.raises(FilterError) as exc:
        filter_rows(
            FIXTURES / "sample_communes_utf8.csv",
            where=[{"col": "commune", "op": "DROP TABLE", "value": "x"}],
        )
    msg = str(exc.value)
    assert "interdit" in msg.lower() or "whitelist" in msg.lower()


def test_eval_operator_forbidden():
    with pytest.raises(FilterError):
        filter_rows(
            FIXTURES / "sample_communes_utf8.csv",
            where=[{"col": "commune", "op": "eval", "value": "exec(...)"}],
        )


def test_missing_value_raises_filter_error():
    with pytest.raises(FilterError):
        filter_rows(
            FIXTURES / "sample_communes_utf8.csv",
            where=[{"col": "commune", "op": "=="}],
        )


def test_unknown_sort_column_raises_filter_error():
    with pytest.raises(FilterError):
        filter_rows(
            FIXTURES / "sample_communes_utf8.csv",
            sort=["-ghost_col"],
        )


# ─── fichier inexistant / format non supporté ─────────────────────────


def test_missing_file_returns_error():
    r = filter_rows(FIXTURES / "does_not_exist.csv")
    assert r.error is not None
    assert "introuvable" in r.error


def test_xls_legacy_refused(tmp_path):
    fake = tmp_path / "old.xls"
    fake.write_bytes(b"fake")
    r = filter_rows(fake)
    assert r.error is not None


# ─── XLSX + JSON ───────────────────────────────────────────────────────


def test_filter_on_xlsx():
    r = filter_rows(
        FIXTURES / "sample_marches.xlsx",
        where=[{"col": "objet", "op": "contains", "value": "Logiciel"}],
    )
    assert r.error is None
    assert r.total_matched == 1


def test_filter_on_json():
    r = filter_rows(
        FIXTURES / "sample_data.json",
        where=[{"col": "code_insee", "op": "==", "value": "02"}],
    )
    assert r.error is None
    assert r.total_matched == 1
