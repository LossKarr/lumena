from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.core_services.intent_classifier import RequestMode, classify_intent
from src.documents.document_intent import document_action_kind
from src.documents.generation_recipe import (
    RECIPE_METADATA_KEY,
    StudioGenerationRecipe,
    changed_document_data,
    merge_document_data,
)
from src.documents.studio import DocumentStudio
from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.documents import (
    _prepare_studio_batch,
    get_documents_handler_defs,
    revise_studio_document_handler,
)


ROOT = Path(__file__).parents[2]


def make_studio(tmp_path: Path) -> DocumentStudio:
    return DocumentStudio(
        tmp_path / "studio",
        builtin_root=ROOT / "assets" / "templates",
        output_root=tmp_path / "output",
    )


def test_merge_document_data_is_recursive_and_non_mutating():
    original = {"client": {"name": "Avant", "city": "Paris"}, "items": [1]}
    patch = {"client": {"name": "Après"}}
    merged = merge_document_data(original, patch)
    assert merged == {"client": {"name": "Après", "city": "Paris"}, "items": [1]}
    assert original["client"]["name"] == "Avant"
    assert merge_document_data(original, patch, replace=True) == patch


def test_changed_document_data_reports_only_new_leaf_values():
    original = {
        "collaborateur": {"name": "Sarah Morel", "service": "Marketing"},
        "bilan": "Objectifs atteints",
        "tags": ["RH"],
    }
    revised = {
        "collaborateur": {"name": "Sarah Morel", "service": "Marketing"},
        "bilan": "Objectifs atteints. CAP-LEADERSHIP-2042",
        "tags": ["RH"],
    }

    assert changed_document_data(original, revised) == {
        "bilan": "Objectifs atteints. CAP-LEADERSHIP-2042",
    }


def test_builtin_batch_rejects_unrendered_identity_alias_before_generation(tmp_path):
    studio = make_studio(tmp_path)

    prepared, errors = _prepare_studio_batch(studio, [{
        "kind": "entretien_annuel",
        "filename": "NovaCare_Entretien_Annuel_Sarah_Morel",
        "data": {
            "employe": {"prenom": "Sarah", "nom": "Morel"},
            "bilan": "Très bonne année.",
        },
    }])

    assert prepared == []
    assert len(errors) == 1
    assert "employe" in errors[0]["error"]
    assert "collaborateur" in errors[0]["error"]


def test_custom_template_keeps_historical_extra_data_compatibility(tmp_path):
    studio = make_studio(tmp_path)
    custom = studio.catalog.clone_builtin("entretien_annuel", "entretien-custom")

    prepared, errors = _prepare_studio_batch(studio, [{
        "kind": "entretien_annuel",
        "template_id": custom.manifest.id,
        "data": {"extension_client": {"reference": "RH-2042"}},
    }])

    assert errors == []
    assert prepared[0]["data"]["extension_client"]["reference"] == "RH-2042"


def test_builtin_batch_accepts_optional_field_rendered_by_template(tmp_path):
    studio = make_studio(tmp_path)

    prepared, errors = _prepare_studio_batch(studio, [{
        "kind": "feuille_temps",
        "data": {"titre": "Feuille de temps - Festival Nantes"},
    }])

    assert errors == []
    assert prepared[0]["data"]["titre"] == "Feuille de temps - Festival Nantes"


@pytest.mark.asyncio
async def test_correct_builtin_identity_is_rendered_without_sample_person(tmp_path):
    from pypdf import PdfReader

    studio = make_studio(tmp_path)
    prepared, errors = _prepare_studio_batch(studio, [{
        "kind": "entretien_annuel",
        "filename": "NovaCare_Entretien_Annuel_Sarah_Morel",
        "data": {
            "collaborateur": {
                "name": "Sarah Morel",
                "poste": "Responsable Marketing Digital",
                "service": "Marketing",
            },
            "manager": "Marc Dubois",
            "bilan": "Très bonne année.",
        },
    }])
    assert errors == []

    item = prepared[0]
    result = await studio.generate(
        template_id=item["template_id"],
        kind=item["kind"],
        output_format=item["output_format"],
        data=item["data"],
        filename=item["filename"],
    )
    text = "\n".join(
        page.extract_text() or ""
        for page in PdfReader(result["path"]).pages
    )

    assert "Sarah Morel" in text
    assert "Morgan Leroy" not in text


@pytest.mark.asyncio
async def test_partial_replace_data_is_refused_and_merge_returns_exact_changes(
    tmp_path, monkeypatch,
):
    studio = make_studio(tmp_path)
    template = studio.catalog.get("entretien_annuel")
    original = await studio.generate(
        kind="entretien_annuel",
        output_format="pdf",
        data=studio.catalog.read_sample_data(template),
        filename="entretien-original",
    )
    monkeypatch.setattr("src.documents.studio.get_document_studio", lambda: studio)

    refused = await revise_studio_document_handler(
        HandlerContext(),
        original["record"]["id"],
        '{"bilan":"CAP-LEADERSHIP-2042"}',
        replace_data=True,
    )
    assert refused.success is False
    assert "replace_data=false" in refused.output

    revised = await revise_studio_document_handler(
        HandlerContext(),
        original["record"]["id"],
        '{"bilan":"CAP-LEADERSHIP-2042"}',
        replace_data=False,
    )
    assert revised.success is True
    payload = __import__("json").loads(revised.output)
    assert payload["changed_fields"] == {"bilan": "CAP-LEADERSHIP-2042"}


@pytest.mark.asyncio
async def test_generation_records_reproducible_recipe_and_normalizes_filename(tmp_path):
    studio = make_studio(tmp_path)
    template = studio.catalog.get("devis")
    result = await studio.generate(
        kind="devis",
        output_format="html",
        data=studio.catalog.read_sample_data(template),
        filename="devis-client.html",
    )
    record = studio.library.get(result["record"]["id"])
    recipe = StudioGenerationRecipe.from_metadata(record.metadata)
    assert Path(result["path"]).name == "devis-client.html"
    assert record.template_id == "devis"
    assert recipe.template_id == "devis"
    assert recipe.output_format == "html"
    assert recipe.data["numero"]


@pytest.mark.asyncio
async def test_revision_creates_child_and_preserves_original_recipe(tmp_path):
    studio = make_studio(tmp_path)
    template = studio.catalog.get("devis")
    original = await studio.generate(
        kind="devis",
        output_format="html",
        data=studio.catalog.read_sample_data(template),
        filename="devis-original",
    )
    original_id = original["record"]["id"]
    preview = studio.preview_revision(original_id, data={"numero": "DEV-REVISION"})
    assert "DEV-REVISION" in preview["html"]

    revised = await studio.revise(original_id, data={"numero": "DEV-REVISION"})
    child = studio.library.get(revised["record"]["id"])
    parent = studio.library.get(original_id)
    assert child.parent_id == original_id
    assert StudioGenerationRecipe.from_metadata(parent.metadata).data["numero"] != "DEV-REVISION"
    assert StudioGenerationRecipe.from_metadata(child.metadata).data["numero"] == "DEV-REVISION"
    history = studio.library.list_transformations(original_id)
    assert history[0]["operation"] == "revise"
    assert history[0]["details"]["output_document_id"] == child.id


@pytest.mark.asyncio
async def test_timesheet_revision_number_is_visible_in_generated_pdf(tmp_path):
    from pypdf import PdfReader

    studio = make_studio(tmp_path)
    template = studio.catalog.get("feuille_temps")
    data = studio.catalog.read_sample_data(template)
    data["numero"] = "FT-2026-009"
    original = await studio.generate(
        kind="feuille_temps",
        output_format="pdf",
        data=data,
        filename="festival-feuille-temps",
    )

    revised = await studio.revise(
        original["record"]["id"],
        data={"numero": "FT-2026-009 / FESTIVAL-NANTES-730"},
    )
    text = "\n".join(
        page.extract_text() or ""
        for page in PdfReader(revised["path"]).pages
    )

    assert "FESTIVAL-NANTES-730" in text
    child = studio.library.get(revised["record"]["id"])
    assert (
        StudioGenerationRecipe.from_metadata(child.metadata).data["numero"]
        == "FT-2026-009 / FESTIVAL-NANTES-730"
    )


@pytest.mark.asyncio
async def test_timesheet_revision_title_is_visible_in_generated_pdf(tmp_path):
    from pypdf import PdfReader

    studio = make_studio(tmp_path)
    template = studio.catalog.get("feuille_temps")
    original = await studio.generate(
        kind="feuille_temps",
        output_format="pdf",
        data=studio.catalog.read_sample_data(template),
        filename="festival-feuille-temps",
    )
    title = "Feuille de Temps - Equipe Technique - FESTIVAL-NANTES-730"

    revised = await studio.revise(
        original["record"]["id"],
        data={"titre": title},
    )
    text = "\n".join(
        page.extract_text() or ""
        for page in PdfReader(revised["path"]).pages
    )

    assert "FESTIVAL-NANTES-730" in text
    child = studio.library.get(revised["record"]["id"])
    assert StudioGenerationRecipe.from_metadata(child.metadata).data["titre"] == title


@pytest.mark.asyncio
async def test_revision_uses_exact_archived_custom_template_version(tmp_path):
    studio = make_studio(tmp_path)
    custom = studio.catalog.clone_builtin("devis", "devis-versionne")
    sample = studio.catalog.read_sample_data(custom)
    generated = await studio.generate(
        template_id=custom.manifest.id,
        kind="devis",
        output_format="html",
        data=sample,
        filename="devis-v1",
    )
    source_v1 = studio.catalog.read_source(custom)
    manifest_v2 = custom.manifest.to_dict()
    studio.catalog.save_custom(
        custom.manifest.id,
        manifest_data=manifest_v2,
        template_source=source_v1 + "\n<div>MARQUEUR VERSION DEUX</div>",
        sample_data=sample,
    )
    revised = await studio.revise(generated["record"]["id"], data={"numero": "ARCHIVE-V1"})
    html = Path(revised["path"]).read_text(encoding="utf-8")
    assert "ARCHIVE-V1" in html
    assert "MARQUEUR VERSION DEUX" not in html


def test_old_generated_document_without_recipe_fails_actionably(tmp_path):
    studio = make_studio(tmp_path)
    source = tmp_path / "old.html"
    source.write_text("<!doctype html><html><body><p>ancien</p></body></html>", encoding="utf-8")
    record, _ = studio.importer.import_file(source, source_kind="generated")
    with pytest.raises(ValueError, match="avant cette fonctionnalité"):
        studio.preview_revision(record.id, data={"x": 1})


def test_document_revision_intent_keeps_document_tools_visible():
    assert document_action_kind("modifie le montant du devis") == "devis"
    assert document_action_kind("mets à jour la facture") == "facture"
    assert classify_intent("modifie le montant du devis") == RequestMode.REACT


def test_document_agent_surface_exposes_complete_lifecycle():
    definitions = {item.name: item for item in get_documents_handler_defs()}
    expected = {
        "get_document_history",
        "preview_document_edit",
        "apply_document_edit",
        "revise_studio_document",
        "convert_library_document",
        "export_library_document",
    }
    assert expected <= set(definitions)
    assert all(definitions[name].category == "documents" for name in expected)


def test_recipe_rejects_missing_metadata():
    with pytest.raises(ValueError, match="recette Studio"):
        StudioGenerationRecipe.from_metadata({RECIPE_METADATA_KEY: None})
