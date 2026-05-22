"""Tests handler data_profile_file — V2.1.

Pas de filter/aggregate/export ici (V2.2+).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.data_workbench import (
    data_aggregate_handler,
    data_export_handler,
    data_filter_rows_handler,
    data_join_handler,
    data_profile_file_handler,
    data_unique_values_handler,
    get_data_workbench_handler_defs,
)


FIXTURES = Path(__file__).parent.parent / "fixtures" / "data_workbench"


@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return HandlerContext.for_testing(
        lumena_root=tmp_path, runtime_root=workspace
    )


# ─── happy path ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_profile_csv_returns_ok(ctx, tmp_path):
    dst = Path(ctx.runtime_root) / "communes.csv"
    dst.write_bytes((FIXTURES / "sample_communes_utf8.csv").read_bytes())
    result = await data_profile_file_handler(ctx, path="communes.csv")
    assert isinstance(result, HandlerResult)
    assert result.success is True
    out = result.output
    assert "communes.csv" in out
    assert "Colonnes" in out
    assert "code_insee" in out
    assert "territoire" in out
    assert "Lignes : 10" in out


@pytest.mark.asyncio
async def test_profile_xlsx(ctx):
    dst = Path(ctx.runtime_root) / "marches.xlsx"
    dst.write_bytes((FIXTURES / "sample_marches.xlsx").read_bytes())
    result = await data_profile_file_handler(ctx, path="marches.xlsx")
    assert result.success is True
    assert "siret_acheteur" in result.output
    assert "Lignes : 4" in result.output


@pytest.mark.asyncio
async def test_profile_json(ctx):
    dst = Path(ctx.runtime_root) / "data.json"
    dst.write_bytes((FIXTURES / "sample_data.json").read_bytes())
    result = await data_profile_file_handler(ctx, path="data.json")
    assert result.success is True
    assert "code_insee" in result.output
    assert "Lignes : 4" in result.output


@pytest.mark.asyncio
async def test_profile_accepts_absolute_path(ctx):
    dst = Path(ctx.runtime_root) / "communes.csv"
    dst.write_bytes((FIXTURES / "sample_communes_utf8.csv").read_bytes())
    result = await data_profile_file_handler(ctx, path=str(dst.resolve()))
    assert result.success is True


# ─── erreurs ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_profile_missing_file_returns_fail(ctx):
    result = await data_profile_file_handler(ctx, path="ghost.csv")
    assert result.success is False
    assert "introuvable" in result.output.lower()


@pytest.mark.asyncio
async def test_profile_xls_legacy_returns_fail(ctx):
    dst = Path(ctx.runtime_root) / "old.xls"
    dst.write_bytes(b"fake xls")
    result = await data_profile_file_handler(ctx, path="old.xls")
    assert result.success is False
    assert ".xls" in result.output


# ─── provenance sidecar ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_profile_displays_provenance_when_present(ctx):
    dst = Path(ctx.runtime_root) / "communes.csv"
    dst.write_bytes((FIXTURES / "sample_communes_utf8.csv").read_bytes())
    sidecar = dst.with_suffix(".csv.datagouv.json")
    sidecar.write_text(
        json.dumps({
            "schema_version": 1,
            "resource_url": "https://www.data.gouv.fr/api/1/datasets/r/uuid-1234",
            "resource_id": "uuid-1234",
            "md5": "abc123",
            "downloaded_at": "2026-05-19T10:00:00+00:00",
        }),
        encoding="utf-8",
    )
    result = await data_profile_file_handler(ctx, path="communes.csv")
    assert result.success is True
    assert "uuid-1234" in result.output
    assert "abc123" in result.output


# ─── HandlerDef format ──────────────────────────────────────────────────


# ─── data_filter_rows handler tests ─────────────────────────────────────


def _prep_csv(ctx):
    dst = Path(ctx.runtime_root) / "communes.csv"
    dst.write_bytes((FIXTURES / "sample_communes_utf8.csv").read_bytes())
    return dst


@pytest.mark.asyncio
async def test_filter_handler_eq_text(ctx):
    _prep_csv(ctx)
    result = await data_filter_rows_handler(
        ctx, path="communes.csv",
        where=[{"col": "region", "op": "==", "value": "Île-de-France"}],
    )
    assert result.success is True
    assert "Paris" in result.output
    assert "Matched : 1" in result.output


@pytest.mark.asyncio
async def test_filter_handler_gt_with_sort_and_limit(ctx):
    _prep_csv(ctx)
    result = await data_filter_rows_handler(
        ctx, path="communes.csv",
        where=[{"col": "population_2024", "op": ">", "value": 200000}],
        sort=["-population_2024"],
        limit=3,
    )
    assert result.success is True
    out = result.output
    # Paris (2,1M) doit apparaître avant Marseille (873k)
    assert out.find("Paris") < out.find("Marseille")
    assert "Matched : " in out


@pytest.mark.asyncio
async def test_filter_handler_dict_where_normalized(ctx):
    """Un seul dict where doit être normalisé en liste."""
    _prep_csv(ctx)
    result = await data_filter_rows_handler(
        ctx, path="communes.csv",
        where={"col": "region", "op": "==", "value": "Île-de-France"},
    )
    assert result.success is True
    assert "Paris" in result.output


@pytest.mark.asyncio
async def test_filter_handler_unknown_column_fails_cleanly(ctx):
    _prep_csv(ctx)
    result = await data_filter_rows_handler(
        ctx, path="communes.csv",
        where=[{"col": "ghost", "op": "==", "value": "x"}],
    )
    assert result.success is False
    assert "inconnue" in result.output.lower() or "ghost" in result.output


@pytest.mark.asyncio
async def test_filter_handler_forbidden_op_fails_cleanly(ctx):
    _prep_csv(ctx)
    result = await data_filter_rows_handler(
        ctx, path="communes.csv",
        where=[{"col": "commune", "op": "DROP", "value": "x"}],
    )
    assert result.success is False
    assert "interdit" in result.output.lower() or "whitelist" in result.output.lower()


@pytest.mark.asyncio
async def test_filter_handler_missing_file_fails(ctx):
    result = await data_filter_rows_handler(
        ctx, path="ghost.csv",
        where=[{"col": "x", "op": "==", "value": "y"}],
    )
    assert result.success is False


# ─── data_join handler tests (V3.4) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_join_handler_inner_basic(ctx):
    """End-to-end : 2 CSV avec code_insee commun → inner join OK."""
    import csv as _csv
    left = Path(ctx.runtime_root) / "communes.csv"
    right = Path(ctx.runtime_root) / "marches.csv"
    with left.open("w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["code_insee", "commune"])
        w.writerow(["75056", "Paris"])
        w.writerow(["13055", "Marseille"])
    with right.open("w", encoding="utf-8", newline="") as f:
        w = _csv.writer(f)
        w.writerow(["code_insee", "marche"])
        w.writerow(["75056", "M001"])
        w.writerow(["75056", "M002"])
    result = await data_join_handler(
        ctx, left_path="communes.csv", right_path="marches.csv",
        on_left="code_insee", how="inner",
    )
    assert result.success is True
    out = result.output
    assert "Aperçu" in out
    assert "75056" in out
    assert "M001" in out and "M002" in out


@pytest.mark.asyncio
async def test_join_handler_unknown_key_fails(ctx):
    import csv as _csv
    left = Path(ctx.runtime_root) / "l.csv"
    right = Path(ctx.runtime_root) / "r.csv"
    for p in (left, right):
        with p.open("w", encoding="utf-8", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["a", "b"])
            w.writerow(["1", "2"])
    result = await data_join_handler(
        ctx, left_path="l.csv", right_path="r.csv",
        on_left="ghost",
    )
    assert result.success is False
    assert "absente" in result.output.lower() or "ghost" in result.output.lower()


@pytest.mark.asyncio
async def test_join_handler_forbidden_how(ctx):
    import csv as _csv
    left = Path(ctx.runtime_root) / "l.csv"
    right = Path(ctx.runtime_root) / "r.csv"
    for p in (left, right):
        with p.open("w", encoding="utf-8", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["a"])
            w.writerow(["1"])
    result = await data_join_handler(
        ctx, left_path="l.csv", right_path="r.csv",
        on_left="a", how="cross",
    )
    assert result.success is False
    assert "interdit" in result.output.lower()


@pytest.mark.asyncio
async def test_join_handler_missing_files(ctx):
    result = await data_join_handler(
        ctx, left_path="ghost1.csv", right_path="ghost2.csv",
        on_left="x",
    )
    assert result.success is False


@pytest.mark.asyncio
async def test_join_handler_does_not_call_datagouv_sirene_geo(ctx):
    import csv as _csv
    left = Path(ctx.runtime_root) / "l.csv"
    right = Path(ctx.runtime_root) / "r.csv"
    for p in (left, right):
        with p.open("w", encoding="utf-8", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["k", "v"])
            w.writerow(["1", "a"])
    with patch("src.services.datagouv.get_datagouv_service") as dg, \
         patch("src.services.sirene.get_sirene_service") as sr, \
         patch("src.services.geo_gouv.get_geo_gouv_service") as gg:
        result = await data_join_handler(
            ctx, left_path="l.csv", right_path="r.csv", on_left="k",
        )
        assert result.success is True
        dg.assert_not_called()
        sr.assert_not_called()
        gg.assert_not_called()


# ─── data_export handler tests (V2.4) ──────────────────────────────────


@pytest.mark.asyncio
async def test_export_csv_filtered(ctx):
    _prep_csv(ctx)
    result = await data_export_handler(
        ctx,
        path="communes.csv",
        output_format="csv",
        where=[{"col": "region", "op": "==", "value": "Île-de-France"}],
        filename="idf.csv",
    )
    assert result.success is True
    # Fichier existe + sidecar existe
    out_path = Path(ctx.runtime_root) / "exports" / "datagouv" / "idf.csv"
    assert out_path.exists()
    sidecar = out_path.with_suffix(".csv.export_meta.json")
    assert sidecar.exists()
    # Vérifier le contenu CSV exporté
    content = out_path.read_text(encoding="utf-8")
    assert "Paris" in content
    # Vérifier provenance
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    assert meta["schema_version"] == 1
    assert meta["output_format"] == "csv"
    assert meta["rows_exported"] == 1
    assert meta["operations"]["where"]
    assert meta["source_md5"]


@pytest.mark.asyncio
async def test_export_json(ctx):
    _prep_csv(ctx)
    result = await data_export_handler(
        ctx, path="communes.csv", output_format="json",
        where=[{"col": "population_2024", "op": ">", "value": 500000}],
    )
    assert result.success is True
    out = Path(ctx.runtime_root) / "exports" / "datagouv" / "communes_export.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert isinstance(data, list)
    assert len(data) == 3  # Paris, Marseille, Lyon


@pytest.mark.asyncio
async def test_export_xlsx(ctx):
    _prep_csv(ctx)
    result = await data_export_handler(
        ctx, path="communes.csv", output_format="xlsx",
    )
    assert result.success is True
    out = Path(ctx.runtime_root) / "exports" / "datagouv" / "communes_export.xlsx"
    assert out.exists()
    assert out.stat().st_size > 0


@pytest.mark.asyncio
async def test_export_with_columns_projection(ctx):
    _prep_csv(ctx)
    result = await data_export_handler(
        ctx, path="communes.csv", output_format="csv",
        columns=["commune", "population_2024"],
        filename="subset.csv",
    )
    assert result.success is True
    out = Path(ctx.runtime_root) / "exports" / "datagouv" / "subset.csv"
    content = out.read_text(encoding="utf-8")
    # Header projeté : 2 colonnes
    first_line = content.split("\n")[0]
    assert "commune" in first_line and "population_2024" in first_line
    assert "code_insee" not in first_line  # exclu


@pytest.mark.asyncio
async def test_export_with_aggregate(ctx):
    _prep_csv(ctx)
    result = await data_export_handler(
        ctx, path="communes.csv", output_format="csv",
        group_by="region", agg="sum", agg_col="population_2024",
        sort=["-result"], limit=3,
        filename="agg.csv",
    )
    assert result.success is True
    out = Path(ctx.runtime_root) / "exports" / "datagouv" / "agg.csv"
    content = out.read_text(encoding="utf-8")
    assert "region" in content.split("\n")[0]
    assert "result" in content.split("\n")[0]


@pytest.mark.asyncio
async def test_export_forbidden_format(ctx):
    _prep_csv(ctx)
    result = await data_export_handler(
        ctx, path="communes.csv", output_format="parquet",
    )
    assert result.success is False
    assert "interdit" in result.output.lower()


@pytest.mark.asyncio
async def test_export_blocks_path_traversal_filename(ctx):
    _prep_csv(ctx)
    result = await data_export_handler(
        ctx, path="communes.csv", output_format="csv",
        filename="../../etc/evil.csv",
    )
    assert result.success is True
    # Le fichier doit rester dans exports/datagouv/, pas ailleurs
    base = Path(ctx.runtime_root) / "exports" / "datagouv"
    safe = base / "evil.csv"
    assert safe.exists()
    assert not (Path(ctx.runtime_root).parent / "etc" / "evil.csv").exists()


@pytest.mark.asyncio
async def test_export_forces_correct_extension(ctx):
    _prep_csv(ctx)
    result = await data_export_handler(
        ctx, path="communes.csv", output_format="json",
        filename="data.csv",  # mauvaise extension demandée
    )
    assert result.success is True
    out = Path(ctx.runtime_root) / "exports" / "datagouv" / "data.json"
    assert out.exists()


@pytest.mark.asyncio
async def test_export_aggregate_requires_agg(ctx):
    _prep_csv(ctx)
    result = await data_export_handler(
        ctx, path="communes.csv", output_format="csv",
        group_by="region",  # sans agg → doit échouer
    )
    assert result.success is False


@pytest.mark.asyncio
async def test_export_unknown_projection_column(ctx):
    _prep_csv(ctx)
    result = await data_export_handler(
        ctx, path="communes.csv", output_format="csv",
        columns=["ghost_col"],
    )
    assert result.success is False
    assert "inconnue" in result.output.lower() or "ghost_col" in result.output


@pytest.mark.asyncio
async def test_export_output_includes_markdown_preview(ctx):
    """Backlog V2.4 : data_export doit renvoyer un aperçu Markdown
    pour éviter un read_file après export."""
    _prep_csv(ctx)
    result = await data_export_handler(
        ctx, path="communes.csv", output_format="csv",
        where=[{"col": "region", "op": "==", "value": "Île-de-France"}],
        filename="idf.csv",
    )
    assert result.success is True
    out = result.output
    # Tableau Markdown présent : pipes + ligne de séparation `---`
    assert "Aperçu" in out
    assert "---" in out
    # Paris doit apparaître dans l'aperçu
    assert "Paris" in out


@pytest.mark.asyncio
async def test_export_does_not_call_datagouv(ctx):
    _prep_csv(ctx)
    with patch("src.services.datagouv.get_datagouv_service") as mock_svc:
        result = await data_export_handler(
            ctx, path="communes.csv", output_format="csv",
        )
        assert result.success is True
        mock_svc.assert_not_called()


# ─── data_aggregate handler tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_aggregate_handler_count_by_region(ctx):
    _prep_csv(ctx)
    result = await data_aggregate_handler(
        ctx, path="communes.csv",
        group_by="region", agg="count",
    )
    assert result.success is True
    out = result.output
    assert "Auvergne-Rhône-Alpes" in out
    # Tableau markdown
    assert "result" in out and "_count" in out


@pytest.mark.asyncio
async def test_aggregate_handler_sum_with_sort_limit(ctx):
    _prep_csv(ctx)
    result = await data_aggregate_handler(
        ctx, path="communes.csv",
        group_by="region", agg="sum", agg_col="population_2024",
        sort=["-result"], limit=3,
    )
    assert result.success is True
    out = result.output
    # IDF en tête (Paris seul = 2,1M)
    assert out.find("Île-de-France") < out.find("Auvergne-Rhône-Alpes")


@pytest.mark.asyncio
async def test_aggregate_handler_forbidden_agg(ctx):
    _prep_csv(ctx)
    result = await data_aggregate_handler(
        ctx, path="communes.csv",
        group_by="region", agg="exec",
    )
    assert result.success is False
    assert "interdite" in result.output.lower()


@pytest.mark.asyncio
async def test_aggregate_handler_missing_agg_col(ctx):
    _prep_csv(ctx)
    result = await data_aggregate_handler(
        ctx, path="communes.csv",
        group_by="region", agg="sum",
    )
    assert result.success is False
    assert "agg_col" in result.output


# ─── data_unique_values handler tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_unique_values_handler_basic(ctx):
    _prep_csv(ctx)
    result = await data_unique_values_handler(
        ctx, path="communes.csv", column="region",
    )
    assert result.success is True
    out = result.output
    assert "Valeur" in out and "Fréquence" in out
    assert "Auvergne-Rhône-Alpes" in out


@pytest.mark.asyncio
async def test_unique_values_handler_unknown_column(ctx):
    _prep_csv(ctx)
    result = await data_unique_values_handler(
        ctx, path="communes.csv", column="ghost",
    )
    assert result.success is False
    assert "inconnue" in result.output.lower() or "ghost" in result.output


@pytest.mark.asyncio
async def test_aggregate_handler_does_not_call_datagouv(ctx):
    _prep_csv(ctx)
    with patch("src.services.datagouv.get_datagouv_service") as mock_svc:
        result = await data_aggregate_handler(
            ctx, path="communes.csv",
            group_by="region", agg="count",
        )
        assert result.success is True
        mock_svc.assert_not_called()


@pytest.mark.asyncio
async def test_filter_handler_returns_markdown_table(ctx):
    _prep_csv(ctx)
    result = await data_filter_rows_handler(
        ctx, path="communes.csv", limit=2,
    )
    out = result.output
    # tableau markdown : pipes + ligne de séparation `---`
    assert "|" in out
    assert "---" in out


@pytest.mark.asyncio
async def test_filter_handler_does_not_call_datagouv(ctx):
    """V2.2 : le handler doit travailler sur le fichier existant uniquement.
    Aucun import datagouv ne doit être nécessaire."""
    _prep_csv(ctx)
    # Patch datagouv pour vérifier qu'il n'est PAS appelé
    with patch("src.services.datagouv.get_datagouv_service") as mock_svc:
        result = await data_filter_rows_handler(
            ctx, path="communes.csv",
            where=[{"col": "region", "op": "==", "value": "Île-de-France"}],
        )
        assert result.success is True
        mock_svc.assert_not_called()


# ─── HandlerDef anti-régression ─────────────────────────────────────────


def test_handler_defs_exposes_v2_1_to_v3_4():
    """V3.4 ajoute data_join. Workbench complet (V2 + V3.4)."""
    defs = get_data_workbench_handler_defs()
    names = {d.name for d in defs}
    expected = {
        "data_profile_file", "data_filter_rows",
        "data_aggregate", "data_unique_values", "data_export",
        "data_join",
    }
    assert names == expected, f"Attendu {expected}, trouvé {names}"


def test_handler_defs_category_documents():
    defs = get_data_workbench_handler_defs()
    for d in defs:
        assert d.category == "documents"
        assert d.source_module == "handlers.data_workbench"


def test_handler_defs_required_params():
    defs = {d.name: d for d in get_data_workbench_handler_defs()}
    assert defs["data_profile_file"].parameters["required"] == ["path"]
    assert defs["data_filter_rows"].parameters["required"] == ["path"]
    assert defs["data_aggregate"].parameters["required"] == ["path", "group_by", "agg"]
    assert defs["data_unique_values"].parameters["required"] == ["path", "column"]
    assert defs["data_export"].parameters["required"] == ["path", "output_format"]
    assert defs["data_join"].parameters["required"] == ["left_path", "right_path", "on_left"]


def test_no_v3_2_v3_3_leak():
    """Workbench V3.4 ne doit pas exposer sirene/geo (V3.2/V3.3 séparés)."""
    names = {d.name for d in get_data_workbench_handler_defs()}
    forbidden = {
        "sirene_search_company", "sirene_get_by_siret",
        "geo_search_address", "geo_reverse", "geo_commune_info",
    }
    assert names.isdisjoint(forbidden), f"Leak V3.2/V3.3 détecté : {names & forbidden}"


def test_profile_description_mentions_roadmap():
    """La description profile doit informer le LLM que filter/aggregate arrivent."""
    defs = {d.name: d for d in get_data_workbench_handler_defs()}
    desc = defs["data_profile_file"].description
    assert "V2.2" in desc or "V2.3" in desc or "V2.4" in desc


def test_filter_description_lists_whitelisted_ops():
    """data_filter_rows doit lister la whitelist d'ops dans sa description."""
    defs = {d.name: d for d in get_data_workbench_handler_defs()}
    desc = defs["data_filter_rows"].description
    for op in ("==", "!=", ">", "<", ">=", "<=", "contains", "in"):
        assert op in desc, f"Op `{op}` manquant dans la description"
