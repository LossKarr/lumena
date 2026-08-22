from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.documents.document_intent import DOCUMENT_KINDS
from src.documents.delivery_manifest import compact_batch_observation
from src.documents.template_catalog import TemplateCatalog
from src.documents.template_renderer import TemplateRenderer
from src.documents.studio import DocumentStudio
from src.reasoning.handlers.documents import (
    _add_mission_publish_hint,
    _merge_studio_data,
    generate_studio_documents_handler,
)


ROOT = Path(__file__).resolve().parents[2]


def _result(kind: str, filename: str) -> dict:
    return {
        "path": f"C:/workspace/documents/{filename or kind}.pdf",
        "record": {
            "id": f"doc_{kind}", "sha256": f"sha-{kind}", "format": "pdf",
            "size": 1000, "template_id": kind,
        },
        "recipe": {"kind": kind, "template_id": kind, "output_format": "pdf"},
        "render_proof": {"status": "render_verified", "verified": True},
    }


class _FakeStudio:
    def __init__(self, *, fail_kind: str = ""):
        self.fail_kind = fail_kind
        self.calls = []
        self.catalog = SimpleNamespace(
            list_templates=lambda: [
                SimpleNamespace(valid=True, manifest=SimpleNamespace(kind=kind))
                for kind in DOCUMENT_KINDS
            ],
            read_sample_data=lambda record: {
                "sample": record.manifest.kind,
                "nested": {"kept": True, "override": "sample"},
            },
        )

    def resolve_template(self, *, template_id="", kind="", output_format="pdf"):
        selected = template_id or kind
        return SimpleNamespace(valid=True, manifest=SimpleNamespace(kind=selected))

    @staticmethod
    def parse_json_object(value, *, field):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError(f"{field} doit etre un objet")
        return parsed

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["kind"] == self.fail_kind:
            raise ValueError("echec cible")
        return _result(kwargs["kind"], kwargs["filename"])


@pytest.mark.asyncio
async def test_batch_generates_30_in_order_and_merges_partial_data(monkeypatch):
    from src.documents import studio as studio_module

    studio = _FakeStudio()
    monkeypatch.setattr(studio_module, "get_document_studio", lambda: studio)
    requests = [
        {"kind": kind, "filename": f"doc-{index}", "data": {"nested": {"override": index}}}
        for index, kind in enumerate(DOCUMENT_KINDS, start=1)
    ]

    result = await generate_studio_documents_handler(None, json.dumps(requests))

    assert result.success is True
    assert result.status_code == "success"
    assert [call["kind"] for call in studio.calls] == list(DOCUMENT_KINDS)
    assert len(result.sub_results) == 30
    assert all(sub.success for sub in result.sub_results)
    assert studio.calls[0]["data"]["sample"] == DOCUMENT_KINDS[0]
    assert studio.calls[0]["data"]["nested"] == {"kept": True, "override": 1}


@pytest.mark.asyncio
async def test_batch_keeps_successes_when_one_document_fails(monkeypatch):
    from src.documents import studio as studio_module

    studio = _FakeStudio(fail_kind="facture")
    monkeypatch.setattr(studio_module, "get_document_studio", lambda: studio)
    requests = [{"kind": "devis"}, {"kind": "facture"}, {"kind": "nda"}]

    result = await generate_studio_documents_handler(None, requests)
    payload = json.loads(result.output)

    assert result.success is True
    assert result.status_code == "partial"
    assert payload["generated"] == 2
    assert payload["failed"] == 1
    assert payload["errors"][0]["index"] == 2
    assert payload["errors"][0]["template_id"] == "facture"
    assert payload["errors"][0]["kind"] == "facture"
    assert payload["errors"][0]["error"].startswith(
        "Document Studio [facture]: echec cible."
    )
    assert "Reessaie uniquement kind='facture'" in payload["errors"][0]["error"]
    assert '"sample":"facture"' in payload["errors"][0]["error"]
    assert [sub.success for sub in result.sub_results] == [True, False, True]
    assert "echec cible" in result.sub_results[1].content


def test_mission_publish_hint_is_strictly_mission_scoped():
    payload = {"path": "C:/workspace/documents/report.pdf"}

    assert _add_mission_publish_hint(dict(payload), None) == payload
    assert _add_mission_publish_hint(
        dict(payload),
        SimpleNamespace(is_mission_run=False, runtime_task_id="task_chat"),
    ) == payload
    assert _add_mission_publish_hint(
        dict(payload),
        SimpleNamespace(is_mission_run=True, runtime_task_id=""),
    ) == payload

    mission_payload = _add_mission_publish_hint(
        dict(payload),
        SimpleNamespace(is_mission_run=True, runtime_task_id="task_mission"),
    )
    hint = mission_payload["mission_publish_hint"]
    assert "publish_mission_workspace" in hint
    assert "automatiquement" in hint
    assert "Copy-Item" in hint
    assert "Ne les copie" in hint


@pytest.mark.asyncio
async def test_batch_mission_result_redirects_directly_to_publisher(monkeypatch):
    from src.documents import studio as studio_module

    studio = _FakeStudio()
    monkeypatch.setattr(studio_module, "get_document_studio", lambda: studio)
    ctx = SimpleNamespace(is_mission_run=True, runtime_task_id="task_batch")

    result = await generate_studio_documents_handler(ctx, [{"kind": "devis"}])
    payload = json.loads(result.output)

    assert result.success is True
    assert payload["generated"] == 1
    assert "publish_mission_workspace" in payload["mission_publish_hint"]
    assert "Copy-Item" in payload["mission_publish_hint"]


@pytest.mark.asyncio
async def test_exact_eleven_document_runtime_batch_is_render_verified(
    monkeypatch, tmp_path,
):
    from src.documents import studio as studio_module

    studio = DocumentStudio(
        root=tmp_path / "studio",
        builtin_root=ROOT / "assets" / "templates",
        output_root=tmp_path / "output",
    )
    monkeypatch.setattr(studio_module, "get_document_studio", lambda: studio)
    kinds = [
        "facture",
        "devis",
        "bon_commande",
        "note_frais",
        "rapport_activite",
        "feuille_temps",
        "procedure_accueil",
        "compte_rendu_reunion",
        "plan_action",
        "attestation",
        "lettre_officielle",
    ]
    requests = [
        {
            "kind": kind,
            "filename": f"runtime-{index:02d}",
            "data": {"date": "21 juillet 2026"},
        }
        for index, kind in enumerate(kinds, start=1)
    ]
    requests[6]["data"]["steps"] = [
        {"number": index, "title": f"Etape {index}", "instruction": "Executer"}
        for index in range(1, 6)
    ]
    requests[7]["data"]["participants"] = [
        {"name": "Alice"},
        {"name": "Benoit"},
        {"name": "Camille"},
    ]

    result = await generate_studio_documents_handler(None, requests)
    payload = json.loads(result.output)

    assert result.success is True
    assert result.status_code == "success"
    assert payload["requested"] == 11
    assert payload["generated"] == 11
    assert payload["failed"] == 0
    assert payload["receipt_id"].startswith("doclot_")
    assert [item["kind"] for item in payload["documents"]] == [
        "facture",
        "devis",
        "bon_commande",
        "note_frais",
        "rapport_activite",
        "feuille_temps",
        "procedure_operationnelle",
        "proces_verbal",
        "plan_action",
        "attestation",
        "lettre_officielle",
    ]
    assert all(item["render_verified"] for item in payload["documents"])
    assert all(Path(item["path"]).is_file() for item in payload["documents"])


@pytest.mark.asyncio
async def test_batch_merges_sample_from_exact_selected_template(monkeypatch):
    from src.documents import studio as studio_module

    studio = _FakeStudio()
    studio.resolve_template = lambda **kwargs: SimpleNamespace(
        valid=True,
        manifest=SimpleNamespace(kind="facture-personnalisee"),
    )
    monkeypatch.setattr(studio_module, "get_document_studio", lambda: studio)

    result = await generate_studio_documents_handler(
        None,
        [{
            "kind": "facture",
            "template_id": "facture-personnalisee",
            "data": {"nested": {"override": "user"}},
        }],
    )

    assert result.success is True
    assert studio.calls[0]["template_id"] == "facture-personnalisee"
    assert studio.calls[0]["data"]["sample"] == "facture-personnalisee"
    assert studio.calls[0]["data"]["nested"] == {"kept": True, "override": "user"}


@pytest.mark.asyncio
@pytest.mark.parametrize("requests", ["not-json", [], [{}] * 31])
async def test_batch_rejects_invalid_or_oversized_requests(monkeypatch, requests):
    from src.documents import studio as studio_module

    monkeypatch.setattr(studio_module, "get_document_studio", lambda: _FakeStudio())
    result = await generate_studio_documents_handler(None, requests)

    assert result.success is False
    assert result.sub_results == ()


def test_structured_list_items_keep_required_plan_action_fields(tmp_path):
    catalog = TemplateCatalog(tmp_path / "studio", ROOT / "assets" / "templates")
    record = catalog.get("plan_action")
    sample = catalog.read_sample_data(record)

    merged = _merge_studio_data(sample, {
        "actions": [{"action": "Ouvrir l'agence", "owner": "Direction"}],
    })

    action = merged["actions"][0]
    assert action["action"] == "Ouvrir l'agence"
    assert action["owner"] == "Direction"
    assert action["id"] == "ACT-01"
    assert action["due"]
    assert action["priority"]
    assert action["status"]
    assert action["indicator"]
    html = TemplateRenderer().render_html(
        record, catalog.read_source(record), merged,
    )
    assert "Ouvrir l" in html
    assert "agence" in html
    assert "ACT-01" in html


def test_structured_list_uses_single_sample_row_as_prototype():
    base = {"rows": [{"id": "ROW-01", "status": "A faire"}]}
    patch = {"rows": [{"label": "Premier"}, {"label": "Deuxieme"}]}

    merged = _merge_studio_data(base, patch)

    assert merged["rows"] == [
        {"id": "ROW-01", "status": "A faire", "label": "Premier"},
        {"id": "ROW-01", "status": "A faire", "label": "Deuxieme"},
    ]


def test_structured_list_extends_multi_row_procedure_schema(tmp_path):
    catalog = TemplateCatalog(tmp_path / "studio", ROOT / "assets" / "templates")
    record = catalog.get("procedure_operationnelle")
    sample = catalog.read_sample_data(record)
    steps = [
        {"number": index, "title": f"Etape {index}", "instruction": "Executer"}
        for index in range(1, 6)
    ]

    merged = _merge_studio_data(sample, {"steps": steps})

    assert len(merged["steps"]) == 5
    assert all(step["evidence"] for step in merged["steps"])
    html = TemplateRenderer().render_html(
        record, catalog.read_source(record), merged,
    )
    assert "Etape 5" in html


def test_structured_list_extends_multi_row_participant_schema(tmp_path):
    catalog = TemplateCatalog(tmp_path / "studio", ROOT / "assets" / "templates")
    record = catalog.get("proces_verbal")
    sample = catalog.read_sample_data(record)

    merged = _merge_studio_data(sample, {
        "participants": [
            {"name": "Alice"},
            {"name": "Benoit"},
            {"name": "Camille"},
        ],
    })

    assert len(merged["participants"]) == 3
    assert all(participant["role"] for participant in merged["participants"])
    html = TemplateRenderer().render_html(
        record, catalog.read_source(record), merged,
    )
    assert "Camille" in html


def test_batch_compaction_preserves_exact_failure_and_receipt():
    raw = {
        "requested": 11,
        "generated": 10,
        "failed": 1,
        "receipt_id": "doclot_runtime",
        "documents": [
            {
                "kind": f"kind_{index}",
                "filename": f"doc-{index}.pdf",
                "render_verified": True,
                "page_count": index,
                "sha256": "x" * 64,
                "path": f"C:/workspace/doc-{index}.pdf",
            }
            for index in range(1, 11)
        ],
        "errors": [{
            "index": 8,
            "kind": "proces_verbal",
            "template_id": "proces_verbal",
            "error": (
                "Document Studio [proces_verbal]: champ role absent. "
                "Reessaie uniquement kind='proces_verbal' avec ce data cible: "
                + "x" * 4000
            ),
        }],
    }

    compacted = compact_batch_observation(json.dumps(raw))
    payload = json.loads(compacted)

    assert payload["requested"] == 11
    assert payload["generated"] == 10
    assert payload["failed"] == 1
    assert payload["receipt_id"] == "doclot_runtime"
    assert payload["errors"][0]["index"] == 8
    assert payload["errors"][0]["kind"] == "proces_verbal"
    assert "champ role absent" in payload["errors"][0]["error"]
    assert "Reessaie uniquement kind='proces_verbal'" in payload["errors"][0]["error"]
    assert len(payload["documents"]) == 10
    assert payload["documents"][5]["page_count"] == 6
    assert "sha256" not in payload["documents"][0]


def test_batch_compaction_rejects_unrelated_observations():
    assert compact_batch_observation("pas du json") is None
    assert compact_batch_observation('{"requested": 2}') is None


def test_scalar_and_empty_lists_keep_replacement_semantics():
    base = {"metrics": ["sample"], "rows": [{"id": "ROW-01"}]}

    assert _merge_studio_data(base, {"metrics": ["user"]})["metrics"] == ["user"]
    assert _merge_studio_data(base, {"rows": []})["rows"] == []
