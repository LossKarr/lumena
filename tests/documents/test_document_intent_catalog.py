from types import SimpleNamespace

import pytest

from src.documents.builtin_templates import BUILTIN_ALIASES
from src.documents.document_intent import (
    STUDIO_BYPASS_TOOLS,
    document_kinds_mentioned,
    normalize_document_kind,
    resolve_document_route,
)
from src.documents.template_catalog import TemplateCatalog
from src.reasoning.react import ReActLoop


@pytest.mark.parametrize(
    ("kind", "alias"),
    [
        (kind, alias)
        for kind, aliases in BUILTIN_ALIASES.items()
        for alias in aliases
    ],
)
def test_every_builtin_alias_routes_to_its_studio_model(kind, alias):
    route = resolve_document_route(f"fais moi un {alias} detaille", mode="agent")
    assert route.kind == kind
    assert route.operation == "create"
    assert route.requires_studio is True
    assert route.requires_document_tools is True
    assert route.confidence == 1.0


def test_compte_rendu_reunion_tool_kind_maps_to_proces_verbal():
    assert normalize_document_kind("compte_rendu_reunion") == "proces_verbal"


def test_compte_rendu_tool_kind_maps_to_proces_verbal():
    assert normalize_document_kind("compte_rendu") == "proces_verbal"


@pytest.mark.parametrize(
    ("query", "kind"),
    [
        ("okay fairt moi un bulletin de paye stp", "bulletin_paie"),
        ("gzenere une fiche de salaire", "bulletin_paie"),
        ("fai moi un bulltin de paie", "bulletin_paie"),
        ("je voudrais une offre de prix", "devis"),
        ("il me faut une proposition commerciale", "devis"),
        ("pourrais tu me preparer un accord de non divulgation", "nda"),
        ("cree un proces verbal", "proces_verbal"),
        ("fait moi un descriptif de poste", "fiche_poste"),
    ],
)
def test_natural_wording_and_bounded_typos_route_safely(query, kind):
    route = resolve_document_route(query, mode="agent")
    assert route.kind == kind
    assert route.operation == "create"
    assert route.requires_studio is True
    assert route.confidence >= 0.86


@pytest.mark.parametrize(
    "query",
    [
        "c'est quoi un bulletin de paye ?",
        "explique moi a quoi sert un bon de commande",
        "quelle est la difference entre une facture et un devis",
    ],
)
def test_information_questions_never_trigger_document_tools(query):
    route = resolve_document_route(query, mode="agent")
    assert route.operation == "inform"
    assert route.requires_studio is False
    assert route.requires_document_tools is False


@pytest.mark.parametrize(
    "query",
    [
        "commande le dejeuner pour midi",
        "note ce point dans ta memoire",
        "corrige le rapport de bug Python",
        "le contrat social de Rousseau",
    ],
)
def test_unrelated_language_does_not_open_studio(query):
    route = resolve_document_route(query, mode="agent")
    assert route.requires_studio is False


@pytest.mark.parametrize(
    ("query", "operation"),
    [
        ("retrouve ma derniere facture dans mes documents", "search_library"),
        ("cherche un modele de facture sur internet", "search_web"),
        ("telecharge ce document pdf", "download"),
        ("importe ce fichier docx", "import"),
        ("convertis ce document en pdf", "convert"),
        ("exporte ce document", "export"),
        ("montre moi l historique de ce document", "history"),
    ],
)
def test_document_operations_open_document_tools_without_forcing_studio(query, operation):
    route = resolve_document_route(query, mode="agent")
    assert route.operation == operation
    assert route.requires_document_tools is True
    assert route.requires_studio is False


def test_implicit_desire_remains_conversational_in_chat_mode():
    route = resolve_document_route("j aurais besoin d une facture", mode="chat")
    assert route.kind == "facture"
    assert route.operation == "inform"
    assert route.requires_document_tools is False


def test_besoin_du_is_an_implicit_creation_only_in_agent_mode():
    query = "J'aurais besoin du bulletin de paie complet"
    agent = resolve_document_route(query, mode="agent")
    chat = resolve_document_route(query, mode="chat")
    assert agent.operation == "create"
    assert agent.kind == "bulletin_paie"
    assert agent.requires_studio is True
    assert chat.operation == "inform"
    assert chat.requires_document_tools is False


@pytest.mark.parametrize(
    "query",
    [
        "lumena j'ai besoin d'un appartement sans fiche de paye de surait me trouver ca dans le 95",
        "trouve-moi une location sans fiche de paie",
        "je cherche un emploi sans bulletin de salaire",
        "je ne veux pas de bulletin de paie, trouve-moi un appartement",
        "aucune fiche de salaire n'est disponible pour mon dossier locatif",
    ],
)
def test_negated_document_mentions_do_not_open_studio(query):
    route = resolve_document_route(query, mode="agent")
    assert route.kind is None
    assert route.operation == "none"
    assert route.requires_studio is False
    assert route.requires_document_tools is False


@pytest.mark.parametrize(
    ("query", "kind"),
    [
        ("j'ai besoin du bulletin de paie complet", "bulletin_paie"),
        ("cree une fiche de paie sans logo", "bulletin_paie"),
        ("prepare un bulletin de salaire sans primes exceptionnelles", "bulletin_paie"),
        ("je veux un contrat de travail sans clause de mobilite", "contrat_travail"),
    ],
)
def test_document_creation_remains_actionable_when_only_a_field_is_negated(query, kind):
    route = resolve_document_route(query, mode="agent")
    assert route.kind == kind
    assert route.operation == "create"
    assert route.requires_studio is True


def test_specific_work_contract_typo_beats_generic_contract_alias():
    route = resolve_document_route(
        "je veux un contrat de travaille de au moins 6 pages complete recto verso",
        mode="agent",
    )
    assert route.kind == "contrat_travail"
    assert route.operation == "create"
    assert route.requires_studio is True
    assert route.matched_alias == "contrat de travail"
    assert route.minimum_pages == 6


def test_work_contract_filename_does_not_add_generic_service_contract():
    query = (
        "Cree avec Document Studio un contrat de travail CDI d'au moins 6 pages. "
        "Utilise uniquement contrat_travail et genere cert-1340-contrat-helios.pdf."
    )
    route = resolve_document_route(query, mode="agent")

    assert route.kind == "contrat_travail"
    assert [item.kind for item in route.items] == ["contrat_travail"]
    assert document_kinds_mentioned(query) == ("contrat_travail",)
    assert route.minimum_pages == 6


def test_explicit_work_and_service_contracts_remain_two_documents():
    query = "Cree un contrat de travail et un contrat de prestation."
    route = resolve_document_route(query, mode="agent")

    assert [item.kind for item in route.items] == [
        "contrat_travail",
        "contrat_prestation",
    ]


def test_generic_contract_alone_keeps_historical_service_contract_kind():
    route = resolve_document_route("Cree un contrat.", mode="agent")

    assert route.kind == "contrat_prestation"
    assert [item.kind for item in route.items] == ["contrat_prestation"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("fais un rapport d'activite d'au moins 8 pages", 8),
        ("fais un rapport d'activite de minimum 12 pages", 12),
        ("fais un rapport d'activite de 4 pages minimum", 4),
        ("fais un rapport d'activite en plusieurs pages", 0),
    ],
)
def test_only_explicit_minimum_page_wording_creates_page_contract(query, expected):
    assert resolve_document_route(query, mode="agent").minimum_pages == expected


def test_negated_document_is_not_reintroduced_by_multi_kind_collection():
    assert document_kinds_mentioned(
        "trouve un appartement sans fiche de paie et prepare un devis"
    ) == ("devis",)


def test_ambiguous_custom_alias_asks_for_model_selection():
    route = resolve_document_route(
        "fais moi un dossier alpha",
        mode="agent",
        vocabulary={"modele_alpha": ["dossier alpha"], "modele_beta": ["dossier alpha"]},
    )
    assert route.kind is None
    assert route.needs_model_selection is True
    assert route.ambiguous_kinds == ("modele_alpha", "modele_beta")
    assert route.requires_studio is True


def test_custom_manifest_aliases_are_routable_and_cache_is_invalidated(tmp_path):
    builtins = tmp_path / "builtins"
    builtins.mkdir()
    catalog = TemplateCatalog(tmp_path / "studio", builtins)
    manifest = {
        "schema_version": 1,
        "id": "atlas",
        "name": "Rapport Atlas",
        "kind": "rapport_client",
        "format": "pdf",
        "renderer": "html-jinja",
        "aliases": ["dossier atlas", "atlas report"],
    }
    catalog.save_custom(
        "atlas",
        manifest_data=manifest,
        template_source="<html><body>{{ title }}</body></html>",
        sample_data={"title": "Atlas"},
    )

    route = resolve_document_route(
        "fais moi mon dossier atlas",
        mode="agent",
        vocabulary=catalog.intent_vocabulary(),
    )
    assert route.kind == "rapport_client"

    manifest["aliases"] = ["dossier nova"]
    catalog.save_custom(
        "atlas",
        manifest_data=manifest,
        template_source="<html><body>{{ title }}</body></html>",
        sample_data={"title": "Nova"},
    )
    refreshed = catalog.intent_vocabulary()
    assert "dossier nova" in refreshed["rapport_client"]


@pytest.mark.parametrize("tool_name", sorted(STUDIO_BYPASS_TOOLS))
def test_studio_gate_blocks_every_bypass_before_real_attempt(tool_name):
    query = "okay fairt moi un bulletin de paye stp"
    state = SimpleNamespace(
        _original_query=query,
        _document_route=resolve_document_route(query, mode="agent"),
        history=[],
    )
    blocked = ReActLoop._structured_document_tool_gate(state, tool_name)
    assert blocked is not None
    assert blocked.success is False
    assert "generate_studio_document" in blocked.content


def test_studio_gate_reopens_fallback_after_real_studio_attempt():
    query = "okay fairt moi un bulletin de paye stp"
    state = SimpleNamespace(
        _original_query=query,
        _document_route=resolve_document_route(query, mode="agent"),
        history=[SimpleNamespace(action=SimpleNamespace(tool_name="generate_studio_document"))],
    )
    assert ReActLoop._structured_document_tool_gate(state, "delegate_task") is None
    assert ReActLoop._structured_document_tool_gate(state, "create_pdf") is None


def test_freeform_pdf_keeps_historical_tools_available():
    state = SimpleNamespace(
        _original_query="cree un rapport pdf libre sur mes notes",
        _document_route=resolve_document_route(
            "cree un rapport pdf libre sur mes notes", mode="agent"
        ),
        history=[],
    )
    assert state._document_route.requires_studio is False
    assert ReActLoop._structured_document_tool_gate(state, "create_pdf") is None
