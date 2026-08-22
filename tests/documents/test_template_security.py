from __future__ import annotations

import json

import pytest

from src.documents.template_models import TemplateManifest, TemplateValidationError
from src.documents.template_security import atomic_write_json, resolve_within, validate_template_source


def test_resolve_within_blocks_traversal(tmp_path):
    with pytest.raises(TemplateValidationError):
        resolve_within(tmp_path / "root", "../outside")


@pytest.mark.parametrize(
    "source",
    [
        "{{ value.__class__ }}",
        "{% include 'secret' %}",
        "{% import 'x' as y %}",
        "{% extends 'base' %}",
    ],
)
def test_forbidden_jinja_constructs_are_rejected(source):
    with pytest.raises(TemplateValidationError):
        validate_template_source(source)


def test_safe_jinja_is_accepted():
    validate_template_source("<h1>{{ title|default('Titre') }}</h1>{% for row in rows %}{{ row.name }}{% endfor %}")


def test_manifest_rejects_unknown_fields():
    with pytest.raises(TemplateValidationError):
        TemplateManifest.from_dict(
            {
                "schema_version": 1,
                "id": "test-model",
                "name": "Test",
                "kind": "report",
                "format": "pdf",
                "renderer": "html-jinja",
                "surprise": True,
            }
        )


def test_atomic_json_never_leaves_temp_file(tmp_path):
    target = tmp_path / "state.json"
    atomic_write_json(target, {"ok": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert list(tmp_path.glob("*.tmp")) == []

