from pathlib import Path

from src.documents.delivery_manifest import (
    DocumentDeliveryProof,
    build_document_grounding_request,
    document_free_answer_is_grounded,
)


def _proof(*, verified=True):
    return DocumentDeliveryProof(
        kind="facture",
        document_id="doc-42",
        filename="facture_nova.pdf",
        path=str(Path("C:/workspace/documents/facture_nova.pdf")),
        sha256="abc",
        template_id="facture-pro",
        format="pdf",
        size=1200,
        logo_id="logo-main",
        render_status="verified" if verified else "failed",
        render_verified=verified,
        page_count=1,
    )


def test_grounding_request_preserves_facts_but_demands_lumena_free_voice():
    query = build_document_grounding_request(
        "Fais ma facture.",
        "C'est pret. facture_nova.pdf - C:/workspace/documents/facture_nova.pdf",
    )

    assert "PREUVES DOCUMENTAIRES DETERMINISTES" in query
    assert "facture_nova.pdf" in query
    assert "TA reponse finale, libre et naturelle" in query
    assert "pas un texte a recopier mot pour mot" in query


def test_natural_document_answer_is_accepted_when_exact_identity_is_kept():
    answer = (
        "J'ai préparé la facture Nova et vérifié son rendu. Tu la trouveras ici : "
        "facture_nova.pdf. Le lot reste accessible avec doclot-42."
    )

    assert document_free_answer_is_grounded(
        answer, [_proof()], receipt_id="doclot-42"
    ) is True


def test_free_answer_missing_exact_file_falls_back():
    assert document_free_answer_is_grounded(
        "J'ai terminé le document.", [_proof()], receipt_id="doclot-42"
    ) is False


def test_unverified_render_requires_an_honest_caveat():
    assert document_free_answer_is_grounded(
        "La facture_nova.pdf est prête, lot doclot-42.",
        [_proof(verified=False)],
        receipt_id="doclot-42",
    ) is False
    assert document_free_answer_is_grounded(
        "La facture_nova.pdf est créée, mais son rendu reste non certifié. Lot doclot-42.",
        [_proof(verified=False)],
        receipt_id="doclot-42",
    ) is True


def test_pending_verification_can_be_expressed_naturally_in_french():
    answer = (
        "J'ai créé facture_nova.pdf, mais je n'ai pas encore pu vérifier la "
        "nouvelle version après modification."
    )

    assert document_free_answer_is_grounded(
        answer,
        [_proof()],
        pending_operation="verify",
    ) is True
