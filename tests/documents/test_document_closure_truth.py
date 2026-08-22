from types import SimpleNamespace

from src.reasoning.final_guards import (
    apply_document_rights_truth_lock,
    claims_document_reuse_rights,
)
from src.reasoning.react import ReActLoop


def _step(tool_name, content, *, success=True):
    return SimpleNamespace(
        action=SimpleNamespace(tool_name=tool_name, tool_args={}),
        observation=SimpleNamespace(success=success, content=content),
    )


def test_document_rights_claim_is_locked_without_explicit_evidence():
    text = "Le document est libre de droits et peut etre redistribue."

    assert claims_document_reuse_rights(text) is True
    locked, info = apply_document_rights_truth_lock(text, rights_proven=False)

    assert info["rights_overclaim"] is True
    assert locked.startswith("⚠️ **Droits de réutilisation NON établis**")
    assert apply_document_rights_truth_lock(locked, rights_proven=False)[0] == locked


def test_honest_unknown_rights_and_proven_rights_are_unchanged():
    honest = "Les droits de reutilisation sont inconnus et la licence est a verifier."
    assert claims_document_reuse_rights(honest) is False
    assert apply_document_rights_truth_lock(honest, rights_proven=False)[0] == honest

    claim = "Reutilisation autorisee par la licence jointe."
    assert apply_document_rights_truth_lock(claim, rights_proven=True)[0] == claim


def test_document_web_rights_evidence_requires_status_and_evidence():
    unknown = SimpleNamespace(history=[_step(
        "download_document",
        '{"record":{"metadata":{"rights_status":"unknown","rights_evidence":""}}}',
    )])
    proven = SimpleNamespace(history=[_step(
        "inspect_document_source",
        '{"rights_status":"licensed","rights_evidence":"Licence CC BY 4.0"}',
    )])
    unrelated = SimpleNamespace(history=[_step("search_documents_web", "{}")])

    assert ReActLoop._document_web_rights_evidence(unknown) == (True, False)
    assert ReActLoop._document_web_rights_evidence(proven) == (True, True)
    assert ReActLoop._document_web_rights_evidence(unrelated) == (False, False)
