from __future__ import annotations

import json
from types import SimpleNamespace

from src.documents.document_intent import resolve_document_route
from src.reasoning.handlers.contracts import SubToolResult
from src.reasoning.plan_progress import (
    document_plan_tool_can_complete_task,
    document_workflow_task_operation,
)
from src.reasoning.react import ReActLoop
from src.reasoning.react_config import Observation, TaskItem


def _action(name: str, args=None):
    return SimpleNamespace(tool_name=name, tool_args=args or {})


def _step(name: str, args=None, content="", success=True, sub_results=()):
    return SimpleNamespace(
        action=_action(name, args),
        observation=Observation(content, success=success, sub_results=sub_results),
    )


def _proof_json(template_id: str, index: int, *, verified: bool = True) -> str:
    return json.dumps({
        "kind": f"kind-{index}",
        "document_id": f"doc-{index}",
        "filename": f"document-{index:02d}.pdf",
        "path": f"C:/documents/document-{index:02d}.pdf",
        "sha256": f"sha-{index}",
        "template_id": template_id,
        "format": "pdf",
        "size": 100 + index,
        "logo_id": "",
        "render_status": "render_verified" if verified else "failed",
        "render_verified": verified,
    })


def _batch_observation(template_ids: list[str], offset: int = 0) -> Observation:
    return Observation(
        "batch result",
        sub_results=tuple(
            SubToolResult(
                tool_name="generate_studio_document",
                success=True,
                content=_proof_json(template_id, offset + index),
                args={"kind": f"kind-{offset + index}", "template_id": template_id},
            )
            for index, template_id in enumerate(template_ids, start=1)
        ),
    )


def _state():
    route = resolve_document_route(
        "Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, "
        "dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, "
        "identifie un champ réellement modifiable, remplace sa valeur par "
        "TEST-REVISION-2026, puis vérifie la nouvelle version.",
        mode="agent",
    )
    custom = [{"id": f"custom-{index}"} for index in range(1, 5)]
    builtin = [{"id": f"builtin-{index}"} for index in range(1, 31)]
    state = SimpleNamespace(
        _document_route=route,
        _document_catalog_evidence={},
        _document_workflow_evidence={
            "batch_proofs": {}, "open_events": [], "revision_events": [],
        },
        _document_workflow_target_proof=None,
        history=[
            _step(
                "list_document_models",
                {"origin": "custom", "limit": 4, "sort": "recent"},
                json.dumps({"models": custom}),
            ),
            _step(
                "list_document_models",
                {"origin": "builtin", "limit": 30, "sort": "name"},
                json.dumps({"models": builtin}),
            ),
        ],
    )
    for step in state.history:
        ReActLoop._record_document_catalog_evidence(
            state, step.action, step.observation,
        )
    return state, route


def _record_full_manifest(state) -> None:
    custom_ids = [f"custom-{index}" for index in range(1, 5)]
    builtin_ids = [f"builtin-{index}" for index in range(1, 31)]
    ReActLoop._record_document_workflow_evidence(
        state, _action("generate_studio_documents"),
        _batch_observation(custom_ids),
    )
    ReActLoop._record_document_workflow_evidence(
        state, _action("generate_studio_documents"),
        _batch_observation(builtin_ids, offset=4),
    )


def test_parallel_catalog_phase_refuses_nested_document_mutation():
    state, _route = _state()
    refused = ReActLoop._structured_document_tool_gate(
        state,
        "parallel_tools",
        {"tool_calls": [
            {
                "name": "list_document_models",
                "args": {"origin": "builtin", "limit": 30, "sort": "name"},
            },
            {
                "name": "generate_studio_documents",
                "args": {"requests": [{"template_id": "builtin-1"}]},
            },
        ]},
    )
    assert refused is not None
    assert refused.origin == "document_policy"
    assert "deux phases" in refused.content
    assert "generate_studio_documents" in refused.content


def test_parallel_catalog_phase_accepts_only_exact_list_calls():
    state, _route = _state()
    allowed = ReActLoop._structured_document_tool_gate(
        state,
        "parallel_tools",
        {"tool_calls": [
            {
                "name": "list_document_models",
                "args": {"origin": "custom", "limit": 4, "sort": "recent"},
            },
            {
                "name": "list_document_models",
                "args": {"origin": "builtin", "limit": 30, "sort": "name"},
            },
        ]},
    )
    assert allowed is None


def test_wrong_catalog_sort_is_rejected_with_exact_guidance():
    state, _route = _state()
    refused = ReActLoop._structured_document_tool_gate(
        state,
        "list_document_models",
        {"origin": "builtin", "limit": 30, "sort": "recent"},
    )
    assert refused is not None
    assert "sort='name'" in refused.content
    assert "exactement" in refused.content


def test_kind_filtered_catalog_cannot_impersonate_count_selection():
    state, _route = _state()
    refused = ReActLoop._structured_document_tool_gate(
        state,
        "list_document_models",
        {"origin": "builtin", "limit": 30, "sort": "name", "kind": "facture"},
    )
    assert refused is not None
    assert "Parametres catalogue incorrects" in refused.content

    rows = [{"id": "facture-only"}]
    ReActLoop._record_document_catalog_evidence(
        state,
        _action(
            "list_document_models",
            {"origin": "builtin", "limit": 30, "sort": "name", "kind": "facture"},
        ),
        Observation(json.dumps({"models": rows})),
    )
    key = ReActLoop._document_catalog_evidence_key(
        {"origin": "builtin", "limit": 30, "sort": "name"},
    )
    assert [row["id"] for row in state._document_catalog_evidence[key]] == [
        f"builtin-{index}" for index in range(1, 31)
    ]


def test_list_templates_cannot_bypass_exact_catalog_selection():
    state, _route = _state()

    refused = ReActLoop._structured_document_tool_gate(state, "list_templates", {})

    assert refused is not None
    assert refused.origin == "document_policy"
    assert "list_document_models" in refused.content


def test_batch_retry_stays_in_custom_group_before_builtin_group():
    state, _route = _state()
    custom_ids = [f"custom-{index}" for index in range(1, 5)]
    builtin_ids = [f"builtin-{index}" for index in range(1, 31)]

    cross_group = ReActLoop._structured_document_tool_gate(
        state,
        "generate_studio_documents",
        {"requests": [
            {"template_id": template_id} for template_id in custom_ids + builtin_ids[:2]
        ]},
    )
    assert cross_group is not None
    assert "groupe custom" in cross_group.content

    ReActLoop._record_document_workflow_evidence(
        state,
        _action("generate_studio_documents"),
        _batch_observation(custom_ids[:2]),
    )
    wrong_retry = ReActLoop._structured_document_tool_gate(
        state,
        "generate_studio_documents",
        {"requests": [{"template_id": builtin_ids[0]}]},
    )
    assert wrong_retry is not None
    assert "custom-3, custom-4" in wrong_retry.content

    exact_retry = ReActLoop._structured_document_tool_gate(
        state,
        "generate_studio_documents",
        {"requests": [{"template_id": value} for value in custom_ids[2:]]},
    )
    assert exact_retry is None

    ReActLoop._record_document_workflow_evidence(
        state,
        _action("generate_studio_documents"),
        _batch_observation(custom_ids[2:], offset=2),
    )
    builtin_batch = ReActLoop._structured_document_tool_gate(
        state,
        "generate_studio_documents",
        {"requests": [{"template_id": value} for value in builtin_ids]},
    )
    assert builtin_batch is None


def test_catalog_plan_credit_requires_exact_origin_evidence():
    state, route = _state()
    builtin_key = ReActLoop._document_catalog_evidence_key({
        "origin": "builtin", "limit": 30, "sort": "name",
    })
    builtin_rows = state._document_catalog_evidence.pop(builtin_key)
    state._task_plan = [
        TaskItem("Lister les 4 modèles personnalisés"),
        TaskItem("Lister les 30 modèles intégrés"),
    ]
    state._emit_plan_state = lambda **_kwargs: None

    assert document_plan_tool_can_complete_task(
        "list_document_models", state._task_plan[1].description,
        compound_workflow=True,
    ) is False
    assert ReActLoop._reconcile_document_catalog_plan(state, 2) == 1
    assert state._task_plan[0].completed is True
    assert state._task_plan[1].completed is False

    state._document_catalog_evidence[builtin_key] = builtin_rows
    assert ReActLoop._reconcile_document_catalog_plan(state, 3) == 1
    assert state._task_plan[1].completed is True
    assert state._task_plan[1].completion_evidence == (
        f"catalogue exact origin=builtin, limit={route.selections[1].limit}, sort=name"
    )


def test_catalog_evidence_never_completes_a_generation_task():
    state, _route = _state()
    state._task_plan = [TaskItem("Générer les 30 modèles intégrés")]
    state._emit_plan_state = lambda **_kwargs: None

    assert ReActLoop._reconcile_document_catalog_plan(state, 4) == 0
    assert state._task_plan[0].completed is False


def test_batch_proofs_survive_compacted_history_and_prevent_duplicate_retry():
    state, route = _state()
    _record_full_manifest(state)
    state.history = [
        _step("list_document_models", content="{...compacte...}"),
        _step("generate_studio_documents", content="{...compacte...}"),
    ]

    manifest, missing, unverified = ReActLoop._structured_document_delivery_manifest(state)
    assert len(manifest) == route.requested_count == 34
    assert missing == ()
    assert unverified == ()


def test_individual_generation_proofs_survive_history_compaction_exactly():
    query = (
        "Cree exactement 6 documents RH : un contrat de travail, une demande "
        "de conge, un entretien annuel, une note de frais, un ordre de mission "
        "et une fiche de poste. Ouvre les 6 documents puis modifie et relis "
        "l'entretien annuel."
    )
    route = resolve_document_route(query, mode="agent")
    state = SimpleNamespace(
        _document_route=route,
        _document_workflow_evidence={
            "batch_proofs": {}, "generation_events": [],
            "open_events": [], "revision_events": [],
        },
        history=[],
    )
    generated = (
        "contrat_travail",
        "demande_conge",
        "entretien_annuel",
        "note_frais",
        "fiche_poste",
    )
    for index, kind in enumerate(generated, start=1):
        content = json.dumps({
            "kind": kind,
            "document_id": f"doc-{kind}",
            "filename": f"{kind}.pdf",
            "path": f"C:/documents/{kind}.pdf",
            "sha256": f"sha-{index}",
            "template_id": kind,
            "format": "pdf",
            "size": 100 + index,
            "render_status": "render_verified",
            "render_verified": True,
        })
        ReActLoop._record_document_workflow_evidence(
            state,
            _action("generate_studio_document", {"kind": kind}),
            Observation(content),
        )

    state.history = []
    manifest, missing, unverified = ReActLoop._structured_document_delivery_manifest(state)

    assert route.requested_count == 6
    assert [proof.kind for proof in manifest] == list(generated)
    assert missing == ("ordre_mission",)
    assert unverified == ()


def test_open_four_of_thirty_four_never_completes_quantitative_plan_task():
    state, _route = _state()
    _record_full_manifest(state)
    state._task_plan = [TaskItem("Ouvrir les 34 documents")]
    state._emit_plan_state = lambda **_kwargs: None

    assert document_plan_tool_can_complete_task(
        "open_document_delivery",
        state._task_plan[0].description,
        compound_workflow=True,
    ) is False

    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_document_delivery", {"receipt_id": "doclot_custom"}),
        Observation(json.dumps({"requested": 4, "opened": 4, "failed": 0})),
    )
    assert ReActLoop._document_workflow_pending_action(state).operation == "open"
    assert ReActLoop._reconcile_document_workflow_plan(state, 5) == 0
    assert state._task_plan[0].completed is False

    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_document_delivery", {"receipt_id": "docbundle_all"}),
        Observation(json.dumps({"requested": 34, "opened": 34, "failed": 0})),
    )
    assert ReActLoop._document_workflow_pending_action(state).operation == "revise"
    assert ReActLoop._reconcile_document_workflow_plan(state, 6) == 1
    assert state._task_plan[0].completion_evidence.startswith("ouverture exacte 34/34")


def test_split_four_plus_thirty_open_receipts_complete_the_exact_workflow():
    state, _route = _state()
    _record_full_manifest(state)
    manifest, missing, unverified = ReActLoop._structured_document_delivery_manifest(state)
    assert not missing and not unverified and len(manifest) == 34

    def payload(rows, receipt_id):
        return json.dumps({
            "receipt_id": receipt_id,
            "requested": len(rows),
            "opened": len(rows),
            "failed": 0,
            "files": [
                {"filename": proof.filename, "path": proof.path}
                for proof in rows
            ],
        })

    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_document_delivery", {"receipt_id": "doclot_custom"}),
        Observation(payload(manifest[:4], "doclot_custom")),
    )
    assert ReActLoop._document_workflow_pending_action(state).operation == "open"
    assert ReActLoop._document_workflow_progress_signature(state)[0] == 4

    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_document_delivery", {"receipt_id": "doclot_builtin"}),
        Observation(payload(manifest[4:], "doclot_builtin")),
    )
    proof_state = ReActLoop._document_workflow_proof_state(state)
    assert proof_state["open"]["opened"] == 34
    assert proof_state["open"]["receipt_ids"] == (
        "doclot_custom", "doclot_builtin",
    )
    assert ReActLoop._document_workflow_pending_action(state).operation == "revise"


def test_complete_manifest_persists_an_idempotent_aggregate_bundle(tmp_path, monkeypatch):
    state, _route = _state()
    _record_full_manifest(state)
    (tmp_path / "delivery_receipts").mkdir()
    (tmp_path / "delivery_bundles").mkdir()
    monkeypatch.setattr(
        "src.documents.studio.get_document_studio",
        lambda: SimpleNamespace(root=tmp_path),
    )

    first = ReActLoop._ensure_document_delivery_reference(state)
    second = ReActLoop._ensure_document_delivery_reference(state)

    assert first.startswith("docbundle_")
    assert second == first
    assert (tmp_path / "delivery_bundles" / f"{first}.json").is_file()


def test_open_post_processing_uses_the_raw_payload_not_compacted_history():
    files = [
        {
            "filename": f"document-{index:02d}.pdf",
            "path": f"C:/documents/document-{index:02d}.pdf",
        }
        for index in range(1, 35)
    ]
    raw = Observation(json.dumps({
        "receipt_id": "docbundle_all",
        "requested": 34,
        "opened": 34,
        "failed": 0,
        "files": files,
        "padding": "x" * 5000,
    }))
    compacted = Observation("{...observation compactee...}")

    assert ReActLoop._document_open_payload(raw)["opened"] == 34
    assert ReActLoop._document_open_payload(compacted) is None


def test_revision_proof_is_durable_and_must_target_frozen_third_document():
    state, _route = _state()
    _record_full_manifest(state)
    target = ReActLoop._document_workflow_target(state)
    assert target.document_id == "doc-3"

    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_document_delivery", {"receipt_id": "docbundle_all"}),
        Observation(json.dumps({"requested": 34, "opened": 34, "failed": 0})),
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("revise_studio_document", {"document_id": "doc-old"}),
        Observation(_proof_json("custom-old", 900)),
    )
    assert ReActLoop._document_workflow_pending_action(state).operation == "revise"

    ReActLoop._record_document_workflow_evidence(
        state,
        _action("revise_studio_document", {
            "document_id": "doc-3",
            "data": {"numero": "TEST-REVISION-2026"},
        }),
        Observation(_proof_json("custom-3", 901)),
    )
    state.history = []  # proof remains authoritative after visible compaction
    assert ReActLoop._document_workflow_target(state).document_id == "doc-3"
    assert ReActLoop._document_workflow_pending_action(state).operation == "verify"

    ReActLoop._record_document_workflow_evidence(
        state,
        _action("read_document", {"path": "C:/documents/document-901.pdf"}),
        Observation("N° TEST-REVISION-2026"),
    )
    assert ReActLoop._document_workflow_pending_action(state) is None


def test_named_revision_and_wrapped_reread_complete_exact_workflow():
    query = (
        "Cr\u00e9e un contrat de travail, une demande de cong\u00e9, un compte rendu "
        "d\u2019entretien annuel, une note de frais, un ordre de mission et une "
        "fiche de poste. Ouvre les 6 documents, puis r\u00e9vise uniquement le "
        "compte rendu d\u2019entretien annuel pour ajouter CAP-LEADERSHIP-2042. "
        "Ouvre la version r\u00e9vis\u00e9e et relis-la."
    )
    route = resolve_document_route(query, mode="agent")
    state = SimpleNamespace(
        _document_route=route,
        _document_workflow_evidence={
            "batch_proofs": {}, "generation_events": [],
            "open_events": [], "revision_events": [],
        },
        _document_workflow_target_proof=None,
        history=[],
    )
    kinds = (
        "contrat_travail", "demande_conge", "entretien_annuel",
        "note_frais", "ordre_mission", "fiche_poste",
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("generate_studio_documents"),
        Observation(
            json.dumps({
                "requested": 6, "generated": 6, "failed": 0,
                "receipt_id": "doclot_runtime",
            }),
            sub_results=tuple(
                SubToolResult(
                    tool_name="generate_studio_document",
                    success=True,
                    content=json.dumps({
                        "kind": kind,
                        "document_id": f"doc-{kind}",
                        "filename": f"{kind}.pdf",
                        "path": f"C:/documents/{kind}.pdf",
                        "sha256": f"sha-{index}",
                        "template_id": kind,
                        "format": "pdf",
                        "size": 100 + index,
                        "render_status": "render_verified",
                        "render_verified": True,
                    }),
                    args={"kind": kind, "template_id": kind},
                )
                for index, kind in enumerate(kinds, start=1)
            ),
        ),
    )
    manifest, missing, unverified = (
        ReActLoop._structured_document_delivery_manifest(state)
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_document_delivery", {"receipt_id": "doclot_runtime"}),
        Observation(json.dumps({
            "receipt_id": "doclot_runtime",
            "requested": 6,
            "opened": 6,
            "failed": 0,
            "files": [
                {"filename": proof.filename, "path": proof.path}
                for proof in manifest
            ],
        })),
    )
    revised_payload = json.loads(_proof_json("entretien_annuel", 700))
    revised_payload["changed_fields"] = {
        "bilan": (
            "Sarah Morel progresse. "
            "Reference CAP-LEADERSHIP-2042."
        ),
    }
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("revise_studio_document", {
            "document_id": "doc-entretien_annuel",
            "data": revised_payload["changed_fields"],
        }),
        Observation(json.dumps(revised_payload)),
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("read_document", {"path": "C:/documents/document-700.pdf"}),
        Observation(
            "Sarah Morel progresse. Reference CAP-LEADERSHIP-\n2042."
        ),
    )

    assert not missing and not unverified
    assert route.requested_kinds == kinds
    assert ReActLoop._document_workflow_target(state).document_id == (
        "doc-entretien_annuel"
    )
    assert ReActLoop._document_workflow_pending_action(state) is None
    state._task_plan = [
        TaskItem("Reviser le compte rendu d'entretien annuel"),
        TaskItem("Relire le document revise"),
    ]
    state._emit_plan_state = lambda **_kwargs: None
    assert ReActLoop._reconcile_document_workflow_plan(state, 12) == 2
    assert all(task.completed for task in state._task_plan)
    assert all(task.completion_confidence == "strong" for task in state._task_plan)


def test_revision_and_verification_plan_credit_uses_exact_workflow_proof():
    state, _route = _state()
    _record_full_manifest(state)
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_document_delivery"),
        Observation(json.dumps({"requested": 34, "opened": 34, "failed": 0})),
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("revise_studio_document", {
            "document_id": "doc-3",
            "data": {"numero": "TEST-REVISION-2026"},
        }),
        Observation(_proof_json("custom-3", 902)),
    )
    state._task_plan = [
        TaskItem("Modifier le troisieme document"),
        TaskItem("Verifier la nouvelle version et donner le bilan"),
    ]
    state._emit_plan_state = lambda **_kwargs: None

    assert ReActLoop._reconcile_document_workflow_plan(state, 9) == 1
    assert [task.completed for task in state._task_plan] == [True, False]

    ReActLoop._record_document_workflow_evidence(
        state,
        _action("read_document", {"path": "C:/documents/document-902.pdf"}),
        Observation("Le numéro est TEST-REVISION-2026."),
    )
    assert ReActLoop._reconcile_document_workflow_plan(state, 10) == 1
    assert all(task.completed for task in state._task_plan)
    assert all(task.completion_confidence == "strong" for task in state._task_plan)


def test_full_replacement_verifies_only_authoritative_parent_child_changes():
    state, _route = _state()
    _record_full_manifest(state)
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_document_delivery", {"receipt_id": "docbundle_all"}),
        Observation(json.dumps({"requested": 34, "opened": 34, "failed": 0})),
    )
    full_payload = {
        "accent": "#e8892f",
        "collaborateur": {"name": "Sarah Morel"},
        "bilan": "Très bonne année. CAP-LEADERSHIP-2042",
    }
    revision_payload = json.loads(_proof_json("custom-3", 904))
    revision_payload["changed_fields"] = {
        "bilan": "Très bonne année. CAP-LEADERSHIP-2042",
    }
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("revise_studio_document", {
            "document_id": "doc-3",
            "data": full_payload,
            "replace_data": True,
        }),
        Observation(json.dumps(revision_payload)),
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("read_document", {"path": "C:/documents/document-904.pdf"}),
        Observation("Bilan\nTrès bonne année. CAP-LEADERSHIP-2042"),
    )

    proof = ReActLoop._document_workflow_proof_state(state)

    assert proof["verification"] is not None
    assert ReActLoop._document_workflow_pending_action(state) is None


def test_missing_changed_value_keeps_verification_pending():
    state, _route = _state()
    _record_full_manifest(state)
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_document_delivery", {"receipt_id": "docbundle_all"}),
        Observation(json.dumps({"requested": 34, "opened": 34, "failed": 0})),
    )
    revision_payload = json.loads(_proof_json("custom-3", 905))
    revision_payload["changed_fields"] = {"bilan": "CAP-LEADERSHIP-2042"}
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("revise_studio_document", {
            "document_id": "doc-3",
            "data": {"bilan": "CAP-LEADERSHIP-2042"},
        }),
        Observation(json.dumps(revision_payload)),
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("read_document", {"path": "C:/documents/document-905.pdf"}),
        Observation("Bilan sans la nouvelle valeur"),
    )

    assert ReActLoop._document_workflow_pending_action(state).operation == "verify"


def test_honest_incomplete_document_final_bypasses_generic_plan_guard():
    source = __import__("pathlib").Path(
        "src/reasoning/react.py"
    ).read_text(encoding="utf-8")

    assert "_document_workflow_incomplete_final = True" in source
    assert "and not _document_workflow_incomplete_final" in source


def test_reread_modified_document_is_verify_not_revision():
    assert document_workflow_task_operation("Relire le document modifie") == "verify"
    assert (
        document_workflow_task_operation(
            "Relire le document modifie et confirmer FESTIVAL-NANTES-730"
        )
        == "verify"
    )


def test_revision_is_refused_until_the_exact_bundle_has_been_opened():
    state, _route = _state()
    _record_full_manifest(state)

    refused = ReActLoop._structured_document_tool_gate(
        state,
        "revise_studio_document",
        {"document_id": "doc-3", "data": {"numero": "TEST-REVISION-2026"}},
    )
    assert refused is not None
    assert refused.origin == "document_policy"
    assert "34/34" in refused.content
    assert "open_document_delivery" in refused.content

    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_document_delivery", {"receipt_id": "docbundle_all"}),
        Observation(json.dumps({"requested": 34, "opened": 34, "failed": 0})),
    )
    assert ReActLoop._structured_document_tool_gate(
        state,
        "revise_studio_document",
        {"document_id": "doc-3", "data": {"numero": "TEST-REVISION-2026"}},
    ) is None


def test_pre_open_revision_never_satisfies_the_ordered_workflow():
    state, _route = _state()
    _record_full_manifest(state)
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("revise_studio_document", {
            "document_id": "doc-3", "data": {"numero": "TOO-EARLY"},
        }),
        Observation(_proof_json("custom-3", 903)),
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_document_delivery", {"receipt_id": "docbundle_all"}),
        Observation(json.dumps({"requested": 34, "opened": 34, "failed": 0})),
    )

    assert ReActLoop._document_workflow_pending_action(state).operation == "revise"


def test_open_before_generation_never_satisfies_the_workflow():
    state, _route = _state()
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_document_delivery", {"receipt_id": "docbundle_all"}),
        Observation(json.dumps({
            "receipt_id": "docbundle_all",
            "requested": 34,
            "opened": 34,
            "failed": 0,
        })),
    )
    _record_full_manifest(state)

    assert ReActLoop._document_workflow_pending_action(state).operation == "open"


def test_wrong_receipt_never_satisfies_the_workflow():
    state, _route = _state()
    _record_full_manifest(state)
    state._document_delivery_reference_id = "docbundle_expected"
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_document_delivery", {"receipt_id": "docbundle_other"}),
        Observation(json.dumps({
            "receipt_id": "docbundle_other",
            "requested": 34,
            "opened": 34,
            "failed": 0,
        })),
    )

    assert ReActLoop._document_workflow_pending_action(state).operation == "open"


def test_successful_batch_receipt_becomes_the_exact_workflow_reference():
    state, _route = _state()
    template_ids = [
        *(f"custom-{index}" for index in range(1, 5)),
        *(f"builtin-{index}" for index in range(1, 31)),
    ]
    result = Observation(
        json.dumps({
            "requested": 34,
            "generated": 34,
            "failed": 0,
            "receipt_id": "doclot_runtime_exact",
        }),
        sub_results=tuple(
                SubToolResult(
                    tool_name="generate_studio_document",
                    success=True,
                    content=_proof_json(template_id, index),
                    args={"kind": f"kind-{index}", "template_id": template_id},
                )
            for index, template_id in enumerate(template_ids, start=1)
        ),
    )

    ReActLoop._record_document_workflow_evidence(
        state, _action("generate_studio_documents"), result,
    )

    assert state._document_delivery_reference_id == "doclot_runtime_exact"
    assert state._document_delivery_reference_signature


def test_new_generation_invalidates_a_frozen_revision_target():
    state, _route = _state()
    _record_full_manifest(state)
    original = ReActLoop._document_workflow_target(state)
    assert original.document_id == "doc-3"

    ReActLoop._record_document_workflow_evidence(
        state,
        _action("generate_studio_documents"),
        _batch_observation(["custom-3"], offset=902),
    )

    assert state._document_workflow_target_proof is None
    assert ReActLoop._document_workflow_target(state).document_id == "doc-903"


def test_exact_duplicate_document_mutation_is_skipped_but_distinct_calls_are_not():
    args = {"requests": [{"kind": "devis", "filename": "devis.pdf"}]}

    assert ReActLoop._duplicate_document_mutation(
        "generate_studio_documents", args, "generate_studio_documents", dict(args),
    )
    assert not ReActLoop._duplicate_document_mutation(
        "generate_studio_documents",
        args,
        "generate_studio_documents",
        {"requests": [{"kind": "facture", "filename": "facture.pdf"}]},
    )
    assert not ReActLoop._duplicate_document_mutation(
        "read_document", {"path": "a.pdf"}, "read_document", {"path": "a.pdf"},
    )


def test_single_exact_open_file_satisfies_open_without_a_bundle():
    route = resolve_document_route(
        "genere un devis puis ouvre le document", mode="agent",
    )
    path = "C:/documents/devis.pdf"
    state = SimpleNamespace(
        _document_route=route,
        _document_workflow_evidence={
            "batch_proofs": {}, "generation_events": [],
            "open_events": [], "revision_events": [], "revision_records": [],
            "verification_events": [], "event_counter": 0,
        },
        history=[],
    )
    generated = Observation(json.dumps({
        "kind": "devis",
        "document_id": "doc-devis",
        "filename": "devis.pdf",
        "path": path,
        "sha256": "sha-devis",
        "template_id": "devis",
        "format": "pdf",
        "size": 100,
        "logo_id": "",
        "render_status": "render_verified",
        "render_verified": True,
        "page_count": 1,
    }))
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("generate_studio_document", {"kind": "devis"}),
        generated,
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_file", {"path": path}),
        Observation("Ouverture effectuee"),
    )

    assert ReActLoop._document_workflow_pending_action(state) is None
    assert ReActLoop._document_final_fulfills_plan_task(
        state, "Fournir le bilan final",
    ) is True


def test_bilan_final_is_not_credited_before_requested_open():
    route = resolve_document_route(
        "genere un devis, ouvre-le puis fournis le bilan final", mode="agent",
    )
    state = SimpleNamespace(
        _document_route=route,
        _document_workflow_evidence={
            "batch_proofs": {}, "generation_events": [],
            "open_events": [], "revision_events": [], "revision_records": [],
            "verification_events": [], "event_counter": 0,
        },
        history=[],
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("generate_studio_document", {"kind": "devis"}),
        Observation(json.dumps({
            "kind": "devis", "document_id": "doc-devis",
            "filename": "devis.pdf", "path": "C:/documents/devis.pdf",
            "sha256": "sha-devis", "template_id": "devis", "format": "pdf",
            "size": 100, "logo_id": "", "render_status": "render_verified",
            "render_verified": True, "page_count": 1,
        })),
    )

    assert ReActLoop._document_final_fulfills_plan_task(
        state, "Fournir le bilan final",
    ) is False


def test_manifest_never_credits_verify_and_bilan_in_compound_workflow():
    state, _route = _state()
    _record_full_manifest(state)
    state._task_plan = [TaskItem("Vérifier et fournir le bilan")]
    state._emit_plan_state = lambda **_kwargs: None

    assert ReActLoop._reconcile_document_plan_from_manifest(state, 11) == 0
    assert state._task_plan[0].completed is False


def test_named_target_before_revision_anaphor_binds_exact_document():
    route = resolve_document_route(
        "Genere exactement ces documents dans cet ordre : devis, facture, "
        "bon de commande, proces-verbal de reunion, rapport d'activite et "
        "lettre officielle. Utilise le document_id du devis retourne par "
        "l'ouverture. Revise uniquement son numero, puis relis le PDF enfant.",
        mode="agent",
    )

    revision = next(
        action for action in route.workflow_actions
        if action.operation == "revise"
    )
    assert route.requested_kinds == (
        "devis", "facture", "bon_commande", "proces_verbal",
        "rapport_activite", "lettre_officielle",
    )
    assert revision.target_ordinal == 1


def test_bare_revision_without_named_or_anaphoric_target_is_not_guessed():
    route = resolve_document_route(
        "Genere un devis et une facture, puis revise le resultat.",
        mode="agent",
    )

    revision = next(
        action for action in route.workflow_actions
        if action.operation == "revise"
    )
    assert revision.target_ordinal == 0


def test_real_revision_handler_shape_is_recorded_with_target_kind_fallback():
    route = resolve_document_route(
        "Genere un devis, ouvre-le, revise uniquement son numero en "
        "DEV-CLOTURE-8427, puis relis le PDF enfant.",
        mode="agent",
    )
    parent_path = "C:/documents/devis.pdf"
    child_path = "C:/documents/devis-revision.pdf"
    state = SimpleNamespace(
        _document_route=route,
        _document_workflow_evidence={
            "batch_proofs": {}, "generation_events": [],
            "open_events": [], "revision_events": [], "revision_records": [],
            "verification_events": [], "event_counter": 0,
        },
        _document_workflow_target_proof=None,
        history=[],
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("generate_studio_document", {"kind": "devis"}),
        Observation(json.dumps({
            "kind": "devis", "document_id": "doc-parent",
            "filename": "devis.pdf", "path": parent_path,
            "sha256": "sha-parent", "template_id": "devis-perso",
            "format": "pdf", "size": 100, "render_status": "render_verified",
            "render_verified": True, "page_count": 1,
        })),
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("open_file", {"path": parent_path}),
        Observation("Ouverture effectuee"),
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("revise_studio_document", {
            "document_id": "doc-parent",
            "data": '{"numero":"DEV-CLOTURE-8427"}',
            "replace_data": False,
        }),
        Observation(json.dumps({
            "path": child_path,
            "record": {
                "id": "doc-child", "sha256": "sha-child",
                "filename": "devis-revision.pdf", "format": "pdf",
                "size": 120, "template_id": "devis-perso",
            },
            # Keep kind absent to exercise the target-derived fallback used
            # when an older/full handler payload omits it.
            "recipe": {
                "template_id": "devis-perso", "output_format": "pdf",
                "data": {"numero": "DEV-CLOTURE-8427"},
            },
            "render_proof": {
                "status": "render_verified", "verified": True, "page_count": 1,
            },
            "changed_fields": {"numero": "DEV-CLOTURE-8427"},
        })),
    )
    ReActLoop._record_document_workflow_evidence(
        state,
        _action("read_document", {"path": child_path}),
        Observation("N° DEV-CLOTURE-8427"),
    )

    proof_state = ReActLoop._document_workflow_proof_state(state)
    assert proof_state["revision"]["proof"].document_id == "doc-child"
    assert proof_state["verification"] is not None
    assert ReActLoop._document_workflow_pending_action(state) is None
