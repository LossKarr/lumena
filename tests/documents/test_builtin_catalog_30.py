from __future__ import annotations

from pathlib import Path

from src.documents.builtin_templates import BUILTIN_ALIASES, BUILTIN_LABELS
from src.documents.template_catalog import TemplateCatalog


ROOT = Path(__file__).resolve().parents[2]


def test_builtin_catalog_has_exactly_thirty_professional_models(tmp_path):
    catalog = TemplateCatalog(tmp_path / "studio", ROOT / "assets" / "templates")
    records = [record for record in catalog.list_templates() if record.read_only]

    assert len(records) == 30
    assert len(BUILTIN_LABELS) == 30
    assert len(BUILTIN_ALIASES) == 30
    assert {record.manifest.id for record in records} == set(BUILTIN_LABELS)


def test_builtin_aliases_are_unique_between_models():
    owners: dict[str, str] = {}
    for kind, aliases in BUILTIN_ALIASES.items():
        for alias in aliases:
            normalized = " ".join(alias.lower().split())
            assert normalized not in owners, (
                f"alias {alias!r} belongs to both {owners.get(normalized)!r} and {kind!r}"
            )
            owners[normalized] = kind


def test_localized_models_expose_honest_compliance_metadata(tmp_path):
    catalog = TemplateCatalog(tmp_path / "studio", ROOT / "assets" / "templates")

    employment = catalog.get("contrat_travail").manifest
    assert employment.scope == "localized"
    assert employment.compliance_level == "reference"
    assert employment.jurisdictions == ("FR", "EU")
    assert employment.legal_notice

    procedure = catalog.get("procedure_operationnelle").manifest
    assert procedure.scope == "universal"
    assert procedure.compliance_level == "structure"
    assert procedure.jurisdictions == ()
