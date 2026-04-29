from src.reasoning.plan_evidence import (
    ProofCapability,
    get_tool_capabilities,
    has_sufficient_proof,
)


def test_read_file_override_stays_read_only():
    caps = get_tool_capabilities("read_file", "files", "files")
    assert caps == frozenset({ProofCapability.FILE_READ})


def test_files_category_defaults_to_write_capability():
    caps = get_tool_capabilities("write_file", "files", "files")
    assert caps == frozenset({ProofCapability.FILE_WRITE})


def test_stripe_module_uses_payment_capability():
    caps = get_tool_capabilities("stripe_create_product", "stripe", "platform")
    assert caps == frozenset({ProofCapability.PAYMENT_MUTATION})


def test_unknown_tool_without_category_is_conservative():
    caps = get_tool_capabilities("mystery_tool", "", "")
    assert caps == frozenset({ProofCapability.GENERIC_READONLY})


def test_web_app_syntax_only_is_not_functional_proof():
    assert not has_sufficient_proof(
        "run_command",
        "Verification syntaxique OK - aucune erreur JS detectee",
        "Verifier que tout est fonctionnel",
        "system",
        "system",
    )


def test_web_app_server_and_http_is_functional_proof():
    assert has_sufficient_proof(
        "run_command",
        "Server listening on port 3000 - HTTP 200 OK",
        "Verifier que tout est fonctionnel",
        "system",
        "system",
    )


def test_delivery_failure_does_not_validate():
    assert not has_sufficient_proof(
        "mail_send",
        "Erreur: adresse invalide",
        "Envoyer le rapport par mail",
        "mail",
        "communication",
    )


def test_delivery_success_validates():
    assert has_sufficient_proof(
        "mail_send",
        "Email envoye avec succes - message_id=123",
        "Envoyer le rapport par mail",
        "mail",
        "communication",
    )


def test_payment_success_validates():
    assert has_sufficient_proof(
        "stripe_create_product",
        "Produit cree avec succes: prod_123",
        "Verifier le paiement Stripe",
        "stripe",
        "platform",
    )


def test_payment_failure_does_not_validate():
    assert not has_sufficient_proof(
        "stripe_create_product",
        "Error: invalid API key",
        "Verifier le paiement Stripe",
        "stripe",
        "platform",
    )


def test_process_status_running_validates_server_runtime():
    assert has_sufficient_proof(
        "process_status",
        "Statut: running\nCommande: node server.js",
        "Verifier que le serveur tourne sur le port 3000",
        "agents",
        "agents",
    )


def test_process_status_does_not_validate_generic_file_check():
    assert not has_sufficient_proof(
        "process_status",
        "Statut: running\nCommande: node server.js",
        "Verifier que le dossier existe et contient server.js",
        "agents",
        "agents",
    )
