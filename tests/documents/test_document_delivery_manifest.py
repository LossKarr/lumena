from __future__ import annotations

import json

from src.documents.delivery_manifest import (
    DocumentDeliveryProof,
    build_document_workflow_final,
    build_document_workflow_incomplete_final,
    build_multi_document_final,
    compact_generation_payload,
    manifest_has_new_proof,
    manifest_progress_signature,
    parse_generation_proof,
    summarize_document_open_events,
    workflow_has_new_proof,
    workflow_progress_signature,
)


def _full_result():
    return {
        "path": r"C:\workspace\documents\bon_commande-perso.pdf",
        "record": {
            "id": "doc_123",
            "sha256": "abc123",
            "filename": "library-copy.pdf",
            "format": "pdf",
            "size": 12345,
            "template_id": "bon_commande",
        },
        "recipe": {
            "kind": "bon_commande",
            "template_id": "bon_commande",
            "output_format": "pdf",
            "logo_id": "logo_losskarr",
        },
        "render_proof": {
            "status": "render_verified",
            "verified": True,
            "thumbnail_path": r"C:\cache\bon.webp",
            "page_count": 6,
        },
    }


def test_compact_payload_uses_exact_output_filename_not_library_name():
    payload = compact_generation_payload(_full_result(), kind="bon_commande")

    assert payload["filename"] == "bon_commande-perso.pdf"
    assert payload["document_id"] == "doc_123"
    assert payload["render_verified"] is True
    assert len(json.dumps(payload)) < 800


def test_parse_compact_generation_proof_roundtrip():
    payload = compact_generation_payload(_full_result(), kind="bon_commande")
    proof = parse_generation_proof(json.dumps(payload), fallback_kind="wrong")

    assert proof is not None
    assert proof.kind == "bon_commande"
    assert proof.filename == "bon_commande-perso.pdf"
    assert proof.logo_id == "logo_losskarr"
    assert proof.render_status == "render_verified"
    assert proof.page_count == 6


def test_parse_rejects_unstructured_or_incomplete_success_text():
    assert parse_generation_proof("ok", fallback_kind="devis") is None
    assert parse_generation_proof('{"kind":"devis"}', fallback_kind="devis") is None


def test_multi_document_final_is_built_from_exact_manifest():
    proof = parse_generation_proof(
        json.dumps(compact_generation_payload(_full_result(), kind="bon_commande"))
    )
    final = build_multi_document_final([proof], requested_count=1)

    assert "bon_commande-perso.pdf" in final
    assert "bon_commande.pdf`" not in final
    assert "rendu verifie" in final
    assert "Logo actif applique: 1/1." in final


def test_multi_document_final_exposes_exact_reopen_receipt():
    proof = parse_generation_proof(
        json.dumps(compact_generation_payload(_full_result(), kind="bon_commande"))
    )
    final = build_multi_document_final(
        [proof], requested_count=1,
        receipt_id="doclot_0123456789abcdef01234567",
    )

    assert "doclot_0123456789abcdef01234567" in final
    assert "ouvre-les" in final


def test_empty_document_final_never_claims_ready():
    final = build_multi_document_final([], requested_count=1)

    assert "C'est pret" not in final
    assert "Livraison documentaire incomplete" in final
    assert "0/1 document(s)" in final


def test_partial_document_final_never_claims_ready():
    proof = parse_generation_proof(
        json.dumps(compact_generation_payload(_full_result(), kind="bon_commande"))
    )
    final = build_multi_document_final([proof], requested_count=2)

    assert "C'est pret" not in final
    assert "Livraison documentaire incomplete" in final
    assert "1/2 document(s)" in final


def test_manifest_progress_requires_a_new_exact_proof():
    proof = parse_generation_proof(
        json.dumps(compact_generation_payload(_full_result(), kind="bon_commande"))
    )
    first = manifest_progress_signature([proof])

    assert manifest_has_new_proof((), first) is True
    assert manifest_has_new_proof(first, first) is False

    other_result = _full_result()
    other_result["path"] = r"C:\workspace\documents\devis.pdf"
    other_result["record"] = {**other_result["record"], "id": "doc_456", "sha256": "def456"}
    other = parse_generation_proof(
        json.dumps(compact_generation_payload(other_result, kind="devis"))
    )
    second = manifest_progress_signature([proof, other])
    assert manifest_has_new_proof(first, second) is True


def test_workflow_final_reports_open_revision_and_verification_without_reopen_hint():
    proof = parse_generation_proof(
        json.dumps(compact_generation_payload(_full_result(), kind="bon_commande"))
    )
    revised_result = _full_result()
    revised_result["path"] = r"C:\workspace\documents\bon-commande-revise.pdf"
    revised_result["record"] = {
        **revised_result["record"],
        "id": "doc_child",
        "sha256": "child-sha",
    }
    revised = parse_generation_proof(
        json.dumps(compact_generation_payload(revised_result, kind="bon_commande"))
    )

    final = build_document_workflow_final(
        [proof],
        requested_count=1,
        receipt_id="docbundle_0123456789abcdef01234567",
        opened=1,
        failed=0,
        target_ordinal=1,
        target=proof,
        revised=revised,
        changed_fields={"numero": "TEST-REVISION-2026"},
        verification_path=revised.path,
    )

    assert "Ouverture: 1/1" in final
    assert "doc_child" in final
    assert "`numero` = `TEST-REVISION-2026`" in final
    assert "relecture confirmée" in final
    assert "ouvre-les" not in final


def test_incomplete_workflow_final_keeps_revision_and_reports_missing_verification():
    proof = parse_generation_proof(
        json.dumps(compact_generation_payload(_full_result(), kind="bon_commande"))
    )
    revised_result = _full_result()
    revised_result["path"] = r"C:\workspace\documents\bon-commande-revise.pdf"
    revised_result["record"] = {
        **revised_result["record"],
        "id": "doc_child",
        "sha256": "child-sha",
    }
    revised = parse_generation_proof(
        json.dumps(compact_generation_payload(revised_result, kind="bon_commande"))
    )

    final = build_document_workflow_incomplete_final(
        [proof],
        requested_count=1,
        receipt_id="docbundle_0123456789abcdef01234567",
        opened=1,
        failed=0,
        target_ordinal=1,
        target=proof,
        revised=revised,
        changed_fields={"titre": "FESTIVAL-NANTES-730"},
        pending_operation="verify",
    )

    assert "Ouverture: 1/1" in final
    assert "doc_child" in final
    assert "FESTIVAL-NANTES-730" in final
    assert "NON CONFIRMEE" in final
    assert "Action restante non prouvee: verify." in final
    assert "ouvre-les" not in final


def _delivery_proof(index: int) -> DocumentDeliveryProof:
    return DocumentDeliveryProof(
        kind=f"kind-{index}",
        document_id=f"doc-{index}",
        filename=f"document-{index:02d}.pdf",
        path=f"C:/workspace/documents/document-{index:02d}.pdf",
        sha256=f"sha-{index}",
        template_id=f"template-{index}",
        format="pdf",
        size=100 + index,
        logo_id="",
        render_status="render_verified",
        render_verified=True,
    )


def _open_event(proofs, receipt_id: str, event_index: int) -> dict:
    rows = list(proofs)
    return {
        "receipt_id": receipt_id,
        "requested": len(rows),
        "opened": len(rows),
        "failed": 0,
        "files": [
            {"filename": proof.filename, "path": proof.path}
            for proof in rows
        ],
        "_event_index": event_index,
    }


def test_split_open_receipts_prove_the_exact_manifest_union():
    proofs = [_delivery_proof(index) for index in range(1, 35)]
    summary = summarize_document_open_events(
        proofs,
        [
            _open_event(proofs[:4], "doclot_custom", 1),
            _open_event(proofs[4:], "doclot_builtin", 2),
        ],
        requested_count=34,
    )

    assert summary is not None
    assert summary["complete"] is True
    assert summary["opened"] == 34
    assert summary["receipt_ids"] == ("doclot_custom", "doclot_builtin")
    assert summary["_event_index"] == 2
    assert [row["filename"] for row in summary["files"]] == [
        proof.filename for proof in proofs
    ]


def test_reopening_the_same_partial_receipt_adds_no_open_progress():
    proofs = [_delivery_proof(index) for index in range(1, 35)]
    event = _open_event(proofs[:4], "doclot_custom", 1)
    duplicate = {**event, "_event_index": 2}

    summary = summarize_document_open_events(
        proofs, [event, duplicate], requested_count=34,
    )

    assert summary is not None
    assert summary["complete"] is False
    assert summary["opened"] == 4
    assert summary["receipt_ids"] == ("doclot_custom",)


def test_open_receipt_with_a_foreign_file_is_not_credited():
    proofs = [_delivery_proof(index) for index in range(1, 3)]
    event = _open_event(proofs, "doclot_wrong", 1)
    event["files"][1]["path"] = "C:/workspace/documents/foreign.pdf"

    summary = summarize_document_open_events(
        proofs, [event], requested_count=2,
    )

    assert summary is not None
    assert summary["complete"] is False
    assert summary["opened"] == 0


def test_workflow_progress_is_monotone_across_open_revision_and_verify():
    opened_four = workflow_progress_signature({"opened": 4})
    opened_all = workflow_progress_signature({"opened": 34})
    revised = workflow_progress_signature(
        {"opened": 34}, revised_document_id="doc-child",
    )
    verified = workflow_progress_signature(
        {"opened": 34},
        revised_document_id="doc-child",
        verification_path="C:/workspace/documents/revised.pdf",
    )

    assert workflow_has_new_proof((0, "", ""), opened_four) is True
    assert workflow_has_new_proof(opened_four, opened_four) is False
    assert workflow_has_new_proof(opened_four, opened_all) is True
    assert workflow_has_new_proof(opened_all, revised) is True
    assert workflow_has_new_proof(revised, verified) is True
    assert workflow_has_new_proof(verified, verified) is False
