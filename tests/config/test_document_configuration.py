from __future__ import annotations

from pathlib import Path

from src.documents.delivery_receipt import MAX_DELIVERY_DOCUMENTS
from src.documents.document_delivery_bundle import MAX_BUNDLE_DOCUMENTS


def test_document_configuration_group_is_bounded_and_unique():
    from web.routes.config import _CONFIG_SCHEMA

    keys = [entry["key"] for entry in _CONFIG_SCHEMA]
    assert len(keys) == len(set(keys))
    schema = {entry["key"]: entry for entry in _CONFIG_SCHEMA}

    assert schema["LUMENA_DOCUMENT_THEME"]["group"] == "Documents"
    assert schema["LUMENA_DOCUMENT_STUDIO_DIR"]["group"] == "Documents"
    assert schema["LUMENA_DOCUMENT_STUDIO_DIR"]["restart"] is True
    assert schema["LUMENA_DOCUMENT_BATCH_SIZE"]["group"] == "Documents"
    assert schema["LUMENA_DOCUMENT_BATCH_SIZE"]["min"] == 1
    assert schema["LUMENA_DOCUMENT_BATCH_SIZE"]["max"] == 30
    assert schema["LUMENA_DOCUMENT_WORKFLOW_MAX_DOCUMENTS"]["group"] == "Documents"
    assert schema["LUMENA_DOCUMENT_WORKFLOW_MAX_DOCUMENTS"]["min"] == 1
    assert schema["LUMENA_DOCUMENT_WORKFLOW_MAX_DOCUMENTS"]["max"] == 100


def test_document_configuration_cannot_weaken_integrity_guards():
    from web.routes.config import _CONFIG_SCHEMA

    keys = {entry["key"] for entry in _CONFIG_SCHEMA}
    assert MAX_DELIVERY_DOCUMENTS == 30
    assert MAX_BUNDLE_DOCUMENTS == 100
    assert not any("PROOF" in key or "RECEIPT" in key or "REVISION_VERIFY" in key for key in keys)
    assert "LUMENA_DOCUMENT_AUTO_OPEN" not in keys
    assert "LUMENA_DOCUMENT_RETRIES" not in keys


def test_documents_panel_is_visible_and_explains_locked_guarantees():
    source = Path("web/static/js/panels.js").read_text(encoding="utf-8")
    assert "{name:'Documents',         level:'simple',   icon:'files'}" in source
    assert "Garanties toujours actives" in source
    assert "Preuve catalogue, ordre exact" in source
