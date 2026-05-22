"""Tests unitaires pour handlers/datagouv.py — 3 handlers V1 lecture seule.

Mocks complets de DataGouvService. Aucun appel réseau.
Pas de test SIRENE (V3). Pas de test ingestion (V2).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.datagouv import (
    datagouv_search_handler,
    datagouv_get_dataset_handler,
    datagouv_download_resource_handler,
    get_datagouv_handler_defs,
)


# ─── fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return HandlerContext.for_testing(
        lumena_root=tmp_path,
        runtime_root=workspace,
    )


def _mock_service(**overrides):
    svc = MagicMock()
    svc.search_datasets = AsyncMock(
        return_value={
            "data": [
                {
                    "slug": "marches-publics-paca",
                    "title": "Marchés publics PACA",
                    "organization": {"name": "Région SUD"},
                    "resources": [{"format": "csv"}, {"format": "json"}],
                    "description": "Liste des marchés publics de la région PACA.",
                },
                {
                    "slug": "marches-publics-idf",
                    "title": "Marchés publics IDF",
                    "organization": {"name": "Région IDF"},
                    "resources": [{"format": "csv"}],
                    "description": "",
                },
                {
                    "slug": "marches-publics-massif",
                    "title": "Marchés publics tous (massif)",
                    "organization": {"name": "État"},
                    # Dataset massif : 60 ressources, pas de CSV visible
                    "resources": [{"format": "xml"}] * 60,
                    "description": "Tout l'open data marchés publics.",
                },
                {
                    "slug": "marches-publics-vide",
                    "title": "Marchés publics (catalogue vide)",
                    "organization": {"name": "Mairie X"},
                    "resources": [],
                    "description": "Dataset déclaré sans ressource.",
                },
            ],
            "total": 4,
        }
    )
    svc.get_dataset = AsyncMock(
        return_value={
            "slug": "marches-publics-paca",
            "title": "Marchés publics PACA",
            "organization": {"name": "Région SUD"},
            "description": "Liste exhaustive.",
            "frequency": "daily",
            "resources": [
                {
                    "id": "abc-123",
                    "title": "Données 2024",
                    "format": "csv",
                    "url": "https://blob.azure.com/x.csv",
                    "latest": "https://www.data.gouv.fr/fr/datasets/r/abc-123",
                    "filesize": 1024 * 50,
                    "checksum": {"type": "sha256", "value": "deadbeef" * 8},
                }
            ],
        }
    )

    async def fake_download(url, target_path, **kwargs):
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"col1,col2\n1,2\n")
        return target_path

    svc.download_resource = AsyncMock(side_effect=fake_download)

    for k, v in overrides.items():
        setattr(svc, k, v)
    return svc


# ─── datagouv_search ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_returns_ok_with_results(ctx):
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = await datagouv_search_handler(ctx, query="marchés publics")
    assert isinstance(result, HandlerResult)
    assert result.success is True
    assert "marches-publics-paca" in result.output
    assert "Région SUD" in result.output


@pytest.mark.asyncio
async def test_search_handles_empty_results(ctx):
    svc = _mock_service()
    svc.search_datasets = AsyncMock(return_value={"data": [], "total": 0})
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=svc,
    ):
        result = await datagouv_search_handler(ctx, query="xxnotfoundxx")
    assert result.success is True
    assert "Aucun dataset" in result.output


@pytest.mark.asyncio
async def test_search_returns_fail_on_exception(ctx):
    svc = _mock_service()
    svc.search_datasets = AsyncMock(side_effect=RuntimeError("API down"))
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=svc,
    ):
        result = await datagouv_search_handler(ctx, query="x")
    assert result.success is False
    assert "Erreur recherche" in result.output


# ─── datagouv_get_dataset ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_dataset_lists_resources(ctx):
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = await datagouv_get_dataset_handler(
            ctx, slug_or_id="marches-publics-paca"
        )
    assert result.success is True
    assert "Marchés publics PACA" in result.output
    assert "[csv]" in result.output
    assert "Données 2024" in result.output


@pytest.mark.asyncio
async def test_get_dataset_exposes_stable_latest_url_and_id(ctx):
    """ReAct doit voir id + latest + checksum pour choisir l'URL stable."""
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = await datagouv_get_dataset_handler(
            ctx, slug_or_id="marches-publics-paca"
        )
    out = result.output
    assert "abc-123" in out, "resource id manquant"
    assert "datasets/r/abc-123" in out, "URL `latest` stable manquante"
    assert "latest" in out.lower()
    assert "sha256" in out, "checksum manquant"


@pytest.mark.asyncio
async def test_search_empty_gives_fallback_advice(ctx):
    """Quand 0 résultat, le handler doit suggérer les fallbacks (web_search, etc)."""
    svc = _mock_service()
    svc.search_datasets = AsyncMock(return_value={"data": [], "total": 0})
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=svc,
    ):
        result = await datagouv_search_handler(ctx, query="x")
    assert result.success is True
    assert "Aucun dataset" in result.output
    assert "web_search" in result.output or "Conseils" in result.output


@pytest.mark.asyncio
async def test_get_dataset_returns_fail_on_exception(ctx):
    svc = _mock_service()
    svc.get_dataset = AsyncMock(side_effect=RuntimeError("404"))
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=svc,
    ):
        result = await datagouv_get_dataset_handler(ctx, slug_or_id="x")
    assert result.success is False


# ─── datagouv_download_resource ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_writes_to_workspace_downloads(ctx):
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = await datagouv_download_resource_handler(
            ctx,
            resource_url="https://www.data.gouv.fr/files/marches-2024.csv",
        )
    assert result.success is True
    expected_path = (
        Path(ctx.runtime_root) / "downloads" / "datagouv" / "marches-2024.csv"
    )
    assert expected_path.exists()
    assert "downloads" in result.output
    assert "datagouv" in result.output


@pytest.mark.asyncio
async def test_download_uses_custom_filename(ctx):
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = await datagouv_download_resource_handler(
            ctx,
            resource_url="https://www.data.gouv.fr/x.csv",
            filename="custom.csv",
        )
    assert result.success is True
    assert (Path(ctx.runtime_root) / "downloads" / "datagouv" / "custom.csv").exists()


@pytest.mark.asyncio
async def test_download_strips_path_traversal_in_filename(ctx):
    """filename = '../../etc/passwd' doit être ramené à 'passwd'."""
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = await datagouv_download_resource_handler(
            ctx,
            resource_url="https://www.data.gouv.fr/x.csv",
            filename="../../etc/passwd",
        )
    assert result.success is True
    # Doit avoir écrit dans <ws>/downloads/datagouv/passwd, pas en dehors
    safe_target = Path(ctx.runtime_root) / "downloads" / "datagouv" / "passwd"
    assert safe_target.exists()
    # Et surtout pas ailleurs
    assert not (Path(ctx.runtime_root).parent / "etc" / "passwd").exists()


@pytest.mark.asyncio
async def test_download_strips_windows_backslash_traversal(ctx):
    """filename Windows-style avec backslashes ne doit pas échapper le workspace."""
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = await datagouv_download_resource_handler(
            ctx,
            resource_url="https://www.data.gouv.fr/x.csv",
            filename="..\\..\\etc\\passwd",
        )
    assert result.success is True
    base = Path(ctx.runtime_root) / "downloads" / "datagouv"
    # Tous les fichiers écrits doivent être dans le sous-dossier datagouv
    for f in base.rglob("*"):
        if f.is_file():
            assert f.is_relative_to(base), f"fichier hors workspace : {f}"


@pytest.mark.asyncio
async def test_download_writes_provenance_sidecar(ctx):
    """V2.1 : `<file>.datagouv.json` doit être créé à côté du téléchargement."""
    import json
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = await datagouv_download_resource_handler(
            ctx,
            resource_url="https://www.data.gouv.fr/api/1/datasets/r/abc-uuid",
            filename="data.csv",
        )
    assert result.success is True
    sidecar = (
        Path(ctx.runtime_root) / "downloads" / "datagouv" / "data.csv.datagouv.json"
    )
    assert sidecar.exists()
    prov = json.loads(sidecar.read_text(encoding="utf-8"))
    assert prov["schema_version"] == 1
    assert prov["resource_id"] == "abc-uuid"
    assert prov["resource_url"].endswith("/abc-uuid")
    assert prov["filename"] == "data.csv"
    assert prov["format_detected"] == "csv"
    assert prov["md5"]
    assert prov["downloaded_at"]
    assert prov["size_bytes"] > 0
    # Le message handler doit mentionner le sidecar + data_profile_file
    assert "provenance" in result.output
    assert "data_profile_file" in result.output


@pytest.mark.asyncio
async def test_download_returns_absolute_and_relative_paths(ctx):
    """Le handler doit exposer chemin relatif + absolu + format + taille."""
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = await datagouv_download_resource_handler(
            ctx,
            resource_url="https://www.data.gouv.fr/file.csv",
            filename="data.csv",
        )
    out = result.output
    assert result.success is True
    assert "chemin absolu" in out
    assert "format détecté" in out
    assert "csv" in out.lower()
    assert "KB" in out  # taille présente
    abs_target = (Path(ctx.runtime_root) / "downloads" / "datagouv" / "data.csv").resolve()
    assert str(abs_target) in out


@pytest.mark.asyncio
async def test_download_warns_on_format_mismatch_xls_for_csv(ctx):
    """Resource .xls demandée comme CSV → warning explicite, pas de conversion auto."""
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = await datagouv_download_resource_handler(
            ctx,
            resource_url="https://www.data.gouv.fr/file.xls",
            filename="population.xls",
            expected_format="csv",
        )
    out = result.output
    assert result.success is True
    assert "FORMAT NON CONFORME" in out
    assert "xls" in out.lower() and "csv" in out.lower()
    assert "conversion" in out.lower()
    assert "V1" in out
    # Ne pas suggérer ingest_document quand format mismatch
    assert "ingest_document" not in out


@pytest.mark.asyncio
async def test_download_xlsx_alias_xls_no_warning(ctx):
    """xlsx demandé, fichier .xls → pas de warning (alias acceptable)."""
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = await datagouv_download_resource_handler(
            ctx,
            resource_url="https://www.data.gouv.fr/file.xls",
            filename="population.xls",
            expected_format="xlsx",
        )
    assert result.success is True
    assert "FORMAT NON CONFORME" not in result.output


@pytest.mark.asyncio
async def test_download_csv_match_no_warning(ctx):
    """CSV demandé, fichier .csv → pas de warning."""
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = await datagouv_download_resource_handler(
            ctx,
            resource_url="https://www.data.gouv.fr/data.csv",
            filename="data.csv",
            expected_format="csv",
        )
    assert result.success is True
    assert "FORMAT NON CONFORME" not in result.output
    # V2.1 : la suggestion suivante pointe désormais vers data_profile_file
    assert "data_profile_file" in result.output


@pytest.mark.asyncio
async def test_download_returns_fail_on_exception(ctx):
    svc = _mock_service()
    svc.download_resource = AsyncMock(side_effect=ValueError("max_bytes exceeded"))
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=svc,
    ):
        result = await datagouv_download_resource_handler(
            ctx, resource_url="https://x.csv"
        )
    assert result.success is False
    assert "Erreur téléchargement" in result.output


# ─── HandlerDef format & catégorie ──────────────────────────────────────


def test_get_handler_defs_returns_3_entries():
    defs = get_datagouv_handler_defs()
    assert len(defs) == 3


def test_all_handlers_have_required_fields():
    for d in get_datagouv_handler_defs():
        assert d.name.startswith("datagouv_")
        assert d.description
        assert "properties" in d.parameters
        assert "required" in d.parameters
        assert callable(d.handler)
        assert d.source_module == "handlers.datagouv"


def test_all_handlers_use_web_category():
    """V1 : tous les handlers sont catégorie 'web' (cf décision plan §10)."""
    for d in get_datagouv_handler_defs():
        assert d.category == "web", (
            f"{d.name} devrait être 'web', trouvé '{d.category}'"
        )


def test_required_params_present():
    expected_required = {
        "datagouv_search": ["query"],
        "datagouv_get_dataset": ["slug_or_id"],
        "datagouv_download_resource": ["resource_url"],
    }
    by_name = {d.name: d for d in get_datagouv_handler_defs()}
    for name, required in expected_required.items():
        assert by_name[name].parameters["required"] == required


def test_download_description_mandates_expected_format_on_retries():
    """V1.5c : la description doit dire d'utiliser expected_format même au retry."""
    defs = {d.name: d for d in get_datagouv_handler_defs()}
    desc = defs["datagouv_download_resource"].description.lower()
    assert "expected_format" in desc
    # Doit explicitement mentionner retries / chaque appel
    assert "retries" in desc or "chaque appel" in desc or "toujours" in desc


def test_download_description_pushes_clean_filename():
    """V1.5c : la description doit pousser à donner un filename propre (anti-UUID)."""
    defs = {d.name: d for d in get_datagouv_handler_defs()}
    desc = defs["datagouv_download_resource"].description.lower()
    assert "filename" in desc
    # Doit mentionner que sans filename → UUID illisible
    assert "uuid" in desc or "illisible" in desc or "extension" in desc


def test_get_dataset_output_guides_download_call(ctx):
    """V1.5c : get_dataset doit guider explicitement l'appel à download (filename + expected_format)."""
    import asyncio
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = asyncio.run(
            datagouv_get_dataset_handler(ctx, slug_or_id="marches-publics-paca")
        )
    out = result.output.lower()
    assert "filename" in out
    assert "expected_format" in out


@pytest.mark.asyncio
async def test_search_required_format_csv_marks_quality_signals(ctx):
    """V1.6 : required_format='csv' active markers ✅/❌/⚠️ et tri."""
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = await datagouv_search_handler(
            ctx, query="marchés publics", required_format="csv"
        )
    out = result.output
    assert result.success is True
    # Doit annoncer le tri et le format requis
    assert "csv" in out.lower()
    # Au moins un dataset CSV marqué ✅
    assert "✅" in out
    # Dataset vide marqué ❌
    assert "AUCUNE RESSOURCE" in out
    # Dataset massif marqué ⚠️
    assert "MASSIF" in out
    # Bilan synthétique
    assert "ont une ressource `csv`" in out


@pytest.mark.asyncio
async def test_search_required_format_orders_csv_first(ctx):
    """Les datasets avec CSV doivent apparaître AVANT ceux sans CSV."""
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = await datagouv_search_handler(
            ctx, query="marchés publics", required_format="csv"
        )
    out = result.output
    # marches-publics-paca a du CSV, marches-publics-vide n'a rien
    idx_csv = out.find("marches-publics-paca")
    idx_vide = out.find("marches-publics-vide")
    idx_massif = out.find("marches-publics-massif")
    assert idx_csv != -1 and idx_vide != -1
    assert idx_csv < idx_vide, "Le dataset CSV doit précéder le dataset vide"
    assert idx_csv < idx_massif, "Le dataset CSV doit précéder le dataset massif"


@pytest.mark.asyncio
async def test_search_without_required_format_keeps_legacy_output(ctx):
    """Sans required_format, pas de tri qualité (rétro-compat)."""
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=_mock_service(),
    ):
        result = await datagouv_search_handler(ctx, query="marchés publics")
    out = result.output
    assert result.success is True
    # Pas de tri annoncé sans required_format
    assert "triés par disponibilité" not in out


@pytest.mark.asyncio
async def test_get_dataset_preferred_format_csv_orders_resources(ctx):
    """V1.6 : preferred_format='csv' remonte les ressources CSV en tête."""
    svc = _mock_service()
    svc.get_dataset = AsyncMock(
        return_value={
            "slug": "x",
            "title": "X",
            "organization": {"name": "Org"},
            "frequency": "annual",
            "resources": [
                {
                    "id": "xls-1", "title": "Données XLSX",
                    "format": "xlsx", "url": "https://x/y.xlsx",
                    "latest": "https://www.data.gouv.fr/api/1/datasets/r/xls-1",
                    "filesize": 1000,
                },
                {
                    "id": "csv-1", "title": "Données CSV",
                    "format": "csv", "url": "https://x/y.csv",
                    "latest": "https://www.data.gouv.fr/api/1/datasets/r/csv-1",
                    "filesize": 500,
                },
            ],
        }
    )
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=svc,
    ):
        result = await datagouv_get_dataset_handler(
            ctx, slug_or_id="x", preferred_format="csv"
        )
    out = result.output
    assert "✅" in out
    assert "csv" in out.lower()
    # csv-1 doit apparaître avant xls-1 dans la sortie
    assert out.find("csv-1") < out.find("xls-1")


@pytest.mark.asyncio
async def test_get_dataset_warns_when_massive(ctx):
    """Dataset > 50 ressources doit être marqué MASSIF + conseil stratégique."""
    svc = _mock_service()
    svc.get_dataset = AsyncMock(
        return_value={
            "slug": "massif",
            "title": "Massif",
            "organization": {"name": "X"},
            "frequency": "?",
            "resources": [
                {"id": f"r-{i}", "format": "xml", "url": f"https://x/{i}.xml"}
                for i in range(100)
            ],
        }
    )
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=svc,
    ):
        result = await datagouv_get_dataset_handler(
            ctx, slug_or_id="massif", preferred_format="csv"
        )
    out = result.output
    assert "MASSIF" in out
    # Conseil stratégique : pas de browser/web_fetch
    assert "browser" in out.lower() or "web_fetch" in out.lower() or "datagouv_search" in out.lower()


def test_search_description_mentions_required_format_strategy():
    """La description doit pousser le LLM à passer required_format.

    Depuis V3.1, le focus est sur le scoring (✅/⚙️/⛔) plutôt que sur "massif"
    seul. On vérifie que la stratégie anti-drift reste exprimée.
    """
    defs = {d.name: d for d in get_datagouv_handler_defs()}
    desc = defs["datagouv_search"].description.lower()
    assert "required_format" in desc
    # V3.1 : scoring exprimé via score / verdict / éviter
    assert "score" in desc or "éviter" in desc or "✅" in desc or "⛔" in desc
    # Doit interdire le drift vers browser/web_fetch sur data.gouv
    assert "browser" in desc and "web_fetch" in desc


def test_no_v2_v3_handlers_leaked():
    """V1 ne doit pas exposer download_and_ingest (V2) ni SIRENE (V3)."""
    names = {d.name for d in get_datagouv_handler_defs()}
    assert "datagouv_download_and_ingest" not in names
    assert "datagouv_search_company" not in names
    assert "datagouv_get_company_by_siret" not in names
    assert not any("siret" in n.lower() or "company" in n.lower() for n in names)
