from types import SimpleNamespace

from src.documents.document_intent import resolve_document_route
from src.core_services.agent_service import _resolve_agent_document_route
from src.reasoning.react import ReActLoop
from src.reasoning.handlers.missions import _worker_routing_objective
from src.subagents.runner import _LEAD_PREFIX


LUMAGRID_OBJECTIVE = (
    "Construis LumaGrid, un jeu web 'Lights Out' 5x5. Workflow obligatoire : "
    "1) Ecris un contrat machine avec write_mission_contract. "
    "2) Genere les stubs. "
    "3) Delegue HTML, CSS et JavaScript a trois workers distincts. "
    "4) Chaque worker doit deleguer son codage au CodeAgent. "
    "5) Verifie la syntaxe JavaScript. "
    "6) Publie le projet dans workspace/lumagrid/. "
    "7) Demarre une preview controlee. "
    "8) Prouve le clic et le reset avec browser_evaluate."
)


def test_code_mission_lead_prompt_does_not_open_document_studio():
    route = resolve_document_route(_LEAD_PREFIX + LUMAGRID_OBJECTIVE, mode="agent")

    assert route.requested_kinds == ()
    assert route.requires_studio is False
    assert route.requires_studio is False


def test_generic_contract_plus_stubs_is_code_protocol_not_service_contract():
    query = (
        "Lance une mission avec une echeance de 25 minutes pour creer HydraTrack, "
        "une application web locale. Utilise le workflow contrat + stubs + 3 "
        "workers, puis lance pytest et verifie le navigateur."
    )

    route = resolve_document_route(query, mode="agent")

    assert route.operation == "none"
    assert route.requested_kinds == ()
    assert route.requires_studio is False


def test_explicit_mission_with_real_documents_keeps_studio_as_capability():
    route = _resolve_agent_document_route(
        "Lance une mission pour coder un CRM, puis cree une facture PDF et un "
        "rapport d'activite avant les tests."
    )

    assert route.requested_kinds == ("facture", "rapport_activite")
    assert route.requires_document_tools is True
    assert route.requires_studio is False
    assert route.owns_run is False


def test_running_mission_keeps_document_requirement_without_owning_the_run():
    route = _resolve_agent_document_route(
        "Cree une facture PDF puis lance pytest sur le projet.",
        mission_run=True,
    )

    assert route.requested_kinds == ("facture",)
    assert route.requires_studio is True
    assert route.requires_document_tools is True
    assert route.owns_run is False


def test_code_worker_contract_preamble_does_not_open_document_studio():
    query = (
        "CONTRAT DE MISSION : lis CONTRAT.md, remplis le stub script.js, "
        "respecte allowed_files et delegue le codage au CodeAgent."
    )

    route = resolve_document_route(query, mode="agent")

    assert route.operation == "none"
    assert route.requested_kinds == ()
    assert route.requires_studio is False


def test_explicit_business_documents_still_route_to_studio():
    service = resolve_document_route(
        "Cree un contrat de prestation PDF pour le client Nova.", mode="agent"
    )
    mission_order = resolve_document_route(
        "Cree un ordre de mission PDF pour le deplacement a Lyon.", mode="agent"
    )

    assert service.requested_kinds == ("contrat_prestation",)
    assert service.requires_studio is True
    assert service.owns_run is True
    assert mission_order.requested_kinds == ("ordre_mission",)
    assert mission_order.requires_studio is True
    assert mission_order.owns_run is True


def test_explicit_invoice_survives_code_contract_vocabulary():
    route = resolve_document_route(
        "Utilise write_mission_contract pour le projet puis cree une facture PDF.",
        mode="agent",
    )

    assert route.requested_kinds == ("facture",)
    assert route.requires_studio is True


def test_code_contract_and_common_avoir_do_not_fabricate_business_documents():
    query = (
        "Construis RainReserve avec Flask. Appelle write_mission_contract avant "
        "de coder. Le contrat doit declarer app.py et tests/test_app.py avec des "
        "signatures. Ces fichiers doivent avoir le meme owner w_fullstack. "
        "Produis avec Document Studio un PDF nomme rapport_rainreserve.pdf."
    )

    route = resolve_document_route(query, mode="agent")

    assert route.requested_kinds == ()
    assert route.kind is None
    assert route.owns_run is False


def test_explicit_service_contract_and_credit_note_remain_documents():
    route = resolve_document_route(
        "Cree un contrat de prestation et un avoir commercial pour Nova.",
        mode="agent",
    )

    assert route.requested_kinds == ("contrat_prestation", "avoir")
    assert route.requires_studio is True


def test_neither_service_contract_nor_credit_note_is_a_positive_request():
    route = resolve_document_route(
        "Produis seulement rapport_solarsip.pdf. Ne produis ni contrat de "
        "prestation ni avoir.",
        mode="agent",
    )

    assert route.requested_kinds == ()
    assert route.owns_run is False


def test_real_service_contract_survives_generic_code_contract_masking():
    route = resolve_document_route(
        "Utilise le workflow contrat + stubs, puis cree un contrat de prestation "
        "PDF pour le client Nova.",
        mode="agent",
    )

    assert route.requested_kinds == ("contrat_prestation",)
    assert route.requires_studio is True
    assert route.owns_run is True


def test_code_mission_run_command_is_not_blocked_by_studio_gate():
    route = resolve_document_route(_LEAD_PREFIX + LUMAGRID_OBJECTIVE, mode="agent")
    loop = SimpleNamespace(_document_route=route)

    blocked = ReActLoop._structured_document_tool_gate(
        loop, "run_command", {"command": "node --check script.js"}
    )

    assert blocked is None


class _TaskStore:
    def __init__(self, metadata):
        self.metadata = metadata

    def get_task(self, _task_id):
        return {"metadata": self.metadata}


def _mission_loop(metadata):
    loop = ReActLoop.__new__(ReActLoop)
    loop.task_id = "task-clickrush"
    loop.task_orchestrator = _TaskStore({"kind": "mission", **metadata})
    loop.runtime_ctx = SimpleNamespace(mode="agent")
    loop._document_route = None
    loop._original_query = ""
    return loop


def test_top_lead_routes_from_original_objective_not_injected_protocol():
    objective = (
        "Construis ClickRush dans le dossier de mission dedie. Ecris le contrat "
        "machine, delegue app.py, index.html et script.js, lance pytest puis prouve "
        "le score avec browser_evaluate."
    )
    loop = _mission_loop({"objective": objective})

    route = ReActLoop._document_route_for_run(
        loop, _LEAD_PREFIX + objective + "\nCree un ordre de mission interne."
    )

    assert route.operation == "none"
    assert route.requested_kinds == ()


def test_top_lead_recomputes_and_downgrades_an_injected_parent_route():
    objective = (
        "Construis HydraTrack avec le workflow contrat + stubs + 3 workers, "
        "puis lance pytest et verifie le navigateur."
    )
    loop = _mission_loop({"objective": objective})
    loop._document_route = resolve_document_route(
        "Cree un contrat de prestation PDF.", mode="agent"
    )

    route = ReActLoop._document_route_for_run(loop)

    assert route.operation == "none"
    assert route.requested_kinds == ()
    assert route.requires_studio is False
    assert route.owns_run is False


def test_worker_routes_from_contract_file_semantics_not_forced_contract_prompt():
    contract = {
        "files": [{
            "path": "script.js",
            "owner": "frontend",
            "description": "Gere le score, les clics et le bouton reset du jeu.",
            "exports": ["function resetGame()"],
        }]
    }
    semantic = _worker_routing_objective(
        "CONTRAT DE MISSION : respecte le contrat et remplis le stub.",
        contract,
        ["script.js"],
    )
    loop = _mission_loop({
        "objective": "CONTRAT DE MISSION : contrat contrat contrat",
        "routing_objective": semantic,
    })

    route = ReActLoop._document_route_for_run(loop)

    assert "score" in semantic
    assert route.operation == "none"
    assert route.requested_kinds == ()


def test_worker_explicitly_assigned_a_report_keeps_document_capability():
    contract = {
        "files": [{
            "path": "facture.pdf",
            "owner": "reporter",
            "description": "Cree une facture PDF professionnelle pour le client Nova.",
        }]
    }
    semantic = _worker_routing_objective("protocole interne", contract, ["facture.pdf"])
    loop = _mission_loop({"routing_objective": semantic})

    route = ReActLoop._document_route_for_run(loop)

    assert route.operation == "create"
    assert route.requires_studio is True
    assert route.owns_run is False


def test_document_capability_never_blocks_unrelated_mission_tools():
    loop = _mission_loop({
        "objective": "Cree une facture PDF puis execute les tests du projet.",
    })
    route = ReActLoop._document_route_for_run(loop)

    assert route.requires_studio is True
    assert route.owns_run is False
    assert ReActLoop._structured_document_tool_gate(
        loop, "run_command", {"command": "python -m pytest -q"},
    ) is None
    assert ReActLoop._structured_document_tool_gate(
        loop, "delegate_task", {"task": "corrige le backend"},
    ) is None


def test_mission_document_evidence_is_additive_and_idempotent():
    free = "HydraTrack est livre. Les tests et le navigateur sont verts."
    proof = "1/1 facture generee et rendue."

    merged = ReActLoop._merge_mission_document_evidence(free, proof)

    assert merged.startswith(free)
    assert "Preuves documentaires:" in merged
    assert proof in merged
    assert ReActLoop._merge_mission_document_evidence(merged, proof) == merged


def test_document_evidence_never_replaces_an_empty_mission_final():
    proof = "1/1 note interne generee et rendue."

    assert ReActLoop._merge_mission_document_evidence("", proof) == ""
    assert ReActLoop._merge_mission_document_evidence("   ", proof) == ""


def test_control_filename_does_not_fabricate_document_verify_action():
    route = resolve_document_route(
        "Genere une note interne PDF puis ecris controle_vega.txt avec son nom.",
        mode="agent",
    )

    assert [action.operation for action in route.workflow_actions] == ["generate"]


def test_explicit_verify_survives_control_filename_mask():
    route = resolve_document_route(
        "Genere une note interne PDF puis verifie controle_vega.txt.",
        mode="agent",
    )

    assert [action.operation for action in route.workflow_actions] == [
        "generate", "verify",
    ]


def test_generic_mission_folder_is_not_fuzzy_order_mission():
    route = resolve_document_route(
        "Construis le jeu dans son dossier de mission dedie.", mode="agent"
    )

    assert route.operation == "none"
    assert route.requested_kinds == ()


def test_parent_and_worker_keep_bounded_proactive_document_creation():
    class _Tools:
        def __init__(self):
            self.requested = ()

        def force_allow_tools(self, names):
            self.requested = tuple(names)
            return list(names)

    for metadata in (
        {"objective": "Analyse le projet et prends les initiatives utiles."},
        {"routing_objective": "Remplis script.js", "allowed_files": ["script.js"]},
    ):
        loop = _mission_loop(metadata)
        loop.tools = _Tools()

        added = ReActLoop._force_mission_proactive_document_tools(loop)

        assert "generate_studio_document" in added
        assert "create_pdf" in added
        assert "create_docx" in added


def test_chat_does_not_force_mission_document_tools():
    loop = ReActLoop.__new__(ReActLoop)
    loop.task_id = None
    loop.task_orchestrator = None
    loop.tools = SimpleNamespace(force_allow_tools=lambda names: list(names))

    assert ReActLoop._force_mission_proactive_document_tools(loop) == []
