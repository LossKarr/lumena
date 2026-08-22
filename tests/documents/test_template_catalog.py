from __future__ import annotations

from pathlib import Path

import pytest

from src.documents.template_catalog import TemplateCatalog
from src.documents.template_models import TemplateValidationError


BUILTINS = Path(__file__).resolve().parents[2] / "assets" / "templates"


@pytest.fixture
def catalog(tmp_path):
    return TemplateCatalog(tmp_path / "studio", BUILTINS)


def test_catalog_exposes_all_legacy_builtins_read_only(catalog):
    records = catalog.list_templates()
    builtins = [r for r in records if r.read_only]
    assert len(builtins) == 30
    assert {r.manifest.id for r in builtins} >= {"facture", "devis", "rapport_activite"}
    assert all(r.manifest.origin == "builtin" for r in builtins)


def test_clone_builtin_creates_custom_without_touching_source(catalog):
    source_text = (BUILTINS / "facture.html.j2").read_text(encoding="utf-8")
    clone = catalog.clone_builtin("facture", "facture-orange", name="Facture orange")
    assert not clone.read_only
    assert clone.manifest.name == "Facture orange"
    assert catalog.read_source(clone) == source_text
    assert catalog.read_sample_data(clone)["numero"].startswith("FAC-")
    assert (BUILTINS / "facture.html.j2").read_text(encoding="utf-8") == source_text


def test_custom_template_persists_its_own_free_logo_position(catalog):
    clone = catalog.clone_builtin("facture", "facture-logo-libre")
    manifest = clone.manifest.to_dict()
    manifest["design"] = {
        **manifest.get("design", {}),
        "logo_layout": "free",
        "logo_x_pct": 72,
        "logo_y_mm": 38,
    }
    catalog.save_custom(
        clone.manifest.id,
        manifest_data=manifest,
        template_source=catalog.read_source(clone),
        sample_data=catalog.read_sample_data(clone),
    )

    saved = catalog.get(clone.manifest.id).manifest.design
    builtin = catalog.get("facture").manifest.design
    assert saved["logo_layout"] == "free"
    assert saved["logo_x_pct"] == 72
    assert saved["logo_y_mm"] == 38
    assert "logo_layout" not in builtin


def test_save_versions_and_restore(catalog):
    clone = catalog.clone_builtin("facture", "facture-versionnee")
    source_v1 = catalog.read_source(clone)
    manifest = clone.manifest.to_dict()
    catalog.save_custom(
        clone.manifest.id,
        manifest_data=manifest,
        template_source=source_v1.replace("FACTURE", "FACTURE TEST"),
        sample_data=catalog.read_sample_data(clone),
    )
    assert catalog.get(clone.manifest.id).manifest.version == 2
    assert catalog.list_versions(clone.manifest.id) == [1]
    restored = catalog.restore(clone.manifest.id, 1)
    assert restored.manifest.version == 3
    assert "FACTURE TEST" not in catalog.read_source(restored)
    assert catalog.list_versions(clone.manifest.id) == [1, 2]


def test_defaults_are_explicit_and_removable(catalog):
    clone = catalog.clone_builtin("facture", "facture-default")
    assert catalog.get_default("facture", "pdf") is None
    catalog.set_default("facture", "pdf", clone.manifest.id)
    assert catalog.get_default("facture", "pdf").manifest.id == clone.manifest.id
    catalog.set_default("facture", "pdf", None)
    assert catalog.get_default("facture", "pdf") is None


def test_default_rejects_incompatible_slot(catalog):
    clone = catalog.clone_builtin("facture", "facture-slot")
    with pytest.raises(TemplateValidationError):
        catalog.set_default("devis", "pdf", clone.manifest.id)


@pytest.mark.parametrize("bad", ["../escape", "A", "x/y", "C:\\escape", "--bad"])
def test_template_ids_cannot_escape_storage(catalog, bad):
    with pytest.raises(TemplateValidationError):
        catalog.clone_builtin("facture", bad)
