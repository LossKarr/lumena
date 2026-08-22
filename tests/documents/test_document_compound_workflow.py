from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.documents.delivery_manifest import DocumentDeliveryProof
from src.documents.document_delivery_bundle import (
    load_delivery_bundle,
    save_delivery_reference,
)
from src.documents.document_intent import resolve_document_route
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.contracts import SubToolResult
from src.reasoning.handlers.documents import (
    open_document_delivery_handler,
    revise_studio_document_handler,
)
from src.reasoning.plan_progress import document_workflow_task_blocks
from src.reasoning.react import ReActLoop
from src.reasoning.react_config import Observation
from src.reasoning.react_config import TaskItem


def _proof(path: Path, index: int) -> DocumentDeliveryProof:
    content = path.read_bytes()
    return DocumentDeliveryProof(
        kind=f"kind-{index}",
        document_id=f"doc-{index}",
        filename=path.name,
        path=str(path),
        sha256=hashlib.sha256(content).hexdigest(),
        template_id=f"template-{index}",
        format="pdf",
        size=len(content),
        logo_id="",
        render_status="render_verified",
        render_verified=True,
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


def _step(tool_name: str, *, args=None, content="", success=True, sub_results=()):
    return SimpleNamespace(
        action=SimpleNamespace(tool_name=tool_name, tool_args=args or {}),
        observation=Observation(content, success=success, sub_results=sub_results),
    )


def _batch(template_ids: list[str], offset: int = 0):
    return _step(
        "generate_studio_documents",
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


def _compound_state():
    route = resolve_document_route(
        "Genere mes 4 derniers modeles personnalises puis 30 documents structures. "
        "Ouvre-les tous, modifie precisement le troisieme et verifie la nouvelle version.",
        mode="agent",
    )
    custom = [{"id": f"custom-{index}"} for index in range(1, 5)]
    builtin = [{"id": f"builtin-{index}"} for index in range(1, 31)]
    history = [
        _step(
            "list_document_models",
            args={"origin": "custom", "limit": 4, "sort": "recent"},
            content=json.dumps({"models": custom}),
        ),
        _step(
            "list_document_models",
            args={"origin": "builtin", "limit": 30, "sort": "name"},
            content=json.dumps({"models": builtin}),
        ),
    ]
    return SimpleNamespace(_document_route=route, history=history), route


def test_compound_route_keeps_both_selections_and_post_actions():
    route = resolve_document_route(
        "Genere mes 4 derniers modeles personnalises puis 30 documents structures. "
        "Ouvre-les tous, modifie precisement le troisieme et verifie la nouvelle version.",
        mode="agent",
    )

    assert route.operation == "create"
    assert route.requires_studio is True
    assert [(item.origin, item.limit, item.sort) for item in route.selections] == [
        ("custom", 4, "recent"),
        ("builtin", 30, "name"),
    ]
    assert route.requested_count == 34
    assert [action.operation for action in route.workflow_actions] == [
        "generate", "open", "revise", "verify",
    ]
    revision = next(action for action in route.workflow_actions if action.operation == "revise")
    assert revision.target_ordinal == 3


def test_exact_runtime_wording_recognizes_custom_then_builtin_models():
    route = resolve_document_route(
        "Génère mes 4 derniers modèles personnalisés puis les 30 modèles intégrés, "
        "dans cet ordre. Ouvre ensuite les 34 documents. Sur le troisième document, "
        "identifie un champ réellement modifiable, remplace sa valeur par "
        "TEST-REVISION-2026, puis vérifie la nouvelle version.",
        mode="agent",
    )

    assert [(item.origin, item.limit, item.sort) for item in route.selections] == [
        ("custom", 4, "recent"),
        ("builtin", 30, "name"),
    ]
    assert route.requested_count == 34


def test_simple_routes_keep_historical_single_selection_and_revision():
    selection = resolve_document_route(
        "Genere mes 4 derniers modeles personnalises", mode="agent",
    )
    revision = resolve_document_route(
        "Modifie mon devis pour changer le numero", mode="agent",
    )

    assert selection.selection_limit == 4
    assert selection.requested_count == 4
    assert len(selection.selections) == 1
    assert revision.operation == "revise"
    assert revision.selections == ()


def test_compound_manifest_accumulates_partial_retries_without_duplicate_inflation():
    state, route = _compound_state()
    custom_ids = [f"custom-{index}" for index in range(1, 5)]
    builtin_ids = [f"builtin-{index}" for index in range(1, 31)]
    state.history.extend([
        _batch(custom_ids),
        _batch(builtin_ids[:23], offset=4),
        _batch(builtin_ids[23:25], offset=27),
        _batch(builtin_ids[25:], offset=29),
    ])

    manifest, missing, unverified = ReActLoop._structured_document_delivery_manifest(state)

    assert len(manifest) == route.requested_count == 34
    assert [proof.template_id for proof in manifest] == custom_ids + builtin_ids
    assert missing == ()
    assert unverified == ()


def test_compound_gate_accepts_only_next_missing_catalog_slice():
    state, _route = _compound_state()
    custom_requests = [
        {"kind": "devis", "template_id": f"custom-{index}", "data": {}}
        for index in range(1, 5)
    ]
    assert ReActLoop._structured_document_tool_gate(
        state, "generate_studio_documents", {"requests": custom_requests},
    ) is None

    state.history.append(_batch([f"custom-{index}" for index in range(1, 5)]))
    builtin_requests = [
        {"kind": "devis", "template_id": f"builtin-{index}", "data": {}}
        for index in range(1, 31)
    ]
    assert ReActLoop._structured_document_tool_gate(
        state, "generate_studio_documents", {"requests": builtin_requests},
    ) is None

    duplicate = [dict(builtin_requests[0])]
    duplicate[0]["template_id"] = "custom-1"
    refused = ReActLoop._structured_document_tool_gate(
        state, "generate_studio_documents", {"requests": duplicate},
    )
    assert refused is not None
    assert "manquants" in refused.content


def test_compound_workflow_requires_open_then_exact_third_revision():
    state, _route = _compound_state()
    all_ids = [f"custom-{index}" for index in range(1, 5)] + [
        f"builtin-{index}" for index in range(1, 31)
    ]
    state.history.extend([_batch(all_ids[:30]), _batch(all_ids[30:], offset=30)])

    assert ReActLoop._document_workflow_pending_action(state).operation == "open"
    assert ReActLoop._document_workflow_target(state).document_id == "doc-3"

    state.history.append(_step(
        "open_document_delivery",
        args={"receipt_id": "docbundle_0123456789abcdef01234567"},
        content=json.dumps({"opened": 34, "requested": 34, "failed": 0}),
    ))
    assert ReActLoop._document_workflow_pending_action(state).operation == "revise"

    state.history.append(_step(
        "revise_studio_document",
        args={"document_id": "doc-3", "data": {"numero": "REV-3"}},
        content=_proof_json("custom-3", 300),
    ))
    assert ReActLoop._document_workflow_pending_action(state).operation == "verify"

    state.history.append(_step(
        "read_document",
        args={"path": "C:/documents/document-300.pdf"},
        content="Numéro REV-3",
    ))
    assert ReActLoop._document_workflow_pending_action(state) is None


def test_document_workflow_plan_tasks_reject_shell_and_read_fallbacks():
    assert document_workflow_task_blocks(
        "run_command", "Ouvrir tous les documents du lot",
    ) is True
    assert document_workflow_task_blocks(
        "read_document", "Modifier le troisieme document",
    ) is True
    assert document_workflow_task_blocks(
        "open_document_delivery", "Ouvrir tous les documents du lot",
    ) is False
    assert document_workflow_task_blocks(
        "revise_studio_document", "Verifier la nouvelle version du document",
    ) is False


def test_generation_manifest_never_completes_open_revision_or_child_verification():
    state, _route = _compound_state()
    all_ids = [f"custom-{index}" for index in range(1, 5)] + [
        f"builtin-{index}" for index in range(1, 31)
    ]
    state.history.extend([_batch(all_ids[:30]), _batch(all_ids[30:], offset=30)])
    state._task_plan = [
        TaskItem("Generer les 34 documents selectionnes"),
        TaskItem("Ouvrir tous les documents du lot"),
        TaskItem("Modifier le troisieme document"),
        TaskItem("Verifier la nouvelle version du document"),
    ]
    state._emit_plan_state = lambda **_kwargs: None

    assert ReActLoop._reconcile_document_plan_from_manifest(state, 4) == 1
    assert [task.completed for task in state._task_plan] == [True, False, False, False]


def test_delivery_reference_uses_bundle_above_30_and_is_tamper_evident(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    proofs = []
    for index in range(34):
        path = output / f"document-{index:02d}.pdf"
        path.write_bytes(f"document {index}".encode())
        proofs.append(_proof(path, index))

    reference = save_delivery_reference(tmp_path, proofs, requested_count=34)

    assert reference["id"].startswith("docbundle_")
    assert len(reference["receipt_ids"]) == 2
    assert len(reference["documents"]) == 34
    loaded = load_delivery_bundle(
        tmp_path / "delivery_bundles",
        tmp_path / "delivery_receipts",
        reference["id"],
    )
    assert [row["document_id"] for row in loaded["documents"]] == [
        f"doc-{index}" for index in range(34)
    ]

    bundle_path = tmp_path / "delivery_bundles" / f"{reference['id']}.json"
    raw = json.loads(bundle_path.read_text(encoding="utf-8"))
    raw["documents"][0]["filename"] = "tampered.pdf"
    bundle_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        load_delivery_bundle(
            tmp_path / "delivery_bundles",
            tmp_path / "delivery_receipts",
            reference["id"],
        )


@pytest.mark.asyncio
async def test_open_delivery_accepts_bundle_and_opens_exact_34(monkeypatch, tmp_path):
    from src.documents import studio as studio_module
    from src.reasoning.handlers import files as files_module

    output = tmp_path / "output"
    output.mkdir()
    proofs = []
    for index in range(34):
        path = output / f"document-{index:02d}.pdf"
        path.write_bytes(f"document {index}".encode())
        proofs.append(_proof(path, index))
    reference = save_delivery_reference(tmp_path, proofs, requested_count=34)
    monkeypatch.setattr(
        studio_module,
        "get_document_studio",
        lambda: SimpleNamespace(root=tmp_path, output_root=output),
    )
    opened = []

    async def _open(_ctx, path=None, file_path=None):
        opened.append(Path(path or file_path).name)
        return HandlerResult.ok("opened", handler_name="open_file")

    monkeypatch.setattr(files_module, "open_file_handler", _open)
    result = await open_document_delivery_handler(None, reference["id"])
    payload = json.loads(result.output)

    assert result.success is True
    assert payload["opened"] == 34
    assert payload["failed"] == 0
    assert opened == [f"document-{index:02d}.pdf" for index in range(34)]


@pytest.mark.asyncio
async def test_revision_refuses_static_unknown_field_without_creating_child(monkeypatch):
    from src.documents import studio as studio_module
    from src.documents.generation_recipe import RECIPE_METADATA_KEY, StudioGenerationRecipe

    recipe = StudioGenerationRecipe.create(
        template_id="devis", template_version="1", kind="devis",
        output_format="pdf", data={"numero": "DEV-1", "client": {"name": "Atlas"}},
        filename_stem="devis", logo_id="",
    )
    record = SimpleNamespace(id="doc-original", metadata={RECIPE_METADATA_KEY: recipe.to_dict()})
    studio = SimpleNamespace(
        library=SimpleNamespace(resolve_reference=lambda *_args, **_kwargs: record),
        parse_json_object=lambda value, field: json.loads(value),
        revise=lambda *_args, **_kwargs: pytest.fail("revision must not run"),
    )
    monkeypatch.setattr(studio_module, "get_document_studio", lambda: studio)

    result = await revise_studio_document_handler(
        None, "doc-original", json.dumps({"title": "Nouveau titre"}),
    )

    assert result.success is False
    assert "non editable" in (result.error or result.output).lower()
    assert "numero" in (result.error or result.output)
