from __future__ import annotations

from pathlib import Path

import pytest

from src.documents.preview_service import PreviewService
from src.documents.template_catalog import TemplateCatalog
from src.documents.template_renderer import TemplateRenderer


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def catalog(tmp_path):
    return TemplateCatalog(tmp_path / "studio", ROOT / "assets" / "templates")


@pytest.mark.parametrize(
    "template_id",
    [
        "attestation",
        "bon_commande",
        "bulletin_paie",
        "contrat_prestation",
        "devis",
        "facture",
        "fiche_poste",
        "lettre_officielle",
        "nda",
        "note_interne",
        "proces_verbal",
        "rapport_activite",
        "relance_impaye",
        "avoir",
        "facture_proforma",
        "bon_livraison",
        "recu_paiement",
        "note_frais",
        "releve_client",
        "ordre_jour",
        "demande_conge",
        "feuille_temps",
        "entretien_annuel",
        "contrat_travail",
        "ordre_mission",
        "cahier_charges",
        "rapport_intervention",
        "rapport_incident",
        "procedure_operationnelle",
        "plan_action",
    ],
)
def test_every_builtin_renders_with_real_sample_data(catalog, template_id):
    record = catalog.get(template_id)
    html = TemplateRenderer().render_html(
        record, catalog.read_source(record), catalog.read_sample_data(record)
    )
    assert "<!DOCTYPE html>" in html
    assert len(html) > 800
    assert "{{" not in html
    assert 'id="lumena-document-design"' in html


def test_timesheet_number_is_optional_and_visible(catalog):
    record = catalog.get("feuille_temps")
    data = catalog.read_sample_data(record)

    without_number = TemplateRenderer().render_html(
        record, catalog.read_source(record), data,
    )
    data["numero"] = "FT-2026-009 / FESTIVAL-NANTES-730"
    with_number = TemplateRenderer().render_html(
        record, catalog.read_source(record), data,
    )

    assert "FESTIVAL-NANTES-730" not in without_number
    assert "N° FT-2026-009 / FESTIVAL-NANTES-730" in with_number


def test_timesheet_title_is_editable_and_keeps_historical_default(catalog):
    record = catalog.get("feuille_temps")
    data = catalog.read_sample_data(record)

    default_html = TemplateRenderer().render_html(
        record, catalog.read_source(record), data,
    )
    data["titre"] = "Feuille de Temps - Equipe Technique - FESTIVAL-NANTES-730"
    revised_html = TemplateRenderer().render_html(
        record, catalog.read_source(record), data,
    )

    assert "FEUILLE DE TEMPS" in default_html
    assert "FESTIVAL-NANTES-730" in revised_html


def test_employment_contract_does_not_duplicate_an_existing_clause_number(catalog):
    record = catalog.get("contrat_travail")
    data = catalog.read_sample_data(record)
    data["clauses"] = [
        {"title": "1. Confidentialité", "content": "Clause déjà numérotée."},
        {"title": "Mobilité", "content": "Clause sans numéro."},
    ]

    html = TemplateRenderer().render_html(
        record, catalog.read_source(record), data,
    )

    assert "1. 1. Confidentialité" not in html
    assert "1. Confidentialité" in html
    assert "2. Mobilité" in html


@pytest.mark.asyncio
async def test_preview_is_real_webp_and_cached(catalog, tmp_path):
    service = PreviewService(catalog, tmp_path / "previews")
    first = await service.generate("facture")
    second = await service.generate("facture")
    thumb = Path(first["thumbnail_path"])
    assert thumb.read_bytes()[:4] == b"RIFF"
    assert Path(first["pdf_path"]).read_bytes()[:4] == b"%PDF"
    assert first["cached"] is False
    assert second["cached"] is True
    assert first["content_hash"] == second["content_hash"]
