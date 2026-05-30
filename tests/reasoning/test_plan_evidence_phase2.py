from src.reasoning.plan_evidence import (
    ProofCapability,
    get_tool_capabilities,
    has_sufficient_proof,
    _NON_PROOF_CAPABILITIES,
)


# ── Phase 2bis : catégorie data (data.gouv / SIRENE / géo / workbench) ────────

def test_data_category_defaults_to_readonly():
    """La catégorie data est readonly par défaut (jamais preuve sans override)."""
    caps = get_tool_capabilities("some_future_data_tool", "data", "data")
    assert caps == frozenset({ProofCapability.GENERIC_READONLY})


def test_data_download_is_file_write_proof():
    """datagouv_download_resource produit un fichier → FILE_WRITE (preuve)."""
    caps = get_tool_capabilities("datagouv_download_resource", "data", "data")
    assert caps == frozenset({ProofCapability.FILE_WRITE})
    # et NE tombe PAS en capability non-preuve
    assert not caps & _NON_PROOF_CAPABILITIES


def test_data_export_is_file_write_proof():
    """data_export écrit un fichier → FILE_WRITE (preuve)."""
    caps = get_tool_capabilities("data_export", "data", "data")
    assert caps == frozenset({ProofCapability.FILE_WRITE})
    assert not caps & _NON_PROOF_CAPABILITIES


def test_data_search_and_analysis_stay_readonly():
    """Recherche / lecture / analyse data restent readonly (pas une preuve)."""
    for tool in ("datagouv_search", "datagouv_get_dataset",
                 "sirene_search_company", "sirene_get_by_siret",
                 "geo_search_address", "geo_reverse", "geo_commune_info",
                 "data_profile_file", "data_filter_rows", "data_aggregate",
                 "data_unique_values", "data_join"):
        caps = get_tool_capabilities(tool, "data", "data")
        assert caps == frozenset({ProofCapability.GENERIC_READONLY}), tool
        assert caps & _NON_PROOF_CAPABILITIES, tool


# ── Phase 2 : mono-catégorie — aucune catégorie runtime en fallback silencieux ─

def test_spotify_is_generic_mutation_not_doc_artifact():
    """spotify (contrôle audio) ne doit PAS être DOC_ARTIFACT mais GENERIC_MUTATION."""
    caps = get_tool_capabilities("spotify_api_play", "spotify", "spotify")
    assert caps == frozenset({ProofCapability.GENERIC_MUTATION})


def test_no_runtime_category_falls_back_silently():
    """Garde-fou Phase 2 : CHAQUE catégorie runtime (_tool_modules) doit avoir une
    entrée explicite dans _CATEGORY_CAPABILITIES ou _MODULE_CAPABILITIES.

    Empêche qu'une catégorie (nouvelle ou issue du passage mono) tombe
    silencieusement en GENERIC_READONLY faute de mapping.
    """
    from src.reasoning.tool_registry import ToolRegistry
    from src.reasoning.plan_evidence import _CATEGORY_CAPABILITIES, _MODULE_CAPABILITIES
    from src.reasoning import tool_categories as tc

    reg = ToolRegistry()
    runtime_categories = set((getattr(reg, "_tool_modules", {}) or {}).values())
    assert runtime_categories, "aucune catégorie runtime détectée"

    orphelines = []
    for cat in runtime_categories:
        semantic = tc.get_semantic_category(cat)
        known = (cat in _MODULE_CAPABILITIES
                 or semantic in _MODULE_CAPABILITIES
                 or semantic in _CATEGORY_CAPABILITIES)
        if not known:
            orphelines.append((cat, semantic))
    assert not orphelines, f"catégories sans capability explicite (fallback silencieux): {orphelines}"


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
