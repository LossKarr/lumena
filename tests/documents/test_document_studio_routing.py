from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace

import pytest

from src.core_services.intent_classifier import RequestMode, classify_intent
from src.documents.document_intent import (
    normalize_document_kind,
    resolve_document_route,
    structured_document_kind,
)
from src.reasoning.tool_registry import ToolRegistry


ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize(
    ("query", "kind"),
    [
        ("gzenere moi un devis test stp", "devis"),
        ("faire moi un devis test", "devis"),
        ("okay mainenant un accord de confidentialite", "nda"),
        ("okay un devis maintenant", "devis"),
        ("Crée une facture professionnelle", "facture"),
        ("okay fairt moi un bulletin de paye stp", "bulletin_paie"),
    ],
)
def test_exact_structured_document_requests_reach_react(query, kind):
    assert structured_document_kind(query) == kind
    assert classify_intent(query) == RequestMode.REACT


def test_bare_information_request_is_not_forced_into_studio():
    assert structured_document_kind("C'est quoi un devis ?") is None
    assert classify_intent("C'est quoi un devis ?") == RequestMode.CHAT


def test_implicit_document_request_is_mode_aware():
    query = "lumena jaurai besoin d'un bon de commande test le plus detailler stp"

    chat = resolve_document_route(query, mode="chat")
    assert chat.kind == "bon_commande"
    assert chat.operation == "inform"
    assert chat.requires_studio is False

    agent = resolve_document_route(query, mode="agent")
    assert agent.kind == "bon_commande"
    assert agent.operation == "create"
    assert agent.requires_studio is True
    assert agent.reason == "implicit_agent_request"
    assert classify_intent(query, SimpleNamespace(mode="chat", source_channel="web")) == RequestMode.CHAT
    assert classify_intent(query, SimpleNamespace(mode="agent", source_channel="web")) == RequestMode.REACT


@pytest.mark.parametrize(
    "query",
    [
        "C'est quoi un bon de commande ?",
        "Explique-moi ce qu'est une facture",
        "À quoi sert un devis ?",
    ],
)
def test_information_requests_never_open_the_studio_rail(query):
    for mode in ("chat", "agent"):
        route = resolve_document_route(query, mode=mode)
        assert route.operation == "inform"
        assert route.requires_studio is False


@pytest.mark.parametrize(
    "query",
    [
        "je voudrais une facture professionnelle",
        "j'ai besoin d'un devis détaillé",
        "il me faut une attestation de travail",
        "pourrais-tu me préparer un contrat de prestation",
    ],
)
def test_natural_desire_wording_is_actionable_only_in_agent_mode(query):
    assert resolve_document_route(query, mode="chat").requires_studio is False
    assert resolve_document_route(query, mode="agent").requires_studio is True


@pytest.mark.parametrize(
    ("provided", "canonical"),
    [
        ("quote", "devis"),
        ("quotation", "devis"),
        ("invoice", "facture"),
        ("purchase order", "bon_commande"),
        ("devis", "devis"),
        ("modele_personnalise", "modele_personnalise"),
    ],
)
def test_document_kind_aliases_are_canonicalized(provided, canonical):
    assert normalize_document_kind(provided) == canonical


@pytest.mark.asyncio
async def test_generate_studio_document_canonicalizes_quote(monkeypatch):
    from src.documents import studio as studio_module
    from src.reasoning.handlers.documents import generate_studio_document_handler

    captured = {}

    class FakeStudio:
        @staticmethod
        def parse_json_object(value, *, field):
            assert field == "data"
            return {"numero": "DEV-TEST"}

        async def generate(self, **kwargs):
            captured.update(kwargs)
            return {"path": "workspace/documents/devis_test.pdf"}

    monkeypatch.setattr(studio_module, "get_document_studio", lambda: FakeStudio())
    result = await generate_studio_document_handler(
        None, kind="quote", data='{"numero":"DEV-TEST"}', filename="devis_test"
    )

    assert result.success is True
    assert captured["kind"] == "devis"


@pytest.mark.asyncio
async def test_generate_studio_document_unknown_kind_is_actionable(monkeypatch):
    from types import SimpleNamespace
    from src.documents import studio as studio_module
    from src.reasoning.handlers.documents import generate_studio_document_handler

    class FakeStudio:
        catalog = SimpleNamespace(
            list_templates=lambda: [
                SimpleNamespace(valid=True, manifest=SimpleNamespace(kind="devis")),
                SimpleNamespace(valid=True, manifest=SimpleNamespace(kind="facture")),
            ]
        )

        @staticmethod
        def parse_json_object(value, *, field):
            return {}

        async def generate(self, **kwargs):
            raise KeyError(kwargs["kind"])

    monkeypatch.setattr(studio_module, "get_document_studio", lambda: FakeStudio())
    result = await generate_studio_document_handler(None, kind="unknown_kind", data="{}")

    assert result.success is False
    assert "devis, facture" in result.output
    assert "list_document_models" in result.output


def _registry() -> ToolRegistry:
    registry = object.__new__(ToolRegistry)
    registry.tools = {}
    registry._tool_modules = {}
    registry._allowed_tools = None
    registry._caller_set_allowed = False
    registry._tools_desc_cache = None
    registry._tool_collection = None
    registry._failed_modules = []
    registry._sig_cache = {}
    for category, names in {
        "system": ["final_answer", "ask_user"],
        "missions": ["create_mission"],
        "files": ["write_file"],
        "documents": [
            "list_document_models", "generate_studio_document",
            "generate_studio_documents", "create_pdf",
        ],
        "memory": ["memory_search"],
    }.items():
        for name in names:
            registry.tools[name] = {"name": name, "description": name, "parameters": {}}
            registry._tool_modules[name] = category
    return registry


def test_agent_followup_open_them_keeps_exact_delivery_tool_visible():
    registry = _registry()
    registry.tools["open_document_delivery"] = {
        "name": "open_document_delivery", "description": "open exact delivery", "parameters": {},
    }
    registry._tool_modules["open_document_delivery"] = "files"

    registry.apply_context_filter("ouvre-les", intent="react")

    assert "open_document_delivery" in registry._allowed_tools


def test_chat_followup_open_them_does_not_enable_agent_file_tools():
    registry = _registry()
    registry.tools["open_document_delivery"] = {
        "name": "open_document_delivery", "description": "open exact delivery", "parameters": {},
    }
    registry._tool_modules["open_document_delivery"] = "files"

    registry.apply_context_filter("ouvre-les", intent="chat")

    assert "open_document_delivery" not in registry._allowed_tools


@pytest.mark.parametrize(
    "query",
    [
        "gzenere moi un devis test stp",
        "faire moi un devis test",
        "okay mainenant un accord de confidentialite",
    ],
)
def test_context_filter_keeps_document_studio_tools_visible(query):
    registry = _registry()
    registry.apply_context_filter(query, intent=classify_intent(query).value)
    assert "list_document_models" in registry._allowed_tools
    assert "generate_studio_document" in registry._allowed_tools


def test_multi_document_route_exposes_batch_tool():
    query = "Fais une attestation de travail.\nCree un devis detaille."
    route = resolve_document_route(query, mode="agent")
    registry = _registry()

    registry.apply_context_filter(query, intent="react", document_route=route)

    assert route.requested_kinds == ("attestation", "devis")
    assert "generate_studio_documents" in registry._allowed_tools


def test_exact_runtime_request_exposes_only_studio_generation_in_agent_mode():
    query = "lumena jaurai besoin d'un bon de commande test le plus detailler stp"
    route = resolve_document_route(query, mode="agent")
    registry = _registry()

    registry.apply_context_filter(
        query,
        intent=classify_intent(
            query,
            SimpleNamespace(mode="agent", source_channel="web"),
            document_route=route,
        ).value,
        document_route=route,
    )

    assert "list_document_models" in registry._allowed_tools
    assert "generate_studio_document" in registry._allowed_tools
    assert "create_pdf" not in registry._allowed_tools


def test_mission_document_capability_keeps_general_and_fallback_tools_visible():
    query = "Cree une facture PDF puis ecris les fichiers du projet."
    route = replace(
        resolve_document_route(query, mode="agent"),
        owns_run=False,
    )
    registry = _registry()

    registry.apply_context_filter(query, intent="react", document_route=route)

    assert route.requires_studio is True
    assert route.owns_run is False
    assert "generate_studio_document" in registry._allowed_tools
    assert "create_pdf" in registry._allowed_tools
    assert "write_file" in registry._allowed_tools


def test_exact_runtime_request_stays_conversational_in_chat_mode():
    query = "lumena jaurai besoin d'un bon de commande test le plus detailler stp"
    route = resolve_document_route(query, mode="chat")
    registry = _registry()

    registry.apply_context_filter(query, intent="chat", document_route=route)

    assert "list_document_models" not in registry._allowed_tools
    assert "generate_studio_document" not in registry._allowed_tools
    assert "memory_search" in registry._allowed_tools


def test_runtime_payslip_typo_exposes_studio_and_hides_all_bypasses():
    from src.documents.document_intent import STUDIO_BYPASS_TOOLS

    query = "okay fairt moi un bulletin de paye stp"
    route = resolve_document_route(query, mode="agent")
    registry = _registry()
    registry.apply_context_filter(query, intent="react", document_route=route)

    assert route.kind == "bulletin_paie"
    assert "list_document_models" in registry._allowed_tools
    assert "generate_studio_document" in registry._allowed_tools
    assert not (set(registry._allowed_tools) & STUDIO_BYPASS_TOOLS)


@pytest.mark.parametrize(
    ("query", "expected", "excluded"),
    [
        ("cherche un document pdf sur internet", "search_documents_web", "create_pdf"),
        ("retrouve ma derniere facture dans mes documents", "search_document_library", "generate_studio_document"),
    ],
)
def test_document_operation_filter_exposes_only_relevant_document_chain(query, expected, excluded):
    route = resolve_document_route(query, mode="agent")
    registry = _registry()
    # Add the operation tools used by the production registry to this fixture.
    for name in (
        "search_document_library", "get_document_record", "get_document_history",
        "search_documents_web", "inspect_document_source", "download_document",
        "export_library_document",
    ):
        registry.tools[name] = {"name": name, "description": name, "parameters": {}}
        registry._tool_modules[name] = "documents"

    registry.apply_context_filter(query, intent="react", document_route=route)

    assert expected in registry._allowed_tools
    assert excluded not in registry._allowed_tools


def test_react_prompt_declares_studio_mandatory_before_legacy_paths():
    """Lot RF-3 du refactor ReAct (2026-08-27) : la regle de creation
    d'artefact a quitte `react.py` avec le corps de `_build_react_prompt`
    pour `src/prompts/react_prompt.py`. Les deux chaines y sont intactes.

    Preuve COMPORTEMENTALE equivalente exigee par le plan avant ce
    repointage :
      tests/reasoning/test_rf3_react_prompt_extraction.py
        - test_comportement_studio_est_declare_obligatoire_avant_les_chemins_legacy
    Celle-la construit le prompt et mesure reellement l'ORDRE que le nom de
    ce test-ci affirme — ce qu'une recherche de sous-chaine ne verifiait pas.
    """
    source = (ROOT / "src" / "prompts" / "react_prompt.py").read_text(encoding="utf-8")
    assert "`generate_studio_document` → OBLIGATOIRE" in source
    assert "N'utilise PAS create_pdf, Python ou CodeAgent" in source


def test_structured_document_gate_blocks_legacy_pdf_until_studio_attempt():
    from types import SimpleNamespace
    from src.reasoning.react import ReActLoop

    state = SimpleNamespace(_original_query="faire moi un devis test", history=[])
    blocked = ReActLoop._structured_document_tool_gate(state, "create_pdf")
    assert blocked is not None and blocked.success is False
    assert "list_document_models" in blocked.content
    assert "generate_studio_document" in blocked.content

    state.history = [SimpleNamespace(action=SimpleNamespace(tool_name="generate_studio_document"))]
    assert ReActLoop._structured_document_tool_gate(state, "create_pdf") is None


def test_mode_aware_gate_blocks_exact_runtime_phrase_only_in_agent_mode():
    from src.reasoning.react import ReActLoop

    query = "lumena jaurai besoin d'un bon de commande test le plus detailler stp"
    agent_state = SimpleNamespace(
        _original_query=query,
        _document_route=resolve_document_route(query, mode="agent"),
        history=[],
    )
    chat_state = SimpleNamespace(
        _original_query=query,
        _document_route=resolve_document_route(query, mode="chat"),
        history=[],
    )

    assert ReActLoop._structured_document_tool_gate(agent_state, "create_pdf") is not None
    assert ReActLoop._structured_document_tool_gate(chat_state, "create_pdf") is None


@pytest.mark.asyncio
async def test_studio_route_skips_generic_direct_pipeline_before_skill_matching():
    from src.reasoning.react import ReActLoop

    query = "j'ai besoin d'un devis détaillé"
    state = SimpleNamespace(
        tools=SimpleNamespace(_caller_set_allowed=False),
        _document_route=resolve_document_route(query, mode="agent"),
    )

    assert await ReActLoop._try_direct_pipeline(state, query) is None


def test_studio_route_suppresses_generic_skill_context_only_for_that_request(monkeypatch):
    from src.core_services.context_service import ContextService

    service = ContextService(SimpleNamespace())
    service.skills_auto_activation = True
    query = "j'ai besoin d'un devis détaillé"
    route = resolve_document_route(query, mode="agent")

    assert service._build_active_skills_context_for_query(query, document_route=route) == ""
    assert service._last_active_skills == []


def test_structured_document_gate_is_inert_for_freeform_pdf():
    from types import SimpleNamespace
    from src.reasoning.react import ReActLoop

    state = SimpleNamespace(_original_query="crée un rapport PDF libre", history=[])
    assert ReActLoop._structured_document_tool_gate(state, "create_pdf") is None


def test_published_custom_alias_is_routable_immediately_in_agent_mode(monkeypatch):
    from src.core_services.agent_service import _resolve_agent_document_route
    from src.documents import studio as studio_module

    catalog = SimpleNamespace(
        intent_vocabulary=lambda: {
            "synthese_orion": ("synthese orion", "brief orion", "note orion"),
        }
    )
    monkeypatch.setattr(
        studio_module,
        "get_document_studio",
        lambda: SimpleNamespace(catalog=catalog),
    )

    route = _resolve_agent_document_route("Prépare-moi un brief Orion pour le comité")

    assert route.kind == "synthese_orion"
    assert route.operation == "create"
    assert route.requires_studio is True


def test_custom_catalog_is_not_consulted_for_information_question():
    from src.documents.document_intent import might_be_custom_document_request

    assert might_be_custom_document_request("C'est quoi un modèle documentaire ?") is False
