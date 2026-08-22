from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.documents.document_intent import resolve_document_route
from src.documents.delivery_manifest import (
    build_document_workflow_final,
    parse_generation_proof,
    workflow_has_new_proof,
)
from src.reasoning.handlers.contracts import SubToolResult
from src.reasoning.handlers.documents import convert_library_document_handler
from src.reasoning.plan_progress import document_workflow_task_operation
from src.reasoning.react import ReActLoop
from src.reasoning.react_config import Observation, TaskItem


QUERY = (
    "Crée un procès-verbal PDF pour l'association Horizon Vert, ouvre-le avec sa "
    "preuve, crée une révision qui modifie uniquement la deuxième décision en "
    "« Budget participatif validé à 12 500 € », relis le PDF révisé pour confirmer "
    "cette phrase, récupère l'historique parent/enfant, exporte cette version révisée "
    "en HTML, puis vérifie que le PDF original, le PDF révisé et le HTML sont tous "
    "présents dans la bibliothèque."
)


def _action(name: str, args=None):
    return SimpleNamespace(tool_name=name, tool_args=args or {})


def _proof(document_id: str, path: str, *, fmt: str = "pdf") -> str:
    return json.dumps({
        "kind": "proces_verbal",
        "document_id": document_id,
        "filename": path.rsplit("/", 1)[-1],
        "path": path,
        "sha256": f"sha-{document_id}",
        "template_id": "proces_verbal",
        "format": fmt,
        "size": 1200,
        "render_status": "render_verified",
        "render_verified": True,
        "page_count": 2,
    })


def _state():
    return SimpleNamespace(
        _document_route=resolve_document_route(QUERY, mode="agent"),
        _document_workflow_evidence={
            "batch_proofs": {}, "generation_events": [],
            "open_events": [], "revision_events": [], "revision_records": [],
            "verification_events": [], "history_events": [],
            "export_events": [], "library_events": [], "event_counter": 0,
        },
        _document_workflow_target_proof=None,
        _document_delivery_reference_id="",
        _document_delivery_reference_signature=(),
        history=[],
    )


def _record_batch_parent(state, document_id: str = "doc-parent") -> None:
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("generate_studio_documents"),
        Observation(
            json.dumps({
                "requested": 1, "generated": 1, "failed": 0,
                "receipt_id": "doclot-horizon",
            }),
            sub_results=(SubToolResult(
                tool_name="generate_studio_document",
                success=True,
                content=_proof(document_id, "C:/documents/horizon.pdf"),
                args={"kind": "proces_verbal"},
            ),),
        ),
    )


def _record_through_verification(state) -> None:
    _record_batch_parent(state)
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_document_delivery", {"receipt_id": "doclot-horizon"}),
        Observation(json.dumps({
            "receipt_id": "doclot-horizon", "requested": 1,
            "opened": 1, "failed": 0,
            "files": [{"filename": "horizon.pdf", "path": "C:/documents/horizon.pdf"}],
        })),
    )
    child = json.loads(_proof("doc-child", "C:/documents/horizon-revision.pdf"))
    child["changed_fields"] = {
        "resolutions": [{
            "titre": "Décision 2",
            "details": "Budget participatif validé à 12 500 €",
        }],
    }
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("revise_studio_document", {
            "document_id": "doc-parent",
            "data": child["changed_fields"],
        }),
        Observation(json.dumps(child, ensure_ascii=False)),
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("read_document", {"path": "C:/documents/horizon-revision.pdf"}),
        Observation("Décision 2 — Budget participatif validé à 12 500 €"),
    )


def test_horizon_route_distinguishes_every_lifecycle_operation():
    route = resolve_document_route(QUERY, mode="agent")

    assert route.requested_kinds == ("proces_verbal",)
    assert [action.operation for action in route.workflow_actions] == [
        "generate", "open", "revise", "verify", "history", "export",
        "library_verify",
    ]
    export = next(action for action in route.workflow_actions if action.operation == "export")
    assert export.output_format == "html"
    assert document_workflow_task_operation(
        "Vérifier que les 3 fichiers sont dans la bibliothèque"
    ) == "library_verify"
    assert document_workflow_task_operation("Récupérer l'historique parent/enfant") == "history"
    assert document_workflow_task_operation("Exporter la version révisée en HTML") == "export"


def test_single_generation_can_persist_an_exact_open_receipt(monkeypatch, tmp_path):
    from src.documents import studio as studio_module

    state = _state()
    monkeypatch.setattr(
        studio_module, "get_document_studio", lambda: SimpleNamespace(root=tmp_path),
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("generate_studio_document", {"kind": "proces_verbal"}),
        Observation(_proof("doc-parent", "C:/documents/horizon.pdf")),
    )

    receipt_id = ReActLoop._ensure_document_delivery_reference(state)

    assert receipt_id.startswith("doclot_")
    assert ReActLoop._document_workflow_pending_action(state).operation == "open"


def test_authoritative_batch_supersedes_unitary_attempt_and_children_never_replace_parent():
    state = _state()
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("generate_studio_document", {"kind": "proces_verbal"}),
        Observation(_proof("doc-first-attempt", "C:/documents/first.pdf")),
    )
    _record_batch_parent(state)
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("revise_studio_document", {
            "document_id": "doc-parent", "data": {"decision": "nouvelle"},
        }),
        Observation(_proof("doc-child", "C:/documents/child.pdf")),
    )

    manifest, missing, unverified = ReActLoop._structured_document_delivery_manifest(state)

    assert not missing and not unverified
    assert [proof.document_id for proof in manifest] == ["doc-parent"]


def test_horizon_lifecycle_reconciles_eight_tasks_only_with_exact_proofs():
    state = _state()
    _record_through_verification(state)
    verified_signature = ReActLoop._document_workflow_progress_signature(state)
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("get_document_history", {"document_id": "doc-child"}),
        Observation(json.dumps({
            "document": {"id": "doc-child", "parent_id": "doc-parent"},
            "transformations": [],
        })),
    )
    history_signature = ReActLoop._document_workflow_progress_signature(state)
    assert workflow_has_new_proof(verified_signature, history_signature) is True
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("convert_library_document", {
            "document_id": "doc-child", "output_format": "html",
        }),
        Observation(json.dumps({
            "record": {
                "id": "doc-html", "parent_id": "doc-child", "format": "html",
                "filename": "horizon.html", "path": "C:/documents/horizon.html",
            },
        })),
    )
    export_signature = ReActLoop._document_workflow_progress_signature(state)
    assert workflow_has_new_proof(history_signature, export_signature) is True
    for document_id in ("doc-parent", "doc-child", "doc-html"):
        ReActLoop._record_document_workflow_evidence(
            state,
            _action("get_document_record", {"document_id": document_id}),
            Observation(json.dumps({"id": document_id})),
        )
    library_signature = ReActLoop._document_workflow_progress_signature(state)
    assert workflow_has_new_proof(export_signature, library_signature) is True

    proof_state = ReActLoop._document_workflow_proof_state(state)
    assert proof_state["history"] is not None
    assert proof_state["export"] is not None
    assert proof_state["library_verify"]["document_ids"] == (
        "doc-child", "doc-html", "doc-parent",
    )
    assert ReActLoop._document_workflow_pending_action(state) is None

    state._task_plan = [
        TaskItem("Consulter le modèle procès-verbal", completed=True),
        TaskItem("Générer le PDF original", completed=True),
        TaskItem("Ouvrir le document avec sa preuve"),
        TaskItem("Créer une révision modifiant la 2e décision"),
        TaskItem("Relire le PDF révisé et confirmer la phrase"),
        TaskItem("Récupérer l'historique parent/enfant"),
        TaskItem("Exporter la version révisée en HTML"),
        TaskItem("Vérifier que les 3 fichiers sont dans la bibliothèque"),
    ]
    state._emit_plan_state = lambda **_kwargs: None
    assert ReActLoop._reconcile_document_workflow_plan(state, 9) == 6
    assert all(task.completed for task in state._task_plan)


def test_wrong_parent_wrong_export_and_two_of_three_library_records_stay_pending():
    state = _state()
    _record_through_verification(state)
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("get_document_history", {"document_id": "doc-child"}),
        Observation(json.dumps({
            "document": {"id": "doc-child", "parent_id": "doc-other"},
        })),
    )
    assert ReActLoop._document_workflow_pending_action(state).operation == "history"

    ReActLoop._record_document_workflow_evidence(
        state,
        _action("get_document_history", {"document_id": "doc-child"}),
        Observation(json.dumps({
            "document": {"id": "doc-child", "parent_id": "doc-parent"},
        })),
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("convert_library_document", {
            "document_id": "doc-other", "output_format": "html",
        }),
        Observation(json.dumps({
            "record": {
                "id": "doc-html-wrong", "parent_id": "doc-other",
                "format": "html", "path": "C:/documents/wrong.html",
            },
        })),
    )
    assert ReActLoop._document_workflow_pending_action(state).operation == "export"

    ReActLoop._record_document_workflow_evidence(
        state,
        _action("convert_library_document", {
            "document_id": "doc-child", "output_format": "html",
        }),
        Observation(json.dumps({
            "record": {
                "id": "doc-html", "parent_id": "doc-child", "format": "html",
                "path": "C:/documents/horizon.html",
            },
        })),
    )
    for document_id in ("doc-parent", "doc-child"):
        ReActLoop._record_document_workflow_evidence(
            state,
            _action("get_document_record", {"document_id": document_id}),
            Observation(json.dumps({"id": document_id})),
        )
    assert ReActLoop._document_workflow_pending_action(state).operation == "library_verify"


def test_lifecycle_final_reports_parent_child_export_and_exact_library_ids():
    parent = parse_generation_proof(_proof("doc-parent", "C:/documents/horizon.pdf"))
    child = parse_generation_proof(
        _proof("doc-child", "C:/documents/horizon-revision.pdf")
    )
    assert parent is not None and child is not None

    final = build_document_workflow_final(
        [parent],
        requested_count=1,
        receipt_id="doclot-horizon",
        opened=1,
        failed=0,
        target_ordinal=1,
        target=parent,
        revised=child,
        changed_fields={"decision": "Budget participatif validé à 12 500 €"},
        verification_path=child.path,
        history_parent_id="doc-parent",
        exported_document_id="doc-html",
        exported_path="C:/documents/horizon.html",
        library_document_ids=("doc-parent", "doc-child", "doc-html"),
    )

    assert "parent `doc-parent` -> enfant `doc-child`" in final
    assert "Export: `doc-html`" in final
    assert "Bibliotheque: 3/3" in final


@pytest.mark.asyncio
async def test_generated_studio_pdf_to_html_uses_exact_recipe_fallback(monkeypatch):
    from src.documents import studio as studio_module

    calls = []
    record = SimpleNamespace(
        id="doc-child", format="pdf", source_kind="generated",
        filename="horizon-revision.pdf",
    )
    studio = SimpleNamespace(
        library=SimpleNamespace(resolve_reference=lambda _value, **_kwargs: record),
        conversions=SimpleNamespace(convert=lambda *_args: (_ for _ in ()).throw(
            ValueError("Conversion pdf->html not supported")
        )),
    )

    async def revise(document_id, **kwargs):
        calls.append((document_id, kwargs))
        return {"record": {"id": "doc-html", "parent_id": document_id}}

    studio.revise = revise
    monkeypatch.setattr(studio_module, "get_document_studio", lambda: studio)

    result = await convert_library_document_handler(None, "doc-child", "html")
    payload = json.loads(result.output)

    assert result.success is True
    assert payload["conversion_mode"] == "studio_recipe"
    assert calls == [("doc-child", {
        "data": {}, "output_format": "html", "filename": "horizon-revision.html",
    })]
